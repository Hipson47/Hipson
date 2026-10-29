"""Actor routing: a deterministic recommendation of which worker should take a piece of work.

Recommendation only. It reads an actor registry (user config) and the ledger's history and never launches,
assigns or authorizes anything. The lead decides.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import statistics
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from hipson import ledger
from hipson.display import bounded_text, escape_display_controls
from hipson.home import detect_hipson_home

ROUTE_SCHEMA_VERSION = "1.0"
BUILTIN_CAPABILITIES = ("browser", "computer_use", "web_research", "long_context", "code_write", "code_review",
                        "creative_writing", "image_gen")
SENSITIVITY_LEVELS = ("public", "internal", "confidential", "secret")
DEFAULT_MIN_SAMPLE = 5
DEFAULT_HISTORY_WEIGHT = 2.0
DEFAULT_PRIOR = 0.5
REWORK_PENALTY = 0.5
AUTHORITY = "Recommendation only: Hipson never launches, assigns or authorizes the recommended actor."
_TOP_KEYS = {"version", "min_sample", "history_weight", "capabilities", "actors", "description"}
_ACTOR_KEYS = {"id", "vendor", "model", "capabilities", "priors", "default_prior", "cost_tier", "roles", "modes",
               "max_sensitivity", "binary", "available", "notes"}


class RoutingError(ValueError):
    """The request or the actor registry cannot produce a recommendation."""


def registry_path(explicit: str | Path | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    return detect_hipson_home()[0] / "actors.json"


def example_registry() -> str:
    from hipson.assets import runtime_asset
    return runtime_asset("config/actors.example.json").read_text(encoding="utf-8")


def _unit(value: object) -> bool:
    return type(value) in (int, float) and 0 <= value <= 1  # type: ignore[operator]


def load_registry(path: Path) -> dict[str, Any]:
    """Read and strictly validate the registry; every problem is reported, nothing is defaulted silently."""
    if not path.is_file():
        raise RoutingError(f"No actor registry at {path}. Create it from the example (hipson route actor "
                           "--print-example > that path) and set your own priors; Hipson ships no vendor ranking.")
    raw = path.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RoutingError(f"Actor registry {path} is not valid JSON: {exc}") from exc
    problems = validate_registry(data)
    if problems:
        raise RoutingError(f"Actor registry {path} is invalid: " + "; ".join(problems))
    known = (*BUILTIN_CAPABILITIES, *[tag for tag in data.get("capabilities", []) if tag not in BUILTIN_CAPABILITIES])
    actors = []
    for item in data["actors"]:
        actors.append({
            "id": item["id"], "vendor": item["vendor"].strip().lower(), "model": item.get("model"),
            "capabilities": list(dict.fromkeys(item["capabilities"])), "priors": dict(item.get("priors", {})),
            "default_prior": item.get("default_prior", DEFAULT_PRIOR), "cost_tier": item.get("cost_tier", 0),
            "roles": list(item.get("roles", ledger.ROLES)), "modes": list(item.get("modes", ledger.MODES)),
            "max_sensitivity": item.get("max_sensitivity", "internal"), "binary": item.get("binary"),
            "available": item.get("available", True),
        })
    return {"path": str(path), "version": data["version"], "sha256": hashlib.sha256(raw).hexdigest(),
            "min_sample": data.get("min_sample", DEFAULT_MIN_SAMPLE),
            "history_weight": data.get("history_weight", DEFAULT_HISTORY_WEIGHT),
            "capabilities": list(known), "actors": actors}


def validate_registry(data: object) -> list[str]:
    if not isinstance(data, dict):
        return ["the registry must be a JSON object"]
    problems = [f"unknown key {key!r}" for key in sorted(set(data) - _TOP_KEYS)]
    if not isinstance(data.get("version"), str) or not data.get("version"):
        problems.append("version must be a non-empty string")
    if "min_sample" in data and (type(data["min_sample"]) is not int or data["min_sample"] < 1):
        problems.append("min_sample must be a positive integer")
    if "history_weight" in data and not (type(data["history_weight"]) in (int, float)
                                         and math.isfinite(data["history_weight"]) and 0 < data["history_weight"] <= 1000):
        problems.append("history_weight must be a finite number between 0 (exclusive) and 1000")
    extra = data.get("capabilities", [])
    if not isinstance(extra, list) or not all(isinstance(tag, str) and ledger.NEED_PATTERN.fullmatch(tag)
                                              for tag in extra):  # Strings first: unhashable members never crash.
        problems.append("capabilities must be a list of lower-case tag names")
        extra = []
    known = set(BUILTIN_CAPABILITIES) | set(extra)
    actors = data.get("actors")
    if not isinstance(actors, list) or not actors:
        return [*problems, "actors must be a non-empty list"]
    seen: set[str] = set()
    for index, item in enumerate(actors):
        where = f"actors[{index}]"
        if not isinstance(item, dict):
            problems.append(f"{where} must be an object")
            continue
        problems += [f"{where}: unknown key {key!r}" for key in sorted(set(item) - _ACTOR_KEYS)]
        actor_id = item.get("id")
        if not isinstance(actor_id, str) or not actor_id.strip() or actor_id != actor_id.strip():
            # The ledger strips actor labels, so a padded id would never match its own history or builders.
            problems.append(f"{where}: id must be the actor label used in the ledger (e.g. codex), without "
                            "surrounding whitespace")
        elif actor_id in seen:
            problems.append(f"{where}: duplicate id {actor_id!r}")
        else:
            seen.add(actor_id)
        if not isinstance(item.get("vendor"), str) or not item["vendor"].strip():
            problems.append(f"{where}: vendor must be a non-empty string")
        for key in ("model", "binary", "notes"):
            if key in item and not isinstance(item[key], str):
                problems.append(f"{where}: {key} must be a string")
        tags = item.get("capabilities")
        if not isinstance(tags, list) or not tags or not all(isinstance(tag, str) for tag in tags):
            problems.append(f"{where}: capabilities must be a non-empty list of names")
            tags = []
        problems += [f"{where}: unknown capability {tag!r} (declare it in top-level capabilities)"
                     for tag in tags if tag not in known]
        priors = item.get("priors", {})
        if not isinstance(priors, dict):
            problems.append(f"{where}: priors must map capability to a number between 0 and 1")
        else:
            problems += [f"{where}: prior for {tag!r} must be between 0 and 1" for tag, value in priors.items()
                         if not _unit(value)]
            problems += [f"{where}: prior for unknown capability {tag!r}" for tag in priors if tag not in known]
        if "default_prior" in item and not _unit(item["default_prior"]):
            problems.append(f"{where}: default_prior must be between 0 and 1")
        if "cost_tier" in item and (type(item["cost_tier"]) is not int or item["cost_tier"] < 0):
            problems.append(f"{where}: cost_tier must be a nonnegative integer")
        for key, allowed in (("roles", ledger.ROLES), ("modes", ledger.MODES)):
            if key in item and (not isinstance(item[key], list) or not all(isinstance(value, str) for value in item[key])
                                or not set(item[key]) <= set(allowed)):
                problems.append(f"{where}: {key} must be a list drawn from {', '.join(allowed)}")
        if "max_sensitivity" in item and item["max_sensitivity"] not in SENSITIVITY_LEVELS:
            problems.append(f"{where}: max_sensitivity must be one of {', '.join(SENSITIVITY_LEVELS)}")
        if "available" in item and type(item["available"]) is not bool:
            problems.append(f"{where}: available must be true or false")
    return problems


def _date(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _lead_verification(record: Mapping[str, Any], lead: str) -> bool:
    """The ledger's own rule: recorded by the task lead in a lead context, never from a worker."""
    context = record.get("context")
    return (record.get("actor") == lead and isinstance(context, dict) and set(context) == {"role", "assignment", "task"}
            and str(context.get("role") or "").strip().lower() != "worker" and context.get("assignment") is None)


def outcomes(store: ledger.Ledger | None, *, role: str) -> list[dict[str, Any]]:
    """One record per ended assignment in this role that carries capability tags, oldest task first."""
    if store is None:
        return []
    records = []
    for task in sorted(store.list_tasks(), key=lambda item: item["id"]):
        view = store.show(task["id"])
        checks = sorted(((stamp, record) for record in store.verifications(task["id"])
                         if _lead_verification(record, view["lead"])
                         and (stamp := _date(record.get("started_at"))) is not None), key=lambda pair: pair[0])
        builders = [(item["id"], _date(item["started_at"])) for item in view["assignments"] if item["role"] == "builder"]
        for item in view["assignments"]:
            ended = _date(item["ended_at"])
            if item["role"] != role or item["status"] == "active" or ended is None or not item.get("needs"):
                continue
            counts = ((item.get("changes") or {}).get("counts") or {}).get("out_of_scope") or {}
            first = next((record for stamp, record in checks if stamp >= ended), None)
            started = _date(item["started_at"])
            records.append({
                "assignment": item["id"], "task": task["id"], "actor": item["actor"], "needs": item["needs"],
                "passed": item["status"] == "passed",
                "scope_violation": sum(value for value in counts.values() if type(value) is int) > 0,
                "first_verification_passed": None if first is None else first.get("exit_code") == 0,
                # Another builder starting later on the same task means this work was redone.
                "reworked": role == "builder" and any(other != item["id"] and begun is not None and begun > ended
                                                      for other, begun in builders),
                "duration_seconds": max(0.0, (ended - started).total_seconds()) if started else None,
            })
    return records


def _rate(values: Sequence[bool]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def history(records: Iterable[Mapping[str, Any]], actor: str, tag: str) -> dict[str, Any]:
    mine = [record for record in records if record["actor"] == actor and tag in record["needs"]]
    verified = [record["first_verification_passed"] for record in mine if record["first_verification_passed"] is not None]
    success = [record["passed"] and not record["scope_violation"] and record["first_verification_passed"] is not False
               for record in mine]
    rework = [record["reworked"] for record in mine]
    durations = [record["duration_seconds"] for record in mine if record["duration_seconds"] is not None]
    score = None
    if mine:
        score = round(min(1.0, max(0.0, sum(success) / len(mine) - REWORK_PENALTY * sum(rework) / len(mine))), 4)
    return {"samples": len(mine), "score": score, "success_rate": _rate(success),
            "first_pass_verify_rate": _rate(verified), "verified_samples": len(verified), "rework_rate": _rate(rework),
            "scope_violation_rate": _rate([record["scope_violation"] for record in mine]),
            "median_duration_seconds": round(statistics.median(durations), 3) if durations else None}


def _builder_vendors(store: ledger.Ledger | None, task: str | None, actors: Mapping[str, Mapping[str, Any]],
                     notes: list[str]) -> dict[str, str]:
    if store is None or task is None:
        return {}
    vendors = {}
    for item in store.show(task)["assignments"]:
        if item["role"] != "builder":
            continue
        entry = actors.get(item["actor"])
        if entry is None:
            notes.append(f"builder {item['actor']} of {task} is not in the registry; its vendor is unknown")
        else:
            vendors[item["actor"]] = str(entry["vendor"])
    return vendors


def recommend(registry: Mapping[str, Any], store: ledger.Ledger | None, *, needs: Sequence[str], role: str = "builder",
              mode: str = "ro", task: str | None = None, sensitivity: str = "internal",
              probe: bool = True) -> dict[str, Any]:
    """Rank registry actors for the request. Deterministic for a fixed registry, ledger and PATH."""
    tags = ledger.validate_needs(list(needs))
    if not tags:
        raise RoutingError("name at least one capability with --needs (e.g. --needs code_write)")
    unknown = [tag for tag in tags if tag not in registry["capabilities"]]
    if unknown:
        raise RoutingError(f"unknown capability {', '.join(unknown)}; known: {', '.join(registry['capabilities'])}")
    if role not in ledger.ROLES or mode not in ledger.MODES:
        raise RoutingError(f"role must be one of {', '.join(ledger.ROLES)} and mode one of {', '.join(ledger.MODES)}")
    if sensitivity not in SENSITIVITY_LEVELS:
        raise RoutingError(f"sensitivity must be one of {', '.join(SENSITIVITY_LEVELS)}")
    if task is not None and store is None:
        raise RoutingError(f"task {task} needs a task ledger")
    if task is not None and store is not None:
        store.show(task)  # Raises LedgerError for an unknown task, whatever else was given.
    notes: list[str] = []
    actors = {entry["id"]: entry for entry in registry["actors"]}
    builder_vendors = _builder_vendors(store, task, actors, notes) if role == "reviewer" else {}
    records = outcomes(store, role=role)
    min_sample, weight = registry["min_sample"], float(registry["history_weight"])
    candidates, rejected = [], []
    for entry in sorted(registry["actors"], key=lambda item: item["id"]):
        reasons = []
        if role not in entry["roles"]:
            reasons.append(f"does not take the {role} role")
        if mode not in entry["modes"]:
            reasons.append(f"does not work in {mode} mode")
        missing = [tag for tag in tags if tag not in entry["capabilities"]]
        if missing:
            reasons.append(f"missing capability {', '.join(missing)}")
        if SENSITIVITY_LEVELS.index(sensitivity) > SENSITIVITY_LEVELS.index(entry["max_sensitivity"]):
            reasons.append(f"not allowed {sensitivity} work (max {entry['max_sensitivity']})")
        if not entry["available"]:
            reasons.append("marked unavailable in the registry")
        elif probe and entry["binary"] and shutil.which(entry["binary"]) is None:
            reasons.append(f"unavailable: {entry['binary']} is not on PATH")
        same = sorted(actor for actor, vendor in builder_vendors.items() if vendor == entry["vendor"])
        if same:
            reasons.append(f"same vendor ({entry['vendor']}) as builder {', '.join(same)}; a reviewer must be independent")
        if reasons:
            rejected.append({"actor": entry["id"], "vendor": entry["vendor"], "reasons": reasons})
            continue
        components: list[dict[str, Any]] = []
        scores: list[float] = []
        for tag in tags:
            prior = float(entry["priors"].get(tag, entry["default_prior"]))
            past = history(records, entry["id"], tag)
            # History counts only from min_sample on; from there it weighs history_weight * n / min_sample against
            # the prior's 1, so at min_sample it already outweighs the prior.
            share = weight * past["samples"] / min_sample if past["samples"] >= min_sample and past["score"] is not None else 0.0
            score = (prior + share * past["score"]) / (1 + share) if share else prior
            scores.append(round(score, 4))
            components.append({"tag": tag, "prior": round(prior, 4), "history": past, "history_weight": round(share, 4),
                               "score": scores[-1]})
        candidates.append({"actor": entry["id"], "vendor": entry["vendor"], "model": entry["model"],
                           "cost_tier": entry["cost_tier"], "score": round(sum(scores) / len(scores), 4),
                           "components": components})
    # Ties break on lower cost, then on the actor id, so equal inputs always give the same order.
    candidates.sort(key=lambda item: (-item["score"], item["cost_tier"], item["actor"]))
    return {
        "artifact_kind": "hipson.actor_route", "schema_version": ROUTE_SCHEMA_VERSION,
        "request": {"needs": tags, "role": role, "mode": mode, "task": task, "sensitivity": sensitivity,
                    "probe": probe},
        "registry": {"path": registry["path"], "version": registry["version"], "sha256": registry["sha256"],
                     "min_sample": min_sample, "history_weight": registry["history_weight"]},
        "history_basis": {"assignments": len(records), "role": role,
                          "builder_vendors": dict(sorted(builder_vendors.items()))},
        "recommended": candidates[0]["actor"] if candidates else None,
        "candidates": candidates, "rejected": rejected, "notes": notes, "authority": AUTHORITY,
        "recorded_event": None,
    }


def request(registry: Mapping[str, Any], store: ledger.Ledger | None, *, needs: Sequence[str], role: str = "builder",
            mode: str = "ro", task: str | None = None, sensitivity: str = "internal", probe: bool = True,
            record: bool = False) -> dict[str, Any]:
    """CLI and MCP entry point: tags default to the task's, and with record the result becomes a 'routed' event."""
    tags = [tag.strip() for value in needs for tag in str(value).split(",") if tag.strip()]
    if not tags and task is not None and store is not None:
        tags = store.show(task)["needs"]
    route = recommend(registry, store, needs=tags, role=role, mode=mode, task=task, sensitivity=sensitivity,
                      probe=probe)
    if record:
        if store is None or task is None:
            raise RoutingError("recording a recommendation needs --task and a task ledger")
        refs = [item["actor"] for item in route["candidates"][:1]] + [route["registry"]["sha256"][:12]]
        route["recorded_event"] = store.record_route(task, actor="hipson", summary=event_summary(route), refs=refs)["seq"]
    return route


def event_summary(route: Mapping[str, Any]) -> str:
    request = route["request"]
    head = (f"route {request['role']}/{request['mode']} needs {','.join(request['needs'])}: "
            + (f"recommended {route['recommended']} ({route['candidates'][0]['score']})" if route["recommended"]
               else "no eligible actor"))
    others = [f"{item['actor']} {item['score']}" for item in route["candidates"][1:4]]
    refused = [f"{item['actor']} ({item['reasons'][0]})" for item in route["rejected"][:4]]
    return "; ".join([head, *(["then " + ", ".join(others)] if others else []),
                      *(["rejected " + ", ".join(refused)] if refused else []),
                      f"registry {route['registry']['version']} {route['registry']['sha256'][:12]}"])


def _shown(value: object, limit: int = 160) -> str:
    # Registry ids and ledger labels are user text: one inert line each, never terminal controls.
    return bounded_text(escape_display_controls(str(value)), limit=limit)


def render(route: Mapping[str, Any]) -> str:
    request = route["request"]
    lines = [f"Route {request['role']}/{request['mode']} for {', '.join(request['needs'])}"
             + (f" on {_shown(request['task'], 40)}" if request["task"] else "")
             + f" (registry {route['registry']['version']}, {route['history_basis']['assignments']} past assignments)"]
    if route["recommended"]:
        lines.append(f"Recommended: {_shown(route['recommended'])}")
    else:
        lines.append("Recommended: none (every actor was rejected)")
    for rank, item in enumerate(route["candidates"], 1):
        parts = ", ".join(f"{part['tag']} {part['score']} (prior {part['prior']}, {part['history']['samples']} samples"
                          + (f", history {part['history']['score']}" if part["history_weight"] else "") + ")"
                          for part in item["components"])
        lines.append(f"  {rank}. {_shown(item['actor'])} [{_shown(item['vendor'])}] score {item['score']} "
                     f"cost {item['cost_tier']}: {parts}")
    for item in route["rejected"]:
        lines.append(f"  rejected {_shown(item['actor'])}: {_shown('; '.join(item['reasons']), 400)}")
    lines += [f"  note: {_shown(note, 400)}" for note in route["notes"]]
    if route["recorded_event"] is not None:
        lines.append(f"Recorded as event {route['recorded_event']} on {request['task']}")
    lines.append(route["authority"])
    return "\n".join(lines)
