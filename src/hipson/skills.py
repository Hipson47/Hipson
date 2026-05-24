"""Codex skill validation helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from hipson.redaction import redact_text

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass
class SkillValidationResult:
    path: Path
    ok: bool
    errors: list[str]


class SkillLookupError(ValueError):
    """Raised when a requested skill cannot be resolved safely."""


def find_skill_files(root: Path) -> list[Path]:
    ignored = {".git", "build", "dist", "mutants", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
    return sorted(path for path in root.rglob("SKILL.md") if not ignored.intersection(path.parts))


def parse_frontmatter(text: str) -> tuple[dict[str, str], list[str]]:
    if not text.startswith("---\n"):
        return {}, ["missing YAML frontmatter"]

    end = text.find("\n---", 4)
    if end == -1:
        return {}, ["unterminated YAML frontmatter"]

    raw = text[4:end].strip()
    data: dict[str, str] = {}
    errors: list[str] = []
    current_key = ""
    for index, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        if line[:1].isspace() and current_key:
            data[current_key] = " ".join(part for part in (data[current_key], line.strip()) if part).strip()
            continue
        if ":" not in line:
            errors.append(f"invalid frontmatter line {index}: {line}")
            continue
        key, value = line.split(":", 1)
        current_key = key.strip()
        data[current_key] = value.strip().strip('"').strip("'")
    return data, errors


def validate_skill_file(path: Path) -> SkillValidationResult:
    errors: list[str] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    frontmatter, parse_errors = parse_frontmatter(text)
    errors.extend(parse_errors)

    name = frontmatter.get("name", "")
    description = frontmatter.get("description", "")
    if not name:
        errors.append("missing required field: name")
    elif not NAME_RE.match(name):
        errors.append("name must be lowercase kebab-case")

    if not description:
        errors.append("missing required field: description")
    elif len(description.split()) < 6:
        errors.append("description must be actionable and specific")

    return SkillValidationResult(path=path, ok=not errors, errors=errors)


def validate_skills(root: Path) -> list[SkillValidationResult]:
    return [validate_skill_file(path) for path in find_skill_files(root)]


def list_skill_metadata(root: Path, query: str = "") -> list[dict[str, object]]:
    normalized_query = query.strip().lower()
    skills: list[dict[str, object]] = []
    for path in find_skill_files(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, _parse_errors = parse_frontmatter(text)
        name = frontmatter.get("name") or path.parent.name
        description = frontmatter.get("description", "")
        haystack = f"{name} {description} {path}".lower()
        if normalized_query and normalized_query not in haystack:
            continue
        validation = validate_skill_file(path)
        skills.append(
            {
                "name": redact_text(name),
                "description": redact_text(description),
                "path": str(path),
                "ok": validation.ok,
                "errors": [redact_text(error) for error in validation.errors],
            }
        )
    return skills


def view_skill(root: Path, *, name: str | None = None, path: str | None = None, max_chars: int = 4000) -> dict[str, object]:
    skill_path = _resolve_skill_path(root, name=name, path=path)
    text = skill_path.read_text(encoding="utf-8", errors="replace")
    frontmatter, _errors = parse_frontmatter(text)
    skill_name = frontmatter.get("name") or skill_path.parent.name
    description = frontmatter.get("description", "")
    content, truncated = _bounded(redact_text(text), max_chars)
    return {
        "name": redact_text(skill_name),
        "description": redact_text(description),
        "path": str(skill_path),
        "content": _untrusted_skill_block(skill_name, content),
        "truncated": truncated,
    }


def format_validation_results(results: list[SkillValidationResult]) -> str:
    lines: list[str] = []
    for result in results:
        status = "ok" if result.ok else "failed"
        lines.append(f"{status}: {result.path}")
        for error in result.errors:
            lines.append(f"  - {error}")
    if not lines:
        return "No SKILL.md files found."
    return "\n".join(lines)


def _resolve_skill_path(root: Path, *, name: str | None, path: str | None) -> Path:
    resolved_root = root.expanduser().resolve()
    if path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = resolved_root / candidate
        candidate = candidate.resolve()
        if candidate.name != "SKILL.md":
            raise SkillLookupError("Skill path must point to SKILL.md")
        try:
            candidate.relative_to(resolved_root)
        except ValueError:
            raise SkillLookupError("Skill path must stay inside the selected root") from None
        if not candidate.exists():
            raise SkillLookupError(f"Skill path does not exist: {candidate}")
        return candidate
    if not name:
        raise SkillLookupError("Skill name or path is required")
    matches = [skill for skill in list_skill_metadata(resolved_root) if skill["name"] == name]
    if not matches:
        raise SkillLookupError(f"Skill not found: {name}")
    if len(matches) > 1:
        raise SkillLookupError(f"Skill name is ambiguous: {name}")
    return Path(str(matches[0]["path"]))


def _bounded(text: str, max_chars: int) -> tuple[str, bool]:
    if max_chars <= 0:
        return "", bool(text)
    if len(text) <= max_chars:
        return text, False
    marker = f"\n[truncated to {max_chars} chars]"
    return text[: max(0, max_chars - len(marker))].rstrip() + marker, True


def _untrusted_skill_block(name: str, content: str) -> str:
    return f"<untrusted_data name=\"skill:{redact_text(name)}\">\n{content}\n</untrusted_data>"
