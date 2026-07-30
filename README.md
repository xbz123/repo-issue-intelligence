# Repo Issue Intelligence

Repository-aware GitHub issue prioritization and investigation, built around an explicit two-stage workflow: rank every open issue cheaply, then investigate only the highest-value issues against the codebase.

The project is an initial, runnable Agent MVP. It does not require an LLM API to produce useful results. Priority decisions are explainable, repository evidence is collected deterministically, and root causes are represented as hypotheses rather than unsupported conclusions. An optional OpenAI-compatible analysis step can inspect bounded code evidence for Top-K issues through Groq or OpenCode while preserving the offline baseline.

## What it does

- Synchronizes open GitHub issues with pagination and pull-request filtering.
- Detects likely duplicate issue pairs using title similarity and token overlap.
- Separates severity, urgency, and engineering priority.
- Applies weighted scoring plus hard P0/P1 override rules.
- Builds a cacheable repository map with languages, runtime files, entrypoints, imports, classes, functions, tests, and detected frameworks.
- Ranks candidate files and symbols using issue-to-code evidence.
- Produces confirmed facts, confidence-scored hypotheses, and a safe reproduction plan.
- Uses LangGraph to route Top-K issues through a repository investigation workflow.
- Optionally calls Groq-hosted GPT-OSS with strict JSON Schema output or OpenCode-hosted
  DeepSeek V4 Flash with JSON-object output plus local Pydantic validation.
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

Enable optional Groq analysis for the selected Top-K issues:

```bash
export GROQ_API_KEY="..."

uv run rii agent-run examples/issues.json \
  --repo examples/demo_repository \
  --top-k 1 \
  --llm \
  --model openai/gpt-oss-20b \
  --database data/agent-runs.sqlite3 \
  --output reports/agent-run-llm.json
```

`--llm` is explicit: without it the workflow remains offline and makes no model requests.
The API key is read from the environment and is never added to run state or traces. Evidence
collection rejects paths outside the repository, skips sensitive filenames, numbers source
lines, and applies a configurable character budget before sending content. GPT-OSS defaults to
low reasoning effort and a bounded output budget so strict structured generation fits within the
free-tier token limits; both values can be changed through `.env`.

OpenCode uses the same OpenAI-compatible chat-completions protocol with a different provider
configuration:

```bash
export OPENCODE_API_KEY="..."

uv run rii agent-run examples/issues.json \
  --repo examples/demo_repository \
  --top-k 1 \
  --llm \
  --provider opencode \
  --model deepseek-v4-flash-free \
  --database data/agent-runs.sqlite3 \
  --output reports/agent-run-opencode.json
```

The OpenCode path defaults to a 4,096-token completion budget and 60-second request timeout because
DeepSeek reasoning tokens share the completion budget and valid responses can exceed 30 seconds.
It uses `json_object` mode followed by the same local schema and evidence-ID validation as Groq.
Only public repository evidence should be sent to free models: OpenCode states that data collected
during the free period may be used to improve those models; see the
[OpenCode Zen documentation](https://opencode.ai/docs/zen).

Run the frozen real-project benchmark:

```bash
uv run rii benchmark benchmarks/cases.json \
  --variant deterministic \
  --output benchmarks/results/deterministic-v0.5-graph-20-cases.json

LLM_MAX_EVIDENCE_CHARS=12000 LLM_MAX_OUTPUT_TOKENS=1600 \
uv run rii benchmark benchmarks/cases.json \
  --variant hybrid \
  --model openai/gpt-oss-20b \
  --temperature 0.1 \
  --seed 1337 \
  --llm-delay-seconds 45 \
  --output benchmarks/results/hybrid-20b-v0.5-graph-20-cases.json

uv run rii benchmark benchmarks/cases.json \
  --variant hybrid \
  --provider opencode \
  --model deepseek-v4-flash-free \
  --temperature 0.1 \
  --seed 1337 \
  --llm-delay-seconds 0 \
  --output benchmarks/results/hybrid-deepseek-v4-flash-v0.5-20-cases.json
```

The Hybrid benchmark uses a deliberately small reranking schema rather than the full investigation
schema. This isolates file-ranking quality from hypothesis-generation reliability and avoids
misclassifying schema failures as localization failures. Each evidence snippet is capped so the
model sees a broad candidate set under the same total character budget. Manifest version 3 embeds
20 complete Issue snapshots across seven repositories, and repository indexing is restricted to
`git ls-files`; live Issue edits and ignored artifacts in reused workspaces therefore cannot
change benchmark inputs.

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

uv run rii benchmark-curate \
  benchmarks/cases-v0.3.json \
  benchmarks/expansion-v0.4-selection.json \
  benchmarks/candidates-v0.4.json \
  --catalog-output benchmarks/candidates/rebuilt-v0.4.json \
  --manifest-output benchmarks/candidates/rebuilt-cases-v0.4.json
```

Discovery only produces `needs_review` or `rejected` candidates. A case enters a frozen manifest
only through a committed manual selection file with review notes; generated raw catalogs under
`benchmarks/candidates/` are intentionally ignored. The committed accepted catalog can be loaded
again to reproduce the manifest without relying on a later, mutable GitHub search result.

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
  llm_client.py           OpenAI-compatible Groq/OpenCode analysis
  models.py              typed domain models
  repository_index.py    repository map and Python AST index
  scoring.py             severity, urgency, priority rules
  service.py             ranking orchestration
```

See `docs/architecture.md` for system boundaries and
`docs/benchmark-expansion.md` for candidate acceptance and expansion protocol.

## Evaluation

The current frozen benchmark contains 20 closed issues with linked fix PRs across seven projects:

- Main: Starlette (4) and FastAPI (3).
- Calibration: Typer (2) and Rich (2).
- Generalization: Textual (3), AnyIO (3), and pytest (3).

Each case uses a committed Issue snapshot and a frozen pre-fix SHA. Only Git-tracked files are
eligible for candidate retrieval.

On the 20-case manifest, the graph-reranked deterministic path completed every case and achieved
File Recall@1 `0.3500`, Recall@5 `0.8583`, Recall@10 `0.8750`, Recall@20 `0.9250`, and MRR
`0.5663`. Static Python import, imported-symbol call, call-name, and test-import evidence reranks
within fixed Top-10 bands, so the lexical Top-10 and Top-20 member sets remain unchanged. The wider
suite deliberately includes harder lexical and multi-file cases; its metrics must not be compared
as if it were the same dataset as the historical 9-case run.

On the preserved 9-case manifest, Retrieval v2 achieved deterministic Recall@5 `0.7593` and MRR
`0.5083`; GPT-OSS 20B reranking achieved Recall@5 `0.8148` and MRR `0.8333`.

On the current 20-case manifest, GPT-OSS 20B Hybrid reranking achieved Recall@1 `0.4167`,
Recall@5 `0.8583`, Recall@10 `0.8750`, Recall@20 `0.9250`, and MRR `0.6738`. Seventeen of 20
cases returned valid model output; three exhausted two attempts and used deterministic fallback.
This improved aggregate Recall@1 and MRR over deterministic Retrieval v3, but calibration-tier
MRR decreased, so no per-project universal improvement is claimed.

DeepSeek V4 Flash Free achieved Recall@1 `0.6417`, Recall@5 `0.9250`, Recall@10 `0.9250`,
Recall@20 `0.9250`, and MRR `0.8458`. All 20 cases returned valid output with no fallback.
Average model latency was `14.6 s`, versus `0.8 s` for successful GPT-OSS 20B responses, so the
quality gain carries a substantial latency and token cost even while the OpenCode model is free.

This supports a bounded claim: deterministic retrieval finds most labeled fix files in its Top-20
pool across the expanded suite. It still does not establish root-cause accuracy.

See [`docs/benchmark-results.md`](docs/benchmark-results.md) for the protocol, per-tier results,
limitations, and next retrieval improvements.

On the historical 9-case suite, a fixed-seed comparison found identical localization metrics for
GPT-OSS 20B and 120B. The 120B run was 16.31% slower and used 48.38% more output tokens, so 20B
remains the default reranker. On a separate three-case full-schema smoke test, 120B succeeded on
the first attempt in 3/3 cases versus 2/3 for 20B; this sample is too small to establish a
production routing rule.

## Safety and scope

The MVP does not execute generated commands, modify the target repository, post labels, close issues, or create pull requests. The default Agent path is deterministic and offline. The optional LLM path analyzes only supplied Top-K evidence, cannot expand repository access, and still ends at the same human review gate. LLM output is an evidence-linked hypothesis, not a confirmed root cause.

## License

MIT
