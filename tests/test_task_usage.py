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


def test_resumed_rollout_counter_below_baseline_stays_unknown():
    rollout = (FIXTURES / "codex-rollout.jsonl").read_text(encoding="utf-8")
    baseline = json.loads(rollout.splitlines()[2])
    baseline["payload"]["info"]["total_token_usage"]["cached_input_tokens"] += 1
    assert task_runner._rollout_usage(f"{json.dumps(baseline)}\n{rollout}", NONCE, failed=False) == _expected(
        "codex_rollout", 0, 0, None, 0, 0, 0)


def test_adapter_usage_source_handles_python_and_inline_runtime_arguments():
    cases = (
        (["python", "-P", "-m", "hipson.cli", "agent", "exec", "--runtime", "codex"], "codex_rollout"),
        (["python", "-m", "hipson.cli", "agent", "exec", "--runtime=agy"], "agy_json"),
        (["hipson.exe", "agent", "exec", "--runtime=claude"], "claude_stream_json"),
        (["python", "-P", "-m", "other.cli", "agent", "exec", "--runtime=agy"], None),
    )
    for command, source in cases:
        assert task_runner._adapter_source(command) == source, command


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
                      "tokens_by_source": {
                          "agy_json": {"assignments": 1, **_expected("", 8, 2, 12, None, 0, 10)["tokens"]},
                          "codex_rollout": {"assignments": 1, **_expected("", 20, 5, 4, None, 2, 25)["tokens"]},
                      },
                      "missing_reasons": {"not_an_agent_runtime": 8}, "elapsed_seconds": 10.0}
    line = ("Usage: attempts builder=3, explorer=7, reviewer=1 | repair rounds 2 | elapsed 10s | "
            "agy_json 10 total tokens (12 cached, 2 output) | codex_rollout 25 total tokens (4 cached, 5 output) | "
            "without usage 8")
    full_text = _show(db, task, "--detail", "full")
    assert line in full_text.splitlines() and '"attempts"' not in full_text
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
    assert line in text.splitlines() and '"attempts"' not in text


def test_usage_line_names_unknown_values_and_stays_bounded():
    unknown = {"attempts": None, "repair_rounds": None, "elapsed_seconds": None, "tokens_by_source": {},
               "without_usage": 0}
    assert cli._render_task_usage(unknown) == ("Usage: attempts unknown | repair rounds unknown | elapsed unknown | "
                                               "tokens unknown")
    sources = {f"source_{index:03}\u001b[31m": {"total_tokens": None, "cached_tokens": 1, "output_tokens": None}
               for index in range(40)}
    many = {"attempts": {}, "repair_rounds": 0, "elapsed_seconds": 1.5, "tokens_by_source": sources,
            "without_usage": 3}
    rendered = cli._render_task_usage(many)
    assert rendered.startswith("Usage: attempts none | repair rounds 0 | elapsed 1.5s | source_000")
    assert "unknown total tokens (1 cached, unknown output)" in rendered
    assert "\u001b" not in rendered and len(rendered) <= len("Usage: ") + 512


def test_closed_tasks_show_no_stale_check_count():
    def task(task_id, status):
        return {"id": task_id, "status": status, "title": "t",
                "checks": [{"recorded": "ok", "current": "stale"}, {"recorded": "failed", "current": "not_checked"}],
                "open_findings": {"minor": 1}, "stale": None}

    page = {"tasks": [task("T-0001", "done"), task("T-0002", "cancelled"), task("T-0003", "review"),
                      task("T-0004", "in_progress")], "more": None}
    lines = cli._render_compact_tasks(page).splitlines()
    assert lines[:4] == ["T-0001 [done] t | checks recorded OK 1/2 | open findings 1",
                         "T-0002 [cancelled] t | checks recorded OK 1/2 | open findings 1",
                         "T-0003 [review] t | checks recorded OK 1/2, stale 1 | open findings 1",
                         "T-0004 [in_progress] t | checks recorded OK 1/2, stale 1 | open findings 1"]


def test_totals_stay_per_source_and_a_cancelled_reviewer_starts_no_repair_count(tmp_path):
    """S8 review: runtimes count tokens differently, and a reviewer that never reviewed is not a review."""
    db = tmp_path / "ledger.sqlite"
    plan = [("builder", "passed", _expected("codex_rollout", 10, 1, 4, 0, 1, 11)),
            ("reviewer", "cancelled", None),
            ("builder", "passed", _expected("codex_rollout", 20, 2, 8, None, 1, 22)),
            ("reviewer", "passed", _expected("claude_stream_json", 3, 4, 50, 6, 0, 63)),
            ("builder", "passed", None)]
    with closing(ledger.Ledger(db)) as store:
        task = store.open_task(project=tmp_path, title="Mixed", objective="Per source")["id"]
        for i, (role, result, usage) in enumerate(plan):
            with patch.object(ledger, "_timestamp", return_value=f"2026-11-22T00:01:{i*2:02d}Z"):
                assignment = store.assign(task, actor="worker", role=role, mode="ro")["id"]
            with patch.object(ledger, "_timestamp", return_value=f"2026-11-22T00:01:{i*2+1:02d}Z"):
                store.finish(assignment, result=result, summary="Done", usage=usage)
    totals = json.loads(_show(db, task, "--detail", "full", "--json"))["usage"]
    assert totals["tokens_by_source"] == {
        "claude_stream_json": {"assignments": 1, **_expected("", 3, 4, 50, 6, 0, 63)["tokens"]},
        # cache_write_tokens is missing from one Codex record, so its Codex sum is unknown, not partial.
        "codex_rollout": {"assignments": 2, **_expected("", 30, 3, 12, None, 2, 33)["tokens"]},
    }
    # Only the builder after the passed review is a repair round; the cancelled reviewer did not review.
    assert totals["repair_rounds"] == 1
