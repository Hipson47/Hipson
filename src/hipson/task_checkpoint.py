"""Opt-in pre-run checkpoints of an rw assignment's claimed scope, and their restore (task run --checkpoint).

The host copies the scope into private state beside the ledger before the worker starts, so rolling back a
failed writer never depends on what the worker did with git. Restore is a dry run unless applied, touches only
the checkpointed scope, holds its own write claim while it runs, and is refused while the assignment or any
overlapping write claim is live. On POSIX every change is made relative to directory descriptors opened without
following links from the verified project root, so a link swapped in during a restore is never followed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import stat
import sys
import time
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from hipson import ledger
from hipson.redaction import is_sensitive_path
from hipson.task_observation import EXCLUDED_NAMES, MAX_DEPTH

FORMAT = 2
MAX_FILES = 20_000
MAX_BYTES = 512 * 1024 * 1024
RETENTION_DAYS = 14
MAX_RETENTION_DAYS = 36500
RESTORE_ACTOR = "hipson-restore"
_CHUNK = 1024 * 1024
_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
_DIRECTORY = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
_FOLD = sys.platform in ("win32", "darwin")  # Case-insensitive by default there.
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_DIGEST = re.compile(r"[0-9a-f]{64}")
_FD_SAFE = (hasattr(os, "O_DIRECTORY") and hasattr(os, "O_NOFOLLOW") and os.open in os.supports_dir_fd
            and os.unlink in os.supports_dir_fd and os.rmdir in os.supports_dir_fd and os.mkdir in os.supports_dir_fd
            and os.symlink in os.supports_dir_fd and os.rename in os.supports_dir_fd)  # replace shares rename


class CheckpointError(ValueError):
    """A checkpoint cannot be taken, read or restored as requested."""


class _MissingParent(CheckpointError):
    """A parent directory does not exist (yet): the path itself is absent."""


@dataclass(frozen=True)
class Caps:
    max_files: int = MAX_FILES
    max_bytes: int = MAX_BYTES


def directory(db: Path) -> Path:
    # Per ledger file, like observation baselines: two ledgers in one directory never share checkpoints.
    return Path(db).resolve().parent / f".{Path(db).name}.checkpoints"


def location(db: Path, assignment: str) -> Path:
    if not assignment.startswith("A-") or not assignment[2:].isdigit():
        raise CheckpointError(f"not an assignment id: {assignment!r}")
    return directory(db) / assignment


def _roots(scope: Sequence[str]) -> list[str]:
    """Normalized claimed paths, without those another claimed path already covers."""
    paths = sorted(ledger.normalize_scope(scope))
    return [path for path in paths if not any(other != path and (other == "." or path.startswith(other + "/"))
                                             for other in paths)]


def _fold(text: str) -> str:
    return text.casefold() if _FOLD else text


def _overlaps(left: str, right: str) -> bool:
    return ledger._overlaps(_fold(_claim_rel(left)), _fold(_claim_rel(right)))


def _claim_rel(rel: str) -> str:
    return rel if rel == "." else _canon_rel(rel)


def _inside(rel: str, roots: Iterable[str]) -> bool:
    return any(root == "." or rel == root or rel.startswith(root + "/") for root in roots)


def private_paths(db: Path) -> set[Path]:
    """The ledger, its sidecars, observation baselines and checkpoints are never part of a checkpoint."""
    base = Path(db).resolve()
    return {base, *(base.with_name(base.name + suffix) for suffix in ("-wal", "-shm", "-journal")),
            base.parent / f".{base.name}.observations", directory(db)}


def _is_link(info: os.stat_result) -> bool:
    # Windows junctions and other reparse points report a directory mode; treat every one as a link.
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE)


def _real_directories(project: Path, rel: str) -> None:
    """Every existing component from the project down to rel must be a real directory, never a link."""
    current, walked = project, PurePosixPath()
    for part in PurePosixPath(rel).parts:
        current, walked = current / part, walked / part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            return
        if _is_link(info) or not stat.S_ISDIR(info.st_mode):
            raise CheckpointError(f"{walked} is not a real directory (a link or a file); refusing to go through it")


def _root_identity(root: Path) -> list[int]:
    info = os.lstat(root)
    if _is_link(info) or not stat.S_ISDIR(info.st_mode):
        raise CheckpointError(f"the project {root} is not a real directory")
    return [info.st_dev, info.st_ino]


def _canon_part(name: str) -> str:
    # Windows ignores trailing dots and spaces in a path component (".git." names ".git").
    return name.rstrip(" .") if sys.platform == "win32" else name


def _canon_rel(rel: str) -> str:
    return "/".join(_canon_part(part) for part in PurePosixPath(rel).parts)


def _private_keys(paths: Iterable[Path]) -> set[str]:
    return {_fold(str(Path(path).resolve())) for path in paths}


def _is_private(path: Path, keys: set[str]) -> bool:
    return _fold(str(path)) in keys


def _refused_root(rel: str, root: Path, keys: set[str]) -> str | None:
    """Why a claimed path may not be checkpointed at all: excluded, sensitive or private state."""
    if rel == ".":
        return None
    parts = PurePosixPath(rel).parts
    if any(_excluded_part(part) for part in parts):
        return "it is or lies in an excluded directory"
    if is_sensitive_path(_canon_rel(rel)):
        return "it is a sensitive path"
    if any(_is_private(root / PurePosixPath(*parts[:index]), keys) for index in range(1, len(parts) + 1)):
        return "it holds the ledger's private state"
    return None


class _Dir:
    """One directory being read without following links. With descriptor support (POSIX) it is a descriptor
    opened with O_NOFOLLOW relative to its verified parent, so a directory swapped for a link while the walk
    runs is never traversed; elsewhere it is a path whose components were checked."""

    def __init__(self, *, fd: int = -1, path: Path) -> None:
        self.fd, self.path = fd, path

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def names(self) -> list[str]:
        return sorted(os.listdir(self.fd if self.fd >= 0 else self.path))

    def lstat(self, name: str) -> os.stat_result:
        return os.lstat(name, dir_fd=self.fd) if self.fd >= 0 else os.lstat(self.path / name)

    def readlink(self, name: str) -> str:
        return os.readlink(name, dir_fd=self.fd) if self.fd >= 0 else os.readlink(self.path / name)

    def open_file(self, name: str) -> int:
        return os.open(name, _FLAGS, dir_fd=self.fd) if self.fd >= 0 else os.open(self.path / name, _FLAGS)

    def mkdir(self, name: str) -> None:
        os.mkdir(name, 0o700, dir_fd=self.fd) if self.fd >= 0 else os.mkdir(self.path / name, 0o700)

    def create(self, name: str) -> int:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        return os.open(name, flags, 0o600, dir_fd=self.fd) if self.fd >= 0 else os.open(self.path / name, flags, 0o600)

    def replace(self, source: str, target: str) -> None:
        if self.fd >= 0:
            os.replace(source, target, src_dir_fd=self.fd, dst_dir_fd=self.fd)
        else:
            os.replace(self.path / source, self.path / target)

    def unlink(self, name: str) -> None:
        os.unlink(name, dir_fd=self.fd) if self.fd >= 0 else os.unlink(self.path / name)

    def rmtree(self, name: str) -> None:
        """Remove a directory in this one without following any link in it (or the name itself)."""
        info = self.lstat(name)
        if _is_link(info) or not stat.S_ISDIR(info.st_mode):
            raise CheckpointError(f"{name} in the checkpoint store is not a real directory; left alone")
        if self.fd >= 0:
            shutil.rmtree(name, dir_fd=self.fd)
        else:
            shutil.rmtree(self.path / name)

    def subdir(self, name: str, rel: str) -> _Dir:
        """A real directory inside this one (FileNotFoundError when absent), never a link."""
        info = self.lstat(name)
        if _is_link(info) or not stat.S_ISDIR(info.st_mode):
            raise CheckpointError(f"{rel} is not a real directory; refusing to use it")
        return self.child(name, info, rel)

    def child(self, name: str, info: os.stat_result, rel: str) -> _Dir:
        if self.fd < 0:
            current = os.lstat(self.path / name)
            if _is_link(current) or not stat.S_ISDIR(current.st_mode):
                raise CheckpointError(f"{rel} changed while it was read; refusing to follow it")
            return _Dir(path=self.path / name)
        try:
            opened = os.open(name, _DIRECTORY, dir_fd=self.fd)
        except OSError as exc:  # ELOOP: it became a link since lstat.
            raise CheckpointError(f"{rel} changed while it was read ({type(exc).__name__}); refusing to follow it") from None
        current = os.fstat(opened)
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            os.close(opened)
            raise CheckpointError(f"{rel} changed while it was read; refusing to follow it")
        return _Dir(fd=opened, path=self.path / name)


def _store_dir(db: Path, *, create: bool = False) -> _Dir | None:
    """The private checkpoint store beside the ledger, opened as a real directory (never a link); None if absent."""
    folder = directory(db)
    if create:
        try:
            os.mkdir(folder, 0o700)
        except FileExistsError:
            pass
    try:
        info = os.lstat(folder)
    except FileNotFoundError:
        return None
    if _is_link(info) or not stat.S_ISDIR(info.st_mode):
        raise CheckpointError(f"the checkpoint store {folder} is not a real directory; refusing to use it")
    if not _FD_SAFE:
        return _Dir(path=folder)
    fd = os.open(folder, _DIRECTORY)
    opened = os.fstat(fd)
    if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
        os.close(fd)
        raise CheckpointError(f"the checkpoint store {folder} changed while it was opened")
    return _Dir(fd=fd, path=folder)


def _open_root(root: Path, expected: list[int] | None = None) -> _Dir:
    identity = _root_identity(root)
    if expected is not None and identity != expected:
        raise CheckpointError(f"the project directory {root} was replaced or moved; refusing to read it")
    if not _FD_SAFE:
        return _Dir(path=root)
    fd = os.open(root, _DIRECTORY)
    info = os.fstat(fd)
    if [info.st_dev, info.st_ino] != identity:
        os.close(fd)
        raise CheckpointError(f"the project {root} changed while it was opened")
    return _Dir(fd=fd, path=root)


def _read(folder: _Dir, name: str, info: os.stat_result, rel: str, objects: _Dir | None) -> str | None:
    """Digest of exactly the regular file that was stat()ed, copied into objects when given. A file that
    changed reads as None when only hashing and refuses the copy when checkpointing."""
    try:
        descriptor = folder.open_file(name)
    except OSError:
        if objects is not None:
            raise CheckpointError(f"{rel} cannot be read") from None
        return None
    handle, temporary = -1, ""
    try:
        if objects is not None:
            temporary = f".copy.{uuid.uuid4().hex}"
            handle = objects.create(temporary)
        with os.fdopen(descriptor, "rb") as source, (os.fdopen(handle, "wb") if handle >= 0 else _NoSink()) as sink:
            descriptor = -1
            handle = -1
            if not _same(os.fstat(source.fileno()), info):
                raise _Changed()
            digest, read = hashlib.sha256(), 0
            while chunk := source.read(_CHUNK):
                digest.update(chunk)
                sink.write(chunk)
                read += len(chunk)
            sink.flush()
            if objects is not None:
                os.fsync(sink.fileno())
        after = folder.lstat(name)
        if read != info.st_size or (after.st_size, after.st_mtime_ns) != (info.st_size, info.st_mtime_ns):
            raise _Changed()
        name_hex = digest.hexdigest()
        if objects is not None:
            objects.replace(temporary, name_hex)
            temporary = ""
        return name_hex
    except _Changed:
        if objects is not None:
            raise CheckpointError(f"{rel} changed while it was copied") from None
        return None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if handle >= 0:
            os.close(handle)
        if temporary and objects is not None:
            try:
                objects.unlink(temporary)
            except OSError:
                pass


class _Changed(Exception):
    pass


class _NoSink:
    def __enter__(self) -> _NoSink:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def write(self, chunk: bytes) -> None:
        pass

    def flush(self) -> None:
        pass

    def fileno(self) -> int:
        return -1


def scan(project: str | Path, scope: Sequence[str], *, caps: Caps | None = None, private: Iterable[Path] = (),
         hash_files: bool = False, copy_into: _Dir | None = None, identity: list[int] | None = None) -> dict[str, Any]:
    """Walk the claimed scope without following links (on POSIX through directory descriptors). Raises
    CheckpointError above the caps. With copy_into, every regular file is copied there as it is read."""
    root = Path(project).resolve()
    limits = caps or Caps()
    roots = _roots(scope)
    keys = _private_keys(private)
    entries: dict[str, dict[str, Any]] = {}
    sensitive: list[str] = []
    excluded: list[str] = []
    absent: list[str] = []
    totals = {"files": 0, "bytes": 0}

    def add(folder: _Dir, name: str, rel: str, info: os.stat_result) -> None:
        if stat.S_ISLNK(info.st_mode):
            entries[rel] = {"type": "link", "target": folder.readlink(name)}
        elif _is_link(info):
            raise CheckpointError(f"{rel} is a Windows junction or reparse point, which checkpoints do not support")
        elif stat.S_ISDIR(info.st_mode):
            entries[rel] = {"type": "dir", "mode": stat.S_IMODE(info.st_mode)}
        elif stat.S_ISREG(info.st_mode):
            totals["files"] += 1
            totals["bytes"] += info.st_size
            if totals["files"] > limits.max_files or totals["bytes"] > limits.max_bytes:
                raise CheckpointError(f"the claimed scope exceeds the checkpoint caps ({limits.max_files} files, "
                                      f"{limits.max_bytes} bytes); narrow --scope or raise --checkpoint-max-files / "
                                      "--checkpoint-max-bytes")
            entries[rel] = {"type": "file", "mode": stat.S_IMODE(info.st_mode), "size": info.st_size,
                            "mtime_ns": info.st_mtime_ns}
            if hash_files or copy_into is not None:
                entries[rel]["sha256"] = _read(folder, name, info, rel, copy_into)
        else:
            raise CheckpointError(f"{rel} is not a regular file, directory or link")

    def walk(folder: _Dir, prefix: str, depth: int) -> None:
        if depth >= MAX_DEPTH:
            raise CheckpointError(f"{prefix} is nested deeper than {MAX_DEPTH} levels")
        for name in folder.names():
            rel = name if prefix == "." else f"{prefix}/{name}"
            if not _valid_rel(rel):
                shown = rel.encode("utf-8", "backslashreplace").decode("utf-8")
                raise CheckpointError(f"{shown!r} has a name checkpoints cannot store (for example a backslash)")
            if _excluded_part(name) or _is_private(root / rel, keys):
                excluded.append(rel)
                continue
            if is_sensitive_path(_canon_rel(rel)):
                sensitive.append(rel)
                continue
            info = folder.lstat(name)
            add(folder, name, rel, info)
            if stat.S_ISDIR(info.st_mode) and not _is_link(info):
                child = folder.child(name, info, rel)
                try:
                    walk(child, rel, depth + 1)
                finally:
                    child.close()

    top = _open_root(root, identity)  # The directory the caller verified, or nothing is read.
    try:
        for rel in roots:
            if rel != "." and not _valid_rel(rel):
                raise CheckpointError(f"cannot checkpoint {rel!r}: checkpoints cannot store that name; narrow --scope")
            reason = _refused_root(rel, root, keys)
            if reason:
                raise CheckpointError(f"cannot checkpoint {rel}: {reason}; narrow --scope")
            if rel == ".":
                walk(top, ".", 0)
                continue
            parts = PurePosixPath(rel).parts
            opened: list[_Dir] = []
            folder, walked = top, PurePosixPath()
            try:
                missing = False
                for part in parts[:-1]:
                    walked = walked / part
                    try:
                        info = folder.lstat(part)
                    except FileNotFoundError:
                        missing = True
                        break
                    if _is_link(info) or not stat.S_ISDIR(info.st_mode):
                        raise CheckpointError(f"{walked} is not a real directory (a link or a file); refusing to go "
                                              "through it")
                    folder = folder.child(part, info, str(walked))
                    opened.append(folder)
                if missing:
                    absent.append(rel)
                    continue
                try:
                    info = folder.lstat(parts[-1])
                except FileNotFoundError:
                    absent.append(rel)
                    continue
                add(folder, parts[-1], rel, info)
                if stat.S_ISDIR(info.st_mode) and not _is_link(info):
                    child = folder.child(parts[-1], info, rel)
                    try:
                        walk(child, rel, 1)
                    finally:
                        child.close()
            finally:
                for item in reversed(opened):
                    item.close()
    finally:
        top.close()
    return {"roots": roots, "absent": absent, "entries": dict(sorted(entries.items())), "sensitive": sorted(sensitive),
            "excluded": sorted(excluded), "files": totals["files"], "bytes": totals["bytes"]}


def _same(opened: os.stat_result, info: os.stat_result) -> bool:
    return (stat.S_ISREG(opened.st_mode) and opened.st_size == info.st_size
            and (opened.st_dev, opened.st_ino) == (info.st_dev, info.st_ino))


def take(db: Path, assignment: str, project: str | Path, scope: Sequence[str], caps: Caps | None = None) -> dict[str, Any]:
    """Copy the claimed scope into private state while walking it; the manifest is written last and the
    directory renamed into place."""
    root = Path(project).resolve()
    location(db, assignment)  # Validates the id.
    identity = _root_identity(root)
    store = _store_dir(db, create=True)
    assert store is not None
    staging_name = f".{assignment}.{uuid.uuid4().hex}.tmp"
    try:
        try:
            store.lstat(assignment)
            raise CheckpointError(f"a checkpoint for {assignment} already exists")
        except FileNotFoundError:
            pass
        store.mkdir(staging_name)
        try:
            staging = store.subdir(staging_name, staging_name)
            try:
                staging.mkdir("objects")
                objects = staging.subdir("objects", "objects")
                try:
                    found = scan(root, scope, caps=caps, private=private_paths(db), copy_into=objects, identity=identity)
                finally:
                    objects.close()
                manifest = {"format": FORMAT, "assignment": assignment, "project": str(root),
                            "project_identity": identity, "taken_at": ledger._timestamp(), **found}
                with os.fdopen(staging.create("manifest.json"), "w", encoding="utf-8") as handle:
                    json.dump(manifest, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                staging.close()
            store.replace(staging_name, assignment)
        except BaseException:
            try:
                store.rmtree(staging_name)
            except (OSError, CheckpointError):
                pass
            raise
    finally:
        store.close()
    return summary(manifest)


def summary(manifest: dict[str, Any]) -> dict[str, Any]:
    entries = manifest["entries"].values()
    return {"files": manifest["files"], "bytes": manifest["bytes"],
            "directories": sum(entry["type"] == "dir" for entry in entries),
            "links": sum(entry["type"] == "link" for entry in entries),
            "untouched_sensitive": len(manifest["sensitive"]), "excluded": len(manifest["excluded"]),
            "scope": manifest["roots"]}


def _valid_rel(rel: object) -> bool:
    if not isinstance(rel, str) or not rel or "\\" in rel or "\0" in rel:
        return False
    path, windows = PurePosixPath(rel), PureWindowsPath(rel)
    if path.is_absolute() or windows.drive or windows.root:  # "C:/x" is relative to POSIX, absolute on Windows.
        return False
    if sys.platform == "win32" and any(part != _canon_part(part) for part in path.parts):
        return False  # "src." and "src " are aliases of "src" there.
    return rel == str(path) and all(part not in ("", ".", "..") for part in path.parts)


def _excluded_part(name: str) -> bool:
    return _fold(_canon_part(name)) in {_fold(item) for item in EXCLUDED_NAMES}


def _protected(rel: str) -> bool:
    """Paths a checkpoint never holds: sensitive files and anything in an excluded directory."""
    return is_sensitive_path(_canon_rel(rel)) or any(_excluded_part(part) for part in PurePosixPath(rel).parts)


def _check_manifest(manifest: dict[str, Any], assignment: str) -> None:
    """Never trust the private files blindly: every path stays relative and inside the recorded roots."""
    problems = []
    roots = manifest.get("roots")
    if not isinstance(roots, list) or not roots or not all(root == "." or _valid_rel(root) for root in roots):
        problems.append("roots")
        roots = []
    entries = manifest.get("entries")
    if not isinstance(entries, dict):
        problems.append("entries")
        entries = {}
    for rel, entry in entries.items():
        kind = entry.get("type") if isinstance(entry, dict) else None
        if not _valid_rel(rel) or not _inside(rel, roots) or kind not in ("file", "dir", "link") or _protected(rel):
            problems.append(f"entry {rel!r}")
        elif kind == "file" and not (isinstance(entry.get("sha256"), str) and _DIGEST.fullmatch(entry["sha256"])
                                     and type(entry.get("mode")) is int and type(entry.get("mtime_ns")) is int):
            problems.append(f"file entry {rel!r}")
        elif kind == "dir" and type(entry.get("mode")) is not int:
            problems.append(f"directory entry {rel!r}")
        elif kind == "link" and not isinstance(entry.get("target"), str):
            problems.append(f"link entry {rel!r}")
    for key in ("absent", "sensitive", "excluded"):
        if not isinstance(manifest.get(key), list) or not all(isinstance(item, str) for item in manifest[key]):
            problems.append(key)
    if not isinstance(manifest.get("taken_at"), str) or not all(
            type(manifest.get(key)) is int and manifest[key] >= 0 for key in ("files", "bytes")):
        problems.append("metadata")
    for entry in entries.values():
        if isinstance(entry, dict) and entry.get("type") == "file" and not (type(entry.get("size")) is int
                                                                             and entry["size"] >= 0):
            problems.append("file size")
            break
    identity = manifest.get("project_identity")
    if not isinstance(manifest.get("project"), str) or not (
            isinstance(identity, list) and len(identity) == 2 and all(type(value) is int for value in identity)):
        problems.append("project")
    if problems:
        raise CheckpointError(f"the checkpoint of {assignment} is damaged ({', '.join(problems[:5])}); "
                              "it will not be used")


def _checkpoint_dir(db: Path, assignment: str) -> tuple[_Dir, _Dir]:
    """The store and this checkpoint's directory, both real directories (FileNotFoundError when absent)."""
    location(db, assignment)
    store = _store_dir(db)
    if store is None:
        raise FileNotFoundError(assignment)
    try:
        return store, store.subdir(assignment, assignment)
    except BaseException:
        store.close()
        raise


def _read_manifest(db: Path, assignment: str) -> str:
    store, folder = _checkpoint_dir(db, assignment)
    try:
        info = folder.lstat("manifest.json")
        if _is_link(info) or not stat.S_ISREG(info.st_mode):
            raise ValueError("the manifest is not a regular file")
        with os.fdopen(folder.open_file("manifest.json"), "rb") as handle:
            return handle.read(64 * 1024 * 1024).decode("utf-8")
    finally:
        folder.close()
        store.close()


def load(db: Path, assignment: str) -> dict[str, Any]:
    try:
        manifest = json.loads(_read_manifest(db, assignment))
    except FileNotFoundError:
        raise CheckpointError(f"no checkpoint for {assignment} (take one with hipson task run --checkpoint)") from None
    except (OSError, ValueError, RecursionError) as exc:
        raise CheckpointError(f"the checkpoint of {assignment} cannot be read: {type(exc).__name__}") from None
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT or manifest.get("assignment") != assignment:
        raise CheckpointError(f"the checkpoint of {assignment} has an unknown format")
    _check_manifest(manifest, assignment)
    return manifest


def _bind(store: ledger.Ledger, assignment: str, manifest: dict[str, Any]) -> ledger.Assignment:
    """The checkpoint must describe this assignment's own claim in its task's project, as the ledger records."""
    item = store.activity(assignment)
    task = store.show(item["task"])
    # Compared as recorded (both were resolved when written); _check_root then detects a replaced directory.
    if manifest["project"] != task["project"] or manifest["roots"] != _roots(item["scope"]):
        raise CheckpointError(f"the checkpoint of {assignment} does not match the ledger's project and scope for it")
    return item


def _check_root(manifest: dict[str, Any]) -> Path:
    root = Path(manifest["project"])
    try:
        identity = _root_identity(root)
        resolved = root.resolve()
    except OSError as exc:
        raise CheckpointError(f"the project {root} cannot be checked: {type(exc).__name__}") from None
    if identity != manifest["project_identity"] or resolved != root:
        raise CheckpointError(f"the project directory {root} was replaced or moved since the checkpoint; "
                              "refusing to restore into it")
    return root


def live_blockers(store: ledger.Ledger, assignment: str, roots: Sequence[str], *, own: str | None = None) -> list[str]:
    """Restore only after the worker stopped and while no other writer holds an overlapping claim."""
    current = store.activity(assignment)
    task = store.show(current["task"])
    reasons = []
    if current["status"] == "active":
        reasons.append(f"{assignment} is still active; stop it or run hipson task reap first")
    for other in [item for item in store.list_tasks() if _fold(item["project"]) == _fold(task["project"])]:
        for item in store.show(other["id"])["assignments"]:
            if item["id"] in (assignment, own) or item["status"] != "active" or item["mode"] != "rw":
                continue
            held = [path for path in item["scope"] if any(_overlaps(path, root) for root in roots)]
            if held:
                reasons.append(f"live writer {item['id']} on {other['id']} holds {', '.join(held)}")
    return reasons


def plan(db: Path, assignment: str, store: ledger.Ledger | None = None) -> dict[str, Any]:
    """What a restore would do, by path, within the checkpointed scope only. Changes nothing."""
    manifest = load(db, assignment)
    if store is not None:
        _bind(store, assignment, manifest)
    root = _check_root(manifest)
    now = scan(root, manifest["roots"], caps=Caps(max_files=MAX_FILES * 10, max_bytes=2**62),
               private=private_paths(db), hash_files=True, identity=manifest["project_identity"])
    saved, present = manifest["entries"], now["entries"]
    keys = _private_keys(private_paths(db))
    for rel in saved:
        if any(_is_private(root / PurePosixPath(*PurePosixPath(rel).parts[:index]), keys)
               for index in range(1, len(PurePosixPath(rel).parts) + 1)):
            raise CheckpointError(f"the checkpoint of {assignment} is damaged (entry {rel!r} is private state)")
    restore, recreate, conflicts = [], [], []
    for rel, entry in saved.items():
        current = present.get(rel)
        if current is None:
            recreate.append(rel)
        elif current["type"] != entry["type"]:
            restore.append(rel)
            if current["type"] == "dir":
                # The directory in the way must empty out by deleting only what the worker created.
                held = [item for item in (*now["sensitive"], *now["excluded"]) if item.startswith(rel + "/")]
                if held:
                    conflicts.append(f"{rel} is now a directory holding files restore leaves alone "
                                     f"({len(held)}, e.g. {held[0]})")
        elif entry["type"] == "file" and (current.get("sha256") != entry["sha256"] or current["mode"] != entry["mode"]
                                          or current["mtime_ns"] != entry["mtime_ns"]):
            restore.append(rel)
        elif entry["type"] == "link" and current["target"] != entry["target"]:
            restore.append(rel)
        elif entry["type"] == "dir" and current["mode"] != entry["mode"]:
            restore.append(rel)
    delete = [rel for rel in present if rel not in saved]
    return {"assignment": assignment, "project": str(root), "scope": manifest["roots"],
            "taken_at": manifest["taken_at"], "restore": sorted(restore), "recreate": sorted(recreate),
            "delete": sorted(delete, key=lambda rel: (-rel.count("/"), rel)), "conflicts": conflicts,
            "untouched": {"sensitive": sorted(set(now["sensitive"]) | set(manifest["sensitive"])),
                          "new_sensitive": sorted(set(now["sensitive"]) - set(manifest["sensitive"])),
                          "excluded": now["excluded"]},
            "clean": not (restore or recreate or delete)}


def _fchmod(fd: int, mode: int) -> None:
    if sys.platform != "win32":  # Only the descriptor path calls it, and that path is POSIX only.
        os.fchmod(fd, mode)


class _Ops:
    """Changes inside the project. With descriptor support (POSIX) every step is relative to directories opened
    component by component with O_NOFOLLOW from the verified root, so a link swapped in meanwhile is never
    followed. Elsewhere (Windows) each parent is checked for links and junctions right before each change."""

    def __init__(self, root: Path, identity: list[int]) -> None:
        self.root = root
        self.root_fd = -1
        if _FD_SAFE:
            self.root_fd = os.open(root, _DIRECTORY)
            info = os.fstat(self.root_fd)
            if [info.st_dev, info.st_ino] != identity:
                os.close(self.root_fd)
                raise CheckpointError(f"the project directory {root} was replaced while restoring")

    def close(self) -> None:
        if self.root_fd >= 0:
            os.close(self.root_fd)

    def _parent(self, rel: str) -> tuple[int, str]:
        """A descriptor of rel's parent (caller closes it unless it is the root) and rel's last name."""
        parts = PurePosixPath(rel).parts
        fd, walked = self.root_fd, PurePosixPath()
        try:
            for part in parts[:-1]:
                walked = walked / part
                try:
                    opened = os.open(part, _DIRECTORY, dir_fd=fd)
                except FileNotFoundError:
                    raise _MissingParent(str(walked)) from None
                except OSError as exc:  # ELOOP for a link, ENOTDIR for a file: never followed.
                    raise CheckpointError(f"{walked} is not a real directory (a link or a file); refusing to go "
                                          f"through it ({type(exc).__name__})") from None
                if fd != self.root_fd:
                    os.close(fd)
                fd = opened
        except BaseException:
            if fd != self.root_fd:
                os.close(fd)
            raise
        return fd, parts[-1]

    def _release(self, fd: int) -> None:
        if fd != self.root_fd:
            os.close(fd)

    def _path(self, rel: str) -> Path:
        _real_directories(self.root, str(PurePosixPath(rel).parent))
        return self.root / rel

    def lstat(self, rel: str) -> os.stat_result | None:
        if self.root_fd < 0:
            path = self._path(rel)
            return os.lstat(path) if os.path.lexists(path) else None
        try:
            fd, name = self._parent(rel)
        except _MissingParent:
            return None
        try:
            return os.lstat(name, dir_fd=fd)
        except FileNotFoundError:
            return None
        finally:
            self._release(fd)

    def remove(self, rel: str) -> bool:
        """Remove a file or link, or an empty directory; False for a directory that still holds files."""
        info = self.lstat(rel)
        if info is None:
            return True
        is_dir = stat.S_ISDIR(info.st_mode) and not _is_link(info)
        if self.root_fd < 0:
            path = self._path(rel)
            try:
                path.rmdir() if is_dir else path.unlink()
            except OSError:
                if is_dir and any(path.iterdir()):
                    return False
                raise
            return True
        fd, name = self._parent(rel)
        try:
            if is_dir:
                try:
                    os.rmdir(name, dir_fd=fd)
                except OSError:
                    folder = self._open_dir(fd, name)
                    try:
                        holds = bool(os.listdir(folder))
                    finally:
                        os.close(folder)
                    if holds:
                        return False
                    raise
            else:
                os.unlink(name, dir_fd=fd)
            return True
        finally:
            self._release(fd)

    def _open_dir(self, fd: int, name: str) -> int:
        return os.open(name, _DIRECTORY, dir_fd=fd)

    def mkdir(self, rel: str) -> None:
        if self.root_fd < 0:
            self._path(rel).mkdir(mode=0o700)
            return
        fd, name = self._parent(rel)
        try:
            os.mkdir(name, 0o700, dir_fd=fd)
        finally:
            self._release(fd)

    def symlink(self, rel: str, target: str) -> None:
        if self.root_fd < 0:
            os.symlink(target, self._path(rel))
            return
        fd, name = self._parent(rel)
        try:
            os.symlink(target, name, dir_fd=fd)
        finally:
            self._release(fd)

    def chmod_dir(self, rel: str, mode: int) -> None:
        if self.root_fd < 0:
            os.chmod(self._path(rel), mode)
            return
        fd, name = self._parent(rel)
        try:
            folder = self._open_dir(fd, name)
            try:
                _fchmod(folder, mode)
            finally:
                os.close(folder)
        finally:
            self._release(fd)

    def write(self, rel: str, data: Any, entry: dict[str, Any]) -> None:
        """Write the saved bytes to a temporary name beside the target, verify, then atomically replace it (a
        rename replaces a file or link at the target itself and never writes through a link)."""
        temporary = f".hipson-restore.{uuid.uuid4().hex}"  # Fixed length: a 255-byte name still restores.
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        parent_fd = -1  # data is the opened saved copy: a missing or swapped one failed before anything was held.
        try:
            if self.root_fd < 0:
                target = self._path(rel)
                held = target.parent / temporary
                handle = os.open(held, flags, 0o600)
            else:
                parent_fd, name = self._parent(rel)
                handle = os.open(temporary, flags, 0o600, dir_fd=parent_fd)
        except BaseException:
            data.close()
            if parent_fd >= 0:
                self._release(parent_fd)
            raise
        try:
            digest = hashlib.sha256()
            with data, os.fdopen(handle, "wb") as out:
                while chunk := data.read(_CHUNK):
                    digest.update(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
                if digest.hexdigest() != entry["sha256"]:
                    raise CheckpointError(f"the saved copy of {rel} changed during the restore")
                if parent_fd >= 0:
                    _fchmod(out.fileno(), entry["mode"])
                    os.utime(out.fileno(), ns=(entry["mtime_ns"], entry["mtime_ns"]))
            if parent_fd >= 0:
                os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            else:
                os.chmod(held, entry["mode"])
                os.utime(held, ns=(entry["mtime_ns"], entry["mtime_ns"]))
                os.replace(held, self._path(rel))
        except BaseException:
            try:
                if parent_fd >= 0:
                    os.unlink(temporary, dir_fd=parent_fd)
                else:
                    held.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        finally:
            if parent_fd >= 0:
                self._release(parent_fd)


def _open_object(db: Path, assignment: str, entry: dict[str, Any]) -> Any:
    """A saved copy: a regular file of the recorded size inside real checkpoint directories, opened through the
    store's directory descriptors on POSIX (never through a link) and never a device or FIFO."""
    digest = entry["sha256"]
    try:
        store, folder = _checkpoint_dir(db, assignment)
    except FileNotFoundError:
        raise CheckpointError(f"the checkpoint of {assignment} is missing; nothing was changed") from None
    except CheckpointError as exc:
        raise CheckpointError(f"{exc}; nothing was changed") from None
    try:
        objects = folder.subdir("objects", "objects")
        try:
            expected = objects.lstat(digest)
            descriptor = objects.open_file(digest)
        finally:
            objects.close()
    except CheckpointError as exc:
        raise CheckpointError(f"the checkpoint store is not intact ({exc}); nothing was changed") from None
    except OSError:
        raise CheckpointError(f"the saved copy {digest[:12]} is missing; nothing was changed") from None
    finally:
        folder.close()
        store.close()
    info = os.fstat(descriptor)
    if (_is_link(expected) or not stat.S_ISREG(info.st_mode) or info.st_size != entry["size"]
            or (info.st_dev, info.st_ino) != (expected.st_dev, expected.st_ino)):
        os.close(descriptor)
        raise CheckpointError(f"the saved copy {digest[:12]} is not the file that was saved; nothing was changed")
    return os.fdopen(descriptor, "rb")


def _verify_objects(db: Path, assignment: str, entries: dict[str, Any], paths: Iterable[str]) -> None:
    """Before any change: every saved copy a restore needs exists and still has its recorded digest."""
    for rel in paths:
        entry = entries[rel]
        if entry["type"] != "file":
            continue
        digest = hashlib.sha256()
        with _open_object(db, assignment, entry) as data:
            while chunk := data.read(_CHUNK):
                digest.update(chunk)
        if digest.hexdigest() != entry["sha256"]:
            raise CheckpointError(f"the saved copy of {rel} is corrupt; nothing was changed")


def _locked(db: Path, assignment: str) -> Any:
    from hipson.init_config import config_locks
    # One key per checkpoint however the ledger path is spelled (LEDGER.sqlite and ledger.sqlite on macOS).
    return config_locks([Path(_fold(str(location(db, assignment).resolve())))])


def apply(db: Path, assignment: str, store: ledger.Ledger) -> dict[str, Any]:
    """Restore the checkpoint exactly under a restore claim, then verify that a fresh plan finds nothing to do.
    The checkpoint stays locked for the whole restore, so retention cannot remove it meanwhile."""
    try:
        with _locked(db, assignment):
            return _apply(db, assignment, store)
    except TimeoutError:
        raise CheckpointError(f"the checkpoint of {assignment} is busy (another restore or prune)") from None


def _apply(db: Path, assignment: str, store: ledger.Ledger) -> dict[str, Any]:
    manifest = load(db, assignment)
    item = _bind(store, assignment, manifest)
    roots = manifest["roots"]
    blockers = live_blockers(store, assignment, roots)
    if blockers:
        raise CheckpointError("restore refused: " + "; ".join(blockers))
    if store.show(item["task"])["status"] in ("done", "cancelled"):
        raise CheckpointError(f"restore refused: {item['task']} is closed; reopen it first")
    # The ledger's own claim keeps other writers out for the whole restore (conflicts are checked atomically).
    claim = store.assign(item["task"], actor=RESTORE_ACTOR, role="builder", mode="rw", scope=roots,
                         owner_pid=os.getpid(), owner_host=socket.gethostname(), owner_boot_id=ledger._boot_id(),
                         owner_pid_namespace=ledger._pid_namespace(), needs=[])["id"]
    outcome, note = "failed", "restore did not finish"
    try:
        late = live_blockers(store, assignment, roots, own=claim)  # Case-insensitive overlaps the claim check misses.
        if late:
            raise CheckpointError("restore refused: " + "; ".join(late))
        steps = plan(db, assignment, store)
        if steps["conflicts"]:
            raise CheckpointError("restore refused before any change: " + "; ".join(steps["conflicts"]))
        saved = manifest["entries"]
        changed = sorted(set(steps["restore"]) | set(steps["recreate"]), key=lambda rel: (rel.count("/"), rel))
        _verify_objects(db, assignment, saved, changed)
        root = _check_root(manifest)
        ops = _Ops(root, manifest["project_identity"])
        kept: list[str] = []
        try:
            # Directories stay writable for the owner while their contents change; saved modes come last.
            for rel in sorted((rel for rel, entry in saved.items() if entry["type"] == "dir"),
                              key=lambda rel: (rel.count("/"), rel)):
                try:
                    info = ops.lstat(rel)
                except CheckpointError:
                    continue  # Below a link the worker put in place of a directory: re-created further down.
                if info is not None and stat.S_ISDIR(info.st_mode) and not _is_link(info):
                    ops.chmod_dir(rel, stat.S_IMODE(info.st_mode) | stat.S_IRWXU)
            for rel in steps["delete"]:  # Deepest first, so a created directory is empty before it goes.
                if not ops.remove(rel):
                    kept.append(rel)
            for rel in changed:  # Parents first, so a directory exists before what it holds.
                entry = saved[rel]
                info = ops.lstat(rel)
                current_dir = info is not None and stat.S_ISDIR(info.st_mode) and not _is_link(info)
                if entry["type"] == "dir":
                    if info is not None and not current_dir:
                        ops.remove(rel)
                    if info is None or not current_dir:
                        ops.mkdir(rel)
                    continue
                # A directory in the way is emptied by now; a file or link is replaced (the write renames over it).
                must_clear = current_dir or (entry["type"] == "link" and info is not None)
                if must_clear and not ops.remove(rel):
                    raise CheckpointError(f"cannot restore {rel}: a directory there holds files restore leaves alone")
                if entry["type"] == "link":
                    ops.symlink(rel, entry["target"])
                else:
                    ops.write(rel, _open_object(db, assignment, entry), entry)
            for rel in sorted((rel for rel, entry in saved.items() if entry["type"] == "dir"),
                              key=lambda rel: (-rel.count("/"), rel)):
                ops.chmod_dir(rel, saved[rel]["mode"])
        finally:
            ops.close()
        after = plan(db, assignment, store)
        outcome = "passed" if after["clean"] else "failed"
        note = f"restored {assignment}: verified {str(after['clean']).lower()}"
        return {**steps, "applied": True, "kept": kept, "verified": after["clean"], "claim": claim,
                "remaining": {key: after[key] for key in ("restore", "recreate", "delete")}}
    except CheckpointError as exc:
        # File names can hold bytes that are not text; the ledger only takes valid Unicode.
        note = f"restore of {assignment} stopped: " + str(exc).encode("utf-8", "backslashreplace").decode("utf-8")
        raise
    finally:
        store.finish(claim, result=outcome, summary=note)
        store.ack(claim, note="restore claim released by hipson task restore")


def _has_active_work(store: ledger.Ledger, assignment: str) -> bool:
    try:
        view = store.show(store.activity(assignment)["task"])
    except ledger.LedgerError:
        return False
    return any(item["status"] == "active" for item in view["assignments"])


def _prune_reason(task_status: str | None, taken: datetime | None, cutoff: datetime, days: int,
                  manifest: dict[str, Any] | None) -> str | None:
    if task_status in ("done", "cancelled"):
        return f"task {task_status}"
    if taken is not None and taken < cutoff:
        return f"older than {days} days" if manifest is not None else f"unreadable and older than {days} days"
    return None


def _store_names(store: _Dir) -> list[tuple[str, os.stat_result]]:
    named = []
    for name in store.names():
        info = store.lstat(name)
        if not _is_link(info) and stat.S_ISDIR(info.st_mode):
            named.append((name, info))
    return named


def checkpoints(db: Path) -> list[dict[str, Any]]:
    store = _store_dir(db)
    if store is None:
        return []
    try:
        names = [name for name, _ in _store_names(store) if not name.startswith(".")]
    finally:
        store.close()
    found = []
    for name in names:
        try:
            manifest = load(db, name)
        except CheckpointError as exc:
            found.append({"assignment": name, "error": str(exc)})
            continue
        found.append({"assignment": name, "taken_at": manifest["taken_at"], **summary(manifest)})
    return found


def prune(db: Path, store: ledger.Ledger | None, *, older_than_days: int = RETENTION_DAYS,
          dry_run: bool = False) -> list[dict[str, str]]:
    """Retention: drop checkpoints of terminal tasks and those older than the window. Never one whose task has
    active work (its worker or a restore in progress). Deletion goes through the store's descriptors."""
    if type(older_than_days) is not int or not 0 <= older_than_days <= MAX_RETENTION_DAYS:
        raise CheckpointError(f"--older-than-days must be between 0 and {MAX_RETENTION_DAYS}")
    cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
    removed: list[dict[str, str]] = []
    folder = _store_dir(db)
    if folder is None:
        return removed
    try:
        for name, info in _store_names(folder):
            if name.startswith("."):
                # Staging left by an interrupted take: safe to drop after a day.
                if time.time() - info.st_mtime > 86400:
                    removed.append({"assignment": name, "reason": "interrupted checkpoint"})
                    if not dry_run:
                        _quietly(folder.rmtree, name)
                continue
            try:
                manifest: dict[str, Any] | None = load(db, name)
                taken = datetime.fromisoformat(str(manifest["taken_at"]).replace("Z", "+00:00")) if manifest else None
            except (CheckpointError, KeyError, ValueError):
                # Unreadable: its saved copies may still matter, so only the window (by directory age) removes it.
                manifest = None
                taken = datetime.fromtimestamp(info.st_mtime, UTC)
            task_status = None
            if store is not None:
                try:
                    view = store.show(store.activity(name)["task"])
                except ledger.LedgerError:
                    view = None
                if view is not None:
                    if any(item["status"] == "active" for item in view["assignments"]):
                        continue
                    task_status = view["status"]
            if not (task_status in ("done", "cancelled") or (taken is not None and taken < cutoff)):
                continue
            # Under the checkpoint's lock, re-checked: a restore may have started since the check above.
            try:
                with _locked(db, name):
                    if store is not None and _has_active_work(store, name):
                        continue
                    reason = _prune_reason(task_status, taken, cutoff, older_than_days, manifest)
                    if reason:
                        removed.append({"assignment": name, "reason": reason})
                        if not dry_run:
                            _quietly(folder.rmtree, name)
            except TimeoutError:
                pass  # Busy: a restore holds it; the next prune decides.
    finally:
        folder.close()
    return removed


def _quietly(action: Any, name: str) -> None:
    try:
        action(name)
    except (OSError, CheckpointError):
        pass
