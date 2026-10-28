"""Regression contracts for the local workflow primitives retained in Hipson 1.7."""

import json
import os
import shlex
import signal
import sqlite3
import subprocess
import sys
from contextlib import chdir, closing
from pathlib import Path
from unittest import SkipTest, TestCase
from unittest.mock import patch

from hipson import (
    evals,
    evidence,
    packet_preflight,
    project,
    provider_doctor,
    quality,
    review_kit,
    run_control,
    session,
    verification,
    workflow,
)
from hipson.contracts import stable_hash
from tests.test_hipson_helpers import init_git_repo, run_cli_inprocess


def _raises(error, match=""):
    return TestCase().assertRaisesRegex(error, match)


def _plan(project_path):
    return {
        "work_id": "work_current",
        "project": str(project_path),
        "task": "Review",
        "repo_state": {"path": str(project_path)},
    }


PASSED = {"work_id": "work_current", "status": "passed", "results": [{"exit_code": 0}]}


def _passing_report(**overrides):
    return {
        "work_id": "work_current",
        "verification_status": "passed",
        "quality_gate": "passed",
        "verification_gate": "passed",
        "human_decision_gate": "passed",
        "sidecar_eval_gate": "not_applicable",
        "release_claim_gate": "passed",
        **overrides,
    }


def test_d01_failed_verification_overrides_old_quality_gates(tmp_path):
    for decision in ["approved", "rejected", "pending"]:
        plan = _plan(tmp_path)
        record = evidence.build_evidence_record(
            work_plan=plan,
            verification={"work_id": plan["work_id"], "status": "failed", "results": [{"exit_code": 1}]},
            quality_report=_passing_report(),
            human_decision=decision,
            ledger_root=tmp_path,
        )
        evidence.append_record(tmp_path, record)
        work = tmp_path / "work.json"
        work.write_text(json.dumps(plan))
        audit = evidence.audit_bundle(work_path=str(work), ledger_root=tmp_path)
        assert audit["latest_status"] == "failed"
        assert audit["latest_gates"]["verification_gate"] == "blocked"
        assert audit["latest_release_claim_gate"] == "blocked"
        assert "verification gate passed" not in record["claims"]["safe"]
        assert "release claim gate passed" not in record["claims"]["safe"]


def test_d01_human_decision_overrides_quality_report(tmp_path):
    for decision, gate in [("rejected", "blocked"), ("pending", "pending")]:
        record = evidence.build_evidence_record(
            work_plan=_plan(tmp_path),
            verification=PASSED,
            quality_report=_passing_report(),
            human_decision=decision,
            ledger_root=tmp_path,
        )
        assert record["gates"]["verification_gate"] == "passed"
        assert record["gates"]["human_decision_gate"] == gate
        assert record["gates"]["release_claim_gate"] == "blocked"


def test_d01_mismatched_quality_report_is_flagged(tmp_path):
    for overrides, diagnostic in [
        ({"work_id": "work_other"}, "work_id"),
        ({"verification_id": "older"}, "verification_id"),
        ({"verification_sha256": stable_hash(PASSED)}, "verification_sha256"),
    ]:
        record = evidence.build_evidence_record(
            work_plan=_plan(tmp_path),
            verification={**PASSED, "verification_id": "current"},
            quality_report=_passing_report(**overrides),
            human_decision="approved",
            ledger_root=tmp_path,
        )
        assert record["gates"]["release_claim_gate"] == "blocked"
        assert any(f"{diagnostic} does not match" in item for item in record["unknowns"])


def test_d01_inconsistent_verification_cannot_pass(tmp_path):
    for verification_result in [
        {**PASSED, "work_id": "work_other"},
        {**PASSED, "results": [{"exit_code": 1}]},
        {"status": "passed"},  # No work id and no command evidence.
        {**PASSED, "results": []},
        {"status": "passed", "results": [{"exit_code": 0}]},
    ]:
        record = evidence.build_evidence_record(
            work_plan=_plan(tmp_path), verification=verification_result, human_decision="approved", ledger_root=tmp_path
        )
        assert record["gates"]["verification_gate"] == "blocked"
        assert record["gates"]["release_claim_gate"] == "blocked"


def test_d01_quality_report_binds_verification_identity(tmp_path):
    work = tmp_path / "work.json"
    work.write_text(json.dumps(_plan(tmp_path)))
    verify = tmp_path / "verify.json"
    first = {**PASSED, "created_at_utc": "first"}
    verify.write_text(json.dumps(first))
    report = quality.build_quality_report(work_path=work, verification_path=verify, decision="approved")
    assert report["verification_sha256"] == stable_hash(first)
    matching = evidence.build_evidence_record(
        work_plan=_plan(tmp_path),
        verification=first,
        quality_report=report,
        human_decision="approved",
        ledger_root=tmp_path,
    )
    assert matching["gates"]["release_claim_gate"] == "passed"
    record = evidence.build_evidence_record(
        work_plan=_plan(tmp_path),
        verification={**first, "created_at_utc": "second"},
        quality_report=report,
        human_decision="approved",
        ledger_root=tmp_path,
    )
    assert record["gates"]["release_claim_gate"] == "blocked"


def test_d01_failed_eval_overrides_passing_sidecar_gate(tmp_path):
    record = evidence.build_evidence_record(
        work_plan=_plan(tmp_path),
        verification=PASSED,
        quality_report=_passing_report(sidecar_eval_gate="passed"),
        quality_eval={"ok": False},
        human_decision="approved",
        ledger_root=tmp_path,
    )
    assert record["gates"]["sidecar_eval_gate"] == "blocked"
    assert record["gates"]["release_claim_gate"] == "blocked"


def test_d01_release_claim_refuses_blocked_or_incomplete_bundle(tmp_path):
    for run_status in ["blocked", "incomplete"]:
        (tmp_path / "audit.json").write_text(
            json.dumps({"latest_gates": {"verification_gate": "passed", "release_claim_gate": "passed"}})
        )
        with patch.object(run_control, "build_status", return_value={"status": run_status}):
            claim = run_control.build_release_claim(tmp_path, claim="ready", human_decision="approved")
        assert claim["allowed"] is False
        assert any(run_status in reason for reason in claim["reasons"])


def test_kit_review_enforces_project_denied_paths_on_run_and_resume(tmp_path):
    repo = init_git_repo(tmp_path)
    result = review_kit.run_review_kit(project_path=repo)
    (repo / ".hipson").mkdir()
    (repo / ".hipson" / "policy.json").write_text(json.dumps({"denied_paths": ["tracked.txt"]}))
    (repo / "tracked.txt").write_text("protected change\n")
    runs_before = sorted((repo / "runs").iterdir())
    with _raises(SystemExit, match="denied paths changed: tracked.txt"):
        review_kit.run_review_kit(project_path=repo)
    with _raises(SystemExit, match="denied paths changed: tracked.txt"):
        review_kit.resume_review_kit(run_path=result["run_dir"], rerun_steps=["verify"])
    assert sorted((repo / "runs").iterdir()) == runs_before


def test_d05_review_preflight_blocks_provider_and_success_bundle(tmp_path):
    repo = init_git_repo(tmp_path)
    preflight = packet_preflight.preflight_packet
    (repo / "sidecar.md").write_text("Finding: advisory review\n")
    with (
        patch.object(packet_preflight, "preflight_packet", side_effect=lambda path: preflight(path, max_chars=1)),
        patch.object(review_kit, "_run_sidecar", return_value=repo / "sidecar.md") as provider,
    ):
        with _raises(SystemExit, match="Packet preflight failed.*exceeds"):
            review_kit.run_review_kit(project_path=repo, ai_profile="cheap_review", run_sidecar=True)
    provider.assert_not_called()
    run_dir = next((repo / "runs").iterdir())
    assert json.loads((run_dir / "preflight.json").read_text())["ok"] is False
    assert not (run_dir / "manifest.json").exists()


def test_d05_resume_checks_preflight_before_provider(tmp_path):
    for cached_ok in [True, False]:
        case_dir = tmp_path / str(cached_ok)
        case_dir.mkdir()
        repo = init_git_repo(case_dir)
        result = review_kit.run_review_kit(project_path=repo, ai_profile="cheap_review")
        run_dir = Path(result["run_dir"])
        (repo / "sidecar.md").write_text("Finding: advisory review\n")
        (run_dir / "review-packet.md").write_text("x" * (packet_preflight.MAX_PACKET_CHARS + 1))
        if not cached_ok:
            cached = json.loads((run_dir / "preflight.json").read_text())
            cached["ok"] = False
            (run_dir / "preflight.json").write_text(json.dumps(cached))
        with patch.object(review_kit, "_run_sidecar", return_value=repo / "sidecar.md") as provider:
            with _raises(SystemExit, match="Packet preflight failed.*exceeds"):
                review_kit.resume_review_kit(run_path=run_dir, run_sidecar=True)
        provider.assert_not_called()
        assert json.loads((run_dir / "preflight.json").read_text())["ok"] is False
        # The older audit must not keep the run green once the current preflight failed.
        status = run_control.build_status(run_dir)
        assert status["status"] == "blocked" and status["preflight_ok"] is False
        assert status["recommended_next_command"].startswith("hipson packet preflight ")


def test_run_status_recommends_only_supported_commands(tmp_path):
    for gates in ({"release_claim_gate": "passed", "verification_gate": "passed"}, {"verification_gate": "blocked"}, {}):
        command = run_control._recommended_next_command(tmp_path, gates)
        assert command.split()[1] not in ("release", "autopilot"), command


def test_d19_resume_recomputes_decision_and_dependent_artifacts(tmp_path):
    repo = init_git_repo(tmp_path)
    initial = review_kit.run_review_kit(project_path=repo, decision="pending")
    run_dir = Path(initial["run_dir"])
    verify_before = (run_dir / "verify.json").read_bytes()
    for decision, expected in [("approved", "passed"), ("rejected", "blocked"), ("pending", "pending")]:
        result = review_kit.resume_review_kit(run_path=run_dir, decision=decision)
        report = json.loads((run_dir / "quality.json").read_text())
        audit = json.loads((run_dir / "audit.json").read_text())
        records = evidence.read_records(run_dir)
        assert report["decision"] == decision
        assert records[-1]["human_decision"]["outcome"] == decision
        assert audit["latest_gates"]["human_decision_gate"] == expected
        assert result["gates"] == audit["latest_gates"]
        assert json.loads((run_dir / "manifest.json").read_text())["gates"] == result["gates"]
        assert f"`human_decision_gate`: `{expected}`" in (run_dir / "summary.md").read_text()
        assert "quality.json" in result["updated_artifacts"]
    assert len(evidence.read_records(run_dir)) == 4
    assert (run_dir / "verify.json").read_bytes() == verify_before


def test_d06_timeout_stops_descendants_and_retains_output(tmp_path):
    if sys.platform == "win32":
        raise SkipTest("requires POSIX process inspection for verification descendant cleanup")
    pid_file = tmp_path / "child.pid"
    script = tmp_path / "spawn.py"
    script.write_text(
        "import subprocess, sys, time\nfrom pathlib import Path\nchild = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)\nPath(sys.argv[1]).write_text(str(child.pid))\nprint('timeout output café', flush=True)\nprint('timeout stderr', file=sys.stderr, flush=True)\ntime.sleep(60)\n",
        encoding="utf-8",
    )
    child_pid = None
    try:
        result = verification.run_verification(
            work_plan=_plan(tmp_path), commands=[shlex.join([sys.executable, str(script), str(pid_file)])], timeout=2
        )["results"][0]
        assert pid_file.exists(), result
        child_pid = int(pid_file.read_text())
        assert result["exit_code"] == 124
        with _raises(ProcessLookupError):
            os.kill(child_pid, 0)
        assert "timeout output café" in result["stdout"]
        assert "timeout stderr" in result["stderr"]
    finally:
        if child_pid is None and pid_file.exists():
            child_pid = int(pid_file.read_text())
        if child_pid is not None:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_d06_supervisor_stops_the_command_when_process_inspection_fails(tmp_path):
    if sys.platform == "win32":
        raise SkipTest("requires POSIX process inspection for verification descendant cleanup")
    pid_file = tmp_path / "command.pid"
    command = [sys.executable, "-c", f"import os, time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); "
               "time.sleep(60)"]
    # Inspection works until the command is running, then fails as if ps disappeared.
    harness = (
        "import os, sys\nfrom unittest.mock import patch\nfrom hipson import task_runner\n"
        "original = task_runner.ProcessTree.members\n"
        f"def members(self):\n    if os.path.exists({str(pid_file)!r}):\n        raise OSError('ps unavailable')\n"
        "    return original(self)\n"
        "with patch.object(task_runner.ProcessTree, 'members', members):\n"
        "    sys.exit(task_runner.supervise_command(sys.argv[1:]))\n"
    )
    pid = None
    try:
        finished = subprocess.run([sys.executable, "-P", "-c", harness, *command], capture_output=True, text=True,
                                  timeout=60)
        assert finished.returncode != 0, finished
        pid = int(pid_file.read_text())
        with _raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        if pid is not None:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_d06_timeout_decodes_bytes_before_bounding(tmp_path):
    failure = subprocess.TimeoutExpired(["tool"], 1, b"before timeout\xff" + b"x" * 14000, b"stderr\xff")
    with patch.object(verification, "supervise", side_effect=failure):
        result = verification._run_one(command="tool", cwd=tmp_path, timeout=1)
    assert result["exit_code"] == 124
    assert result["stdout"].startswith("before timeout\ufffd")
    assert result["stderr"] == "stderr\ufffd"
    assert "truncated" in result["stdout"]
    assert len(result["stdout"]) <= verification.MAX_OUTPUT_CHARS


def test_d18_quoted_shell_characters_are_argument_data(tmp_path):
    for code in ["print(1 > 0)", "print(';')", "print('&& | > < $(data) `data`')"]:
        command = shlex.join([sys.executable, "-c", code])
        assert verification.parse_command(command) == [sys.executable, "-c", code]
        result = verification.run_verification(work_plan=_plan(tmp_path), commands=[command])
        assert result["status"] == "passed", result


def test_d18_unquoted_shell_operators_are_rejected():
    for command in [
        "tool;other",
        "tool && other",
        "tool || other",
        "tool|other",
        "tool >file",
        "tool <file",
        "tool $(other)",
        "tool `other`",
        "tool\nother",
    ]:
        with _raises(SystemExit, match="shell control syntax"):
            verification.parse_command(command)


def test_d07_discovery_and_profiles_include_only_finite_package_scripts(tmp_path):
    for profile in ["full", "release"]:
        scripts = {
            name: "tool"
            for name in ["dev", "start", "serve", "watch", "preview", "test", "lint", "typecheck", "build", "check"]
        }
        (tmp_path / "package.json").write_text(json.dumps({"scripts": scripts}))
        expected = [f"npm run {name}" for name in ["test", "lint", "typecheck", "build", "check"]]
        assert project.discover_package_commands(tmp_path) == expected
        plan = workflow.build_work_plan(task="Review", project_path=str(tmp_path), include_diff=False)
        assert review_kit._verification_commands_for_profile(plan, verify_profile=profile, verify_limit=None) == [
            "git diff --check",
            *expected,
        ]


def test_d10_packet_followups_work_from_a_different_cwd(tmp_path):
    repo = init_git_repo(tmp_path)
    repo = repo.rename(tmp_path / "project with spaces")
    caller = tmp_path / "caller"
    caller.mkdir()
    with chdir(caller):
        plan = workflow.build_work_plan(
            task="Review", project_path=str(repo), include_diff=False, write_packet=True, ai_quality=True
        )
    packet = Path(plan["packet"]["path"])
    assert packet.is_absolute() and packet == repo / "runs" / "review-packet.md"
    assert packet.exists()
    args = shlex.split(plan["packet_preflight"]["command"])
    assert args[:4] == ["cd", str(repo), "&&", "hipson"]
    with chdir(caller), chdir(args[1]):
        rc, _, stderr = run_cli_inprocess(*args[4:])
    assert rc == 0, stderr
    assert Path(plan["packet_preflight"]["output"]).is_absolute()
    assert Path(plan["packet_preflight"]["output"]).exists()
    for key in ("dry_run_command", "run_command"):
        assert str(packet) in shlex.split(plan["ai_quality"][key])
    assert str(packet) in shlex.split(plan["packet"]["command"])


def test_d10_resolved_packet_paths_preserve_output_policy(tmp_path):
    with _raises(SystemExit, match="Path traversal"):
        workflow.build_work_plan(
            task="Review",
            project_path=str(tmp_path),
            include_diff=False,
            write_packet=True,
            packet_output="runs/../docs/packet.md",
        )
    assert not (tmp_path / "docs" / "packet.md").exists()


def _legacy_session_db(path):
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(session.INITIAL_SCHEMA)
        connection.execute(
            "INSERT INTO sessions VALUES ('legacy', 'Old session', '/tmp', NULL, 'active', 'then', 'then')"
        )
        connection.execute("INSERT INTO messages VALUES ('message', 'legacy', 'user', 'needle body', '{}', 'then')")


def test_d15_read_only_store_works_with_query_only_connection_and_old_schema(tmp_path):
    db = tmp_path / "legacy.sqlite"
    _legacy_session_db(db)
    before = db.read_bytes()
    with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA query_only = ON")
        store = session.SessionStore(path=db, connection=connection, read_only=True)
        assert store.list_sessions()[0]["id"] == "legacy"
        assert store.list_messages("legacy")[0]["content"] == "needle body"
        assert store.search_messages("needle")[0]["session_id"] == "legacy"
        assert store.list_approval_records(session_id="legacy") == []
    assert db.read_bytes() == before


def test_d15_session_cli_does_not_migrate_legacy_stores(tmp_path):
    for args in [("list",), ("show", "legacy"), ("search", "needle")]:
        db = tmp_path / f"legacy-{args[0]}.sqlite"
        _legacy_session_db(db)
        before = db.read_bytes()
        db.chmod(0o444)
        try:
            rc, stdout, stderr = run_cli_inprocess("session", *args, "--session-db", str(db), "--json")
            assert rc == 0, stderr
            assert "legacy" in stdout
            assert db.read_bytes() == before
        finally:
            db.chmod(0o644)


def test_d17_message_storage_is_bounded_redacted_and_marked(tmp_path):
    with closing(session.open_session_store(tmp_path / "session.sqlite")) as store:
        sid = store.create_session(str(tmp_path))
        store.add_message(sid, "user", "token=sk-example-secret1234567890 " + "x" * 10000)
        store.add_message(sid, "assistant", "short body")
        messages = store.list_messages(sid)
    content = messages[0]["content"]
    assert len(content) <= session.MAX_SESSION_STRING_CHARS
    assert content.endswith(f"... [truncated to {session.MAX_SESSION_STRING_CHARS} chars]")
    assert "sk-example-secret" not in content
    assert "[REDACTED]" in content
    assert messages[1]["content"] == "short body"


def test_d21_packet_commands_print_invalid_project_diagnostic(tmp_path):
    for kind in ["review", "exec"]:
        extra = ["--goal", "Fix"] if kind == "exec" else []
        rc, stdout, stderr = run_cli_inprocess("packet", kind, str(tmp_path / "missing"), "--title", "Review", *extra)
        assert rc == 1
        assert not stdout
        assert "does not exist" in stderr and "missing" in stderr


def test_d21_scan_many_validates_missing_registry_paths(tmp_path):
    for path_field in ["", "    path: ''\n"]:
        registry = tmp_path / "repos.yaml"
        registry.write_text(f"repos:\n  - name: broken\n{path_field}")
        rc, _, stderr = run_cli_inprocess("scan-many", str(registry))
        assert rc == 1
        assert "broken" in stderr and "path is required" in stderr


def test_d22_eval_preserves_hidden_and_absolute_paths_and_rejects_outside(tmp_path):
    repo = tmp_path / "repo"
    hidden = repo / ".github" / "workflows" / "ci.yml"
    hidden.parent.mkdir(parents=True)
    hidden.write_text("name: CI\n")
    inside = repo / "src" / "app.py"
    inside.parent.mkdir()
    inside.write_text("pass\n")
    outside = tmp_path / "outside.py"
    outside.write_text("pass\n")
    assert evals._hallucinated_files(f"`.github/workflows/ci.yml` `{hidden}` `{inside}` `./src/app.py`", repo) == []
    missing = evals._hallucinated_files(f"`{outside}` `../outside.py` `.github/workflows/missing.yml`", repo)
    assert missing == [str(outside), ".github/workflows/missing.yml"]


def test_d23_provider_doctor_requires_a_usable_provider_and_agent(tmp_path):
    for config in [
        {},
        {"providers": {}, "agents": {}},
        {
            "providers": {"remote": {"base_url": "https://example.com/v1", "api_key_env": "HIPSON_TEST_KEY"}},
            "agents": {},
        },
    ]:
        config_path = tmp_path / "providers.json"
        config_path.write_text(json.dumps(config))
        with (
            patch.dict(os.environ, {"HIPSON_TEST_KEY": "test-key"}),
            patch.object(provider_doctor.agents, "load_provider_envs", return_value=[]),
        ):
            result = provider_doctor.doctor_payload(config_path=str(config_path))
        assert result["ready_for_real_run"] is False
        assert result["config_ok"] is False
        assert any("at least one provider and one agent" in item for item in result["recommendations"])
