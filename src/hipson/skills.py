"""Codex skill validation helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass
class SkillValidationResult:
    path: Path
    ok: bool
    errors: list[str]


def find_skill_files(root: Path) -> list[Path]:
    ignored = {".git", "build", "dist", "__pycache__", ".pytest_cache"}
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
