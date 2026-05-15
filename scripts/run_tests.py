#!/usr/bin/env python3
"""Dependency-free test runner for Hipson helper tests."""

from __future__ import annotations

import importlib
import inspect
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    tests = []
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        module = importlib.import_module(f"tests.{path.stem}")
        tests.extend(
            (f"{path.stem}.{name}", fn)
            for name, fn in inspect.getmembers(module, inspect.isfunction)
            if name.startswith("test_")
        )

    failed = 0
    for name, fn in tests:
        try:
            params = inspect.signature(fn).parameters
            if "tmp_path" in params:
                with tempfile.TemporaryDirectory() as temp_dir:
                    fn(tmp_path=Path(temp_dir))
            else:
                fn()
            print(f"PASS {name}")
        except Exception as exc:
            failed += 1
            print(f"FAIL {name}: {exc}")

    print(f"{len(tests) - failed}/{len(tests)} tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
