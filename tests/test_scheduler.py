import contextlib
import io
import json
from pathlib import Path

from hipson import cli
from hipson.scheduler import Scheduler
from hipson.session import open_session_store


def test_scheduler_create_and_list_due_job(tmp_path: Path):
    store = open_session_store(tmp_path / "runtime.sqlite")
    try:
        scheduler = Scheduler.with_defaults(store)
        job_id = scheduler.create_tool_job(
            tool_name="repo.changed_files",
            input_data={"path": "."},
            run_after="2026-01-01T00:00:00Z",
        )
        due = scheduler.list_due_jobs(now="2026-01-01T00:00:01Z")
    finally:
        store.close()

    assert len(due) == 1
    assert due[0]["id"] == job_id
    assert due[0]["payload"]["tool"] == "repo.changed_files"


def test_scheduler_tick_runs_due_read_tool_and_persists_success(tmp_path: Path):
    store = open_session_store(tmp_path / "runtime.sqlite")
    try:
        scheduler = Scheduler.with_defaults(store)
        job_id = scheduler.create_tool_job(tool_name="repo.changed_files", input_data={"path": "."})

        results = scheduler.tick(cwd=tmp_path, now="2026-01-01T00:00:01Z")
        jobs = store.list_jobs()
    finally:
        store.close()

    assert len(results) == 1
    assert results[0].job_id == job_id
    assert results[0].status == "completed"
    assert jobs[0]["status"] == "completed"
    assert jobs[0]["payload"]["last_result"] == {"changed_files": [], "untracked_files": []}


def test_scheduler_tick_persists_unknown_tool_failure(tmp_path: Path):
    store = open_session_store(tmp_path / "runtime.sqlite")
    try:
        scheduler = Scheduler.with_defaults(store)
        scheduler.create_tool_job(tool_name="missing.tool", input_data={})

        results = scheduler.tick(cwd=tmp_path, now="2026-01-01T00:00:01Z")
        jobs = store.list_jobs()
    finally:
        store.close()

    assert results[0].status == "failed"
    assert "Unknown tool" in results[0].error
    assert jobs[0]["status"] == "failed"
    assert "Unknown tool" in str(jobs[0]["payload"]["last_error"])


def test_scheduler_requires_approval_for_non_read_jobs(tmp_path: Path):
    store = open_session_store(tmp_path / "runtime.sqlite")
    try:
        scheduler = Scheduler.with_defaults(store)
        scheduler.create_tool_job(
            tool_name="packet.review.create",
            input_data={
                "project": ".",
                "title": "Review",
                "output": "runs/review.md",
            },
        )

        results = scheduler.tick(cwd=tmp_path, now="2026-01-01T00:00:01Z")
        jobs = store.list_jobs()
    finally:
        store.close()

    assert results[0].status == "failed"
    assert "Non-read scheduler jobs require explicit approval" in results[0].error
    assert jobs[0]["status"] == "failed"
    assert not (tmp_path / "runs" / "review.md").exists()


def test_scheduler_does_not_run_future_jobs(tmp_path: Path):
    store = open_session_store(tmp_path / "runtime.sqlite")
    try:
        scheduler = Scheduler.with_defaults(store)
        scheduler.create_tool_job(
            tool_name="repo.changed_files",
            input_data={"path": "."},
            run_after="2026-01-02T00:00:00Z",
        )

        results = scheduler.tick(cwd=tmp_path, now="2026-01-01T00:00:01Z")
        jobs = store.list_jobs()
    finally:
        store.close()

    assert results == []
    assert jobs[0]["status"] == "pending"


def test_scheduler_cli_create_list_and_tick(tmp_path: Path):
    session_db = tmp_path / "runtime.sqlite"
    stdout = io.StringIO()

    with contextlib.chdir(tmp_path), contextlib.redirect_stdout(stdout):
        create_code = cli.main(
            [
                "scheduler",
                "--session-db",
                str(session_db),
                "create",
                "--tool",
                "repo.changed_files",
                "--input",
                '{"path":"."}',
                "--run-after",
                "2026-01-01T00:00:00Z",
            ]
        )
    job_id = stdout.getvalue().strip()
    stdout = io.StringIO()
    with contextlib.chdir(tmp_path), contextlib.redirect_stdout(stdout):
        list_code = cli.main(["scheduler", "--session-db", str(session_db), "list", "--json"])
    listed = json.loads(stdout.getvalue())
    stdout = io.StringIO()
    with contextlib.chdir(tmp_path), contextlib.redirect_stdout(stdout):
        tick_code = cli.main(
            [
                "scheduler",
                "--session-db",
                str(session_db),
                "tick",
                "--now",
                "2026-01-01T00:00:01Z",
                "--json",
            ]
        )
    ticked = json.loads(stdout.getvalue())

    assert create_code == 0
    assert list_code == 0
    assert tick_code == 0
    assert listed["jobs"][0]["id"] == job_id
    assert ticked["results"][0]["status"] == "completed"
