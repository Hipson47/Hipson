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

from hipson import ledger, task_activity, task_verification


def parse_check(text: str) -> list[str]:
    """Split one --check value like a POSIX shell would, without ever running a shell."""
    if os.name == 'nt' and '\\' in text:
        raise ledger.LedgerError(f'--check {text!r}: use forward slashes in paths; a backslash is an escape here')
    try:
        argv = shlex.split(text)
    except ValueError as exc:
        raise ledger.LedgerError(f'--check {text!r}: {exc}') from exc
    if not argv:
        raise ledger.LedgerError('--check needs a command')
    return argv


def add_check_arguments(parser: argparse.ArgumentParser, *, required: bool = False) -> None:
    parser.add_argument('--check', action='append', default=[], required=required, metavar='COMMAND',
                        help="Acceptance check run by the lead, e.g. --check 'pytest -q'; repeatable, no shell")


def _db(args: argparse.Namespace) -> Path:
    return Path(args.db).expanduser().resolve() if args.db else ledger.default_ledger_db()


def done_blockers(view: ledger.TaskView) -> list[str]:
    """Done blockers other than verification, each with the command that clears it."""
    task = view['id']
    blockers = [f"active assignment {item['id']}: wait for it, or hipson task reap --task {task}"
                for item in view['assignments'] if item['status'] == 'active']
    blockers += [f"unacknowledged result {item['id']}: read it (hipson task show --task {task}), then "
                 f"hipson task ack --assignment {item['id']}"
                 for item in view['assignments'] if item['ack_state'] == 'unacknowledged']
    blockers += [f"open {item['severity']} finding {item['id']}: fix it, then hipson task finding update "
                 f"--finding {item['id']} --to fixed --actor {view['lead']}"
                 for item in view['findings'] if item['severity'] in ('blocking', 'major')]
    if view['claims'] and not any(item['status'] == 'active' for item in view['assignments']):
        blockers.append(f'held claims: hipson task reap --task {task}')
    return blockers


def close(db: Path, task: str, *, to: str = 'done', summary: str = '', actor: str | None = None,
          timeout: float = 300, command: list[str] | None = None,
          on_record: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Verify (declared checks that are not yet fresh, or one given command), then review and done.

    Nothing transitions unless every gate is known to pass, so a refused close leaves the task as it was.
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
    blockers = done_blockers(view) if to == 'done' else []
    active = [item['id'] for item in view['assignments'] if item['status'] == 'active']
    if active:  # Verifying while a worker still writes would only produce stale evidence.
        raise ledger.LedgerError(f'Cannot close {task}: ' + '; '.join(blockers or [f'active assignment {active[0]}']))
    if command is not None:
        task_verification.validate(command, timeout)
        pending = [command]
    else:
        pending = [state['command'] for state in view['verification']['checks'] if not state['passing']]
    records = task_verification.run_checks(db, task, pending, label='close', timeout=timeout,
                                           actor=actor, on_record=on_record) if pending else []
    failed = next((record for record in records if record['exit_code'] != 0), None)
    if failed is not None:
        return {'task': task, 'closed': False, 'status': view['status'], 'records': records,
                'reasons': [f"check `{shlex.join(failed['command'])}` failed with exit {failed['exit_code']}"],
                'exit_code': int(failed['exit_code']) or 1}
    with closing(ledger.Ledger(db, create=False)) as store:
        view = store.show(task)
        verification = view['verification']
        if not verification['passing']:
            hint = ([] if view['checks'] or records else
                    [f"no acceptance checks declared: run hipson task close --task {task} -- COMMAND, or declare "
                     f"them with hipson task checks --task {task} --check 'COMMAND'"])
            return {'task': task, 'closed': False, 'status': view['status'], 'records': records,
                    'reasons': hint + verification['reasons'], 'exit_code': 1}
        blockers = done_blockers(view) if to == 'done' else []
        if blockers:
            return {'task': task, 'closed': False, 'status': view['status'], 'records': records,
                    'reasons': blockers, 'exit_code': 1}
        status = view['status']
        if status == 'open':
            store.set_status(task, to='in_progress', actor=actor, summary='started by close')
            status = 'in_progress'
        if status == 'in_progress':
            view = store.set_status(task, to='review', actor=actor, summary=summary)
        if to == 'done':
            view = store.set_status(task, to='done', actor=actor, summary=summary)
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
                                   acceptance=args.acceptance, lead=args.lead, checks=checks)
            task = view['id']
            store.set_status(task, to='in_progress', actor=args.lead, summary='started by task do')
    except (ledger.LedgerError, OSError, sqlite3.Error) as exc:
        print(task_activity.safe_text(exc), file=sys.stderr)
        return 1
    print(f'hipson task do: opened {task} in {project}; checks: {ledger.checks_text(checks)}', file=sys.stderr, flush=True)
    worker = [sys.executable, '-m', 'hipson.cli', 'task', 'run', '--db', str(db), '--task', task,
              '--actor', args.actor, '--role', args.role, '--mode', args.mode,
              *[item for path in args.scope for item in ('--scope', path)],
              *(['--lease-seconds', str(args.lease_seconds)] if args.lease_seconds is not None else []),
              *(['--result-file', args.result_file] if args.result_file else []),
              *(['--rollout', args.rollout] if args.rollout else []), *args.worker_command]
    try:
        code = subprocess.run(worker, cwd=project, check=False).returncode
    except KeyboardInterrupt:
        code = 130
    try:
        with closing(ledger.Ledger(db, create=False)) as store:
            view = store.show(task)
            mine = [item for item in view['assignments'] if item['actor'] == args.actor]
            assignment = mine[-1] if mine else None
            report = assignment['result'] if assignment else None
            if assignment and assignment['status'] != 'active':
                print(f"hipson task do: {assignment['id']} {assignment['status']}; "
                      f"result: {task_activity.safe_text(report['summary'] if report else 'none', 400)}", file=sys.stderr)
            healthy = (code == 0 and assignment is not None and assignment['status'] == 'passed'
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
    parser.add_argument('--to', choices=('review', 'done'), default='done')
    parser.add_argument('--timeout', type=float, default=300, help='Per-check timeout in seconds')
    parser.add_argument('worker_command', nargs=argparse.REMAINDER, help='-- WORKER COMMAND [ARGS...]')
