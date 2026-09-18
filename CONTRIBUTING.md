# Contributing

Thanks for considering contributing. This project is an experimental,
honestly-scoped forensic media detector — see the README's "Important
scientific status" section before anything else: the current analyzer is
a deterministic heuristic, not a validated AI detector, and PRs should
keep that framing intact rather than overstate accuracy.

## Ground rules

- **No accuracy claims without evidence.** If you add or change a
  detection signal, it needs to keep or improve determinism and needs a
  test. Don't tune thresholds to make demo images look good without
  checking what that does to the false-positive rate on clean media.
- **Every input-handling change needs an adversarial test**, not just a
  happy-path one. `tests/test_safe_fetch.py` and `tests/test_analyzer.py`
  (the decompression-bomb tests specifically) are the pattern to follow.
- **Keep `requirements.txt` lean.** If your change needs a new heavy
  dependency (a model framework, a large library) that isn't used by
  the live request path yet, it likely belongs in its own
  `requirements-*.txt` the way `requirements-ml.txt` does for
  torch/torchvision — see `docs/AUDIT_HARDENING_REPORT_v0.3.0.md` for why.
- **Security-relevant changes** (anything touching `app/core/safe_fetch.py`,
  `app/core/rate_limit.py`, or upload/URL handling in `app/api/routes.py`)
  should include a short note in the PR description explaining the threat
  model, not just the change — a reviewer shouldn't have to reverse-engineer
  what attack a fix addresses.

## Local setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# Only if you're working on the (currently unused) Grad-CAM path:
# pip install -r requirements-ml.txt

PYTHONPATH=. pytest -v
```

## What's genuinely useful to work on right now

See the README's "Next Steps" section and
`docs/AUDIT_HARDENING_REPORT_v0.3.0.md`'s "Next gate" for the current
honest list. In rough priority order:

1. **File magic-byte validation on uploads** — currently trusts the
   client-supplied `Content-Type` header, which is trivially spoofable.
   Good first issue: self-contained, testable, doesn't touch the
   forensic logic.
2. **A real evaluation harness** — fixed train/val/test splits,
   cross-generator test sets, ROC-AUC/PR-AUC/calibration. This is the
   single most valuable contribution possible: it's what turns "a
   heuristic signal" into an actual measured detector.
3. **C2PA provenance parsing** — currently scaffolded as
   `"c2pa_status": "unknown"` everywhere; wiring in real Content
   Credentials parsing would be a genuinely new capability, not a fix.
4. **Redis-backed rate limiting** — the current limiter is
   correct but in-memory/single-instance (see `app/core/rate_limit.py`'s
   own docstring); needed before this runs behind more than one API
   replica.
5. **Web dashboard / Chrome extension clients** — `/v1/analyze/url`
   already exists specifically for this; nothing consumes it yet.

## Reporting a security issue

If you find a real vulnerability (in the spirit of the DNS-rebinding fix
in v0.3.0), please open an issue describing the threat model — this
project doesn't have a formal disclosure process yet, so a normal GitHub
issue is fine for now, but flag it as security-relevant in the title.
