# Real-Project File Localization Benchmark

## Current result: Retrieval v3 on the 20-case suite

On 2026-07-30, the deterministic runner completed all 20 frozen Issue/Fix-PR cases across
Starlette, Typer, Textual, AnyIO, FastAPI, pytest, and Rich.

| Scope | Cases | Recall@1 | Recall@5 | Recall@10 | Recall@20 | MRR | Analysis per case |
|---|---:|---:|---:|---:|---:|---:|---:|
| Overall | 20/20 | 0.3500 | 0.8583 | 0.8750 | 0.9250 | 0.5663 | 1,095 ms |
| Main | 7/7 | 0.5714 | 1.0000 | 1.0000 | 1.0000 | 0.7190 | 1,065 ms |
| Calibration | 4/4 | 0.1250 | 0.6250 | 0.6250 | 0.8750 | 0.4105 | 625 ms |
| Generalization | 9/9 | 0.2778 | 0.8519 | 0.8889 | 0.8889 | 0.5167 | 1,327 ms |

The expansion added 11 manually reviewed cases to the historical nine: three AnyIO, three
FastAPI, three pytest, and two Rich cases. Every case has a frozen public Issue snapshot, linked
fix PR, reviewed production-file labels, and a pre-fix SHA. The discovery pipeline rejected
blocking failures automatically but never auto-accepted ground truth; the committed selection
file records the manual acceptance notes.

Retrieval v3 adds local Python import resolution, imported-symbol call evidence, bounded call-name
definition links, two-hop import evidence, and matching-test imports. Graph bonuses rerank only
inside the original lexical ranks 1-10 and 11-20. This preserves Recall@10 and Recall@20 by
construction while improving ordering: versus the committed v0.4 output, Recall@1 improved by
0.0750, Recall@5 by 0.2666, and MRR by 0.0738.

Three expected files remain outside the lexical Top-20 pool: Textual's `_compositor.py`, AnyIO's
`_asyncio.py` in the free-threading case, and Rich's `highlighter.py`. The index can observe static
relations involving these files, but the bounded reranker deliberately cannot inject them into
the candidate pool. Recovering them requires candidate-generation evidence such as reverse
references, runtime/backend dispatch, Git history, or a controlled pool expansion—not stronger
reranking weights.

The machine-readable artifacts are:

- `benchmarks/cases.json` — current manifest version 3;
- `benchmarks/cases-v0.3.json` — preserved 9-case manifest version 2;
- `benchmarks/expansion-v0.4-selection.json` — explicit manual selections;
- `benchmarks/candidates-v0.4.json` — accepted candidate audit records;
- `benchmarks/results/deterministic-v0.4-20-cases.json` — pre-graph deterministic output;
- `benchmarks/results/deterministic-v0.5-graph-20-cases.json` — current deterministic output.

The 20-case Hybrid run has not yet been recorded. Historical model results below remain useful for
integration and model-size decisions, but they must not be presented as LLM results on the
expanded dataset.

## Historical 9-case result: Retrieval v2

Retrieval v2 was evaluated on 2026-07-30 without changing the nine frozen cases or their expected
fix files.

| Variant | Cases | Recall@1 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|
| Retrieval v1, deterministic | 9/9 | 0.2222 | 0.4444 | — | — | 0.3518 |
| Retrieval v2, deterministic | 9/9 | 0.2222 | 0.7593 | 0.9444 | 0.9444 | 0.5083 |
| Retrieval v2, GPT-OSS 20B rerank | 9/9 | 0.5000 | 0.8148 | 0.9444 | 0.9444 | 0.8333 |

The deterministic improvement came from exact stack-trace/source-path extraction, normalized
CamelCase, snake_case, dotted and plural identifiers, bounded repository content matching,
source-over-test preference, and a 20-file candidate pool. Recall@5 improved by 0.3149 absolute
and MRR by 0.1565 compared with v1. In the latest manifest-v2 rerun, average deterministic
analysis time was 835 ms per case; the earlier v1 run averaged 451 ms.

GPT-OSS 20B then improved ordering within the same candidate pool: Recall@1 increased by 0.2778
and MRR by 0.3250 over deterministic Retrieval v2. Eight requests returned valid structured
responses on the first attempt and averaged 793 ms. The ninth case hit Groq HTTP 429 twice and
used the deterministic fallback, so the aggregate reports 8/9 model successes rather than
concealing the quota failure. A separate retry after a 60-second backoff also received 429,
evidence of an unresolved provider quota window rather than a schema failure.

The v2 Hybrid run used `openai/gpt-oss-20b`, a 16,000-character total evidence budget, a
600-character per-candidate cap, a 1,600-token completion limit, low reasoning effort,
`temperature=0.1`, `seed=1337`, and a 40-second inter-case delay. The raw artifacts are:

- `benchmarks/results/deterministic-retrieval-v2.json`
- `benchmarks/results/hybrid-20b-retrieval-v2.json`
- `benchmarks/results/hybrid-20b-retrieval-v2-kitty-retry.json`
- `benchmarks/results/retrieval-v2-comparison.json`

## Retrieval v1 baseline

On 2026-07-29, both variants completed all nine frozen Issue/Fix-PR cases.

| Variant | Cases | File Recall@1 | File Recall@5 | MRR | Analysis per case |
|---|---:|---:|---:|---:|---:|
| Deterministic | 9/9 | 0.2222 | 0.4444 | 0.3518 | 451 ms |
| Hybrid, GPT-OSS 20B rerank | 9/9 | 0.2778 | 0.4444 | 0.4259 | 1,063 ms |

Hybrid improved the first relevant file's ordering but did not retrieve any additional expected
files. This supports a limited conclusion: the small model is useful as a reranker over retrieved
evidence, while deterministic candidate generation remains the main recall bottleneck.

The Hybrid run produced nine of nine valid structured responses with no fallback. Successful model
requests averaged 589 ms and consumed 21,980 input tokens plus 1,237 output tokens in total.
Analysis time starts inside `evaluate_case`, after repository preparation, and excludes clone,
fetch, checkout, and the configured 30-second inter-case quota delay.

## Historical 9-case dataset and protocol

The projects have distinct roles:

- Starlette: four main benchmark cases covering middleware, responses, sessions, requests, and
  protocol behavior.
- Typer: two simpler calibration cases covering option environment variables and Rich help.
- Textual: three generalization cases with multi-file event, rendering, and terminal-protocol
  behavior.

| Tier | Issue | Fix PR | Expected source files |
|---|---|---|---|
| Starlette main | [#3048](https://github.com/encode/starlette/issues/3048) | [#3189](https://github.com/encode/starlette/pull/3189) | `starlette/responses.py` |
| Starlette main | [#2019](https://github.com/encode/starlette/issues/2019) | [#3166](https://github.com/encode/starlette/pull/3166) | `starlette/middleware/sessions.py`, `starlette/requests.py` |
| Starlette main | [#2516](https://github.com/encode/starlette/issues/2516) | [#2620](https://github.com/encode/starlette/pull/2620) | `starlette/middleware/base.py` |
| Starlette main | [#2977](https://github.com/encode/starlette/issues/2977) | [#3029](https://github.com/encode/starlette/pull/3029) | `starlette/requests.py` |
| Typer calibration | [#1787](https://github.com/fastapi/typer/issues/1787) | [#1788](https://github.com/fastapi/typer/pull/1788) | `typer/core.py` |
| Typer calibration | [#1159](https://github.com/fastapi/typer/issues/1159) | [#1356](https://github.com/fastapi/typer/pull/1356) | `typer/rich_utils.py` |
| Textual generalization | [#6452](https://github.com/Textualize/textual/issues/6452) | [#6455](https://github.com/Textualize/textual/pull/6455) | `src/textual/screen.py`, `src/textual/widget.py` |
| Textual generalization | [#6205](https://github.com/Textualize/textual/issues/6205) | [#6206](https://github.com/Textualize/textual/pull/6206) | `src/textual/_compositor.py`, `src/textual/widget.py` |
| Textual generalization | [#6417](https://github.com/Textualize/textual/issues/6417) | [#6542](https://github.com/Textualize/textual/pull/6542) | `src/textual/_keyboard_protocol.py`, `src/textual/_xterm_parser.py`, `src/textual/drivers/linux_driver.py` |

Each case records a complete snapshot of the public closed issue (title, body, labels, timestamps,
URL, author, and comment count), its linked fix PR, the PR parent commit used as the pre-fix
checkout, and source files changed by the fix. The runner uses the snapshot directly rather than
fetching mutable issue text, verifies the exact checkout SHA, confirms that every expected source
file exists, and indexes only paths returned by `git ls-files`. Pull-request test and documentation
files are not treated as required source-file labels.

File Recall@K is the macro-average fraction of each case's expected source files present in the
first K candidates. MRR uses the rank of the first expected source file. The preserved manifest
and raw outputs are:

- `benchmarks/cases-v0.3.json`
- `benchmarks/results/deterministic-v1.json`
- `benchmarks/results/hybrid-v1.json`

The Hybrid run used `openai/gpt-oss-20b`, a 6,000-character evidence budget, a 600-token completion
budget, low reasoning effort, at most two attempts, and a 30-second delay between cases and failed
retries.

## Results by project role

| Tier | Variant | Recall@1 | Recall@5 | MRR |
|---|---|---:|---:|---:|
| Starlette main | Deterministic | 0.2500 | 0.3750 | 0.3750 |
| Starlette main | Hybrid | 0.2500 | 0.3750 | 0.3750 |
| Typer calibration | Deterministic | 0.5000 | 0.5000 | 0.5000 |
| Typer calibration | Hybrid | 0.5000 | 0.5000 | 0.5000 |
| Textual generalization | Deterministic | 0.0000 | 0.5000 | 0.2222 |
| Textual generalization | Hybrid | 0.1667 | 0.5000 | 0.4444 |

The aggregate improvement came from Textual. In the `remove_children` case, GPT-OSS moved
`src/textual/widget.py` from rank 3 to rank 1. Starlette and Typer did not improve at the aggregate
tier level.

## What failed during protocol development

The first Hybrid protocol reused the full investigation schema. It required evidence observations,
contradictions, hypotheses, evidence citations, missing evidence, and validation steps in one
response. GPT-OSS 20B frequently returned a hypothesis with an empty `evidence_ids` array, which
Groq correctly rejected with HTTP 400 `json_validate_failed`. Rapid retries also caused HTTP 429
responses under the configured token-per-minute limit.

The final localization protocol separates concerns:

1. deterministic code retrieves bounded candidate evidence;
2. the LLM returns only a summary and an ordered list of supplied evidence IDs;
3. full hypothesis generation remains in the Agent workflow and is evaluated separately.

This reduced the final run to one successful request per case. It also prevents an unrelated
hypothesis-format failure from being counted as a file-localization failure.

## Limitations and next experiment

Twenty cases across seven repositories are materially better for error analysis but still too few
for a strong general quality claim. The Hybrid path only reranks evidence that deterministic
retrieval already found and cannot recover a file outside the 20-file candidate pool. Token totals
include successful final requests only; they do not estimate development-time failed requests.

The next retrieval iteration should add reverse-reference and runtime/backend-dispatch evidence,
inspect Git history and related tests, and label symbols for the most reliable cases. Candidate
pool expansion should be evaluated separately from reranking so Recall@20 changes remain
attributable. After that, expand toward 30-50 cases.

## GPT-OSS 20B versus 120B

A paired model-size experiment used the same nine cases, evidence, prompt, low reasoning effort,
6,000-character evidence budget, 600-token output budget, `temperature=0.1`, `seed=1337`, and
30-second quota delay.

| Model | Recall@1 | Recall@5 | MRR | Valid responses | Model latency | Output tokens |
|---|---:|---:|---:|---:|---:|---:|
| GPT-OSS 20B | 0.2778 | 0.4444 | 0.4259 | 9/9 | 713 ms | 1,145 |
| GPT-OSS 120B | 0.2778 | 0.4444 | 0.4259 | 9/9 | 830 ms | 1,699 |

All nine cases had the same first-relevant-file rank. Five cases changed the order of non-decisive
Top-5 candidates, but no quality metric changed. In this bounded reranking task, 120B was 16.31%
slower and generated 48.38% more output tokens without a localization gain. The evidence supports
keeping 20B as the default reranker.

The API returned multiple `system_fingerprint` values for each run, so the fixed seed is
best-effort reproducibility rather than a guarantee of an identical backend.

### Full investigation schema smoke test

One representative case from each project was also run through the full hypothesis schema.

| Model | Final success | First-attempt success | Fallbacks | Successful-call latency |
|---|---:|---:|---:|---:|
| GPT-OSS 20B | 3/3 | 2/3 | 0 | 1,010 ms |
| GPT-OSS 120B | 3/3 | 3/3 | 0 | 1,946 ms |

This is only a three-case stability smoke test. It provides a preliminary signal that 120B may be
more reliable for the complex schema, but it is insufficient to justify routing all investigations
to 120B. The current runner records that a retry happened but not the intermediate provider error
type, so the single 20B retry cannot be attributed specifically to JSON schema validation.

The paired artifacts are:

- `benchmarks/results/hybrid-20b-seed1337-v1.json`
- `benchmarks/results/hybrid-120b-seed1337-v1.json`
- `benchmarks/results/hybrid-full-20b-seed1337-smoke-v1.json`
- `benchmarks/results/hybrid-full-120b-seed1337-smoke-v1.json`
- `benchmarks/results/model-comparison-seed1337-v1.json`
