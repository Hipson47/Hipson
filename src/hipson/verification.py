"""Run local verification commands and persist bounded evidence."""

from __future__ import annotations

import json
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

from hipson import output_policy
from hipson.contracts import VerificationResult, sha256_text, timestamp
from hipson.redaction import redact_text

MAX_OUTPUT_CHARS = 12000
DEFAULT_TIMEOUT = 120
DISALLOWED_TOKENS = {";", "&&", "||", "|", ">", ">>", "<", "$(", "`"}


def load_work_plan(path: str | Path) -> dict[str, Any]:
    work_path = Path(path).expanduser().resolve()
    try:
        data = json.loads(work_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"Work plan does not exist: {work_path}") from None
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid work plan JSON: {exc}") from None
    if not isinstance(data, dict):
        raise SystemExit("Work plan JSON must be an object")
    return data


def verification_commands(work_plan: dict[str, Any], limit: int | None = None) -> list[str]:
    commands = work_plan.get("verification", [])
    if not isinstance(commands, list):
        raise SystemExit("Work plan verification field must be a list")
    values = [str(command).strip() for command in commands if str(command).strip()]
    if limit is not None:
        if limit <= 0:
            raise SystemExit("--limit must be greater than zero")
        return values[: max(0, limit)]
    return values


def run_verification(
    *,
    work_plan: dict[str, Any],
    commands: list[str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    project = Path(str(work_plan.get("project", "."))).expanduser().resolve()
    if not project.exists() or not project.is_dir():
        raise SystemExit(f"Work plan project is not a directory: {project}")
    selected_commands = commands if commands is not None else verification_commands(work_plan)
    if not selected_commands:
        raise SystemExit("No verification commands selected")
    results = [_run_one(command=command, cwd=project, timeout=timeout) for command in selected_commands]
    return {
        "artifact_kind": "hipson.verification",
        "schema_version": "1.0",
        "work_id": str(work_plan.get("work_id", "")),
        "task": redact_text(str(work_plan.get("task", ""))),
        "project": str(project),
        "created_at_utc": timestamp(),
        "status": "passed" if all(result["exit_code"] == 0 for result in results) else "failed",
        "results": results,
    }


def write_verification_artifact(
    result: dict[str, Any],
    output: str | Path,
    *,
    cwd: str | Path | None = None,
    allow_unsafe_output: bool = False,
) -> Path:
    path = output_policy.resolve_output_path(
        output,
        cwd=cwd,
        allow_unsafe=allow_unsafe_output,
        description="verification output",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def default_verification_output(work_plan: dict[str, Any]) -> Path:
    work_id = str(work_plan.get("work_id", "work")) or "work"
    project = Path(str(work_plan.get("project", "."))).expanduser().resolve()
    return project / "runs" / f"{work_id}-verification.json"


def parse_command(command: str) -> list[str]:
    if any(token in command for token in DISALLOWED_TOKENS):
        raise SystemExit(f"Refusing verification command with shell control syntax: {command}")
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise SystemExit(f"Invalid verification command: {exc}") from None
    if not argv:
        raise SystemExit("Verification command cannot be empty")
    return argv


def _run_one(*, command: str, cwd: Path, timeout: int) -> VerificationResult:
    argv = parse_command(command)
    started = time.monotonic()
    started_at = timestamp()
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        exit_code = proc.returncode
        stdout = proc.stdout
        stderr = proc.stderr
    except FileNotFoundError as exc:
        exit_code = 127
        stdout = ""
        stderr = str(exc)
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        stderr = stderr or f"Timed out after {timeout}s"
    ended_at = timestamp()
    duration_ms = int((time.monotonic() - started) * 1000)
    bounded_stdout = _bounded(stdout)
    bounded_stderr = _bounded(stderr)
    return {
        "command": redact_text(command),
        "argv": [redact_text(part) for part in argv],
        "cwd": str(cwd),
        "exit_code": exit_code,
        "status": "passed" if exit_code == 0 else "failed",
        "duration_ms": duration_ms,
        "started_at_utc": started_at,
        "ended_at_utc": ended_at,
        "stdout": bounded_stdout,
        "stderr": bounded_stderr,
        "stdout_sha256": sha256_text(bounded_stdout),
        "stderr_sha256": sha256_text(bounded_stderr),
    }


def _bounded(text: str) -> str:
    redacted = redact_text(text)
    if len(redacted) <= MAX_OUTPUT_CHARS:
        return redacted
    marker = f"\n[verification output truncated to {MAX_OUTPUT_CHARS} chars]"
    return redacted[: max(0, MAX_OUTPUT_CHARS - len(marker))].rstrip() + marker
