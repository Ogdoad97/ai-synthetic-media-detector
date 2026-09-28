"""
Detection metrics, implemented in plain numpy (no scikit-learn dependency
at runtime; tests cross-check every function against scikit-learn).

Convention everywhere: label 1 = synthetic (positive), label 0 = real
(negative); higher score = more likely synthetic; a sample is flagged when
`score >= threshold`.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np


class SingleClassError(ValueError):
    """A ranking metric was requested but only one class is present."""


def _validate(scores, labels) -> Tuple[np.ndarray, np.ndarray]:
    s = np.asarray(scores, dtype=np.float64)
    y = np.asarray(labels, dtype=np.int64)
    if s.ndim != 1 or y.ndim != 1 or s.shape != y.shape:
        raise ValueError("scores and labels must be 1-D arrays of equal length")
    if s.size == 0:
        raise ValueError("no samples")
    if not np.all(np.isfinite(s)):
        raise ValueError("scores must be finite")
    if not np.all((y == 0) | (y == 1)):
        raise ValueError("labels must be 0 (real) or 1 (synthetic)")
    return s, y


def _require_both(y: np.ndarray) -> Tuple[int, int]:
    n_pos = int((y == 1).sum())
    n_neg = int((y == 0).sum())
    if n_pos == 0 or n_neg == 0:
        raise SingleClassError(
            f"needs both classes (got {n_pos} synthetic, {n_neg} real)"
        )
    return n_pos, n_neg


def _average_ranks(x: np.ndarray) -> np.ndarray:
    """1-based ranks, ties share their average rank."""
    uniq, inv, counts = np.unique(x, return_inverse=True, return_counts=True)
    cum = np.cumsum(counts)
    avg = (cum - counts + 1 + cum) / 2.0
    return avg[inv]


def roc_auc(scores, labels) -> float:
    """
    ROC-AUC via the rank-sum (Mann-Whitney) identity. Tied scores between a
    positive and a negative count half - the standard convention, and the
    one that matters here because the baseline detector clips to [0, 1] and
    produces many exact ties at 0.0 and 1.0.
    """
    s, y = _validate(scores, labels)
    n_pos, n_neg = _require_both(y)
    ranks = _average_ranks(s)
    return float((ranks[y == 1].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def _cumulative_counts(s: np.ndarray, y: np.ndarray):
    """(thresholds desc, tp, fp) evaluated at each distinct score, rule: score >= thr."""
    order = np.argsort(-s, kind="mergesort")
    s_sorted, y_sorted = s[order], y[order]
    distinct = np.where(np.diff(s_sorted))[0]
    idx = np.r_[distinct, y_sorted.size - 1]
    tp = np.cumsum(y_sorted)[idx]
    fp = (idx + 1) - tp
    return s_sorted[idx], tp.astype(np.float64), fp.astype(np.float64)


def roc_curve(scores, labels) -> Dict[str, List[float]]:
    s, y = _validate(scores, labels)
    n_pos, n_neg = _require_both(y)
    thr, tp, fp = _cumulative_counts(s, y)
    return {
        "fpr": [0.0] + (fp / n_neg).tolist(),
        "tpr": [0.0] + (tp / n_pos).tolist(),
        "thresholds": [math.inf] + thr.tolist(),
    }


def average_precision(scores, labels) -> float:
    """PR-AUC as average precision (step-wise, no interpolation; ties grouped)."""
    s, y = _validate(scores, labels)
    n_pos, _ = _require_both(y)
    _, tp, fp = _cumulative_counts(s, y)
    precision = tp / (tp + fp)
    recall = tp / n_pos
    prev = np.r_[0.0, recall[:-1]]
    return float(np.sum((recall - prev) * precision))


def rates_at_threshold(scores, labels, threshold: float) -> Dict[str, Optional[float]]:
    """TPR / FPR (and counts) when flagging `score >= threshold`."""
    s, y = _validate(scores, labels)
    flagged = s >= threshold
    n_pos = int((y == 1).sum())
    n_neg = int((y == 0).sum())
    tp = int((flagged & (y == 1)).sum())
    fp = int((flagged & (y == 0)).sum())
    return {
        "threshold": float(threshold),
        "tp": tp, "fn": n_pos - tp, "fp": fp, "tn": n_neg - fp,
        "tpr": (tp / n_pos) if n_pos else None,
        "fpr": (fp / n_neg) if n_neg else None,
    }


def threshold_at_fpr(negative_scores, target_fpr: float) -> float:
    """
    Smallest threshold whose false-positive rate on `negative_scores`
    (real samples) is <= target_fpr, under the rule `score >= threshold`.
    Ties are respected: the threshold never splits a group of equal scores,
    so the guarantee holds exactly on the data it was chosen from.
    """
    if not 0.0 <= target_fpr <= 1.0:
        raise ValueError("target_fpr must be in [0, 1]")
    neg = np.sort(np.asarray(negative_scores, dtype=np.float64))
    if neg.size == 0:
        raise ValueError("need at least one real sample to choose a threshold")
    if not np.all(np.isfinite(neg)):
        raise ValueError("scores must be finite")
    allowed = int(math.floor(target_fpr * neg.size + 1e-12))
    candidates = np.r_[np.unique(neg), np.nextafter(neg[-1], np.inf)]
    counts = neg.size - np.searchsorted(neg, candidates, side="left")
    return float(candidates[int(np.argmax(counts <= allowed))])


def wilson_interval(successes: int, n: int, z: float = 1.96) -> Tuple[Optional[float], Optional[float]]:
    """95% Wilson score interval for a proportion (well-behaved at 0 and n)."""
    if n <= 0:
        return None, None
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def bootstrap_auc_ci(scores, labels, n_boot: int = 1000, seed: int = 0,
                     alpha: float = 0.05) -> Tuple[Optional[float], Optional[float]]:
    """Stratified percentile bootstrap CI for ROC-AUC (deterministic for a seed)."""
    s, y = _validate(scores, labels)
    _require_both(y)
    rng = np.random.default_rng(seed)
    pos, neg = s[y == 1], s[y == 0]
    aucs = np.empty(n_boot)
    for i in range(n_boot):
        bp = rng.choice(pos, size=pos.size, replace=True)
        bn = rng.choice(neg, size=neg.size, replace=True)
        aucs[i] = roc_auc(np.r_[bp, bn], np.r_[np.ones(bp.size, int), np.zeros(bn.size, int)])
    lo, hi = np.percentile(aucs, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(lo), float(hi)


def calibration_curve(scores, labels, n_bins: int = 10) -> List[Dict[str, float]]:
    """
    Reliability table: within each score bin, mean score vs the observed
    fraction of synthetic samples. A calibrated probability would have
    mean_score ~= observed_rate in every bin.
    """
    s, y = _validate(scores, labels)
    if s.min() < 0.0 or s.max() > 1.0:
        raise ValueError("calibration needs scores in [0, 1]")
    bins = np.minimum((s * n_bins).astype(int), n_bins - 1)
    out = []
    for b in range(n_bins):
        m = bins == b
        if m.any():
            out.append({
                "bin_low": b / n_bins, "bin_high": (b + 1) / n_bins,
                "count": int(m.sum()),
                "mean_score": float(s[m].mean()),
                "observed_rate": float(y[m].mean()),
            })
    return out


def expected_calibration_error(scores, labels, n_bins: int = 10) -> float:
    s, y = _validate(scores, labels)
    total = s.size
    return float(sum(
        row["count"] / total * abs(row["observed_rate"] - row["mean_score"])
        for row in calibration_curve(s, y, n_bins)
    ))


def brier_score(scores, labels) -> float:
    s, y = _validate(scores, labels)
    if s.min() < 0.0 or s.max() > 1.0:
        raise ValueError("brier score needs scores in [0, 1]")
    return float(np.mean((s - y) ** 2))
