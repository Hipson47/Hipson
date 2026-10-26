"""Exercise outcome accounting and the Linux CI no-skips policy."""

import contextlib
import importlib.util
import io
import os
import subprocess
import sys
from pathlib import Path
from unittest import SkipTest
from unittest.mock import patch

from scripts import run_tests
from tests.test_hipson_helpers import REPO_ROOT


def _pass(tmp_path):
    assert tmp_path.is_dir()


def _skip():
    raise SkipTest("unsupported platform")


def _fail():
    raise AssertionError("broken check")


def test_standalone_runner_counts_each_outcome_and_failure():
    output = io.StringIO()
    with patch.dict(os.environ, {"CI": ""}), contextlib.redirect_stdout(output):
        assert run_tests.run_tests([("passes", _pass), ("skips", _skip), ("fails", _fail)]) == 1
    assert output.getvalue().splitlines() == [
        "PASS passes", "SKIP skips: unsupported platform", "FAIL fails: broken check",
        "1 passed, 1 skipped, 1 failed of 3",
    ]


def test_standalone_runner_skip_is_allowed_locally_and_fails_in_linux_ci():
    for ci, expected in (("", 0), ("true", 1)):
        output = io.StringIO()
        with patch.dict(os.environ, {"CI": ci}), patch.object(sys, "platform", "linux"), contextlib.redirect_stdout(output):
            assert run_tests.run_tests([("skips", _skip)]) == expected
        assert "0 passed, 1 skipped, 0 failed of 1" in output.getvalue()
        assert "PASS skips" not in output.getvalue()
        assert ("Linux CI requires zero skipped" in output.getvalue()) == bool(ci)


def test_pytest_rejects_runtime_and_collection_skips_in_linux_ci(tmp_path: Path):
    # Keep the dependency-free runner usable when pytest is not installed.
    # The CI matrix installs pytest, so this control always executes there.
    if importlib.util.find_spec("pytest") is None:
        raise SkipTest("pytest is required for the no-skips policy integration control")
    probe = tmp_path / "test_probe.py"
    passing = tmp_path / "test_pass.py"
    passing.write_text("def test_pass(): pass\n", encoding="utf-8")
    for source in (
        "from unittest import SkipTest\ndef test_skip(): raise SkipTest('probe')\n",
        "import pytest\npytest.skip('probe', allow_module_level=True)\n",
    ):
        probe.write_text(source, encoding="utf-8")
        for ci in ("", "true"):
            env = {**os.environ, "CI": ci, "PYTHONPATH": str(REPO_ROOT), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
            env.pop("PYTEST_ADDOPTS", None)
            # Load the real policy even though the probes live outside tests/.
            # Keep a passing test so a collection skip cannot fail just because
            # pytest collected no tests (exit 5).
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "tests.conftest", str(probe), str(passing)],
                cwd=REPO_ROOT, env=env, text=True, capture_output=True, timeout=30, check=False,
            )
            enforce = bool(ci) and sys.platform == "linux"
            assert result.returncode == (1 if enforce else 0), result.stdout + result.stderr
            assert ("Linux CI requires zero skipped tests" in result.stdout) == enforce, result.stdout + result.stderr
