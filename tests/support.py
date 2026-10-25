"""Stdlib-only policy shared by the pytest and standalone test runners."""

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require_no_skips() -> bool:
    return sys.platform == "linux" and bool(os.environ.get("CI"))


def hipson_cli() -> str | None:
    """The installed CLI: HIPSON_BIN, then PATH, then this checkout's .venv (CI installs without one)."""
    # An explicit override is authoritative; do not hide a broken override with a fallback.
    candidate = os.environ.get("HIPSON_BIN") or shutil.which("hipson") or str(ROOT / ".venv" / "bin" / "hipson")
    return shutil.which(candidate)
