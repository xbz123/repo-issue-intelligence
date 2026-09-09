# Protocol v2 PR5B: committed resume and explicit LLM retry

Scope: frozen R5 5B.1–5B.8, based on PR69 merged as
`226b9f50c1374a15529048c12ba5ba46deab8455`. V1 remains the default. This PR
adds no HTTP authorization, review writer, schema or owner/generation fields.

## Commands and configuration

```text
rii agent-resume RUN_ID --protocol v2 --database PRIVATE_DATABASE
rii agent-retry-llm RUN_ID --issue NUMBER --protocol v2 --database PRIVATE_DATABASE --allow-external-llm
rii agent-retry-llm RUN_ID --issue NUMBER --protocol v2 --database PRIVATE_DATABASE --allow-external-llm --recover-unknown
```

There is no `agent-recover` command. LLM-enabled resume also needs the current
`--allow-external-llm` permission. Recovery compares current analyzer settings,
endpoint/CLI version, engine/runtime and protocol with the frozen record;
CLI recovery also reconstructs current budgets and preserves captured parameter
origins. Re-supply the original explicit analyzer options when necessary.
Changed settings are refused before dispatch, not silently adopted. Reported
model/usage remain observations and are not required to match earlier responses.

Strict recovery requires a known clean Git engine at the same recorded source
revision/runtime. Dirty, unknown, upgraded or moved engine identity is refused.
To change model/prompt/budgets, use a new `agent-run --protocol v2` with
`--parent-run-id RUN_ID` and the desired inputs/options. The original run,
reports, attempts and reviews remain unchanged; no new command is introduced.

Optional `--output` uses the existing atomic owner-only JSON publisher and
refuses database/SQLite sidecars, writer locks and attempt execution guards,
including file aliases. Recovery defaults to a compact status message.

## Stage continuation

Committed resume uses the original captured revision/manifest, original Issue
snapshots and selected ordinals. It builds one repository map, skips committed
deterministic reports, continues missing evidence sealing, and starts only
Issues with no previous attempt. Uncommitted deterministic running/failed
stages can continue; already committed results are never overwritten.

Any retained attempt prevents automatic provider replay. Confirmed failures
need explicit `agent-retry-llm`; successful or reviewed Issues need a new run.
Pure LLM retry reads only the original Issue, report, sealed evidence and
configuration, even if the target checkout was changed or removed.
`tracked_worktree` deterministic resume remains unsupported, but its sealed
evidence is usable for same-configuration LLM retry.

## Proving local execution stopped

The database foreground lock still serializes run orchestration. For adapters
declaring the synchronous HTTP lifetime contract, a unique owner-only attempt
guard is created with exclusive/no-follow flags and locked before attempt
insertion. It stays locked through the synchronous call and terminal commit.
The guard is retained after close; JSON export cannot replace it.

Recovery opens the existing guard without creating it and obtains a new
nonblocking exclusive lock. Missing, unsupported, busy or unsafe guards refuse
recovery before run/attempt mutation. Guards remain held while settling old
`in_progress` rows to `unknown` and starting an explicitly authorized retry.
The old unknown terminal is never rewritten; the retry gets a different ID.

This is local stop evidence only, not a lease or proof of a remote outcome.
No TTL, PID guess, asserted stopped boolean or forced takeover is accepted.
The private same-user directory is trusted; malicious same-UID file replacement
is outside the local Store threat model. Never delete retained guard files.

Codex CLI and custom background/subprocess adapters do not currently provide
the full local-chain lifetime guarantee. Their abandoned/unknown attempts
therefore fail closed even with `--recover-unknown`. Merely passing a descriptor
to a launcher or seeing its parent exit would not prove that descendants
stopped. Normal same-configuration Codex continuation and confirmed-failure
retry are not replaced by an unsafe unknown-recovery shortcut.

## Acceptance and validation

Tests cover config/runtime drift, dirty/unknown provenance, all five interruption
checkpoints, committed versus tracked-worktree recovery, exact persisted retry
inputs, explicit permission, review/success refusal, real process exit and
cross-process lock contention, and private export aliases. A clean synthetic
engine test uses two real processes and actual Git/runtime capture without a
runtime stub. Other synthetic cases stub OS/runtime observations only; providers
are fake and no real requests are sent.

Initial regressions failed before their implementations: missing public/CLI
entries, unverified runtime acceptance, report/seal continuation gaps,
uncommitted deterministic stages, parent linkage and guard export aliases.
A process-test cleanup initially waited on a terminated multiprocessing Event;
that unsuccessful run was interrupted and is not counted as validation.

Local validation on 2026-09-09:

- Final full suite: `907 passed`, one existing Starlette deprecation warning.
- Final recovery/retry selection: `43 passed`, including Codex confirmed-failure
  retry, version drift and unproven subprocess-stop refusal.
- Earlier affected Store/execution/CLI selection: `188 passed`. This preceded
  the final closed-origin regression and three Codex cases; it does not replace
  the final full-suite gate.
- The final closed-origin regression first failed because arbitrary `budget.*`
  fields were silently ignored. Only the two defined derived budget markers
  are now regenerated; unknown origin fields remain rejected.
- Ruff, compileall, changed-file/range formatting and diff whitespace checks
  passed. All 153 unique task IDs and 306 plan-anchor references were checked.
- After the full run, the test-only remote-completion interruption was moved
  to after the fake analyzer returned a complete response and before finalization;
  all five checkpoint cases passed again. Production behavior was unchanged.

Independent review against merged baseline `226b9f5`:

- Standards: no confirmed blockers or actionable heuristic smells, including
  the final test-only checkpoint refinement.
- Spec: no confirmed blockers for 5B.1–5B.8 and RFC stage/unknown boundaries.
  The unavailable reviewer test environment is not counted as a passed test
  run; the actual test results above were executed separately from static review.

No user database was migrated; no physical power-loss, native Windows or
remote exactly-once behavior is claimed.

## GitHub validation

[PR70](https://github.com/xbz123/repo-issue-intelligence/pull/70) remains open
and unmerged. Code head `e1ae1a365b00605cdb8b31920d7067949260a97a` passed
[CI](https://github.com/xbz123/repo-issue-intelligence/actions/runs/34313136749):
Python 3.11 and 3.12 each passed Ruff and `907 tests`, one existing warning each.
Both checkout logs identify merge ref `cc0b51bf3bc98cda9b1b2cd9ed74b756880b3129`,
combining baseline `226b9f5` with the code head. Its tree
`8a8b5f541f6651085b306ccee92c17e899e62745` exactly equals the checked local head.

The checklist records 5B.1–5B.8 as `verified`, not `merged`. GitHub bot review
is a separate signal reported on the PR; Copilot's quota-blocked review did
not execute and is not counted as approval. PR6–PR8 and G1 remain planned.
