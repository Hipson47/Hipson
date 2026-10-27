"""Credential-carrying HTTP requests that never follow redirects."""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import Any

_installed = False


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Default urllib would resend the Authorization header to any redirect target, including plain HTTP."""

    def redirect_request(self, req: urllib.request.Request, fp: Any, code: int, msg: str, headers: Any,
                         newurl: str) -> None:
        raise urllib.error.HTTPError(req.full_url, code, f"refusing redirect to {newurl}: provider requests carry "
                                     "credentials and are only sent to the configured URL", headers, fp)


def urlopen_no_redirect(request: urllib.request.Request, *, timeout: float) -> Any:
    """urlopen through a process opener without redirect handling (installed once, lazily)."""
    global _installed
    if not _installed:
        urllib.request.install_opener(urllib.request.build_opener(_RefuseRedirects))
        _installed = True
    # Callers validate the URL policy (https, or http only for loopback) before building the request.
    return urllib.request.urlopen(request, timeout=timeout)  # nosec B310
