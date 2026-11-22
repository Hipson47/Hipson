"""Recorded read contracts and their separation from live task gates."""

import ast
import json
import os
import re
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, redirect_stdout
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from threading import Barrier, Event
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hipson import cli, flow_driver, lead_context, ledger, task_context, task_flow, task_projection, task_verification
from tests.test_flow_driver import _spec
from tests.test_ledger_recovery import _v1_file
from tests.test_task_flow import PASS, SECOND, _open, _project


def _worker_result(summary):
    return {"status": "completed", "summary": summary, "files_changed": [], "commands": [], "findings": [], "risks": []}


def test_projection_fields_recorded_evidence_and_unicode_bounds(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    text = "漢☃😀" * 180
    checks = [["check", str(index)] for index in range(7)]
    with closing(ledger.Ledger(tmp_path / "ledger.sqlite")) as store:
        task = store.open_task(project=project, title=text, objective=text, checks=checks, review_policy="required")["id"]
        reviewer = store.assign(task, actor="reviewer", role="reviewer", mode="ro")["id"]
        for phase in ("start", "end"):
            store.record_review_tree(reviewer, phase=phase, revision="saved-revision", fingerprint="saved-fingerprint",
                                     source="snapshot")
        store.finish(reviewer, result="passed", summary="PRIVATE_EVENT_TEXT", worker_result=_worker_result("first\n" + text))
        explorer = store.assign(task, actor="scout", role="explorer", mode="ro")["id"]
        blocking = store.add_finding(task, actor="reviewer", severity="blocking", title="PRIVATE_FINDING_TEXT")["id"]
        major = store.add_finding(task, actor="reviewer", severity="major", title="PRIVATE_FINDING_TEXT")["id"]
        fixed = store.add_finding(task, actor="reviewer", severity="minor", title="Already fixed")["id"]
        store.update_finding(fixed, status="fixed", actor="claude")
        store.add_finding(task, actor="reviewer", severity="info", title="PRIVATE_FINDING_TEXT")
        heartbeat = "2099-01-01T00:00:00+00:00"
        store.observe(explorer, {"heartbeat_at": heartbeat, "progress": "PRIVATE_ACTIVITY_TEXT"})

        # Verify selection by full command identity and append order.
        for record_id, exit_code in (("V-old", 3), ("V-last", 0)):
            store._record_verification({
                "id": record_id, "task": task, "actor": "claude", "command": ["display-only"],
                "argv_sha256": task_verification.argv_identity(checks[0]),
                "revision": "a" * 40, "fingerprint": None, "exit_code": exit_code, "state_changed": False,
                "coverage": "complete", "cleanup_confirmed": True,
                "context": {"role": None, "assignment": None, "task": None}, "builder_finished_seq": 0,
                "finished_at": "2099-01-01T01:00:00+02:00", "error": text,
                "files": [{"path": "PRIVATE_INVENTORY", "state": "missing"}], "output_tail": "PRIVATE_OUTPUT_TAIL",
            })

    with closing(ledger.Ledger(tmp_path / "ledger.sqlite", read_only=True)) as store, \
            patch.object(task_verification, "project_state", side_effect=AssertionError("content read")), \
            patch.object(task_projection.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 1)) as git:
        view = task_projection.project_task(store, task)
        assert git.call_count == 1 and git.call_args.kwargs["timeout"] == 1

    assert view["view_schema"] == task_projection.VIEW_SCHEMA == 1
    assert view["freshness_source"] == "recorded"
    assert view["gate"] == "not_evaluated" and view["gate_note"] == "task verify / task close evaluate the gates"
    assert view["title"] == text[:159] + "…" and view["objective"] == text[:399] + "…"
    assert view["last_activity_at"] == heartbeat
    assert view["review"] == {"state": "ineligible", "assignment_id": reviewer}
    assert view["assignments"][0]["summary"] == ("first " + text)[:199] + "…"
    assert view["assignments"][0]["ack_state"] == "unacknowledged"
    assert view["assignments"][1]["summary"] is None and view["assignments"][1]["ack_state"] == "not_required"
    assert set(view["truncated_fields"]) == {"title", "objective", "assignments.0.summary", "reasons.0"}
    assert view["open_findings"] == {"blocking": 1, "major": 1, "minor": 0, "info": 1}
    assert view["blocking_findings"] == [blocking] and view["major_findings"] == [major]
    assert view["checks"][0] == {
        "command": checks[0], "recorded": "unknown", "current": "unknown", "record_id": "V-last", "exit_code": 0,
        "recorded_at": "2099-01-01T01:00:00+02:00", "recorded_revision": "a" * 40, "head_matches": None,
    }
    assert all(item["recorded"] == "missing" and item["current"] == "unknown" and item["record_id"] is None
               for item in view["checks"][1:])
    assert len(view["reasons"]) == 5 and view["omitted_reasons"] == 2
    assert view["reasons"][0].startswith("Check 1: Verification recorded no working-tree fingerprint")
    assert len(view["reasons"][0]) == 240 and view["reasons"][0].endswith("…")
    assert view["assignments"][0]["status"] == "completed"
    serialized = task_projection.dumps(view).encode("utf-8")
    assert b"PRIVATE_" not in serialized and b"saved-fingerprint" not in serialized
    assert json.loads(serialized) == view


def test_list_budget_preserves_order_and_reuses_head_per_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    db = tmp_path / "ledger.sqlite"
    with closing(ledger.Ledger(db)) as store:
        first, second, third, terminal = [
            store.open_task(project=project, title="漢😀" * 75, objective=name)["id"]
            for name in ("first", "second", "third", "terminal")
        ]
        store.set_status(terminal, to="cancelled", actor="claude")
        assignment = store.assign(third, actor="scout", role="explorer", mode="ro")["id"]
        store.finish(assignment, result="passed", summary="Ready")
        store.add_finding(second, actor="reviewer", severity="major", title="Fix me")

    with closing(ledger.Ledger(db, read_only=True)) as store, patch.object(task_projection, "_head", return_value=None) as head:
        statements = []
        store.connection.set_trace_callback(statements.append)
        board = task_projection.project_list(store, kind="task.board")
        assert [sql for sql in statements if sql in ("BEGIN", "COMMIT")] == ["BEGIN", "COMMIT"]
        head.assert_called_once_with(str(project.resolve()))
        assert [task["id"] for task in board["tasks"]] == [first, second, third]
        now = task_projection.project_list(store, kind="task.now")
        assert [task["id"] for task in now["tasks"]] == [third, second, first]
        assert task_projection.project_list(store, kind="task.now") == now
        assert task_projection.project_task(store, terminal)["status"] == "cancelled"
        assert task_projection.project_list(store, kind="task.board", include_terminal=True)["tasks"][-1]["id"] == terminal

        tiny = task_projection.project_list(store, kind="task.now", byte_budget=1_000)
        assert tiny["truncated"] is True and tiny["omitted"] == 2
        assert tiny["more"] == {"tool": "task.now", "arguments": {"detail": "compact", "offset": 1},
                                "cli": "hipson task now --detail compact --offset 1 --json"}
        stub, = tiny["tasks"]
        assert stub["id"] == third and stub["too_large"] is True
        assert set(stub) == {"id", "status", "title", "too_large", "hint"} and "task show" in stub["hint"]
        encoded = task_projection.dumps(tiny).encode("utf-8")
        assert json.loads(encoded) == tiny and len(encoded) <= 1_000

        prefix = {**tiny, "tasks": now["tasks"][:1]}
        budget = len(task_projection.dumps(prefix).encode("utf-8"))
        assert "漢😀" in task_projection.dumps(prefix)
        assert task_projection.project_list(store, kind="task.now", byte_budget=budget) == prefix
        assert task_projection.project_list(store, kind="task.now", byte_budget=budget - 1)["tasks"][0]["too_large"]

        page = task_projection.project_list(store, kind="task.now", offset=tiny["more"]["arguments"]["offset"], limit=1)
        assert [item["id"] for item in page["tasks"]] == [second]
        assert page["more"]["arguments"] == {"detail": "compact", "offset": 2, "limit": 1}
        final = task_projection.project_list(store, kind="task.now", offset=page["more"]["arguments"]["offset"], limit=1)
        assert [item["id"] for item in final["tasks"]] == [first] and final["more"] is None
        assert not task_projection.project_list(store, kind="task.now", offset=3)["tasks"]
        with pytest.raises(ValueError, match="too small"):
            task_projection.project_list(store, kind="task.now", byte_budget=10)
        with pytest.raises(ValueError, match="offset"):
            task_projection.project_list(store, kind="task.now", offset=-1)
        with pytest.raises(ValueError, match="limit"):
            task_projection.project_list(store, kind="task.now", limit=0)


def test_projection_reads_v1_without_migration(tmp_path):
    db = tmp_path / "legacy.sqlite"
    _v1_file(db)
    before = db.read_bytes()
    with closing(ledger.Ledger(db, read_only=True)) as store, patch.object(task_projection, "_head", return_value=None):
        task = task_projection.project_task(store, "T-0001")
    assert task["checks"] == [] and task["review_policy"] == "none"
    assert task["assignments"][1]["ack_state"] == "not_required"
    assert task["assignments"][1]["summary"] is None
    assert db.read_bytes() == before


_READ_HANDLERS = {
    "cli.py": {"command_task_read"},
    "mcp_server.py": {"_task_read_projection", "_compact_task_text"},
    "lead_context.py": {"build_compact"},
}
_READ_DISPATCH = {
    "cli.py": {
        "build_parser": (
            (None, "read.set_defaults(func=command_task_read)"),
            (None, "resume.set_defaults(func=command_task_read)"),
        ),
        "command_task_read": (("action == 'resume'", "payload = lead_context.build_compact("
                               "store, project=project, include_all=args.all, cli_db=cli_db)"),),
    },
    "mcp_server.py": {
        "_call_tool": ((None, "text = _compact_task_text(payload) if compact else json.dumps(payload, ensure_ascii=False)"),),
        "_task_tool_payload": (("name in _TASK_READS and (arguments.get('detail') == 'compact' or 'section' in arguments)",
                                "return _task_read_projection(name, arguments, db)"),),
        "_task_read_projection": (("name == 'lead.resume'", "return lead_context.build_compact("
                                   "store, project=project, include_all=include_all)"),),
    },
}


def _projection_references(source, *, module=""):
    low_level = re.compile(r"\b(?:task_projection|projection_rows)\b")
    handlers = re.compile(r"\b(?:command_task_read|_task_read_projection|_compact_task_text|build_compact)\b")
    tree = ast.parse(source)
    declarations = {id(method) for node in tree.body if module == "ledger.py" and isinstance(node, ast.ClassDef)
                    and node.name == "Ledger" for method in node.body
                    if isinstance(method, ast.FunctionDef) and method.name == "projection_rows"}
    permitted = set()
    dispatch = set()
    for function in (node for node in tree.body if isinstance(node, ast.FunctionDef)):
        if function.name in _READ_HANDLERS.get(module, ()):
            declarations.add(id(function))
            permitted.update(id(child) for statement in function.body for child in ast.walk(statement))
        for guard, statement in _READ_DISPATCH.get(module, {}).get(function.name, ()):
            shape = ast.parse(statement).body[0]
            expected = ast.dump(shape)
            if guard is None:
                candidates = ast.walk(function)
            else:
                candidates = (child for branch in ast.walk(function) if isinstance(branch, ast.If)
                              and ast.unparse(branch.test) == guard for child in branch.body)
            match = next((candidate for candidate in candidates
                          if isinstance(candidate, type(shape)) and ast.dump(candidate) == expected), None)
            if match is not None:
                dispatch.update(id(child) for child in ast.walk(match))
    return [node.lineno for node in ast.walk(tree)
            if id(node) not in declarations and any(
                (module != "task_projection.py" and id(node) not in permitted and low_level.search(value))
                or (id(node) not in dispatch and handlers.search(value))
                for value in vars(node).values() if isinstance(value, str))]


def test_gate_sources_do_not_reference_projection():
    root = Path(ledger.__file__).parent
    violations = []
    for module in sorted(root.rglob("*.py")):
        relative = module.relative_to(root).as_posix()
        violations.extend(f"{relative}:{line}" for line in _projection_references(
            module.read_text(encoding="utf-8"), module=relative))
    assert not violations, "\n".join(violations)


def test_gate_source_check_catches_dynamic_references():
    assert _projection_references("importlib.import_module('hipson.task_projection')") == [1]
    assert _projection_references("getattr(store, 'projection_rows')") == [1]
    assert _projection_references("from hipson import task_projection") == [1]
    assert _projection_references("class Ledger:\n    def projection_rows(self): ...", module="ledger.py") == []
    assert _projection_references("class Ledger:\n    def projection_rows(self): self.projection_rows()",
                                  module="ledger.py") == [2]
    source = "def command_task_read():\n    import task_projection\ndef command_task():\n    import task_projection"
    assert _projection_references(source, module="cli.py") == [4]


def test_gate_source_check_rejects_injected_calls_to_read_handlers():
    read_text = Path.read_text
    gate_file = Path(task_flow.__file__)

    def read_with_injected_calls(path, *args, **kwargs):
        source = read_text(path, *args, **kwargs)
        if path == gate_file:
            original = "def command_close(args: argparse.Namespace) -> int:\n"
            assert original in source
            return source.replace(original, original + "    command_task_read(args)\n"
                                  "    mcp_server._task_read_projection(name, arguments, db)\n"
                                  "    mcp_server._compact_task_text(payload)\n"
                                  "    lead_context.build_compact(store)\n", 1)
        return source

    with patch.object(Path, "read_text", read_with_injected_calls), pytest.raises(AssertionError) as exc:
        test_gate_sources_do_not_reference_projection()
    assert sum(line.strip().startswith("task_flow.py:") for line in str(exc.value).splitlines()) == 4
    test_gate_sources_do_not_reference_projection()


def test_close_and_live_content_gate_work_with_projection_disabled(tmp_path):
    project = _project(tmp_path)
    db = tmp_path / "ledger.sqlite"

    def forbidden(*args, **kwargs):
        raise AssertionError("a gate reached the compact projection")

    with patch.dict(os.environ), patch.multiple(task_projection, project_task=forbidden, project_list=forbidden,
                                                show_task=forbidden, read_section=forbidden, resume_inputs=forbidden,
                                                dumps=forbidden), \
            patch.object(ledger.Ledger, "projection_rows", forbidden):
        for name in ("HIPSON_ROLE", "HIPSON_TASK", "HIPSON_ASSIGNMENT"):
            os.environ.pop(name, None)
        with closing(ledger.Ledger(db)) as store:
            task = _open(store, project, checks=(PASS,))
        first = task_flow.close(db, task, to="review")
        assert first["closed"] and first["status"] == "review" and len(first["records"]) == 1
        (project / "source.py").write_text("value = 2\n")
        with closing(ledger.Ledger(db)) as store:
            try:
                store.close_task(task, actor="claude")
            except ledger.CloseRefusedError as exc:
                assert any("working tree changed" in reason for reason in exc.reasons)
            else:
                raise AssertionError("close accepted an unverified content edit")
        final = task_flow.close(db, task)
        assert final["closed"] and final["status"] == "done" and len(final["records"]) == 1

        spec = flow_driver.validate_spec(json.loads(_spec(tmp_path, project, checks=(PASS,), close_to="done").read_text()))
        digest = flow_driver.spec_digest(spec)
        flow_task = flow_driver._open(db, spec, digest, str(project))
        with closing(ledger.Ledger(db)) as store:
            store.set_status(flow_task, to="in_progress", actor=spec["lead"])
            builder = store.assign(flow_task, actor="builder", role="builder", mode="rw", scope=["source.py"])["id"]
            store.finish(builder, result="passed", summary="Ready", worker_result=_worker_result("Ready"),
                         failure_journal={"result_state": "valid"})
            store.ack(builder)
            record = _evidence(store, flow_task, project, actor=spec["lead"])
            _finish_review(store, flow_task, record)
        with patch.object(flow_driver, "_launch", forbidden):
            flow = flow_driver.run(db, spec, digest, resume=flow_task)
        assert flow["status"] == "done" and flow["steps"] == ["ack", "close", "stop"], flow

        with closing(ledger.Ledger(db)) as store:
            verify_task = _open(store, project, checks=(PASS,))
        with redirect_stdout(StringIO()) as stdout:
            assert cli.main(["task", "verify", "--task", verify_task, "--db", str(db), "--json"]) == 0
        record, = json.loads(stdout.getvalue())
        assert record["exit_code"] == 0 and record["fingerprint"]
        with closing(ledger.Ledger(db, read_only=True)) as store:
            assert "Gate: passing (current)" in task_context.context(store, verify_task)["verification_summary"]
            (project / "source.py").write_text("value = 3\n")
            assert "Gate: not passing (stale)" in task_context.context(store, verify_task)["verification_summary"]


def _evidence(store, task, project, **overrides):
    command = overrides.get("command", PASS)
    evidence = {
        **task_verification.project_state(str(project), store.path),
        "id": "V-" + str(store.connection.execute("SELECT COUNT(*) FROM verifications").fetchone()[0] + 1),
        "task": task, "actor": "claude", "command": command, "argv_sha256": task_verification.argv_identity(command),
        "finished_at": datetime.now(UTC).isoformat(), "builder_finished_seq": store._builder_finished_seq(task),
        "context": {"role": None, "assignment": None, "task": None}, "exit_code": 0,
        "coverage": "complete", "metadata_fallback_paths": [], "state_changed": False, "cleanup_confirmed": True,
        **overrides,
    }
    store._record_verification(evidence)
    return evidence


def _finish_review(store, task, record, *, actor="reviewer", mode="ro"):
    assignment = store.assign(task, actor=actor, role="reviewer", mode=mode,
                              scope=["source.py"] if mode == "rw" else [])["id"]
    for phase in ("start", "end"):
        store.record_review_tree(assignment, phase=phase, revision=record["revision"], fingerprint=record["fingerprint"],
                                 source="snapshot")
    store.finish(assignment, result="passed", summary="Reviewed", worker_result=_worker_result("Reviewed"))
    return assignment


def _assert_gate_parity(view, compact):
    full = view["verification"]
    assert compact["gate"] == "not_evaluated"
    full_checks = full["checks"] or ([full] if full["latest"] else [])
    assert len(compact["checks"]) == len(full_checks)
    for check, projected in zip(full_checks, compact["checks"], strict=True):
        assert set(projected) == {"command", "recorded", "current", "record_id", "exit_code", "recorded_at",
                                  "recorded_revision", "head_matches"}
        assert projected["recorded"] in {"ok", "unknown", "partial", "missing", "failing"}
        assert projected["current"] in {"not_checked", "stale", "unknown"}
        assert projected["current"] != "unknown" or projected["head_matches"] is None
        if check["passing"]:
            assert projected["current"] != "stale"
            continue
        assert check["reasons"]
        detected_stale = ("stale: HEAD moved", "stale: project changed during verification",
                          "stale: verification predates latest finished builder assignment")
        if any(reason.startswith(detected_stale) for reason in check["reasons"]):
            assert projected["current"] == "stale", (view["id"], check, projected)
        else:
            assert projected["current"] == "not_checked", (view["id"], check, projected)
        reasons = check["reasons"]
        current = full["current"]
        if check["freshness"] == "missing":
            assert projected["recorded"] == "missing"
        elif any(reason.startswith("failing verification") for reason in reasons):
            assert projected["recorded"] == "failing"
        elif current["fingerprint"] and any(reason.startswith("unavailable working-tree fingerprint")
                                            for reason in reasons):
            assert projected["recorded"] == "unknown"
        elif any(reason.startswith("partial content coverage") for reason in reasons):
            if not any(":metadata:" in item["state"] for item in current["files"]):
                assert projected["recorded"] == "partial"
        elif any(reason.startswith(("verification was not recorded", "verification process cleanup"))
                 for reason in reasons):
            assert projected["recorded"] == "unknown"
        elif all(reason.startswith("stale: working tree changed") for reason in reasons):
            assert projected["recorded"] == "ok"
    assert view["review"]["passing"] or compact["review"]["state"] not in {"recorded_snapshot", "not_required"}, (
        view["id"], view["review"], compact["review"])


def test_projection_parity_for_recorded_checks_and_review_eligibility(tmp_path):
    project = _project(tmp_path)
    with closing(ledger.Ledger(tmp_path / "ledger.sqlite")) as store:
        states = {}

        def seed(name, *, checks=(PASS,), review=False, **record):
            task = store.open_task(project=project, title=name, objective=name, checks=checks,
                                   review_policy="required" if review else "none")["id"]
            saved = _evidence(store, task, project, **record)
            return task, saved

        clean, _ = seed("clean")
        states[clean] = ("ok", "not_checked", "not_required")
        clean_review, record = seed("independent snapshot", review=True)
        _finish_review(store, clean_review, record)
        states[clean_review] = ("ok", "not_checked", "recorded_snapshot")

        for name, recorded, current, evidence in (
            ("failing", "failing", "not_checked", {"exit_code": 4}),
            ("coverage", "partial", "not_checked", {"coverage": "partial"}),
            ("paths", "partial", "not_checked", {"metadata_fallback_paths": ["large"]}),
            ("fingerprint", "unknown", "not_checked", {"fingerprint": ""}),
            ("cleanup", "unknown", "not_checked", {"cleanup_confirmed": False}),
            ("worker", "unknown", "not_checked", {"context": {"role": "Worker", "assignment": None, "task": None}}),
            ("worker assignment", "unknown", "not_checked",
             {"context": {"role": None, "assignment": "A-1", "task": None}}),
            ("non-lead actor", "unknown", "not_checked", {"actor": "different lead"}),
            ("changed", "ok", "stale", {"state_changed": True}),
            ("HEAD", "ok", "stale", {"revision": "a" * 40}),
            ("changed and failed", "failing", "stale", {"state_changed": True, "exit_code": 1}),
            ("failed without fingerprint", "failing", "not_checked", {"exit_code": 2, "fingerprint": ""}),
            ("inventory metadata", "partial", "not_checked",
             {"files": [{"path": "large", "state": "100644:metadata:file_limit"}]}),
            ("unrecorded cleanup", "unknown", "not_checked", {"cleanup_confirmed": None}),
            ("unrecorded coverage", "unknown", "not_checked", {"coverage": None}),
        ):
            task, _ = seed(name, **evidence)
            states[task] = (recorded, current, "not_required")

        missing = _open(store, project)
        states[missing] = ("missing", "not_checked", "not_required")

        builder_stale, _ = seed("builder completed")
        builder = store.assign(builder_stale, actor="builder", role="builder", mode="rw", scope=["source.py"])["id"]
        store.finish(builder, result="passed", summary="Ready")
        states[builder_stale] = ("ok", "stale", "not_required")

        for actor, mode in (("CLAUDE", "ro"), ("reviewer", "rw")):
            task, record = seed(actor, review=True)
            _finish_review(store, task, record, actor=actor, mode=mode)
            states[task] = ("ok", "not_checked", "ineligible")

        same_builder, _ = seed("review by builder", review=True)
        builder = store.assign(same_builder, actor="Builder", role="builder", mode="rw", scope=["source.py"])["id"]
        store.finish(builder, result="passed", summary="Ready")
        _finish_review(store, same_builder, _evidence(store, same_builder, project), actor="builder")
        states[same_builder] = ("ok", "not_checked", "ineligible")

        stale_review, record = seed("review predates verification", review=True)
        _finish_review(store, stale_review, record)
        _evidence(store, stale_review, project)
        states[stale_review] = ("ok", "not_checked", "ineligible")

        early_review, record = seed("review predates builder", review=True)
        _finish_review(store, early_review, record)
        builder = store.assign(early_review, actor="builder", role="builder", mode="rw", scope=["source.py"])["id"]
        store.finish(builder, result="passed", summary="Ready")
        states[early_review] = ("ok", "stale", "ineligible")

        other_snapshot, record = seed("wrong candidate", review=True)
        _finish_review(store, other_snapshot, {**record, "fingerprint": "different-tree"})
        states[other_snapshot] = ("ok", "not_checked", "changed")

        multiple, record = seed("multiple checks", checks=(PASS, SECOND), review=True)
        _evidence(store, multiple, project, command=SECOND)
        _finish_review(store, multiple, record)
        states[multiple] = ("ok", "not_checked", "recorded_snapshot")

        adhoc, _ = seed("ad hoc", checks=())
        latest = _evidence(store, adhoc, project, command=SECOND, exit_code=3)
        states[adhoc] = ("failing", "not_checked", "not_required")
        empty = store.open_task(project=project, title="No checks or records", objective="No checks or records")["id"]
        unverified = store.open_task(project=project, title="Review without verification",
                                     objective="Review without verification", checks=[PASS],
                                     review_policy="required")["id"]
        _finish_review(store, unverified, {"revision": "a" * 40, "fingerprint": "never-verified"})

        compact = task_projection.project_list(store, kind="task.board", byte_budget=64_000)
        by_id = {task["id"]: task for task in compact["tasks"]}
        assert set(by_id) == {*states, empty, unverified}
        assert by_id[empty]["checks"] == []
        assert by_id[empty]["reasons"] == [task_projection.NO_VERIFICATION]
        _assert_gate_parity(store.show(empty), by_id[empty])
        # review_state alone passes without an accepted verification; the verification gate is what refuses.
        assert not store.show(unverified)["verification"]["passing"]
        assert by_id[unverified]["review"]["state"] == "unverified"
        assert by_id[unverified]["checks"][0]["recorded"] == "missing"
        _assert_gate_parity(store.show(unverified), by_id[unverified])
        failed = next(view for view in by_id.values() if view["title"] == "failed without fingerprint")
        assert failed["reasons"][0] == "Check 1: Verification exit code: 2"
        for task, (recorded, current, review) in states.items():
            projected = by_id[task]
            full = store.show(task)
            _assert_gate_parity(full, projected)
            check = projected["checks"][0]
            assert (check["recorded"], check["current"], projected["review"]["state"]) == (recorded, current, review)
            assert check["head_matches"] is (None if task == missing else full["title"] != "HEAD")
        head_moved = next(view for view in by_id.values() if view["title"] == "HEAD")
        assert "Check 1: HEAD moved since verification" in head_moved["reasons"]
        assert by_id[multiple]["checks"][1]["recorded"] == "ok"
        assert by_id[adhoc]["checks"][0]["record_id"] == latest["id"]
        assert by_id[adhoc]["checks"][0]["command"] == SECOND

        with patch.object(task_projection, "_head", return_value=None):
            unavailable = task_projection.project_task(store, clean)["checks"][0]
            assert unavailable["recorded"] == "ok" and unavailable["current"] == "unknown"
            assert unavailable["head_matches"] is None
            latest_builder = task_projection.project_task(store, builder_stale)["checks"][0]
            assert latest_builder["current"] == "stale" and latest_builder["head_matches"] is None

        # The full view detects a content change even with the same HEAD and mtime.
        source = project / "source.py"
        before = source.stat()
        source.write_text("value = 2\n")
        os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
        with patch.object(task_verification, "project_state", side_effect=AssertionError("content read")), \
                patch.object(task_projection, "_git", wraps=task_projection._git) as git:
            changed = task_projection.project_task(store, clean)
        git.assert_called_once_with(str(project), "rev-parse", "--verify", "HEAD")
        full = store.show(clean)
        assert full["verification"]["checks"][0]["freshness"] == "stale"
        assert full["verification"]["checks"][0]["reasons"][0].startswith("stale: working tree changed")
        _assert_gate_parity(full, changed)
        check = changed["checks"][0]
        assert check["head_matches"] is True and check["recorded"] == "ok" and check["current"] == "not_checked"


def test_projection_terminal_assignment_inclusion_matches_board(tmp_path):
    project = _project(tmp_path)
    with closing(ledger.Ledger(tmp_path / "ledger.sqlite")) as store:
        terminal = []
        for name in ("active", "unread", "historical", "acknowledged", "empty"):
            task = _open(store, project, checks=())
            terminal.append(task)
            if name != "empty":
                assignment = store.assign(task, actor="worker", role="explorer", mode="ro")["id"]
                if name == "historical":
                    store.finish(assignment, result="passed", summary="Already delivered")
            store.set_status(task, to="cancelled", actor="claude")
            if name in ("unread", "acknowledged"):
                store.finish(assignment, result="passed", summary="Delivered after close")
                if name == "acknowledged":
                    store.ack(assignment)
        done = _open(store, project, checks=())
        active = store.assign(done, actor="worker", role="explorer", mode="ro")["id"]
        for status in ("review", "done"):
            store.set_status(done, to=status, actor="claude", force=True, reason="Test terminal assignment handling")
        board = [task["id"] for task in store.board()]
        assert board == [*terminal[:2], done]
        for kind in ("task.board", "task.now"):
            assert {task["id"] for task in task_projection.project_list(store, kind=kind)["tasks"]} == set(board)
            assert {task["id"] for task in task_projection.project_list(
                store, kind=kind, include_terminal=True)["tasks"]} == {*terminal, done}
        assert task_projection.project_task(store, terminal[2])["assignments"][0]["ack_state"] == "not_required"
        store.finish(active, result="passed", summary="Delivered after force close")
        assert [task["id"] for task in store.board()] == board
        assert done in {task["id"] for task in task_projection.project_list(store, kind="task.now")["tasks"]}


def test_task_bounds_keep_newest_assignments_and_counts_and_escape_controls(tmp_path):
    project = tmp_path / ("x" * max(1, 210 - len(str(tmp_path)) - 1))
    project.mkdir()
    unicode_text = "漢😀" * 240
    command = ["x" * 210, unicode_text, "more"]
    checks = [command] + [["check", str(index)] for index in range(1, 16)]
    control = "\x1b\u202e"
    with closing(ledger.Ledger(tmp_path / "ledger.sqlite")) as store:
        lead = control + unicode_text
        task = store.open_task(project=project, title=control + unicode_text, objective=control + unicode_text,
                               lead=lead, checks=checks)["id"]
        ids = []
        for index in range(10):
            actor = f"{index} {control}{unicode_text}"
            assignment = store.assign(task, actor=actor, role="explorer", mode="ro")["id"]
            store.finish(assignment, result="passed", summary="Ready",
                         worker_result=_worker_result(f"one\ntwo {control}{unicode_text}"))
            ids.append(assignment)
        for severity in ("blocking", "major"):
            for index in range(12):
                store.add_finding(task, actor="worker", severity=severity, title=f"{severity} {index}")

        # Supported writes enforce the same 16-check limit. The reader caps older or externally populated rows too.
        store.connection.execute("UPDATE tasks SET checks = ? WHERE id = ?", (json.dumps([*checks, SECOND]), task))
        view = task_projection.project_task(store, task)

    assert len(view["checks"]) == 16 and "checks" in view["truncated_fields"]
    assert len(" ".join(view["checks"][0]["command"])) == 400
    assert all(len(arg) <= 200 for arg in view["checks"][0]["command"])
    assert "checks.0.command" in view["truncated_fields"]
    assert [item["id"] for item in view["assignments"]] == ids[-8:]
    assert view["omitted_assignments"] == 2
    assert len(view["blocking_findings"]) == len(view["major_findings"]) == 10
    assert view["open_findings"] == {"blocking": 12, "major": 12, "minor": 0, "info": 0}
    assert len(view["project"]) == len(view["lead"]) == len(view["assignments"][0]["actor"]) == 200
    values = [view["title"], view["objective"], view["lead"], view["assignments"][0]["actor"],
              view["assignments"][0]["summary"]]
    for value in values:
        assert "\\u001b\\u202e" in value and "\x1b" not in value and "\u202e" not in value
    assert view["assignments"][0]["summary"].startswith("one two ")


def test_projection_with_empty_ledger_and_old_schemas(tmp_path):
    with closing(ledger.Ledger(tmp_path / "empty.sqlite")) as store:
        for kind in ("task.board", "task.now"):
            result = task_projection.project_list(store, kind=kind)
            assert result["tasks"] == [] and result["more"] is None and not result["truncated"]
    project = _project(tmp_path)
    for version in (6, 10):
        db = tmp_path / f"v{version}.sqlite"
        _v1_file(db)
        with closing(sqlite3.connect(db)) as connection:
            for migration in ledger.MIGRATIONS[:version - 1]:
                for statement in migration:
                    connection.execute(statement)
            connection.execute("UPDATE tasks SET project = ?", (str(project),))
            connection.commit()
        with closing(ledger.Ledger(db, read_only=True)) as store:
            task = task_projection.project_task(store, "T-0001")
            assert task["checks"] == [] and task["review_policy"] == "none"
            assert store.connection.execute("SELECT version FROM schema_version").fetchone()[0] == version
            assert task_projection.project_list(store, kind="task.board")["tasks"]


def test_malformed_evidence_becomes_unknown_without_leaking_invalid_fields(tmp_path):
    project = _project(tmp_path)
    with closing(ledger.Ledger(tmp_path / "ledger.sqlite")) as store:
        malformed_tasks = []
        for key, bad in (
            ("revision", []), ("revision", "r" * 400), ("finished_at", []), ("finished_at", "z" * 400),
            ("id", {}), ("id", "v" * 400), ("exit_code", "0"), ("exit_code", True), ("exit_code", 10**90),
            ("context", "lead"), ("command", [123]), ("command", ["x" * 4097]),
            ("cleanup_confirmed", 1), ("state_changed", 0), ("fingerprint", "x" * 65),
            ("files", [{"path": "x", "state": 10}]),
            ("metadata_fallback_paths", [{}]),
        ):
            task = _open(store, project)
            good = _evidence(store, task, project)
            record_id = f"malformed-{task}"
            invalid = {**good, "id": record_id, key: bad}
            store.connection.execute("INSERT INTO verifications (id, task, evidence) VALUES (?, ?, ?)",
                                     (record_id, task, json.dumps(invalid)))
            malformed_tasks.append(task)
        for checks in ([], [PASS]):
            task = _open(store, project, checks=checks)
            _evidence(store, task, project)
            store.connection.execute("INSERT INTO verifications (id, task, evidence) VALUES (?, ?, ?)",
                                     (f"broken-{task}", task, "{ broken"))
            malformed_tasks.append(task)

        listed = task_projection.project_list(store, kind="task.board")
        by_id = {item["id"]: item for item in listed["tasks"]}
        for task in malformed_tasks:
            view = by_id[task]
            check, = view["checks"]
            assert check["recorded"] == "unknown" and check["current"] == "not_checked"
            assert "malformed" in view["reasons"][0]
            assert all(check[key] is None for key in ("record_id", "exit_code", "recorded_at", "recorded_revision"))
        task_projection.dumps(listed).encode("utf-8")


def test_large_evidence_does_not_load_the_inventory(tmp_path):
    project = _project(tmp_path)
    with closing(ledger.Ledger(tmp_path / "ledger.sqlite")) as store:
        task = _open(store, project)
        files = [{"path": f"PRIVATE_INVENTORY/{index:04d}-" + "x" * 122, "state": "100644:" + "f" * 64}
                 for index in range(1_650)]
        record = _evidence(store, task, project, files=files, output_tail="PRIVATE_OUTPUT_TAIL",
                           unexpected_field="PRIVATE_UNKNOWN_FIELD")
        assert 400_000 <= len(json.dumps(record).encode("utf-8")) < 420_000
        saved, = store.projection_rows(task=task, include_terminal=True)[0]["verifications"]
        assert set(saved) == {"task", "id", "seq", "identity", "fields", "files_type", "fallback_type",
                              "malformed_fallback", "malformed_files", "duplicate_fields", "metadata_fallback"}
        assert len(saved["fields"]) < 1_024 and b"PRIVATE_" not in saved["fields"]
        with patch.object(store, "verifications", side_effect=AssertionError("loaded full evidence")):
            compact = task_projection.project_task(store, task)
        check, = compact["checks"]
        assert check["record_id"] == record["id"]
        assert (check["recorded"], check["current"]) == ("ok", "not_checked")
        _assert_gate_parity(store.show(task), compact)

        # A metadata fallback at the end of the large inventory is still visible.
        files[-1]["state"] = "100644:metadata:file_limit"
        _evidence(store, task, project, files=files)
        partial = task_projection.project_task(store, task)
        assert (partial["checks"][0]["recorded"], partial["checks"][0]["current"]) == ("partial", "not_checked")
        _assert_gate_parity(store.show(task), partial)


def test_projection_batches_tables_and_selects_latest_verifications(tmp_path):
    project = _project(tmp_path)
    db = tmp_path / "ledger.sqlite"
    with closing(ledger.Ledger(db)) as store:
        declared = _open(store, project, checks=(PASS, SECOND))
        adhoc = _open(store, project, checks=())
        for task in (declared, adhoc):
            for command in (PASS, SECOND, PASS, SECOND, PASS):
                _evidence(store, task, project, command=command)
        expected = {declared: ["V-4", "V-5"], adhoc: ["V-10"]}
        statements = []
        store.connection.set_trace_callback(statements.append)
        rows = store.projection_rows(include_terminal=False)
        assert {row["id"]: [record["id"] for record in row["verifications"]] for row in rows} == expected
        for table in ("assignments", "events", "findings", "verifications"):
            selects = [query for query in statements if re.search(rf"FROM {table}\b", query)]
            assert sum("task IN (" in query for query in selects) == 1, selects
        assert not any("WHERE task = " in query and query.lstrip().startswith("SELECT * FROM") for query in statements)
        compact = task_projection.project_task(store, declared)
        assert [check["record_id"] for check in compact["checks"]] == ["V-5", "V-4"]


def test_head_uses_lead_git_hardening_and_handles_bad_arguments(tmp_path):
    project = str(tmp_path)
    sha = "c" * 40
    result = SimpleNamespace(stdout=(sha + "\n").encode(), returncode=0)
    with patch.dict(os.environ, GIT_CONFIG_COUNT="1", git_work_tree="BAD"), \
            patch.object(task_projection.subprocess, "run", return_value=result) as run:
        assert task_projection._head(project) == sha
        projected, = run.call_args.args
        kwargs = run.call_args.kwargs
        assert lead_context._git(project, "rev-parse", "--verify", "HEAD") == result.stdout
        full, = run.call_args.args
    assert projected == full
    assert kwargs["timeout"] == 1 and kwargs["check"] is False
    assert {key: value for key, value in kwargs["env"].items() if key.upper().startswith("GIT_")} == {
        "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0",
    }
    assert task_projection._head(project + "\0") is None
    with patch.object(task_projection.subprocess, "run", return_value=SimpleNamespace(stdout=b"x" * 80, returncode=0)):
        assert task_projection._head(project) is None


def test_head_reads_overlap_and_cancel_queued_projects_at_the_deadline(tmp_path):
    paths = [tmp_path / f"project-{i}" for i in range(9)]
    for path in paths:
        path.mkdir()
    with closing(ledger.Ledger(tmp_path / "ledger.sqlite")) as store:
        for path in paths:
            store.open_task(project=path, title=path.name, objective="Test concurrent HEADs")
        first_wave = Barrier(4)
        release = Event()

        def slow(project):
            first_wave.wait(timeout=3)
            release.wait(timeout=3)
            return None

        futures = []
        real_pool = ThreadPoolExecutor

        class CapturePool(real_pool):
            def submit(self, fn, /, *args, **kwargs):
                future = super().submit(fn, *args, **kwargs)
                futures.append(future)
                return future

        with patch.object(task_projection, "_head", side_effect=slow) as head, \
                patch.object(task_projection, "ThreadPoolExecutor", CapturePool):
            try:
                view = task_projection.project_list(store, kind="task.board")
                assert sorted(task["title"] for task in view["tasks"]) == sorted(path.name for path in paths)
                assert first_wave.n_waiting == 0 and head.call_count == 4
                assert sum(future.cancelled() for future in futures) == 5
            finally:
                release.set()
