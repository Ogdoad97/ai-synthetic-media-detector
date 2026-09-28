# Evaluation harness (Gate D3)

`backend/evaluation/` turns "a heuristic score" into something you can make
measured claims about. It is a development tool, not part of the API.

> **Status:** built and tested (139 tests, 12 protocol safeguards
> mutation-checked). **Not yet run on a real dataset.** The bundled `demo`
> command uses procedurally generated toy images and its report is stamped
> "NOT A MEASUREMENT". Any number from it says nothing about real detection.

## Run it

From `backend/` (`pip install -r requirements-dev.txt` for the tests):

```
python -m evaluation demo --out reports/demo                 # plumbing check only
python -m evaluation run --manifest data/manifest.jsonl --root data/ \
       --out reports/2026-xx-xx --heldout gen_c --target-fpr 0.01
```

Outputs: `report.md` (human), `report.json` (source of truth), `splits.json`
(the frozen split; pass it back with `--frozen-splits` to re-evaluate a new
detector version on identical data).

## Manifest

JSONL (or CSV), one row per file:

| field | required | meaning |
|---|---|---|
| `path` | yes | relative to `--root`; must be unique |
| `label` | yes | `0` real, `1` synthetic |
| `generator` | yes | `real` or `real:<source>` for real media (must be label 0), otherwise the generator name |
| `media_type` | no | `image` (default) or `video`; evaluated separately |
| `group` | **strongly advised** | source image / prompt / video id. Everything in one group lands in one split. Without it, near-duplicates can leak across splits and inflate results (the report warns) |

## Protocol (what keeps it honest)

1. **Splits come from data identity** (SHA-256 of `seed:group`), never from
   scores. Independent of row order; adding data never moves existing groups.
   Default `0/0.3/0.7` (train/val/test) because the baseline is training-free;
   use e.g. `--split 0.5,0.2,0.3` once a trained model (Gate D4) exists.
2. **Threshold is chosen on validation *real* samples only**, at a target FPR,
   then frozen. Nothing from test feeds back into any choice.
3. **Everything is reported at that frozen threshold *and* at the deployed
   API threshold** (0.60), so you see what users actually get.
4. **Held-out generators** (`--heldout`) are excluded from validation entirely
   and scored against the same real test pool: a cross-generator check.
5. **Robustness**: test images are re-scored after JPEG q90/70/50, 50%
   downscale, resize round-trip, 80% crop, blur, and noise, with the
   threshold still frozen from *clean* validation.
6. **Uncertainty is shown**: bootstrap CI on AUC, Wilson CI on TPR/FPR, and
   warnings for: target FPR finer than the validation set can resolve, tiny
   test sets, a single generator, no held-out generator, missing `group`
   column, unscorable files (excluded and counted by class), and AUC < 0.5.

## What a report can and cannot support

It supports claims about *that dataset, those generators, those transforms*.
It does not support "detects AI images" in general. A held-out generator tests
one kind of generalization; the next generator can still fail. If the result
is poor, publish it anyway: an honest "near chance under JPEG" is the point of
this gate.

## Choosing data (not done for you)

You need real photos with a clear licence plus outputs from several
generators, ideally with matched content. Published benchmarks exist for this
(e.g. GenImage, Synthbuster, ArtiFact); check each one's current licence,
availability and known biases before use, and record the provenance in the
report. Avoid very small or low-resolution sets (e.g. 32x32 thumbnails):
forensic signals at that size are not representative of real uploads.
