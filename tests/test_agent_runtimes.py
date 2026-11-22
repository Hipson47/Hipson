"""Native runtime adapters against fake CLIs: inventory levels, profiles, normalization and failure classes."""

import argparse
import errno
import gc
import io
import json
import os
import shlex
import stat
import subprocess
import sys
from contextlib import ExitStack, chdir, closing, contextmanager, redirect_stderr
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
    "agy": "--print --output-format --json-schema --conversation --model --effort --mode --settings plugin mcp",
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
    handle.write(json.dumps({'argv': args, 'prompt_has_rules': 'Worker rules:' in prompt,
                            'prompt_has_reviewer_rules': (
                                'List only defects, risks or gaps that need a decision in findings.' in prompt
                                and 'Confirmations that something works belong in the summary.' in prompt)}) + '\n')
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


@contextmanager
def _worker(tmp_path, *, checks=()):
    project = tmp_path / "project"
    project.mkdir(parents=True)
    db = tmp_path / "ledger.sqlite"
    with closing(ledger.Ledger(db)) as store:
        task = store.open_task(project=project, title="Worker test", objective="Work on project", checks=list(checks))["id"]
        role, mode = ("builder", "rw") if checks else ("reviewer", "ro")
        assignment = store.assign(task, actor=role, role=role, mode=mode, scope=["."] if checks else [])["id"]
    brief, result = tmp_path / "brief.md", tmp_path / "first.result.json"
    brief.write_text("review it", encoding="utf-8")
    env = {"HIPSON_ROLE": "worker", "HIPSON_TASK": task, "HIPSON_ASSIGNMENT": assignment,
           "HIPSON_RUN_DB": str(db), "HIPSON_FLOW_BRIEF": str(brief), "HIPSON_FLOW_RESULT": str(result),
           "HIPSON_HOME": str(tmp_path / "hh"), "FAKE_LOG": str(tmp_path / "calls.jsonl")}
    parser = argparse.ArgumentParser()
    runtimes.configure(parser.add_subparsers())

    def run(runtime, *options):
        args = parser.parse_args(["exec", "--runtime", runtime, *options])
        return args.func(args)

    with patch.dict(os.environ, env), chdir(project):
        os.environ.pop("HIPSON_PROJECT", None)
        os.environ.pop("FAKE_MODE", None)
        yield brief, result, run


@pytest.mark.parametrize("case", ["fresh", "default_home", "missing_id", "invalid_id", "stale", "no_rollout"])
def test_exec_codex_records_only_models_from_a_matching_recent_rollout(tmp_path, monkeypatch, case):
    thread_id = "fedcba98-7654-4321-8abc-0123456789ab"
    emitted = None if case == "missing_id" else "*" if case == "invalid_id" else thread_id
    other_id = "abcdef01-2345-4321-8abc-0123456789ab"
    contexts = [
        {"type": "session_meta", "payload": {"model": "not-a-turn"}},
        {"type": "turn_context", "model": "wrong-level", "payload": {"model": "model-z"}},
        {"type": "turn_context", "payload": {"model": "model-a"}},
        {"type": "turn_context", "payload": {"model": "model-z"}},
        {"type": "turn_context", "payload": {"model": 123}},
    ]
    fake = f"""
from pathlib import Path

sessions = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex') / 'sessions' / '2026' / '11' / '22'
sessions.mkdir(parents=True)
other = sessions / 'rollout-2026-11-22T12-00-00-{other_id}.jsonl'
other.write_text(json.dumps({{'type': 'turn_context', 'payload': {{'model': 'wrong-thread'}}}}), encoding='utf-8')
if {case != 'no_rollout'!r}:
    matched = sessions / 'rollout-2026-11-22T12-00-00-{thread_id}.jsonl'
    matched.write_text('malformed line\\n' + '\\n'.join(map(json.dumps, {contexts!r})), encoding='utf-8')
    if {case == 'stale'!r}:
        os.utime(matched, (1, 1))
if {emitted is not None!r}:
    print(json.dumps({{'type': 'thread.started', 'thread_id': {emitted!r}}}))
print(json.dumps({{'type': 'turn.completed', 'model': 'not-from-rollout'}}))
Path(args[args.index('--output-last-message') + 1]).write_text(json.dumps({RESULT!r}), encoding='utf-8')
"""
    if case == "default_home":
        monkeypatch.delenv("CODEX_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
    else:
        monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    with _worker(tmp_path) as (_, result, run), patch.dict(os.environ, _env(_fake_cli(tmp_path, "codex", fake))):
        assert run("codex") == 0
    meta = json.loads(result.with_name(result.name + ".meta.json").read_text())
    assert meta["outcome"] == "ok"
    assert meta["models"] == (["model-a", "model-z"] if case in ("fresh", "default_home") else None)


def test_codex_rollout_model_read_is_byte_bounded(tmp_path, monkeypatch):
    thread = "fedcba98-7654-4321-8abc-0123456789ab"
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    first = b'{"type":"turn_context","payload":{"model":"first"}}\n'
    second = b'{"type":"turn_context","payload":{"model":"over-budget"}}\n'
    (sessions / f"rollout-test-{thread}.jsonl").write_bytes(first + second)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr(runtimes, "MAX_CODEX_ROLLOUT_BYTES", len(first) + len(second) - 2)
    assert runtimes._codex_rollout_models(thread, 0) == ["first"]


def test_agy_permission_failure_reports_first_redacted_line_and_headless_hint(tmp_path):
    secret = "ghp_" + "a" * 36
    denial = f"jetski: command with {secret}\x1b[0m\twas auto-denied " + "z" * 400
    fake = f"""
print(json.dumps({{'status': 'SUCCESS'}}))
print('unrelated diagnostic', file=sys.stderr)
print({denial!r}, file=sys.stderr)
print('second auto-denied diagnostic', file=sys.stderr)
"""
    with _worker(tmp_path) as (_, result, run), patch.dict(os.environ, _env(_fake_cli(tmp_path, "agy", fake))):
        assert run("agy") == runtimes.EXIT["permission_denied"]
    meta = json.loads(result.with_name(result.name + ".meta.json").read_text())
    assert meta["outcome"] == "permission_denied"
    original, line, hint = meta["detail"].split("; ", 2)
    assert original == "no structured_output in the runtime result"
    assert line.startswith(r"jetski: command with [REDACTED]\u001b[0m\twas auto-denied ") and len(line) <= 300
    assert hint == ("Antigravity auto-denies commands outside its allow list in headless mode; "
                    "grant the command in agy's settings or use another builder runtime.")
    assert secret not in json.dumps(meta)


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_other_runtime_permission_failures_include_the_denial_without_agy_hint(tmp_path, runtime):
    denial = "Command permission was denied"
    with pytest.raises(runtimes.RuntimeFailure) as error:
        runtimes.normalize(runtime, 7, denial, "other permission denied", tmp_path / "last")
    assert error.value.kind == "permission_denied"
    assert error.value.detail.endswith(f"; {denial}") and "Antigravity" not in error.value.detail


def test_exec_refuses_symlinked_runtime_state(tmp_path):
    filenames = ("first.result.schema.json", "first.result.json", "first.result.json.meta.json")
    for index, filename in enumerate(filenames):
        case_dir = tmp_path / str(index)
        stderr = io.StringIO()
        with _worker(case_dir) as (_, result, run):
            target = case_dir / "untouched.txt"
            target.write_text("keep this", encoding="utf-8")
            link = result.with_name(filename)
            try:
                link.symlink_to(target)
            except OSError:
                if sys.platform != "win32":
                    raise
                raise SkipTest('Windows symlinks require Developer Mode or SeCreateSymbolicLinkPrivilege') from None
            with patch.dict(os.environ, _env(_fake_cli(case_dir, "claude", CLAUDE_WORKER))), redirect_stderr(stderr):
                code = run("claude")
            assert link.is_symlink() and target.read_text(encoding="utf-8") == "keep this", filename
            assert code == runtimes.EXIT["unsupported"] and not (case_dir / "calls.jsonl").exists(), filename
            detail = stderr.getvalue()
            assert filename in detail and "symlink" in detail and "remove" in detail, (filename, detail)


def test_exec_records_launch_oserror_as_provider_error(tmp_path):
    launch_error = OSError(errno.E2BIG, "Argument list too long")
    stderr = io.StringIO()
    with (_worker(tmp_path) as (_, result, run), redirect_stderr(stderr),
          patch.dict(os.environ, _env(_fake_cli(tmp_path, "agy", "sys.exit(1)"))),
          patch.object(runtimes.subprocess, "run", side_effect=launch_error)):
        assert run("agy") == runtimes.EXIT["provider_error"]
    meta = json.loads(result.with_name(result.name + ".meta.json").read_text())
    assert meta["outcome"] == "provider_error" and "Argument list too long" in meta["detail"]
    assert "could not launch agy" in stderr.getvalue()
    assert not result.exists()


def test_t0155_agy_unavailable_model_reports_redacted_cli_diagnostic(tmp_path):
    # T-0155 and the native probe have no verbatim rejection. The installed CLI
    # contains both message fragments below; the available models are fixture data.
    secret = "ghp_" + "a1B2" * 9
    message = ("invalid model selection (--model \"missing-model\" --effort \"\"): "
               "model missing-model is not recognized as a known model or custom model in settings\n"
               f"Available models: example-pro, example-flash (token={secret})")
    cases = [(1, '{"status":"ERROR"}', message),
             (0, json.dumps({"status": "ERROR", "error": message.split(": ", 1)[1]}), "")]
    for index, (exit_code, stdout, diagnostic) in enumerate(cases):
        case_dir = tmp_path / str(index)
        stderr = io.StringIO()
        process = subprocess.CompletedProcess([], exit_code, stdout, diagnostic)
        with (_worker(case_dir) as (_, result, run), redirect_stderr(stderr),
              patch.object(runtimes.shutil, "which", return_value=sys.executable),
              patch.object(runtimes.subprocess, "run", return_value=process)):
            assert run("agy", "--model", "missing-model") == 64, stderr.getvalue()
        meta = json.loads(result.with_name(result.name + ".meta.json").read_text())
        assert meta["outcome"] == "model_unavailable"
        for output in (stderr.getvalue(), json.dumps(meta)):
            assert "missing-model" in output and "example-pro, example-flash" in output
            assert "[REDACTED]" in output and secret not in output
        assert not result.exists()


def test_agy_rate_limit_and_auth_take_precedence_over_quoted_model_rejection(tmp_path):
    quote = 'The test quotes "invalid model selection".'
    cases = [("429", "rate_limited"), ("401 Unauthorized", "auth_required"),
             ("429 and 401 Unauthorized", "rate_limited")]
    for evidence, kind in cases:
        stdout = json.dumps({"status": "ERROR", "result": quote})
        with pytest.raises(runtimes.RuntimeFailure) as failure:
            runtimes.normalize("agy", 1, stdout, quote + "\n" + evidence, tmp_path / "last")
        assert failure.value.kind == kind


def test_agy_model_rejection_uses_only_error_channels_and_bounds_the_diagnostic(tmp_path):
    phrase = "invalid model selection"
    last = tmp_path / "last"
    for stdout in (phrase, json.dumps({"status": "ERROR", "result": phrase})):
        with pytest.raises(runtimes.RuntimeFailure) as failure:
            runtimes.normalize("agy", 1, stdout, "", last)
        assert failure.value.kind == "provider_error"

    secret = "ghp_" + "a1B2" * 9
    diagnostic = f"{phrase}: token={secret}\n" + "example-model " * 100
    for payload, stderr in (({"status": "ERROR", "message": diagnostic}, ""),
                            ({"status": "ERROR", "error": {"message": diagnostic}}, ""),
                            ({"status": "ERROR"}, diagnostic)):
        with pytest.raises(runtimes.RuntimeFailure) as failure:
            runtimes.normalize("agy", 1, json.dumps(payload), stderr, last)
        assert failure.value.kind == "model_unavailable"
        detail = failure.value.detail
        assert detail.startswith(phrase) and "[REDACTED]" in detail and secret not in detail
        assert len(detail) == 600


def _agy_user_settings(home, content):
    settings = home / ".gemini" / "antigravity-cli" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(content, encoding="utf-8")
    return settings


def test_t0160_agy_session_layer_merges_exact_checks_and_preserves_user_denies(tmp_path):
    user_allow = ["read_file(src/**)", "command(pytest -q)"]
    user_deny = ["read_file(.env)", "command(python3 private_check.py)", "read_file(.env)"]
    original = json.dumps({"permissions": {"allow": user_allow, "deny": user_deny}, "toolPermission": "ask"})
    settings = _agy_user_settings(tmp_path, original)
    schema = tmp_path / "schema.json"
    schema.write_text("{}")
    options = dict(profile="native", prompt="p", schema_path=schema, last_path=tmp_path / "last", cwd=str(tmp_path))
    checks = [["pytest", "-q"], ["python3", "tests/check.py"], ["pytest", "tests/*"]]
    with patch.object(Path, "home", return_value=tmp_path), ExitStack() as files:
        argv, _ = runtimes.build_command("agy", mode="rw", checks=checks, settings_files=files, **options)
        review, _ = runtimes.build_command("agy", mode="ro", checks=checks, **options)
        no_checks, _ = runtimes.build_command("agy", mode="rw", **options)
        assert settings.read_text(encoding="utf-8") == original
        layer = json.loads(Path(argv[argv.index("--settings") + 1]).read_text(encoding="utf-8"))
    assert layer == {"permissions": {"allow": [*user_allow, "command(python3 tests/check.py)"], "deny": user_deny}}
    assert all("--settings" not in command for command in (review, no_checks))


def test_agy_session_layer_passes_overlapping_check_allow_and_user_deny(tmp_path):
    deny = "command(git status)"
    _agy_user_settings(tmp_path, json.dumps({"permissions": {"allow": ["command(git *)"], "deny": [deny]}}))
    schema = tmp_path / "schema.json"
    schema.write_text("{}")
    with patch.object(Path, "home", return_value=tmp_path), ExitStack() as files:
        argv, _ = runtimes.build_command("agy", profile="native", mode="rw", prompt="p", schema_path=schema,
                                        last_path=tmp_path / "last", cwd=str(tmp_path), checks=[["git", "status"]],
                                        settings_files=files)
        layer = json.loads(Path(argv[argv.index("--settings") + 1]).read_text(encoding="utf-8"))
    assert layer == {"permissions": {"allow": ["command(git *)", deny], "deny": [deny]}}


def test_agy_omits_session_settings_when_user_permissions_cannot_be_merged(tmp_path):
    settings_dir = tmp_path / "home"
    settings = _agy_user_settings(settings_dir, "")
    schema = tmp_path / "schema.json"
    schema.write_text("{}")
    bad_settings = [[], {"permissions": []}, {"permissions": {"allow": "command(x)"}},
                    {"permissions": {"allow": ["command(x)", 7]}}, {"permissions": {"allow": None}},
                    {"permissions": {"allow": ["command(git *)"], "deny": "command(git push *)"}},
                    {"permissions": {"deny": ["command(x)", 7]}}, {"permissions": {"deny": None}}]
    for content in ["{unreadable", *(json.dumps(value) for value in bad_settings)]:
        settings.write_text(content, encoding="utf-8")
        with patch.object(Path, "home", return_value=settings_dir), ExitStack() as files:
            argv, _ = runtimes.build_command("agy", profile="native", mode="rw", prompt="p", schema_path=schema,
                                            last_path=tmp_path / "last", cwd=str(tmp_path), checks=[["pytest", "-q"]],
                                            settings_files=files)
        assert "--settings" not in argv, content
        assert settings.read_text(encoding="utf-8") == content


def test_agy_records_omitted_check_grants_in_meta_and_failure_detail(tmp_path):
    malformed = tmp_path / "malformed"
    _agy_user_settings(malformed, '{"permissions":{"deny":[7]}}')
    unreadable = tmp_path / "unreadable"
    (unreadable / ".gemini" / "antigravity-cli" / "settings.json").mkdir(parents=True)
    for settings_home, reason in ((malformed, "permissions.deny must be a list of strings"),
                                  (unreadable, "user settings could not be read")):
        for outcome in ("ok", "provider_error"):
            def launch(argv, *, outcome=outcome, **kwargs):
                assert "--settings" not in argv
                if outcome == "provider_error":
                    return subprocess.CompletedProcess(argv, 1, "", "Run failed")
                payload = {"status": "SUCCESS", "structured_output": RESULT}
                return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

            stderr = io.StringIO()
            with (_worker(settings_home / outcome, checks=[["git", "status"]]) as (_, result, run),
                  redirect_stderr(stderr), patch.object(Path, "home", return_value=settings_home),
                  patch.object(runtimes.shutil, "which", return_value=sys.executable),
                  patch.object(runtimes, "_version", return_value="agy test"),
                  patch.object(runtimes.subprocess, "run", side_effect=launch)):
                assert run("agy") == runtimes.EXIT[outcome]
            meta = json.loads(result.with_name(result.name + ".meta.json").read_text())
            note = meta["check_grants_note"]
            assert meta["outcome"] == outcome and "Check grants were not passed" in note and reason in note
            if outcome == "provider_error":
                assert note in meta["detail"] and note in stderr.getvalue()
            else:
                assert "detail" not in meta and result.is_file()


def test_agy_settings_are_private_and_removed_after_success_failure_and_timeout(tmp_path):
    user_rule = "read_file(personal-notes/**)"
    _agy_user_settings(tmp_path, json.dumps({"permissions": {"allow": [user_rule]}}))
    for outcome in ("ok", "provider_error", "timeout", "launch_error"):
        paths = []

        def launch(argv, *, outcome=outcome, paths=paths, **kwargs):
            path = Path(argv[argv.index("--settings") + 1])
            paths.append(path)
            assert path.is_file() and all(user_rule not in arg for arg in argv)
            assert json.loads(path.read_text(encoding="utf-8")) == {
                "permissions": {"allow": [user_rule, "command(pytest -q)"]}}
            if os.name == "posix":
                assert stat.S_IMODE(path.stat().st_mode) == 0o600
            if outcome == "timeout":
                raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
            if outcome == "launch_error":
                raise OSError(errno.E2BIG, "Argument list too long")
            if outcome == "provider_error":
                return subprocess.CompletedProcess(argv, 1, "", "Run failed")
            payload = {"status": "SUCCESS", "structured_output": RESULT}
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

        with (_worker(tmp_path / outcome, checks=[["pytest", "-q"]]) as (_, result, run),
              patch.object(Path, "home", return_value=tmp_path),
              patch.object(runtimes.shutil, "which", return_value=sys.executable),
              patch.object(runtimes, "_version", return_value="agy test"),
              patch.object(runtimes.subprocess, "run", side_effect=launch)):
            expected = "provider_error" if outcome == "launch_error" else outcome
            assert run("agy") == runtimes.EXIT[expected]
        assert len(paths) == 1 and not paths[0].exists(), outcome
        assert result.exists() == (outcome == "ok")


def test_exec_preflight_stops_a_logged_out_runtime_before_launch(tmp_path):
    launched = tmp_path / "launched"
    body = f"open({str(launched)!r}, 'w').write('x'); sys.exit(0)"
    stderr = io.StringIO()
    with (_worker(tmp_path) as (_, result, run), redirect_stderr(stderr),
          patch.dict(os.environ, _env(_fake_cli(tmp_path, "claude", body, auth='{"loggedIn": false}')))):
        assert run("claude") == runtimes.EXIT["auth_required"]
    assert not launched.exists() and not result.exists()
    meta = json.loads(result.with_name(result.name + ".meta.json").read_text())
    assert meta["outcome"] == "auth_required" and meta["preflight"] == {"auth": "logged_out"}
    assert "claude reports it is not logged in (auth status)" in stderr.getvalue()


def test_exec_refuses_oversize_agy_prompt_before_launch(tmp_path):
    stderr = io.StringIO()
    with (_worker(tmp_path) as (brief, result, run), redirect_stderr(stderr),
          patch.dict(os.environ, _env(_fake_cli(tmp_path, "agy", "sys.exit(1)"))),
          patch.object(runtimes.subprocess, "run", side_effect=AssertionError("unexpected launch")) as launch):
        brief.write_text("é" * 49_950, encoding="utf-8")  # The worker rules push the prompt over 100,000 bytes.
        assert run("agy") == runtimes.EXIT["unsupported"]
    launch.assert_not_called()
    meta = json.loads(result.with_name(result.name + ".meta.json").read_text())
    assert meta["outcome"] == "unsupported" and "100000 byte" in meta["detail"]
    assert "100000 byte argv limit" in stderr.getvalue()


def test_controlled_claude_refuses_task_project_settings_in_snapshot(tmp_path):
    for filename in ("settings.json", "settings.local.json"):
        case_dir = tmp_path / filename
        stderr = io.StringIO()
        with _worker(case_dir) as (_, result, run):
            project = Path.cwd()
            settings = project / ".claude" / filename
            settings.parent.mkdir()
            settings.write_text("{}", encoding="utf-8")
            snapshot = case_dir / "snapshot"
            snapshot.mkdir()
            env = {**_env(_fake_cli(case_dir, "claude", CLAUDE_WORKER)),
                   "HIPSON_PROJECT": str(project), "HIPSON_REVIEW_SNAPSHOT": str(snapshot)}
            with patch.dict(os.environ, env), chdir(snapshot), redirect_stderr(stderr):
                assert run("claude", "--profile", "controlled") == runtimes.EXIT["unsupported"], filename
                assert not (case_dir / "calls.jsonl").exists(), filename
                meta = json.loads(result.with_name(result.name + ".meta.json").read_text())
                assert meta["outcome"] == "unsupported" and str(settings) in meta["detail"], (filename, meta)
                assert "use profile native or remove" in meta["detail"], (filename, meta)
                assert run("claude", "--profile", "native") == 0, (filename, stderr.getvalue())
            assert meta["detail"] in stderr.getvalue(), (filename, stderr.getvalue())


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
    assert all(call["argv"][call["argv"].index("--output-format") + 1] == "stream-json" for call in calls)
    assert all(call["prompt_has_rules"] for call in calls)
    assert [call["prompt_has_reviewer_rules"] for call in calls] == [False, True]
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
    for assignment in view["assignments"]:
        usage = assignment["usage"]
        assert usage["source"] == "claude_stream_json" and usage["reason"] is None
        assert usage["tokens"]["output_tokens"] == 7 and usage["tokens"]["total_tokens"] is None
        assert usage["elapsed_seconds"] >= 0 and meta["brief_tokens_estimate"] > 0


def test_flow_grants_agy_checks_to_the_builder_only(tmp_path):
    fake = f"""
from pathlib import Path

mode = args[args.index('--mode') + 1]
layer = None
if '--settings' in args:
    path = Path(args[args.index('--settings') + 1])
    layer = json.loads(path.read_text(encoding='utf-8'))
with open(os.environ['FAKE_LOG'], 'a', encoding='utf-8') as log:
    log.write(json.dumps([mode, layer]) + '\\n')
result = {RESULT!r}
if mode == 'accept-edits':
    Path('src/parser/pairs.py').write_text(
        "def parse_pair(text):\\n    return tuple(part.strip() for part in text.split(':'))\\n")
    result['files_changed'] = [{{'path': 'src/parser/pairs.py', 'change': 'split and trim'}}]
print(json.dumps({{'status': 'SUCCESS', 'structured_output': result}}))
"""
    bin_dir = _fake_cli(tmp_path, "agy", fake)
    project, db = _repo(tmp_path), tmp_path / "ledger.sqlite"
    path = _spec(tmp_path, project)
    spec = json.loads(path.read_text())
    hipson = [PY, "-m", "hipson.cli", "agent", "exec", "--runtime", "agy"]
    for role in ("builder", "reviewer"):
        spec[role]["command"] = [*hipson, "--role", role]
    path.write_text(json.dumps(spec))
    home = tmp_path / "home"
    home.mkdir()
    log = tmp_path / "agy-calls.jsonl"
    env = {**_env(bin_dir), "FAKE_LOG": str(log), "HIPSON_HOME": str(tmp_path / "hh"),
           "HOME": str(home), "USERPROFILE": str(home)}
    proc, result = _flow(project, db, path, env=env)
    assert proc.returncode == 0 and result["state"] == "accepted", (proc.stderr, result)
    grant = f"command({shlex.join(spec['checks'][0])})"
    assert [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] == [
        ["accept-edits", {"permissions": {"allow": [grant]}}],
        ["plan", None],
    ]


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


def test_session_continuation_requires_a_passed_assignment_of_the_same_task_and_role(tmp_path):
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
    with patch.dict(os.environ, {"HIPSON_RUN_DB": ledger_a}), chdir(tmp_path):
        os.environ.pop("HIPSON_PROJECT", None)
        assert runtimes._previous_session(result, "claude", task, "builder") == "session-good"
        good.unlink()
        assert runtimes._previous_session(result, "claude", task, "builder") is None
        (tmp_path / "array.result.json.meta.json").write_text("[1]", encoding="utf-8")
        assert runtimes._previous_session(result, "claude", task, "builder") is None


def test_session_continuation_ignores_symlinked_meta(tmp_path):
    with _worker(tmp_path) as (_, result, _):
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


def test_exec_resumes_reviewer_session_across_snapshots_using_task_project(tmp_path):
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
    stderr = io.StringIO()
    with _worker(tmp_path) as (_, result, run), redirect_stderr(stderr):
        project = os.getcwd()
        task, db, assignment = (os.environ[key] for key in ("HIPSON_TASK", "HIPSON_RUN_DB", "HIPSON_ASSIGNMENT"))
        env = {**_env(bin_dir), "HIPSON_PROJECT": project}
        first_snapshot = tmp_path / "snapshot-1"
        first_snapshot.mkdir()
        with patch.dict(os.environ, {**env, "HIPSON_REVIEW_SNAPSHOT": str(first_snapshot)}), chdir(first_snapshot):
            assert run("codex") == 0
        first = json.loads(result.with_name(result.name + ".meta.json").read_text())
        assert first["project"] == project and first["session"] == "thread-snapshot"

        with closing(ledger.Ledger(db)) as store:
            store.finish(assignment, result="passed", summary="passed")
            next_assignment = store.assign(task, actor="reviewer", role="reviewer", mode="ro")["id"]
        second_snapshot = tmp_path / "snapshot-2"
        second_snapshot.mkdir()
        result = result.with_name("second.result.json")
        with (patch.dict(os.environ, {**env, "HIPSON_ASSIGNMENT": next_assignment,
                                     "HIPSON_REVIEW_SNAPSHOT": str(second_snapshot)}), chdir(second_snapshot)):
            assert run("codex", "--continue-session", "--result", str(result)) == 0
        second = json.loads(result.with_name(result.name + ".meta.json").read_text())
        assert second["project"] == project and second["resumed"]
        assert second["session_requested"] == second["session"] == first["session"]
    assert stderr.getvalue().count("codex native session=thread-snapshot") == 2


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
        assert resumed[:3][1:] == ["exec", "resume"] and f"sandbox_mode={sandbox}" in resumed
        assert resumed[resumed.index(f"sandbox_mode={sandbox}") - 1] == "--config"
    # Direct callers such as the native probe keep the settings file alive with argv for the launch.
    with patch.object(Path, "home", return_value=tmp_path):
        argv, _ = runtimes.build_command("agy", mode="rw", checks=[["python3", "tests/check.py"], ["rm", "-rf", "*"]],
                                         **common)
    settings = Path(argv[argv.index("--settings") + 1])
    layer = json.loads(settings.read_text(encoding="utf-8"))
    assert layer == {"permissions": {"allow": ["command(python3 tests/check.py)"]}}
    del argv
    gc.collect()
    assert not settings.exists()
    argv, _ = runtimes.build_command("agy", mode="ro", **common)
    assert "--settings" not in argv


def test_a_session_whose_resume_failed_is_not_resumed_again(tmp_path):
    result = tmp_path / "build-3.result.json"
    database = str((tmp_path / "l.sqlite").resolve())
    with closing(ledger.Ledger(database)) as store:
        task = store.open_task(project=tmp_path, title="Build", objective="Build project")["id"]
        older, newer = (store.assign(task, actor="test", role="builder", mode="ro")["id"] for _ in range(2))
        for item in (older, newer):
            store.finish(item, result="passed", summary="passed")

    def meta(name, stamp, **values):
        data = {"outcome": "ok", "runtime": "claude", "task": task, "role": "builder", "ledger": database,
                "project": str(tmp_path), "assignment": older, **values}
        path = tmp_path / f"{name}.result.json.meta.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        os.utime(path, (stamp, stamp))

    meta("build-1", 100, session="session-old")
    meta("build-2", 200, session="session-new", assignment=newer)
    with patch.dict(os.environ, {"HIPSON_RUN_DB": database}), chdir(tmp_path):
        os.environ.pop("HIPSON_PROJECT", None)
        assert runtimes._previous_session(result, "claude", task, "builder") == "session-new"
        # The provider no longer has session-new: that attempt failed, so the older good session is next.
        meta("build-3", 300, outcome="provider_error", resumed=True, session_requested="session-new", session=None)
        assert runtimes._previous_session(result, "claude", task, "builder") == "session-old"
        # A failure older than the success does not count against it.
        meta("build-0", 50, outcome="provider_error", resumed=True, session_requested="session-old", session=None)
        assert runtimes._previous_session(result, "claude", task, "builder") == "session-old"
        meta("build-4", 400, outcome="provider_error", resumed=True, session_requested="session-old", session=None)
        assert runtimes._previous_session(result, "claude", task, "builder") is None


def test_windows_execution_contexts_are_detected_and_kept_apart(tmp_path):
    native, shim, forward = tmp_path / "agy.exe", tmp_path / "codex.cmd", tmp_path / "agy.CMD"
    native.write_bytes(b"MZ")
    shim.write_text('@"C:/Program Files/nodejs/node.exe" "%~dp0node_modules/codex/bin/codex.js" %*' + chr(10))
    forward.write_text("@echo off" + chr(10) + 'for /f %%I in (`wsl.exe -d Ubuntu --exec wslpath -a "%CD%"`) do x' + chr(10))
    def context(path):
        return runtimes.execution_context(path, windows=True)

    assert context(str(native)) == {"context": "native"}
    assert context(str(shim)) == {"context": "batch-shim", "launcher": str(shim)}  # Its node and script are absent.
    assert context(str(forward)) == {"context": "wsl-forwarded", "launcher": str(forward)}
    assert context(None) == {"context": None}
    assert "keeps Windows and WSL separate" in runtimes._context_limitation("agy", context(str(forward)))
    assert "cannot pass" in runtimes._context_limitation("claude", context(str(shim)))
    assert runtimes._context_limitation("codex", {"context": "native"}) is None
    # A .cmd file on POSIX is just a file name.
    assert runtimes.execution_context(str(forward), windows=False) == {"context": "native"}
    # An npm launcher whose node and script exist runs them directly (no cmd.exe); so does "python script.py %*".
    (tmp_path / "node.exe").write_bytes(b"MZ")
    (tmp_path / "node_modules/@openai/codex/bin").mkdir(parents=True)
    (tmp_path / "node_modules/@openai/codex/bin/codex.js").write_text("")
    npm = tmp_path / "codex2.cmd"
    npm.write_text('@ECHO off\nIF EXIST "%dp0%\\node.exe" (SET "_prog=%dp0%\\node.exe")\n'
                   'endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  '
                   '"%dp0%\\node_modules\\@openai\\codex\\bin\\codex.js" %*\n')
    found = context(str(npm))
    assert found["context"] == "batch-shim" and found["direct"][0] == str(tmp_path / "node.exe")
    assert found["direct"][1].endswith("codex.js") and os.path.isfile(found["direct"][1])
    assert runtimes._context_limitation("codex", found) is None
    (tmp_path / "tool.py").write_text("")
    py = tmp_path / "tool.cmd"
    py.write_text(f'@"{sys.executable}" "%~dp0tool.py" %*\n')
    assert context(str(py))["direct"] == [sys.executable, str(tmp_path / "tool.py")]



def test_wsl_forwarding_is_recognised_at_command_position_only(tmp_path):
    cases = {
        r'@"C:\Program Files\nodejs\node.exe" "%~dp0node_modules\codex\bin\codex.js" %*': "batch-shim",
        r'node C:\tools\wsl\bin\x.js %*': "batch-shim",                 # A directory named wsl.
        r'"C:\Users\wsl\AppData\Roaming\npm\node.exe" "%~dp0claude.js" %*': "batch-shim",  # A user named wsl.
        "REM wsl notes\n@node x.js %*": "batch-shim",                    # Comments do not run anything.
        ":: run wsl later\n@node x.js %*": "batch-shim",
        r'node \\wsl$\Ubuntu\home\u\x.js %*': "batch-shim",               # A Windows process reading WSL files.
        "bash.exe -c agy": "batch-shim",                                  # Git Bash is not WSL.
        "for /f %%I in (`wsl.exe -d Ubuntu --exec wslpath -a %CD%`) do set X=%%I": "wsl-forwarded",
        "@wsl.exe -d Ubuntu -- agy %*": "wsl-forwarded",
        "wsl agy %*": "wsl-forwarded",
        r'"C:\Windows\System32\wsl.exe" -e agy %*': "wsl-forwarded",
        "ubuntu2204.exe run agy %*": "wsl-forwarded",
    }
    for index, (text, expected) in enumerate(cases.items()):
        launcher = tmp_path / f"case{index}.cmd"
        launcher.write_text("@echo off\n" + text + "\n")
        assert runtimes.execution_context(str(launcher), windows=True)["context"] == expected, text


def test_codex_effort_must_be_a_level_name_because_it_is_passed_unquoted(tmp_path):
    schema, last = tmp_path / "s.json", tmp_path / "l.json"
    schema.write_text("{}")
    common = dict(profile="native", mode="rw", prompt="p", schema_path=schema, last_path=last, cwd=str(tmp_path))
    for effort in ("1", "true", "1.5", "[a]", "x y", "high=1"):
        with pytest.raises(runtimes.RuntimeFailure) as error:
            runtimes.build_command("codex", effort=effort, **common)
        assert error.value.kind == "unsupported" and "level name" in error.value.detail, effort
    for effort in ("low", "xhigh", "max", "very_high"):
        argv, _ = runtimes.build_command("codex", effort=effort, **common)
        assert f"model_reasoning_effort={effort}" in argv
