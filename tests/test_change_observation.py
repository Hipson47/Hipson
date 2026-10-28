"""Opt-in post-run change observation: bounded snapshots, scope attribution, findings and ledger records."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pytest

from hipson import ledger, task_observation
from hipson.task_observation import Caps, classify, findings, take
from tests.test_hipson_helpers import REPO_ROOT, init_git_repo
from tests.test_task_verification import _project


def _tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    for relative, text in {"src/app.py": "a = 1\n", "src/util.py": "b = 2\n", "docs/guide.md": "# Guide\n",
                           "README.md": "readme\n"}.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(text, encoding="utf-8")
    return root


def _observe(root: Path, change, *, mode="rw", scope=("src",), others=(), caps: Caps | None = None):
    before = take(root, caps)
    change(root)
    return classify(before, take(root, caps), mode=mode, scope=scope, others=others)


def test_in_scope_edit_is_expected_and_raises_no_finding(tmp_path):
    record = _observe(_tree(tmp_path), lambda root: (root / "src/app.py").write_text("a = 2\n"))
    assert record["status"] == "changes" and not record["truncated"]
    assert record["counts"]["in_scope"] == {"created": 0, "modified": 1, "deleted": 0}
    assert record["counts"]["out_of_scope"] == {"created": 0, "modified": 0, "deleted": 0}
    assert record["paths"] == [{"path": "src/app.py", "change": "modified", "attribution": "in_scope",
                                "evidence": "content"}]
    assert findings(record, "A-0001") == []


def test_out_of_scope_create_modify_and_delete_each_raise_one_finding(tmp_path):
    def change(root):
        (root / "notes.txt").write_text("new\n")
        (root / "docs/extra.md").write_text("new\n")
        (root / "README.md").write_text("changed\n")
        (root / "docs/guide.md").unlink()
        (root / "src/new.py").write_text("ok\n")  # In scope: expected.

    record = _observe(_tree(tmp_path), change)
    assert record["counts"]["out_of_scope"] == {"created": 2, "modified": 1, "deleted": 1}
    assert record["counts"]["in_scope"]["created"] == 1
    assert [item["attribution"] for item in record["paths"]][:4] == ["out_of_scope"] * 4  # Unexpected first.
    raised = findings(record, "A-0007")
    assert [item["title"] for item in raised] == [
        "Observed 2 created path(s) outside the scope of assignment A-0007",
        "Observed 1 modified path(s) outside the scope of assignment A-0007",
        "Observed 1 deleted path(s) outside the scope of assignment A-0007",
    ]
    assert raised[0]["detail"].startswith("docs/extra.md, notes.txt.") and raised[0]["location"] == "docs/extra.md"
    assert "not attribution" in raised[2]["detail"]


def test_any_write_by_a_read_only_assignment_raises_a_finding(tmp_path):
    record = _observe(_tree(tmp_path), lambda root: (root / "src/app.py").write_text("a = 3\n"), mode="ro")
    assert record["counts"]["out_of_scope"]["modified"] == 1  # A read scope never permits writes.
    assert findings(record, "A-0002")[0]["title"] == (
        "Observed 1 modified path(s) outside the scope of read-only assignment A-0002")


def test_edit_then_revert_is_no_change_even_with_a_new_mtime(tmp_path):
    def change(root):
        path = root / "src/app.py"
        original = path.read_bytes()
        path.write_text("temporary\n")
        path.write_bytes(original)
        info = path.stat()
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 5_000_000_000))

    record = _observe(_tree(tmp_path), change)
    assert record["status"] == "clean" and record["paths"] == []


def test_without_a_hash_a_metadata_change_still_counts(tmp_path):
    def change(root):
        (root / "README.md").write_text("longer readme text\n")

    record = _observe(_tree(tmp_path), change, caps=Caps(max_hash_bytes=1))
    assert record["paths"] == [{"path": "README.md", "change": "modified", "attribution": "out_of_scope",
                                "evidence": "metadata"}]
    # Same-size edits with preserved timestamps would be invisible without hashes: never clean.
    assert record["status"] == "incomplete"
    assert record["incomplete_reasons"] == ["hash budget 1 bytes reached"]


def test_cap_hit_is_incomplete_and_never_clean(tmp_path):
    root = _tree(tmp_path)
    record = _observe(root, lambda root: (root / "zz-after-the-cap.txt").write_text("x"), caps=Caps(max_files=2))
    assert record["truncated"] is True and record["status"] == "incomplete"
    assert record["paths"] == []  # Beyond the cap nothing can be compared: no claim either way.
    assert record["incomplete_reasons"] == ["file cap 2 reached"]
    assert task_observation.summary(record).startswith("incomplete, not a clean result (file cap 2 reached)")
    assert "no changes" not in task_observation.summary(record)
    clean = _observe(root, lambda root: None, caps=Caps(max_files=2))
    assert clean["status"] == "incomplete"


def test_change_in_a_path_claimed_by_an_overlapping_assignment_is_unattributed(tmp_path):
    record = _observe(_tree(tmp_path), lambda root: (root / "docs/guide.md").write_text("edited\n"),
                      others=[("A-0042", ["docs"])])
    assert record["counts"]["unattributed"]["modified"] == 1
    assert record["concurrent_assignments"] == ["A-0042"]
    assert findings(record, "A-0001") == []


def test_git_and_tool_caches_are_excluded_and_listed(tmp_path):
    def change(root):
        (root / ".git").mkdir()
        (root / ".git" / "index").write_text("x")
        (root / "src" / "__pycache__").mkdir()
        (root / "src" / "__pycache__" / "app.pyc").write_bytes(b"x")
        (root / ".venv" / "lib").mkdir(parents=True)
        (root / ".venv" / "lib" / "installed.py").write_text("x")
        (root / "src" / ".git").write_text("gitdir: ../.git/worktrees/x\n")  # Worktree/submodule pointer file.

    record = _observe(_tree(tmp_path), change)
    assert record["status"] == "clean"
    assert {".git", "__pycache__", ".venv", "node_modules"} <= set(record["excluded_dirs"])


def test_secret_files_are_compared_without_any_content_digest(tmp_path):
    root = _tree(tmp_path)
    (root / ".env").write_text("TOKEN=one\n")
    before = take(root)
    keys = task_observation._key
    assert before["entries"][keys(".env")][4] is None and before["entries"][keys("README.md")][4] is not None
    (root / ".env").write_text("TOKEN=two-longer\n")
    record = classify(before, take(root), mode="rw", scope=["src"], others=[])
    assert record["paths"] == [{"path": ".env", "change": "modified", "attribution": "out_of_scope",
                                "evidence": "metadata"}]


def test_scope_comparison_follows_case_insensitive_filesystems(tmp_path):
    with patch.object(task_observation, "_FOLD", True):
        record = _observe(_tree(tmp_path), lambda root: (root / "src/app.py").write_text("x\n"), scope=("SRC",))
    assert record["counts"]["in_scope"]["modified"] == 1


def test_secret_shaped_paths_are_redacted_in_the_record(tmp_path):
    name = "token-ghp_" + "a" * 36 + ".txt"
    record = _observe(_tree(tmp_path), lambda root: (root / name).write_text("x"))
    assert "ghp_" + "a" * 36 not in json.dumps(record)
    assert record["counts"]["out_of_scope"]["created"] == 1


def test_caps_must_be_positive_integers():
    for bad in (0, -1, 1.5, True):
        with pytest.raises(ValueError, match="positive integer"):
            Caps(max_files=bad)  # type: ignore[arg-type]


def _ledger(tmp_path: Path, project: Path):
    store = ledger.Ledger(tmp_path / "ledger.sqlite")
    task = store.open_task(project=project, title="Observe", objective="Observe changes")["id"]
    store.set_status(task, to="in_progress", actor="claude")
    return store, task


def test_record_changes_adds_major_findings_and_an_event_once(tmp_path):
    root = _tree(tmp_path)
    store, task = _ledger(tmp_path, root)
    with closing(store):
        assignment = store.assign(task, actor="codex", role="builder", mode="rw", scope=["src"])["id"]
        with pytest.raises(ledger.LedgerError, match="still active"):
            store.record_changes(assignment, {"status": "clean"})
        store.finish(assignment, result="passed", summary="done")
        record = _observe(root, lambda root: (root / "notes.txt").write_text("x"))
        view = store.record_changes(assignment, record, findings(record, assignment))
        assert view["changes"]["counts"]["out_of_scope"]["created"] == 1
        open_findings = store.findings(task=task, status="open")
        assert [(item["severity"], item["source_assignment"]) for item in open_findings] == [("major", assignment)]
        event = store.events(task)[-1]
        assert event["type"] == "observed" and event["actor"] == "hipson"
        assert event["refs"] == [assignment, open_findings[0]["id"]]
        assert "1 outside scope" in event["summary"]
        assert any("open major finding" in reason for reason in ledger.done_blockers(store.show(task)))
        with pytest.raises(ledger.LedgerError, match="already has a change observation"):
            store.record_changes(assignment, record)
        with pytest.raises(ledger.LedgerError, match="known status"):
            store.record_changes(assignment, {"status": "fine"})
        assert ledger.reserved_event_type("observed")


def test_overlapping_writers_are_found_by_time_and_project(tmp_path):
    root = _tree(tmp_path)
    store, task = _ledger(tmp_path, root)
    with closing(store):
        mine = store.assign(task, actor="codex", role="builder", mode="rw", scope=["src"])["id"]
        other = store.assign(task, actor="agy", role="builder", mode="rw", scope=["docs"])["id"]
        reader = store.assign(task, actor="claude", role="reviewer", mode="ro", scope=[])["id"]
        store.finish(other, result="passed", summary="done")
        store.finish(reader, result="passed", summary="done")
        assert store.overlapping_writers(mine) == [(other, ["docs"])]
        store.finish(mine, result="passed", summary="done")
        later = store.assign(task, actor="codex", role="builder", mode="rw", scope=["src"])["id"]
        assert store.overlapping_writers(later) == []


def test_v7_ledgers_open_and_migrate_with_an_empty_changes_column(tmp_path):
    db = tmp_path / "ledger.sqlite"
    with closing(ledger.Ledger(db)) as store:
        task = store.open_task(project=tmp_path, title="Old", objective="Old ledger")["id"]
        store.assign(task, actor="codex", role="builder", mode="ro", scope=[])
    with closing(sqlite3.connect(db)) as connection:
        connection.execute("ALTER TABLE assignments DROP COLUMN changes")
        connection.execute("UPDATE schema_version SET version = 7")
        connection.commit()
    with closing(ledger.Ledger(db, read_only=True)) as store:
        assert store.show(task)["assignments"][0]["changes"] is None
    with closing(sqlite3.connect(db)) as connection:
        assert connection.execute("SELECT version FROM schema_version").fetchone()[0] == 7
    with closing(ledger.Ledger(db)) as store:
        assert store.connection.execute("SELECT version FROM schema_version").fetchone()[0] == 8
        assert "changes" in [row[1] for row in store.connection.execute("PRAGMA table_info(assignments)")]


def _env(tmp_path: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if key not in ("HIPSON_TASK", "HIPSON_ASSIGNMENT", "HIPSON_ROLE")}
    env.update(HIPSON_LEDGER=str(tmp_path / "ledger.sqlite"), PYTHONPATH=str(REPO_ROOT / "src"))
    return env


def test_task_run_observe_records_changes_and_findings_end_to_end(tmp_path):
    project = _project(tmp_path)
    (project / "src").mkdir()
    store, task = _ledger(tmp_path, project)
    store.close()
    worker = ("from pathlib import Path; Path('src/new.py').write_text('x'); Path('outside.txt').write_text('y'); "
              "Path('source.py').write_text('value = 2\\n')")
    run = subprocess.run([sys.executable, "-P", "-m", "hipson.cli", "task", "run", "--task", task, "--actor", "codex",
                          "--role", "builder", "--mode", "rw", "--scope", "src", "--observe", "--",
                          sys.executable, "-c", worker], cwd=project, env=_env(tmp_path), capture_output=True,
                         text=True, timeout=120)
    assert run.returncode == 0, run.stderr
    assert "observed changes: changes: in scope: 1 created; out of scope: 1 created, 1 modified" in run.stderr
    with closing(ledger.Ledger(tmp_path / "ledger.sqlite", read_only=True)) as store:
        view = store.show(task)
        changes = view["assignments"][0]["changes"]
        assert changes["counts"]["out_of_scope"] == {"created": 1, "modified": 1, "deleted": 0}
        assert sorted(item["title"] for item in view["findings"]) == [
            f"Observed 1 created path(s) outside the scope of assignment {view['assignments'][0]['id']}",
            f"Observed 1 modified path(s) outside the scope of assignment {view['assignments'][0]['id']}"]
    assert not task_observation.pending(tmp_path / "ledger.sqlite")  # The baseline is consumed.
    shown = subprocess.run([sys.executable, "-P", "-m", "hipson.cli", "task", "show", "--task", task],
                           cwd=project, env=_env(tmp_path), capture_output=True, text=True, timeout=60)
    assert "Changes changes: in scope: 1 created; out of scope: 1 created, 1 modified" in shown.stdout
    assert "out_of_scope created: outside.txt" in shown.stdout
    now = subprocess.run([sys.executable, "-P", "-m", "hipson.cli", "task", "now", "--all"],
                         cwd=project, env=_env(tmp_path), capture_output=True, text=True, timeout=60)
    assert now.returncode == 0, now.stderr
    assert "observed changes: in scope: 1 created; out of scope: 1 created, 1 modified" in now.stdout
    now_json = subprocess.run([sys.executable, "-P", "-m", "hipson.cli", "task", "now", "--all", "--json"],
                              cwd=project, env=_env(tmp_path), capture_output=True, text=True, timeout=60)
    assert '"out_of_scope"' in now_json.stdout and '"changes"' in now_json.stdout  # MCP task.now shares this JSON.


def test_task_run_without_observe_records_nothing(tmp_path):
    project = _project(tmp_path)
    store, task = _ledger(tmp_path, project)
    store.close()
    run = subprocess.run([sys.executable, "-P", "-m", "hipson.cli", "task", "run", "--task", task, "--actor", "codex",
                          "--role", "builder", "--mode", "rw", "--scope", ".", "--",
                          sys.executable, "-c", "open('x.txt', 'w').write('x')"], cwd=project, env=_env(tmp_path),
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr
    with closing(ledger.Ledger(tmp_path / "ledger.sqlite", read_only=True)) as store:
        view = store.show(task)
    assert view["assignments"][0]["changes"] is None and view["findings"] == []


def test_reap_completes_a_stored_baseline_when_the_supervisor_died(tmp_path):
    root = _tree(tmp_path)
    db = tmp_path / "ledger.sqlite"
    store, task = _ledger(tmp_path, root)
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    with closing(store):
        assignment = store.assign(task, actor="codex", role="builder", mode="rw", scope=["src"], owner_pid=gone.pid,
                                  owner_host=store._hostname(), owner_boot_id=ledger._boot_id(),
                                  owner_pid_namespace=ledger._pid_namespace())["id"]
    task_observation.start(db, assignment, root, Caps())
    (root / "stray.txt").write_text("written by the dead worker")
    reaped = subprocess.run([sys.executable, "-P", "-m", "hipson.cli", "task", "reap", "--db", str(db), "--json"],
                            env=_env(tmp_path), capture_output=True, text=True, timeout=60)
    assert reaped.returncode == 0, reaped.stderr
    with closing(ledger.Ledger(db, read_only=True)) as store:
        item = store.show(task)["assignments"][0]
        assert item["status"] == "cancelled"
        assert item["changes"]["counts"]["out_of_scope"]["created"] == 1
    assert task_observation.pending(db) == []


def test_project_policy_can_opt_in_and_rejects_bad_values(tmp_path):
    import argparse

    from hipson import policy, task_runner
    repo = init_git_repo(tmp_path)
    args = argparse.Namespace(observe=False, observe_max_files=None, observe_max_bytes=None)
    assert task_runner.observation_caps(args, str(repo)) is None
    (repo / ".hipson").mkdir()
    (repo / ".hipson" / "policy.json").write_text(json.dumps({"observe_changes": True, "observe_max_files": 50}))
    assert task_runner.observation_caps(args, str(repo)) == Caps(max_files=50)
    (repo / ".hipson" / "policy.json").write_text(json.dumps({"observe_changes": "yes", "observe_max_bytes": 0}))
    issues = policy.load_policy(repo)["issues"]
    assert "observe_changes must be true or false" in issues and "observe_max_bytes must be a positive integer" in issues
    assert task_runner.observation_caps(args, str(repo)) is None  # An invalid policy never enables anything.


def test_baseline_never_stores_secret_shaped_names(tmp_path):
    root = _tree(tmp_path)
    secret = "ghp_" + "b" * 36
    (root / "src" / f"{secret}.txt").write_text("x")
    db = tmp_path / "ledger.sqlite"
    task_observation.start(db, "A-0001", root, Caps())
    stored = task_observation.baseline_path(db, "A-0001").read_text(encoding="utf-8")
    assert secret not in stored and "\\u0000" in stored  # NUL-marked digest component, JSON-escaped.
    (root / "src" / f"{secret}.txt").write_text("changed")
    before = json.loads(stored)
    record = classify(before, take(root), mode="rw", scope=["src"], others=[])
    assert record["counts"]["in_scope"]["modified"] == 1 and secret not in json.dumps(record)


def test_unreadable_directories_make_the_observation_incomplete(tmp_path):
    root = _tree(tmp_path)
    scandir = os.scandir

    def failing(path):
        if str(path).endswith("docs"):
            raise PermissionError("denied")
        return scandir(path)

    with patch.object(task_observation.os, "scandir", side_effect=failing):
        record = _observe(root, lambda root: (root / "docs/guide.md").write_text("hidden edit\n"))
    assert record["status"] == "incomplete" and record["incomplete_reasons"] == ["unreadable directory"]
    assert record["paths"] == []  # Nothing under the unreadable directory is claimed either way.
    before = take(root)
    with patch.object(task_observation.os, "scandir", side_effect=failing):
        after = take(root)
    record = classify(before, after, mode="rw", scope=["src"], others=[])
    assert record["counts"]["out_of_scope"]["deleted"] == 0 and record["status"] == "incomplete"


def test_hashing_reads_exactly_the_stat_ed_regular_file(tmp_path):
    path = tmp_path / "file.txt"
    path.write_bytes(b"abc")
    info = path.lstat()
    digest, read = task_observation._digest(str(path), info)
    assert read == 3 and digest is not None
    path.write_bytes(b"abc and more bytes")  # Grew after stat: no digest, bounded read.
    assert task_observation._digest(str(path), info) == (None, 0)
    if sys.platform != "win32" and hasattr(os, "symlink"):
        outside = tmp_path / "outside.txt"
        outside.write_bytes(b"abc")
        path.unlink()
        os.symlink(outside, path)  # Replaced by a link after stat: never followed.
        assert task_observation._digest(str(path), info) == (None, 0)


def test_traversal_is_bounded_by_entries_and_depth(tmp_path):
    root = tmp_path / "wide"
    root.mkdir()
    for index in range(50):
        (root / f"f{index:02d}").write_text("x")
    snapshot = take(root, Caps(max_files=5))
    assert snapshot["truncated"] and snapshot["entries"] == {} and snapshot["incomplete"] == ["file cap 5 reached"]
    deep = tmp_path / "deep"
    current = deep
    for _ in range(task_observation.MAX_DEPTH + 5):
        current = current / "d"
    current.mkdir(parents=True)
    snapshot = take(deep)
    assert snapshot["truncated"] and f"depth limit {task_observation.MAX_DEPTH} reached" in snapshot["incomplete"]


def test_in_project_ledger_and_baselines_are_never_attributed(tmp_path):
    project = _tree(tmp_path)
    db = project / "ledger.sqlite"
    store, task = _ledger_at(db, project)
    with closing(store):
        assignment = store.assign(task, actor="codex", role="reviewer", mode="ro", scope=[])["id"]
    task_observation.start(db, assignment, project, Caps())
    with closing(ledger.Ledger(db)) as store:
        store.add_event(task, actor="claude", type="note", summary="ledger write during the run")
        store.finish(assignment, result="passed", summary="read only")
    view = task_observation.complete(db, assignment)
    assert view is not None and view["changes"]["status"] == "clean", view["changes"]
    assert set(task_observation.ledger_exclusions(db, project)) == {
        "ledger.sqlite", "ledger.sqlite-wal", "ledger.sqlite-shm", "ledger.sqlite-journal",
        ".ledger.sqlite.observations"}


def _ledger_at(db: Path, project: Path):
    store = ledger.Ledger(db)
    task = store.open_task(project=project, title="Observe", objective="Observe changes")["id"]
    store.set_status(task, to="in_progress", actor="claude")
    return store, task


def test_ledgers_in_one_directory_keep_separate_baselines(tmp_path):
    first = task_observation.baseline_path(tmp_path / "a.sqlite", "A-0001")
    second = task_observation.baseline_path(tmp_path / "b.sqlite", "A-0001")
    assert first != second and first.parent != second.parent


def test_attribution_window_ends_at_the_final_snapshot(tmp_path):
    root = _tree(tmp_path)
    store, task = _ledger(tmp_path, root)
    with closing(store):
        reader = store.assign(task, actor="claude", role="reviewer", mode="ro", scope=[])["id"]
        stamp = ledger._timestamp()
        later = store.assign(task, actor="agy", role="builder", mode="rw", scope=["docs"])["id"]
        store.finish(reader, result="passed", summary="done")
        assert store.overlapping_writers(reader, until=stamp) == []  # Started after the snapshot.
        assert store.overlapping_writers(reader) == [(later, ["docs"])]  # Default: until the reader ended.


def test_explicit_observe_keeps_policy_caps(tmp_path):
    import argparse

    from hipson import task_runner
    repo = init_git_repo(tmp_path)
    (repo / ".hipson").mkdir()
    (repo / ".hipson" / "policy.json").write_text(json.dumps({"observe_max_files": 7, "observe_max_bytes": 9}))
    args = argparse.Namespace(observe=True, observe_max_files=None, observe_max_bytes=None)
    assert task_runner.observation_caps(args, str(repo)) == Caps(max_files=7, max_hash_bytes=9)
    args.observe_max_files = 3
    assert task_runner.observation_caps(args, str(repo)) == Caps(max_files=3, max_hash_bytes=9)


def test_observed_event_lists_the_changed_paths(tmp_path):
    root = _tree(tmp_path)
    store, task = _ledger(tmp_path, root)
    with closing(store):
        assignment = store.assign(task, actor="codex", role="builder", mode="rw", scope=["src"])["id"]
        store.finish(assignment, result="passed", summary="done")
        record = _observe(root, lambda root: (root / "src/new.py").write_text("x"))
        store.record_changes(assignment, record, findings(record, assignment))
        assert "paths: in_scope created src/new.py" in store.events(task)[-1]["summary"]


def test_a_failed_baseline_is_recorded_as_unobserved(tmp_path):
    root = _tree(tmp_path)
    db = tmp_path / "ledger.sqlite"
    store, task = _ledger(tmp_path, root)
    with closing(store):
        assignment = store.assign(task, actor="codex", role="builder", mode="rw", scope=["src"])["id"]
        store.finish(assignment, result="failed", summary="worker not started")
    task_observation.start_failed(db, assignment, "baseline failed: PermissionError")
    view = task_observation.complete(db, assignment)
    assert view is not None and view["changes"]["status"] == "unobserved"
    assert view["changes"]["incomplete_reasons"] == ["baseline failed: PermissionError"]
    assert task_observation.summary(view["changes"]).startswith("unobserved (baseline failed: PermissionError")


def test_orphan_recovery_inside_assign_and_mcp_reap_complete_observations(tmp_path):
    from hipson import mcp_server
    root = _tree(tmp_path)
    db = tmp_path / "ledger.sqlite"
    store, task = _ledger(tmp_path, root)
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    with closing(store):
        orphan = store.assign(task, actor="codex", role="builder", mode="rw", scope=["src"], owner_pid=gone.pid,
                              owner_host=store._hostname(), owner_boot_id=ledger._boot_id(),
                              owner_pid_namespace=ledger._pid_namespace())["id"]
    task_observation.start(db, orphan, root, Caps())
    (root / "stray.txt").write_text("x")
    with patch.dict(os.environ, HIPSON_LEDGER=str(db)):
        mcp_server._tool_payload("task.reap", {"approved": True}, project=root)
    with closing(ledger.Ledger(db, read_only=True)) as store:
        item = store.activity(orphan)
    assert item["status"] == "cancelled" and item["changes"]["counts"]["out_of_scope"]["created"] == 1
    assert task_observation.pending(db) == []


def test_a_literal_name_cannot_collide_with_an_encoded_secret_component(tmp_path):
    if sys.platform == "win32":
        pytest.skip("Windows file names cannot contain ':'")
    root = _tree(tmp_path)
    secret = "ghp_" + "c" * 36
    (root / f"{secret}.txt").write_text("one")
    encoded = task_observation._key(f"{secret}.txt")
    (root / encoded.replace("\0", "sha256:")).write_text("two")  # The old marker, as a literal file name.
    before = take(root)
    assert len([key for key in before["entries"] if key not in ("README.md",)]) >= 2
    (root / f"{secret}.txt").write_text("changed")
    record = classify(before, take(root), mode="rw", scope=["src"], others=[])
    assert record["counts"]["out_of_scope"]["modified"] == 1


def test_unreadable_secret_shaped_directory_hides_its_descendants(tmp_path):
    root = _tree(tmp_path)
    secret_dir = root / "src" / ("ghp_" + "d" * 36)
    secret_dir.mkdir()
    (secret_dir / "file.py").write_text("x")
    before = take(root)
    scandir = os.scandir

    def failing(path):
        if str(path).endswith("d" * 36):
            raise PermissionError("denied")
        return scandir(path)

    with patch.object(task_observation.os, "scandir", side_effect=failing):
        after = take(root)
    record = classify(before, after, mode="ro", scope=[], others=[])
    assert record["counts"]["out_of_scope"]["deleted"] == 0 and record["status"] == "incomplete"


def test_redacted_text_never_satisfies_a_scope(tmp_path):
    root = _tree(tmp_path)
    secret_dir = root / "src" / ("ghp_" + "e" * 36)
    secret_dir.mkdir()
    (secret_dir / "file.py").write_text("x")
    redacted_scope = task_observation.redact_text(f"src/{'ghp_' + 'e' * 36}/file.py")
    assert redacted_scope != f"src/{'ghp_' + 'e' * 36}/file.py"
    record = _observe(root, lambda root: (secret_dir / "file.py").write_text("y"), scope=(redacted_scope,))
    assert record["counts"]["out_of_scope"]["modified"] == 1 and record["counts"]["in_scope"]["modified"] == 0
    exact = _observe(root, lambda root: (secret_dir / "file.py").write_text("z"), scope=("src",))
    assert exact["counts"]["in_scope"]["modified"] == 1


def test_files_without_an_identity_are_not_hashed(tmp_path):
    path = tmp_path / "file.txt"
    path.write_bytes(b"abc")
    info = os.stat_result((stat_mode(path), 0, 0, 1, 0, 0, 3, 0, 0, 0))
    assert task_observation._digest(str(path), info) == (None, 0)


def stat_mode(path: Path) -> int:
    return path.lstat().st_mode


def test_secret_shaped_ledger_exclusions_are_encoded(tmp_path):
    project = _tree(tmp_path)
    db = project / ("ghp_" + "f" * 36 + ".sqlite")
    stored = json.dumps(task_observation.ledger_exclusions(db, project))
    assert "f" * 36 not in stored and "\\u0000" in stored


def test_window_end_is_taken_after_the_final_walk(tmp_path):
    root = _tree(tmp_path)
    db = tmp_path / "ledger.sqlite"
    task_observation.start(db, "A-0001", root, Caps())
    stamps = []
    real_take = task_observation.take

    def slow_take(*args, **kwargs):
        stamps.append(("walk", ledger._timestamp()))
        return real_take(*args, **kwargs)

    with patch.object(task_observation, "take", side_effect=slow_take):
        _, stamp = task_observation.capture_after(db, "A-0001")
    assert stamp > stamps[0][1]


def test_unwritable_baseline_storage_still_records_unobserved(tmp_path):
    project = _project(tmp_path)
    store, task = _ledger(tmp_path, project)
    store.close()
    blocker = tmp_path / ".ledger.sqlite.observations"
    blocker.write_text("not a directory")  # Baseline and failure marker storage both fail.
    run = subprocess.run([sys.executable, "-P", "-m", "hipson.cli", "task", "run", "--task", task, "--actor", "codex",
                          "--role", "builder", "--mode", "rw", "--scope", ".", "--observe", "--",
                          sys.executable, "-c", "pass"], cwd=project, env=_env(tmp_path), capture_output=True,
                         text=True, timeout=120)
    assert run.returncode != 0 and "observation baseline" in run.stderr, run.stderr
    with closing(ledger.Ledger(tmp_path / "ledger.sqlite", read_only=True)) as store:
        changes = store.show(task)["assignments"][0]["changes"]
    assert changes["status"] == "unobserved" and changes["incomplete_reasons"][0].startswith("baseline failed")


def test_legacy_records_without_incomplete_reasons_still_render(tmp_path):
    root = _tree(tmp_path)
    store, task = _ledger(tmp_path, root)
    with closing(store):
        assignment = store.assign(task, actor="codex", role="builder", mode="rw", scope=["src"])["id"]
        store.finish(assignment, result="passed", summary="done")
        legacy = {"status": "incomplete", "truncated": True, "counts": {}, "paths": [], "caps": {"max_files": 2}}
        store.connection.execute("UPDATE assignments SET changes = ? WHERE id = ?", (json.dumps(legacy), assignment))
        changes = store.activity(assignment)["changes"]
    assert changes["incomplete_reasons"] == []
    assert task_observation.summary(changes).startswith("incomplete, not a clean result")


def test_secret_detection_runs_before_case_folding_and_across_separators():
    aws = "AKIA" + "A" * 16
    with patch.object(task_observation, "_FOLD", True):
        assert aws not in task_observation._key(f"src/{aws}.db") and aws.lower() not in task_observation._key(aws)
    spanning = task_observation._key("token=/private-value")
    assert "private-value" not in spanning and spanning.split("/")[-1].startswith(task_observation._HASHED)


def test_keys_preserve_case_so_distinct_files_never_merge():
    assert task_observation._key("A.db") != task_observation._key("a.db")
    with patch.object(task_observation, "_FOLD", True):
        assert task_observation._key("A.db") == "A.db"
        assert task_observation._covered("Src/app.py", ["src"])  # Scope matching still ignores case there.
        assert not task_observation._covered("srcx/app.py", ["src"])
    before = {"entries": {"A.db": ["A.db", "file", 1, 1, "x", "A.db"], "a.db": ["a.db", "file", 1, 1, "y", "a.db"]},
              "truncated": False, "last": None, "unreadable": [], "incomplete": [], "caps": {}}
    after = {**before, "entries": {"A.db": ["A.db", "file", 2, 2, "z", "A.db"], "a.db": ["a.db", "file", 1, 1, "y", "a.db"]}}
    record = classify(before, after, mode="rw", scope=["src"], others=[])
    assert record["status"] == "changes" and record["counts"]["out_of_scope"]["modified"] == 1


def test_truncation_bound_follows_the_walk_order_with_mixed_case(tmp_path):
    root = tmp_path / "mixed"
    (root / "a").mkdir(parents=True)
    (root / "Z.db").write_text("z")
    (root / "a" / "f.db").write_text("f")
    with patch.object(task_observation, "_FOLD", True):
        before = take(root, Caps(max_files=2))
        (root / "Z.db").unlink()
        after = take(root, Caps(max_files=2))
        record = classify(before, after, mode="ro", scope=[], others=[])
    assert record["counts"]["out_of_scope"]["created"] == 0, record["paths"]
    assert record["status"] == "incomplete"


def test_no_part_of_a_separator_spanning_secret_survives():
    key = task_observation._key("token=first-part/private-tail")
    assert "first-part" not in key and "private-tail" not in key
    assert all(part.startswith(task_observation._HASHED) for part in key.split("/"))


def test_secret_tails_keep_their_ancestors_for_scope_and_unreadable_prefixes():
    child = task_observation._key("src/token=/private-value/app.py")
    parent = task_observation._key("src/token=/private-value")
    assert child.startswith(parent + "/") and "private-value" not in child
    assert task_observation._covered(child, ["src"])
    secret_dir = "src/" + "ghp_" + "g" * 36
    assert task_observation._key(secret_dir + "/file.py").startswith(task_observation._key(secret_dir) + "/")


def test_mixed_case_claims_match_hashed_components_on_case_insensitive_filesystems():
    lower = "ghp_" + "h" * 36
    with patch.object(task_observation, "_FOLD", True):
        key = task_observation._match(f"src/{lower}/file.py")
        assert task_observation._covered(key, [f"SRC/{lower.upper()}"])
    with patch.object(task_observation, "_FOLD", False):
        assert not task_observation._covered(task_observation._match(f"src/{lower}/file.py"), [f"src/{lower.upper()}"])


def test_ledger_exclusions_ignore_case_on_case_insensitive_filesystems(tmp_path):
    project = _tree(tmp_path)
    (project / ".LEDGER.sqlite.observations").mkdir()
    (project / ".LEDGER.sqlite.observations" / "A-0001.json").write_text("{}")
    db = project / "ledger.sqlite"
    with patch.object(task_observation, "_FOLD", True):
        snapshot = take(project, exclude=task_observation.ledger_exclusions(db, project))
    assert not any("observations" in key.casefold() for key in snapshot["entries"])


def test_hashing_starts_where_the_redactor_first_changes_text():
    path = 'src/token="first-part/private-tail"/app.py'
    key = task_observation._key(path)
    match = task_observation._match(path)
    assert "first-part" not in key and "private-tail" not in key and key.startswith("src/")
    assert task_observation._covered(match, ["src"]) and "first-part" not in match
    assert task_observation._prefix_of(task_observation._match('src/token="first-part'), match)
    pem = "keys/-----BEGIN PRIVATE KEY-----/MIIBVgIBADANBgkqhkiG9w0BAQEFAASC/-----END PRIVATE KEY-----.pem"
    assert "MIIBVgIBADANBgkqhkiG9w0BAQEFAASC" not in task_observation._key(pem)


def test_exclusions_match_hashed_and_plain_spellings_on_case_insensitive_filesystems():
    name = ".ghp_" + "h" * 36 + ".sqlite.observations"
    with patch.object(task_observation, "_FOLD", True):
        assert task_observation._equal(task_observation._match(name), task_observation._match(name.upper()))
    assert not task_observation._equal(task_observation._match(name), task_observation._match("other"))


def test_unreadable_directories_are_stored_opaquely(tmp_path):
    root = tmp_path / "keys"
    partial = root / "-----BEGIN PRIVATE KEY-----" / "MIIBVgIBADANBgkqhkiG9w0BAQEFAASC"
    partial.mkdir(parents=True)
    (partial / "-----END PRIVATE KEY-----.pem").write_text("x")
    scandir = os.scandir

    def failing(path):
        if str(path).endswith("MIIBVgIBADANBgkqhkiG9w0BAQEFAASC"):
            raise PermissionError("denied")
        return scandir(path)

    with patch.object(task_observation.os, "scandir", side_effect=failing):
        snapshot = take(tmp_path)
    stored = json.dumps(snapshot)
    assert "MIIBVgIBADANBgkqhkiG9w0BAQEFAASC" not in stored and snapshot["incomplete"] == ["unreadable directory"]


def test_distinct_files_under_a_secret_directory_stay_distinct_when_folding(tmp_path):
    secret = "ghp_" + "k" * 36
    with patch.object(task_observation, "_FOLD", True):
        upper, lower = task_observation._key(f"{secret}/A.txt"), task_observation._key(f"{secret}/a.txt")
        assert upper != lower  # Primary identity keeps case even where matching ignores it.
        assert task_observation._match(f"{secret}/A.txt") == task_observation._match(f"{secret}/a.txt")
