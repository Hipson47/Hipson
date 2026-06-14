"""Local packet preflight checks before provider-backed sidecar use."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hipson.contracts import sha256_text, timestamp
from hipson.redaction import is_sensitive_path, redact_text, sanitize_path

MAX_PACKET_CHARS = 120_000


def preflight_packet(path: str | Path, *, max_chars: int = MAX_PACKET_CHARS) -> dict[str, Any]:
    packet_path = Path(path).expanduser().resolve()
    errors: list[str] = []
    warnings: list[str] = []
    if not packet_path.exists():
        errors.append("packet does not exist")
        return _payload(packet_path, "", errors, warnings, max_chars=max_chars)
    if packet_path.is_dir():
        errors.append("packet path is a directory")
        return _payload(packet_path, "", errors, warnings, max_chars=max_chars)
    if is_sensitive_path(packet_path):
        errors.append("packet path is sensitive")
    raw = packet_path.read_text(encoding="utf-8", errors="replace")
    redacted = redact_text(raw)
    if len(raw) > max_chars:
        errors.append(f"packet exceeds max_chars={max_chars}")
    if raw != redacted:
        warnings.append("redaction changed packet content")
    if not raw.strip():
        warnings.append("packet is empty")
    return _payload(packet_path, redacted, errors, warnings, max_chars=max_chars)


def write_preflight(payload: dict[str, Any], output: str | Path) -> Path:
    path = Path(output).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _payload(path: Path, redacted: str, errors: list[str], warnings: list[str], *, max_chars: int) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "created_at_utc": timestamp(),
        "ok": not errors,
        "path": sanitize_path(path),
        "size_chars": len(redacted),
        "max_chars": max_chars,
        "sha256": sha256_text(redacted) if redacted else "",
        "redaction_policy": "hipson.redaction.v1",
        "errors": errors,
        "warnings": warnings,
        "cautions": [
            "Preflight is local and does not send packet content to providers.",
            "Sidecar output remains advisory after provider use.",
        ],
    }
