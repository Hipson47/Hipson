"""A delivered result stays visible through other work and is consumed before closure."""

from e2e.agents.support import event_seq, parser_works, passed_worker


def check(ctx):
    task = ctx.task()
    if task is None:
        return ['result-busy: task disappeared']
    failures = []
    results = [a for a in task['assignments'] if passed_worker(a, 'builder', 'rw')]
    if len(results) != 1:
        return ['result-busy: expected one structured worker delivery']
    item = results[0]
    other_events = [e for e in ctx.state['events'] if e['task'] == ctx.seed['other'] and e['type'] == 'progress']
    ack = event_seq(ctx, 'acknowledged', item['id'])
    finished = event_seq(ctx, 'finished', item['id'])
    if not other_events or not finished < other_events[0]['seq'] < ack:
        failures.append('result-busy: delivery must remain pending while lead handles independent work')
    boards = [e for e in ctx.audit if e['args'][:2] == ['task', 'board'] and e['exit_code'] == 0
              and item['id'] in e['output'] and 'unacknowledged' in e['output']]
    if not boards:
        failures.append('result-busy: board must expose unread delivery')
    inspected = [e for e in ctx.audit if e['args'][:2] in (['task', 'show'], ['task', 'activity'])
                 and e['exit_code'] == 0 and 'completed' in e['output']]
    if not inspected or item['ack_state'] != 'acknowledged' or ack <= finished:
        failures.append('result-busy: inspect and explicitly acknowledge delivered result before closing')
    if task['status'] != 'done' or task['claims'] or not parser_works(ctx):
        failures.append('result-busy: verify parser and close task through review')
    if any(e['type'] == 'done_override' for e in ctx.state['events']):
        failures.append('result-busy: override cannot substitute for consuming a result')
    return failures
