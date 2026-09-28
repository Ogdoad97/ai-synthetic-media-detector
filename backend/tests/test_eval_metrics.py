"""Gate D3: the metric implementations, cross-checked against scikit-learn / scipy."""
import math

import numpy as np
import pytest

from evaluation import metrics as M

sk = pytest.importorskip("sklearn.metrics")


def _random_case(rng):
    n = int(rng.integers(6, 300))
    y = rng.integers(0, 2, n)
    while y.min() == y.max():
        y = rng.integers(0, 2, n)
    s = np.round(rng.normal(y * rng.uniform(0, 2), 1, n), int(rng.integers(0, 3)))  # ties on purpose
    return s, y


@pytest.mark.parametrize("seed", range(40))
def test_ranking_metrics_match_sklearn_including_ties(seed):
    s, y = _random_case(np.random.default_rng(seed))
    assert M.roc_auc(s, y) == pytest.approx(sk.roc_auc_score(y, s), abs=1e-12)
    assert M.average_precision(s, y) == pytest.approx(sk.average_precision_score(y, s), abs=1e-12)
    fpr, tpr, _ = sk.roc_curve(y, s, drop_intermediate=False)
    mine = M.roc_curve(s, y)
    assert {(round(a, 12), round(b, 12)) for a, b in zip(fpr, tpr)} == \
           {(round(a, 12), round(b, 12)) for a, b in zip(mine["fpr"], mine["tpr"])}


def test_known_textbook_example():
    s, y = [0.1, 0.4, 0.35, 0.8], [0, 0, 1, 1]
    assert M.roc_auc(s, y) == pytest.approx(0.75)
    assert M.average_precision(s, y) == pytest.approx(0.8333333333)


def test_perfect_inverted_and_constant_scores():
    y = [0, 0, 0, 1, 1, 1]
    assert M.roc_auc([.1, .2, .3, .7, .8, .9], y) == 1.0
    assert M.roc_auc([.9, .8, .7, .3, .2, .1], y) == 0.0
    assert M.roc_auc([.5] * 6, y) == 0.5          # the baseline clips to 0/1, so ties are common


def test_invalid_inputs_are_rejected():
    with pytest.raises(M.SingleClassError):
        M.roc_auc([.1, .2], [0, 0])
    with pytest.raises(M.SingleClassError):
        M.average_precision([.1, .2], [1, 1])
    with pytest.raises(ValueError):
        M.roc_auc([.1, float("nan")], [0, 1])
    with pytest.raises(ValueError):
        M.roc_auc([.1, .2], [0, 2])
    with pytest.raises(ValueError):
        M.roc_auc([.1, .2, .3], [0, 1])
    with pytest.raises(ValueError):
        M.roc_auc([], [])


def test_rates_at_threshold_uses_score_geq_threshold():
    r = M.rates_at_threshold([.2, .5, .5, .9], [0, 0, 1, 1], 0.5)
    assert (r["tp"], r["fn"], r["fp"], r["tn"]) == (2, 0, 1, 1)
    assert r["tpr"] == 1.0 and r["fpr"] == 0.5


@pytest.mark.parametrize("seed", range(25))
def test_threshold_at_fpr_guarantee_and_minimality(seed):
    rng = np.random.default_rng(seed)
    neg = np.round(rng.uniform(0, 1, int(rng.integers(10, 400))), int(rng.integers(1, 3)))  # ties
    for target in (0.0, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0):
        thr = M.threshold_at_fpr(neg, target)
        allowed = math.floor(target * neg.size + 1e-12)
        assert (neg >= thr).sum() <= allowed                       # guarantee holds on its own data
        lower = np.unique(neg)[np.unique(neg) < thr]               # minimality: any lower cut breaks it
        assert all((neg >= v).sum() > allowed for v in lower)


def test_threshold_never_splits_a_tie_group():
    neg = [0.5] * 10
    thr = M.threshold_at_fpr(neg, 0.05)      # allows 0 of 10 false positives
    assert thr > 0.5 and (np.array(neg) >= thr).sum() == 0


def test_threshold_extremes():
    neg = [0.1, 0.2, 0.3]
    assert M.threshold_at_fpr(neg, 0.0) > 0.3
    assert M.threshold_at_fpr(neg, 1.0) == 0.1
    with pytest.raises(ValueError):
        M.threshold_at_fpr([], 0.1)
    with pytest.raises(ValueError):
        M.threshold_at_fpr(neg, 1.5)


@pytest.mark.parametrize("k,n", [(0, 10), (10, 10), (5, 10), (1, 100), (37, 120), (0, 1)])
def test_wilson_matches_scipy(k, n):
    from scipy.stats import binomtest
    ref = binomtest(k, n).proportion_ci(confidence_level=0.95, method="wilson")
    lo, hi = M.wilson_interval(k, n)
    assert lo == pytest.approx(ref.low, abs=2e-4) and hi == pytest.approx(ref.high, abs=2e-4)


def test_wilson_empty_sample():
    assert M.wilson_interval(0, 0) == (None, None)


def test_bootstrap_ci_is_deterministic_contains_auc_and_narrows_with_data():
    rng = np.random.default_rng(0)

    def data(n):
        y = np.r_[np.ones(n), np.zeros(n)].astype(int)
        return np.r_[rng.normal(1, 1, n), rng.normal(0, 1, n)], y

    s, y = data(40)
    a = M.bootstrap_auc_ci(s, y, n_boot=300, seed=5)
    assert a == M.bootstrap_auc_ci(s, y, n_boot=300, seed=5)
    assert a != M.bootstrap_auc_ci(s, y, n_boot=300, seed=6)
    assert a[0] <= M.roc_auc(s, y) <= a[1]
    s2, y2 = data(800)
    b = M.bootstrap_auc_ci(s2, y2, n_boot=300, seed=5)
    assert (b[1] - b[0]) < (a[1] - a[0])


def test_bootstrap_perfect_separation_is_degenerate_one():
    assert M.bootstrap_auc_ci([.1, .2, .8, .9], [0, 0, 1, 1], n_boot=50) == (1.0, 1.0)


def test_calibration_bins_ece_and_brier():
    s = [0.05, 0.05, 0.95, 0.95]
    assert M.brier_score(s, [0, 0, 1, 1]) == pytest.approx(0.0025)
    # perfectly calibrated: 90% of the 0.9-scored samples are positive
    s = [0.9] * 10 + [0.1] * 10
    y = [1] * 9 + [0] + [1] + [0] * 9
    assert M.expected_calibration_error(s, y) == pytest.approx(0.0, abs=1e-12)
    # confidently wrong: score 0.9 but nothing is synthetic
    assert M.expected_calibration_error([0.9] * 5, [0] * 5) == pytest.approx(0.9)
    rows = M.calibration_curve([0.0, 1.0], [0, 1])          # 0.0 and 1.0 both land in valid bins
    assert [r["count"] for r in rows] == [1, 1]
    for bad in (lambda: M.calibration_curve([1.2], [1]), lambda: M.brier_score([-0.1], [0])):
        with pytest.raises(ValueError):
            bad()
