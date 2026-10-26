"""Checkpoint format and pure-Python inference for the specialist models.

A checkpoint is a directory holding ``manifest.json`` and ``weights.bin``. The manifest names
the model (id, agent, version), its task, labels, feature configuration, architecture, the
dataset it was trained on (generator + seed + SHA-256), the training configuration, the
held-out metrics, and the SHA-256 of ``weights.bin``. Loading verifies that hash; a mismatch
refuses to load (the agent then runs without the specialist, which is the safe fallback).

Architecture: one optional ReLU hidden layer over a sparse hashed input, then a linear output
with softmax (one label) or sigmoid (several labels). Weights are little-endian float32.
"""

from __future__ import annotations

import hashlib
import json
import math
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .features import featurize

CHECKPOINT_ROOT = Path(__file__).parent / "checkpoints"


class CheckpointError(RuntimeError):
    """The checkpoint is missing, malformed or does not match its manifest hash."""


@dataclass(frozen=True)
class Prediction:
    label: str
    score: float


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SpecialistModel:
    """A loaded, hash-verified checkpoint."""

    def __init__(self, manifest: dict[str, Any], weights: array) -> None:
        self.manifest = manifest
        arch = manifest["architecture"]
        self.dim: int = int(arch["input_dim"])
        self.hidden: int = int(arch.get("hidden", 0))
        self.labels: list[str] = list(manifest["labels"])
        self.multilabel: bool = manifest["task"]["type"] == "multilabel"
        self.fields: list[str] = list(manifest["features"]["fields"])
        self.char_ngrams: bool = bool(manifest["features"].get("char_ngrams", True))
        n_out = len(self.labels)
        h = self.hidden
        sizes = [self.dim * h, h, h * n_out, n_out] if h else [self.dim * n_out, n_out]
        if len(weights) != sum(sizes):
            raise CheckpointError(
                f"{self.model_id}: weights have {len(weights)} values, expected {sum(sizes)}"
            )
        offsets = [0]
        for s in sizes:
            offsets.append(offsets[-1] + s)
        self._w = [weights[offsets[i] : offsets[i + 1]] for i in range(len(sizes))]

    @property
    def model_id(self) -> str:
        return f"{self.manifest['model_id']}@{self.manifest['version']}"

    @classmethod
    def load(cls, directory: Path) -> SpecialistModel:
        manifest_path, weights_path = directory / "manifest.json", directory / "weights.bin"
        if not manifest_path.is_file() or not weights_path.is_file():
            raise CheckpointError(f"no checkpoint at {directory}")
        manifest = json.loads(manifest_path.read_text())
        digest = _sha256(weights_path)
        if digest != manifest.get("weights_sha256"):
            raise CheckpointError(
                f"{directory.name}: weights.bin SHA-256 {digest[:12]}… does not match the manifest"
            )
        weights = array("f")
        weights.frombytes(weights_path.read_bytes())
        return cls(manifest, weights)

    def vector(self, inputs: dict[str, str]) -> dict[int, float]:
        return featurize(
            ((f, inputs.get(f, "")) for f in self.fields), self.dim, char_ngrams=self.char_ngrams
        )

    def scores(self, inputs: dict[str, str]) -> list[float]:
        x = self.vector(inputs)
        n_out = len(self.labels)
        if self.hidden:
            w1, b1, w2, b2 = self._w
            h = self.hidden
            act = list(b1)
            for i, v in x.items():
                row = i * h
                for j in range(h):
                    act[j] += v * w1[row + j]
            act = [a if a > 0 else 0.0 for a in act]
            logits = list(b2)
            for j, a in enumerate(act):
                if a:
                    row = j * n_out
                    for k in range(n_out):
                        logits[k] += a * w2[row + k]
        else:
            w, b = self._w
            logits = list(b)
            for i, v in x.items():
                row = i * n_out
                for k in range(n_out):
                    logits[k] += v * w[row + k]
        if self.multilabel:
            return [1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, z)))) for z in logits]
        top = max(logits)
        exps = [math.exp(z - top) for z in logits]
        total = sum(exps)
        return [e / total for e in exps]

    def predict(self, inputs: dict[str, str], *, top_k: int = 5) -> list[Prediction]:
        ranked = sorted(zip(self.labels, self.scores(inputs), strict=True), key=lambda p: -p[1])
        return [Prediction(label, round(score, 4)) for label, score in ranked[:top_k]]


def write_checkpoint(
    directory: Path, manifest: dict[str, Any], weights: list[float] | array
) -> dict[str, Any]:
    """Write weights.bin and a manifest carrying its SHA-256. Returns the manifest written."""
    directory.mkdir(parents=True, exist_ok=True)
    blob = array("f", weights).tobytes()
    (directory / "weights.bin").write_bytes(blob)
    manifest = {
        **manifest,
        "weights_sha256": hashlib.sha256(blob).hexdigest(),
        "weights_bytes": len(blob),
    }
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    return manifest
