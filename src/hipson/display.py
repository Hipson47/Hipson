"""Bounded, redacted display text shared by CLI and worker context views."""

from __future__ import annotations

import json

from hipson.redaction import redact_text


def escape_display_controls(text: str) -> str:
    """Keep readable Unicode while making terminal controls and format characters visible."""
    return "".join(char if char.isprintable() else json.dumps(char)[1:-1] for char in text)


def bounded_text(value: object, *, limit: int = 220, escape_controls: bool = False) -> str:
    text = redact_text(str(value))
    if escape_controls:
        text = escape_display_controls(text)
    if len(text) <= limit:
        return text
    marker = f"... [truncated to {limit} chars]"
    return text[: max(0, limit - len(marker))].rstrip() + marker
