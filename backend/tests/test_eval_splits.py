"""Gate D3: manifest validation, deterministic group-aware splits, split freezing."""
import json
import random
from collections import Counter

import pytest

from evaluation.splits import (
    ManifestError, Sample, assert_no_group_leakage, assign_splits, check_files_exist,
    load_frozen_splits, load_manifest, manifest_sha256, write_frozen_splits,
)


def mk(n_groups=300, per_group=3):
    out = []
    for g in range(n_groups):
        for k in range(per_group):
            gen = ["real:cam", "gen_a", "gen_b"][k % 3]
            out.append(Sample(f"g{g}_{k}.png", f"g{g}_{k}.png", 0 if gen.startswith("real") else 1, gen, "image", f"grp{g}"))
    return out


def write_manifest(tmp_path, rows, name="m.jsonl"):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p


def row(path="a.png", label=0, gen="real", **kw):
    return {"path": path, "label": label, "generator": gen, **kw}


class TestAssignment:
    def test_deterministic_and_independent_of_row_order(self):
        samples = mk()
        a = assign_splits(samples, seed=1)
        shuffled = samples[:]
        random.Random(0).shuffle(shuffled)
        assert assign_splits(shuffled, seed=1) == a
        assert assign_splits(samples, seed=1) == a

    def test_adding_samples_never_moves_existing_groups(self):
        before = assign_splits(mk(200), seed=3, fractions=(0.5, 0.2, 0.3))
        after = assign_splits(mk(400), seed=3, fractions=(0.5, 0.2, 0.3))
        assert all(after[k] == v for k, v in before.items())

    def test_samples_sharing_a_group_share_a_split(self):
        samples = mk()
        split_of = assign_splits(samples, seed=9, fractions=(0.4, 0.3, 0.3))
        by_group = {}
        for s in samples:
            by_group.setdefault(s.group, set()).add(split_of[s.id])
        assert all(len(v) == 1 for v in by_group.values())
        assert_no_group_leakage(samples, split_of)

    def test_fractions_are_respected_over_many_groups(self):
        samples = mk(3000, per_group=1)
        counts = Counter(assign_splits(samples, seed=2, fractions=(0.5, 0.2, 0.3)).values())
        for name, want in (("train", .5), ("val", .2), ("test", .3)):
            assert counts[name] / 3000 == pytest.approx(want, abs=0.03)

    def test_default_gives_training_free_detector_no_train_split(self):
        assert "train" not in set(assign_splits(mk(500, 1), seed=1).values())

    def test_seed_changes_the_assignment(self):
        s = mk()
        assert assign_splits(s, seed=1) != assign_splits(s, seed=2)

    def test_heldout_generators_go_only_to_test_unseen(self):
        samples = mk()
        split_of = assign_splits(samples, seed=4, heldout_generators=["gen_b"])
        for s in samples:
            assert (split_of[s.id] == "test_unseen") == (s.generator == "gen_b")
        assert_no_group_leakage(samples, split_of)      # held-out generators are exempt by design

    def test_invalid_configuration_is_rejected(self):
        s = mk(10)
        with pytest.raises(ManifestError):
            assign_splits(s, fractions=(0.5, 0.5, 0.5))
        with pytest.raises(ManifestError):
            assign_splits(s, fractions=(-0.1, 0.6, 0.5))
        with pytest.raises(ManifestError):
            assign_splits(s, heldout_generators=["nope"])
        with pytest.raises(ManifestError):
            assign_splits(s, heldout_generators=["real:cam"])

    def test_leakage_check_catches_a_group_split_across_sets(self):
        s = [Sample("a", "a", 0, "real", "image", "G"), Sample("b", "b", 1, "gen", "image", "G")]
        with pytest.raises(ManifestError, match="leakage"):
            assert_no_group_leakage(s, {"a": "val", "b": "test"})


class TestManifest:
    def test_loads_jsonl_and_csv(self, tmp_path):
        rows = [row("a.png", 0, "real:x", group="g1"), row("b.png", 1, "genA", group="g2", media_type="video")]
        samples, warns = load_manifest(write_manifest(tmp_path, rows))
        assert [(s.id, s.label, s.generator, s.media_type, s.group) for s in samples] == \
               [("a.png", 0, "real:x", "image", "g1"), ("b.png", 1, "genA", "video", "g2")]
        assert warns == []
        csvp = tmp_path / "m.csv"
        csvp.write_text("path,label,generator,group\na.png,0,real,g1\nb.png,1,genA,g2\n")
        assert len(load_manifest(csvp)[0]) == 2

    def test_missing_group_column_warns_about_leakage(self, tmp_path):
        _, warns = load_manifest(write_manifest(tmp_path, [row("a.png"), row("b.png", 1, "g")]))
        assert warns and "group" in warns[0]

    @pytest.mark.parametrize("bad,match", [
        (row(label=2), "label must be"),
        (row(label=0, gen="genA"), "contradicts"),
        (row(label=1, gen="real"), "contradicts"),
        (row(media_type="audio"), "media_type"),
        ({"path": "a.png", "label": 0}, "needs path"),
        ({"path": "a.png", "label": "x", "generator": "real"}, "needs path"),
    ])
    def test_invalid_rows(self, tmp_path, bad, match):
        with pytest.raises(ManifestError, match=match):
            load_manifest(write_manifest(tmp_path, [bad]))

    def test_duplicate_path_empty_and_bad_json(self, tmp_path):
        with pytest.raises(ManifestError, match="duplicate"):
            load_manifest(write_manifest(tmp_path, [row("a.png"), row("a.png")]))
        p = tmp_path / "e.jsonl"; p.write_text("\n")
        with pytest.raises(ManifestError, match="empty"):
            load_manifest(p)
        p.write_text('{"path": "a"\n')
        with pytest.raises(ManifestError, match="invalid JSON"):
            load_manifest(p)

    def test_missing_files_are_reported(self, tmp_path):
        (tmp_path / "a.png").write_bytes(b"x")
        samples, _ = load_manifest(write_manifest(tmp_path, [row("a.png"), row("gone.png")]))
        with pytest.raises(ManifestError, match="1 manifest file.*gone.png"):
            check_files_exist(samples, tmp_path)


class TestFreezing:
    def test_roundtrip_and_refusals(self, tmp_path):
        samples, _ = load_manifest(write_manifest(tmp_path, [row("a.png", group="g1"), row("b.png", 1, "gen", group="g2")]))
        h = manifest_sha256(tmp_path / "m.jsonl")
        split_of = assign_splits(samples, seed=5)
        f = tmp_path / "splits.json"
        write_frozen_splits(f, samples, split_of, h, 5, (0, .3, .7), [])
        assert load_frozen_splits(f, samples, h) == split_of

        with pytest.raises(ManifestError, match="different manifest"):
            load_frozen_splits(f, samples, "0" * 64)
        with pytest.raises(ManifestError, match="exactly"):
            load_frozen_splits(f, samples[:1], h)
        payload = json.loads(f.read_text()); payload["splits"]["ghost.png"] = "test"
        f.write_text(json.dumps(payload))
        with pytest.raises(ManifestError, match="exactly"):
            load_frozen_splits(f, samples, h)

    def test_any_edit_to_the_manifest_changes_its_hash(self, tmp_path):
        p = write_manifest(tmp_path, [row("a.png")])
        h1 = manifest_sha256(p)
        write_manifest(tmp_path, [row("a.png", group="x")])
        assert manifest_sha256(p) != h1
