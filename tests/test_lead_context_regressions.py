"""Regressions for read-only resume and actionable lead visibility."""

import os
import re
import shlex
import shutil
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from hipson import lead_context, mcp_server
from hipson.ledger import Ledger, LedgerError
from tests.support import hipson_cli
from tests.test_lead_context import _assign, _cli, _git, _open, _repo
from tests.test_ledger import _verify


def _files(root):
    return {path.relative_to(root): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in root.rglob('*') if path.is_file()}


def test_resume_never_invokes_clean_or_process_filters(tmp_path):
    for driver in ('clean', 'process'):
        project = tmp_path / driver
        _repo(project)
        marker = tmp_path / f'{driver}-invoked'
        helper = tmp_path / f'{driver}.py'
        helper.write_text('import pathlib, sys\npathlib.Path(sys.argv[1]).write_text("invoked")\n'
                          + ('sys.stdout.buffer.write(sys.stdin.buffer.read())\n' if driver == 'clean' else ''))
        _git(project, 'config', f'filter.sneaky.{driver}',
             shlex.join([Path(sys.executable).as_posix(), helper.as_posix(), marker.as_posix()]))
        (project / '.git/info/attributes').write_text('*.txt filter=sneaky\n')
        # Same bytes with stale index metadata: normal status invokes the configured helper.
        tracked = project / 'file.txt'
        stamp = tracked.stat().st_mtime_ns + 10_000_000_000
        os.utime(tracked, ns=(stamp, stamp))
        before = _files(project)
        with closing(Ledger(tmp_path / f'{driver}.sqlite')) as store:
            _open(store, project)
            view = lead_context.build(store, resume=True)
            assert not marker.exists(), f'{driver} helper executed'
            assert view['projects'][0]['dirty_files'] == 1
            assert 'stat' in lead_context.render(view)
        assert _files(project) == before


def test_resume_accepts_git_for_windows_whole_second_index_timestamps(tmp_path):
    project = tmp_path / 'project'
    _repo(project)
    git = lead_context._git

    def whole_seconds(*args):
        data = git(*args)
        if data is not None and '--debug' in args:
            return re.sub(rb'((?:ctime|mtime): \d+):\d+', rb'\1:0', data)
        return data

    before = _files(project)
    with patch.object(lead_context, '_git', side_effect=whole_seconds), \
            patch.object(sys, 'platform', 'win32'):
        assert lead_context._stat_dirty(str(project)) == 0
        tracked = project / 'file.txt'
        stamp = tracked.stat().st_mtime_ns + 10_000_000_000
        os.utime(tracked, ns=(stamp, stamp))
        assert lead_context._stat_dirty(str(project)) == 1
    assert {path: value for path, value in _files(project).items() if path != Path('file.txt')} == {
        path: value for path, value in before.items() if path != Path('file.txt')}


def test_resume_prioritizes_atomic_action_records_before_descriptions(tmp_path):
    with closing(Ledger(tmp_path / 'ledger.sqlite')) as store:
        task = _open(store, tmp_path)
        delivery = _assign(store, task, 'delivered')
        store.finish(delivery, result='failed', summary='Pending repair ' + 'x' * 4000,
                     failure_journal={'reason': 'Worker died ' + 'x' * 4000})
        writers = [_assign(store, task, f'src/held-{n}', actor=f'writer-{n}') for n in range(5)]
        finding = store.add_finding(task, actor='reviewer', severity='blocking',
                                    title='Repair required', detail='x' * 4000)['id']
        for n in range(3):
            store.open_task(project=tmp_path, title=f'Other {n}', objective='description ' * 60,
                            acceptance=['descriptive acceptance ' * 30])
        for resume in (False, True):
            text = lead_context.render(lead_context.build(store, resume=resume))
            assert len(text.encode()) + 1 <= lead_context.TEXT_LIMIT
            assert delivery in text and finding in text
            for n, writer in enumerate(writers):
                assert writer in text and f'src/held-{n}' in text
            for line in text.splitlines():
                if any(field in line for field in ('progress=', 'result:', 'report=', 'journal:')):
                    assert re.search(r'A-\d+', line), f'Orphan assignment detail: {line}'


def test_post_closure_deliveries_remain_pending_in_default_cli_and_mcp_views(tmp_path):
    db = tmp_path / 'ledger.sqlite'
    for terminal in ('cancelled', 'done'):
        with closing(Ledger(db)) as store:
            task = _open(store, tmp_path, terminal)
            delivery = _assign(store, task, terminal)
            # Equal timestamps must not obscure the ordering of closure and delivery.
            with patch('hipson.ledger._timestamp', return_value='2026-01-01T00:00:00+00:00'):
                if terminal == 'done':
                    _verify(store, task)
                    store.set_status(task, to='review', actor='lead')
                store.set_status(task, to=terminal, actor='lead', force=terminal == 'done',
                                 reason='Close with running worker' if terminal == 'done' else '')
                store.finish(delivery, result='failed', summary='Late changed files',
                             failure_journal={'reason': 'Late worker failure'}, worker_result={
                                 'status': 'partial', 'summary': 'Late changed files',
                                 'files_changed': [{'path': 'src/parser.py', 'change': 'Partial repair'}],
                                 'commands': [], 'findings': [], 'risks': []})
            item = store.activity(delivery)
            assert item['ack_state'] == 'unacknowledged'
            assert item['acknowledged_at'] is None and item['ack_note'] == ''
            store.add_event(task, actor='lead', type='status', summary='Late delivery still needs inspection')
            assert store.activity(delivery)['ack_state'] == 'unacknowledged'
        with patch.dict(os.environ, HIPSON_LEDGER=str(db)):
            for args, tool in ((('task', 'now'), 'task.now'), (('lead', 'resume'), 'lead.resume')):
                code, text, errors = _cli(*args)
                assert code == 0 and not errors and task in text and delivery in text
                payload = mcp_server._tool_payload(tool, {}, project=tmp_path)
                now = payload.get('now', payload)
                assert any(entry['task'] == task and entry['pending_results'] for entry in now['tasks'])
        with closing(Ledger(db)) as store:
            store.ack(delivery, note='Inspected late delivery')
            assert task not in [entry['task'] for entry in lead_context.build(store)['tasks']]


def test_pre_closure_finish_and_reaped_journals_remain_historical(tmp_path):
    with closing(Ledger(tmp_path / 'ledger.sqlite', pid_alive=lambda _: False)) as store:
        task = _open(store, tmp_path)
        finished = _assign(store, task, 'finished')
        store.finish(finished, result='passed', summary='Historical outcome')
        reaped = store.assign(task, actor='gone', role='builder', mode='rw', scope=['gone'],
                              owner_pid=9876, owner_host=store._hostname())['id']
        store.reap(task=task)
        store.set_status(task, to='cancelled', actor='lead', summary='No longer needed')
        for assignment in (finished, reaped):
            assert store.activity(assignment)['ack_state'] == 'not_required'


def test_lead_cli_and_mcp_reads_never_touch_wal_sidecars(tmp_path):
    source = tmp_path / 'source.sqlite'
    with closing(Ledger(source)) as writer:
        _open(writer, tmp_path)
        for with_shm in (False, True):
            directory = tmp_path / str(with_shm)
            directory.mkdir()
            db = directory / 'ledger.sqlite'
            for suffix in ('', '-wal', '-shm') if with_shm else ('', '-wal'):
                shutil.copyfile(str(source) + suffix, str(db) + suffix)
            before = _files(directory)
            with patch.dict(os.environ, HIPSON_LEDGER=str(db)):
                for args, tool in ((('task', 'now'), 'task.now'), (('lead', 'resume'), 'lead.resume')):
                    code, text, errors = _cli(*args)
                    assert code == 1 and not text and 'WAL' in errors and 'recovery' in errors
                    try:
                        mcp_server._tool_payload(tool, {}, project=tmp_path)
                    except LedgerError as exc:
                        assert 'WAL' in str(exc) and 'recovery' in str(exc)
                    else:
                        raise AssertionError('Unsafe WAL read accepted')
                    assert _files(directory) == before


def test_lead_views_preserve_directory_and_inert_sidecars(tmp_path):
    db = tmp_path / 'ledger.sqlite'
    project = tmp_path / 'repo'
    _repo(project)
    with closing(Ledger(db)) as store:
        _open(store, project)
    # Native read-only SQLite can leave these inert sidecars behind. Strict reads
    # may ignore an empty WAL, but must not create, initialize or update its SHM.
    Path(f'{db}-wal').touch()
    Path(f'{db}-shm').write_bytes(b'untouched shared-memory sentinel')
    before = _files(tmp_path)
    with patch.dict(os.environ, HIPSON_LEDGER=str(db)):
        for args, tool in ((('task', 'now'), 'task.now'), (('lead', 'resume'), 'lead.resume')):
            for _ in range(2):
                code, text, errors = _cli(*args)
                assert code == 0 and text and not errors, errors
                mcp_server._tool_payload(tool, {}, project=tmp_path)
                assert _files(tmp_path) == before


def test_lead_views_refuse_rollback_recovery_without_touching_directory(tmp_path):
    db = tmp_path / 'ledger.sqlite'
    with closing(Ledger(db)) as store:
        _open(store, tmp_path)
    Path(f'{db}-journal').write_bytes(b'recovery needed')
    before = _files(tmp_path)
    with patch.dict(os.environ, HIPSON_LEDGER=str(db)):
        for args, tool in ((('task', 'now'), 'task.now'), (('lead', 'resume'), 'lead.resume')):
            code, text, errors = _cli(*args)
            assert code == 1 and not text and 'rollback journal' in errors
            try:
                mcp_server._tool_payload(tool, {}, project=tmp_path)
            except LedgerError as exc:
                assert 'rollback journal' in str(exc)
            else:
                raise AssertionError('Unsafe rollback read accepted')
            assert _files(tmp_path) == before


def test_readme_task_quick_start_executes_to_done(tmp_path):
    readme = (Path(__file__).resolve().parents[1] / 'README.md').read_text()
    block = next(block for block in re.findall(r'```bash\n(.*?)```', readme, re.S)
                 if block.startswith('hipson task open --project . --title "Fix parser"'))
    commands = []
    for line in block.splitlines():
        args = shlex.split(line)
        assert args.pop(0) == 'hipson'
        commands.append(args)
    _run_task_example(tmp_path, commands)


def test_platform_smoke_task_lifecycle_executes_to_done(tmp_path):
    script = (Path(__file__).resolve().parents[1] / 'scripts/platform_smoke.sh').read_text()
    block = script.split('# Manual ledger lifecycle', 1)[1].split('# Worker wrapper:', 1)[0]
    commands = []
    for line in block.splitlines():
        if line.startswith('check '):
            args = shlex.split(line)
            assert args[:1] == ['check'] and args[2] == '$H'
            commands.append(args[3:])
    assert commands, 'Missing smoke lifecycle commands'
    _run_task_example(tmp_path, commands)


def _run_task_example(tmp_path, commands):
    env = {**os.environ, 'HIPSON_LEDGER': str(tmp_path / 'ledger.sqlite')}
    for key in ('HIPSON_ROLE', 'HIPSON_TASK', 'HIPSON_ASSIGNMENT'):
        env.pop(key, None)
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(tmp_path), '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
                    'commit', '--allow-empty', '-qm', 'Example'], check=True, capture_output=True)
    executable = hipson_cli()
    assert executable, 'Installed hipson CLI required'
    for args in commands:
        if 'python' in args:
            args[args.index('python')] = sys.executable
        result = subprocess.run([executable, *args], cwd=tmp_path, env=env, capture_output=True,
                                text=True, timeout=15, check=False)
        assert result.returncode == 0, (args, result.stdout, result.stderr)
    with closing(Ledger(tmp_path / 'ledger.sqlite', read_only=True)) as store:
        assert store.show('T-0001')['status'] == 'done'
