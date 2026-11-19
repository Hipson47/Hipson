"""Exercise the live comparison with local fake runtimes and the real Hipson ledger."""

import io
import json
import os
import shlex
import sys
from contextlib import redirect_stdout
from unittest import SkipTest
from unittest.mock import patch

from tests.test_agent_runtimes import _env, _fake

if sys.platform == "linux":
    from scripts import flow_compare as compare

LINUX_ONLY_REASON = "agent e2e harness is Linux/WSL2-only: requires /proc, prctl and Linux process groups"


def _fake_path(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    return bin_dir


def test_dry_run_does_not_spawn_processes(tmp_path):
    if sys.platform != "linux":
        raise SkipTest(LINUX_ONLY_REASON)
    bin_dir = _fake_path(tmp_path)
    for name in compare.RUNTIMES:
        _fake(bin_dir, name, "raise AssertionError('dry-run launched a runtime')")

    for runtime, preferred in (("claude", "codex"), ("codex", "claude"), ("agy", "claude")):
        destination = tmp_path / f"dry-{runtime}.json"
        stdout = io.StringIO()
        with (patch.dict(os.environ, _env(bin_dir)), redirect_stdout(stdout),
              patch.object(compare.subprocess, "Popen", side_effect=AssertionError(f"{runtime} dry-run started a process"))):
            assert compare.main(["--live", runtime, "--model", "sample", "--effort", "high",
                                 "--dry-run", "--output", str(destination)]) == 0, runtime
        plan = json.loads(stdout.getvalue())
        assert plan["mode"] == "live" and plan["dry_run"] and not destination.exists(), runtime
        assert plan["reviewer_runtime"] == preferred, runtime
        assert [(item["case"], item["strategy"]) for item in plan["plans"]] == [
            (case, strategy) for case in ("fix", "hidden-trim") for strategy in ("A", "B", "C")
        ], runtime
        for direct, previous, flow in (plan["plans"][:3], plan["plans"][3:]):
            case = (runtime, direct["case"])
            check = previous["argv"][previous["argv"].index("--check") + 1]
            assert shlex.split(check)[-1] == ("tests/acceptance.py" if direct["case"] == "fix" else "tests/split.py"), case
            if runtime == "claude":
                assert direct["argv"] == ["claude", "--print", "--output-format", "json", "--permission-mode", "acceptEdits",
                                          "--allowedTools", f"Bash({check})", "--model", "sample", "--effort", "high"], case
            elif runtime == "codex":
                assert direct["argv"] == ["codex", "exec", "--json", "--sandbox", "workspace-write", "--cd", direct["cwd"],
                                          "--model", "sample", "--config", 'model_reasoning_effort="high"', "-"], case
            else:
                assert direct["argv"] == ["agy", "--print", "<prompt elided>", "--output-format", "json", "--mode",
                                          "accept-edits", "--model", "sample", "--effort", "high"], case
            assert direct["stdin"] == (None if runtime == "agy" else "<prompt elided>"), case
            assert flow["max_repairs"] == 1 and flow["close_to"] == "done", case
            assert flow["worker_argv"]["reviewer"][4:] == ["agent", "exec", "--runtime", preferred, "--role", "reviewer",
                                                         "--timeout", "180", "--model", "sample", "--effort", "high"], case
            assert previous["argv"][previous["argv"].index("--") + 1:-4] == flow["worker_argv"]["builder"], case


def test_dry_run_same_runtime_fallback():
    if sys.platform != "linux":
        raise SkipTest(LINUX_ONLY_REASON)
    stdout = io.StringIO()
    with (patch.object(compare.shutil, "which", side_effect=lambda name: name if name == "agy" else None),
          redirect_stdout(stdout)):
        assert compare.main(["--live", "agy", "--live-case", "fix", "--dry-run"]) == 0
    plan = json.loads(stdout.getvalue())
    assert plan["runtime"] == plan["reviewer_runtime"] == "agy"
    assert set(plan["plans"][-1]["worker_argv"]) == {"builder", "reviewer"}


RUNTIME_USAGE_CASES = (
    ("claude", [{"type": "result", "result": "Status: completed", "num_turns": 3,
                 "usage": {"input_tokens": 8, "output_tokens": 6, "cache_read_input_tokens": 0,
                           "cache_creation_input_tokens": 21}}], "Status: completed", (8, 6, 0, 21), 3),
    ("codex", [{"type": "thread.started", "thread_id": "thread"},
               {"type": "item.completed", "item": {"type": "agent_message", "text": "Status: completed"}},
               {"type": "turn.completed", "usage": {"input_tokens": 11, "output_tokens": 5, "cached_input_tokens": 4}},
               {"type": "item.completed", "item": {"type": "agent_message", "text": "Status: incomplete"}},
               {"type": "turn.completed", "usage": {"input_tokens": 7, "output_tokens": 0, "cached_input_tokens": 3}}],
     "Status: incomplete", (11, 5, 7, None), 2),  # Codex input includes cached input: 11-4 + 7-3.
    ("agy", [{"status": "SUCCESS", "response": "Status: completed", "usage": {"input_tokens": 9, "output_tokens": 5}}],
     "Status: completed", (9, 5, None, None), None),
    ("agy", [{"status": "ERROR", "result": "Status: completed", "usage": {"input_tokens": 12,
              "output_tokens": 2, "cache_read_tokens": 0}, "num_turns": 1}], None, (12, 2, 0, None), 1),
    ("claude", [{"subtype": "error_during_execution", "result": "Status: completed"}], None, (None,) * 4, None),
)


def test_runtime_json_usage(tmp_path):
    if sys.platform != "linux":
        raise SkipTest(LINUX_ONLY_REASON)
    bin_dir = _fake_path(tmp_path)
    env = {**os.environ, **_env(bin_dir)}
    for runtime, events, answer, tokens, turns in RUNTIME_USAGE_CASES:
        case = (runtime, answer)
        stream = "\n".join(json.dumps(event) for event in events)
        _fake(bin_dir, runtime, f"print({stream!r})")
        code, stdout, launched = compare._live_process([runtime], tmp_path, env, None, "A")
        assert code == 0 and launched, case
        message, evidence = compare._runtime_evidence(runtime, stdout)
        assert message == answer, case
        assert evidence == {"tokens": dict(zip(compare.TOKEN_KEYS, tokens, strict=True)), "turns": turns}, case


FAKE_RUN = r"""
from pathlib import Path

args = sys.argv[1:]
if args == ['--version']:
    print(RUNTIME + ' 9.9.9')
    sys.exit(0)
if args == ['auth', 'status']:
    print('{"loggedIn": true}')
    sys.exit(0)
assert os.environ['HOME'] == os.environ['EXPECTED_HOME']
assert os.environ['CODEX_HOME'] == os.environ['EXPECTED_CODEX_HOME']
assert os.environ['HIPSON_LEDGER'] != os.environ['OUTSIDE_LEDGER']
assert args[args.index('--model') + 1] == 'fixture-model'
assert args[args.index('--effort') + 1] == 'high'
assert not Path.cwd().is_relative_to(Path(os.environ['EXPECTED_HOME']))
source = Path('src/parser/pairs.py')
summary = f"Read {os.environ['HOME']}/fixture token={os.environ['TEST_TOKEN']} " + 'x' * 600
result = {'status': 'completed', 'summary': summary, 'files_changed': [], 'commands': [], 'findings': [], 'risks': []}
if RUNTIME == 'claude':
    prompt = sys.stdin.read()
    assert "split on ':' and trim whitespace" in prompt
    assert args[args.index('--allowedTools') + 1].endswith(' tests/split.py)')
    stage = os.environ.get('HIPSON_FLOW_STAGE')
    pair = "tuple(part.strip() for part in text.split(':'))" if stage == 'repair' else "tuple(text.split(':'))"
    source.write_text(f'def parse_pair(text):\n    return {pair}\n')
    usage = {'input_tokens': 4, 'output_tokens': 3, 'cache_read_input_tokens': 2, 'cache_creation_input_tokens': 1}
    print(json.dumps({'type': 'result', 'subtype': 'success', 'num_turns': 2, 'usage': usage,
                      'result': 'Status: completed\n' + summary, 'structured_output': result}))
else:
    assert os.environ['HIPSON_FLOW_STAGE'] == 'review'
    assert args[args.index('--mode') + 1] == 'plan'
    assert "split on ':' and trim whitespace" in args[args.index('--print') + 1]
    if '.strip()' not in source.read_text():
        result['findings'].append({'severity': 'major', 'title': 'Missing whitespace trimming',
                                   'detail': 'Trim both parts before returning', 'location': 'src/parser/pairs.py:2'})
    print(json.dumps({'status': 'SUCCESS', 'usage': {'input_tokens': 7, 'output_tokens': 2, 'cache_read_tokens': 3},
                      'structured_output': result}))
"""


def test_live_hidden_trim_measures_review_and_redacts_report(tmp_path):
    if sys.platform != "linux":
        raise SkipTest(LINUX_ONLY_REASON)
    bin_dir = _fake_path(tmp_path)
    for name in ("claude", "agy"):
        _fake(bin_dir, name, f"RUNTIME = {name!r}\n{FAKE_RUN}")
    home = tmp_path / "home"
    outside = tmp_path / "outside.sqlite"
    secret = "raw-secret-from-env"
    env = {**_env(bin_dir), "HOME": str(home), "CODEX_HOME": str(home / ".codex"),
           "EXPECTED_HOME": str(home), "EXPECTED_CODEX_HOME": str(home / ".codex"),
           "HIPSON_LEDGER": str(outside), "HIPSON_ROLE": "worker", "OUTSIDE_LEDGER": str(outside),
           "TEST_TOKEN": secret}
    destination = tmp_path / "report.json"
    stdout = io.StringIO()
    with patch.dict(os.environ, env), redirect_stdout(stdout):
        assert compare.main(["--live", "claude", "--reviewer-runtime", "agy", "--live-case", "hidden-trim",
                             "--model", "fixture-model", "--effort", "high", "--output", str(destination)]) == 0
    report = destination.read_text()
    assert report == stdout.getvalue()
    assert str(home) not in report and secret not in report and not outside.exists()
    data = json.loads(report)
    assert data["artifact_kind"] == "hipson.flow_compare" and data["mode"] == "live"
    assert data["runtime_versions"] == {"claude": "claude 9.9.9", "agy": "agy 9.9.9"}
    assert data["model"] == "fixture-model" and data["effort"] == "high"
    direct, previous, flow = data["runs"]
    assert [run["false_completion"] for run in data["runs"]] == [True, True, False]
    assert [(run["accepted"], run["ground_truth_ok"]) for run in data["runs"]] == [(True, False), (True, False), (True, True)]
    assert all(not run["scope_violation"] and not run["tampered_tests"] for run in data["runs"])
    assert [(run["model_sessions"], run["worker_runs"], run["check_runs"]) for run in data["runs"]] == [
        (1, 1, None), (1, 1, 1), (4, 4, 2),
    ]
    assert [session["runtime"] for session in flow["sessions"]] == ["claude", "agy", "claude", "agy"]
    assert [run["turns"] for run in data["runs"]] == [2, 2, None]
    assert direct["tokens"] == previous["tokens"] == dict(zip(compare.TOKEN_KEYS, (4, 3, 2, 1), strict=True))
    assert flow["tokens"] == dict(zip(compare.TOKEN_KEYS, (22, 10, 10, None), strict=True))
    assert flow["brief_bytes"] == sum(session["brief_bytes"] for session in flow["sessions"])
    assert data["summary"]["A"]["sum"]["check_runs"] is None
    assert data["summary"]["C"]["median"]["input_tokens"] == 22
    for run in (direct, flow):
        assert "~/fixture token=[REDACTED]" in run["summary"] and len(run["summary"]) <= 400


def test_live_oracle_reports_removed_acceptance_test(tmp_path):
    if sys.platform != "linux":
        raise SkipTest(LINUX_ONLY_REASON)
    fixture = compare._setup(tmp_path / "repo", live=True)
    (fixture.workdir / "tests/acceptance.py").unlink()
    truth = compare._live_truth(fixture.workdir, fixture.env)
    assert truth == {"ok": False, "tests_intact": False, "out_of_scope": ["tests/acceptance.py"]}


def test_offline_report_contract_is_unchanged(tmp_path):
    if sys.platform != "linux":
        raise SkipTest(LINUX_ONLY_REASON)
    output = tmp_path / "offline.json"
    stdout = io.StringIO()
    with (patch.object(compare, "_live_main", side_effect=AssertionError("offline comparison called the live runner")),
          redirect_stdout(stdout)):
        assert compare.main(["--case", "correct", "--strategy", "C", "--output", str(output)]) == 0
    assert stdout.getvalue().startswith("case     C           \ncorrect  ok-done")
    data = json.loads(output.read_text())
    assert list(data) == ["artifact_kind", "revision", "dirty", "python", "model", "note", "summary", "runs"]
    assert data["artifact_kind"] == "hipson.flow_compare" and data["model"] is None
    assert list(data["summary"]) == ["A1", "A2", "B", "C"]
    run, = data["runs"]
    duration = run.pop("duration_seconds")
    assert duration >= 0
    assert run == {"case": "correct", "probe": "good work is accepted", "strategy": "C", "accepted": True,
                   "ground_truth_ok": True, "false_completion": False, "lost_good_work": False,
                   "accepted_scope_violation": False, "accepted_tampered_tests": False,
                   "accepted_without_independent_review": False, "done_without_review_in_ledger": 0,
                   "lead_commands": 1, "worker_runs": 2, "check_runs": 1, "tokens": None}
