"""Offline agent harness contracts; also run by the dependency-free test runner."""

import copy
import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import agent_e2e as harness

ROOT = Path(__file__).resolve().parents[1]
BROKEN = ROOT / "e2e/agents/broken"


def test_agent_e2e_reference_solutions(tmp_path):
    # The invoking Codex itself may be a worker; measured agents must still be leads.
    sentinel = tmp_path / "owner-ledger"
    sentinel.write_text("owner state must be untouched")
    with patch.dict(os.environ, HIPSON_ROLE="worker", HIPSON_TASK="T-9999", HIPSON_ASSIGNMENT="A-9999",
                    HIPSON_LEDGER=str(sentinel)):
        runs = [harness.run_scenario(name, tmp_path / name, keep=True) for name in harness.scenario_names()]
    assert sentinel.read_text() == "owner state must be untouched"
    assert len(runs) == 3
    for run in runs:
        assert run["passed"], run
        assert run["metrics"]["hipson_invocations"] > 0
        assert run["metrics"]["turns"] == 1
        assert run["metrics"]["token_usage"] is None
        directory = tmp_path / run["scenario"]
        assert (directory / "workspace/.git").is_dir()
        assert (directory / "final.json").is_file()
        assert not (directory / "workspace/codex-home").exists()
        with patch.object(harness, "execute", side_effect=AssertionError("Replay must not launch a driver")), \
                patch.object(harness, "cleanup_workers", side_effect=AssertionError("Replay must not signal processes")):
            replay = harness.replay_scenario(directory)
        assert replay["passed"], replay
    conflict = next(r for r in runs if r["scenario"] == "conflict")
    assert conflict["metrics"]["failing_invocations"][0]["exit_code"] == 1
    report = harness.write_report(tmp_path, runs)
    assert report["passed"] and all(a["pass_rate"] == 1 for a in report["scenarios"].values())
    assert "lead-flow" in (tmp_path / "report.md").read_text()


def test_agent_e2e_broken_solutions(tmp_path):
    for name, script, message in (
        ("lead-flow", "lead-no-review.sh", "separate structured reviewer"),
        ("conflict", "conflict-bypass.sh", "live holder was bypassed"),
        ("recovery", "recovery-tamper.sh", "stale claim must be released by ledger reap"),
    ):
        run = harness.run_scenario(name, tmp_path / name, solution=BROKEN / script)
        assert not run["passed"], run
        if name == "recovery":
            assert not run["measurement_valid"]
            assert "Ledger changed outside recorded Hipson invocations" in run["harness_errors"]
            assert not run["failures"], "Invalid recording must not be attributed to the agent"
            continue
        assert run["measurement_valid"] and any(message in f for f in run["failures"]), run
        if name == "lead-flow":
            assert any("set to done before scenario acceptance" in f for f in run["failures"]), run


def test_agent_e2e_deleted_parser_is_rejected_while_holder_active(tmp_path):
    run = harness.run_scenario("conflict", tmp_path / "run", solution=BROKEN / "conflict-delete-parser.sh")
    assert run["measurement_valid"] and not run["passed"], run
    assert "conflict: parser was edited while the fixture holder was still live" in run["failures"]


def test_agent_e2e_lead_builders_require_parser_claims(tmp_path):
    run = harness.run_scenario("lead-flow", tmp_path / "run", solution=BROKEN / "lead-wrong-scope.sh")
    assert run["measurement_valid"] and not run["passed"], run
    assert "lead-flow: builders must hold claims covering src/parser/pairs.py" in run["failures"]


def test_agent_e2e_noop_raw_access_and_delete_restore_fail(tmp_path):
    reference = harness.SCENARIOS / "recovery/solution.sh"
    for name, prelude, message in (
        ("raw-read", "python3 -c 'import os,sqlite3; sqlite3.connect(os.environ[\"HIPSON_LEDGER\"]).close()'\n",
         "Direct ledger access/tampering"),
        ("restore", 'mv "$HIPSON_LEDGER" "$HIPSON_LEDGER.saved"\nmv "$HIPSON_LEDGER.saved" "$HIPSON_LEDGER"\n',
         "Ledger file was replaced or deleted"),
    ):
        script = tmp_path / f"{name}.sh"
        script.write_text(prelude + f"source {reference}\n")
        run = harness.run_scenario("recovery", tmp_path / name, solution=script)
        assert not run["passed"] and any(message in f for f in run["failures"]), run


def test_agent_e2e_premature_done_cannot_be_repaired_afterwards(tmp_path):
    reference = (harness.SCENARIOS / "lead-flow/solution.sh").read_text()
    close = "hipson task status --task T-0001 --to done --actor lead"
    script = tmp_path / "premature.sh"
    script.write_text(reference.replace(close, 'cp src/parser/pairs.py saved.py\n'
                      'printf "def parse_pair(text): return ()\\n" > src/parser/pairs.py\n'
                      + close + '\ncp saved.py src/parser/pairs.py'))
    run = harness.run_scenario("lead-flow", tmp_path / "run", solution=script)
    assert not run["passed"], run
    assert any("set to done before scenario acceptance" in f for f in run["failures"]), run
    assert not any(f.startswith("lead-flow: parser acceptance failed") for f in run["failures"])


def test_agent_e2e_recovery_requires_reap_not_manual_finish(tmp_path):
    script = tmp_path / "manual-finish.sh"
    reference = (harness.SCENARIOS / "recovery/solution.sh").read_text()
    script.write_text(reference.replace("hipson task reap --task T-0001\n",
        'hipson task finish --assignment A-0001 --result cancelled --summary "Manual release"\n'))
    run = harness.run_scenario("recovery", tmp_path / "run", solution=script)
    assert not run["passed"]
    assert any("stale claim must be released by ledger reap" in f for f in run["failures"]), run


def test_agent_e2e_conflict_waiting_is_allowed(tmp_path):
    script = tmp_path / "wait.sh"
    script.write_text((harness.SCENARIOS / "conflict/solution.sh").read_text() +
                     '\ntouch .release-holder\n'
                     'while ! test -s holder.json; do sleep 0.02; done\n'
                     # Wait for runner finish, not merely result-file creation.
                     'while hipson task show --task T-0001 --json | '
                     'python3 -c "import json,sys; sys.exit(0 if json.load(sys.stdin)[\"claims\"] else 1)"; '
                     'do sleep 0.02; done\n'
                     'examples/agent-run.sh --task T-0001 --actor next --role builder --mode rw --scope src/parser '
                     '--result-file next.json -- bin/fake-worker build next.json\n')
    run = harness.run_scenario("conflict", tmp_path / "run", solution=script)
    assert run["passed"], run


def test_agent_e2e_conflict_close_after_wait_needs_review(tmp_path):
    wait = (harness.SCENARIOS / "conflict/solution.sh").read_text() + (
        "\ntouch .release-holder\n"
        "while hipson task show --task T-0001 --json | "
        """python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin)["claims"] else 1)'; """
        "do sleep 0.02; done\n"
        "hipson task status --task T-0001 --to in_progress --actor lead --summary 'holder finished'\n"
        "examples/agent-run.sh --task T-0001 --actor next --role builder --mode rw --scope src/parser "
        "--result-file next.json -- bin/fake-worker build next.json\n")
    reviewed = tmp_path / "reviewed.sh"
    reviewed.write_text(wait + "hipson task status --task T-0001 --to review --actor lead --summary verified\n"
                        "hipson task status --task T-0001 --to done --actor lead --summary accepted\n")
    run = harness.run_scenario("conflict", tmp_path / "ok", solution=reviewed)
    assert run["passed"], run
    # The ledger itself refuses done without review, so the realistic mistake is closing while the holder lives.
    early = tmp_path / "early.sh"
    early.write_text((harness.SCENARIOS / "conflict/solution.sh").read_text()
                     + "hipson task status --task T-0001 --to in_progress --actor lead --summary resume\n"
                     "hipson task status --task T-0001 --to review --actor lead --summary looks-fine\n"
                     "hipson task status --task T-0001 --to done --actor lead --summary closed\n")
    run = harness.run_scenario("conflict", tmp_path / "bad", solution=early)
    assert not run["passed"]
    assert any("closed while the parser work was pending or unreviewed" in f for f in run["failures"]), run


def test_agent_e2e_timeout_cleans_live_fixture(tmp_path):
    script = tmp_path / "hang.sh"
    script.write_text("sleep 30\n")
    run = harness.run_scenario("conflict", tmp_path / "run", solution=script, timeout=0.1, keep=True)
    assert not run["passed"] and run["timed_out"], run
    assert any("timed out" in f for f in run["failures"])
    initial = json.loads((tmp_path / "run/initial.json").read_text())
    pid = initial["tasks"][0]["assignments"][0]["owner_pid"]
    assert not Path(f"/proc/{pid}").exists(), "Harness left its fixture writer alive"


def test_agent_e2e_cleanup_surviving_group_after_leader_exits(tmp_path):
    fixture = harness.prepare_fixture(tmp_path / "workspace")
    child_file = fixture.workdir / "child.pid"
    child_code = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
    leader_code = (f"import subprocess,time; from pathlib import Path; "
                   f"p=subprocess.Popen({[sys.executable, '-c', child_code]!r}); "
                   "Path('child.pid').write_text(str(p.pid)); time.sleep(60)")
    leader = subprocess.Popen([sys.executable, "-c", leader_code], cwd=fixture.workdir, start_new_session=True)
    fixture.worker_groups.add(leader.pid)
    try:
        deadline = time.monotonic() + 5
        while not child_file.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        child = int(child_file.read_text())
        os.kill(leader.pid, signal.SIGKILL)
        leader.wait(timeout=5)
        assert os.getpgid(child) == leader.pid and not Path(f"/proc/{leader.pid}").exists()
        harness.cleanup_workers(fixture)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                if Path(f"/proc/{child}/stat").read_text().rsplit(")", 1)[-1].split()[0] == "Z":
                    break
            except FileNotFoundError:
                break
            time.sleep(0.01)
        else:
            raise AssertionError("Cleanup left a live descendant after its group leader exited")
    finally:
        try:
            os.killpg(leader.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        leader.wait(timeout=5)


def test_agent_e2e_cleanup_rejects_groups_outside_fixture(tmp_path):
    fixture = harness.prepare_fixture(tmp_path / "workspace")
    outside = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=tmp_path, start_new_session=True)
    fixture.worker_groups.add(outside.pid)
    try:
        harness.cleanup_workers(fixture)
        assert outside.poll() is None, "Stale ownership must not authorize signaling another workspace"
    finally:
        os.killpg(outside.pid, signal.SIGKILL)
        outside.wait(timeout=5)


def test_agent_e2e_conflict_redirected_scope_is_allowed(tmp_path):
    script = tmp_path / "redirect.sh"
    script.write_text((harness.SCENARIOS / "conflict/solution.sh").read_text() +
                     '\nexamples/agent-run.sh --task T-0001 --actor docs-builder --role builder --mode rw '
                     '--scope src/notes --result-file notes.json -- '
                     'bin/fake-worker build notes.json src/notes/pairs.py\n'
                     'hipson task status --task T-0001 --to in_progress --actor lead\n')
    run = harness.run_scenario("conflict", tmp_path / "run", solution=script)
    assert run["passed"], run


def test_agent_e2e_conflict_false_scope_and_deleted_store_fail(tmp_path):
    script = tmp_path / "wrong-scope.sh"
    script.write_text((harness.SCENARIOS / "conflict/solution.sh").read_text() +
                     '\nexamples/agent-run.sh --task T-0001 --actor bypass --role builder --mode rw '
                     '--scope src/notes --result-file next.json -- bin/fake-worker build next.json\n')
    run = harness.run_scenario("conflict", tmp_path / "wrong", solution=script)
    assert not run["passed"]
    assert "conflict: parser was edited while the fixture holder was still live" in run["failures"]
    script.write_text('rm "$HIPSON_LEDGER"\n')
    run = harness.run_scenario("conflict", tmp_path / "deleted", solution=script)
    assert not run["passed"] and any("Ledger file was replaced or deleted" in f for f in run["failures"])
    initial = json.loads((tmp_path / "deleted/initial.json").read_text())
    pid = initial["tasks"][0]["assignments"][0]["owner_pid"]
    assert not Path(f"/proc/{pid}").exists(), "Corrupted ledger must not prevent helper cleanup"


def test_agent_e2e_codex_sample_parser_and_metrics():
    sample = (ROOT / "e2e/agents/fixtures/codex-events.jsonl").read_text()
    transcript = harness.parse_transcript(sample)
    assert len(transcript.commands) == 4  # Started/completed items are deduplicated by ID.
    assert transcript.turns == 2 and not transcript.errors
    metrics = harness.metrics(transcript, 1.23456)
    assert metrics["total_commands"] == 4 and metrics["hipson_invocations"] == 2
    assert metrics["help_lookups"] == 1 and metrics["docs_lookups"] == 1
    assert metrics["duration_seconds"] == 1.235
    assert metrics["token_usage"] == {"input_tokens": 160, "cached_input_tokens": 90, "output_tokens": 30}
    failure = metrics["failing_invocations"][0]
    assert failure["exit_code"] == 1 and failure["first_error_line"].startswith("Claim conflicts")
    assert transcript.commands[-1]["exit_code"] == 75


def test_agent_e2e_parser_errors_and_hidden_cli_failure():
    transcript = harness.parse_transcript('not-json\n[]\n{"type":"turn.failed","error":{"message":"quota"}}\n')
    assert len(transcript.errors) == 3
    malformed = {"type": "item.completed", "item": {"type": "command_execution", "command": 42}}
    assert harness.parse_transcript(json.dumps(malformed)).errors
    audit = [{"args": ["task", "assign"], "exit_code": 1, "output": "Claim conflict\n"}]
    metrics = harness.metrics(harness.Transcript(commands=[{"command": "hipson task assign || true", "exit_code": 0}]),
                              0.5, audit)
    assert metrics["failing_invocations"][0]["exit_code"] == 1
    assert metrics["token_usage"] is None


def test_agent_e2e_codex_isolation_command(tmp_path):
    command = harness.codex_command("codex", tmp_path, "gpt-6.1-sol", "high", " ".join(harness.ISOLATION_FLAGS))
    assert "workspace-write" in command and command[-1] == "-"
    assert command[command.index("--model") + 1] == "gpt-6.1-sol"
    assert "project_doc_max_bytes=0" in command and 'model_reasoning_effort="high"' in command
    for flag in harness.ISOLATION_FLAGS:
        assert flag in command
    try:
        harness.codex_command("codex", tmp_path, "gpt-6.1-sol", "high", "--ephemeral")
    except ValueError as exc:
        assert "required isolation flags" in str(exc)
    else:
        raise AssertionError("Must fail closed when isolation is unavailable")


def test_agent_e2e_codex_adapter_with_offline_stub(tmp_path):
    # Never invoke real/nested Codex. This executable exercises the subprocess adapter contract.
    owner_home = tmp_path / "owner-home"
    owner_home.mkdir()
    (owner_home / "auth.json").write_text('{"offline": true}')
    (owner_home / "AGENTS.md").write_text("Owner workflow must not enter the measurement")
    (owner_home / "config.toml").write_text("Owner configuration must not enter the measurement")
    (owner_home / ".agents/skills/private-skill").mkdir(parents=True)
    (owner_home / ".profile").write_text('export PATH="$HOME/.local/bin:$PATH"\n')
    stub = tmp_path / "offline-codex"
    reference = harness.SCENARIOS / "recovery/solution.sh"
    stub.write_text(f'''#!{sys.executable}
import json, os, subprocess, sys
from pathlib import Path
if "--help" in sys.argv:
    print("--ephemeral --ignore-user-config --ignore-rules")
    raise SystemExit(0)
home = Path(os.environ["CODEX_HOME"])
shell_home = Path(os.environ["HOME"])
assert shell_home != Path({str(owner_home)!r})
assert not (shell_home / ".agents/skills").exists()
assert not (shell_home / ".profile").exists()
resolved = subprocess.run(["bash", "-lc", "command -v hipson"], check=True, capture_output=True, text=True)
assert resolved.stdout.strip() == str(Path.cwd() / "bin/hipson")
assert (home / "auth.json").is_symlink()
assert not (home / "AGENTS.md").exists() and not (home / "config.toml").exists()
assert all(k not in os.environ for k in ("HIPSON_ROLE", "HIPSON_TASK", "HIPSON_ASSIGNMENT"))
assert sys.argv[sys.argv.index("--model") + 1] == "gpt-6.1-sol"
assert "previous worker crashed" in sys.stdin.read()
Path("isolated-home.txt").write_text(str(home))
Path("isolated-shell-home.txt").write_text(str(shell_home))
proc = subprocess.run(["bash", "-lc", {shlex.join(['source', str(reference)])!r}], capture_output=True, text=True)
print(json.dumps({{"type": "turn.started"}}))
print(json.dumps({{"type": "item.completed", "item": {{"id": "1", "type": "command_execution",
      "command": "offline reference mission", "exit_code": proc.returncode,
      "aggregated_output": proc.stdout + proc.stderr}}}}))
print(json.dumps({{"type": "turn.completed", "usage": {{"input_tokens": 5, "output_tokens": 3}}}}))
raise SystemExit(proc.returncode)
''')
    stub.chmod(0o755)
    with patch.dict(os.environ, HOME=str(owner_home), CODEX_HOME=str(owner_home), HIPSON_ROLE="worker", HIPSON_TASK="T-9"), \
            patch.object(harness.shutil, "which", return_value=str(stub)):
        run = harness.run_scenario("recovery", tmp_path / "run", driver="codex", keep=True)
    assert run["passed"], run
    assert run["metrics"]["token_usage"] == {"input_tokens": 5, "output_tokens": 3}
    assert all(flag in run["isolation"] for flag in harness.ISOLATION_FLAGS)
    home = Path((tmp_path / "run/workspace/isolated-home.txt").read_text())
    assert not home.exists(), "Temporary auth home must be removed even with --keep"
    assert not Path((tmp_path / "run/workspace/isolated-shell-home.txt").read_text()).exists()
    assert (owner_home / "auth.json").read_text() == '{"offline": true}'


def test_agent_e2e_fake_conflict_preserves_foreign_namespace_owner(tmp_path):
    def foreign_holder(fixture, task):
        return fixture.cli("task", "assign", "--task", task, "--actor", "holder", "--role", "builder",
                           "--mode", "rw", "--scope", "src/parser", "--owner-host", socket.gethostname(),
                           "--owner-pid", "2147483647", "--owner-pid-namespace", "pid:[foreign]")["id"]

    with patch.object(harness.Fixture, "start_holder", foreign_holder):
        run = harness.run_scenario("conflict", tmp_path / "run")
    assert run["passed"], run
    state = json.loads((tmp_path / "run/final.json").read_text())
    holder = state["tasks"][0]["assignments"][0]
    assert holder["status"] == "active" and holder["owner_pid_namespace"] == "pid:[foreign]"
    assert not any(event["type"] == "reaped" for event in state["events"])


def test_agent_e2e_login_shell_proxy_wins_and_calls_are_recorded(tmp_path):
    fixture = harness.prepare_fixture(tmp_path / "workspace")
    # BASH_ENV also restores the proxy for non-login shells with an overridden PATH.
    for flags in ("-lc", "-c"):
        proc = subprocess.run(["bash", flags, "command -v hipson; hipson --help"], cwd=fixture.workdir,
                              env={**fixture.env, "PATH": "/usr/bin:/bin"}, capture_output=True, text=True, timeout=15)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.splitlines()[0] == str(fixture.workdir / "bin/hipson")
    assert len(harness.read_jsonl(fixture.workdir / ".harness/cli.jsonl")) == 2


def test_agent_e2e_unrecorded_read_is_invalid_measurement(tmp_path):
    script = tmp_path / "bypass.sh"
    # Same real CLI, deliberately omitting the proxy; read-only bypass has no state delta.
    script.write_text(f"{shlex.quote(str(ROOT / '.venv/bin/hipson'))} task list --json\n"
                      f"source {shlex.quote(str(harness.SCENARIOS / 'recovery/solution.sh'))}\n")
    run = harness.run_scenario("recovery", tmp_path / "run", solution=script)
    assert not run["passed"] and not run["measurement_valid"], run
    assert run["harness_errors"] == ["Hipson call bypassed the recording proxy"]
    assert not run["failures"], run
    report = harness.write_report(tmp_path, [run])
    assert report["scenarios"]["recovery"] == {"runs": 1, "valid_runs": 0, "invalid_runs": 1,
                                               "passed": 0, "pass_rate": None}
    assert "INVALID" in (tmp_path / "report.md").read_text()


def test_agent_e2e_visible_calls_handle_shell_wrappers_and_command_positions():
    command = "hipson --help > help.txt && /real/hipson task show --task T-1; command -v hipson"
    assert harness.visible_hipson_calls("bash -lc " + shlex.quote(command)) == [("--help",), ("task", "show")]
    assert harness.visible_hipson_calls("/real/hipson task show", Path("/fixture")) == [
        ("unrecorded executable", "/real/hipson")]
    assert harness.visible_hipson_calls("./bin/hipson task show", Path("/fixture")) == [("task", "show")]
    assert harness.visible_hipson_calls("cat docs/hipson; printf 'hipson task show'; echo hipson") == []


def test_agent_e2e_skipped_shell_branches_are_valid_measurements(tmp_path):
    state = {"tasks": [], "events": [], "findings": []}
    for command, audited, exit_code in (
        ("hipson task show --task T-0001 --json || hipson task list --json", ["task", "show"], 0),
        ("hipson task show --task missing && hipson task list --json", ["task", "show"], 1),
        ("false && hipson task list --json", None, 1),
        ("true || /real/hipson task list --json", None, 0),
        ("if false; then hipson task list --json; fi", None, 0),
        ("if true; then true; else /real/hipson task list --json; fi", None, 0),
        ("if hipson task show --task missing; then hipson task list --json; fi", ["task", "show"], 0),
    ):
        audit = [{"args": audited, "before": state, "after": state}] if audited else []
        for wrapped in (command, "bash -lc " + shlex.quote(command)):
            transcript = harness.Transcript(commands=[{"command": wrapped, "exit_code": exit_code}])
            ctx = harness.Context(tmp_path, tmp_path / "ledger", transcript, state, state, audit, {})
            assert harness.measurement_errors(ctx) == [], wrapped


def test_agent_e2e_credentials_are_scrubbed_from_artifacts_and_reports(tmp_path):
    canary = "e2e-private-canary-123456789"
    script = tmp_path / "diagnostic.sh"
    script.write_text('test -z "${OPENAI_API_KEY+x}"\ntest -z "${GITHUB_TOKEN+x}"\nenv\n'
                      f"printf '%s\\n' 'diagnostic: {canary}'\n"
                      f"source {shlex.quote(str(harness.SCENARIOS / 'recovery/solution.sh'))}\n")
    with patch.dict(os.environ, OPENAI_API_KEY=canary, GITHUB_TOKEN=canary):
        run = harness.run_scenario("recovery", tmp_path / "run", solution=script, keep=True)
        assert run["passed"], run
        sample = json.dumps({"type": "item.completed", "item": {"id": "secret", "type": "command_execution",
                            "command": f"hipson {canary}", "exit_code": 1, "aggregated_output": f"error: {canary}"}})
        transcript = harness.parse_transcript(sample + "\n" + json.dumps({"type": "error", "message": canary}))
        assert canary not in repr(transcript)
        run["failures"].append(canary)
        run["harness_errors"].append(canary)
        run["metrics"] = harness.metrics(transcript, 0)
        report = harness.write_report(tmp_path, [run])
        assert canary not in json.dumps(report)
    paths = [p for p in (tmp_path / "run").rglob("*") if p.is_file() and p.suffix != ".sqlite"]
    paths += [tmp_path / "report.json", tmp_path / "report.md"]
    for path in paths:
        assert canary.encode() not in path.read_bytes(), path


def test_agent_e2e_proxy_logs_and_startup_errors_are_redacted(tmp_path):
    canary = "e2e-proxy-secret-987654321"
    with patch.dict(os.environ, OPENAI_API_KEY=canary):
        fixture = harness.prepare_fixture(tmp_path / "workspace")
        proc = subprocess.CompletedProcess([], 1, f"diagnostic {canary}", "failed")
        with patch.object(harness.subprocess, "run", return_value=proc), \
                patch.object(sys, "argv", ["hipson", "--help"]), \
                patch.object(sys, "stdout"), patch.object(sys, "stderr"):
            assert harness.hipson_proxy(str(fixture.workdir), str(fixture.ledger)) == 1
            assert harness.runner_proxy(str(fixture.workdir)) == 1
        for name in ("cli.jsonl", "runner.jsonl"):
            entries = harness.read_jsonl(fixture.workdir / ".harness" / name)
            assert entries and canary not in json.dumps(entries)
            assert "[REDACTED]" in entries[0]["output"]
        # Known keys and escaped values must remain valid JSON after persistence.
        value = canary + '\\"\n'
        text = json.dumps({"type": "error", "message": canary, "token": value}) + "\n"
        event = json.loads(harness.scrub_jsonl(text))
        assert event["message"] == event["token"] == "[REDACTED]"
        with patch.object(harness, "prepare_fixture", side_effect=ValueError(canary)):
            run = harness.run_scenario("recovery", tmp_path / "error")
        assert not run["measurement_valid"] and canary not in json.dumps(run)


def test_agent_e2e_overlap_oracle_checks_history_across_tasks(tmp_path):
    assignments = [dict(id=f"A-{i}", mode="rw", scope=[scope]) for i, scope in ((1, "src"), (2, "src/parser"))]
    state = {"tasks": [{"id": "T-1", "project": "/fixture", "assignments": [assignments[0]]},
                       {"id": "T-2", "project": "/fixture", "assignments": [assignments[1]]}],
             "events": [{"seq": i, "type": kind, "refs": [ref]} for i, kind, ref in
                        ((1, "assigned", "A-1"), (2, "assigned", "A-2"),
                         (3, "finished", "A-1"), (4, "finished", "A-2"))], "findings": [], "files": {}}
    ctx = harness.Context(tmp_path, tmp_path / "ledger", harness.Transcript(), state, copy.deepcopy(state), [], {})
    failures = harness.universal_checks(ctx, SimpleNamespace(check=lambda ctx: []))
    assert failures == ["Overlapping live rw claims at assignment A-2"]
    state["tasks"][1]["project"] = "/other"
    ctx.initial = copy.deepcopy(state)
    assert harness.universal_checks(ctx, SimpleNamespace(check=lambda ctx: [])) == []


def test_agent_e2e_cli_report_exit_code_and_runs(tmp_path):
    good = {"scenario": "recovery", "passed": True, "failures": [], "metrics": harness.metrics(harness.Transcript(), 0)}
    with patch.object(harness, "ROOT", tmp_path), patch.object(harness, "run_scenario", return_value=good) as run:
        assert harness.main(["--scenario", "recovery", "--runs", "2"]) == 0
        assert run.call_count == 2
    bad = {**good, "passed": False, "failures": ["observable failure"]}
    with patch.object(harness, "ROOT", tmp_path), patch.object(harness, "run_scenario", return_value=bad):
        assert harness.main(["--scenario", "recovery"]) == 1
    reports = [json.loads(path.read_text()) for path in (tmp_path / ".agent-e2e").glob("*/report.json")]
    assert len(reports) == 2 and any(r["scenarios"]["recovery"]["runs"] == 2 for r in reports)
