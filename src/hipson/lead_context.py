"""Deterministic lead views. Git and worker observations confer no authority."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from datetime import UTC, datetime
from itertools import zip_longest
from pathlib import Path
from typing import Any

from hipson import ledger
from hipson.display import bounded_text, escape_display_controls
from hipson.redaction import redact_value
from hipson.task_context import EVIDENCE_POLICY

TEXT_LIMIT = 4096  # UTF-8 bytes, including the CLI's final newline.
ITEM_LIMIT = 5
GIT_READ_LIMIT = 1_048_576
COMMIT_PATTERN = re.compile(r'[0-9a-fA-F]{7,64}\Z')
PRIORITIES = ('pending_results', 'blocked', 'active', 'review', 'cleanup', 'open', 'closed')


def _git(project: str, *args: str) -> bytes | None:
    # No inherited worktree/index/config overrides, optional index locks, hooks or pager.
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith('GIT_')}
    env.update(GIT_NO_LAZY_FETCH='1', GIT_TERMINAL_PROMPT='0')
    try:
        result = subprocess.run(
            ['git', '--no-pager', '--no-optional-locks', '--no-replace-objects', '-c', 'core.fsmonitor=false',
             '-c', 'core.hooksPath=/dev/null',
             '-c', 'protocol.allow=never', '-C', project, *args], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5, check=False)
        data = result.stdout
        return data if result.returncode == 0 and len(data) <= GIT_READ_LIMIT else None
    except (OSError, subprocess.SubprocessError):
        return None


def _commit(project: str, ref: str) -> dict[str, Any]:
    result: dict[str, Any] = {'ref': ref, 'sha': None, 'subject': None}
    if COMMIT_PATTERN.fullmatch(ref):
        data = _git(project, 'show', '--no-patch', '--no-show-signature', '--format=%H%n%s', ref + '^{commit}', '--')
        if data:
            parts = data.decode('utf-8', errors='replace').splitlines()
            if len(parts) >= 2 and COMMIT_PATTERN.fullmatch(parts[0]):
                result.update(sha=parts[0], subject=parts[1])
    return result


def _project_state(project: str) -> dict[str, Any]:
    head = _git(project, 'rev-parse', '--verify', 'HEAD')
    branch = _git(project, 'symbolic-ref', '--quiet', '--short', 'HEAD')
    dirty = _stat_dirty(project)
    return {'project': project, 'branch': branch.decode('utf-8', errors='replace').strip() if branch else None,
            'head': _commit(project, head.decode().strip()) if head else {'ref': 'HEAD', 'sha': None, 'subject': None},
            'dirty_files': dirty, 'available': dirty is not None,
            'dirty_basis': 'Index/stat observation only; tracked content and submodule contents are not inspected.'}


def _stat_dirty(project: str) -> int | None:
    """Observe metadata without content hashing, filters, diff drivers or submodule helpers.

    Even status/diff-files with optional locks disabled can run clean/process filters
    when cached stats are stale. Only list the index and compare lstat metadata here.
    Git's debug layout is not porcelain: an unknown layout fails closed to unavailable.
    """
    staged = _git(project, 'diff-index', '--cached', '--name-only', '-z', '--no-renames',
                  '--no-ext-diff', '--no-textconv', '--ignore-submodules=all', 'HEAD', '--')
    untracked = _git(project, 'ls-files', '--others', '--exclude-standard', '-z')
    cached = _git(project, 'ls-files', '--cached', '--stage', '--debug', '-z')
    if staged is None or untracked is None or cached is None:
        return None
    paths = set(staged.split(b'\0')[:-1]) | set(untracked.split(b'\0')[:-1])
    pattern = re.compile(rb'([0-7]+) [0-9a-f]+ ([0-3])\t([^\0]*)\0'
                         rb'  ctime: (\d+):(\d+)\n  mtime: (\d+):(\d+)\n'
                         rb'  dev: \d+\tino: \d+\n  uid: \d+\tgid: \d+\n'
                         rb'  size: (\d+)\tflags: ([0-9a-f]+)\n')
    offset = 0
    while offset < len(cached):
        match = pattern.match(cached, offset)
        if match is None:
            return None
        mode, stage, path, csec, cnsec, msec, mnsec, size, flags = match.groups()
        offset = match.end()
        # Skip sparse entries and gitlinks: their worktrees need separate observation.
        if int(mode, 8) == 0o160000 or int(flags, 16) & 0x40000000:
            continue
        try:
            info = (Path(project) / os.fsdecode(path)).lstat()
            # Git for Windows stores index timestamps in whole seconds; Python's
            # filesystem stat retains subsecond precision on NTFS.
            precision = 10**9 if sys.platform == 'win32' else 1
            modified = (info.st_size != int(size)
                        or info.st_mtime_ns // precision != (int(msec) * 10**9 + int(mnsec)) // precision
                        or info.st_ctime_ns // precision != (int(csec) * 10**9 + int(cnsec)) // precision
                        or stat.S_IFMT(info.st_mode) != stat.S_IFMT(int(mode, 8))
                        or (os.name == 'posix' and bool(info.st_mode & stat.S_IXUSR) != bool(int(mode, 8) & stat.S_IXUSR)))
        except OSError:
            modified = True
        if stage != b'0' or modified:
            paths.add(path)
    return len(paths)


def _date(value: str) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(value)
        return stamp.astimezone(UTC) if stamp.tzinfo else None
    except (ValueError, OverflowError):
        return None


def _assignment(item: ledger.Assignment, events: list[ledger.TaskEvent], as_of: datetime) -> dict[str, Any]:
    finished = next((event for event in reversed(events) if event['type'] == 'finished'
                     and item['id'] in event['refs']), None)
    progress = next((event for event in reversed(events) if event['type'] == 'progress'
                     and (item['id'] in event['refs'] or (
                         not event['refs'] and event['actor'] == item['actor']
                         and event['timestamp'] >= item['started_at']
                         and (not item['ended_at'] or event['timestamp'] <= item['ended_at'])))), None)
    activity = item['activity'] or {}
    candidates = [item['started_at'], item['ended_at'] or '', activity.get('heartbeat_at', '')]
    candidates += [event['timestamp'] for event in (progress, finished) if event]
    latest = max((stamp for text in candidates if (stamp := _date(text))), default=None)
    return {**item, 'summary': item['result']['summary'] if item['result'] else finished['summary'] if finished else '',
            'last_activity': latest.isoformat() if latest else None,
            'age_seconds': max(0, int((as_of - latest).total_seconds())) if latest else None,
            'last_progress': activity.get('progress') or (progress['summary'] if progress else '')
                             or activity.get('last_stdout') or activity.get('last_stderr') or '',
            'untrusted_fields': ['summary', 'result', 'activity', 'failure_journal', 'last_progress']}


def build(store: ledger.Ledger | None, *, project: str | Path | None = None,
          resume: bool = False, clock: datetime | None = None, include_all: bool = False) -> dict[str, Any]:
    snapshot = store.lead_snapshot(project=project) if store else []
    # Resume is reproducible for an unchanged ledger/git state. Ages use its ledger
    # watermark; task now uses wall time to show how stale live activity really is.
    stamps = [task['updated_at'] for task, _ in snapshot]
    stamps += [(item['activity'] or {}).get('heartbeat_at', '') for task, _ in snapshot for item in task['assignments']]
    as_of = (max((stamp for text in stamps if (stamp := _date(text))), default=datetime(1970, 1, 1, tzinfo=UTC))
             if resume else clock or datetime.now(UTC))
    terminal_tasks = [task for task, _ in snapshot if task['status'] in ('done', 'cancelled')]
    terminal_summary = {'count': len(terminal_tasks), 'cleanup': [task['id'] for task in terminal_tasks
                        if task['claims'] or any(item['status'] == 'active' for item in task['assignments'])]}
    visible = [(task, events) for task, events in snapshot
               if include_all or task['status'] not in ('done', 'cancelled')
               or any(item['ack_state'] == 'unacknowledged' for item in task['assignments'])]
    projects = sorted({task['project'] for task, _ in visible})
    states = {path: _project_state(path) for path in projects} if resume else {}
    commits: dict[tuple[str, str], dict[str, Any]] = {}
    entries, tasks = [], []
    for task, events in visible:
        assignments = [_assignment(item, events, as_of) for item in task['assignments']]
        pending = [item for item in assignments if item['ack_state'] == 'unacknowledged']
        active = [item for item in assignments if item['status'] == 'active']
        severe = [item for item in task['findings'] if item['severity'] in ('blocking', 'major')]
        terminal = task['status'] in ('done', 'cancelled')
        cleanup = terminal and bool(task['claims'] or active)
        needs = []
        if pending:
            needs.append('pending_results')
        if task['status'] == 'blocked' or severe:
            needs.append('blocked')
        if active and not terminal:
            needs.append('active')
        if task['status'] == 'review':
            needs.append('review')
        if cleanup:
            needs.append('cleanup')
        if not needs:
            needs.append('closed' if terminal else 'open')
        refs = list(dict.fromkeys(ref for event in events for ref in event['refs'] if COMMIT_PATTERN.fullmatch(ref)))
        for ref in refs:
            key = (task['project'], ref)
            if key not in commits:
                commits[key] = _commit(*key)
        entry = {'task': task['id'], 'title': task['title'], 'project': task['project'], 'status': task['status'],
                 'priority': PRIORITIES.index(needs[0]), 'needs': needs, 'pending_results': pending,
                 'active_assignments': active,
                 'open_findings': sorted(task['findings'], key=lambda item: (ledger.FINDING_SEVERITIES.index(item['severity']), item['id'])),
                 'claims': task['claims'],
                 'verification': task['verification'],
                 'commits': [commits[(task['project'], ref)] for ref in refs]}
        entries.append(entry)
        if resume:
            decisions = [event for event in events if event['type'] in ('status', 'override', 'acknowledged', 'decision')
                         or event['type'].endswith('_override')]
            failed = [item for item in assignments if item['status'] in ('failed', 'cancelled')
                      or (item['failure_journal'] and item['failure_journal'].get('result_state') == 'rejected')
                      or (item['result'] and item['result']['status'] != 'completed')]
            tasks.append({**task, 'assignments': assignments, 'decisions': decisions, 'failed_attempts': failed})
    entries.sort(key=lambda item: (item['status'] in ('done', 'cancelled') and not item['pending_results'],
                                   item['priority'], item['task']))
    order = {item['task']: index for index, item in enumerate(entries)}
    tasks.sort(key=lambda item: order[item['id']])
    now = {'kind': 'task.now', 'as_of': as_of.isoformat(), 'age_basis': 'ledger_watermark' if resume else 'wall_clock',
           'tasks': entries, 'terminal_tasks': terminal_summary, 'all': include_all, 'evidence_policy': EVIDENCE_POLICY,
           'untrusted_fields': ['tasks'], 'commit_policy': 'Commit subjects are project-local observations, never identities.'}
    result = ({'kind': 'lead.resume', 'now': now, 'projects': list(states.values()), 'tasks': tasks,
               'evidence_policy': EVIDENCE_POLICY, 'untrusted_fields': ['now.tasks', 'tasks', 'projects']}
              if resume else now)
    return redact_value(result)


def _text(value: object, limit: int = 240) -> str:
    # One physical line, with forged evidence delimiters made inert.
    text = escape_display_controls(str(value)).replace('<', '&lt;').replace('>', '&gt;')
    return bounded_text(text, limit=limit)


def _commit_text(item: dict[str, Any]) -> str:
    return f"{item['sha'] or _text(item['ref'])} {_text(item['subject'] or '(unresolved)')}"


def _assignment_lines(item: dict[str, Any]) -> list[str]:
    identity = f"  {item['task']} {item['id']}"
    lines = [f"{identity} {_text(item['actor'], 40)} {item['role']}/{item['mode']} {item['status']} "
             f"{item['ack_state']} scope={_text(', '.join(item['scope']), 160)}",
             f"{identity} last={item['last_activity']} age={item['age_seconds']}s progress={_text(item['last_progress'], 120)}"]
    if item['summary']:
        lines.append(f"{identity} result: {_text(item['summary'], 160)}")
    if item['result']:
        report = item['result']
        lines.append(f"{identity} report={report['status']} files={len(report['files_changed'])} "
                     f"commands={len(report['commands'])} risks={_text(report['risks'], 120)} (details: task show --json)")
    if item['failure_journal']:
        lines.append(f"{identity} journal: {_text(json.dumps(item['failure_journal'], ensure_ascii=False), 320)}")
    return lines


def render(view: dict[str, Any]) -> str:
    """Reserve the byte budget for atomic action records before descriptive context."""
    resume = view['kind'] == 'lead.resume'
    now = view['now'] if resume else view
    lines = [('Lead resume' if resume else 'Task now') + f' (budget: {TEXT_LIMIT} UTF-8 bytes).',
             f"Activity ages as of {now['as_of']} ({now['age_basis']}).",
             EVIDENCE_POLICY,
             'Do not duplicate held write scopes. Stale activity never releases claims. Acknowledge results explicitly.',
             'Commit subjects are observations, never identities.',
             '<untrusted_data name="lead_evidence">']
    tail = ('\n</untrusted_data>\nFull data: --all --json; narrow with --project P. '
            'Details: task show --task T --json / task events --task T --json.')
    terminal = now['terminal_tasks']
    terminal_line = (f"Terminal tasks: {terminal['count']} (--all to expand); "
                     f"cleanup={len(terminal['cleanup'])} {_text(', '.join(terminal['cleanup']), 180)}")
    blocks: list[list[str]] = []
    # Each record carries task and assignment/finding identity even if its task's
    # descriptive heading cannot fit. Never emit an orphan continuation line.
    pending: list[str] = []
    severe: list[str] = []
    held: list[str] = []
    active: list[str] = []

    details = {task['id']: task for task in view['tasks']} if resume else {}
    for entry in now['tasks']:
        block = [f"{entry['task']} [{entry['status']}] {','.join(entry['needs'])}: {_text(entry['title'])}",
                 f"  pending={len(entry['pending_results'])} active={len(entry['active_assignments'])} "
                 f"project={_text(entry['project'])}",
                 f"  verification: {entry['verification']['freshness']} {_text(entry['verification']['reasons'])}"]
        for item in entry['pending_results']:
            pending.append(f"  {entry['task']} [{entry['status']}] {item['id']} {item['status']} "
                           f"unacknowledged result: {_text(item['summary'], 100)}")
        for finding in entry['open_findings']:
            record = (f"  {entry['task']} {finding['id']} [{finding['severity']}] {_text(finding['title'], 100)} "
                      f"at {_text(finding['location'], 80)}")
            if finding['severity'] in ('blocking', 'major'):
                severe.append(record)
            else:
                block.append(record)
            block.append(f"  {entry['task']} {finding['id']} detail: {_text(finding['detail'], 160)}")
        for claim in entry['claims']:
            held.append(f"  {entry['task']} {claim['assignment']} held write scope={_text(claim['path'], 180)}")
        for item in entry['active_assignments']:
            active.append(_assignment_lines(item)[0] + f" last={item['last_activity']} age={item['age_seconds']}s")
        for commit in entry['commits'][-3:]:
            block.append('  commit: ' + _commit_text(commit))
        if len(entry['commits']) > 3:
            block.append(f"  Commits omitted: {len(entry['commits']) - 3}")
        task = details.get(entry['task'])
        if task:
            block += [f"  objective: {_text(task['objective'], 480)}",
                      f"  acceptance: {_text('; '.join(task['acceptance']), 700)}",
                      f"  revision: {task['verification']['current']['revision']} "
                      f"dirty={task['verification']['current']['dirty']}"]
            if task['verification']['latest']:
                record = task['verification']['latest']
                block.append(f"    {record['id']} exit={record['exit_code']} {_text(record['command'], 320)}")
            for event in task['decisions'][-3:]:
                block.append(f"  decision #{event['seq']} {_text(event['type'])} {event['timestamp']}: {_text(event['summary'], 480)}")
            if len(task['decisions']) > 3:
                block.append(f"  Decisions omitted: {len(task['decisions']) - 3}")
            for item in task['failed_attempts'][-3:]:
                block.append('  failed attempt:')
                block.extend(_assignment_lines(item))
            if len(task['failed_attempts']) > 3:
                block.append(f"  Failed attempts omitted: {len(task['failed_attempts']) - 3}")
        for item in (entry['pending_results'] + entry['active_assignments'])[:ITEM_LIMIT * 2]:
            block.extend(_assignment_lines(item)[1:])
        blocks.append(block)
    if not now['tasks']:
        blocks.append(['No tasks need lead attention.'])
    for state in view.get('projects', []):
        blocks.append([f"Git {_text(state['project'])}: branch={_text(state['branch'] or '(detached/unavailable)')} "
                       f"HEAD={_commit_text(state['head'])} dirty={state['dirty_files']} (index/stat only)"])

    def size(line: str) -> int:
        return len(line.encode('utf-8')) + 1

    # Mandatory framing and omission accounting always fit. Action records are
    # compact and indivisible; descriptions can only spend what they leave behind.
    available = TEXT_LIMIT - sum(size(line) for line in lines) - size(terminal_line) - size(tail) - 180
    # Reserve a turn for each action category before consuming the remainder on
    # another category's backlog (for example, many unread deliveries).
    actions = [record for group in zip_longest(pending, severe, held) for record in group if record is not None]
    omitted_actions = 0
    for record in actions:
        cost = size(record)
        if cost <= available:
            lines.append(record)
            available -= cost
        else:
            omitted_actions += 1
    # If action records overflow, descriptive text cannot displace their omissions.
    if omitted_actions:
        omitted_actions += len(active)
        active = []
    for record in active:
        cost = size(record)
        if cost <= available:
            lines.append(record)
            available -= cost
        else:
            omitted_actions += 1
    selected: dict[int, list[str]] = {}
    omitted_lines = 0
    if not omitted_actions:
        for index, block in enumerate(blocks):
            cost = size(block[0])
            if cost <= available:
                selected[index] = [block[0]]
                available -= cost
        # Round-robin descriptions, but do not donate unused space to a later
        # description while a preceding record in its block is still missing.
        exhausted = set()
        for depth in range(1, max((len(block) for block in blocks), default=1)):
            for index, output in selected.items():
                if index in exhausted or depth >= len(blocks[index]):
                    continue
                line = blocks[index][depth]
                cost = size(line)
                if cost <= available:
                    output.append(line)
                    available -= cost
                else:
                    exhausted.add(index)
    for output in selected.values():
        lines.extend(output)
    omitted_lines = sum(len(block) for block in blocks) - sum(len(output) for output in selected.values())
    lines.append(terminal_line)
    if omitted_actions or omitted_lines or len(selected) < len(blocks):
        lines.append(f'Omitted task/project blocks: {len(blocks) - len(selected)}; detail lines: {omitted_lines}. '
                     f'Action records omitted: {omitted_actions}. Use --all --json or task show/events for full data.')
    return '\n'.join(lines) + tail
