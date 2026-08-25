# LLM Evaluation Protocol

Every OpenCode model path must be compared with the existing deterministic baseline before any
quality improvement is claimed.

## Variants

1. `deterministic`: rules, duplicate similarity, AST index, lexical candidate generation, and
   bounded static-graph reranking inside fixed Top-10 bands.
2. `hybrid`: deterministic retrieval followed only by OpenCode
   `deepseek-v4-flash` evidence-ID reranking.

An `llm-only` variant remains future work. Hybrid preserves the deterministic Top-20 as its exact
fallback and appends unique files from a separate Top-40 retrieval pass to a model-only candidate
pool. The Top-20 reserves one slot for a directly supported path; the Top-40 pass reserves three,
without changing the deterministic fallback. The model can promote at most three selected pool
files before the unchanged deterministic order fills the final Top-20. It sends no
`response_format` and accepts one unique plain-text
`RANK:` line; the CLI does not accept a different provider or model. The rank request disables
reasoning, starts with an 8,192-token output budget, and retries once at 20,000 tokens only if the
first completion is truncated. The complete frozen Issue is sent. Under the default 100,000-character
total evidence budget, each of the 40 candidate snippets is limited to 2,500 characters and at most
200 source lines. The provider context window remains an additional request-size limit.

The full Agent analysis path also disables model reasoning before requesting its strict JSON
object. This preserves the 20,000-token completion ceiling for the auditable analysis fields rather
than allowing hidden reasoning to exhaust the response budget before valid JSON is produced.
Normal Agent runs and evaluation both use temperature `0.1` and a 180-second timeout. A structured
analysis response ending with `finish_reason=length` is classified as non-retryable
`output_truncated` instead of being retried with the same exhausted ceiling.
`agent-evaluate --omit-max-tokens` is a diagnostic-only mode that omits the request field and
records `max_output_tokens=null`; it delegates the ceiling to the provider rather than creating a
truly unlimited response.

## Dataset

The current frozen dataset contains 200 closed issues across 58 repositories that link to a fix
pull request. Manifest version 20 stores the complete evaluated Issue snapshot and never fetches
mutable Issue text during a benchmark run. It records:

- issue number, title, body, labels, timestamps, URL, author, and comment count;
- duplicate master, when applicable;
- files changed by the fix;
- 267 reviewed production-file targets and 177 manually reviewed symbols across 143 cases;
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

## Manifest-v20 pool-40 external result

Three authorized v0.30 zero-delay runs evaluated all 200 frozen public cases with OpenCode
`deepseek-v4-flash`, temperature `0.1`, seed `1337`, and the bounded Top-40 protocol. All 600
case-runs returned one valid known-ID `RANK:` line. Of these, 598 succeeded on the first attempt and
two recovered through the bounded truncation retry. No case used fallback, reported an unknown
evidence ID, or failed execution.

| Run | File R@1 | R@5 | R@10 | R@20 | MRR | Pool R | Mean LLM latency | Input / output tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 0.5572 | 0.7972 | 0.8388 | 0.8907 | 0.7483 | 0.9057 | 6.00 s | 3,443,977 / 1,964 |
| 2 | 0.5622 | 0.7922 | 0.8388 | 0.8907 | 0.7521 | 0.9057 | 6.31 s | 3,443,977 / 1,968 |
| 3 | 0.5672 | 0.7963 | 0.8355 | 0.8857 | 0.7543 | 0.9057 | 5.79 s | 3,443,977 / 1,964 |
| Mean | 0.5622 | 0.7952 | 0.8377 | 0.8890 | 0.7516 | 0.9057 | 6.03 s | - |

The corrected deterministic baseline is `0.3510/0.6517/0.7505/0.8665`, with MRR `0.5396`.
The model-only pool contains 234 of 267 production targets; the final Top-20 contains 227, 227,
and 226, compared with 221 in the deterministic Top-20. NumPy `dlpack.c` and pandas
`_arrow_string_mixins.py` are newly promoted in every run. Every run also recovers
`tornado/locks.py`, Pylint `class_checker.py`, and SciPy `_matfuncs.py`; runs 1 and 2 recover
Setuptools `_apply_pyprojecttoml.py`. No deterministic Top-20 ground-truth file is displaced.

Across the three runs, 138/200 complete candidate orders were identical and nine cases changed
expected-file reciprocal rank. Pairwise ordering changes affected 43, 46, and 43 cases. Fixed seed
therefore remains best effort even though protocol success and Recall@20 were stable. Symbol
Recall@20 was `0.4668`; mean symbol MRR was `0.4867`. The runs used 10,331,931 input tokens and
5,896 output tokens in total. Relative to v0.29, mean Recall@20 improves from `0.8807` to `0.8890`,
while Recall@1 decreases from `0.5705` to `0.5622` and MRR from `0.7526` to `0.7516`; the change is
therefore a candidate-recall improvement, not a uniform ranking improvement.

The compact reviewed artifact is
`benchmarks/results/deepseek-v4-flash-pool40-manifest-v20-summary.json`. The three full raw JSON
files were validated locally but are not committed because they duplicate large candidate lists.
The pre-change 36-target miss audit is retained without Issue bodies or snippets at
`benchmarks/results/pool40-miss-taxonomy-manifest-v20.json`.
These measurements establish bounded localization and rank-protocol behavior on this frozen public
suite, not root-cause accuracy or reliability on unseen/private repositories.

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
latency, and category telemetry. They now receive one bounded strict retry because repeated live
runs demonstrated that the fixed-seed service can return valid JSON for the same input after an
invalid response. Transport, HTTP 429, and HTTP 5xx failures remain retryable with bounded
exponential backoff and `retry-after` support. The provider contract now contains five
fields and one hypothesis; duplicate deterministic-candidate metadata and four derivable response
fields were removed while the persisted public model remains compatible. This change has local
contract coverage. The minified response schema fell from 2,376 to 1,905 characters. On the same
three frozen cases, removing the duplicate candidate list reduced serialized user-request size by
37.9% to 42.4%. These are payload-size measurements, not reliability results; the change must not
be described as improving model success until the external three-case suite is repeated after the
provider limit resets.

## Compact-contract external follow-up

On 2026-08-12, the same Starlette, Typer, and Textual suite was run three times against the compact
provider contract, with a 30-second delay between cases. All nine case-runs remain in the reported
denominator. The provider rejected every request with HTTP 429 before inference: 0/9 analyses were
valid, all nine failures were categorized as `rate_limit`, and no input or output tokens were
reported. Each case performed the configured two retryable attempts, for 18 provider attempts in
total. The accumulated provider-call latency was 8,825.983 ms, or 980.665 ms per failed case-run.

All nine failed Agent runs persisted the terminal status and error and passed the SQLite public-JSON
round-trip check. This validates the error-aware retry, telemetry, and failure-persistence path, but
does not measure the compact contract's structured-output reliability because no request reached
model inference. Additional schema or prompt changes are not justified by this result. The same
suite should be repeated only after the provider/account limit resets.

Machine-readable artifacts:

- `benchmarks/results/agent-analysis-compact-v0.21-run1.json`
- `benchmarks/results/agent-analysis-compact-v0.21-run2.json`
- `benchmarks/results/agent-analysis-compact-v0.21-run3.json`

## DeepSeek V4 Flash readiness attempt

On 2026-08-16, the runtime model was changed from the historical
`deepseek-v4-flash-free` endpoint to `deepseek-v4-flash`, local Issue/evidence character
truncation was removed, and the same frozen Starlette, Typer, and Textual full-analysis suite was
run with zero delay between cases. The original `https://opencode.ai/zen/v1/chat/completions`
route returned HTTP 401 before inference for both configured workspaces. Those three failed Agent
runs persisted correctly and remain in the denominator in
`benchmarks/results/agent-analysis-v0.25-deepseek-v4-flash-unbounded-run1.json`.

The runtime was then moved to OpenCode's Go-compatible route,
`https://opencode.ai/zen/go/v1/chat/completions`. A minimal rank request succeeded with 279 input
tokens, 31 output tokens, and 2.17 seconds of provider latency. The first three-case full-analysis
diagnostic used a 4,096-token completion budget and produced one valid result; the two failures
both consumed exactly 4,096 output tokens before failing local JSON validation. At an 8,192-token
budget, the two retained runs completed 3/3 and 2/3 cases. The remaining invalid response again
consumed exactly the full 8,192-token budget, while a separate transport timeout recovered on its
second attempt. These diagnostics are retained in
`agent-analysis-v0.25-deepseek-v4-flash-go-run1.json` and the two
`agent-analysis-v0.25-deepseek-v4-flash-go-8192-run*.json` artifacts.

An intermediate 16,384-token configuration completed three independent zero-delay runs with 9/9
valid analyses, 9/9 first-attempt responses, and 9/9 payloads restored from SQLite. Those runs used
115,827 input tokens and 29,657 output tokens in total. Mean provider latency was 67.14 seconds per
case (population standard deviation 19.15 seconds; range 38.62--104.40 seconds). The retained
artifacts are the three `agent-analysis-v0.25-deepseek-v4-flash-go-16384-run*.json` files.

The requested final 20,000-token configuration was then evaluated with the same fixed cases,
temperature 0.1, seed 1337, and zero delay. Across three runs it produced 8/9 valid analyses, 6/9
first-attempt successes, and 9/9 payloads restored from SQLite. The single failed Typer case
exhausted both attempts with provider `ReadTimeout`; two other cases recovered from one read timeout
on their second attempt. The runs made 12 attempts, used 104,475 input tokens and 27,229 output
tokens, and recorded a mean per-case LLM elapsed time of 164.39 seconds including the failed case
(population standard deviation 117.59 seconds; range 41.62--360.25 seconds). The largest valid
response used 4,928 output tokens, so none approached the 20,000-token ceiling. Every valid
response contained observations for all 20 supplied evidence snippets and exactly one
evidence-grounded hypothesis. The reviewed artifacts are the three
`agent-analysis-v0.25-deepseek-v4-flash-go-20000-run*.json` files.

Fixed seed 1337 remains best effort: every successfully repeated case produced a different
normalized analysis. The 20,000-token result therefore supports endpoint availability and an 8/9
contract-valid rate on this small public three-case suite, while also exposing provider read-timeout
instability. It does not establish deterministic generation or production reliability. Provider
URLs are redacted from structured provider-error messages before artifacts are persisted. Strict
JSON, observation-coverage, and evidence-ID validation failures receive at most one additional
request because the live service is nondeterministic even with a fixed seed; the second response
must pass the unchanged schema and evidence checks in full. No malformed JSON is repaired or
accepted locally.

## Current manifest-v8 rank-only reliability result

The first Go-endpoint full run retained the historical 256/1,024-token rank budget and produced
only 6/50 valid ranks; all 44 fallbacks were `output_truncated`. Raising the two budgets to
4,096/8,192 improved a five-case diagnostic, but an 8,192-token full run still produced only 35/50
valid ranks and 15 truncation fallbacks. These failures remained in the aggregate metrics rather
than being excluded.

The final protocol starts at 8,192 tokens and retries once at 20,000 only after a truncated
completion. Three complete manifest-v8 runs then produced 150/150 valid known-ID ranks with zero
fallback. Their mean File Recall@1/5/10/20 was `0.7367/0.8600/0.9000/1.0000`, with mean MRR
`0.8894`; all three symbol runs produced Recall@1/5/10/20 of
`0.6667/0.6970/0.6970/0.7121` with MRR `0.7424`. The runs made 57, 50, and 50 requests and used
2,500,818 input plus 163,383 output tokens in total.

Despite the fixed seed and disabled reasoning request, only 29/50 full candidate orders were
identical across all repeats. Run 1 also recorded 162,491 output tokens and 28.53 seconds mean LLM
latency, while runs 2 and 3 each recorded 446 output tokens and 1.62/1.46 seconds. The plain rank
contract is therefore reliable on this suite, but hosted token accounting, latency, and exact
ordering remain nondeterministic.

Reviewed artifacts:

- `benchmarks/results/hybrid-deepseek-v4-flash-go-v0.25-rank20000-manifest-v8-50-cases-run1.json`
- `benchmarks/results/hybrid-deepseek-v4-flash-go-v0.25-rank20000-manifest-v8-50-cases-run2.json`
- `benchmarks/results/hybrid-deepseek-v4-flash-go-v0.25-rank20000-manifest-v8-50-cases-run3.json`

## Manifest-v8 full-Agent reliability result

On 2026-08-16, the final 20,000-token protocol was run three times over all 50 frozen manifest-v8
cases with temperature 0.1, seed 1337, no inter-case delay, and at most two strict attempts. All
150 case-runs stayed in the denominator and all 150 terminal Agent payloads passed the SQLite
public-JSON round trip.

| Run | Final valid | First-attempt valid | Attempts | Input tokens | Output tokens | Mean successful LLM latency |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 47/50 | 43/50 | 57 | 1,077,464 | 369,658 | 57.90 s |
| 2 | 50/50 | 46/50 | 54 | 1,055,474 | 306,181 | 58.30 s |
| 3 | 43/50 | 34/50 | 66 | 1,294,740 | 62,128 | 10.51 s |
| Combined | 140/150 | 123/150 | 177 | 3,427,678 | 737,967 | - |

The bounded second request recovered 17 case-runs that failed their first strict contract, raising
aggregate success from 82.00% to 93.33%. Ten case-runs still failed after both attempts: seven
ended in invalid JSON/schema responses, two omitted the required one-to-one evidence-observation
coverage, and one ended in an HTTP 5xx response. Forty of the 50 cases succeeded in all three
runs; the union of failed case IDs contains ten cases. Valid responses contained 19 or 20 evidence
observations because one frozen case supplies 19 readable snippets; every valid response contained
exactly one evidence-linked hypothesis.

The run-level final success range of 86%--100%, first-attempt range of 68%--92%, and large token and
latency shifts show that the fixed seed does not stabilize the hosted service. The retry improves
availability, but also repeats the entire strict request and therefore increases latency and token
use. These results validate the implemented contract, retry telemetry, failure denominator, and
persistence path. They do not score hypothesis correctness and must not be reported as a 93.33%
root-cause accuracy result.

Reviewed artifacts:

- `benchmarks/results/agent-analysis-v0.25-deepseek-v4-flash-go-20000-strict-retry-manifest-v8-50-cases-run1.json`
- `benchmarks/results/agent-analysis-v0.25-deepseek-v4-flash-go-20000-strict-retry-manifest-v8-50-cases-run2.json`
- `benchmarks/results/agent-analysis-v0.25-deepseek-v4-flash-go-20000-strict-retry-manifest-v8-50-cases-run3.json`

### Provider-default output-limit comparison

The same client was also tested without sending `max_tokens`. This does not remove the model or
provider context ceiling; it delegates the completion default to the hosted service and makes that
default subject to provider changes. Three repeated five-case diagnostics produced 5/5, 3/5, and
4/5 valid final analyses, or 12/15 combined. The first diagnostic also incurred two near-timeout
retries and a mean successful LLM latency of 125.61 seconds, while the other two averaged 9.25 and
8.45 seconds.

An authorized full manifest-v8 run then evaluated all 50 cases through the reproducible
`--omit-max-tokens` path. It produced 38/50 valid final analyses, 31/50 first-attempt successes,
69 total attempts, and 50/50 persisted terminal payloads. All 12 final failures were categorized as
invalid structured responses after two attempts. The run recorded 1,409,072 input tokens, 63,612
output tokens, and 9.92 seconds mean successful LLM latency. Its immediately preceding explicit
20,000-token run produced 43/50 final and 34/50 first-attempt successes under the same manifest,
temperature, seed, retry count, and zero-delay settings.

This single full comparison plus the three small repeats provides no evidence that omitting the
field improves contract reliability. The project therefore retains 20,000 as the normal explicit
default for auditability and provider-default stability. It does not claim that 20,000 is an
intrinsically optimal model limit, because the hosted service remained nondeterministic and the two
full protocols were run sequentially rather than as simultaneous paired requests.

Artifacts:

- `benchmarks/results/agent-analysis-v0.25-server-default-output-diagnostic-5-cases-run1.json`
- `benchmarks/results/agent-analysis-v0.25-server-default-output-diagnostic-5-cases-run2.json`
- `benchmarks/results/agent-analysis-v0.25-server-default-output-diagnostic-5-cases-run3.json`
- `benchmarks/results/agent-analysis-v0.25-server-default-output-manifest-v8-50-cases-run1.json`

## Client-derived validation-step contract result

The recurring low-token `invalid_response` failures were next classified without storing model
response content. Pydantic validation types and field paths are now retained as bounded error
metadata, separating invalid JSON from schema validation while excluding response values. A
seven-case reproduction selected the cases that repeatedly failed in both the explicit-20,000 and
provider-default runs. Five of seven failed after two attempts, and every failure was the same
schema violation: `hypothesis.validation_step=missing`. The two successful cases passed on their
first attempt.

`validation_step` is a safety-sensitive but deterministic workflow instruction rather than a
model judgment. It was therefore removed from the provider hypothesis contract and is now derived
locally from the first model-cited evidence location. The generated instruction asks for
inspection plus the smallest existing relevant test without modifying files. The persisted public
`LLMHypothesis` schema remains unchanged. Strict JSON parsing, all other provider fields,
one-observation-per-evidence coverage, evidence-ID validation, and the one-hypothesis limit remain
enforced. No malformed JSON or schema-invalid response is repaired or accepted.

The same seven cases then completed 7/7 on their first attempt. Three independent zero-delay runs
subsequently evaluated all 50 frozen manifest-v8 cases:

| Run | Final valid | First-attempt valid | Attempts | Persisted | Input tokens | Output tokens | Mean LLM latency |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | 50/50 | 50/50 | 50 | 50/50 | 1,002,496 | 44,408 | 8.05 s |
| 2 | 50/50 | 50/50 | 50 | 50/50 | 1,002,496 | 43,986 | 7.38 s |
| 3 | 50/50 | 50/50 | 50 | 50/50 | 1,002,496 | 44,345 | 7.14 s |
| Combined | 150/150 | 150/150 | 150 | 150/150 | 3,007,488 | 132,739 | 7.52 s |

Every valid response retained exactly one hypothesis and one observation for each of the 19 or 20
readable evidence snippets supplied by its case. Combined provider latency had a population
standard deviation of 1.23 seconds and ranged from 4.91 to 11.38 seconds. The result establishes
contract reliability on this frozen public suite across these three sequential runs. It does not
score hypothesis correctness, prove reliability on private or unseen repositories, or make the
hosted model deterministic.

Reviewed artifacts:

- `benchmarks/results/agent-analysis-v0.25-deepseek-v4-flash-go-20000-client-validation-manifest-v8-50-cases-run1.json`
- `benchmarks/results/agent-analysis-v0.25-deepseek-v4-flash-go-20000-client-validation-manifest-v8-50-cases-run2.json`
- `benchmarks/results/agent-analysis-v0.25-deepseek-v4-flash-go-20000-client-validation-manifest-v8-50-cases-run3.json`

## Bounded-input production-default result

After the contract-valid runs above, the normal Agent and benchmark paths were given explicit
production defaults: at most 200 numbered source lines per evidence snippet, 100,000 repository
evidence characters per request, temperature `0.1`, and a 180-second timeout. Issue snapshots remain
complete. Structured analysis now classifies `finish_reason=length` as non-retryable
`output_truncated`, because repeating the same exhausted 20,000-token budget cannot recover it.

Three independent zero-delay 50-case Agent runs produced:

| Run | Final valid | First-attempt valid | Attempts | Persisted | Input tokens | Output tokens | Mean LLM latency |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | 50/50 | 49/50 | 51 | 50/50 | 882,486 | 43,972 | 7.54 s |
| 2 | 50/50 | 50/50 | 50 | 50/50 | 882,486 | 44,143 | 6.95 s |
| 3 | 50/50 | 49/50 | 51 | 50/50 | 882,486 | 45,185 | 11.39 s |
| Combined | 150/150 | 148/150 | 152 | 150/150 | 2,647,458 | 133,300 | - |

The two recovered retries were `werkzeug-duplicate-100-continue` and `typer-option-envvar`. The
latter accumulated 187.57 seconds across a failed first request and successful retry, which confirms
that the old 60-second production timeout was not aligned with the evaluated recovery path. Relative
to the preceding three pre-guard runs, combined input fell 11.97% and the maximum single-case input
fell from 68,407 to 33,408 tokens (51.16%).

The same bounded inputs were then used for three rank-only hybrid runs. All three completed 50/50
model requests with zero fallback, 688,370 input tokens, and 446 output tokens. Every run reported
File Recall@1/5/10/20 of `0.7467/0.8600/0.9000/1.0000`, MRR `0.8983`, and Symbol
Recall@1/5/10/20 of `0.6667/0.6970/0.6970/0.7121` with MRR `0.7424` on 33 labeled
cases. Six of 50 cases nevertheless changed their complete candidate ordering in at least one run,
so seed 1337 remains best effort rather than deterministic generation.

Only the compact aggregate is committed at
`benchmarks/results/deepseek-bounded-input-manifest-v8-summary.json`; the six raw run files were
kept outside Git to avoid further repository growth. These results validate provider contracts,
fallback accounting, and persistence on the frozen public suite. They do not measure hypothesis
correctness or establish behavior on private or unseen repositories.

## Current manifest-v8 deterministic result and retained paired LLM result

Manifest v8 deterministic v0.25 completed three 50-case runs with structurally identical candidates,
symbols, and metrics after timestamps and elapsed fields were excluded. File Recall@1 was
`0.4067`, Recall@5 `0.6900`, Recall@10 `0.7800`, Recall@20 `1.0000`, and MRR `0.6038`. On 33
symbol-labeled cases, Symbol Recall@1 was `0.3485`, Recall@5 `0.5455`, Recall@10 `0.5758`,
Recall@20 `0.7121`, and symbol MRR `0.4873`. The v0.17 function-local import edge recovered
`rich/highlighter.py` at rank 18. v0.18 added bounded shared qualified-call evidence and recovered
`scrapy/utils/decorators.py::_warn_spider_arg` at rank 18. v0.19 added bounded reverse-import
evidence and recovered `celery/worker/pidbox.py` at rank 19. v0.20 added a unique,
same-subsystem package re-export hop from exact Issue paths and recovered
`src/poetry/utils/env/python/manager.py` at rank 17. v0.21 preserved ordered traceback frames and
selected the deepest uniquely resolved function in a uniquely resolved repository file, recovering
`Executor._create_directory_url_reference` for `poetry-relative-directory-url`. v0.22 resolves
exact source-line references against either the indexed checkout or the immutable GitHub commit
named by the Issue. It recovered `WebSocketsSansIOProtocol.handle_connect` and `EnvManager.get`.
v0.23 adds bounded fenced source excerpt evidence constrained by Issue path candidates and unique
file/symbol resolution. It recovered Click's `prompt`. All 50 file lists and the other 49 symbol
lists were unchanged from v0.22, and no labeled symbol regressed.
v0.24 adds uniquely resolved constructor evidence only when a title class and syntactic code call
are backed by construction wording or constructor-docstring semantics. Explicit method evidence
remains stronger. It recovered Pydantic's `TypeAdapter.__init__`; all file lists and the other 49
symbol lists were unchanged from v0.23.
v0.25 adds a bounded adjacent owner-to-method title phrase. Compound owners, non-generic method
terms, production symbols, and a unique strongest within-file match are required. It recovered
pip's `ConfigOptionParser.error`; all file lists and the other 49 symbol lists were unchanged from
v0.24. The new title signal is not used for file retrieval or blame seed selection.

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
They have not yet been rerun against the current v0.25 output. The current deterministic
artifacts are
`benchmarks/results/deterministic-v0.25-qualified-title-50-cases-run1.json`,
`benchmarks/results/deterministic-v0.25-qualified-title-50-cases-run2.json`, and
`benchmarks/results/deterministic-v0.25-qualified-title-50-cases-run3.json`.

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

- `deepseek-v4-flash-free` was the historical benchmark reranker. Its plain rank-only
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
