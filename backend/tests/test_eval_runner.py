"""
Gate D3: the evaluation protocol itself.

Detectors are replaced by a scorer that reads a score straight off the
image (solid grey level / 255), so every expected number below is known in
advance and a protocol bug shows up as a wrong number, not a fuzzy one.
"""
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from evaluation import metrics as M
from evaluation.__main__ import main as cli_main
from evaluation.demo_data import make_demo_dataset
from evaluation.perturbations import PERTURBATIONS
from evaluation.report import render_markdown
from evaluation.runner import run_evaluation
from evaluation.scorers import FunctionScorer
from evaluation.splits import Sample, assign_splits

INFO = {"name": "test-scorer", "version": "t", "validated": False, "synthetic_threshold": 0.6}


def grey_score(data: bytes) -> float:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError("undecodable")
    return float(img.mean()) / 255.0


def scorer(log=None):
    def fn(data):
        if log is not None:
            img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            log.append((img.shape[1], img.shape[0], data[:2] == b"\xff\xd8"))
        return grey_score(data)
    return FunctionScorer(fn, info=INFO)


def build(tmp_path, level, n_groups=150, gens=("real:cam", "gen_a", "gen_b"), size=(32, 32),
          seed=1, heldout=(), fractions=(0.0, 0.3, 0.7)):
    """level(split, sample) -> grey level 0..255; returns (samples, split_of)."""
    samples = []
    for g in range(n_groups):
        for gen in gens:
            samples.append(Sample(f"{gen.replace(':', '_')}_{g}.png", f"{gen.replace(':', '_')}_{g}.png",
                                  0 if gen.startswith("real") else 1, gen, "image", f"grp{g}"))
    split_of = assign_splits(samples, seed, fractions, heldout)
    for s in samples:
        v = int(level(split_of[s.id], s))
        cv2.imwrite(str(tmp_path / s.path), np.full((size[1], size[0], 3), v, np.uint8))
    return samples, split_of


def run(tmp_path, samples, split_of, **kw):
    kw.setdefault("perturb", {})
    kw.setdefault("n_boot", 50)
    kw.setdefault("target_fpr", 0.05)
    kw.setdefault("seed", 1)
    return run_evaluation(samples, tmp_path, kw.pop("scorer", scorer()), split_of=split_of, **kw)


class TestFrozenThreshold:
    def test_threshold_comes_from_validation_reals_and_test_is_scored_at_it(self, tmp_path):
        # val reals sit at 26; test reals sit MUCH higher (128). A runner that tuned on
        # test would pick a threshold above 128 and report FPR 0; the frozen one must not.
        def level(split, s):
            if s.label == 0:
                return 26 if split == "val" else 128
            return 200
        samples, split_of = build(tmp_path, level)
        rep = run(tmp_path, samples, split_of)
        m = rep["media"]["image"]
        thr = m["threshold"]["frozen"]
        assert 26 / 255 < thr < 27 / 255
        assert m["threshold"]["chosen_on"] == "validation real samples only"
        fz = m["test"]["at_frozen_threshold"]
        assert fz["fpr"] == 1.0 and fz["tpr"] == 1.0          # 128/255 >= thr: honest, ugly FPR

    def test_threshold_is_independent_of_everything_in_test_and_heldout(self, tmp_path):
        def level_a(split, s):
            return 30 if (s.label == 0 and split == "val") else (60 if s.label == 0 else 200)
        def level_b(split, s):
            return 30 if (s.label == 0 and split == "val") else (240 if s.label == 0 else 10)
        ta, tb = tmp_path / "a", tmp_path / "b"
        ta.mkdir(); tb.mkdir()
        sa, sp = build(ta, level_a, heldout=["gen_b"])
        sb, _ = build(tb, level_b, heldout=["gen_b"])
        ra, rb = run(ta, sa, sp), run(tb, sb, sp)
        assert ra["media"]["image"]["threshold"]["frozen"] == rb["media"]["image"]["threshold"]["frozen"]
        assert ra["media"]["image"]["test"]["roc_auc"] != rb["media"]["image"]["test"]["roc_auc"]

    def test_threshold_matches_an_independent_computation(self, tmp_path):
        rng = np.random.default_rng(3)
        vals = {}
        def level(split, s):
            v = int(rng.integers(5, 120)) if s.label == 0 else int(rng.integers(100, 250))
            vals[s.id] = (split, v)
            return v
        samples, split_of = build(tmp_path, level)
        rep = run(tmp_path, samples, split_of, target_fpr=0.1)
        val_real = [v / 255 for sid, (sp, v) in vals.items() if sp == "val" and sid.startswith("real")]
        assert rep["media"]["image"]["threshold"]["frozen"] == M.threshold_at_fpr(val_real, 0.1)


class TestReporting:
    def test_per_generator_rows_and_auc_vs_detection_are_distinct(self, tmp_path):
        # gen_b ranks above real (AUC 1.0) but under the frozen threshold (detection 0.0)
        def level(split, s):
            return {"real:cam": 30 if split == "val" else 20, "gen_a": 200, "gen_b": 25}[s.generator]
        samples, split_of = build(tmp_path, level)
        pg = run(tmp_path, samples, split_of)["media"]["image"]["per_generator"]
        assert pg["gen_a"]["detection_rate_at_frozen"] == 1.0 and pg["gen_a"]["roc_auc_vs_test_real"] == 1.0
        assert pg["gen_b"]["detection_rate_at_frozen"] == 0.0 and pg["gen_b"]["roc_auc_vs_test_real"] == 1.0
        assert pg["real:cam"]["false_positive_rate_at_frozen"] == 0.0
        assert pg["gen_a"]["detection_rate_at_deployed"] == 1.0          # 200/255 >= 0.6
        assert pg["gen_b"]["detection_rate_at_deployed"] == 0.0

    def test_heldout_generator_is_never_in_validation_and_is_reported_separately(self, tmp_path):
        def level(split, s):
            return {"real:cam": 30, "gen_a": 200, "gen_b": 90}[s.generator]
        samples, split_of = build(tmp_path, level, heldout=["gen_b"])
        rep = run(tmp_path, samples, split_of, heldout_generators=["gen_b"])
        m = rep["media"]["image"]
        n_b = sum(1 for s in samples if s.generator == "gen_b")
        assert m["counts"]["test_unseen"] == n_b
        assert m["test_unseen_generators"]["generators"] == ["gen_b"]
        assert m["test_unseen_generators"]["n_synthetic"] == n_b
        assert m["test_unseen_generators"]["n_real"] == m["test"]["n_real"]   # same real pool
        assert m["test"]["n_synthetic"] == m["counts"]["test"] - m["test"]["n_real"]
        assert not any("held-out" in w.lower() and "No held-out" in w for w in m["warnings"])

    def test_deployed_threshold_is_reported_next_to_the_frozen_one(self, tmp_path):
        samples, split_of = build(tmp_path, lambda sp, s: 30 if s.label == 0 else 200)
        m = run(tmp_path, samples, split_of)["media"]["image"]
        assert m["threshold"]["deployed"] == 0.6
        assert m["test"]["at_deployed_threshold"]["threshold"] == 0.6

    def test_markdown_renders_all_sections_without_error(self, tmp_path):
        samples, split_of = build(tmp_path, lambda sp, s: 30 if s.label == 0 else 200, heldout=["gen_b"])
        rep = run(tmp_path, samples, split_of, heldout_generators=["gen_b"], perturb={"jpeg_q90": PERTURBATIONS["jpeg_q90"]})
        md = render_markdown(rep)
        for needle in ("# Evaluation report", "Frozen threshold", "Per generator", "Robustness",
                       "Calibration", "does and does not support", "Held-out generators"):
            assert needle in md
        assert "SYNTHETIC DEMO" not in md
        assert "SYNTHETIC DEMO" in render_markdown({**rep, "demo": True})


class TestWarnings:
    def _warnings(self, rep):
        return " || ".join(rep["media"]["image"]["warnings"])

    def test_resolution_single_generator_no_heldout_and_small_test(self, tmp_path):
        samples, split_of = build(tmp_path, lambda sp, s: 30 if s.label == 0 else 200,
                                  n_groups=40, gens=("real:cam", "gen_a"))
        w = self._warnings(run(tmp_path, samples, split_of, target_fpr=0.01))
        assert "below the resolution" in w
        assert "Only 1 synthetic generator" in w
        assert "No held-out generators" in w
        assert "Small test set" in w

    def test_inverted_score_is_called_out(self, tmp_path):
        samples, split_of = build(tmp_path, lambda sp, s: 200 if s.label == 0 else 30)
        rep = run(tmp_path, samples, split_of)
        assert rep["media"]["image"]["test"]["roc_auc"] == 0.0
        assert "BELOW real ones" in self._warnings(rep)
        assert "(inverted)" in render_markdown(run(tmp_path, samples, split_of, perturb={"jpeg_q90": PERTURBATIONS["jpeg_q90"]}))

    def test_no_group_warning_is_carried_into_the_report(self, tmp_path):
        samples, split_of = build(tmp_path, lambda sp, s: 30 if s.label == 0 else 200, n_groups=20)
        rep = run(tmp_path, samples, split_of, manifest_warnings=["No 'group' column: ..."])
        assert rep["warnings"] == ["No 'group' column: ..."]


class TestFailuresAreVisible:
    def test_unscorable_files_are_excluded_counted_and_warned_about(self, tmp_path):
        samples, split_of = build(tmp_path, lambda sp, s: 30 if s.label == 0 else 200)
        bad_real = next(s for s in samples if s.label == 0 and split_of[s.id] == "test")
        bad_syn = next(s for s in samples if s.label == 1 and split_of[s.id] == "test")
        clean = run(tmp_path, samples, split_of)["media"]["image"]["test"]
        for s in (bad_real, bad_syn):
            (tmp_path / s.path).write_bytes(b"not an image")
        m = run(tmp_path, samples, split_of)["media"]["image"]
        assert m["failures"]["count"] == 2 and m["failures"]["real"] == 1 and m["failures"]["synthetic"] == 1
        assert m["failures"]["examples"][0]["error"] == "ValueError"
        assert m["test"]["n_real"] == clean["n_real"] - 1 and m["test"]["n_synthetic"] == clean["n_synthetic"] - 1
        assert "EXCLUDED" in " ".join(m["warnings"])


class TestRobustness:
    @pytest.mark.parametrize("name,dims,jpeg", [
        ("downscale_50", (32, 24), False),
        ("center_crop_80", (51, 38), False),
        ("resize_roundtrip_50", (64, 48), False),
        ("blur_sigma_1", (64, 48), False),
        ("noise_sigma_3", (64, 48), False),
        ("jpeg_q70", (64, 48), True),
    ])
    def test_the_transform_is_really_applied_to_test_and_heldout_images_only(self, tmp_path, name, dims, jpeg):
        samples, split_of = build(tmp_path, lambda sp, s: 30 if s.label == 0 else 200,
                                  size=(64, 48), heldout=["gen_b"])
        log = []
        rep = run(tmp_path, samples, split_of, scorer=scorer(log), heldout_generators=["gen_b"],
                  perturb={name: PERTURBATIONS[name]})
        n_eval = sum(1 for s in samples if split_of[s.id] in ("test", "test_unseen"))
        n_val = sum(1 for s in samples if split_of[s.id] == "val")
        perturbed = [e for e in log if (e[0], e[1]) == dims and e[2] == jpeg]
        clean_calls = [e for e in log if (e[0], e[1]) == (64, 48) and e[2] is False]
        if name in ("downscale_50", "center_crop_80"):
            assert len(perturbed) == n_eval
        elif jpeg:
            assert len(perturbed) == n_eval
        else:
            assert len(clean_calls) == n_val + 2 * n_eval           # clean pass + perturbed pass
        entry = rep["media"]["image"]["robustness"]["transforms"][name]
        assert entry["at_frozen_threshold"]["threshold"] == rep["media"]["image"]["threshold"]["frozen"]
        assert entry["roc_auc"] == 1.0 and entry["failed_to_score"] == 0

    def test_a_transform_that_destroys_the_signal_is_visible_at_the_frozen_threshold(self, tmp_path):
        samples, split_of = build(tmp_path, lambda sp, s: 30 if s.label == 0 else 200)
        wipe = lambda data: cv2.imencode(".png", np.full((32, 32, 3), 30, np.uint8))[1].tobytes()
        rep = run(tmp_path, samples, split_of, perturb={"wipe": wipe})["media"]["image"]
        assert rep["test"]["at_frozen_threshold"]["tpr"] == 1.0
        assert rep["robustness"]["transforms"]["wipe"]["at_frozen_threshold"]["tpr"] == 0.0

    @pytest.mark.parametrize("name", list(PERTURBATIONS))
    def test_every_shipped_perturbation_is_deterministic_and_decodable(self, name):
        rng = np.random.default_rng(0)
        ok, buf = cv2.imencode(".png", rng.integers(0, 255, (40, 60, 3), dtype=np.uint8))
        a, b = PERTURBATIONS[name](buf.tobytes()), PERTURBATIONS[name](buf.tobytes())
        assert a == b and cv2.imdecode(np.frombuffer(a, np.uint8), cv2.IMREAD_COLOR) is not None

    def test_undecodable_input_to_a_perturbation_raises_cleanly(self):
        with pytest.raises(ValueError):
            PERTURBATIONS["jpeg_q90"](b"junk")


class TestVideo:
    def test_video_gets_its_own_threshold_and_no_perturbation_suite(self, tmp_path):
        samples, split_of = build(tmp_path, lambda sp, s: 30 if s.label == 0 else 200, n_groups=40, gens=("real:cam", "gen_a"))
        vids = []
        for i in range(60):
            for gen, lvl in (("real:cam", 90), ("gen_a", 240)):
                p = f"v_{gen.replace(':', '_')}_{i}.mp4"
                (tmp_path / p).write_text(str(lvl))
                vids.append(Sample(p, p, 0 if gen.startswith("real") else 1, gen, "video", f"vid{i}"))
        allsamples = samples + vids
        sp = {**split_of, **assign_splits(vids, 1)}
        fs = FunctionScorer(grey_score, video_fn=lambda path: int(Path(path).read_text()) / 255, info=INFO)
        rep = run_evaluation(allsamples, tmp_path, fs, split_of=sp, target_fpr=0.05, n_boot=30, seed=1)
        img, vid = rep["media"]["image"], rep["media"]["video"]
        assert 90 / 255 < vid["threshold"]["frozen"] < 91 / 255      # video reals sit at 90, not 30
        assert 30 / 255 < img["threshold"]["frozen"] < 31 / 255
        assert "image-only" in vid["robustness"]["note"]
        assert vid["test"]["roc_auc"] == 1.0


class TestCli:
    def test_demo_runs_end_to_end_and_is_loudly_labelled(self, tmp_path):
        out = tmp_path / "demo"
        assert cli_main(["demo", "--out", str(out), "--no-perturb", "--n-boot", "30"]) == 0
        md, js = (out / "report.md").read_text(), json.loads((out / "report.json").read_text())
        assert md.startswith("> **SYNTHETIC DEMO DATA - NOT A MEASUREMENT")
        assert js["demo"] is True and js["detector"]["name"] == "forensic-baseline"
        assert js["protocol"]["heldout_generators"] == ["toygen_b"]
        assert (out / "splits.json").exists()

    def test_bad_manifest_exits_2_with_a_message(self, tmp_path, capsys):
        m = tmp_path / "m.jsonl"
        m.write_text(json.dumps({"path": "a.png", "label": 1, "generator": "real"}) + "\n")
        assert cli_main(["run", "--manifest", str(m), "--root", str(tmp_path), "--out", str(tmp_path / "o")]) == 2
        assert "contradicts" in capsys.readouterr().err

    def test_missing_image_files_exit_2(self, tmp_path, capsys):
        m = tmp_path / "m.jsonl"
        m.write_text(json.dumps({"path": "nope.png", "label": 0, "generator": "real"}) + "\n")
        assert cli_main(["run", "--manifest", str(m), "--root", str(tmp_path), "--out", str(tmp_path / "o")]) == 2
        assert "not found" in capsys.readouterr().err

    def test_frozen_splits_survive_a_different_seed(self, tmp_path):
        manifest = make_demo_dataset(tmp_path / "d", n_per_class=12)
        base = ["run", "--manifest", str(manifest), "--root", str(tmp_path / "d"), "--no-perturb", "--n-boot", "20"]
        assert cli_main(base + ["--out", str(tmp_path / "o1"), "--seed", "1"]) == 0
        assert cli_main(base + ["--out", str(tmp_path / "o2"), "--seed", "999",
                                "--frozen-splits", str(tmp_path / "o1" / "splits.json")]) == 0
        assert cli_main(base + ["--out", str(tmp_path / "o3"), "--seed", "999"]) == 0
        s = lambda d: json.loads((tmp_path / d / "splits.json").read_text())["splits"]
        assert s("o2") == s("o1")            # frozen: the new seed is ignored
        assert s("o3") != s("o1")            # not frozen: the seed re-rolls the split
