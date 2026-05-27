import contextlib
import io
from pathlib import Path

from hipson import cli
from hipson.providers import FakeProvider, OpenAICompatibleProvider, ProviderResponse, ProviderToolCall
from hipson.runtime import HipsonRuntime, RuntimeMode
from hipson.session import open_session_store
from hipson.tools import PathPolicy, ToolContext, ToolRegistry, ToolResult, ToolSpec, build_default_registry


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
        approvals = store.list_approval_records(session_id=result.session_id)
    finally:
        store.close()

    assert result.answer == "no changes"
    assert result.tool_iterations == 1
    assert len(provider.calls) == 2
    assert [message["role"] for message in provider.calls[0].messages] == ["system", "user"]
    assert "<untrusted_data name=\"user_request\">" in provider.calls[0].messages[1]["content"]
    assert "<untrusted_data name=\"user_request\">" not in provider.calls[0].messages[0]["content"]
    tool_payloads = {tool["name"]: tool for tool in provider.calls[0].tools}
    assert tool_payloads["repo.changed_files"] == {
        "name": "repo.changed_files",
        "description": "List changed and untracked files for a local repository.",
        "input_schema": {"required": {"path": "str"}, "optional": {}},
        "risk_level": "read",
        "approval_required": False,
    }
    assert "handler" not in str(provider.calls[0].tools)
    assert len(tool_calls) == 1
    assert tool_calls[0]["tool_name"] == "repo.changed_files"
    assert tool_calls[0]["status"] == "completed"
    assert tool_calls[0]["risk_level"] == "read"
    assert tool_calls[0]["output"] == {"changed_files": [], "untracked_files": []}
    assert approvals[0]["tool_call_id"] == tool_calls[0]["id"]
    assert approvals[0]["source"] == "runtime"
    assert approvals[0]["decision"] == "approved"


def test_runtime_executes_openai_compatible_provider_tool_call_without_network(tmp_path: Path):
    responses = [
        (
            b'{"choices":[{"message":{"content":"checking","tool_calls":[{"id":"call-1",'
            b'"function":{"name":"repo.changed_files","arguments":"{\\"path\\":\\".\\"}"}}]},'
            b'"finish_reason":"tool_calls"}]}'
        ),
        b'{"choices":[{"message":{"content":"no changes"},"finish_reason":"stop"}]}',
    ]
    captured_bodies: list[str] = []

    def transport(_url: str, _headers: dict[str, str], body: bytes, _timeout: float) -> bytes:
        captured_bodies.append(body.decode("utf-8"))
        return responses.pop(0)

    store = open_session_store(tmp_path / "runtime.sqlite")
    provider = OpenAICompatibleProvider(
        base_url="https://provider.example/v1",
        api_key="test-provider-token",
        transport=transport,
    )
    runtime = HipsonRuntime(
        store=store,
        provider=provider,
        registry=build_default_registry(),
        runtime_mode=RuntimeMode.REAL,
        model="test-model",
    )

    try:
        result = runtime.run("scan changed files", cwd=tmp_path)
        tool_calls = store.list_tool_calls(result.session_id)
        approvals = store.list_approval_records(session_id=result.session_id)
    finally:
        store.close()

    assert result.answer == "no changes"
    assert result.tool_iterations == 1
    assert len(captured_bodies) == 2
    assert '"model": "test-model"' in captured_bodies[0]
    assert '"repo.changed_files"' in captured_bodies[0]
    assert "repo.changed_files: completed" in captured_bodies[1]
    assert tool_calls[0]["tool_name"] == "repo.changed_files"
    assert tool_calls[0]["status"] == "completed"
    assert approvals[0]["decision"] == "approved"


def test_runtime_rejects_unknown_tool_and_persists_rejection(tmp_path: Path):
    store = open_session_store(tmp_path / "runtime.sqlite")
    provider = FakeProvider.with_tool_call(name="missing.tool", input_data={})
    runtime = HipsonRuntime(store=store, provider=provider)

    try:
        result = runtime.run("call missing tool", cwd=tmp_path)
        tool_calls = store.list_tool_calls(result.session_id)
        approvals = store.list_approval_records(session_id=result.session_id)
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
    assert approvals[0]["decision"] == "rejected"


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
    assert tool_calls[0]["approval_status"] == "invalid_input"
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


def test_runtime_rejection_summary_caps_multiple_rejected_calls_and_redacts(tmp_path: Path):
    secret = "sk-test-secret1234567890"
    store = open_session_store(tmp_path / "runtime.sqlite")
    provider = FakeProvider(
        responses=[
            ProviderResponse(
                text="bad batch",
                tool_calls=[
                    ProviderToolCall(id=f"call-{index}", name=f"missing.{index}.{secret}", input={})
                    for index in range(5)
                ],
                raw_metadata={"provider": "fake"},
            ),
            ProviderResponse(text="done", raw_metadata={"provider": "fake"}),
        ]
    )
    runtime = HipsonRuntime(store=store, provider=provider)

    try:
        result = runtime.run("call several unsafe tools", cwd=tmp_path)
        tool_calls = store.list_tool_calls(result.session_id)
    finally:
        store.close()

    assert result.tool_iterations == 5
    assert len(tool_calls) == 5
    assert "Tool call rejection(s)" in result.answer
    assert "missing.0" in result.answer
    assert "missing.1" in result.answer
    assert "missing.2" in result.answer
    assert "missing.3" not in result.answer
    assert "... 2 more rejected tool call(s)" in result.answer
    assert secret not in result.answer
    assert "[REDACTED]" in result.answer


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


def test_runtime_path_policy_block_prevents_handler_execution(tmp_path: Path):
    called = False

    def handler(_input_data: dict[str, object], _context: ToolContext) -> ToolResult:
        nonlocal called
        called = True
        return ToolResult(ok=True, output={"ok": True}, summary="read ran")

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="demo.path_read",
            description="Reads a workspace path.",
            input_schema={"required": {"path": "str"}, "optional": {}},
            output_contract={"ok": "bool"},
            risk_level="read",
            approval_required=False,
            handler=handler,
            path_policies=(PathPolicy("path", "read_workspace"),),
        )
    )
    store = open_session_store(tmp_path / "runtime.sqlite")
    provider = FakeProvider.with_tool_call(name="demo.path_read", input_data={"path": "../outside"})
    runtime = HipsonRuntime(store=store, provider=provider, registry=registry)

    try:
        result = runtime.run("try path escape", cwd=tmp_path)
        tool_calls = store.list_tool_calls(result.session_id)
    finally:
        store.close()

    assert called is False
    assert tool_calls[0]["status"] == "rejected"
    assert tool_calls[0]["approval_status"] == "blocked"
    assert "Path traversal is not allowed" in result.answer


def test_runtime_handler_failure_is_persisted_and_visible(tmp_path: Path):
    def handler(_input_data: dict[str, object], _context: ToolContext) -> ToolResult:
        raise SystemExit("OPENROUTER_API_KEY=sk-test-secret1234567890")

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="demo.raises",
            description="Raises SystemExit.",
            input_schema={"required": {}, "optional": {}},
            output_contract={"value": "str"},
            risk_level="read",
            approval_required=False,
            handler=handler,
        )
    )
    store = open_session_store(tmp_path / "runtime.sqlite")
    provider = FakeProvider(
        responses=[
            ProviderResponse(
                text="try tool",
                tool_calls=[ProviderToolCall(id="call-1", name="demo.raises", input={})],
                raw_metadata={"provider": "fake"},
            ),
            ProviderResponse(text="done", raw_metadata={"provider": "fake"}),
        ]
    )
    runtime = HipsonRuntime(store=store, provider=provider, registry=registry)

    try:
        result = runtime.run("raise safely", cwd=tmp_path)
        tool_calls = store.list_tool_calls(result.session_id)
    finally:
        store.close()

    assert tool_calls[0]["status"] == "failed"
    assert tool_calls[0]["approval_status"] == "approved"
    assert "sk-test-secret1234567890" not in str(tool_calls[0]["error"])
    assert "demo.raises" in result.answer
    assert "handler failed" in result.answer
    assert "sk-test-secret1234567890" not in result.answer


def test_runtime_output_contract_failure_is_persisted_as_failed(tmp_path: Path):
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="demo.bad_output",
            description="Returns wrong output shape.",
            input_schema={"required": {}, "optional": {}},
            output_contract={"value": "str"},
            risk_level="read",
            approval_required=False,
            handler=lambda _input_data, _context: ToolResult(ok=True, output={"value": 123}, summary="bad"),
        )
    )
    store = open_session_store(tmp_path / "runtime.sqlite")
    provider = FakeProvider(
        responses=[
            ProviderResponse(
                text="try bad output",
                tool_calls=[ProviderToolCall(id="call-1", name="demo.bad_output", input={})],
                raw_metadata={"provider": "fake"},
            ),
            ProviderResponse(text="done", raw_metadata={"provider": "fake"}),
        ]
    )
    runtime = HipsonRuntime(store=store, provider=provider, registry=registry)

    try:
        result = runtime.run("validate output", cwd=tmp_path)
        tool_calls = store.list_tool_calls(result.session_id)
    finally:
        store.close()

    assert tool_calls[0]["status"] == "failed"
    assert "output validation failed" in result.answer
    assert "must be str" in str(tool_calls[0]["error"])


def test_runtime_persists_bounded_redacted_tool_output(tmp_path: Path):
    secret = "sk-test-secret1234567890"
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="demo.large_output",
            description="Returns a large output string.",
            input_schema={"required": {}, "optional": {}},
            output_contract={"markdown": "str"},
            risk_level="read",
            approval_required=False,
            handler=lambda _input_data, _context: ToolResult(
                ok=True,
                output={"markdown": f"{secret}\n" + ("x" * 8_000)},
                summary="large output",
            ),
        )
    )
    store = open_session_store(tmp_path / "runtime.sqlite")
    provider = FakeProvider(
        responses=[
            ProviderResponse(
                text="large",
                tool_calls=[ProviderToolCall(id="call-1", name="demo.large_output", input={})],
                raw_metadata={"provider": "fake"},
            ),
            ProviderResponse(text="done", raw_metadata={"provider": "fake"}),
        ]
    )
    runtime = HipsonRuntime(store=store, provider=provider, registry=registry)

    try:
        result = runtime.run("persist bounded", cwd=tmp_path)
        tool_calls = store.list_tool_calls(result.session_id)
    finally:
        store.close()

    persisted = str(tool_calls[0]["output"])
    assert result.answer == "done"
    assert secret not in persisted
    assert len(persisted) < 1_400
    assert "truncated" in persisted


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

    assert "Stopped after 3 tool iteration(s)." in result.answer
    assert "Tool call rejection(s)" in result.answer
    assert "repo.changed_files" in result.answer
    assert "max tool iterations" in result.answer
    assert result.tool_iterations == 3
    assert len(tool_calls) == 4
    assert tool_calls[-1]["status"] == "rejected"
    assert tool_calls[-1]["approval_status"] == "max_tool_iterations"
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


def test_chat_cli_real_provider_requires_explicit_config_without_network(tmp_path: Path):
    session_db = tmp_path / "runtime.sqlite"
    stdout = io.StringIO()
    stderr = io.StringIO()

    with contextlib.chdir(tmp_path), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        exit_code = cli.main(
            [
                "chat",
                "--provider",
                "openai-compatible",
                "--api-key-env",
                "HIPSON_TEST_MISSING_PROVIDER_KEY",
                "-q",
                "hello from cli",
                "--session-db",
                str(session_db),
            ]
        )

    assert exit_code == 1
    assert stdout.getvalue() == ""
    assert "Chat provider is not configured" in stderr.getvalue()
    assert "HIPSON_TEST_MISSING_PROVIDER_KEY" in stderr.getvalue()
    assert not session_db.exists()


def test_chat_cli_fake_and_real_provider_modes_are_mutually_exclusive(tmp_path: Path):
    stdout = io.StringIO()
    stderr = io.StringIO()

    with contextlib.chdir(tmp_path), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        exit_code = cli.main(["chat", "--fake", "--provider", "openai-compatible", "-q", "hello"])

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert "--fake cannot be combined with --provider" in stderr.getvalue()


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


def test_chat_cli_explicit_fake_tool_call_uses_runtime_pipeline(tmp_path: Path):
    session_db = tmp_path / "runtime.sqlite"
    stdout = io.StringIO()

    with contextlib.chdir(tmp_path), contextlib.redirect_stdout(stdout):
        exit_code = cli.main(
            [
                "chat",
                "--fake",
                "-q",
                "check changed files",
                "--session-db",
                str(session_db),
                "--fake-tool-call",
                "repo.changed_files",
                "--fake-tool-input",
                '{"path":"."}',
            ]
        )

    store = open_session_store(session_db)
    try:
        sessions = store.list_sessions()
        tool_calls = store.list_tool_calls(str(sessions[0]["id"]))
    finally:
        store.close()

    assert exit_code == 0
    assert stdout.getvalue().startswith("Fake/offline mode: Fake provider response")
    assert "Tool calls:" in stdout.getvalue()
    assert "repo.changed_files: completed" in stdout.getvalue()
    assert len(tool_calls) == 1
    assert tool_calls[0]["tool_name"] == "repo.changed_files"
    assert tool_calls[0]["status"] == "completed"


def test_chat_cli_fake_tool_call_rejects_unsafe_tool_before_runtime(tmp_path: Path):
    session_db = tmp_path / "runtime.sqlite"
    stdout = io.StringIO()
    stderr = io.StringIO()

    with contextlib.chdir(tmp_path), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        exit_code = cli.main(
            [
                "chat",
                "--fake",
                "-q",
                "write packet",
                "--session-db",
                str(session_db),
                "--fake-tool-call",
                "packet.review.create",
                "--fake-tool-input",
                '{"project":".","title":"x"}',
            ]
        )

    assert exit_code == 1
    assert stdout.getvalue() == ""
    assert "Fake tool-call demo is limited to read-risk" in stderr.getvalue()
    assert not session_db.exists()


def test_chat_cli_fake_tool_input_requires_fake_tool_call(tmp_path: Path):
    session_db = tmp_path / "runtime.sqlite"
    stdout = io.StringIO()
    stderr = io.StringIO()

    with contextlib.chdir(tmp_path), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        exit_code = cli.main(
            [
                "chat",
                "--fake",
                "-q",
                "bad demo",
                "--session-db",
                str(session_db),
                "--fake-tool-input",
                '{"path":"."}',
            ]
        )

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert "--fake-tool-input requires --fake-tool-call" in stderr.getvalue()
    assert not session_db.exists()


def test_runtime_default_registry_has_no_shell_auto_execution_tool():
    tool_names = {spec.name for spec in build_default_registry().list()}

    assert "shell.run" not in tool_names
