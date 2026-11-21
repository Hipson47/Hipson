"""Recorded read contracts and their separation from live task gates."""

import ast
import json
import os
import subprocess
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from hipson import ledger, task_flow, task_projection, task_verification
from tests.test_ledger_recovery import _v1_file
from tests.test_task_flow import PASS, _open, _project


def _worker_result(summary):
    return {"status": "completed", "summary": summary, "files_changed": [], "commands": [], "findings": [], "risks": []}


def test_projection_fields_recorded_evidence_and_unicode_bounds(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    text = "ż☃😀" * 180
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
                "context": {"role": None, "assignment": None, "task": None},
                "finished_at": "2099-01-01T01:00:00+02:00", "error": text,
                "files": [{"path": "PRIVATE_INVENTORY"}], "output_tail": "PRIVATE_OUTPUT_TAIL",
            })

    with closing(ledger.Ledger(tmp_path / "ledger.sqlite", read_only=True)) as store, \
            patch.object(task_verification, "project_state", side_effect=AssertionError("content read")), \
            patch.object(task_projection.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 1)) as git:
        view = task_projection.project_task(store, task)
        assert git.call_count == 1 and git.call_args.kwargs["timeout"] == 1

    assert view["view_schema"] == task_projection.VIEW_SCHEMA == 1
    assert view["freshness_source"] == "recorded" and "task verify" in view["freshness_note"]
    assert view["title"] == text[:159] + "…" and view["objective"] == text[:399] + "…"
    assert view["last_activity_at"] == heartbeat
    assert view["review"] == {"state": "snapshot_recorded", "assignment_id": reviewer}
    assert view["assignments"][0]["summary"] == ("first " + text)[:199] + "…"
    assert view["assignments"][0]["ack_state"] == "unacknowledged"
    assert view["assignments"][1]["summary"] is None and view["assignments"][1]["ack_state"] == "not_required"
    assert set(view["truncated_fields"]) == {"title", "objective", "assignments.0.summary", "reasons.0"}
    assert view["open_findings"] == {"blocking": 1, "major": 1, "minor": 0, "info": 1}
    assert view["blocking_findings"] == [blocking] and view["major_findings"] == [major]
    assert view["checks"][0] == {
        "command": checks[0], "state": "unknown", "record_id": "V-last", "exit_code": 0,
        "recorded_at": "2099-01-01T01:00:00+02:00", "recorded_revision": "a" * 40, "head_matches": None,
    }
    assert all(item["state"] == "missing" and item["record_id"] is None for item in view["checks"][1:])
    assert len(view["reasons"]) == 5 and view["omitted_reasons"] == 2
    assert len(view["reasons"][0]) == 240 and view["reasons"][0].endswith("…")
    serialized = json.dumps(view).encode("utf-8")
    assert b"PRIVATE_" not in serialized and b"saved-fingerprint" not in serialized
    assert json.loads(serialized) == view


def test_list_budget_preserves_order_and_reuses_head_per_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    db = tmp_path / "ledger.sqlite"
    with closing(ledger.Ledger(db)) as store:
        first, second, third, terminal = [
            store.open_task(project=project, title="😀" * 150, objective=name)["id"]
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

        tiny = task_projection.project_list(store, kind="task.now", byte_budget=150)
        assert tiny["truncated"] is True and tiny["omitted"] == 3
        assert tiny["tasks"] == [] and tiny["more"] == {"kind": "task.now", "offset": 0}
        encoded = json.dumps(tiny).encode("utf-8")
        assert json.loads(encoded) == tiny and len(encoded) <= 150

        prefix = {**tiny, "tasks": now["tasks"][:1], "omitted": 2, "more": {"kind": "task.now", "offset": 1}}
        limit = len(json.dumps(prefix).encode("utf-8"))
        assert task_projection.project_list(store, kind="task.now", byte_budget=limit) == prefix
        assert task_projection.project_list(store, kind="task.now", byte_budget=limit - 1) == tiny


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


def test_gate_sources_do_not_reference_projection():
    # Guard the entire ledger (including gate helpers) and the modules that
    # implement verification, flow, context, review and restore gates.
    names = {"task_projection", "projection_rows"}
    modules = ("ledger", "task_verification", "flow_driver", "task_flow", "actor_routing",
               "task_checkpoint", "task_context", "review_snapshot")
    violations = []
    for module in modules:
        path = Path(ledger.__file__).with_name(f"{module}.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            references = []
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                references = [item.name for item in node.names]
                if isinstance(node, ast.ImportFrom) and node.module:
                    references.append(node.module)
            elif isinstance(node, ast.Attribute):
                references = [node.attr]
            elif isinstance(node, ast.Name):
                references = [node.id]
            if any(names.intersection(reference.split(".")) for reference in references):
                violations.append(f"{path.name}:{node.lineno}: {', '.join(references)}")
    assert not violations, "\n".join(violations)


def test_close_and_live_content_gate_work_with_projection_disabled(tmp_path):
    project = _project(tmp_path)
    db = tmp_path / "ledger.sqlite"

    def forbidden(*args, **kwargs):
        raise AssertionError("a gate reached the compact projection")

    with patch.dict(os.environ), patch.multiple(task_projection, project_task=forbidden, project_list=forbidden), \
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
