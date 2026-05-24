from pathlib import Path

from hipson.gateway import CliGateway, GatewayRequest
from hipson.providers import FakeProvider, ProviderResponse, ProviderToolCall
from hipson.runtime import HipsonRuntime
from hipson.session import open_session_store


def test_cli_gateway_passes_message_to_runtime(tmp_path: Path):
    store = open_session_store(tmp_path / "runtime.sqlite")
    runtime = HipsonRuntime(store=store, provider=FakeProvider.with_text("gateway ok"))
    gateway = CliGateway(runtime)

    try:
        response = gateway.send(GatewayRequest(message="hello gateway", cwd=tmp_path))
        messages = store.list_messages(response.session_id)
    finally:
        store.close()

    assert response.answer == "gateway ok"
    assert response.tool_iterations == 0
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "hello gateway"


def test_cli_gateway_cannot_bypass_runtime_approval_policy(tmp_path: Path):
    store = open_session_store(tmp_path / "runtime.sqlite")
    provider = FakeProvider(
        responses=[
            ProviderResponse(
                text="trying write",
                tool_calls=[
                    ProviderToolCall(
                        id="call-1",
                        name="packet.review.create",
                        input={
                            "project": ".",
                            "title": "Review",
                            "output": "src/review.md",
                        },
                    )
                ],
                raw_metadata={"provider": "fake"},
            )
        ]
    )
    runtime = HipsonRuntime(store=store, provider=provider)
    gateway = CliGateway(runtime)

    try:
        response = gateway.send(GatewayRequest(message="write through gateway", cwd=tmp_path))
        tool_calls = store.list_tool_calls(response.session_id)
    finally:
        store.close()

    assert response.answer.startswith("Fake provider response")
    assert "Tool call rejection(s)" in response.answer
    assert "packet.review.create" in response.answer
    assert "Write path must be under runs/" in response.answer
    assert tool_calls[0]["tool_name"] == "packet.review.create"
    assert tool_calls[0]["status"] == "rejected"
    assert tool_calls[0]["approval_status"] == "requires_approval"
    assert not (tmp_path / "src" / "review.md").exists()
