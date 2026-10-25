#!/usr/bin/env python3
"""Enforce the committed baseline against `mutmut export-cicd-stats` (3.8)."""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COUNTS = frozenset({
    "killed", "survived", "total", "no_tests", "skipped", "suspicious", "timeout",
    "check_was_interrupted_by_user", "segfault",
})


def check_results(stats: dict, baseline: dict) -> list[str]:
    """Count only test-killed mutants as kills; fail closed on incomplete results."""
    if not isinstance(stats, dict) or stats.keys() != COUNTS:
        raise ValueError("Expected mutmut 3.8 CI/CD stats with all documented counters")
    if any(type(value) is not int or value < 0 for value in stats.values()):
        raise ValueError("Mutation counters must be non-negative integers")
    for key in ("min_total", "max_survived", "max_no_tests", "max_timeout"):
        if type(baseline.get(key)) is not int or baseline[key] < 0:
            raise ValueError(f"Invalid mutation baseline: {key}")
    minimum_score = baseline.get("min_killed_percent")
    if type(minimum_score) not in (int, float) or not 0 < minimum_score <= 100:
        raise ValueError("Invalid mutation baseline: min_killed_percent")
    total = stats["total"]
    errors = []
    if total == 0 or total < baseline["min_total"]:
        errors.append(f"Mutation population {total} is below baseline {baseline['min_total']}")
    # 3.8 omits `not_checked` from this export. A shortfall in the counters means
    # unfinished (or unsupported) outcomes and must never become a green gate.
    if sum(value for key, value in stats.items() if key != "total") != total:
        errors.append("Incomplete mutation results: counters do not sum to total")
    for key in ("skipped", "suspicious", "check_was_interrupted_by_user", "segfault"):
        if stats[key]:
            errors.append(f"Unacceptable mutation outcome: {key}={stats[key]}")
    for key in ("survived", "no_tests", "timeout"):
        if stats[key] > baseline[f"max_{key}"]:
            errors.append(f"{key}={stats[key]} exceeds baseline {baseline[f'max_{key}']}")
    score = 100 * stats["killed"] / total if total else 0
    if score < minimum_score:
        errors.append(f"Mutation score {score:.2f}% is below baseline {minimum_score:.2f}%")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stats", type=Path, default=ROOT / "mutants" / "mutmut-cicd-stats.json")
    parser.add_argument("--config", type=Path, default=ROOT / "pyproject.toml")
    args = parser.parse_args(argv)
    try:
        baseline = tomllib.loads(args.config.read_text(encoding="utf-8"))["tool"]["hipson"]["mutation-gate"]
        stats = json.loads(args.stats.read_text(encoding="utf-8"))
        errors = check_results(stats, baseline)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"FAIL mutation gate: {exc}", file=sys.stderr)
        return 1
    total = stats["total"]
    score = 100 * stats["killed"] / total if total else 0
    print(f"Mutation score: {stats['killed']}/{total} killed ({score:.2f}%); "
          f"{stats['survived']} survived, {stats['no_tests']} no tests, {stats['timeout']} timeout")
    for error in errors:
        print(f"FAIL mutation gate: {error}", file=sys.stderr)
    if not errors:
        print(f"PASS mutation gate (baseline {baseline['measured_on']})")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
