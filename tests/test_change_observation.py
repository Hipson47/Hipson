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


def test_cap_hit_is_incomplete_and_never_clean(tmp_path):
    root = _tree(tmp_path)
    record = _observe(root, lambda root: (root / "zz-after-the-cap.txt").write_text("x"), caps=Caps(max_files=2))
    assert record["truncated"] is True and record["status"] == "incomplete"
    assert record["paths"] == []  # Beyond the cap nothing can be compared: no claim either way.
    assert "INCOMPLETE" in task_observation.summary(record) and "no changes" not in task_observation.summary(record)
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

    record = _observe(_tree(tmp_path), change)
    assert record["status"] == "clean"
    assert {".git", "__pycache__", ".venv", "node_modules"} <= set(record["excluded_dirs"])


def test_secret_files_are_compared_without_any_content_digest(tmp_path):
    root = _tree(tmp_path)
    (root / ".env").write_text("TOKEN=one\n")
    before = take(root)
    assert before["entries"][".env"][3] is None and before["entries"]["README.md"][3] is not None
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
