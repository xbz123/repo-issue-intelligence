# LLM Evaluation Protocol

The optional Groq path must be compared with the existing deterministic baseline before any
quality improvement is claimed.

## Variants

1. `deterministic`: rules, duplicate similarity, AST index, and lexical candidate ranking.
2. `hybrid`: deterministic retrieval followed by GPT-OSS 20B evidence-ID reranking.

An `llm-only` variant remains future work. The implemented Hybrid benchmark cannot discover files
outside the deterministic candidate pool.

## Dataset

The current frozen dataset contains nine closed issues that link to a fix pull request. It records:

- issue number and text;
- duplicate master, when applicable;
- files changed by the fix;
- symbols changed by the fix, when recoverable;
- repository commit used for indexing;
- the labeling source and any ambiguity.

Do not include API keys, private repository content, or unreviewed generated labels.

## Metrics

- Issue-type accuracy.
- Reproduction-completeness accuracy.
- Duplicate precision, recall, and F1.
- File Recall@1 and Recall@5.
- Invalid structured-response rate.
- Unknown evidence-reference rate.
- Input and output tokens per issue.
- End-to-end latency per issue.

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

The real-project report also contains a fixed-seed GPT-OSS 20B/120B comparison and a separate
three-case full-schema stability smoke test. Model-size conclusions must not mix the localization
and schema-reliability endpoints.
