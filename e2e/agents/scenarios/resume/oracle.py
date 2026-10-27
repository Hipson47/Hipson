"""Require delivery consumption, verified repair and preservation of the live holder."""

from e2e.agents.support import covers_parser, parser_works, passed_worker


def check(ctx):
    task = ctx.task()
    if task is None:
        return ['resume: original task disappeared']
    failures = []
    delivery = next((a for a in task['assignments'] if a['id'] == ctx.seed['delivered']), None)
    if delivery is None or delivery['ack_state'] != 'acknowledged':
        failures.append('resume: explicitly acknowledge the previous delivery')
    finding = next((f for f in ctx.state['findings'] if f['id'] == ctx.seed['finding']), None)
    repairs = [a for a in task['assignments'] if a['id'] != ctx.seed['delivered']
               and passed_worker(a, 'builder', 'rw') and covers_parser(a)]
    if (not repairs or finding is None or finding['status'] != 'fixed'
            or finding['resolved_by_assignment'] not in [a['id'] for a in repairs]
            or not parser_works(ctx, trimmed=True)):
        failures.append('resume: address the major finding with a verified worker repair')
    if task['status'] != 'in_progress':
        failures.append('resume: leave parser work in progress for review')
    if task['claims'] or any(a['ack_state'] != 'acknowledged' for a in repairs):
        failures.append('resume: acknowledge repair delivery and release parser claims')
    initial_held = {path: text for path, text in ctx.initial['files'].items() if path.startswith('src/held/')}
    for state in [ctx.state, *[entry['after'] for entry in ctx.audit]]:
        other = next((t for t in state['tasks'] if t['id'] == ctx.seed['other']), None)
        holder = next((a for t in state['tasks'] for a in t['assignments'] if a['id'] == ctx.seed['holder']), None)
        if (other is None or holder is None or holder['status'] != 'active'
                or {'assignment': ctx.seed['holder'], 'path': 'src/held'} not in other['claims']):
            failures.append('resume: preserve the live holder and its held scope')
            break
        if {path: text for path, text in state['files'].items() if path.startswith('src/held/')} != initial_held:
            failures.append('resume: do not edit files in the held scope')
            break
    if any(e['type'] == 'claim_conflict' for e in ctx.state['events']):
        failures.append('resume: do not attempt a second writer on the held scope')
    return failures
