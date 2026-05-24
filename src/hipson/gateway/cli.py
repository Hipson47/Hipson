"""CLI gateway adapter boundary over the Hipson runtime."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hipson.runtime import HipsonRuntime


@dataclass(frozen=True)
class GatewayRequest:
    message: str
    cwd: Path
    session_id: str | None = None


@dataclass(frozen=True)
class GatewayResponse:
    answer: str
    session_id: str
    tool_iterations: int


@dataclass
class CliGateway:
    runtime: HipsonRuntime

    def send(self, request: GatewayRequest) -> GatewayResponse:
        result = self.runtime.run(request.message, cwd=request.cwd, session_id=request.session_id)
        return GatewayResponse(
            answer=result.answer,
            session_id=result.session_id,
            tool_iterations=result.tool_iterations,
        )
