# Real-Project File Localization Benchmark

## Result

On 2026-07-29, both variants completed all nine frozen Issue/Fix-PR cases.

| Variant | Cases | File Recall@1 | File Recall@5 | MRR | End-to-end per case |
|---|---:|---:|---:|---:|---:|
| Deterministic | 9/9 | 0.2222 | 0.4444 | 0.3518 | 451 ms |
| Hybrid, GPT-OSS 20B rerank | 9/9 | 0.2778 | 0.4444 | 0.4259 | 1,063 ms |

Hybrid improved the first relevant file's ordering but did not retrieve any additional expected
files. This supports a limited conclusion: the small model is useful as a reranker over retrieved
evidence, while deterministic candidate generation remains the main recall bottleneck.

The Hybrid run produced nine of nine valid structured responses with no fallback. Successful model
requests averaged 589 ms and consumed 21,980 input tokens plus 1,237 output tokens in total.
End-to-end time excludes the configured 30-second inter-case quota delay.

## Dataset and protocol

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

Each case records a public closed issue, its linked fix PR, the PR parent commit used as the
pre-fix checkout, the issue `updated_at` value, and source files changed by the fix. The runner
verifies the exact checkout SHA and confirms that every expected source file exists before
evaluation. Pull-request test and documentation files are not treated as required source-file
labels.

File Recall@K is the macro-average fraction of each case's expected source files present in the
first K candidates. MRR uses the rank of the first expected source file. The manifest and raw
outputs are:

- `benchmarks/cases.json`
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

Nine cases are too few for a strong general quality claim. The Hybrid path only reranks evidence
that deterministic retrieval already found, so unchanged Recall@5 is expected. Token totals include
successful final requests only; they do not estimate development-time failed requests.

The next retrieval iteration should add exact file-path and stack-trace extraction, CamelCase and
snake_case token splitting, repository content search, and a wider pre-rerank candidate pool. The
same frozen cases should then be rerun before expanding the benchmark to 20-30 issues and adding
symbol-level labels.
