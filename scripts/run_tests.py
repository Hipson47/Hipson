#!/usr/bin/env python3
"""Dependency-free test runner for Hipson helper tests."""

from __future__ import annotations

import importlib
import inspect
import sys
import tempfile
from pathlib import Path
from unittest import SkipTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))


def discover_tests():
    tests = []
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        module = importlib.import_module(f"tests.{path.stem}")
        tests.extend(
            (f"{path.stem}.{name}", fn)
            for name, fn in inspect.getmembers(module, inspect.isfunction)
            if name.startswith("test_")
        )
    return tests


def run_tests(tests) -> int:
    from tests.support import require_no_skips

    passed = skipped = failed = 0
    for name, fn in tests:
        try:
            params = inspect.signature(fn).parameters
            if "tmp_path" in params:
                with tempfile.TemporaryDirectory() as temp_dir:
                    fn(tmp_path=Path(temp_dir))
            else:
                fn()
            passed += 1
            print(f"PASS {name}")
        except SkipTest as exc:
            skipped += 1
            print(f"SKIP {name}: {exc}")
        except Exception as exc:
            failed += 1
            print(f"FAIL {name}: {exc}")

    print(f"{passed} passed, {skipped} skipped, {failed} failed of {len(tests)}")
    if skipped and require_no_skips():
        print("FAIL: Linux CI requires zero skipped tests")
    return 1 if failed or (skipped and require_no_skips()) else 0


def main() -> int:
    return run_tests(discover_tests())


if __name__ == "__main__":
    raise SystemExit(main())
