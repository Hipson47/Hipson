"""Regressions from the 1.6 stabilization review (also runs without pytest)."""

import io
import json
import os
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from hipson import ledger, mcp_server, task_activity, task_verification
from hipson.redaction import redact_text
from tests.test_ledger import _error
from tests.test_task_verification import _git, _project

PY = Path(sys.executable).as_posix()


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
