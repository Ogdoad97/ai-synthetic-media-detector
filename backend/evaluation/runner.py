"""
The evaluation protocol, in the order that keeps it honest:

  1. splits are assigned from data identity alone (see splits.py)
  2. the operating threshold is chosen on VALIDATION real samples only,
     at a target false-positive rate, then FROZEN
  3. test / held-out-generator / perturbed-test results are all reported
     at that frozen threshold (and at the API's deployed threshold)
  4. every number that rests on few samples carries an interval, and
     anything that limits what can be concluded is listed under `warnings`

Nothing here reads test results back into any choice.
"""
from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from . import HARNESS_VERSION, metrics as M
from .perturbations import PERTURBATIONS
from .scorers import Scorer
from .splits import (
    DEFAULT_FRACTIONS, SPLIT_TEST, SPLIT_TRAIN, SPLIT_UNSEEN, SPLIT_VAL,
    Sample, assert_no_group_leakage, assign_splits,
)


def _score_sample(scorer: Scorer, s: Sample, root: Path,
                  transform: Optional[Callable[[bytes], bytes]] = None) -> float:
    full = Path(root) / s.path
    if s.media_type == "video":
        return scorer.score_video(str(full))
    data = full.read_bytes()
    return scorer.score_image(transform(data) if transform else data)


def _score_all(scorer, samples, root, transform=None):
    """-> ({id: score}, [(id, label, error class)])  Failures are kept, not hidden."""
    scores: Dict[str, float] = {}
    failures: List[Dict[str, Any]] = []
    for s in samples:
        try:
            scores[s.id] = _score_sample(scorer, s, root, transform)
        except Exception as exc:       # one bad file must not sink the run
            failures.append({"id": s.id, "label": s.label, "generator": s.generator,
                             "error": type(exc).__name__})
    return scores, failures


def _rates(scores, labels, threshold, with_ci=True) -> Dict[str, Any]:
    r = M.rates_at_threshold(scores, labels, threshold)
    if with_ci:
        r["tpr_ci95"] = M.wilson_interval(r["tp"], r["tp"] + r["fn"])
        r["fpr_ci95"] = M.wilson_interval(r["fp"], r["fp"] + r["tn"])
    return r


def _safe(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except M.SingleClassError:
        return None


def _headline(pos_scores: List[float], neg_scores: List[float], frozen: float,
              deployed: Optional[float], n_boot: int, seed: int) -> Dict[str, Any]:
    scores = pos_scores + neg_scores
    labels = [1] * len(pos_scores) + [0] * len(neg_scores)
    out: Dict[str, Any] = {"n_synthetic": len(pos_scores), "n_real": len(neg_scores)}
    if not pos_scores or not neg_scores:
        out["note"] = "needs both real and synthetic samples for ranking metrics"
        return out
    out["roc_auc"] = M.roc_auc(scores, labels)
    out["roc_auc_ci95"] = M.bootstrap_auc_ci(scores, labels, n_boot=n_boot, seed=seed)
    out["pr_auc"] = M.average_precision(scores, labels)
    out["at_frozen_threshold"] = _rates(scores, labels, frozen)
    if deployed is not None:
        out["at_deployed_threshold"] = _rates(scores, labels, deployed)
    out["brier"] = M.brier_score(scores, labels)
    out["ece"] = M.expected_calibration_error(scores, labels)
    out["calibration_curve"] = M.calibration_curve(scores, labels)
    return out


def _per_generator(test_samples, scores, real_neg_scores, frozen, deployed, n_boot, seed):
    by_gen: Dict[str, List[Sample]] = defaultdict(list)
    for s in test_samples:
        if s.id in scores:
            by_gen[s.generator].append(s)
    rows = {}
    for gen, group in sorted(by_gen.items()):
        sc = [scores[s.id] for s in group]
        is_real = group[0].label == 0
        row: Dict[str, Any] = {"n": len(sc), "kind": "real" if is_real else "synthetic",
                               "mean_score": sum(sc) / len(sc)}
        if is_real:
            row["false_positive_rate_at_frozen"] = _rates(sc, [0] * len(sc), frozen)["fpr"]
            row["fpr_ci95"] = _rates(sc, [0] * len(sc), frozen)["fpr_ci95"]
            if deployed is not None:
                row["false_positive_rate_at_deployed"] = _rates(sc, [0] * len(sc), deployed)["fpr"]
        else:
            labels = [1] * len(sc) + [0] * len(real_neg_scores)
            allsc = sc + real_neg_scores
            row["roc_auc_vs_test_real"] = _safe(M.roc_auc, allsc, labels)
            r = _rates(sc, [1] * len(sc), frozen)
            row["detection_rate_at_frozen"] = r["tpr"]
            row["detection_ci95"] = r["tpr_ci95"]
            if deployed is not None:
                row["detection_rate_at_deployed"] = _rates(sc, [1] * len(sc), deployed)["tpr"]
        rows[gen] = row
    return rows


def _evaluate_media_type(media, samples, split_of, scorer, root, target_fpr, deployed,
                         perturb, n_boot, seed) -> Dict[str, Any]:
    mine = [s for s in samples if s.media_type == media]
    by_split = {sp: [s for s in mine if split_of[s.id] == sp]
                for sp in (SPLIT_TRAIN, SPLIT_VAL, SPLIT_TEST, SPLIT_UNSEEN)}
    warnings: List[str] = []
    result: Dict[str, Any] = {"counts": {sp: len(v) for sp, v in by_split.items()}, "warnings": warnings}

    # ---- score (clean) -------------------------------------------------
    val_scores, val_fail = _score_all(scorer, by_split[SPLIT_VAL], root)
    test_scores, test_fail = _score_all(scorer, by_split[SPLIT_TEST], root)
    unseen_scores, unseen_fail = _score_all(scorer, by_split[SPLIT_UNSEEN], root)
    failures = val_fail + test_fail + unseen_fail
    result["failures"] = {
        "count": len(failures),
        "real": sum(1 for f in failures if f["label"] == 0),
        "synthetic": sum(1 for f in failures if f["label"] == 1),
        "examples": failures[:10],
    }
    if failures:
        warnings.append(
            f"{len(failures)} file(s) failed to score and are EXCLUDED from all metrics "
            f"({result['failures']['real']} real, {result['failures']['synthetic']} synthetic). "
            "If failures are lopsided between classes the metrics are biased."
        )

    # ---- freeze the threshold from validation real samples only ---------
    val_real = [val_scores[s.id] for s in by_split[SPLIT_VAL] if s.label == 0 and s.id in val_scores]
    if not val_real:
        warnings.append("No scored real samples in the validation split: cannot choose an "
                        "operating threshold, so no frozen-threshold results are reported.")
        return result
    frozen = M.threshold_at_fpr(val_real, target_fpr)
    if len(val_real) * target_fpr < 1:
        warnings.append(
            f"Target FPR {target_fpr:g} is below the resolution of {len(val_real)} validation "
            f"real samples (need >= {int(1 / target_fpr)}); the frozen threshold sits just above "
            "the highest validation real score and gives no real guarantee on new data."
        )
    val_pairs = [(val_scores[s.id], s.label) for s in by_split[SPLIT_VAL] if s.id in val_scores]
    result["threshold"] = {
        "target_fpr": target_fpr, "frozen": frozen, "chosen_on": "validation real samples only",
        "n_validation_real": len(val_real), "deployed": deployed,
    }
    result["validation"] = {
        "roc_auc": _safe(M.roc_auc, [p[0] for p in val_pairs], [p[1] for p in val_pairs]),
        "note": "informational; the test split is what supports any claim",
    }

    # ---- test (seen generators) & held-out generators -------------------
    test_real = [test_scores[s.id] for s in by_split[SPLIT_TEST] if s.label == 0 and s.id in test_scores]
    test_syn = [test_scores[s.id] for s in by_split[SPLIT_TEST] if s.label == 1 and s.id in test_scores]
    unseen_syn = [unseen_scores[s.id] for s in by_split[SPLIT_UNSEEN] if s.id in unseen_scores]
    result["test"] = _headline(test_syn, test_real, frozen, deployed, n_boot, seed)
    if by_split[SPLIT_UNSEEN]:
        result["test_unseen_generators"] = _headline(unseen_syn, test_real, frozen, deployed, n_boot, seed)
        result["test_unseen_generators"]["generators"] = sorted({s.generator for s in by_split[SPLIT_UNSEEN]})
    else:
        warnings.append("No held-out generators: results say nothing about generators the "
                        "threshold hasn't seen. Use --heldout to test cross-generator generalization.")
    for label, h in (("test", result["test"]), ("held-out generators", result.get("test_unseen_generators", {}))):
        if h.get("roc_auc") is not None and h["roc_auc"] < 0.5:
            warnings.append(
                f"ROC-AUC on {label} is {h['roc_auc']:.3f} (< 0.5): the score ranks synthetic samples "
                "BELOW real ones on this data - worse than random, so the signal is inverted here."
            )
    n_gens = len({s.generator for s in mine if s.label == 1})
    if n_gens < 2:
        warnings.append(f"Only {n_gens} synthetic generator in this dataset: any 'detects AI images' "
                        "claim is really 'detects this one generator'.")
    if len(test_real) < 100 or len(test_syn) + len(unseen_syn) < 100:
        warnings.append(f"Small test set ({len(test_real)} real / {len(test_syn) + len(unseen_syn)} synthetic): "
                        "see the confidence intervals; differences inside them are noise.")

    # ---- per generator / per real source --------------------------------
    eval_samples = by_split[SPLIT_TEST] + by_split[SPLIT_UNSEEN]
    pooled = {**test_scores, **unseen_scores}
    result["per_generator"] = _per_generator(eval_samples, pooled, test_real, frozen, deployed, n_boot, seed)

    # ---- robustness (images only), threshold stays frozen ---------------
    if media == "image" and perturb:
        rob = {}
        for name, fn in perturb.items():
            t_scores, t_fail = _score_all(scorer, by_split[SPLIT_TEST], root, fn)
            u_scores, u_fail = _score_all(scorer, by_split[SPLIT_UNSEEN], root, fn)
            p_real = [t_scores[s.id] for s in by_split[SPLIT_TEST] if s.label == 0 and s.id in t_scores]
            p_syn = [t_scores[s.id] for s in by_split[SPLIT_TEST] if s.label == 1 and s.id in t_scores]
            p_un = [u_scores[s.id] for s in by_split[SPLIT_UNSEEN] if s.id in u_scores]
            entry = _headline(p_syn + p_un, p_real, frozen, deployed, n_boot=200, seed=seed)
            entry.pop("calibration_curve", None)
            entry["failed_to_score"] = len(t_fail) + len(u_fail)
            rob[name] = entry
        result["robustness"] = {
            "note": "test + held-out samples re-scored after each transform; threshold stays frozen "
                    "from CLEAN validation, so this shows how the operating point shifts in the wild",
            "transforms": rob,
        }
    elif media == "video":
        result["robustness"] = {"note": "perturbation suite is image-only; video is evaluated clean."}
    return result


def run_evaluation(
    samples: Sequence[Sample], root: Path, scorer: Scorer, *,
    target_fpr: float = 0.01, seed: int = 1337,
    fractions: Tuple[float, float, float] = DEFAULT_FRACTIONS,
    heldout_generators: Iterable[str] = (),
    perturb: Optional[Dict[str, Callable[[bytes], bytes]]] = None,
    n_boot: int = 1000, split_of: Optional[Dict[str, str]] = None,
    manifest_sha256: Optional[str] = None, manifest_warnings: Sequence[str] = (),
    demo: bool = False,
) -> Dict[str, Any]:
    heldout = sorted(set(heldout_generators))
    if split_of is None:
        split_of = assign_splits(samples, seed, fractions, heldout)
    assert_no_group_leakage(samples, split_of)
    info = scorer.info()
    deployed = info.get("synthetic_threshold")

    media_types = sorted({s.media_type for s in samples})
    report: Dict[str, Any] = {
        "harness_version": HARNESS_VERSION,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "demo": demo,
        "detector": info,
        "protocol": {
            "manifest_sha256": manifest_sha256, "seed": seed, "fractions": list(fractions),
            "heldout_generators": heldout, "target_fpr": target_fpr,
            "threshold_source": "validation real samples only, frozen before test",
        },
        "n_samples": len(samples),
        "warnings": list(manifest_warnings),
        "media": {},
    }
    for media in media_types:
        report["media"][media] = _evaluate_media_type(
            media, samples, split_of, scorer, root, target_fpr, deployed,
            PERTURBATIONS if perturb is None else perturb, n_boot, seed)
    return report
