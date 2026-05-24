"""Deterministic provider implementation for tests and offline runtime wiring."""

from __future__ import annotations

from dataclasses import dataclass, field

from hipson.providers.base import ProviderError, ProviderRequest, ProviderResponse, ProviderToolCall


@dataclass
class FakeProvider:
    responses: list[ProviderResponse] = field(default_factory=list)
    failure: ProviderError | None = None
    calls: list[ProviderRequest] = field(default_factory=list)
    _index: int = 0

    @classmethod
    def with_text(cls, text: str) -> FakeProvider:
        return cls(responses=[ProviderResponse(text=text, raw_metadata={"provider": "fake"})])

    @classmethod
    def with_tool_call(
        cls,
        *,
        name: str,
        input_data: dict[str, object],
        text: str = "",
        call_id: str = "fake-call-1",
    ) -> FakeProvider:
        return cls(
            responses=[
                ProviderResponse(
                    text=text,
                    tool_calls=[ProviderToolCall(id=call_id, name=name, input=input_data)],
                    raw_metadata={"provider": "fake"},
                )
            ]
        )

    @classmethod
    def with_failure(cls, message: str, detail: str = "") -> FakeProvider:
        return cls(failure=ProviderError(message=message, redacted_detail=detail))

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        if self.failure is not None:
            raise self.failure
        if self._index < len(self.responses):
            response = self.responses[self._index]
            self._index += 1
            return response
        return ProviderResponse(text="Fake provider response", raw_metadata={"provider": "fake"})
