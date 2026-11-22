"""Usage counters from the sanitized 2026-11-19 native probe evidence and ledger reads."""

import argparse
import json
import os
import time
from contextlib import closing, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from hipson import agent_runtimes, cli, ledger, mcp_server, task_projection, task_runner, task_verification
from tests.test_ledger import assert_strict_schema

FIXTURES = Path(__file__).parent / "fixtures" / "runtime-usage"
NONCE = "ledger-run-fixture"


def _expected(source, inp, out, cached, written, reasoning, total):
    return {"source": source, "reason": None,
            "tokens": dict(zip(agent_runtimes.TOKEN_FIELDS, (inp, out, cached, written, reasoning, total), strict=True))}


def test_recorded_native_outputs(tmp_path):
    rollout = (FIXTURES / "codex-rollout.jsonl").read_text(encoding="utf-8")
    agy = (FIXTURES / "agy-result.json").read_text(encoding="utf-8")
    claude = (FIXTURES / "claude-stream.jsonl").read_text(encoding="utf-8")
    expected = _expected("codex_rollout", 57653, 940, 38016, 0, 281, 58593)
    assert task_runner._rollout_usage(rollout, NONCE, failed=False) == expected
    raw = json.loads(rollout.splitlines()[2])["payload"]["info"]["total_token_usage"]
    codex = json.dumps({"type": "turn.completed", "usage": raw})
    assert agent_runtimes.stdout_usage("codex", codex, failed=False) == expected
    assert agent_runtimes.stdout_usage("agy", agy, failed=True) == _expected(
        "agy_json", 37638, 3193, 127884, None, 2432, 40831)
    assert agent_runtimes.stdout_usage("claude", claude, failed=False) == _expected(
        "claude_stream_json", 12, 1266, 198344, 41009, 46, 240631)
    # A resumed transcript can retain the old cumulative report and repeat its new report.
    baseline = json.loads(rollout.splitlines()[2])
    baseline["payload"]["info"]["total_token_usage"] = {**dict.fromkeys(raw, 0), "input_tokens": 2, "total_tokens": 2}
    repeated = "\n".join((json.dumps(baseline), rollout, rollout.splitlines()[2]))
    assert task_runner._rollout_usage(repeated, NONCE, failed=False) == _expected(
        "codex_rollout", 57651, 940, 38016, 0, 281, 58591)

    store_path = tmp_path / "db"
    with closing(ledger.Ledger(store_path)) as store:
        task = store.open_task(project=tmp_path, title="Rollout usage", objective="Record the run")["id"]
        assignment = store.assign(task, actor="codex", role="builder", mode="ro")["id"]
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    other = sessions / "other.jsonl"
    other.write_text(rollout.replace(NONCE, "another-run"), encoding="utf-8")
    matched = sessions / "matched.jsonl"
    matched.write_text(rollout, encoding="utf-8")
    args = argparse.Namespace(ref=[], rollout="codex", result_file=None)
    with patch.dict(os.environ, {"CODEX_HOME": str(tmp_path)}):
        task_runner._finish(store_path, args, assignment, 0, 0, "", NONCE, 0.0, {}, str(tmp_path))
    with closing(ledger.Ledger(store_path, read_only=True)) as store:
        usage = store.activity(assignment)["usage"]
        assert usage == {**expected, "elapsed_seconds": usage["elapsed_seconds"]}
        assert usage["elapsed_seconds"] >= 0
        event = store.events(task)[-1]
        assert "model=fixture-model effort=high" in event["summary"]
        assert event["refs"] == [assignment, str(matched)]


def test_usage_missing_reasons_and_unknown_counters(tmp_path):
    for name, source in agent_runtimes.USAGE_SOURCES.items():
        report = agent_runtimes.stdout_usage(name, "{}", failed=False)
        assert report == {"source": source, "tokens": None, "reason": "runtime_reported_no_usage"}
        assert agent_runtimes.stdout_usage(name, "", failed=True) == {**report, "reason": "runtime_error"}
    assert agent_runtimes.stdout_usage("agy", '{"usage":{"input_tokens":0,"output_tokens":true,"thinking_tokens":-1},'
                                           '"brief_tokens_estimate":123}', failed=False) == _expected(
        "agy_json", 0, None, None, None, None, None)
    assert agent_runtimes.stdout_usage("claude", '{"type":"assistant","usage":{"input_tokens":99}}', failed=True) == {
        "source": "claude_stream_json", "tokens": None, "reason": "runtime_error"}
    with patch.dict(os.environ, {"CODEX_HOME": str(tmp_path)}):
        _, missing = task_runner._rollout_facts(NONCE, time.time(), [], failed=False)
    assert missing == {"source": "codex_rollout", "tokens": None, "reason": "rollout_not_found"}
    assert task_runner._rollout_usage(NONCE, NONCE, failed=False) == {
        **missing, "reason": "runtime_reported_no_usage"}

    path = tmp_path / "usage.json"
    path.write_text('{"source":"claude_stream_json","tokens":{"input_tokens":0,"output_tokens":true,'
                    '"brief_tokens_estimate":123}}', encoding="utf-8")
    assert task_runner._adapter_usage(path, "claude_stream_json") == _expected(
        "claude_stream_json", 0, None, None, None, None, None)
    assert task_runner._adapter_usage(path, "agy_json") == {
        "source": "agy_json", "tokens": None, "reason": "runtime_error"}


def _show(db, task, *flags):
    stdout = StringIO()
    with redirect_stdout(stdout):
        assert cli.main(["task", "show", "--task", task, "--db", str(db), *flags]) == 0
    return stdout.getvalue()


def test_task_show_usage_totals_include_unpaged_assignments_and_preserve_full_view(tmp_path):
    db = tmp_path / "db"
    with closing(ledger.Ledger(db)) as store:
        task = store.open_task(project=tmp_path, title="Counters", objective="Count usage")["id"]
        for i, role in enumerate(["builder", "reviewer", *["explorer"] * 7, "builder", "builder"]):
            with patch.object(ledger, "_timestamp", return_value=f"2026-11-22T00:00:{i*2:02d}Z"):
                assignment = store.assign(task, actor="worker", role=role, mode="ro")["id"]
            if i == 10:
                continue  # Active attempts count, but have no elapsed time or missing-usage reason yet.
            usage = (_expected("agy_json", 8, 2, 12, None, 0, 10) if i == 0 else
                     _expected("codex_rollout", 20, 5, 4, None, 2, 25) if i == 9 else None)
            with patch.object(ledger, "_timestamp", return_value=f"2026-11-22T00:00:{i*2+1:02d}Z"):
                store.finish(assignment, result="passed", summary="Finished", usage=usage)
        full_before_usage = store.show(task)

    full = json.loads(_show(db, task, "--detail", "full", "--json"))
    assert_strict_schema("task.schema.json", full)
    totals = full.pop("usage")
    assert full == full_before_usage
    assert totals == {"attempts": {"builder": 3, "reviewer": 1, "explorer": 7},
                      "repair_rounds": 2, "without_usage": 8,
                      "tokens": _expected("", 28, 7, 16, None, 2, 35)["tokens"],
                      "missing_reasons": {"not_an_agent_runtime": 8}, "elapsed_seconds": 10.0}
    assert "Usage: " in _show(db, task, "--detail", "full")
    with patch.object(task_verification, "project_state", side_effect=AssertionError("fingerprint computed")):
        compact = json.loads(_show(db, task, "--detail", "compact", "--json"))
        text = _show(db, task, "--detail", "compact")
        assignments = json.loads(_show(db, task, "--section", "assignments", "--limit", "1", "--cursor", "1"))
        mcp = mcp_server._task_read_projection("task.show", {"task": task, "section": "assignments"}, db)
        with closing(ledger.Ledger(db, read_only=True)) as store:
            board = task_projection.project_list(store, kind="task.board", include_terminal=True)
    assert compact["usage"] == assignments["usage"] == mcp["usage"] == board["tasks"][0]["usage"] == totals
    assert compact["omitted_assignments"] == 3 and len(assignments["records"]) == 1
    assert assignments["records"][0]["usage"]["reason"] == "not_an_agent_runtime"
    assert len(task_projection.dumps(assignments).encode()) <= task_projection.SECTION_BUDGET
    assert "Usage: " in text
