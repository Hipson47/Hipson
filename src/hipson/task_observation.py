"""Bounded OS-level observation of what changed in a project while an assignment ran (opt-in).

A snapshot taken before launch is compared with one taken after the worker exits, on any path including
reap: path, size, mtime and (within caps) content hash, independent of Git and of what the worker reports.
The result is an observation, not attribution: the lead or another process may have written too. Changes in
paths claimed by assignments that overlapped in time are recorded as unattributed instead of blamed on this
worker. A truncated observation is reported as incomplete and never as clean.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hipson import ledger
from hipson.redaction import is_sensitive_path, redact_text
from hipson.task_verification import _TOOL_CACHE_DIRS

FORMAT = 1
MAX_FILES = 20_000
MAX_HASH_BYTES = 256 * 1024 * 1024
FILE_HASH_LIMIT = 8 * 1024 * 1024
MAX_LISTED = 200
# Version control, tool caches and local environments: a worker installing dependencies is not a scoped edit.
EXCLUDED_DIRS = frozenset({".git", ".venv", "venv", "node_modules", *_TOOL_CACHE_DIRS})
CHANGES = ("created", "modified", "deleted")
UNCERTAINTY = ("Filesystem observation of the project tree before and after the run, not attribution: the lead or "
               "another process may also have written. Changes in paths claimed by overlapping assignments are "
               "unattributed.")
# Case-insensitive defaults on Windows and macOS: scope comparison follows the filesystem, not the spelling.
_FOLD = sys.platform in ("win32", "darwin")


@dataclass(frozen=True)
class Caps:
    max_files: int = MAX_FILES
    max_hash_bytes: int = MAX_HASH_BYTES

    def __post_init__(self) -> None:
        for name, value in (("max_files", self.max_files), ("max_hash_bytes", self.max_hash_bytes)):
            if type(value) is not int or value < 1:
                raise ValueError(f"Observation {name} must be a positive integer")


def _key(path: str) -> str:
    return path.casefold() if _FOLD else path


def _order(path: str) -> tuple[str, ...]:
    # The walk visits names sorted per directory, which is lexicographic order on path components.
    return tuple(path.split("/"))


def _walk(directory: str, prefix: str) -> Iterator[tuple[str, os.DirEntry[str]]]:
    try:
        with os.scandir(directory) as listing:
            items = sorted(listing, key=lambda entry: entry.name)
    except OSError:
        return
    for entry in items:
        relative = prefix + entry.name
        try:
            directory_entry = entry.is_dir(follow_symlinks=False)
        except OSError:
            continue
        if directory_entry:
            if entry.name not in EXCLUDED_DIRS:
                yield from _walk(entry.path, relative + "/")
        else:
            yield relative, entry


def _digest(path: str) -> str | None:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def take(project: str | Path, caps: Caps | None = None) -> dict[str, Any]:
    """Snapshot paths (never contents) under the project; .git and known tool caches are excluded."""
    caps = caps or Caps()
    root = Path(project).resolve()
    entries: dict[str, list[Any]] = {}
    hashed = 0
    last = None
    truncated = False
    for relative, entry in _walk(str(root), ""):
        if len(entries) >= caps.max_files:
            truncated = True
            break
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        digest = None
        if stat.S_ISLNK(info.st_mode):
            kind = "link"
            try:
                digest = hashlib.sha256(os.fsencode(os.readlink(entry.path))).hexdigest()
            except OSError:
                digest = None
        elif stat.S_ISREG(info.st_mode):
            kind = "file"
            # Secret files are compared by size and mtime only: no digest of their content is kept anywhere.
            if (info.st_size <= FILE_HASH_LIMIT and hashed + info.st_size <= caps.max_hash_bytes
                    and not is_sensitive_path(relative)):
                digest = _digest(entry.path)
                hashed += info.st_size
        else:
            kind = "other"
        entries[relative] = [kind, info.st_size, info.st_mtime_ns, digest]
        last = relative
    return {"format": FORMAT, "root": str(root), "entries": entries, "truncated": truncated,
            "last": last if truncated else None, "hashed_bytes": hashed,
            "caps": {"max_files": caps.max_files, "max_hash_bytes": caps.max_hash_bytes}}


def diff(before: dict[str, Any], after: dict[str, Any]) -> tuple[dict[str, list[str]], list[str], bool]:
    """Created/modified/deleted paths, paths judged by metadata only, and whether the comparison is partial."""
    bounds = [_order(snap["last"]) for snap in (before, after) if snap["truncated"] and snap["last"]]
    limit = min(bounds) if bounds else None
    truncated = bool(before["truncated"] or after["truncated"])

    def within(path: str) -> bool:
        return limit is None or _order(path) <= limit

    old, new = before["entries"], after["entries"]
    changes: dict[str, list[str]] = {name: [] for name in CHANGES}
    metadata: list[str] = []
    for path in sorted(set(old) | set(new), key=_order):
        if not within(path):
            continue
        if path not in old:
            changes["created"].append(path)
        elif path not in new:
            changes["deleted"].append(path)
        else:
            (kind, size, mtime, digest), (kind2, size2, mtime2, digest2) = old[path], new[path]
            if kind != kind2:
                changes["modified"].append(path)
            elif digest is not None and digest2 is not None:
                if digest != digest2:  # Equal content after an edit and revert is no change.
                    changes["modified"].append(path)
            elif (size, mtime) != (size2, mtime2):
                changes["modified"].append(path)
                metadata.append(path)
    return changes, metadata, truncated


def _covered(path: str, scope: Sequence[str]) -> bool:
    key = _key(path)
    return any(item == "." or key == _key(item) or key.startswith(_key(item).rstrip("/") + "/") for item in scope)


def classify(before: dict[str, Any], after: dict[str, Any], *, mode: str, scope: Sequence[str],
             others: Sequence[tuple[str, Sequence[str]]]) -> dict[str, Any]:
    """Bounded observation record: rw changes inside the assignment's scope are expected, everything else is not."""
    changes, metadata, truncated = diff(before, after)
    counts = {group: dict.fromkeys(CHANGES, 0) for group in ("in_scope", "out_of_scope", "unattributed")}
    listed: list[dict[str, str]] = []
    concurrent: set[str] = set()
    for change in CHANGES:
        for path in changes[change]:
            holders = [assignment for assignment, claimed in others if _covered(path, claimed)]
            if mode == "rw" and _covered(path, scope):
                group = "in_scope"
            elif holders:
                group = "unattributed"
                concurrent.update(holders)
            else:
                group = "out_of_scope"
            counts[group][change] += 1
            listed.append({"path": redact_text(path), "change": change, "attribution": group,
                           "evidence": "metadata" if path in metadata else "content" if change == "modified" else "presence"})
    # Out-of-scope paths first: they are what the lead must look at.
    listed.sort(key=lambda item: (item["attribution"] != "out_of_scope", item["attribution"], item["path"]))
    total = sum(sum(group.values()) for group in counts.values())
    return {
        "status": "incomplete" if truncated else "changes" if total else "clean",
        "observed_at": ledger._timestamp(), "mode": mode, "truncated": truncated,
        "files_before": len(before["entries"]), "files_after": len(after["entries"]),
        "counts": counts, "paths": listed[:MAX_LISTED], "omitted": max(0, len(listed) - MAX_LISTED),
        "concurrent_assignments": sorted(concurrent), "caps": before["caps"],
        "excluded_dirs": sorted(EXCLUDED_DIRS), "uncertainty": UNCERTAINTY,
    }


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


def baseline_path(db: Path, assignment: str) -> Path:
    return Path(db).parent / "observations" / f"{assignment}.json"


def start(db: Path, assignment: str, project: str | Path, caps: Caps) -> None:
    """Take and privately store the pre-run snapshot, so reap can still compare it if the supervisor dies."""
    snapshot = take(project, caps)
    target = baseline_path(db, assignment)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{assignment}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(snapshot, handle)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def complete(db: Path, assignment: str) -> ledger.Assignment | None:
    """Compare a finished assignment with its stored baseline and record the result once; None if not observed."""
    path = baseline_path(db, assignment)
    record: dict[str, Any] | None = None
    error = ""
    try:
        before = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        before = None
        error = f"baseline unreadable: {type(exc).__name__}"
    with closing(ledger.Ledger(db, create=False, read_only=True)) as store:
        item = store.activity(assignment)
        others = store.overlapping_writers(assignment)
    if item["status"] == "active":
        raise ledger.LedgerError(f"Assignment {assignment} is still active; observe it after it ends")
    if item["changes"] is not None:  # Already recorded (for example by a concurrent reap): drop the baseline.
        path.unlink(missing_ok=True)
        return item
    if before is not None:
        try:
            after = take(before["root"], Caps(**before["caps"]))
            record = classify(before, after, mode=item["mode"], scope=item["scope"], others=others)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            error = f"observation failed: {type(exc).__name__}"
    if record is None:
        record = {"status": "unobserved", "observed_at": ledger._timestamp(), "mode": item["mode"],
                  "truncated": False, "files_before": 0, "files_after": 0,
                  "counts": {group: dict.fromkeys(CHANGES, 0) for group in ("in_scope", "out_of_scope", "unattributed")},
                  "paths": [], "omitted": 0, "concurrent_assignments": [], "caps": {}, "excluded_dirs": [],
                  "uncertainty": f"{error}; project changes are unknown."}
    with closing(ledger.Ledger(db, create=False)) as store:
        recorded = store.record_changes(assignment, record, findings(record, assignment))
    path.unlink(missing_ok=True)
    return recorded


def pending(db: Path) -> list[str]:
    """Assignments with a stored baseline (for example reaped while their supervisor was gone)."""
    directory = Path(db).parent / "observations"
    try:
        return sorted(item.stem for item in directory.iterdir()
                      if item.suffix == ".json" and item.stem.startswith("A-"))
    except OSError:
        return []


def summary(record: dict[str, Any] | None) -> str:
    """One line for task show; a truncated or failed observation never reads as clean."""
    if not record:
        return ""
    if record["status"] == "unobserved":
        return f"unobserved ({record['uncertainty']})"
    parts = [f"{group.replace('_', ' ')}: " + ", ".join(f"{count} {change}" for change, count in counts.items() if count)
             for group, counts in record["counts"].items() if any(counts.values())]
    if record["truncated"]:
        # Never "no changes": beyond the cap nothing was compared.
        return (f"{record['status']}: INCOMPLETE, stopped at {record['caps'].get('max_files')} files, not a clean "
                "result" + (f"; before the cap {'; '.join(parts)}" if parts else ""))
    return f"{record['status']}: {'; '.join(parts) or 'no changes observed'}"
