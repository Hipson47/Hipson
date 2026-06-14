"""Agent bootstrap discovery payloads."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hipson import agent_contract, policy
from hipson.agent_install import AGENT_TARGETS
from hipson.contracts import SCHEMA_VERSION
from hipson.home import detect_codex_home, detect_hipson_home
from hipson.project import resolve_project


def build_bootstrap(*, target: str, project_path: str | Path = ".") -> dict[str, Any]:
    if target not in AGENT_TARGETS:
        raise SystemExit(f"--target must be one of: {', '.join(AGENT_TARGETS)}")
    project = resolve_project(str(project_path))
    warnings: list[str] = []
    contract_available = True
    try:
        contract = agent_contract.build_agent_contract(project)
    except SystemExit as exc:
        contract_available = False
        contract = {}
        warnings.append(str(exc))

    policy_payload = policy.load_policy(project)
    warnings.extend(policy_payload.get("warnings", []))
    surfaces = installed_surfaces()
    return {
        "artifact_kind": "hipson.agent_bootstrap",
        "schema_version": SCHEMA_VERSION,
        "target": target,
        "project": str(project),
        "contract_available": contract_available,
        "contract_artifact_kind": contract.get("artifact_kind", "") if isinstance(contract, dict) else "",
        "recommended_first_command": "hipson contract show --json",
        "recommended_first_tool": "contract.show" if target == "mcp" else "",
        "installed_surfaces": surfaces,
        "policy": {
            "path": policy_payload.get("path", ""),
            "default_workflow": policy_payload.get("policy", {}).get("default_workflow", "autopilot_review")
            if isinstance(policy_payload.get("policy"), dict)
            else "autopilot_review",
            "local_only": policy_payload.get("policy", {}).get("local_only", True)
            if isinstance(policy_payload.get("policy"), dict)
            else True,
        },
        "warnings": warnings,
        "fallback_commands": [
            "hipson work --task \"...\" --write-packet --work-output runs/work.json",
            "hipson packet preflight runs/review-packet.md -o runs/preflight.json --json",
            "hipson verify run --work runs/work.json -o runs/verify.json --json",
            "hipson evidence append --work runs/work.json --verification runs/verify.json --quality-report runs/quality.json",
            "hipson audit show --work runs/work.json --json",
        ],
    }


def agent_surfaces_report(*, project_path: str | Path = ".") -> dict[str, Any]:
    project = resolve_project(str(project_path))
    warnings: list[str] = []
    try:
        agent_contract.build_agent_contract(project)
        contract_available = True
    except SystemExit as exc:
        contract_available = False
        warnings.append(str(exc))
    policy_payload = policy.load_policy(project)
    warnings.extend(policy_payload.get("warnings", []))
    surfaces = installed_surfaces()
    return {
        "artifact_kind": "hipson.agent_surfaces_doctor",
        "schema_version": SCHEMA_VERSION,
        "project": str(project),
        "surfaces": surfaces,
        "policy_valid": bool(policy_payload.get("valid")),
        "policy_issues": policy_payload.get("issues", []),
        "contract_available": contract_available,
        "recommended_next_command": "hipson install agents --all --dry-run"
        if not any(surface.get("installed") for surface in surfaces.values())
        else "hipson agent bootstrap --target codex --json",
        "warnings": warnings,
    }


def installed_surfaces() -> dict[str, dict[str, object]]:
    codex_home, codex_warnings = detect_codex_home()
    hipson_home, hipson_warnings = detect_hipson_home()
    cursor_home = Path.home() / ".cursor"
    claude_home = Path.home() / ".claude"
    return {
        "codex": {
            "home": str(codex_home),
            "installed": (codex_home / "AGENTS.md").exists(),
            "warnings": codex_warnings,
        },
        "cursor": {
            "home": str(cursor_home),
            "installed": (cursor_home / "rules" / "hipson.mdc").exists(),
            "warnings": [],
        },
        "claude": {
            "home": str(claude_home),
            "installed": (claude_home / "CLAUDE.md").exists(),
            "warnings": [],
        },
        "mcp": {
            "home": str(hipson_home / "mcp"),
            "installed": (hipson_home / "mcp" / "hipson-mcp.md").exists(),
            "warnings": hipson_warnings,
        },
    }
