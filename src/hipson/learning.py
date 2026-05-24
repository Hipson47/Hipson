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


def propose_from_session(store: SessionStore, session_id: str, *, max_summary_chars: int = 500) -> list[LearningProposal]:
    session = store.get_session(session_id)
    if session is None:
        raise LearningError(f"Session does not exist: {session_id}")

    messages = store.list_messages(session_id, limit=50)
    tool_calls = store.list_tool_calls(session_id)
    proposals: list[LearningProposal] = []
    memory = _memory_proposal(session, messages, max_summary_chars=max_summary_chars)
    if memory is not None:
        proposals.append(memory)
    skill = _skill_reference_proposal(str(session["id"]), messages, tool_calls)
    if skill is not None:
        proposals.append(skill)
    return proposals


def _memory_proposal(
    session: dict[str, object],
    messages: list[dict[str, object]],
    *,
    max_summary_chars: int,
) -> LearningProposal | None:
    if not messages:
        return None
    source = _last_non_empty_message(messages)
    if source is None:
        return None
    content = _cap(redact_text(str(source["content"])), max_summary_chars)
    summary = redact_text(f"Session outcome candidate: {content}")
    source_refs = [f"session:{session['id']}", f"message:{source['id']}"]
    repo = str(session.get("repo_root") or session.get("cwd") or "")
    payload: dict[str, object] = {
        "scope": "repo" if session.get("repo_root") else "session",
        "repo": redact_metadata(repo),
        "kind": "handoff",
        "summary": summary,
        "tags": ["runtime", "session"],
        "sources": source_refs,
        "confidence": 0.6,
    }
    return LearningProposal(
        id=_proposal_id(str(session["id"]), "memory", summary, payload, source_refs),
        kind="memory",
        summary=summary,
        payload=payload,
        source_refs=source_refs,
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
