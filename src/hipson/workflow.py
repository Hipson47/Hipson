"""Local work-session planner for Codex-first Hipson workflows."""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import TypedDict, cast

from hipson import contracts, model_profiles, output_policy
from hipson.packets import compile_executor_packet, compile_review_packet, csv_items
from hipson.project import build_scan, changed_files, discover_commands, git_root, resolve_project
from hipson.redaction import redact_text
from hipson.router import RouteResult, route_task


class PacketPlan(TypedDict):
    mode: str
    path: str
    command: str
    written: bool
    reason: str


class PacketPreflightPlan(TypedDict):
    command: str
    output: str
    required_before_sidecar: bool
    reason: str


class AIQualityPlan(TypedDict):
    enabled: bool
    mode: str
    profile: str
    agent: str
    model: str
    packet_path: str
    dry_run_command: str
    run_command: str
    reason: str
    cautions: list[str]


class WorkPlan(TypedDict):
    artifact_kind: str
    schema_version: str
    work_id: str
    created_at_utc: str
    task: str
    project: str
    repo_state: contracts.RepoState
    route: RouteResult
    scan: str
    changed_files: list[str]
    discovered_commands: list[str]
    selected_skills: list[str]
    packet: PacketPlan
    packet_preflight: PacketPreflightPlan
    ai_quality: AIQualityPlan
    verification: list[str]
    memory: list[str]
    audit: list[str]
    next_actions: list[str]


def build_work_plan(
    *,
    task: str,
    project_path: str = ".",
    include_diff: bool = True,
    diff_lines: int = 120,
    write_packet: bool = False,
    packet_output: str | None = None,
    inspect: str | None = None,
    allowed_edit: str | None = None,
    acceptance: str | None = None,
    verification: str | None = None,
    skills: str | None = None,
    ai_quality: bool = False,
    ai_free: bool = False,
    ai_agent: str | None = None,
    ai_model: str | None = None,
    ai_profile: str | None = None,
    allow_unsafe_output: bool = False,
) -> WorkPlan:
    """Build a provider-free, auditable Codex work plan for one task."""

    project = resolve_project(project_path)
    route = route_task(task)
    scan = build_scan(project, include_diff=include_diff, diff_lines=diff_lines)
    root = git_root(project)
    changed = changed_files(project, root)
    commands = discover_commands(project)
    selected_skills = recommend_skills(task=task, route=route, explicit=csv_items(skills))
    verification_commands = _verification_commands(commands, verification)
    packet_plan = _packet_plan(
        task=task,
        project=project,
        route=route,
        scan=scan,
        changed=changed,
        commands=commands,
        selected_skills=selected_skills,
        write_packet=write_packet,
        packet_output=packet_output,
        inspect=inspect,
        allowed_edit=allowed_edit,
        acceptance=acceptance,
        verification=verification_commands[0],
        allow_unsafe_output=allow_unsafe_output,
    )
    preflight_plan = _packet_preflight_plan(packet_plan["path"])
    quality_plan = _ai_quality_plan(
        enabled=ai_quality or ai_free or bool(ai_agent) or bool(ai_model) or bool(ai_profile),
        free=ai_free,
        task=task,
        route=route,
        packet_path=packet_plan["path"],
        agent=ai_agent,
        model=ai_model,
        profile=ai_profile,
    )
    memory_commands = [
        _command(
            "hipson",
            "memory",
            "add",
            "--scope",
            "repo",
            "--repo",
            str(project),
            "--kind",
            "handoff",
            "--summary",
            "[compact outcome, files touched, verification, risks, next step]",
        )
    ]
    audit_contract = [
        "Source of truth is the git diff plus command output, not model confidence.",
        "This command is provider-free and does not call sidecars or Hermes.",
        "Packets and scans are redacted before persistence by the existing packet/scan paths.",
        "Packet preflight is local and must pass before provider-backed sidecar use.",
        "Verification is not claimed until the listed commands are run.",
        "Hermes is optional status/intake infrastructure; Codex remains the coding control surface.",
    ]
    if quality_plan["enabled"]:
        audit_contract.extend(
            [
                "AI quality passes are explicit opt-in sidecar calls on bounded packets.",
                "Free or model-selected sidecars are advisory only and cannot approve work.",
            ]
        )
    next_actions = [
        _command("hipson", "route", "--task", task),
        _command("hipson", "scan", str(project), "--include-diff"),
        packet_plan["command"],
        preflight_plan["command"],
    ]
    if quality_plan["enabled"]:
        next_actions.extend([quality_plan["dry_run_command"], quality_plan["run_command"]])
    next_actions.extend([verification_commands[0], memory_commands[0]])
    return {
        "artifact_kind": "hipson.work_plan",
        "schema_version": contracts.SCHEMA_VERSION,
        "work_id": contracts.new_id("work"),
        "created_at_utc": contracts.timestamp(),
        "task": task,
        "project": str(project),
        "repo_state": contracts.repo_state(project),
        "route": route,
        "scan": scan,
        "changed_files": changed,
        "discovered_commands": commands,
        "selected_skills": selected_skills,
        "packet": packet_plan,
        "packet_preflight": preflight_plan,
        "ai_quality": quality_plan,
        "verification": verification_commands,
        "memory": memory_commands,
        "audit": audit_contract,
        "next_actions": next_actions,
    }


def recommend_skills(*, task: str, route: RouteResult, explicit: list[str] | None = None) -> list[str]:
    """Return a small curated skill/sidecar set for the task."""

    normalized = task.lower()
    recommendations: list[str] = []
    _append_unique(recommendations, route["recommended_skill"])

    mode = route["mode"]
    risk = route["risk"]
    if mode in {"review", "sidecar-review"}:
        _append_unique(recommendations, "review-packet")
        _append_unique(recommendations, "reviewer_cheap")
    if mode == "exec":
        _append_unique(recommendations, "executor-packet")
        _append_unique(recommendations, "skills/hipson-gpt/skill_ai-coding-workflows.md")
    if mode == "verify":
        _append_unique(recommendations, "verify")
    if mode == "handoff":
        _append_unique(recommendations, "handoff")
        _append_unique(recommendations, "memory_summarizer_cheap")

    if risk == "security" or any(token in normalized for token in ["security", "auth", "token", "secret", "credential"]):
        _append_unique(recommendations, "skills/external/openai-curated/security-threat-model")
        _append_unique(recommendations, "reviewer_cheap")
    if any(token in normalized for token in ["ui", "ux", "frontend", "motion", "visual", "figma", "landing"]):
        _append_unique(recommendations, "skills/hipson-premium-ui-ux")
        _append_unique(recommendations, "skills/hipson-visual-experience-director")
        _append_unique(recommendations, "skills/hipson-creative-frontend-motion-architect")
        _append_unique(recommendations, "premium_ui_ux")
    if any(token in normalized for token in ["hermes", "telegram", "scheduler", "status", "intake"]):
        _append_unique(recommendations, "docs/hermes-integration.md")
    if any(token in normalized for token in ["readme", "docs", "documentation", "release claim"]):
        _append_unique(recommendations, "skills/hipson-readme-craft")

    for skill in explicit or []:
        _append_unique(recommendations, skill)
    return recommendations


def render_work_plan(plan: WorkPlan) -> str:
    route = plan["route"]
    packet = plan["packet"]
    preflight = plan["packet_preflight"]
    ai_quality = plan["ai_quality"]
    daily = [
        f"1. Route: `{_command('hipson', 'route', '--task', plan['task'])}`",
        f"2. Scan: `{_command('hipson', 'scan', plan['project'], '--include-diff')}`",
        f"3. Packet/execute: `{packet['command']}`",
        f"4. Packet preflight: `{preflight['command']}`",
    ]
    if ai_quality["enabled"]:
        daily.extend(
            [
                f"5. AI preview: `{ai_quality['dry_run_command']}`",
                f"6. AI quality pass: `{ai_quality['run_command']}`",
                f"7. Verify: `{plan['verification'][0]}`",
                f"8. Memory/handoff: `{plan['memory'][0]}`",
            ]
        )
    else:
        daily.extend(
            [
                f"5. Verify: `{plan['verification'][0]}`",
                f"6. Memory/handoff: `{plan['memory'][0]}`",
            ]
        )
    lines = [
        "# Hipson Work Brief",
        "",
        f"- Work ID: `{plan['work_id']}`",
        f"- Created: `{plan['created_at_utc']}`",
        f"- Task: {plan['task']}",
        f"- Project: `{plan['project']}`",
        f"- Mode: `{route['mode']}`",
        f"- Risk: `{route['risk']}`",
        f"- Recommended skill: `{route['recommended_skill']}`",
        f"- Human review required: `{str(route['requires_human_review']).lower()}`",
        "",
        "## Daily Workflow",
        *daily,
        "",
        "## Packet",
        f"- Mode: `{packet['mode']}`",
        f"- Path: `{packet['path']}`",
        f"- Written: `{str(packet['written']).lower()}`",
        f"- Reason: {packet['reason']}",
        "",
        "## Packet Preflight",
        f"- Command: `{preflight['command']}`",
        f"- Output: `{preflight['output']}`",
        f"- Required before sidecar: `{str(preflight['required_before_sidecar']).lower()}`",
        f"- Reason: {preflight['reason']}",
        "",
        "## AI Quality Layer",
        *_ai_quality_lines(plan["ai_quality"]),
        "",
        "## Selected Skills And Sidecars",
        *_bullet_code(plan["selected_skills"], "none"),
        "",
        "## Changed Files",
        *_bullet_code(plan["changed_files"], "none"),
        "",
        "## Verification Commands",
        *_bullet_code(plan["verification"], "none discovered"),
        "",
        "## Audit Contract",
        *_bullet_text(plan["audit"], "none"),
        "",
        "## Known Unknowns",
        "- Verification commands have not been run by `hipson work`.",
        "- Sidecar output, if requested later, remains advisory and must be checked against local files.",
        "- Hermes is not involved unless a later `hipson hermes intake` command is run explicitly.",
        "",
        "## Scan",
        "",
        plan["scan"].rstrip(),
    ]
    return "\n".join(lines).rstrip() + "\n"


def print_work_plan(plan: WorkPlan, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(_json_safe_plan(plan), indent=2, ensure_ascii=False))
    else:
        print(render_work_plan(plan))


def write_work_plan(
    plan: WorkPlan,
    output: str,
    *,
    cwd: str | Path | None = None,
    allow_unsafe_output: bool = False,
) -> Path:
    path = output_policy.resolve_output_path(
        output,
        cwd=cwd,
        allow_unsafe=allow_unsafe_output,
        description="work output",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_safe_plan(plan), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _packet_plan(
    *,
    task: str,
    project: Path,
    route: RouteResult,
    scan: str,
    changed: list[str],
    commands: list[str],
    selected_skills: list[str],
    write_packet: bool,
    packet_output: str | None,
    inspect: str | None,
    allowed_edit: str | None,
    acceptance: str | None,
    verification: str,
    allow_unsafe_output: bool,
) -> PacketPlan:
    mode = route["mode"]
    packet_mode = _packet_mode(mode)
    default_output = "runs/review-packet.md" if packet_mode == "review" else "runs/executor-packet.md"
    output = packet_output or default_output
    command = _packet_command(
        packet_mode=packet_mode,
        project=project,
        task=task,
        output=output,
        allowed_edit=allowed_edit,
        acceptance=acceptance,
        verification=verification,
        selected_skills=selected_skills,
    )
    reason = "packet command prepared"
    written = False
    if write_packet:
        if packet_mode == "exec" and not csv_items(allowed_edit):
            raise ValueError("--write-packet for implementation work requires --allowed-edit")
        packet_text = _compile_packet(
            packet_mode=packet_mode,
            task=task,
            project=project,
            scan=scan,
            changed=changed,
            commands=commands,
            selected_skills=selected_skills,
            inspect=inspect,
            allowed_edit=allowed_edit,
            acceptance=acceptance,
            verification=verification,
        )
        _write_packet(output, packet_text, cwd=project, allow_unsafe_output=allow_unsafe_output)
        written = True
        reason = "packet written locally"
    return {
        "mode": packet_mode,
        "path": output,
        "command": command,
        "written": written,
        "reason": reason,
    }


def _packet_preflight_plan(packet_path: str) -> PacketPreflightPlan:
    path = Path(packet_path)
    output = str(path.with_suffix(".preflight.json")) if path.suffix else f"{packet_path}.preflight.json"
    return {
        "command": _command("hipson", "packet", "preflight", packet_path, "-o", output, "--json"),
        "output": output,
        "required_before_sidecar": True,
        "reason": "Local packet safety gate before any provider-backed sidecar call.",
    }


def _ai_quality_plan(
    *,
    enabled: bool,
    free: bool,
    task: str,
    route: RouteResult,
    packet_path: str,
    agent: str | None,
    model: str | None,
    profile: str | None,
) -> AIQualityPlan:
    profile_data: dict[str, object] = {}
    if profile:
        profile_data = model_profiles.get_profile(profile)
        model_profiles.validate_profile_for_task(profile, profile_data, task=task, risk=route["risk"])
        enabled = True
    if not enabled:
        return {
            "enabled": False,
            "mode": "off",
            "profile": "",
            "agent": "",
            "model": "",
            "packet_path": packet_path,
            "dry_run_command": "",
            "run_command": "",
            "reason": "AI quality pass not requested; core work brief remains provider-free.",
            "cautions": [],
        }

    selected_agent = agent or str(profile_data.get("agent", "")) or _default_quality_agent(route, free=free)
    selected_model = model or str(profile_data.get("model", "")) or ("openrouter/free" if free else "")
    mode = "profile" if profile else "free" if selected_model == "openrouter/free" else "model" if selected_model else "agent"
    command_parts = ["hipson", "sidecar", "run", "--agent", selected_agent, "--packet", packet_path]
    if selected_model:
        command_parts.extend(["--model", selected_model])
    run_command = _command(*command_parts)
    dry_run_command = _command(*command_parts, "--dry-run")
    return {
        "enabled": True,
        "mode": mode,
        "profile": profile or "",
        "agent": selected_agent,
        "model": selected_model,
        "packet_path": packet_path,
        "dry_run_command": dry_run_command,
        "run_command": run_command,
        "reason": "Optional AI quality pass prepared for a bounded packet.",
        "cautions": [
            "Run the dry-run preview before sending any packet to a provider.",
            "Do not send secrets, broad logs, or sensitive customer context to free or unknown models.",
            "Treat AI quality output as advisory; local diff, tests, and human review remain authoritative.",
        ],
    }


def _default_quality_agent(route: RouteResult, *, free: bool) -> str:
    if free:
        return "coder_review_free" if route["mode"] == "exec" else "reviewer_free"
    if route["mode"] == "exec":
        return "coder_review_cheap"
    return "reviewer_cheap"


def _compile_packet(
    *,
    packet_mode: str,
    task: str,
    project: Path,
    scan: str,
    changed: list[str],
    commands: list[str],
    selected_skills: list[str],
    inspect: str | None,
    allowed_edit: str | None,
    acceptance: str | None,
    verification: str,
) -> str:
    if packet_mode == "review":
        return compile_review_packet(
            title=task,
            project=str(project),
            scope="current git delta",
            scan=scan,
            changed_files=changed,
            commands=commands,
            selected_skills=selected_skills,
        )
    return compile_executor_packet(
        title=task,
        goal=task,
        project=str(project),
        scope="next bounded task",
        scan=scan,
        changed_files=changed,
        commands=commands,
        files_to_inspect=csv_items(inspect) or changed,
        allowed_edit=csv_items(allowed_edit),
        acceptance=acceptance or "Observable task outcome is implemented without broadening scope.",
        verification=verification,
        selected_skills=selected_skills,
    )


def _packet_mode(route_mode: str) -> str:
    if route_mode == "exec":
        return "exec"
    return "review"


def _packet_command(
    *,
    packet_mode: str,
    project: Path,
    task: str,
    output: str,
    allowed_edit: str | None,
    acceptance: str | None,
    verification: str,
    selected_skills: list[str],
) -> str:
    if packet_mode == "review":
        return _command(
            "hipson",
            "packet",
            "review",
            str(project),
            "--title",
            task,
            "--include-diff",
            "--skills",
            ",".join(selected_skills),
            "-o",
            output,
        )
    return _command(
        "hipson",
        "packet",
        "exec",
        str(project),
        "--title",
        task,
        "--goal",
        task,
        "--allowed-edit",
        allowed_edit or "[required before executor packet]",
        "--acceptance",
        acceptance or "Observable task outcome is implemented without broadening scope.",
        "--verification",
        verification,
        "--skills",
        ",".join(selected_skills),
        "-o",
        output,
    )


def _verification_commands(commands: list[str], explicit: str | None) -> list[str]:
    if explicit:
        return [explicit]
    preferred = ["git diff --check"]
    discovered = [command for command in commands if command not in preferred]
    return preferred + discovered


def _write_packet(
    output: str,
    packet_text: str,
    *,
    cwd: str | Path | None,
    allow_unsafe_output: bool,
) -> None:
    path = output_policy.resolve_output_path(
        output,
        cwd=cwd,
        allow_unsafe=allow_unsafe_output,
        description="packet output",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(packet_text, encoding="utf-8")


def _command(*parts: str) -> str:
    return shlex.join(list(parts))


def _append_unique(values: list[str], item: object) -> None:
    text = str(item).strip()
    if text and text not in values:
        values.append(text)


def _bullet_code(items: list[str], empty: str) -> list[str]:
    if not items:
        return [f"- {empty}"]
    return [f"- `{item}`" for item in items]


def _bullet_text(items: list[str], empty: str) -> list[str]:
    if not items:
        return [f"- {empty}"]
    return [f"- {item}" for item in items]


def _ai_quality_lines(plan: AIQualityPlan) -> list[str]:
    if not plan["enabled"]:
        return ["- Enabled: `false`", f"- Reason: {plan['reason']}"]
    lines = [
        "- Enabled: `true`",
        f"- Mode: `{plan['mode']}`",
        f"- Profile: `{plan['profile'] or 'none'}`",
        f"- Agent: `{plan['agent']}`",
        f"- Model: `{plan['model'] or 'agent default'}`",
        f"- Packet: `{plan['packet_path']}`",
        f"- Preview: `{plan['dry_run_command']}`",
        f"- Run: `{plan['run_command']}`",
        f"- Reason: {plan['reason']}",
        "- Cautions:",
    ]
    lines.extend(f"  - {item}" for item in plan["cautions"])
    return lines


def _json_safe_plan(plan: WorkPlan) -> dict[str, object]:
    payload = cast(dict[str, object], dict(plan))
    payload["scan"] = redact_text(cast(str, payload["scan"]))
    return payload
