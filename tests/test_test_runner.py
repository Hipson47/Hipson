"""Exercise outcome accounting and the CI platform skip allowlist."""

import contextlib
import importlib.util
import io
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import SkipTest
from unittest.mock import patch

from scripts import run_tests
from tests import conftest, support
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


def test_both_runners_enforce_exact_platform_skip_reasons():
    with patch.dict(os.environ, {"CI": "true"}):
        for platform in ("linux", "darwin", "win32"):
            allowed = support.PLATFORM_SKIP_REASONS.get(platform, frozenset())
            with patch.object(support, "sys", SimpleNamespace(platform=platform)):
                for reason in (*sorted(allowed), "missing prerequisite", "", "unsupported platform"):
                    def skip(reason=reason):
                        raise SkipTest(reason)

                    with contextlib.redirect_stdout(io.StringIO()):
                        assert run_tests.run_tests([("probe", skip)]) == (0 if reason in allowed else 1)
                    for hook in ("pytest_collectreport", "pytest_runtest_logreport"):
                        policy = conftest._SkipPolicy()
                        getattr(policy, hook)(SimpleNamespace(
                            skipped=True, nodeid="probe", longrepr=("probe.py", 1, f"Skipped: {reason}"),
                        ))
                        session = SimpleNamespace(config=SimpleNamespace(
                            pluginmanager=SimpleNamespace(get_plugin=lambda name: None)
                        ), exitstatus=0)
                        policy.pytest_sessionfinish(session, 0)
                        assert session.exitstatus == (0 if reason in allowed else 1)
                for reason in allowed:
                    # Match the complete reason, never a prefix that could hide a broken prerequisite.
                    assert support.skip_failure(reason + " (missing prerequisite)")


def test_pytest_enforces_runtime_and_collection_skip_policy_in_ci(tmp_path: Path):
    # Keep the dependency-free runner usable when pytest is not installed.
    # The CI matrix installs pytest, so this control always executes there.
    if importlib.util.find_spec("pytest") is None:
        raise SkipTest("pytest is required for the no-skips policy integration control")
    probe = tmp_path / "test_probe.py"
    passing = tmp_path / "test_pass.py"
    bootstrap = tmp_path / "policy_bootstrap.py"
    passing.write_text("def test_pass(): pass\n", encoding="utf-8")
    for platform in ("linux", "darwin", "win32"):
        # Simulate only policy selection, leaving pytest/pathlib on the real host OS.
        bootstrap.write_text(
            "from types import SimpleNamespace\nfrom tests import support\n"
            f"support.sys = SimpleNamespace(platform={platform!r})\n",
            encoding="utf-8",
        )
        allowed_reason = sorted(support.LINUX_ONLY_SKIPS)[0]
        for source in (
            "from unittest import SkipTest\ndef test_skip(): raise SkipTest({reason!r})\n",
            "import pytest\npytest.skip({reason!r}, allow_module_level=True)\n",
        ):
            for ci, reason in (("", "probe"), ("true", "probe"), ("true", allowed_reason)):
                probe.write_text(source.format(reason=reason), encoding="utf-8")
                env = {**os.environ, "CI": ci, "PYTHONPATH": os.pathsep.join((str(tmp_path), str(REPO_ROOT))),
                       "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1"}
                env.pop("PYTEST_ADDOPTS", None)
                # Keep a passing test so a collection skip cannot fail just because
                # pytest collected no tests (exit 5).
                result = subprocess.run(
                    [sys.executable, "-m", "pytest", "-q", "-p", "policy_bootstrap", "-p", "tests.conftest",
                     str(probe), str(passing)],
                    cwd=REPO_ROOT, env=env, text=True, encoding="utf-8", capture_output=True, timeout=30, check=False,
                )
                enforce = bool(ci) and (reason == "probe" or platform == "linux")
                assert result.returncode == (1 if enforce else 0), result.stdout + result.stderr
                message = "Linux CI requires zero skipped tests" if platform == "linux" else f"Unexpected {platform} CI skip"
                assert (message in result.stdout) == enforce, result.stdout + result.stderr
