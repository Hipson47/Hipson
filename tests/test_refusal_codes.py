"""Inventory the explicit refusals and check their wire format."""

from __future__ import annotations

import ast
import json
import re
import shlex
import sys
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path

from hipson import ledger, mcp_server
from hipson.task_calls import read_call
from tests.test_hipson_helpers import run_cli_inprocess
from tests.test_ledger_mcp import _call

ROOT = Path(__file__).resolve().parents[1]
MODULES = ("ledger", "task_flow", "task_verification", "task_runner", "cli")

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


def _raise_sites(module: str, tree: ast.Module):
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    for node in sorted((item for item in parents if isinstance(item, ast.Raise)), key=lambda item: item.lineno):
        scopes = []
        handler = None
        parent = parents.get(node)
        while parent is not None:
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                scopes.append(parent.name)
            if isinstance(parent, ast.ExceptHandler) and handler is None:
                handler = ast.unparse(parent.type) if parent.type else "bare except"
            parent = parents.get(parent)
        owner = ".".join(reversed(scopes)) or "<module>"
        yield node, f"{module}.{owner}", ast.unparse(node.exc) if node.exc else handler


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
        for node, owner, expression in _raise_sites(module, tree):
            call = node.exc
            code = next((kw.value for kw in call.keywords if kw.arg == "code"), None) if isinstance(call, ast.Call) else None
            # A gate refusal is never empty, so its first classified reason is its code.
            if (code is None and isinstance(call, ast.Call)
                    and ast.unparse(call.func).split(".")[-1] in {"GateRefusedError", "CloseRefusedError"}
                    and any(kw.arg == "reason_codes" and isinstance(kw.value, ast.Call)
                            and ast.unparse(kw.value.func) == "_gate_reason_codes" for kw in call.keywords)):
                gated.append(owner)
                continue
            if code is None and (owner, expression) in ALLOWLIST:
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
    assert Counter(documented) == Counter(set(coded) | gate_codes)


def test_coded_task_refusal_matches_across_mcp_and_cli(tmp_path):
    db = tmp_path / "task ledger.sqlite"
    with closing(ledger.Ledger(db)):
        pass
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
        assert rc == 0 and not stderr and json.loads(stdout) is not None


def test_mcp_uncoded_and_empty_fix_errors(tmp_path):
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
        "error_schema": 1, "code": "assignment.not_found", "message": "Assignment not found: A-4040", "fix": [],
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
