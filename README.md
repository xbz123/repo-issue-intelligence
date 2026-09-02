# Repo Issue Intelligence

Repository-aware GitHub issue prioritization and investigation, built around an explicit two-stage workflow: rank every open issue cheaply, then investigate only the highest-value issues against the codebase.

The project is an initial, runnable Agent MVP. It does not require an LLM API to produce useful results. Priority decisions are explainable, repository evidence is collected deterministically, and root causes are represented as hypotheses rather than unsupported conclusions. An optional OpenCode DeepSeek V4 Flash analysis step can inspect selected code evidence for Top-K issues, while the rank-only hybrid benchmark can use Codex CLI GPT-5.6-Luna without changing the offline baseline.

## What it does

- Synchronizes open GitHub issues with pagination and pull-request filtering.
- Detects likely duplicate issue pairs using title similarity and token overlap.
- Separates severity, urgency, and engineering priority.
- Applies weighted scoring plus hard P0/P1 override rules.
- Builds a cacheable repository map with languages, runtime files, entrypoints, imports, classes,
  functions, shipped `*.schema.json` artifacts, lexically scoped file-and-symbol-resolved call
  edges, tests, and detected frameworks.
- Ranks candidate files and symbols using issue-to-code evidence.
- Produces confirmed facts, confidence-scored hypotheses, and a safe reproduction plan.
- Uses LangGraph to route Top-K issues through a repository investigation workflow.
- Optionally calls OpenCode-hosted DeepSeek V4 Flash with JSON-object output plus local
  Pydantic validation.
- Requires one support/contradiction/neutral observation per repository evidence ID.
- Validates every LLM hypothesis against repository evidence IDs and named missing artifacts.
- Persists Agent runs, node traces, retries, latency, and snapshots in SQLite.
- Stops at a queryable human review state with explicit approve/reject actions.
- Evaluates file localization on frozen historical Issue/Fix-PR pairs.
- Exposes both a Typer CLI and FastAPI endpoints.

## Why two stages

```text
all open issues -> normalize -> deduplicate -> prioritize -> ranked queue
                                                        |
                                                Top-K / selected
                                                        v
repository map -> locate files/symbols -> hypotheses -> reproduction plan -> human review
```

## Quick start

Requirements: Python 3.11+ and uv.

```bash
git clone https://github.com/xbz123/repo-issue-intelligence.git
cd repo-issue-intelligence
uv sync --frozen --extra dev
uv run pytest -q
```

An equivalent named Conda development environment can be created with:

```bash
conda create -n agent_dev python=3.11 -y
uv pip install --python "$(conda run -n agent_dev python -c 'import sys; print(sys.executable)')" \
  -e '.[dev]'
conda run -n agent_dev python -m pytest -q
```

Run the included offline demo:

```bash
uv run rii rank examples/issues.json --output data/ranked.json
uv run rii duplicates examples/issues.json --threshold 0.35
uv run rii index examples/demo_repository --output data/repository-map.json
uv run rii investigate-issue examples/issues.json \
  --issue 184 \
  --repo examples/demo_repository \
  --output reports/issue-184.json
```

Run the Agent workflow up to human review:

```bash
uv run rii agent-run examples/issues.json \
  --repo examples/demo_repository \
  --top-k 1 \
  --database data/agent-runs.sqlite3 \
  --output reports/agent-run.json

uv run rii agent-show <run-id> --database data/agent-runs.sqlite3
uv run rii agent-review <run-id> \
  --decision approved \
  --notes "Evidence reviewed" \
  --database data/agent-runs.sqlite3
```

The workflow is synchronous in this version. Approval records a decision but does not execute the generated reproduction plan.

Enable optional OpenCode DeepSeek V4 Flash analysis for the selected Top-K issues:

```bash
export OPENCODE_API_KEY="..."

uv run rii agent-run examples/issues.json \
  --repo examples/demo_repository \
  --top-k 1 \
  --llm \
  --database data/agent-runs.sqlite3 \
  --output reports/agent-run-opencode.json
```

`--llm` is explicit: without it the workflow remains offline and makes no model requests. The
runtime does not accept provider or model overrides: all external analysis uses OpenCode
`deepseek-v4-flash`. The API key is read from the environment and is never added to run state
or traces. Evidence collection rejects paths outside the repository, skips sensitive filenames,
numbers source lines, and sends only evidence selected from deterministic Top-K candidates. The
default external-model path limits each snippet to 200 numbered source lines and the combined
repository evidence to 100,000 characters. These values can be changed with
`OPENCODE_MAX_LINES_PER_EVIDENCE` and `OPENCODE_MAX_EVIDENCE_CHARS`; direct library callers may
explicitly pass `None` for public-repository diagnostics that intentionally need full files. Issue
bodies are not locally truncated, and the provider context window remains an additional hard
boundary.
Requests use the OpenCode Go endpoint
`https://opencode.ai/zen/go/v1/chat/completions`.

The OpenCode path disables reasoning and defaults to temperature `0.1`, a 20,000-token completion
budget, and a 180-second timeout for both normal Agent runs and evaluation. It uses `json_object`
mode with a compact five-field provider contract followed by local Pydantic and evidence-ID
validation. A response with `finish_reason=length` is reported as `output_truncated` and is not
repeated with the same exhausted budget. Redundant candidate metadata is not sent twice: the model receives the
Issue and selected source snippets, while the client derives the affected component, contradiction
summary, retained evidence order, safe validation step, and more-evidence flag before persisting
the full public model. The validation step is based on the first evidence ID cited by the model;
it is not an additional provider-required field.
If deterministic localization yields no readable repository evidence for an Issue, that Issue
skips the model call, records the skip in node trace metadata, and still reaches human review.
Only public repository evidence should be sent to external models; see the
[OpenCode Zen documentation](https://opencode.ai/docs/zen).

Evaluate the full JSON analysis path on frozen public Issue/repository inputs:

```bash
uv run rii agent-evaluate benchmarks/cases.json \
  --case-id starlette-streaming-denial-response \
  --case-id typer-option-envvar \
  --case-id textual-remove-children-reflow \
  --llm-delay-seconds 0 \
  --output benchmarks/results/agent-analysis-latest.json
```

This is separate from the rank-only localization benchmark. Each case runs through LangGraph,
uses only Git-tracked files at the frozen pre-fix SHA, validates the complete analysis and every
evidence reference, restores the final Agent payload from a temporary SQLite store, and retains
failures in the denominator. The command exits non-zero for provider/schema failures or skipped
evidence so it can be used as a reliability gate.

Agent evaluation also reports whether reviewed fix files were present in the supplied evidence and
whether the persisted hypothesis cited them. The fixed 24-case, 24-repository quality slice and its
manual rubric are documented in
[`docs/hypothesis-quality-evaluation.md`](docs/hypothesis-quality-evaluation.md). These file-level
metrics do not claim that the hypothesis text identifies the correct causal mechanism.
The first authorized slice run completed 24/24 strict analyses and SQLite restorations. Reviewed
fix evidence was available for 20 cases; 18 hypotheses cited at least one reviewed fix file, for an
overall hit rate of `0.7500` and an evidence-available conditional rate of `0.9000`. One reviewer
scored 16 hypotheses fully correct, seven plausible but incomplete, and one contradicted; this
single-reviewer result is not a production accuracy estimate.

The pre-guard 20,000-token Go-endpoint reliability check ran all 50 frozen public cases three times
with no inter-case delay. It produced 150/150 valid first-attempt analyses and restored all 150
terminal payloads from SQLite. This followed a diagnostic that traced the preceding recurring
schema failures to a missing provider-generated `hypothesis.validation_step`; moving that safe,
non-mutating step to deterministic client normalization removed it from the provider contract
without relaxing JSON, schema, evidence-coverage, or evidence-ID validation. This is a
provider-contract and persistence result, not a root-cause accuracy claim. The full diagnostic
progression and retained artifacts are documented in
[`docs/llm-evaluation.md`](docs/llm-evaluation.md).

The bounded-input follow-up repeated both external paths three times. Full Agent analysis completed
`150/150` case-runs with `148/150` first-attempt successes and `150/150` SQLite round trips. The
evidence guards reduced total input from 3,007,488 to 2,647,458 tokens (`-11.97%`) and the largest
case from 68,407 to 33,408 tokens (`-51.16%`) relative to the preceding pre-guard runs. Rank-only
hybrid completed `150/150` requests with zero fallback and identical run-level metrics, although
six cases changed their full ordering at least once. The compact machine-readable result is
[`benchmarks/results/deepseek-bounded-input-manifest-v8-summary.json`](benchmarks/results/deepseek-bounded-input-manifest-v8-summary.json).

For a provider-default output-limit diagnostic, `agent-evaluate --omit-max-tokens` omits the
`max_tokens` field entirely. The default remains the explicit 20,000-token ceiling so normal runs
stay reproducible across provider-default changes. The authorized 50-case diagnostic returned
38/50 valid final analyses and 31/50 first-attempt successes, versus 43/50 and 34/50 in the
immediately preceding explicit-cap run under the older provider-generated-validation contract;
omitting the field did not improve that contract's reliability.

Run the frozen real-project benchmark:

```bash
uv run rii benchmark benchmarks/cases.json \
  --variant deterministic \
  --output benchmarks/results/deterministic-v0.27-final-200-cases-run1.json

uv run rii benchmark benchmarks/cases.json \
  --variant hybrid \
  --llm-delay-seconds 0 \
  --output benchmarks/results/hybrid-gpt-5.6-luna-pool40-latest.json
```

Audit reviewed production targets that remain outside the deterministic Top-40 candidate pool:

```bash
uv run rii benchmark-miss-audit benchmarks/cases.json \
  --output benchmarks/results/candidate-pool-miss-audit-index-v25.json
```

The audit reuses the frozen Issue snapshots, pre-fix repositories, and repository-map cache. It
records each missing target's language, repository, diagnostic wide rank, and available evidence;
the wide rank is diagnostic and does not change production candidate selection.
The current v0.35/index-v25 audit retrieves 246/267 reviewed production targets inside Top-40 and
records the remaining 21 misses.

The Hybrid benchmark is intentionally fixed to Codex CLI `gpt-5.6-luna`; it does not accept
provider, model, temperature, or seed overrides. Install and authenticate Codex CLI before running
it. Each rerank launches an ephemeral non-interactive `codex exec` process in an empty temporary
directory and a separate temporary `CODEX_HOME` that links only file-based authentication state,
never user configuration or global `AGENTS.md`. It disables tool-oriented features, uses a
zero-byte project-instruction budget and read-only sandbox, and sends UTF-8 prompt/output streams
through pipes. Reasoning effort is fixed to `medium`. A strict JSON Schema allows only one
`reranked_evidence_ids` array containing one to three strings; local
validation removes duplicate IDs and rejects unknown IDs, extra fields, empty output, and malformed
JSON. Provider errors are classified without persisting raw CLI diagnostics. Authentication,
quota, model, and invalid-output failures fall back immediately; timeouts, rate limits, transport
failures, and provider 5xx errors retain the existing single bounded retry.

Hybrid preserves the deterministic Top-20 as its exact fallback and appends unique candidates from
a separate Top-40 retrieval pass to the model-only pool. At most three model-selected files can be
promoted before the unchanged base order fills the final Top-20. The full frozen Issue is sent with
at most 40 source snippets. Under the default 100,000-character total evidence budget, each snippet
is limited to 2,500 characters and 200 source lines. Current manifest version 20 embeds 200 complete
Issue snapshots across 58 repositories, corrected pre-fix SHAs, and 177 manually reviewed symbol
targets across 143 cases. Older deterministic and DeepSeek artifacts remain committed as historical
provenance; the current runtime variants are deterministic and Codex CLI rank-only hybrid.
Repository indexing is restricted to `git ls-files`; live Issue edits and ignored artifacts in
reused workspaces therefore cannot change benchmark inputs. A cached commit is reused without a
network request. The runner also keeps an ignored repository-map cache under the benchmark
workspace, keyed by repository, exact pre-fix SHA, tracked/materialized file scope, index schema,
and complete interpreter identity. Missing, stale, or invalid cache entries are rebuilt, and each
case records whether it was a cold miss or warm hit. Cache write failures do not fail the benchmark.
The ignored cache is a local performance aid, not tamper-evident storage; remove its directory to
force a rebuild after manual changes or suspected filesystem corruption.

Discover and curate additional Issue/Fix-PR cases:

```bash
uv run rii benchmark-discover agronholm/anyio fastapi/fastapi pytest-dev/pytest \
  Textualize/rich \
  --target-per-repository 3 \
  --scan-limit-per-repository 50 \
  --base-manifest benchmarks/cases-v0.24-expanded-160-cases.json \
  --output benchmarks/candidates/discovered.json

uv run rii benchmark-audit pytest-dev/pytest 634 1766 \
  --tier generalization \
  --output benchmarks/candidates/pytest-634-pr-1766.json

uv run rii benchmark-audit agronholm/anyio 1220 1224 \
  --tier generalization \
  --output benchmarks/candidates/anyio-1220-pr-1224.json

uv run rii benchmark-plan benchmarks/cases-v0.24-expanded-160-cases.json \
  benchmarks/candidates/discovered.json \
  --target-total-cases 162 \
  --reserve-cases 0 \
  --max-primary-per-repository 2 \
  --target-multi-file-share 0 \
  --output benchmarks/candidates/discovered-review-queue.json
```

Discovery only produces `needs_review` or `rejected` candidates. A case enters a frozen manifest
only through a committed manual selection file with review notes; generated raw catalogs under
`benchmarks/candidates/` are intentionally ignored. Discovery loads the ordered PR commit history,
uses the parent of the first PR commit as pre-fix SHA, and rejects a proposed pre-fix SHA that
appears inside the fix PR. The historical v0.4 catalog is retained for provenance;
`benchmarks/candidates-v0.7.json` contains the corrected expansion audit records.
The two `benchmark-audit` commands are independent single-case audit examples; their outputs are
not inputs to the queue command shown. That command creates a small ignored working queue from the
example discovery source. The
base-manifest filter keeps discovery scanning past Issue/fix-PR identities already frozen in the
160-case input. It does not reproduce or overwrite the committed
`benchmarks/expansion-v200-review-queue-v19.json` provenance archive. Planning a 160-to-200
expansion requires candidate catalogs with at least 40 feasible primary cases plus the requested
reserves; the four-repository discovery example is intentionally not presented as that full pool.
`benchmark-plan` uses maximum-cardinality Issue/fix-PR matching for overlapping catalogs, requires
the complete blocking-audit set, jointly enforces repository and multi-file quotas, and records the
pre-fix SHA provenance in a `needs_review` queue. A manual selection may only narrow the audited
file list, never add an unseen path, and can record rejected candidate IDs. A selection passed with
`--review-decisions` prevents reviewed rejections from returning only when every referenced
candidate ID is present in the supplied catalogs; unknown IDs fail closed instead of being silently
ignored.
`benchmark-plan` cannot accept candidates or alter the frozen manifest. The archived v19 queue
records the final review pool used to complete manifest v20; it is provenance, not an active queue.
The original 30% multi-file target was reduced to the observed 47/200 final count:
manual review rejected or narrowed most automatically classified multi-file records because they
were documentation-only, partial, mismatched, auxiliary, or omitted a material production file.
The suite does not lower the ground-truth standard to satisfy a quota.

### Benchmark case selection standard

A case is accepted only when all of the following hold:

- the public Issue is closed and describes a concrete behavior, compatibility, performance,
  packaging, or user-visible contract change with enough context to identify the defect;
- the fix PR is merged in the same repository and is the canonical substantive fix, rather than a
  partial step, later documentation cleanup, duplicate attempt, or one PR reused for several Issues;
- the frozen pre-fix SHA is the parent of the first ordered PR commit and is not itself in the PR;
- every material production file is present in the audited PR scope and exists at the pre-fix SHA;
  tests, docs, changelog, snapshots, generated files, workflows, and auxiliary benchmarks are
  excluded unless they are the actual shipped behavior;
- a symbol target is recorded only when it belongs to an expected file and resolves to one qualified
  identity in the pre-fix repository map; repeated conditional or overload records with that same
  identity are equivalent, while new, ambiguous, non-Python, multi-method, and module-level fixes
  stay file-only;
- case ID, `(repository, Issue)`, and `(repository, fix PR)` are unique, and repository/file
  concentration is considered when multiple equally valid choices are available.

Rejected and missed cases are never removed to improve metrics. All accepted cases, including
Top-20 misses, remain in the denominator, and each final deterministic result is reproduced three
times after removing only timestamps, elapsed fields, and cache provenance.

## Analyze a real GitHub repository

```bash
cp .env.example .env
# Set GITHUB_TOKEN in .env; the CLI loads this file automatically.
uv run rii sync owner/repository --limit 100 --output data/issues.json
uv run rii rank data/issues.json --output data/ranked.json
uv run rii investigate-issue data/issues.json --issue 123 --repo /path/to/clone --output reports/issue-123.json
```

## Run the API

```bash
uv run rii serve --host 127.0.0.1 --port 8000
```

OpenAPI documentation: `http://127.0.0.1:8000/docs`.

Core endpoints:

- `GET /health`
- `POST /v1/issues/score`
- `POST /v1/issues/rank`
- `POST /v1/repository/index`
- `POST /v1/agent/runs`
- `GET /v1/agent/runs/{run_id}`
- `POST /v1/agent/runs/{run_id}/review`

The v0.5 API continues to run the offline workflow. Optional provider analysis is exposed through
the CLI first so model credentials and quota use remain an explicit local operator decision.

## Priority model

```text
priority_score =
    0.30 * severity
  + 0.20 * urgency
  + 0.15 * affected_users
  + 0.10 * reproducibility
  + 0.10 * duplicate_count
  + 0.10 * release_blocking
  + 0.05 * recency
```

Hard rules override the average:

- Actively exploited security issue -> P0.
- Reproducible data loss or corruption -> P0.
- Release blocker -> at least P1.
- Missing diagnostic details -> `needs_information=true`.

Every result includes factor values and human-readable reasons.

## Project layout

```text
src/repo_issue_intelligence/
  agent_store.py         SQLite run, trace, and snapshot persistence
  agent_workflow.py      LangGraph Top-K investigation workflow
  api.py                 FastAPI service
  benchmark.py           frozen real-Issue file-localization evaluation
  benchmark_discovery.py candidate discovery, audit, and manual curation
  cli.py                 command-line interface
  duplicates.py          issue similarity and duplicate candidates
  github_client.py       paginated GitHub REST synchronization
  investigator.py        file/symbol ranking and hypothesis generation
  evidence.py            bounded repository source evidence collection
  llm_client.py           OpenCode DeepSeek analysis and reranking
  models.py              typed domain models
  repository_index.py    repository map, Python AST, and bounded declaration index
  scoring.py             severity, urgency, priority rules
  service.py             ranking orchestration
```

See `docs/architecture.md` for system boundaries and
`docs/benchmark-expansion.md` for candidate acceptance and expansion protocol.

## Evaluation

The current frozen benchmark contains 200 closed issues with linked fix PRs across 58 projects:
17 main, 11 calibration, and 172 generalization cases. It records 267 reviewed production-file
targets and 177 reviewed symbols across 143 cases. Each case uses a committed Issue snapshot and the
parent of the first ordered fix-PR commit as its pre-fix SHA. Only Git-tracked files are eligible
for candidate retrieval.

Three manifest-v20 deterministic v0.31 index-v19 runs completed 200/200 cases. Candidate-file
orders and all metrics were identical after excluding timestamps, elapsed fields, and cache
provenance; candidate-symbol lists were identical for 199/200 cases, with one same-file symbol-only
variation retained in the report.
One index-v20 review-validation run then completed 200/200 with zero failures and exactly the same
candidate files, candidate symbols, and metrics as index-v19 run 1. Four Ruff maps restore the real
`Truthiness` enum previously hidden by `if !...` macro misclassification; a direct audit found no
Top-40 candidate or evidence changes, so the anomaly-triggered repeat policy did not require more
runs.
One index-v21 run likewise completed 200/200 with zero failures and matched index v20 across all
193 maps, candidate lists, and metrics. It adds conservative support for keyword-named macros from
older Rust editions plus a mixed definition/invocation linearity guard; the frozen suite contains
none of those legacy forms, so no repeat was required.
One index-v22 review-validation run completed 200/200 with zero failures after replacing substring
test-file detection with exact path and filename conventions. A full old-map replay checked every
changed-map case: 38 candidate-file orders changed, while only `ruff-os-exit-private-member`
changed any metric, improving its first expected-file rank from 2 to 1. No per-case metric regressed
and no Top-20 ground-truth target was lost, so the anomaly-triggered policy did not require another
run.
One index-v23 review-validation run completed 200/200 with zero failures after recognizing
separator-based test directories such as `test-data/` and serializing the Python 3.11/3.12
process-global warning-filter context. Its candidate-file orders, candidate-symbol lists, and every
per-case and aggregate metric exactly matched index v22, so no additional run was required.
The final index-v25 run completed 200/200 with zero failures after adding unique local-module alias
calls, separately recorded same-class receiver calls, unique exact-stem test-to-source expansion,
and directly supported alternate symbols. An intermediate index-v24 run exposed a Jinja symbol
regression; a four-case targeted check verified the receiver-call isolation fix before the final
full run. No prior Top-20 production target or reviewed symbol target is lost.
The repository map adds conservative Rust declarations without inferred Rust call edges, normalizes
raw identifiers and `::` paths, consumes an optional UTF-8 BOM, masks Rust script shebangs, accepts
Rust 2024 safe foreign functions, distinguishes control-flow negation from delimiter-backed macro
calls, and handles macro paths and declarations that span lines; Unicode Issue identifiers and
traceback symbols use NFC-normalized boundaries, while JavaScript and TypeScript content matching
preserves exact identifier spelling including `$`; embedded dollar identifiers require an explicit
JavaScript/TypeScript fence, while untyped shell variables are ignored. TypeScript, TSX, C, and C++
remain file-only.
File Recall@1/5/10/20 is
`0.3577/0.6617/0.7493/0.8732`, with MRR `0.5471`. Relative to index v23, two new targets enter
Top-20 and none leave it. Symbol Recall@1/5/10/20 is
`0.2448/0.4260/0.4505/0.4994`, with MRR `0.3537`; five reviewed symbol targets become retrievable
and none are lost. Forty-three of 267 production targets remain outside deterministic Top-20 and stay in
the denominator.

Three authorized manifest-v20 OpenCode `deepseek-v4-flash` pool-40 runs completed all 600 case-runs.
The provider returned 595 valid ranks (580 on the first attempt and 15 after one retry); five cases
used deterministic fallback after two OpenCode HTTP 500 responses. Invalid structure and unknown
evidence IDs remained zero. Mean File Recall@1/5/10/20 was
`0.5747/0.8008/0.8433/0.8965`, with MRR `0.7606`. The expanded pool reserves three slots for
directly supported paths while deterministic Top-20 keeps one. Rust declarations increase pool
coverage from 234/267 to 236/267 production targets, and the final Top-20 covers 229, 228, and 229.
The uv stale-interpreter `project/mod.rs` target enters deterministic Top-20 and
`uv-pep508/src/lib.rs` enters Top-40; DeepSeek selects both in every run, and no deterministic
Top-20 ground-truth target is lost. Mean Symbol Recall@20 is `0.4668`, with MRR `0.4870`; these
labels remain Python-only. Only 125/200 complete candidate orders are identical across all repeats,
so seed 1337 remains best effort despite the improved mean file metrics.
The three runs used 10,070,864 input tokens and 5,846 output tokens. They were generated under
index v14, and review expansion through index v18 preserved all Top-40 inputs. Index v19 leaves all
193 maps unchanged but changes Top-40 evidence for one Prefect and one PyO3 case. Index v20 changes
four Ruff maps but leaves their Top-40 reports and evidence byte-for-byte equivalent, so the DeepSeek
inputs remain unchanged. Index v21 is map-identical to v20. The DeepSeek metrics are retained only as
historical provenance and were not rerun. The hybrid runtime now uses Codex CLI
`gpt-5.6-luna`. Its first complete 200-case run returned 200/200 valid first-attempt ranks with no
fallback. File Recall@1/5/10/20 was `0.6147/0.8192/0.8501/0.9007`, with MRR `0.7860`; Symbol
Recall@20 was `0.4668`, with MRR `0.5049`. This is one run, so repeat stability remains unmeasured.
See the compact Luna
[`run summary`](benchmarks/results/gpt-5.6-luna-pool40-manifest-v20-run1-summary.json) and DeepSeek
[`manifest-v20 summary`](benchmarks/results/deepseek-v4-flash-pool40-manifest-v20-summary.json);
the duplicate raw run files remain outside Git. The compact pre-change miss audit is
[`pool40-miss-taxonomy-manifest-v20.json`](benchmarks/results/pool40-miss-taxonomy-manifest-v20.json).

v0.25 adds conservative title phrase evidence for qualified methods. It requires adjacent
owner-to-method semantic terms in the title, a non-generic compound owner, one uniquely strongest
method within the file, and a production symbol. The evidence is used only for within-file symbol
selection, not file retrieval or blame seeds. This recovered `ConfigOptionParser.error` for
`pip-rich-option-error-usage`; all 50 candidate-file lists and the other 49 symbol lists were
unchanged from v0.24.

v0.24 recognizes a title-scoped constructor call only when the called class resolves to one
qualified `__init__` target and the title has explicit construction wording or concrete semantic
support in that constructor's docstring. A title method reference and a complete qualified method
reference remain stronger evidence; ambiguous owners, label-only class names, and generic setup
calls are skipped. This recovered `TypeAdapter.__init__` for
`pydantic-typeadapter-union-typing`. All 50 candidate-file lists and the other 49 symbol lists were
unchanged from v0.23.

v0.23 recognizes bounded multi-line source excerpts in fenced Issue blocks only after a path or
basename narrows the eligible files. The excerpt must contain 3-12 non-empty lines and 60-2,000
characters, occur once in one eligible file, and resolve to one enclosing symbol; duplicate matches
are rejected. Python trailing comments may differ, but the remaining source must still meet the
same minimum evidence threshold. This recovered `prompt` for `click-hidden-prompt-custom-error`.
All 50 candidate-file lists and the other 49 symbol lists were unchanged from v0.22.

v0.22 resolves exact `path.py#L...` references and immutable GitHub source links to the enclosing
qualified symbol. Immutable links are parsed from their referenced 40-character commit with local
Git before the identity is matched against the frozen checkout; mutable branch links such as
`blob/main` are ignored. This recovered `WebSocketsSansIOProtocol.handle_connect` for
`uvicorn-nonascii-websocket-headers` and `EnvManager.get` for `poetry-empty-conda-prefix`. All 50
candidate-file lists were unchanged from v0.21; 47/50 symbol lists were unchanged, and no labeled
symbol regressed.

The retained v0.21 policy parses canonical Python and compact numbered traceback frames, resolves
each frame path to one repository file, and selects the deepest uniquely resolved function in that
file. Installed package paths may map through a confirmed `src` or `lib` layout only when the
stripped suffix is unique; real top-level `src` and `lib` packages remain intact. This recovered
`Executor._create_directory_url_reference` for `poetry-relative-directory-url`.

The retained v0.34/index-v25 policy resolves qualified calls through unique local module aliases,
records unrebound same-class `self`/`cls` calls without feeding them into the older direct-caller
vote, and adds an expansion-only test-to-source relation for unique exact filename stems. Candidate
locations can report up to two additional directly supported symbols at the same file rank. Its
single final 200-case run completed 200/200 with no failures, recovered two Top-20 production
targets without losing an earlier Top-20 target, and raised deterministic File Recall@20 from
`0.8690` to `0.8732` and Symbol Recall@20 from `0.4645` to `0.4994`. The compact audit is retained;
the raw run remains outside Git.

The v0.35 follow-up changes only the separate Top-40 retrieval pass. It recognizes at most 16
hyphenated long CLI options, matches their compound source identifiers, adds low-frequency Rust
filename-stem evidence, and recognizes dependency names in root `setup.py` for release/deprecation
Issues. The accepted 200-case audit recovers nine prior pool misses without adding one, raising
Top-40 coverage from 237/267 to 246/267; the deterministic Top-20 metrics remain unchanged.

The retained v0.20 policy follows a single safe package re-export hop from an Issue-referenced
source path. Both files must be production files in the same package subsystem, the facade must be
`__init__.py`, and the source must actually read the imported name while the module-level binding
and target definition must each resolve uniquely. The relation is
expansion-only and tail-protected, so it cannot rerank an existing shortlist. This recovered
`src/poetry/utils/env/python/manager.py` at rank 17; the other 49 candidate and symbol lists were
unchanged.

v0.19 adds a bounded reverse-import expansion inside title-matching subsystems. The imported
production module must contain at least two functions sharing the same non-path title term and
have one to three in-scope importers; package roots and auxiliary paths cannot supply scope. This
recovered `celery/worker/pidbox.py` at rank 19 without regressing an earlier Top-20 ground-truth
match. The relation can consume a bounded tail expansion slot but cannot rerank files already in
the shortlist.

The retained v0.18 baseline records qualified calls rooted at unshadowed module imports, including
aliases, but uses them for cross-file expansion only when the Issue explicitly references both the
full call and a seed caller, the call occurs in two or three production files, and the caller
identity is unique or has a single non-overload implementation. This added
`scrapy/utils/decorators.py` at rank 18 with `_warn_spider_arg`; the other 49 case outputs were
unchanged. The relation reserves only a bounded tail expansion slot and contributes no reranking
bonus.

The retained v0.17 baseline conservatively resolves unshadowed leading function-local `from` imports and records those
calls separately from module-level edges. A bounded constructor-aware second hop recovered
`rich/highlighter.py` at rank 18 for the JSON highlighting case. Direct function-local edges cannot rerank
or expand a candidate by themselves, do not receive strong Top-10 promotion, and are not followed
through another function-local import.

v0.16 preserves exact identifier mention frequency outside fenced reproduction blocks when choosing
among safely scoped direct symbol references. Short bare names still need owner or path scope; a
candidate-unique name needs at least five non-underscore characters before it can override semantic
ranking.

The v0.15 retrieval policy reserves at most one additional non-auxiliary shortlist slot for an
exact path, specific title-to-path match, path identifier, or primary symbol match. This recovered
Poetry's directly named `console/commands/publish.py` without allowing long traceback test-file
lists to displace production candidates.

Qualified AST identities distinguish repeated local names while the public unqualified `symbol`
field remains compatible. Bare identifiers require identifier boundaries and only receive direct
priority when sufficiently specific and candidate-unique, or when constrained by an owner or
uniquely resolved repository path. Inference consumes lexically scope-resolved direct calls and
unique local-module aliases. Only unrebound conventional `self`/`cls` calls to a unique same-class
method are recorded; arbitrary receivers, shadowed names, ambiguous definitions, and legacy broad
call maps cannot fabricate strong graph evidence. Real
top-level `src` and `lib` modules/packages retain their importable names, while layout directories
are stripped only when they are actual source roots.

Three retained OpenCode `deepseek-v4-flash` rank-only runs reranked the manifest-v8 deterministic
v0.25 candidate pool and kept all 150 case-runs in the denominator. Every response produced a
valid known-ID rank and no case used deterministic fallback. Mean File Recall@1/5/10/20 was
`0.7367/0.8600/0.9000/1.0000`, with mean MRR `0.8894`; the corresponding deterministic values are
`0.4067/0.6900/0.7800/1.0000` and `0.6038`. Symbol Recall@1/5/10/20 was
`0.6667/0.6970/0.6970/0.7121` with MRR `0.7424` in all three runs.

The runs made 57, 50, and 50 requests. Provider telemetry varied sharply despite
`reasoning_effort=none`: run 1 recorded 162,491 output tokens and 28.53 seconds mean LLM latency,
while runs 2 and 3 each recorded 446 output tokens and 1.62/1.46 seconds. Only 29/50 complete file
orders were identical across all repeats. The result therefore supports a reliable rank protocol
and bounded ordering gain on this frozen pool, not deterministic generation, new-file discovery,
or root-cause accuracy.

The completed expansion accepts 150 manually reviewed additions and reaches 200 total cases;
multi-file coverage is 47/200 after the tox ground-truth correction. The final direct batch accepts
40 audited cases and records 21 explicit rejections while leaving four archived queue candidates
unselected. Forty-six of the 267
reviewed production targets remain outside the deterministic Top-20, and the current index provides
file-only symbol ground truth for TypeScript, Rust, and C. Superseded manifests and retained
50-case DeepSeek runs remain provenance only and must not be mixed with manifest-v20 metrics.

See [`docs/benchmark-results.md`](docs/benchmark-results.md) for the protocol, per-tier results,
limitations, and next retrieval improvements.

## Safety and scope

The MVP does not execute generated commands, modify the target repository, post labels, close issues, or create pull requests. The default Agent path is deterministic and offline. The optional LLM path analyzes only supplied Top-K evidence, cannot expand repository access, and still ends at the same human review gate. LLM output is an evidence-linked hypothesis, not a confirmed root cause.

## License

MIT
