import contextlib
import io
import json
from pathlib import Path

from hipson import cli
from hipson.approvals import ApprovalPolicy
from hipson.scheduler import Scheduler
from hipson.session import open_session_store
from hipson.tools import ToolRegistry, ToolResult, ToolSpec


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


def test_scheduler_approved_flag_cannot_run_dangerous_tools(tmp_path: Path):
    called = False

    def handler(_input_data: dict[str, object], _context) -> ToolResult:
        nonlocal called
        called = True
        return ToolResult(ok=True, output={"ok": True}, summary="dangerous ran")

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="dangerous.demo",
            description="Dangerous job.",
            input_schema={"required": {}, "optional": {}},
            output_contract={"ok": "bool"},
            risk_level="dangerous",
            approval_required=True,
            handler=handler,
        )
    )
    store = open_session_store(tmp_path / "runtime.sqlite")
    try:
        scheduler = Scheduler(store=store, registry=registry, approval_policy=ApprovalPolicy())
        scheduler.create_tool_job(tool_name="dangerous.demo", input_data={}, approved=True)

        results = scheduler.tick(cwd=tmp_path, now="2026-01-01T00:00:01Z")
        jobs = store.list_jobs()
    finally:
        store.close()

    assert called is False
    assert results[0].status == "failed"
    assert "Scheduler does not run dangerous jobs" in results[0].error
    assert jobs[0]["status"] == "failed"


def test_scheduler_nested_tool_input_cannot_bypass_path_policy(tmp_path: Path):
    store = open_session_store(tmp_path / "runtime.sqlite")
    try:
        scheduler = Scheduler.with_defaults(store)
        scheduler.create_tool_job(
            tool_name="memory.search",
            input_data={"query": "x", "memory_dir": str(Path.home())},
        )

        results = scheduler.tick(cwd=tmp_path, now="2026-01-01T00:00:01Z")
        jobs = store.list_jobs()
    finally:
        store.close()

    assert results[0].status == "failed"
    assert "Broad home/profile paths" in results[0].error
    assert jobs[0]["status"] == "failed"


def test_scheduler_last_result_is_bounded_and_redacted(tmp_path: Path):
    secret = "sk-test-secret1234567890"
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="demo.large",
            description="Large scheduler output.",
            input_schema={"required": {}, "optional": {}},
            output_contract={"markdown": "str"},
            risk_level="read",
            approval_required=False,
            handler=lambda _input_data, _context: ToolResult(
                ok=True,
                output={"markdown": f"{secret}\n" + ("x" * 8_000)},
                summary="large scheduler output",
            ),
        )
    )
    store = open_session_store(tmp_path / "runtime.sqlite")
    try:
        scheduler = Scheduler(store=store, registry=registry, approval_policy=ApprovalPolicy())
        scheduler.create_tool_job(tool_name="demo.large", input_data={})

        results = scheduler.tick(cwd=tmp_path, now="2026-01-01T00:00:01Z")
        jobs = store.list_jobs()
    finally:
        store.close()

    persisted = str(jobs[0]["payload"]["last_result"])
    assert results[0].status == "completed"
    assert secret not in persisted
    assert len(persisted) < 1_400
    assert "truncated" in persisted


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
