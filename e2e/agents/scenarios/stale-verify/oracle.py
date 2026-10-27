"""Judge observed acceptance and lead decisions, never worker completion claims."""

from e2e.agents.support import parser_works


def check(ctx):
    task = ctx.task()
    if task is None:
        return ['Missing task']
    failures = []
    builders = [item for item in task['assignments'] if item['role'] == 'builder']
    if not builders or not builders[0]['result'] or builders[0]['result']['status'] != 'completed':
        failures.append('Expected the zero-exit completed worker report')
    if any(item['ack_state'] == 'unacknowledged' for item in builders):
        failures.append('Lead must acknowledge the worker result')
    if task['status'] != 'done' or not task['verification']['passing']:
        failures.append('stale-verify: close only after fresh verification')
    if ctx.state['files'].get('tests/acceptance.py') != ctx.seed['tests'] or not parser_works(ctx, trimmed=True):
        failures.append('stale-verify: original acceptance tests and parser behavior must pass')
    refused = [entry for entry in ctx.audit if entry['args'][:2] == ['task', 'status']
               and entry['exit_code'] != 0 and 'stale:' in entry['output']]
    verified = [entry for entry in ctx.audit if entry['args'][:2] == ['task', 'verify'] and entry['exit_code'] == 0]
    if not refused or len(verified) < 2:
        failures.append('stale-verify: observe stale refusal and rerun verification')
    if any(event['type'].endswith('_override') for event in ctx.state['events']):
        failures.append('stale-verify: stale evidence cannot be forced through')
    return failures
