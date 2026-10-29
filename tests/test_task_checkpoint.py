"""Opt-in pre-run checkpoints and restore: exact restore within scope, refusals, dry runs, caps and retention."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from unittest import SkipTest

import pytest

from hipson import ledger, task_checkpoint
from tests.test_hipson_helpers import REPO_ROOT
from tests.test_task_verification import _project


def _tree(root: Path, *, skip: tuple[str, ...] = (".git",)) -> dict[str, tuple]:
    """Exact state for comparison: type, bytes or link target, permission bits and (files) mtime."""
    state: dict[str, tuple] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if rel.split("/")[0] in skip:
            continue
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode):
            state[rel] = ("link", os.readlink(path))
        elif stat.S_ISDIR(info.st_mode):
            state[rel] = ("dir", stat.S_IMODE(info.st_mode))
        else:
            state[rel] = ("file", path.read_bytes(), stat.S_IMODE(info.st_mode), info.st_mtime_ns)
    return state


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")


def _setup(tmp_path: Path, scope=("src",)):
    root = tmp_path / "project"
    _write(root, {"src/app.py": "a = 1\n", "src/pkg/util.py": "u = 1\n", "src/data.bin": "x" * 5000,
                  "docs/guide.md": "# Guide\n", "src/.env": "TOKEN=private\n"})
    db = tmp_path / "ledger.sqlite"
    store = ledger.Ledger(db)
    task = store.open_task(project=root, title="Checkpoint", objective="Checkpoint")["id"]
    assignment = store.assign(task, actor="codex", role="builder", mode="rw", scope=list(scope))["id"]
    return root, db, store, task, assignment


def _worker_damage(root: Path) -> None:
    (root / "src/app.py").write_text("a = 2  # broken\n")
    (root / "src/pkg/util.py").unlink()
    (root / "src/pkg").rmdir()
    _write(root, {"src/new.py": "created\n", "src/newdir/deep/file.txt": "created\n"})
    (root / "src/data.bin").chmod(0o600)
    (root / "docs/guide.md").write_text("edited outside the scope\n")


def test_restore_brings_back_the_exact_scope_and_leaves_everything_else(tmp_path):
    root, db, store, task, assignment = _setup(tmp_path)
    with closing(store):
        before = _tree(root)
        saved = task_checkpoint.take(db, assignment, root, ["src"])
        assert saved["files"] == 3 and saved["untouched_sensitive"] == 1 and saved["scope"] == ["src"]
        assert not (task_checkpoint.location(db, assignment) / "objects").joinpath("..", ".env").exists()
        _worker_damage(root)
        (root / "src/.env").write_text("TOKEN=changed-by-worker\n")
        store.finish(assignment, result="failed", summary="broke things")
        result = task_checkpoint.apply(db, assignment, store)
    assert result["verified"] and result["restore"] == ["src/app.py", "src/data.bin"]
    assert result["recreate"] == ["src/pkg", "src/pkg/util.py"]
    assert result["delete"] == ["src/newdir/deep/file.txt", "src/newdir/deep", "src/new.py", "src/newdir"]
    after = _tree(root)
    expected = {**before, "docs/guide.md": after["docs/guide.md"], "src/.env": after["src/.env"]}
    assert after == expected  # The scope is exact again, mtimes included.
    assert (root / "docs/guide.md").read_text() == "edited outside the scope\n"  # Outside: untouched.
    assert (root / "src/.env").read_text() == "TOKEN=changed-by-worker\n"  # Sensitive: never copied or touched.


def test_the_dry_run_changes_nothing_and_lists_every_action(tmp_path):
    root, db, store, task, assignment = _setup(tmp_path)
    with closing(store):
        task_checkpoint.take(db, assignment, root, ["src"])
        _worker_damage(root)
        damaged = _tree(root)
        steps = task_checkpoint.plan(db, assignment)
    assert _tree(root) == damaged
    assert not steps["clean"] and steps["restore"] and steps["recreate"] and steps["delete"]
    assert steps["untouched"]["sensitive"] == ["src/.env"]


def test_restore_is_refused_while_the_assignment_or_an_overlapping_writer_is_live(tmp_path):
    root, db, store, task, assignment = _setup(tmp_path)
    with closing(store):
        task_checkpoint.take(db, assignment, root, ["src"])
        with pytest.raises(task_checkpoint.CheckpointError, match=f"{assignment} is still active"):
            task_checkpoint.apply(db, assignment, store)
        store.finish(assignment, result="failed", summary="done")
        other = store.assign(task, actor="claude", role="builder", mode="rw", scope=["src/pkg"])["id"]
        with pytest.raises(task_checkpoint.CheckpointError, match=f"live writer {other} on {task} holds src/pkg"):
            task_checkpoint.apply(db, assignment, store)
        store.finish(other, result="passed", summary="done")
        unrelated = store.assign(task, actor="claude", role="builder", mode="rw", scope=["docs"])["id"]
        assert task_checkpoint.live_blockers(store, assignment, ["src"]) == []
        store.finish(unrelated, result="passed", summary="done")


def test_oversize_scopes_and_unsafe_parents_are_refused_and_checkpoints_are_single_use(tmp_path):
    root, db, store, task, assignment = _setup(tmp_path)
    with closing(store):
        with pytest.raises(task_checkpoint.CheckpointError, match="exceeds the checkpoint caps"):
            task_checkpoint.take(db, assignment, root, ["src"], task_checkpoint.Caps(max_files=2))
        with pytest.raises(task_checkpoint.CheckpointError, match="exceeds the checkpoint caps"):
            task_checkpoint.take(db, assignment, root, ["src"], task_checkpoint.Caps(max_bytes=100))
        assert not task_checkpoint.location(db, assignment).exists()
        task_checkpoint.take(db, assignment, root, ["src"])
        with pytest.raises(task_checkpoint.CheckpointError, match="already exists"):
            task_checkpoint.take(db, assignment, root, ["src"])
        with pytest.raises(task_checkpoint.CheckpointError, match="no checkpoint for A-0099"):
            task_checkpoint.plan(db, "A-0099")
        with pytest.raises(task_checkpoint.CheckpointError, match="not an assignment id"):
            task_checkpoint.location(db, "../escape")


def _symlinks(tmp_path: Path) -> None:
    try:
        os.symlink(tmp_path, tmp_path / "probe-link", target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        raise SkipTest(f"symlinks are not available here: {exc}") from exc
    (tmp_path / "probe-link").unlink()


def test_restore_never_writes_through_a_link_the_worker_planted(tmp_path):
    _symlinks(tmp_path)
    root, db, store, task, assignment = _setup(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "util.py").write_text("outside, must stay\n")
    with closing(store):
        os.symlink("app.py", root / "src/alias.py")
        task_checkpoint.take(db, assignment, root, ["src"])
        # The worker swaps a directory for a link to outside and retargets a link.
        (root / "src/pkg/util.py").unlink()
        (root / "src/pkg").rmdir()
        os.symlink(outside, root / "src/pkg", target_is_directory=True)
        (root / "src/alias.py").unlink()
        os.symlink("data.bin", root / "src/alias.py")
        store.finish(assignment, result="failed", summary="done")
        result = task_checkpoint.apply(db, assignment, store)
    assert result["verified"]
    assert (outside / "util.py").read_text() == "outside, must stay\n" and sorted(os.listdir(outside)) == ["util.py"]
    assert not (root / "src/pkg").is_symlink() and (root / "src/pkg/util.py").read_text() == "u = 1\n"
    assert os.readlink(root / "src/alias.py") == "app.py"


def test_a_scope_below_a_linked_directory_is_refused(tmp_path):
    _symlinks(tmp_path)
    root, db, store, task, assignment = _setup(tmp_path, scope=("lib/inner",))
    (tmp_path / "elsewhere").mkdir()
    os.symlink(tmp_path / "elsewhere", root / "lib", target_is_directory=True)
    with closing(store), pytest.raises(task_checkpoint.CheckpointError, match="lib is not a real directory"):
        task_checkpoint.take(db, assignment, root, ["lib/inner"])


def test_type_changes_excluded_directories_and_kept_created_directories(tmp_path):
    root, db, store, task, assignment = _setup(tmp_path)
    with closing(store):
        _write(root, {"src/node_modules/dep/index.js": "dependency\n"})
        task_checkpoint.take(db, assignment, root, ["src"])
        (root / "src/app.py").unlink()
        _write(root, {"src/app.py/inside.txt": "a directory where a file was\n",
                      "src/node_modules/dep/index.js": "changed dependency\n",
                      "src/made/.env": "SECRET=created\n", "src/made/plain.txt": "created\n"})
        store.finish(assignment, result="failed", summary="done")
        result = task_checkpoint.apply(db, assignment, store)
    assert (root / "src/app.py").read_text() == "a = 1\n"
    assert (root / "src/node_modules/dep/index.js").read_text() == "changed dependency\n"  # Excluded: untouched.
    assert result["kept"] == ["src/made"] and (root / "src/made/.env").exists()
    assert not (root / "src/made/plain.txt").exists()


def _env(tmp_path: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in ("HIPSON_TASK", "HIPSON_ASSIGNMENT", "HIPSON_ROLE")}
    env.update(HIPSON_LEDGER=str(tmp_path / "ledger.sqlite"), PYTHONPATH=str(REPO_ROOT / "src"))
    return env


def _hipson(project: Path, tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-P", "-m", "hipson.cli", *args], cwd=project, env=_env(tmp_path),
                          capture_output=True, text=True, timeout=120)


def test_cli_checkpoint_run_restore_dry_run_apply_and_events(tmp_path):
    project = _project(tmp_path)
    _write(project, {"src/app.py": "a = 1\n", "src/keep.txt": "keep\n", "notes.txt": "outside\n"})
    with closing(ledger.Ledger(tmp_path / "ledger.sqlite")) as store:
        task = store.open_task(project=project, title="T", objective="o")["id"]
        store.set_status(task, to="in_progress", actor="claude")
    worker = ("from pathlib import Path; Path('src/app.py').write_text('broken'); Path('src/keep.txt').unlink(); "
              "Path('src/new.txt').write_text('new'); Path('notes.txt').write_text('outside changed')")
    run = _hipson(project, tmp_path, "task", "run", "--task", task, "--actor", "codex", "--role", "builder",
                  "--mode", "rw", "--scope", "src", "--checkpoint", "--", sys.executable, "-c", worker)
    assert run.returncode == 0, run.stderr
    assert "checkpoint: 2 files" in run.stderr
    with closing(ledger.Ledger(tmp_path / "ledger.sqlite", read_only=True)) as store:
        assignment = store.show(task)["assignments"][0]["id"]
    dry = _hipson(project, tmp_path, "task", "restore", "--assignment", assignment, "--json")
    assert dry.returncode == 0, dry.stderr
    planned = json.loads(dry.stdout)
    assert (planned["applied"], planned["restore"], planned["recreate"], planned["delete"]) == (
        False, ["src/app.py"], ["src/keep.txt"], ["src/new.txt"])
    assert (project / "src/app.py").read_text() == "broken"  # The dry run changed nothing.
    text = _hipson(project, tmp_path, "task", "restore", "--assignment", assignment)
    assert "Would restore" in text.stdout and "apply with: hipson task restore" in text.stdout
    applied = _hipson(project, tmp_path, "task", "restore", "--assignment", assignment, "--apply")
    assert applied.returncode == 0, applied.stderr
    assert "verified: the scope matches the checkpoint" in applied.stdout
    assert (project / "src/app.py").read_text() == "a = 1\n" and (project / "src/keep.txt").read_text() == "keep\n"
    assert not (project / "src/new.txt").exists() and (project / "notes.txt").read_text() == "outside changed"
    with closing(ledger.Ledger(tmp_path / "ledger.sqlite", read_only=True)) as store:
        events = [event for event in store.events(task) if event["type"] in ("checkpointed", "restored")]
        with pytest.raises(ledger.LedgerError, match="reserved"):
            store.add_event(task, actor="worker", type="restored", summary="forged")
    assert [event["type"] for event in events] == ["checkpointed", "restored"]
    assert events[0]["summary"].startswith(f"{assignment}: checkpoint of src: 2 files")
    assert "restored 1, re-created 1, deleted 1 path(s) in src; verified true" in events[1]["summary"]
    listed = _hipson(project, tmp_path, "task", "checkpoints", "--json")
    assert [item["assignment"] for item in json.loads(listed.stdout)] == [assignment]


def test_cli_refuses_oversize_scopes_and_read_only_checkpoints_before_assigning(tmp_path):
    project = _project(tmp_path)
    _write(project, {"src/a.txt": "a", "src/b.txt": "b", "src/c.txt": "c"})
    with closing(ledger.Ledger(tmp_path / "ledger.sqlite")) as store:
        task = store.open_task(project=project, title="T", objective="o")["id"]
        store.set_status(task, to="in_progress", actor="claude")
    oversize = _hipson(project, tmp_path, "task", "run", "--task", task, "--actor", "codex", "--role", "builder",
                       "--mode", "rw", "--scope", "src", "--checkpoint", "--checkpoint-max-files", "2",
                       "--", sys.executable, "-c", "pass")
    assert oversize.returncode == 64 and "exceeds the checkpoint caps" in oversize.stderr
    readonly = _hipson(project, tmp_path, "task", "run", "--task", task, "--actor", "codex", "--role", "explorer",
                       "--checkpoint", "--", sys.executable, "-c", "pass")
    assert readonly.returncode == 64 and "--checkpoint needs --mode rw" in readonly.stderr
    with closing(ledger.Ledger(tmp_path / "ledger.sqlite", read_only=True)) as store:
        assert store.show(task)["assignments"] == []
    missing = _hipson(project, tmp_path, "task", "restore", "--assignment", "A-0001")
    assert missing.returncode == 1 and "Assignment not found" in missing.stderr


def test_retention_prunes_terminal_and_expired_checkpoints_but_never_active_ones(tmp_path):
    root, db, store, task, assignment = _setup(tmp_path)
    with closing(store):
        task_checkpoint.take(db, assignment, root, ["src"])
        assert task_checkpoint.prune(db, store) == []  # Active: kept.
        store.finish(assignment, result="passed", summary="done")
        assert task_checkpoint.prune(db, store) == []  # Ended, task still open, recent: kept.
        assert task_checkpoint.prune(db, store, older_than_days=0, dry_run=True) == [
            {"assignment": assignment, "reason": "older than 0 days"}]
        assert task_checkpoint.location(db, assignment).exists()  # Dry run.
        store.ack(assignment)
        store.set_status(task, to="cancelled", actor="claude")
        assert task_checkpoint.prune(db, store) == [{"assignment": assignment, "reason": "task cancelled"}]
        assert not task_checkpoint.location(db, assignment).exists() and task_checkpoint.checkpoints(db) == []


def test_the_ledger_and_its_private_state_inside_the_project_are_never_checkpointed(tmp_path):
    root = tmp_path / "project"
    _write(root, {"app.py": "a\n"})
    db = root / "ledger.sqlite"
    with closing(ledger.Ledger(db)) as store:
        task = store.open_task(project=root, title="T", objective="o")["id"]
        assignment = store.assign(task, actor="codex", role="builder", mode="rw", scope=["."])["id"]
        saved = task_checkpoint.take(db, assignment, root, ["."])
        manifest = task_checkpoint.load(db, assignment)
    assert saved["files"] == 1 and list(manifest["entries"]) == ["app.py"]
    assert "ledger.sqlite" in manifest["excluded"] and ".ledger.sqlite.checkpoints" not in manifest["entries"]
