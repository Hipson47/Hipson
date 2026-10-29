"""Fail CI on skips outside the exact platform allowlist in tests.support; keep every test away from the
developer's real ledger and Hipson home."""

import os
import tempfile

from tests.support import skip_failure

# A test that forgets --db, or a mutant under mutation testing that breaks the ledger path, would otherwise
# write to ~/.config/hipson/ledger.sqlite (it did: test tasks and a schema migration reached a real ledger).
# Tests that need other values still set them explicitly.
_ISOLATED = tempfile.mkdtemp(prefix="hipson-tests-")
os.environ["HIPSON_LEDGER"] = os.path.join(_ISOLATED, "ledger.sqlite")
os.environ["HIPSON_HOME"] = os.path.join(_ISOLATED, "home")


class _SkipPolicy:
    def __init__(self):
        self.skipped = []

    def pytest_collectreport(self, report):
        self._record_skip(report)

    def pytest_runtest_logreport(self, report):
        self._record_skip(report)

    def _record_skip(self, report):
        if report.skipped:
            detail = report.longrepr
            reason = detail[2].removeprefix("Skipped: ") if isinstance(detail, tuple) else str(detail)
            failure = skip_failure(reason)
            if failure:
                self.skipped.append(f"{report.nodeid}: {failure}")

    def pytest_sessionfinish(self, session, exitstatus):
        if self.skipped:
            reporter = session.config.pluginmanager.get_plugin("terminalreporter")
            if reporter:
                for failure in self.skipped:
                    reporter.write_line(failure, red=True)
            session.exitstatus = exitstatus or 1


def pytest_configure(config):
    config.pluginmanager.register(_SkipPolicy(), "hipson-skip-policy")
