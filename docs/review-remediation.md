# Review assessment and compatibility-safe remediation

The reviewed checkout was `7cbf600` (PR68). This remediation is based on merged
`f19ee60` (PR73); PR74's result catalog is a separate, still-open change. Findings
about missing functionality must be evaluated against the version actually run.
An old local checkout does not acquire newer safety features just because they
exist on the remote main branch.

## Assessment by finding

| ID | Assessment and action |
|---|---|
| H1 | Confirmed. Match the RCE acronym at word boundaries; ordinary source/force/commerce/enforce text no longer implies critical impact. Other term policies are unchanged. |
| H2 | Accurate for the old checkout, superseded on this base by PR6. Bearer, Host/Origin, loopback, scope and resource guards already exist. This is not remote multi-tenant security. |
| H3 | Confirmed URL/component-boundary failures repaired. Explicit URLs cannot become exact source-path evidence. Relative references match actual path components; absolute paths and conventional Git diff a/b prefixes can supply checkout context. Revision-bound source links keep their separate validated path. Existing suffix ambiguity remains: pkg/util.py can match both pkg/util.py and src/pkg/util.py; this patch does not establish unique-path confidence. |
| M1 | Confirmed. Python UTF-8 BOM decoding now matches the supported UTF-8 source contract. |
| M2 | Superseded in part: PR5B resume/retry and PR73 per-Issue review exist. HTTP V2 retry/recover-unknown is still unfinished; this patch does not implement or claim it. |
| M3 | Confirmed denominator/communication gap. Preserve legacy completed-case fields, label them explicitly, and add all-case file metrics with zero credit for failed execution. No historical score is rewritten. |
| M4 | Confirmed. Rust comment scanning splits on physical LF rather than Python's broader Unicode line-separator set. |
| M5 | Confirmed. Cache successful LLM results only within one node execution so a later transient failure does not resend earlier Issues. LLM retries require explicit retryability; known read-only local I/O stages retain bounded OSError retries with backoff. This is not distributed exactly-once execution or new V1 partial-result durability. |
| M6 | Mixed. Non-monotonic Run transitions are required by guarded resume/retry; a blanket forward-only restriction is incorrect. The DB-level state/report constraint gap is real. Readers now reject inconsistent pairs, including paginated summaries. DDL is deliberately unchanged: a compatible explicit schema-upgrade path is required before adding constraints to already-created V2 databases. This part remains open. |
| M7 | Confirmed latent adapter gap. An explicit outcome_uncertain error now remains unknown, not a definite provider failure, and is not automatically replayed. |
| M8 | Partly confirmed, not fully repaired. V1 partial-clone preparation can require network hydration; the unconditional offline-cache wording is corrected. A robust explicit offline/hydration policy remains separate work. Discovery metadata alone does not prove pre-fix file existence; curator verification and preparation checks remain necessary. No new network lookup or automatic candidate acceptance is introduced. |
| M9 | Defensive bug confirmed under nonterminating responses, not an assertion that normal GitHub always loops. Issues, search and generic list pagination now have a 100-page budget and raise rather than silently return incomplete results on exhaustion. |
| M10 | Confirmed. Candidate source-content/snippet reads stop at 1,000,001 characters before deciding the 1,000,000-character limit. Immutable blob reads check a 4,000,000-byte bound first and retain the character bound after decoding. This is not a new universal size limit on all indexer inputs. |
| L1 | Confirmed. Remove only actual leading ./ prefixes, preserve dot-prefixed names, and do not erase parent traversal. |
| L2 | Confirmed short-token false positives (e.g. os/posix and re/requests). Import evidence now uses component terms; the report's os/django example itself is not a substring match. |
| L3 | Confirmed heuristic limitation (fixes -> fixe), not a demonstrated correctness contract or ranking improvement. No ad-hoc English stemmer expansion is made without retrieval evidence. |
| L4 | Not reproduced: the two cited call/import branches are reachable and return their 8/5 capped bonuses. No dead-code deletion is justified. |
| L5 | Confirmed. Repository names . and .. are rejected before HTTP, using the shared repository validator. |
| L6 | Confirmed. GitHub tokens are SecretStr values in settings; the HTTP client unwraps them only for the Authorization header. |
| L7 | Confirmed legacy race/lifecycle gap. Review eligibility and update share one immediate transaction, and connections close at context exit. V1 review does not become append-only or versioned. |
| L8 | Confirmed. NUL-delimited Git filename output stays binary until filesystem decoding; no stripping or newline conversion corrupts path names. |
| L9 | Confirmed. Directory exclusion is exact/boundary-aware rather than treating docker/docgen/documentation_lib as documentation. Curator approval remains mandatory. |
| L10 | Confirmed for Git checkout defaults. Native tracked/unignored enumeration avoids ignored clone/cache trees while preserving tracked ignored files, new unignored source, explicit included-file lists and ordinary non-Git directory walking. Existing HTTP authorization/resource limits are not relaxed. |

## Validation and boundaries

Repairs use synthetic repositories, SQLite databases and mock transports. No
real provider call, benchmark rerun, training, source-data migration, historical
artifact rewrite or default V2 switch is part of this patch. Index semantics
advance to v26 so v25 maps cannot silently survive parser/enumeration changes.

Local verification: 1097 tests passed with one existing Starlette warning; whole-repository
Ruff and `git diff --check` passed. Regression checks cover the confirmed failure paths
and preserve the existing read-only I/O retry and Git diff path-prefix contracts.

The remaining M6 DDL upgrade and M8 source-presence/offline-preparation work are
not marked resolved. Existing V2 database signatures remain readable; malformed
rows fail closed rather than being rewritten. V1 final batch failure can still
omit earlier LLM results from the final Run; use V2's staged persistence for that
stronger guarantee. This assessment does not claim the project or G1 is complete.

Independent Standards review identified a stale retry-policy paragraph, now corrected.
Independent Spec review found no confirmed blocking regression within the selected scope.
Its residual observations were the existing path-suffix ambiguity above and malformed
empty-string reports inserted only by bypassing SQLite CHECK constraints; the new state/report
presence guard is not a complete audit of arbitrarily corrupted database contents.
