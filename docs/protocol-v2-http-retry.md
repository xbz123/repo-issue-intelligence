# PR7B: explicit HTTP analysis retry

Scope: R5 7B.7 / A30 and the HTTP concurrency integration of 7B.5 / A49,
based on merged PR75 (`4b1ab24`). This does not switch the V1 default, add
background execution, change the database schema or complete G1.

## Contract

`POST /v2/agent/runs/{run_id}/issues/{issue_number}/llm-retry` accepts only
`{}` (confirmed-failure retry) or `{"recover_unknown": true}`. The flag is a
strict boolean, not a string or number. Unknown recovery is explicit because
the earlier remote call may have run; another attempt can duplicate cost or
remote execution. Success never establishes remote exactly-once behavior.

Database, principal, provider, model, endpoint, credentials and budgets cannot
be supplied in the body. The database is selected by `RII_API_V2_DATABASE`;
the analyzer uses current server Settings and must match the frozen run's
configuration and engine/runtime. Runs originally using incompatible CLI
overrides require matching server configuration or a new run, not silent
adoption of the current defaults.

Enable only the required operation in `RII_API_OPERATIONS`: `retry` and
`recover-unknown` are separate permissions, absent from the defaults. Add an
explicit `RII_API_EXTERNAL_GRANTS` entry matching the server principal,
canonical analysis root, provider and the specific operation. A `retry` grant
does not permit unknown recovery. Existing token, Host/Origin, scope, private
database and body-size checks still apply. No grant is created by this change.

The adapter rechecks the original bearer token, current principal, operation,
scope and provider grant before PR5B execution, before each attempt claim,
immediately before analyzer dispatch and before returning metadata. Automatic
429/5xx backoff does not reuse an earlier permission decision. Server analyzer
configuration changes during a request also stop further sends. PR5B receives
only a callback; it imports no HTTP or authentication implementation.

Revocation before execution preserves retained state. Revocation after an
attempt was claimed but before dispatch terminalizes that unsent attempt as
a local failure. Revocation during an already-dispatched call cannot retract
evidence or cancel a remote call; retained results are preserved, subsequent
dispatch is blocked, and response authorization is rechecked. Policy-file
changes take effect at the next check, not as a transaction with a remote
provider. Shell environment changes require restarting the server.

Execution is synchronous (200, not a background 202). The response contains
only run/Issue IDs, LLM state, selected attempt/evidence IDs, review version
and the duplicate-execution warning flag; no source, report, analysis or raw
provider error. State conflicts and frozen-configuration mismatch are 409;
unknown resources 404, invalid bodies 422, revoked authentication 401/403,
unsafe stores 503. A busy process-local work slot returns 503 with Retry-After;
the database writer lock also rejects cross-process executors with 409.

Review can still run concurrently: its atomic Store eligibility checks reject
an active retry with 409, and a committed review prevents retry. Saved reports,
sealed evidence and prior attempts are never overwritten. Unknown recovery
retains PR5B's execution-guard requirements; an active/unverifiable prior call
cannot be reclaimed. Migrated legacy history is not a V2 retry target.

## Validation

The initial two route tests failed with 404 before implementation and passed
afterwards. Tests use synthetic repositories/private databases and
`httpx.MockTransport` only; no real model call or evidence transfer was made.
The HTTP suite covers permission revocation before execution, after claim and
during backoff, frozen-input reuse, configuration/body rejection and independent
process review-first, retry-first and two-retry schedules.

Local full-suite verification: 1209 passed, one existing Starlette deprecation
warning (307.16 seconds). This includes 44 HTTP retry cases and four added
work-slot routing cases. Whole-repository Ruff and `git diff --check` passed.
The earlier affected selection had three sandbox permission failures (loopback
binding and `/var/tmp` aliases); those passed with the required local permissions,
and the final full run also passed them without production-code changes.

Run the affected integration selection with:

```sh
pytest tests/test_api_retry.py tests/test_api_security.py tests/test_api_v2.py \
  tests/test_agent_retry.py tests/test_agent_resume.py tests/test_execution_claims.py \
  tests/test_review_service.py tests/test_agent_cli_v2.py -q
```

CI and independent review evidence belong to the delivery PR;
passing these checks does not complete G1 migration, release or rollback gates.
