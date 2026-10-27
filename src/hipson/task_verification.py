"""Lead-run verification bound to raw project content, never to a worker report."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import stat
import subprocess
import sys
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

from hipson import ledger, task_activity
from hipson.display import escape_display_controls

OUTPUT_LIMIT = 8192
_TOOL_CACHE_DIRS = frozenset({
    '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache', '.hypothesis', '.tox', '.nox', '.gradle',
})


def _is_tool_cache(path: Path, *, directory: bool = False) -> bool:
    """Exclude known untracked tool caches, never generic build/target trees."""
    parts = path.parts if directory else path.parts[:-1]
    return (any(part in _TOOL_CACHE_DIRS for part in parts)
            or any(left == 'node_modules' and right == '.cache'
                   for left, right in zip(parts, parts[1:], strict=False))
            or (not directory and (path.name == '.coverage' or path.name.endswith(('.pyc', '.pyo')))))


def _walk_error(error: OSError) -> None:
    raise error


def _fingerprint(files: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256(b'hipson-raw-tree-v2\0')
    for item in files:
        digest.update(os.fsencode(item['path']) + b'\0' + item['state'].encode() + b'\0'
                      + (item['index'] or '-').encode() + b'\0' + str(item['tracked']).encode() + b'\0')
    return digest.hexdigest()


def _changes(before: dict[str, Any], after: dict[str, Any], generated: set[str]) -> list[str]:
    """Describe content/index changes; command-created untracked paths are observations."""
    old = {item['path']: item for item in before['files']}
    new = {item['path']: item for item in after['files']
           if item['path'] not in generated or item['tracked']}
    changes = []
    for path in sorted(old.keys() | new.keys()):
        left, right = old.get(path), new.get(path)
        if left == right:
            continue
        details = []
        for field in ('state', 'index', 'tracked'):
            previous, current = left.get(field) if left else None, right.get(field) if right else None
            if previous != current:
                details.append(f'{field} before={previous} after={current}')
        display_path = escape_display_controls(task_activity.safe_text(path)).replace('<', '&lt;').replace('>', '&gt;')
        changes.append(f'{display_path}: ' + ', '.join(details))
    return changes


def _filter_overrides(project: str) -> list[str]:
    """Disable configured drivers while retaining Git's built-in text/EOL conversion."""
    from hipson.lead_context import _git

    names = _git(project, 'config', '--null', '--name-only', '--list')
    if names is None:
        raise ValueError('Git filter configuration is unavailable or exceeds its read limit')
    overrides: list[str] = []
    for key in sorted(set(names.split(b'\0')[:-1])):
        if key.startswith(b'filter.') and key.rsplit(b'.', 1)[-1] in (b'clean', b'smudge', b'process', b'required'):
            value = 'false' if key.endswith(b'.required') else ''
            overrides.extend(('-c', os.fsdecode(key) + '=' + value))
    return overrides


def project_state(project: str, db: Path, *, generated: set[str] | None = None,
                  covered: set[str] | None = None) -> dict[str, Any]:
    """Bind raw content; use Git's built-in normalization only for the dirty flag.

    A raw content inventory is used instead of `git diff HEAD`: even --no-textconv
    diff can execute clean/process filters. Disable drivers before asking Git to hash
    normalized content. Never follow directory symlinks. Gitlinks and unreadable/special
    files fail closed rather than silently omitting content.
    """
    from hipson.lead_context import _git

    root = Path(project).resolve()
    result: dict[str, Any] = {'revision': 'none', 'subject': None, 'fingerprint': None,
                              'dirty': None, 'error': None, 'files': [], 'generated_files': []}
    try:
        if not root.is_dir():
            raise ValueError('Project directory is unavailable')
        top = _git(project, 'rev-parse', '--show-toplevel')
        git = top is not None
        index_dirty = False
        if not git and any((parent / '.git').is_file() or (parent / '.git' / 'HEAD').is_file()
                           for parent in (root, *root.parents)):
            raise ValueError('Git repository is present but its state is unavailable')
        head = _git(project, 'rev-parse', '--verify', 'HEAD') if git else None
        if top is not None:
            if not head:
                raise ValueError('Git HEAD is unavailable (commit the initial revision first)')
            result['revision'] = head.decode('ascii').strip()
            subject = _git(project, 'show', '--no-patch', '--no-show-signature', '--format=%s', 'HEAD', '--')
            result['subject'] = task_activity.safe_text(subject.decode('utf-8', errors='replace').strip()) if subject else None
            # A registered subdirectory still binds the entire repository's state.
            top_path = os.fsdecode(top).removesuffix('\n')
            if sys.platform == 'win32':
                top_path = top_path.removesuffix('\r')
            root = Path(top_path).resolve()
            tree = _git(str(root), 'ls-tree', '-r', '-z', '--full-tree', 'HEAD')
            index = _git(str(root), 'ls-files', '--stage', '-z')
            others = _git(str(root), 'ls-files', '--others', '--exclude-standard', '-z')
            if tree is None or index is None or others is None:
                raise ValueError('Git content inventory is unavailable or exceeds its read limit')
            baseline = {}
            for row in tree.split(b'\0'):
                if row:
                    metadata, name = row.split(b'\t', 1)
                    mode, kind, blob = metadata.split()
                    if kind != b'blob':
                        raise ValueError('Git submodules require separate verification; project fingerprint unavailable')
                    baseline[name] = (mode, blob)
            paths = set(baseline)
            indexed = set()
            index_entries: dict[bytes, bytes] = {}
            for row in index.split(b'\0'):
                if row:
                    metadata, name = row.split(b'\t', 1)
                    if metadata.startswith(b'160000 '):
                        raise ValueError('Git submodules require separate verification; project fingerprint unavailable')
                    mode, blob, stage = metadata.split()
                    index_dirty |= stage != b'0' or baseline.get(name) != (mode, blob)
                    indexed.add(name)
                    paths.add(name)
                    index_entries[name] = index_entries.get(name, b'') + metadata + b'\0'
            index_dirty |= indexed != baseline.keys()
            paths.update(others.split(b'\0')[:-1])
        else:
            baseline, index_entries, indexed = {}, {}, set()
            paths = set()
            for directory, dirs, walk_files in os.walk(root, followlinks=False, onerror=_walk_error):
                for entry in list(dirs):
                    path = Path(directory) / entry
                    if _is_tool_cache(path.relative_to(root), directory=True):
                        dirs.remove(entry)
                    elif path.is_symlink():
                        paths.add(os.fsencode(path.relative_to(root).as_posix()))
                        dirs.remove(entry)
                paths.update(os.fsencode((Path(directory) / name).relative_to(root).as_posix()) for name in walk_files)
        files: list[dict[str, Any]] = []
        dirty = index_dirty
        filter_overrides: list[str] | None = None
        excluded = {db, Path(str(db) + '-wal'), Path(str(db) + '-shm'), Path(str(db) + '-journal')}
        for name in sorted(paths):
            relative = Path(os.fsdecode(name))
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('Invalid repository path in content inventory')
            path = root / relative
            if path in excluded:
                continue  # The verification's own ledger writes must not invalidate it.
            tracked = name in baseline or name in indexed
            filename = os.fsdecode(name)
            if not tracked and _is_tool_cache(relative):
                continue  # Earlier test runs' caches must not become covered project content.
            if not tracked and ((covered is not None and filename not in covered)
                                or (generated is not None and filename in generated)):
                # Observe artifacts without opening them: FIFOs, unreadable output,
                # and artifacts still being written cannot affect covered content.
                result['generated_files'].append(filename)
                continue
            # A tracked directory can have been replaced by a symlink to outside the tree.
            if any(parent.is_symlink() for parent in path.parents if parent != root and parent.is_relative_to(root)):
                raise ValueError('Directory symlink prevents contained project fingerprinting')
            try:
                info = path.lstat()
            except FileNotFoundError:
                files.append({'path': filename, 'state': 'missing',
                              'index': hashlib.sha256(index_entries[name]).hexdigest() if name in index_entries else None,
                              'tracked': tracked})
                dirty = True
                continue
            if stat.S_ISLNK(info.st_mode):
                content = os.fsencode(os.readlink(path))
                mode = b'120000'
                size = len(content)
            elif stat.S_ISREG(info.st_mode):
                content = None
                size = info.st_size
                mode = b'100755' if os.name == 'posix' and info.st_mode & stat.S_IXUSR else b'100644'
            else:
                raise ValueError('Special file prevents project fingerprinting')
            # The acceptance fingerprint always covers raw bytes, including line endings.
            algorithm = 'sha256' if len(str(result['revision'])) == 64 else 'sha1'
            blob_hash = hashlib.new(algorithm, usedforsecurity=False)
            blob_hash.update(f'blob {size}\0'.encode())
            file_hash = hashlib.sha256()
            if content is not None:
                blob_hash.update(content)
                file_hash.update(content)
            else:
                with path.open('rb') as stream:
                    while chunk := stream.read(65536):
                        blob_hash.update(chunk)
                        file_hash.update(chunk)
            blob = blob_hash.hexdigest().encode()
            if git and mode != b'120000' and name in baseline and baseline[name][0] == mode \
                    and baseline[name][1] != blob:
                # A CRLF checkout can differ from its LF blob without being dirty.
                # Never let hash-object run a repository-configured clean/process helper.
                if filter_overrides is None:
                    filter_overrides = _filter_overrides(str(root))
                normalized = _git(str(root), *filter_overrides, 'hash-object', '--', filename)
                if normalized is None:
                    raise ValueError('Git normalized content hash is unavailable')
                blob = normalized.strip()
            if content is None:
                after = path.lstat()
                if (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
                        after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise ValueError('Project changed while fingerprinting')
            files.append({'path': filename, 'state': mode.decode() + ':' + file_hash.hexdigest(),
                          'index': hashlib.sha256(index_entries[name]).hexdigest() if name in index_entries else None,
                          'tracked': tracked})
            dirty |= baseline.get(name) != (mode, blob)
        result.update(files=files, fingerprint=_fingerprint(files), dirty=dirty if git else bool(files))
        if git and _git(project, 'rev-parse', '--verify', 'HEAD') != head:
            raise ValueError('HEAD moved while fingerprinting')
    except (OSError, ValueError, UnicodeError) as exc:
        result.update(fingerprint=None, error=task_activity.safe_text(exc))
    return result


def verification_state(records: list[dict[str, Any]], current: dict[str, Any], builder_seq: int) -> dict[str, Any]:
    def reasons(item: dict[str, Any]) -> list[str]:
        errors = []
        if item['exit_code'] != 0:
            errors.append(f"failing verification: exit {item['exit_code']}")
        if item['builder_finished_seq'] < builder_seq:
            errors.append('stale: verification predates latest finished builder assignment')
        if item['revision'] != current['revision']:
            errors.append(f"stale: HEAD moved; before={item['revision']} after={current['revision']}")
        if not current['fingerprint'] or not item['fingerprint']:
            errors.append('unavailable working-tree fingerprint: ' +
                          (current.get('error') or item.get('error') or 'unknown'))
        elif 'files' in item:
            changes = _changes(item, current, set(item.get('generated_files', [])))
            if changes:
                errors.append('stale: working tree changed since verification: ' + '; '.join(changes[:20])
                              + (f'; {len(changes) - 20} more changed paths' if len(changes) > 20 else ''))
        elif item['fingerprint'] != current['fingerprint']:
            errors.append('stale: working tree changed; before=' + item['fingerprint'] + ' after=' + current['fingerprint'])
        if item['state_changed']:
            errors.append('stale: project changed during verification: ' + '; '.join(item.get('changes', [])[:20])
                          if item.get('changes') else 'stale: project changed during verification or cleanup was not confirmed')
        if item.get('cleanup_confirmed') is False:
            errors.append('verification process cleanup was not confirmed')
        return errors

    latest = records[-1] if records else None
    accepted = latest if latest and not reasons(latest) else None
    errors = [] if accepted else reasons(latest) if latest else ['missing lead verification; run hipson task verify']
    freshness = ('current' if accepted else 'missing' if latest is None else 'stale'
                 if any('stale:' in reason for reason in errors) else 'failing' if latest['exit_code'] != 0 else 'unknown')
    return {'freshness': freshness, 'passing': accepted is not None, 'reasons': errors,
            'accepted': accepted['id'] if accepted else None, 'latest': latest, 'current': current,
            'untrusted_fields': ['latest.command', 'latest.label', 'latest.subject', 'latest.output_tail',
                                 'latest.files', 'latest.generated_files', 'latest.changes', 'current.subject',
                                 'current.files', 'current.generated_files']}


def configure(parser: argparse.ArgumentParser) -> None:
    parser.set_defaults(func=command)
    parser.add_argument('--task', required=True)
    parser.add_argument('--label', default='')
    parser.add_argument('--timeout', type=float, default=300)
    parser.add_argument('worker_command', nargs=argparse.REMAINDER, help='-- COMMAND [ARGS...] (no shell)')


def validate(command: list[str], timeout: float) -> None:
    if not command or any(not isinstance(item, str) or '\0' in item for item in command) or not command[0].strip():
        raise ledger.LedgerError('Verification requires a nonempty command argv without NULs')
    if not math.isfinite(timeout) or not 0 < timeout <= 86400:
        raise ledger.LedgerError('Verification timeout must be positive, finite and at most 86400 seconds')


def command(args: argparse.Namespace) -> int:
    from hipson import task_runner
    try:
        if not args.worker_command or args.worker_command[0] != '--':
            raise ledger.LedgerError('A verification command after -- is required')
        argv = args.worker_command[1:]
        validate(argv, args.timeout)
        db = Path(args.db).expanduser().resolve() if args.db else ledger.default_ledger_db()
        with closing(ledger.Ledger(db, create=False, read_only=True)) as store:
            project = store.show(args.task)['project']
        # Use the worker runner's private supervisor and cross-platform containment.
        args.verification_id = 'V-' + uuid.uuid4().hex
        args.label = task_activity.safe_text(args.label)
        args.rollout = None
        check: dict[str, object] = {'expected': project}
        code = task_runner._execute(db, args, argv, False, check, 1.0)
        with closing(ledger.Ledger(db, create=False, read_only=True)) as store:
            records = store.verifications(args.task)
        record = next((item for item in records if item['id'] == args.verification_id), None)
        if record is None:
            raise ledger.LedgerError(f'Verification evidence unavailable (command exit {code}); closure remains gated')
        print(json.dumps(record, ensure_ascii=True) if args.json else
              f"{record['id']} exit={record['exit_code']} duration={record['duration_seconds']:.3f}s "
              f"revision={record['revision']} fingerprint={record['fingerprint']} changed={record['state_changed']}\n"
              + '<untrusted_data name="verification_output">\n'
              + escape_display_controls(record['output_tail']).replace('<', '&lt;').replace('>', '&gt;')
              + '\n</untrusted_data>')
        return code
    except (ledger.LedgerError, OSError, sqlite3.Error) as exc:
        print(task_activity.safe_text(exc), file=sys.stderr)
        return 1


def run(db: Path, task: str, command: list[str], *, label: str = '', timeout: float = 300) -> dict[str, Any]:
    """MCP uses a private CLI supervisor too, without changing its server process group."""
    validate(command, timeout)
    invocation = [sys.executable, '-m', 'hipson.cli', 'task', 'verify', '--db', str(db), '--task', task,
                  '--label', label, '--timeout', str(timeout), '--json', '--', *command]
    try:
        process = subprocess.run(invocation, capture_output=True, timeout=timeout + 30, check=False)
    except subprocess.SubprocessError as exc:
        raise ledger.LedgerError('Verification supervisor did not finish within its execution budget') from exc
    try:
        return json.loads(process.stdout)
    except (ValueError, UnicodeError) as exc:
        raise ledger.LedgerError(task_activity.safe_text(process.stderr.decode(errors='replace')) or
                                 'Verification did not return recorded evidence') from exc


def finish(db: Path, args: argparse.Namespace, code: int, started_at: str, started: float,
           before: dict[str, Any], builder_seq: int, snapshot: dict[str, Any], project: str, safe: bool) -> None:
    covered = {item['path'] for item in before['files']}
    after = project_state(project, db, covered=covered)
    generated = set(after['generated_files'])
    changes = _changes(before, after, generated)
    if before['revision'] != after['revision']:
        changes.append(f"HEAD before={before['revision']} after={after['revision']}")
    if not before['fingerprint'] or not after['fingerprint']:
        changes.append(f"fingerprint unavailable; before={before['error']} after={after['error']}")
    output = '\n'.join(item['text'] for item in snapshot.get('observations', []))
    output = task_activity.safe_text(output, 65536)[-OUTPUT_LIMIT:]
    record = {'id': args.verification_id, 'task': args.task, 'label': args.label,
              'command': [task_activity.safe_text(arg, 4096) for arg in args.worker_command[1:]],
              'exit_code': code, 'duration_seconds': max(0, time.monotonic() - started),
              'started_at': started_at, 'finished_at': ledger._timestamp(),
              **before, 'state_changed': bool(changes), 'builder_finished_seq': builder_seq, 'output_tail': output,
              'generated_files': sorted(generated), 'changes': changes, 'cleanup_confirmed': safe,
              'untrusted_fields': ['label', 'command', 'subject', 'output_tail', 'error', 'files', 'generated_files', 'changes']}
    with closing(ledger.Ledger(db, create=False)) as store:
        store._record_verification(record)
