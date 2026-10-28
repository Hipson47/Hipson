"""Lead-run verification bound to raw project content, never to a worker report."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shlex
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
from hipson.redaction import redact_text

OUTPUT_LIMIT = 8192
FILE_HASH_LIMIT = 16 * 1024 * 1024
TOTAL_HASH_LIMIT = 128 * 1024 * 1024
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
    """Bind covered paths and working-tree content; index fields are observations only."""
    digest = hashlib.sha256(b'hipson-raw-tree-v3\0')
    for item in files:
        digest.update(os.fsencode(item['path']) + b'\0' + item['state'].encode() + b'\0')
    return digest.hexdigest()


def _path_key(path: str) -> str:
    return hashlib.sha256(os.fsencode(path)).hexdigest()


def _stored_files(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Persist redacted display paths; the raw-path hash keeps identity for freshness comparisons."""
    return [{**item, 'path': redact_text(item['path']), 'key': _path_key(item['path'])} for item in files]


def _changes(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Describe covered path/content changes, including command-created source."""
    # Records from before 1.6.1 stored raw paths without a key; their hash is the same identity.
    old = {item.get('key') or _path_key(item['path']): item for item in before['files']}
    new = {item.get('key') or _path_key(item['path']): item for item in after['files']}
    changes = []
    for key in sorted(old.keys() | new.keys(), key=lambda name: (new.get(name) or old[name])['path']):
        left, right = old.get(key), new.get(key)
        previous, current = left['state'] if left else None, right['state'] if right else None
        if previous == current:
            continue
        path = (right or left or {})['path']
        display_path = escape_display_controls(task_activity.safe_text(path)).replace('<', '&lt;').replace('>', '&gt;')
        changes.append(f'{display_path}: state before={previous} after={current}')
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


def project_state(project: str, db: Path) -> dict[str, Any]:
    """Bind raw content; use Git's built-in normalization only for the dirty flag.

    A raw content inventory is used instead of `git diff HEAD`: even --no-textconv
    diff can execute clean/process filters. Disable drivers before asking Git to hash
    normalized content. Content reads are bounded; larger files bind lstat metadata
    instead, explicitly reported in the file inventory. Never follow directory symlinks.
    Gitlinks and unreadable/special files fail closed rather than silently omitting content.
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
        content_bytes = 0
        metadata_fallback = False
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
            if size > FILE_HASH_LIMIT or size > TOTAL_HASH_LIMIT - content_bytes:
                reason = 'file_limit' if size > FILE_HASH_LIMIT else 'total_limit'
                metadata_fallback = True
                files.append({'path': filename,
                              'state': f'{mode.decode()}:metadata:{reason}:file_limit_bytes={FILE_HASH_LIMIT}:'
                                       f'total_limit_bytes={TOTAL_HASH_LIMIT}:dev={info.st_dev}:ino={info.st_ino}:'
                                       f'size={info.st_size}:mtime_ns={info.st_mtime_ns}:ctime_ns={info.st_ctime_ns}',
                              'index': hashlib.sha256(index_entries[name]).hexdigest() if name in index_entries else None,
                              'tracked': tracked})
                dirty |= name not in baseline or baseline[name][0] != mode
                continue  # Do not let hash-object bypass the content read budget either.
            # Always read content within the budget: Git can trust cached size/mtime
            # even after a same-size edit with restored timestamps. Keep raw line endings.
            algorithm = 'sha256' if len(str(result['revision'])) == 64 else 'sha1'
            blob_hash = hashlib.new(algorithm, usedforsecurity=False)
            blob_hash.update(f'blob {size}\0'.encode())
            file_hash = hashlib.sha256()
            if content is not None:
                blob_hash.update(content)
                file_hash.update(content)
            else:
                with path.open('rb') as stream:
                    remaining = size
                    while remaining:
                        chunk = stream.read(min(65536, remaining))
                        if not chunk:
                            raise ValueError('Project changed while fingerprinting')
                        blob_hash.update(chunk)
                        file_hash.update(chunk)
                        remaining -= len(chunk)
            content_bytes += size
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
        # Unknown content cannot establish a clean checkout, even when its stats match.
        clean_or_unknown = None if metadata_fallback else False
        result.update(files=_stored_files(files), fingerprint=_fingerprint(files),
                      dirty=(True if dirty else clean_or_unknown) if git else bool(files))
        if git and _git(project, 'rev-parse', '--verify', 'HEAD') != head:
            raise ValueError('HEAD moved while fingerprinting')
    except (OSError, ValueError, UnicodeError) as exc:
        result.update(fingerprint=None, error=task_activity.safe_text(exc))
    return result


def argv_identity(argv: list[str]) -> str:
    """Identify the complete executed argv; the stored command is bounded and redacted for display."""
    return hashlib.sha256(json.dumps(argv, ensure_ascii=True).encode('ascii')).hexdigest()


def _fallback_paths(state: dict[str, Any]) -> list[str]:
    return sorted(item['path'] for item in state.get('files', []) if ':metadata:' in item['state'])


def _worker_context(context: dict[str, Any]) -> bool:
    return str(context.get('role') or '').strip().lower() == 'worker' or context.get('assignment') is not None


def invocation_context() -> dict[str, Any]:
    context = {key: os.environ.get('HIPSON_' + key.upper()) for key in ('role', 'assignment', 'task')}
    if _worker_context(context):
        raise ledger.LedgerError('Lead verification is unavailable in a known worker context (HIPSON_ROLE/HIPSON_ASSIGNMENT)')
    return {key: task_activity.safe_text(value) if value is not None else None for key, value in context.items()}


def _freshness(record: dict[str, Any] | None, errors: list[str]) -> str:
    if not errors:
        return 'current'
    if record is None:
        return 'missing'
    if any(reason.startswith('stale:') for reason in errors):
        return 'stale'
    if record['exit_code'] != 0:
        return 'failing'
    return 'partial' if any(reason.startswith('partial content coverage') for reason in errors) else 'unknown'


def verification_state(records: list[dict[str, Any]], current: dict[str, Any], builder_seq: int,
                       lead: str, checks: list[list[str]] | None = None) -> dict[str, Any]:
    """Without declared checks the latest lead verification decides; with them, each check's latest run does."""
    def reasons(item: dict[str, Any]) -> list[str]:
        errors = []
        context = item.get('context')
        if (item.get('actor') != lead or not isinstance(context, dict)
                or set(context) != {'role', 'assignment', 'task'} or _worker_context(context)):
            errors.append('verification was not recorded by the task lead in a lead context')
        if item['exit_code'] != 0:
            errors.append(f"failing verification: exit {item['exit_code']}")
        fallback = sorted(set(_fallback_paths(item) + _fallback_paths(current) + item.get('metadata_fallback_paths', [])))
        if fallback or item.get('coverage') == 'partial':
            errors.append('partial content coverage (metadata fallback): ' + '; '.join(
                escape_display_controls(task_activity.safe_text(path)).replace('<', '&lt;').replace('>', '&gt;')
                for path in fallback))
        if item['builder_finished_seq'] < builder_seq:
            errors.append('stale: verification predates latest finished builder assignment')
        if item['revision'] != current['revision']:
            errors.append(f"stale: HEAD moved; before={item['revision']} after={current['revision']}")
        if not current['fingerprint'] or not item['fingerprint']:
            errors.append('unavailable working-tree fingerprint: ' +
                          (current.get('error') or item.get('error') or 'unknown'))
        elif 'files' in item:
            changes = _changes(item, current)
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
    check_states: list[dict[str, Any]] = []
    if checks:
        for check in checks:
            # Match the full-argv identity: a longer command must not pass as the check it truncates to.
            identity = argv_identity(check)
            record = next((item for item in reversed(records) if item.get('argv_sha256') == identity), None)
            problems = reasons(record) if record else ['missing: never verified; run hipson task verify (no command runs all checks)']
            check_states.append({'command': check, 'record': record['id'] if record else None,
                                 'passing': not problems, 'freshness': _freshness(record, problems), 'reasons': problems})
        errors = [f'check `{shlex.join(state["command"])}`: {reason}'
                  for state in check_states for reason in state['reasons']]
        accepted_ids = [] if errors else [state['record'] for state in check_states]
        failed = next((state for state in check_states if not state['passing']), None)
        freshness = failed['freshness'] if failed else 'current'
    else:
        problems = reasons(latest) if latest else ['missing lead verification; run hipson task verify']
        errors = problems
        accepted_ids = [latest['id']] if latest and not problems else []
        freshness = _freshness(latest, problems)
    return {'freshness': freshness, 'passing': bool(accepted_ids), 'reasons': errors,
            'accepted': accepted_ids[-1] if accepted_ids else None, 'accepted_ids': accepted_ids,
            'checks': check_states, 'latest': latest, 'current': current,
            'untrusted_fields': ['latest.actor', 'latest.context', 'latest.metadata_fallback_paths',
                                 'latest.command', 'latest.label', 'latest.subject', 'latest.output_tail',
                                 'latest.files', 'latest.generated_files', 'latest.changes', 'current.subject',
                                 'current.files', 'current.generated_files']}


def configure(parser: argparse.ArgumentParser) -> None:
    parser.set_defaults(func=command)
    parser.add_argument('--task', required=True)
    parser.add_argument('--label', default='')
    parser.add_argument('--actor', help='Invoking actor label (default: task lead); not authenticated')
    parser.add_argument('--timeout', type=float, default=300)
    parser.add_argument('worker_command', nargs=argparse.REMAINDER,
                        help="-- COMMAND [ARGS...] (no shell); omit to run every declared acceptance check")


def validate(command: list[str], timeout: float) -> None:
    if not command or any(not isinstance(item, str) or '\0' in item for item in command) or not command[0].strip():
        raise ledger.LedgerError('Verification requires a nonempty command argv without NULs')
    if not math.isfinite(timeout) or not 0 < timeout <= 86400:
        raise ledger.LedgerError('Verification timeout must be positive, finite and at most 86400 seconds')


def command(args: argparse.Namespace) -> int:
    from hipson import task_runner
    try:
        args.verification_context = invocation_context()
        if args.actor is not None:
            args.actor = task_activity.safe_text(ledger.require_text(args.actor, 'actor'))
        db = Path(args.db).expanduser().resolve() if args.db else ledger.default_ledger_db()
        if not args.worker_command:
            return _command_checks(db, args)
        if args.worker_command[0] != '--':
            raise ledger.LedgerError('A verification command after -- is required (or no command to run declared checks)')
        argv = args.worker_command[1:]
        validate(argv, args.timeout)
        with closing(ledger.Ledger(db, create=False, read_only=True)) as store:
            task = store.show(args.task)
            project = task['project']
            if args.actor is None:
                args.actor = task['lead']
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
        print(json.dumps(record, ensure_ascii=True) if args.json else render(record))
        if record['fingerprint'] is None:
            print(f"Warning: this run cannot be closure evidence: {task_activity.safe_text(record.get('error') or 'fingerprint unavailable')}."
                  " Fix that (for example commit the initial revision) and verify again.", file=sys.stderr)
        return code
    except (ledger.LedgerError, OSError, sqlite3.Error) as exc:
        print(task_activity.safe_text(exc), file=sys.stderr)
        return 1


def render(record: dict[str, Any]) -> str:
    return (f"{record['id']} exit={record['exit_code']} duration={record['duration_seconds']:.3f}s "
            f"revision={record['revision']} fingerprint={record['fingerprint']} changed={record['state_changed']}\n"
            f"command: {escape_display_controls(shlex.join(record['command']))}\n"
            + '<untrusted_data name="verification_output">\n'
            + escape_display_controls(record['output_tail']).replace('<', '&lt;').replace('>', '&gt;')
            + '\n</untrusted_data>')


def declared_checks(db: Path, task: str) -> list[list[str]]:
    with closing(ledger.Ledger(db, create=False, read_only=True)) as store:
        return store.show(task)['checks']


def run_checks(db: Path, task: str, checks: list[list[str]], *, label: str = '', timeout: float = 300,
               actor: str | None = None, on_record: Any = None) -> list[dict[str, Any]]:
    """Run checks in order and stop at the first failing one; each run is its own verification record."""
    records = []
    for check in checks:
        record = run(db, task, check, label=label, timeout=timeout, actor=actor)
        records.append(record)
        if on_record:
            on_record(record)
        if record['exit_code'] != 0:
            break
    return records


def _command_checks(db: Path, args: argparse.Namespace) -> int:
    checks = declared_checks(db, args.task)
    if not checks:
        raise ledger.LedgerError(f'{args.task} declares no acceptance checks; pass -- COMMAND [ARGS...] or declare them '
                                 f"with: hipson task checks --task {args.task} --check 'COMMAND ARGS'")
    validate(checks[0], args.timeout)
    records = run_checks(db, args.task, checks, label=args.label, timeout=args.timeout, actor=args.actor,
                         on_record=None if args.json else lambda record: print(render(record), flush=True))
    if args.json:
        print(json.dumps(records, ensure_ascii=True))
    failed = next((record for record in records if record['exit_code'] != 0), None)
    if failed is None:
        if not args.json:
            print(f'All {len(records)} acceptance checks passed.')
        return 0
    skipped = len(checks) - len(records)
    print(f"Check failed: {shlex.join(failed['command'])} (exit {failed['exit_code']})"
          + (f'; {skipped} later checks not run' if skipped else ''), file=sys.stderr)
    return int(failed['exit_code']) or 1


def run(db: Path, task: str, command: list[str], *, label: str = '', timeout: float = 300,
        actor: str | None = None) -> dict[str, Any]:
    """MCP uses a private CLI supervisor too, without changing its server process group."""
    invocation_context()
    if actor is not None:
        ledger.require_text(actor, 'actor')
    validate(command, timeout)
    # -P keeps the project directory off sys.path: a project json.py must not shadow the stdlib here.
    invocation = [sys.executable, '-P', '-m', 'hipson.cli', 'task', 'verify', '--db', str(db), '--task', task,
                  '--label', label, '--timeout', str(timeout), '--json',
                  *(['--actor', actor] if actor is not None else []), '--', *command]
    from hipson.task_runner import supervise
    try:
        # Forward Ctrl+C/TERM to the supervisor so it stops its check tree and records the outcome.
        _, stdout, stderr = supervise(invocation, capture=True, timeout=timeout + 30)
    except subprocess.SubprocessError as exc:
        raise ledger.LedgerError('Verification supervisor did not finish within its execution budget') from exc
    try:
        return json.loads(stdout)
    except (ValueError, UnicodeError) as exc:
        raise ledger.LedgerError(task_activity.safe_text(stderr.decode(errors='replace')) or
                                 'Verification did not return recorded evidence') from exc


def finish(db: Path, args: argparse.Namespace, code: int, started_at: str, started: float,
           before: dict[str, Any], builder_seq: int, snapshot: dict[str, Any], project: str, safe: bool) -> None:
    after = project_state(project, db)
    changes = _changes(before, after)
    if before['revision'] != after['revision']:
        changes.append(f"HEAD before={before['revision']} after={after['revision']}")
    if not before['fingerprint'] or not after['fingerprint']:
        changes.append(f"fingerprint unavailable; before={before['error']} after={after['error']}")
    output = '\n'.join(item['text'] for item in snapshot.get('observations', []))
    output = task_activity.safe_text(output, 65536)[-OUTPUT_LIMIT:]
    fallback = sorted(set(_fallback_paths(before) + _fallback_paths(after)))
    record = {'id': args.verification_id, 'task': args.task, 'label': args.label,
              'actor': args.actor, 'context': args.verification_context,
              'coverage': 'partial' if fallback else 'complete', 'metadata_fallback_paths': fallback,
              'command': [task_activity.safe_text(arg, 4096) for arg in args.worker_command[1:]],
              'argv_sha256': argv_identity(args.worker_command[1:]),
              'exit_code': code, 'duration_seconds': max(0, time.monotonic() - started),
              'started_at': started_at, 'finished_at': ledger._timestamp(),
              **before, 'state_changed': bool(changes), 'builder_finished_seq': builder_seq, 'output_tail': output,
              'generated_files': [], 'changes': changes, 'cleanup_confirmed': safe,
              'untrusted_fields': ['actor', 'context', 'metadata_fallback_paths', 'label', 'command', 'subject',
                                   'output_tail', 'error', 'files', 'generated_files', 'changes']}
    with closing(ledger.Ledger(db, create=False)) as store:
        store._record_verification(record)
