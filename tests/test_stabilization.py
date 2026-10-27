"""Regressions from the 1.6 stabilization review (also runs without pytest)."""

import io
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

from hipson import init_process, ledger, mcp_server, task_activity, task_runner, task_verification
from hipson.redaction import redact_text
from hipson.sandbox import git_filter_overrides, prepare_read_only_command
from hipson.task_windows import batch_command
from tests.test_ledger import _error
from tests.test_task_verification import _git, _project

PY = Path(sys.executable).as_posix()
SRC = str(Path(__file__).resolve().parents[1] / 'src')


def test_batch_targets_get_their_arguments_through_the_environment_not_cmd_parsing(tmp_path):
    target = tmp_path / 'worker.cmd'
    target.write_text('@echo off\n')
    command, env = batch_command([str(target), 'a&whoami', '100%', 'x^y'], {'KEEP': '1'}, 'HIPSON_T_')
    assert isinstance(command, str) and 'whoami' not in command and '%HIPSON_T_1%' in command
    assert env['HIPSON_T_1'] == 'a&whoami' and env['HIPSON_T_2'] == '100%' and env['KEEP'] == '1'
    assert batch_command([PY, 'a&b'], {}, 'HIPSON_T_') == ([PY, 'a&b'], {})  # Executables are untouched.
    _error(lambda: batch_command([str(target), 'say "hi"'], {}, 'HIPSON_T_'), 'quotes', ValueError)


def test_read_only_git_reads_disable_configured_filter_drivers(tmp_path):
    project = _project(tmp_path)
    _git(project, 'config', 'filter.mark.clean', 'touch marker')
    _git(project, 'config', 'filter.mark.process', 'touch marker')
    overrides = git_filter_overrides(project)
    assert overrides == ['-c', 'filter.mark.clean=', '-c', 'filter.mark.process=']
    argv, _ = prepare_read_only_command(['git', 'status', '--porcelain'], cwd=project)
    assert argv[argv.index('status') - 4:argv.index('status')] == overrides
    assert git_filter_overrides(tmp_path / 'missing') == []


def test_successful_init_subprocess_tolerates_non_utf8_diagnostics(tmp_path):
    result = init_process.run_contained(
        [sys.executable, '-c', 'import sys; sys.stdout.write("ok"); sys.stderr.buffer.write(bytes([0xE9]))'],
        tmp_path, env=dict(os.environ), input_text=None, timeout=20)
    assert result.returncode == 0 and result.stdout == 'ok' and result.stderr == '�'


def test_interrupting_verification_lets_the_supervisor_stop_its_check(tmp_path):
    if sys.platform == 'win32':
        raise SkipTest('requires POSIX signals to interrupt hipson task do; Windows shares console Ctrl+C')
    project = _project(tmp_path)
    db = tmp_path / 'ledger.sqlite'
    with closing(ledger.Ledger(db)) as store:
        task = store.open_task(project=project, title='Stop', objective='Interrupt verification')['id']
    ready, pid_file = tmp_path / 'ready', tmp_path / 'check.pid'
    check = (f'import os, time; open({str(pid_file)!r}, "w").write(str(os.getpid())); '
             f'open({str(ready)!r}, "w").close(); time.sleep(60)')
    lead = (f'import sys; sys.path.insert(0, {SRC!r}); from pathlib import Path; '
            f'from hipson import task_verification; '
            f'task_verification.run(Path({str(db)!r}), {task!r}, [{PY!r}, "-c", {check!r}], timeout=60)')
    env = {key: value for key, value in os.environ.items() if not key.startswith('HIPSON_')}
    env.update(PYTHONPATH=SRC, AGENT_RUN_GRACE_SECONDS='2')
    process = subprocess.Popen([sys.executable, '-c', lead], cwd=project, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 20
        while not ready.exists() and time.monotonic() < deadline and process.poll() is None:
            time.sleep(0.05)
        assert ready.exists(), process.communicate(timeout=5)
        process.send_signal(signal.SIGINT)
        process.communicate(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
    pid = int(pid_file.read_text())
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        pass
    else:
        os.kill(pid, signal.SIGKILL)
        raise AssertionError('The interrupted verification check survived')
    with closing(ledger.Ledger(db, read_only=True)) as store:
        records = store.verifications(task)
    assert len(records) == 1 and records[0]['exit_code'] == 130  # The supervisor recorded the interruption.


def test_supervise_times_out_by_stopping_then_killing(tmp_path):
    started = time.monotonic()
    _error(lambda: task_runner.supervise([sys.executable, '-c', 'import time; time.sleep(30)'], timeout=0.5),
           'timed out', subprocess.TimeoutExpired)
    assert time.monotonic() - started < 20


def _open(store, project, checks=()):
    task = store.open_task(project=project, title='Stable', objective='Stabilization regressions', checks=checks)['id']
    store.set_status(task, to='in_progress', actor='claude')
    return task


def test_worker_journal_never_runs_repository_filter_helpers(tmp_path):
    project = _project(tmp_path)
    marker = tmp_path / 'filter-ran'
    script = tmp_path / 'filter.py'
    script.write_text(f'import sys\nopen({str(marker)!r}, "w").close()\nsys.stdout.write(sys.stdin.read())\n')
    _git(project, 'config', 'filter.mark.clean', f'"{PY}" "{script.as_posix()}"')
    (project / '.gitattributes').write_text('*.py filter=mark\n')
    os.utime(project / 'source.py', (1, 1))  # Stale stat data makes status re-hash tracked content.
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith('GIT_')}
    subprocess.run(['git', '-C', str(project), 'status', '--porcelain'], env=env, capture_output=True, check=True)
    assert marker.exists(), 'fixture must prove that plain git status runs the configured filter'
    marker.unlink()
    os.utime(project / 'source.py', (2, 2))
    journal = task_activity.project_changes(str(project))
    assert not marker.exists() and isinstance(journal['changed_files'], list)


def test_close_to_review_rechecks_active_assignments_atomically(tmp_path):
    project = _project(tmp_path)
    with closing(ledger.Ledger(tmp_path / 'ledger.sqlite')) as store:
        task = _open(store, project)
        task_verification.run(store.path, task, [PY, '-c', 'pass'])
        store.assign(task, actor='late', role='builder', mode='rw', scope=['src'])
        _error(lambda: store.close_task(task, to='review', actor='claude'), 'active assignment',
               ledger.CloseRefusedError)
        assert store.show(task)['status'] == 'in_progress'


def test_reserved_event_types_are_refused_and_free_events_still_work(tmp_path):
    project = _project(tmp_path)
    with closing(ledger.Ledger(tmp_path / 'ledger.sqlite')) as store:
        task = _open(store, project)
        for forged in ('status', 'opened', 'lead_verified', 'checks_set', 'review_override', 'finding_opened'):
            _error(lambda forged=forged: store.add_event(task, actor='x', type=forged, summary='forged'), 'reserved')
        assert store.add_event(task, actor='x', type='progress', summary='ok')['type'] == 'progress'
        assert store.add_event(task, actor='x', type='project_mismatch_override', summary='runner')['type']


def test_lone_surrogates_are_refused_at_ingestion_and_never_crash_mcp_output(tmp_path):
    _error(lambda: ledger.require_text('bad \ud800', 'summary'), 'lone surrogate')
    result = {'status': 'completed', 'summary': 'bad \ud800', 'files_changed': [], 'commands': [],
              'findings': [], 'risks': []}
    _error(lambda: ledger.validate_task_result(result), 'lone surrogate')
    raw = io.BytesIO()
    stdout = io.TextIOWrapper(raw, encoding='utf-8', errors='strict', newline='\n')
    with patch.object(mcp_server, '_handle_request', return_value={'jsonrpc': '2.0', 'id': 1, 'result': 'x\ud800'}):
        mcp_server.serve_stdio(project_path=tmp_path, stdin=io.StringIO('{}\n'), stdout=stdout)
    stdout.flush()
    assert json.loads(raw.getvalue().decode('utf-8'))['result'] == 'x\ud800'


def test_verification_inventory_redacts_paths_but_keeps_freshness_identity(tmp_path):
    project = _project(tmp_path)
    name = 'token=review_dummy_value_1234567890.txt'
    assert redact_text(name) != name, 'fixture name must be secret-shaped for the redactor'
    (project / name).write_text('one\n')
    with closing(ledger.Ledger(tmp_path / 'ledger.sqlite')) as store:
        task = _open(store, project)
        record = task_verification.run(store.path, task, [PY, '-c', 'pass'])
        stored = store.connection.execute('SELECT evidence FROM verifications WHERE id = ?', (record['id'],)).fetchone()[0]
        assert 'review_dummy_value_1234567890' not in stored and 'review_dummy_value_1234567890' not in json.dumps(record)
        assert store.set_status(task, to='review', actor='claude')['verification']['passing']
        (project / name).write_text('two\n')
        reasons = store.show(task)['verification']['reasons']
        assert any('stale: working tree changed' in reason for reason in reasons)
        assert 'review_dummy_value_1234567890' not in ' '.join(reasons)
