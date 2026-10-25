"""Stdlib-only policy shared by the pytest and standalone test runners."""

import os
import sys


def require_no_skips() -> bool:
    return sys.platform == "linux" and bool(os.environ.get("CI"))
