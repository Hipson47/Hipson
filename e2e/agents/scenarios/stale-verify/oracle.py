"""Judge observed acceptance and lead decisions, never worker completion claims."""

from e2e.agents.support import parser_works


def _command(argv):
    """Normalize parent options and exclude the executed command after --."""
    args = argv[:argv.index('--')] if '--' in argv else argv
    if args[:1] != ['task']:
        return '', {}
    index = 1
    while index < len(args):
        if args[index] == '--db':
            index += 2
        elif args[index] == '--json' or args[index].startswith('--db='):
            index += 1
        else:
            break
    action = args[index] if index < len(args) else ''
    options = {}
    index += 1
    while index < len(args):
        arg = args[index]
        if arg.startswith('--') and '=' in arg:
            key, value = arg.split('=', 1)
            options[key] = value
        elif arg.startswith('--') and index + 1 < len(args) and not args[index + 1].startswith('--'):
            options[arg] = args[index + 1]
            index += 1
        index += 1
    return action, options


def _ordered(ctx, task_id):
    """Require this task's pass, changed candidate, refusal, rerun and closure."""
    cutoff = max((event['seq'] for event in ctx.state['events']), default=0)
    stage = 0
    first = None
    rerun = None
    closed = False
    for entry in ctx.audit:
        # Historical closure must never borrow a later verify/refusal from the full audit.
        if max((event['seq'] for event in entry['after']['events']), default=0) > cutoff:
            break
        before = next((task for task in entry['before']['tasks'] if task['id'] == task_id), None)
        after = next((task for task in entry['after']['tasks'] if task['id'] == task_id), None)
        if before is None or after is None:
            continue
        current = before['verification']['current']
        if stage == 1 and first != (current['revision'], current['fingerprint']):
            stage = 2
        action, options = _command(entry['args'])
        if options.get('--task') == task_id:
            verification = after['verification']
            previous_record = before['verification'].get('latest') or {}
            if (action == 'verify' and entry['exit_code'] == 0 and verification['passing']
                    and verification['accepted'] != previous_record.get('id')):
                if stage == 0:
                    first = (verification['current']['revision'], verification['current']['fingerprint'])
                    stage = 1
                elif stage >= 3:
                    rerun = verification['accepted']
                    stage = 4
            if (action == 'status' and options.get('--to') in ('review', 'done')
                    and entry['exit_code'] != 0 and 'stale:' in entry['output'] and stage == 2
                    and before['verification']['freshness'] == 'stale'):
                stage = 3
        if after['status'] in ('review', 'done') and before['status'] != after['status']:
            if stage != 4 or not after['verification']['passing'] or after['verification']['accepted'] != rerun:
                return False
            closed = after['status'] == 'done'
            if closed:
                break
    return stage == 4 and closed


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
    if not _ordered(ctx, task['id']):
        failures.append('stale-verify: require ordered task pass, change, stale refusal, rerun and closure')
    if any(event['task'] == task['id'] and event['type'].endswith('_override') for event in ctx.state['events']):
        failures.append('stale-verify: stale evidence cannot be forced through')
    return failures
