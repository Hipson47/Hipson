"""The native probe needs no real provider for fixture, report and command coverage."""

import io
import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest import SkipTest
from unittest.mock import patch

from scripts import native_probe as probe
from tests.test_agent_runtimes import RESULT, _env, _fake_cli

CLAUDE = """
import re, shlex, subprocess
from pathlib import Path

prompt = sys.stdin.read()
root = Path.cwd()
mode = args[args.index('--permission-mode') + 1]
session_flag = '--resume' if '--resume' in args else '--session-id'
session = args[args.index(session_flag) + 1] if session_flag in args else 'manual-session'
report = {result}

if 'Fix the bug' in prompt:
    source = root / 'src/calc.py'
    assert 'left - right' in source.read_text()
    assert not (root / 'stray.txt').exists()
    assert {brief} in prompt and 'Delegate the bug search to a subagent' in prompt
    source.write_text(source.read_text().replace('left - right', 'left + right'))
    subprocess.run(['git', 'add', 'src/calc.py'], check=True)
    (root / 'stray.txt').touch()
    marker = re.search(r'INSTR-\\w+-OK', (root / 'CLAUDE.md').read_text())[0]
    skill = (root / '.claude/skills/probe-marker/SKILL.md').read_text()
    report.update(summary=marker + ' ' + os.environ['PROBE_FAKE_SECRET'] + ' /home/sample/file ' + 400 * 'z',
                  risks=[re.search(r'SKILL-OK', skill)[0]],
                  commands=[{{'command': 'python3 tests/check.py', 'exit_code': 0}}])
    settings = json.loads((root / '.claude/settings.json').read_text())
    hook = settings['hooks']['PostToolUse'][0]['hooks'][0]['command']
    for tool in ('Agent', 'Edit'):
        subprocess.run(shlex.split(hook), input=json.dumps({{'tool_name': tool}}), text=True, check=True)
elif mode == 'plan':
    assert 'probe-write.txt' in prompt
    report.update(status='blocked', summary='Writing was denied.')
elif 'Remember this code word' in prompt:
    (root.parent / 'fake-session').write_text(re.search(r'PROBE-[a-f0-9]+', prompt)[0])
else:
    assert session_flag == '--resume'
    word = (root.parent / 'fake-session').read_text()
    assert word not in prompt
    assert not list((root.parent / 'session').glob('session-start.brief.md'))
    report['summary'] = word

print(json.dumps({{'subtype': 'success', 'session_id': session, 'structured_output': report,
                  'usage': {{'input_tokens': 7, 'ignored_text': os.environ['PROBE_FAKE_SECRET']}},
                  'modelUsage': {{'fake-claude': {{}}}}, 'num_turns': 2,
                  'permission_denials': [{{'tool_name': 'Bash', 'tool_input': {{'private': 'not in report'}}}}]}}))
"""


def test_dry_run_prints_plan_without_launching():
    stdout = io.StringIO()
    unexpected_launch = AssertionError("Dry run launched a provider runner")
    with (patch.object(probe, "run_contained", side_effect=unexpected_launch),
          patch.object(probe.task_runner, "supervise", side_effect=unexpected_launch), redirect_stdout(stdout)):
        assert probe.main(["--runtime", "claude", "--runtime", "codex", "--runtime", "agy", "--dry-run",
                           "--model", "sample-model", "--effort", "medium"]) == 0
    output = stdout.getvalue()
    plan = json.loads(output)
    assert plan["dry_run"] and len(plan["runs"]) == 15
    assert all(row["exit_code"] is None and row["verdict"] == "not-observed" for row in plan["runs"])
    claude, codex, agy = [row["argv"] for row in plan["runs"] if row["path"] == "manual"]
    assert "--print" in claude and "Bash(python3 tests/check.py)" in claude
    assert codex[1:3] == ["exec", "--cd"] and codex[-1] == "-"
    assert 'model_reasoning_effort=medium' in codex
    assert agy[agy.index("--print") + 1] == "<prompt>" and "600s" in agy
    assert "Fix the bug" not in output and "Remember this code word" not in output
    assert "<scratch>" in output and str(Path.home()) not in output
    for row in plan["runs"]:
        if row["path"] == "hipson":
            assert row["argv"][:5][1:] == ["-m", "hipson.cli", "task", "run"]
            assert "--db" in row["argv"] and "--" in row["argv"]
        if row["probe"].startswith("session-"):
            assert "--continue-session" in row["argv"]


def test_fixture_and_report_with_fake_claude(tmp_path):
    if sys.platform == "win32":
        raise SkipTest("native probe Claude hook requires POSIX directory descriptors and no-follow open flags")
    bin_dir = _fake_cli(tmp_path, "claude", CLAUDE.format(result=repr(RESULT), brief=repr(probe.BUILD)))
    secret = "fake-value-without-a-secret-shaped-prefix"
    env = {**_env(bin_dir), "PROBE_FAKE_SECRET": secret, "HIPSON_HOME": str(tmp_path / "ambient-hipson")}
    outfile = tmp_path / "probe.json"
    stdout = io.StringIO()
    with patch.dict(os.environ, env), redirect_stdout(stdout):
        assert probe.main(["--runtime", "claude", "--out", str(outfile)]) == 0
    assert stdout.getvalue() == ""
    output = outfile.read_text()
    builder, manual, reviewer, first, second = json.loads(output)["runs"]
    assert all(row["verdict"] == "execution-verified" for row in (builder, manual, reviewer, first, second))
    for row in (builder, manual):
        evidence = row["evidence"]
        assert evidence["check_passed"] and evidence["instruction_marker"] and evidence["skill_marker"]
        assert evidence["hook_log_lines"] == 2 and evidence["subagent_tool_observed"]
        assert evidence["models"] == ["fake-claude"] and evidence["usage"] == {"input_tokens": 7}
        assert evidence["num_turns"] == 2 and evidence["permission_denials"] == ["Bash"]
        assert evidence["reported_check_exit_code"] == 0
        assert len(evidence["summary"]) == 300
    assert builder["evidence"]["assignment_status"] == "passed"
    assert "assignment_status" not in manual["evidence"]
    assert reviewer["evidence"]["result_returned"] and not reviewer["evidence"]["write_file_exists"]
    assert first["evidence"]["session_recorded"]
    assert second["evidence"]["resumed"] and second["evidence"]["session_requested_matches"]
    assert second["evidence"]["code_word_present"] and second["evidence"]["summary"] == "<code word>"
    assert "~/file" in output and "/home/sample" not in output
    assert secret not in output and "not in report" not in output
    assert not (tmp_path / "ambient-hipson").exists()


def test_provider_failures_and_reviewer_write_are_recorded(tmp_path):
    if sys.platform == "win32":  # agent exec refuses a batch-script agy (see test_agent_runtimes).
        raise SkipTest("Antigravity takes its prompt as an argument, which a .cmd test shim cannot carry on Windows")
    bin_dir = _fake_cli(tmp_path, "codex", "print('usage limit reached', file=sys.stderr); sys.exit(1)")
    _fake_cli(tmp_path, "agy", """from pathlib import Path
if args[args.index('--mode') + 1] != 'plan':
    print('permission was denied', file=sys.stderr)
    sys.exit(1)
Path('probe-write.txt').touch()
print(json.dumps({'status': 'SUCCESS', 'structured_output': """ + repr(RESULT) + "}))")
    stdout = io.StringIO()
    with patch.dict(os.environ, _env(bin_dir)), redirect_stdout(stdout):
        assert probe.main(["--runtime", "codex", "--runtime", "agy"]) == 0
    rows = json.loads(stdout.getvalue())["runs"]
    codex, agy = rows[:5], rows[5:]
    assert {row["evidence"]["failure_class"] for row in codex} == {"rate_limited"}
    assert all(row["verdict"] == "error" for row in codex)
    reviewer = next(row for row in agy if row["probe"] == "reviewer")
    assert reviewer["evidence"]["result_returned"] and reviewer["evidence"]["write_file_exists"]
    assert reviewer["verdict"] == "not-observed"
    for row in agy:
        if row is not reviewer:
            assert row["verdict"] == "refused" and row["evidence"]["failure_class"] == "permission_denied"
