"""Read-only project cleanup checks through CLI and MCP."""

import json
import shlex
from contextlib import closing
from unittest.mock import patch

from hipson import ledger, task_verification
from tests.test_hipson_helpers import run_cli_inprocess
from tests.test_ledger_mcp import _call, _payload
from tests.test_task_projection import _evidence
from tests.test_task_verification import _git, _project


def test_cleanup_refuses_before_removing_the_worktree_in_the_november_incident(tmp_path):
    repo = _project(tmp_path)
    worktree = tmp_path / "incident worktree"
    _git(repo, "worktree", "add", "--detach", str(worktree))
    db = tmp_path / "ledger.sqlite"
    with closing(ledger.Ledger(db)) as store:
        # Start the disposable ledger at the incident ID without copying real task history.
        stamp = ledger._timestamp()
        store.connection.execute(
            """INSERT INTO tasks (id, project, title, objective, acceptance, status, lead, created_at, updated_at, revision)
               VALUES ('T-0150', ?, 'Earlier task', 'Seed the incident ID', '[]', 'done', 'claude', ?, ?, 0)""",
            (str(repo), stamp, stamp),
        )
        task = store.open_task(project=worktree, title="Code review in worktree", objective="Close before removal")["id"]
        assert task == "T-0151"

    before = db.read_bytes()
    args = ("task", "cleanup", "--db", str(db), "--project", str(worktree))
    entries = sorted(tmp_path.rglob("*"))
    with patch.object(task_verification, "project_state", side_effect=AssertionError("content fingerprint")) as state:
        code, stdout, stderr = run_cli_inprocess(*args, "--json")
        assert code == 1 and "\n" not in stdout
        report, refusal = json.loads(stdout), json.loads(stderr)
        assert report == {"project": str(worktree), "omitted_tasks": 0, "tasks": [
            {"id": task, "project": str(worktree), "status": "open", "assignments": []},
        ]}
        assert refusal["error_schema"] == 1 and refusal["code"] == "cleanup.tasks_open"
        show, close, cancel = refusal["fix"]
        assert show["arguments"] == {"task": task, "detail": "compact"}
        assert close["tool"] == "task.close" and close["arguments"] == {"task": task}
        assert f"task close --task {task}" in close["cli"]
        assert cancel["arguments"] == {"task": task, "to": "cancelled", "actor": "claude"}
        assert shlex.split(cancel["cli"])[-2:] == ["--summary", "REASON"]
        assert not any("approved" in fix["arguments"] for fix in refusal["fix"])

        code, text, diagnostic = run_cli_inprocess(*args)
        assert code == 1 and f"{task} [open]" in text
        assert f"fix: hipson task close --task {task}" in diagnostic
        print(f"Incident cleanup:\n{text}{diagnostic}")
        result = _call(repo, "task.cleanup", ledger_db=db, project=str(worktree))
        assert result["isError"] is True
        message, body, evidence = (item["text"] for item in result["content"])
        assert message == refusal["message"] and json.loads(body) == refusal and json.loads(evidence) == report
        assert db.read_bytes() == before and sorted(tmp_path.rglob("*")) == entries

        # Simulate the caller ignoring the refusal. The recorded project binding survives removal.
        _git(repo, "worktree", "remove", str(worktree))
        assert not worktree.exists()
        assert run_cli_inprocess(*args, "--json") == (1, stdout, stderr)
        assert db.read_bytes() == before
        state.assert_not_called()


def test_cleanup_reports_descendants_and_active_assignments_on_terminal_tasks(tmp_path):
    root = _project(tmp_path)
    child = root / "child"
    child.mkdir()
    sibling = root.with_name(root.name + "-other")
    sibling.mkdir()
    db = tmp_path / "ledger.sqlite"
    with closing(ledger.Ledger(db)) as store:
        done = store.open_task(project=root, title="Finished parent task", objective="Inspect closure")["id"]
        store.set_status(done, to="in_progress", actor="claude")
        _evidence(store, done, root)
        store.set_status(done, to="review", actor="claude")
        store.set_status(done, to="done", actor="claude")
        cancelled = store.open_task(project=child, title="Cancelled child task", objective="Inspect active claims")["id"]
        assignment = store.assign(cancelled, actor="builder", role="builder", mode="rw", scope=["a.txt", "b.txt"])["id"]
        store.set_status(cancelled, to="cancelled", actor="claude")
        store.open_task(project=sibling, title="Unrelated open task", objective="Check path boundaries")

    selected = str(child / "..")
    args = ("task", "cleanup", "--db", str(db), "--project", selected, "--json")
    before = db.read_bytes()
    code, stdout, stderr = run_cli_inprocess(*args)
    assert code == 1 and json.loads(stderr)["code"] == "cleanup.tasks_open"
    report = json.loads(stdout)
    assert report["project"] == str(root)
    assert [(task["id"], task["status"]) for task in report["tasks"]] == [(done, "done"), (cancelled, "cancelled")]
    assert report["tasks"][1]["assignments"] == [
        {"id": assignment, "role": "builder", "mode": "rw", "status": "active", "claims": ["a.txt", "b.txt"]},
    ]
    fixes = json.loads(stderr)["fix"]
    assert [(fix["tool"], fix["arguments"]) for fix in fixes] == [
        ("task.show", {"task": cancelled, "detail": "compact"}),
        ("task.activity", {"assignment": assignment}),
    ]
    assert db.read_bytes() == before

    with closing(ledger.Ledger(db)) as store:
        store.finish(assignment, result="cancelled", summary="Worktree can be removed")
    before = db.read_bytes()
    code, stdout, stderr = run_cli_inprocess(*args)
    assert code == 0 and not stderr
    report = json.loads(stdout)
    assert report["tasks"][1]["assignments"][0] == {
        "id": assignment, "role": "builder", "mode": "rw", "status": "cancelled", "claims": [],
    }
    assert _payload(_call(root, "task.cleanup", ledger_db=db, project=selected)) == report
    assert db.read_bytes() == before

    # Match the ledger's case-insensitive project comparison on Windows and macOS.
    with patch.object(ledger.sys, "platform", "darwin"):
        case_variant = root.with_name(root.name.upper())
        result = _payload(_call(root, "task.cleanup", ledger_db=db, project=str(case_variant)))
    assert result["tasks"] == report["tasks"]


def test_cleanup_does_not_create_an_absent_ledger(tmp_path):
    db = tmp_path / "ledger.sqlite"
    report = {"project": str(tmp_path), "tasks": [], "omitted_tasks": 0}
    code, stdout, stderr = run_cli_inprocess("task", "cleanup", "--db", str(db), "--project", str(tmp_path), "--json")
    assert code == 0 and not stderr and json.loads(stdout) == report
    assert _payload(_call(tmp_path, "task.cleanup", ledger_db=db, project=str(tmp_path))) == report
    assert not list(tmp_path.iterdir())


def test_cleanup_report_and_fix_list_are_bounded(tmp_path):
    """S5 review: a broad path (/, a home directory) must not produce an unbounded report or fix list."""
    from hipson import task_calls

    db = tmp_path / "ledger.sqlite"
    root = tmp_path / "many"
    with closing(ledger.Ledger(db)) as store:
        for index in range(task_calls.CLEANUP_TASK_LIMIT + 5):
            project = root / f"p{index}"
            project.mkdir(parents=True)
            store.open_task(project=project, title=f"T{index}", objective="Bound", lead="lead")
    report, refusal = task_calls.cleanup_project(db, str(root))
    assert len(report["tasks"]) == task_calls.CLEANUP_TASK_LIMIT and report["omitted_tasks"] == 5
    assert refusal is not None and refusal.code == "cleanup.tasks_open"
    assert len(refusal.fix) == task_calls.CLEANUP_FIX_LIMIT
    assert "omitted" in str(refusal) and str(task_calls.CLEANUP_TASK_LIMIT + 5) in str(refusal)
