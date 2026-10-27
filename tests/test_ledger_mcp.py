"""Task ledger MCP acceptance tests, also runnable by scripts/run_tests.py."""

import io
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from hipson import mcp_server
from hipson.ledger import Ledger
from tests.support import hipson_cli
from tests.test_hipson_helpers import assert_schema_valid, run_cli_inprocess
from tests.test_ledger import SCHEMAS, _strict_errors, assert_strict_schema

TASK_TOOLS = {
    "task.verify": ({"task", "command", "approved"}, {"label", "timeout"}),
    'lead.resume': (set(), {'project', 'all'}),
    'task.now': (set(), {'project', 'all'}),
    "task.activity": ({"assignment"}, set()),
    "task.ack": ({"assignment", "approved"}, {"note"}),
    "task.show": ({"task"}, set()),
    "task.brief": ({"task", "role"}, {"scope"}),
    "task.context": (set(), {"task"}),
    "task.events": ({"task"}, {"after"}),
    "task.list": (set(), {"project", "status"}),
    "task.board": (set(), set()),
    "task.findings": (set(), {"task", "status", "severity"}),
    "task.finding_add": ({"task", "actor", "severity", "title", "approved"},
                         {"detail", "location", "source_assignment"}),
    "task.finding_update": ({"finding", "status", "actor", "approved"},
                            {"resolution_note", "resolved_by_assignment"}),
    "task.open": ({"project", "title", "objective", "approved"}, {"acceptance", "lead"}),
    "task.assign": ({"task", "actor", "role", "mode", "approved"},
                    {"scope", "owner_pid", "owner_boot_id", "owner_host", "owner_pid_namespace", "lease_seconds"}),
    "task.finish": ({"assignment", "result", "summary", "approved"}, {"ref"}),
    "task.reap": ({"approved"}, {"task", "dry_run"}),
    "task.event": ({"task", "actor", "type", "summary", "approved"}, {"ref"}),
    "task.status": ({"task", "to", "actor", "approved"}, {"summary", "force", "reason"}),
}
WRITE_ARGUMENTS = {
    "task.verify": {"task": "T-0001", "command": [sys.executable, "-c", "pass"]},
    "task.ack": {"assignment": "A-0001"},
    "task.open": {"project": ".", "title": "Ledger", "objective": "Track ownership"},
    "task.assign": {"task": "T-0001", "actor": "codex", "role": "builder", "mode": "rw", "scope": ["src"]},
    "task.finish": {"assignment": "A-0001", "result": "passed", "summary": "Checks pass"},
    "task.reap": {},
    "task.event": {"task": "T-0001", "actor": "codex", "type": "progress", "summary": "Ready"},
    "task.status": {"task": "T-0001", "to": "review", "actor": "claude"},
    "task.finding_add": {"task": "T-0001", "actor": "claude", "severity": "major", "title": "Missing guard"},
    "task.finding_update": {"finding": "F-0001", "status": "fixed", "actor": "claude"},
}


def _request(request_id, name, arguments):
    return {"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}}


def _responses(project, requests, *, ledger_db=None):
    stdout = io.StringIO()
    stdin = io.StringIO("".join(json.dumps(request) + "\n" for request in requests))
    env = {"HIPSON_LEDGER": str(ledger_db)} if ledger_db is not None else {}
    with patch.dict(os.environ, env):
        mcp_server.serve_stdio(project_path=project, stdin=stdin, stdout=stdout)
    responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [response["id"] for response in responses] == [request["id"] for request in requests]
    assert all("error" not in response for response in responses)
    return [response["result"] for response in responses]


def _call(server_project, name, *, ledger_db=None, **arguments):
    return _responses(server_project, [_request(1, name, arguments)], ledger_db=ledger_db)[0]


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def _assert_error(result, message):
    assert result["isError"] is True, result
    assert message in result["content"][0]["text"], result


def _assert_assignment(payload):
    root = json.loads((SCHEMAS / "task.schema.json").read_text(encoding="utf-8"))
    errors = _strict_errors(root["$defs"]["assignment"], payload, root)
    assert not errors, errors


def _open(project, db=None, **arguments):
    return _payload(_call(project, "task.open", ledger_db=db, **{
        **WRITE_ARGUMENTS["task.open"], "project": str(project), "approved": True, **arguments,
    }))


def test_ledger_mcp_catalog_schemas_and_contract(tmp_path: Path):
    tools = _responses(tmp_path, [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}])[0]["tools"]
    descriptors = {tool["name"]: tool for tool in tools}
    assert set(TASK_TOOLS).issubset(descriptors)
    assert len(tools) == len(descriptors)
    rc, stdout, stderr = run_cli_inprocess("mcp", "serve", "--project", str(tmp_path), "--catalog")
    assert rc == 0, stderr
    catalog = json.loads(stdout)
    assert_schema_valid("mcp-server-catalog.schema.json", catalog)
    catalog_tools = {tool["name"]: tool for tool in catalog["tools"]}
    for name, (required, optional) in TASK_TOOLS.items():
        schema = descriptors[name]["inputSchema"]
        assert schema["type"] == "object" and schema["additionalProperties"] is False
        assert set(schema.get("required", [])) == required
        assert set(schema["properties"]) == required | optional
        assert "db" not in schema["properties"]
        assert catalog_tools[name]["input_schema"] == schema
        assert catalog_tools[name]["approval_required"] == ("approved" in required)
        assert catalog_tools[name]["fallback_command"].startswith('hipson lead ' if name == 'lead.resume' else 'hipson task ')
    for name, field, values in (
        ("task.assign", "role", ["builder", "reviewer", "explorer", "researcher"]),
        ("task.assign", "mode", ["ro", "rw"]),
        ("task.status", "to", ["open", "in_progress", "blocked", "review", "done", "cancelled"]),
        ("task.list", "status", ["open", "in_progress", "blocked", "review", "done", "cancelled"]),
    ):
        assert descriptors[name]["inputSchema"]["properties"][field] == {"type": "string", "enum": values}
    result_choices = descriptors["task.finish"]["inputSchema"]["properties"]["result"]["anyOf"]
    assert result_choices[0] == {"type": "string", "enum": ["passed", "failed", "cancelled"]}
    assert result_choices[1] == json.loads((SCHEMAS / "task-result.schema.json").read_text())
    for name, field in (("task.open", "acceptance"), ("task.assign", "scope"),
                        ("task.finish", "ref"), ("task.event", "ref")):
        assert descriptors[name]["inputSchema"]["properties"][field] == {
            "type": "array", "items": {"type": "string"},
        }
    assert descriptors["task.events"]["inputSchema"]["properties"]["after"] == {"type": "integer"}
    contract = _payload(_call(tmp_path, "contract.show"))
    assert_schema_valid("agent-contract.schema.json", contract)
    capabilities = contract["adapter_capabilities"]["mcp_stdio"]["capabilities"]
    for name in TASK_TOOLS:
        capability = name.replace(".", "_")
        assert ("gated_" + capability if name in WRITE_ARGUMENTS else capability) in capabilities
    assert list(tmp_path.iterdir()) == []


def test_ledger_mcp_full_lifecycle_and_strict_payloads(tmp_path: Path):
    db = tmp_path / "ledger.sqlite"

    def command(name, **arguments):
        return _payload(_call(tmp_path, name, ledger_db=db, **arguments))

    opened = _open(tmp_path, db, acceptance=["Claims do not overlap", "Tests pass"])
    assert_strict_schema("task.schema.json", opened)
    task = opened["id"]
    assert opened["lead"] == "claude" and opened["acceptance"] == ["Claims do not overlap", "Tests pass"]
    assigned = command("task.assign", **{**WRITE_ARGUMENTS["task.assign"], "task": task, "approved": True})
    _assert_assignment(assigned)
    view = command("task.show", task=task)
    assert_strict_schema("task.schema.json", view)
    assert view["status"] == "in_progress" and view["assignments"] == [assigned]
    assert view["claims"] == [{"assignment": assigned["id"], "path": "src"}]
    event = command("task.event", task=task, actor="codex", type="progress", summary="Ready",
                    ref=[assigned["id"], "report.md"], approved=True)
    assert_strict_schema("task-event.schema.json", event)
    assert event["refs"] == [assigned["id"], "report.md"]
    listed = command("task.list", project=str(tmp_path), status="in_progress")
    assert [item["id"] for item in listed] == [task]
    assert command("task.list", project=str(tmp_path / "other")) == []
    board = command("task.board")
    assert board[0]["assignments"] == [assigned]
    for item in listed + board:
        assert_strict_schema("task.schema.json", item)
    finished = command("task.finish", assignment=assigned["id"], result="passed", summary="Checks pass",
                       ref=["report.md"], approved=True)
    _assert_assignment(finished)
    assert finished["status"] == "passed" and finished["ended_at"]
    command("task.ack", assignment=assigned["id"], approved=True)
    command("task.verify", task=task, command=[sys.executable, "-c", "pass"], approved=True)
    for state in ("review", "done"):
        view = command("task.status", task=task, to=state, actor="claude", approved=True)
        assert_strict_schema("task.schema.json", view)
        assert view["status"] == state and view["claims"] == []
    assert command("task.board") == []
    events = command("task.events", task=task)
    assert [item["type"] for item in events] == ["opened", "assigned", "progress", "finished", "acknowledged", "lead_verified", "status", "status"]
    assert view["revision"] == len(events)
    for item in events:
        assert_strict_schema("task-event.schema.json", item)
    assert command("task.events", task=task, after=event["seq"]) == events[3:]
    assert command("task.events", task=task, after=events[-1]["seq"]) == []
    assert command("task.show", task=task) == view
    with closing(Ledger(db)) as store:
        assert view == store.show(task) and events == store.events(task)


def test_ledger_mcp_write_approval_refuses_without_any_writes(tmp_path: Path):
    db = tmp_path / "ledger.sqlite"

    def refused():
        requests = []
        for name, arguments in WRITE_ARGUMENTS.items():
            for approval in ({}, {"approved": False}, {"approved": "true"}, {"approved": 1}):
                requests.append(_request(len(requests) + 1, name, {**arguments, **approval}))
        results = _responses(tmp_path, requests, ledger_db=db)
        for request, result in zip(requests, results, strict=True):
            _assert_error(result, request["params"]["name"] + " requires approved=true")

    refused()
    assert not db.exists() and list(tmp_path.iterdir()) == []
    task = _open(tmp_path, db)["id"]
    _payload(_call(tmp_path, "task.assign", ledger_db=db, **{**WRITE_ARGUMENTS["task.assign"], "approved": True}))
    with closing(Ledger(db)) as store:
        before = store.show(task), store.events(task)
    refused()
    with closing(Ledger(db)) as store:
        assert (store.show(task), store.events(task)) == before


def test_ledger_mcp_errors_preserve_conflict_event_and_continue(tmp_path: Path):
    db = str(tmp_path / "ledger.sqlite")
    first = _open(tmp_path, db)["id"]
    holder = _payload(_call(tmp_path, "task.assign", ledger_db=db, **{
        **WRITE_ARGUMENTS["task.assign"], "task": first, "approved": True,
    }))
    task = _open(tmp_path, db)["id"]
    before = _payload(_call(tmp_path, "task.show", ledger_db=db, task=task))
    cases = [
        ("task.assign", {**WRITE_ARGUMENTS["task.assign"], "task": task, "scope": ["src/hipson"]}, holder["id"]),
        ("task.status", {"task": task, "to": "done", "actor": "claude"}, "Invalid task transition"),
        ("task.show", {"task": "T-9999"}, "Task not found"),
        ("task.events", {"task": "T-9999"}, "Task not found"),
        ("task.assign", {**WRITE_ARGUMENTS["task.assign"], "task": task, "scope": ["../src"]}, "relative repository paths"),
        ("task.finish", {"assignment": "A-9999", "result": "passed", "summary": "Done"}, "Assignment not found"),
        ("task.events", {"task": task, "after": -1}, "between 0 and"),
        ("task.events", {"task": task, "after": 2**63}, "between 0 and"),
    ]
    requests = []
    for name, arguments, _ in cases:
        approval = {"approved": True} if name in WRITE_ARGUMENTS else {}
        requests.append(_request(len(requests) + 1, name, {**arguments, **approval}))
    requests.append(_request(len(requests) + 1, "task.events", {"task": task}))
    results = _responses(tmp_path, requests, ledger_db=db)
    for result, (_, _, message) in zip(results[:-1], cases, strict=True):
        _assert_error(result, message)
    events = _payload(results[-1])
    assert [event["type"] for event in events] == ["opened", "claim_conflict"]
    for event in events:
        assert_strict_schema("task-event.schema.json", event)
    assert holder["id"] in events[-1]["refs"]
    after = _payload(_call(tmp_path, "task.show", task=task, ledger_db=db))
    assert after["revision"] == before["revision"] + 1 and after["status"] == "open"
    assert after["claims"] == after["assignments"] == []
    assert _payload(_call(tmp_path, "task.show", task=first, ledger_db=db))["assignments"] == [holder]


def test_ledger_mcp_bad_arguments_fail_before_db_initialization(tmp_path: Path):
    db = tmp_path / "ledger.sqlite"
    for name, arguments in (
        ("task.show", {}),
        ("task.open", {"project": ".", "title": "Ledger"}),
        ("task.assign", {**WRITE_ARGUMENTS["task.assign"], "scope": "src"}),
        ("task.assign", {**WRITE_ARGUMENTS["task.assign"], "scope": [None]}),
        ("task.assign", {**WRITE_ARGUMENTS["task.assign"], "role": "invalid"}),
        ("task.assign", {**WRITE_ARGUMENTS["task.assign"], "mode": "invalid"}),
        ("task.finish", {**WRITE_ARGUMENTS["task.finish"], "result": "invalid"}),
        ("task.status", {**WRITE_ARGUMENTS["task.status"], "to": "invalid"}),
        ("task.list", {"status": "invalid"}),
        ("task.board", {"project": "."}),
        ("task.show", {"task": None}),
        ("task.events", {"task": "T-0001", "after": True}),
        ("task.events", {"task": "T-0001", "after": "1"}),
        ("task.event", {**WRITE_ARGUMENTS["task.event"], "ref": [1]}),
    ):
        approval = {"approved": True} if name in WRITE_ARGUMENTS else {}
        _assert_error(_call(tmp_path, name, ledger_db=db, **{**arguments, **approval}), name)
        assert not db.exists()


def test_ledger_mcp_rejects_caller_db_for_every_task_tool(tmp_path: Path):
    configured = tmp_path / "configured" / "ledger.sqlite"
    caller_db = tmp_path / "caller" / "ledger.sqlite"
    for name in TASK_TOOLS:
        arguments = dict(WRITE_ARGUMENTS.get(name, {}))
        if name in ("task.show", "task.events", "task.brief"):
            arguments["task"] = "T-0001"
        if name == "task.brief":
            arguments["role"] = "builder"
        if name == 'task.activity':
            arguments['assignment'] = 'A-0001'
        if name in WRITE_ARGUMENTS:
            arguments["approved"] = True
        result = _call(tmp_path, name, ledger_db=configured, db=str(caller_db), **arguments)
        _assert_error(result, f"{name}: unknown argument: db")
        assert list(tmp_path.iterdir()) == []


def test_ledger_mcp_semantic_errors_never_open_or_migrate_store(tmp_path: Path):
    missing = tmp_path / "missing" / "ledger.sqlite"
    legacy = tmp_path / "legacy.sqlite"
    with closing(sqlite3.connect(legacy)) as connection:
        connection.executescript((Path(__file__).parent / "fixtures" / "task-ledger-v1.sql").read_text())
    before = legacy.read_bytes()
    cases = [
        ("task.open", {field: value}, f"{field} must not be empty")
        for field in ("title", "objective", "lead") for value in ("", " \t\n")
    ] + [
        (name, {field: value}, f"{field} must not be empty")
        for name, fields in (
            ("task.assign", ("actor", "owner_boot_id", "owner_host", "owner_pid_namespace")),
            ("task.finish", ("summary",)),
            ("task.event", ("actor", "type", "summary")),
            ("task.status", ("actor",)),
        ) for field in fields for value in ("", " \t\n")
    ] + [
        ("task.assign", {"scope": []}, "at least one scope path"),
    ] + [
        ("task.assign", {"mode": mode, "scope": [scope]}, "Scope")
        for mode in ("ro", "rw")
        for scope in ("", "/src", "../src", "src/../tests", "C:\\src", "src\x00", "src ", " src", "src/ bad")
    ]
    for db in (missing, legacy):
        for name, changes, message in cases:
            arguments = {**WRITE_ARGUMENTS[name], "approved": True, **changes}
            # A constructor spy proves the check precedes all store access, even on v1 stores.
            with patch.object(mcp_server.ledger, "Ledger", wraps=Ledger) as constructor:
                _assert_error(_call(tmp_path, name, ledger_db=db, **arguments), message)
                constructor.assert_not_called()
            assert not missing.parent.exists()
            assert legacy.read_bytes() == before
            assert set(tmp_path.iterdir()) == {legacy}


def test_ledger_mcp_missing_store_reads_create_nothing(tmp_path: Path):
    home = tmp_path / "home"
    for db in (tmp_path / "missing" / "ledger.sqlite", tmp_path / "ledger.sqlite", None):
        with patch.dict(os.environ, {"HIPSON_HOME": str(home)}):
            os.environ.pop("HIPSON_LEDGER", None)
            results = _responses(tmp_path, [
                _request(1, "task.list", {}),
                _request(2, "task.board", {}),
                _request(3, "task.show", {"task": "T-0001"}),
                _request(4, "task.events", {"task": "T-0001"}),
            ], ledger_db=db)
        assert _payload(results[0]) == _payload(results[1]) == []
        for result in results[2:]:
            _assert_error(result, "Task not found: T-0001")
        assert list(tmp_path.iterdir()) == []


def test_ledger_mcp_read_does_not_recreate_disappearing_store(tmp_path: Path):
    # '#' and '%' exercise URI escaping on every OS; '?' is not a legal Windows filename.
    db = tmp_path / ("ledger%#.sqlite" if sys.platform == "win32" else "ledger?%#.sqlite")
    opened = _open(tmp_path, db)
    assert _payload(_call(tmp_path, "task.show", ledger_db=db, task=opened["id"])) == opened

    def remove_before_connect(path, *, create, read_only):
        path.unlink()
        return Ledger(path, create=create, read_only=read_only)

    with patch.object(mcp_server.ledger, "Ledger", side_effect=remove_before_connect):
        _assert_error(_call(tmp_path, "task.board", ledger_db=db), "unable to open database")
    # The store is not recreated; SQLite's own -wal/-shm sidecars from the earlier read may remain.
    assert not db.exists()
    assert all(path.name.endswith(("-wal", "-shm")) for path in tmp_path.iterdir())


def test_ledger_mcp_central_db_precedence_and_cross_project_reads(tmp_path: Path):
    home = tmp_path / "home"
    env_db = tmp_path / "env.sqlite"
    server_project = tmp_path / "server"
    server_project.mkdir()
    other_project = tmp_path / "other"
    other_project.mkdir()
    with patch.dict(os.environ, {"HIPSON_HOME": str(home), "HIPSON_LEDGER": str(env_db)}):
        # The server environment selects the store independently of server/project paths.
        env_task = _payload(_call(server_project, "task.open", **{
            **WRITE_ARGUMENTS["task.open"], "project": str(other_project), "approved": True,
        }))
        assert not home.exists()
        assert _payload(_call(server_project, "task.show", task=env_task["id"])) == env_task
        assert _payload(_call(server_project, "task.list")) == [
            {key: value for key, value in env_task.items() if key not in ("claims", "assignments", "findings", "verification")}
        ]
        assert _payload(_call(server_project, "task.board")) == [env_task]
        assert _payload(_call(server_project, "task.list", project=str(server_project))) == []
        os.environ.pop("HIPSON_LEDGER")
        assert _payload(_call(server_project, "task.board")) == []
        assert not home.exists()
        home_task = _open(other_project)
        assert _payload(_call(server_project, "task.show", task=home_task["id"])) == home_task
        assert (home / "ledger.sqlite").exists()
    assert list(server_project.iterdir()) == []


def test_ledger_mcp_storage_errors_are_tool_errors(tmp_path: Path):
    corrupt = tmp_path / "corrupt.sqlite"
    corrupt.write_text("This is not a SQLite database", encoding="utf-8")
    requests = [_request(1, "task.board", {}), {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    for db, message in ((corrupt, "not a database"), (tmp_path, "unable to open database")):
        results = _responses(tmp_path, requests, ledger_db=db)
        _assert_error(results[0], message)
        assert {tool["name"] for tool in results[1]["tools"]} == set(mcp_server.MCP_TOOL_NAMES)
    assert _payload(_call(tmp_path, "task.board", ledger_db=tmp_path / "healthy.sqlite")) == []


def test_ledger_mcp_redacts_ledger_validation_and_storage_errors(tmp_path: Path):
    secret = "sk-" + "syntheticcredential" * 2
    db = tmp_path / "ledger.sqlite"
    for existing in (False, True):
        if existing:
            _open(tmp_path, db)
        for name in ("task.show", "task.events"):
            result = _call(tmp_path, name, ledger_db=db, task=secret)
            _assert_error(result, "Task not found: [REDACTED]")
            assert secret not in json.dumps(result)
    result = _call(tmp_path, "task.board", ledger_db=db, **{secret: "unknown argument"})
    _assert_error(result, "unknown argument: [REDACTED]")
    assert secret not in json.dumps(result)
    for error in (OSError, sqlite3.OperationalError):
        with patch.object(mcp_server.ledger, "Ledger", side_effect=error(f"Storage failure: {secret}")):
            result = _call(tmp_path, "task.board", ledger_db=db)
        _assert_error(result, "Storage failure: [REDACTED]")
        assert secret not in json.dumps(result)


def test_ledger_mcp_real_stdio_read_and_write(tmp_path: Path):
    cli = hipson_cli()
    assert cli, "requires installed hipson CLI (HIPSON_BIN, PATH, or checkout .venv)"
    project = tmp_path / "server"
    project.mkdir()
    env = {**os.environ, "HIPSON_LEDGER": str(tmp_path / "central.sqlite"),
           "HIPSON_HOME": str(tmp_path / "home")}
    if sys.platform == "win32":
        # Windows expands unknown ~users to sibling directories without a user lookup.
        # Force unavailable home expansion so this real stdio error/recovery control is portable.
        env.pop("USERPROFILE", None)
        env.pop("HOMEPATH", None)
    requests = [
        _request(1, "task.open", {**WRITE_ARGUMENTS["task.open"], "project": str(tmp_path), "approved": True}),
        _request(2, "task.show", {"task": "T-0001"}),
        _request(3, "task.events", {"task": "T-0001", "after": 0}),
        _request(4, "task.open", {
            **WRITE_ARGUMENTS["task.open"], "project": "~__no_such_user__/x", "approved": True,
        }),
        _request(5, "task.list", {"project": "~__no_such_user__/x"}),
        {"jsonrpc": "2.0", "id": 6, "method": "tools/list"},
    ]
    result = subprocess.run(
        [cli, "mcp", "serve", "--project", str(project), "--stdio"],
        cwd=tmp_path, env=env, input="".join(json.dumps(request) + "\n" for request in requests),
        capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert [response["id"] for response in responses] == [1, 2, 3, 4, 5, 6]
    opened, shown, events = [_payload(response["result"]) for response in responses[:3]]
    assert_strict_schema("task.schema.json", opened)
    assert shown == opened and shown["project"] == str(tmp_path)
    assert events[0]["type"] == "opened" and len(events) == 1
    assert_strict_schema("task-event.schema.json", events[0])
    for response in responses[3:5]:
        _assert_error(response["result"], "Could not determine home directory")
    assert {tool["name"] for tool in responses[5]["result"]["tools"]} == set(mcp_server.MCP_TOOL_NAMES)
    assert list(project.iterdir()) == [] and not (tmp_path / "home").exists()
