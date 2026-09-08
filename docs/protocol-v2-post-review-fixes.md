# Protocol v2 post-merge review fixes

This follow-up targets `main` after PR64/65 (`31930db`). It fixes the five PR64
comments published after merge, without adding PR4/PR5 behavior or changing R5.
PR66 remains a separate PR; apply the repaired base before its next validation.

| Feedback | Reproduction and correction |
|---|---|
| [Symlink destination](https://github.com/xbz123/repo-issue-intelligence/pull/64#discussion_r3954238796) | create/migrate previously published through an alias the Store rejected; both now reject unsupported symlink ancestors before any creation. |
| [Directory durability](https://github.com/xbz123/repo-issue-intelligence/pull/64#discussion_r3954238799) | publication had zero directory syncs; new directory entries, receipt and DB publication now have ordered syncs and explicit failure handling. |
| [Out-of-file range](https://github.com/xbz123/repo-issue-intelligence/pull/64#discussion_r3954238805) | `100-110` on a four-line file returned unrelated content; V2 now skips the stale candidate while retaining later candidates and their ranks. |
| [Conflicting budget](https://github.com/xbz123/repo-issue-intelligence/pull/64#discussion_r3954238810) | contradictory output-token/timeout records were accepted; configuration validation and Store creation now reject them rather than silently selecting a value. |
| [Closed view](https://github.com/xbz123/repo-issue-intelligence/pull/64#discussion_r3954238813) | a recreated closed-view directory could supply replacement evidence; both explicit-view collector paths now check lifecycle state. |

All five were reproduced through existing public interfaces or filesystem I/O
boundaries before their fix. No real model calls or user-database migrations are
used. Tests preserve V1 behavior, closed input contracts and original sources.
See [data protection](protocol-v2-data-protection.md) for failure/retention limits.

Local validation: 75 affected tests passed; one full suite completed with
685 passed and one existing Starlette deprecation warning. Ruff, compileall,
diff whitespace checks and all 195 tracked JSON parses passed.

Independent Spec review caught an additional omission edge case in the initial
fix: request-only timeout was mistaken for an explicit null budget. `5520ef9`
adds separate persisted budget origins and six capture/Store/read/attempt cases;
the related configuration/Store suite passes 58 tests. Explicit conflicts remain
rejected. Both independent Astra Standards and Spec rechecks report no blockers.

[Final code CI](https://github.com/xbz123/repo-issue-intelligence/actions/runs/34187765869)
on `5520ef9`: Python 3.11 and 3.12 each pass **691 tests**, with one existing
dependency warning. Review scope is `31930db...5520ef9`; no real power-loss or
provider validation is claimed. Delivery is [PR67](https://github.com/xbz123/repo-issue-intelligence/pull/67),
kept unmerged. PR66 is unchanged and must receive this repaired base separately.

## PR67 follow-up: aliases and copied explicit nulls

Review of `395f9e8` found two remaining configuration paths:

| Feedback | Regression and correction |
|---|---|
| [Output-token alias](https://github.com/xbz123/repo-issue-intelligence/pull/67#discussion_r3956408459) | Reject `request_parameters.output_tokens=100` with budget `200`, and contradictory request aliases. Bind a valid alias to the attempt's canonical `max_output_tokens`, including explicit null. Explicit request aliases still supersede lower-priority defaults. |
| [Copied explicit-null budget](https://github.com/xbz123/repo-issue-intelligence/pull/67#discussion_r3956408466) | Check budget field presence before persistence, so `model_copy` cannot conceal an explicit null behind a stale omitted marker. Reject that contradiction even without a corresponding request parameter, preventing a null from silently disappearing on readback. |

Budget serialization omits only unset null output-token/timeout fields and
preserves non-null values even on models constructed with an empty field set.
The Store read adapter retains
compatibility with older serialized null defaults using their stored origins;
it does not rewrite historical rows. Those origins cannot independently prove
the intent of an already ambiguous historical record. New in-memory copies are
checked before that boundary. Regression tests use capture, Store creation,
readback and attempt binding; historical rows are represented at the SQLite
persistence boundary. No schema or V1 default changes are included.

Follow-up local full suite: **708 passed**, one existing Starlette deprecation
warning. Ruff, compileall, changed-region formatting, diff whitespace checks
and all 195 tracked JSON parses passed. Existing unrelated formatting drift
was left untouched. The six alias-conflict/copied-null regression cases also
ran against an isolated `395f9e8` source snapshot: all six failed because the
expected rejection was missing, and all pass in the repaired full suite.
Independent Luna max Standards and Spec reviews of the new delta from
`395f9e8` report zero remaining findings; the Spec reviewer independently ran
the 75 configuration/Store tests.
[Follow-up CI](https://github.com/xbz123/repo-issue-intelligence/actions/runs/34213674624)
on repair commit `aa93f0b`: Python 3.11 and 3.12 each pass **708 tests**, with
one existing warning. Review scope is `395f9e8...aa93f0b`; the earlier Astra
and CI results above apply only to the earlier revision. PR67 remains unmerged.
