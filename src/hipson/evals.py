"""Local sidecar report evals for AI-dev quality workflows."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from hipson import quality
from hipson.contracts import timestamp
from hipson.project import discover_commands, resolve_project
from hipson.redaction import redact_text, sanitize_path

MAX_EVAL_TEXT_CHARS = 60_000
FILE_REF_RE = re.compile(r"(?<!://)\b([A-Za-z0-9_./-]+\.(?:py|md|json|toml|ya?ml|ts|tsx|js|jsx|css|html|sh))\b")
COMMAND_REF_RE = re.compile(r"`([^`\n]{2,180})`")


def run_quality_eval(
    *,
    project_path: str | Path = ".",
    packet_path: str | Path | None = None,
    sidecar_path: str | Path,
    verification_path: str | Path | None = None,
) -> dict[str, Any]:
    project = resolve_project(str(project_path))
    sidecar_text = _read_bounded_text(sidecar_path)
    analysis_text = _analysis_text(sidecar_text)
    packet_text = _read_bounded_text(packet_path) if packet_path else ""
    verification = _load_optional_json(verification_path)
    findings = quality.parse_sidecar_findings(analysis_text)
    issues = []
    if not sidecar_text.strip():
        issues.append(_issue("empty_sidecar_output", "error", "Sidecar report is empty."))
    if not findings:
        issues.append(_issue("missing_structured_findings", "warning", "No structured sidecar findings were detected."))
    for file_ref in _hallucinated_files(analysis_text, project):
        issues.append(_issue("hallucinated_file", "error", f"Sidecar references a missing file: {file_ref}"))
    for command in _suspicious_commands(analysis_text, project, discover_commands(project)):
        issues.append(_issue("suspicious_command", "warning", f"Sidecar recommends a command that does not match this repo: {command}"))
    if not _verification_passed(verification):
        issues.append(_issue("missing_verification", "error", "No passed local verification artifact was attached."))
    score = _score(issues)
    return {
        "schema_version": "1.0",
        "created_at_utc": timestamp(),
        "ok": not any(issue["severity"] == "error" for issue in issues),
        "score": score,
        "project": sanitize_path(project),
        "packet": sanitize_path(str(packet_path or "")),
        "sidecar": sanitize_path(str(sidecar_path)),
        "verification_status": str(verification.get("status", "missing")) if verification else "missing",
        "finding_count": len(findings),
        "packet_chars": len(packet_text),
        "sidecar_chars": len(sidecar_text),
        "issues": issues,
        "checks": [
            "empty sidecar output",
            "missing structured findings",
            "hallucinated file references",
            "repo-mismatched command recommendations",
            "missing or failed local verification",
        ],
    }


def render_eval(result: dict[str, Any]) -> str:
    lines = [
        "# Hipson Quality Eval",
        "",
        f"- OK: `{str(result['ok']).lower()}`",
        f"- Score: `{result['score']}`",
        f"- Verification: `{result['verification_status']}`",
        f"- Finding count: `{result['finding_count']}`",
        "",
        "## Issues",
        *_issue_lines(result.get("issues", [])),
        "",
        "## Checks",
        *_bullet_lines(result.get("checks", [])),
    ]
    return "\n".join(lines).rstrip() + "\n"


def write_eval(result: dict[str, Any], output: str | Path) -> Path:
    path = Path(output).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _read_bounded_text(path: str | Path | None) -> str:
    if not path:
        return ""
    artifact = Path(path).expanduser().resolve()
    if not artifact.exists():
        raise SystemExit(f"Eval artifact does not exist: {artifact}")
    if artifact.is_dir():
        raise SystemExit(f"Eval artifact is a directory: {artifact}")
    text = artifact.read_text(encoding="utf-8", errors="replace")
    redacted = redact_text(text)
    if len(redacted) > MAX_EVAL_TEXT_CHARS:
        return redacted[:MAX_EVAL_TEXT_CHARS]
    return redacted


def _load_optional_json(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    artifact = Path(path).expanduser().resolve()
    data = json.loads(artifact.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"Eval JSON artifact must be an object: {artifact}")
    return data


def _analysis_text(text: str) -> str:
    text = re.sub(r"## Metadata\s*```json\s*\{.*?\}\s*```", "", text, flags=re.DOTALL)
    lines = []
    for line in text.splitlines():
        if line.startswith(("- Model:", "- Packet:", "- Created:")):
            continue
        lines.append(line)
    return "\n".join(lines)


def _hallucinated_files(text: str, project: Path) -> list[str]:
    missing = []
    seen: set[str] = set()
    for raw in FILE_REF_RE.findall(text):
        candidate = raw.strip().lstrip("./")
        if not candidate or candidate in seen or candidate.startswith((".venv/", "build/", "dist/")):
            continue
        seen.add(candidate)
        path = Path(candidate)
        if path.is_absolute():
            exists = path.exists()
        else:
            exists = (project / path).exists()
        if not exists:
            missing.append(sanitize_path(candidate))
    return missing


def _suspicious_commands(text: str, project: Path, discovered: list[str]) -> list[str]:
    suspicious = []
    seen: set[str] = set()
    for raw in COMMAND_REF_RE.findall(text):
        command = redact_text(raw.strip())
        if not _looks_like_command(command):
            continue
        if command in seen or _command_is_known(command, project, discovered):
            continue
        seen.add(command)
        suspicious.append(command)
    return suspicious


def _command_is_known(command: str, project: Path, discovered: list[str]) -> bool:
    if command in discovered:
        return True
    if command.startswith(("git ", "hipson ", "uv run ", "python -m ", "python3 -m ")):
        return True
    if command.startswith(("pytest", "ruff ", "mypy ")):
        return True
    if command.startswith(("npm ", "pnpm ", "yarn ")):
        return (project / "package.json").exists()
    if command.startswith("make "):
        return (project / "Makefile").exists() or (project / "makefile").exists()
    if command.startswith("cargo "):
        return (project / "Cargo.toml").exists()
    if command.startswith("go "):
        return (project / "go.mod").exists()
    return False


def _looks_like_command(command: str) -> bool:
    return command.startswith(
        (
            "git ",
            "hipson ",
            "uv ",
            "python ",
            "python3 ",
            "pytest",
            "ruff ",
            "mypy ",
            "npm ",
            "pnpm ",
            "yarn ",
            "make ",
            "cargo ",
            "go ",
        )
    )


def _verification_passed(verification: dict[str, Any]) -> bool:
    return str(verification.get("status", "missing")) == "passed"


def _issue(kind: str, severity: str, message: str) -> dict[str, str]:
    return {"kind": kind, "severity": severity, "message": redact_text(message)}


def _score(issues: list[dict[str, str]]) -> int:
    score = 100
    for issue in issues:
        score -= 30 if issue["severity"] == "error" else 10
    return max(0, score)


def _issue_lines(items: object) -> list[str]:
    values = items if isinstance(items, list) else []
    if not values:
        return ["- none"]
    return [f"- `{item.get('severity')}` {item.get('kind')}: {item.get('message')}" for item in values if isinstance(item, dict)]


def _bullet_lines(items: object) -> list[str]:
    values = items if isinstance(items, list) else []
    if not values:
        return ["- none"]
    return [f"- {item}" for item in values]
