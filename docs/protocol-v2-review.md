# Protocol v2 review writes (PR7B draft)

The current draft provides per-Issue review writes through the review service,
`POST /v2/agent/runs/{run_id}/issues/{issue_number}/reviews`, and
`agent-review-v2`. V1 remains the default protocol. This document does not claim
that the remaining PR7B retry/recovery integration is complete.

## Idempotency and correction validation

The HTTP boundary checks the current token, principal, operation permission, and
repository scope before calling the review service, including on replays.
Within a short `BEGIN IMMEDIATE` Store transaction:

1. Look up `(run_id, issue_number, principal_id, idempotency_key, review)`.
2. For an existing key, compare the canonical complete payload. An identical
   payload returns the stored response; a different payload raises
   `IDEMPOTENCY_PAYLOAD_MISMATCH` (HTTP 409).
3. Only a new key checks the current review version, target evidence/attempt
   bindings, active attempt, and correction scope before appending a review.
   The existing database trigger advances the version in the same transaction.

Replays return the original response, not a projection of the current Issue.
In particular, sealing evidence after a review must not cause its original
request to be revalidated against the later evidence file set. The new-record
correction checks use the same locked database state as the INSERT, without
loading source content or consulting the current checkout. The correction
scope rules themselves are unchanged by this repair.

## Regression evidence

At `95ad229`, the added service and HTTP late-sealing replay regressions both
failed: the service rejected the correction and HTTP returned 409 instead of
replaying the original success. The repair tests also cover payload mismatch,
new-request correction guards, principal isolation, current authorization
revocation, and concurrent identical submissions using independent Store
connections. Replays and rejected requests must not append records or advance
the review version.

Local repair validation: the focused service/API suite passed all 15 tests.
The full run passed 1036 tests with three sandbox permission failures: loopback
binding and two native `/var/tmp` alias cases. Those exact three existing tests
then passed outside the sandbox without code changes (all 1039 tests covered).
Ruff, changed-file formatting, and diff whitespace checks passed. Test runs
reported one existing Starlette deprecation warning; CI is recorded separately
on the pull request for the pushed head.

Run the focused checks with:

```sh
pytest tests/test_review_service.py tests/test_api_v2.py -q
ruff check .
git diff --check
```

## Local concurrency and legacy acceptance

The follow-up adds nine acceptance cases without changing production code:

- Six independent-process checks exercise `submit_issue_review` against
  `start_attempt`, with and without retry eligibility. They force review-first,
  start-first, and simultaneous schedules. Exactly one operation commits;
  the other conflicts, and retained reports, evidence and earlier attempts stay
  unchanged. The existing short Store transactions provide this protection.
- Two checks run the existing PR5B `retry_issue_llm` service in a separate
  process using `httpx.MockTransport`, while review is submitted through the
  authenticated HTTP endpoint. Review-first prevents any mock dispatch;
  retry-first rejects the review with HTTP 409 while the attempt is active.
- One check migrates a synthetic test database, verifies V1 source review still
  works, and verifies HTTP/CLI review entrypoints cannot write the migrated
  legacy copy. The target database bytes remain unchanged and the V2 run-level
  review route remains absent.

These checks cover the local review/start/retry interaction and legacy write
boundary of 7B.5/7B.8. They do **not** exercise an HTTP retry endpoint: the
7B.7 retry/recover-unknown authorization-to-dispatch implementation and its
permission-revocation tests remain pending. The PR must remain Draft until
the remaining PR7B implementation and acceptance gates are met.

The combined execution-claim, PR5B retry, V2 API, review-service and V1-default
compatibility selection passed 83 tests (one existing Starlette warning).
Ruff, formatting of the three changed test files and diff whitespace checks
also passed. Reproduce the combined selection with:

```sh
pytest tests/test_execution_claims.py tests/test_agent_retry.py tests/test_api_v2.py \
  tests/test_review_service.py \
  tests/test_agent_cli_v2.py::test_v1_default_run_show_review_preserve_legacy_database -q
```

No real provider call, user-database migration, default V2 switch, or PR8 work
is part of these tests; all stores and transport responses are synthetic.
