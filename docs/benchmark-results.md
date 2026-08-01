# Real-Project File Localization Benchmark

## Current result: scope-resolved call edges on 32 frozen cases

On 2026-08-01, manifest v7 retained the 32 manually reviewed Issue/Fix-PR cases across 13 public
Python repositories and added qualified class/function ownership to the symbol contract. Every case
stores an immutable Issue snapshot, the fix PR, and the parent of the first PR commit. The runner
checked out all 32 pre-fix commits, verified the reviewed production files, and indexed only
Git-tracked paths.

| Scope | Cases | Recall@1 | Recall@5 | Recall@10 | Recall@20 | MRR | Analysis per case |
|---|---:|---:|---:|---:|---:|---:|---:|
| Overall | 32/32 | 0.4479 | 0.7812 | 0.8906 | 0.9844 | 0.6428 | 4,765 ms |
| Main | 12/12 | 0.5417 | 0.7500 | 0.9167 | 1.0000 | 0.6826 | 6,550 ms |
| Calibration | 7/7 | 0.2857 | 0.7143 | 0.7857 | 0.9286 | 0.4812 | 2,209 ms |
| Generalization | 13/13 | 0.4487 | 0.8462 | 0.9231 | 1.0000 | 0.6930 | 4,492 ms |

Sixteen cases now contain 17 symbol targets taken from reviewed production hunks:

| Labeled scope | Cases | Symbol Recall@1 | Symbol Recall@5 | Symbol Recall@10 | Symbol Recall@20 | Symbol MRR |
|---|---:|---:|---:|---:|---:|---:|
| Overall | 16 | 0.1875 | 0.4688 | 0.4688 | 0.5938 | 0.3099 |
| Main | 4 | 0.2500 | 0.2500 | 0.2500 | 0.5000 | 0.2667 |
| Calibration | 6 | 0.0000 | 0.5000 | 0.5000 | 0.6667 | 0.2042 |
| Generalization | 6 | 0.3333 | 0.5833 | 0.5833 | 0.5833 | 0.4444 |

Symbol aggregates exclude unlabeled cases instead of counting them as misses. A match requires the
exact reviewed file and symbol and retains the parent file's candidate rank. The investigator now
stores both the backward-compatible local name and a qualified identity. Exact qualified
identifiers from inline code, fenced examples, and tracebacks rank before title semantics.
Bare local identifiers receive the same priority only when they are unique in the final candidate
range, constrained by an exact owner, or scoped by a path that uniquely resolves to one repository
file; repeated unscoped names are only semantic tie-breakers. Loose suffix paths still contribute
to file retrieval. Qualified matching preserves case and dot boundaries. Source-content retrieval
also matches dotted values only as complete, case-preserving tokens and excludes their component
terms; syntactic object calls in Issue text separately expose their local callee. The ASGI event
`websocket.accept` therefore neither selects nor contributes terminal-name content evidence for
the Python method `WebSocket.accept`. Bare names also require identifier boundaries: `get`, `set`,
`data`, and `run` no longer match `target`, `reset`, `metadata`, or `runner` as substrings.

Compared with v0.11, all 32 file orderings and all 32 per-file local-symbol candidate lists change.
The scope-safe call contract and direct title-to-path protection improve Recall@1 by `0.0104`,
Recall@20 by `0.0156`, and MRR by `0.0364`; Recall@5 decreases by `0.0678` and Recall@10 by
`0.0156`. Typer's reviewed option file is rank 2, Textual's two reviewed files are ranks 3/18,
Rich's ANSI parser is rank 17, and Pydantic's experimental pipeline is recovered at rank 15.
The stricter evidence contract removes false-positive dotted, ambiguous-basename, unresolved
receiver, and lexically shadowed call scope, but the lower Recall@5 is retained rather than hidden.
The corrected selector still restores `cookie_parser` and `send_wrapper` for the Starlette session
case and selects `WebSocket.send_denial_response` for the denial-response case. Trio's reviewed
`WorkerThread.__init__` target remains a miss because the issue identifies the owning classes but
not that method; allowing the owner to choose across different method names would recreate the
reviewed false-positive mechanism. Rich's `highlighter.py` is the only reviewed production file
absent from the Top-20 candidate pool, and several correct files are retrieved while the
within-file selector chooses a neighboring function. This is useful negative evidence: candidate
generation is close to saturation on the current file suite, while symbol localization remains a
material bottleneck.

Relative to the immediately preceding B1/B2 pre-fix artifact, boundary-aware bare-name matching
changes 25 file orderings and 25 corresponding symbol lists without changing any Recall metric.
The pytest fixture-ordering target improves from rank 8 to 7, while Rich's ANSI target moves from
rank 16 to 17; file MRR changes from `0.6424` to `0.6428` and symbol MRR from `0.3101` to `0.3099`.

The retrieval structure still combines lexical and content evidence, history, within-file call
edges, and bounded two-hop propagation through uniquely resolved concrete functions. The current
call contract retains broad local-name maps for stored-data compatibility, but inference consumes
only `resolved_calls`. Those edges require a unique caller identity, a uniquely resolved local
function or `from ... import ...` target, and a lexical-scope check using Python's symbol table.
Parameters, assignments, loop/exception/pattern targets, local imports, nested definitions,
closures, declared globals, ambiguous aliases, and duplicate qualified callers do not become
inference edges; definition-time rebinding also invalidates a target. Attribute calls such as
`self.rebuild()`, `backend.rebuild()`, or
`view.refresh_layout()` also cannot resolve without receiver analysis. The graph blocks ambiguous
definitions and abstract/interface layers, preserves the strong relation that triggers a Top-10
diversity promotion, and prevents graph-tail expansions from evicting base candidates with direct
path or symbol evidence. The Textual Top-10 loss is retained as honest negative evidence:
recovering it safely requires receiver/type or runtime-dispatch resolution, not terminal-name
matching.
Repository module resolution strips `src/` or `lib/` only when that directory is a source-layout
root without a root `__init__.py`; top-level `src.py`, `lib.py`, and real `src`/`lib` packages keep
their importable names.
Two complete review-fixed v0.12 runs produced identical candidate lists and metrics after
recursively removing timestamps and elapsed-time fields.

Current machine-readable artifacts:

- `benchmarks/cases.json` — current manifest version 7;
- `benchmarks/cases-v0.10-corrected-20-cases.json` — frozen manifest v5 used for paired LLM runs;
- `benchmarks/candidates-v0.11.json` — accepted audit catalog for the 12 new cases;
- `benchmarks/expansion-v0.11-selection.json` — explicit manual acceptance decisions;
- `benchmarks/cases-v0.11-32-cases.json` — retained pre-qualified-symbol manifest v6;
- `benchmarks/cases-v0.12-qualified-symbols-32-cases.json` — named copy of manifest v7;
- `benchmarks/results/deterministic-v0.11-expanded-32-cases.json` — retained v0.11 baseline;
- `benchmarks/results/deterministic-v0.12-qualified-symbols-32-cases.json` — current
  deterministic run;
- `benchmarks/results/hybrid-deepseek-v4-flash-v0.12-manifest-v7-32-cases.json` — current
  manifest-v7 OpenCode DeepSeek rerank;
- `benchmarks/results/hybrid-deepseek-v4-flash-v0.10-manifest-v5-20-cases.json` — corrected-v5
  OpenCode rerank;
- `benchmarks/results/hybrid-gpt-oss-20b-v0.10-manifest-v5-20-cases.json` — corrected-v5 Groq
  rerank;
- `benchmarks/results/hybrid-full-deepseek-v4-flash-v0.10-manifest-v5-3-cases.json` — corrected-v5
  full-schema smoke test.

All results generated from manifest versions 2 and 3 remain committed for provenance but are
superseded as localization-quality evidence. Provider integration observations may remain useful,
but their Recall and MRR values must not be mixed with the corrected v5 or current v7 suites.

## External LLM reranking on current manifest v7

On 2026-08-01, an authorized OpenCode `deepseek-v4-flash-free` run reranked the current
manifest-v7 candidate pool. Every frozen Issue snapshot and candidate snippet came from the 13
public benchmark repositories. All 32 cases remain in the denominator, including cases where the
provider response failed validation and the runner used its deterministic fallback.

| Variant | Cases | Recall@1 | Recall@5 | Recall@10 | Recall@20 | MRR | Valid LLM |
|---|---:|---:|---:|---:|---:|---:|---:|
| Deterministic v0.12 | 32/32 | 0.4479 | 0.7812 | 0.8906 | 0.9844 | 0.6428 | — |
| DeepSeek V4 Flash Free | 32/32 | 0.7135 | 0.8958 | 0.9375 | 0.9844 | 0.8547 | 28/32 |

The same paired run produced the following symbol metrics on the 16 labeled cases:

| Variant | Labeled cases | Symbol Recall@1 | Symbol Recall@5 | Symbol Recall@10 | Symbol Recall@20 | Symbol MRR |
|---|---:|---:|---:|---:|---:|---:|
| Deterministic v0.12 | 16 | 0.1875 | 0.4688 | 0.4688 | 0.5938 | 0.3099 |
| DeepSeek V4 Flash Free | 16 | 0.4688 | 0.5312 | 0.5312 | 0.5938 | 0.5245 |

DeepSeek increased file Recall@1 by `0.2656`, Recall@5 by `0.1146`, Recall@10 by `0.0469`,
and MRR by `0.2119`; Recall@20 was unchanged because reranking cannot add a missing file to the
deterministic Top-20 pool. Twelve expected-file ranks improved, 18 were unchanged, and two
worsened. Five labeled symbol ranks improved, 11 were unchanged, and none worsened. Restricting
the paired calculation to the 28 cases with valid LLM responses still increased file Recall@1
from `0.4405` to `0.7440` and MRR from `0.6394` to `0.8816`, so the aggregate improvement is not
an artifact of dropping fallback cases.

Twenty-four cases completed in one provider attempt. Eight required a second attempt: four then
returned a valid rerank, while `anyio-freethreading-runvar`, `fastapi-pydantic-json-form`,
`click-help-parameter-name`, and `pydantic-typeadapter-union-typing` exhausted two `json_invalid`
responses and used the exact deterministic order. A separate four-case diagnostic rerun recovered
the FastAPI and Pydantic cases on the first attempt; the AnyIO and Click cases again failed twice.
This indicates a mixture of transient and reproducible schema reliability failures for the current
model and prompt.

The 28 successful final calls recorded 145,469 input and 59,682 output tokens and averaged
`20.1 s` of provider latency. Failed attempts do not expose token usage in the stored artifact, so
these counts are not total provider consumption. Analysis-stage latency after repository
preparation averaged `36.3 s` per case. The run uses `seed=1337` as best effort, but it is one full
run and does not establish variance or deterministic provider output.

A later deterministic single-case audit reproduced the current Werkzeug metrics and expected-file
rank but found a metric-neutral rank-20 tail difference from the earlier committed deterministic
artifact (`src/werkzeug/_reloader.py` instead of `src/werkzeug/urls.py`). Two repeated current
runs matched each other. The cause is not established, so exact long-tail candidate reproducibility
remains a documented residual risk even though it does not affect the reported benchmark metrics.

## External LLM reranking on corrected manifest v5

The paired external-model runs intentionally use the frozen 20-case manifest v5. This isolates
model reranking from the later v6 dataset expansion and prevents mutable GitHub Issue text from
entering the comparison.

| Model | Cases | Recall@1 | Recall@5 | Recall@10 | Recall@20 | MRR | Valid LLM |
|---|---:|---:|---:|---:|---:|---:|---:|
| Deterministic v0.10 | 20/20 | 0.3000 | 0.8583 | 0.9000 | 1.0000 | 0.5394 | — |
| DeepSeek V4 Flash Free | 20/20 | 0.4417 | 0.8583 | 0.9250 | 1.0000 | 0.7257 | 20/20 |
| GPT-OSS 20B | 20/20 | 0.3917 | 0.8583 | 0.9250 | 1.0000 | 0.6794 | 17/20 |

DeepSeek increased MRR by `0.1863` and Recall@1 by `0.1417` over the deterministic ordering. All
20 responses passed local JSON and evidence-ID validation with no fallback. Successful requests
averaged `17.5 s` and consumed 102,925 input plus 38,376 output tokens.

GPT-OSS increased MRR by `0.1400` and Recall@1 by `0.0917`. Seventeen responses were valid; three
cases exhausted retry handling after Groq HTTP 429 and used the deterministic fallback. Across the
run there were 24 attempts and four cases retried. Successful calls averaged `870 ms` and consumed
82,329 input plus 2,763 output tokens. The run kept the configured primary key and did not rotate
to an exposed or backup credential.

On the five symbol-labeled v5 cases, DeepSeek reached Symbol Recall@1 `0.5000`, Recall@5 `0.8000`,
and Symbol MRR `0.7111`; GPT-OSS reached `0.3000`, `0.8000`, and `0.6111`. Both retained Symbol
Recall@20 `1.0000`. These are small-sample reranking results, not evidence that either model can
recover a symbol or file missing from deterministic retrieval.

A separate three-case DeepSeek smoke test exercised the full investigation schema rather than the
compact rerank contract. Two cases returned valid full analyses; Typer exhausted two invalid
structured-output attempts and fell back. The result supports keeping localization reranking and
full hypothesis generation as separate evaluated contracts.

### Superseded Retrieval v4 DeepSeek Hybrid result

DeepSeek V4 Flash Free reranked the Retrieval v4 candidate pool through OpenCode's
OpenAI-compatible chat-completions endpoint:

| Scope | Cases | Recall@1 | Recall@5 | Recall@10 | Recall@20 | MRR | Valid LLM |
|---|---:|---:|---:|---:|---:|---:|---:|
| Overall | 20/20 | 0.5917 | 0.9833 | 1.0000 | 1.0000 | 0.8500 | 20/20 |
| Main | 7/7 | 0.9286 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 7/7 |
| Calibration | 4/4 | 0.2500 | 1.0000 | 1.0000 | 1.0000 | 0.6250 | 4/4 |
| Generalization | 9/9 | 0.4815 | 0.9630 | 1.0000 | 1.0000 | 0.8333 | 9/9 |

Compared with deterministic Retrieval v4, Hybrid improved Recall@1 by `0.2417`, Recall@5 by
`0.1250`, Recall@10 by `0.1250`, and MRR by `0.2837`. All 20 requests passed local JSON and
evidence-ID validation on the first attempt; there were no fallbacks. The run consumed 103,713
input and 38,254 output tokens and averaged `16.5 s` of model latency.

The three newly retrieved files were all used by the model: Textual's expected files ranked 1 and
3, AnyIO's ranked 2 and 4, and Rich's ranked 2 and 4. The v0.6 run is not a paired proof of model
improvement over v0.5 because OpenCode's supplied seed is best effort and repeated outputs vary.
The deterministic Recall@20 change is the attributable retrieval result.

### Repeated-run stability and free-model screen

Three complete DeepSeek Retrieval v3 runs produced:

| Metric | Mean | Sample std. dev. |
|---|---:|---:|
| Recall@1 | 0.6250 | 0.0289 |
| Recall@5 | 0.9139 | 0.0096 |
| Recall@10 / Recall@20 | 0.9250 | 0.0000 |
| MRR | 0.8408 | 0.0287 |
| Successful-request latency | 16.3 s | 1.5 s |

Across 60 case-runs, 59 returned valid model output and one used deterministic fallback. These
statistics include all three runs, not the best run.

The five-case screen used the same frozen Typer, Textual, AnyIO, and Rich cases:

| Model | Recall@1 | Recall@5 | MRR | Valid LLM | Model latency |
|---|---:|---:|---:|---:|---:|
| DeepSeek V4 Flash Free | 0.5000 | 0.8000 | 0.8000 | 5/5 | 12.4 s |
| Nemotron 3 Ultra Free | 0.5000 | 0.8000 | 0.8000 | 5/5 | 26.5 s |
| North Mini Code Free | 0.3000 | 0.8000 | 0.6167 | 3/5 | 40.3 s |
| Ling 3.0 Flash Free | — | — | — | 0/5 | — |

Nemotron matched DeepSeek's localization metrics but was about 2.1 times slower. North timed out
twice and was slower on successful requests. Ling's upstream provider rejected all ten attempts
with HTTP 400, so its deterministic fallback rankings are deliberately not reported as model
quality. DeepSeek remains the default free reranker for this benchmark.

OpenCode currently lists these model IDs as free and documents that free-period data may be used
for model improvement. The project therefore sends only public Issue snapshots and public source
evidence. See the [OpenCode Zen model documentation](https://opencode.ai/docs/zen).

### Historical Retrieval v3 GPT-OSS Hybrid result

GPT-OSS 20B reranked the same fixed candidate pool:

| Scope | Cases | Recall@1 | Recall@5 | Recall@10 | Recall@20 | MRR | Valid LLM |
|---|---:|---:|---:|---:|---:|---:|---:|
| Overall | 20/20 | 0.4167 | 0.8583 | 0.8750 | 0.9250 | 0.6738 | 17/20 |
| Main | 7/7 | 0.6429 | 1.0000 | 1.0000 | 1.0000 | 0.8571 | 7/7 |
| Calibration | 4/4 | 0.0000 | 0.6250 | 0.6250 | 0.8750 | 0.2438 | 2/4 |
| Generalization | 9/9 | 0.4259 | 0.8519 | 0.8889 | 0.8889 | 0.7222 | 8/9 |

Compared with deterministic Retrieval v3, Hybrid improved Recall@1 by 0.0667 and MRR by 0.1075.
Recall@5, Recall@10, and Recall@20 were unchanged. The aggregate gain came from the main and
generalization tiers; calibration MRR decreased, so the result does not support a claim that LLM
reranking improves every project or case.

Seventeen cases returned valid structured output. Three cases exhausted both attempts and used
the deterministic fallback: both Typer cases returned HTTP 400 `output_parse_failed`, while the
AnyIO free-threading case returned HTTP 400 `tool_use_failed`. There were no 429 responses. The
successful responses consumed 83,592 input and 2,581 output tokens and averaged 798 ms of model
latency. Average analysis time was 11,014 ms because the three failed cases included the configured
retry backoff; it excludes repository preparation and the 62-second inter-case quota delay.

The run used `openai/gpt-oss-20b`, a 12,000-character evidence budget, a 1,600-token completion
limit, low reasoning effort, `temperature=0.1`, `seed=1337`, at most two attempts, and a 62-second
inter-case delay. Historical model results below remain useful for model-size decisions, but
their nine-case metrics must not be mixed with this expanded result.

### Historical Retrieval v3 OpenCode DeepSeek V4 Flash result

DeepSeek V4 Flash Free reranked the same frozen candidate pool through OpenCode's
OpenAI-compatible chat-completions endpoint:

| Scope | Cases | Recall@1 | Recall@5 | Recall@10 | Recall@20 | MRR | Valid LLM |
|---|---:|---:|---:|---:|---:|---:|---:|
| Overall | 20/20 | 0.6417 | 0.9250 | 0.9250 | 0.9250 | 0.8458 | 20/20 |
| Main | 7/7 | 0.7857 | 1.0000 | 1.0000 | 1.0000 | 0.8929 | 7/7 |
| Calibration | 4/4 | 0.5000 | 0.8750 | 0.8750 | 0.8750 | 0.7083 | 4/4 |
| Generalization | 9/9 | 0.5926 | 0.8889 | 0.8889 | 0.8889 | 0.8704 | 9/9 |

Compared with deterministic Retrieval v3, DeepSeek improved Recall@1 by `0.2917`, Recall@5 by
`0.0667`, Recall@10 by `0.0500`, and MRR by `0.2795`; Recall@20 remained bounded by the same
candidate pool. Compared with GPT-OSS 20B, it improved Recall@1 by `0.2250` and MRR by `0.1720`.
Unlike the GPT-OSS run, every tier improved over its deterministic baseline.

All 20 cases returned locally validated JSON and evidence IDs; one case succeeded on its second
attempt and none fell back. The run used a 16,000-character evidence budget, 4,096-token completion
limit, 60-second timeout, `temperature=0.1`, best-effort `seed=1337`, and zero inter-case delay.
It consumed 104,570 input and 32,981 output tokens. Successful model requests averaged `14.6 s`,
about 18 times the GPT-OSS 20B model latency (`0.8 s`), so the quality improvement has a material
latency and token tradeoff.

The model is free for a limited period, and OpenCode states that free-period data may be used for
model improvement. This project therefore sends only public benchmark Issue text and public source
evidence. Repeated targeted runs produced different rankings despite the supplied seed, so the
committed artifact is one complete run rather than a claim of deterministic model behavior.

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

Thirty-two cases across 13 repositories are enough for repeatable error analysis but remain too
small for a broad general-quality claim. The suite contains only Python projects, and its accepted
cases depend on public Issue/Fix-PR relationships that are easier to audit than many real
maintenance tasks. The Hybrid path only reranks evidence deterministic retrieval already found;
it cannot recover Pydantic's missed pipeline file or any other file outside the 20-file pool.
Token totals include successful final responses, not every development-time request.

The next retrieval iteration should focus on the measured failures rather than adding prompt
complexity: runtime/backend dispatch, multi-symbol ranking, semantic test-to-source mapping, and
cross-language relations. Further expansion should target 40-50 cases only when the new cases add
those failure modes. Candidate-pool changes must continue to be evaluated separately from model
reranking, and v7 LLM comparisons should use repeated runs rather than selecting a single favorable
sample.

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
