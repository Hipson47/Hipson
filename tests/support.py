"""Stdlib-only policy shared by the pytest and standalone test runners."""

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Exact reasons only: missing tools, broken imports and other unexpected skips fail CI.
# Linux/WSL2 runs every integration. macOS lacks Linux process inspection, while native
# Windows also lacks POSIX process groups, chmod directory permissions and guaranteed symlinks.
LINUX_ONLY_SKIPS = frozenset({
    "requires Linux prctl subreaper and /proc zombie inspection",
    "requires Linux prctl child subreaping, /proc boot identity and SIGKILL process groups",
    "agent e2e harness is Linux/WSL2-only: requires /proc, prctl and Linux process groups",
})
PLATFORM_SKIP_REASONS = {
    "darwin": LINUX_ONLY_SKIPS,
    "win32": LINUX_ONLY_SKIPS | {
        "agent-run.sh requires Linux/WSL2 or macOS POSIX process groups; native Windows is unsupported",
        "Windows chmod cannot enforce POSIX directory/sidecar permissions; ACL errors are mocked separately",
        "requires POSIX directory mode bits and geteuid; Windows ACL errors are mocked separately",
        "Windows symlinks require Developer Mode or SeCreateSymbolicLinkPrivilege",
        "requires POSIX signals to interrupt hipson task do; Windows shares console Ctrl+C",
        "requires POSIX process inspection for verification descendant cleanup",
    },
}


def skip_failure(reason: str) -> str | None:
    """Explain disallowed CI skips; local runs may skip unavailable prerequisites."""
    if not os.environ.get("CI"):
        return None
    if sys.platform == "linux":
        return f"Linux CI requires zero skipped tests: {reason}"
    if sys.platform in PLATFORM_SKIP_REASONS and reason not in PLATFORM_SKIP_REASONS[sys.platform]:
        return f"Unexpected {sys.platform} CI skip: {reason}"
    return None


def hipson_cli() -> str | None:
    """The installed CLI: HIPSON_BIN, then PATH, then this checkout's .venv (CI installs without one)."""
    # An explicit override is authoritative; do not hide a broken override with a fallback.
    venv_cli = ROOT / ".venv" / ("Scripts/hipson.exe" if sys.platform == "win32" else "bin/hipson")
    candidate = os.environ.get("HIPSON_BIN") or shutil.which("hipson") or str(venv_cli)
    return shutil.which(candidate)
