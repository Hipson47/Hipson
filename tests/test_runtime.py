import contextlib
import io
from pathlib import Path

from hipson import cli
from hipson.providers import FakeProvider, ProviderResponse, ProviderToolCall
from hipson.runtime import HipsonRuntime, RuntimeMode
from hipson.session import open_session_store
from hipson.tools import ToolContext, ToolRegistry, ToolResult, ToolSpec, build_default_registry


def test_runtime_no_tool_answer_persists_transcript(tmp_path: Path):
    store = open_session_store(tmp_path / "runtime.sqlite")
    runtime = HipsonRuntime(store=store, provider=FakeProvider.with_text("done"))

    try:
        result = runtime.run("hello runtime", cwd=tmp_path)
        messages = store.list_messages(result.session_id)
    finally:
        store.close()

    assert result.answer == "done"
    assert result.tool_iterations == 0
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "hello runtime"
    assert messages[1]["content"] == "done"


def test_runtime_executes_one_read_tool_call_and_persists_result(tmp_path: Path):
    store = open_session_store(tmp_path / "runtime.sqlite")
    provider = FakeProvider(
        responses=[
            ProviderResponse(
                text="checking files",
                tool_calls=[ProviderToolCall(id="call-1", name="repo.changed_files", input={"path": "."})],
                raw_metadata={"provider": "fake"},
            ),
            ProviderResponse(text="no changes", raw_metadata={"provider": "fake"}),
        ]
    )
    runtime = HipsonRuntime(store=store, provider=provider, registry=build_default_registry())

    try:
        result = runtime.run("scan changed files", cwd=tmp_path)
        tool_calls = store.list_tool_calls(result.session_id)
    finally:
        store.close()

    assert result.answer == "no changes"
    assert result.tool_iterations == 1
    assert len(provider.calls) == 2
    assert len(tool_calls) == 1
    assert tool_calls[0]["tool_name"] == "repo.changed_files"
    assert tool_calls[0]["status"] == "completed"
    assert tool_calls[0]["risk_level"] == "read"
    assert tool_calls[0]["output"] == {"changed_files": [], "untracked_files": []}


def test_runtime_rejects_unknown_tool_and_persists_rejection(tmp_path: Path):
    store = open_session_store(tmp_path / "runtime.sqlite")
    provider = FakeProvider.with_tool_call(name="missing.tool", input_data={})
    runtime = HipsonRuntime(store=store, provider=provider)

    try:
        result = runtime.run("call missing tool", cwd=tmp_path)
        tool_calls = store.list_tool_calls(result.session_id)
    finally:
        store.close()

    assert result.tool_iterations == 1
    assert tool_calls[0]["tool_name"] == "missing.tool"
    assert tool_calls[0]["status"] == "rejected"
    assert tool_calls[0]["approval_status"] == "rejected"
    assert "Unknown tool" in str(tool_calls[0]["error"])
    assert "Tool call rejection(s)" in result.answer
    assert "missing.tool" in result.answer
    assert "Unknown tool" in result.answer


def test_runtime_rejects_invalid_tool_input_and_persists_rejection(tmp_path: Path):
    store = open_session_store(tmp_path / "runtime.sqlite")
    provider = FakeProvider.with_tool_call(name="repo.scan", input_data={})
    runtime = HipsonRuntime(store=store, provider=provider)

    try:
        result = runtime.run("scan badly", cwd=tmp_path)
        tool_calls = store.list_tool_calls(result.session_id)
    finally:
        store.close()

    assert result.tool_iterations == 1
    assert tool_calls[0]["tool_name"] == "repo.scan"
    assert tool_calls[0]["status"] == "rejected"
    assert tool_calls[0]["approval_status"] == "approved"
    assert "missing required input" in str(tool_calls[0]["error"])
    assert "Tool call rejection(s)" in result.answer
    assert "repo.scan" in result.answer
    assert "missing required input" in result.answer


def test_runtime_rejection_answer_is_redacted_and_bounded(tmp_path: Path):
    secret_name = "missing.sk-test-secret1234567890"
    store = open_session_store(tmp_path / "runtime.sqlite")
    provider = FakeProvider.with_tool_call(name=secret_name, input_data={})
    runtime = HipsonRuntime(store=store, provider=provider)

    try:
        result = runtime.run("call secret-looking tool", cwd=tmp_path)
        tool_calls = store.list_tool_calls(result.session_id)
    finally:
        store.close()

    assert secret_name not in result.answer
    assert "[REDACTED]" in result.answer
    assert secret_name not in str(tool_calls[0]["tool_name"])
    assert secret_name not in str(tool_calls[0]["error"])


def test_runtime_non_fake_mode_blocks_external_risk_tool(tmp_path: Path):
    called = False

    def handler(_input_data: dict[str, object], _context: ToolContext) -> ToolResult:
        nonlocal called
        called = True
        return ToolResult(ok=True, output={"ok": True}, summary="external ran")

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="external.demo",
            description="External test tool",
            input_schema={"required": {}, "optional": {}},
            output_contract={"required": {"ok": "bool"}},
            risk_level="external",
            approval_required=True,
            handler=handler,
        )
    )
    store = open_session_store(tmp_path / "runtime.sqlite")
    provider = FakeProvider.with_tool_call(name="external.demo", input_data={})
    runtime = HipsonRuntime(store=store, provider=provider, registry=registry)

    try:
        result = runtime.run("try external tool", cwd=tmp_path)
        tool_calls = store.list_tool_calls(result.session_id)
    finally:
        store.close()

    assert called is False
    assert tool_calls[0]["status"] == "rejected"
    assert tool_calls[0]["approval_status"] == "requires_approval"
    assert "External actions require explicit approval" in result.answer


def test_runtime_fake_mode_allows_documented_external_dry_run_path(tmp_path: Path):
    called = False

    def handler(_input_data: dict[str, object], _context: ToolContext) -> ToolResult:
        nonlocal called
        called = True
        return ToolResult(ok=True, output={"ok": True}, summary="external fake path ran")

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="external.demo",
            description="External test tool",
            input_schema={"required": {}, "optional": {}},
            output_contract={"required": {"ok": "bool"}},
            risk_level="external",
            approval_required=True,
            handler=handler,
        )
    )
    store = open_session_store(tmp_path / "runtime.sqlite")
    provider = FakeProvider(
        responses=[
            ProviderResponse(
                text="try external fake",
                tool_calls=[ProviderToolCall(id="call-1", name="external.demo", input={})],
                raw_metadata={"provider": "fake"},
            ),
            ProviderResponse(text="fake complete", raw_metadata={"provider": "fake"}),
        ]
    )
    runtime = HipsonRuntime(store=store, provider=provider, registry=registry, runtime_mode=RuntimeMode.FAKE)

    try:
        result = runtime.run("try external fake tool", cwd=tmp_path)
        tool_calls = store.list_tool_calls(result.session_id)
    finally:
        store.close()

    assert called is True
    assert result.answer == "fake complete"
    assert tool_calls[0]["status"] == "completed"
    assert tool_calls[0]["approval_status"] == "approved"


def test_runtime_provider_failure_is_redacted_and_persisted(tmp_path: Path):
    store = open_session_store(tmp_path / "runtime.sqlite")
    provider = FakeProvider.with_failure(
        "Provider exploded with OPENROUTER_API_KEY=sk-test-secret1234567890",
        detail="body token=abc123secret password=hunter2",
    )
    runtime = HipsonRuntime(store=store, provider=provider)

    try:
        result = runtime.run("hello", cwd=tmp_path)
        messages = store.list_messages(result.session_id)
    finally:
        store.close()

    rendered_messages = "\n".join(str(message["content"]) for message in messages)
    assert "sk-test-secret1234567890" not in result.answer
    assert "hunter2" not in result.answer
    assert "abc123secret" not in rendered_messages
    assert "[REDACTED]" in result.answer
    assert messages[-1]["metadata"]["status"] == "provider_error"


def test_runtime_stops_after_max_tool_iterations(tmp_path: Path):
    store = open_session_store(tmp_path / "runtime.sqlite")
    provider = FakeProvider(
        responses=[
            ProviderResponse(
                text="again",
                tool_calls=[ProviderToolCall(id=f"call-{index}", name="repo.changed_files", input={"path": "."})],
                raw_metadata={"provider": "fake"},
            )
            for index in range(4)
        ]
    )
    runtime = HipsonRuntime(store=store, provider=provider, max_tool_iterations=3)

    try:
        result = runtime.run("keep using tools", cwd=tmp_path)
        tool_calls = store.list_tool_calls(result.session_id)
    finally:
        store.close()

    assert result.answer == "Stopped after 3 tool iteration(s)."
    assert result.tool_iterations == 3
    assert len(tool_calls) == 3
    assert len(provider.calls) == 4


def test_chat_cli_query_fails_closed_without_fake_provider(tmp_path: Path):
    session_db = tmp_path / "runtime.sqlite"
    stdout = io.StringIO()
    stderr = io.StringIO()

    with contextlib.chdir(tmp_path), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        exit_code = cli.main(
            [
                "chat",
                "-q",
                "hello from cli",
                "--session-db",
                str(session_db),
            ]
        )

    assert exit_code == 1
    assert stdout.getvalue() == ""
    assert "No chat provider is configured" in stderr.getvalue()
    assert not session_db.exists()


def test_chat_cli_fake_response_requires_fake_flag(tmp_path: Path):
    session_db = tmp_path / "runtime.sqlite"
    stdout = io.StringIO()
    stderr = io.StringIO()

    with contextlib.chdir(tmp_path), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        exit_code = cli.main(
            [
                "chat",
                "-q",
                "hello from cli",
                "--session-db",
                str(session_db),
                "--fake-response",
                "cli ok",
            ]
        )

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert "--fake-response requires --fake" in stderr.getvalue()
    assert not session_db.exists()


def test_chat_cli_query_uses_explicit_fake_provider_and_temp_db(tmp_path: Path):
    session_db = tmp_path / "runtime.sqlite"
    stdout = io.StringIO()

    with contextlib.chdir(tmp_path), contextlib.redirect_stdout(stdout):
        exit_code = cli.main(
            [
                "chat",
                "--fake",
                "-q",
                "hello from cli",
                "--session-db",
                str(session_db),
                "--fake-response",
                "cli ok",
            ]
        )

    store = open_session_store(session_db)
    try:
        sessions = store.list_sessions()
        messages = store.list_messages(str(sessions[0]["id"]))
    finally:
        store.close()

    assert exit_code == 0
    assert stdout.getvalue().strip() == "Fake/offline mode: cli ok"
    assert len(sessions) == 1
    assert [message["role"] for message in messages] == ["user", "assistant"]
