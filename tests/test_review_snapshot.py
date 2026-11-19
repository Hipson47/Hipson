"""Immutable review snapshots: the reviewer reads the exact candidate lead verification accepted."""

import json
import os
import stat
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from unittest import SkipTest
from unittest.mock import patch

import pytest

from hipson import flow_driver, ledger, review_snapshot, task_verification
from tests.test_flow_driver import PY, _flow, _repo, _spec, _view
from tests.test_hipson_helpers import run_cli
from tests.test_task_verification import _git

NL = chr(10)
RESULT = {'status': 'completed', 'summary': 'ok', 'files_changed': [], 'commands': [], 'findings': [], 'risks': []}


def _candidate(tmp_path):
    """A repository with a modified, a deleted, an untracked and an executable file."""
    project = _repo(tmp_path)
    (project / 'run.sh').write_text('#!/bin/sh' + NL)
    (project / 'run.sh').chmod(0o755)
    (project / 'gone.txt').write_text('old' + NL)
    _git(project, 'add', '.')
    _git(project, '-c', 'user.name=T', '-c', 'user.email=t@e', 'commit', '-qm', 'more')
    (project / 'src/parser/pairs.py').write_text('CHANGED = 1' + NL)
    (project / 'gone.txt').unlink()
    (project / 'src/parser/new.py').write_text('NEW = 2' + NL)
    return project


def test_a_snapshot_is_the_candidate_bound_by_its_fingerprint(tmp_path):
    project, db = _candidate(tmp_path), tmp_path / 'ledger.sqlite'
    directory = tmp_path / 'snap'
    manifest = review_snapshot.create(str(project), db, directory)
    live = task_verification.project_state(str(project), db)
    assert manifest['fingerprint'] == live['fingerprint'] and manifest['revision'] == live['revision']
    copy = review_snapshot.workdir(directory, manifest)
    assert copy == directory / 'tree' and review_snapshot.verify(directory, manifest) == live['fingerprint']
    # Uncommitted, deleted and untracked candidate changes are all preserved, and Git sees them as in the project.
    assert (copy / 'src/parser/pairs.py').read_text() == 'CHANGED = 1' + NL and not (copy / 'gone.txt').exists()
    assert (copy / 'src/parser/new.py').read_text() == 'NEW = 2' + NL
    status = {line.strip() for line in _git(copy, 'status', '--porcelain').splitlines()}
    assert {'M src/parser/pairs.py', 'D gone.txt', '?? src/parser/new.py'} <= status, status
    assert 'CHANGED = 1' in _git(copy, 'diff', 'HEAD')
    lines = flow_driver.candidate_diff(str(copy), ['src/parser'])
    assert 'CHANGED = 1' in lines[1] and 'new.py' in lines[1]
    # Nothing was written to the project's repository, and the copy's files are read-only.
    assert _git(project, 'worktree', 'list').count(NL) == 0
    assert not os.access(copy / 'src/parser/pairs.py', os.W_OK)
    if os.name == 'posix':
        assert (copy / 'run.sh').stat().st_mode & stat.S_IXUSR
    review_snapshot.remove(directory)
    assert not directory.exists()


def test_snapshot_verification_detects_edited_missing_added_and_forged_content(tmp_path):
    project, db = _candidate(tmp_path), tmp_path / 'ledger.sqlite'
    cases = []
    for index, damage in enumerate(('edit', 'delete', 'add', 'cache', 'forge', 'escape')):
        directory = tmp_path / f'snap-{index}'
        manifest = review_snapshot.create(str(project), db, directory)
        target = directory / 'tree' / 'src/parser/pairs.py'
        if damage == 'edit':
            target.chmod(0o644)
            target.write_text('EDITED' + NL)
        elif damage == 'delete':
            target.chmod(0o644)
            target.unlink()
        elif damage == 'add':
            (directory / 'tree' / 'src/parser/extra.py').write_text('x' + NL)
        elif damage == 'cache':  # A reviewer's tools may write caches; they are not candidate content.
            (directory / 'tree' / '__pycache__').mkdir()
            (directory / 'tree' / '__pycache__' / 'x.pyc').write_bytes(b'x')
        elif damage == 'forge':
            manifest = {**manifest, 'fingerprint': '0' * 64}
        else:
            manifest = {**manifest, 'entries': [{'path': '../outside', 'state': 'missing'}]}
        try:
            review_snapshot.verify(directory, manifest)
            cases.append((damage, None))
        except review_snapshot.SnapshotError as exc:
            cases.append((damage, str(exc)))
        review_snapshot.remove(directory)
    assert cases == [
        ('edit', 'snapshot content differs from its manifest (missing, replaced or edited file)'),
        ('delete', 'snapshot content differs from its manifest (missing, replaced or edited file)'),
        ('add', 'snapshot gained 1 file(s) that are not part of the candidate'),
        ('cache', None),
        ('forge', 'snapshot manifest fingerprint does not match its entries'),
        ('escape', 'snapshot manifest names a path outside the snapshot')]
    with pytest.raises(review_snapshot.SnapshotError, match='manifest unreadable'):
        review_snapshot.load(tmp_path / 'nothing')


def test_a_candidate_that_cannot_be_bound_is_never_snapshotted(tmp_path):
    project, db = _candidate(tmp_path), tmp_path / 'ledger.sqlite'
    with patch.object(task_verification, 'FILE_HASH_LIMIT', 4):
        with pytest.raises(review_snapshot.SnapshotError, match='exceeds the snapshot budget'):
            review_snapshot.create(str(project), db, tmp_path / 'big')
    assert not (tmp_path / 'big').exists()
    (tmp_path / 'taken').mkdir()
    with pytest.raises(review_snapshot.SnapshotError, match='already exists'):
        review_snapshot.create(str(project), db, tmp_path / 'taken')
    try:
        (project / 'src/parser/out').symlink_to('../../../elsewhere')
    except OSError:
        if sys.platform != 'win32':
            raise
        raise SkipTest('Windows symlinks require Developer Mode or SeCreateSymbolicLinkPrivilege') from None
    with pytest.raises(review_snapshot.SnapshotError, match='symlink points outside the project'):
        review_snapshot.create(str(project), db, tmp_path / 'link')
    (project / 'src/parser/out').unlink()
    (project / 'src/parser/in').symlink_to('pairs.py')  # A link within the tree is copied as a link.
    manifest = review_snapshot.create(str(project), db, tmp_path / 'inside')
    assert os.readlink(tmp_path / 'inside/tree/src/parser/in') == 'pairs.py'
    assert review_snapshot.verify(tmp_path / 'inside', manifest) == manifest['fingerprint']


def test_a_subdirectory_project_reviews_from_its_place_in_the_copy(tmp_path):
    project, db = _candidate(tmp_path), tmp_path / 'ledger.sqlite'
    manifest = review_snapshot.create(str(project / 'src'), db, tmp_path / 'snap')
    assert manifest['workdir'] == 'src'
    assert review_snapshot.workdir(tmp_path / 'snap', manifest) == tmp_path / 'snap' / 'tree' / 'src'
    with pytest.raises(review_snapshot.SnapshotError, match='outside the snapshot'):
        review_snapshot.workdir(tmp_path / 'snap', {**manifest, 'workdir': '../..'})


def test_the_documented_race_a_live_edit_reverted_during_review_is_not_seen(tmp_path):
    # Before snapshots the reviewer read the live tree: an edit made and reverted during the review gave equal
    # start/end fingerprints, so the review passed for content it never saw. Now it reads the verified copy.
    project, db = _repo(tmp_path), tmp_path / 'ledger.sqlite'
    proc, result = _flow(project, db, _spec(tmp_path, project, reviewer='aba'))
    assert proc.returncode == 0 and result['state'] == 'accepted' and result['open_findings'] == [], result
    view, events = _view(db)
    review = next(item for item in view['assignments'] if item['role'] == 'reviewer')
    assert review['result']['summary'].endswith('read trimmed source')  # The verified candidate, not the edit.
    trees = [event['summary'] for event in events if event['type'] == 'reviewed']
    assert len(trees) == 2 and all(summary.endswith('source=snapshot') for summary in trees)
    assert not list(flow_driver._flow_dir(db, 'T-0001').glob('*.snapshot'))  # Removed after the review.


def test_a_live_change_during_review_does_not_close_under_that_review(tmp_path):
    project, db = _repo(tmp_path), tmp_path / 'ledger.sqlite'
    spec = _spec(tmp_path, project, reviewer='mutate-once')
    raw = json.loads(spec.read_text())
    raw['reviewer']['observe'] = False  # The edit stands for another process, not the reviewer's own write.
    spec.write_text(json.dumps(raw))
    proc, result = _flow(project, db, spec)
    # The review saw the verified candidate, untouched by the concurrent edit; the edit then makes verification
    # stale, so the flow stops for the lead instead of closing the changed tree under that review.
    assert proc.returncode == 4 and result['state'] == 'verification_inconclusive', result
    assert 'stale: working tree changed since verification' in result['reasons'][0]
    view, _ = _view(db)
    review = [item for item in view['assignments'] if item['role'] == 'reviewer'][-1]
    assert review['result']['summary'].endswith('read trimmed source') and view['status'] == 'in_progress'
    assert not view['verification']['passing']
    closed = run_cli(project, 'task', 'close', '--db', str(db), '--task', 'T-0001')
    assert closed.returncode != 0 and _view(db)[0]['status'] != 'done'


def test_manual_reviewers_read_a_snapshot_and_a_tampered_or_live_review_never_qualifies(tmp_path):
    project, db = _repo(tmp_path), tmp_path / 'ledger.sqlite'
    run_cli(project, 'task', 'open', '--db', str(db), '--project', str(project), '--title', 'Manual', '--objective',
            'x', '--lead', 'lead', '--require-review', '--check', PY + ' -c pass', '--start')
    build = run_cli(project, 'task', 'run', '--db', str(db), '--task', 'T-0001', '--actor', 'builder', '--role',
                    'builder', '--mode', 'rw', '--scope', 'src/parser', '--', PY, '-c',
                    "open('src/parser/pairs.py', 'w').write('A = 1' + chr(10))")
    assert build.returncode == 0, build.stderr
    run_cli(project, 'task', 'ack', '--db', str(db), '--assignment', 'A-0001')
    assert run_cli(project, 'task', 'verify', '--db', str(db), '--task', 'T-0001').returncode == 0
    runs = tmp_path / '.ledger.sqlite.runs'
    dead = subprocess.Popen([PY, '-c', 'pass'])
    dead.wait()
    leftover = runs / f'review-{dead.pid}-stale'  # A killed runner's snapshot: swept by the next reviewer.
    (leftover / 'tree').mkdir(parents=True)
    (leftover / 'tree' / 'file').write_text('x')
    (leftover / 'tree' / 'file').chmod(0o444)
    result = tmp_path / 'review.json'
    seen = tmp_path / 'seen.json'

    def review(*extra, code):
        result.write_text(json.dumps(RESULT))
        return run_cli(project, 'task', 'run', '--db', str(db), '--task', 'T-0001', '--actor', 'reviewer', '--role',
                       'reviewer', '--mode', 'ro', '--result-file', str(result), *extra, '--', PY, '-c', code)

    probe = ("import json, os; json.dump({'cwd': os.getcwd(), 'snapshot': os.environ.get('HIPSON_REVIEW_SNAPSHOT'), "
             "'project': os.environ.get('HIPSON_PROJECT'), 'text': open('src/parser/pairs.py').read()}, "
             f"open({str(seen)!r}, 'w'))")
    # A reviewer that edits its snapshot invalidates its own review.
    tamper = "import os; os.chmod('src/parser/pairs.py', 0o644); open('src/parser/pairs.py', 'w').write('B = 2')"
    assert review(code=tamper).returncode == 0
    assert not list(runs.glob('review-*'))  # Its own snapshot and the stale one are both gone.
    run_cli(project, 'task', 'ack', '--db', str(db), '--assignment', 'A-0002')
    reasons = _view(db)[0]['review']['reasons']
    assert reasons[0].startswith('review required: latest review A-0002 does not qualify: the reviewed snapshot '
                                 'changed during the review'), reasons
    assert (project / 'src/parser/pairs.py').read_text() == 'A = 1' + NL  # The project was never touched.
    # A live-tree review is recorded as such and never satisfies the gate.
    live = review('--live-tree', code='pass')
    assert live.returncode == 0, live.stderr
    run_cli(project, 'task', 'ack', '--db', str(db), '--assignment', 'A-0003')
    reasons = _view(db)[0]['review']['reasons']
    assert 'A-0003 does not qualify: it read the live working tree' in reasons[0], reasons
    assert review(code=probe).returncode == 0
    facts = json.loads(seen.read_text())
    assert Path(facts['cwd']) == Path(facts['snapshot']) and Path(facts['cwd']).parent.parent == runs.resolve()
    assert Path(facts['project']) == project.resolve() and facts['text'] == 'A = 1' + NL
    run_cli(project, 'task', 'ack', '--db', str(db), '--assignment', 'A-0004')
    assert _view(db)[0]['review']['accepted'] == 'A-0004'
    # A prepared snapshot that is missing is refused before any assignment exists.
    missing = review('--snapshot', str(tmp_path / 'absent'), code='pass')
    assert missing.returncode == 75 and 'could not prepare the review snapshot' in missing.stderr
    assert len(_view(db)[0]['assignments']) == 4
    misused = run_cli(project, 'task', 'run', '--db', str(db), '--task', 'T-0001', '--actor', 'x', '--role',
                      'explorer', '--snapshot', str(tmp_path), '--', PY, '-c', 'pass')
    assert misused.returncode == 64 and '--snapshot needs --role reviewer' in misused.stderr


def test_an_interrupted_reviews_snapshot_is_removed_on_resume(tmp_path):
    project, db = _repo(tmp_path), tmp_path / 'ledger.sqlite'
    spec = _spec(tmp_path, project, reviewer='crash')
    _flow(project, db, spec)
    flows = flow_driver._flow_dir(db, 'T-0001')
    stale = flows / 'review-7.snapshot'
    review_snapshot.create(str(project), db, stale)  # What a killed flow leaves behind: read-only files.
    proc, result = _flow(project, db, spec, '--resume', 'T-0001', '--retry')
    assert not stale.exists() and not list(flows.glob('*.snapshot')), result


def test_a_builder_that_changes_protected_paths_stops_the_flow(tmp_path):
    project, db = _repo(tmp_path), tmp_path / 'ledger.sqlite'
    # Tests inside the builder's own write scope: no out-of-scope finding, the protection still holds.
    spec = _spec(tmp_path, project, builder='tamper')
    raw = json.loads(spec.read_text())
    raw['builder']['scope'] = ['src/parser', 'tests']
    spec.write_text(json.dumps(raw))
    proc, result = _flow(project, db, spec)
    assert proc.returncode == 4 and result['state'] == 'needs_lead', result
    assert 'A-0001 changed protected paths' in result['reasons'][0] and 'tests/acceptance.py' in result['reasons'][0]
    with closing(ledger.Ledger(db, read_only=True)) as store:
        assert not store.verifications('T-0001')  # Nothing judged the tampered test.
    _git(project, 'checkout', '--', 'tests')
    proc, resumed = _flow(project, db, spec, '--resume', 'T-0001')
    # Restored: the stop clears, and the next builder (the same tampering profile) is stopped again.
    assert resumed['builder_attempts'] == 2 and 'A-0002 changed protected paths' in resumed['reasons'][0], resumed
    # Runtime configuration is protected even under a whole-project scope.
    other = tmp_path / 'other'
    other.mkdir()
    project2, db2 = _repo(other), other / 'ledger.sqlite'
    spec2 = _spec(other, project2, builder='hook')
    raw = json.loads(spec2.read_text())
    raw['builder']['scope'] = ['.']
    spec2.write_text(json.dumps(raw))
    proc, result = _flow(project2, db2, spec2)
    assert result['state'] == 'needs_lead' and '.claude' in result['reasons'][0], result


def test_protected_paths_and_state_contract(tmp_path):
    project = _repo(tmp_path)
    spec = flow_driver.validate_spec({**json.loads(_spec(tmp_path, project).read_text()), 'protected': ['src/x']})
    names = flow_driver.protected_paths(str(project), spec)
    assert names == sorted(['tests/acceptance.py', 'src/x', *flow_driver.RUNTIME_CONFIG])
    state = flow_driver.protected_state(str(project), ['tests', 'src/x', 'tests/acceptance.py'])
    assert state['src/x'] == 'missing' and len(state['tests']) == 64
    (project / 'tests' / '__pycache__').mkdir()
    (project / 'tests' / '__pycache__' / 'a.pyc').write_bytes(b'x')
    assert flow_driver.protected_state(str(project), ['tests'])['tests'] == state['tests']
    (project / 'tests' / 'new.py').write_text('x')
    assert flow_driver.protected_state(str(project), ['tests'])['tests'] != state['tests']
    with pytest.raises(flow_driver.FlowError, match='spec.protected'):
        flow_driver.validate_spec({**json.loads(_spec(tmp_path, project).read_text()), 'protected': ['../out']})
