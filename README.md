# Repo Issue Intelligence

Repository-aware GitHub issue prioritization and investigation, built around an explicit two-stage workflow: rank every open issue cheaply, then investigate only the highest-value issues against the codebase.

The project is an initial, runnable Agent MVP. It does not require an LLM API to produce useful results. Priority decisions are explainable, repository evidence is collected deterministically, and root causes are represented as hypotheses rather than unsupported conclusions. An optional OpenCode DeepSeek V4 Flash analysis step can inspect bounded code evidence for Top-K issues while preserving the offline baseline.

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
`deepseek-v4-flash-free`. The API key is read from the environment and is never added to run state
or traces. Evidence collection rejects paths outside the repository, skips sensitive filenames,
numbers source lines, and applies a configurable character budget before sending content.

The OpenCode path defaults to a 4,096-token completion budget and 60-second request timeout because
DeepSeek reasoning tokens share the completion budget and valid responses can exceed 30 seconds.
It uses `json_object` mode with a compact five-field provider contract followed by local Pydantic
and evidence-ID validation. Redundant candidate metadata is not sent twice: the model receives the
Issue and bounded source snippets, while the client derives the affected component, contradiction
summary, retained evidence order, and more-evidence flag before persisting the full public model.
If deterministic localization yields no readable repository evidence for an Issue, that Issue
skips the model call, records the skip in node trace metadata, and still reaches human review.
Only public repository evidence should be sent to free models: OpenCode states that data collected
during the free period may be used to improve those models; see the
[OpenCode Zen documentation](https://opencode.ai/docs/zen).

Evaluate the full JSON analysis path on frozen public Issue/repository inputs:

```bash
uv run rii agent-evaluate benchmarks/cases.json \
  --case-id starlette-streaming-denial-response \
  --case-id typer-option-envvar \
  --case-id textual-remove-children-reflow \
  --llm-delay-seconds 30 \
  --output benchmarks/results/agent-analysis-latest.json
```

This is separate from the rank-only localization benchmark. Each case runs through LangGraph,
uses only Git-tracked files at the frozen pre-fix SHA, validates the complete analysis and every
evidence reference, restores the final Agent payload from a temporary SQLite store, and retains
failures in the denominator. The command exits non-zero for provider/schema failures or skipped
evidence so it can be used as a reliability gate.

Run the frozen real-project benchmark:

```bash
uv run rii benchmark benchmarks/cases.json \
  --variant deterministic \
  --output benchmarks/results/deterministic-v0.17-function-local-imports-50-cases-run1.json

LLM_MAX_EVIDENCE_CHARS=16000 \
uv run rii benchmark benchmarks/cases.json \
  --variant hybrid \
  --temperature 0.1 \
  --seed 1337 \
  --llm-delay-seconds 0 \
  --output benchmarks/results/hybrid-deepseek-v4-flash-rank-none-v0.14-manifest-v8-run1.json
```

The Hybrid benchmark is intentionally fixed to OpenCode `deepseek-v4-flash-free`; it does not
accept provider or model overrides. The reranker requests no grammar-constrained response format
and parses exactly one `RANK: E3,E1,E2` line. Duplicate IDs are removed, unknown IDs are rejected,
and omitted candidates keep their deterministic order. Only transport errors, HTTP 429, and HTTP
5xx responses are retried with bounded exponential backoff; invalid ranks and HTTP 4xx responses
fall back immediately. This isolates file ordering from hypothesis generation and removes the
unreliable JSON-schema path from the benchmark. Reasoning is disabled for this narrow ranking task,
the completion budget is bounded at 256 tokens with one 1,024-token truncation retry, Issue bodies
are capped at 2,000 characters, and each evidence snippet is capped at 300 characters under the
existing 16,000-character total budget. Current manifest version 8 embeds 50 complete Issue
snapshots across 21 repositories, corrected pre-fix SHAs, and 39 manually reviewed symbol targets
across 33 cases. Older deterministic and DeepSeek artifacts remain committed as historical
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
```

Discovery only produces `needs_review` or `rejected` candidates. A case enters a frozen manifest
only through a committed manual selection file with review notes; generated raw catalogs under
`benchmarks/candidates/` are intentionally ignored. Discovery loads the ordered PR commit history,
uses the parent of the first PR commit as pre-fix SHA, and rejects a proposed pre-fix SHA that
appears inside the fix PR. The historical v0.4 catalog is retained for provenance;
`benchmarks/candidates-v0.7.json` contains the corrected expansion audit records.

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

The current frozen benchmark contains 50 closed issues with linked fix PRs across 21 projects:
17 main, 11 calibration, and 22 generalization cases. It records 62 reviewed production-file
targets and 39 reviewed symbols across 33 cases. Each case uses a committed Issue snapshot and the
parent of the first ordered fix-PR commit as its pre-fix SHA. Only Git-tracked files are eligible
for candidate retrieval.

Three manifest-v8 deterministic v0.17 runs completed 50/50 cases and produced identical candidates,
symbols, and metrics after excluding timestamps and elapsed fields. File Recall@1 is `0.4067`,
Recall@5 `0.6900`, Recall@10 `0.7800`, Recall@20 `0.9600`, and MRR `0.6027`. Symbol Recall@1 is
`0.2273`, Recall@5 `0.4242`, Recall@10 `0.4545`, Recall@20 `0.5455`, and symbol MRR `0.3342`.

v0.17 conservatively resolves unshadowed leading function-local `from` imports and records those
calls separately from module-level edges. A bounded constructor-aware second hop recovered
`rich/highlighter.py` at rank 18 for the JSON highlighting case. The other 49 per-case metrics and
all previously selected candidate symbols were unchanged. Direct function-local edges cannot rerank
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

Two authorized OpenCode `deepseek-v4-flash-free` rank-only runs over the earlier deterministic
v0.13 candidate pool kept all 50 cases in the denominator. Both produced 50/50 valid ranks with no
fallback, grammar error, invalid rank, or unknown evidence ID. File Recall@1 was `0.6567` and
`0.6767`, Recall@5/10/20 was
`0.8200/0.8600/0.9300` in both runs, and MRR was `0.8226` and `0.8326`. The run-level mean and
population standard deviation were Recall@1 `0.6667 +/- 0.0100` and MRR
`0.8276 +/- 0.0050`. All requests completed in one attempt, with 170,521 input tokens in each run
and only 485/491 output tokens. Average model latency was `4.13 s` and `4.96 s`.

The two runs retained the same deterministic 20-file set for every case but changed the order in
14/50 cases; only `pydantic-safe-annotations-metaclass` changed expected-file reciprocal rank.
Fixed seed is therefore best effort rather than deterministic model output. Because v0.15 changed
candidate membership and v0.17 changes it again, these runs remain historical paired evidence and
have not yet been repeated against the new pool. They support a bounded ordering-gain and
protocol-reliability claim, not a production-reliability or root-cause-accuracy claim.

The added slice is deliberately reported with its limitations: 16 of the 18 new Issues are from
2026, and only 11 of 50 cases have multi-file production ground truth. Three cases still miss at
least one reviewed file at Top-20, so hybrid reranking cannot recover them. Superseded manifests
and older DeepSeek runs remain committed for provenance but must not be mixed with current
manifest-v8 quality metrics.

See [`docs/benchmark-results.md`](docs/benchmark-results.md) for the protocol, per-tier results,
limitations, and next retrieval improvements.

## Safety and scope

The MVP does not execute generated commands, modify the target repository, post labels, close issues, or create pull requests. The default Agent path is deterministic and offline. The optional LLM path analyzes only supplied Top-K evidence, cannot expand repository access, and still ends at the same human review gate. LLM output is an evidence-linked hypothesis, not a confirmed root cause.

## License

MIT
