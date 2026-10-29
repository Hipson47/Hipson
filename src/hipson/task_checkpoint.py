"""Opt-in pre-run checkpoints of an rw assignment's claimed scope, and their restore (task run --checkpoint).

The host copies the scope into private state beside the ledger before the worker starts, so rolling back a
failed writer never depends on what the worker did with git. Restore is a dry run unless applied, touches only
the checkpointed scope, and is refused while the assignment or any overlapping write claim is live.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import time
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from hipson import ledger
from hipson.redaction import is_sensitive_path
from hipson.task_observation import EXCLUDED_NAMES, MAX_DEPTH

FORMAT = 1
MAX_FILES = 20_000
MAX_BYTES = 512 * 1024 * 1024
RETENTION_DAYS = 14
_CHUNK = 1024 * 1024
_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)


class CheckpointError(ValueError):
    """A checkpoint cannot be taken, read or restored as requested."""


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
    return [path for path in paths if not any(other != path and ledger._overlaps(other, path)
                                             and (other == "." or path.startswith(other + "/")) for other in paths)]


def _inside(rel: str, roots: Iterable[str]) -> bool:
    return any(root == "." or rel == root or rel.startswith(root + "/") for root in roots)


def private_paths(db: Path) -> set[Path]:
    """The ledger, its sidecars, observation baselines and checkpoints are never part of a checkpoint."""
    base = Path(db).resolve()
    return {base, *(base.with_name(base.name + suffix) for suffix in ("-wal", "-shm", "-journal")),
            base.parent / f".{base.name}.observations", directory(db)}


def _real_directories(project: Path, rel: str) -> None:
    """Every existing component from the project down to rel must be a real directory, never a link."""
    current, walked = project, PurePosixPath()
    for part in PurePosixPath(rel).parts:
        current, walked = current / part, walked / part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            return
        if not stat.S_ISDIR(info.st_mode):
            raise CheckpointError(f"{walked} is not a real directory (a link or a file); refusing to go through it")


def scan(project: str | Path, scope: Sequence[str], *, caps: Caps | None = None,
         private: Iterable[Path] = (), hash_files: bool = False) -> dict[str, Any]:
    """Walk the claimed scope without following links. Raises CheckpointError above the caps."""
    root = Path(project).resolve()
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
        if not stat.S_ISDIR(info.st_mode):
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
                if child.name in EXCLUDED_NAMES or path in skip:
                    excluded.append(child_rel)
                    continue
                if is_sensitive_path(child_rel):
                    sensitive.append(child_rel)
                    continue
                info = os.lstat(path)
                add(child_rel, path, info)
                if stat.S_ISDIR(info.st_mode):
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
    handle, temporary = tempfile.mkstemp(dir=objects, prefix=".copy.")
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
        manifest = {"format": FORMAT, "assignment": assignment, "project": str(root),
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


def load(db: Path, assignment: str) -> dict[str, Any]:
    path = location(db, assignment) / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CheckpointError(f"no checkpoint for {assignment} (take one with hipson task run --checkpoint)") from None
    except (OSError, ValueError) as exc:
        raise CheckpointError(f"the checkpoint of {assignment} cannot be read: {type(exc).__name__}") from None
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT or manifest.get("assignment") != assignment:
        raise CheckpointError(f"the checkpoint of {assignment} has an unknown format")
    return manifest


def live_blockers(store: ledger.Ledger, assignment: str, roots: Sequence[str]) -> list[str]:
    """Restore only after the worker stopped and while no other writer holds an overlapping claim."""
    current = store.activity(assignment)
    task = store.show(current["task"])
    reasons = []
    if current["status"] == "active":
        reasons.append(f"{assignment} is still active; stop it or run hipson task reap first")
    for other in store.list_tasks(project=task["project"]):
        for item in store.show(other["id"])["assignments"]:
            if item["id"] == assignment or item["status"] != "active" or item["mode"] != "rw":
                continue
            held = [path for path in item["scope"] if any(ledger._overlaps(path, root) for root in roots)]
            if held:
                reasons.append(f"live writer {item['id']} on {other['id']} holds {', '.join(held)}")
    return reasons


def plan(db: Path, assignment: str) -> dict[str, Any]:
    """What a restore would do, by path, within the checkpointed scope only. Changes nothing."""
    manifest = load(db, assignment)
    root = Path(manifest["project"])
    now = scan(root, manifest["roots"], caps=Caps(max_files=MAX_FILES * 10, max_bytes=2**62),
               private=private_paths(db), hash_files=True)
    saved, present = manifest["entries"], now["entries"]
    restore, recreate, delete = [], [], []
    for rel, entry in saved.items():
        current = present.get(rel)
        if current is None:
            recreate.append(rel)
        elif current["type"] != entry["type"]:
            restore.append(rel)
        elif entry["type"] == "file" and (current.get("sha256") != entry["sha256"] or current["mode"] != entry["mode"]):
            restore.append(rel)
        elif entry["type"] == "link" and current["target"] != entry["target"]:
            restore.append(rel)
        elif entry["type"] == "dir" and current["mode"] != entry["mode"]:
            restore.append(rel)
    delete = [rel for rel in present if rel not in saved]
    # Sensitive files are never copied, restored or deleted, whether they existed before or the worker made them.
    kept = sorted(set(now["sensitive"]) - set(manifest["sensitive"]))
    return {"assignment": assignment, "project": str(root), "scope": manifest["roots"],
            "taken_at": manifest["taken_at"], "restore": sorted(restore), "recreate": sorted(recreate),
            "delete": sorted(delete, key=lambda rel: (-rel.count("/"), rel)),
            "untouched": {"sensitive": sorted(set(now["sensitive"]) | set(manifest["sensitive"])),
                          "new_sensitive": kept, "excluded": now["excluded"]},
            "clean": not (restore or recreate or delete)}


def _remove(path: Path, rel: str) -> bool:
    """Remove one path without following links. A directory goes only when empty: what it still holds was
    deliberately left alone (sensitive or excluded files). Returns False for a directory kept that way."""
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode):
        path.unlink()
        return True
    try:
        path.rmdir()
    except OSError:
        if any(path.iterdir()):
            return False
        raise
    return True


def _write_file(db: Path, assignment: str, root: Path, rel: str, entry: dict[str, Any]) -> None:
    source = location(db, assignment) / "objects" / entry["sha256"]
    target = root / rel
    _real_directories(root, str(PurePosixPath(rel).parent))
    handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.restore.")
    try:
        digest = hashlib.sha256()
        with open(source, "rb") as data, os.fdopen(handle, "wb") as out:
            while chunk := data.read(_CHUNK):
                digest.update(chunk)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        if digest.hexdigest() != entry["sha256"]:
            raise CheckpointError(f"the saved copy of {rel} is corrupt; nothing more is restored")
        os.chmod(temporary, entry["mode"])
        os.utime(temporary, ns=(entry["mtime_ns"], entry["mtime_ns"]))
        if os.path.lexists(target) and stat.S_ISDIR(os.lstat(target).st_mode) and not _remove(target, rel):
            raise CheckpointError(f"cannot restore {rel}: a directory there holds files restore leaves alone")
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def apply(db: Path, assignment: str, store: ledger.Ledger) -> dict[str, Any]:
    """Restore the checkpoint exactly, then verify that a fresh plan finds nothing left to do."""
    manifest = load(db, assignment)
    blockers = live_blockers(store, assignment, manifest["roots"])
    if blockers:
        raise CheckpointError("restore refused: " + "; ".join(blockers))
    steps = plan(db, assignment)
    root = Path(manifest["project"])
    saved = manifest["entries"]
    kept = []
    for rel in steps["delete"]:  # Deepest first, so a created directory is empty before it goes.
        target = root / rel
        _real_directories(root, str(PurePosixPath(rel).parent))
        if os.path.lexists(target) and not _remove(target, rel):
            kept.append(rel)
    changed = sorted(set(steps["restore"]) | set(steps["recreate"]), key=lambda rel: (rel.count("/"), rel))
    for rel in changed:  # Parents first, so a directory exists before what it holds.
        entry, target = saved[rel], root / rel
        _real_directories(root, str(PurePosixPath(rel).parent))
        current = os.lstat(target) if os.path.lexists(target) else None
        if entry["type"] == "dir":
            if current is not None and not stat.S_ISDIR(current.st_mode):
                target.unlink()
            target.mkdir(exist_ok=True)
            os.chmod(target, entry["mode"])
        elif entry["type"] == "link":
            if current is not None and not _remove(target, rel):
                raise CheckpointError(f"cannot restore {rel}: a directory there holds files restore leaves alone")
            os.symlink(entry["target"], target)
        else:
            if current is not None and stat.S_ISLNK(current.st_mode):
                target.unlink()  # Replace the link itself; never write through it.
            _write_file(db, assignment, root, rel, entry)
    after = plan(db, assignment)
    return {**steps, "applied": True, "kept": kept, "verified": after["clean"],
            "remaining": {key: after[key] for key in ("restore", "recreate", "delete")}}


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
    """Retention: drop checkpoints of terminal tasks and those older than the window, never one still active."""
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
        reason = None
        try:
            manifest = load(db, path.name)
            taken = datetime.fromisoformat(manifest["taken_at"].replace("Z", "+00:00"))
        except (CheckpointError, KeyError, ValueError):
            manifest, taken = None, None
        status = None
        if store is not None:
            try:
                item = store.activity(path.name)
                status = item["status"]
                task_status = store.show(item["task"])["status"]
            except ledger.LedgerError:
                task_status = None
        else:
            task_status = None
        if status == "active":
            continue
        if task_status in ("done", "cancelled"):
            reason = f"task {task_status}"
        elif taken is not None and taken < cutoff:
            reason = f"older than {older_than_days} days"
        elif manifest is None:
            reason = "unreadable checkpoint"
        if reason:
            removed.append({"assignment": path.name, "reason": reason})
            if not dry_run:
                shutil.rmtree(path, ignore_errors=True)
    return removed
