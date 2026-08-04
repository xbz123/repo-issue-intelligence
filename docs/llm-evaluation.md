# LLM Evaluation Protocol

Every optional provider path must be compared with the existing deterministic baseline before any
quality improvement is claimed.

## Variants

1. `deterministic`: rules, duplicate similarity, AST index, lexical candidate generation, and
   bounded static-graph reranking inside fixed Top-10 bands.
2. `hybrid`: deterministic retrieval followed by provider-backed evidence-ID reranking.

An `llm-only` variant remains future work. The implemented Hybrid benchmark cannot discover files
outside the deterministic candidate pool.

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
Groq/OpenCode comparison. Manifest version 6 is retained as
`benchmarks/cases-v0.11-32-cases.json` for the pre-qualified-symbol baseline, and manifest version
7 is retained as `benchmarks/cases-v0.12-qualified-symbols-32-cases.json`.

## Metrics

- Issue-type accuracy.
- Reproduction-completeness accuracy.
- Duplicate precision, recall, and F1.
- File Recall@1, @5, @10, and @20 plus file MRR.
- Symbol Recall@1, @5, @10, and @20 plus symbol MRR on labeled cases only.
- Invalid structured-response rate.
- Unknown evidence-reference rate.
- Input and output tokens per issue.
- Analysis latency per issue, measured after repository preparation.

## Reporting

Machine-readable results are saved under `benchmarks/results/`; the reviewed result is
`docs/benchmark-results.md`. All evaluated issues, including failures, must remain in the output.

## Current smoke result

On 2026-07-29, the synthetic issue `#184` and `examples/demo_repository` completed one hybrid
Groq run with `openai/gpt-oss-20b`:

- one request, no retry;
- 1,242 input tokens and 552 output tokens;
- 1,145 ms model-request latency;
- strict structured output accepted;
- `E1` correctly marked `contradicts_issue` because the supplied code catches
  `ExpiredSignatureError` and returns HTTP 401 while the Issue reports HTTP 500;
- hypotheses were limited to unverified routing/middleware and deployment-version mismatches;
- the generated report, traces, and snapshots contained no API key.

This is an integration and grounding smoke test over one synthetic case. The real-project
localization benchmark is reported separately in `docs/benchmark-results.md`.

On 2026-07-30, the same synthetic path also completed through OpenCode
`deepseek-v4-flash-free` with the full investigation schema:

- one request, no retry;
- 1,424 input tokens and 2,745 output tokens;
- 22,452 ms model-request latency;
- one evidence observation and two evidence-linked hypotheses accepted;
- the Agent stopped at `awaiting_review`;
- the persisted report and trace identified provider `opencode` and contained no API key field.

The real-project report also contains a fixed-seed GPT-OSS 20B/120B comparison and a separate
three-case full-schema stability smoke test, both on the historical nine-case suite. Model-size
conclusions must not mix datasets or the localization and schema-reliability endpoints.

## Current manifest-v8 paired result

Manifest v8 completed two deterministic 50-case runs with structurally identical candidates,
symbols, and metrics after timestamps and elapsed fields were excluded. File Recall@1 was
`0.4067`, Recall@5 `0.6900`, Recall@10 `0.7800`, Recall@20 `0.9300`, and MRR `0.6016`. On 33
symbol-labeled cases, Symbol Recall@1 was `0.1970`, Recall@5/10 `0.3636`, Recall@20 `0.4242`,
and symbol MRR `0.2866`.

The authorized OpenCode `deepseek-v4-flash-free` run used the same 50 candidate pools and kept all
fallbacks in the denominator. Hybrid File Recall@1 was `0.6267`, Recall@5 `0.8133`, Recall@10
`0.8800`, Recall@20 `0.9300`, and MRR `0.7831`. Fifteen expected-file ranks improved, 35 were
unchanged, and none worsened. On the 29 cases with valid model output, Recall@1 increased from
`0.4080` to `0.7874` and MRR from `0.6095` to `0.9224`.

Only 29 of 50 reranks were valid. Thirteen cases exhausted two attempts after an upstream DFLASH
grammar HTTP 400, and eight exhausted two invalid JSON responses. Every fallback preserved the
deterministic order. Successful final calls used 150,558 input and 69,217 output tokens and
averaged `23.3 s`; failed attempts do not expose token counts. The result therefore supports
ordering quality when a rerank is valid, but the observed 58% success rate does not support a
production-reliability claim. The reviewed artifact is
`benchmarks/results/hybrid-deepseek-v4-flash-v0.13-manifest-v8-50-cases.json`.

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

The retained corrected manifest-v5 snapshot also received paired real-provider reruns:

- OpenCode DeepSeek V4 Flash completed 20/20 valid reranks with no fallback. File Recall@1 was
  `0.4417`, Recall@5 `0.8583`, Recall@10 `0.9250`, Recall@20 `1.0000`, and MRR `0.7257`.
  Average successful model latency was `17.5 s`; the run used 102,925 input and 38,376 output
  tokens.
- Groq GPT-OSS 20B produced 17/20 valid reranks. Three cases exhausted retries with HTTP 429 and
  used the deterministic fallback. File Recall@1 was `0.3917`, Recall@5 `0.8583`, Recall@10
  `0.9250`, Recall@20 `1.0000`, and MRR `0.6794`. Average successful model latency was `870 ms`;
  successful final calls used 82,329 input and 2,763 output tokens.

These are paired localization results on manifest v5, not manifest v7. They support an ordering
improvement over the same v5 deterministic candidate pool, while Recall@20 remains bounded by
deterministic retrieval. A separate three-real-case OpenCode full-schema run succeeded on two
cases; the Typer case exhausted two invalid structured responses and used fallback. The compact
rerank schema is therefore more reliable than the full hypothesis schema.

## Free-model selection

The historical shortlist is deliberately small:

- `deepseek-v4-flash-free` remains the default quality reference because its valid v8 reranks
  raised paired MRR substantially, but the 50-case run returned only 29/50 valid final responses.
  Any production path must retain deterministic fallback and monitor upstream DFLASH grammar
  errors as well as invalid JSON.
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
