# Protocol v2 PR5A: execution claims and single-row finalization

Scope: frozen R5 5A.1–5A.7, based on merged PR68 (`7cbf600`). This work retains
the existing attempt table, partial unique index and conditional finalizer;
it adds no owner/generation fields, schema, worker or review implementation.
Local checks, independent review and CI remain distinct gates.

## Interface and qualification

`AgentStoreV2.start_attempt` validates the frozen run configuration, active
`RUNNING` control state, successful deterministic report and current nonempty
sealed evidence within the same `BEGIN IMMEDIATE` transaction as insertion.
Only the winning insert can proceed to a backend. The committed attempt is
readable on another connection before dispatch. A run in another control state
cannot start new work; explicit resume transitions belong to PR5B.

`finalize_attempt` continues to update exactly one `in_progress` row. Only
success selects that same Issue's analysis in the same transaction. A duplicate
or late result has no effect, and a failed pointer write rolls back the terminal
update. Review versions are unchanged; review/start integration stays in PR7B.

Each connection explicitly uses SQLite's bounded five-second busy handler.
Exhausted BUSY/LOCKED contention is a `StoreConflict`, not an unbounded Python
retry loop. This retry concerns SQLite lock acquisition only, never provider
resubmission. Network work holds no SQLite write transaction. The separate G0
foreground run lock remains; this does not parallelize the default CLI workflow.

## Unknown is not a recoverable lease

PR5A provides no abandoned-attempt recovery entry point. A process exit after
start, whether before or during dispatch, leaves `in_progress`; it is not taken
over using a TTL, PID guess or an asserted "stopped" boolean. Current synchronous
backend calls may finalize their observed uncertain outcome as `unknown` when
the local call has unwound. Neither state is automatically resent.

An Issue with any retained `unknown` attempt is refused by default at the same
atomic start gate. Explicit recovery and proof that the local execution chain
has actually stopped belong to PR5B; no override is exposed here. This is the
conservative 5A.5 boundary, not completed resume support or remote exactly-once.

## Acceptance map

| R5 task | Evidence |
|---|---|
| 5A.1 | Existing migration-owned unique active index; two-process same-Issue claim test |
| 5A.2 | Inactive-run refusal and existing request/evidence qualification tests |
| 5A.3 | Independent readback before fake dispatch; process exit before/during dispatch |
| 5A.4 | Competing terminal writers, exact winning row, late refusal; existing pointer-failure rollback |
| 5A.5 | Unknown-history refusal; dead-process `in_progress` retained without replay |
| 5A.6 | Two Issues progress while fake backend is held; bounded SQLite contention |
| 5A.7 | Independent spawn processes/connections for start/start, finalize/finalize, unknown/finalize and reads |

The existing terminal-row test now retries a confirmed failure rather than
implicitly retrying unknown. Dedicated unknown tests preserve and strengthen
the unknown immutability/refusal coverage. No user database, real provider,
HTTP/review race, default V2 switch, native Windows or power-loss claim is part
of this gate.

## Validation (2026-09-08)

- Baseline differential: the six inactive-run, unknown-history and busy-start
  regressions fail against unchanged `7cbf600` source (`6 failed, 8 deselected`)
  and pass against this implementation (`6 passed, 8 deselected`).
- Affected Store/execution selection: `109 passed`.
- Full local suite: `864 passed`, one existing Starlette deprecation warning.
- Ruff, compileall, changed-Python-file format checks and `git diff --check`
  passed. No dependency or schema change was needed.
- Independent Standards review against `7cbf600`: no confirmed blockers or
  actionable code smells; a stale checklist progress sentence was corrected.
- Independent Spec review against `7cbf600`: no confirmed blockers for
  5A.1–5A.7, including the conservative no-recovery boundary above.

## GitHub validation and delivery

[PR69](https://github.com/xbz123/repo-issue-intelligence/pull/69) is open and
unmerged. Code head `660d04dba317de4e7eedc687d3696ccfec435051` passed
[CI](https://github.com/xbz123/repo-issue-intelligence/actions/runs/34275717360):
Python 3.11 and 3.12 each passed Ruff and `864 tests`, with one existing test
warning per job.

Both checkout logs identify merge ref
`563321c674e3c1fcecec106236a3c7de07968010`, combining `main` at `7cbf600` with
the code head. Its tree, `0af0f80a2662662033e2198ccef1533219a4f7b5`, exactly
matches the locally validated and independently reviewed head tree.

The checklist records 5A.1–5A.7 as `verified`, not `merged`. GitHub bot review
status remains separate from these local/CI gates and is reported on the PR.
Copilot's quota-blocked review did not execute and is not counted as approval.
PR5B–PR8 and G1 remain planned; V1 remains the default.
