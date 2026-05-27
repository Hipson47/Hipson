"""OpenAI-compatible provider adapter for Hipson's primary runtime."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from hipson.providers.base import ProviderError, ProviderRequest, ProviderResponse, ProviderToolCall
from hipson.redaction import redact_text

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_API_KEY_ENV = "OPENROUTER_API_KEY"
DEFAULT_MODEL = "openai/gpt-4o-mini"
MAX_PROVIDER_ERROR_CHARS = 600
LOCAL_HTTP_HOSTS = {"localhost", "127.0.0.1", "::1"}

ProviderTransport = Callable[[str, dict[str, str], bytes, float], bytes]


@dataclass
class OpenAICompatibleProvider:
    """Dependency-free chat-completions adapter.

    Tests can pass a stub transport so unit coverage never requires network or
    credentials. The default transport uses stdlib urllib for explicit real
    provider use from the CLI.
    """

    base_url: str
    api_key: str
    timeout: float = 90.0
    allow_local_http: bool = False
    transport: ProviderTransport | None = None

    @classmethod
    def from_env(
        cls,
        *,
        base_url: str | None = None,
        api_key_env: str = DEFAULT_API_KEY_ENV,
        timeout: float = 90.0,
        allow_local_http: bool = False,
        transport: ProviderTransport | None = None,
    ) -> OpenAICompatibleProvider:
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise ProviderError(
                message="No chat provider API key is configured",
                redacted_detail=f"Set {api_key_env} or choose --fake for offline mode.",
            )
        return cls(
            base_url=validate_base_url(base_url or DEFAULT_BASE_URL, allow_local_http=allow_local_http),
            api_key=api_key,
            timeout=timeout,
            allow_local_http=allow_local_http,
            transport=transport,
        )

    def __post_init__(self) -> None:
        self.base_url = validate_base_url(self.base_url, allow_local_http=self.allow_local_http)

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        payload = _request_payload(request)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost/hipson",
            "X-Title": "Hipson Runtime",
        }
        url = f"{self.base_url}/chat/completions"
        try:
            response_body = (self.transport or _default_transport)(url, headers, body, self.timeout)
        except urllib.error.HTTPError as exc:
            detail = _read_http_error(exc)
            raise ProviderError(message=f"Provider HTTP {exc.code}", redacted_detail=detail) from None
        except urllib.error.URLError as exc:
            raise ProviderError(
                message="Provider request failed",
                redacted_detail=_bounded_redacted(str(exc.reason)),
            ) from None
        except OSError as exc:
            raise ProviderError(
                message="Provider transport failed",
                redacted_detail=_bounded_redacted(str(exc)),
            ) from None

        try:
            data = json.loads(response_body.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise ProviderError(
                message="Provider returned invalid JSON",
                redacted_detail=_bounded_redacted(f"{exc}: {response_body.decode('utf-8', errors='replace')}"),
            ) from None

        return _parse_chat_completion(data)


def validate_base_url(raw_url: str, *, allow_local_http: bool = False) -> str:
    raw = raw_url.strip().rstrip("/")
    parsed = urllib.parse.urlparse(raw)
    if not parsed.scheme:
        raise ProviderError("Malformed provider URL", "missing scheme")
    if parsed.scheme not in {"http", "https"}:
        raise ProviderError("Unsupported provider URL scheme", parsed.scheme)
    if not parsed.netloc:
        raise ProviderError("Malformed provider URL", "missing host")
    if parsed.scheme == "https":
        return raw
    if _is_local_http(parsed) and allow_local_http:
        return raw
    if _is_local_http(parsed):
        raise ProviderError("Local HTTP provider URLs require explicit opt-in", "pass --allow-local-provider-http")
    raise ProviderError("Provider base URL must use https:// for remote providers", raw)


def _default_transport(url: str, headers: dict[str, str], body: bytes, timeout: float) -> bytes:
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 - URL policy is validated above.
        return response.read()


def _request_payload(request: ProviderRequest) -> dict[str, object]:
    payload: dict[str, object] = {
        "model": request.model,
        "messages": request.messages,
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
    }
    if request.tools:
        payload["tools"] = [_openai_tool(tool) for tool in request.tools]
        payload["tool_choice"] = "auto"
    return payload


def _openai_tool(tool: dict[str, object]) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": str(tool.get("name", "")),
            "description": str(tool.get("description", "")),
            "parameters": _parameters_schema(tool.get("input_schema")),
        },
    }


def _parameters_schema(schema: object) -> dict[str, object]:
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}, "additionalProperties": False}
    required = schema.get("required", {})
    optional = schema.get("optional", {})
    required_fields = required if isinstance(required, dict) else {}
    optional_fields = optional if isinstance(optional, dict) else {}
    properties = {
        str(name): {"type": _json_schema_type(str(type_name))}
        for name, type_name in {**required_fields, **optional_fields}.items()
    }
    return {
        "type": "object",
        "properties": properties,
        "required": sorted(str(name) for name in required_fields),
        "additionalProperties": False,
    }


def _json_schema_type(type_name: str) -> str | list[str]:
    if "|" in type_name:
        types: set[str] = set()
        for part in (item.strip() for item in type_name.split("|") if item.strip()):
            mapped = _json_schema_type(part)
            if isinstance(mapped, list):
                types.update(mapped)
            else:
                types.add(mapped)
        return sorted(types)
    if type_name.startswith("list["):
        return "array"
    return {
        "str": "string",
        "bool": "boolean",
        "int": "integer",
        "float": "number",
        "dict": "object",
        "object": "object",
        "null": "null",
    }.get(type_name, "string")


def _parse_chat_completion(data: object) -> ProviderResponse:
    if not isinstance(data, dict):
        raise ProviderError("Provider response was not an object")
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderError("Provider response had no choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise ProviderError("Provider choice was not an object")
    message = first.get("message")
    if not isinstance(message, dict):
        raise ProviderError("Provider choice had no message")
    content = message.get("content")
    text = redact_text(content if isinstance(content, str) else "")
    tool_calls = _parse_tool_calls(message.get("tool_calls"))
    metadata: dict[str, object] = {"provider": "openai-compatible"}
    finish_reason = first.get("finish_reason")
    if isinstance(finish_reason, str):
        metadata["finish_reason"] = redact_text(finish_reason)
    return ProviderResponse(text=text, tool_calls=tool_calls, raw_metadata=metadata)


def _parse_tool_calls(value: object) -> list[ProviderToolCall]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ProviderError("Provider tool_calls was not a list")
    calls: list[ProviderToolCall] = []
    for index, raw_call in enumerate(value):
        if not isinstance(raw_call, dict):
            raise ProviderError("Provider tool call was not an object")
        function = raw_call.get("function")
        if not isinstance(function, dict):
            raise ProviderError("Provider tool call had no function")
        name = function.get("name")
        if not isinstance(name, str) or not name:
            raise ProviderError("Provider tool call had no function name")
        arguments = function.get("arguments", "{}")
        input_data = _parse_tool_arguments(arguments)
        raw_id = raw_call.get("id")
        call_id = raw_id if isinstance(raw_id, str) and raw_id else f"provider-call-{index + 1}"
        calls.append(ProviderToolCall(id=redact_text(call_id), name=redact_text(name), input=input_data))
    return calls


def _parse_tool_arguments(arguments: object) -> dict[str, object]:
    if isinstance(arguments, dict):
        return arguments
    if not isinstance(arguments, str):
        raise ProviderError("Provider tool arguments were not JSON")
    try:
        parsed = json.loads(arguments or "{}")
    except json.JSONDecodeError as exc:
        raise ProviderError("Provider tool arguments were invalid JSON", _bounded_redacted(str(exc))) from None
    if not isinstance(parsed, dict):
        raise ProviderError("Provider tool arguments were not an object")
    return parsed


def _read_http_error(exc: urllib.error.HTTPError) -> str:
    body = exc.read().decode("utf-8", errors="replace")
    return _bounded_redacted(body or str(exc.reason))


def _bounded_redacted(text: str, *, max_chars: int = MAX_PROVIDER_ERROR_CHARS) -> str:
    redacted = redact_text(text)
    if len(redacted) <= max_chars:
        return redacted
    marker = f"\n[provider text truncated to {max_chars} chars]"
    return redacted[: max(0, max_chars - len(marker))].rstrip() + marker


def _is_local_http(parsed: urllib.parse.ParseResult) -> bool:
    return (parsed.hostname or "").casefold() in LOCAL_HTTP_HOSTS
