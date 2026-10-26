"""Regressions for init's client ownership, environment and working directory."""

import json
import os
import signal
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from unittest.mock import patch

from hipson import init_setup
from tests.support import ROOT
from tests.test_init import _Setup
from tests.test_init_security import _wait


def _write_codex(setup, **overrides):
    entry = {**setup.registration(), "env": {"HIPSON_LEDGER": str(setup.db)}, **overrides}
    setup.codex_home.mkdir(exist_ok=True)
    setup.codex_config.write_text(
        '[mcp_servers.hipson]\n' + ''.join(f'{key} = {json.dumps(value)}\n' for key, value in entry.items() if key != "env")
        + '[mcp_servers.hipson.env]\n' + ''.join(f'{key} = {json.dumps(value)}\n' for key, value in entry.get("env", {}).items()),
        encoding="utf-8",
    )


def test_init_claude_unowned_failure_keeps_concurrent_edits_and_recovery_steps(tmp_path):
    for existing in (False, True):
        for failure in ("timeout", "interrupt", "failed-exit", "invalid-output"):
            setup = _Setup(tmp_path / f"{existing}-{failure}")
            if existing:
                setup.foreign_configs()
            path = setup.project / ".mcp.json"
            before = path.read_bytes() if existing else None
            edited = b'{"mcpServers": {}, "concurrent editor": true}\n'
            def interrupted(argv, project, failure=failure, path=path, edited=edited):
                path.write_bytes(edited)
                if failure == "timeout":
                    raise subprocess.TimeoutExpired(argv, 1)
                if failure == "interrupt":
                    raise KeyboardInterrupt
                return subprocess.CompletedProcess(argv, 0 if failure == "invalid-output" else 1, "", "failed")
            with patch.dict(os.environ, setup.env, clear=True):
                plan = init_setup.build_plan(project_path=str(setup.project), client="claude", scope="project", dry_run=False)
                with patch.object(init_setup, "_run", side_effect=interrupted):
                    init_setup.apply_plan(plan)
            assert not plan.report["ok"]
            assert path.read_bytes() == edited, (existing, failure, plan.report)
            action = plan.report["actions"][1]
            assert action["status"] == "failed"
            assert str(path) in action["recovery"] and "manual" in action["recovery"].lower()
            if existing:
                backup = Path(action["backup"])
                assert backup.read_bytes() == before
                assert str(backup) in action["recovery"]
            else:
                assert "remove" in action["recovery"].lower() and "inspect" in action["recovery"].lower()
            assert action["recovery"] in init_setup.format_report(plan.report)


def test_init_codex_binds_ledger_and_verifies_only_forwarded_environment(tmp_path):
    for selector in ("HIPSON_LEDGER", "HIPSON_HOME", "XDG_CONFIG_HOME"):
        setup = _Setup(tmp_path / selector)
        for key in ("HIPSON_LEDGER", "HIPSON_HOME", "XDG_CONFIG_HOME"):
            if key != selector:
                setup.env.pop(key, None)
        setup.env["INIT_UNFORWARDED_SECRET"] = "must-not-reach-server"
        calls = []
        original = init_setup._run
        def recording(argv, project, calls=calls, original=original, **kwargs):
            calls.append((project, kwargs.get("env")))
            return original(argv, project, **kwargs)
        with patch.dict(os.environ, setup.env, clear=True):
            plan = init_setup.build_plan(project_path=str(setup.project), client="codex", scope="project", dry_run=False)
            db = plan.report["checks"]["ledger"]
            entry = tomllib.loads(plan.report["actions"][1]["append"])["mcp_servers"]["hipson"]
            assert entry.get("env") == {"HIPSON_LEDGER": db}
            with patch.object(init_setup, "_run", side_effect=recording):
                init_setup.apply_plan(plan)
        assert plan.report["ok"], plan.report
        env = calls[0][1]
        assert env["HIPSON_LEDGER"] == db
        assert "INIT_UNFORWARDED_SECRET" not in env and "HIPSON_HOME" not in env and "XDG_CONFIG_HOME" not in env
        # Probe the server's effective ledger in a real process, with the exact verification environment.
        probe = subprocess.run([sys.executable, "-c", "from hipson.ledger import default_ledger_db; print(default_ledger_db())"],
                               cwd=calls[0][0], env=env, capture_output=True, text=True, encoding="utf-8", timeout=10)
        assert probe.returncode == 0, probe.stderr
        assert probe.stdout.strip() == db
        # A first task opened by the CLI is visible through this same MCP environment.
        opened = subprocess.run([setup.cli, "task", "open", "--project", str(setup.project), "--title", "Visible to lead",
                                 "--objective", "Check shared ledger", "--json"], cwd=setup.root, env=setup.env,
                                capture_output=True, text=True, encoding="utf-8", timeout=10)
        assert opened.returncode == 0, opened.stderr
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "task.board", "arguments": {}}},
        ]
        board = original([entry["command"], *entry["args"]], calls[0][0], env=env,
                         input_text=''.join(json.dumps(request) + '\n' for request in requests))
        assert board.returncode == 0 and "Visible to lead" in board.stdout, board


def test_init_codex_unforwarded_ledger_is_a_conflict_but_explicit_forwarding_matches(tmp_path):
    for forwarded in (False, True):
        setup = _Setup(tmp_path / str(forwarded))
        _write_codex(setup, env={}, **({"env_vars": ["HIPSON_LEDGER"]} if forwarded else {}))
        before = setup.snapshots()
        report = setup.run("--client", "codex", "--dry-run", "--json", expected=0 if forwarded else 1)
        assert report["ok"] == forwarded
        assert report["actions"][1]["status"] == ("skipped" if forwarded else "conflict")
        assert setup.snapshots() == before


def test_init_codex_cwd_conflicts_are_rejected_before_writes(tmp_path):
    for variant in ("wrong-project", "missing-user-cwd", "missing-layer-cwd"):
        setup = _Setup(tmp_path / variant)
        if variant == "missing-layer-cwd":
            layer = setup.project / ".codex" / "config.toml"
            layer.parent.mkdir()
            layer.write_text('[mcp_servers.hipson]\ncwd = ' + json.dumps(str(setup.root / "missing")) + '\n', encoding="utf-8")
        else:
            _write_codex(setup, cwd=str(setup.root if variant == "wrong-project" else setup.root / "missing"),
                         args=["mcp", "serve", "--project=.", "--stdio"])
        before = setup.snapshots()
        report = setup.run("--client", "codex", "--yes", "--json", expected=1)
        assert not report["ok"] and report["actions"][1]["status"] == "conflict"
        assert "working directory" in report["errors"][0]
        assert setup.snapshots() == before


def test_init_codex_relative_project_uses_server_cwd_and_verification_launches_there(tmp_path):
    setup = _Setup(tmp_path)
    args = ["mcp", "serve", "--project=" + setup.project.name, "--stdio"]
    _write_codex(setup, cwd=str(setup.root), args=args)
    calls = []
    original = init_setup._run
    def recording(argv, project, **kwargs):
        calls.append((argv, project))
        return original(argv, project, **kwargs)
    with patch.dict(os.environ, setup.env, clear=True):
        plan = init_setup.build_plan(project_path=str(setup.project), client="codex", scope="project", dry_run=False)
        assert plan.report["ok"], plan.report
        with patch.object(init_setup, "_run", side_effect=recording):
            init_setup.apply_plan(plan)
    assert plan.report["ok"] and calls == [([setup.registration()["command"], *args], setup.root)], plan.report


def test_init_claude_disabled_for_this_project_never_reports_success_or_changes_preference(tmp_path):
    for scope in ("user", "project", "local", "absent"):
        setup = _Setup(tmp_path / scope)
        if scope != "absent":
            setup.register_claude(scope, setup.registration())
        setup.claude_config.parent.mkdir(exist_ok=True)
        config = json.loads(setup.claude_config.read_bytes()) if setup.claude_config.exists() else {}
        config.setdefault("projects", {}).setdefault(str(setup.project), {})["disabledMcpServers"] = ["hipson", "foreign"]
        setup.claude_config.write_text(json.dumps(config), encoding="utf-8")
        before = setup.snapshots()
        for option in ("--dry-run", "--yes"):
            report = setup.run("--client", "claude", "--scope", "user" if scope == "user" else "project", option, "--json", expected=1)
            assert not report["ok"] and "disabled for this project" in report["errors"][0]
            assert "/mcp" in report["errors"][0] and "re-enable" in report["errors"][0]
            assert setup.snapshots() == before and setup.calls() == []


def test_init_detached_descendant_limit_is_documented_and_observable(tmp_path):
    docs = (ROOT / "docs/INSTALL.md").read_text(encoding="utf-8")
    assert "Descendants that detach from the POSIX process group" in docs
    assert "Windows Job" in docs
    if sys.platform == "win32":
        return  # Windows Job containment is covered by the existing real .cmd descendant test.
    source = tmp_path / "detach.py"
    pidfile, heartbeat = tmp_path / "pid", tmp_path / "heartbeat"
    source.write_text('''import os, subprocess, sys, time
from pathlib import Path
if len(sys.argv) > 1:
    Path("pid").write_text(str(os.getpid()))
    while True:
        Path("heartbeat").write_text(str(time.time_ns()))
        time.sleep(.02)
subprocess.Popen([sys.executable, __file__, "child"], start_new_session=True,
                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(60)
''', encoding="utf-8")
    original = subprocess.Popen.communicate
    injected = False
    def timed_out(child, *args, **kwargs):
        nonlocal injected
        if not injected:
            injected = True
            _wait(heartbeat)
            raise subprocess.TimeoutExpired(child.args, 1)
        return original(child, *args, **kwargs)
    try:
        with patch.object(subprocess.Popen, "communicate", timed_out):
            try:
                init_setup._run([sys.executable, str(source)], tmp_path)
            except subprocess.TimeoutExpired:
                pass
            else:
                raise AssertionError("expected timeout")
        before = heartbeat.read_bytes()
        deadline = time.monotonic() + 3
        while heartbeat.read_bytes() == before:
            assert time.monotonic() < deadline, "detached descendant unexpectedly contained"
            time.sleep(.02)
    finally:
        if pidfile.exists():
            try:
                os.kill(int(pidfile.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass
