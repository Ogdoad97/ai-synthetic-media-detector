"""
Gate D3 evaluation harness.

Turns "a heuristic signal" into a *measured* detector: fixed splits decided
before any tuning, an operating threshold chosen on validation data only
and then frozen, cross-generator and robustness reporting, and honest
uncertainty (confidence intervals, resolution warnings).

Run from `backend/`:   python -m evaluation --help

Deliberately outside `app/`: it is a development tool, not part of the
API's request path, and pulls in no dependency the API doesn't already have.
"""
HARNESS_VERSION = "1.0.0"
