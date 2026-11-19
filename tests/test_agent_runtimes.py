"""Native runtime adapters against fake CLIs: inventory levels, profiles, normalization and failure classes."""

import argparse
import errno
import json
import os
import stat
import sys
from contextlib import closing
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
    source = f"import json, os, sys\n{body}\n"
    if os.name == "nt":
        # Windows has no shebangs: a .cmd shim, like npm-installed runtimes, runs the script with this Python.
        (bin_dir / f"{name}.py").write_text(source)
        path = bin_dir / f"{name}.cmd"
        path.write_text(f'@"{sys.executable}" "%~dp0{name}.py" %*\n')
        return path
    path = bin_dir / name
    path.write_text(f"#!{sys.executable}\n{source}")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def _fake_cli(tmp_path, name, run_body, *, help_text=None, auth=None, plugins="[]"):
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


def test_session_ids_never_repeat_across_runs():
    # Task/assignment ids restart in every ledger; an id derived from them collided live across two ledgers.
    assert len({runtimes.session_id() for _ in range(100)}) == 100


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


@pytest.fixture
def worker(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    db = tmp_path / "ledger.sqlite"
    with closing(ledger.Ledger(db)) as store:
        task = store.open_task(project=project, title="Review", objective="Review project")["id"]
        assignment = store.assign(task, actor="reviewer", role="reviewer", mode="ro")["id"]
    brief, result = tmp_path / "brief.md", tmp_path / "first.result.json"
    brief.write_text("review it", encoding="utf-8")
    for key, value in {"HIPSON_ROLE": "worker", "HIPSON_TASK": task, "HIPSON_ASSIGNMENT": assignment,
                       "HIPSON_RUN_DB": str(db), "HIPSON_FLOW_BRIEF": str(brief), "HIPSON_FLOW_RESULT": str(result),
                       "HIPSON_HOME": str(tmp_path / "hh"), "FAKE_LOG": str(tmp_path / "calls.jsonl")}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("HIPSON_PROJECT", raising=False)
    monkeypatch.delenv("FAKE_MODE", raising=False)
    monkeypatch.chdir(project)
    parser = argparse.ArgumentParser()
    runtimes.configure(parser.add_subparsers())

    def run(runtime, *options):
        args = parser.parse_args(["exec", "--runtime", runtime, *options])
        return args.func(args)

    return brief, result, run


@pytest.mark.parametrize("filename", ["first.result.schema.json", "first.result.json", "first.result.json.meta.json"])
def test_exec_refuses_symlinked_runtime_state(worker, tmp_path, capsys, filename):
    _, result, run = worker
    target = tmp_path / "untouched.txt"
    target.write_text("keep this", encoding="utf-8")
    link = result.with_name(filename)
    try:
        link.symlink_to(target)
    except OSError:
        if sys.platform != "win32":
            raise
        raise SkipTest('Windows symlinks require Developer Mode or SeCreateSymbolicLinkPrivilege') from None
    with patch.dict(os.environ, _env(_fake_cli(tmp_path, "claude", CLAUDE_WORKER))):
        code = run("claude")
    assert link.is_symlink() and target.read_text(encoding="utf-8") == "keep this"
    assert code == runtimes.EXIT["unsupported"] and not (tmp_path / "calls.jsonl").exists()
    detail = capsys.readouterr().err
    assert filename in detail and "symlink" in detail and "remove" in detail


def test_exec_records_launch_oserror_as_provider_error(worker, tmp_path, capsys):
    _, result, run = worker
    launch_error = OSError(errno.E2BIG, "Argument list too long")
    with (patch.dict(os.environ, _env(_fake_cli(tmp_path, "agy", "sys.exit(1)"))),
          patch.object(runtimes.subprocess, "run", side_effect=launch_error)):
        assert run("agy") == runtimes.EXIT["provider_error"]
    meta = json.loads(result.with_name(result.name + ".meta.json").read_text())
    assert meta["outcome"] == "provider_error" and "Argument list too long" in meta["detail"]
    assert "could not launch agy" in capsys.readouterr().err
    assert not result.exists()


def test_exec_preflight_stops_a_logged_out_runtime_before_launch(worker, tmp_path, capsys):
    _, result, run = worker
    launched = tmp_path / "launched"
    body = f"open({str(launched)!r}, 'w').write('x'); sys.exit(0)"
    with patch.dict(os.environ, _env(_fake_cli(tmp_path, "claude", body, auth='{"loggedIn": false}'))):
        assert run("claude") == runtimes.EXIT["auth_required"]
    assert not launched.exists() and not result.exists()
    meta = json.loads(result.with_name(result.name + ".meta.json").read_text())
    assert meta["outcome"] == "auth_required" and meta["preflight"] == {"auth": "logged_out"}
    assert "claude reports it is not logged in (auth status)" in capsys.readouterr().err


def test_exec_refuses_oversize_agy_prompt_before_launch(worker, tmp_path, capsys):
    brief, result, run = worker
    brief.write_text("é" * 49_950, encoding="utf-8")  # The worker rules push the full prompt over 100,000 bytes.
    with (patch.dict(os.environ, _env(_fake_cli(tmp_path, "agy", "sys.exit(1)"))),
          patch.object(runtimes.subprocess, "run", side_effect=AssertionError("unexpected launch")) as launch):
        assert run("agy") == runtimes.EXIT["unsupported"]
    launch.assert_not_called()
    meta = json.loads(result.with_name(result.name + ".meta.json").read_text())
    assert meta["outcome"] == "unsupported" and "100000 byte" in meta["detail"]
    assert "100000 byte argv limit" in capsys.readouterr().err


@pytest.mark.parametrize("filename", ["settings.json", "settings.local.json"])
def test_controlled_claude_refuses_task_project_settings_in_snapshot(worker, tmp_path, monkeypatch, capsys, filename):
    _, result, run = worker
    project = Path.cwd()
    settings = project / ".claude" / filename
    settings.parent.mkdir()
    settings.write_text("{}", encoding="utf-8")
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    monkeypatch.setenv("HIPSON_PROJECT", str(project))
    monkeypatch.setenv("HIPSON_REVIEW_SNAPSHOT", str(snapshot))
    monkeypatch.chdir(snapshot)
    with patch.dict(os.environ, _env(_fake_cli(tmp_path, "claude", CLAUDE_WORKER))):
        assert run("claude", "--profile", "controlled") == runtimes.EXIT["unsupported"]
        assert not (tmp_path / "calls.jsonl").exists()
        meta = json.loads(result.with_name(result.name + ".meta.json").read_text())
        assert meta["outcome"] == "unsupported" and str(settings) in meta["detail"]
        assert "use profile native or remove" in meta["detail"]
        assert run("claude", "--profile", "native") == 0
    assert meta["detail"] in capsys.readouterr().err


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
    # The only added permission is the lead's declared check, exactly, and only for the writer.
    grants = [call["argv"][i + 1] for call in calls for i, arg in enumerate(call["argv"]) if arg == "--allowedTools"]
    assert grants == [f"Bash({PY} tests/acceptance.py)"]
    flows = db.parent / ".ledger.sqlite.flows" / "T-0001"
    meta = json.loads((flows / "build-1.result.json.meta.json").read_text())
    assert meta["outcome"] == "ok" and meta["session"] == meta["session_requested"] and len(meta["session"]) == 36
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



def test_mode_and_grants_come_from_the_recorded_assignment_not_flags_or_environment(tmp_path):
    # Review (Codex) M8: a reviewer assignment runs read-only whatever --role defaults to, and an ambient
    # HIPSON_FLOW_CHECKS cannot widen the grant.
    bin_dir = _fake_cli(tmp_path, "claude", CLAUDE_WORKER)
    project, db = _repo(tmp_path), tmp_path / "ledger.sqlite"
    run_cli = __import__("tests.test_hipson_helpers", fromlist=["run_cli"]).run_cli
    run_cli(project, "task", "open", "--db", str(db), "--project", str(project), "--title", "t", "--objective", "o",
            "--check", "true", "--start")
    brief, result = tmp_path / "brief.md", tmp_path / "flows" / "r.result.json"
    result.parent.mkdir()
    brief.write_text("review it")
    env = {**_env(bin_dir), "FAKE_LOG": str(tmp_path / "calls.jsonl"), "HIPSON_HOME": str(tmp_path / "hh"),
           "HIPSON_FLOW_CHECKS": json.dumps([["rm", "-rf", "/"]])}
    agent = [PY, "-m", "hipson.cli", "agent", "exec", "--runtime", "claude", "--brief", str(brief), "--result", str(result)]
    proc = run_cli(project, "task", "run", "--db", str(db), "--task", "T-0001", "--actor", "rev", "--role", "reviewer",
                   "--mode", "ro", "--", *agent, env=env)
    assert proc.returncode == 0, proc.stderr
    call = json.loads((tmp_path / "calls.jsonl").read_text().splitlines()[-1])
    assert call["argv"][call["argv"].index("--permission-mode") + 1] == "plan" and "--allowedTools" not in call["argv"]
    proc = run_cli(project, "task", "run", "--db", str(db), "--task", "T-0001", "--actor", "rev2", "--role", "reviewer",
                   "--mode", "ro", "--", *agent, "--role", "builder", env=env)
    assert proc.returncode != 0
    meta = json.loads(result.with_name(result.name + ".meta.json").read_text())
    assert meta["outcome"] == "unsupported" and "is a reviewer assignment" in meta["detail"]


def test_failed_or_stale_runtime_output_is_never_success(tmp_path):
    # Review (Codex) M9.
    last = tmp_path / "last.json"
    envelope = json.dumps({"subtype": "success", "is_error": False, "session_id": "s", "structured_output": RESULT,
                           "permission_denials": [{"tool_name": "Bash", "tool_input": {"command": "curl -H 'Authorization: Bearer x'"}}]})
    with pytest.raises(runtimes.RuntimeFailure) as error:
        runtimes.normalize("claude", 124, envelope, "", last)
    assert error.value.kind == "timeout"
    _, meta = runtimes.normalize("claude", 0, envelope, "", last)
    assert meta["permission_denials"] == ["Bash"]  # Tool names only, never the denied input.
    with pytest.raises(runtimes.RuntimeFailure, match="wrote no --output-last-message file"):
        runtimes.normalize("codex", 0, "", "", last)


def test_session_continuation_requires_a_passed_assignment_of_the_same_task_and_role(tmp_path, monkeypatch):
    result = tmp_path / "build-2.result.json"
    # _ledger_path() resolves the ledger, so the recorded paths are resolved ones (C:\... on Windows).
    ledger_a = str((tmp_path / "a.sqlite").resolve())
    with closing(ledger.Ledger(ledger_a)) as store:
        task = store.open_task(project=tmp_path, title="Build", objective="Build project")["id"]
        other_task = store.open_task(project=tmp_path, title="Other", objective="Other task")["id"]

        def assign(task_id=task, role="builder", status="passed"):
            assignment = store.assign(task_id, actor="test", role=role, mode="ro")["id"]
            if status != "active":
                store.finish(assignment, result=status, summary=status)
            return assignment

        passed, active, failed = assign(), assign(status="active"), assign(status="failed")
        other_task_assignment, reviewer = assign(other_task), assign(role="reviewer")

    def meta(name, timestamp=200, **values):
        data = {"outcome": "ok", "runtime": "claude", "task": task, "role": "builder", "ledger": ledger_a,
                "project": str(tmp_path), "session": f"session-{name}", "assignment": passed, **values}
        path = tmp_path / f"{name}.result.json.meta.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        os.utime(path, (timestamp, timestamp))
        return path

    good = meta("good", timestamp=100)
    meta("other-ledger", ledger=str(tmp_path / "other.sqlite"))
    meta("other-project", project="/elsewhere")
    meta("other-runtime", runtime="codex")
    meta("other-role", role="reviewer")
    meta("other-task", task=other_task)
    meta("bad-session", session="--dangerous-flag")
    meta("missing-assignment", assignment=None)
    meta("unknown-assignment", assignment="A-9999")
    meta("active-assignment", assignment=active)
    meta("failed-assignment", assignment=failed)
    meta("wrong-task-assignment", assignment=other_task_assignment)
    meta("wrong-role-assignment", assignment=reviewer)
    (tmp_path / "directory.result.json.meta.json").mkdir()
    monkeypatch.setenv("HIPSON_RUN_DB", ledger_a)
    monkeypatch.delenv("HIPSON_PROJECT", raising=False)
    monkeypatch.chdir(tmp_path)
    assert runtimes._previous_session(result, "claude", task, "builder") == "session-good"
    good.unlink()
    assert runtimes._previous_session(result, "claude", task, "builder") is None
    (tmp_path / "array.result.json.meta.json").write_text("[1]", encoding="utf-8")
    assert runtimes._previous_session(result, "claude", task, "builder") is None


def test_session_continuation_ignores_symlinked_meta(worker, tmp_path):
    _, result, _ = worker
    task, db, assignment = (os.environ[key] for key in ("HIPSON_TASK", "HIPSON_RUN_DB", "HIPSON_ASSIGNMENT"))
    with closing(ledger.Ledger(db)) as store:
        store.finish(assignment, result="passed", summary="passed")
    target = tmp_path / "external.json"
    target.write_text(json.dumps({"outcome": "ok", "runtime": "claude", "task": task, "role": "reviewer",
                                 "ledger": str(Path(db).resolve()), "project": os.getcwd(), "assignment": assignment,
                                 "session": "session-from-link"}), encoding="utf-8")
    try:
        (tmp_path / "linked.result.json.meta.json").symlink_to(target)
    except OSError:
        if sys.platform != "win32":
            raise
        raise SkipTest('Windows symlinks require Developer Mode or SeCreateSymbolicLinkPrivilege') from None
    assert runtimes._previous_session(result, "claude", task, "reviewer") is None


def test_exec_resumes_reviewer_session_across_snapshots_using_task_project(worker, tmp_path, monkeypatch, capsys):
    _, result, run = worker
    project = os.getcwd()
    task, db, assignment = (os.environ[key] for key in ("HIPSON_TASK", "HIPSON_RUN_DB", "HIPSON_ASSIGNMENT"))
    bin_dir = _fake_cli(tmp_path, "codex", f"""
assert os.path.samefile(os.getcwd(), os.environ['HIPSON_REVIEW_SNAPSHOT'])
if args[1] != 'resume':
    assert args[args.index('--cd') + 1] == os.getcwd()
    session = 'thread-snapshot'
else:
    session = args[-2]
with open(args[args.index('--output-last-message') + 1], 'w', encoding='utf-8') as last:
    json.dump({RESULT!r}, last)
print(json.dumps({{'type': 'thread.started', 'thread_id': session}}))
""")
    monkeypatch.setenv("PATH", _env(bin_dir)["PATH"])
    monkeypatch.setenv("HIPSON_PROJECT", project)
    first_snapshot = tmp_path / "snapshot-1"
    first_snapshot.mkdir()
    monkeypatch.setenv("HIPSON_REVIEW_SNAPSHOT", str(first_snapshot))
    monkeypatch.chdir(first_snapshot)
    assert run("codex") == 0
    first = json.loads(result.with_name(result.name + ".meta.json").read_text())
    assert first["project"] == project and first["session"] == "thread-snapshot"

    with closing(ledger.Ledger(db)) as store:
        store.finish(assignment, result="passed", summary="passed")
        next_assignment = store.assign(task, actor="reviewer", role="reviewer", mode="ro")["id"]
    second_snapshot = tmp_path / "snapshot-2"
    second_snapshot.mkdir()
    monkeypatch.setenv("HIPSON_ASSIGNMENT", next_assignment)
    monkeypatch.setenv("HIPSON_REVIEW_SNAPSHOT", str(second_snapshot))
    monkeypatch.chdir(second_snapshot)
    result = result.with_name("second.result.json")
    assert run("codex", "--continue-session", "--result", str(result)) == 0
    second = json.loads(result.with_name(result.name + ".meta.json").read_text())
    assert second["project"] == project and second["resumed"]
    assert second["session_requested"] == second["session"] == first["session"]
    assert capsys.readouterr().err.count("codex native session=thread-snapshot") == 2


def test_grants_skip_environment_assignment_words_and_verified_needs_the_same_version(tmp_path):
    assert not runtimes._exact_pattern(["MODE=test", "echo", "demo"]) and runtimes._exact_pattern(["pytest", "-q"])
    bin_dir = _fake_cli(tmp_path, "agy", "sys.exit(0)")
    home = tmp_path / "hh"
    (home / "runtimes").mkdir(parents=True)
    (home / "runtimes" / "agy.verified.json").write_text(json.dumps({"structured_output": {"version": "agy 1.0.0"}}))
    with patch.dict(os.environ, {**_env(bin_dir), "HIPSON_HOME": str(home)}):
        stale = runtimes.inventory("agy", project=tmp_path)
        assert stale["capabilities"]["structured_output"]["state"] == "documented"
        (home / "runtimes" / "agy.verified.json").write_text(json.dumps({"structured_output": {"version": "agy 9.9.9"}}))
        assert runtimes.inventory("agy", project=tmp_path)["capabilities"]["structured_output"]["state"] == "verified"



def test_verified_needs_a_known_version_on_both_sides(tmp_path):
    home = tmp_path / "hh"
    (home / "runtimes").mkdir(parents=True)
    (home / "runtimes" / "agy.verified.json").write_text(json.dumps({"structured_output": {"version": None}}))
    bin_dir = _fake_cli(tmp_path, "agy", "sys.exit(0)")
    script = bin_dir / ("agy.py" if os.name == "nt" else "agy")  # The script itself, not the Windows .cmd shim.
    script.write_text(script.read_text().replace("print('agy 9.9.9'); sys.exit(0)", "sys.exit(1)"))
    with patch.dict(os.environ, {**_env(bin_dir), "HIPSON_HOME": str(home)}):
        report = runtimes.inventory("agy", project=tmp_path)
    assert report["version"] is None and report["capabilities"]["structured_output"]["state"] == "documented"


def test_session_lookup_resolves_the_ledger_fallback(tmp_path):
    result = tmp_path / "x.result.json"
    (tmp_path / "a.result.json.meta.json").write_text(json.dumps({
        "outcome": "ok", "runtime": "claude", "task": "T-0001", "role": "builder", "ledger": None,
        "project": str(tmp_path), "session": "session-from-unknown-ledger"}))
    with (patch.dict(os.environ, {"HIPSON_LEDGER": str(tmp_path / "l.sqlite")}, clear=False),
          patch.object(os, "getcwd", return_value=str(tmp_path))):
        os.environ.pop("HIPSON_RUN_DB", None)
        assert runtimes._previous_session(result, "claude", "T-0001", "builder") is None
        assert runtimes._ledger_path() == str((tmp_path / "l.sqlite").resolve())


def test_resume_and_check_grants_follow_each_runtimes_documented_interface(tmp_path):
    schema, last = tmp_path / "s.json", tmp_path / "l.json"
    schema.write_text("{}")
    common = dict(profile="native", prompt="p", schema_path=schema, last_path=last, cwd=str(tmp_path))
    # codex exec resume refuses --sandbox; the same mode goes through sandbox_mode, never a wider one.
    for mode, sandbox in (("ro", "read-only"), ("rw", "workspace-write")):
        fresh, _ = runtimes.build_command("codex", mode=mode, **common)
        resumed, _ = runtimes.build_command("codex", mode=mode, resume="0123456789abcdef", **common)
        assert fresh[fresh.index("--sandbox") + 1] == sandbox and "--sandbox" not in resumed
        assert resumed[:3][1:] == ["exec", "resume"] and f'sandbox_mode="{sandbox}"' in resumed
        assert resumed[resumed.index(f'sandbox_mode="{sandbox}"') - 1] == "--config"
    # agy: a session-only settings layer grants exactly the declared checks; none without checks.
    argv, _ = runtimes.build_command("agy", mode="rw", checks=[["python3", "tests/check.py"], ["rm", "-rf", "*"]],
                                     **common)
    assert json.loads(argv[argv.index("--settings") + 1]) == {"permissions": {"allow": ["command(python3 tests/check.py)"]}}
    argv, _ = runtimes.build_command("agy", mode="ro", **common)
    assert "--settings" not in argv
