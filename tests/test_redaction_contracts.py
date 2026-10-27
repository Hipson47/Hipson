"""Exact contracts of diff-path parsing and Git filter disabling (part of the mutation gate selection)."""

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

from hipson import sandbox
from hipson.redaction import SKIPPED, _diff_paths, drop_sensitive_diff_sections, redact_incomplete_private_keys
from hipson.sandbox import git_filter_overrides, prepare_read_only_command

OMITTED = SKIPPED + " (sensitive file diff omitted)"


def _git(root, *args):
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    subprocess.run(["git", "-C", str(root), *args], env=env, check=True, capture_output=True, timeout=10)


def test_diff_paths_reads_whole_names_from_every_header_form():
    assert _diff_paths(["diff --git a/src/app.py b/src/app.py"]) == ["src/app.py"]
    assert _diff_paths(["diff --git a/x y.md b/x y.md"]) == ["x y.md"]
    assert _diff_paths(['diff --git "a/d/\\303\\251 x.md" "b/d/\\303\\251 x.md"']) == ["d/é x.md", "d/é x.md"]
    assert _diff_paths(['diff --git "a/q\\"t.txt" "b/q\\"t.txt"']) == ['q"t.txt', 'q"t.txt']
    renamed = ["diff --git a/old name.txt b/new.env", "similarity index 90%",
               "rename from old name.txt", "rename to new.env"]
    assert _diff_paths(renamed) == ["old name.txt", "new.env"]
    copied = ["diff --git a/a.txt b/c.txt", "copy from a.txt", "copy to c.txt"]
    assert _diff_paths(copied) == ["a.txt", "c.txt"]
    headers = ["diff --git a/f.py b/f.py", "--- a/f.py\t2026-01-01", "+++ b/f.py\t2026-01-01"]
    assert _diff_paths(headers) == ["f.py", "f.py", "f.py"]
    added = ["diff --git a/n.py b/n.py", "new file mode 100644", "--- /dev/null", "+++ b/n.py"]
    assert _diff_paths(added) == ["n.py", "n.py"]
    quoted_headers = ["diff --git x", '--- "a/sp ace.py"', '+++ "b/sp ace.py"']
    assert _diff_paths(quoted_headers) == ["sp ace.py", "sp ace.py"]


def test_diff_paths_stops_at_hunks_and_falls_back_conservatively():
    section = ["diff --git a/app.py b/app.py", "--- a/app.py", "+++ b/app.py", "@@ -1 +1 @@", "--- a/.env", "+++ b/.env"]
    assert _diff_paths(section) == ["app.py", "app.py", "app.py"]
    assert _diff_paths(["diff --cc merged.txt"]) == ["merged.txt"]  # Token fallback for combined diffs.
    assert _diff_paths(["diff --git A/up.txt b/other.txt"]) == ["A/up.txt", "other.txt"]  # Only a/ and b/ strip.
    assert _diff_paths(["diff"]) == []
    assert _diff_paths(["diff --git"]) == []
    assert _diff_paths(["diff --git a/x b/x"]) == ["x"]  # One-character names parse from the header.
    assert _diff_paths(['diff --git x "a/qq"']) == ["x", "qq"]  # Fallback strips quotes, then a/ b/.


def test_diff_paths_unquotes_exactly():
    tabs = ["diff --git q", "--- a/f\tx\ty"]
    assert _diff_paths(tabs) == ["f"]  # The first tab ends the name.
    escaped = ["diff --git q", '--- "a/q\\"t.py"']
    assert _diff_paths(escaped) == ['q"t.py']  # C escapes decode instead of a bare quote strip.
    assert _diff_paths(["diff --git q", '--- "a/unterminated']) == ["unterminated"]
    assert _diff_paths(['diff --git "a/\\377.txt" "b/\\377.txt"']) == ["�.txt", "�.txt"]  # Invalid UTF-8.
    assert _diff_paths(['diff --git "a/€x" "b/€x"']) == ["\\u20acx", "\\u20acx"]  # Never raises.
    assert _diff_paths(["diff --git q", "rename from  spaced.txt"]) == [" spaced.txt"]


def test_drop_sensitive_diff_sections_exact_output():
    diff = "\n".join([
        "preamble line",
        "diff --git a/app.py b/app.py", "@@ -1 +1 @@", "-a", "+b",
        "diff --git a/.env b/.env", "@@ -1 +1 @@", "-K=1", "+K=2",
        "diff --cc config/.env.production", "@@@ -1 +1 @@@", "+key",
        "diff --combined ok.txt", "+fine",
    ])
    assert drop_sensitive_diff_sections(diff) == "\n".join([
        "preamble line",
        "diff --git a/app.py b/app.py", "@@ -1 +1 @@", "-a", "+b",
        OMITTED,
        OMITTED,
        "diff --combined ok.txt", "+fine",
    ])
    assert drop_sensitive_diff_sections("") == ""
    assert drop_sensitive_diff_sections("not a diff\nat all") == "not a diff\nat all"


def test_incomplete_private_key_blocks_are_redacted_to_the_end():
    text = "keep\n-----BEGIN RSA PRIVATE KEY-----\nAAAA\nBBBB"
    assert redact_incomplete_private_keys(text) == "keep\n[REDACTED]"
    assert redact_incomplete_private_keys("no keys here") == "no keys here"


def test_git_filter_overrides_exact_list_from_the_repository(tmp_path):
    _git(tmp_path, "init", "-q")
    for key, value in (("filter.zeta.clean", "z"), ("filter.alpha.smudge", "a"), ("filter.alpha.process", "p"),
                       ("filter.alpha.required", "true"), ("filter.alpha.other", "x"), ("core.notfilter", "1")):
        _git(tmp_path, "config", key, value)
    overrides = git_filter_overrides(tmp_path)
    repo = [item for item in overrides if ".alpha." in item or ".zeta." in item]
    assert repo == ["filter.alpha.process=", "filter.alpha.required=false", "filter.alpha.smudge=",
                    "filter.zeta.clean="]
    assert overrides[0::2] == ["-c"] * (len(overrides) // 2) and len(overrides) % 2 == 0
    assert not any("other" in item or "core." in item for item in overrides)


def test_git_filter_overrides_ignore_inherited_git_variables_and_fail_closed_to_empty(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "filter.mine.clean", "c")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    with patch.dict(os.environ, GIT_DIR=str(elsewhere / ".git"), GIT_CONFIG_PARAMETERS="'filter.injected.clean=x'"):
        overrides = git_filter_overrides(tmp_path)
    assert "filter.mine.clean=" in overrides and not any("injected" in item for item in overrides)
    assert git_filter_overrides(tmp_path / "missing") == []
    with patch.object(sandbox.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, b"filter.x.clean\0", b"")):
        assert git_filter_overrides(tmp_path) == []
    with patch.object(sandbox.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 10)):
        assert git_filter_overrides(tmp_path) == []


def test_git_filter_overrides_parses_null_separated_names_exactly(tmp_path):
    listing = b"user.name\0filter.b.clean\0filter.a.clean\0filter.a.clean\0"
    with patch.object(sandbox.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, listing, b"")) as run:
        assert git_filter_overrides(tmp_path) == ["-c", "filter.a.clean=", "-c", "filter.b.clean="]
    argv = run.call_args.args[0]
    assert argv == ["git", "-c", "core.fsmonitor=false", "config", "--null", "--name-only", "--list"]
    assert run.call_args.kwargs["cwd"] == str(tmp_path) and run.call_args.kwargs["timeout"] == 10
    assert not any(key.upper().startswith("GIT_") for key in run.call_args.kwargs["env"])


def test_prepare_read_only_command_adds_filter_overrides_only_with_cwd(tmp_path):
    with patch.object(sandbox, "git_filter_overrides", return_value=["-c", "filter.f.clean="]) as overrides:
        argv, _ = prepare_read_only_command(["git", "diff", "--stat"], cwd=tmp_path)
        assert overrides.call_args.args == (tmp_path,)
        assert argv == ["git", "--no-pager", "-c", "core.pager=cat", "-c", "core.fsmonitor=false", "-c", "diff.external=",
                        "-c", "filter.f.clean=", "diff", "--no-ext-diff", "--no-textconv", "--stat"]
        overrides.reset_mock()
        argv, _ = prepare_read_only_command(["git", "diff", "--stat"])
        overrides.assert_not_called()
        assert "filter.f.clean=" not in argv
    assert isinstance(Path(tmp_path), Path)


def test_sensitive_path_line_filter_splits_and_strips_exactly():
    from hipson.redaction import redact_sensitive_paths
    assert redact_sensitive_paths("see `(.env)` here") == SKIPPED  # Wrappers are stripped before the check.
    assert redact_sensitive_paths("a\t.env") == SKIPPED  # Any whitespace separates tokens.
    assert redact_sensitive_paths("X.envX") == "X.envX"  # Only the documented wrapper characters strip.
    assert redact_sensitive_paths("ok line\nsecret .env\nok") == "ok line\n" + SKIPPED + "\nok"


def test_diff_header_parse_keeps_git_spacing():
    # A doubled space is not Git's header form: fall back to tokens rather than trimming the name.
    assert _diff_paths(["diff --git  a/x b/x"]) == ["x", "x"]
