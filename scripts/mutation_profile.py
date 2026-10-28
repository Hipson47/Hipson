#!/usr/bin/env python3
"""Point mutmut at a named profile: rewrite [tool.mutmut] from [tool.hipson.mutation-profiles.NAME].

mutmut 3.8 reads only [tool.mutmut], so a second bounded gate swaps that section in a CI checkout, runs, and
restores the file (`git checkout -- pyproject.toml`). Never commit the rewritten file.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def render(profile: dict[str, object]) -> str:
    def array(values: object) -> str:
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values) or not values:
            raise ValueError("profile lists must be nonempty string lists")
        return "[\n" + "".join(f"    {json.dumps(value)},\n" for value in values) + "]"

    return ("[tool.mutmut]\n"
            f"also_copy = {array(profile['also_copy'])}\n"
            f"source_paths = {array(profile['source_paths'])}\n"
            f"pytest_add_cli_args_test_selection = {array(profile['tests'])}\n"
            'pytest_add_cli_args = ["-q"]\n'
            # Mutate only lines the profile's tests execute; other paths of these modules are gated elsewhere.
            "mutate_only_covered_lines = true\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile")
    parser.add_argument("--config", type=Path, default=ROOT / "pyproject.toml")
    args = parser.parse_args(argv)
    text = args.config.read_text(encoding="utf-8")
    try:
        profile = tomllib.loads(text)["tool"]["hipson"]["mutation-profiles"][args.profile]
        start = text.index("[tool.mutmut]")
        end = text.index("\n[", start + 1) + 1
        section = render(profile)
    except (KeyError, ValueError) as exc:
        print(f"FAIL mutation profile {args.profile}: {exc}", file=sys.stderr)
        return 1
    args.config.write_text(text[:start] + section + "\n" + text[end:], encoding="utf-8")
    tomllib.loads(args.config.read_text(encoding="utf-8"))  # The rewritten file must still parse.
    print(f"mutmut profile {args.profile}: {', '.join(profile['source_paths'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
