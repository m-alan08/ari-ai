"""LLM providers: live Anthropic, deterministic FakeLLM, and record/replay.

Only `AnthropicProvider` produces *live* evidence. `FakeLLM` and
`ReplayProvider` exist for deterministic CI and are labelled `fake` / `replay`
on every AgentRun they produce; their output must never be presented as live.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..contracts import AgentName, new_id
from ..redact import redact
from .types import LLMRequest, LLMResponse, LLMUnavailable, LLMUsage, ProviderMode


class LLMProvider(Protocol):
    mode: ProviderMode

    def complete(self, request: LLMRequest) -> LLMResponse: ...


# --------------------------------------------------------------------------- live


def supports_server_fallback(model: str) -> bool:
    """Claude Haiku has no server-side refusal fallback; the parameter is not sent for it."""
    return not model.split(":", 1)[-1].startswith("claude-haiku-")


class AnthropicProvider:
    """Live Claude via the official `anthropic` SDK (manual tool-use loop is in runtime).

    - Model comes from the request (resolved by the gateway from env/overrides).
    - Current models run adaptive thinking by default and Opus/Sonnet 5.5 reject
      forced tool_choice, so we send `tool_choice=auto` and steer via the prompt.
    - Server-side refusal fallback is enabled by default
      (`ANTHROPIC_SERVER_FALLBACKS=default`; set to `off` to disable). It is never
      sent for Claude Haiku, which has no server-side fallback.
    - SDK retries 408/409/429/5xx/connection errors (`max_retries`); anything
      that still fails is raised as `LLMUnavailable`.
    """

    mode: ProviderMode = "live"

    def __init__(
        self,
        client: Any | None = None,
        *,
        max_retries: int = 3,
        server_fallbacks: str | None = None,
    ) -> None:
        import anthropic

        self._anthropic = anthropic
        self.client = client if client is not None else anthropic.Anthropic(max_retries=max_retries)
        fb = (
            server_fallbacks
            if server_fallbacks is not None
            else os.environ.get("ANTHROPIC_SERVER_FALLBACKS", "default")
        )
        self.server_fallbacks = None if fb in ("", "off", "none") else fb

    def complete(self, request: LLMRequest) -> LLMResponse:
        a = self._anthropic
        kwargs: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": request.messages,
            "tools": request.tools,
            "tool_choice": {"type": "auto"},
            "output_config": {"effort": request.effort},
            "timeout": request.timeout_s,
        }
        try:
            if self.server_fallbacks and supports_server_fallback(request.model):
                msg = self.client.beta.messages.create(
                    **kwargs,
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks=self.server_fallbacks,
                )
            else:
                msg = self.client.messages.create(**kwargs)
        except a.NotFoundError as e:  # bad model id
            raise LLMUnavailable(f"model not found: {request.model}: {e.message}") from e
        except a.RateLimitError as e:
            raise LLMUnavailable(f"rate limited after retries: {e.message}") from e
        except a.BadRequestError as e:
            raise LLMUnavailable(f"bad request (not retried): {e.message}") from e
        except a.APIStatusError as e:
            raise LLMUnavailable(f"api status {e.status_code}: {e.message}") from e
        except a.APIConnectionError as e:
            raise LLMUnavailable(f"connection error: {e}") from e

        content = [b.model_dump(mode="json", exclude_none=True) for b in msg.content]
        usage = LLMUsage(
            input_tokens=int(getattr(msg.usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(msg.usage, "output_tokens", 0) or 0),
            cache_read_input_tokens=int(getattr(msg.usage, "cache_read_input_tokens", 0) or 0),
            cache_creation_input_tokens=int(getattr(msg.usage, "cache_creation_input_tokens", 0) or 0),
        )
        stop_details = getattr(msg, "stop_details", None)
        return LLMResponse(
            content=content,
            stop_reason=str(msg.stop_reason),
            usage=usage,
            model=str(msg.model),
            provider_mode="live",
            response_id=str(msg.id),
            stop_details=stop_details.model_dump(mode="json") if stop_details is not None else None,
        )


# --------------------------------------------------------------------------- fake


@dataclass
class FakeTurn:
    """One scripted assistant turn: zero or more tool calls and optional text."""

    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    text: str = ""
    stop_reason: str | None = None


FakeStep = FakeTurn | Callable[[LLMRequest], FakeTurn]


def assistant_turns(request: LLMRequest) -> int:
    return sum(1 for m in request.messages if m["role"] == "assistant")


def last_tool_results(request: LLMRequest) -> list[dict[str, Any]]:
    """Tool results the runtime sent back in the latest user message (parsed JSON)."""
    if not request.messages or request.messages[-1]["role"] != "user":
        return []
    out: list[dict[str, Any]] = []
    content = request.messages[-1]["content"]
    if isinstance(content, str):
        return []
    for block in content:
        if block.get("type") != "tool_result":
            continue
        text = block.get("content")
        if isinstance(text, list):
            text = "".join(b.get("text", "") for b in text)
        parsed: dict[str, Any] = {"is_error": bool(block.get("is_error")), "raw": text}
        try:
            start = text.index("{")
            end = text.rindex("}") + 1
            parsed["json"] = json.loads(text[start:end])
        except (ValueError, AttributeError):
            pass
        out.append(parsed)
    return out


class FakeLLM:
    """Deterministic scripted provider for CI. Labelled `fake` on every run.

    `scripts[agent]` is a list of steps; the step index is the number of
    assistant turns already in the conversation, so the same script works for
    every run of that agent (e.g. one Probe run per hypothesis). A callable step
    receives the full request and can adapt to tool results, exercising the
    same runtime path a live model does.
    """

    mode: ProviderMode = "fake"

    def __init__(self, scripts: dict[AgentName, list[FakeStep]]) -> None:
        self.scripts = scripts
        self.calls: list[LLMRequest] = []
        self._lock = threading.Lock()

    def complete(self, request: LLMRequest) -> LLMResponse:
        with self._lock:
            self.calls.append(request)
        steps = self.scripts.get(request.agent)
        if not steps:
            raise LLMUnavailable(f"FakeLLM has no script for agent {request.agent}")
        idx = assistant_turns(request)
        if idx >= len(steps):
            turn = FakeTurn(text="(fake script exhausted)")
        else:
            step = steps[idx]
            turn = step(request) if callable(step) else step
        content: list[dict[str, Any]] = []
        if turn.text:
            content.append({"type": "text", "text": turn.text})
        for name, args in turn.tool_calls:
            content.append(
                {"type": "tool_use", "id": f"toolu_fake_{new_id('t')[2:]}", "name": name, "input": args}
            )
        stop = turn.stop_reason or ("tool_use" if turn.tool_calls else "end_turn")
        approx_in = len(json.dumps(request.messages, default=str)) // 4 + len(request.system) // 4
        approx_out = len(json.dumps(content)) // 4
        return LLMResponse(
            content=content,
            stop_reason=stop,
            usage=LLMUsage(input_tokens=approx_in, output_tokens=approx_out),
            model=f"fake:{request.model}",
            provider_mode="fake",
            response_id=new_id("fakemsg"),
        )


# --------------------------------------------------------------------------- record / replay


def _request_fingerprint(request: LLMRequest) -> str:
    tool_names = sorted(t["name"] for t in request.tools)
    return hashlib.sha256(
        json.dumps({"agent": request.agent.value, "tools": tool_names}, sort_keys=True).encode()
    ).hexdigest()[:16]


class RecordingProvider:
    """Wraps a live provider and appends redacted request/response pairs to JSONL."""

    def __init__(self, inner: LLMProvider, path: Path) -> None:
        self.inner = inner
        self.path = path
        self.mode = inner.mode
        path.parent.mkdir(parents=True, exist_ok=True)

    def complete(self, request: LLMRequest) -> LLMResponse:
        resp = self.inner.complete(request)
        record = {
            "agent": request.agent.value,
            "fingerprint": _request_fingerprint(request),
            "model": resp.model,
            "recorded_from_mode": resp.provider_mode,
            "request_messages": redact(request.messages),
            "response": {
                "content": redact(resp.content),
                "stop_reason": resp.stop_reason,
                "usage": vars(resp.usage),
                "response_id": resp.response_id,
            },
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
        return resp


class ReplayProvider:
    """Replays a recording in order per agent. Labelled `replay`, never `live`."""

    mode: ProviderMode = "replay"

    def __init__(self, path: Path) -> None:
        self._queues: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                self._queues[rec["agent"]].append(rec)

    def complete(self, request: LLMRequest) -> LLMResponse:
        queue = self._queues.get(request.agent.value)
        if not queue:
            raise LLMUnavailable(f"replay exhausted for agent {request.agent}")
        rec = queue.pop(0)
        if rec["fingerprint"] != _request_fingerprint(request):
            raise LLMUnavailable("replay fingerprint mismatch (tool set changed); re-record")
        r = rec["response"]
        return LLMResponse(
            content=r["content"],
            stop_reason=r["stop_reason"],
            usage=LLMUsage(**r["usage"]),
            model=f"replay:{rec['model']}",
            provider_mode="replay",
            response_id=r.get("response_id"),
        )
