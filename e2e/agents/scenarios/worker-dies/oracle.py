"""Recovery preserves the failed delivery and journals its actual process outcome."""

from e2e.agents.support import assignment_observations, covers_parser, event_seq, parser_works, passed_worker


def check(ctx):
    task = ctx.task()
    if task is None:
        return ['worker-dies: task disappeared']
    failures = []
    # Identify the failed run by observed exit evidence, never a prescribed actor label.
    failed_ids = {observed['id'] for item in task['assignments']
                  for observed in assignment_observations(ctx, item['id'])
                  if (observed.get('failure_journal') or {}).get('exit_code') == 9}
    if len(failed_ids) != 1:
        return ['worker-dies: preserve exit, partial changes and log evidence without inventing a result']
    failed_id = failed_ids.pop()
    observations = assignment_observations(ctx, failed_id)
    first_ended = next((i for i, a in enumerate(observations) if a['status'] != 'active'), len(observations))
    observations = observations[first_ended:]
    journal = observations[0]['failure_journal'] if observations else {}
    if (not journal or journal.get('result_state') != 'none' or not journal.get('changed_files')
            or not any(line.get('text') == 'worker stopped before result' for line in journal.get('log_tail', []))
            or not any(run['exit_code'] == 9 for run in ctx.runners)
            or any(a['status'] != 'failed' or a['result'] is not None or a['failure_journal'] != journal
                   for a in observations)):
        failures.append('worker-dies: preserve exit, partial changes and log evidence without inventing a result')
    failed_finish = event_seq(ctx, 'finished', failed_id)
    replacements = [a for a in task['assignments'] if passed_worker(a, 'builder', 'rw') and covers_parser(a)
                    and event_seq(ctx, 'assigned', a['id']) > failed_finish > 0]
    if not replacements or not parser_works(ctx) or task['claims'] or task['status'] != 'review':
        failures.append('worker-dies: replacement must recover parser and leave task in review')
    if any(a['ack_state'] != 'acknowledged' for a in task['assignments']):
        failures.append('worker-dies: acknowledge failure journal and replacement result')
    return failures
