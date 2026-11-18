"""hipson task flow: spec contract, review gate, bounded repair, recovery and refusal states."""

import json
import os
import signal
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from unittest import SkipTest
from unittest.mock import patch

import pytest

from hipson import flow_driver, ledger, task_verification
from tests.test_hipson_helpers import REPO_ROOT, run_cli
from tests.test_task_verification import _git

PY = Path(sys.executable).as_posix()
WORKER = str(REPO_ROOT / 'e2e' / 'agents' / 'bin' / 'flow-worker')
TRIM_CHECK = [PY, 'tests/acceptance.py']
SPLIT_CHECK = [PY, '-c', "import sys; sys.path.insert(0, '.'); from src.parser.pairs import parse_pair; "
                         "assert parse_pair('a:b') == ('a', 'b')"]


def _repo(tmp_path):
    project = tmp_path / 'project'
    (project / 'src' / 'parser').mkdir(parents=True)
    (project / 'tests').mkdir()
    (project / 'src' / 'parser' / 'pairs.py').write_text("def parse_pair(text):\n    return tuple(text.split(','))\n")
    (project / 'tests' / 'acceptance.py').write_text(
        "import sys\nsys.path.insert(0, '.')\nfrom src.parser.pairs import parse_pair\n"
        "assert parse_pair('a:b') == ('a', 'b'), parse_pair('a:b')\n"
        "assert parse_pair(' a : b ') == ('a', 'b'), parse_pair(' a : b ')\n")
    (project / '.gitignore').write_text('__pycache__/\n.interrupted-once\n.worker-ready\n')
    _git(project, 'init', '-q')
    _git(project, 'add', '.')
    _git(project, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'Initial')
    return project


def _spec(tmp_path, project, builder='correct', reviewer='strict', checks=(TRIM_CHECK,), **extra):
    spec = {'version': 1, 'title': 'Fix pair parser', 'objective': 'Split a:b and trim whitespace',
            'project': str(project), 'lead': 'lead', 'checks': [list(check) for check in checks],
            'builder': {'actor': 'builder', 'command': [PY, WORKER, 'builder', builder], 'scope': ['src/parser']},
            'reviewer': {'actor': 'reviewer', 'command': [PY, WORKER, 'reviewer', reviewer]},
            'check_timeout': 60, **extra}
    path = tmp_path / f'spec-{len(list(tmp_path.glob("spec-*.json")))}.json'
    path.write_text(json.dumps(spec))
    return path


def _flow(project, db, spec, *args, env=None):
    proc = run_cli(project, 'task', 'flow', '--db', str(db), '--json', '--spec', str(spec), *args,
                   env={'AGENT_RUN_GRACE_SECONDS': '1', **(env or {})}, timeout=180)
    try:
        return proc, json.loads(proc.stdout)
    except ValueError:
        raise AssertionError(proc.stdout + proc.stderr) from None


def _view(db, task='T-0001'):
    with closing(ledger.Ledger(db, read_only=True)) as store:
        return store.show(task), store.events(task)


def test_spec_validation_refuses_invalid_configuration_before_any_write(tmp_path):
    project = _repo(tmp_path)
    base = json.loads(_spec(tmp_path, project).read_text())
    cases = [
        ({k: v for k, v in base.items() if k != 'checks'}, 'spec.checks: required property missing'),
        ({**base, 'checks': []}, 'spec.checks: needs at least 1 item(s)'),
        ({**base, 'version': 2}, 'spec.version: expected one of [1]'),
        ({**base, 'max_repairs': 6}, 'spec.max_repairs: must be between 0 and 5'),
        ({**base, 'max_repairs': True}, 'spec.max_repairs: expected integer'),
        ({**base, 'extra': 1}, "spec: unknown properties ['extra']"),
        ({**base, 'reviewer': {**base['reviewer'], 'actor': 'Builder'}}, 'reviewer.actor must differ'),
        ({**base, 'reviewer': {**base['reviewer'], 'actor': 'lead'}}, 'reviewer.actor must differ'),
        ({**base, 'builder': {**base['builder'], 'scope': []}}, 'spec.builder.scope: needs at least 1 item(s)'),
        ({**base, 'builder': {**base['builder'], 'env_allow': ['GH_TOKEN']}}, 'builder.env_allow needs'),
        ({**base, 'builder': {**base['builder'], 'command': ['  ']}}, 'must not be empty or blank'),
        ({**base, 'check_timeout': float('inf')}, 'spec.check_timeout'),
    ]
    for raw, message in cases:
        with pytest.raises(flow_driver.FlowError) as error:
            flow_driver.validate_spec(raw)
        assert error.value.state == 'invalid_config' and message in str(error.value), (message, error.value)
    duplicate = tmp_path / 'duplicate.json'
    duplicate.write_text('{"version": 1, "version": 1}')
    db = tmp_path / 'ledger.sqlite'
    proc, payload = _flow(project, db, duplicate)
    assert proc.returncode == 64 and payload['state'] == 'invalid_config' and 'duplicate keys' in payload['reasons'][0]
    assert not db.exists()
    spec = flow_driver.validate_spec(base)
    assert spec['max_repairs'] == 2 and spec['close_to'] == 'review' and spec['builder']['observe'] is True
    assert flow_driver.spec_digest(spec) == flow_driver.spec_digest(flow_driver.validate_spec(base))
    checked = run_cli(project, 'task', 'flow', '--db', str(db), '--spec', str(_spec(tmp_path, project)), '--check')
    assert checked.returncode == 0 and 'spec valid' in checked.stdout and not db.exists()


def test_spec_schema_file_matches_the_embedded_contract():
    assert json.loads((REPO_ROOT / 'schemas' / 'task-flow.schema.json').read_text()) == flow_driver.SPEC_SCHEMA


def test_correct_work_is_verified_reviewed_and_left_for_lead_acceptance(tmp_path):
    project, db = _repo(tmp_path), tmp_path / 'ledger.sqlite'
    proc, result = _flow(project, db, _spec(tmp_path, project))
    assert proc.returncode == 0, proc.stderr
    assert result['state'] == 'accepted' and result['status'] == 'review' and result['next'] == 'hipson task close --task T-0001'
    assert result['steps'] == ['start', 'build:build', 'ack', 'verify', 'review', 'ack', 'close', 'stop']
    view, events = _view(db)
    assert view['review_policy'] == 'required' and view['review'] == {
        'required': True, 'passing': True, 'accepted': 'A-0002', 'current': 'A-0002', 'reasons': []}
    assert [item['ack_note'] for item in view['assignments']] == [flow_driver.LEAD_NOTE] * 2
    assert any(event['type'] == 'flow' and event['summary'].startswith('started spec_sha256=') for event in events)
    # Final acceptance stays with the lead, through the ordinary close (the review gate passes).
    closed = run_cli(project, 'task', 'close', '--db', str(db), '--task', 'T-0001')
    assert closed.returncode == 0, closed.stderr
    # Resuming finished work repeats nothing.
    proc, again = _flow(project, db, _spec(tmp_path, project), '--resume', 'T-0001')
    assert proc.returncode == 0 and again['steps'] == ['stop'] and again['status'] == 'done'
    assert len(_view(db)[0]['assignments']) == 2


def test_failing_checks_drive_one_repair_with_their_diagnostic_output(tmp_path):
    project, db = _repo(tmp_path), tmp_path / 'ledger.sqlite'
    proc, result = _flow(project, db, _spec(tmp_path, project, builder='needs-repair', close_to='done'))
    assert proc.returncode == 0, proc.stderr
    assert result['state'] == 'accepted' and result['status'] == 'done' and result['builder_attempts'] == 2
    view, events = _view(db)
    failed = [record for record in task_verification.ledger.Ledger(db, read_only=True).verifications('T-0001')
              if record['exit_code'] != 0]
    assert len(failed) == 1
    repair = [event for event in events if event['type'] == 'flow' and event['summary'].startswith('repair attempt 2 of 3')]
    assert repair and repair[0]['refs'] == [failed[0]['id']]
    brief = (db.parent / '.ledger.sqlite.flows' / 'T-0001' / 'repair-2.brief.md').read_text()
    assert failed[0]['id'] in brief and '<untrusted_data' in brief and "('a', 'b')" in brief
    assert "tuple(part.strip()" in (project / 'src/parser/pairs.py').read_text()


def test_review_finding_drives_repair_and_is_resolved_only_after_a_clean_re_review(tmp_path):
    project, db = _repo(tmp_path), tmp_path / 'ledger.sqlite'
    # The visible check misses trimming; only the independent review catches it.
    proc, result = _flow(project, db, _spec(tmp_path, project, builder='needs-repair', checks=(SPLIT_CHECK,),
                                            close_to='done'))
    assert proc.returncode == 0, proc.stderr
    assert result['state'] == 'accepted' and result['status'] == 'done'
    assert result['steps'] == ['start', 'build:build', 'ack', 'verify', 'review', 'ack', 'build:repair', 'ack',
                               'verify', 'review', 'ack', 'resolve', 'close', 'stop']
    with closing(ledger.Ledger(db, read_only=True)) as store:
        findings = store.findings(task='T-0001')
    assert [(item['status'], item['source_assignment'], item['resolved_by_assignment']) for item in findings] == [
        ('fixed', 'A-0002', 'A-0003')]
    brief = (db.parent / '.ledger.sqlite.flows' / 'T-0001' / 'review-2.brief.md').read_text()
    assert findings[0]['id'] in brief


def test_budget_exhaustion_and_false_green_never_reach_review(tmp_path):
    project, db = _repo(tmp_path), tmp_path / 'ledger.sqlite'
    proc, result = _flow(project, db, _spec(tmp_path, project, builder='false-green', max_repairs=1))
    assert proc.returncode == 2 and result['state'] == 'checks_failed' and result['status'] == 'in_progress'
    assert result['builder_attempts'] == 2 and 'repair budget spent: 2 of 2 builder attempts' in result['reasons'][-1]
    # Resuming cannot reset the budget: it is counted from the ledger.
    proc, again = _flow(project, db, _spec(tmp_path, project, builder='false-green', max_repairs=1), '--resume', 'T-0001')
    assert proc.returncode == 2 and again['steps'] == ['stop'] and len(_view(db)[0]['assignments']) == 2
    proc, blocked = _flow(project, db, _spec(tmp_path, project, builder='never-fixes', checks=(SPLIT_CHECK,),
                                             max_repairs=0))
    assert proc.returncode == 2 and blocked['state'] == 'review_blocked' and blocked['status'] == 'in_progress'
    assert blocked['open_findings'] and 'repair budget spent: 1 of 1' in blocked['reasons'][-1]


def test_tampered_tests_are_caught_as_out_of_scope_and_need_the_lead(tmp_path):
    project, db = _repo(tmp_path), tmp_path / 'ledger.sqlite'
    proc, result = _flow(project, db, _spec(tmp_path, project, builder='tamper'))
    assert proc.returncode == 4 and result['state'] == 'needs_lead' and result['status'] == 'in_progress'
    assert 'not from a review' in result['reasons'][0]
    view, _ = _view(db)
    assert view['findings'][0]['severity'] == 'major' and not ledger.Ledger(db, read_only=True).verifications('T-0001')


def test_worker_failures_stop_with_actionable_states_and_retry_is_explicit(tmp_path):
    project, db = _repo(tmp_path), tmp_path / 'ledger.sqlite'
    crash = _spec(tmp_path, project, builder='crash')
    proc, result = _flow(project, db, crash)
    assert proc.returncode == 3 and result['state'] == 'worker_failed' and 'A-0001 failed (exit 5)' in result['reasons'][0]
    proc, retried = _flow(project, db, crash, '--resume', 'T-0001', '--retry')
    assert proc.returncode == 3 and retried['builder_attempts'] == 2 and retried['steps'][0] == 'build:build'
    proc, malformed = _flow(project, db, _spec(tmp_path, project, builder='malformed'))
    assert proc.returncode == 3 and 'no valid structured result (result file: rejected)' in malformed['reasons'][0]
    proc, reviewer = _flow(project, db, _spec(tmp_path, project, reviewer='crash'))
    assert proc.returncode == 3 and reviewer['state'] == 'reviewer_failed' and reviewer['status'] == 'in_progress'
    retry = run_cli(project, 'task', 'flow', '--db', str(db), '--spec', str(crash), '--retry')
    assert retry.returncode == 64 and '--retry needs --resume' in retry.stderr


def test_resume_refuses_changed_specs_checks_and_foreign_tasks(tmp_path):
    project, db = _repo(tmp_path), tmp_path / 'ledger.sqlite'
    spec = _spec(tmp_path, project, builder='crash')
    _flow(project, db, spec)
    proc, changed = _flow(project, db, _spec(tmp_path, project, builder='crash', max_repairs=5), '--resume', 'T-0001')
    assert proc.returncode == 65 and changed['state'] == 'conflict' and 'spec differs' in changed['reasons'][0]
    run_cli(project, 'task', 'checks', '--db', str(db), '--task', 'T-0001', '--check', 'true', '--reason', 'weaken')
    proc, checks = _flow(project, db, spec, '--resume', 'T-0001')
    assert proc.returncode == 65 and 'declared checks changed' in ' '.join(checks['reasons'])
    run_cli(project, 'task', 'open', '--db', str(db), '--project', str(project), '--title', 'Manual', '--objective', 'x')
    proc, foreign = _flow(project, db, spec, '--resume', 'T-0002')
    assert proc.returncode == 65 and 'not started by hipson task flow' in foreign['reasons'][0]


def test_a_worker_cannot_run_or_resume_the_flow_that_judges_it(tmp_path):
    project, db = _repo(tmp_path), tmp_path / 'ledger.sqlite'
    proc = run_cli(project, 'task', 'flow', '--db', str(db), '--spec', str(_spec(tmp_path, project)),
                   env={'HIPSON_ROLE': 'worker'})
    assert proc.returncode == 1 and 'worker context' in proc.stderr and not db.exists()
    event = run_cli(project, 'task', 'event', '--db', str(db), '--task', 'T-0001', '--actor', 'w', '--type', 'flow',
                    '--summary', 'started spec_sha256=forged')
    assert event.returncode != 0


@pytest.mark.skipif(sys.platform == 'win32', reason='requires POSIX signals to interrupt hipson task do; Windows shares console Ctrl+C')
def test_an_interrupted_flow_resumes_without_repeating_completed_work(tmp_path):
    project, db = _repo(tmp_path), tmp_path / 'ledger.sqlite'
    spec = _spec(tmp_path, project, builder='interrupt-once', close_to='done')
    env = {**os.environ, 'PYTHONPATH': str(REPO_ROOT / 'src'), 'AGENT_RUN_GRACE_SECONDS': '1'}
    flow = subprocess.Popen([sys.executable, '-m', 'hipson.cli', 'task', 'flow', '--db', str(db), '--json', '--spec',
                             str(spec)], cwd=project, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True)
    deadline = time.monotonic() + 60
    while not (project / "src/parser/.worker-ready").exists():
        if time.monotonic() > deadline or flow.poll() is not None:
            flow.kill()
            raise AssertionError(flow.communicate())
        time.sleep(0.05)
    flow.send_signal(signal.SIGTERM)
    stdout, stderr = flow.communicate(timeout=60)
    assert flow.returncode == 130 and json.loads(stdout)['state'] == 'interrupted', stderr
    view, _ = _view(db)
    assert [item['status'] for item in view['assignments']] == ['cancelled']
    proc, resumed = _flow(project, db, spec, '--resume', 'T-0001')
    assert proc.returncode == 0 and resumed["status"] == "done", (proc.stderr, resumed)
    assert resumed['steps'][:2] == ['ack', 'build:build']
    view, events = _view(db)
    assert [(item['role'], item['status']) for item in view['assignments']] == [
        ('builder', 'cancelled'), ('builder', 'passed'), ('reviewer', 'passed')]
    assert any(event['summary'].startswith('interrupted during builder run') for event in events)


def test_review_policy_gate_on_the_manual_path(tmp_path):
    project = _repo(tmp_path)
    db = tmp_path / 'ledger.sqlite'
    state = {'revision': 'a' * 40, 'subject': 's', 'fingerprint': 'f' * 64, 'dirty': False, 'error': None,
             'files': [], 'generated_files': []}
    with patch.object(task_verification, 'project_state', return_value=dict(state)), \
            closing(ledger.Ledger(db)) as store:
        task = store.open_task(project=project, title='Gate', objective='Review gate', lead='lead',
                               review_policy='required')['id']
        assert store.events(task)[-1]['type'] == 'review_policy'
        store.set_status(task, to='in_progress', actor='lead')
        build = store.assign(task, actor='builder', role='builder', mode='rw', scope=['src'])['id']
        store.finish(build, result='passed', summary='built')
        store.ack(build)

        def verify():
            record = {'id': 'V-' + os.urandom(16).hex(), 'task': task, 'actor': 'lead',
                      'context': {'role': None, 'assignment': None, 'task': None}, 'exit_code': 0,
                      'command': ['true'], 'argv_sha256': task_verification.argv_identity(['true']),
                      'builder_finished_seq': store._builder_finished_seq(task), 'state_changed': False,
                      'cleanup_confirmed': True, 'coverage': 'complete', 'metadata_fallback_paths': [], **state}
            store._record_verification(record)

        def review(actor, *, mode='ro', result=True):
            item = store.assign(task, actor=actor, role='reviewer', mode=mode, scope=['src'] if mode == 'rw' else [])['id']
            store.finish(item, result='passed', summary='reviewed', worker_result={
                'status': 'completed', 'summary': 'ok', 'files_changed': [], 'commands': [], 'findings': [],
                'risks': []} if result else None)
            store.ack(item)
            return item

        early = review('reviewer')  # Before verification: did not review the verified tree.
        verify()
        assert store.show(task)['review']['reasons'] == [
            f'review required: latest review {early} does not qualify: it started before the accepted lead '
            'verification, so it did not review the verified tree; run an independent read-only reviewer after the '
            'latest builder and lead verification (hipson task flow, or hipson task run --role reviewer --mode ro)']
        store.set_status(task, to='review', actor='lead')  # Review is where the review happens: not gated.
        with pytest.raises(ledger.LedgerError, match='review required'):
            store.set_status(task, to='done', actor='lead')
        with pytest.raises(ledger.CloseRefusedError, match='review required'):
            store.close_task(task, to='done', actor='lead')
        self_review = review('BUILDER')
        assert f'{self_review} does not qualify: actor BUILDER is the lead or a builder' in store.show(task)['review']['reasons'][0]
        unstructured = review('other', result=False)
        assert 'no completed structured result' in store.show(task)['review']['reasons'][0] and unstructured
        writer = review('writer', mode='rw')
        assert 'is not read-only' in store.show(task)['review']['reasons'][0] and writer
        good = review('reviewer')
        view = store.show(task)
        assert view['review'] == {'required': True, 'passing': True, 'accepted': good, 'current': good, 'reasons': []}
        store.close_task(task, to='done', actor='lead')
        # A task without the policy is unaffected.
        plain = store.open_task(project=project, title='Plain', objective='No gate')['id']
        assert store.show(plain)['review'] == {'required': False, 'passing': True, 'accepted': None, 'current': None,
                                               'reasons': []}
    with pytest.raises(ledger.LedgerError, match='Review policy must be one of'):
        ledger.Ledger(db).open_task(project=project, title='x', objective='x', review_policy='optional')


def test_v10_ledgers_migrate_to_the_review_policy_column(tmp_path):
    db = tmp_path / 'ledger.sqlite'
    with closing(ledger.Ledger(db)) as store:
        store.open_task(project=tmp_path, title='Old', objective='Before review policies')
        store.connection.execute('BEGIN IMMEDIATE')
        store.connection.execute('ALTER TABLE tasks DROP COLUMN review_policy')
        store.connection.execute('UPDATE schema_version SET version = 10')
        store.connection.commit()
    with closing(ledger.Ledger(db, read_only=True)) as store:
        assert store.show('T-0001')['review_policy'] == 'none'
        assert store.connection.execute('SELECT version FROM schema_version').fetchone()[0] == 10
    with closing(ledger.Ledger(db)) as store:
        assert store.connection.execute('SELECT version FROM schema_version').fetchone()[0] == 11
        assert store.show('T-0001')['review'] == {'required': False, 'passing': True, 'accepted': None,
                                                  'current': None, 'reasons': []}


def test_decide_is_pure_and_stops_on_live_work():
    view = {'id': 'T-0001', 'status': 'in_progress', 'assignments': [
        {'id': 'A-0001', 'status': 'active', 'role': 'builder', 'ack_state': 'not_required'}],
        'claims': [], 'findings': []}
    spec = {'close_to': 'review', 'max_repairs': 2}
    step = flow_driver.decide(view, [], spec, verified_for=set(), retry=False)
    assert step.kind == 'stop' and step.state == 'running' and flow_driver.EXIT[step.state] == 75
    if sys.platform == 'win32':
        raise SkipTest('remaining cases are platform independent and covered above')


def test_runner_progress_state_inside_the_project_is_not_attributed_to_the_worker(tmp_path):
    # Found by scripts/flow_compare.py: with TMPDIR inside the project, the runner's own progress file was
    # reported as an out-of-scope change of the worker.
    project, db = _repo(tmp_path), tmp_path / 'ledger.sqlite'
    (project / '.tmp').mkdir()
    with open(project / '.gitignore', 'a') as handle:
        handle.write('.tmp/' + chr(10))
    proc, result = _flow(project, db, _spec(tmp_path, project), env={'TMPDIR': str(project / '.tmp')})
    assert proc.returncode == 0 and result['state'] == 'accepted', result
    view, _ = _view(db)
    assert [path['attribution'] for path in view['assignments'][0]['changes']['paths']] == ['in_scope']
