"""Provider readiness checks that do not send repository data."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from hipson import agents


def doctor_payload(*, config_path: str | None = None, env_path: str | None = None) -> dict[str, Any]:
    env_paths = agents.load_provider_envs(env_path)
    config = agents.load_json(Path(config_path).expanduser().resolve() if config_path else agents.DEFAULT_CONFIG)
    providers = config.get("providers", {})
    provider_results = {}
    for name, provider in sorted(providers.items()):
        if not isinstance(provider, dict):
            provider_results[str(name)] = {"ok": False, "error": "provider config must be an object"}
            continue
        provider_results[str(name)] = _provider_status(provider)
    agent_results = _agent_results(config)
    config_ok = all(item.get("ok") for item in provider_results.values()) and all(item.get("ok") for item in agent_results.values())
    ready_for_real_run = config_ok and all(item.get("api_key_present") for item in provider_results.values())
    return {
        "ok": config_ok,
        "config_ok": config_ok,
        "ready_for_real_run": ready_for_real_run,
        "config": str(config_path or agents.DEFAULT_CONFIG),
        "env_paths": [str(path) for path in env_paths],
        "sent_repo_data": False,
        "providers": provider_results,
        "agents": agent_results,
        "recommendations": _recommendations(provider_results, agent_results),
    }


def _provider_status(provider: dict[str, Any]) -> dict[str, Any]:
    key_name = str(provider.get("api_key_env", "OPENROUTER_API_KEY"))
    try:
        base_url = agents.validate_provider_base_url(provider)
        url_ok = True
        url_error = ""
    except SystemExit as exc:
        base_url = str(provider.get("base_url", ""))
        url_ok = False
        url_error = str(exc)
    return {
        "ok": url_ok,
        "base_url": base_url,
        "api_key_env": key_name,
        "api_key_present": bool(os.environ.get(key_name)),
        "url_ok": url_ok,
        "url_error": url_error,
    }


def _agent_results(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    results = {}
    for name, agent in sorted(config.get("agents", {}).items()):
        if not isinstance(agent, dict):
            results[str(name)] = {"ok": False, "error": "agent config must be an object"}
            continue
        model = str(agent.get("model", ""))
        provider = str(agent.get("provider", ""))
        errors = []
        if not provider:
            errors.append("missing provider")
        if provider not in config.get("providers", {}):
            errors.append(f"unknown provider: {provider}")
        if not model:
            errors.append("missing model")
        try:
            agents.normalize_model_override(model)
        except SystemExit as exc:
            errors.append(str(exc))
        if int(agent.get("context_budget", 0) or 0) < 0:
            errors.append("context_budget cannot be negative")
        results[str(name)] = {
            "ok": not errors,
            "provider": provider,
            "model": model,
            "requires_external_provider": bool(agent.get("requires_external_provider", True)),
            "can_handle_sensitive_context": bool(agent.get("can_handle_sensitive_context", False)),
            "errors": errors,
        }
    return results


def _recommendations(
    provider_results: dict[str, dict[str, Any]],
    agent_results: dict[str, dict[str, Any]],
) -> list[str]:
    recommendations = []
    if any(not provider.get("api_key_present") for provider in provider_results.values()):
        recommendations.append("Provider API keys are optional, but real sidecar runs need the configured env var.")
    if any(not provider.get("url_ok") for provider in provider_results.values()):
        recommendations.append("Fix provider base URLs before attempting real sidecar runs.")
    if any(not agent.get("ok") for agent in agent_results.values()):
        recommendations.append("Fix invalid agent config entries before routing sidecars.")
    recommendations.append("This doctor does not send repository files, packets, or prompts to providers.")
    return recommendations
