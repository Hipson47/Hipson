"""Make every unexpected skip a failing Linux CI check."""

from tests.support import require_no_skips


class _NoSkips:
    def __init__(self):
        self.skipped = []

    def pytest_collectreport(self, report):
        if report.skipped:
            self.skipped.append(report.nodeid)

    def pytest_runtest_logreport(self, report):
        if report.skipped:
            self.skipped.append(report.nodeid)

    def pytest_sessionfinish(self, session, exitstatus):
        if self.skipped:
            reporter = session.config.pluginmanager.get_plugin("terminalreporter")
            if reporter:
                reporter.write_line(f"Linux CI requires zero skipped tests: {self.skipped}", red=True)
            session.exitstatus = exitstatus or 1


def pytest_configure(config):
    if require_no_skips():
        config.pluginmanager.register(_NoSkips(), "hipson-no-skips")
