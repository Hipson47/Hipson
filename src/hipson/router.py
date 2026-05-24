"""Deterministic workflow routing for agent-native Hipson usage."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, TypedDict

Mode = Literal["review", "exec", "scan", "verify", "handoff", "sidecar-review", "memory"]
Risk = Literal["normal", "security", "architecture", "ui", "data-loss", "unknown"]

ROUTE_KEYS = ("mode", "risk", "recommended_skill", "commands", "requires_human_review", "reason")
TOKEN_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
BUILD_VERIFY_SIGNALS = frozenset(
    {"run", "verify", "failed", "failing", "failure", "check", "test", "tests", "ci", "release", "gate", "gates"}
)


class RouteResult(TypedDict):
    mode: Mode
    risk: Risk
    recommended_skill: str
    commands: list[str]
    requires_human_review: bool
    reason: str


@dataclass(frozen=True)
class Rule:
    mode: Mode
    recommended_skill: str
    keywords: tuple[str, ...]
    reason: str


MODE_RULES: tuple[Rule, ...] = (
    Rule("sidecar-review", "sidecar-review", ("second opinion", "sidecar", "model review"), "second-opinion task"),
    Rule("memory", "memory", ("remember", "memory", "decision"), "durable-memory task"),
    Rule("handoff", "handoff", ("summarize", "handoff", "progress"), "handoff/progress task"),
    Rule("verify", "verify", ("verify", "test", "build", "check", "release gate", "release gates"), "verification task"),
    Rule("scan", "repo-delta-scan", ("status", "current state", "what changed"), "repo-state task"),
    Rule("review", "review-packet", ("review", "security review", "test gap", "critique", "audit"), "review task"),
    Rule("exec", "executor-packet", ("implement", "fix", "add", "refactor"), "implementation task"),
)

RISK_RULES: tuple[tuple[Risk, tuple[str, ...], str], ...] = (
    ("security", ("security", "auth", "secret", "secrets", "token", "password"), "security-sensitive task"),
    (
        "data-loss",
        ("migration", "database", "delete", "data loss", "destructive"),
        "data-loss/destructive task",
    ),
    ("architecture", ("architecture", "refactor", "cross-module"), "architecture-impacting task"),
    ("ui", ("ui", "ux", "accessibility", "screenshot"), "UI/UX task"),
)


def route_task(task: str) -> RouteResult:
    normalized = _normalize(task)
    mode, skill, mode_reason = _detect_mode(normalized)
    risk, risk_reason = _detect_risk(normalized)
    commands = _commands_for(mode, risk, task)
    requires_human_review = risk in {"security", "architecture", "data-loss"} or mode in {"review", "sidecar-review"}
    reason_parts = [mode_reason]
    if risk_reason:
        reason_parts.append(risk_reason)
    return {
        "mode": mode,
        "risk": risk,
        "recommended_skill": skill,
        "commands": commands,
        "requires_human_review": requires_human_review,
        "reason": "; ".join(reason_parts),
    }


def format_text_route(route: RouteResult) -> str:
    lines = [
        f"mode: {route['mode']}",
        f"risk: {route['risk']}",
        f"recommended_skill: {route['recommended_skill']}",
        f"requires_human_review: {str(route['requires_human_review']).lower()}",
        f"reason: {route['reason']}",
        "commands:",
    ]
    lines.extend(f"- {command}" for command in route["commands"])
    return "\n".join(lines)


def _detect_mode(task: str) -> tuple[Mode, str, str]:
    tokens = _tokens(task)
    if _is_build_implementation_task(tokens):
        return "exec", "executor-packet", "implementation task"
    for rule in MODE_RULES:
        if any(_matches_keyword(tokens, keyword) for keyword in rule.keywords):
            return rule.mode, rule.recommended_skill, rule.reason
    if task:
        return "scan", "repo-delta-scan", "default repo-state task"
    return "scan", "repo-delta-scan", "empty task"


def _detect_risk(task: str) -> tuple[Risk, str]:
    if not task:
        return "unknown", "unknown risk"
    tokens = _tokens(task)
    for risk, keywords, reason in RISK_RULES:
        if any(_matches_keyword(tokens, keyword) for keyword in keywords):
            return risk, reason
    return "normal", ""


def _commands_for(mode: Mode, risk: Risk, task: str) -> list[str]:
    risk_arg = risk if risk != "unknown" else "normal"
    quoted_task = _quote(task or "[task]")
    commands_by_mode: dict[Mode, list[str]] = {
        "scan": ["hipson scan . --include-diff"],
        "review": [
            "hipson scan . --include-diff",
            f"hipson packet review . --title {_quote(_title(task, 'Review task'))} --include-diff -o runs/review-packet.md",
        ],
        "exec": [
            "hipson scan . --include-diff",
            (
                "hipson packet exec . "
                f"--title {_quote(_title(task, 'Implement task'))} "
                f"--goal {_quote(task or '[goal]')} "
                '--allowed-edit "[fill allowed files or directories]" '
                '--acceptance "[fill observable success]" '
                "-o runs/executor-packet.md"
            ),
        ],
        "verify": [
            "git diff --check",
            "[run project test/build/typecheck commands]",
        ],
        "handoff": [
            "hipson scan . --include-diff",
            'hipson memory add --scope repo --repo . --kind handoff --summary "[compact handoff]"',
        ],
        "sidecar-review": [
            "hipson scan . --include-diff",
            f"hipson packet review . --title {_quote(_title(task, 'Sidecar review'))} --include-diff -o runs/review-packet.md",
            f"hipson sidecar route --task {quoted_task} --risk {risk_arg}",
        ],
        "memory": [
            f"hipson memory search {quoted_task}",
            'hipson memory add --scope repo --repo . --kind decision --summary "[decision]"',
        ],
    }
    return commands_by_mode[mode]


def _normalize(task: str) -> str:
    return " ".join(task.casefold().split())


def _tokens(task: str) -> tuple[str, ...]:
    return tuple(TOKEN_RE.findall(task))


def _matches_keyword(tokens: tuple[str, ...], keyword: str) -> bool:
    keyword_tokens = _tokens(_normalize(keyword))
    if not keyword_tokens:
        return False
    if len(keyword_tokens) == 1:
        return keyword_tokens[0] in tokens
    return _contains_token_sequence(tokens, keyword_tokens)


def _contains_token_sequence(tokens: tuple[str, ...], keyword_tokens: tuple[str, ...]) -> bool:
    if len(keyword_tokens) > len(tokens):
        return False
    return any(
        tokens[index : index + len(keyword_tokens)] == keyword_tokens
        for index in range(len(tokens) - len(keyword_tokens) + 1)
    )


def _is_build_implementation_task(tokens: tuple[str, ...]) -> bool:
    return "build" in tokens and len(tokens) > 1 and not BUILD_VERIFY_SIGNALS.intersection(tokens)


def _title(task: str, fallback: str) -> str:
    compact = " ".join(task.split())
    if not compact:
        return fallback
    return compact[:80]


def _quote(value: str) -> str:
    safe = " ".join(value.split()).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{safe}"'
