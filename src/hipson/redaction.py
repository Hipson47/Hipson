"""Redaction and sensitive-file guards used before persistence or provider calls."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

REDACTION = "[REDACTED]"
SKIPPED = "[sensitive file skipped]"

# Whole credential names only, case-insensitive with '_' and '-' interchangeable
# or omitted. Include the assignment vocabulary in SECRET_PATTERNS below.
CREDENTIAL_KEYS = {
    "password", "passwd", "secret", "token", "apikey", "accesskey", "privatekey",
    "authorization", "cookie", "clientsecret", "credential", "credentials",
    "accesstoken", "refreshtoken", "accesskeyid", "secretaccesskey",
    "awssecretaccesskey", "openrouterapikey",
}

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


def redact_value(value: Any) -> Any:
    """Copy JSON-like data, masking credential values of any type and string patterns.

    Match whole credential keys after case folding and removing '_' / '-'; never
    substring-match unrelated fields such as token_count or requires_credentials.
    """
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {
            key: REDACTION
            if isinstance(key, str) and key.casefold().replace("_", "").replace("-", "") in CREDENTIAL_KEYS
            else redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_value(item) for item in value]
    return value


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


def redact_incomplete_private_keys(text: str) -> str:
    """A bounded read can cut off a key's END marker; redact from any surviving BEGIN to the end."""
    return re.sub(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*", REDACTION, text, flags=re.DOTALL)


def drop_sensitive_diff_sections(diff: str) -> str:
    """Omit every per-file section of a unified diff whose old or new path is sensitive.

    Line filtering alone removes headers but keeps hunk bodies, i.e. the secret values themselves.
    """
    sections: list[list[str]] = [[]]
    for line in diff.splitlines():
        if line.startswith(("diff --git ", "diff --cc ", "diff --combined ")):
            sections.append([])
        sections[-1].append(line)
    lines: list[str] = []
    for section in sections:
        if section and section[0].startswith("diff ") and any(is_sensitive_path(path) for path in _diff_paths(section)):
            lines.append(SKIPPED + " (sensitive file diff omitted)")
        else:
            lines.extend(section)
    return "\n".join(lines)


def _diff_paths(section: list[str]) -> list[str]:
    """Complete old/new paths of one diff section; whitespace inside a file name is kept intact."""
    def unquote(path: str) -> str:
        path = path.split("\t", 1)[0]
        return path[1:-1] if len(path) > 1 and path[0] == path[-1] == '"' else path

    paths: list[str] = []
    rest = section[0].split(" ", 2)[2] if section[0].count(" ") >= 2 else ""
    size = (len(rest) - 5) // 2
    if not rest.startswith('"') and size > 0 and rest == f"a/{rest[2:2 + size]} b/{rest[2:2 + size]}":
        paths.append(rest[2:2 + size])
    for line in section[1:]:
        if line.startswith("@@"):
            break  # Hunk lines are content: "--- a/x" there is a removed line, not a header.
        if line.startswith(("--- ", "+++ ")):
            value = unquote(line[4:])
            paths.append(value[2:] if value[:2] in ("a/", "b/") else value)
        elif line.startswith(("rename from ", "rename to ", "copy from ", "copy to ")):
            paths.append(unquote(line.split(" ", 2)[2]))
    if not paths:  # Unparseable header: fall back to conservative token checks.
        paths = [token.strip('"')[2:] if token.strip('"')[:2] in ("a/", "b/") else token.strip('"')
                 for token in rest.split()]
    return [path for path in paths if path and path != "/dev/null"]


def redact_metadata(value: str) -> str:
    return sanitize_path(redact_text(value))
