import io
import urllib.error

from hipson.providers import (
    FakeProvider,
    OpenAICompatibleProvider,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
    validate_base_url,
)


def test_fake_provider_returns_deterministic_text_without_network():
    provider = FakeProvider.with_text("ready")
    request = ProviderRequest(model="fake-model", messages=[{"role": "user", "content": "hello"}], tools=[])

    response = provider.complete(request)

    assert response == ProviderResponse(text="ready", raw_metadata={"provider": "fake"})
    assert provider.calls == [request]


def test_fake_provider_can_emit_valid_tool_call():
    provider = FakeProvider.with_tool_call(name="repo.scan", input_data={"path": "."}, text="I will scan.")
    request = ProviderRequest(model="fake-model", messages=[], tools=[{"name": "repo.scan"}])

    response = provider.complete(request)

    assert response.text == "I will scan."
    assert response.tool_calls == [ProviderToolCall(id="fake-call-1", name="repo.scan", input={"path": "."})]
    assert response.raw_metadata == {"provider": "fake"}


def test_fake_provider_configured_failure_is_redacted():
    provider = FakeProvider.with_failure(
        "Provider failed with OPENROUTER_API_KEY=sk-test-secret1234567890",
        detail="body password=hunter2 token=abc123secret",
    )
    request = ProviderRequest(model="fake-model", messages=[], tools=[])

    try:
        provider.complete(request)
    except ProviderError as exc:
        rendered = str(exc)
    else:
        raise AssertionError("Expected fake provider failure")

    assert "sk-test-secret1234567890" not in rendered
    assert "hunter2" not in rendered
    assert "abc123secret" not in rendered
    assert "[REDACTED]" in rendered
    assert provider.calls == [request]


def test_fake_provider_default_response_is_deterministic():
    provider = FakeProvider()
    request = ProviderRequest(model="fake-model", messages=[], tools=[])

    first = provider.complete(request)
    second = provider.complete(request)

    assert first == ProviderResponse(text="Fake provider response", raw_metadata={"provider": "fake"})
    assert second == first


def test_openai_compatible_provider_uses_stub_transport_without_network():
    captured: dict[str, object] = {}

    def transport(url: str, headers: dict[str, str], body: bytes, timeout: float) -> bytes:
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = body.decode("utf-8")
        captured["timeout"] = timeout
        return (
            b'{"choices":[{"message":{"content":"ready","tool_calls":[{"id":"call-1",'
            b'"function":{"name":"repo.changed_files","arguments":"{\\"path\\":\\".\\"}"}}]},'
            b'"finish_reason":"tool_calls"}]}'
        )

    provider = OpenAICompatibleProvider(
        base_url="https://provider.example/v1",
        api_key="test-provider-token",
        timeout=12,
        transport=transport,
    )
    request = ProviderRequest(
        model="test-model",
        messages=[{"role": "user", "content": "hello"}],
        tools=[
            {
                "name": "repo.changed_files",
                "description": "List changed files.",
                "input_schema": {"required": {"path": "str"}, "optional": {}},
            }
        ],
    )

    response = provider.complete(request)

    assert captured["url"] == "https://provider.example/v1/chat/completions"
    assert "Bearer test-provider-token" == captured["headers"]["Authorization"]
    assert '"tools"' in str(captured["body"])
    assert captured["timeout"] == 12
    assert response.text == "ready"
    assert response.tool_calls == [ProviderToolCall(id="call-1", name="repo.changed_files", input={"path": "."})]
    assert response.raw_metadata == {"provider": "openai-compatible", "finish_reason": "tool_calls"}


def test_openai_compatible_provider_url_policy_is_fail_closed():
    assert validate_base_url("https://provider.example/v1") == "https://provider.example/v1"
    assert validate_base_url("http://localhost:8080/v1", allow_local_http=True) == "http://localhost:8080/v1"

    for url in ["http://provider.example/v1", "ftp://provider.example/v1", "provider.example/v1", "https:///missing"]:
        try:
            validate_base_url(url)
        except ProviderError as exc:
            rendered = str(exc)
        else:
            raise AssertionError(f"Expected unsafe provider URL to fail: {url}")
        lowered = rendered.casefold()
        assert "provider" in lowered or "scheme" in lowered or "host" in lowered

    try:
        validate_base_url("http://localhost:8080/v1")
    except ProviderError as exc:
        assert "explicit opt-in" in str(exc)
    else:
        raise AssertionError("Expected local HTTP to require opt-in")


def test_openai_compatible_provider_redacts_and_bounds_http_errors():
    secret = "sk-test-secret1234567890"
    body = (f'{{"error":"OPENROUTER_API_KEY={secret} password=hunter2"}}' + ("x" * 5_000)).encode("utf-8")

    def transport(_url: str, _headers: dict[str, str], _body: bytes, _timeout: float) -> bytes:
        raise urllib.error.HTTPError(
            url="https://provider.example/v1/chat/completions",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=io.BytesIO(body),
        )

    provider = OpenAICompatibleProvider(
        base_url="https://provider.example/v1",
        api_key="test-provider-token",
        transport=transport,
    )

    try:
        provider.complete(ProviderRequest(model="test-model", messages=[], tools=[]))
    except ProviderError as exc:
        rendered = str(exc)
    else:
        raise AssertionError("Expected provider HTTP error")

    assert "Provider HTTP 401" in rendered
    assert secret not in rendered
    assert "hunter2" not in rendered
    assert "[REDACTED]" in rendered
    assert len(rendered) < 800


def test_openai_compatible_provider_rejects_malformed_tool_arguments():
    def transport(_url: str, _headers: dict[str, str], _body: bytes, _timeout: float) -> bytes:
        return (
            b'{"choices":[{"message":{"content":"","tool_calls":[{"id":"bad",'
            b'"function":{"name":"repo.scan","arguments":"[]"}}]}}]}'
        )

    provider = OpenAICompatibleProvider(
        base_url="https://provider.example/v1",
        api_key="test-provider-token",
        transport=transport,
    )

    try:
        provider.complete(ProviderRequest(model="test-model", messages=[], tools=[]))
    except ProviderError as exc:
        assert "not an object" in str(exc)
    else:
        raise AssertionError("Expected malformed provider tool call to fail")
