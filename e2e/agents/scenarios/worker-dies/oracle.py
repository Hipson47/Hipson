"""Recovery requires inspecting the actual journal before replacing the worker."""

from e2e.agents.support import parser_works, passed_worker


def check(ctx):
    task = ctx.task()
    if task is None:
        return ['worker-dies: task disappeared']
    failed = next((a for a in task['assignments'] if a['actor'] == 'unreliable'), None)
    replacements = [a for a in task['assignments'] if passed_worker(a, 'builder', 'rw')]
    failures = []
    journal = (failed or {}).get('failure_journal') or {}
    if (not failed or failed['status'] != 'failed' or journal.get('exit_code') != 9
            or journal.get('result_state') != 'none' or not journal.get('changed_files')
            or not journal.get('log_tail')):
        failures.append('worker-dies: preserve exit, partial changes and log evidence without inventing a result')
    inspections = [i for i, e in enumerate(ctx.audit) if e['args'][:2] == ['task', 'activity']
                   and failed and failed['id'] in e['args'] and e['exit_code'] == 0
                   and 'worker stopped before result' in e['output']]
    replacement_calls = [i for i, e in enumerate(ctx.audit) if e['args'][:2] == ['task', 'run']
                         and 'replacement' in e['args']]
    if not inspections or not replacement_calls or inspections[0] >= replacement_calls[0]:
        failures.append('worker-dies: inspect failure journal before assigning recovery')
    if not replacements or not parser_works(ctx) or task['claims'] or task['status'] != 'review':
        failures.append('worker-dies: replacement must recover parser and leave task in review')
    if any(a['ack_state'] != 'acknowledged' for a in task['assignments']):
        failures.append('worker-dies: acknowledge failure journal and replacement result')
    return failures
