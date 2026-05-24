"""Provider protocol for Hipson's future primary runtime loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from hipson.redaction import redact_text


@dataclass(frozen=True)
class ProviderToolCall:
    id: str
    name: str
    input: dict[str, object]


@dataclass(frozen=True)
class ProviderRequest:
    model: str
    messages: list[dict[str, str]]
    tools: list[dict[str, object]]
    temperature: float = 0.2
    max_tokens: int = 1200


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    tool_calls: list[ProviderToolCall] = field(default_factory=list)
    raw_metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderError(Exception):
    message: str
    redacted_detail: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "message", redact_text(self.message))
        object.__setattr__(self, "redacted_detail", redact_text(self.redacted_detail))

    def __str__(self) -> str:
        if self.redacted_detail:
            return f"{self.message}: {self.redacted_detail}"
        return self.message


class ChatProvider(Protocol):
    def complete(self, request: ProviderRequest) -> ProviderResponse:
        """Return one assistant response for a runtime request."""
