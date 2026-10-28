"""Bounded OS-level observation of what changed in a project while an assignment ran (opt-in).

A snapshot taken before launch is compared with one taken after the worker stops, on any path including
reap: path, size, mtime and (within the hashing budget) content hash, independent of Git and of what the worker
reports. The result is an observation, not attribution: the lead or another process may have written too.
Changes in paths claimed by assignments that overlapped the observed interval are recorded as unattributed
instead of blamed on this worker. Any cap, unreadable path or skipped content hash makes the observation
incomplete, which never reads as clean.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import sys
import tempfile
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hipson import ledger
from hipson.redaction import is_sensitive_path, redact_text
from hipson.task_verification import _TOOL_CACHE_DIRS

FORMAT = 2
MAX_FILES = 20_000
MAX_HASH_BYTES = 256 * 1024 * 1024
MAX_DEPTH = 64
MAX_LISTED = 200
MAX_REASONS = 20
# Version control (directory or worktree pointer file), local environments and tool caches: a worker
# installing dependencies or a test run writing caches is not a scoped source edit.
EXCLUDED_NAMES = frozenset({".git", ".venv", "venv", "node_modules", *_TOOL_CACHE_DIRS})
CHANGES = ("created", "modified", "deleted")
GROUPS = ("in_scope", "out_of_scope", "unattributed")
UNCERTAINTY = ("Filesystem observation of the project tree before and after the run, not attribution: the lead or "
               "another process may also have written. Changes in paths claimed by overlapping assignments are "
               "unattributed.")
# Case-insensitive defaults on Windows and macOS: scope comparison follows the filesystem, not the spelling.
_FOLD = sys.platform in ("win32", "darwin")
_HASHED = "sha256:"


@dataclass(frozen=True)
class Caps:
    max_files: int = MAX_FILES
    max_hash_bytes: int = MAX_HASH_BYTES

    def __post_init__(self) -> None:
        for name, value in (("max_files", self.max_files), ("max_hash_bytes", self.max_hash_bytes)):
            if type(value) is not int or value < 1:
                raise ValueError(f"Observation {name} must be a positive integer")


def _fold(path: str) -> str:
    return path.casefold() if _FOLD else path


def _key(path: str) -> str:
    """Stored identity of a path: the path itself, or a digest when its name looks like a secret."""
    return path if redact_text(path) == path else _HASHED + hashlib.sha256(path.encode("utf-8", "surrogatepass")).hexdigest()


def _order(key: str) -> tuple[str, ...]:
    # The walk visits names sorted per directory, which is lexicographic order on path components.
    return tuple(key.split("/"))


def _digest(path: str, info: os.stat_result) -> tuple[str | None, int]:
    """Hash exactly the regular file that was stat()ed; a replaced, grown or special file yields no digest."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None, 0
    with os.fdopen(descriptor, "rb") as source:
        opened = os.fstat(source.fileno())
        same = stat.S_ISREG(opened.st_mode) and opened.st_size == info.st_size and (
            not info.st_ino or (opened.st_dev, opened.st_ino) == (info.st_dev, info.st_ino))
        if not same:
            return None, 0
        digest, read = hashlib.sha256(), 0
        while read <= info.st_size and (chunk := source.read(min(1024 * 1024, info.st_size + 1 - read))):
            digest.update(chunk)
            read += len(chunk)
    return (digest.hexdigest() if read == info.st_size else None), read


def take(project: str | Path, caps: Caps | None = None, *, exclude: Sequence[str] = ()) -> dict[str, Any]:
    """Snapshot paths (never contents) under the project in a bounded, deterministic walk."""
    caps = caps or Caps()
    root = Path(project).resolve()
    skip = {_fold(item) for item in exclude}
    entries: dict[str, list[Any]] = {}
    reasons: list[str] = []
    unreadable: list[str] = []
    hashed = visited = 0
    last: str | None = None
    truncated = False
    # Iterative depth-first walk; children are pushed in reverse sorted order so they pop in sorted order.
    stack: list[tuple[str, str, int, os.DirEntry[str] | None]] = [("", str(root), 0, None)]
    while stack:
        relative, absolute, depth, entry = stack.pop()
        if entry is None:  # A directory to list.
            if depth > MAX_DEPTH:
                truncated = True
                reasons.append(f"depth limit {MAX_DEPTH} reached")
                break
            try:
                with os.scandir(absolute) as listing:
                    items = []
                    for child in listing:
                        items.append(child)
                        if visited + len(items) > caps.max_files:
                            break
            except OSError:
                if relative:
                    unreadable.append(_key(relative.rstrip("/")))
                    reasons.append("unreadable directory: " + redact_text(relative.rstrip("/")))
                    continue
                raise
            if visited + len(items) > caps.max_files:
                # Paths before this directory are complete; nothing at or after it is compared.
                truncated = True
                reasons.append(f"file cap {caps.max_files} reached")
                break
            visited += len(items)
            for child in sorted(items, key=lambda item: item.name, reverse=True):
                if child.name in EXCLUDED_NAMES or _fold(relative + child.name) in skip:
                    continue
                try:
                    directory = child.is_dir(follow_symlinks=False)
                except OSError:
                    directory = False
                path = relative + child.name
                stack.append((path + "/", child.path, depth + 1, None) if directory else (path, child.path, depth, child))
            if relative:
                last = _key(relative.rstrip("/"))
            continue
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError:
            unreadable.append(_key(relative))
            reasons.append("unreadable file: " + redact_text(relative))
            continue
        digest = None
        if stat.S_ISLNK(info.st_mode):
            kind = "link"
            try:
                digest = hashlib.sha256(os.fsencode(os.readlink(absolute))).hexdigest()
            except OSError:
                reasons.append("unreadable link: " + redact_text(relative))
        elif stat.S_ISREG(info.st_mode):
            kind = "file"
            if is_sensitive_path(relative):
                pass  # Secret files are compared by size and mtime only: no digest of their content anywhere.
            elif hashed + info.st_size > caps.max_hash_bytes:
                reasons.append(f"hash budget {caps.max_hash_bytes} bytes reached")
            else:
                digest, read = _digest(absolute, info)
                hashed += read
                if digest is None:
                    reasons.append("file changed while hashing: " + redact_text(relative))
        else:
            kind = "other"
        key = _key(relative)
        entries[key] = [redact_text(relative), kind, info.st_size, info.st_mtime_ns, digest]
        last = key
    return {"format": FORMAT, "root": str(root), "entries": entries, "truncated": truncated,
            "last": last if truncated else None, "unreadable": sorted(set(unreadable)),
            "incomplete": list(dict.fromkeys(reasons))[:MAX_REASONS], "hashed_bytes": hashed,
            "caps": {"max_files": caps.max_files, "max_hash_bytes": caps.max_hash_bytes}, "exclude": list(exclude)}


def diff(before: dict[str, Any], after: dict[str, Any]) -> tuple[dict[str, list[str]], set[str], list[str]]:
    """Created/modified/deleted keys, keys judged by metadata only, and why the comparison is incomplete."""
    bounds = [snap["last"] for snap in (before, after) if snap["truncated"]]
    hidden = {key for snap in (before, after) for key in snap["unreadable"]}
    old, new = before["entries"], after["entries"]

    def comparable(key: str) -> bool:
        if key in hidden or any(not key.startswith(_HASHED) and key.startswith(prefix + "/") for prefix in hidden):
            return False
        if not bounds:
            return True
        if any(bound is None or bound.startswith(_HASHED) for bound in bounds) or key.startswith(_HASHED):
            return key in old and key in new  # No usable order bound: only paths seen in both snapshots.
        return _order(key) <= min(_order(bound) for bound in bounds)

    changes: dict[str, list[str]] = {name: [] for name in CHANGES}
    metadata: set[str] = set()
    for key in sorted(set(old) | set(new), key=_order):
        if not comparable(key):
            continue
        if key not in old:
            changes["created"].append(key)
        elif key not in new:
            changes["deleted"].append(key)
        else:
            (_, kind, size, mtime, digest), (_, kind2, size2, mtime2, digest2) = old[key], new[key]
            if kind != kind2:
                changes["modified"].append(key)
            elif digest is not None and digest2 is not None:
                if digest != digest2:  # Equal content after an edit and revert is no change.
                    changes["modified"].append(key)
            elif (size, mtime) != (size2, mtime2):
                changes["modified"].append(key)
                metadata.add(key)
    reasons = list(dict.fromkeys([*before["incomplete"], *after["incomplete"]]))[:MAX_REASONS]
    return changes, metadata, reasons


def _covered(path: str, scope: Sequence[str]) -> bool:
    key = _fold(path)
    return any(item == "." or key == _fold(item) or key.startswith(_fold(item).rstrip("/") + "/") for item in scope)


def classify(before: dict[str, Any], after: dict[str, Any], *, mode: str, scope: Sequence[str],
             others: Sequence[tuple[str, Sequence[str]]]) -> dict[str, Any]:
    """Bounded observation record: rw changes inside the assignment's scope are expected, everything else is not."""
    changes, metadata, reasons = diff(before, after)
    counts = {group: dict.fromkeys(CHANGES, 0) for group in GROUPS}
    listed: list[dict[str, str]] = []
    concurrent: set[str] = set()
    for change in CHANGES:
        for key in changes[change]:
            display = (after["entries"].get(key) or before["entries"][key])[0]
            path = display if key.startswith(_HASHED) else key  # Secret-shaped names match scope as redacted text.
            holders = [assignment for assignment, claimed in others if _covered(path, claimed)]
            if mode == "rw" and _covered(path, scope):
                group = "in_scope"
            elif holders:
                group = "unattributed"
                concurrent.update(holders)
            else:
                group = "out_of_scope"
            counts[group][change] += 1
            listed.append({"path": display, "change": change, "attribution": group,
                           "evidence": "metadata" if key in metadata else "content" if change == "modified" else "presence"})
    # Out-of-scope paths first: they are what the lead must look at.
    listed.sort(key=lambda item: (item["attribution"] != "out_of_scope", item["attribution"], item["path"]))
    total = sum(sum(group.values()) for group in counts.values())
    return {
        "status": "incomplete" if reasons else "changes" if total else "clean",
        "observed_at": ledger._timestamp(), "mode": mode, "truncated": bool(before["truncated"] or after["truncated"]),
        "incomplete_reasons": reasons,
        "files_before": len(before["entries"]), "files_after": len(after["entries"]),
        "counts": counts, "paths": listed[:MAX_LISTED], "omitted": max(0, len(listed) - MAX_LISTED),
        "concurrent_assignments": sorted(concurrent), "caps": before["caps"],
        "excluded_dirs": sorted(EXCLUDED_NAMES), "uncertainty": UNCERTAINTY,
    }


def unobserved(mode: str, reason: str) -> dict[str, Any]:
    return {"status": "unobserved", "observed_at": ledger._timestamp(), "mode": mode, "truncated": False,
            "incomplete_reasons": [reason], "files_before": 0, "files_after": 0,
            "counts": {group: dict.fromkeys(CHANGES, 0) for group in GROUPS}, "paths": [], "omitted": 0,
            "concurrent_assignments": [], "caps": {}, "excluded_dirs": [],
            "uncertainty": f"{reason}; project changes are unknown."}


def findings(record: dict[str, Any], assignment: str) -> list[dict[str, str]]:
    """One major finding per kind of unexpected change; a read-only assignment may not change anything."""
    result = []
    for change in CHANGES:
        paths = [item["path"] for item in record["paths"]
                 if item["attribution"] == "out_of_scope" and item["change"] == change]
        count = record["counts"]["out_of_scope"][change]
        if not count:
            continue
        who = "read-only assignment" if record["mode"] == "ro" else "assignment"
        listed = ", ".join(paths[:50]) + (f" and {count - len(paths[:50])} more" if count > len(paths[:50]) else "")
        result.append({
            "title": f"Observed {count} {change} path(s) outside the scope of {who} {assignment}",
            "detail": (f"{listed}. {UNCERTAINTY} Resolve as fixed after reverting, or wontfix with a reason if the "
                       "change was expected."),
            "location": paths[0] if paths else "",
        })
    return result


def _directory(db: Path) -> Path:
    # Per ledger file: two ledgers in one directory must never share assignment baselines.
    return Path(db).resolve().parent / f".{Path(db).name}.observations"


def baseline_path(db: Path, assignment: str) -> Path:
    return _directory(db) / f"{assignment}.json"


def ledger_exclusions(db: Path, project: str | Path) -> list[str]:
    """Project-relative paths of the active ledger, its SQLite sidecars and the baselines, when inside the project."""
    root = Path(project).resolve()
    database = Path(db).resolve()
    candidates = [database, *(database.with_name(database.name + suffix) for suffix in ("-wal", "-shm", "-journal")),
                  _directory(db)]
    excluded = []
    for path in candidates:
        try:
            excluded.append(path.relative_to(root).as_posix())
        except ValueError:
            continue
    return excluded


def _write(target: Path, payload: dict[str, Any]) -> None:
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.stem}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def start(db: Path, assignment: str, project: str | Path, caps: Caps) -> None:
    """Take and privately store the pre-run snapshot, so reap can still compare it if the supervisor dies."""
    _write(baseline_path(db, assignment), take(project, caps, exclude=ledger_exclusions(db, project)))


def start_failed(db: Path, assignment: str, reason: str) -> None:
    """Remember that observation was requested but has no baseline, so the result says unobserved, not nothing."""
    _write(baseline_path(db, assignment), {"format": FORMAT, "error": redact_text(reason)})


def _baseline(db: Path, assignment: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(baseline_path(db, assignment).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        return {"error": f"baseline unreadable: {type(exc).__name__}"}
    return payload if isinstance(payload, dict) else {"error": "baseline malformed"}


def capture_after(db: Path, assignment: str) -> tuple[dict[str, Any], str] | None:
    """Final snapshot taken while the assignment still owns its claims; None when there is no usable baseline."""
    before = _baseline(db, assignment)
    if not before or "error" in before or before.get("format") != FORMAT:
        return None
    stamp = ledger._timestamp()
    return take(before["root"], Caps(**before["caps"]), exclude=before.get("exclude", [])), stamp


def complete(db: Path, assignment: str, *, after: tuple[dict[str, Any], str] | None = None) -> ledger.Assignment | None:
    """Compare an ended assignment with its stored baseline and record the result once; None if not observed."""
    path = baseline_path(db, assignment)
    before = _baseline(db, assignment)
    if before is None:
        return None
    with closing(ledger.Ledger(db, create=False, read_only=True)) as store:
        item = store.activity(assignment)
    if item["status"] == "active":
        raise ledger.LedgerError(f"Assignment {assignment} is still active; observe it after it ends")
    if item["changes"] is not None:  # Already recorded (for example by a concurrent reap): drop the baseline.
        path.unlink(missing_ok=True)
        return item
    record: dict[str, Any] | None = None
    if "error" in before:
        record = unobserved(item["mode"], str(before["error"]))
    elif before.get("format") != FORMAT:
        record = unobserved(item["mode"], "baseline from another Hipson version")
    else:
        try:
            snapshot, stamp = after or (take(before["root"], Caps(**before["caps"]), exclude=before.get("exclude", [])),
                                        ledger._timestamp())
            with closing(ledger.Ledger(db, create=False, read_only=True)) as store:
                others = store.overlapping_writers(assignment, until=stamp)
            record = classify(before, snapshot, mode=item["mode"], scope=item["scope"], others=others)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            record = unobserved(item["mode"], f"observation failed: {type(exc).__name__}")
    with closing(ledger.Ledger(db, create=False)) as store:
        recorded = store.record_changes(assignment, record, findings(record, assignment))
    path.unlink(missing_ok=True)
    return recorded


def pending(db: Path) -> list[str]:
    """Assignments with a stored baseline (for example reaped while their supervisor was gone)."""
    try:
        return sorted(item.stem for item in _directory(db).iterdir()
                      if item.suffix == ".json" and item.stem.startswith("A-"))
    except OSError:
        return []


def complete_pending(db: Path) -> None:
    """Record observations for ended assignments whose baseline is still waiting (reaped or orphan-recovered)."""
    for assignment in pending(db):
        try:
            complete(db, assignment)
        except (ledger.LedgerError, OSError, sqlite3.Error):
            continue  # Still active, or not in this ledger.


def summary(record: dict[str, Any] | None) -> str:
    """One line for task show; an incomplete or failed observation never reads as clean."""
    if not record:
        return ""
    if record["status"] == "unobserved":
        return f"unobserved ({record['uncertainty']})"
    parts = [f"{group.replace('_', ' ')}: " + ", ".join(f"{count} {change}" for change, count in counts.items() if count)
             for group, counts in record["counts"].items() if any(counts.values())]
    if record["status"] == "incomplete":
        return (f"incomplete, not a clean result ({'; '.join(record['incomplete_reasons'][:3])})"
                + (f"; observed {'; '.join(parts)}" if parts else ""))
    return f"{record['status']}: {'; '.join(parts) or 'no changes observed'}"
