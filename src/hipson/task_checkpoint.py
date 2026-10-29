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
import tempfile
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
    return ledger._overlaps(_fold(left), _fold(right))


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


def _refused_root(rel: str, root: Path, skip: set[Path]) -> str | None:
    """Why a claimed path may not be checkpointed at all: excluded, sensitive or private state."""
    if rel == ".":
        return None
    parts = PurePosixPath(rel).parts
    if any(_excluded_part(part) for part in parts):
        return "it is or lies in an excluded directory"
    if is_sensitive_path(rel):
        return "it is a sensitive path"
    if any((root / PurePosixPath(*parts[:index])) in skip for index in range(1, len(parts) + 1)):
        return "it holds the ledger's private state"
    return None


def scan(project: str | Path, scope: Sequence[str], *, caps: Caps | None = None,
         private: Iterable[Path] = (), hash_files: bool = False) -> dict[str, Any]:
    """Walk the claimed scope without following links. Raises CheckpointError above the caps."""
    root = Path(project).resolve()
    _root_identity(root)
    limits = caps or Caps()
    roots = _roots(scope)
    skip = {path.resolve() for path in private}
    entries: dict[str, dict[str, Any]] = {}
    sensitive: list[str] = []
    excluded: list[str] = []
    total = files = 0

    def add(rel: str, path: Path, info: os.stat_result) -> None:
        nonlocal total, files
        if stat.S_ISLNK(info.st_mode):
            entries[rel] = {"type": "link", "target": os.readlink(path)}
        elif _is_link(info):
            raise CheckpointError(f"{rel} is a Windows junction or reparse point, which checkpoints do not support")
        elif stat.S_ISDIR(info.st_mode):
            entries[rel] = {"type": "dir", "mode": stat.S_IMODE(info.st_mode)}
        elif stat.S_ISREG(info.st_mode):
            files += 1
            total += info.st_size
            entries[rel] = {"type": "file", "mode": stat.S_IMODE(info.st_mode), "size": info.st_size,
                            "mtime_ns": info.st_mtime_ns}
            if hash_files:
                entries[rel]["sha256"] = _hash(path, info)
        else:
            raise CheckpointError(f"{rel} is not a regular file, directory or link")
        if files > limits.max_files or total > limits.max_bytes:
            raise CheckpointError(f"the claimed scope exceeds the checkpoint caps ({limits.max_files} files, "
                                  f"{limits.max_bytes} bytes); narrow --scope or raise --checkpoint-max-files / "
                                  "--checkpoint-max-bytes")

    absent = []
    for rel in roots:
        reason = _refused_root(rel, root, skip)
        if reason:
            raise CheckpointError(f"cannot checkpoint {rel}: {reason}; narrow --scope")
        if rel != ".":
            _real_directories(root, str(PurePosixPath(rel).parent))
        start = root if rel == "." else root / rel
        try:
            info = os.lstat(start)
        except FileNotFoundError:
            absent.append(rel)
            continue
        if rel != ".":
            add(rel, start, info)
        if _is_link(info) or not stat.S_ISDIR(info.st_mode):
            continue
        stack = [(start, rel, 0)]
        while stack:
            folder, prefix, depth = stack.pop()
            if depth >= MAX_DEPTH:
                raise CheckpointError(f"{prefix} is nested deeper than {MAX_DEPTH} levels")
            with os.scandir(folder) as listing:
                children = sorted(listing, key=lambda entry: entry.name)
            for child in children:
                child_rel = child.name if prefix == "." else f"{prefix}/{child.name}"
                path = Path(child.path)
                # The walk starts from the resolved project and never follows links, so paths stay canonical.
                if _excluded_part(child.name) or path in skip:
                    excluded.append(child_rel)
                    continue
                if is_sensitive_path(child_rel):
                    sensitive.append(child_rel)
                    continue
                info = os.lstat(path)
                add(child_rel, path, info)
                if stat.S_ISDIR(info.st_mode) and not _is_link(info):
                    stack.append((path, child_rel, depth + 1))
    return {"roots": roots, "absent": absent, "entries": dict(sorted(entries.items())), "sensitive": sorted(sensitive),
            "excluded": sorted(excluded), "files": files, "bytes": total}


def _hash(path: Path, info: os.stat_result) -> str | None:
    """Digest of exactly the regular file that was stat()ed; None when it changed or cannot be read."""
    try:
        descriptor = os.open(path, _FLAGS)
    except OSError:
        return None
    with os.fdopen(descriptor, "rb") as source:
        if not _same(os.fstat(source.fileno()), info):
            return None
        digest, read = hashlib.sha256(), 0
        while chunk := source.read(_CHUNK):
            digest.update(chunk)
            read += len(chunk)
    return digest.hexdigest() if read == info.st_size else None


def _same(opened: os.stat_result, info: os.stat_result) -> bool:
    return (stat.S_ISREG(opened.st_mode) and opened.st_size == info.st_size
            and (opened.st_dev, opened.st_ino) == (info.st_dev, info.st_ino))


def _copy_object(path: Path, objects: Path) -> tuple[str, int]:
    """Copy one file into the object store, hashing what was read; refuse if it changes meanwhile."""
    info = os.lstat(path)
    descriptor = os.open(path, _FLAGS)
    try:
        handle, temporary = tempfile.mkstemp(dir=objects, prefix=".copy.")
    except BaseException:
        os.close(descriptor)
        raise
    digest, read = hashlib.sha256(), 0
    try:
        with os.fdopen(descriptor, "rb") as source, os.fdopen(handle, "wb") as target:
            if not _same(os.fstat(source.fileno()), info):
                raise CheckpointError(f"{path.name} changed while it was copied")
            while chunk := source.read(_CHUNK):
                digest.update(chunk)
                target.write(chunk)
                read += len(chunk)
            target.flush()
            os.fsync(target.fileno())
        after = os.lstat(path)
        if read != info.st_size or (after.st_size, after.st_mtime_ns) != (info.st_size, info.st_mtime_ns):
            raise CheckpointError(f"{path.name} changed while it was copied")
        name = digest.hexdigest()
        os.replace(temporary, objects / name)
        return name, read
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def take(db: Path, assignment: str, project: str | Path, scope: Sequence[str], caps: Caps | None = None) -> dict[str, Any]:
    """Copy the claimed scope into private state; the manifest is written last, the directory renamed into place."""
    root = Path(project).resolve()
    target = location(db, assignment)
    if target.exists():
        raise CheckpointError(f"a checkpoint for {assignment} already exists")
    identity = _root_identity(root)
    found = scan(root, scope, caps=caps, private=private_paths(db))
    directory(db).mkdir(mode=0o700, parents=True, exist_ok=True)
    staging = directory(db) / f".{assignment}.{uuid.uuid4().hex}.tmp"
    objects = staging / "objects"
    objects.mkdir(mode=0o700, parents=True)
    try:
        for rel, entry in found["entries"].items():
            if entry["type"] == "file":
                entry["sha256"], copied = _copy_object(root / rel, objects)
                if copied != entry["size"]:
                    raise CheckpointError(f"{rel} changed while it was copied")
        manifest = {"format": FORMAT, "assignment": assignment, "project": str(root), "project_identity": identity,
                    "taken_at": ledger._timestamp(), **found}
        (staging / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        os.replace(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
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
    return rel == str(path) and all(part not in ("", ".", "..") for part in path.parts)


def _excluded_part(name: str) -> bool:
    return _fold(name) in {_fold(item) for item in EXCLUDED_NAMES}


def _protected(rel: str) -> bool:
    """Paths a checkpoint never holds: sensitive files and anything in an excluded directory."""
    return is_sensitive_path(rel) or any(_excluded_part(part) for part in PurePosixPath(rel).parts)


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
    identity = manifest.get("project_identity")
    if not isinstance(manifest.get("project"), str) or not (
            isinstance(identity, list) and len(identity) == 2 and all(type(value) is int for value in identity)):
        problems.append("project")
    if problems:
        raise CheckpointError(f"the checkpoint of {assignment} is damaged ({', '.join(problems[:5])}); "
                              "it will not be used")


def load(db: Path, assignment: str) -> dict[str, Any]:
    path = location(db, assignment) / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
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
    for other in store.list_tasks(project=task["project"]):
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
               private=private_paths(db), hash_files=True)
    saved, present = manifest["entries"], now["entries"]
    private = private_paths(db)
    for rel in saved:
        if any((root / PurePosixPath(*PurePosixPath(rel).parts[:index])) in private
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

    def write(self, rel: str, source: Path, entry: dict[str, Any]) -> None:
        """Write the saved bytes to a temporary name beside the target, verify, then atomically replace it (a
        rename replaces a file or link at the target itself and never writes through a link)."""
        temporary = f".{PurePosixPath(rel).name}.restore.{uuid.uuid4().hex}"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        data = open(source, "rb")  # Opened first: a missing saved copy fails before anything else is held.
        parent_fd = -1
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


def _verify_objects(db: Path, assignment: str, entries: dict[str, Any], paths: Iterable[str]) -> None:
    """Before any change: every saved copy a restore needs exists and still has its recorded digest."""
    objects = location(db, assignment) / "objects"
    for rel in paths:
        entry = entries[rel]
        if entry["type"] != "file":
            continue
        digest = hashlib.sha256()
        try:
            with open(objects / entry["sha256"], "rb") as data:
                while chunk := data.read(_CHUNK):
                    digest.update(chunk)
        except OSError:
            raise CheckpointError(f"the saved copy of {rel} is missing; nothing was changed") from None
        if digest.hexdigest() != entry["sha256"]:
            raise CheckpointError(f"the saved copy of {rel} is corrupt; nothing was changed")


def _locked(db: Path, assignment: str) -> Any:
    from hipson.init_config import config_locks
    return config_locks([location(db, assignment)])


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
                    ops.write(rel, location(db, assignment) / "objects" / entry["sha256"], entry)
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
        note = f"restore of {assignment} stopped: {exc}"
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
        return f"older than {days} days"
    if manifest is None:
        return "unreadable checkpoint"
    return None


def checkpoints(db: Path) -> list[dict[str, Any]]:
    folder = directory(db)
    if not folder.is_dir():
        return []
    found = []
    for path in sorted(folder.iterdir()):
        if path.name.startswith(".") or not path.is_dir():
            continue
        try:
            manifest = load(db, path.name)
        except CheckpointError as exc:
            found.append({"assignment": path.name, "error": str(exc)})
            continue
        found.append({"assignment": path.name, "taken_at": manifest["taken_at"], **summary(manifest)})
    return found


def prune(db: Path, store: ledger.Ledger | None, *, older_than_days: int = RETENTION_DAYS,
          dry_run: bool = False) -> list[dict[str, str]]:
    """Retention: drop checkpoints of terminal tasks and those older than the window. Never one whose task has
    active work (its worker or a restore in progress)."""
    cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
    removed = []
    folder = directory(db)
    if not folder.is_dir():
        return []
    for path in sorted(folder.iterdir()):
        if path.name.startswith("."):
            # Staging left by an interrupted take: safe to drop after a day.
            if path.is_dir() and time.time() - path.stat().st_mtime > 86400:
                removed.append({"assignment": path.name, "reason": "interrupted checkpoint"})
                if not dry_run:
                    shutil.rmtree(path, ignore_errors=True)
            continue
        try:
            manifest: dict[str, Any] | None = load(db, path.name)
            taken = datetime.fromisoformat(str(manifest["taken_at"]).replace("Z", "+00:00")) if manifest else None
        except (CheckpointError, KeyError, ValueError):
            manifest, taken = None, None
        task_status = None
        if store is not None:
            try:
                view = store.show(store.activity(path.name)["task"])
            except ledger.LedgerError:
                view = None
            if view is not None:
                if any(item["status"] == "active" for item in view["assignments"]):
                    continue
                task_status = view["status"]
        reason = None
        if task_status in ("done", "cancelled") or (taken is not None and taken < cutoff) or manifest is None:
            # Under the checkpoint's lock, re-checked: a restore may have started since the check above.
            try:
                with _locked(db, path.name):
                    if store is not None and _has_active_work(store, path.name):
                        continue
                    reason = _prune_reason(task_status, taken, cutoff, older_than_days, manifest)
                    if reason:
                        removed.append({"assignment": path.name, "reason": reason})
                        if not dry_run:
                            shutil.rmtree(path, ignore_errors=True)
            except TimeoutError:
                pass  # Busy: a restore holds it; the next prune decides.
    return removed
