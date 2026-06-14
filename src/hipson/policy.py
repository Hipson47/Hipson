"""Project policy file support for Hipson agent autopilot."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hipson.contracts import SCHEMA_VERSION
from hipson.project import changed_files, git_root, resolve_project
from hipson.redaction import sanitize_path

POLICY_JSON = Path(".hipson") / "policy.json"
POLICY_YAML = Path(".hipson") / "policy.yaml"
DEFAULT_POLICY: dict[str, Any] = {
    "default_workflow": "autopilot_review",
    "denied_paths": [],
    "allowed_paths": [],
    "prompt_required_operations": ["provider_call", "destructive_write", "release_claim", "security_decision"],
    "local_only": True,
    "release_gates": ["verification_gate", "human_decision_gate", "release_claim_gate"],
    "agent_integration": {
        "enabled": True,
        "default_target": "codex",
        "autopilot_on_non_trivial_work": True,
    },
}


def load_policy(project_path: str | Path = ".") -> dict[str, Any]:
    project = resolve_project(str(project_path))
    path = _policy_path(project)
    warnings: list[str] = []
    if path is None:
        payload = dict(DEFAULT_POLICY)
        source = "default"
    else:
        payload = _load_policy_file(path)
        source = str(path)
    policy = _merge_defaults(payload)
    issues = validate_policy_payload(policy)
    return {
        "artifact_kind": "hipson.project_policy",
        "schema_version": SCHEMA_VERSION,
        "project": str(project),
        "path": source,
        "policy": policy,
        "valid": not issues,
        "issues": issues,
        "warnings": warnings,
    }


def validate_policy(project_path: str | Path = ".") -> dict[str, Any]:
    return load_policy(project_path)


def enforce_autopilot_policy(
    *,
    project_path: str | Path = ".",
    operation: str,
    run_sidecar: bool = False,
    approved_operations: list[str] | None = None,
) -> dict[str, Any]:
    payload = load_policy(project_path)
    if not payload.get("valid"):
        issues = "; ".join(str(issue) for issue in payload.get("issues", []))
        raise SystemExit(f"Project policy is invalid: {issues}")
    policy = payload["policy"]
    if not isinstance(policy, dict):
        raise SystemExit("Project policy payload must be an object")
    approved = set(approved_operations or [])
    _enforce_denied_paths(project_path, policy)
    if run_sidecar:
        if policy.get("local_only") is True:
            raise SystemExit("Project policy local_only blocks provider_call; set local_only false before running sidecars.")
        if "provider_call" in _string_list(policy.get("prompt_required_operations")) and "provider_call" not in approved:
            raise SystemExit("Project policy requires explicit approval for provider_call.")
    if operation in _string_list(policy.get("prompt_required_operations")) and operation not in approved:
        raise SystemExit(f"Project policy requires explicit approval for {operation}.")
    return payload


def validate_policy_payload(policy: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    denied = _path_set(policy.get("denied_paths"))
    allowed = _path_set(policy.get("allowed_paths"))
    conflicts = sorted(denied & allowed)
    for path in conflicts:
        issues.append(f"Path is both denied and allowed: {path}")
    for path in denied | allowed:
        if path in {"", ".", "/", "~"}:
            issues.append(f"Unsafe broad policy path: {path or '<empty>'}")
        if ".." in Path(path).parts:
            issues.append(f"Path traversal is not allowed in policy paths: {path}")
    prompt_ops = _string_list(policy.get("prompt_required_operations"))
    if "provider_call" not in prompt_ops:
        issues.append("prompt_required_operations must include provider_call")
    gates = _string_list(policy.get("release_gates"))
    for gate in ("verification_gate", "human_decision_gate", "release_claim_gate"):
        if gate not in gates:
            issues.append(f"release_gates must include {gate}")
    if policy.get("local_only") is not True:
        issues.append("local_only must remain true by default")
    return issues


def _enforce_denied_paths(project_path: str | Path, policy: dict[str, Any]) -> None:
    denied = sorted(_path_set(policy.get("denied_paths")))
    if not denied:
        return
    project = resolve_project(str(project_path))
    root = git_root(project)
    changed = changed_files(project, root)
    blocked = [
        path
        for path in changed
        if any(path == denied_path or path.startswith(f"{denied_path.rstrip('/')}/") for denied_path in denied)
    ]
    if blocked:
        raise SystemExit(f"Project policy denied paths changed: {', '.join(blocked)}")


def _policy_path(project: Path) -> Path | None:
    json_path = project / POLICY_JSON
    yaml_path = project / POLICY_YAML
    if json_path.exists():
        return json_path
    if yaml_path.exists():
        return yaml_path
    return None


def _load_policy_file(path: Path) -> dict[str, Any]:
    if path.suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = _parse_simple_yaml(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"Policy file must contain an object: {path}")
    return data


def _merge_defaults(payload: dict[str, Any]) -> dict[str, Any]:
    merged = dict(DEFAULT_POLICY)
    for key, value in payload.items():
        if key == "agent_integration" and isinstance(value, dict):
            base = dict(DEFAULT_POLICY["agent_integration"])
            base.update(value)
            merged[key] = base
        else:
            merged[key] = value
    return merged


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current_key = ""
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.startswith("  - ") and current_key:
            result.setdefault(current_key, []).append(_scalar(line[4:].strip()))
            continue
        if ":" not in line:
            raise SystemExit(f"Unsupported policy YAML line: {raw_line}")
        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        current_key = key
        if not value:
            result[key] = []
        elif value.startswith("[") or value.startswith("{"):
            result[key] = json.loads(value)
        else:
            result[key] = _scalar(value)
    return result


def _scalar(value: str) -> object:
    stripped = value.strip().strip('"').strip("'")
    if stripped.lower() == "true":
        return True
    if stripped.lower() == "false":
        return False
    return stripped


def _path_set(value: object) -> set[str]:
    return {sanitize_path(str(item)).strip() for item in _string_list(value)}


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]
