"""Stdlib-only checks for the platform smoke report and guards."""

import io

from scripts.platform_smoke import Report, boot_identity_skip, failure_detail, mask_private, term_forwarding_skip


def test_mask_private_replaces_home_before_login():
    text = "project /home/tester/src; user=tester; 日本"
    assert mask_private(text, "/home/tester", "tester") == "project ~/src; user=<user>; 日本"
    assert mask_private("a path", "/", "a") == "a path"


def test_mask_private_handles_windows_path_variants_and_case():
    text = r'C:\Users\TESTER\src C:/Users/Tester/src {"home": "C:\\Users\\Tester"} login=TESTER'
    assert mask_private(text, r"C:\Users\Tester", "Tester", windows=True) == (
        r'~\src ~/src {"home": "~"} login=<user>'
    )


def test_failure_detail_keeps_only_the_last_15_lines():
    output = "\n".join(f"line {number}" for number in range(1, 21))
    assert failure_detail(output).splitlines() == [f"      line {number}" for number in range(6, 21)]


def test_report_formats_pass_fail_summary_and_masks_details():
    stream = io.StringIO()
    console = io.StringIO()
    report = Report(stream, console)
    report.home = "/home/tester"
    report.login = "tester"
    with report.checking("first check"):
        pass
    report.failure("second check", "tester wrote /home/tester/log")
    assert report.finish() == 1
    assert stream.getvalue() == (
        "PASS  first check\nFAIL  second check\n      <user> wrote ~/log\n"
        "\nRESULT: 1 passed, 1 failed\nFailed checks:\n  - second check\n"
    )
    assert console.getvalue() == stream.getvalue()


def test_report_returns_zero_without_failures():
    stream = io.StringIO()
    report = Report(stream, io.StringIO())
    report.success("first check")
    assert report.finish() == 0
    assert stream.getvalue() == "PASS  first check\n\nRESULT: 1 passed, 0 failed\n"


def test_platform_guards_provide_skip_reasons():
    for system in ("Linux", "Darwin"):
        assert boot_identity_skip(system) is None
        assert term_forwarding_skip(system) is None
    assert boot_identity_skip("Windows") == "skipped on Windows: boot identity is available only on Linux and macOS"
    assert term_forwarding_skip("Windows") == "skipped on Windows: TERM forwarding requires POSIX signals"
    assert boot_identity_skip("FreeBSD") == "skipped on FreeBSD: boot identity is available only on Linux and macOS"
