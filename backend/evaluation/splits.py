"""
Dataset manifest, deterministic group-aware splits, and split freezing.

The rule this module enforces: the split is decided from the *data's
identity* (a hash of its group id and a seed) - never from scores, never
from results - so it cannot be re-rolled after seeing how a detector did.
"""
from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

VALID_MEDIA_TYPES = {"image", "video"}
# (train, val, test). The only detector today is training-free, so the
# default gives no data to "train"; pass e.g. (0.5, 0.2, 0.3) once a
# trained model (Gate D4) needs it.
DEFAULT_FRACTIONS: Tuple[float, float, float] = (0.0, 0.3, 0.7)

SPLIT_TRAIN, SPLIT_VAL, SPLIT_TEST, SPLIT_UNSEEN = "train", "val", "test", "test_unseen"


class ManifestError(ValueError):
    """The manifest or split configuration is invalid."""


@dataclass(frozen=True)
class Sample:
    id: str                 # unique; the manifest path
    path: str
    label: int              # 0 real, 1 synthetic
    generator: str          # "real" / "real:<source>" or a generator name
    media_type: str = "image"
    group: str = ""         # samples sharing a group always land in one split


def _is_real_generator(name: str) -> bool:
    return name == "real" or name.startswith("real:")


def load_manifest(path: Path) -> Tuple[List[Sample], List[str]]:
    """
    Load a .jsonl or .csv manifest. Columns: path, label, generator
    (required); media_type, group (optional). Returns (samples, warnings).
    """
    path = Path(path)
    if path.suffix.lower() == ".csv":
        with open(path, newline="") as f:
            rows = list(csv.DictReader(f))
    else:
        rows = []
        with open(path) as f:
            for n, line in enumerate(f, 1):
                if line.strip():
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError as exc:
                        raise ManifestError(f"{path.name}:{n}: invalid JSON ({exc.msg})") from None
    if not rows:
        raise ManifestError("manifest is empty")

    warnings: List[str] = []
    samples: List[Sample] = []
    seen: Set[str] = set()
    has_group = any((r.get("group") or "").strip() for r in rows)
    for i, r in enumerate(rows, 1):
        try:
            p = str(r["path"]).strip()
            label = int(r["label"])
            gen = str(r["generator"]).strip()
        except (KeyError, ValueError, TypeError):
            raise ManifestError(f"row {i}: needs path, integer label, generator") from None
        if not p or not gen:
            raise ManifestError(f"row {i}: empty path or generator")
        if label not in (0, 1):
            raise ManifestError(f"row {i}: label must be 0 (real) or 1 (synthetic), got {label}")
        if (label == 0) != _is_real_generator(gen):
            raise ManifestError(
                f"row {i}: label {label} contradicts generator '{gen}' "
                "(real samples must be label 0 with generator 'real' or 'real:<source>')"
            )
        media = str(r.get("media_type") or "image").strip().lower()
        if media not in VALID_MEDIA_TYPES:
            raise ManifestError(f"row {i}: media_type must be image or video, got '{media}'")
        if p in seen:
            raise ManifestError(f"row {i}: duplicate path '{p}'")
        seen.add(p)
        group = (r.get("group") or "").strip() or p
        samples.append(Sample(p, p, label, gen, media, group))

    if not has_group:
        warnings.append(
            "No 'group' column: every file is its own group, so near-duplicates or "
            "images derived from the same source/prompt can land in different splits "
            "and inflate results. Add a group id (source image, prompt, or video id)."
        )
    return samples, warnings


def check_files_exist(samples: Sequence[Sample], root: Path) -> None:
    missing = [s.path for s in samples if not (Path(root) / s.path).is_file()]
    if missing:
        head = ", ".join(missing[:5])
        raise ManifestError(f"{len(missing)} manifest file(s) not found under root, e.g. {head}")


def manifest_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _unit_interval(group: str, seed: int) -> float:
    digest = hashlib.sha256(f"{seed}:{group}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def assign_splits(
    samples: Sequence[Sample],
    seed: int = 1337,
    fractions: Tuple[float, float, float] = DEFAULT_FRACTIONS,
    heldout_generators: Iterable[str] = (),
) -> Dict[str, str]:
    """
    Deterministic {sample id: split}. Depends only on (group, seed,
    fractions): independent of row order, and adding new samples never
    moves an existing group. Generators in `heldout_generators` are held
    out of val entirely (split 'test_unseen') so they can measure
    cross-generator generalization with a threshold that never saw them.
    """
    tr, va, te = fractions
    if min(fractions) < 0 or abs(tr + va + te - 1.0) > 1e-9:
        raise ManifestError("split fractions must be non-negative and sum to 1")
    heldout = set(heldout_generators)
    known = {s.generator for s in samples}
    unknown = heldout - known
    if unknown:
        raise ManifestError(f"held-out generator(s) not in manifest: {sorted(unknown)}")
    if any(_is_real_generator(g) for g in heldout):
        raise ManifestError("real sources cannot be held out; hold out synthetic generators only")

    out: Dict[str, str] = {}
    for s in samples:
        if s.generator in heldout:
            out[s.id] = SPLIT_UNSEEN
            continue
        u = _unit_interval(s.group, seed)
        out[s.id] = SPLIT_TRAIN if u < tr else SPLIT_VAL if u < tr + va else SPLIT_TEST
    return out


def assert_no_group_leakage(samples: Sequence[Sample], split_of: Dict[str, str]) -> None:
    """Every group lives in exactly one split (held-out generators excepted, by design)."""
    where: Dict[str, str] = {}
    for s in samples:
        sp = split_of[s.id]
        if sp == SPLIT_UNSEEN:
            continue
        if where.setdefault(s.group, sp) != sp:
            raise ManifestError(f"group '{s.group}' spans multiple splits (leakage)")


def write_frozen_splits(path: Path, samples: Sequence[Sample], split_of: Dict[str, str],
                        manifest_hash: str, seed: int, fractions, heldout: Iterable[str]) -> None:
    payload = {
        "manifest_sha256": manifest_hash, "seed": seed,
        "fractions": list(fractions), "heldout_generators": sorted(heldout),
        "splits": {s.id: split_of[s.id] for s in samples},
    }
    Path(path).write_text(json.dumps(payload, indent=1, sort_keys=True))


def load_frozen_splits(path: Path, samples: Sequence[Sample], manifest_hash: str) -> Dict[str, str]:
    """Reuse a previously frozen split; refuses if it doesn't match this manifest exactly."""
    payload = json.loads(Path(path).read_text())
    if payload.get("manifest_sha256") != manifest_hash:
        raise ManifestError("frozen splits were made for a different manifest (hash mismatch)")
    splits = payload["splits"]
    ids = {s.id for s in samples}
    if set(splits) != ids:
        raise ManifestError("frozen splits do not cover exactly the manifest's samples")
    return dict(splits)
