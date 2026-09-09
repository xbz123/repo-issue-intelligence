# PR6: trusted-local API security

Scope: R5 6.1–6.9, based on PR70 merged as `bf17ac6`. G0 and PR1A are already
available. This is a deliberate security change to the existing V1 HTTP API,
not a default V2 switch. PR7A now adds [V2 query/evidence reads](protocol-v2-api-query.md);
review/retry HTTP writes remain PR7B work.
The authorization service is available for their later integration; it does not
send a provider request or replace the CLI's current external-transfer consent.

## Supported deployment and identity

Use `rii serve` on a trusted POSIX workstation, one server process. It accepts
literal loopback IPs (or `localhost`, pinned to `127.0.0.1`), forces one worker
even when `WEB_CONCURRENCY` is set, and disables proxy-header identity trust and
request access logs. Startup reports the OS UID as the local launcher identity;
this is not a human identity or a new review record. Development reload is not
a durable background-execution service. Direct custom Uvicorn launches, reverse
proxies, remote/multi-user deployment and multiple server processes are not the
supported boundary.

Every HTTP path except `/health` requires one `Authorization: Bearer ...` header,
including legacy write/read routes and OpenAPI documentation. Tokens are supplied
only by server secret configuration, must be at least 32 ASCII non-whitespace
characters, and are compared in constant time. Missing/invalid tokens return
401 before body parsing or Store creation. Cookies, query parameters, body
reviewer/principal fields and forwarded-user headers cannot authenticate.
Validation responses do not echo rejected input. Request bodies reject unknown
fields; Issue URLs reject userinfo, query and fragment rather than retaining
potential credentials.

`RII_API_PRINCIPAL` is a server-derived logical identity for the shared token.
It is not proof of distinct people. The token is never stored as a principal or
passed to run/attempt services. The existing V1 review representation remains V1;
per-Issue principal-bound review records are not claimed before PR7B.

## Configuration

Use a protected environment or a private `.env` file; do not commit credentials.

| Setting | Default / meaning |
|---|---|
| `RII_API_TOKEN` | unset: sensitive HTTP is disabled |
| `RII_API_PRINCIPAL` | `local-operator`; server identity, not a body field |
| `RII_API_ANALYSIS_ROOTS` | `[]`; JSON array of absolute canonical source roots |
| `RII_API_OPERATIONS` | `["index","run","read","review","evidence"]`; allowed operations |
| `RII_API_ORIGINS` | `[]`; additional exact loopback origins accepted by the header policy |
| `RII_API_EXTERNAL_GRANTS` | `[]`; explicit principal/root/provider/operation grants |
| `AGENT_DB_PATH` | existing legacy DB setting; HTTP additionally enforces private storage |

Configuration is read on each request/authorization check. A private `.env`
change is visible to subsequent checks; changes to a shell's environment require
restarting the server. Revoking roots, operations or transfer grants denies
subsequent operations. PR7B must authenticate and authorize again immediately
before retry/recovery dispatch; this PR tests decisions, not that future endpoint.

Hosts must identify loopback. A supplied Origin must be the exact request origin
or an explicitly allowed loopback origin. Duplicate Host/Authorization/Origin
headers and Forwarded/X-Forwarded-* headers are rejected. No cookies or permissive
CORS headers are installed; adding an origin to the header allowlist does not
enable a cross-origin browser UI. Use an explicit Authorization header, including
when fetching `/openapi.json`.

An example transfer grant (not enabled by default) is:

```json
{"principal":"local-operator","analysis_root":"/path/to/repo/src","provider":"opencode","operation":"retry"}
```

The corresponding operation must also be enabled. Provider must match current
server configuration; unsafe configured API URLs are refused through the existing
endpoint validator. HTTP never accepts a caller's base URL. A `retry` grant does
not permit `recover-unknown`. Default offline V1 HTTP execution never calls a
provider, even when CLI provider credentials exist in the environment.

## Source scope and storage

Authorization uses the analysis root, never a discovered parent Git root.
No allowlist means no scan. Relative paths, `..`, symlink paths and links/special
files in the scanned subtree are refused. Indexer skip directories are shared;
unreadable traversal fails closed instead of producing a successful partial map.
Only index/new-run operations require an existing checkout. Retained read/review
and future sealed retry authorization use the original absolute scope even after
the checkout is moved, without rescanning it.

The local same-user filesystem is trusted between validation and reading. This
is not a sandbox against hostile same-UID path replacement or a remote tenant.

HTTP creates a missing legacy ledger only in an owner-only directory (`0700`)
with a private regular database (`0600`). Existing unsafe directories, files,
hardlinks, user-created symlinks or SQLite sidecars are refused, not chmod-ed or
migrated. Database ancestors retain the existing Store's exact `/tmp` and `/var`
system-alias exceptions; this does not permit a symlink database entry or other
symlinked directories. Canonical analysis-root requirements are unchanged.
V2 data/backup/export protection continues to use the existing private database
and export tools; see [data protection](protocol-v2-data-protection.md).
No backups, output files or source archives are published by this API change.

New legacy workflow failure records retain exception type and the trace's node
stage, not raw exception text that could contain credentials. The V1 evaluation
persistence check uses the same safe summary; failure category, attempt counts and telemetry
remain unchanged. The outer repository-preparation failure export also uses that
summary; its synthetic Git-process failure canary was reproduced before repair.
Regular benchmark preparation/evaluation failures and hybrid provider fallback
now use the same safe summary. Reused benchmark errors receive a response-only
generic projection; metrics, fallback classification and telemetry are retained,
without changing checkpoint records or re-executing reused cases. API analyzer
construction rejects unsafe base URLs; benchmark configuration checks both base
URLs and manifest Issue URLs with the shared validator before checkpointing. Valid endpoint
spelling and the existing checkpoint identity comparison are unchanged.
The old multi-Issue batch-abort behavior and immutable T0
fixture are unchanged; only the current persisted error wording is redacted.
HTTP also redacts historical Run
and trace error fields in a response-only projection. Retained Issue URLs that
fail the same URL validator as new HTTP input become `null`; safe URLs remain
unchanged, including successful historical/CLI-created runs without errors.
Original stored URLs and errors are not rewritten. Authorized Issue text,
source evidence and model analyses remain sensitive content, not automatically
scrubbed public artifacts. Retention and
cleanup remain manual; no encryption at rest or historical cleanup is implied.

## Fixed resource boundaries

| Resource | Limit |
|---|---|
| HTTP request body, including streamed/chunked bodies | 1,048,576 bytes; 10 seconds to receive |
| Issues per request | 100 |
| Single JSON text / total JSON text (including supplied evidence) | 100,000 / 250,000 characters |
| JSON structure | 32 nesting levels, 20,000 visited values |
| `limit` / `page_size` query parameters | 1–100 (actual V2 pagination is PR7A) |
| Indexed source file / total indexed source bytes | 2,000,000 / 32,000,000 bytes |
| Scanned directory/file entries | 20,000 |
| Concurrent state-building/writing HTTP requests | 1 per supported server process; excess returns 503 |

Raw-body and payload checks precede model/state construction; source checks
precede indexing/workflow execution. Chunked bodies do not trust Content-Length.
Source byte accounting uses the indexer's shared language classifier, including
case-insensitive source extensions and shipped JSON schemas. Non-source assets
do not consume that byte budget, but still count toward the entry limit and
undergo link/special-file checks. The resource limit values are unchanged.
Compressed requests are refused. Execution capacity is released on failure as
well as success. Only repository indexing, new Runs and legacy Run review use
the slot. Review retains serialization because its legacy Store operation is
read/check/write; this is not PR7's per-Issue concurrency protocol. Score/rank,
health/read and unrelated routes remain independent of an occupied slot.
Route classification uses Starlette's own root-path handling. These are bounded
local API limits, not a general distributed quota or job scheduler.

## Validation record

Permanent checks live in `tests/test_api_security.py` at the confirmed serve,
HTTP and authorization-service boundaries, with the existing V1 API/workflow/CLI
tests retaining compatibility coverage. RED evidence includes non-loopback
startup, missing token, browser/proxy spoofing, forged reviewer, default scan
authorization, absent transfer policy, five resource limits, concurrent runs,
private-file modes, raw validation/persisted errors and historical error projection.
Retained-scope authorization and unreadable walks were separately reproduced
before repair. A real loopback subprocess verifies startup, authentication,
proxy refusal, local launcher identity and secret-free logs, then is shut down.

Initial implementation validation passed `62` focused security/workflow/evaluation/baseline
tests and `967` full-suite tests, with one existing Starlette deprecation warning.
Ruff, formatting of the new/HTTP boundary files, compileall and diff whitespace
checks passed. The first full run (`2 failed, 964 passed`) exposed old raw-error
comparison assumptions; the shared summary and current assertions were repaired
without editing the frozen fixture. An intermediate `966 passed` predates the
last outer-evaluation repair and is not the final gate.

Initial independent Standards and Spec reviews against `bf17ac6` found no remaining
confirmed violations, actionable smells, missing requirements or scope expansion
after their findings were repaired. Static reviews and executed tests are separate
evidence. Head-specific CI is recorded with the implementation PR, which remains
unmerged. This document does not claim real provider
calls, V2 HTTP retry integration, native Windows, physical power loss, remote
exactly-once or multi-tenant security.

## Review repair: source budgets and system database aliases

GitHub review of `96280a7` identified two P2 compatibility defects: non-source
assets were incorrectly charged to source byte budgets, and valid database paths
under system aliases were rejected before the existing private-path handler.
These repair the existing PR6 boundary, without weakening source limits or file
permissions and without extending the analysis-root alias policy.

Before repair, the asset selection gave `4 failed, 3 passed`, and the native
macOS system-alias selection also gave `4 failed, 3 passed`. Tests exercise both
index and run with a large asset or aggregate small assets; oversized source,
uppercase extensions and JSON schemas remain refused. Native `/tmp` and `/var/tmp`
tests cover new/existing database paths, create/read/review and unsafe permissions.
Arbitrary user directory/database/SQLite-sidecar links remain rejected. Linux CI
executes the same path cases but does not claim to emulate macOS system symlinks.

Repair local validation passed `52` security tests and `981` full-suite tests,
with one existing Starlette deprecation warning. Ruff, changed-file formatting,
compileall and diff whitespace checks passed. Independent increment reviews
against `96280a7` found no documented Standards violations or actionable smells,
and no confirmed Spec gaps, incorrect behavior or scope expansion for R5 PR6
6.4/6.8/6.9 and the existing data-protection alias contract. Static review and
executed tests are separate evidence. Repair-head CI is recorded on PR71; the
earlier 967-test evidence does not validate this repair.

## Full-PR review after `3d0cfa9`

The next GitHub review identified three distinct defects (the executor-slot
comment was duplicated): historical Issue URLs escaped the HTTP response
projection, method-wide serialization blocked independent scoring requests, and
regular benchmark failures still exported raw exceptions. The full-PR review
uses base `bf17ac6`, not only the preceding two-fix increment.

Regression tests on `3d0cfa9` produced `4 failed`: historical HTTP URL disclosure,
scoring blocked during a Run, benchmark preparation failure disclosure, and
hybrid provider-fallback disclosure. The last path was identified by following
the benchmark caller chain beyond the original comment. Only synthetic canaries
and temporary repositories/databases were used; this is evidence of reachable
disclosure paths, not evidence that real credentials were exposed.

Independent follow-through also reproduced two related gaps: a real API
analyzer accepted userinfo in its base URL before benchmark configuration was
stored, and reusing historical benchmark results re-exported their raw errors.
The same configuration included unvalidated manifest Issue URLs; the final
configuration guard covers both URL sources. Client/configuration/reuse controls
gave `6 failed, 5 passed` before repair, and the later manifest-URL controls gave
`2 failed, 2 passed` before their repair.
These fall under R5 6.9/A05, not new benchmark functionality or historical-data
migration. Historical records must remain readable without being silently
rewritten; newly emitted responses and result artifacts must not repeat their
credential-bearing diagnostics.

The final affected API/security/benchmark/client selection passed `156` tests,
and the final local full suite passed `1005` tests, each with one existing
Starlette deprecation warning. Ruff, changed HTTP files' formatting, changed
URL-validation range formatting, compileall and diff whitespace checks passed.
Unrelated legacy formatting is preserved. The intermediate `1003`-test
full-suite pass predates the final manifest-URL repair and is not its acceptance
gate. Independent full-PR Standards review against `bf17ac6` identified five
documented violations in the pre-repair head, now closed; the final WIP has no
remaining documented violation or actionable Fowler smell. The independent
Spec review additionally identified the manifest-URL path and confirmed that
it is closed, with no remaining confirmed gaps, incorrect behavior or PR7 scope
expansion for PR6 6.1–6.9. Static review is separate from test execution.
Repair-head CI evidence follows the exact repair tree
recorded on PR71. No provider request, user database migration, new dependency
or frozen-fixture/ranking change is part of this repair.
