"""S4a evidence acceptance tests, also runnable by scripts/run_tests.py."""

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

from hipson import contracts, evidence, hermes, mcp_server, memory, run_control, workflow
from hipson.redaction import REDACTION, redact_value
from tests.test_hipson_helpers import REPO_ROOT, run_cli, run_cli_inprocess
from tests.test_ledger import _strict_errors


def _plan(project, work_id="work_evidence"):
    return {
        "artifact_kind": "hipson.work_plan",
        "schema_version": "1.0",
        "work_id": work_id,
        "project": str(project),
        "task": "Harden evidence",
        "repo_state": {"path": str(project)},
    }


def _work_file(project, work_id="work_evidence"):
    path = project / "runs" / f"{work_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_plan(project, work_id)), encoding="utf-8")
    return path


def _append(project, **kwargs):
    root = project / "runs"
    record = evidence.build_evidence_record(work_plan=_plan(project), ledger_root=root, **kwargs)
    evidence.append_record(root, record)
    return record


def _schema_errors(record, schema_name="evidence-record.schema.json"):
    schema = json.loads((REPO_ROOT / "schemas" / schema_name).read_text(encoding="utf-8"))
    return _strict_errors(schema, record, schema)


def _stdio(project, requests):
    stdout = io.StringIO()
    mcp_server.serve_stdio(project_path=project,
                           stdin=io.StringIO("".join(json.dumps(request) + "\n" for request in requests)),
                           stdout=stdout)
    return [json.loads(line)["result"] for line in stdout.getvalue().split("\n") if line.strip()]


def _tool(project, name, **arguments):
    result = _stdio(project, [{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": name, "arguments": arguments}}])[0]
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def _resource(project):
    request = {"jsonrpc": "2.0", "id": 1, "method": "resources/read",
               "params": {"uri": "hipson://latest-audit"}}
    result = _stdio(project, [request])[0]
    assert "contents" in result, result
    return json.loads(result["contents"][0]["text"])


def _concurrent_appends(tmp_path, *, fallback):
    count = 12
    script = """
import sys, time
from pathlib import Path
from hipson import evidence
root = Path(sys.argv[1])
if sys.argv[3] == 'fallback':
    evidence.fcntl = None
record = evidence.build_evidence_record(
    work_plan={'work_id': 'work_concurrent', 'task': 'Concurrent append'},
    ledger_root=root, actor='worker-' + sys.argv[2], task_id='T-0003')
(root / (sys.argv[2] + '.ready')).touch()
deadline = time.monotonic() + 20
while not (root / 'go').exists():
    if time.monotonic() > deadline:
        raise RuntimeError('Concurrent append barrier timed out')
    time.sleep(0.01)
evidence.append_record(root, record)
print(record['record_hash'])
"""
    children = []
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    try:
        for index in range(count):
            children.append(subprocess.Popen(
                [sys.executable, "-c", script, str(tmp_path), str(index), "fallback" if fallback else "native"],
                env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            ))
        deadline = time.monotonic() + 20
        while len(list(tmp_path.glob("*.ready"))) < count:
            assert all(child.poll() is None for child in children), "Writer failed before barrier"
            assert time.monotonic() < deadline, "Writers did not reach barrier"
            time.sleep(0.01)
        (tmp_path / "go").touch()
        hashes = []
        for child in children:
            stdout, stderr = child.communicate(timeout=30)
            assert child.returncode == 0, stderr
            hashes.append(stdout.strip())
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)
    records = evidence.read_records(tmp_path)
    assert len(records) == count
    assert len({record["event_id"] for record in records}) == count
    assert set(hashes) == {record["record_hash"] for record in records}
    assert records[0]["previous_hash"] == ""
    assert all(current["previous_hash"] == previous["record_hash"]
               for previous, current in zip(records[:-1], records[1:], strict=True))
    assert evidence.verify_chain(tmp_path)["ok"] is True
    rc, stdout, stderr = run_cli_inprocess("evidence", "verify", "--ledger-dir", str(tmp_path), "--json")
    assert rc == 0, stderr
    assert json.loads(stdout)["record_count"] == count


def test_evidence_concurrent_processes_keep_chain(tmp_path: Path):
    _concurrent_appends(tmp_path, fallback=False)


def test_evidence_lock_backend_matches_platform():
    if sys.platform == "win32":
        assert evidence.fcntl is None
    else:
        import fcntl

        assert evidence.fcntl is fcntl


def test_evidence_portable_lock_serializes_processes(tmp_path: Path):
    _concurrent_appends(tmp_path, fallback=True)
    assert not (tmp_path / ".evidence.lock.d").exists()


def test_evidence_fallback_lock_timeout_and_exception_cleanup(tmp_path: Path):
    lock = tmp_path / ".evidence.lock.d"
    lock.mkdir()
    with patch.object(evidence, "fcntl", None), patch.object(evidence.time, "monotonic", side_effect=[0, 31]):
        try:
            evidence.append_record(tmp_path, {})
        except TimeoutError as exc:
            assert "stale lock" in str(exc)
        else:
            raise AssertionError("Stale lock must fail closed")
    assert not evidence.ledger_path(tmp_path).exists()
    lock.rmdir()
    with patch.object(evidence, "fcntl", None), patch.object(evidence, "latest_hash", side_effect=OSError("read failed")):
        try:
            evidence.append_record(tmp_path, {})
        except OSError:
            pass
        else:
            raise AssertionError("Expected read failure")
    assert not lock.exists()
    record = evidence.build_evidence_record(work_plan=_plan(tmp_path), ledger_root=tmp_path)
    evidence.append_record(tmp_path, record)
    assert evidence.verify_chain(tmp_path)["ok"] is True


def test_evidence_verify_detects_tampering_links_and_malformed_records(tmp_path: Path):
    _append(tmp_path)
    _append(tmp_path)
    root = tmp_path / "runs"
    path = evidence.ledger_path(root)
    records = evidence.read_records(root)
    records[0]["work"]["task"] = "Edited after append"
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    result = run_cli(tmp_path, "evidence", "verify", "--json")
    assert result.returncode == 1, result.stderr
    errors = json.loads(result.stdout)["errors"]
    assert any(error["line"] == 1 and "record_hash" in error["error"] for error in errors)
    records[0]["record_hash"] = contracts.stable_hash({k: v for k, v in records[0].items() if k != "record_hash"})
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    assert any(error["line"] == 2 and "previous_hash" in error["error"] for error in evidence.verify_chain(root)["errors"])
    path.write_text('not-json\n[]\n{"record_hash": "bad"}\n', encoding="utf-8")
    result = run_cli(tmp_path, "evidence", "verify", "--ledger-dir", str(root))
    assert result.returncode == 1, result.stderr
    assert "line 1: Invalid JSON" in result.stdout
    assert "line 2: Record must be an object" in result.stdout
    assert "line 3:" in result.stdout


def test_evidence_verify_empty_ledger_does_not_create_directory(tmp_path: Path):
    missing = tmp_path / "missing"
    result = evidence.verify_chain(missing)
    assert result["ok"] is True and result["record_count"] == 0
    assert not missing.exists()


def test_evidence_legacy_schema_and_hashes_preserved(tmp_path: Path):
    legacy = (REPO_ROOT / "tests" / "fixtures" / "evidence-v1.jsonl").read_bytes()
    path = evidence.ledger_path(tmp_path)
    path.write_bytes(legacy)
    original = evidence.read_records(tmp_path)
    assert evidence.verify_chain(tmp_path)["ok"] is True
    for record in original:
        assert "actor" not in record and "task_id" not in record
        assert not _schema_errors(record)
    record = evidence.build_evidence_record(work_plan=_plan(tmp_path), ledger_root=tmp_path)
    evidence.append_record(tmp_path, record)
    assert path.read_bytes().startswith(legacy)
    assert record["previous_hash"] == original[-1]["record_hash"]
    assert "actor" not in record and "task_id" not in record
    assert not _schema_errors(record)
    assert evidence.verify_chain(tmp_path)["ok"] is True


def test_evidence_cli_attribution_roundtrip_and_filters(tmp_path: Path):
    work = _work_file(tmp_path)
    records = []
    for task_id, actor in (("T-0003", "codex"), ("T-99999", "reviewer"), ("T-0003", "lead")):
        rc, stdout, stderr = run_cli_inprocess("evidence", "append", "--work", str(work), "--task-id", task_id,
                                               "--actor", actor, "--json")
        assert rc == 0, stderr
        record = json.loads(stdout)["record"]
        assert record["task_id"] == task_id and record["actor"] == actor
        assert not _schema_errors(record)
        records.append(record)
    for extra in ((), ("--work", str(work))):
        rc, stdout, stderr = run_cli_inprocess("evidence", "show", "--ledger-dir", str(tmp_path / "runs"),
                                               "--task-id", "T-0003", "--json", *extra)
        assert rc == 0, stderr
        assert json.loads(stdout)["records"] == [records[0], records[2]]
    rc, stdout, stderr = run_cli_inprocess("evidence", "show", "--work", str(work), "--task-id", "T-0003", "--latest", "--json")
    assert rc == 0, stderr
    assert json.loads(stdout)["records"] == [records[2]]
    for task_id, expected in (("T-0003", [records[0], records[2]]), ("T-1234", [])):
        rc, stdout, stderr = run_cli_inprocess("audit", "show", "--work", str(work), "--task-id", task_id, "--json")
        assert rc == 0, stderr
        assert json.loads(stdout)["evidence_records"] == expected


def test_evidence_rejects_invalid_task_ids_and_actor_types(tmp_path: Path):
    work = _work_file(tmp_path)
    for task_id in ("T-1", "T-0001\n", "t-0003", "A-0001", "", 3):
        try:
            evidence.build_evidence_record(work_plan=_plan(tmp_path), ledger_root=tmp_path, task_id=task_id)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Accepted invalid task id: {task_id!r}")
        assert _schema_errors({**_append(tmp_path), "task_id": task_id})
    for command in (("evidence", "append"), ("evidence", "show"), ("audit", "show")):
        rc, _, stderr = run_cli_inprocess(*command, "--work", str(work), "--task-id", "invalid")
        assert rc != 0 and "task_id" in stderr
    for record in ({"task_id": "invalid"}, {"actor": 123}):
        try:
            evidence.append_record(tmp_path / "invalid", record)
        except ValueError:
            pass
        else:
            raise AssertionError("Accepted invalid attribution")
    assert not (tmp_path / "invalid").exists()


def test_evidence_cli_recursively_redacts_all_persisted_values(tmp_path: Path):
    secret = "sk-evidence-secret1234567890"
    work = _work_file(tmp_path)
    plan = _plan(tmp_path)
    plan["ai_quality"] = {"enabled": True, "nested": [{"note": secret}]}
    plan["repo_state"]["branch"] = secret
    plan["task"] = f"Inspect {secret}"
    work.write_text(json.dumps(plan), encoding="utf-8")
    payload = {"status": "passed", "nested": [{"detail": [secret, {"message": "password=hunter2"}]}],
               "auth": {"Password": "short-pass", "TOKEN": 123, "api_key": {"raw": "short-key"}}, "ok": True}
    artifact = tmp_path / "raw.json"
    artifact.write_text(json.dumps(payload), encoding="utf-8")
    rc, stdout, stderr = run_cli_inprocess(
        "evidence", "append", "--work", str(work), "--verification", str(artifact),
        "--quality-report", str(artifact), "--quality-eval", str(artifact),
        "--actor", secret, "--decision", secret, "--sidecar-report", secret, "--json",
    )
    assert rc == 0, stderr
    text = evidence.ledger_path(tmp_path / "runs").read_text(encoding="utf-8")
    assert secret not in text and "hunter2" not in text and REDACTION in text
    record = json.loads(stdout)["record"]
    assert record == evidence.read_records(tmp_path / "runs")[0]
    assert record["actor"] == REDACTION
    assert record["verification"]["nested"][0]["detail"][0] == REDACTION
    assert record["quality"]["report"]["nested"][0]["detail"][0] == REDACTION
    assert record["quality"]["eval"]["nested"][0]["detail"][0] == REDACTION
    for persisted in (record["verification"], record["quality"]["report"], record["quality"]["eval"]):
        assert persisted["auth"] == dict.fromkeys(("Password", "TOKEN", "api_key"), REDACTION)
    assert "short-pass" not in text and "short-key" not in text
    assert secret in artifact.read_text(encoding="utf-8"), "Input artifacts must remain untouched"
    assert evidence.verify_chain(tmp_path / "runs")["ok"] is True
    # The persistence boundary also catches fields added after building a record.
    record["free_text"] = {"notes": [secret, True, 42, None], "client_secret": ["short-client-secret"]}
    evidence.append_record(tmp_path / "runs", record)
    assert record["free_text"]["notes"] == [REDACTION, True, 42, None]
    assert record["free_text"]["client_secret"] == REDACTION
    assert secret not in evidence.ledger_path(tmp_path / "runs").read_text(encoding="utf-8")
    assert evidence.verify_chain(tmp_path / "runs")["ok"] is True


def test_work_output_redacts_task_commands_and_nested_fields(tmp_path: Path):
    secret = "sk-work-secret1234567890123"
    output = tmp_path / "runs" / "work.json"
    rc, stdout, stderr = run_cli_inprocess("work", "--project", str(tmp_path), "--task", f"Review {secret}",
                                           "--no-diff", "--verification", f"echo {secret}",
                                           "--work-output", str(output), "--json")
    assert rc == 0, stderr
    assert secret not in output.read_text(encoding="utf-8")
    assert secret not in stdout
    plan = workflow.build_work_plan(task="Review", project_path=str(tmp_path), include_diff=False)
    plan["ai_quality"]["cautions"] = [secret, "password=hunter2"]
    plan["ai_quality"]["auth"] = {"private_key": {"raw": "short-private-key"}, "cookie": False}
    plan["changed_files"] = [secret]
    workflow.write_work_plan(plan, str(output), cwd=tmp_path)
    text = output.read_text(encoding="utf-8")
    assert secret not in text and "hunter2" not in text
    assert "short-private-key" not in text
    assert json.loads(text)["ai_quality"]["auth"] == {"private_key": REDACTION, "cookie": REDACTION}
    assert plan["ai_quality"]["auth"]["cookie"] is False
    assert plan["ai_quality"]["cautions"][0] == secret, "Do not mutate caller payloads"
    assert redact_value({"nested": [(secret, False), None, 42]}) == {"nested": [[REDACTION, False], None, 42]}


def test_release_claim_copies_redacted_for_allowed_and_blocked(tmp_path: Path):
    secret = "sk-claim-secret123456789012"
    for allowed in (True, False):
        audit = {"latest_gates": {"release_claim_gate": "passed" if allowed else "blocked", "verification_gate": "passed",
                                  "auth": {"credentials": ["short-claim-secret"], "cookie": None}}}
        (tmp_path / "audit.json").write_text(json.dumps(audit), encoding="utf-8")
        claim = run_control.build_release_claim(tmp_path, claim=f"Ready {secret}", human_decision="approved")
        assert claim["allowed"] is allowed
        assert secret not in json.dumps(claim)
        assert claim["safe_to_claim"] == ([f"Ready {REDACTION}"] if allowed else [])
        assert claim["unsafe_to_claim"] == ([] if allowed else [f"Ready {REDACTION}"])
        assert claim["gates"]["auth"] == {"credentials": REDACTION, "cookie": REDACTION}
        claim["extra"] = {"access_key": {"raw": "added-after-build"}}
        output = run_control.write_release_claim(tmp_path, claim)
        assert secret not in output.read_text(encoding="utf-8")
        assert json.loads(output.read_text(encoding="utf-8"))["extra"]["access_key"] == REDACTION
        assert claim["extra"]["access_key"] == {"raw": "added-after-build"}


def test_mcp_evidence_and_audit_use_work_project_and_filter_task(tmp_path: Path):
    project = tmp_path / "target"
    server_project = tmp_path / "server"
    server_project.mkdir()
    work = _work_file(project)
    artifact = tmp_path / "verification.json"
    secret = "sk-mcp-secret12345678901234"
    artifact.write_text(json.dumps({"status": "passed", "nested": [secret]}), encoding="utf-8")
    appended = _tool(server_project, "evidence.append", work=str(work), verification=str(artifact), quality_report=str(artifact),
                     approved=True, actor="codex", task_id="T-0003")
    assert appended["ledger"] == str(project / "runs" / "evidence.jsonl")
    assert appended["record"]["actor"] == "codex" and appended["record"]["task_id"] == "T-0003"
    assert secret not in json.dumps(appended)
    _append(project, task_id="T-0004")
    audit = _tool(server_project, "audit.show", work=str(work), task_id="T-0003")
    assert audit["evidence_records"] == [appended["record"]]
    assert not (server_project / "runs").exists()
    override = tmp_path / "explicit-ledger"
    appended = _tool(server_project, "evidence.append", work=str(work), approved=True, actor="reviewer", ledger_root=str(override))
    audit = _tool(server_project, "audit.show", work=str(work), ledger_root=str(override))
    assert audit["evidence_records"] == [appended["record"]]
    assert "task_id" not in appended["record"]
    schemas = mcp_server.MCP_INPUT_SCHEMAS
    assert schemas["evidence.append"]["properties"]["actor"]["type"] == "string"
    assert schemas["evidence.append"]["properties"]["task_id"]["pattern"] == evidence.TASK_ID_PATTERN


def test_mcp_invalid_attribution_errors_are_recoverable(tmp_path: Path):
    work = _work_file(tmp_path)
    requests = []
    for index, (name, extra) in enumerate((("evidence.append", {"task_id": "invalid"}),
                                           ("evidence.append", {"actor": 3}), ("audit.show", {"task_id": "T-1"})), 1):
        requests.append({"jsonrpc": "2.0", "id": index, "method": "tools/call", "params": {
            "name": name, "arguments": {"work": str(work), "approved": True, **extra},
        }})
    results = _stdio(tmp_path, requests)
    assert all(result["isError"] is True for result in results)
    assert not evidence.ledger_path(tmp_path / "runs").exists()


def test_mcp_latest_audit_resource_via_subprocess_stdio(tmp_path: Path):
    empty = _resource(tmp_path)
    assert empty["latest_status"] == "no-evidence" and empty["evidence_records"] == []
    assert not _schema_errors(empty, "audit-bundle.schema.json")
    assert empty["work"]["work_id"] == ""
    assert empty["work"]["repo"]["path"] == str(tmp_path)
    assert set(empty["latest_gates"].values()) == {"no-evidence"}
    assert not (tmp_path / "runs").exists()
    first = _append(tmp_path, actor="codex", task_id="T-0003")
    assert _resource(tmp_path)["evidence_records"] == [first]
    # Run bundles use their own ledger under runs/<work_id>/.
    root = tmp_path / "runs" / "work_latest"
    record = evidence.build_evidence_record(work_plan=_plan(tmp_path, "work_latest"), ledger_root=root,
                                            verification={"status": "passed"}, task_id="T-0003")
    evidence.append_record(root, record)
    stamp = time.time_ns() + 1_000_000
    os.utime(evidence.ledger_path(root), ns=(stamp, stamp))
    request = {"jsonrpc": "2.0", "id": 1, "method": "resources/read", "params": {"uri": "hipson://latest-audit"}}
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    result = subprocess.run([sys.executable, "-m", "hipson.cli", "mcp", "serve", "--project", str(tmp_path), "--stdio"],
                            input=json.dumps(request) + "\n", capture_output=True, text=True, env=env, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    payload = json.loads(json.loads(result.stdout)["result"]["contents"][0]["text"])
    assert payload["evidence_records"] == [record]
    assert payload["latest_status"] == "passed"
    assert payload["work"]["work_id"] == "work_latest"


def test_mcp_latest_audit_reads_saved_bundles_and_ignores_other_projects(tmp_path: Path):
    work = _work_file(tmp_path)
    record = _append(tmp_path)
    bundle = evidence.audit_bundle(work_path=str(work), ledger_root=tmp_path / "runs")
    saved = tmp_path / "runs" / "audit.json"
    saved.write_text(json.dumps(bundle), encoding="utf-8")
    stamp = time.time_ns() + 1_000_000
    os.utime(saved, ns=(stamp, stamp))
    assert _resource(tmp_path) == bundle
    saved.unlink()
    foreign_root = tmp_path / "runs" / "foreign"
    foreign = evidence.build_evidence_record(work_plan=_plan(tmp_path / "other", "work_foreign"), ledger_root=foreign_root)
    evidence.append_record(foreign_root, foreign)
    # The foreign candidate must be newer, even when rapid writes share a filesystem timestamp.
    stamp = evidence.ledger_path(tmp_path / "runs").stat().st_mtime_ns + 1_000_000_000
    os.utime(evidence.ledger_path(foreign_root), ns=(stamp, stamp))
    assert _resource(tmp_path)["evidence_records"] == [record]


def test_redact_value_masks_whole_credential_keys_of_any_type():
    keys = (
        "password", "PassWd", "SECRET", "Token", "api_key", "APIKEY", "api-key",
        "access_key", "private_key", "Authorization", "Cookie", "client_secret",
        "credential", "Credentials", "access_token", "refresh_token", "access_key_id",
        "secret_access_key", "AWS_SECRET_ACCESS_KEY", "OPENROUTER_API_KEY",
    )
    for key in keys:
        for value in ("short", 42, False, None, ["short"], {"raw": "short"}, ("short",)):
            original = {"nested": [{key: value}]}
            assert redact_value(original) == {"nested": [{key: REDACTION}]}, (key, value)
            assert original["nested"][0][key] == value
    metadata = {"token_count": 42, "secretary": "Ada", "tokenizer": "bpe",
                "requires_credentials": True, "credentials_path": "config.json",
                "password_policy": {"length": 12}, "private_key_path": "key.txt"}
    assert redact_value(metadata) == metadata
    assert redact_value({"note": "password=short", "nested": ["sk-test1234567890123456"]}) == {
        "note": f"password={REDACTION}", "nested": [REDACTION],
    }


def test_evidence_unicode_separators_are_not_jsonl_boundaries(tmp_path: Path):
    work = _work_file(tmp_path)
    root = tmp_path / "runs"
    separators = "before\u0085middle\u2028next\u2029after"
    records = [_append(tmp_path, actor=separators), _append(tmp_path, actor=separators)]
    text = evidence.ledger_path(root).read_text(encoding="utf-8")
    assert text.count("\n") == 2 and text.count(separators) == 2
    assert evidence.read_records(root) == records
    assert evidence.verify_chain(root)["ok"] is True
    for command in (("evidence", "verify", "--ledger-dir", str(root)),
                    ("evidence", "show", "--work", str(work)),
                    ("audit", "show", "--work", str(work))):
        rc, stdout, stderr = run_cli_inprocess(*command, "--json")
        assert rc == 0, stderr
        payload = json.loads(stdout)
        if command[:2] == ("evidence", "verify"):
            assert payload["ok"] is True and payload["record_count"] == 2
        else:
            assert payload["records" if command[0] == "evidence" else "evidence_records"] == records
    assert _resource(tmp_path)["evidence_records"] == records
    assert run_control._latest_evidence_record(root) == records[-1]
    with evidence.ledger_path(root).open("a", encoding="utf-8") as handle:
        handle.write("[]\n")
    assert evidence.verify_chain(root)["errors"] == [{"line": 3, "error": "Record must be an object"}]


def test_other_jsonl_readers_preserve_unicode_separators(tmp_path: Path):
    row = {"message": "before\u0085middle\u2028next\u2029after"}
    path = tmp_path / "notes.jsonl"
    memory.append_jsonl(path, row)
    assert memory.read_jsonl(path) == [row]
    event_path = hermes.append_bus_event(row, hipson_home=tmp_path)
    assert hermes.read_bus_events(hipson_home=tmp_path) == [row]
    assert hermes._event_count(event_path) == 1


def test_evidence_readers_reject_malformed_decoded_shapes(tmp_path: Path):
    valid = evidence.build_evidence_record(work_plan=_plan(tmp_path), ledger_root=tmp_path)
    malformed = [[], None, {}, {**valid, "work": []}, {**valid, "verification": []},
                 {**valid, "quality": {"summary": []}}, {**valid, "gates": []},
                 {**valid, "work": {**valid["work"], "work_id": []}},
                 {**valid, "actor": 42}, {**valid, "task_id": None}]
    for record in malformed:
        evidence.ledger_path(tmp_path).write_text(json.dumps(record) + "\n", encoding="utf-8")
        try:
            evidence.read_records(tmp_path)
        except ValueError as exc:
            assert "line 1:" in str(exc)
        else:
            raise AssertionError(f"Accepted malformed record: {record}")
        checked = evidence.verify_chain(tmp_path)
        assert checked["ok"] is False and checked["errors"][0]["line"] == 1


def test_evidence_append_rejects_unreadable_records_without_changing_bytes(tmp_path: Path):
    root = tmp_path / "runs"
    first = _append(tmp_path)
    path = evidence.ledger_path(root)
    before = path.read_bytes()
    for changes, message in (
        ({"verification": {"status": False}}, "Verification.status must be str"),
        ({"quality": {"summary": []}}, "Quality.summary must be dict"),
        ({"work": {}}, "Work.schema_version must be str"),
        ({"gates": {}}, "Gates.verification_gate must be str"),
        ({"claims": {}}, "Claims.safe must be list"),
        ({"artifact_kind": "other"}, "Record.artifact_kind must be hipson.evidence_record"),
    ):
        record = evidence.build_evidence_record(work_plan=_plan(tmp_path), ledger_root=root)
        record.update(changes)
        original = json.loads(json.dumps(record))
        try:
            evidence.append_record(root, record)
        except ValueError as exc:
            assert message in str(exc)
        else:
            raise AssertionError(f"Append accepted an unreadable record: {changes}")
        assert path.read_bytes() == before
        assert record == original
        assert evidence.read_records(root) == [first]
    second = _append(tmp_path)
    assert second["previous_hash"] == first["record_hash"]
    assert evidence.verify_chain(root)["ok"] is True


def test_evidence_cli_rejects_bad_verification_before_creating_ledger(tmp_path: Path):
    work = _work_file(tmp_path)
    artifact = tmp_path / "verification.json"
    artifact.write_text('{"status": false}', encoding="utf-8")
    rc, stdout, stderr = run_cli_inprocess(
        "evidence", "append", "--work", str(work), "--verification", str(artifact), "--json",
    )
    assert rc != 0
    assert "Verification.status must be str" in stdout + stderr
    assert not evidence.ledger_path(tmp_path / "runs").exists()


def test_evidence_append_validates_final_redacted_shape(tmp_path: Path):
    record = evidence.build_evidence_record(work_plan=_plan(tmp_path), ledger_root=tmp_path)
    record["unknowns"] = ("token=private-value",)  # Redaction converts tuples to JSON arrays.
    record["record_hash"] = False  # Finalization replaces caller-provided hashes.
    evidence.append_record(tmp_path, record)
    assert record["unknowns"] == ["token=[REDACTED]"]
    assert evidence.read_records(tmp_path) == [record]
    assert evidence.verify_chain(tmp_path)["ok"] is True


def test_mcp_latest_audit_isolates_corrupt_candidates_and_keeps_serving(tmp_path: Path):
    work = _work_file(tmp_path)
    record = _append(tmp_path)
    bundle = evidence.audit_bundle(work_path=str(work), ledger_root=tmp_path / "runs")
    saved = tmp_path / "runs" / "audit.json"
    saved.write_text(json.dumps(bundle), encoding="utf-8")
    evidence.ledger_path(tmp_path / "runs").unlink()
    malformed = tmp_path / "runs" / "broken"
    malformed.mkdir()
    bad_ledger = evidence.ledger_path(malformed)
    bad_ledger.write_text("[]\n", encoding="utf-8")
    bad_audit = malformed / "audit.json"
    request = {"jsonrpc": "2.0", "id": 1, "method": "resources/read",
               "params": {"uri": "hipson://latest-audit"}}
    followup = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    for data in (b"[]", b"{broken", b"\xff", json.dumps({**bundle, "latest_gates": []}).encode(),
                 json.dumps({**bundle, "evidence_records": [[]]}).encode()):
        bad_audit.write_bytes(data)
        stamp = time.time_ns() + 1_000_000
        for path in (bad_ledger, bad_audit):
            os.utime(path, ns=(stamp, stamp))
        results = _stdio(tmp_path, [request, followup])
        payload = json.loads(results[0]["contents"][0]["text"])
        assert payload["evidence_records"] == [record]
        assert {item["path"] for item in payload["candidate_errors"]} == {str(bad_ledger), str(bad_audit)}
        assert all(item["error"] for item in payload["candidate_errors"])
        assert any("Corrupt" in item for item in payload["unknowns"])
        assert not _schema_errors(payload, "audit-bundle.schema.json")
        assert results[1]["tools"]
    # A failed direct ledger read also returns an error without ending stdio.
    direct = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "audit.show", "arguments": {"work": str(work), "ledger_root": str(malformed)},
    }}
    results = _stdio(tmp_path, [direct, followup])
    assert results[0]["isError"] is True and results[1]["tools"]
    saved.unlink()
    payload = _resource(tmp_path)
    assert payload["latest_status"] == "no-evidence" and len(payload["candidate_errors"]) == 2
    assert not _schema_errors(payload, "audit-bundle.schema.json")
