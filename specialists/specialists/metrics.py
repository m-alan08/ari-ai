"""Evaluation metrics (pure Python)."""

from __future__ import annotations

from collections import Counter
from typing import Any

from .data.corpus import SAFE


def single_label(gold: list[str], pred: list[str], labels: list[str]) -> dict[str, Any]:
    n = len(gold)
    acc = sum(g == p for g, p in zip(gold, pred, strict=True)) / n if n else 0.0
    per: dict[str, dict[str, float]] = {}
    for lab in labels:
        tp = sum(g == lab and p == lab for g, p in zip(gold, pred, strict=True))
        fp = sum(g != lab and p == lab for g, p in zip(gold, pred, strict=True))
        fn = sum(g == lab and p != lab for g, p in zip(gold, pred, strict=True))
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        per[lab] = {
            "precision": round(prec, 4),
            "recall": round(rec, 4),
            "f1": round(f1, 4),
            "support": tp + fn,
        }
    present = [lab for lab in labels if per[lab]["support"]]
    macro = sum(per[lab]["f1"] for lab in present) / len(present) if present else 0.0
    return {"n": n, "accuracy": round(acc, 4), "macro_f1": round(macro, 4), "per_label": per}


def detection(gold: list[str], pred: list[str]) -> dict[str, Any]:
    """Binary vulnerable-vs-safe view of a CWE classifier (any CWE counts as 'flagged')."""
    tp = sum(g != SAFE and p != SAFE for g, p in zip(gold, pred, strict=True))
    fp = sum(g == SAFE and p != SAFE for g, p in zip(gold, pred, strict=True))
    fn = sum(g != SAFE and p == SAFE for g, p in zip(gold, pred, strict=True))
    tn = sum(g == SAFE and p == SAFE for g, p in zip(gold, pred, strict=True))
    exact = sum(g != SAFE and g == p for g, p in zip(gold, pred, strict=True))
    pos = tp + fn
    return {
        "precision": round(tp / (tp + fp), 4) if tp + fp else 0.0,
        "recall": round(tp / pos, 4) if pos else 0.0,
        "false_positive_rate": round(fp / (fp + tn), 4) if fp + tn else 0.0,
        "false_negative_rate": round(fn / pos, 4) if pos else 0.0,
        "cwe_exact_on_vulnerable": round(exact / pos, 4) if pos else 0.0,
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
    }


def ranked(gold: list[list[str]], pred: list[list[str]], k: int) -> dict[str, Any]:
    """Precision/recall/F1 of the top-k list against the gold label set (micro), plus hit@1."""
    tp = sum(len(set(p[:k]) & set(g)) for g, p in zip(gold, pred, strict=True))
    n_pred = sum(len(p[:k]) for p in pred)
    n_gold = sum(len(g) for g in gold)
    prec = tp / n_pred if n_pred else 0.0
    rec = tp / n_gold if n_gold else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    hit1 = (
        sum(bool(p) and p[0] in g for g, p in zip(gold, pred, strict=True)) / len(gold)
        if gold
        else 0.0
    )
    return {
        "n": len(gold),
        "k": k,
        "precision_at_k": round(prec, 4),
        "recall_at_k": round(rec, 4),
        "f1_at_k": round(f1, 4),
        "hit_at_1": round(hit1, 4),
    }


def label_counts(rows: list[dict[str, Any]], multilabel: bool) -> dict[str, int]:
    c: Counter = Counter()
    for r in rows:
        c.update(r["label"] if multilabel else [r["label"]])
    return dict(sorted(c.items()))
