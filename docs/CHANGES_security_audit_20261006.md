# Security audit — 2026-10-06

Fresh pass, part of a portfolio-wide audit, done here because this
project's last two hardening passes (Gate D1 security/robustness, Gate
D2 input validation) predate the evaluation harness (Gate D3) — new
file-handling code worth checking with a security lens specifically,
plus a dependency and deployment-config re-check.

## Found and fixed

### Evaluation harness: no path-traversal guard on manifest-declared paths
A manifest's `path` field is data someone downloads and hands to the
loader — a shared benchmark's own file, a colleague's — untrusted the
same way an upload is, not a string the code itself builds. Nothing
checked it before this audit: an absolute path, or one with a `..`
component, would be joined straight onto `--root` and read from wherever
it pointed, anywhere on the machine running the harness.

Fixed in `evaluation/splits.py`, two layers: `load_manifest` rejects an
absolute path or a `..` component structurally, before any file lookup
runs; `check_files_exist` separately resolves symlinks before checking
containment under `root`, so a path that's relative and `..`-free in the
manifest's own text still can't escape `root` via a symlink planted
underneath it. New tests (`tests/test_eval_splits.py::TestPathTraversalGuard`,
6 cases: absolute path, three `..` variants, a plain relative path and a
subdirectory path both confirmed unaffected, and a symlink-escape case)
— both halves mutation-checked (removed each in turn, confirmed the
matching test fails) before being trusted.

### Docker Compose: Redis and Flower published with no bind restriction
`docker-compose.yml` published both `redis` (6379, no authentication at
all) and `flower` (5555, Celery's monitoring dashboard, no authentication
unless explicitly configured) as `"PORT:PORT"` — which binds to every
network interface, not just the host's loopback. This is one of the most
common real-world cloud-misconfiguration classes there is: an exposed,
unauthenticated Redis instance is routinely found and abused (data theft,
`FLUSHALL`, in some configurations remote code execution) within minutes
of going live on a public IP, and an open Flower dashboard hands anyone
who finds it visibility into (and on older/misconfigured setups, control
over) the task queue. The project's own README told people to run exactly
this compose file as the standard setup path, with no warning.

Fixed: both ports now bind to `127.0.0.1` only (`"127.0.0.1:PORT:PORT"`),
which doesn't affect the `api`/`worker` containers talking to `redis`
over the internal Docker network — that never goes through the published
port at all, only host-machine access does. Added `FLOWER_BASIC_AUTH`
(optional, `user:password`) for a login on top of that. Implemented as a
shell conditional rather than always passing `--basic-auth=$VAR` even
when unset: Flower's `basic_auth` option is `multiple=True`, so an empty
value becomes a single empty-string credential — truthy, not "no auth" —
which would have silently locked out the legitimate operator too.
Verified directly (not assumed): extracted the YAML-folded command string
and ran it both with and without the env var set, confirmed the resulting
shell logic matches intent in both cases, and read Flower's own
`get_current_user()` source to confirm `if basic_auth:` really does treat
`['']` as "auth required" before writing the fix this way.

## Checked and found clean

- **Dependencies**: `pip-audit` against all three requirements files
  (`requirements.txt`, `requirements-ml.txt`, `requirements-dev.txt`) —
  zero known vulnerabilities. Unlike a committed npm lockfile, these are
  open `>=` ranges with nothing pinning them to a stale version, and
  `pip-audit` resolves the same way a real install would, so this
  reflects current reality, not a floor.
- The rest of `evaluation/`: nothing touches `eval`/`exec`/`pickle`, and
  no other path is constructed from the dataset's own label/generator
  strings — only from `root` (operator-supplied) and the now-validated
  `path`.
- Re-confirmed Gate D1/D2's findings haven't regressed (magic-byte
  validation, sanitized errors, SSRF-resistant URL fetch, rate limiting,
  decompression-bomb guard) — no code in those paths has changed since
  Gate D2 shipped, so this was a check that nothing silently drifted, not
  a re-audit from scratch.

## Not done in this pass
Gate D3 itself (running the harness on real data to actually measure
detection accuracy) is unrelated to this audit and remains open for the
reason already documented in `docs/EVALUATION_HARNESS.md` — a real
dataset, not a vulnerability.
