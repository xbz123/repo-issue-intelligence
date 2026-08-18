# Repo Issue Intelligence

Repository-aware GitHub issue prioritization and investigation, built around an explicit two-stage workflow: rank every open issue cheaply, then investigate only the highest-value issues against the codebase.

The project is an initial, runnable Agent MVP. It does not require an LLM API to produce useful results. Priority decisions are explainable, repository evidence is collected deterministically, and root causes are represented as hypotheses rather than unsupported conclusions. An optional OpenCode DeepSeek V4 Flash analysis step can inspect selected code evidence for Top-K issues while preserving the offline baseline.

## What it does

- Synchronizes open GitHub issues with pagination and pull-request filtering.
- Detects likely duplicate issue pairs using title similarity and token overlap.
- Separates severity, urgency, and engineering priority.
- Applies weighted scoring plus hard P0/P1 override rules.
- Builds a cacheable repository map with languages, runtime files, entrypoints, imports, classes,
  functions, lexically scoped file-and-symbol-resolved call edges, tests, and detected frameworks.
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
  --output benchmarks/results/deterministic-v0.26-batch1-60-cases-run1.json

uv run rii benchmark benchmarks/cases.json \
  --variant hybrid \
  --temperature 0.1 \
  --seed 1337 \
  --llm-delay-seconds 0 \
  --output benchmarks/results/hybrid-deepseek-v4-flash-go-v0.26-rank20000-latest.json
```

The Hybrid benchmark is intentionally fixed to OpenCode `deepseek-v4-flash`; it does not
accept provider or model overrides. The reranker requests no grammar-constrained response format
and parses exactly one `RANK: E3,E1,E2` line. Duplicate IDs are removed, unknown IDs are rejected,
and omitted candidates keep their deterministic order. Only transport errors, HTTP 429, and HTTP
5xx responses are retried with bounded exponential backoff; invalid ranks and HTTP 4xx responses
fall back immediately. This isolates file ordering from hypothesis generation and removes the
unreliable JSON-schema path from the benchmark. Reasoning is disabled for this narrow ranking task,
and the completion budget starts at 8,192 tokens with one 20,000-token truncation retry. The full
frozen Issue and selected deterministic candidate snippets are sent without project-defined input
character caps. Current manifest version 9 embeds 60 complete Issue
snapshots across 31 repositories, corrected pre-fix SHAs, and 56 manually reviewed symbol targets
across 41 cases. Older deterministic and DeepSeek artifacts remain committed as historical
provenance, but the current benchmark runtime supports only deterministic and DeepSeek rank
variants.
Repository indexing is restricted to `git ls-files`; live Issue edits and ignored artifacts in
reused workspaces therefore cannot change benchmark inputs. A cached commit is reused without a
network request.

Discover and curate additional Issue/Fix-PR cases:

```bash
uv run rii benchmark-discover agronholm/anyio fastapi/fastapi pytest-dev/pytest \
  Textualize/rich \
  --target-per-repository 3 \
  --scan-limit-per-repository 50 \
  --output benchmarks/candidates/discovered.json

uv run rii benchmark-audit pytest-dev/pytest 634 1766 \
  --tier generalization \
  --output benchmarks/candidates/pytest-634-pr-1766.json

uv run rii benchmark-audit agronholm/anyio 1220 1224 \
  --tier generalization \
  --output benchmarks/candidates/anyio-1220-pr-1224.json

uv run rii benchmark-plan benchmarks/cases.json \
  benchmarks/candidates/discovered.json \
  --target-total-cases 200 \
  --reserve-cases 50 \
  --max-primary-per-repository 5 \
  --target-multi-file-share 0.30 \
  --output benchmarks/expansion-v200-review-queue.json
```

Discovery only produces `needs_review` or `rejected` candidates. A case enters a frozen manifest
only through a committed manual selection file with review notes; generated raw catalogs under
`benchmarks/candidates/` are intentionally ignored. Discovery loads the ordered PR commit history,
uses the parent of the first PR commit as pre-fix SHA, and rejects a proposed pre-fix SHA that
appears inside the fix PR. The historical v0.4 catalog is retained for provenance;
`benchmarks/candidates-v0.7.json` contains the corrected expansion audit records.
`benchmark-plan` uses maximum-cardinality Issue/fix-PR matching for overlapping catalogs, requires
the complete blocking-audit set, jointly enforces repository and multi-file quotas, and records the
pre-fix SHA provenance in a `needs_review` queue. It cannot accept candidates or alter the frozen
manifest.

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
  repository_index.py    repository map and Python AST index
  scoring.py             severity, urgency, priority rules
  service.py             ranking orchestration
```

See `docs/architecture.md` for system boundaries and
`docs/benchmark-expansion.md` for candidate acceptance and expansion protocol.

## Evaluation

The current frozen benchmark contains 60 closed issues with linked fix PRs across 31 projects:
17 main, 11 calibration, and 32 generalization cases. It records 86 reviewed production-file
targets and 56 reviewed symbols across 41 cases. Each case uses a committed Issue snapshot and the
parent of the first ordered fix-PR commit as its pre-fix SHA. Only Git-tracked files are eligible
for candidate retrieval.

Three manifest-v9 deterministic v0.26 runs completed 60/60 cases and produced identical candidates,
symbols, and metrics after excluding timestamps and elapsed fields. File Recall@1 is `0.3811`,
Recall@5 `0.6517`, Recall@10 `0.7517`, Recall@20 `0.9717`, and MRR `0.6115`. Symbol Recall@1 is
`0.3049`, Recall@5 `0.4756`, Recall@10 `0.5122`, Recall@20 `0.6341`, and symbol MRR `0.4345`.
The new batch adds ten older multi-file cases from ten repositories and exposes four production
targets that are absent from the current deterministic Top-20.

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
uniquely resolved repository path. Inference
consumes only lexically scope-resolved direct calls; unresolved receiver calls, shadowed names,
ambiguous definitions, and legacy broad call maps cannot fabricate strong graph evidence. Real
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

The v0.14 batch deliberately improves the earlier temporal and structural skew: all ten additions
are multi-file cases created from 2013 through 2023, raising multi-file coverage to 21/60. Four of
the 86 reviewed production targets remain outside the deterministic Top-20, so this expanded sample
does not support a perfect-recall claim. Superseded manifests and the retained 50-case DeepSeek runs
remain committed for provenance but must not be mixed with current manifest-v9 quality metrics.

See [`docs/benchmark-results.md`](docs/benchmark-results.md) for the protocol, per-tier results,
limitations, and next retrieval improvements.

## Safety and scope

The MVP does not execute generated commands, modify the target repository, post labels, close issues, or create pull requests. The default Agent path is deterministic and offline. The optional LLM path analyzes only supplied Top-K evidence, cannot expand repository access, and still ends at the same human review gate. LLM output is an evidence-linked hypothesis, not a confirmed root cause.

## License

MIT
