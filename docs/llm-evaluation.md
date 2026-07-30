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

The current frozen dataset contains 20 closed issues across seven repositories that link to a fix
pull request. Manifest version 3 stores the complete evaluated Issue snapshot and never fetches
mutable Issue text during a benchmark run. It records:

- issue number, title, body, labels, timestamps, URL, author, and comment count;
- duplicate master, when applicable;
- files changed by the fix;
- symbols changed by the fix, when recoverable;
- repository commit used for indexing;
- the labeling source and any ambiguity.

Repository preparation verifies the frozen SHA, and evaluation indexes only Git-tracked paths so
ignored artifacts in reused workspaces cannot change candidate rankings.

Candidate discovery can reject invalid pairs, derive a proposed pre-fix SHA, and identify eligible
production files, but it cannot accept benchmark ground truth. Every accepted case must appear in
a committed manual selection file with Issue/PR relationship and changed-file review notes.

Do not include API keys, private repository content, or unreviewed generated labels. The original
nine-case manifest version 2 is preserved as `benchmarks/cases-v0.3.json` for historical model
comparisons.

## Metrics

- Issue-type accuracy.
- Reproduction-completeness accuracy.
- Duplicate precision, recall, and F1.
- File Recall@1 and Recall@5.
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

On the current 20-case manifest version 3, GPT-OSS 20B Hybrid reranking completed all cases with
17 valid model responses and three deterministic fallbacks. It improved Recall@1 from `0.3500`
to `0.4167` and MRR from `0.5663` to `0.6738`; Recall@5, Recall@10, and Recall@20 were unchanged.
The calibration tier regressed, so the supported claim is an aggregate ordering improvement, not
universal improvement across projects.

On 2026-07-30, three complete DeepSeek Retrieval v3 runs established best-effort seed stability:
mean Recall@1 was `0.6250 ± 0.0289`, Recall@5 `0.9139 ± 0.0096`, Recall@10/20 `0.9250 ±
0.0000`, and MRR `0.8408 ± 0.0287` (sample standard deviation). Fifty-nine of 60 case-runs
returned valid model output; one used deterministic fallback.

After Retrieval v4 raised deterministic Recall@20 to `1.0000`, a new DeepSeek run achieved
Recall@1 `0.5917`, Recall@5 `0.9833`, Recall@10/20 `1.0000`, and MRR `0.8500`. All 20
responses validated on their first attempt with no fallback. This run consumed 103,713 input and
38,254 output tokens and averaged `16.5 s` model latency.

## Free-model selection

The initial shortlist is deliberately small:

- `deepseek-v4-flash-free` remains the default: 5/5 valid screening responses, MRR `0.8000`,
  and `12.4 s` average latency on the screening subset.
- `nemotron-3-ultra-free` matched DeepSeek's screening metrics but averaged `26.5 s`, so it does
  not justify a full run yet.
- `north-mini-code-free` returned only 3/5 valid responses and averaged `40.3 s` when successful.
- `ling-3.0-flash-free` returned upstream HTTP 400 for all ten attempts. Its fallback metrics are
  not model measurements.
- `big-pickle` is not a primary benchmark model because its opaque identity weakens
  reproducibility and interview explainability.

The screen used five frozen high-discrimination cases and the same rerank contract. Free-model
data remains limited to public Issue and repository content because OpenCode documents that
free-period data may be used for model improvement.
