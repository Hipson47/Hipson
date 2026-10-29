"""Opt-in pre-run checkpoints and restore: exact restore within scope, refusals, dry runs, caps and retention."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest import SkipTest
from unittest.mock import patch

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
    # Windows chmod only toggles the read-only bit, so 0o600 leaves data.bin's mode as it was there.
    expected = ["src/app.py"] if sys.platform == "win32" else ["src/app.py", "src/data.bin"]
    assert result["verified"] and result["restore"] == expected
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


def _manifest_path(db: Path, assignment: str) -> Path:
    return task_checkpoint.location(db, assignment) / "manifest.json"


def test_a_tampered_manifest_is_never_used(tmp_path):
    root, db, store, task, assignment = _setup(tmp_path)
    with closing(store):
        task_checkpoint.take(db, assignment, root, ["src"])
        store.finish(assignment, result="failed", summary="done")
        original = _manifest_path(db, assignment).read_text()
        manifest = json.loads(original)
        some = next(entry for entry in manifest["entries"].values() if entry["type"] == "file")
        for rel in ("/tmp/victim", "../outside.txt", "docs/guide.md", "src/../docs/x", "src\\x"):
            changed = {**manifest, "entries": {**manifest["entries"], rel: some}}
            _manifest_path(db, assignment).write_text(json.dumps(changed))
            with pytest.raises(task_checkpoint.CheckpointError, match="is damaged"):
                task_checkpoint.apply(db, assignment, store)
        # Roots that differ from the assignment's claim in the ledger are refused too.
        _manifest_path(db, assignment).write_text(json.dumps({**manifest, "roots": ["."], "entries": {}}))
        with pytest.raises(task_checkpoint.CheckpointError, match="does not match the ledger"):
            task_checkpoint.apply(db, assignment, store)
        with pytest.raises(task_checkpoint.CheckpointError, match="does not match the ledger"):
            task_checkpoint.plan(db, assignment, store)
        _manifest_path(db, assignment).write_text(original)
        assert task_checkpoint.apply(db, assignment, store)["verified"]


def test_a_replaced_project_directory_is_refused(tmp_path):
    _symlinks(tmp_path)
    root, db, store, task, assignment = _setup(tmp_path)
    other = tmp_path / "other-tree"
    _write(other, {"src/app.py": "someone else's file\n"})
    with closing(store):
        task_checkpoint.take(db, assignment, root, ["src"])
        store.finish(assignment, result="failed", summary="done")
        root.rename(tmp_path / "moved-away")
        os.symlink(other, root, target_is_directory=True)
        with pytest.raises(task_checkpoint.CheckpointError, match="replaced or moved|not a real directory"):
            task_checkpoint.apply(db, assignment, store)
    assert (other / "src/app.py").read_text() == "someone else's file\n"


@pytest.mark.skipif(not task_checkpoint._FD_SAFE, reason="descriptor-relative restore is POSIX only")
def test_a_link_swapped_in_after_planning_is_never_followed(tmp_path):
    root, db, store, task, assignment = _setup(tmp_path)
    outside = tmp_path / "outside"
    _write(outside, {"deep/file.txt": "outside, must stay\n"})
    with closing(store):
        task_checkpoint.take(db, assignment, root, ["src"])
        _write(root, {"src/newdir/deep/file.txt": "created\n"})
        store.finish(assignment, result="failed", summary="done")
        real_plan = task_checkpoint.plan

        def plan_then_swap(*args, **kwargs):
            steps = real_plan(*args, **kwargs)
            if (root / "src/newdir").is_dir() and not (root / "src/newdir").is_symlink():
                (root / "src/newdir/deep/file.txt").unlink()
                (root / "src/newdir/deep").rmdir()
                (root / "src/newdir").rmdir()
                os.symlink(outside, root / "src/newdir", target_is_directory=True)
            return steps

        with patch.object(task_checkpoint, "plan", plan_then_swap), \
                pytest.raises(task_checkpoint.CheckpointError, match="src/newdir is not a real directory"):
            task_checkpoint.apply(db, assignment, store)
    assert (outside / "deep/file.txt").read_text() == "outside, must stay\n"


def test_windows_junctions_count_as_links():
    junction = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_file_attributes=task_checkpoint._REPARSE)
    plain = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_file_attributes=0)
    assert task_checkpoint._is_link(junction) and not task_checkpoint._is_link(plain)  # type: ignore[arg-type]


def test_excluded_sensitive_and_private_scope_roots_are_refused(tmp_path):
    root, db, store, task, assignment = _setup(tmp_path)
    _write(root, {"node_modules/pkg/index.js": "x", ".git/config": "x"})
    with closing(store):
        for scope in ([".env"], ["src/.env"], ["node_modules/pkg"], [".git"], ["secrets/key.pem"]):
            with pytest.raises(task_checkpoint.CheckpointError, match="cannot checkpoint"):
                task_checkpoint.take(db, assignment, root, scope)


def test_restore_holds_a_claim_and_releases_it(tmp_path):
    root, db, store, task, assignment = _setup(tmp_path)
    with closing(store):
        task_checkpoint.take(db, assignment, root, ["src"])
        _worker_damage(root)
        store.finish(assignment, result="failed", summary="done")
        real_verify = task_checkpoint._verify_objects
        attempts = []

        def writer_arrives(*args, **kwargs):
            with closing(ledger.Ledger(db)) as other:
                with pytest.raises(ledger.ClaimConflictError):
                    other.assign(task, actor="claude", role="builder", mode="rw", scope=["src/pkg"])
                attempts.append("refused")
            return real_verify(*args, **kwargs)

        with patch.object(task_checkpoint, "_verify_objects", writer_arrives):
            result = task_checkpoint.apply(db, assignment, store)
        claim = store.activity(result["claim"])
    assert attempts == ["refused"] and result["verified"]
    assert claim["actor"] == task_checkpoint.RESTORE_ACTOR and claim["status"] == "passed"
    assert claim["ack_state"] == "acknowledged" and claim["scope"] == ["src"]


def test_live_claims_compare_case_insensitively_where_the_filesystem_does(tmp_path):
    root, db, store, task, assignment = _setup(tmp_path)
    with closing(store):
        store.finish(assignment, result="failed", summary="done")
        store.assign(task, actor="claude", role="builder", mode="rw", scope=["SRC/app.py"])
        with patch.object(task_checkpoint, "_FOLD", True):
            assert task_checkpoint.live_blockers(store, assignment, ["src"])
        with patch.object(task_checkpoint, "_FOLD", False):
            assert task_checkpoint.live_blockers(store, assignment, ["src"]) == []


def test_a_missing_saved_copy_stops_the_restore_before_any_change(tmp_path):
    root, db, store, task, assignment = _setup(tmp_path)
    with closing(store):
        task_checkpoint.take(db, assignment, root, ["src"])
        _worker_damage(root)
        store.finish(assignment, result="failed", summary="done")
        damaged = _tree(root)
        manifest = task_checkpoint.load(db, assignment)
        (task_checkpoint.location(db, assignment) / "objects" / manifest["entries"]["src/app.py"]["sha256"]).unlink()
        with pytest.raises(task_checkpoint.CheckpointError, match="missing; nothing was changed"):
            task_checkpoint.apply(db, assignment, store)
        assert all(item["status"] != "active" for item in store.show(task)["assignments"])
    assert _tree(root) == damaged


def test_retention_never_drops_a_checkpoint_while_its_task_has_active_work(tmp_path):
    root, db, store, task, assignment = _setup(tmp_path)
    with closing(store):
        task_checkpoint.take(db, assignment, root, ["src"])
        store.finish(assignment, result="failed", summary="done")
        other = store.assign(task, actor="claude", role="builder", mode="rw", scope=["docs"])["id"]
        assert task_checkpoint.prune(db, store, older_than_days=0) == []
        store.finish(other, result="passed", summary="done")
        assert [item["assignment"] for item in task_checkpoint.prune(db, store, older_than_days=0)] == [assignment]


@pytest.mark.skipif(sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="POSIX permission bits as a normal user")
def test_restrictive_directory_modes_are_restored_after_their_contents(tmp_path):
    root, db, store, task, assignment = _setup(tmp_path)
    with closing(store):
        (root / "src/pkg").chmod(0o555)
        task_checkpoint.take(db, assignment, root, ["src"])
        (root / "src/pkg").chmod(0o755)
        (root / "src/pkg/util.py").write_text("changed\n")
        store.finish(assignment, result="failed", summary="done")
        result = task_checkpoint.apply(db, assignment, store)
    assert result["verified"] and (root / "src/pkg/util.py").read_text() == "u = 1\n"
    assert stat.S_IMODE(os.lstat(root / "src/pkg").st_mode) == 0o555
    (root / "src/pkg").chmod(0o755)


def test_a_changed_mtime_alone_is_restored(tmp_path):
    root, db, store, task, assignment = _setup(tmp_path)
    with closing(store):
        task_checkpoint.take(db, assignment, root, ["src"])
        before = os.lstat(root / "src/app.py").st_mtime_ns
        os.utime(root / "src/app.py", ns=(before + 10**10, before + 10**10))
        store.finish(assignment, result="failed", summary="done")
        assert task_checkpoint.plan(db, assignment, store)["restore"] == ["src/app.py"]
        assert task_checkpoint.apply(db, assignment, store)["verified"]
    assert os.lstat(root / "src/app.py").st_mtime_ns == before


def test_manifest_entries_cannot_name_drives_protected_or_private_paths(tmp_path):
    assert not task_checkpoint._valid_rel("C:/Users/me/victim.txt") and not task_checkpoint._valid_rel("c:x")
    root = tmp_path / "project"
    _write(root, {"app.py": "a\n"})
    db = root / "ledger.sqlite"
    with closing(ledger.Ledger(db)) as store:
        task = store.open_task(project=root, title="T", objective="o")["id"]
        assignment = store.assign(task, actor="codex", role="builder", mode="rw", scope=["."])["id"]
        task_checkpoint.take(db, assignment, root, ["."])
        store.finish(assignment, result="failed", summary="done")
        original = _manifest_path(db, assignment).read_text()
        manifest = json.loads(original)
        some = manifest["entries"]["app.py"]
        for rel in ("C:/Users/me/victim.txt", ".env", ".git/config", "node_modules/x/index.js", "NODE_MODULES/y"):
            if rel == "NODE_MODULES/y" and not task_checkpoint._FOLD:
                continue
            _manifest_path(db, assignment).write_text(json.dumps({**manifest, "entries": {**manifest["entries"], rel: some}}))
            with pytest.raises(task_checkpoint.CheckpointError, match="is damaged"):
                task_checkpoint.plan(db, assignment, store)
        _manifest_path(db, assignment).write_text(json.dumps({**manifest, "entries": {**manifest["entries"],
                                                                                         "ledger.sqlite": some}}))
        with pytest.raises(task_checkpoint.CheckpointError, match="private state|is damaged"):
            task_checkpoint.apply(db, assignment, store)
        _manifest_path(db, assignment).write_text(original)


def test_excluded_scope_components_fold_case_where_the_filesystem_does(tmp_path):
    root, db, store, task, assignment = _setup(tmp_path)
    _write(root, {".git/config": "x"})
    with closing(store), patch.object(task_checkpoint, "_FOLD", True), \
            pytest.raises(task_checkpoint.CheckpointError, match="excluded directory"):
        task_checkpoint.take(db, assignment, root, [".GIT/config"])


def test_prune_skips_a_checkpoint_a_restore_holds(tmp_path):
    root, db, store, task, assignment = _setup(tmp_path)
    with closing(store):
        task_checkpoint.take(db, assignment, root, ["src"])
        store.finish(assignment, result="failed", summary="done")

        class Busy:
            def __enter__(self):
                raise TimeoutError

            def __exit__(self, *exc):
                return False

        with patch.object(task_checkpoint, "_locked", lambda db, assignment: Busy()):
            assert task_checkpoint.prune(db, store, older_than_days=0) == []
            with pytest.raises(task_checkpoint.CheckpointError, match="is busy"):
                task_checkpoint.apply(db, assignment, store)
        assert task_checkpoint.location(db, assignment).exists()


def test_write_claims_overlap_case_insensitively_on_windows_and_macos():
    for platform, expected in (("win32", True), ("darwin", True), ("linux", False)):
        with patch.object(ledger.sys, "platform", platform):
            assert ledger._claims_overlap("SRC/pkg", "src") is expected
            assert ledger._claims_overlap("src/pkg", "src") is True


def test_a_wholly_deleted_or_linked_scope_is_rebuilt(tmp_path):
    root, db, store, task, assignment = _setup(tmp_path)
    with closing(store):
        before = _tree(root)
        task_checkpoint.take(db, assignment, root, ["src"])
        env = (root / "src/.env").read_text()
        import shutil as _shutil
        _shutil.rmtree(root / "src")
        store.finish(assignment, result="failed", summary="done")
        result = task_checkpoint.apply(db, assignment, store)
    assert result["verified"]
    after = _tree(root)
    assert {key: value for key, value in after.items() if key != "src/.env"} == {
        key: value for key, value in before.items() if key != "src/.env"}
    assert env  # The sensitive file was never saved, so a deleted one stays deleted.


def test_restore_bookkeeping_never_counts_as_rework(tmp_path):
    from hipson import actor_routing

    root, db, store, task, assignment = _setup(tmp_path)
    with closing(store):
        store.connection.execute("UPDATE assignments SET needs = ? WHERE id = ?", (json.dumps(["code_write"]), assignment))
        store.connection.commit()
        task_checkpoint.take(db, assignment, root, ["src"])
        store.finish(assignment, result="failed", summary="done")
        claim = task_checkpoint.apply(db, assignment, store)["claim"]
        records = actor_routing.outcomes(store, role="builder")
        assert store.activity(claim)["needs"] == []
    assert [(record["assignment"], record["reworked"]) for record in records] == [(assignment, False)]


def test_the_text_dry_run_reports_conflicts(tmp_path):
    project = _project(tmp_path)
    _write(project, {"src/app.py": "a = 1\n"})
    with closing(ledger.Ledger(tmp_path / "ledger.sqlite")) as store:
        task = store.open_task(project=project, title="T", objective="o")["id"]
        store.set_status(task, to="in_progress", actor="claude")
    worker = ("from pathlib import Path; Path('src/app.py').unlink(); Path('src/app.py').mkdir(); "
              "Path('src/app.py/.env').write_text('SECRET=1')")
    run = _hipson(project, tmp_path, "task", "run", "--task", task, "--actor", "codex", "--role", "builder",
                  "--mode", "rw", "--scope", "src", "--checkpoint", "--", sys.executable, "-c", worker)
    assert run.returncode == 0, run.stderr
    with closing(ledger.Ledger(tmp_path / "ledger.sqlite", read_only=True)) as store:
        assignment = store.show(task)["assignments"][0]["id"]
    dry = _hipson(project, tmp_path, "task", "restore", "--assignment", assignment)
    assert "blocked: src/app.py is now a directory holding files restore leaves alone" in dry.stdout
    assert "apply with" not in dry.stdout


def test_private_state_and_locks_fold_case_where_the_filesystem_does(tmp_path):
    keys = task_checkpoint._private_keys([tmp_path / "ledger.sqlite"])
    with patch.object(task_checkpoint, "_FOLD", True):
        folded = task_checkpoint._private_keys([tmp_path / "ledger.sqlite"])
        assert task_checkpoint._is_private(tmp_path / "LEDGER.sqlite", folded)
        seen = []
        with patch("hipson.init_config.config_locks", lambda paths: seen.append(paths) or open(os.devnull)):
            task_checkpoint._locked(tmp_path / "Ledger.sqlite", "A-0001")
            task_checkpoint._locked(tmp_path / "ledger.SQLITE", "A-0001")
        assert seen[0] == seen[1]
    with patch.object(task_checkpoint, "_FOLD", False):
        assert not task_checkpoint._is_private(tmp_path / "LEDGER.sqlite", keys)


def test_windows_trailing_dot_and_space_aliases_are_never_trusted():
    with patch.object(task_checkpoint.sys, "platform", "win32"):
        for rel in (".git./config", "src./a.py", "src /a.py", "node_modules./x"):
            assert not task_checkpoint._valid_rel(rel), rel
        assert task_checkpoint._excluded_part("node_modules.") and task_checkpoint._excluded_part(".git ")
        assert task_checkpoint._protected(".env.") and task_checkpoint._protected(".git./config")
    assert task_checkpoint._valid_rel("src./a.py")  # Elsewhere "src." is its own directory.


def test_differently_cased_project_paths_share_claims_on_windows_and_macos(tmp_path):
    with closing(ledger.Ledger(tmp_path / "ledger.sqlite")) as store:
        first = store.open_task(project=tmp_path / "Repo", title="A", objective="o")["id"]
        second = store.open_task(project=tmp_path / "repo", title="B", objective="o")["id"]
        held = store.assign(first, actor="codex", role="builder", mode="rw", scope=["src"])["id"]
        with patch.object(ledger.sys, "platform", "darwin"):
            with pytest.raises(ledger.ClaimConflictError, match=held):
                store.assign(second, actor="claude", role="builder", mode="rw", scope=["SRC/pkg"])
        with patch.object(ledger.sys, "platform", "linux"):
            store.assign(second, actor="claude", role="builder", mode="rw", scope=["src"])
        done = store.assign(first, actor="codex", role="builder", mode="rw", scope=["docs"])["id"]
        store.finish(done, result="passed", summary="x")
        with patch.object(task_checkpoint, "_FOLD", True):
            assert any(held in reason for reason in task_checkpoint.live_blockers(store, done, ["src"]))


@pytest.mark.skipif(not task_checkpoint._FD_SAFE, reason="descriptor-anchored walk is POSIX only")
def test_a_directory_swapped_for_a_link_during_the_walk_is_never_followed(tmp_path):
    root, db, store, task, assignment = _setup(tmp_path)
    outside = tmp_path / "credentials"
    _write(outside, {"util.py": "TOKEN=outside\n"})
    real_lstat = task_checkpoint._Dir.lstat

    def lstat_then_swap(self, name):
        info = real_lstat(self, name)
        if name == "pkg" and (root / "src/pkg").is_dir() and not (root / "src/pkg").is_symlink():
            (root / "src/pkg/util.py").unlink()
            (root / "src/pkg").rmdir()
            os.symlink(outside, root / "src/pkg", target_is_directory=True)
        return info

    with closing(store), patch.object(task_checkpoint._Dir, "lstat", lstat_then_swap), \
            pytest.raises(task_checkpoint.CheckpointError, match="src/pkg changed while it was read"):
        task_checkpoint.take(db, assignment, root, ["src"])
    assert not task_checkpoint.location(db, assignment).exists()
    assert not list(task_checkpoint.directory(db).glob("*/objects/*"))


def test_the_walk_reads_only_the_verified_project_directory(tmp_path):
    _symlinks(tmp_path)
    root, db, store, task, assignment = _setup(tmp_path)
    with closing(store):
        with pytest.raises(task_checkpoint.CheckpointError, match="replaced or moved"):
            task_checkpoint.scan(root, ["src"], identity=[0, 0])
        task_checkpoint.take(db, assignment, root, ["src"])
        store.finish(assignment, result="failed", summary="done")
        other = tmp_path / "other-tree"
        _write(other, {"src/app.py": "elsewhere\n"})
        real_check = task_checkpoint._check_root

        def check_then_swap(manifest):
            checked = real_check(manifest)
            root.rename(tmp_path / "moved-away")
            os.symlink(other, root, target_is_directory=True)
            return checked

        with patch.object(task_checkpoint, "_check_root", check_then_swap), \
                pytest.raises(task_checkpoint.CheckpointError, match="replaced or moved|not a real directory"):
            task_checkpoint.plan(db, assignment, store)
    assert (other / "src/app.py").read_text() == "elsewhere\n"


def test_windows_claims_ignore_trailing_dots_and_spaces():
    with patch.object(ledger.sys, "platform", "win32"):
        assert ledger._claims_overlap("src.", "src") and ledger._claims_overlap("SRC /a.py", "src")
        assert ledger._claim_key(".") == "." and not ledger._claims_overlap("src.x", "src")
        with patch.object(task_checkpoint.sys, "platform", "win32"), patch.object(task_checkpoint, "_FOLD", True):
            assert task_checkpoint._overlaps("src.", "SRC") and task_checkpoint._overlaps(".", "src")
    with patch.object(ledger.sys, "platform", "linux"):
        assert not ledger._claims_overlap("src.", "src")


@pytest.mark.skipif(not hasattr(os, "mkfifo") or sys.platform == "darwin",
                    reason="needs a filesystem that accepts arbitrary name bytes")
def test_an_undecodable_special_file_name_still_releases_the_restore_claim(tmp_path):
    root, db, store, task, assignment = _setup(tmp_path)
    with closing(store):
        task_checkpoint.take(db, assignment, root, ["src"])
        store.finish(assignment, result="failed", summary="done")
        os.mkfifo(os.fsencode(str(root / "src")) + b"/bad\xff")
        with pytest.raises(task_checkpoint.CheckpointError, match="not a regular file"):
            task_checkpoint.apply(db, assignment, store)
        restore = [item for item in store.show(task)["assignments"] if item["actor"] == task_checkpoint.RESTORE_ACTOR]
    assert len(restore) == 1 and restore[0]["status"] == "failed" and restore[0]["ack_state"] == "acknowledged"


def test_an_unreadable_checkpoint_is_kept_for_the_retention_window(tmp_path):
    root, db, store, task, assignment = _setup(tmp_path)
    with closing(store):
        task_checkpoint.take(db, assignment, root, ["src"])
        store.finish(assignment, result="failed", summary="done")
        _manifest_path(db, assignment).write_text("{broken")
        assert task_checkpoint.prune(db, store) == []
        assert (task_checkpoint.location(db, assignment) / "objects").is_dir()
        assert task_checkpoint.prune(db, store, older_than_days=0) == [
            {"assignment": assignment, "reason": "unreadable and older than 0 days"}]


def test_saved_copies_are_never_read_through_links_or_from_devices(tmp_path):
    _symlinks(tmp_path)
    root, db, store, task, assignment = _setup(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("a = 1\n")
    with closing(store):
        task_checkpoint.take(db, assignment, root, ["src"])
        (root / "src/app.py").write_text("changed\n")
        store.finish(assignment, result="failed", summary="done")
        manifest = task_checkpoint.load(db, assignment)
        saved = task_checkpoint.location(db, assignment) / "objects" / manifest["entries"]["src/app.py"]["sha256"]
        for target in (outside, Path("/dev/zero")):
            if not target.exists():
                continue
            saved.unlink()
            os.symlink(target, saved)
            with pytest.raises(task_checkpoint.CheckpointError, match="missing|not the file that was saved"):
                task_checkpoint.apply(db, assignment, store)
    assert (root / "src/app.py").read_text() == "changed\n"


@pytest.mark.skipif(sys.platform == "win32", reason="backslashes are separators on Windows")
def test_names_a_manifest_cannot_hold_are_refused_before_launch(tmp_path):
    root, db, store, task, assignment = _setup(tmp_path)
    (root / "src" / "odd\\name.txt").write_text("x")
    with closing(store), pytest.raises(task_checkpoint.CheckpointError, match="cannot store"):
        task_checkpoint.take(db, assignment, root, ["src"])


def test_missing_manifest_metadata_is_damage_not_a_traceback(tmp_path):
    root, db, store, task, assignment = _setup(tmp_path)
    with closing(store):
        task_checkpoint.take(db, assignment, root, ["src"])
        manifest = json.loads(_manifest_path(db, assignment).read_text())
        for key in ("taken_at", "files", "bytes"):
            _manifest_path(db, assignment).write_text(json.dumps({k: v for k, v in manifest.items() if k != key}))
            with pytest.raises(task_checkpoint.CheckpointError, match="is damaged"):
                task_checkpoint.load(db, assignment)
        entries = {rel: ({k: v for k, v in entry.items() if k != "size"} if entry["type"] == "file" else entry)
                   for rel, entry in manifest["entries"].items()}
        _manifest_path(db, assignment).write_text(json.dumps({**manifest, "entries": entries}))
        with pytest.raises(task_checkpoint.CheckpointError, match="is damaged"):
            task_checkpoint.load(db, assignment)
        listed = task_checkpoint.checkpoints(db)
    assert listed[0]["assignment"] == assignment and "damaged" in listed[0]["error"]
