"""Safe Codex installer helpers."""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from hipson.assets import runtime_asset
from hipson.home import detect_codex_home

START_MARKER = "<!-- hipson:start -->"
END_MARKER = "<!-- hipson:end -->"


@dataclass
class InstallPlan:
    codex_home: Path
    agents_path: Path
    skill_target: Path
    actions: list[str]
    warnings: list[str]


def managed_block(source: Path | None = None) -> str:
    source = source or runtime_asset("codex-workflow-kit/global/AGENTS.md")
    body = source.read_text(encoding="utf-8").strip()
    return f"{START_MARKER}\n{body}\n{END_MARKER}\n"


def merge_managed_block(existing: str, block: str) -> str:
    start_count = existing.count(START_MARKER)
    end_count = existing.count(END_MARKER)
    if start_count != end_count:
        raise ValueError("AGENTS.md contains an incomplete Hipson marker block.")
    if start_count > 1:
        raise ValueError("AGENTS.md contains multiple Hipson marker blocks.")
    if start_count == 0:
        separator = "\n\n" if existing.strip() else ""
        return existing.rstrip() + separator + block

    start = existing.index(START_MARKER)
    end = existing.index(END_MARKER, start) + len(END_MARKER)
    return existing[:start] + block.rstrip() + existing[end:]


def backup_file(path: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.backup-{stamp}")
    shutil.copy2(path, backup)
    return backup


def build_install_plan(codex_home: Path | None = None) -> InstallPlan:
    warnings: list[str] = []
    if codex_home is None:
        codex_home, warnings = detect_codex_home()
    agents_path = codex_home / "AGENTS.md"
    skill_target = codex_home / "skills" / "hipson-workflow"
    skill_sources = _packaged_codex_skills()
    actions = [
        f"ensure directory {codex_home}",
        f"merge Hipson marker block into {agents_path}",
    ]
    actions.extend(f"replace skill directory {codex_home / 'skills' / source.name}" for source in skill_sources)
    return InstallPlan(codex_home, agents_path, skill_target, actions, warnings)


def install_codex(dry_run: bool = True, codex_home: Path | None = None) -> InstallPlan:
    plan = build_install_plan(codex_home)
    if dry_run:
        return plan

    plan.codex_home.mkdir(parents=True, exist_ok=True)
    (plan.codex_home / "skills").mkdir(parents=True, exist_ok=True)

    block = managed_block()
    existing = plan.agents_path.read_text(encoding="utf-8") if plan.agents_path.exists() else ""
    merged = merge_managed_block(existing, block)
    if merged != existing:
        if plan.agents_path.exists():
            backup = backup_file(plan.agents_path)
            plan.actions.append(f"backed up {plan.agents_path} to {backup}")
        plan.agents_path.write_text(merged, encoding="utf-8")

    for source_skill in _packaged_codex_skills():
        skill_target = plan.codex_home / "skills" / source_skill.name
        if skill_target.exists():
            backup = skill_target.with_name(f"{skill_target.name}.backup-{time.strftime('%Y%m%d-%H%M%S')}")
            shutil.move(str(skill_target), str(backup))
            plan.actions.append(f"backed up {skill_target} to {backup}")
        shutil.copytree(source_skill, skill_target)
    return plan


def format_install_plan(plan: InstallPlan, dry_run: bool) -> str:
    lines = [f"Codex home: {plan.codex_home}", f"Mode: {'dry-run' if dry_run else 'apply'}"]
    if plan.warnings:
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in plan.warnings)
    lines.append("Actions:")
    lines.extend(f"- {action}" for action in plan.actions)
    return "\n".join(lines)


def _packaged_codex_skills() -> list[Path]:
    skills_dir = runtime_asset("codex-workflow-kit/skills")
    return sorted(path for path in skills_dir.iterdir() if (path / "SKILL.md").exists())
