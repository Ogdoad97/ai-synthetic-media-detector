"""Render an evaluation result dict as Markdown (the JSON is the source of truth)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _f(x: Optional[float], nd: int = 3) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


def _ci(ci) -> str:
    return "" if not ci or ci[0] is None else f" [{ci[0]:.3f}, {ci[1]:.3f}]"


def _rate(r: Optional[Dict[str, Any]], key: str) -> str:
    return "n/a" if not r else f"{_f(r[key])}{_ci(r.get(key + '_ci95'))}"


def _headline_rows(name: str, h: Dict[str, Any]) -> List[str]:
    if "roc_auc" not in h:
        return [f"| {name} | {h.get('n_synthetic', 0)} / {h.get('n_real', 0)} | {h.get('note', '')} | | | | |"]
    fz, dp = h["at_frozen_threshold"], h.get("at_deployed_threshold")
    return [
        f"| {name} | {h['n_synthetic']} / {h['n_real']} | {_f(h['roc_auc'])}{_ci(h['roc_auc_ci95'])} | "
        f"{_f(h['pr_auc'])} | {_rate(fz, 'tpr')} / {_rate(fz, 'fpr')} | "
        f"{_rate(dp, 'tpr')} / {_rate(dp, 'fpr')} | {_f(h['ece'])} |"
    ]


def render_markdown(rep: Dict[str, Any]) -> str:
    L: List[str] = []
    if rep.get("demo"):
        L += ["> **SYNTHETIC DEMO DATA - NOT A MEASUREMENT OF DETECTION ACCURACY.**",
              "> Procedurally generated toy images; these numbers only prove the pipeline runs.", ""]
    d, p = rep["detector"], rep["protocol"]
    L += [f"# Evaluation report - {d.get('name')} v{d.get('version')}", "",
          f"- Detector validated flag: **{d.get('validated')}** | harness v{rep['harness_version']} | {rep['generated_at']}",
          f"- Samples: {rep['n_samples']} | seed {p['seed']} | split (train/val/test) {p['fractions']} | "
          f"held-out generators: {p['heldout_generators'] or 'none'}",
          f"- Manifest sha256: `{p['manifest_sha256']}`",
          f"- Threshold: chosen on **validation real samples only** at target FPR {p['target_fpr']:g}, frozen before test", ""]
    if rep["warnings"]:
        L += ["## Warnings", *[f"- {w}" for w in rep["warnings"]], ""]

    for media, m in rep["media"].items():
        L += [f"## {media.capitalize()}", ""]
        if m["warnings"]:
            L += [*[f"- WARNING: {w}" for w in m["warnings"]], ""]
        if "threshold" not in m:
            continue
        t = m["threshold"]
        L += [f"Frozen threshold **{t['frozen']:.4f}** (from {t['n_validation_real']} validation real samples); "
              f"deployed API threshold {t['deployed']}. Rates shown as value [95% CI].", "",
              "| Set | synthetic / real | ROC-AUC [95% CI] | PR-AUC | TPR / FPR @ frozen | TPR / FPR @ deployed | ECE |",
              "|---|---|---|---|---|---|---|"]
        L += _headline_rows("Test (seen generators)", m["test"])
        if "test_unseen_generators" in m:
            L += _headline_rows("Held-out generators", m["test_unseen_generators"])
        L += [""]
        L += ["### Per generator / source", "",
              "| Name | n | Kind | AUC vs real | Detection or FPR @ frozen | @ deployed |", "|---|---|---|---|---|---|"]
        for name, r in m["per_generator"].items():
            if r["kind"] == "real":
                L.append(f"| {name} | {r['n']} | real | - | FPR {_f(r['false_positive_rate_at_frozen'])}{_ci(r['fpr_ci95'])} | "
                         f"FPR {_f(r.get('false_positive_rate_at_deployed'))} |")
            else:
                L.append(f"| {name} | {r['n']} | synthetic | {_f(r['roc_auc_vs_test_real'])} | "
                         f"TPR {_f(r['detection_rate_at_frozen'])}{_ci(r['detection_ci95'])} | "
                         f"TPR {_f(r.get('detection_rate_at_deployed'))} |")
        L += [""]
        rob = m.get("robustness", {})
        if rob.get("transforms"):
            clean = m["test"]
            L += ["### Robustness (threshold stays frozen)", "", rob["note"], "",
                  "| Transform | AUC | dAUC vs clean | TPR @ frozen | FPR @ frozen |", "|---|---|---|---|---|"]
            for name, e in rob["transforms"].items():
                if "roc_auc" not in e:
                    L.append(f"| {name} | n/a | | | |")
                    continue
                dauc = e["roc_auc"] - clean["roc_auc"] if "roc_auc" in clean else None
                fz = e["at_frozen_threshold"]
                flag = " (inverted)" if e["roc_auc"] < 0.5 else ""
                L.append(f"| {name} | {_f(e['roc_auc'])}{flag} | {_f(dauc)} | {_f(fz['tpr'])} | {_f(fz['fpr'])} |")
            L += [""]
        elif rob.get("note"):
            L += [rob["note"], ""]
        L += ["### Calibration (test, seen generators)", "",
              "`confidence_percentage` is `score x 100`. It is only a probability if mean score ~ observed rate in every bin.", "",
              "| Score bin | n | mean score | observed synthetic rate |", "|---|---|---|---|"]
        for b in m["test"].get("calibration_curve", []):
            L.append(f"| {b['bin_low']:.1f}-{b['bin_high']:.1f} | {b['count']} | {b['mean_score']:.3f} | {b['observed_rate']:.3f} |")
        L += [f"", f"ECE {_f(m['test'].get('ece'))}, Brier {_f(m['test'].get('brier'))}", ""]

    L += ["## What this report does and does not support", "",
          "- It supports claims about THIS dataset, these generators, and these transforms only.",
          "- A frozen threshold's FPR is an estimate; the interval shows how loose it is at this sample size.",
          "- Held-out generators test one kind of generalization; a new generator can still fail.",
          "- No threshold or scoring change should ship without a fresh report showing the effect."]
    return "\n".join(L) + "\n"
