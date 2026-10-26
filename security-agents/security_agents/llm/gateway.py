"""LLM gateway adapter.

INTEGRATION NOTE: ADR-0007 places the single LLM gateway in `backend/` (owner:
Srihith, CORE-010). It does not exist yet, so this module is the *consumer-side
port*: `LLMGateway` is the narrow interface the seven-agent runtime needs, and
`ProviderGateway` is a reference implementation over `LLMProvider`. When the
backend gateway lands, it should satisfy `LLMGateway.complete()` and this file
shrinks to an adapter — no second gateway is intended.
"""

from __future__ import annotations

import json
import os
from typing import Protocol

from ..contracts import AgentName
from .providers import AnthropicProvider, LLMProvider
from .types import LLMRequest, LLMResponse, LLMUsage, ProviderMode

DEFAULT_MODEL = "claude-haiku-5-5"

# USD per million tokens (input, output). Unknown models are priced at the most
# expensive known rate so budgets fail safe.
PRICING_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-1": (10.0, 50.0),
}
_FALLBACK_PRICE = (10.0, 50.0)


def parse_overrides(raw: str | None) -> dict[AgentName, str]:
    """`AGENT_MODEL_OVERRIDES` as JSON (`{"patch": "..."}`) or `scout=...,patch=...`."""
    if not raw:
        return {}
    raw = raw.strip()
    pairs: dict[str, str]
    if raw.startswith("{"):
        pairs = {str(k): str(v) for k, v in json.loads(raw).items()}
    else:
        pairs = {}
        for item in raw.split(","):
            if "=" in item:
                k, v = item.split("=", 1)
                pairs[k.strip()] = v.strip()
    return {AgentName(k): v for k, v in pairs.items() if k in AgentName._value2member_map_}


class LLMGateway(Protocol):
    mode: ProviderMode

    def model_for(self, agent: AgentName) -> str: ...

    def complete(self, request: LLMRequest) -> LLMResponse: ...

    def cost_usd(self, model: str, usage: LLMUsage) -> float: ...


class ProviderGateway:
    def __init__(
        self,
        provider: LLMProvider,
        *,
        default_model: str | None = None,
        overrides: dict[AgentName, str] | None = None,
    ) -> None:
        self.provider = provider
        self.mode: ProviderMode = provider.mode
        self.default_model = default_model or os.environ.get("ANTHROPIC_MODEL") or DEFAULT_MODEL
        self.overrides = (
            overrides if overrides is not None else parse_overrides(os.environ.get("AGENT_MODEL_OVERRIDES"))
        )

    def model_for(self, agent: AgentName) -> str:
        return self.overrides.get(agent, self.default_model)

    def complete(self, request: LLMRequest) -> LLMResponse:
        return self.provider.complete(request)

    def cost_usd(self, model: str, usage: LLMUsage) -> float:
        try:
            from backend.llm import cost_usd as backend_cost
        except ImportError:
            backend_cost = None
        if backend_cost is not None:
            return backend_cost(
                model,
                {
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cache_read_input_tokens": usage.cache_read_input_tokens,
                    "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0),
                },
            )
        base = model.split(":", 1)[-1]
        pin, pout = PRICING_PER_MTOK.get(base, _FALLBACK_PRICE)
        cache_read = getattr(usage, "cache_read_input_tokens", 0) * pin * 0.1
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) * pin * 1.25
        return (usage.input_tokens * pin + usage.output_tokens * pout + cache_read + cache_write) / 1_000_000


def live_gateway_from_env() -> ProviderGateway:
    """Live gateway. Requires credentials resolvable by the Anthropic SDK."""
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise RuntimeError("live mode requires ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN in the environment")
    return ProviderGateway(AnthropicProvider())
