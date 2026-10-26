"""Runtime advice from the specialist models, for the security-agents orchestrator.

``Specialists.advise(agent, inputs)`` returns an ``Advice``: ranked labels with scores and the
provenance of the answer. Rules the orchestrator relies on:

* A checkpoint that is missing, fails its SHA-256 check or raises at inference yields
  ``source="unavailable"`` with the reason; the assessment continues without it (fallback).
* A model whose held-out evaluation did not beat its deterministic baseline is never used: the
  baseline table recorded in its manifest answers instead (``source="baseline:<name>"``).
* Advice is data, never authority. Nothing here can approve, verify, widen scope or change a
  verdict; the orchestrator only puts it in prompts, ranks with it, and audits it.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .model import CHECKPOINT_ROOT, CheckpointError, SpecialistModel

AGENTS = ("scout", "probe", "trace", "diagnose", "patch", "fortify", "verify")


@dataclass
class Advice:
    agent: str
    model_id: str | None
    source: str  # "model" | "baseline:<name>" | "unavailable"
    predictions: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: float = 0.0
    reason: str | None = None

    @property
    def top(self) -> str | None:
        return self.predictions[0]["label"] if self.predictions else None

    def score(self, label: str) -> float | None:
        return next((p["score"] for p in self.predictions if p["label"] == label), None)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


class Specialists:
    """Lazily loads the seven checkpoints; safe to construct when none exist."""

    def __init__(self, root: Path = CHECKPOINT_ROOT, *, enabled: bool = True) -> None:
        self.root = root
        self.enabled = enabled
        self._models: dict[str, SpecialistModel | CheckpointError] = {}

    def _load(self, agent: str) -> SpecialistModel | CheckpointError:
        if agent not in self._models:
            try:
                self._models[agent] = SpecialistModel.load(self.root / agent)
            except (CheckpointError, OSError, ValueError, KeyError) as exc:
                self._models[agent] = CheckpointError(str(exc))
        return self._models[agent]

    def manifest(self, agent: str) -> dict[str, Any] | None:
        m = self._load(agent)
        return None if isinstance(m, CheckpointError) else m.manifest

    def advise(self, agent: str, inputs: dict[str, str], *, top_k: int | None = None) -> Advice:
        if not self.enabled:
            return Advice(agent, None, "unavailable", reason="specialists disabled")
        m = self._load(agent)
        if isinstance(m, CheckpointError):
            return Advice(agent, None, "unavailable", reason=str(m)[:200])
        k = top_k or int(m.manifest["task"].get("top_k") or 3)
        promoted = bool(m.manifest.get("evaluation", {}).get("promoted"))
        started = time.perf_counter()
        if not promoted:
            fallback = m.manifest.get("fallback") or {}
            table: dict[str, Any] = fallback.get("table") or {}
            field_name = fallback.get("key", "cwe")
            key = (
                inputs.get(field_name, "").split()[0]
                if field_name and inputs.get(field_name)
                else ""
            )
            answer = table.get(key) or fallback.get("default")
            if not answer:
                return Advice(
                    agent,
                    m.model_id,
                    "unavailable",
                    reason="model not promoted and no baseline answer",
                )
            labels = answer if isinstance(answer, list) else [answer]
            return Advice(
                agent,
                m.model_id,
                f"baseline:{fallback.get('name', 'lookup')}",
                [{"label": x, "score": 1.0} for x in labels[:k]],
                round((time.perf_counter() - started) * 1000, 3),
                reason="model did not beat this baseline on held-out data",
            )
        try:
            preds = m.predict(inputs, top_k=k)
        except Exception as exc:  # noqa: BLE001 - inference must never fail an assessment
            return Advice(
                agent, m.model_id, "unavailable", reason=f"inference error: {type(exc).__name__}"
            )
        return Advice(
            agent,
            m.model_id,
            "model",
            [{"label": p.label, "score": p.score} for p in preds],
            round((time.perf_counter() - started) * 1000, 3),
        )

    def status(self) -> dict[str, dict[str, Any]]:
        """Per agent: loaded or not, version, promoted, held-out score (for health/UI)."""
        out: dict[str, dict[str, Any]] = {}
        for agent in AGENTS:
            m = self._load(agent)
            if isinstance(m, CheckpointError):
                out[agent] = {"loaded": False, "reason": str(m)[:200]}
            else:
                ev = m.manifest.get("evaluation", {})
                out[agent] = {
                    "loaded": True,
                    "model": m.model_id,
                    "promoted": ev.get("promoted"),
                    "metric": ev.get("primary_metric"),
                    "test": ev.get("test"),
                    "best_baseline": ev.get("best_baseline"),
                    "best_baseline_test": ev.get("best_baseline_test"),
                    "weights_sha256": m.manifest.get("weights_sha256"),
                }
        return out


_default: Specialists | None = None


def default() -> Specialists:
    global _default
    if _default is None:
        import os

        _default = Specialists(enabled=os.environ.get("SPECIALISTS_DISABLED", "") != "1")
    return _default
