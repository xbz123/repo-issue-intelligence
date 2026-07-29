# LLM Evaluation Protocol

The optional Groq path must be compared with the existing deterministic baseline before any
quality improvement is claimed.

## Variants

1. `deterministic`: rules, duplicate similarity, AST index, and lexical candidate ranking.
2. `llm-only`: GPT-OSS 20B receives issue text and the same bounded evidence, without the
   deterministic candidate confidence.
3. `hybrid`: deterministic retrieval followed by GPT-OSS 20B structured reranking.

## Dataset

Start with 20-30 closed issues that link to a fix pull request. Record:

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

Save machine-readable results under `benchmarks/results/` and a reviewed summary under
`reports/`. Report all evaluated issues, including failures. A smoke test proves integration
only; it does not prove that the LLM improves investigation quality.

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

This is an integration and grounding smoke test over one synthetic case, not evidence that the
hybrid method outperforms the deterministic baseline. The historical fix-PR benchmark above is
still required before making a quality claim.
