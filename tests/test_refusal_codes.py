"""Inventory the explicit refusals and check their wire format."""

from __future__ import annotations

import ast
import json
import re
import shlex
import sys
from collections import Counter, defaultdict
from contextlib import closing, contextmanager
from pathlib import Path

import pytest

from hipson import ledger, mcp_server, task_runner, task_verification
from hipson.task_calls import VERIFIER_REFUSAL_CODES, read_call
from tests.test_flow_driver import _flow, _repo, _spec
from tests.test_hipson_helpers import run_cli, run_cli_inprocess
from tests.test_ledger_mcp import _call
from tests.test_task_verification import _git, _project

ROOT = Path(__file__).resolve().parents[1]
MODULES = ("ledger", "task_flow", "task_verification", "task_runner", "task_calls", "cli")

# Bare re-raises are identified by their handler, and other exceptions by the raised expression.
ALLOWLIST = {
    ("ledger.load_task_result", "LedgerError"): "Preserve the worker result decoder's existing refusal.",
    ("ledger._open_read_only", "BaseException"): "Re-raise after closing the failed SQLite connection.",
    ("ledger._read_only_connection", "sqlite3.OperationalError"): "Propagate the original SQLite access error.",
    ("ledger._sidecar_read_only_connection", "sqlite3.OperationalError"): "Preserve SQLite WAL opening errors.",
    ("ledger._sidecar_read_only_connection", "BaseException"): "Re-raise after closing the snapshot.",
    ("ledger.Ledger.__init__", "BaseException"): "Re-raise initialization errors after closing the store.",
    ("ledger.Ledger._transaction", "BaseException"): "Propagate the operation's failure after rolling back.",
    ("task_verification._walk_error", "error"): "Forward os.walk's original error to the fingerprint evidence handler.",
    ("task_verification.run", "ledger.LedgerError(ledger.refusal_text(diagnostic))"):
        "Forward unstructured supervisor diagnostics without assigning a code.",
    ("task_verification._supervisor_refusal",
     "ledger.LedgerError(body['message'], code=body['code'], fix=cast(list[ReadCall], body['fix']))"):
        "Forward only a validated private verifier envelope.",
    ("task_verification.run", "refusal"): "Propagate the coded refusal returned by the private verifier.",
    ("task_runner.supervise", "subprocess.TimeoutExpired(command, timeout or 0, output, errors)"):
        "Preserve the subprocess timeout contract for the supervising caller.",
    ("task_runner.supervise", "subprocess.TimeoutExpired"): "Propagate the timeout after reaping the subprocess.",
    ("task_runner.worker_environment", "ValueError('not UTF-8 text')"):
        "The enclosing policy parser decides whether bad encoding warrants a refusal.",
    ("task_runner._finish", "Exception"): "Re-raise after marking a failed worker finalization.",
    ("task_runner._execute_owned", "OSError"): "Re-raise a job attachment failure after killing the child.",
    ("task_runner._active", "OSError('worker process tracking is unavailable')"):
        "Both process trackers being absent violates the supervisor invariant.",
    ("task_runner._stop", "OSError('worker process tracking is unavailable')"):
        "POSIX stop requires the process tracker initialized by the supervisor.",
    ("cli.<module>", "SystemExit(main())"): "Return the CLI exit status to the host process.",
}

# Reused codes require an explicit statement of which sites share the condition.
SHARED_CONDITIONS = {
    "assignment.active": ("ledger.Ledger.record_changes", "ledger.Ledger.ack"),
    "assignment.already_finished": ("ledger.Ledger.observe", "ledger.Ledger.finish"),
    "assignment.lease_invalid": ("ledger.Ledger.assign", "task_runner.run"),
    "assignment.not_found": ("ledger.Ledger._assignment", "cli.command_task"),
    "checks.command_required": ("ledger.validate_checks", "task_flow.parse_check"),
    "close.status_not_allowed": ("ledger.Ledger.close_task", "task_flow.close", "task_flow.close"),
    "close.target_invalid": ("ledger.Ledger.close_task", "task_flow.close"),
    "finding.severity_invalid": ("ledger.Ledger.add_finding", "ledger.Ledger.findings"),
    "finding.status_invalid": ("ledger.Ledger.update_finding", "ledger.Ledger.findings"),
    "ledger.snapshot_changed": ("ledger._sidecar_read_only_connection.check_budget", "ledger._sidecar_read_only_connection"),
    "project.unavailable": ("task_verification.project_state", "task_runner.run", "task_runner._execute_owned"),
    "run.launch_failed": ("task_runner.supervise_command", "task_runner._execute_owned"),
    "run.bundle_unavailable": ("cli.command_run_status", "cli.command_run_validate", "cli.command_run_handoff"),
    "verify.head_moved": ("task_verification.project_state", "task_verification.check_refusal"),
    "run.command_required": ("task_flow.command_do", "task_runner.run"),
    "sidecar.profile_required": ("cli.command_autopilot_review", "cli.command_autopilot_implement"),
    "task.not_found": ("ledger.Ledger.projection_rows", "ledger.Ledger._task", "cli.command_route_actor",
                       "cli.command_task_read", "cli.command_task"),
    "verify.command_required": ("task_flow.command_close", "task_flow.command_close", "task_verification.command"),
    "verify.evidence_unavailable": ("task_verification.command", "task_verification.run"),
    "verify.git_submodules": ("task_verification.project_state", "task_verification.project_state"),
    "verify.tree_changed": ("task_verification.project_state", "task_verification.project_state"),
    "verify.walk_limit": ("task_verification.project_state", "task_verification.project_state"),
}


def _owner(module, node, parents):
    scopes = []
    while (node := parents.get(node)) is not None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scopes.append(node.name)
    return f"{module}." + (".".join(reversed(scopes)) or "<module>")


def _raise_sites(module: str, tree: ast.Module):
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    raises = [node for node in parents if isinstance(node, ast.Raise)]
    error_types = {"LedgerError", "LedgerOpenError", "GateRefusedError", "CloseRefusedError", "ClaimConflictError"}
    for node in sorted(raises, key=lambda item: item.lineno):
        parent = parents.get(node)
        while parent is not None and not isinstance(parent, ast.ExceptHandler):
            parent = parents.get(parent)
        handler = ast.unparse(parent.type) if isinstance(parent, ast.ExceptHandler) and parent.type else "bare except"
        yield node, _owner(module, node, parents), ast.unparse(node.exc) if node.exc else handler, node.exc
    for node in parents:
        if (isinstance(node, ast.Call) and ast.unparse(node.func).split(".")[-1] in error_types
                and any(kw.arg == "code" for kw in node.keywords) and not isinstance(parents[node], ast.Raise)):
            yield node, _owner(module, node, parents), ast.unparse(node), node


# These legacy CLI command families forward diagnostics from outside the task ledger.
# The count pins the existing print-and-return sites, including ones sharing an owner.
LEGACY_STDERR_RETURNS = {
    "command_scan": 1, "command_scan_many": 3, "command_skill_view": 1, "command_skill_use": 1,
    "command_install_codex": 1, "command_route": 1, "command_contract_show": 1, "command_agent_bootstrap": 1,
    "command_autopilot_resume": 1, "command_policy_show": 1, "command_policy_validate": 1, "command_mcp_serve": 1,
    "command_release_claim": 1, "command_work": 1, "command_evidence_verify": 1, "command_audit_show": 1,
    "command_audit_export": 1, "command_provider_doctor": 1, "command_model_profile_show": 1,
    "command_model_profile_recommend": 1, "command_quality_report": 1, "command_quality_eval": 1,
    "command_hermes_intake": 1, "command_hermes_events_show": 1, "command_chat": 7, "command_sidecar_list": 1,
    "command_sidecar_run": 1, "command_memory": 1, "command_session_show": 2, "command_tool_show": 1,
    "command_learn_propose": 2, "command_learn_apply_memory": 7, "command_scheduler_create": 1,
}


def _stderr_returns(module, tree):
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    for node in ast.walk(tree):
        for _, block in ast.iter_fields(node):
            if not isinstance(block, list):
                continue
            for before, after in zip(block, block[1:], strict=False):
                if not isinstance(after, ast.Return) or after.value is None:
                    continue
                if isinstance(after.value, ast.Constant) and after.value.value == 0:
                    continue
                if not isinstance(before, ast.Expr) or not isinstance(call := before.value, ast.Call):
                    continue
                if ast.unparse(call.func) == "_message" or any(
                        kw.arg == "file" and ast.unparse(kw.value) == "sys.stderr" for kw in call.keywords):
                    yield _owner(module, before, parents), before.lineno


def test_stderr_and_nonzero_returns_use_the_coded_boundary():
    found = Counter()
    unexpected = []
    for module in MODULES:
        path = ROOT / "src" / "hipson" / f"{module}.py"
        for owner, line in _stderr_returns(module, ast.parse(path.read_text(encoding="utf-8"))):
            if owner.startswith("cli.") and owner.removeprefix("cli.") in LEGACY_STDERR_RETURNS:
                found[owner.removeprefix("cli.")] += 1
            else:
                unexpected.append(f"{path.name}:{line}: {owner}")
    assert not unexpected, "Print-and-return refusals missing a coded boundary:\n" + "\n".join(unexpected)
    assert found == LEGACY_STDERR_RETURNS


def test_every_refusal_has_a_documented_literal_code():
    coded = defaultdict(list)
    uncoded = Counter()
    message_codes = {}
    gate_codes = set()
    disallowed = []
    gated = []
    for module in MODULES:
        path = ROOT / "src" / "hipson" / f"{module}.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if module == "ledger":
            mapping, = (node.value for node in tree.body if isinstance(node, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id == "_GATE_REASON_CODES"
                                for target in node.targets))
            gate_codes.update(ast.literal_eval(mapping).values())
            gate_codes.add("close.refused")  # The _gate_reason_codes fallback for an unclassified reason.
        for node, owner, expression, call in _raise_sites(module, tree):
            code = next((kw.value for kw in call.keywords if kw.arg == "code"), None) if isinstance(call, ast.Call) else None
            # The gate classifies its ordered reasons before constructing the refusal.
            if (code is None and isinstance(call, ast.Call)
                    and (ast.unparse(call.func) == "close_refusal"
                         or (ast.unparse(call.func) == "GateRefusedError"
                             and any(kw.arg == "reason_codes" and ast.unparse(kw.value) == "refusal.reason_codes"
                                     for kw in call.keywords)))):
                gated.append(owner)
                continue
            if (owner, expression) in ALLOWLIST:
                uncoded[owner, expression] += 1
                continue
            if not (isinstance(code, ast.Constant) and isinstance(code.value, str)
                    and re.fullmatch(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*", code.value)):
                disallowed.append(f"{path.name}:{node.lineno}: {expression}")
                continue
            assert isinstance(call, ast.Call)
            assert ast.unparse(call.func).split(".")[-1] in {
                "LedgerError", "LedgerOpenError", "GateRefusedError", "CloseRefusedError", "ClaimConflictError",
            }, (owner, expression)
            coded[code.value].append(owner)
            if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
                message = call.args[0].value
                previous = message_codes.setdefault(message, code.value)
                assert previous == code.value, (owner, expression, previous)
    assert not disallowed, "Refusal raises missing a literal code=:\n" + "\n".join(disallowed)
    assert uncoded == Counter(ALLOWLIST.keys())
    assert all(reason and "\n" not in reason for reason in ALLOWLIST.values())
    duplicates = {code: Counter(owners) for code, owners in coded.items() if len(owners) > 1}
    assert duplicates == {code: Counter(owners) for code, owners in SHARED_CONDITIONS.items()}
    assert Counter(gated) == Counter({"ledger.Ledger.close_task": 1, "ledger.Ledger._set_status_locked": 2})

    doc = (ROOT / "docs" / "TASK_LEDGER.md").read_text(encoding="utf-8")
    table = doc.split("## Refusal codes\n", 1)[1].split("\n## ", 1)[0]
    documented = re.findall(r"^\| `([a-z0-9_.]+)` \|", table, flags=re.M)
    flow_codes = {"flow.spec_invalid", "flow.resume_required", "flow.resume_mismatch", "flow.conflict"}
    transport_codes = {"mcp.tool_not_in_profile"}  # Exercised over tools/call in test_ledger_mcp.py.
    assert Counter(documented) == Counter(set(coded) | gate_codes | flow_codes | transport_codes)
    assert VERIFIER_REFUSAL_CODES <= set(documented)


def test_coded_task_refusal_matches_across_mcp_and_cli(tmp_path):
    db = tmp_path / "task ledger.sqlite"
    with closing(ledger.Ledger(db)) as store:
        task = store.open_task(project=tmp_path, title="Find me", objective="Correct the ID")["id"]
    result = _call(tmp_path, "task.show", task="T-4040", ledger_db=db)
    message = "Task not found: T-4040 (list tasks with hipson task list or hipson task now --all)"
    assert result["isError"] is True
    assert result["content"][0] == {"type": "text", "text": message}
    assert len(result["content"]) == 2 and result["content"][1]["type"] == "text"
    body = json.loads(result["content"][1]["text"])
    assert body == {
        "error_schema": 1, "code": "task.not_found", "message": message,
        "fix": [
            {"tool": "task.list", "arguments": {}, "cli": f"hipson task list --db {shlex.quote(str(db))} --json"},
            {"tool": "task.now", "arguments": {"detail": "compact"},
             "cli": f"hipson task now --detail compact --db {shlex.quote(str(db))} --json"},
        ],
    }
    args = ("task", "show", "--db", str(db), "--task", "T-4040")
    assert run_cli_inprocess(*args) == (1, "", message + "\n" + "".join(f"fix: {call['cli']}\n" for call in body["fix"]))
    rc, stdout, stderr = run_cli_inprocess(*args, "--json")
    assert (rc, stdout, json.loads(stderr)) == (1, "", body)
    for call in body["fix"]:
        assert _call(tmp_path, call["tool"], ledger_db=db, **call["arguments"])["isError"] is False
        rc, stdout, stderr = run_cli_inprocess(*shlex.split(call["cli"])[1:])
        assert rc == 0 and not stderr and task in stdout
    assert run_cli_inprocess("task", "show", "--task", task, "--db", str(db), "--json")[0] == 0


def test_mcp_approval_and_unknown_assignment_errors(tmp_path):
    db = tmp_path / "ledger.sqlite"
    with closing(ledger.Ledger(db)):
        pass
    assert "error_schema 1" in mcp_server.MCP_INSTRUCTIONS
    result = _call(tmp_path, "task.ack", ledger_db=db, assignment="A-4040")
    assert result == {"isError": True, "content": [
        {"type": "text", "text": "task.ack requires approved=true for MCP write/gated execution"},
    ]}
    result = _call(tmp_path, "task.ack", ledger_db=db, assignment="A-4040", approved=True)
    assert result["content"][0]["text"] == "Assignment not found: A-4040"
    assert json.loads(result["content"][1]["text"]) == {
        "error_schema": 1, "code": "assignment.not_found", "message": "Assignment not found: A-4040",
        "fix": [read_call("task.now", {"detail": "compact", "all": True}, str(db))],
    }


def test_mcp_preserves_refusal_from_verifier_subprocess(tmp_path):
    db = tmp_path / "ledger.sqlite"
    with closing(ledger.Ledger(db)):
        pass
    result = _call(tmp_path, "task.verify", ledger_db=db, task="T-4040", approved=True,
                   command=[sys.executable, "-c", "pass"])
    assert result["isError"] is True
    body = json.loads(result["content"][1]["text"])
    assert body["code"] == "task.not_found"
    assert body["message"] == result["content"][0]["text"]
    assert [call["tool"] for call in body["fix"]] == ["task.list", "task.now"]


def test_call_builder_keeps_mcp_approval_out_of_cli():
    call = read_call("task.verify", {"task": "T-0001", "approved": True}, None)
    assert call["arguments"]["approved"] is True
    assert shlex.split(call["cli"]) == ["hipson", "task", "verify", "--task", "T-0001", "--json"]


# P1-3 acceptance inventory. Each test runs the returned call(s) before retrying the operation.
#  1. Stale checks                test_fix_stale_checks
#  2. Missing checks              test_fix_missing_checks
#  3. Failing checks              test_fix_failing_checks
#  4. Missing snapshot review     test_fix_missing_snapshot_review
#  5. Unacknowledged results      test_fix_unacknowledged_result
#  6. Open blocking findings      test_fix_open_blocking_finding
#  7. Task not found              test_coded_task_refusal_matches_across_mcp_and_cli
#  8. Invalid transition          test_fix_invalid_transition
#  9. Claim conflict              test_fix_claim_conflict
# 10. RW run without scope        test_fix_rw_run_without_scope
# 11. Project unavailable         test_fix_unavailable_project
# 12. HEAD moved                  test_fix_verify_on_moved_head
# 13. Invalid flow spec           test_fix_invalid_flow_spec
# 14. Resume spec mismatch        test_fix_flow_resume_mismatch
# 15. Unknown assignment on ack   test_fix_ack_unknown_assignment

PASS = [sys.executable, "-c", "pass"]
LEAD = "actual-lead"
REPORT = {"status": "completed", "summary": "Inspected snapshot", "files_changed": [],
          "commands": [], "findings": [], "risks": []}


@contextmanager
def _work(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    project, db = _project(tmp_path), tmp_path / "task ledger.sqlite"
    with closing(ledger.Ledger(db)) as store:
        yield project, db, store


def _task(work, *, checks=(PASS,), start=True, review="none"):
    project, _, store = work
    task = store.open_task(project=project, title="Candidate", objective="Fix test", lead=LEAD,
                           checks=checks, review_policy=review)["id"]
    if start:
        store.set_status(task, to="in_progress", actor=LEAD)
    return task


def _cli(cwd, db, *args):
    return run_cli(cwd, "task", "--db", str(db), "--json", *args)


def _refusal(proc, code, tools, rc=1):
    assert proc.returncode == rc, (proc.stdout, proc.stderr)
    body = json.loads(proc.stderr)
    assert body["error_schema"] == 1 and body["code"] == code
    assert [call["tool"] for call in body["fix"]] == tools
    return body["fix"]


def _follow(cwd, call, **fill):
    argv = []
    for word in shlex.split(call["cli"])[1:]:
        value = fill.get(word, word)
        argv.extend(value if isinstance(value, list) else [value])
    proc = run_cli(cwd, *argv)
    assert proc.returncode == 0, (argv, proc.stdout, proc.stderr)
    return proc.stdout


def _review_transition(project, db, task):
    return _cli(project, db, "status", "--task", task, "--to", "review", "--actor", LEAD)


def _finished(store, task, *, role="explorer", mode="ro"):
    item = store.assign(task, actor="worker", role=role, mode=mode)["id"]
    store.finish(item, result="passed", summary="delivery")
    return item


def test_fix_stale_checks(tmp_path):
    with _work(tmp_path) as work:
        project, db, store = work
        task = _task(work)
        assert _cli(project, db, "verify", "--task", task).returncode == 0
        store.ack(_finished(store, task, role="builder"))
        verify, = _refusal(_review_transition(project, db, task), "close.checks_stale", ["task.verify"])
        _follow(project, verify)
        assert _review_transition(project, db, task).returncode == 0


def test_fix_missing_checks(tmp_path):
    with _work(tmp_path) as work:
        project, db, _ = work
        task = _task(work)
        verify, = _refusal(_review_transition(project, db, task), "close.checks_missing", ["task.verify"])
        assert verify["arguments"] == {"task": task, "actor": LEAD, "approved": True}
        _follow(project, verify)
        assert _review_transition(project, db, task).returncode == 0


def test_fix_failing_checks(tmp_path):
    with _work(tmp_path) as work:
        project, db, _ = work
        check = project / "check.py"
        check.write_text("raise SystemExit(3)")
        task = _task(work, checks=([sys.executable, check.name],))
        read, verify = _refusal(_cli(project, db, "close", "--task", task), "close.checks_failed",
                                ["task.show", "task.verify"], rc=3)
        assert read["arguments"] == {"task": task, "section": "verification"}
        assert "check.py" in _follow(project, read)
        check.write_text("pass")
        _follow(project, verify)
        assert _cli(project, db, "close", "--task", task).returncode == 0


def test_fix_missing_snapshot_review(tmp_path):
    with _work(tmp_path) as work:
        project, db, store = work
        task = _task(work, review="required")
        result = _call(project, "task.close", ledger_db=db, task=task, approved=True)
        assert result["isError"] is False and json.loads(result["content"][0]["text"])["closed"] is False
        body = json.loads(result["content"][1]["text"])
        assert body["code"] == "close.review_required"
        verify, review = body["fix"]
        assert verify["tool"] == "task.verify"
        _follow(project, verify)
        assert review["arguments"] == {"task": task, "role": "reviewer", "mode": "ro"}
        report = tmp_path / "review outcome.json"
        command = [sys.executable, "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).write_text(sys.argv[2])",
                   str(report), json.dumps(REPORT)]
        _follow(project, review, REVIEWER="independent", RESULT_PATH=str(report), COMMAND=command)
        assignment, = store.show(task)["assignments"]
        assert assignment["result"]["status"] == "completed"
        assert _cli(project, db, "activity", "--assignment", assignment["id"]).returncode == 0
        assert _cli(project, db, "ack", "--assignment", assignment["id"]).returncode == 0
        assert _cli(project, db, "close", "--task", task).returncode == 0


def test_fix_unacknowledged_result(tmp_path):
    with _work(tmp_path) as work:
        project, db, store = work
        task = _task(work)
        assignment = _finished(store, task)
        read, ack = _refusal(_cli(project, db, "close", "--task", task), "close.results_unacknowledged",
                             ["task.activity", "task.ack"])
        assert assignment in _follow(project, read)
        assert ack["arguments"] == {"assignment": assignment, "actor": LEAD, "approved": True}
        _follow(project, ack)
        assert _cli(project, db, "close", "--task", task).returncode == 0


def test_fix_open_blocking_finding(tmp_path):
    with _work(tmp_path) as work:
        project, db, store = work
        task = _task(work)
        finding = store.add_finding(task, actor="reviewer", severity="blocking", title="Resolve a problem")["id"]
        read, update = _refusal(_cli(project, db, "close", "--task", task), "close.findings_open",
                                ["task.findings", "task.finding_update"])
        assert finding in _follow(project, read)
        assert update["arguments"] == {"finding": finding, "actor": LEAD, "approved": True}
        _follow(project, update, STATUS="fixed")
        assert _cli(project, db, "close", "--task", task).returncode == 0


def test_fix_invalid_transition(tmp_path):
    with _work(tmp_path) as work:
        project, db, _ = work
        task = _task(work, start=False)
        show, start = _refusal(_review_transition(project, db, task), "status.transition_not_allowed",
                               ["task.show", "task.status"])
        assert show["arguments"] == {"task": task, "detail": "compact"}
        assert task in _follow(project, show)
        # No ready-made call to a terminal status: cancelled is irreversible and stays the lead's decision.
        assert start["arguments"]["to"] == "in_progress"
        assert start["arguments"]["actor"] == LEAD
        _follow(project, start)
        assert _cli(project, db, "verify", "--task", task).returncode == 0
        assert _review_transition(project, db, task).returncode == 0


def _run_worker(project, db, task, *args, command=PASS, role="builder", mode="rw"):
    return _cli(project, db, "run", "--task", task, "--actor", "builder", "--role", role,
                "--mode", mode, *args, "--", *command)


def test_fix_claim_conflict(tmp_path):
    with _work(tmp_path) as work:
        project, db, store = work
        task, holder_task = _task(work), _task(work)
        holder = store.assign(holder_task, actor="other", role="builder", mode="rw", scope=["src"])["id"]
        show, reap = _refusal(_run_worker(project, db, task, "--scope", "src"), "claim.conflict",
                              ["task.show", "task.reap"], rc=75)
        assert show["arguments"] == {"task": holder_task, "section": "assignments"}
        assert holder in _follow(project, show)
        assert reap["arguments"] == {"task": holder_task, "dry_run": True, "approved": True}
        assert json.loads(_follow(project, reap)) == []
        preview = _call(project, reap["tool"], ledger_db=db, **reap["arguments"])
        assert preview["isError"] is False and json.loads(preview["content"][0]["text"]) == []
        store.finish(holder, result="passed", summary="The owner has finished its work")
        assert _run_worker(project, db, task, "--scope", "src").returncode == 0


def test_fix_rw_run_without_scope(tmp_path):
    with _work(tmp_path) as work:
        project, db, store = work
        task = _task(work)
        run, = _refusal(_run_worker(project, db, task, "--env", "minimal"), "scope.required", ["task.run"], rc=64)
        assert run["arguments"]["command"] == PASS and run["arguments"]["env"] == "minimal"
        _follow(project, run, PATH="src")
        assignment, = store.show(task)["assignments"]
        assert assignment["scope"] == ["src"] and assignment["status"] == "passed"


def test_fix_unavailable_project(tmp_path):
    with _work(tmp_path) as work:
        project, db, _ = work
        task = _task(work)
        offline = project.rename(tmp_path / "offline")
        show, help_call = _refusal(_run_worker(tmp_path, db, task, "--scope", "src"), "project.unavailable",
                                   ["task.show", "task.run"], rc=76)
        assert json.loads(_follow(tmp_path, show))["project"] == str(project)
        assert "--allow-project-mismatch" in _follow(tmp_path, help_call)
        offline.rename(project)
        assert _run_worker(project, db, task, "--scope", "src").returncode == 0


def test_fix_verify_on_moved_head(tmp_path):
    with _work(tmp_path) as work:
        project, db, store = work
        task = _task(work, checks=())
        # This check commits exactly once; its first run succeeds but cannot establish verification freshness.
        git = ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-qm", "Next"]
        command = [sys.executable, "-c", "import subprocess; from pathlib import Path; "
                   "p=Path('marker'); subprocess.run(" + repr(git) + ",check=True) if not p.exists() else None; p.touch()"]
        proc = _cli(project, db, "verify", "--task", task, "--", *command)
        verify, = _refusal(proc, "verify.head_moved", ["task.verify"], rc=0)
        assert not store.show(task)["verification"]["passing"]
        assert verify["arguments"]["command"] == command
        record = json.loads(_follow(project, verify))
        assert record["revision"] == _git(project, "rev-parse", "HEAD") and not record["state_changed"]
        assert _review_transition(project, db, task).returncode == 0


def test_fix_invalid_flow_spec(tmp_path):
    project, db = _repo(tmp_path), tmp_path / "ledger.sqlite"
    spec = _spec(tmp_path, project)
    valid = spec.read_text()
    spec.write_text("{}")
    check, = _refusal(_cli(project, db, "flow", "--spec", str(spec)), "flow.spec_invalid", ["task.flow"], rc=64)
    spec.write_text(valid)
    assert json.loads(_follow(project, check))["valid"]
    assert _cli(project, db, "flow", "--spec", str(spec)).returncode == 0


def test_fix_flow_resume_mismatch(tmp_path):
    project, db = _repo(tmp_path), tmp_path / "ledger.sqlite"
    spec = _spec(tmp_path, project, close_to="done")
    proc, result = _flow(project, db, spec)
    assert proc.returncode == 0, proc.stderr
    task = result["task"]
    changed = _spec(tmp_path, project, close_to="done", max_repairs=5)
    resume, = _refusal(_cli(project, db, "flow", "--spec", str(changed), "--resume", task),
                       "flow.resume_mismatch", ["task.flow"], rc=65)
    assert resume["arguments"] == {"resume": task}
    result = json.loads(_follow(project, resume, MATCHING_SPEC=str(spec)))
    assert result["task"] == task and result["status"] == "done"


def test_fix_ack_unknown_assignment(tmp_path):
    with _work(tmp_path) as work:
        project, db, store = work
        task = _task(work)
        assignment = _finished(store, task)
        now, = _refusal(_cli(project, db, "ack", "--assignment", "A-4444"), "assignment.not_found", ["task.now"])
        assert now["arguments"]["detail"] == "compact"
        store.close()  # The compact board requires a checkpointed database.
        assert assignment in _follow(project, now)
        assert _cli(project, db, "ack", "--assignment", assignment).returncode == 0


def test_runner_prelaunch_refusals_are_coded(tmp_path):
    cases = [
        ("review", "run.result_file_required", 64, "needs --result-file", ["task.run"]),
        ("wrong-project", "run.project_mismatch", 76, "project mismatch", ["task.show", "task.run"]),
        ("snapshot-role", "run.snapshot_requires_reviewer", 64, "--snapshot needs --role reviewer", []),
        ("project-check", "run.project_check_failed", 76, "could not verify task project", ["task.show", "task.run"]),
    ]
    for case, code, rc, message, tools in cases:
        with _work(tmp_path / case) as work, pytest.MonkeyPatch.context() as monkeypatch:
            _runner_prelaunch_refusal(work, tmp_path, monkeypatch, case, code, rc, message, tools)


def _runner_prelaunch_refusal(work, tmp_path, monkeypatch, case, code, rc, message, tools):
    project, db, store = work
    task = _task(work, review="required")
    cwd = tmp_path if case == "wrong-project" else project
    flags = ("--snapshot", str(tmp_path / "snapshot")) if case == "snapshot-role" else ()
    role = "reviewer" if case == "review" else "builder"
    if case == "project-check":
        monkeypatch.chdir(project)

        def unavailable(_):
            raise OSError("Cannot inspect cwd")

        monkeypatch.setattr(task_runner, "_project_check", unavailable)
        output = run_cli_inprocess("task", "run", "--task", task, "--db", str(db), "--json", "--actor", "worker",
                                   "--role", role, "--", *PASS)
        assert output[0] == rc and message in json.loads(output[2])["message"]
        assert json.loads(output[2])["code"] == code
        assert [call["tool"] for call in json.loads(output[2])["fix"]] == tools
    else:
        proc = _run_worker(cwd, db, task, *flags, role=role, mode="ro")
        _refusal(proc, code, tools, rc=rc)
        assert message in json.loads(proc.stderr)["message"]
    assert store.show(task)["assignments"] == []


def test_run_and_verify_bundle_refusals_are_coded(tmp_path):
    missing = str(tmp_path / "missing")
    for action in ("status", "validate", "handoff"):
        rc, stdout, stderr = run_cli_inprocess("run", action, "--run", missing, "--json")
        assert (rc, stdout, json.loads(stderr)["code"]) == (1, "", "run.bundle_unavailable")
    rc, stdout, stderr = run_cli_inprocess("verify", "run", "--work", missing, "--json")
    assert (rc, stdout, json.loads(stderr)["code"]) == (1, "", "verify.artifact_unavailable")


def test_launch_oserror_has_code_and_preserves_exit(tmp_path):
    for executable, rc in (("missing", 127), ("unlaunchable", 126)):
        with _work(tmp_path / executable) as work:
            _check_launch_error(work, executable, rc)


def _check_launch_error(work, executable, rc):
    project, db, store = work
    task = _task(work)
    command = project / executable
    if executable == "unlaunchable":
        command.mkdir()
    proc = _run_worker(project, db, task, "--scope", "src", command=[str(command)])
    assert proc.returncode == rc
    progress, refusal = proc.stderr.splitlines()
    assert f"{task} A-0001" in progress
    body = json.loads(refusal)
    assert (body["code"], body["fix"]) == ("run.launch_failed", [])
    assert "worker not started" in body["message"]
    assert store.show(task)["claims"] == []
    assert store.show(task)["assignments"][0]["status"] == "failed"


def test_coded_flow_ledger_errors_from_worker_context(tmp_path):
    with _work(tmp_path) as work, pytest.MonkeyPatch.context() as monkeypatch:
        project, db, _ = work
        spec = _spec(tmp_path, project)
        monkeypatch.setenv("HIPSON_ROLE", "worker")
        proc = _cli(project, db, "flow", "--spec", str(spec))
        _refusal(proc, "verify.worker_context", [])


def test_supervisor_only_accepts_whole_known_envelopes(tmp_path):
    with pytest.MonkeyPatch.context() as monkeypatch:
        call = read_call("task.now", {"detail": "compact"}, str(tmp_path / "ledger.sqlite"))
        body = {"error_schema": 1, "code": "task.not_found", "message": "Missing\nsecond line", "fix": [call]}
        valid = json.dumps(body)
        exc = task_verification._supervisor_refusal(valid + "\n")
        assert exc is not None and exc.error_payload() == body

        for malformed in [
            "unstructured diagnostic\n" + valid,
            json.dumps({**body, "error_schema": True}),
            json.dumps({**body, "code": "task.undocumented"}),
            json.dumps({**body, "fix": [{**call, "tool": "task.no_such_tool"}]}),
            json.dumps({**body, "fix": [{**call, "cli": ["hipson"]}]}),
            json.dumps({**body, "fix": [{**call, "arguments": {"detail": {"bad": True}}}]}),
            json.dumps({**body, "fix": [{"tool": call["tool"]}]}),
        ]:
            assert task_verification._supervisor_refusal(malformed) is None
            monkeypatch.setattr(task_runner, "supervise", lambda *args, diagnostic=malformed, **kwargs: (1, b"", diagnostic.encode()))
            with pytest.raises(ledger.LedgerError) as exc:
                task_verification.run(tmp_path / "ledger.sqlite", "T-9999", PASS)
            assert exc.value.code is None and exc.value.fix == []

        monkeypatch.setattr(task_runner, "supervise", lambda *args, **kwargs: (1, b"", b""))
        with pytest.raises(ledger.LedgerError) as exc:
            task_verification.run(tmp_path / "ledger.sqlite", "T-9999", PASS)
        assert exc.value.code == "verify.evidence_unavailable"


def test_error_message_bound_matches_cli_and_mcp_with_control_characters(tmp_path):
    db = tmp_path / "ledger.sqlite"
    with closing(ledger.Ledger(db)):
        pass
    task = "unknown\x00with\ufeffmarkers\ufffe" + "x" * 70000
    args = ("task", "show", "--task", task, "--db", str(db))
    _, _, stderr = run_cli_inprocess(*args, "--json")
    body = json.loads(stderr)
    prefix = "Task not found: unknownwithmarkers"
    expected = prefix + "x" * (65536 - len(prefix))
    assert body["message"] == expected
    _, _, human = run_cli_inprocess(*args)
    assert human.splitlines()[0] == expected
    result = _call(tmp_path, "task.show", ledger_db=db, task=task)
    assert result["content"][0]["text"] == expected
    assert json.loads(result["content"][1]["text"]) == body


def test_lifecycle_actor_flags_default_lead_and_keep_explicit_gates(tmp_path):
    with _work(tmp_path) as work:
        project, db, store = work
        task = _task(work)
        assignment = _finished(store, task)
        explicit = _finished(store, task)
        assert _cli(project, db, "ack", "--assignment", assignment).returncode == 0
        assert _cli(project, db, "ack", "--assignment", explicit, "--actor", "delegate").returncode == 0
        third = _finished(store, task)
        assert _call(project, "task.ack", ledger_db=db, assignment=third, actor="mcp-delegate", approved=True)["isError"] is False
        assert [event["actor"] for event in store.events(task) if event["type"] == "acknowledged"] == [
            LEAD, "delegate", "mcp-delegate",
        ]
        proc = _cli(project, db, "status", "--task", task, "--to", "review")
        assert proc.returncode == 2 and "--actor" in proc.stderr
        assert _cli(project, db, "close", "--task", task).returncode == 0
        assert store.verifications(task)[-1]["actor"] == LEAD
        assert store.events(task)[-1]["actor"] == LEAD


def test_timeout_result_and_refusal_suggest_double_limit(tmp_path):
    for surface in ("verify", "close", "mcp"):
        with _work(tmp_path / surface) as work:
            _check_timeout(work, surface)


def _check_timeout(work, surface):
    project, db, _ = work
    check = project / "check.py"
    check.write_text("import time; time.sleep(5)")
    task = _task(work, checks=([sys.executable, check.name],))
    timeout = 1 if surface == "mcp" else 0.4
    if surface == "mcp":
        result = _call(project, "task.verify", ledger_db=db, task=task, timeout=timeout, approved=True)
        assert result["isError"] is False
        records = json.loads(result["content"][0]["text"])["records"]
        verify, = json.loads(result["content"][1]["text"])["fix"]
    else:
        proc = _cli(project, db, surface, "--task", task, "--timeout", "0.4")
        verify, = _refusal(proc, "verify.check_timeout", ["task.verify"], rc=124)
        payload = json.loads(proc.stdout)
        records = payload["records"] if surface == "close" else payload
    record, = records
    assert record["exit_code"] == 124 and record["timeout_seconds"] == timeout
    assert f"{timeout:g}s per-check timeout" in record["timeout_hint"]
    assert verify["arguments"] == {"task": task, "actor": LEAD, "approved": True, "timeout": 2 * timeout}
    assert f"--timeout {2 * timeout}" in verify["cli"]
    check.write_text("pass")
    _follow(project, verify)
    if surface == "mcp":
        retry = _call(project, verify["tool"], ledger_db=db, **verify["arguments"])
        assert retry["isError"] is False


def test_exit_124_before_deadline_has_no_timeout_hint(tmp_path):
    with _work(tmp_path) as work:
        project, db, _ = work
        task = _task(work, checks=([sys.executable, "-c", "raise SystemExit(124)"],))
        proc = _cli(project, db, "verify", "--task", task, "--timeout", "10")
        _refusal(proc, "close.checks_failed", ["task.show", "task.verify"], rc=124)
        record, = json.loads(proc.stdout)
        assert "timeout_seconds" not in record and "timeout_hint" not in record


def test_fix_flow_retry_without_resume_points_at_the_task_and_resume(tmp_path):
    """S4b review: the resume_required fix must address --retry without --resume, not just re-check the spec."""
    project, db = _repo(tmp_path), tmp_path / "ledger.sqlite"
    spec = _spec(tmp_path, project, close_to="done")
    proc, result = _flow(project, db, spec)
    assert proc.returncode == 0, proc.stderr
    task = result["task"]
    now, resume = _refusal(_cli(project, db, "flow", "--spec", str(spec), "--retry"),
                           "flow.resume_required", ["task.now", "task.flow"], rc=64)
    assert task in _follow(project, now)
    assert resume["arguments"] == {"spec": str(spec), "retry": True}
    assert shlex.split(resume["cli"])[-3:-1] == ["--resume", "TASK"] or "--resume TASK" in resume["cli"]
    assert json.loads(_follow(project, resume, TASK=task))["task"] == task


def test_verify_fix_never_replays_a_redacted_or_truncated_command(tmp_path):
    """S4b review: a stored command is redacted per argument and cut at 4,096 characters; replaying it runs other argv."""
    from hipson.task_calls import verify_call

    db = tmp_path / "ledger.sqlite"
    plain = verify_call("T-0001", LEAD, db, command=["pytest", "-q"], explicit=True)
    assert plain["arguments"]["command"] == ["pytest", "-q"] and "COMMAND" not in plain["cli"]
    from hipson.redaction import redact_text, redact_value

    raw_secret = "--token=" + "a" * 24
    assert redact_text(raw_secret) != raw_secret  # A raw secret the error body would redact later.
    split = (["curl", "Bearer", "abcdefghijklmnop"], ["run", "token", "=", "abc123secret"],
             ["run", "password:", "hunter2hunter2"])
    for command in split:
        assert all(redact_text(arg) == arg for arg in command)  # Only the joined line is redacted.
        refusal = task_verification.timeout_refusal(
            {"task": "T-0001", "timeout_seconds": 3}, db, command=command, lead=LEAD)
        assert redact_value(refusal.error_payload()["fix"]) == refusal.fix
    for command in (["run", "--token=[REDACTED]"], ["run", "x" * 4096], ["run", raw_secret],
                    ["run", "﻿bom"], ["run", "[unsupported encoded observation]"], *split):
        call = verify_call("T-0001", LEAD, db, command=command, explicit=True)
        assert "command" not in call["arguments"], call
        assert shlex.split(call["cli"])[-2:] == ["--", "COMMAND"]


def test_timeout_retry_keeps_the_command_template_when_the_stored_command_is_not_replayable(tmp_path):
    from hipson.task_calls import gate_fix_calls

    view = {"id": "T-0001", "lead": LEAD, "checks": [],
            "verification": {"passing": False, "latest": {"command": ["run", "--token=[REDACTED]"], "timeout_seconds": 5}}}
    show, retry = gate_fix_calls([("failing verification: exit 124", "close.checks_failed")], view,
                                 tmp_path / "ledger.sqlite")
    assert show["arguments"]["section"] == "verification"
    assert retry["tool"] == "task.verify" and retry["arguments"]["timeout"] == 10
    assert "command" not in retry["arguments"] and shlex.split(retry["cli"])[-2:] == ["--", "COMMAND"]


def test_head_moved_code_comes_from_the_recorded_revision_not_a_path_name(tmp_path):
    """S4b review: a file named 'HEAD before=...' must not make a refusal claim that HEAD moved."""
    from hipson import task_verification

    db = tmp_path / "ledger.sqlite"
    with closing(ledger.Ledger(db)) as store:
        task = store.open_task(project=tmp_path, title="Candidate", objective="Head", lead=LEAD)["id"]
    forged = {"task": task, "error": None, "changes": ["HEAD before=1 after=2: missing before=file:1"]}
    assert task_verification.check_refusal(forged, db, lead=LEAD) is None
    moved = task_verification.check_refusal({**forged, "changes": ["HEAD before=abc after=def"]}, db, lead=LEAD)
    assert moved is not None and moved.code == "verify.head_moved"
    assert moved.fix[0]["tool"] == "task.verify"


def test_verification_refusal_retries_keep_the_command_template(tmp_path):
    """timeout_refusal and check_refusal were given an explicit argv; a dropped argv must stay a template."""
    from hipson import task_verification

    db = tmp_path / "ledger.sqlite"
    with closing(ledger.Ledger(db)) as store:
        task = store.open_task(project=tmp_path, title="Candidate", objective="Retry", lead=LEAD)["id"]
    hidden = ["run", "--token=[REDACTED]"]
    timeout = task_verification.timeout_refusal({"task": task, "timeout_seconds": 3}, db, command=hidden, lead=LEAD)
    moved = task_verification.check_refusal({"task": task, "error": None, "changes": ["HEAD before=a after=b"]},
                                            db, command=hidden, lead=LEAD)
    for refusal in (timeout, moved):
        retry, = refusal.fix
        assert "command" not in retry["arguments"] and shlex.split(retry["cli"])[-2:] == ["--", "COMMAND"]
    kept = task_verification.check_refusal({"task": task, "error": None, "changes": ["HEAD before=a after=b"]},
                                           db, command=["pytest", "-q"], lead=LEAD)
    assert kept.fix[0]["arguments"]["command"] == ["pytest", "-q"]
