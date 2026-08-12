# LLM Evaluation Protocol

Every OpenCode model path must be compared with the existing deterministic baseline before any
quality improvement is claimed.

## Variants

1. `deterministic`: rules, duplicate similarity, AST index, lexical candidate generation, and
   bounded static-graph reranking inside fixed Top-10 bands.
2. `hybrid`: deterministic retrieval followed only by OpenCode
   `deepseek-v4-flash-free` evidence-ID reranking.

An `llm-only` variant remains future work. The implemented Hybrid benchmark cannot discover files
outside the deterministic candidate pool. It sends no `response_format` and accepts one unique
plain-text `RANK:` line; the CLI does not accept a different provider or model. The rank request
disables reasoning, asks for at most three IDs, starts with a 256-token output budget, and retries
once at 1,024 tokens only if the first completion is truncated.

## Dataset

The current frozen dataset contains 50 closed issues across 21 repositories that link to a fix
pull request. Manifest version 8 stores the complete evaluated Issue snapshot and never fetches
mutable Issue text during a benchmark run. It records:

- issue number, title, body, labels, timestamps, URL, author, and comment count;
- duplicate master, when applicable;
- files changed by the fix;
- 39 manually reviewed symbols across 33 high-confidence cases;
- the parent of the first fix-PR commit used for indexing.

Repository preparation verifies the frozen SHA, and evaluation indexes only Git-tracked paths so
ignored artifacts in reused workspaces cannot change candidate rankings.

Candidate discovery can reject invalid pairs, derive a proposed pre-fix SHA, and identify eligible
production files, but it cannot accept benchmark ground truth. Every accepted case must appear in
a committed manual selection file with Issue/PR relationship and changed-file review notes.

Do not include API keys, private repository content, or unreviewed generated labels. Manifest
versions 2 and 3 are retained for provenance but are superseded because their pre-fix SHAs were
not derived correctly. Manifest version 5 is retained as
`benchmarks/cases-v0.10-corrected-20-cases.json` because it is the frozen input for the corrected
20-case DeepSeek run. Manifest version 6 is retained as
`benchmarks/cases-v0.11-32-cases.json` for the pre-qualified-symbol baseline, and manifest version
7 is retained as `benchmarks/cases-v0.12-qualified-symbols-32-cases.json`.

## Metrics

- Issue-type accuracy.
- Reproduction-completeness accuracy.
- Duplicate precision, recall, and F1.
- File Recall@1, @5, @10, and @20 plus file MRR.
- Symbol Recall@1, @5, @10, and @20 plus symbol MRR on labeled cases only.
- Rank-protocol success rate and fallback reasons.
- Unknown evidence-reference rate.
- Input and output tokens per issue.
- Analysis latency per issue, measured after repository preparation.
- MRR on valid reranks separately from overall MRR with all fallbacks retained.

## Reporting

Machine-readable results are saved under `benchmarks/results/`; the reviewed result is
`docs/benchmark-results.md`. All evaluated issues, including failures, must remain in the output.

## Current smoke result

On 2026-07-30, the same synthetic path also completed through OpenCode
`deepseek-v4-flash-free` with the full investigation schema:

- one request, no retry;
- 1,424 input tokens and 2,745 output tokens;
- 22,452 ms model-request latency;
- one evidence observation and two evidence-linked hypotheses accepted;
- the Agent stopped at `awaiting_review`;
- the persisted report and trace identified provider `opencode` and contained no API key field.

This is an integration and grounding smoke test over one synthetic case. The real-project
localization benchmark is reported separately in `docs/benchmark-results.md`.

## Three-run real-project full-analysis baseline

On 2026-08-12, three authorized runs evaluated the complete Agent JSON contract on the frozen
Starlette main, Typer calibration, and Textual generalization cases. All nine case-runs remained in
the denominator. The first suite returned 3/3 valid analyses in one attempt, with 30,440 input and
10,714 output tokens and mean provider latency of 15.75 seconds. The second suite returned one
invalid structured response and then two HTTP 429 failures. After increasing the inter-case delay
from three to 30 seconds, the third suite still returned three HTTP 429 failures.

Across the three suites, 3/9 case-runs produced a valid full analysis, 5/9 ended in rate limiting,
and 1/9 ended in invalid structured output. The first successful suite produced exactly one
observation per supplied evidence item and one evidence-linked hypothesis for each case. The
observed success rate is therefore 33.33%, far below the rank-only protocol, and does not support a
production-reliability claim. The result also shows that a fixed seed and a longer delay between
cases do not overcome the current account/provider limit.

During this run, an initial persistence check compared the in-memory strict response subclass with
the restored public base model and reported false despite identical serialized payloads. The
evaluator now compares the public JSON payload and has a regression test using the real response
subclass boundary. Structured-response and evidence-contract failures now preserve request, token,
latency, and category telemetry. They are non-retryable because repeating the same request does not
repair an invalid contract. Transport, HTTP 429, and HTTP 5xx failures remain retryable with
bounded exponential backoff and `retry-after` support. The next independent experiment is to make
the full-analysis contract smaller before repeating the external suite after the provider limit
resets.

## Current manifest-v8 deterministic result and retained paired LLM result

Manifest v8 deterministic v0.16 completed three 50-case runs with structurally identical candidates,
symbols, and metrics after timestamps and elapsed fields were excluded. File Recall@1 was
`0.4067`, Recall@5 `0.6900`, Recall@10 `0.7800`, Recall@20 `0.9500`, and MRR `0.6027`. On 33
symbol-labeled cases, Symbol Recall@1 was `0.2273`, Recall@5 `0.4242`, Recall@10 `0.4545`,
Recall@20 `0.5455`, and symbol MRR `0.3342`. Four cases gained a reviewed symbol hit, none lost
one, and all file candidate lists remained unchanged.

Two authorized OpenCode `deepseek-v4-flash-free` rank-only runs used the same 50 deterministic
v0.13 candidate pools and retained all cases in the denominator. Both returned 50/50 valid ranks
with no fallback. Run 1 File Recall@1/5/10/20 was `0.6567/0.8200/0.8600/0.9300` with MRR `0.8226`;
run 2 was `0.6767/0.8200/0.8600/0.9300` with MRR `0.8326`. The two-run mean and population
standard deviation were Recall@1 `0.6667 +/- 0.0100` and MRR `0.8276 +/- 0.0050`.

Protocol success was 100% in both runs. Valid-response MRR therefore equals overall MRR, and the
fallback-reason map is empty. Grammar HTTP 400, invalid rank, and unknown evidence ID counts were
all zero. All 100 requests completed in one attempt. Each run used 170,521 input tokens; output
was 485 and 491 tokens, and average model latency was `4.13 s` and `4.96 s`. Run-level mean
latency was `4.55 +/- 0.41 s` (population standard deviation).

Against deterministic retrieval, both runs improved 18 case-level reciprocal ranks, left 28
unchanged, and worsened four. Every case retained the same 20-file candidate set, but 14/50 file
orders changed between repeats. Only `pydantic-safe-annotations-metaclass` changed expected-file
reciprocal rank, from rank 2 to rank 1. Seed 1337 is therefore best effort. The reviewed artifacts
are `benchmarks/results/hybrid-deepseek-v4-flash-rank-none-v0.14-manifest-v8-run1.json` and
`benchmarks/results/hybrid-deepseek-v4-flash-rank-none-v0.14-manifest-v8-run2.json`.
They have not yet been rerun against the changed v0.16 symbol-selection policy. The current
deterministic artifact is
`benchmarks/results/deterministic-v0.16-symbol-mentions-50-cases.json`.

The retained manifest-v7 v0.12 baseline completed 32/32 cases with File Recall@1 `0.4479`,
Recall@5 `0.7812`, Recall@10 `0.8906`, Recall@20 `0.9844`, and MRR `0.6428`.
After adding the reviewed `WorkerThread.__init__` target, the 16 labeled cases and 17 targets reach
Symbol Recall@1 `0.1875`, Symbol Recall@5 `0.4688`, Symbol Recall@10 `0.4688`, Symbol Recall@20
`0.5938`, and symbol MRR `0.3099`. The qualified identity is representable, but the review-fixed
selector does not infer `__init__` from an owner-only mention. Source-content retrieval now treats
dotted tokens as complete, case-preserving identities and excludes their component terms unless a
syntactic call in Issue text separately exposes the local callee. Function-level source relations
consume exact file-and-symbol `resolved_calls` produced with lexical scope analysis; parameters,
local assignments/imports, closures, statically visible `global` assignments, unresolved receivers,
legacy broad maps, or definition-time rebinding cannot contribute inference edges. Specific
title-to-path matches remain in the candidate pool when weaker graph expansions are added. A full
audit found changed file orderings and per-file symbol lists in all 32 cases relative to v0.11,
including the corrected Starlette protocol-event and session-cookie selections. Relative to the
immediately preceding v0.12 artifact, the complete
scope-safe contract changed 31 file lists and 31 symbol lists, increased File Recall@20 by `0.0156`
and MRR by `0.0356`, and lowered Recall@5 by `0.0521`. Two complete review-fixed v0.12 runs produced
identical candidates, symbols, and metrics after excluding timing fields.

An authorized manifest-v7 run then sent the frozen public Issue snapshots and bounded public
candidate snippets to OpenCode `deepseek-v4-flash-free`. All 32 cases completed and remained in the
denominator. Twenty-eight responses passed local schema and evidence-ID validation; four cases
exhausted two `json_invalid` attempts and used the deterministic fallback. File Recall@1 was
`0.7135`, Recall@5 `0.8958`, Recall@10 `0.9375`, Recall@20 `0.9844`, and MRR `0.8547`. On the 16
labeled cases, Symbol Recall@1 was `0.4688`, Recall@5/10 `0.5312`, Recall@20 `0.5938`, and MRR
`0.5245`. Relative to the paired deterministic baseline, 12 file ranks improved, 18 were unchanged,
and two worsened; five labeled symbol ranks improved and none worsened.

The 28 successful final calls recorded 145,469 input and 59,682 output tokens and averaged
`20.1 s` of provider latency. Failed attempts are not included in those token totals. A separate
diagnostic rerun recovered two of the four fallback cases; the AnyIO free-threading and Click
parameter cases again produced invalid JSON twice. This is a single best-effort-seed run rather
than a stability estimate. A later deterministic Werkzeug audit also found a metric-neutral
rank-20 tail difference from the earlier artifact, so exact long-tail candidate reproducibility is
not claimed. The reviewed artifact is
`benchmarks/results/hybrid-deepseek-v4-flash-v0.12-manifest-v7-32-cases.json`.

The retained corrected manifest-v5 snapshot also received a real OpenCode rerun:

- OpenCode DeepSeek V4 Flash completed 20/20 valid reranks with no fallback. File Recall@1 was
  `0.4417`, Recall@5 `0.8583`, Recall@10 `0.9250`, Recall@20 `1.0000`, and MRR `0.7257`.
  Average successful model latency was `17.5 s`; the run used 102,925 input and 38,376 output
  tokens.

This is a localization result on manifest v5, not manifest v7. It supports an ordering improvement
over the same v5 deterministic candidate pool, while Recall@20 remains bounded by deterministic
retrieval. A separate three-real-case OpenCode full-schema run succeeded on two
cases; the Typer case exhausted two invalid structured responses and used fallback. The compact
rerank schema is therefore more reliable than the full hypothesis schema.

## Free-model selection

The historical shortlist is deliberately small:

- `deepseek-v4-flash-free` remains the only current benchmark reranker. Its plain rank-only
  protocol returned 50/50 valid ranks in each of two v8 runs and raised paired MRR substantially.
  Fixed-seed ordering still changed in 14/50 cases, so deterministic fallback and protocol
  telemetry remain required even though neither run used fallback.
- `nemotron-3-ultra-free` matched DeepSeek's screening metrics but averaged `26.5 s`, so it does
  not justify a full run yet.
- `north-mini-code-free` returned only 3/5 valid responses and averaged `40.3 s` when successful.
- `ling-3.0-flash-free` returned upstream HTTP 400 for all ten attempts. Its fallback metrics are
  not model measurements.
- `big-pickle` is not a primary benchmark model because its opaque identity weakens
  reproducibility and interview explainability.

The screen used five high-discrimination cases and the same rerank contract, but its localization
metrics are superseded by the pre-fix correction. Free-model data remains limited to public Issue
and repository content because OpenCode documents that free-period data may be used for model
improvement.
