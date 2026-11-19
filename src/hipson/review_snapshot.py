"""Immutable review snapshots: a reviewer reads a private copy of the exact candidate lead verification accepted.

A live working tree can change while a reviewer reads it, and can change back before the review ends, so equal
fingerprints at the start and end of a review do not prove what the reviewer saw. A snapshot removes the race:
`create` copies every covered file from the very bytes it hashes (task_verification.project_state with copy_to),
so its fingerprint is the copy's own and is compared with the accepted verification before any reviewer starts.
The copy is a private directory with read-only files; `verify` re-hashes it from its manifest when the review
starts and ends, so a missing, replaced or edited snapshot is detected instead of trusted.

In a Git project the copy is also a small repository whose HEAD is the candidate's base commit, read through
alternates from the project's object store (nothing is written to the project), so `git diff HEAD` and status in
the snapshot show the candidate change the way they would in the project. Project-scoped trust is per path: a
runtime may not apply its project-local configuration to a snapshot directory (see docs/AGENT_RUNTIMES.md).

The guarantee is about what Hipson supplies (working directory, brief, recorded identity). A reviewer that reads
the live project by absolute path is outside it; actor labels are not authenticated.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Any

from hipson import task_verification
from hipson.lead_context import _git

KIND = 'hipson.review_snapshot'
MANIFEST = 'manifest.json'
TREE = 'tree'
_READ_ONLY = ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)


class SnapshotError(ValueError):
    """A snapshot could not be created or no longer matches its manifest; the review cannot proceed."""


def tree(directory: Path) -> Path:
    return Path(directory) / TREE


def create(project: str, db: Path, directory: Path) -> dict[str, Any]:
    """Copy the current candidate into a new private `directory`; return its manifest (also written there)."""
    directory = Path(directory)
    if directory.exists() or directory.is_symlink():
        raise SnapshotError(f'snapshot directory {directory} already exists')
    directory.mkdir(mode=0o700)
    try:
        copy = tree(directory)
        copy.mkdir(mode=0o700)
        state = task_verification.project_state(project, db, copy_to=copy)
        if not state['fingerprint']:
            raise SnapshotError(f"the candidate cannot be snapshotted: {state['error'] or 'fingerprint unavailable'}")
        if state['revision'] != 'none':
            _repository(project, copy, state['revision'])
        _seal(copy)
        manifest = {'artifact_kind': KIND, 'schema_version': '1.0', 'revision': state['revision'],
                    'fingerprint': state['fingerprint'], 'workdir': _workdir(project, state['revision']),
                    'entries': state['entries']}
        path = directory / MANIFEST
        path.write_text(json.dumps(manifest, ensure_ascii=True, sort_keys=True), encoding='utf-8')
        path.chmod(0o400)
        return manifest
    except BaseException:
        remove(directory)
        raise


def _workdir(project: str, revision: str) -> str:
    """The project's place in the copy: a task registered on a repository subdirectory binds the whole repository."""
    if revision == 'none':
        return '.'
    top = _git(project, 'rev-parse', '--show-toplevel')
    if top is None:
        raise SnapshotError('could not locate the repository root of the project')
    root = Path(os.fsdecode(top).rstrip('\r\n')).resolve()
    try:
        return Path(project).resolve().relative_to(root).as_posix()
    except ValueError as exc:  # A path spelling Git and the OS disagree on: fail closed, never crash.
        raise SnapshotError('the project is not inside the repository Git reports for it') from exc


def workdir(directory: Path, manifest: dict[str, Any]) -> Path:
    """Where a reviewer starts: the snapshot's copy of the task project."""
    relative = Path(str(manifest.get('workdir') or '.'))
    if relative.is_absolute() or '..' in relative.parts:
        raise SnapshotError('snapshot manifest names a working directory outside the snapshot')
    return tree(directory) / relative


def _repository(project: str, copy: Path, revision: str) -> None:
    """Make the copy a repository whose HEAD and index are the base commit, sharing the project's objects."""
    objects = _git(project, 'rev-parse', '--path-format=absolute', '--git-path', 'objects')
    if objects is None or _git(str(copy), 'init', '--quiet', '--template=') is None:
        raise SnapshotError('could not initialise the snapshot repository')
    source = objects.decode('utf-8', errors='surrogateescape').strip()
    (copy / '.git' / 'objects' / 'info').mkdir(parents=True, exist_ok=True)
    (copy / '.git' / 'objects' / 'info' / 'alternates').write_text(source + '\n', encoding='utf-8')
    # Line-ending settings decide how Git compares the copied bytes with the base commit: without the project's
    # own, every CRLF file of a Windows checkout would show as changed in the reviewer's diff.
    for key in ('core.autocrlf', 'core.eol', 'core.safecrlf'):
        value = _git(project, 'config', '--get', key)
        if value is not None and _git(str(copy), 'config', key, value.decode('utf-8', 'replace').strip()) is None:
            raise SnapshotError(f'could not copy {key} into the snapshot repository')
    attributes = _git(project, 'rev-parse', '--path-format=absolute', '--git-path', 'info/attributes')
    if attributes is not None:
        source_attributes = Path(attributes.decode('utf-8', errors='surrogateescape').strip())
        if source_attributes.is_file() and not source_attributes.is_symlink():
            (copy / '.git' / 'info').mkdir(exist_ok=True)
            (copy / '.git' / 'info' / 'attributes').write_bytes(source_attributes.read_bytes())
    if (_git(str(copy), 'update-ref', '--no-deref', 'HEAD', revision) is None
            or _git(str(copy), 'read-tree', revision) is None):
        raise SnapshotError(f'could not set the snapshot repository to {revision}')


def _seal(copy: Path) -> None:
    """Read-only files outside the snapshot's .git; directories stay writable for caches a reviewer's tools make."""
    for root, dirs, files in os.walk(copy, followlinks=False):
        if Path(root) == copy and '.git' in dirs:
            dirs.remove('.git')
        for name in files:
            path = Path(root) / name
            if not path.is_symlink():
                path.chmod(stat.S_IMODE(path.lstat().st_mode) & _READ_ONLY)


def load(directory: Path) -> dict[str, Any]:
    try:
        manifest = json.loads((Path(directory) / MANIFEST).read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError) as exc:
        raise SnapshotError(f'snapshot manifest unreadable: {type(exc).__name__}') from exc
    if (not isinstance(manifest, dict) or manifest.get('artifact_kind') != KIND
            or not isinstance(manifest.get('entries'), list) or not isinstance(manifest.get('fingerprint'), str)):
        raise SnapshotError('snapshot manifest is not a Hipson review snapshot')
    return manifest


def verify(directory: Path, manifest: dict[str, Any]) -> str:
    """Re-hash the snapshot against its manifest; return the fingerprint, or raise on any difference."""
    copy = tree(directory)
    files = []
    for entry in manifest['entries']:
        if not isinstance(entry, dict) or not isinstance(entry.get('path'), str) or not isinstance(entry.get('state'), str):
            raise SnapshotError('snapshot manifest entry is malformed')
        relative = Path(entry['path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise SnapshotError('snapshot manifest names a path outside the snapshot')
        files.append({'path': entry['path'], 'state': _state(copy / relative)})
        if files[-1]['state'] != entry['state']:
            raise SnapshotError('snapshot content differs from its manifest (missing, replaced or edited file)')
    expected = {entry['path'] for entry in manifest['entries'] if entry['state'] != 'missing'}
    extra = [path for path in _present(copy) if path not in expected
             and not task_verification._is_tool_cache(Path(path))]
    if extra:  # Tool caches (pytest, bytecode) are tolerated; any other new file changes what was reviewed.
        raise SnapshotError(f'snapshot gained {len(extra)} file(s) that are not part of the candidate')
    fingerprint = task_verification._fingerprint(files)
    if fingerprint != manifest['fingerprint']:
        raise SnapshotError('snapshot manifest fingerprint does not match its entries')
    return fingerprint


def _present(copy: Path) -> list[str]:
    """Every file and link in the copy outside its .git, as manifest paths; linked directories are not entered."""
    found = []
    for root, dirs, files in os.walk(copy, followlinks=False):
        if Path(root) == copy and '.git' in dirs:
            dirs.remove('.git')
        for name in list(dirs):
            if (Path(root) / name).is_symlink():
                dirs.remove(name)
                files.append(name)
        found += [(Path(root) / name).relative_to(copy).as_posix() for name in files]
    return found


def _state(path: Path) -> str:
    """The same per-file state project_state binds: mode and SHA-256 of the raw bytes, or missing."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return 'missing'
    if stat.S_ISLNK(info.st_mode):
        return '120000:' + hashlib.sha256(os.fsencode(os.readlink(path))).hexdigest()
    if not stat.S_ISREG(info.st_mode):
        return 'special'
    mode = '100755' if os.name == 'posix' and info.st_mode & stat.S_IXUSR else '100644'
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(65536):
            digest.update(chunk)
    return f'{mode}:{digest.hexdigest()}'


def remove(directory: Path) -> None:
    """Delete a snapshot, read-only files included (Windows refuses to unlink them); a missing one is fine."""
    directory = Path(directory)
    if directory.is_symlink():
        directory.unlink()
        return
    if not directory.exists():
        return

    def writable(function: Any, path: str, _: Any) -> None:
        os.chmod(path, stat.S_IWUSR | stat.S_IRUSR | (stat.S_IXUSR if os.path.isdir(path) else 0))
        function(path)

    for root, dirs, files in os.walk(directory, followlinks=False):
        for name in dirs + files:
            path = Path(root) / name
            if not path.is_symlink():
                try:
                    path.chmod(stat.S_IMODE(path.lstat().st_mode) | stat.S_IWUSR)
                except OSError:
                    pass
    if sys.version_info >= (3, 12):
        shutil.rmtree(directory, onexc=writable)
    else:  # Python 3.11 has only onerror (same arguments for this handler).
        shutil.rmtree(directory, onerror=writable)


def sweep(parent: Path, pattern: str) -> list[Path]:
    """Remove leftover snapshot directories matching `pattern` directly in `parent` (an interrupted review)."""
    removed: list[Path] = []
    if not Path(parent).is_dir():
        return removed
    for path in sorted(Path(parent).glob(pattern)):
        if path.is_dir() and not path.is_symlink():
            remove(path)
            removed.append(path)
    return removed
