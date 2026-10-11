# Safety and Trust Requirements

This document turns the Public Benefit Charter into engineering work. Statuses describe the known project state from the README and contribution guidance; they are not a claim that a new audit or test run has occurred.

## Status legend
- **Existing:** described as present in current project documentation; re-verify during release.
- **Partial:** some protection exists, but a documented gap remains.
- **Open:** implementation or evidence is still needed.

## Requirements register

| ID | Requirement | Current status | Acceptance evidence |
|---|---|---|---|
| TRUST-01 | Every API result and user-facing surface must make clear that the current heuristic is experimental and not proof of origin. | Partial | API/schema tests and UI copy tests verify warnings in relevant outputs. |
| TRUST-02 | Do not claim detector accuracy until a licensed, documented, held-out real-vs-generated evaluation has been run. | Open | Reproducible report with dataset provenance/licence, class balance, per-class metrics, ROC/PR curves, calibration where applicable, and limitations. Keep detector.validated false until a reviewed gate passes. |
| TRUST-03 | Test false positives and false negatives across relevant media types, generators, transformations, and contextual conditions where suitable datasets exist. | Open | Evaluation plan records coverage, known gaps, confidence intervals where possible, and limitations. |
| TRUST-04 | Treat missing, invalid, and valid provenance as distinct states; do not equate absent credentials with synthetic media. | Partial | C2PA integration tests for absent, valid, invalid, malformed, and unsupported credentials. Current README says parsing is scaffolded and returns unknown. |
| TRUST-05 | Keep remote URL fetching resistant to SSRF, DNS rebinding, redirects to private addresses, and unsafe protocols. | Existing | Adversarial tests and security review for every safe-fetch change. |
| TRUST-06 | Validate uploaded content using file signatures and size/decompression limits; return sanitised errors. | Existing | Tests for mismatched content type, malformed files, decompression bombs, oversized inputs, and errors that do not leak internals. |
| TRUST-07 | Rate limiting must work across API replicas before horizontal scaling. | Partial | Redis-backed shared limiter and multi-instance tests; document the current in-memory limitation until complete. |
| TRUST-08 | Avoid retaining uploaded media longer than necessary; define cleanup, logging, and retention behaviour. | Partial | Documented data flow and retention policy plus cleanup tests for success, failure, and cancellation. Review logs for content/secret leakage. |
| TRUST-09 | Protect task IDs, results, and stored media from unauthorised access. | Open | Threat model, access-control tests, and deployment review. Do not expose sensitive task results through guessable or unauthenticated identifiers. |
| TRUST-10 | Secrets and service dashboards must not be exposed on public interfaces without authentication. | Existing / verify | Deployment tests confirm Redis/Flower binding and authentication settings; secret scanning is included in CI. |
| TRUST-11 | Changes to security boundaries include threat-model notes and adversarial regression tests. | Existing | PR checklist completed for URL fetching, uploads, parsing, rate limiting, and worker handling. |
| TRUST-12 | Publish known limitations and evaluation results without implying certainty beyond evidence. | Existing / ongoing | README and evaluation report updated during each release review. |
| TRUST-13 | Provide a responsible route for security reports without requiring public disclosure of sensitive exploit details. | Partial | Private reporting instructions and maintained response workflow; current contribution guidance says no formal disclosure process exists. |
| TRUST-14 | Detector changes are reproducible and include regression tests. | Partial | Versioned model/data references where licensing permits, pinned dependencies, repeatable evaluation commands, and regression report. |
| TRUST-15 | Do not use private or unlicensed media datasets without appropriate permission and provenance records. | Open | Dataset register documents source, licence/permission, intended use, retention, and restrictions. |

## Release gates
A release must not be described as a scientifically validated detector unless TRUST-02 has passed review. A security hardening pass is not evidence of detection accuracy.

Before each release:
1. Run unit, integration, and adversarial input tests.
2. Review dependency and secret-scanning results.
3. Verify warnings and limitations are visible in API documentation and relevant clients.
4. Check temporary-file cleanup, task/result access, and deployment defaults.
5. Record unresolved requirements and residual risks in release notes.
6. Confirm public claims do not exceed measured evidence.

## Prioritised implementation plan

### P0 — Integrity and baseline
- Preserve detector.validated=false until a documented evaluation gate passes.
- Add or verify tests for uncertainty/limitation messaging in API responses.
- Add secret scanning to CI and review tracked files/history for accidental credentials; rotate exposed credentials.
- Document media retention, deletion, and task-result access behaviour.

### P1 — Evidence and privacy
- Run python -m evaluation on a properly licensed dataset, document provenance, and publish the report.
- Add cleanup tests for worker success, failure, and cancellation.
- Review task/result endpoints for access control and privacy risks.
- Add a dataset register and repeatable evaluation instructions.

### P2 — Deployment and provenance
- Implement shared Redis-backed rate limiting before multi-replica deployment.
- Implement C2PA parsing with explicit valid/invalid/absent/unknown states.
- Establish a private vulnerability-reporting route and response workflow.

## Traceability
Link each requirement ID to the relevant issue, pull request, test, or report when implementation begins. Update statuses only when evidence exists. This file is not evidence that a requirement has passed.