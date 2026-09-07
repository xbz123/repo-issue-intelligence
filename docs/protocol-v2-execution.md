# Protocol v2 PR4: foreground CLI beta

This branch implements R5 4.1–4.10 and exercises the G0 vertical gate. It is not
G1, does not switch the default protocol, and does not enable HTTP, cross-process
resume, manual retry/recovery, per-Issue review writes, or automatic repair.

Status: PR4 and G0 are **verified on the combined branch**, with
[PR66](https://github.com/xbz123/repo-issue-intelligence/pull/66) kept as an
unmerged draft. Verification is not a release or authorization to merge.

## Dependency boundary

PR4 is stacked on `codex/protocol-v2-pr4-base` at `d55e95c`, combining the verified
PR2B head `4461ca9` ([PR64](https://github.com/xbz123/repo-issue-intelligence/pull/64))
and PR3 head `f9b6902` ([PR65](https://github.com/xbz123/repo-issue-intelligence/pull/65)).
Both prerequisites remain open. This integration branch is not a release branch;
PR4 must be retargeted and its combined checks rerun after the prerequisites merge.
No R5 scope or acceptance requirement is changed by the stack.

## Opt in explicitly

V1 commands and databases remain the default. Create a dedicated V2 database once;
its directory must be private (`0700`) and the database private (`0600`). The
existing creation command creates a missing private parent but never overwrites
an existing destination or migrates a source implicitly.

```bash
uv run rii agent-db create-v2 --destination private/v2.sqlite3
uv run rii agent-run examples/issues.json --protocol v2 \
  --repo examples/demo_repository --database private/v2.sqlite3 --top-k 3
uv run rii agent-show RUN_ID --protocol v2 --database private/v2.sqlite3
```

The default `--capture-mode committed` requires clean tracked input within the
analysis subtree. `--capture-mode tracked_worktree` explicitly captures current
tracked bytes. Both use one fixed view/map for the run; unrelated Git-root files
do not expand a nested analysis scope. Untracked files are not implicitly added.

Without `--llm`, execution is offline. V2 rejects `--llm` unless the **current
invocation** includes `--allow-external-llm`; this permission is not inferred from
stored configuration, a past run, or HTTP authorization. Codex CLI also sends
Issue/evidence content to an external service; it is not local inference.

```bash
uv run rii agent-run examples/issues.json --protocol v2 \
  --repo examples/demo_repository --database private/v2.sqlite3 \
  --llm --llm-backend codex-cli --allow-external-llm --max-llm-attempts 2
```

Credentials remain backend configuration, not CLI arguments or run records.
Fake transport integration tests never constitute permission to send real input.

## Execution and failure semantics

Each Issue commits its deterministic report, seals its complete evidence set,
then reads those stored snippets back before starting an immutable attempt.
Provider failure retries only that Issue, with the same evidence/configuration.
Success and the selected-attempt pointer are finalized in one transaction. An
empty sealed set becomes `skipped_no_evidence`; disabled analysis creates no
attempt. All ordinary provider failures can still reach `AWAITING_REVIEW`.

Transport uncertainty and interruption are recorded as `unknown`, not assumed
unsent and not automatically replayed. Unknown programming, shared-map, and
storage failures stop the run; previously committed sibling reports remain
readable. If the database itself becomes inaccessible or corrupt, recording a
final failure status may also fail: a missing terminal marker is not success.

The default HTTP client explicitly disables transport retries. A Codex attempt
counts one controlled CLI invocation, not a known number of its internal remote
requests. Requested parameters, reported metadata, and local observations remain
separate; absent model/usage reports stay null. Summary diagnostics report model
name differences without asserting verified underlying model identity.

A private persistent `DATABASE.writer.lock` enforces one foreground executor per
database. It does not hold a long SQLite write transaction and readers remain
available. Do not remove the lock file while a process may use the database.
This is a local-operator beta boundary, not a sandbox against a malicious user
with the same OS account.

`agent-show` derives summaries in one read snapshot from stages, sealed evidence,
latest/selected attempts, and existing review records. It writes no second
outcome authority and no map/source snapshots. Review completion does not mean
approval or that an Issue has been fixed; writing V2 reviews remains PR7B work.

## Evaluation and artifacts

`agent-evaluate --protocol v2 --allow-external-llm` uses per-case private V2
databases and committed pre-fix inputs. It reads actual persisted evidence and
attempts, not a second evidence collection or V1 graph replay. Execution cases,
provider cases/attempts, no-evidence-eligible cases, and grounding cases have
separate denominators. Missing usage coverage yields null totals, not zero usage.
These metrics describe execution and file grounding, not verified root causes.

Evaluation ledgers remain at `WORKSPACE/.agent-evaluation-v2/RUN_ID/agent.sqlite3`;
each case artifact records a workspace-relative `database_path`. They are not
temporary databases and have no automatic TTL. A persisted fatal execution is
included in the attempted-case denominator and stops the evaluation batch;
the partial artifact records `completed=false` and the CLI exits nonzero.
Failures before a run can be created still raise rather than inventing a record.
Archive or delete these private records only by a separate explicit decision.

The default V2 exports are `reports/agent-run-v2.json` and
`benchmarks/results/agent-analysis-v2-latest.json`; V1 paths and artifact models
remain unchanged. V2 aggregate models reject V1 cases. Do not average the two
protocols together or publish raw evidence artifacts.

## G0 validation evidence

| R5 gate | Executable seam |
|---|---|
| G0.1, G0.2 | `test_agent_cli_v2.py`: run/show, official demo committed subtree, exact ledger and excluded inputs |
| G0.3 | `test_issue_execution.py`: A/C success, B two provider failures, all failures, disabled/no evidence |
| G0.4 | `test_issue_execution.py`, `test_agent_cli_v2.py`: primary references and requested/reported/null metadata |
| G0.5 | `test_agent_cli_v2.py`, existing V1 CLI/workflow tests: default run/show/review and legacy DB remain usable |
| G0.6 | `test_issue_execution.py`, `test_agent_store_v2.py`, CLI tests: seal failure, unsupported inputs, two-process writer rejection |
| G0.7 | Default-denied transfer tests and the limitations documented above |

Local full-suite validation on 2026-09-08 completed with **714 passed**, one
existing Starlette deprecation warning. A subsequent confirmed-CLI-failure retry
fix added one case; the final affected HTTP/CLI retry and unknown-outcome selection
passed **7 tests**. Ruff, compileall, diff whitespace checks and all 195 tracked
JSON parses passed. Initial CI caught two test-only assertions that did not strip
ANSI color codes; color/plain denial-message coverage now uses Rich's ANSI parser
while retaining exit-code and zero-dispatch checks (4 focused cases passed).
Python 3.11/3.12 CI on `e4555fb` each passed 717 tests. Independent Standards
review found no blockers. Spec review identified a reader-only enum mismatch:
`review_state` must preserve `approved/rejected/needs_information`, not collapse
them to `reviewed`. That correction leaves review writes out of scope; the
expanded Store checks passed 14 tests.

Final code acceptance at `7d9a4cb`:

| Gate | Result |
|---|---|
| Python 3.11 CI | [719 passed, 1 existing warning](https://github.com/xbz123/repo-issue-intelligence/actions/runs/34169038405/job/101885665636) |
| Python 3.12 CI | [719 passed, 1 existing warning](https://github.com/xbz123/repo-issue-intelligence/actions/runs/34169038405/job/101885665482) |
| Independent Astra Standards | No hard-standard violations, actionable smell findings or blockers; full PR4 diff plus final delta checked |
| Independent Astra Spec | Initial P2 enum finding fixed; all three decision cases independently checked, no remaining blockers |
| Local artifact checks | Ruff, format on new execution/evaluation files, compileall, diff whitespace, 195 JSON files and 343 Markdown targets passed |

The review fixed point is `d55e95c`, not `main`; predecessor changes were not
misrepresented as new PR4 code. No real provider calls or user-database migration
are part of this acceptance run. PR5–PR8 and G1 remain unimplemented here.
