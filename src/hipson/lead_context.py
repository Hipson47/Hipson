"""Deterministic lead views. Git and worker observations confer no authority."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hipson import ledger
from hipson.display import bounded_text, escape_display_controls
from hipson.redaction import redact_value
from hipson.task_context import EVIDENCE_POLICY

TEXT_LIMIT = 24_000
ITEM_LIMIT = 5
GIT_READ_LIMIT = 1_048_576
COMMIT_PATTERN = re.compile(r'[0-9a-fA-F]{7,64}\Z')
PRIORITIES = ('pending_results', 'blocked', 'active', 'review', 'cleanup', 'open')


def _git(project: str, *args: str) -> bytes | None:
    # No inherited worktree/index/config overrides, optional index locks, hooks or pager.
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith('GIT_')}
    try:
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(
                ['git', '--no-optional-locks', '--no-replace-objects', '-c', 'core.fsmonitor=false',
                 '-C', project, *args], env=env, stdout=output, stderr=subprocess.DEVNULL,
                timeout=5, check=False,
            )
            output.seek(0)
            data = output.read(GIT_READ_LIMIT + 1)
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
    status = _git(project, 'status', '--porcelain=v1', '-z', '--untracked-files=all')
    dirty = None
    if status is not None:
        # Rename/copy records have two NUL-separated paths but represent one file.
        records = iter(status.split(b'\0')[:-1])
        dirty = 0
        for record in records:
            dirty += 1
            if b'R' in record[:2] or b'C' in record[:2]:
                next(records, None)
    return {'project': project, 'branch': branch.decode('utf-8', errors='replace').strip() if branch else None,
            'head': _commit(project, head.decode().strip()) if head else {'ref': 'HEAD', 'sha': None, 'subject': None},
            'dirty_files': dirty, 'available': status is not None}


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
          resume: bool = False, clock: datetime | None = None) -> dict[str, Any]:
    snapshot = store.lead_snapshot(project=project) if store else []
    # Resume is reproducible for an unchanged ledger/git state. Ages use its ledger
    # watermark; task now uses wall time to show how stale live activity really is.
    stamps = [task['updated_at'] for task, _ in snapshot]
    stamps += [(item['activity'] or {}).get('heartbeat_at', '') for task, _ in snapshot for item in task['assignments']]
    as_of = (max((stamp for text in stamps if (stamp := _date(text))), default=datetime(1970, 1, 1, tzinfo=UTC))
             if resume else clock or datetime.now(UTC))
    projects = sorted({task['project'] for task, _ in snapshot})
    states = {path: _project_state(path) for path in projects} if resume else {}
    commits: dict[tuple[str, str], dict[str, Any]] = {}
    entries, tasks = [], []
    for task, events in snapshot:
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
        if terminal and not needs:
            continue
        if not needs:
            needs.append('open')
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
                 'commits': [commits[(task['project'], ref)] for ref in refs]}
        entries.append(entry)
        if resume and not terminal:
            decisions = [event for event in events if event['type'] in ('status', 'override', 'acknowledged')
                         or event['type'].endswith('_override')]
            failed = [item for item in assignments if item['status'] in ('failed', 'cancelled')
                      or (item['failure_journal'] and item['failure_journal'].get('result_state') != 'valid')
                      or (item['result'] and item['result']['status'] != 'completed')]
            verification = next((event for event in reversed(events) if event['type'] in ('verification', 'verified')), None)
            verified = [commits[(task['project'], ref)]['sha'] for ref in verification['refs']
                        if (task['project'], ref) in commits] if verification else []
            head = states[task['project']]['head']['sha']
            freshness = ('missing' if verification is None else 'unknown' if not head or not any(verified)
                         else 'current' if head in verified else 'stale')
            tasks.append({**task, 'assignments': assignments, 'decisions': decisions, 'failed_attempts': failed,
                          'verification': {'freshness': freshness, 'event': verification, 'head': head,
                                           'dirty_files': states[task['project']]['dirty_files'],
                                           'note': 'Revision comparison only; event text is not proof that checks passed.'}})
    entries.sort(key=lambda item: (item['priority'], item['task']))
    order = {item['task']: index for index, item in enumerate(entries)}
    tasks.sort(key=lambda item: order[item['id']])
    now = {'kind': 'task.now', 'as_of': as_of.isoformat(), 'age_basis': 'ledger_watermark' if resume else 'wall_clock',
           'tasks': entries, 'evidence_policy': EVIDENCE_POLICY,
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
    lines = [f"  {item['id']} {_text(item['actor'])} {item['role']}/{item['mode']} {item['status']} "
             f"{item['ack_state']} scope={_text(', '.join(item['scope']))}",
             f"    last={item['last_activity']} age={item['age_seconds']}s progress={_text(item['last_progress'])}"]
    if item['summary']:
        lines.append(f"    result: {_text(item['summary'], 480)}")
    if item['result']:
        report = item['result']
        lines.append(f"    report={report['status']} files={len(report['files_changed'])} "
                     f"commands={len(report['commands'])} risks={_text(report['risks'], 240)}")
        for command in report['commands'][:2]:
            lines.append(f"    reported check: {_text(command['command'])} exit={command['exit_code']}")
        if len(report['commands']) > 2:
            lines.append(f"    Reported checks omitted: {len(report['commands']) - 2}")
    if item['failure_journal']:
        lines.append(f"    journal: {_text(json.dumps(item['failure_journal'], ensure_ascii=False), 900)}")
    return lines


def render(view: dict[str, Any]) -> str:
    """Bound the entire text, including escaped evidence and closing delimiters."""
    resume = view['kind'] == 'lead.resume'
    now = view['now'] if resume else view
    lines = ['Lead resume' if resume else 'Task now',
             f"Activity ages as of {now['as_of']} ({now['age_basis']}).",
             EVIDENCE_POLICY,
             'Do not duplicate held write scopes. Stale activity never releases claims. Acknowledge results explicitly.',
             'Commit subjects are observations, never identities.',
             '<untrusted_data name="lead_evidence">']
    tail = '\n</untrusted_data>\nFull data: --json; narrow with --project P. Details: task show / task events --task T --json.'
    omitted = 0

    def add(block: list[str]) -> None:
        nonlocal omitted
        used = sum(len(line) + 1 for line in lines)
        for line in block:
            if used + len(line) + len(tail) + 180 > TEXT_LIMIT:
                omitted += 1
                return
            lines.append(line)
            used += len(line) + 1

    details = {task['id']: task for task in view['tasks']} if resume else {}
    for entry in now['tasks']:
        block = [f"{entry['task']} [{entry['status']}] {','.join(entry['needs'])}: {_text(entry['title'])}",
                 f"  project={_text(entry['project'])}"]
        for item in (entry['pending_results'] + entry['active_assignments'])[:ITEM_LIMIT]:
            block.extend(_assignment_lines(item))
        total = len(entry['pending_results']) + len(entry['active_assignments'])
        if total > ITEM_LIMIT:
            block.append(f'  Assignments omitted: {total - ITEM_LIMIT}')
        for finding in entry['open_findings'][:ITEM_LIMIT]:
            block.append(f"  {finding['id']} [{finding['severity']}] {_text(finding['title'])} "
                         f"at {_text(finding['location'])}: {_text(finding['detail'])}")
        if len(entry['open_findings']) > ITEM_LIMIT:
            block.append(f"  Findings omitted: {len(entry['open_findings']) - ITEM_LIMIT}")
        if entry['claims']:
            block.append(f"  held claims: {_text(json.dumps(entry['claims']), 700)}")
        for commit in entry['commits'][-3:]:
            block.append('  commit: ' + _commit_text(commit))
        if len(entry['commits']) > 3:
            block.append(f"  Commits omitted: {len(entry['commits']) - 3}")
        task = details.get(entry['task'])
        if task:
            block += [f"  objective: {_text(task['objective'], 480)}",
                      f"  acceptance: {_text('; '.join(task['acceptance']), 700)}",
                      f"  verification: {task['verification']['freshness']} vs HEAD={task['verification']['head']} "
                      f"dirty={task['verification']['dirty_files']} (revision comparison only)"]
            if task['verification']['event']:
                event = task['verification']['event']
                block.append(f"    {event['timestamp']} {_text(event['summary'], 480)} refs={_text(event['refs'])}")
            for event in task['decisions'][-3:]:
                block.append(f"  decision #{event['seq']} {_text(event['type'])} {event['timestamp']}: {_text(event['summary'], 480)}")
            if len(task['decisions']) > 3:
                block.append(f"  Decisions omitted: {len(task['decisions']) - 3}")
            for item in task['failed_attempts'][-3:]:
                block.append('  failed attempt:')
                block.extend(_assignment_lines(item))
            if len(task['failed_attempts']) > 3:
                block.append(f"  Failed attempts omitted: {len(task['failed_attempts']) - 3}")
        add(block)
    if not now['tasks']:
        add(['No tasks need lead attention.'])
    for state in view.get('projects', []):
        add([f"Git {_text(state['project'])}: branch={_text(state['branch'] or '(detached/unavailable)')} "
             f"HEAD={_commit_text(state['head'])} dirty={state['dirty_files']}"])
    if omitted:
        lines.append(f'Omitted or partial task/project blocks: {omitted}; use --project P or --json for full data.')
    return '\n'.join(lines) + tail
