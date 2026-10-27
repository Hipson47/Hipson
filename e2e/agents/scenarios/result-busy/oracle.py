"""A delivered result stays visible through other work and is consumed before closure."""

from e2e.agents.support import covers_parser, event_seq, parser_works, passed_worker


def check(ctx):
    task = ctx.task()
    if task is None:
        return ['result-busy: task disappeared']
    failures = []
    results = [a for a in task['assignments'] if passed_worker(a, 'builder', 'rw') and covers_parser(a)]
    if len(results) != 1:
        return ['result-busy: expected one structured worker delivery']
    item = results[0]
    other_events = [e for e in ctx.state['events'] if e['task'] == ctx.seed['other'] and e['type'] == 'progress']
    ack = event_seq(ctx, 'acknowledged', item['id'])
    finished = event_seq(ctx, 'finished', item['id'])
    other = next((t for t in ctx.state['tasks'] if t['id'] == ctx.seed['other']), None)
    if (not other_events or not 0 < finished < other_events[0]['seq'] < ack
            or other is None or other['status'] != 'in_progress'):
        failures.append('result-busy: delivery must remain pending while lead handles independent work')
    done = ctx.transitions('done', task['id'])
    review = ctx.transitions('review', task['id'])
    if item['ack_state'] != 'acknowledged' or not 0 < finished < ack or (done and ack >= done[0]['seq']):
        failures.append('result-busy: explicitly acknowledge delivered result before closing')
    if (task['status'] != 'done' or task['claims'] or not parser_works(ctx)
            or not review or not done or not review[0]['seq'] < done[0]['seq']):
        failures.append('result-busy: verify parser and close task through review')
    if any(e['type'] == 'done_override' for e in ctx.state['events']):
        failures.append('result-busy: override cannot substitute for consuming a result')
    return failures
