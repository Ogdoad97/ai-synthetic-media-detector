# Media Privacy and Retention

**Status: operational review required before public deployment.** This document records the current architecture and the safeguards that must be verified. It does not claim that a production privacy audit has passed.

## Data handled

The API may receive uploaded images/videos or public URLs. Upload endpoints write media to `TEMP_MEDIA_DIR`; Celery workers read the files and are intended to remove them in a `finally` block. URL analysis downloads remote media to a randomly named temporary file and also attempts cleanup in a `finally` block. Analysis results may include scores, findings, heatmap data, source URL, original filename, and frame-level results. Celery's result backend stores task results according to its configuration and expiry policy.

## Known risk requiring action

The current `GET /v1/tasks/{task_id}` route retrieves Celery task status/results without user authentication or ownership checks. A task identifier is not a substitute for access control. Until an authentication/authorization design is implemented and tested, do not expose this endpoint on an unrestricted public deployment if task results may contain sensitive media-derived information.

## Required controls

1. **Minimise retention:** configure a bounded Celery result expiry appropriate to the service, document the effective value, and ensure temporary media is removed after success, failure, timeout, and worker cancellation.
2. **Access control:** authenticate users or use unguessable, short-lived, scoped access tokens; verify that callers may access the specific task result. Do not rely solely on obscurity of task IDs.
3. **Data minimisation:** avoid logging uploaded content, full sensitive URLs, credentials embedded in URLs, or unnecessary personal data. Review exception and access logs.
4. **Deployment separation:** keep temporary media and result stores private to the service network; do not expose Redis or Flower publicly. Restrict dashboard access.
5. **User notice:** explain what is processed, why, how long it is retained, whether third-party services receive it, and how users can request deletion where applicable.
6. **Cleanup verification:** add tests for cleanup after success, failure, timeout, and cancellation; verify behaviour with actual Celery workers as well as unit mocks.
7. **Incident response:** document who handles suspected data exposure and how credentials, task access, and affected data are contained.

## Acceptance evidence

Do not mark this area complete until there is a reviewed data-flow diagram, configured and tested result expiry, task-result access-control tests, cleanup regression tests, and deployment verification. Link the evidence to TRUST-08 and TRUST-09 in [Safety and Trust Requirements](SAFETY_AND_TRUST_REQUIREMENTS.md).

## Current limitation

This repository's documented test and security hardening history does not, by itself, establish that all the controls above exist or that the service is compliant with any particular privacy law. Deployment owners must assess applicable legal obligations for their operating context.
