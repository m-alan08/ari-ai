"""Provider-neutral LLM request/response types.

Content blocks use the Anthropic Messages wire shape (plain dicts) so that live
responses, including thinking blocks, can be appended back to the conversation
unchanged, and FakeLLM/replay produce exactly the same shapes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from ..contracts import AgentName

ProviderMode = Literal["live", "fake", "replay"]


@dataclass(frozen=True)
class LLMRequest:
    agent: AgentName
    model: str
    system: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    max_tokens: int
    effort: str = "medium"
    timeout_s: float = 300.0
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class LLMResponse:
    content: list[dict[str, Any]]
    stop_reason: str
    usage: LLMUsage
    model: str
    provider_mode: ProviderMode
    response_id: str | None = None
    stop_details: dict[str, Any] | None = None

    def tool_uses(self) -> list[dict[str, Any]]:
        return [b for b in self.content if b.get("type") == "tool_use"]

    def text(self) -> str:
        return "\n".join(b.get("text", "") for b in self.content if b.get("type") == "text")


class LLMUnavailable(Exception):
    """Upstream model failure after retries (maps to a structured ErrorCode)."""

    def __init__(self, message: str, *, code: object | None = None) -> None:
        super().__init__(message)
        from ..contracts import ErrorCode

        if code is None:
            self.code = ErrorCode.LLM_UNAVAILABLE
        elif isinstance(code, ErrorCode):
            self.code = code
        else:
            try:
                self.code = ErrorCode(str(code))
            except ValueError:
                self.code = ErrorCode.LLM_UNAVAILABLE
