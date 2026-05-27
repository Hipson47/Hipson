"""Minimal persistent runtime loop for offline Hipson chat."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from hipson.approvals import ApprovalPolicy
from hipson.home import detect_hipson_home
from hipson.project import git_root
from hipson.prompt import PromptContext, assemble_prompt_messages
from hipson.providers import ChatProvider, FakeProvider, ProviderError, ProviderRequest, ProviderToolCall
from hipson.redaction import redact_text
from hipson.session import SessionStore, open_session_store
from hipson.tools import (
    ToolContext,
    ToolRegistry,
    ToolRegistryError,
    ToolResult,
    ToolSpec,
    bounded_tool_output,
    build_default_registry,
)

DEFAULT_MODEL = "fake"
DEFAULT_MAX_TOOL_ITERATIONS = 3
NO_CHAT_PROVIDER_MESSAGE = (
    "No chat provider is configured. Use --fake for offline test mode, "
    "or configure a real provider after runtime provider support is implemented."
)
MAX_REJECTION_NOTICES = 3
MAX_REJECTION_SUMMARY_CHARS = 240


class RuntimeMode(StrEnum):
    NO_PROVIDER = "no_provider"
    FAKE = "fake"
    REAL = "real"


@dataclass(frozen=True)
class RuntimeToolCallRecord:
    name: str
    status: str
    summary: str


@dataclass(frozen=True)
class RuntimeResult:
    answer: str
    session_id: str
    tool_iterations: int
    tool_calls: list[RuntimeToolCallRecord] = field(default_factory=list)


@dataclass
class HipsonRuntime:
    store: SessionStore
    provider: ChatProvider | None = None
    registry: ToolRegistry = field(default_factory=build_default_registry)
    approval_policy: ApprovalPolicy = field(default_factory=ApprovalPolicy)
    runtime_mode: RuntimeMode = RuntimeMode.REAL
    model: str = DEFAULT_MODEL
    max_tool_iterations: int = DEFAULT_MAX_TOOL_ITERATIONS

    def run(self, request: str, *, cwd: Path | None = None, session_id: str | None = None) -> RuntimeResult:
        provider = self._provider_or_raise()
        runtime_cwd = (cwd or Path.cwd()).resolve()
        active_session_id = self._session_id(session_id, runtime_cwd)
        self.store.add_message(active_session_id, "user", request)

        tool_records: list[RuntimeToolCallRecord] = []
        tool_summaries: list[str] = []
        assistant_text = ""

        for iteration in range(self.max_tool_iterations + 1):
            try:
                response = provider.complete(
                    ProviderRequest(
                        model=self.model,
                        messages=self._prompt_messages(
                            request=request,
                            cwd=runtime_cwd,
                            session_id=active_session_id,
                            tool_summaries=tool_summaries,
                        ),
                        tools=[_provider_tool_payload(spec) for spec in self.registry.list()],
                    )
                )
            except ProviderError as exc:
                answer = redact_text(f"Provider failed: {exc}")
                self.store.add_message(active_session_id, "assistant", answer, {"status": "provider_error"})
                return RuntimeResult(answer=answer, session_id=active_session_id, tool_iterations=len(tool_records))

            assistant_text = redact_text(response.text)
            assistant_message_id = self.store.add_message(
                active_session_id,
                "assistant",
                assistant_text,
                {"provider": response.raw_metadata.get("provider", "unknown")},
            )

            if not response.tool_calls:
                return RuntimeResult(
                    answer=_answer_with_tool_rejections(assistant_text or "No provider response.", tool_records),
                    session_id=active_session_id,
                    tool_iterations=len(tool_records),
                    tool_calls=tool_records,
                )

            if iteration >= self.max_tool_iterations:
                answer = f"Stopped after {self.max_tool_iterations} tool iteration(s)."
                skipped_records = [
                    self._persist_max_iteration_tool_call(
                        active_session_id,
                        tool_call,
                        assistant_message_id=assistant_message_id,
                    )
                    for tool_call in response.tool_calls
                ]
                visible_records = tool_records + skipped_records
                self.store.add_message(
                    active_session_id,
                    "assistant",
                    _answer_with_tool_rejections(answer, visible_records),
                    {
                        "status": "max_tool_iterations",
                        "attempted_tool_calls": [
                            {"name": record.name, "status": record.status, "summary": record.summary}
                            for record in skipped_records
                        ],
                    },
                )
                return RuntimeResult(
                    answer=_answer_with_tool_rejections(answer, visible_records),
                    session_id=active_session_id,
                    tool_iterations=len(tool_records),
                    tool_calls=visible_records,
                )

            for tool_call in response.tool_calls:
                record = self._handle_tool_call(
                    tool_call,
                    active_session_id,
                    assistant_message_id=assistant_message_id,
                    cwd=runtime_cwd,
                )
                tool_records.append(record)
                tool_summaries.append(f"{record.name}: {record.status}: {record.summary}")

        answer = assistant_text or "No provider response."
        return RuntimeResult(
            answer=_answer_with_tool_rejections(answer, tool_records),
            session_id=active_session_id,
            tool_iterations=len(tool_records),
            tool_calls=tool_records,
        )

    def _provider_or_raise(self) -> ChatProvider:
        if self.runtime_mode == RuntimeMode.NO_PROVIDER or self.provider is None:
            raise RuntimeError(NO_CHAT_PROVIDER_MESSAGE)
        return self.provider

    def _session_id(self, session_id: str | None, cwd: Path) -> str:
        if session_id is None:
            return self.store.create_session(cwd=str(cwd), repo_root=_repo_root(cwd), title="Hipson runtime session")
        if self.store.get_session(session_id) is None:
            raise RuntimeError(f"Session does not exist: {session_id}")
        return session_id

    def _prompt_messages(self, *, request: str, cwd: Path, session_id: str, tool_summaries: list[str]) -> list[dict[str, str]]:
        session_summary = "\n".join(tool_summaries) if tool_summaries else ""
        return assemble_prompt_messages(
            PromptContext(
                current_request=request,
                session_summary=session_summary,
                tool_specs=self.registry.list(),
                repo_facts={"cwd": str(cwd), "repo_root": _repo_root(cwd), "session_id": session_id},
            )
        )

    def _handle_tool_call(
        self,
        tool_call: ProviderToolCall,
        session_id: str,
        *,
        assistant_message_id: str,
        cwd: Path,
    ) -> RuntimeToolCallRecord:
        try:
            spec = self.registry.get(tool_call.name)
        except ToolRegistryError as exc:
            return self._persist_rejected_tool_call(
                session_id,
                tool_call,
                assistant_message_id=assistant_message_id,
                risk_level="dangerous",
                approval_status="rejected",
                error=str(exc),
            )
        try:
            self.registry.validate_input(tool_call.name, tool_call.input)
        except ToolRegistryError as exc:
            return self._persist_rejected_tool_call(
                session_id,
                tool_call,
                assistant_message_id=assistant_message_id,
                risk_level=spec.risk_level,
                approval_status="invalid_input",
                error=str(exc),
            )

        repo_root = _repo_root(cwd)
        context = ToolContext(cwd=cwd, repo_root=Path(repo_root) if repo_root is not None else None, session_id=session_id)
        decision = self.approval_policy.evaluate_tool(
            spec,
            tool_call.input,
            context,
            fake_provider=self.runtime_mode == RuntimeMode.FAKE,
        )
        if not decision.allowed:
            return self._persist_rejected_tool_call(
                session_id,
                tool_call,
                assistant_message_id=assistant_message_id,
                risk_level=spec.risk_level,
                approval_status="blocked" if decision.blocked else "requires_approval",
                error=decision.reason,
            )

        try:
            result = self.registry.run(tool_call.name, tool_call.input, context)
        except ToolRegistryError as exc:
            return self._persist_rejected_tool_call(
                session_id,
                tool_call,
                assistant_message_id=assistant_message_id,
                risk_level=spec.risk_level,
                approval_status="approved",
                error=str(exc),
            )

        return self._persist_tool_result(
            session_id,
            tool_call,
            result,
            assistant_message_id=assistant_message_id,
            risk_level=spec.risk_level,
        )

    def _persist_tool_result(
        self,
        session_id: str,
        tool_call: ProviderToolCall,
        result: ToolResult,
        *,
        assistant_message_id: str,
        risk_level: str,
    ) -> RuntimeToolCallRecord:
        status = "completed" if result.ok else "failed"
        tool_name = _safe_tool_name(tool_call.name)
        summary = redact_text(_tool_result_summary(result))
        self.store.add_tool_call(
            session_id,
            message_id=assistant_message_id,
            tool_name=tool_name,
            input_data=tool_call.input,
            output_data=bounded_tool_output(result),
            risk_level=risk_level,
            approval_status="approved",
            status=status,
            error=result.error,
        )
        return RuntimeToolCallRecord(name=tool_name, status=status, summary=summary)

    def _persist_rejected_tool_call(
        self,
        session_id: str,
        tool_call: ProviderToolCall,
        *,
        assistant_message_id: str,
        risk_level: str,
        approval_status: str,
        error: str,
    ) -> RuntimeToolCallRecord:
        tool_name = _safe_tool_name(tool_call.name)
        summary = redact_text(error)
        self.store.add_tool_call(
            session_id,
            message_id=assistant_message_id,
            tool_name=tool_name,
            input_data=tool_call.input,
            output_data={},
            risk_level=risk_level,
            approval_status=approval_status,
            status="rejected",
            error=summary,
        )
        return RuntimeToolCallRecord(name=tool_name, status="rejected", summary=summary)

    def _persist_max_iteration_tool_call(
        self,
        session_id: str,
        tool_call: ProviderToolCall,
        *,
        assistant_message_id: str,
    ) -> RuntimeToolCallRecord:
        try:
            risk_level = self.registry.get(tool_call.name).risk_level
        except ToolRegistryError:
            risk_level = "dangerous"
        return self._persist_rejected_tool_call(
            session_id,
            tool_call,
            assistant_message_id=assistant_message_id,
            risk_level=risk_level,
            approval_status="max_tool_iterations",
            error=f"Tool call skipped because max tool iterations ({self.max_tool_iterations}) was reached.",
        )


def default_session_db() -> Path:
    hipson_home, _warnings = detect_hipson_home()
    return hipson_home / "runtime.sqlite"


def run_chat_once(
    request: str,
    *,
    cwd: Path | None = None,
    session_db: str | Path | None = None,
    session_id: str | None = None,
    provider: ChatProvider | None = None,
    runtime_mode: RuntimeMode = RuntimeMode.REAL,
) -> RuntimeResult:
    if provider is None and runtime_mode != RuntimeMode.FAKE:
        raise RuntimeError(NO_CHAT_PROVIDER_MESSAGE)
    store = open_session_store(session_db or default_session_db())
    try:
        runtime_provider = provider
        if runtime_provider is None and runtime_mode == RuntimeMode.FAKE:
            runtime_provider = FakeProvider()
        runtime = HipsonRuntime(store=store, provider=runtime_provider, runtime_mode=runtime_mode)
        return runtime.run(request, cwd=cwd, session_id=session_id)
    finally:
        store.close()


def _provider_tool_payload(spec: ToolSpec) -> dict[str, object]:
    return {
        "name": spec.name,
        "description": spec.description,
        "input_schema": spec.input_schema,
        "risk_level": spec.risk_level,
        "approval_required": spec.approval_required,
    }


def _repo_root(cwd: Path) -> str | None:
    root = git_root(cwd)
    return str(root) if root is not None else None


def _answer_with_tool_rejections(answer: str, tool_records: list[RuntimeToolCallRecord]) -> str:
    rejection_summary = _rejection_summary(tool_records)
    if not rejection_summary:
        return answer
    return f"{answer}\n\n{rejection_summary}"


def _rejection_summary(tool_records: list[RuntimeToolCallRecord]) -> str:
    rejected = [record for record in tool_records if record.status in {"rejected", "failed"}]
    if not rejected:
        return ""
    visible = rejected[:MAX_REJECTION_NOTICES]
    lines = ["Tool call rejection(s):"]
    for record in visible:
        lines.append(
            f"- {_bounded(redact_text(record.name), 80)} ({record.status}): "
            f"{_bounded(redact_text(record.summary), MAX_REJECTION_SUMMARY_CHARS)}"
        )
    remaining = len(rejected) - len(visible)
    if remaining > 0:
        lines.append(f"- ... {remaining} more rejected tool call(s)")
    return "\n".join(lines)


def _safe_tool_name(tool_name: str) -> str:
    return _bounded(redact_text(tool_name), 120)


def _tool_result_summary(result: ToolResult) -> str:
    if result.ok:
        return result.summary
    if result.error and result.summary:
        return f"{result.summary}: {result.error}"
    return result.error or result.summary


def _bounded(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[: max(0, limit - 3)]}..."
