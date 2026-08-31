# Hypothesis Quality Evaluation

The full Agent evaluator reports file-grounding quality separately from provider-contract
reliability. A valid JSON response and successful SQLite round trip do not imply that a hypothesis
uses evidence from a reviewed fix file.

## Automated metrics

- `expected_file_evidence_recall`: fraction of reviewed fix files present in the snippets supplied
  to the model. This is the deterministic retrieval ceiling for the request.
- `hypothesis_expected_file_recall`: fraction of reviewed fix files whose snippets are cited by the
  persisted hypothesis.
- `hypothesis_expected_file_hit`: whether the hypothesis cites at least one reviewed fix file.
- `overall_hypothesis_expected_file_hit_rate`: hit rate over every selected case, with provider,
  schema, and no-evidence failures retained as misses.
- `hypothesis_hit_rate_when_expected_evidence_available`: hypothesis hit rate only among cases
  where at least one reviewed fix file was available to cite.

These are file-grounding metrics. They do not judge whether the free-text causal explanation
matches the fix mechanism.

## Frozen evaluation slice

`benchmarks/hypothesis-quality-v1-24-cases.json` contains 24 manifest-v20 generalization cases from
24 repositories. Selection was result-blind and uses three fixed strata:

- eight cases with non-Python production targets;
- eight Python multi-file cases;
- eight Python single-file cases.

The file and symbol labels inherit the reviewed manifest-v20 ground truth. This slice was selected
from the existing benchmark and therefore is not independent retrieval held-out data. The selection
record and manual scoring rubric are in `benchmarks/hypothesis-quality-v1-review.json`.

Run the public-repository evaluation only after authorizing external evidence transfer:

```bash
uv run rii agent-evaluate benchmarks/hypothesis-quality-v1-24-cases.json \
  --llm-delay-seconds 0 \
  --output benchmarks/results/hypothesis-quality-v1-latest.json
```

All provider/schema failures remain in the overall hit-rate denominator. Valid-response-only and
evidence-available conditional rates are reported separately. The provider contract, the rule
requiring one observation per evidence item, evidence-ID validation, and the one-hypothesis limit
are unchanged.

## Manual rubric

For each valid response, reviewers score three dimensions from 0 to 2:

1. hypothesis correctness: contradicted, plausible but incomplete, or matches the reviewed fix;
2. evidence sufficiency: unsupported, partially supported, or directly supported;
3. missing-evidence quality: irrelevant, useful, or actionable and discriminating.

Manual scores must be recorded separately from automated metrics and must not be inferred from
contract success.

## First reviewed result

One authorized zero-delay DeepSeek V4 Flash run completed 24/24 cases on the first attempt and
restored all 24 terminal Agent payloads from SQLite. Twenty cases supplied evidence from at least
one reviewed fix file, and 18 hypotheses cited at least one reviewed fix file. Overall hypothesis
file-hit rate was `0.7500`; conditioned on reviewed fix evidence being available, it was `0.9000`.
Mean expected-file evidence recall was `0.7396`, and mean hypothesis expected-file recall was
`0.5868`.

By stratum, evidence/hypothesis hits were 5/4 for non-Python, 7/7 for Python multi-file, and 8/7
for Python single-file cases. Four misses were deterministic evidence-retrieval failures; two had
reviewed fix evidence available but the hypothesis cited a different file.

A single reviewer compared each hypothesis with the frozen Issue and the public fix PR metadata.
Sixteen hypotheses received correctness score 2, seven received score 1, and one received score 0.
Mean 0-2 scores were `1.6250` for correctness, `1.2917` for evidence sufficiency, and `1.7083` for
missing-evidence quality. These manual scores have no inter-rater reliability estimate and must not
be presented as a production accuracy claim. The compact result is
`benchmarks/results/hypothesis-quality-v1-deepseek-run1-summary.json`; raw response content remains
outside Git.
