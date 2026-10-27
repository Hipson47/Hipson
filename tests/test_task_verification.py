"""Revision-bound verification acceptance (also runs without pytest)."""

import importlib.util
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from unittest import SkipTest
from unittest.mock import patch

from hipson import lead_context, ledger, task_context, task_verification
from tests.test_hipson_helpers import run_cli
from tests.test_ledger import _error, _rows, assert_strict_schema
from tests.test_ledger_mcp import _assert_error, _call, _payload
from tests.test_ledger_recovery import _v1_file


def verify_fixture(store, task):
    """Execute a trivial lead check for tests whose subject is another lifecycle invariant."""
    project = Path(store.show(task)['project'])
    project.mkdir(parents=True, exist_ok=True)
    return task_verification.run(store.path, task, [sys.executable, '-c', 'pass'])


def _git(root, *args):
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith('GIT_')}
    return subprocess.run(['git', '-C', str(root), *args], env=env, capture_output=True,
                          text=True, timeout=10, check=True).stdout.strip()


def _project(tmp_path, *, gitignore=True):
    project = tmp_path / 'project'
    project.mkdir()
    _git(project, 'init', '-q')
    (project / 'source.py').write_text('value = 1\n')
    if gitignore:
        (project / '.gitignore').write_text('__pycache__/\n')
    _git(project, 'add', '.')
    _git(project, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'Initial candidate')
    return project


def _task(store, project):
    task = store.open_task(project=project, title='Candidate', objective='Require independent current evidence')['id']
    store.set_status(task, to='in_progress', actor='lead')
    return task


def test_verification_missing_worker_claims_legacy_events_and_force(tmp_path):
    project = _project(tmp_path)
    with closing(ledger.Ledger(tmp_path / 'ledger.sqlite')) as store:
        task = _task(store, project)
        assignment = store.assign(task, actor='worker', role='builder', mode='rw', scope=['source.py'])
        report = {'status': 'completed', 'summary': 'Tests passed', 'files_changed': [],
                  'commands': [{'command': 'tests', 'exit_code': 0}], 'findings': [], 'risks': []}
        store.finish(assignment['id'], result='passed', summary='All done', worker_result=report)
        store.ack(assignment['id'])
        store.add_event(task, actor='lead', type='verification', summary='pass', refs=[_git(project, 'rev-parse', 'HEAD')])
        before = _rows(store)
        _error(lambda: store.set_status(task, to='review', actor='lead'), 'missing lead verification')
        assert _rows(store) == before
        _error(lambda: store.set_status(task, to='review', actor='lead', force=True), 'Override reason')
        store.set_status(task, to='review', actor='lead', force=True, reason='Document missing evidence')
        _error(lambda: store.set_status(task, to='done', actor='lead'), 'missing lead verification')
        store.set_status(task, to='done', actor='lead', force=True, reason='Explicit exception')
        overrides = [event for event in store.events(task) if event['type'].endswith('_override')]
        assert [event['type'] for event in overrides] == ['review_override', 'done_override']
        assert all('missing lead verification' in event['summary'] for event in overrides)
        assert 'OVERRIDDEN' in store.events(task)[-1]['summary']


def test_verify_records_real_command_exit_timing_revision_and_redacted_tail(tmp_path):
    project = _project(tmp_path)
    db = tmp_path / 'ledger.sqlite'
    with closing(ledger.Ledger(db)) as store:
        task = _task(store, project)
        command = [sys.executable, '-c', 'import os; print(os.getcwd()); print(os.environ["TEST_API_KEY"]); raise SystemExit(7)']
        with patch.dict(os.environ, TEST_API_KEY='private-verification-credential'):
            result = run_cli(project, 'task', 'verify', '--db', str(db), '--task', task,
                                          '--label', 'acceptance', '--json', '--', *command)
        assert result.returncode == 7, (result.stdout, result.stderr)
        record = json.loads(result.stdout)
        assert record['command'] == command and record['exit_code'] == 7
        assert record['duration_seconds'] >= 0 and record['started_at'] <= record['finished_at']
        assert record['revision'] == _git(project, 'rev-parse', 'HEAD') and len(record['fingerprint']) == 64
        assert record['subject'] == 'Initial candidate' and record['dirty'] is False
        assert str(project) in record['output_tail'] and 'private-verification-credential' not in json.dumps(record)
        _error(lambda: store.set_status(task, to='review', actor='lead'), 'failing verification: exit 7')
        passed = task_verification.run(db, task, [sys.executable, '-c', 'print("passed")'])
        view = store.set_status(task, to='review', actor='lead')
        assert view['verification']['passing'] and passed['id'] in store.events(task)[-1]['refs']
        assert_strict_schema('task.schema.json', view)
        assert passed['id'] in task_context.brief(store, task, role='reviewer')['verification']
        assert lead_context.build(store)['tasks'][0]['verification']['freshness'] == 'current'
        store.set_status(task, to='done', actor='lead')
        assert store.verifications(task) == [record, passed]
        try:
            store.connection.execute('UPDATE verifications SET evidence = evidence')
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError('Verification history must be immutable')


def test_verify_stale_head_index_tracked_untracked_and_builder_finish(tmp_path):
    project = _project(tmp_path)
    db = tmp_path / 'ledger.sqlite'
    with closing(ledger.Ledger(db)) as store:
        task = _task(store, project)
        verify_fixture(store, task)
        store.set_status(task, to='review', actor='lead')
        file = project / 'source.py'
        old = file.stat()
        file.write_text('value = 2\n')
        os.utime(file, ns=(old.st_atime_ns, old.st_mtime_ns))
        _error(lambda: store.set_status(task, to='done', actor='lead'), 'working tree changed')
        verify_fixture(store, task)
        _git(project, 'add', '.')
        _error(lambda: store.set_status(task, to='done', actor='lead'), 'working tree changed')
        verify_fixture(store, task)
        (project / 'new.py').write_text('new = True\n')
        _error(lambda: store.set_status(task, to='done', actor='lead'), 'working tree changed')
        verify_fixture(store, task)
        (project / 'new.py').write_text('new = False\n')
        _error(lambda: store.set_status(task, to='done', actor='lead'), 'working tree changed')
        verify_fixture(store, task)
        _git(project, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'Later candidate')
        _error(lambda: store.set_status(task, to='done', actor='lead'), 'HEAD moved')
        verify_fixture(store, task)
        assignment = store.assign(task, actor='worker', role='builder', mode='ro')
        store.finish(assignment['id'], result='passed', summary='No changes needed')
        store.ack(assignment['id'])
        _error(lambda: store.set_status(task, to='done', actor='lead'), 'latest finished builder assignment')
        verify_fixture(store, task)
        assert store.set_status(task, to='done', actor='lead')['status'] == 'done'


def test_verify_command_mutation_timeout_and_bounded_output(tmp_path):
    project = _project(tmp_path)
    with closing(ledger.Ledger(tmp_path / 'ledger.sqlite')) as store:
        task = _task(store, project)
        record = task_verification.run(store.path, task, [sys.executable, '-c',
            'from pathlib import Path; Path("source.py").write_text("value = 2\\n"); print("pass")'])
        assert record['exit_code'] == 0 and record['state_changed']
        _error(lambda: store.set_status(task, to='review', actor='lead'), 'changed during verification')
        record = task_verification.run(store.path, task, [sys.executable, '-c', 'import time; time.sleep(60)'], timeout=0.1)
        assert record['exit_code'] == 124 and record['duration_seconds'] < 10
        record = task_verification.run(store.path, task, [sys.executable, '-c',
            'print("x" * 100000); print("-----BEGIN PRIVATE KEY-----"); print("private material"); '
            'print("-----END PRIVATE KEY-----"); print("safe tail")'])
        assert len(record['output_tail']) <= task_verification.OUTPUT_LIMIT
        assert 'private material' not in record['output_tail'] and 'safe tail' in record['output_tail']
        record = task_verification.run(store.path, task, ['hipson-verification-executable-does-not-exist'])
        assert record['exit_code'] == 127


def test_verify_python_import_artifacts_do_not_invalidate_the_candidate(tmp_path):
    project = _project(tmp_path)
    (project / '.gitignore').write_text('ignored-output/\n')
    (project / 'm.py').write_text('value = 1\n')
    (project / 'test_m.py').write_text('import m\nassert m.value == 1\n')
    with closing(ledger.Ledger(tmp_path / 'ledger.sqlite')) as store:
        task = _task(store, project)
        before = task_verification.project_state(str(project), store.path)
        record = task_verification.run(store.path, task, [sys.executable, 'test_m.py'])
        assert record['exit_code'] == 0 and not record['state_changed']
        assert record['fingerprint'] == before['fingerprint']
        assert record['generated_files'] == []  # Tool caches are excluded even without Git ignore rules.
        assert not any(item['path'].startswith('__pycache__/') for item in record['files'])
        view = store.set_status(task, to='review', actor='lead')
        assert view['verification']['passing']
        assert view['verification']['current']['fingerprint'] == record['fingerprint']
        assert_strict_schema('task.schema.json', view)
        # Tool caches can change or disappear without changing the verified candidate.
        for path in (project / '__pycache__').glob('m.*.pyc'):
            path.write_bytes(b'changed artifact')
            path.unlink()
        ignored = project / 'ignored-output'
        ignored.mkdir()
        (ignored / 'build.txt').write_text('ignored')
        assert store.set_status(task, to='done', actor='lead')['verification']['passing']


def test_verify_new_untracked_special_artifacts_are_observations(tmp_path):
    if sys.platform == 'win32':
        return  # Native Windows has no os.mkfifo; ordinary artifact coverage runs there.
    project = _project(tmp_path)
    with closing(ledger.Ledger(tmp_path / 'ledger.sqlite')) as store:
        task = _task(store, project)
        record = task_verification.run(store.path, task, [sys.executable, '-c',
            'from pathlib import Path; Path("test.pipe").write_text("artifact")'])
        assert record['generated_files'] == ['test.pipe'] and not record['state_changed']
        (project / 'test.pipe').unlink()
        os.mkfifo(project / 'test.pipe')
        store.set_status(task, to='review', actor='lead')
        assert store.set_status(task, to='done', actor='lead')['verification']['passing']


def test_verify_retry_excludes_prior_bytecode_but_keeps_late_source_edits_stale(tmp_path):
    for gitignore in (False, True):
        fixture = tmp_path / str(gitignore)
        fixture.mkdir()
        project = _project(fixture, gitignore=gitignore)
        module = project / 'm.py'
        module.write_text('value = 0\n')
        (project / 'test_m.py').write_text('import m\nassert m.value == 1\n')
        with closing(ledger.Ledger(fixture / 'ledger.sqlite')) as store:
            task = _task(store, project)
            command = [sys.executable, 'test_m.py']
            failed = task_verification.run(store.path, task, command)
            assert failed['exit_code'] == 1
            assert list((project / '__pycache__').glob('m.*.pyc'))
            _error(lambda task=task: store.set_status(task, to='review', actor='lead'), 'failing verification')
            fixed = 'value = 1  # repaired\n'  # Change size too, so Python cannot reuse old bytecode.
            module.write_text(fixed)
            passed = task_verification.run(store.path, task, command)
            assert passed['exit_code'] == 0 and not passed['state_changed'], passed
            assert not any(item['path'].startswith('__pycache__/') for item in passed['files'])
            assert store.set_status(task, to='review', actor='lead')['verification']['accepted'] == passed['id']
            late = project / 'late_source.py'
            late.write_text('new = True\n')
            _error(lambda task=task: store.set_status(task, to='done', actor='lead'), 'late_source.py:')
            late.unlink()
            module.write_text('value = 2\n')
            _error(lambda task=task: store.set_status(task, to='done', actor='lead'), 'm.py: state before=')
            module.write_text(fixed)
            assert store.set_status(task, to='done', actor='lead')['verification']['accepted'] == passed['id']


def test_verify_pytest_cache_and_ignored_files_do_not_invalidate_the_candidate(tmp_path):
    project = _project(tmp_path)
    (project / 'test_example.py').write_text('def test_value():\n    assert 1 + 1 == 2\n')
    with closing(ledger.Ledger(tmp_path / 'ledger.sqlite')) as store:
        task = _task(store, project)
        if importlib.util.find_spec('pytest'):
            command = [sys.executable, '-m', 'pytest', '-q', 'test_example.py']
        else:
            # Preserve the stdlib-only runner: reproduce pytest's self-ignoring cache
            # when pytest isn't installed. Dev/CI environments exercise real pytest.
            command = [sys.executable, '-c', 'from pathlib import Path; p = Path(".pytest_cache"); '
                       'p.mkdir(); (p / ".gitignore").write_text("*\\n"); (p / "README.md").write_text("cache")']
        record = task_verification.run(store.path, task, command)
        assert record['exit_code'] == 0 and not record['state_changed'], record
        assert (project / '.pytest_cache').is_dir()
        assert record['generated_files'] == []  # Both pytest's cache and bytecode are Git-ignored here.
        store.set_status(task, to='review', actor='lead')
        (project / '.pytest_cache' / 'README.md').write_text('changed ignored artifact')
        assert store.set_status(task, to='done', actor='lead')['verification']['passing']


def test_fingerprint_excludes_only_known_untracked_tool_caches(tmp_path):
    caches = (
        '__pycache__/m.pyc', 'pkg/__pycache__/data', 'pkg/module.pyc', 'module.pyo', '.pyc',
        '.pytest_cache/v/cache/nodeids', 'pkg/.mypy_cache/state', '.ruff_cache/state',
        '.hypothesis/examples/data', '.coverage', 'pkg/.coverage', '.tox/py/lib/file',
        'pkg/.nox/session/file', 'node_modules/.cache/data', 'web/node_modules/.cache/data', '.gradle/state',
    )
    sources = ('target/source.rs', 'build/source.py', 'node_modules/package/source.js', '.cache/source.py',
               'pkg/late_source.py', '.coverage.source', 'module.py')
    for git in (False, True):
        fixture = tmp_path / str(git)
        fixture.mkdir()
        project = _project(fixture, gitignore=False) if git else fixture / 'project'
        project.mkdir(exist_ok=True)
        db = fixture / 'ledger.sqlite'
        before = task_verification.project_state(str(project), db)
        assert before['fingerprint'] and before['dirty'] is False, before
        for name in caches:
            path = project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('old cache')
        cached = task_verification.project_state(str(project), db)
        assert cached['fingerprint'] == before['fingerprint'] and cached['dirty'] is False, cached
        for name in caches:
            (project / name).write_text('changed cache')
        assert task_verification.project_state(str(project), db) == cached
        for name in sources:
            path = project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('source')
            changed = task_verification.project_state(str(project), db)
            assert changed['fingerprint'] != cached['fingerprint'] and changed['dirty'] is True, name
            assert name in {item['path'] for item in changed['files']}, name
            path.unlink()
        assert task_verification.project_state(str(project), db) == cached


def test_fingerprint_build_target_need_gitignore_and_tracked_caches_stay_covered(tmp_path):
    project = _project(tmp_path)
    db = tmp_path / 'ledger.sqlite'
    (project / '.gitignore').write_text('build/\ntarget/\n')
    before = task_verification.project_state(str(project), db)
    for name in ('build/source.py', 'target/source.rs'):
        path = project / name
        path.parent.mkdir()
        path.write_text('ignored artifact')
    assert task_verification.project_state(str(project), db) == before
    # Ignore rules and cache exclusions must never hide indexed or committed content.
    for name in ('module.pyc', '__pycache__/m.pyc', '.coverage', 'node_modules/.cache/file', 'target/source.rs'):
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('indexed content')
        _git(project, 'add', '-f', name)
    indexed = task_verification.project_state(str(project), db)
    with closing(ledger.Ledger(db)) as store:
        task = _task(store, project)
        record = verify_fixture(store, task)
        assert record['exit_code'] == 0 and not record['state_changed']
        store.set_status(task, to='review', actor='lead')
        for name in ('module.pyc', '__pycache__/m.pyc', '.coverage', 'node_modules/.cache/file', 'target/source.rs'):
            path = project / name
            assert any(item['path'] == name and item['tracked'] for item in indexed['files']), name
            path.write_text('changed indexed content')
            _error(lambda: store.set_status(task, to='done', actor='lead'), name + ':')
            path.write_text('indexed content')
        assert store.set_status(task, to='done', actor='lead')['verification']['passing']
    _git(project, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'Track artifacts')
    committed = task_verification.project_state(str(project), db)
    (project / 'module.pyc').write_text('changed committed content')
    assert task_verification.project_state(str(project), db)['fingerprint'] != committed['fingerprint']


def test_verify_preexisting_untracked_and_tracked_changes_have_precise_reasons(tmp_path):
    for name in ('source.py', 'candidate.py'):
        fixture = tmp_path / name
        fixture.mkdir()
        project = _project(fixture)
        target = project / name
        target.write_text('value = 1\n')
        with closing(ledger.Ledger(fixture / 'ledger.sqlite')) as store:
            task = _task(store, project)
            original = task_verification.project_state(str(project), store.path)
            record = task_verification.run(store.path, task, [sys.executable, '-c',
                f'from pathlib import Path; Path({name!r}).write_text("value = 2\\n")'])
            assert record['exit_code'] == 0 and record['state_changed']
            assert name in record['changes'][0] and 'before=' in record['changes'][0] and 'after=' in record['changes'][0]
            assert record['fingerprint'] == original['fingerprint'] and record['generated_files'] == []
            _error(lambda task=task: store.set_status(task, to='review', actor='lead'), 'changed during verification')
            verify_fixture(store, task)
            target.unlink()
            _error(lambda task=task: store.set_status(task, to='review', actor='lead'), name + ': state before=')


def test_generated_artifacts_becoming_tracked_require_new_verification(tmp_path):
    project = _project(tmp_path)
    with closing(ledger.Ledger(tmp_path / 'ledger.sqlite')) as store:
        task = _task(store, project)
        record = task_verification.run(store.path, task, [sys.executable, '-c',
            'from pathlib import Path; Path("generated.txt").write_text("artifact")'])
        assert record['generated_files'] == ['generated.txt'] and not record['state_changed']
        store.set_status(task, to='review', actor='lead')
        _git(project, 'add', 'generated.txt')
        _error(lambda: store.set_status(task, to='done', actor='lead'), 'generated.txt:')


def test_latest_failed_verification_cannot_reuse_an_older_pass(tmp_path):
    project = _project(tmp_path)
    with closing(ledger.Ledger(tmp_path / 'ledger.sqlite')) as store:
        task = _task(store, project)
        verify_fixture(store, task)
        record = task_verification.run(store.path, task, [sys.executable, '-c', 'raise SystemExit(3)'])
        assert not record['state_changed']
        _error(lambda: store.set_status(task, to='review', actor='lead'), 'failing verification: exit 3')


def test_verification_read_only_git_avoids_filters_hooks_pagers_and_overrides(tmp_path):
    project = _project(tmp_path)
    sentinel = tmp_path / 'executed'
    malicious = f'echo executed > "{sentinel}"'
    _git(project, 'config', 'filter.evidence.clean', malicious)
    _git(project, 'config', 'filter.evidence.process', malicious)
    _git(project, 'config', 'core.fsmonitor', malicious)
    _git(project, 'config', 'core.pager', malicious)
    (project / '.gitattributes').write_text('*.py filter=evidence\n')
    (project / 'source.py').write_text('value = 2\n')
    db = tmp_path / 'ledger.sqlite'
    with patch.dict(os.environ, GIT_DIR='/unavailable', GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='core.pager',
                    GIT_CONFIG_VALUE_0=malicious, GIT_EXTERNAL_DIFF=malicious):
        state = task_verification.project_state(str(project), db)
    assert state['fingerprint'] and state['dirty'] is True and not sentinel.exists(), state
    try:
        (project / 'outside').symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        if sys.platform == 'win32':
            raise SkipTest('Windows symlinks require Developer Mode or SeCreateSymbolicLinkPrivilege') from None
        raise
    state = task_verification.project_state(str(project), db)
    assert state['fingerprint'], state  # Hash the symlink target, never traverse it.


def test_verification_non_git_and_v5_migration_are_idempotent_and_read_only(tmp_path):
    db = tmp_path / 'old.sqlite'
    _v1_file(db)
    with closing(sqlite3.connect(db)) as connection:
        for migration in (ledger.MIGRATION_V1_TO_V2, ledger.MIGRATION_V2_TO_V3,
                          ledger.MIGRATION_V3_TO_V4, ledger.MIGRATION_V4_TO_V5):
            for statement in migration:
                connection.execute(statement)
        connection.execute("UPDATE tasks SET status = 'done'")
        connection.commit()
    original = db.read_bytes()
    with closing(ledger.Ledger(db, read_only=True)) as store:
        assert store.show('T-0001')['status'] == 'done'
        assert store.show('T-0001')['verification']['freshness'] == 'missing'
    assert db.read_bytes() == original
    for _ in range(2):
        with closing(ledger.Ledger(db)) as store:
            assert store.connection.execute('SELECT version FROM schema_version').fetchone()[0] == 6
            assert store.show('T-0001')['status'] == 'done'
    project = tmp_path / 'plain'
    project.mkdir()
    (project / 'file.txt').write_text('one')
    with closing(ledger.Ledger(db)) as store:
        task = _task(store, project)
        record = verify_fixture(store, task)
        assert record['revision'] == 'none' and record['fingerprint']
        (project / 'file.txt').write_text('two')
        _error(lambda: store.set_status(task, to='review', actor='lead'), 'working tree changed')


def test_mcp_verify_approval_validation_and_shared_close_gate(tmp_path):
    db = tmp_path / 'ledger.sqlite'
    args = {'task': 'T-0001', 'command': [sys.executable, '-c', 'pass']}
    _assert_error(_call(tmp_path, 'task.verify', ledger_db=db, **args), 'approved=true')
    assert not db.exists()
    for command in ([], [''], ['arg\0bad']):
        _assert_error(_call(tmp_path, 'task.verify', ledger_db=db, task='T-0001', command=command, approved=True),
                      'Verification requires')
    assert not db.exists()
    project = _project(tmp_path)
    with closing(ledger.Ledger(db)) as store:
        task = _task(store, project)
    _assert_error(_call(tmp_path, 'task.status', ledger_db=db, task=task, to='review', actor='lead', approved=True),
                  'missing lead verification')
    record = _payload(_call(tmp_path, 'task.verify', ledger_db=db, **args, approved=True))
    assert record['exit_code'] == 0
    view = _payload(_call(tmp_path, 'task.status', ledger_db=db, task=task, to='review', actor='lead', approved=True))
    assert view['verification']['accepted'] == record['id']


def test_verify_timeout_cleans_detached_descendants(tmp_path):
    if sys.platform != 'linux':
        raise SkipTest('requires Linux prctl subreaper and /proc zombie inspection')
    project = _project(tmp_path)
    child_pid = project / 'child.pid'
    source = ('import subprocess, sys, time; from pathlib import Path; '
              'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"],start_new_session=True); '
              'Path("child.pid").write_text(str(p.pid)); time.sleep(60)')
    with closing(ledger.Ledger(tmp_path / 'ledger.sqlite')) as store:
        task = _task(store, project)
        record = task_verification.run(store.path, task, [sys.executable, '-c', source], timeout=0.3)
    assert record['exit_code'] == 124
    pid = int(child_pid.read_text())
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return
    # Clean up a regression before reporting the failure.
    os.kill(pid, signal.SIGKILL)
    time.sleep(0.05)
    raise AssertionError('Verification timeout left a detached descendant alive')


def test_v6_migration_rolls_back_table_and_version_together(tmp_path):
    db = tmp_path / 'v5.sqlite'
    _v1_file(db)
    with closing(sqlite3.connect(db)) as connection:
        for migration in (ledger.MIGRATION_V1_TO_V2, ledger.MIGRATION_V2_TO_V3,
                          ledger.MIGRATION_V3_TO_V4, ledger.MIGRATION_V4_TO_V5):
            for statement in migration:
                connection.execute(statement)
        connection.commit()
    with patch.object(ledger, 'MIGRATION_V5_TO_V6', (*ledger.MIGRATION_V5_TO_V6, 'INVALID MIGRATION')):
        _error(lambda: ledger.Ledger(db), 'syntax error', sqlite3.OperationalError)
    with closing(sqlite3.connect(db)) as connection:
        assert connection.execute('SELECT version FROM schema_version').fetchone()[0] == 5
        assert connection.execute("SELECT name FROM sqlite_master WHERE name = 'verifications'").fetchall() == []
    with closing(ledger.Ledger(db)) as store:
        assert store.connection.execute('SELECT version FROM schema_version').fetchone()[0] == 6


def test_verification_evidence_and_event_commit_atomically(tmp_path):
    project = _project(tmp_path)
    with closing(ledger.Ledger(tmp_path / 'ledger.sqlite')) as store:
        task = _task(store, project)
        evidence = verify_fixture(store, task)
        other = {**evidence, 'id': 'V-' + 'a' * 32}
        before = _rows(store)
        with patch.object(store, '_append_event', side_effect=sqlite3.IntegrityError('event failure')):
            _error(lambda: store._record_verification(other), 'event failure', sqlite3.IntegrityError)
        assert _rows(store) == before and store.verifications(task) == [evidence]


def test_git_unavailable_submodules_and_unsafe_directory_links_fail_closed(tmp_path):
    project = _project(tmp_path)
    db = tmp_path / 'ledger.sqlite'
    with patch.object(lead_context, '_git', return_value=None):
        state = task_verification.project_state(str(project), db)
    assert state['fingerprint'] is None and 'unavailable' in state['error']
    _git(project, 'update-index', '--add', '--cacheinfo', '160000,' + _git(project, 'rev-parse', 'HEAD') + ',module')
    state = task_verification.project_state(str(project), db)
    assert state['fingerprint'] is None and 'submodule' in state['error']
    _git(project, 'update-index', '--force-remove', 'module')
    (project / 'directory').mkdir()
    (project / 'directory/file.txt').write_text('tracked')
    _git(project, 'add', '.')
    (project / 'directory/file.txt').unlink()
    (project / 'directory').rmdir()
    try:
        (project / 'directory').symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        if sys.platform == 'win32':
            raise SkipTest('Windows symlinks require Developer Mode or SeCreateSymbolicLinkPrivilege') from None
        raise
    state = task_verification.project_state(str(project), db)
    assert state['fingerprint'] is None and 'symlink' in state['error']


def test_verification_dirty_flag_includes_index_only_changes(tmp_path):
    project = _project(tmp_path)
    file = project / 'source.py'
    file.write_text('value = 2\n')
    _git(project, 'add', 'source.py')
    file.write_text('value = 1\n')  # Worktree matches HEAD, but staged content differs.
    state = task_verification.project_state(str(project), tmp_path / 'ledger.sqlite')
    assert state['fingerprint'] and state['dirty'] is True, state


def test_fingerprint_preserves_trailing_spaces_in_git_project_path(tmp_path):
    if sys.platform == 'win32':
        # Native Windows normalizes trailing spaces in directory names.
        return
    project = _project(tmp_path)
    spaced = tmp_path / 'project '
    project.rename(spaced)
    before = task_verification.project_state(str(spaced), tmp_path / 'ledger.sqlite')
    (spaced / 'source.py').write_text('value = 3\n')
    after = task_verification.project_state(str(spaced), tmp_path / 'ledger.sqlite')
    assert before['fingerprint'] and after['fingerprint'] and before['fingerprint'] != after['fingerprint']
