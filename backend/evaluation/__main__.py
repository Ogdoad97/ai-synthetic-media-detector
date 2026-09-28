"""
CLI:  python -m evaluation run  --manifest M.jsonl --root DIR --out OUT/
      python -m evaluation demo --out OUT/           (toy data, plumbing check only)
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from .demo_data import make_demo_dataset
from .report import render_markdown
from .runner import run_evaluation
from .scorers import BaselineScorer
from .splits import (
    DEFAULT_FRACTIONS, ManifestError, assign_splits, check_files_exist, load_frozen_splits,
    load_manifest, manifest_sha256, write_frozen_splits,
)


def _fractions(text: str):
    parts = tuple(float(x) for x in text.split(","))
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("--split needs three numbers: train,val,test")
    return parts


def _execute(manifest: Path, root: Path, out: Path, args, demo: bool) -> int:
    samples, warns = load_manifest(manifest)
    check_files_exist(samples, root)
    mhash = manifest_sha256(manifest)
    heldout = [g for g in (args.heldout or "").split(",") if g]
    if args.frozen_splits:
        split_of = load_frozen_splits(Path(args.frozen_splits), samples, mhash)
    else:
        split_of = assign_splits(samples, args.seed, args.split, heldout)
    out.mkdir(parents=True, exist_ok=True)
    write_frozen_splits(out / "splits.json", samples, split_of, mhash, args.seed, args.split, heldout)
    rep = run_evaluation(
        samples, root, BaselineScorer(), target_fpr=args.target_fpr, seed=args.seed,
        fractions=args.split, heldout_generators=heldout,
        perturb={} if args.no_perturb else None, n_boot=args.n_boot, split_of=split_of,
        manifest_sha256=mhash, manifest_warnings=warns, demo=demo)
    (out / "report.json").write_text(json.dumps(rep, indent=1))
    (out / "report.md").write_text(render_markdown(rep))
    print(f"wrote {out/'report.md'}, {out/'report.json'}, {out/'splits.json'}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="evaluation", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "demo"):
        p = sub.add_parser(name)
        p.add_argument("--out", required=True, type=Path)
        p.add_argument("--target-fpr", type=float, default=0.01)
        p.add_argument("--seed", type=int, default=1337)
        p.add_argument("--split", type=_fractions, default=DEFAULT_FRACTIONS,
                       help="train,val,test fractions (default 0,0.3,0.7: the baseline is training-free)")
        p.add_argument("--heldout", default="", help="comma-separated synthetic generators to hold out")
        p.add_argument("--frozen-splits", help="reuse a splits.json from an earlier run")
        p.add_argument("--no-perturb", action="store_true")
        p.add_argument("--n-boot", type=int, default=1000)
        if name == "run":
            p.add_argument("--manifest", required=True, type=Path)
            p.add_argument("--root", required=True, type=Path)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "demo":
            with tempfile.TemporaryDirectory() as tmp:
                manifest = make_demo_dataset(Path(tmp))
                if not args.heldout:
                    args.heldout = "toygen_b"
                return _execute(manifest, Path(tmp), args.out, args, demo=True)
        return _execute(args.manifest, args.root, args.out, args, demo=False)
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
