"""Positive and negative controls for the actual CI mutation gate."""

import contextlib
import io
import json
from pathlib import Path

from scripts.mutation_gate import check_results, main

BASELINE = {
    "measured_on": "2026-10-25", "min_total": 100, "min_killed_percent": 80,
    "max_survived": 15, "max_no_tests": 5, "max_timeout": 0,
}
STATS = {
    "killed": 80, "survived": 15, "total": 100, "no_tests": 5, "skipped": 0,
    "suspicious": 0, "timeout": 0, "check_was_interrupted_by_user": 0, "segfault": 0,
}


def test_mutation_gate_accepts_baseline_and_improved_results():
    assert check_results(STATS, BASELINE) == []
    assert check_results({**STATS, "killed": 100, "survived": 0, "no_tests": 0}, BASELINE) == []


def test_mutation_gate_rejects_survivors_no_tests_and_score_regressions():
    for changes, expected in (
        ({"killed": 79, "survived": 16}, "survived=16 exceeds baseline"),
        ({"killed": 79, "no_tests": 6}, "no_tests=6 exceeds baseline"),
        ({"total": 101, "timeout": 1}, "Mutation score"),
    ):
        errors = check_results({**STATS, **changes}, BASELINE)
        assert any(expected in error for error in errors), errors


def test_mutation_gate_rejects_empty_incomplete_and_abnormal_runs():
    empty = dict.fromkeys(STATS, 0)
    assert any("population" in error for error in check_results(empty, BASELINE))
    assert any("Incomplete" in error for error in check_results({**STATS, "total": 101}, BASELINE))
    for key in ("skipped", "suspicious", "check_was_interrupted_by_user", "segfault", "timeout"):
        errors = check_results({**STATS, "killed": 79, key: 1}, BASELINE)
        assert any(f"{key}=1" in error for error in errors), errors
    for malformed in ([], {}, {**STATS, "killed": -1}, {**STATS, "total": True}, {**STATS, "extra": 0}):
        try:
            check_results(malformed, BASELINE)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Malformed stats accepted: {malformed}")


def test_mutation_gate_cli_fails_closed_on_bad_or_missing_stats(tmp_path: Path):
    config = tmp_path / "pyproject.toml"
    config.write_text('[tool.hipson.mutation-gate]\n' + "\n".join(
        f"{key} = {json.dumps(value)}" for key, value in BASELINE.items()
    ), encoding="utf-8")
    stats = tmp_path / "stats.json"
    args = ["--config", str(config), "--stats", str(stats)]
    for content, exit_code in ((None, 1), ("not json", 1), ("{}", 1),
                               (json.dumps(STATS), 0), (json.dumps({**STATS, "killed": 79, "survived": 16}), 1)):
        if content is not None:
            stats.write_text(content, encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            assert main(args) == exit_code


def test_mutation_gate_reads_a_named_baseline_section(tmp_path: Path):
    config = tmp_path / "pyproject.toml"
    config.write_text('[tool.hipson.mutation-gate]\nmeasured_on = "a"\nmin_total = 1000\nmin_killed_percent = 99\n'
                      'max_survived = 0\nmax_no_tests = 0\nmax_timeout = 0\n'
                      '[tool.hipson.mutation-gate-other]\nmeasured_on = "b"\nmin_total = 100\nmin_killed_percent = 80\n'
                      'max_survived = 15\nmax_no_tests = 5\nmax_timeout = 0\n', encoding="utf-8")
    stats = tmp_path / "stats.json"
    stats.write_text(json.dumps(STATS), encoding="utf-8")
    with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
        assert main(["--stats", str(stats), "--config", str(config), "--section", "mutation-gate-other"]) == 0
    assert "PASS mutation gate (baseline b)" in out.getvalue()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        assert main(["--stats", str(stats), "--config", str(config)]) == 1
        assert main(["--stats", str(stats), "--config", str(config), "--section", "missing"]) == 1


def test_mutation_profile_swaps_only_the_mutmut_section(tmp_path: Path):
    import tomllib

    from scripts.mutation_profile import main as profile_main
    config = tmp_path / "pyproject.toml"
    config.write_text('[project]\nname = "x"\n\n[tool.mutmut]\nalso_copy = ["a"]\nsource_paths = ["old.py"]\n'
                      'pytest_add_cli_args_test_selection = [\n    "tests/old.py",\n]\nmutate_only_covered_lines = true\n\n'
                      '[tool.hipson.mutation-profiles.p]\nalso_copy = ["src"]\nsource_paths = ["src/new.py"]\n'
                      'tests = ["tests/new.py"]\n', encoding="utf-8")
    with contextlib.redirect_stdout(io.StringIO()):
        assert profile_main(["p", "--config", str(config)]) == 0
    data = tomllib.loads(config.read_text(encoding="utf-8"))
    assert data["tool"]["mutmut"] == {"also_copy": ["src"], "source_paths": ["src/new.py"],
                                      "pytest_add_cli_args_test_selection": ["tests/new.py"],
                                      "pytest_add_cli_args": ["-q"], "mutate_only_covered_lines": True}
    assert data["project"] == {"name": "x"} and data["tool"]["hipson"]["mutation-profiles"]["p"]["tests"] == ["tests/new.py"]
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        assert profile_main(["missing", "--config", str(config)]) == 1
