"""Deterministic provider-free router for safe local Hipson chat tasks."""

from __future__ import annotations

import re
from dataclasses import dataclass

from hipson.redaction import redact_text
from hipson.tools import ToolResult

TOKEN_RE = re.compile(r"[a-z0-9]+")
MAX_LOCAL_ANSWER_CHARS = 2_400
MAX_LOCAL_LIST_ITEMS = 10


@dataclass(frozen=True)
class LocalRoute:
    intent: str
    tool_name: str | None
    input_data: dict[str, object]
    explanation: str
    missing_input: str = ""


def route_local_request(request: str) -> LocalRoute | None:
    tokens = _tokens(request)
    if not tokens:
        return None
    normalized = " ".join(tokens)

    if _is_memory_search(tokens, normalized):
        query = _extract_memory_query(tokens)
        if not query:
            return LocalRoute(
                intent="memory_search",
                tool_name=None,
                input_data={},
                explanation="Memory search needs a query.",
                missing_input="memory query",
            )
        return LocalRoute(
            intent="memory_search",
            tool_name="memory.search",
            input_data={"query": query, "limit": 5},
            explanation="Search local Hipson memory for matching notes.",
        )

    if _is_skill_list(tokens, normalized):
        return LocalRoute(
            intent="skill_list",
            tool_name="skill.list",
            input_data={},
            explanation="List available skills without loading full skill text.",
        )

    if _is_changed_files(tokens, normalized):
        return LocalRoute(
            intent="changed_files",
            tool_name="repo.changed_files",
            input_data={"path": "."},
            explanation="List changed and untracked files in the current repository.",
        )

    if _is_repo_scan(tokens, normalized):
        return LocalRoute(
            intent="repo_scan",
            tool_name="repo.scan",
            input_data={"path": ".", "include_diff": False, "diff_lines": 3},
            explanation="Scan the current repository and propose a safe next local PR.",
        )

    return None


def supported_local_intents() -> list[str]:
    return [
        "scan this repo / review current repo / propose next safe PR",
        "show changed files / what changed / git status",
        "search memory for <query> / what do we remember about <query>",
        "list skills / show available skills",
    ]


def unsupported_local_route_answer(request: str) -> str:
    supported = "\n".join(f"- {intent}" for intent in supported_local_intents())
    return _bounded(
        "Local/router mode could not map this request to a safe built-in workflow.\n"
        f"Request: {redact_text(request)}\n\n"
        "Supported local-router intents:\n"
        f"{supported}\n\n"
        "Use --fake for explicit offline fake-provider demos, or --provider only when a real provider is configured.",
        MAX_LOCAL_ANSWER_CHARS,
    )


def render_local_route_answer(
    route: LocalRoute,
    result: ToolResult | None,
    persisted_tool_call: dict[str, object] | None = None,
) -> str:
    if route.tool_name is None:
        return _bounded(
            f"Local/router mode recognized `{route.intent}` but needs a {route.missing_input}.\n"
            "Try: `hipson chat -q \"search memory for runtime approvals\"`.",
            MAX_LOCAL_ANSWER_CHARS,
        )
    if result is None:
        error = str(persisted_tool_call.get("error", "")) if persisted_tool_call else "Tool execution did not produce a result."
        return _bounded(
            f"Local/router mode selected `{route.tool_name}`, but the tool did not complete.\n"
            f"Reason: {redact_text(error)}",
            MAX_LOCAL_ANSWER_CHARS,
        )
    if not result.ok:
        detail = result.error or result.summary
        return _bounded(
            f"Local/router mode selected `{route.tool_name}`, but the tool failed.\n"
            f"Reason: {redact_text(detail)}",
            MAX_LOCAL_ANSWER_CHARS,
        )
    if route.intent == "repo_scan":
        return _repo_scan_answer(result)
    if route.intent == "changed_files":
        return _changed_files_answer(result)
    if route.intent == "memory_search":
        return _memory_search_answer(result)
    if route.intent == "skill_list":
        return _skill_list_answer(result)
    return _bounded(f"Local/router mode completed `{route.tool_name}`: {result.summary}", MAX_LOCAL_ANSWER_CHARS)


def _is_repo_scan(tokens: tuple[str, ...], normalized: str) -> bool:
    token_set = set(tokens)
    repo_words = {"repo", "repository", "project"}
    if "scan" in token_set and (token_set & repo_words or "this" in token_set):
        return True
    if "review" in token_set and (token_set & repo_words or "current" in token_set):
        return True
    if _has_phrase(normalized, "propose next safe pr") or _has_phrase(normalized, "next safe pr"):
        return True
    if {"fix", "next"}.issubset(token_set) and {"what", "should"}.issubset(token_set):
        return True
    return False


def _is_changed_files(tokens: tuple[str, ...], normalized: str) -> bool:
    token_set = set(tokens)
    if _has_phrase(normalized, "git status") or _has_phrase(normalized, "what changed"):
        return True
    if "changed" in token_set and ("files" in token_set or "file" in token_set):
        return True
    if {"list", "repo", "changes"}.issubset(token_set):
        return True
    return False


def _is_memory_search(tokens: tuple[str, ...], normalized: str) -> bool:
    token_set = set(tokens)
    return (
        "memory" in token_set
        or "remember" in token_set
        or _has_phrase(normalized, "what do we remember")
        or _has_phrase(normalized, "what do you remember")
    )


def _is_skill_list(tokens: tuple[str, ...], normalized: str) -> bool:
    token_set = set(tokens)
    return (
        _has_phrase(normalized, "list skills")
        or _has_phrase(normalized, "show available skills")
        or _has_phrase(normalized, "what skills")
        or ("skills" in token_set and ("list" in token_set or "available" in token_set or "show" in token_set))
    )


def _extract_memory_query(tokens: tuple[str, ...]) -> str:
    token_list = list(tokens)
    starts: list[int] = []
    for marker in ("about", "for"):
        if marker in token_list:
            starts.append(token_list.index(marker) + 1)
    for marker in ("memory", "remember", "search", "find"):
        if marker in token_list:
            starts.append(token_list.index(marker) + 1)
    for start in sorted(starts):
        query_tokens = [token for token in token_list[start:] if token not in _query_stop_tokens()]
        query = " ".join(query_tokens).strip()
        if query and query not in {"memory", "remember"}:
            return query
    return ""


def _query_stop_tokens() -> set[str]:
    return {"memory", "memories", "remember", "search", "find", "for", "about", "the", "a", "an", "do", "we", "you"}


def _repo_scan_answer(result: ToolResult) -> str:
    output = result.output
    changed = _string_list(output.get("changed_files"))
    commands = _string_list(output.get("commands"))
    lines = [
        "Local/router mode executed `repo.scan` in read-only mode.",
        f"Repository state: {len(changed)} changed or untracked file(s) reported by the local scan.",
        "Changed files:",
        *_format_items(changed, empty="none"),
        "Discovered commands:",
        *_format_items(commands, empty="none detected"),
        "Next safe PR:",
        _next_safe_pr(changed, commands),
        "This is deterministic local routing based only on local tool output, not provider analysis.",
    ]
    return _bounded("\n".join(lines), MAX_LOCAL_ANSWER_CHARS)


def _changed_files_answer(result: ToolResult) -> str:
    output = result.output
    changed = _string_list(output.get("changed_files"))
    untracked = _string_list(output.get("untracked_files"))
    lines = ["Local/router mode executed `repo.changed_files` in read-only mode."]
    if not changed and not untracked:
        lines.append("No changed or untracked files were found.")
    else:
        lines.extend(["Changed files:", *_format_items(changed, empty="none")])
        lines.extend(["Untracked files:", *_format_items(untracked, empty="none")])
    return _bounded("\n".join(lines), MAX_LOCAL_ANSWER_CHARS)


def _memory_search_answer(result: ToolResult) -> str:
    raw_results = result.output.get("results", [])
    rows = raw_results if isinstance(raw_results, list) else []
    lines = [f"Local/router mode executed `memory.search` and found {len(rows)} result(s)."]
    if not rows:
        lines.append("No matching memory notes were found.")
    for row in rows[:MAX_LOCAL_LIST_ITEMS]:
        if isinstance(row, dict):
            summary = redact_text(str(row.get("summary", "")))
            repo = redact_text(str(row.get("repo", "")))
            kind = redact_text(str(row.get("kind", "")))
            lines.append(f"- {kind or 'memory'} {f'[{repo}]' if repo else ''}: {summary}")
    return _bounded("\n".join(lines), MAX_LOCAL_ANSWER_CHARS)


def _skill_list_answer(result: ToolResult) -> str:
    raw_skills = result.output.get("skills", [])
    skills = raw_skills if isinstance(raw_skills, list) else []
    lines = [f"Local/router mode executed `skill.list` and found {len(skills)} skill(s)."]
    for skill in skills[:MAX_LOCAL_LIST_ITEMS]:
        if isinstance(skill, dict):
            name = redact_text(str(skill.get("name", "")))
            description = redact_text(str(skill.get("description", "")))
            lines.append(f"- {name}: {_bounded(description, 140)}")
    if len(skills) > MAX_LOCAL_LIST_ITEMS:
        lines.append(f"- ... {len(skills) - MAX_LOCAL_LIST_ITEMS} more skill(s)")
    return _bounded("\n".join(lines), MAX_LOCAL_ANSWER_CHARS)


def _next_safe_pr(changed: list[str], commands: list[str]) -> str:
    if changed:
        first = changed[0]
        return (
            f"Stabilize the current local change set in a focused PR, starting with `{redact_text(first)}`. "
            "Run the relevant local verification command before committing."
        )
    if commands:
        return (
            f"Keep the tree clean and choose a small documented hardening or test task; "
            f"verify with `{redact_text(commands[0])}` if it applies."
        )
    return "The scan found no changed files or discovered commands; pick a small documented backlog item and add focused tests."


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(TOKEN_RE.findall(text.casefold()))


def _has_phrase(normalized: str, phrase: str) -> bool:
    normalized_phrase = " ".join(_tokens(phrase))
    return f" {normalized_phrase} " in f" {normalized} "


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [redact_text(str(item)) for item in value if str(item).strip()]


def _format_items(items: list[str], *, empty: str) -> list[str]:
    if not items:
        return [f"- {empty}"]
    lines = [f"- `{item}`" for item in items[:MAX_LOCAL_LIST_ITEMS]]
    if len(items) > MAX_LOCAL_LIST_ITEMS:
        lines.append(f"- ... {len(items) - MAX_LOCAL_LIST_ITEMS} more")
    return lines


def _bounded(value: str, limit: int) -> str:
    redacted = redact_text(value)
    if len(redacted) <= limit:
        return redacted
    marker = f"... [truncated to {limit} chars]"
    return redacted[: max(0, limit - len(marker))].rstrip() + marker


__all__ = [
    "LocalRoute",
    "render_local_route_answer",
    "route_local_request",
    "supported_local_intents",
    "unsupported_local_route_answer",
]
