"""Compact CLI and MCP reads over the same ledger."""

import json
import os
import shlex
from contextlib import chdir, closing, redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from hipson import __version__, cli, lead_context, ledger, mcp_server, task_projection
from hipson.redaction import redact_value
from scripts.agent_ux_measure import READS, _cli, _mcp, _tool_text
from tests.fixtures.agent_ux_ledger import build
from tests.test_ledger_recovery import _v1_file

SECTION_EVIDENCE_FIELDS = {
    "verification": {"actor", "context", "metadata_fallback_paths", "label", "command", "subject", "output_tail",
                     "error", "files", "generated_files", "changes"},
    "assignments": {"actor", "result", "activity", "failure_journal", "changes"},
    "findings": {"title", "detail", "location", "resolution_note"},
    "events": {"actor", "type", "summary", "refs"},
}
# Columns a person or agent cannot write freely: identifiers, enums, references and timestamps.
FINDING_SYSTEM_COLUMNS = {"id", "task", "source_assignment", "severity", "status", "resolved_by_assignment",
                          "created_at", "updated_at", "resolved_at"}


def _json_cli(*args):
    return json.loads(_cli([*args, "--json"]))


def _call_mcp(name, arguments):
    return json.loads(_tool_text(name, arguments))


def _replay_cli(call):
    program, *args = shlex.split(call)
    assert program == "hipson"
    return json.loads(_cli(args))


def test_compact_reads_and_continuations_on_fixture(tmp_path):
    fixture = build(tmp_path / "ledger path")
    with closing(ledger.Ledger(fixture.db, read_only=True)) as store:
        tasks = {task["id"] for task in store.list_tasks()}
    with patch.dict(os.environ, HIPSON_LEDGER=str(fixture.db)), \
            patch.object(ledger.Ledger, "_view", side_effect=AssertionError("fingerprinting view")), \
            patch.object(ledger.Ledger, "lead_snapshot", side_effect=AssertionError("full resume")):
        for name in READS:
            arguments = {"task": fixture.heavy_task} if name == "task.show" else {}
            command = [*name.split("."), "--detail", "compact"]
            if arguments:
                command.extend(["--task", fixture.heavy_task])
            text = _cli([*command, "--json"])
            payload = json.loads(text)
            assert text == _tool_text(name, {**arguments, "detail": "compact"}) == task_projection.dumps(payload)
            assert payload["view_schema"] == 1 and payload["hipson_version"] == __version__
            prefix = {"task.show": "", "lead.resume": "now.tasks[]."}.get(name, "tasks[].")
            assert payload["evidence_policy"] == lead_context.EVIDENCE_POLICY
            assert payload["untrusted_fields"] == [prefix + "assignments[].actor", prefix + "assignments[].summary"]
            assert fixture.heavy_task in _cli(command)

        resume = _call_mcp("lead.resume", {"detail": "compact"})
        assert resume["now"] == _call_mcp("task.now", {"detail": "compact"})
        assert sum(sum(project["counts"].values()) for project in resume["projects"]) == len(tasks)
        assert len(resume["projects"]) == 3
        for field in ("unacknowledged_results", "blocking_findings", "major_findings"):
            item, = resume[field]
            assert item["task"] in tasks and resume["omitted"][field] == 0

        # Exercise both the byte cap and the caller's limit. Replay each continuation verbatim.
        with patch.dict(task_projection.LIST_BUDGETS, {"task.board": 1_600, "task.now": 1_600}):
            for name in ("task.board", "task.now"):
                arguments = {"detail": "compact", "limit": 4, "all": True}
                page = _call_mcp(name, arguments)
                assert page == _json_cli(*name.split("."), "--detail", "compact", "--limit", "4", "--all")
                ids = []
                for _ in tasks:
                    assert 0 < len(page["tasks"]) < 4
                    assert len(task_projection.dumps(page).encode("utf-8")) <= 1_600
                    ids.extend(task["id"] for task in page["tasks"])
                    if page["more"] is None:
                        break
                    more = page["more"]
                    assert more["arguments"] == {**arguments, "offset": len(ids)}
                    assert page["omitted"] == len(tasks) - len(ids)
                    page = _call_mcp(more["tool"], more["arguments"])
                    assert page == _replay_cli(more["cli"])
                assert page["more"] is None
                assert len(ids) == len(tasks) and set(ids) == tasks

            project = str(fixture.db.parent / "inventory")
            now = _call_mcp("lead.resume", {"detail": "compact", "project": project, "all": True})["now"]
            assert now["more"]["arguments"]["project"] == project
            assert _replay_cli(now["more"]["cli"]) == _call_mcp(now["more"]["tool"], now["more"]["arguments"])

        with patch.dict(os.environ, HIPSON_LEDGER=str(tmp_path / "wrong.sqlite")):
            page = _json_cli("task", "board", "--detail", "compact", "--limit", "1", "--db", str(fixture.db))
            second = _replay_cli(page["more"]["cli"])
            assert second["omitted"] == len(tasks) - 2 and second["tasks"][0]["id"] != page["tasks"][0]["id"]

        with patch.dict(task_projection.LIST_BUDGETS, {"task.board": 1_000}):
            first, = _call_mcp("task.board", {"detail": "compact"})["tasks"]
            assert first["too_large"]
            show_args = {"task": first["id"], "detail": "compact"}
            expected = _call_mcp("task.show", show_args)
            assert shlex.split(first["hint"]) == [
                "hipson", "task", "show", "--task", first["id"], "--detail", "compact", "--json",
            ]
            assert _replay_cli(first["hint"]) == expected

        # The page repeats the --db path in more.cli and in the stub hint; long temp paths (macOS) need room.
        db_room = 2 * len(shlex.quote(str(fixture.db)).encode("utf-8"))
        with patch.dict(task_projection.LIST_BUDGETS, {"task.board": 1_200 + db_room}), \
                patch.dict(os.environ, HIPSON_LEDGER=str(tmp_path / "wrong.sqlite")):
            page = _json_cli("task", "board", "--detail", "compact", "--db", str(fixture.db))
            first, = page["tasks"]
            assert first["too_large"]
            assert shlex.split(first["hint"])[-3:] == ["--db", str(fixture.db), "--json"]
            assert _replay_cli(first["hint"])["id"] == first["id"]


def test_compact_resume_text_includes_context_summaries(tmp_path):
    fixture = build(tmp_path / "fixture")
    args = ["lead", "resume", "--detail", "compact"]
    env = {"HIPSON_LEDGER": str(fixture.db)}
    with patch.dict(os.environ, env):
        complete = _cli([*args, "--json"])
        assert "Truncated: false" in _cli(args).splitlines()
    with patch.dict(os.environ, env), patch.object(task_projection, "RESUME_BUDGET", len(complete.encode("utf-8")) - 20):
        payload = _json_cli(*args)
        lines = _cli(args).splitlines()
        for field, label, identifier in (
            ("unacknowledged_results", "Unacknowledged results", "assignment"),
            ("blocking_findings", "Blocking findings", "finding"),
            ("major_findings", "Major findings", "finding"),
        ):
            item, = payload[field]
            assert f"{label}: {item['task']}/{item[identifier]}" in lines
        assert payload["projects"] and "Projects:" in lines
        for project in payload["projects"]:
            counts = ", ".join(f"{status}={count}" for status, count in project["counts"].items())
            assert f"  {project['project']}: {counts}" in lines
        assert payload["truncated"] and payload["omitted"]["projects"] > 0
        assert "Omitted: " + ", ".join(f"{field}={count}" for field, count in payload["omitted"].items()) in lines
        assert "Truncated: true" in lines


def test_compact_cli_continuations_resolve_relative_project(tmp_path):
    fixture = build(tmp_path / "fixture")
    with patch.dict(os.environ, HIPSON_LEDGER=str(fixture.db)), \
            patch.dict(task_projection.LIST_BUDGETS, {"task.now": 2_000}):
        for name in ("task.board", "task.now", "lead.resume"):
            command = [*name.split("."), "--detail", "compact", "--project", "inventory"]
            if name != "lead.resume":
                command.extend(["--limit", "1"])
            with chdir(fixture.db.parent):
                payload = _json_cli(*command)
                args = {"detail": "compact", "project": "inventory"}
                if name != "lead.resume":
                    args["limit"] = 1
                assert payload == _call_mcp(name, args)
            more = payload.get("now", payload)["more"]
            assert more["arguments"]["project"] == str(fixture.db.parent / "inventory")
            with chdir(tmp_path):
                page = _replay_cli(more["cli"])
            assert page == _call_mcp(more["tool"], more["arguments"]) and page["tasks"]


def test_full_detail_uses_s1_read_handlers_on_fixture(tmp_path):
    fixture = build(tmp_path / "fixture")

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2030, 1, 1, tzinfo=UTC).astimezone(tz)

    with patch.dict(os.environ, HIPSON_LEDGER=str(fixture.db)), patch.object(lead_context, "datetime", FixedClock), \
            patch.object(task_projection, "project_list", side_effect=AssertionError("compact list")), \
            patch.object(task_projection, "show_task", side_effect=AssertionError("compact show")), \
            patch.object(lead_context, "build_compact", side_effect=AssertionError("compact resume")):
        parser = cli.build_parser()
        for name in READS:
            args = {"task": fixture.heavy_task} if name == "task.show" else {}
            command = name.split(".")
            if args:
                command.extend(["--task", fixture.heavy_task])
            s1_handler = cli.command_task if name in ("task.board", "task.show") else cli.command_lead_view
            for flags in ([], ["--json"]):
                with redirect_stdout(StringIO()) as stdout:
                    assert s1_handler(parser.parse_args([*command, *flags])) == 0
                expected = stdout.getvalue()
                assert _cli([*command, *flags]) == _cli([*command, "--detail", "full", *flags]) == expected
            # S1 sends the same full payload, serialized with default separators in MCP.
            expected_mcp = json.dumps(json.loads(expected), ensure_ascii=False)
            assert _tool_text(name, args) == _tool_text(name, {**args, "detail": "full"}) == expected_mcp


def test_show_sections_return_paged_records(tmp_path):
    db = tmp_path / "ledger.sqlite"
    project = tmp_path / "workspace"
    project.mkdir()
    with closing(ledger.Ledger(db)) as store:
        task = store.open_task(project=project, title="Paged records", objective="Exercise section reads")["id"]
        for index in range(7):
            item = store.assign(task, actor=f"worker-{index}", role="explorer", mode="ro")
            store.finish(item["id"], result="passed", summary="Review the output")
            finding = store.add_finding(task, actor="reviewer", severity="minor",
                                        title=f"Finding {index}", detail="Existing detail")
            if index == 0:
                store.update_finding(finding["id"], actor="claude", status="fixed")
            store._record_verification({"id": f"V-{index}", "task": task, "exit_code": 0, "revision": "none",
                                        "fingerprint": None, "state_changed": False,
                                        "files": [{"path": f"source-{item}.py", "state": "fixture"}
                                                  for item in range(20 if index == 0 else 1)],
                                        "output_tail": "token=section-secret"})
        assignments = [store._assignment_dict(row) for row in store.connection.execute(
            "SELECT * FROM assignments WHERE task = ? ORDER BY started_at, id", (task,))]
        expected = redact_value({"assignments": assignments, "findings": store.findings(task=task),
                                 "verification": store.verifications(task), "events": store.events(task)})
        for record in expected["verification"]:
            record.update(files_total=len(record["files"]), files_truncated=False)
        with patch.dict(os.environ, HIPSON_LEDGER=str(db)):
            # These reads also work while the writer still holds a nonempty WAL.
            assert _call_mcp("task.board", {"detail": "compact"})["tasks"][0]["id"] == task
            page = _json_cli("task", "show", "--task", task, "--section", "verification", "--limit", "1")
            assert page["records"] == expected["verification"][:1]

    with patch.dict(os.environ, HIPSON_LEDGER=str(db)), \
            patch.object(ledger.Ledger, "_view", side_effect=AssertionError("full task view")):
        show = _json_cli("task", "show", "--task", task, "--detail", "compact")
        for call in show["more"]:
            section = call["arguments"]["section"]
            first = _replay_cli(call["cli"])
            assert first == _call_mcp(call["tool"], call["arguments"])
            assert first["records"] == expected[section][:5] and first["next_cursor"] == 5
            records = []
            cursor = 0
            for _ in expected[section]:
                page = _json_cli("task", "show", "--task", task, "--section", section, "--limit", "3",
                                 "--cursor", str(cursor))
                assert page == _call_mcp("task.show", {"task": task, "section": section, "limit": 3, "cursor": cursor})
                keys = {"task", "section", "records", "next_cursor", "evidence_policy", "untrusted_fields"}
                if section == "verification":
                    keys.add("files_note")
                assert page.keys() == keys
                assert page["evidence_policy"] == lead_context.EVIDENCE_POLICY
                assert set(page["untrusted_fields"]) == {f"records[].{field}" for field in SECTION_EVIDENCE_FIELDS[section]}
                assert page["task"] == task and page["section"] == section and len(page["records"]) <= 3
                records.extend(page["records"])
                cursor = page["next_cursor"]
                if cursor is None:
                    break
                assert cursor == len(records)
            assert cursor is None and records == expected[section]
        assert _call_mcp("task.show", {"task": task, "section": "events", "cursor": 9_007_199_254_740_991}) == {
            "task": task, "section": "events", "records": [], "next_cursor": None,
            "evidence_policy": lead_context.EVIDENCE_POLICY,
            "untrusted_fields": ["records[].actor", "records[].type", "records[].summary", "records[].refs"],
        }
        assert expected["verification"][0]["output_tail"] == "token=[REDACTED]"


def _section_page(task, section, *, cursor=None, limit=None):
    arguments = {"task": task, "section": section}
    flags = ["task", "show", "--task", task, "--section", section]
    for key, value in (("cursor", cursor), ("limit", limit)):
        if value is not None:
            arguments[key] = value
            flags.extend([f"--{key}", str(value)])
    cli_text = _cli([*flags, "--json"])
    assert cli_text == _tool_text("task.show", arguments)
    assert len(cli_text.encode("utf-8")) <= task_projection.SECTION_BUDGET == 64_000
    return json.loads(cli_text)


def test_verification_section_pages_past_invalid_evidence(tmp_path):
    db = tmp_path / "ledger.sqlite"
    with closing(ledger.Ledger(db)) as store:
        task = store.open_task(project=tmp_path, title="Malformed data", objective="Read later records")["id"]
        store.connection.executemany("INSERT INTO verifications (id, task, evidence) VALUES (?, ?, ?)", [
            ("V-1", task, "{broken"),
            ("V-2", task, '[ ["summary", "Not an object"] ]'),
            ("V-3", task, "42"),
            ("V-4", task, '{"id":"V-4","files":[]}'),
        ])
    with patch.dict(os.environ, HIPSON_LEDGER=str(db)):
        page = _section_page(task, "verification", limit=2)
        assert page["records"] == [{"seq": 1, "invalid_evidence": True}, {"seq": 2, "invalid_evidence": True}]
        assert page["next_cursor"] == 2
        page = _section_page(task, "verification", cursor=page["next_cursor"])
        assert page["records"] == [{"seq": 3, "invalid_evidence": True},
                                   {"id": "V-4", "files": [], "files_total": 0, "files_truncated": False}]
        assert page["next_cursor"] is None


def test_old_schema_reports_unavailable_sections_without_migrating(tmp_path):
    db = tmp_path / "legacy.sqlite"
    _v1_file(db)
    before = db.read_bytes()
    with patch.dict(os.environ, HIPSON_LEDGER=str(db)):
        for section in ("verification", "findings"):
            page = _section_page("T-0001", section)
            assert page["unavailable"] == f"schema v1 does not store {section}"
            assert page["records"] == [] and page["next_cursor"] is None
        assert "unavailable" not in _section_page("T-0001", "events")
    assert db.read_bytes() == before


def _assert_section_text_bounded(task, section):
    flags = ["task", "show", "--task", task, "--section", section]
    text = _cli(flags)
    assert len(text.encode("utf-8")) <= 64_000
    assert text == _cli([*flags, "--json"])


def test_show_section_byte_budget_and_fixture_paging(tmp_path):
    # The tracked fixture marker contributes one more entry to the inventory.
    fixture = build(tmp_path / "fixture", inventory_files=1049)
    with closing(ledger.Ledger(fixture.db, read_only=True)) as store, \
            patch.dict(os.environ, HIPSON_LEDGER=str(fixture.db)):
        page = _section_page(fixture.heavy_task, "verification")
        record, = page["records"]
        assert len(record["files"]) == 20
        assert record["files_total"] == 1050 and record["files_truncated"] is True
        assert "task show --detail full" in page["files_note"]
        for section in SECTION_EVIDENCE_FIELDS:
            _assert_section_text_bounded(fixture.heavy_task, section)

        for task in store.list_tasks():
            task_id = task["id"]
            expected = redact_value({
                "verification": store.verifications(task_id),
                "assignments": [store._assignment_dict(row) for row in store.connection.execute(
                    "SELECT * FROM assignments WHERE task = ? ORDER BY started_at, id", (task_id,))],
                "findings": store.findings(task=task_id),
                "events": store.events(task_id),
            })
            for record in expected["verification"]:
                files = record["files"]
                record.update(files=files[:20], files_total=len(files), files_truncated=len(files) > 20)
            for section, expected_records in expected.items():
                records = []
                cursor = 0
                for _ in range(max(1, len(expected_records))):
                    page = _section_page(task_id, section, cursor=cursor, limit=2)
                    assert len(page["records"]) <= 2
                    if expected_records:
                        assert page["records"]
                    records.extend(page["records"])
                    cursor = page["next_cursor"]
                    if cursor is None:
                        break
                    assert page["records"] and cursor == len(records)
                assert cursor is None and records == expected_records


def test_show_section_paging_advances_past_oversized_record(tmp_path):
    db = tmp_path / "ledger.sqlite"
    with closing(ledger.Ledger(db)) as store:
        task = store.open_task(project=tmp_path, title="Large event", objective="Page recorded events")["id"]
        # Each medium record fits alone, while two together exceed the byte budget.
        for index in range(2):
            store.add_event(task, actor="worker", type="note", summary=f"{index}:" + "😀" * 8_100)
        oversized = store.add_event(task, actor="worker", type="note", summary='😀"\\' * 20_000)
        last = store.add_event(task, actor="worker", type="note", summary="Next event")
        opened, first, second, _, _ = store.events(task)

    with patch.dict(os.environ, HIPSON_LEDGER=str(db)):
        _assert_section_text_bounded(task, "events")
        page = _section_page(task, "events")
        assert page["records"] == [opened, first]
        assert page["next_cursor"] == 2
        page = _section_page(task, "events", cursor=page["next_cursor"])
        assert page["records"] == [second]
        assert page["next_cursor"] == 3
        page = _section_page(task, "events", cursor=page["next_cursor"])
        record, = page["records"]
        assert record["truncated_fields"] == ["summary"]
        assert record["summary"].endswith("…")
        assert oversized["summary"].startswith(record["summary"][:-1])
        assert len(record["summary"]) > 1
        assert {**oversized, "summary": record["summary"], "truncated_fields": ["summary"]} == record
        assert page["next_cursor"] == 4
        page = _section_page(task, "events", cursor=page["next_cursor"])
        assert page["records"] == [last]
        assert page["next_cursor"] is None


def test_section_fit_lists_only_fields_that_changed():
    record = {"summary": "a" * 500, "actor": "b" * 450, "refs": ["keep"]}
    original = record.copy()
    page = {"records": [record]}
    section_prefix = task_projection._section_prefix

    def keep_summary(value, budget):
        return value if value == original["summary"] else section_prefix(value, budget)

    with patch.object(task_projection, "SECTION_BUDGET", 900), \
            patch.object(task_projection, "_section_prefix", side_effect=keep_summary):
        task_projection._fit_section_record(page, record)
    assert record["truncated_fields"] == [key for key, value in original.items() if record[key] != value] == ["actor"]
    assert len(task_projection.dumps(page).encode("utf-8")) <= 900


def test_read_discovery_and_invalid_arguments(tmp_path):
    init = _mcp("initialize", {})
    assert init["serverInfo"]["version"] == __version__
    assert "view_schema 1" in init["instructions"] and "changes to compact in 1.9" in init["instructions"]
    tools = {tool["name"]: tool for tool in _mcp("tools/list", {})["tools"]}
    for name in READS:
        tool = tools[name]
        assert tool["inputSchema"]["properties"]["detail"]["enum"] == ["compact", "full"]
        assert "detail=compact" in tool["description"] and "default in 1.8" in tool["description"]

    illegal_calls = [
        ("task.board", {"detail": "tiny"}, ["--detail", "tiny"], ("compact", "full")),
        ("lead.resume", {"detail": 1}, ["--detail", "1"], ("compact", "full")),
        ("task.now", {"detail": "compact", "offset": -1}, ["--detail", "compact", "--offset", "-1"], ("offset",)),
        ("task.board", {"detail": "compact", "limit": 101}, ["--detail", "compact", "--limit", "101"], ("limit", "100")),
        ("task.now", {"limit": 1}, ["--limit", "1"], ("limit", "compact")),
        ("task.show", {"detail": "compact", "section": "events"},
         ["--section", "events", "--detail", "compact"], ("mutually exclusive",)),
        ("task.show", {"cursor": 1}, ["--cursor", "1"], ("cursor", "section")),
    ]
    with patch.dict(os.environ, HIPSON_LEDGER=str(tmp_path / "missing.sqlite")):
        for name in ("task.board", "task.now", "lead.resume"):
            payload = _call_mcp(name, {"detail": "compact"})
            assert payload == _json_cli(*name.split("."), "--detail", "compact")
            page = payload.get("now", payload)
            assert page["tasks"] == [] and page["more"] is None
        for name, arguments, flags, fragments in illegal_calls:
            command = name.split(".")
            if name == "task.show":
                arguments = {"task": "T-0001", **arguments}
                command.extend(["--task", "T-0001"])
            request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": name, "arguments": arguments}})
            result = mcp_server._handle_request(request, project=Path("."))["result"]
            assert result["isError"] and all(fragment in result["content"][0]["text"] for fragment in fragments)
            with redirect_stderr(StringIO()) as stderr:
                try:
                    code = cli.main([*command, *flags])
                except SystemExit as exc:
                    code = exc.code
            assert code and all(fragment in stderr.getvalue() for fragment in fragments)


def test_section_untrusted_fields_cover_what_the_records_declare(tmp_path):
    """Derive the expected fields from real records, not from the constants under test."""
    from tests.test_task_flow import PASS, _open
    from tests.test_task_verification import _project

    db = tmp_path / "ledger.sqlite"
    project = _project(tmp_path)
    with closing(ledger.Ledger(db)) as store:
        task = _open(store, project, checks=(PASS,))
        finding = store.add_finding(task, actor="reviewer", severity="minor", title="Title", detail="Detail")
        store.update_finding(finding["id"], actor="claude", status="fixed", resolution_note="Note")
        columns = {row["name"] for row in store.connection.execute("PRAGMA table_info(findings)")}
    with redirect_stdout(StringIO()):
        assert cli.main(["task", "verify", "--task", task, "--db", str(db), "--json"]) == 0
    with patch.dict(os.environ, HIPSON_LEDGER=str(db)):
        verification = _call_mcp("task.show", {"task": task, "section": "verification"})
        findings = _call_mcp("task.show", {"task": task, "section": "findings"})
    record, = verification["records"]
    declared = set(record["untrusted_fields"])
    assert declared and {f"records[].{field}" for field in declared} <= set(verification["untrusted_fields"])
    assert findings["records"][0]["resolution_note"] == "Note"
    free_text = columns - FINDING_SYSTEM_COLUMNS
    assert free_text == {"title", "detail", "location", "resolution_note"}
    assert {f"records[].{field}" for field in free_text} <= set(findings["untrusted_fields"])
