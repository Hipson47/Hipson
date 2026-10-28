"""Declared acceptance checks, one-step close and one-shot delegation (also runs without pytest)."""

import json
import os
import shlex
import signal
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from unittest import SkipTest
from unittest.mock import patch

from hipson import ledger, task_context, task_flow, task_verification
from tests.test_hipson_helpers import run_cli
from tests.test_ledger import _error, assert_strict_schema
from tests.test_ledger_mcp import _assert_error, _call, _payload
from tests.test_ledger_recovery import _v1_file
from tests.test_task_verification import _project

PY = Path(sys.executable).as_posix()  # Forward slashes keep --check values portable to Windows.
PASS = [PY, '-c', 'pass']
SECOND = [PY, '-c', 'import sys; sys.exit(0)']
FAIL = [PY, '-c', 'import sys; sys.exit(3)']


def _cli(project, db, *args, env=None):
    return run_cli(project, 'task', *args[:1], '--db', str(db), *args[1:], env=env)


def _open(store, project, checks=(PASS,)):
    task = store.open_task(project=project, title='Flow', objective='Close on declared checks', checks=checks)['id']
    store.set_status(task, to='in_progress', actor='claude')
    return task


def test_declared_checks_gate_review_and_done_per_command(tmp_path):
    project = _project(tmp_path)
    with closing(ledger.Ledger(tmp_path / 'ledger.sqlite')) as store:
        task = _open(store, project, checks=(PASS, SECOND))
        view = store.show(task)
        assert view['checks'] == [PASS, SECOND]
        assert [state['freshness'] for state in view['verification']['checks']] == ['missing', 'missing']
        assert_strict_schema('task.schema.json', view)
        # Any other passing command is evidence, but it cannot stand in for a declared check.
        task_verification.run(store.path, task, [PY, '-c', 'print("ad hoc")'])
        _error(lambda: store.set_status(task, to='review', actor='claude'), f'check `{shlex.join(PASS)}`: missing')
        task_verification.run(store.path, task, PASS)
        _error(lambda: store.set_status(task, to='review', actor='claude'), f'check `{shlex.join(SECOND)}`: missing')
        second = task_verification.run(store.path, task, SECOND)
        view = store.set_status(task, to='review', actor='claude')
        assert view['verification']['passing'] and len(view['verification']['accepted_ids']) == 2
        assert view['verification']['accepted'] == second['id']
        event = store.events(task)[-1]
        assert set(event['refs']) == set(view['verification']['accepted_ids'])
        assert 'acceptance checks: ' + ledger.checks_text([PASS, SECOND]) in event['summary']
        assert_strict_schema('task.schema.json', view)
        (project / 'source.py').write_text('value = 2\n')
        _error(lambda: store.set_status(task, to='done', actor='claude'), 'stale: working tree changed')
        task_verification.run(store.path, task, PASS)
        task_verification.run(store.path, task, SECOND)
        assert store.show(task)['verification']['passing']
        task_verification.run(store.path, task, [PY, '-c', 'pass', 'extra'])
        assert store.show(task)['verification']['passing']
        # A later failing run of the same argv replaces its earlier pass.
        flaky = [PY, '-c', 'import os, sys; sys.exit(int(os.environ.get("FLOW_EXIT", "0")))']
        store.set_checks(task, checks=[PASS, flaky], actor='claude', reason='environment-driven check')
        task_verification.run(store.path, task, flaky)
        assert store.show(task)['verification']['passing']
        with patch.dict(os.environ, FLOW_EXIT='3'):
            task_verification.run(store.path, task, flaky)
        _error(lambda: store.set_status(task, to='done', actor='claude'), f'check `{shlex.join(flaky)}`: failing')


def test_a_longer_command_cannot_pass_as_the_check_it_truncates_to(tmp_path):
    project = _project(tmp_path)
    with closing(ledger.Ledger(tmp_path / 'ledger.sqlite')) as store:
        failing = [PY, '-c', 'import sys; sys.exit(3)'.ljust(ledger.CHECK_ARG_LIMIT)]
        task = _open(store, project, checks=(failing,))
        record = task_verification.run(store.path, task, [PY, '-c', failing[2] + ' if False else None'])
        assert record['exit_code'] == 0 and record['command'] == failing  # Display form is bounded.
        assert record['argv_sha256'] != task_verification.argv_identity(failing)
        _error(lambda: store.set_status(task, to='review', actor='claude'), 'missing: never verified')
        assert task_verification.run(store.path, task, failing)['exit_code'] == 3
        _error(lambda: store.set_status(task, to='review', actor='claude'), 'failing')


def test_cli_verify_without_command_runs_checks_in_order_and_stops_at_first_failure(tmp_path):
    project = _project(tmp_path)
    db = tmp_path / 'ledger.sqlite'
    with closing(ledger.Ledger(db)) as store:
        task = _open(store, project, checks=(FAIL, PASS))
    result = _cli(project, db, 'verify', '--task', task)
    assert result.returncode == 3, result.stderr
    assert 'Check failed' in result.stderr and '1 later checks not run' in result.stderr
    assert f'command: {shlex.join(FAIL)}' in result.stdout
    with closing(ledger.Ledger(db, read_only=True)) as store:
        assert [record['command'] for record in store.verifications(task)] == [FAIL]
        assert store.show(task)['verification']['freshness'] == 'failing'
    with closing(ledger.Ledger(db)) as store:
        store.set_checks(task, checks=[PASS], actor='claude', reason='the failing check was wrong')
    result = _cli(project, db, '--json', 'verify', '--task', task)
    assert result.returncode == 0, result.stderr
    assert [record['command'] for record in json.loads(result.stdout)] == [PASS]
    empty = _cli(project, db, 'open', '--project', str(project), '--title', 'No checks', '--objective', 'Ad hoc')
    other = empty.stdout.split()[0]
    refused = _cli(project, db, 'verify', '--task', other)
    assert refused.returncode == 1 and 'declares no acceptance checks' in refused.stderr


def test_set_checks_is_audited_needs_reason_to_replace_and_validates_argv(tmp_path):
    project = _project(tmp_path)
    db = tmp_path / 'ledger.sqlite'
    with closing(ledger.Ledger(db)) as store:
        task = _open(store, project, checks=())
        store.set_checks(task, checks=[PASS, PASS], actor='claude')
        assert store.show(task)['checks'] == [PASS]
        _error(lambda: store.set_checks(task, checks=[], actor='claude'), 'requires --reason')
        store.set_checks(task, checks=[SECOND], actor='claude', reason='replace')
        event = store.events(task)[-1]
        assert event['type'] == 'checks_set' and 'previously: ' + shlex.join(PASS) in event['summary']
        for bad, message in ((['pytest -q'], 'argv list'), ([[]], 'argv list'), ([['']], 'command name'),
                             ([['echo', 'a\nb']], 'control characters'),
                             ([['curl', '-H', 'Authorization: Bearer ghp_' + 'a' * 36]], 'secret-shaped'),
                             ([PASS] * 17, 'at most 16')):
            _error(lambda bad=bad: store.set_checks(task, checks=bad, actor='claude', reason='x'), message)
        assert store.show(task)['checks'] == [SECOND]
    worker = _cli(project, db, 'checks', '--task', task, '--check', 'true', '--reason', 'weaken',
                  env={'HIPSON_ROLE': 'worker'})
    assert worker.returncode == 1 and 'worker context' in worker.stderr
    both = _cli(project, db, 'checks', '--task', task, '--clear', '--check', 'true')
    assert both.returncode == 1 and '--clear' in both.stderr
    cleared = _cli(project, db, 'checks', '--task', task, '--clear', '--reason', 'manual verification only')
    assert cleared.returncode == 0 and 'none declared' in cleared.stdout
    with closing(ledger.Ledger(db)) as store:
        store.set_status(task, to='cancelled', actor='claude')
        _error(lambda: store.set_checks(task, checks=[PASS], actor='claude'), 'cancelled')


def test_parse_check_splits_like_posix_and_refuses_windows_backslashes():
    assert task_flow.parse_check("pytest -q 'tests/a b.py'") == ['pytest', '-q', 'tests/a b.py']
    for bad, message in (('', 'needs a command'), ('pytest "unterminated', 'No closing quotation')):
        _error(lambda bad=bad: task_flow.parse_check(bad), message)
    with patch.object(task_flow.os, 'name', 'nt'):
        _error(lambda: task_flow.parse_check('pytest tests\\unit'), 'forward slashes')
        assert task_flow.parse_check('pytest tests/unit') == ['pytest', 'tests/unit']


def test_close_runs_pending_checks_then_review_and_done(tmp_path):
    project = _project(tmp_path)
    db = tmp_path / 'ledger.sqlite'
    opened = _cli(project, db, 'open', '--project', str(project), '--title', 'Solo', '--objective', 'One step close',
                  '--check', shlex.join(PASS), '--start')
    assert opened.returncode == 0, opened.stderr
    task = opened.stdout.split()[0]
    assert '[in_progress]' in opened.stdout
    closed = _cli(project, db, 'close', '--task', task, '--summary', 'solo work')
    assert closed.returncode == 0, closed.stderr
    assert f'{task} [done]' in closed.stdout
    with closing(ledger.Ledger(db, read_only=True)) as store:
        view = store.show(task)
        types = [event['type'] for event in store.events(task)]
    assert view['status'] == 'done' and types[-3:] == ['lead_verified', 'status', 'status']
    assert_strict_schema('task.schema.json', view)
    again = _cli(project, db, 'close', '--task', task)
    assert again.returncode == 1 and 'from status done' in again.stderr


def test_close_reuses_fresh_checks_and_refuses_without_changing_state(tmp_path):
    project = _project(tmp_path)
    db = tmp_path / 'ledger.sqlite'
    with closing(ledger.Ledger(db)) as store:
        task = _open(store, project)
        task_verification.run(db, task, PASS)
        assignment = store.assign(task, actor='codex', role='builder', mode='ro')['id']
        store.finish(assignment, result='passed', summary='done')
        bare = _open(store, project, checks=())
    # A builder result after the verification makes it stale; its unread result refuses done before any check runs.
    result = task_flow.close(db, task)
    assert not result['closed'] and any('unacknowledged result' in reason for reason in result['reasons'])
    with closing(ledger.Ledger(db, read_only=True)) as store:
        before = store.show(task)
        assert before['status'] == 'in_progress' and len(store.verifications(task)) == 1
    with closing(ledger.Ledger(db)) as store:
        store.ack(assignment, note='read')
    assert task_flow.close(db, task, to='review')['status'] == 'review'
    with closing(ledger.Ledger(db, read_only=True)) as store:
        assert len(store.verifications(task)) == 2  # The stale check was rerun once.
    assert task_flow.close(db, task)['status'] == 'done'
    with closing(ledger.Ledger(db, read_only=True)) as store:
        assert len(store.verifications(task)) == 2  # The fresh run was reused.
    refused = task_flow.close(db, bare)
    assert not refused['closed'] and 'no acceptance checks declared' in refused['reasons'][0]
    failing = task_flow.close(db, bare, command=FAIL)
    assert not failing['closed'] and failing['exit_code'] == 3
    assert task_flow.close(db, bare, command=PASS)['status'] == 'done'
    with patch.dict(os.environ, HIPSON_ASSIGNMENT='A-0001'):
        _error(lambda: task_flow.close(db, bare), 'worker context')


def test_close_rechecks_every_gate_in_one_transaction(tmp_path):
    project = _project(tmp_path)
    db = tmp_path / 'ledger.sqlite'
    with closing(ledger.Ledger(db)) as store:
        task = _open(store, project)
        task_verification.run(db, task, PASS)
        # A finding that lands after the caller's checks still refuses done, and review is rolled back too.
        store.add_finding(task, actor='reviewer', severity='major', title='Late finding')
        events = len(store.events(task))
        _error(lambda: store.close_task(task, actor='claude'), 'open major finding', ledger.CloseRefusedError)
        assert store.show(task)['status'] == 'in_progress' and len(store.events(task)) == events
        store.update_finding(store.findings(task=task)[0]['id'], actor='claude', status='fixed', resolution_note='ok')
        events = len(store.events(task))
        original = store._set_status_locked

        def fail_done(task_id, **kwargs):
            if kwargs['to'] == 'done':
                raise ledger.LedgerError('injected failure after review')
            return original(task_id, **kwargs)

        with patch.object(store, '_set_status_locked', side_effect=fail_done):
            _error(lambda: store.close_task(task, actor='claude'), 'injected failure')
        assert store.show(task)['status'] == 'in_progress' and len(store.events(task)) == events
        assert store.close_task(task, actor='claude')['status'] == 'done'
        cancelled = _open(store, project)

    def cancel(record):
        with closing(ledger.Ledger(db)) as writer:
            writer.set_status(cancelled, to='cancelled', actor='claude', summary='cancelled during verification')

    result = task_flow.close(db, cancelled, to='review', on_record=cancel)
    assert not result['closed'] and result['status'] == 'cancelled' and result['exit_code'] == 1
    assert 'close works from open, in_progress or review' in result['reasons'][0]


def _worker(tmp_path, project, *, code=0, status='completed'):
    result = tmp_path / 'result.json'
    script = tmp_path / 'worker.py'
    script.write_text(
        'import json, os, sys\n'
        f'open({str(project / "source.py")!r}, "w").write("value = 3\\n")\n'
        f'json.dump({{"status": {status!r}, "summary": "edited source", "files_changed": '
        '[{"path": "source.py", "change": "value"}], "commands": [], "findings": [], "risks": []}, '
        f'open({str(result)!r}, "w"))\n'
        'assert os.environ["HIPSON_ROLE"] == "worker"\n'
        f'sys.exit({code})\n')
    return result, Path(script).as_posix()


def test_do_opens_runs_worker_verifies_and_closes(tmp_path):
    project = _project(tmp_path)
    db = tmp_path / 'ledger.sqlite'
    result, script = _worker(tmp_path, project)
    done = _cli(project, db, 'do', '--title', 'One shot', '--check', shlex.join(PASS), '--actor', 'fake',
                '--scope', 'source.py', '--result-file', str(result), '--', PY, script)
    assert done.returncode == 0, done.stderr
    assert 'opened T-0001' in done.stderr and 'T-0001 [done]' in done.stdout
    with closing(ledger.Ledger(db, read_only=True)) as store:
        view = store.show('T-0001')
    assert view['status'] == 'done' and view['project'] == str(project.resolve())
    assignment = view['assignments'][0]
    assert assignment['result']['summary'] == 'edited source' and 'task do' in assignment['ack_note']
    assert (project / 'source.py').read_text() == 'value = 3\n'


def test_do_leaves_failed_or_partial_work_in_progress(tmp_path):
    project = _project(tmp_path)
    db = tmp_path / 'ledger.sqlite'
    result, script = _worker(tmp_path, project, code=4)
    failed = _cli(project, db, 'do', '--title', 'Fails', '--check', shlex.join(PASS), '--actor', 'fake',
                  '--scope', 'source.py', '--', PY, script)
    assert failed.returncode == 4 and 'stays in_progress' in failed.stderr
    result, script = _worker(tmp_path, project, status='partial')
    partial = _cli(project, db, 'do', '--title', 'Partial', '--check', shlex.join(PASS), '--actor', 'fake',
                   '--scope', 'source.py', '--result-file', str(result), '--', PY, script)
    assert partial.returncode == 1 and 'stays in_progress' in partial.stderr
    with closing(ledger.Ledger(db, read_only=True)) as store:
        for task in ('T-0001', 'T-0002'):
            assert store.show(task)['status'] == 'in_progress' and not store.verifications(task)
    missing = _cli(project, db, 'do', '--title', 'No check', '--actor', 'fake', '--', PY, script)
    assert missing.returncode != 0 and '--check' in missing.stderr
    # A malformed report, or a requested one that never appears, is not an unstructured success.
    result, script = _worker(tmp_path, project)
    rejected = tmp_path / 'rejected.json'
    rejected.write_text('{"status": "completed"')
    bad = _cli(project, db, 'do', '--title', 'Rejected', '--check', shlex.join(PASS), '--actor', 'fake',
               '--scope', 'source.py', '--result-file', str(rejected), '--', PY, script)
    assert bad.returncode == 1 and 'result file: rejected' in bad.stderr, bad.stderr
    absent = _cli(project, db, 'do', '--title', 'Absent', '--check', shlex.join(PASS), '--actor', 'fake',
                  '--scope', 'source.py', '--result-file', str(tmp_path / 'never.json'), '--', PY, '-c', 'pass')
    assert absent.returncode == 1 and 'result file: none' in absent.stderr, absent.stderr
    with closing(ledger.Ledger(db, read_only=True)) as store:
        for task in ('T-0003', 'T-0004'):
            view = store.show(task)
            assert view['status'] == 'in_progress' and view['assignments'][0]['ack_state'] == 'unacknowledged'


def test_do_finds_its_assignment_despite_label_normalization(tmp_path):
    project = _project(tmp_path)
    db = tmp_path / 'ledger.sqlite'
    result, script = _worker(tmp_path, project)
    done = _cli(project, db, 'do', '--title', 'Label', '--check', shlex.join(PASS), '--actor', ' fake ',
                '--scope', 'source.py', '--result-file', str(result), '--', PY, script)
    assert done.returncode == 0 and 'T-0001 [done]' in done.stdout, done.stderr


def test_interrupting_do_lets_the_runner_stop_its_worker(tmp_path):
    if sys.platform == 'win32':
        raise SkipTest('requires POSIX signals to interrupt hipson task do; Windows shares console Ctrl+C')
    project = _project(tmp_path)
    db = tmp_path / 'ledger.sqlite'
    ready, pid_file = tmp_path / 'ready', tmp_path / 'worker.pid'
    worker = (f'import os, time; open({str(pid_file)!r}, "w").write(str(os.getpid())); '
              f'open({str(ready)!r}, "w").close(); time.sleep(60)')
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src'), 'AGENT_RUN_GRACE_SECONDS': '2'}
    for key in ('HIPSON_ROLE', 'HIPSON_TASK', 'HIPSON_ASSIGNMENT'):
        env.pop(key, None)
    process = subprocess.Popen([sys.executable, '-m', 'hipson.cli', 'task', 'do', '--db', str(db), '--title', 'Stop',
                                '--check', shlex.join(PASS), '--actor', 'fake', '--scope', 'source.py', '--',
                                PY, '-c', worker], cwd=project, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True)
    try:
        deadline = time.monotonic() + 20
        while not ready.exists() and time.monotonic() < deadline and process.poll() is None:
            time.sleep(0.05)
        assert ready.exists(), process.communicate(timeout=5)
        process.send_signal(signal.SIGINT)
        _, stderr = process.communicate(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
    assert process.returncode == 130 and 'stays in_progress' in stderr, stderr
    pid = int(pid_file.read_text())
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        pass
    else:
        os.kill(pid, signal.SIGKILL)
        raise AssertionError('The interrupted worker survived task do')
    with closing(ledger.Ledger(db, read_only=True)) as store:
        view = store.show('T-0001')
    assert view['status'] == 'in_progress' and view['assignments'][0]['status'] == 'cancelled' and not view['claims']


def test_brief_lists_checks_for_the_worker(tmp_path):
    project = _project(tmp_path)
    with closing(ledger.Ledger(tmp_path / 'ledger.sqlite')) as store:
        task = _open(store, project)
        view = task_context.brief(store, task, role='builder')
        assert view['checks'] == [task_context._text(shlex.join(PASS), limit=600)]
        assert '## Acceptance checks' in task_context.render(view)


def test_mcp_checks_verify_and_close(tmp_path):
    project = _project(tmp_path)
    db = tmp_path / 'ledger.sqlite'
    task = _payload(_call(tmp_path, 'task.open', ledger_db=db, project=str(project), title='MCP',
                          objective='Close via MCP', checks=[PASS], approved=True))['id']
    _assert_error(_call(tmp_path, 'task.open', ledger_db=db, project=str(project), title='Bad', objective='x',
                        checks=['pytest -q'], approved=True), 'argv list')
    _payload(_call(tmp_path, 'task.status', ledger_db=db, task=task, to='in_progress', actor='claude', approved=True))
    verified = _payload(_call(tmp_path, 'task.verify', ledger_db=db, task=task, approved=True))
    assert verified['passing'] and [record['command'] for record in verified['records']] == [PASS]
    with patch.dict(os.environ, HIPSON_ROLE='worker'):
        _assert_error(_call(tmp_path, 'task.checks', ledger_db=db, task=task, checks=[], reason='weaken',
                            approved=True), 'worker context')
    view = _payload(_call(tmp_path, 'task.checks', ledger_db=db, task=task, checks=[PASS, SECOND],
                          reason='add a second check', approved=True))
    assert view['checks'] == [PASS, SECOND]
    closed = _payload(_call(tmp_path, 'task.close', ledger_db=db, task=task, approved=True))
    assert closed['closed'] and closed['status'] == 'done' and len(closed['records']) == 1


def test_v7_migration_adds_empty_checks_atomically_and_reads_v6_without_migrating(tmp_path):
    db = tmp_path / 'v6.sqlite'
    _v1_file(db)
    with closing(sqlite3.connect(db)) as connection:
        for migration in (ledger.MIGRATION_V1_TO_V2, ledger.MIGRATION_V2_TO_V3, ledger.MIGRATION_V3_TO_V4,
                          ledger.MIGRATION_V4_TO_V5, ledger.MIGRATION_V5_TO_V6):
            for statement in migration:
                connection.execute(statement)
        connection.commit()
    original = db.read_bytes()
    with closing(ledger.Ledger(db, read_only=True)) as store:
        assert store.show('T-0001')['checks'] == []
    assert db.read_bytes() == original
    with patch.object(ledger, 'MIGRATION_V6_TO_V7', (*ledger.MIGRATION_V6_TO_V7, 'INVALID MIGRATION')):
        _error(lambda: ledger.Ledger(db), 'syntax error', sqlite3.OperationalError)
    with closing(sqlite3.connect(db)) as connection:
        assert connection.execute('SELECT version FROM schema_version').fetchone()[0] == 6
        assert 'checks' not in [row[1] for row in connection.execute('PRAGMA table_info(tasks)')]
    with closing(ledger.Ledger(db)) as store:
        assert store.connection.execute('SELECT version FROM schema_version').fetchone()[0] == ledger.SCHEMA_VERSION
        assert {task['id']: task['checks'] for task in store.list_tasks()} == {'T-0001': [], 'T-0002': []}


def test_unborn_repository_verification_warns_that_it_cannot_close(tmp_path):
    project = tmp_path / 'unborn'
    project.mkdir()
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith('GIT_')}
    subprocess.run(['git', 'init', '-q', str(project)], check=True, env=env, timeout=10)
    db = tmp_path / 'ledger.sqlite'
    with closing(ledger.Ledger(db)) as store:
        task = _open(store, project)
    result = _cli(project, db, 'verify', '--task', task, '--', *PASS)
    assert result.returncode == 0 and 'cannot be closure evidence' in result.stderr
    assert 'commit the initial revision' in result.stderr


def test_refusals_name_the_next_command(tmp_path):
    project = _project(tmp_path)
    with closing(ledger.Ledger(tmp_path / 'ledger.sqlite')) as store:
        task = _open(store, project)
        _error(lambda: store.set_status(task, to='review', actor='claude'), f'next: hipson task close --task {task}')
        _error(lambda: store.set_status(task, to='done', actor='claude'), '(allowed: blocked, cancelled, review)')
        _error(lambda: store.show('T-9999'), 'hipson task list')
        holder = store.assign(task, actor='codex', role='builder', mode='rw', scope=['src'])['id']
        _error(lambda: store.assign(task, actor='other', role='builder', mode='rw', scope=['src']),
               f'hipson task activity --assignment {holder}', ledger.ClaimConflictError)


def test_resume_does_not_call_a_passed_unstructured_worker_a_failed_attempt(tmp_path):
    from hipson import lead_context
    project = _project(tmp_path)
    with closing(ledger.Ledger(tmp_path / 'ledger.sqlite')) as store:
        task = _open(store, project)
        passed = store.assign(task, actor='codex', role='builder', mode='ro')['id']
        store.finish(passed, result='passed', summary='done', failure_journal={'result_state': 'none', 'exit_code': 0})
        rejected = store.assign(task, actor='codex', role='builder', mode='ro')['id']
        store.finish(rejected, result='passed', summary='bad report', failure_journal={'result_state': 'rejected'})
        view = lead_context.build(store, resume=True)
    failed = [item['id'] for item in view['tasks'][0]['failed_attempts']]
    assert failed == [rejected]
