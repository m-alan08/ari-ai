"""NumPy training for the specialist models (offline only; inference never imports this).

Mini-batch Adam on softmax cross-entropy (one label) or sigmoid binary cross-entropy (several
labels), optional one ReLU hidden layer, L2 weight decay, inverted dropout on the hidden layer,
and early stopping on the validation loss. Everything is seeded, so a run is reproducible.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .config import TrainConfig
from .features import featurize


def matrix(
    rows: list[dict[str, str]], fields: list[str], dim: int, *, char_ngrams: bool = True
) -> np.ndarray:
    x = np.zeros((len(rows), dim), dtype=np.float32)
    for n, row in enumerate(rows):
        for i, v in featurize(
            ((f, row.get(f, "")) for f in fields), dim, char_ngrams=char_ngrams
        ).items():
            x[n, i] = v
    return x


def targets(labels: list[list[str]] | list[str], vocab: list[str], multilabel: bool) -> np.ndarray:
    index = {label: i for i, label in enumerate(vocab)}
    y = np.zeros((len(labels), len(vocab)), dtype=np.float32)
    for n, lab in enumerate(labels):
        for item in lab if multilabel else [lab]:  # type: ignore[list-item]
            y[n, index[item]] = 1.0
    return y


class Net:
    def __init__(self, dim: int, hidden: int, n_out: int, rng: np.random.Generator) -> None:
        self.hidden = hidden
        if hidden:
            self.params = [
                (rng.standard_normal((dim, hidden)) * np.sqrt(2.0 / 64)).astype(np.float32),
                np.zeros(hidden, np.float32),
                (rng.standard_normal((hidden, n_out)) * np.sqrt(1.0 / hidden)).astype(np.float32),
                np.zeros(n_out, np.float32),
            ]
        else:
            self.params = [np.zeros((dim, n_out), np.float32), np.zeros(n_out, np.float32)]

    def forward(self, x: np.ndarray, *, drop: float = 0.0, rng: np.random.Generator | None = None):
        if not self.hidden:
            w, b = self.params
            return x @ w + b, None
        w1, b1, w2, b2 = self.params
        h = np.maximum(0.0, x @ w1 + b1)
        mask = None
        if drop and rng is not None:
            mask = (rng.random(h.shape) >= drop).astype(np.float32) / (1.0 - drop)
            h = h * mask
        return h @ w2 + b2, (h, mask)

    def flat(self) -> list[float]:
        return np.concatenate([p.ravel() for p in self.params]).astype(np.float32).tolist()


def _probs(logits: np.ndarray, multilabel: bool) -> np.ndarray:
    if multilabel:
        return 1.0 / (1.0 + np.exp(-np.clip(logits, -60, 60)))
    z = logits - logits.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def _loss(p: np.ndarray, y: np.ndarray, multilabel: bool) -> float:
    eps = 1e-7
    if multilabel:
        return float(-(y * np.log(p + eps) + (1 - y) * np.log(1 - p + eps)).mean())
    return float(-(y * np.log(p + eps)).sum(axis=1).mean())


def train(
    x: np.ndarray,
    y: np.ndarray,
    xv: np.ndarray,
    yv: np.ndarray,
    *,
    multilabel: bool,
    cfg: TrainConfig,
) -> tuple[Net, dict[str, Any]]:
    rng = np.random.default_rng(cfg.seed)
    net = Net(x.shape[1], cfg.hidden, y.shape[1], rng)
    m = [np.zeros_like(p) for p in net.params]
    v = [np.zeros_like(p) for p in net.params]
    b1, b2, eps = 0.9, 0.999, 1e-8
    step = 0
    best = (float("inf"), [p.copy() for p in net.params], 0)
    history: list[dict[str, float]] = []
    for epoch in range(cfg.epochs):
        order = rng.permutation(len(x))
        for start in range(0, len(x), cfg.batch_size):
            idx = order[start : start + cfg.batch_size]
            xb, yb = x[idx], y[idx]
            logits, cache = net.forward(xb, drop=cfg.dropout, rng=rng)
            g = (_probs(logits, multilabel) - yb) / len(idx)
            if net.hidden:
                w1, _, w2, _ = net.params
                h, mask = cache
                gw2 = h.T @ g
                gb2 = g.sum(0)
                gh = g @ w2.T
                if mask is not None:
                    gh = gh * mask
                gh = gh * (h > 0)
                grads = [xb.T @ gh, gh.sum(0), gw2, gb2]
            else:
                grads = [xb.T @ g, g.sum(0)]
            step += 1
            for i, (p, gr) in enumerate(zip(net.params, grads, strict=True)):
                if p.ndim == 2:
                    gr = gr + cfg.weight_decay * p
                m[i] = b1 * m[i] + (1 - b1) * gr
                v[i] = b2 * v[i] + (1 - b2) * gr * gr
                mh = m[i] / (1 - b1**step)
                vh = v[i] / (1 - b2**step)
                p -= (cfg.lr * mh / (np.sqrt(vh) + eps)).astype(np.float32)
        train_loss = _loss(_probs(net.forward(x)[0], multilabel), y, multilabel)
        val_loss = _loss(_probs(net.forward(xv)[0], multilabel), yv, multilabel)
        history.append(
            {"epoch": epoch + 1, "train_loss": round(train_loss, 5), "val_loss": round(val_loss, 5)}
        )
        if val_loss < best[0] - 1e-5:
            best = (val_loss, [p.copy() for p in net.params], epoch + 1)
        elif epoch + 1 - best[2] >= cfg.patience:
            break
    net.params = best[1]
    return net, {
        "best_epoch": best[2],
        "best_val_loss": round(best[0], 5),
        "epochs_run": len(history),
        "history": history,
    }


def predict_proba(net: Net, x: np.ndarray, multilabel: bool) -> np.ndarray:
    return _probs(net.forward(x)[0], multilabel)
