"""Safe output path policy for generated Hipson artifacts."""

from __future__ import annotations

from pathlib import Path

from hipson.redaction import is_sensitive_path
from hipson.sandbox import ALLOWED_GENERATED_DIRS, check_write_path


def resolve_output_path(
    output: str | Path,
    *,
    cwd: str | Path | None = None,
    allow_unsafe: bool = False,
    description: str = "output",
) -> Path:
    """Resolve a generated artifact output path through Hipson's write policy."""

    base = Path(cwd).expanduser().resolve() if cwd is not None else Path.cwd().resolve()
    raw = Path(output).expanduser()
    if allow_unsafe:
        candidate = raw if raw.is_absolute() else base / raw
        resolved = candidate.resolve()
        if is_sensitive_path(resolved):
            raise SystemExit(f"Refusing sensitive {description} path: {resolved}")
        return resolved

    decision = check_write_path(raw, base)
    if decision.allowed and decision.path is not None:
        return decision.path

    allowed = ", ".join(f"{name}/" for name in sorted(ALLOWED_GENERATED_DIRS))
    reason = decision.reason or "path is outside the allowed generated artifact directories"
    raise SystemExit(
        f"Unsafe {description} path: {reason}. Use {allowed} or pass --allow-unsafe-output for an explicit override."
    )
