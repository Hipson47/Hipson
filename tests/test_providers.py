from hipson.providers import FakeProvider, ProviderError, ProviderRequest, ProviderResponse, ProviderToolCall


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
