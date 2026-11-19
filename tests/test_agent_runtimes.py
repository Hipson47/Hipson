"""Native runtime adapters against fake CLIs: inventory levels, profiles, normalization and failure classes."""

import json
import os
import stat
import sys
from pathlib import Path
from unittest import SkipTest
from unittest.mock import patch

import pytest

from hipson import agent_runtimes as runtimes
from hipson import ledger
from tests.test_flow_driver import PY, _flow, _repo, _spec, _view

RESULT = {"status": "completed", "summary": "done", "files_changed": [], "commands": [], "findings": [], "risks": []}
HELP = {
    "claude": "--print --output-format --json-schema --resume --session-id --model --effort --permission-mode "
              "--setting-sources --strict-mcp-config --plugin-dir --mcp-config --agents",
    "codex": "exec resume plugin mcp --model --config --sandbox --output-schema --output-last-message --ephemeral "
             "--ignore-user-config --ignore-rules --json",
    "agy": "--print --output-format --json-schema --conversation --model --effort --mode plugin mcp",
}


def _fake(bin_dir, name, body):
    path = bin_dir / name
    path.write_text(f"#!{sys.executable}\nimport json, os, sys\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def _fake_cli(tmp_path, name, run_body, *, help_text=None, auth=None, plugins="[]"):
    if os.name == "nt":
        raise SkipTest("fake runtime executables use POSIX shebangs")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _fake(bin_dir, name, f"""
args = sys.argv[1:]
if args == ['--version']:
    print('{name} 9.9.9'); sys.exit(0)
if '--help' in args:
    print({json.dumps(help_text if help_text is not None else HELP[name])}); sys.exit(0)
if args[:2] in (['auth', 'status'], ['login', 'status']):
    print({json.dumps(auth if auth is not None else ('{"loggedIn": true, "authMethod": "claude.ai"}' if name == 'claude' else 'Logged in using ChatGPT'))}); sys.exit(0)
if args[:2] == ['plugin', 'list'] or args[:3] == ['plugin', 'list', '--json']:
    print({json.dumps(plugins)}); sys.exit(0)
if args[:2] == ['mcp', 'list']:
    print('No MCP servers configured.'); sys.exit(0)
{run_body}
""")
    return bin_dir


def _env(bin_dir):
    return {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}


def test_inventory_separates_documented_configured_enabled_and_unsupported(tmp_path):
    bin_dir = _fake_cli(tmp_path, "claude", "sys.exit(0)", help_text=HELP["claude"].replace("--agents", ""),
                        plugins='[{"id": "a@m", "enabled": true}, {"id": "b@m", "enabled": false}]')
    home = tmp_path / "home"
    (home / ".claude" / "skills" / "deploy").mkdir(parents=True)
    (home / ".claude" / "skills" / "deploy" / "SKILL.md").write_text("---\nname: deploy\n---\n")
    project = tmp_path / "project"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "settings.json").write_text('{"hooks": {"PreToolUse": []}}')
    (project / ".mcp.json").write_text('{"mcpServers": {"local": {"command": "x"}}}')
    with patch.dict(os.environ, {**_env(bin_dir), "HOME": str(home), "HIPSON_HOME": str(tmp_path / "hh")}), \
            patch.object(Path, "home", return_value=home):
        report = runtimes.inventory("claude", project=project)
    assert report["installed"] and report["version"] == "claude 9.9.9"
    assert report["auth"] == {"state": "logged_in", "method": "claude.ai", "evidence": "auth status"}
    assert report["capabilities"]["structured_output"]["state"] == "documented"
    assert report["capabilities"]["subagents"] == {"state": "unsupported", "evidence": "--help lacks --agents"}
    configured = report["configured"]
    assert configured["plugins"] == [{"id": "a@m", "enabled": True}, {"id": "b@m", "enabled": False}]
    assert configured["skills"] == ["deploy"] and configured["mcp_servers"] == ["local"]
    assert configured["hooks"] == ["PreToolUse"] and len(configured["workspace_executables"]) == 2
    assert not report["verified"]


def test_inventory_reports_missing_runtimes_and_logged_out_auth(tmp_path):
    with patch.object(runtimes.shutil, "which", return_value=None):
        missing = runtimes.inventory("codex", project=tmp_path)
    assert not missing["installed"] and missing["limitations"] == ["codex is not on PATH"]
    bin_dir = _fake_cli(tmp_path, "codex", "sys.exit(0)", auth="Not logged in")
    with patch.dict(os.environ, {**_env(bin_dir), "CODEX_HOME": str(tmp_path / "ch")}):
        out = runtimes.inventory("codex", project=tmp_path)
    assert out["auth"]["state"] == "unknown" and out["capabilities"]["controlled_isolation"]["state"] == "documented"
    agy_dir = _fake_cli(tmp_path, "agy", "sys.exit(0)")
    with patch.dict(os.environ, _env(agy_dir)):
        agy = runtimes.inventory("agy", project=tmp_path)
    assert agy["capabilities"]["controlled_isolation"] == {"state": "unsupported", "evidence": "no documented flag"}
    assert agy["auth"]["state"] == "unknown" and "smoke run is the evidence" in agy["limitations"][0]


def test_native_profile_adds_no_isolation_flags_and_controlled_profile_does(tmp_path):
    schema, last = tmp_path / "s.json", tmp_path / "l.json"
    schema.write_text("{}")
    common = dict(prompt="p", schema_path=schema, last_path=last, cwd=str(tmp_path), session="sid")
    isolation = {"--setting-sources", "--strict-mcp-config", "--no-session-persistence", "--ephemeral",
                 "--ignore-user-config", "--ignore-rules", "--bare", "--safe-mode"}
    for name in ("claude", "codex", "agy"):
        argv, _ = runtimes.build_command(name, profile="native", mode="rw", **common)
        assert not isolation & set(argv), (name, argv)
        assert not {"--dangerously-skip-permissions", "--dangerously-bypass-approvals-and-sandbox"} & set(argv)
    claude, stdin = runtimes.build_command("claude", profile="controlled", mode="ro", **common)
    assert stdin == "p" and {"--setting-sources", "--strict-mcp-config"} <= set(claude)
    assert claude[claude.index("--permission-mode") + 1] == "plan" and claude[claude.index("--session-id") + 1] == "sid"
    codex, _ = runtimes.build_command("codex", profile="controlled", mode="ro", **common)
    assert {"--ephemeral", "--ignore-user-config", "--ignore-rules"} <= set(codex)
    assert codex[codex.index("--sandbox") + 1] == "read-only" and codex[-1] == "-"
    resumed, _ = runtimes.build_command("codex", profile="native", mode="rw", resume="thread-1", **common)
    assert resumed[1:3] == ["exec", "resume"] and resumed[-2:] == ["thread-1", "-"]
    with pytest.raises(runtimes.RuntimeFailure, match="unsupported: agy documents no configuration-free run"):
        runtimes.build_command("agy", profile="controlled", mode="ro", **common)
    agy, stdin = runtimes.build_command("agy", profile="native", mode="ro", resume="conv-1", **common)
    assert stdin is None and agy[agy.index("--mode") + 1] == "plan" and agy[agy.index("--conversation") + 1] == "conv-1"


def test_session_ids_are_bound_to_task_and_assignment():
    assert runtimes.session_id("T-0001", "A-0001") == runtimes.session_id("T-0001", "A-0001")
    assert runtimes.session_id("T-0001", "A-0001") != runtimes.session_id("T-0002", "A-0001")


def test_normalize_accepts_only_valid_structured_results_and_classifies_failures(tmp_path):
    last = tmp_path / "last.json"
    ok = json.dumps({"type": "result", "subtype": "success", "is_error": False, "session_id": "s1",
                     "usage": {"output_tokens": 3}, "modelUsage": {"m": {}}, "structured_output": RESULT})
    report, meta = runtimes.normalize("claude", 0, ok, "", last)
    assert report["status"] == "completed" and meta["session"] == "s1" and meta["models"] == ["m"]
    cases = [
        ("claude", 0, "not json", "", "malformed_output"),
        ("claude", 1, "", "Error: You've hit your usage limit", "rate_limited"),
        ("claude", 0, json.dumps({"subtype": "error_during_execution", "is_error": True, "result": "Not logged in"}),
         "", "auth_required"),
        ("claude", 0, json.dumps({"subtype": "success", "is_error": False,
                                  "structured_output": {**RESULT, "status": "done"}}), "", "malformed_output"),
        ("claude", 0, json.dumps({"subtype": "success", "is_error": False}), "", "malformed_output"),
        ("codex", 1, '{"type": "thread.started", "thread_id": "t1"}', "ERROR: You've hit your usage limit",
         "rate_limited"),
        ("codex", 0, "", "", "malformed_output"),
        ("agy", 0, "", "jetski: a tool required the \"command\" permission that headless mode cannot prompt for, "
                       "so it was auto-denied", "permission_denied"),
        ("agy", 0, json.dumps({"status": "ERROR"}), "", "provider_error"),
        ("agy", 0, "garbage", "", "malformed_output"),
    ]
    for name, code, stdout, stderr, kind in cases:
        with pytest.raises(runtimes.RuntimeFailure) as error:
            runtimes.normalize(name, code, stdout, stderr, last)
        assert error.value.kind == kind, (name, stdout, stderr, error.value)
    last.write_text(json.dumps(RESULT))
    events = '{"type": "thread.started", "thread_id": "t9"}\n{"type": "turn.completed", "usage": {"input_tokens": 5}}'
    report, meta = runtimes.normalize("codex", 0, events, "", last)
    assert meta["session"] == "t9" and meta["usage"] == {"input_tokens": 5}
    agy = json.dumps({"status": "SUCCESS", "conversation_id": "c1", "structured_output": RESULT, "usage": {"x": 1}})
    assert runtimes.normalize("agy", 0, agy, "", last)[1]["session"] == "c1"
    assert runtimes.classify(124, "") == "timeout"


def test_exec_refuses_outside_a_recorded_assignment(tmp_path):
    proc = __import__("tests.test_hipson_helpers", fromlist=["run_cli"]).run_cli(
        tmp_path, "agent", "exec", "--runtime", "claude", env={"HIPSON_ROLE": "", "HIPSON_ASSIGNMENT": ""})
    assert proc.returncode == runtimes.EXIT["unsupported"] and "worker command of hipson task run" in proc.stderr


CLAUDE_WORKER = r"""
prompt = sys.stdin.read()
schema = json.loads(args[args.index('--json-schema') + 1])
assert schema['required'] == ['status', 'summary', 'files_changed', 'commands', 'findings', 'risks']
log = os.environ['FAKE_LOG']
with open(log, 'a') as handle:
    handle.write(json.dumps({'argv': args, 'prompt_has_rules': 'Worker rules:' in prompt}) + '\n')
mode = args[args.index('--permission-mode') + 1]
result = {'status': 'completed', 'summary': mode, 'files_changed': [], 'commands': [], 'findings': [], 'risks': []}
if mode == 'acceptEdits':
    open('src/parser/pairs.py', 'w').write("def parse_pair(text):\n    return tuple(part.strip() for part in text.split(':'))\n")
    result['files_changed'] = [{'path': 'src/parser/pairs.py', 'change': 'trim'}]
session = args[args.index('--resume') + 1] if '--resume' in args else args[args.index('--session-id') + 1]
if os.environ.get('FAKE_MODE') == 'limit':
    print('Error: usage limit reached', file=sys.stderr); sys.exit(1)
print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False, 'session_id': session,
                  'usage': {'output_tokens': 7}, 'modelUsage': {'fake-model': {}}, 'structured_output': result}))
"""


def test_flow_runs_native_runtime_workers_end_to_end_with_session_and_usage_evidence(tmp_path):
    bin_dir = _fake_cli(tmp_path, "claude", CLAUDE_WORKER)
    project, db = _repo(tmp_path), tmp_path / "ledger.sqlite"
    hipson = [PY, "-m", "hipson.cli", "agent", "exec", "--runtime", "claude"]
    spec = json.loads(_spec(tmp_path, project).read_text())
    spec["builder"]["command"] = [*hipson, "--role", "builder"]
    spec["reviewer"]["command"] = [*hipson, "--role", "reviewer"]
    path = tmp_path / "native.json"
    path.write_text(json.dumps(spec))
    env = {**_env(bin_dir), "FAKE_LOG": str(tmp_path / "calls.jsonl"), "HIPSON_HOME": str(tmp_path / "hh")}
    proc, result = _flow(project, db, path, env=env)
    assert proc.returncode == 0 and result["state"] == "accepted", (proc.stderr, result)
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert [call["argv"][call["argv"].index("--permission-mode") + 1] for call in calls] == ["acceptEdits", "plan"]
    assert all(call["prompt_has_rules"] for call in calls)
    assert not any("--setting-sources" in call["argv"] for call in calls)  # Native: the user's configuration.
    # The only added permission is the lead's declared check, exactly.
    grants = [call["argv"][i + 1] for call in calls for i, arg in enumerate(call["argv"]) if arg == "--allowedTools"]
    assert grants == [f"Bash({PY} tests/acceptance.py)"] * 2
    flows = db.parent / ".ledger.sqlite.flows" / "T-0001"
    meta = json.loads((flows / "build-1.result.json.meta.json").read_text())
    assert meta["outcome"] == "ok" and meta["session"] == runtimes.session_id("T-0001", "A-0001")
    assert meta["usage"] == {"output_tokens": 7} and meta["brief_bytes"] > 0 and meta["role"] == "builder"
    verified = json.loads((tmp_path / "hh" / "runtimes" / "claude.verified.json").read_text())
    assert verified["structured_output"]["assignment"] == "A-0002"
    view, _ = _view(db)
    assert view["review"]["passing"] and view["status"] == "review"


def test_a_rate_limited_runtime_is_a_classified_worker_failure(tmp_path):
    bin_dir = _fake_cli(tmp_path, "claude", CLAUDE_WORKER)
    project, db = _repo(tmp_path), tmp_path / "ledger.sqlite"
    spec = json.loads(_spec(tmp_path, project).read_text())
    spec["builder"]["command"] = [PY, "-m", "hipson.cli", "agent", "exec", "--runtime", "claude"]
    path = tmp_path / "limited.json"
    path.write_text(json.dumps(spec))
    env = {**_env(bin_dir), "FAKE_LOG": str(tmp_path / "calls.jsonl"), "FAKE_MODE": "limit",
           "HIPSON_HOME": str(tmp_path / "hh")}
    proc, result = _flow(project, db, path, env=env)
    assert proc.returncode == 3 and result["state"] == "worker_failed"
    view, _ = _view(db)
    assert view["assignments"][0]["failure_journal"]["exit_code"] == runtimes.EXIT["rate_limited"]
    meta = json.loads((db.parent / ".ledger.sqlite.flows" / "T-0001" / "build-1.result.json.meta.json").read_text())
    assert meta["outcome"] == "rate_limited"
    assert ledger.TASK_RESULT_SCHEMA["required"] == runtimes.result_schema()["required"]



def test_check_grants_are_exact_patterns_only(tmp_path):
    schema = tmp_path / "s.json"
    schema.write_text("{}")
    argv, _ = runtimes.build_command("claude", profile="native", mode="rw", prompt="p", schema_path=schema,
                                     last_path=tmp_path / "l", cwd=str(tmp_path),
                                     checks=[["pytest", "-q"], ["pytest", "tests/*"], ["python3", "-c", "x)"]])
    grants = [argv[i + 1] for i, arg in enumerate(argv) if arg == "--allowedTools"]
    assert grants == ["Bash(pytest -q)"]
