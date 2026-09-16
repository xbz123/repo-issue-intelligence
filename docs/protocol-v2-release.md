# G1 release acceptance and cutover rehearsal

Baseline: PR76 merged as `3d440c2` on 2026-09-16. This record distinguishes
implemented release checks, executed evidence and an actual operator cutover.
No user database or live provider is used by the release tests. G1.8 stays
gated until the prerequisite evidence and independent review are accepted.

## Acceptance evidence

`tests/test_protocol_v2_e2e.py` runs six synthetic release scenarios. Its
`g1_*` JUnit properties contain portable observations, not private source or
database archives. CI preserves Linux 3.11/3.12 and macOS smoke JUnit reports.

| Gate | Evidence / remaining requirement |
|---|---|
| G1.1 | Linux full suite plus macOS Git/context/view/symlink/database/G1 smoke; new CI jobs must actually pass. |
| G1.2 | CLI migration of the retained synthetic legacy fixture to a new private target; source and backup bytes unchanged, imported history read-only. No claim of migrating an operator's production database. |
| G1.3 | Mixed success/failure/unknown/controlled empty evidence, HTTP evidence read, retry/recover, mixed reviews and identical replay; disabled/fatal cases independently exercised. |
| G1.4 | Two official-demo cases compare every candidate (including symbol alternatives) and deterministic file metrics between clean tracked checkout and fixed view. An untracked decoy is separately excluded. This is a release regression fixture, not a rerun of the 200-case historical benchmark or evidence of new quality gains. Existing repository-view and benchmark characterization tests remain required. |
| G1.5 | 1,000-file fixed synthetic repository, three Issues and 30 explicitly appended diagnostic traces; raw map is not persisted in legacy snapshots or trace payloads. Measurements below. |
| G1.6 | HTTP and CLI summary accounting agrees; requested model is distinct from reported-B, missing observations remain null, original failed/unknown attempts survive retries. Full Store/migration/finalizer constraints and concurrency tests remain required. |
| G1.7 | Independent security review of token/Host/Origin/scope, symlink/userinfo, redaction, export and backup boundaries; findings and residuals must be recorded before release. |
| G1.8 | Default switch and retirement of implicit legacy writes are a separate gated change, not implied by adding tests. |
| G1.9 | Stop writes, export new V2 run data, archive complete V2 ledger to a new file, validate restored V2 data and read original legacy backup; never overwrite either generation. |
| G1.10 | Publish actual CLI/HTTP and recovery boundaries, acknowledge PR8 dataset roles and remaining F1–F4 work. This record is not a release approval by itself. |

### G1.4 blocking compatibility finding

All 200 manifest-v20 commit objects were found in the existing local cache.
The offline real-repository comparison was paused after six completed cases:
five equal, one symbol difference; the other 194 are not verified. No historical
result or manifest was changed. The tool compares candidate order, primary/
alternate symbols and actual file/symbol metrics; diagnostic evidence prose is
not used as the ranking gate. Field names are checked against the result schema.

`starlette-http2-cookie-headers` captures `f617177ab955f7e79e0d863a7c28adb6200b4acc`,
but its Issue contains a source-line link to
`6ee94f2cac955eeae68d2898a8dec8cf17b48736/starlette/requests.py#L143`.
The checkout path selects `HTTPConnection.cookies`; the V2 fixed view selects
`cookie_parser` for that same file. File order and observed scalar metrics agree,
but the required symbol-selection equivalence does not.

The existing `_source_line_positions_by_path` explicitly refuses references to a
revision other than the captured one for V2. A one-case counterfactual disabling
that channel in both paths restores equality. That is diagnostic evidence only,
not a passing frozen-input acceptance run. The first real case also differed in
diagnostic prose while its ranking projection remained equal; these differences
are reported separately rather than conflated.

G1.4 and G1.8 remain blocked pending an explicit compatibility disposition:
retain the frozen-source boundary and document foreign-revision references as
an intentional versioned difference, or choose another contract-preserving
resolution. Do not silently weaken the captured-revision guard, rewrite gold,
or count the partial run as a complete 200-case pass.

Reproduce using a new output path and an already-prepared local source cache:

```sh
uv run python scripts/verify_protocol_v2_ranking.py \
  --manifest benchmarks/cases.json --cache /path/to/existing/cache \
  --output /path/to/new/private-g1-report.json \
  --case-id starlette-http2-cookie-headers
```

Omit `--case-id` for the full collection. The tool uses isolated local clones,
disables lazy fetch and user Git hooks/filters, refuses an existing report path,
checkpoints only its new report, and exits nonzero for mismatched/unverified cases.
It never runs project code, models or tests from the compared repositories.

Local macOS pre-cutover run: six tests passed, one existing Starlette warning,
63.21 seconds. The earlier pre-hardening repository-context/view, database/migration, Store,
execution-claim, API-security and V2 CLI gate selection additionally passed
327 tests (79.10 seconds, the same existing warning); this is local macOS
evidence, not a substitute for the latest head's hosted CI after the path repair.
Store scale observations:
serialized map 499,139 bytes; database
749,568 bytes; Issues 3, traces 30, attempts 0; trace payloads 4,590 bytes;
legacy snapshots 0. The trace stress is explicit test input, not a claim that
normal execution automatically emits 30 traces. Sizes are observations for this
fixture/environment, not a percentage saving or a general upper bound.

The mixed model run sends Issues `[1, 2, 3, 2, 3]`: successful Issue 1 is not
resent; Issue 4 has an injected empty collector outcome and never sends. The
existing collector budget tests independently cover naturally empty evidence.
Fatal injection sends `[1, 2]`, preserves Issue 1 and does not start Issue 3.
Disabled sends nothing. These are mock calls, not real provider observations.

### G1.7 path-credential repair

Independent review reproduced an A05 violation: an endpoint path such as
`/v1/api_key=syntheticSecret` could be retained in captured configuration. Seven
new endpoint/Git-remote canaries failed before repair. The shared URL boundary
now rejects any `=` authority/path assignment and credential-named path colon assignments,
including names such as `client_secret`/`refresh_token`/`private_key` and bounded
multi-layer percent encoding. Assignment-style routes, including nonsensitive
values, are deliberately unsupported. Unsafe remote identities are discarded;
errors do not echo the value. Eight additional prefix/assignment cases exposed
the first guard's limited name matching before the broader rule was applied.
Six further authority canaries failed before authority assignment rejection.
The final configuration/context/LLM-client/HTTP-retry selection passed 208 tests
(55.45 seconds), with the existing Starlette warning. Existing non-URL CLI flag
values and encoded-query error codes remain covered and unchanged.

This is not a universal secret detector: an opaque host/route label can be either a
public identifier or a credential. Operators must not embed secrets in host/route
names. Legitimate `tokenize`, `password-reset`, `secret-sauce` and encoded-slash
routes remain accepted. Existing ledger contents are not scrubbed or migrated
by this change. Independent incremental review confirmed the reported assignment
class closed (32 focused and 14 compatibility checks passed, plus bounded canary
probes); it approved pre-cutover publication, not final G1/cutover. Head-specific
CI remains required. The path fix does not resolve the separate G1.4 gate.

## Operator migration and rollback runbook

Run this only during an explicitly approved local maintenance window. Use new
paths below; replace the examples with intentionally selected private paths.
Stop the CLI/server writers and verify no active execution before taking the
cutover backup. A SQLite snapshot is consistent, but it is not a substitute for
stopping writers when deciding which generation is authoritative.

1. Inspect the source with `rii agent-db inspect legacy/agent.sqlite3`. Preserve
   the original program/configuration and legacy file. No startup auto-migration.
2. Create an owner-only archive directory (`0700`), then use the existing
   `backup_agent_database` function for a create-only SQLite backup:

   ```sh
   python -c 'from repo_issue_intelligence.agent_store_migrations import backup_agent_database; backup_agent_database("legacy/agent.sqlite3", "archive/legacy-before.sqlite3")'
   rii agent-db migrate --source legacy/agent.sqlite3 --destination private/release-v2.sqlite3
   rii agent-db inspect private/release-v2.sqlite3
   ```

3. Keep the migration receipt beside its original destination. Validate legacy
   history using the read-only legacy projection and test one deterministic V2
   run against the selected target. Do not combine separate beta databases.
4. Select only this V2 target for CLI/HTTP execution; stop using the original
   legacy source as a writer. Test read/review/retry permissions separately;
   migration does not grant external transfer. No live model smoke is implied.
5. For rollback, stop V2 writers first. Export every new run via
   `agent-show RUN_ID --protocol v2 --database private/release-v2.sqlite3` to a
   private file (set `umask 077` before shell redirection), and retain a complete
   create-only SQLite backup of the V2 ledger using the same backup function.
   The ledger, not just JSON summaries, retains all attempts and evidence.
6. Verify restored V2 summaries and the legacy backup before selecting rollback
   software. Never restore over the current V2 file or merge new V2 data into an
   old V1 database. V1 cannot faithfully represent every V2 state. Keep V2 data
   readable with the matching binary even if serving is rolled back.

Copied migration receipts retain their original inode binding: do not treat a
copied receipt as provenance for a new restored path. Preserve the original
database/receipt pair separately; a restored native V2 ledger remains readable,
while missing imported-history provenance is explicitly unavailable.

## Unchanged limits

Trusted local POSIX only; not remote multi-tenant identity. No automatic repair
execution, no remote exactly-once or physical-power-loss guarantee. Policy
rechecks cannot retract evidence already sent. Existing V2 database schema is
unchanged; arbitrary database tampering is not repaired by a reader check.
Manifest v20 is regression/development, not an independent holdout. F1.2–F1.7
and F2–F4 remain later work; F5/F6 remain demand-driven.
