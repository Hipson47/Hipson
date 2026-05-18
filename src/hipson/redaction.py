"""Redaction and sensitive-file guards used before persistence or provider calls."""

from __future__ import annotations

import re
from pathlib import Path

REDACTION = "[REDACTED]"
SKIPPED = "[sensitive file skipped]"

SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[a-z0-9._\-+/=]{12,}"),
    re.compile(r"(?i)(bearer\s+)[a-z0-9._\-+/=]{12,}"),
    re.compile(r"(?i)([?&](?:api[_-]?key|token|secret|password|passwd|access[_-]?token)=)([^&#\s]+)"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-or-v1-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)\b((?:api[_-]?key|token|secret|password|passwd|authorization|access[_-]?key[_-]?id|secret[_-]?access[_-]?key|aws[_-]?secret[_-]?access[_-]?key|openrouter[_-]?api[_-]?key)\s*[:=]\s*[\"'])([^\"'\n]+)([\"'])"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password|passwd|authorization|access[_-]?key[_-]?id|secret[_-]?access[_-]?key)(\s*[:=]\s*)([^\s'\"`,}&]+)"),
    re.compile(r"(?i)([\"'](?:api[_-]?key|token|secret|password|passwd|authorization|access[_-]?key[_-]?id|secret[_-]?access[_-]?key)[\"']\s*:\s*[\"'])([^\"']+)([\"'])"),
]

SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".sqlite", ".db"}
SENSITIVE_NAMES = {".env", ".envrc"}
SENSITIVE_PARTS = {".ssh", ".aws", ".azure", ".config", ".gnupg"}


def redact_text(text: str) -> str:
    redacted = text
    for pattern in SECRET_PATTERNS:
        if pattern.groups == 3:
            redacted = pattern.sub(
                lambda match: (
                    f"{match.group(1)}{match.group(2)}{REDACTION}"
                    if match.group(2).strip().startswith((":", "="))
                    else f"{match.group(1)}{REDACTION}{match.group(3)}"
                ),
                redacted,
            )
        elif pattern.groups == 2:
            redacted = pattern.sub(lambda match: f"{match.group(1)}{REDACTION}", redacted)
        elif pattern.groups == 1:
            redacted = pattern.sub(lambda match: f"{match.group(1)}{REDACTION}", redacted)
        else:
            redacted = pattern.sub(REDACTION, redacted)
    return redacted


def is_sensitive_path(path: str | Path) -> bool:
    p = Path(path)
    parts = {part.lower() for part in p.parts}
    name = p.name.lower()
    if name in SENSITIVE_NAMES or name.startswith(".env."):
        return True
    if p.suffix.lower() in SENSITIVE_SUFFIXES:
        return True
    if parts.intersection(SENSITIVE_PARTS):
        return True
    return False


def summarize_sensitive_path(path: str | Path) -> str:
    return SKIPPED


def sanitize_path(path: str | Path) -> str:
    return summarize_sensitive_path(path) if is_sensitive_path(path) else str(path)


def redact_sensitive_paths(text: str) -> str:
    lines = []
    for line in text.splitlines():
        tokens = [token.strip("'\"`()[]{}<>:;,") for token in re.split(r"\s+", line) if token.strip()]
        if any(is_sensitive_path(token) for token in tokens):
            lines.append(SKIPPED)
        else:
            lines.append(line)
    return "\n".join(lines)


def redact_metadata(value: str) -> str:
    return sanitize_path(redact_text(value))
