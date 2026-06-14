"""Curated model profile registry for AI-dev quality passes."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from hipson.assets import runtime_asset
from hipson.redaction import redact_text

DEFAULT_CONFIG = runtime_asset("config/model_profiles.json")
SENSITIVE_CONTEXT_MARKERS = (
    "sensitive context",
    "raw secret",
    "raw secrets",
    "api key",
    "api_key",
    ".env",
    "private key",
    "credential value",
    "token value",
    "customer data",
    "production data",
    "prod data",
)


def load_profiles(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path).expanduser().resolve() if path else DEFAULT_CONFIG
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"Missing model profile config: {config_path}") from None
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid model profile JSON: {exc}") from None
    if not isinstance(data, dict):
        raise SystemExit("Model profile config must be a JSON object")
    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        raise SystemExit("Model profile config must contain a profiles object")
    return data


def profiles(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    values = data.get("profiles", {})
    return {str(name): dict(profile) for name, profile in values.items() if isinstance(profile, dict)}


def get_profile(name: str, path: str | Path | None = None) -> dict[str, Any]:
    all_profiles = profiles(load_profiles(path))
    if name not in all_profiles:
        available = ", ".join(sorted(all_profiles)) or "none"
        raise SystemExit(f"Unknown model profile '{name}'. Available: {available}")
    profile = dict(all_profiles[name])
    profile["name"] = name
    return profile


def recommend_profile(*, task: str, risk: str = "normal", path: str | Path | None = None) -> dict[str, Any]:
    task_text = f"{task} {risk}".lower()
    candidates: list[tuple[int, str, dict[str, Any]]] = []
    rejected: list[dict[str, str]] = []
    all_profiles = profiles(load_profiles(path))
    for name, profile in all_profiles.items():
        rejection = _policy_rejection(name, profile, task_text)
        if rejection:
            rejected.append({"name": name, "reason": rejection})
            continue
        score = _score(name, profile, task_text)
        if score > 0:
            candidates.append((score, name, profile))
    if not candidates:
        fallback = dict(all_profiles.get("cheap_review", {}))
        if not fallback:
            raise SystemExit("No model profiles are available for this task")
        rejection = _policy_rejection("cheap_review", fallback, task_text)
        if rejection:
            raise SystemExit(f"No safe model profile for this task: {rejection}")
        fallback["name"] = "cheap_review"
        fallback["reason"] = "fallback normal review profile"
        fallback["policy"] = _policy_summary(fallback, task_text)
        fallback["rejected_profiles"] = rejected
        return fallback
    score, name, profile = sorted(candidates, key=lambda item: (-item[0], item[1]))[0]
    selected = dict(profile)
    selected["name"] = name
    selected["reason"] = f"matched profile routing score {score}"
    selected["policy"] = _policy_summary(selected, task_text)
    selected["rejected_profiles"] = rejected
    return selected


def validate_profile_for_task(name: str, profile: dict[str, Any], *, task: str, risk: str = "normal") -> None:
    task_text = f"{task} {risk}".lower()
    rejection = _policy_rejection(name, profile, task_text)
    if rejection:
        raise SystemExit(f"Model profile '{name}' is blocked for this task: {rejection}")


def render_profile(profile: dict[str, Any]) -> str:
    lines = [
        f"name: {profile.get('name', '')}",
        f"agent: {profile.get('agent', '')}",
        f"model: {profile.get('model', '')}",
        f"cost_tier: {profile.get('cost_tier', '')}",
        f"quality_tier: {profile.get('quality_tier', '')}",
        f"requires_opt_in: {str(bool(profile.get('requires_opt_in', True))).lower()}",
        f"sensitive_allowed: {str(bool(profile.get('sensitive_allowed', False))).lower()}",
        f"notes: {redact_text(str(profile.get('notes', '')))}",
    ]
    if profile.get("reason"):
        lines.append(f"reason: {redact_text(str(profile['reason']))}")
    policy = profile.get("policy")
    if isinstance(policy, dict):
        lines.append(f"policy: {redact_text(str(policy.get('status', '')))}")
        lines.append(f"safe_next_command: {redact_text(str(policy.get('safe_next_command', '')))}")
    return "\n".join(lines)


def _score(name: str, profile: dict[str, Any], task_text: str) -> int:
    tokens = _tokens(task_text)
    use_tokens = _tokens(" ".join(str(item) for item in profile.get("use_when", [])))
    avoid_tokens = _tokens(" ".join(str(item) for item in profile.get("avoid_when", [])))
    if tokens.intersection(avoid_tokens):
        return -1
    score = len(tokens.intersection(use_tokens)) * 3
    if "security" in tokens and name == "security_gate":
        score += 3
    return score


def _policy_rejection(name: str, profile: dict[str, Any], task_text: str) -> str:
    tokens = _tokens(task_text)
    avoid_tokens = _tokens(" ".join(str(item) for item in profile.get("avoid_when", [])))
    blocked_tokens = sorted(tokens.intersection(avoid_tokens))
    if blocked_tokens:
        return f"task matches avoid_when tokens for {name}: {', '.join(blocked_tokens)}"
    if _has_sensitive_context(task_text) and not bool(profile.get("sensitive_allowed", False)):
        return f"{name} does not allow sensitive context"
    return ""


def _policy_summary(profile: dict[str, Any], task_text: str) -> dict[str, str]:
    return {
        "status": "allowed",
        "sensitivity": "sensitive" if _has_sensitive_context(task_text) else "normal",
        "cost_tier": redact_text(str(profile.get("cost_tier", ""))),
        "quality_tier": redact_text(str(profile.get("quality_tier", ""))),
        "safe_next_command": "Run packet preflight before any sidecar/provider call.",
    }


def _has_sensitive_context(task_text: str) -> bool:
    return any(marker in task_text for marker in SENSITIVE_CONTEXT_MARKERS)


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[a-zA-Z0-9_.:/-]+", text) if len(token) > 1}
