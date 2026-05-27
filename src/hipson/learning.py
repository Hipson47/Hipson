"""Approval-gated learning proposal helpers for runtime sessions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Literal

from hipson.redaction import redact_metadata, redact_text
from hipson.session import SessionStore

ProposalKind = Literal["memory", "skill_reference"]


class LearningError(ValueError):
    """Raised when learning proposals cannot be generated."""


@dataclass(frozen=True)
class LearningProposal:
    id: str
    kind: ProposalKind
    summary: str
    payload: dict[str, object]
    source_refs: list[str]
    confidence: float = 0.6
    approval_required: bool = True
    approval_status: str = "proposed"
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind,
            "summary": self.summary,
            "payload": self.payload,
            "source_refs": self.source_refs,
            "confidence": self.confidence,
            "approval_required": self.approval_required,
            "approval_status": self.approval_status,
            "tags": self.tags,
        }


@dataclass(frozen=True)
class _Trajectory:
    summary: str
    request: str
    outcome: str
    tool_summary: str
    source_refs: list[str]
    confidence: float


def propose_from_session(store: SessionStore, session_id: str, *, max_summary_chars: int = 500) -> list[LearningProposal]:
    session = store.get_session(session_id)
    if session is None:
        raise LearningError(f"Session does not exist: {session_id}")

    messages = store.list_messages(session_id, limit=50)
    tool_calls = store.list_tool_calls(session_id)
    proposals: list[LearningProposal] = []
    memory = _memory_proposal(session, messages, tool_calls, max_summary_chars=max_summary_chars)
    if memory is not None:
        proposals.append(memory)
    skill = _skill_reference_proposal(str(session["id"]), messages, tool_calls)
    if skill is not None:
        proposals.append(skill)
    return proposals


def _memory_proposal(
    session: dict[str, object],
    messages: list[dict[str, object]],
    tool_calls: list[dict[str, object]],
    *,
    max_summary_chars: int,
) -> LearningProposal | None:
    if not messages:
        return None
    trajectory = _session_trajectory(messages, tool_calls, max_summary_chars=max_summary_chars)
    if trajectory is None:
        return None
    summary = redact_text(f"Session trajectory candidate: {trajectory.summary}")
    source_refs = [f"session:{session['id']}", *trajectory.source_refs]
    repo = str(session.get("repo_root") or session.get("cwd") or "")
    payload: dict[str, object] = {
        "scope": "repo" if session.get("repo_root") else "session",
        "repo": redact_metadata(repo),
        "kind": "handoff",
        "summary": summary,
        "request": trajectory.request,
        "outcome": trajectory.outcome,
        "tool_summary": trajectory.tool_summary,
        "tags": ["runtime", "session"],
        "sources": source_refs,
        "confidence": trajectory.confidence,
    }
    return LearningProposal(
        id=_proposal_id(str(session["id"]), "memory", summary, payload, source_refs),
        kind="memory",
        summary=summary,
        payload=payload,
        source_refs=source_refs,
        confidence=trajectory.confidence,
        tags=["runtime", "session"],
    )


def _skill_reference_proposal(
    session_id: str,
    messages: list[dict[str, object]],
    tool_calls: list[dict[str, object]],
) -> LearningProposal | None:
    name = _skill_name_from_tool_calls(tool_calls) or _skill_name_from_messages(messages)
    if name is None:
        return None
    source_refs = [f"message:{message['id']}" for message in messages if _mentions_skill(str(message.get("content", "")))]
    if not source_refs:
        source_refs = [f"tool_call:{call['id']}" for call in tool_calls if str(call.get("tool_name")) == "skill.view"]
    summary = redact_text(f"Consider using skill reference `{name}` for similar future sessions.")
    payload: dict[str, object] = {
        "skill": redact_metadata(name),
        "usage": "reference_data_only",
        "reason": summary,
    }
    return LearningProposal(
        id=_proposal_id(session_id, "skill_reference", summary, payload, source_refs),
        kind="skill_reference",
        summary=summary,
        payload=payload,
        source_refs=source_refs,
        confidence=0.5,
        tags=["skill", "reference"],
    )


def _last_non_empty_message(messages: list[dict[str, object]]) -> dict[str, object] | None:
    for message in reversed(messages):
        if str(message.get("content", "")).strip():
            return message
    return None


def _session_trajectory(
    messages: list[dict[str, object]],
    tool_calls: list[dict[str, object]],
    *,
    max_summary_chars: int,
) -> _Trajectory | None:
    request_message = _first_non_empty_role(messages, "user")
    outcome_message = _last_non_empty_role(messages, "assistant") or _last_non_empty_message(messages)
    if request_message is None and outcome_message is None and not tool_calls:
        return None

    request = _message_excerpt(request_message, max_summary_chars=max_summary_chars // 3) if request_message else ""
    outcome = _message_excerpt(outcome_message, max_summary_chars=max_summary_chars // 3) if outcome_message else ""
    tool_summary = _cap("; ".join(_tool_call_excerpt(call) for call in tool_calls[-5:]), max_summary_chars // 3)
    parts = []
    if request:
        parts.append(f"request: {request}")
    if outcome:
        parts.append(f"outcome: {outcome}")
    if tool_summary:
        parts.append(f"tools: {tool_summary}")
    summary = _cap(" | ".join(parts), max_summary_chars)
    source_refs: list[str] = []
    for message in (request_message, outcome_message):
        if message is not None:
            ref = f"message:{message['id']}"
            if ref not in source_refs:
                source_refs.append(ref)
    for call in tool_calls[-5:]:
        source_refs.append(f"tool_call:{call['id']}")
    confidence = 0.75 if tool_calls else 0.65
    return _Trajectory(
        summary=summary,
        request=request,
        outcome=outcome,
        tool_summary=tool_summary,
        source_refs=source_refs,
        confidence=confidence,
    )


def _first_non_empty_role(messages: list[dict[str, object]], role: str) -> dict[str, object] | None:
    for message in messages:
        if str(message.get("role")) == role and str(message.get("content", "")).strip():
            return message
    return None


def _last_non_empty_role(messages: list[dict[str, object]], role: str) -> dict[str, object] | None:
    for message in reversed(messages):
        if str(message.get("role")) == role and str(message.get("content", "")).strip():
            return message
    return None


def _message_excerpt(message: dict[str, object], *, max_summary_chars: int) -> str:
    return _cap(redact_text(str(message.get("content", ""))), max(80, max_summary_chars))


def _tool_call_excerpt(tool_call: dict[str, object]) -> str:
    name = redact_text(str(tool_call.get("tool_name", "")))
    status = redact_text(str(tool_call.get("status", "")))
    error = redact_text(str(tool_call.get("error", ""))).strip()
    return _cap(f"{name} {status}{': ' + error if error else ''}", 180)


def _skill_name_from_tool_calls(tool_calls: list[dict[str, object]]) -> str | None:
    for call in reversed(tool_calls):
        if str(call.get("tool_name")) != "skill.view":
            continue
        output = call.get("output")
        if isinstance(output, dict) and isinstance(output.get("name"), str):
            return output["name"]
    return None


def _skill_name_from_messages(messages: list[dict[str, object]]) -> str | None:
    for message in reversed(messages):
        content = str(message.get("content", "")).lower()
        if "hipson-workflow" in content:
            return "hipson-workflow"
        if _mentions_skill(content):
            return "hipson-workflow"
    return None


def _mentions_skill(content: str) -> bool:
    lowered = content.lower()
    return "skill" in lowered or "workflow" in lowered


def _cap(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    marker = f"\n[truncated to {max_chars} chars]"
    return text[: max(0, max_chars - len(marker))].rstrip() + marker


def _proposal_id(
    session_id: str,
    kind: ProposalKind,
    summary: str,
    payload: dict[str, object],
    source_refs: list[str],
) -> str:
    encoded = json.dumps(
        {
            "session_id": session_id,
            "kind": kind,
            "summary": summary,
            "payload": payload,
            "source_refs": source_refs,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return f"learn_{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:16]}"
