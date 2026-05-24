from pathlib import Path

from hipson.redaction import REDACTION
from hipson.session import open_session_store


def test_session_store_bounds_and_redacts_direct_tool_call_payloads(tmp_path: Path):
    secret = "sk-test-secret1234567890"
    store = open_session_store(tmp_path / "runtime.sqlite")
    try:
        session_id = store.create_session(cwd=str(tmp_path), title=f"session {secret}")
        message_id = store.add_message(
            session_id,
            "assistant",
            f"provider body OPENROUTER_API_KEY={secret}",
            {"detail": f"Bearer abc123secret4567890 {secret}"},
        )
        store.add_tool_call(
            session_id,
            message_id=message_id,
            tool_name="demo.large",
            input_data={"prompt": f"token={secret}", "blob": "i" * 8_000},
            output_data={
                "markdown": f"OPENROUTER_API_KEY={secret}\n" + ("o" * 8_000),
                "items": [{"value": index} for index in range(100)],
            },
            error=f"provider failed with password=hunter2 {secret}",
        )

        sessions = store.list_sessions()
        messages = store.list_messages(session_id)
        tool_calls = store.list_tool_calls(session_id)
    finally:
        store.close()

    rendered = f"{sessions} {messages} {tool_calls}"
    assert secret not in rendered
    assert "abc123secret4567890" not in rendered
    assert "hunter2" not in rendered
    assert REDACTION in rendered
    assert "truncated" in str(tool_calls[0]["input"])
    assert "truncated" in str(tool_calls[0]["output"])
    assert len(str(tool_calls[0]["input"])) < 1_500
    assert len(str(tool_calls[0]["output"])) < 2_500
