"""Short lead flows over the ledger: declared acceptance checks, one-step close and one-shot delegation."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

from hipson import ledger, task_activity, task_checkpoint, task_runner, task_verification


def parse_check(text: str) -> list[str]:
    """Split one --check value like a POSIX shell would, without ever running a shell."""
    if os.name == 'nt' and '\\' in text:
        raise ledger.LedgerError(f'--check "{text}": use forward slashes in paths; a backslash is an escape here')
    try:
        argv = shlex.split(text)
    except ValueError as exc:
        raise ledger.LedgerError(f'--check "{text}": {exc}') from exc
    if not argv:
        raise ledger.LedgerError('--check needs a command')
    return argv


def add_check_arguments(parser: argparse.ArgumentParser, *, required: bool = False) -> None:
    parser.add_argument('--check', action='append', default=[], required=required, metavar='COMMAND',
                        help="Acceptance check run by the lead, e.g. --check 'pytest -q'; repeatable, no shell")


def _db(args: argparse.Namespace) -> Path:
    return Path(args.db).expanduser().resolve() if args.db else ledger.default_ledger_db()


def _refused(task: str, db: Path, reasons: list[str], records: list[dict[str, Any]], code: int = 1) -> dict[str, Any]:
    with closing(ledger.Ledger(db, create=False, read_only=True)) as store:
        status = store.show(task)['status']
    return {'task': task, 'closed': False, 'status': status, 'records': records, 'reasons': reasons, 'exit_code': code}


def close(db: Path, task: str, *, to: str = 'done', summary: str = '', actor: str | None = None,
          timeout: float = 300, command: list[str] | None = None,
          on_record: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Verify (declared checks that are not yet fresh, or one given command), then review and done.

    Known blockers refuse before any check runs; the transitions happen in one ledger transaction that
    rechecks every gate, so a refused close never leaves an intermediate status behind.
    """
    task_verification.invocation_context()
    if to not in ('review', 'done'):
        raise ledger.LedgerError('close target must be review or done')
    with closing(ledger.Ledger(db, create=False, read_only=True)) as store:
        view = store.show(task)
    actor = actor or view['lead']
    if view['status'] not in ('open', 'in_progress', 'review'):
        raise ledger.LedgerError(f"Cannot close {task} from status {view['status']}")
    if view['status'] == 'review' and to == 'review':
        raise ledger.LedgerError(f'{task} is already in review')
    active = [item['id'] for item in view['assignments'] if item['status'] == 'active']
    blockers = ledger.done_blockers(view) if to == 'done' or active else []
    if blockers:  # Verifying now would only append evidence to a close that must be refused anyway.
        return _refused(task, db, blockers, [])
    if command is not None:
        task_verification.validate(command, timeout)
        pending = [command]
    else:
        pending = [state['command'] for state in view['verification']['checks'] if not state['passing']]
    records = task_verification.run_checks(db, task, pending, label='close', timeout=timeout,
                                           actor=actor, on_record=on_record) if pending else []
    failed = next((record for record in records if record['exit_code'] != 0), None)
    if failed is not None:
        reason = f"check `{shlex.join(failed['command'])}` failed with exit {failed['exit_code']}"
        if failed['exit_code'] == 124 and failed['duration_seconds'] >= timeout - 1:
            reason += f"; it hit the {timeout:g}s per-check timeout, rerun with --timeout SECONDS"
        return _refused(task, db, [reason], records, int(failed['exit_code']) or 1)
    try:
        with closing(ledger.Ledger(db, create=False)) as store:
            view = store.close_task(task, to=to, actor=actor, summary=summary)
    except ledger.CloseRefusedError as exc:
        hint = ([] if view['checks'] or records else
                [f"no acceptance checks declared: run hipson task close --task {task} -- COMMAND, or declare "
                 f"them with hipson task checks --task {task} --check 'COMMAND'"])
        return _refused(task, db, hint + exc.reasons, records)
    return {'task': task, 'closed': True, 'status': view['status'], 'records': records, 'reasons': [],
            'accepted': view['verification']['accepted_ids'], 'exit_code': 0}


def _print_close(result: dict[str, Any]) -> None:
    if result['closed']:
        print(f"{result['task']} [{result['status']}]: lead verification {', '.join(result['accepted'])}")
    else:
        print(f"{result['task']} not closed (still {result['status']}):", file=sys.stderr)
        for reason in result['reasons']:
            print(f'  - {task_activity.safe_text(reason)}', file=sys.stderr)


def command_close(args: argparse.Namespace) -> int:
    try:
        if args.worker_command and args.worker_command[0] != '--':
            raise ledger.LedgerError('A verification command must follow --')
        command = args.worker_command[1:] if args.worker_command else None
        if command == []:
            raise ledger.LedgerError('A verification command after -- is required')
        result = close(_db(args), args.task, to=args.to, summary=args.summary, actor=args.actor,
                       timeout=args.timeout, command=command,
                       on_record=None if args.json else lambda record: print(task_verification.render(record), flush=True))
    except (ledger.LedgerError, OSError, sqlite3.Error) as exc:
        print(task_activity.safe_text(exc), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=True))
    else:
        _print_close(result)
    return int(result['exit_code'])


def command_checks(args: argparse.Namespace) -> int:
    try:
        task_verification.invocation_context()  # A worker must not rewrite what accepts its own work.
        if bool(args.check) == args.clear:
            raise ledger.LedgerError('Pass --check (repeatable) to declare checks, or --clear to remove them')
        checks = [parse_check(item) for item in args.check]
        with closing(ledger.Ledger(_db(args), create=False)) as store:
            actor = args.actor or store.show(args.task)['lead']
            view = store.set_checks(args.task, checks=checks, actor=actor, reason=args.reason)
    except (ledger.LedgerError, OSError, sqlite3.Error) as exc:
        print(task_activity.safe_text(exc), file=sys.stderr)
        return 1
    print(json.dumps(view['checks'], ensure_ascii=True) if args.json else
          f"{view['id']} acceptance checks: {ledger.checks_text(view['checks'])}")
    return 0


def _project(value: str | None) -> str:
    start = Path(value or '.').expanduser().resolve()
    env = {key: item for key, item in os.environ.items() if not key.upper().startswith('GIT_')}
    try:
        top = subprocess.run(['git', 'rev-parse', '--show-toplevel'], cwd=start, env=env, capture_output=True,
                             text=True, encoding='utf-8', timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return str(start)
    return str(Path(top.stdout.strip()).resolve()) if top.returncode == 0 and top.stdout.strip() else str(start)


def _run_worker(command: list[str], cwd: str) -> int:
    return task_runner.supervise(command, cwd=cwd)[0]


def command_do(args: argparse.Namespace) -> int:
    """Open a task with checks, run one worker, and close it when the worker and every check pass."""
    try:
        task_verification.invocation_context()
        if not args.worker_command or args.worker_command[0] != '--' or len(args.worker_command) == 1:
            raise ledger.LedgerError('A worker command after -- is required')
        checks = [parse_check(item) for item in args.check]
        ledger.validate_assignment_scope(args.scope, mode=args.mode)
        db = _db(args)
        project = _project(args.project)
        with closing(ledger.Ledger(db)) as store:
            view = store.open_task(project=project, title=args.title, objective=args.objective or args.title,
                                   acceptance=args.acceptance, lead=args.lead, checks=checks,
                                   needs=task_runner.parse_needs(args.needs))
            task = view['id']
            store.set_status(task, to='in_progress', actor=args.lead, summary='started by task do')
    except (ledger.LedgerError, OSError, sqlite3.Error) as exc:
        print(task_activity.safe_text(exc), file=sys.stderr)
        return 1
    print(f'hipson task do: opened {task} in {project}; checks: {ledger.checks_text(checks)}', file=sys.stderr, flush=True)
    # -P: the worker runs in the project directory, whose modules must not shadow the stdlib.
    worker = [sys.executable, '-P', '-m', 'hipson.cli', 'task', 'run', '--db', str(db), '--task', task,
              '--actor', args.actor, '--role', args.role, '--mode', args.mode,
              *[item for path in args.scope for item in ('--scope', path)],
              *(['--lease-seconds', str(args.lease_seconds)] if args.lease_seconds is not None else []),
              *(['--result-file', args.result_file] if args.result_file else []),
              *(['--rollout', args.rollout] if args.rollout else []),
              *(['--observe'] if args.observe else []),
              *(['--observe-max-files', str(args.observe_max_files)] if args.observe_max_files else []),
              *(['--observe-max-bytes', str(args.observe_max_bytes)] if args.observe_max_bytes else []),
              *(['--env', args.env] if args.env else []),
              *[item for name in args.env_allow for item in ('--env-allow', name)],
              *(['--checkpoint'] if args.checkpoint else []),
              *(['--checkpoint-max-files', str(args.checkpoint_max_files)] if args.checkpoint_max_files else []),
              *(['--checkpoint-max-bytes', str(args.checkpoint_max_bytes)] if args.checkpoint_max_bytes else []),
              *args.worker_command]
    code = _run_worker(worker, project)
    try:
        with closing(ledger.Ledger(db, create=False)) as store:
            view = store.show(task)
            # This task was opened above, so its only assignment is the one the runner recorded.
            assignment = view['assignments'][0] if len(view['assignments']) == 1 else None
            report = assignment['result'] if assignment else None
            state = ((assignment['failure_journal'] or {}) if assignment else {}).get('result_state')
            if assignment and assignment['status'] != 'active':
                print(f"hipson task do: {assignment['id']} {assignment['status']}; result file: {state or 'n/a'}; "
                      f"result: {task_activity.safe_text(report['summary'] if report else 'none', 400)}", file=sys.stderr)
            # A rejected or missing requested report is not an unstructured success.
            healthy = (code == 0 and assignment is not None and assignment['status'] == 'passed'
                       and state != 'rejected' and (state == 'valid' or not args.result_file)
                       and (report is None or report['status'] == 'completed'))
            if not healthy or assignment is None:
                print(f'hipson task do: worker did not complete (exit {code}); {task} stays {view["status"]}. '
                      f'Inspect: hipson task show --task {task}; retry: hipson task run --task {task} ...',
                      file=sys.stderr)
                return code or 1
            if assignment['ack_state'] == 'unacknowledged':
                store.ack(assignment['id'], note='acknowledged by hipson task do after printing the result to the lead')
        result = close(db, task, to=args.to, actor=args.lead, timeout=args.timeout,
                       on_record=None if args.json else lambda record: print(task_verification.render(record), flush=True))
    except (ledger.LedgerError, OSError, sqlite3.Error) as exc:
        print(task_activity.safe_text(exc), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=True))
    else:
        _print_close(result)
    return int(result['exit_code'])


def configure_close(parser: argparse.ArgumentParser) -> None:
    parser.set_defaults(func=command_close)
    parser.add_argument('--task', required=True)
    parser.add_argument('--to', choices=('review', 'done'), default='done')
    parser.add_argument('--summary', default='')
    parser.add_argument('--actor', help='Recorded lead label (default: task lead); not authenticated')
    parser.add_argument('--timeout', type=float, default=300, help='Per-check timeout in seconds')
    parser.add_argument('worker_command', nargs=argparse.REMAINDER,
                        help='-- COMMAND [ARGS...] to verify with; omit to run declared checks that are not fresh')


def configure_checks(parser: argparse.ArgumentParser) -> None:
    parser.set_defaults(func=command_checks)
    parser.add_argument('--task', required=True)
    add_check_arguments(parser)
    parser.add_argument('--clear', action='store_true', help='Remove all declared checks (requires --reason)')
    parser.add_argument('--reason', default='', help='Required when replacing or clearing declared checks')
    parser.add_argument('--actor', help='Recorded lead label (default: task lead); not authenticated')


def configure_do(parser: argparse.ArgumentParser) -> None:
    parser.set_defaults(func=command_do)
    parser.add_argument('--title', required=True)
    parser.add_argument('--objective', help='Defaults to the title')
    parser.add_argument('--project', help='Defaults to the current git repository (or directory)')
    add_check_arguments(parser, required=True)
    parser.add_argument('--acceptance', action='append', default=[], help='Acceptance criterion text; repeatable')
    parser.add_argument('--lead', default='claude')
    parser.add_argument('--actor', required=True, help='Worker label, e.g. codex')
    parser.add_argument('--role', choices=ledger.ROLES, default='builder')
    parser.add_argument('--mode', choices=ledger.MODES, default='rw')
    parser.add_argument('--scope', action='append', default=[], help='Claimed write scope; repeatable (required for rw)')
    parser.add_argument('--lease-seconds', type=int)
    parser.add_argument('--result-file', help='Structured worker result to ingest (schemas/task-result.schema.json)')
    parser.add_argument('--rollout', choices=['codex'])
    task_runner.add_observe_arguments(parser)
    task_runner.add_env_arguments(parser)
    task_runner.add_needs_arguments(parser, 'the task')
    task_runner.add_checkpoint_arguments(parser)
    parser.add_argument('--to', choices=('review', 'done'), default='done')
    parser.add_argument('--timeout', type=float, default=300, help='Per-check timeout in seconds')
    parser.add_argument('worker_command', nargs=argparse.REMAINDER, help='-- WORKER COMMAND [ARGS...]')


def configure_restore(parser: argparse.ArgumentParser) -> None:
    parser.set_defaults(func=command_restore)
    parser.add_argument('--assignment', required=True)
    parser.add_argument('--apply', action='store_true',
                        help='Perform the restore (default: dry run listing what would change)')


def _paths(label: str, paths: list[str]) -> list[str]:
    shown = [f'  {label}: {task_activity.safe_text(path, 200)}' for path in paths[:50]]
    return shown + ([f'  {label}: ... {len(paths) - 50} more'] if len(paths) > 50 else [])


def command_restore(args: argparse.Namespace) -> int:
    """Dry run by default; --apply restores within the checkpointed scope only, refused while anything is live."""
    db = _db(args)
    try:
        with closing(ledger.Ledger(db, create=False, read_only=not args.apply)) as store:
            assignment = store.activity(args.assignment)
            if args.apply:
                result = task_checkpoint.apply(db, args.assignment, store)
                store.record_checkpoint(args.assignment, type='restored', summary=(
                    f"{args.assignment}: restored {len(result['restore'])}, re-created {len(result['recreate'])}, "
                    f"deleted {len(result['delete']) - len(result['kept'])} path(s) in "
                    f"{', '.join(result['scope'])}; verified {str(result['verified']).lower()}"))
            else:
                result = {**task_checkpoint.plan(db, args.assignment, store), 'applied': False,
                          'blockers': task_checkpoint.live_blockers(
                              store, args.assignment, task_checkpoint.load(db, args.assignment)['roots'])}
    except (task_checkpoint.CheckpointError, ledger.LedgerError, OSError, sqlite3.Error) as exc:
        print(f'hipson task restore: {task_activity.safe_text(exc, 1200)}', file=sys.stderr)
        return 1
    result['task'] = assignment['task']
    if getattr(args, 'json', False):
        print(json.dumps(result, ensure_ascii=True))
    else:
        verb = 'Restored' if result['applied'] else 'Would restore'
        lines = [f"{verb} {args.assignment} ({assignment['task']}) to its checkpoint of {result['taken_at']} "
                 f"in {', '.join(result['scope'])}:"]
        lines += _paths('restore', result['restore']) + _paths('re-create', result['recreate'])
        lines += _paths('delete', result['delete'])
        if result['clean']:
            lines.append('  nothing to do: the scope matches the checkpoint')
        if result['untouched']['sensitive']:
            lines.append(f"  left alone: {len(result['untouched']['sensitive'])} sensitive file(s) (never copied)")
        if result['applied']:
            lines += _paths('kept (holds files left alone)', result['kept'])
            lines.append('  verified: the scope matches the checkpoint' if result['verified']
                         else '  NOT verified: ' + json.dumps(result['remaining']))
        elif result['blockers']:
            lines += [f'  blocked: {task_activity.safe_text(reason, 300)}' for reason in result['blockers']]
        else:
            lines.append(f'  apply with: hipson task restore --assignment {args.assignment} --apply')
        print('\n'.join(lines))
    return 0 if not result['applied'] or result['verified'] else 1


def configure_checkpoints(parser: argparse.ArgumentParser) -> None:
    parser.set_defaults(func=command_checkpoints)
    parser.add_argument('--prune', action='store_true',
                        help='Remove checkpoints of done/cancelled tasks and those older than --older-than-days')
    parser.add_argument('--older-than-days', type=int, default=task_checkpoint.RETENTION_DAYS)
    parser.add_argument('--dry-run', action='store_true', help='With --prune, only list what would go')


def command_checkpoints(args: argparse.Namespace) -> int:
    db = _db(args)
    if args.older_than_days < 0:
        print('hipson task checkpoints: --older-than-days must be nonnegative', file=sys.stderr)
        return 64
    try:
        store = ledger.Ledger(db, create=False, read_only=True) if db.exists() else None
        try:
            payload: Any = (task_checkpoint.prune(db, store, older_than_days=args.older_than_days, dry_run=args.dry_run)
                            if args.prune else task_checkpoint.checkpoints(db))
        finally:
            if store is not None:
                store.close()
    except (ledger.LedgerError, OSError, sqlite3.Error) as exc:
        print(f'hipson task checkpoints: {task_activity.safe_text(exc, 600)}', file=sys.stderr)
        return 1
    if getattr(args, 'json', False):
        print(json.dumps(payload, ensure_ascii=True))
    elif args.prune:
        verb = 'Would remove' if args.dry_run else 'Removed'
        print('\n'.join(f"{verb} {item['assignment']}: {item['reason']}" for item in payload) or 'Nothing to prune.')
    else:
        print('\n'.join(f"{item['assignment']} {item.get('taken_at', '?')}: {item.get('files', '?')} files, "
                        f"{item.get('bytes', '?')} bytes in {', '.join(item.get('scope', []))}"
                        + (f" ({item['error']})" if 'error' in item else '') for item in payload)
              or 'No checkpoints.')
    return 0
