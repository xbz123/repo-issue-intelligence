# Repo Issue Intelligence

Repository-aware GitHub issue prioritization and investigation, built around an explicit two-stage workflow: rank every open issue cheaply, then investigate only the highest-value issues against the codebase.

The project is an initial, runnable Agent MVP. It does not require an LLM API to produce useful results. Priority decisions are explainable, repository evidence is collected deterministically, and root causes are represented as hypotheses rather than unsupported conclusions. An optional Groq-backed analysis step can inspect bounded code evidence for Top-K issues while preserving the offline baseline.

## What it does

- Synchronizes open GitHub issues with pagination and pull-request filtering.
- Detects likely duplicate issue pairs using title similarity and token overlap.
- Separates severity, urgency, and engineering priority.
- Applies weighted scoring plus hard P0/P1 override rules.
- Builds a cacheable repository map with languages, runtime files, entrypoints, imports, classes, functions, tests, and detected frameworks.
- Ranks candidate files and symbols using issue-to-code evidence.
- Produces confirmed facts, confidence-scored hypotheses, and a safe reproduction plan.
- Uses LangGraph to route Top-K issues through a repository investigation workflow.
- Optionally calls Groq-hosted GPT-OSS 20B with strict JSON Schema output.
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

Run the frozen real-project benchmark:

```bash
uv run rii benchmark benchmarks/cases.json \
  --variant deterministic \
  --output benchmarks/results/deterministic-retrieval-v2.json

LLM_MAX_EVIDENCE_CHARS=16000 LLM_MAX_OUTPUT_TOKENS=1600 \
uv run rii benchmark benchmarks/cases.json \
  --variant hybrid \
  --model openai/gpt-oss-20b \
  --temperature 0.1 \
  --seed 1337 \
  --llm-delay-seconds 40 \
  --output benchmarks/results/hybrid-20b-retrieval-v2.json
```

The Hybrid benchmark uses a deliberately small reranking schema rather than the full investigation
schema. This isolates file-ranking quality from hypothesis-generation reliability and avoids
misclassifying schema failures as localization failures. Each evidence snippet is capped so the
model sees a broad candidate set under the same total character budget. Manifest version 2 embeds
the complete evaluated Issue snapshot, and repository indexing is restricted to `git ls-files`;
live Issue edits and ignored artifacts in reused workspaces therefore cannot change benchmark
inputs.

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

The v0.3 API continues to run the offline workflow. Optional Groq analysis is exposed through
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
  cli.py                 command-line interface
  duplicates.py          issue similarity and duplicate candidates
  github_client.py       paginated GitHub REST synchronization
  investigator.py        file/symbol ranking and hypothesis generation
  evidence.py            bounded repository source evidence collection
  llm_client.py           strict-schema Groq GPT-OSS analysis
  models.py              typed domain models
  repository_index.py    repository map and Python AST index
  scoring.py             severity, urgency, priority rules
  service.py             ranking orchestration
```

See `docs/architecture.md` for boundaries and planned milestones.

## Evaluation

The first frozen benchmark contains nine closed issues with linked fix PRs:

- Starlette: four main benchmark cases.
- Typer: two simple calibration cases.
- Textual: three complex generalization cases.

Each case uses a committed Issue snapshot and a frozen pre-fix SHA. Only Git-tracked files are
eligible for candidate retrieval.

At the frozen pre-fix commits, Retrieval v2's deterministic path achieved File Recall@1 `0.2222`,
Recall@5 `0.7593`, Recall@10/20 `0.9444`, and MRR `0.5083`. This improved Recall@5 by `0.3149`
absolute over Retrieval v1. GPT-OSS 20B reranking achieved Recall@1 `0.5000`, Recall@5 `0.8148`,
and MRR `0.8333`. Eight of nine model requests succeeded; the remaining Groq HTTP 429 case used
the deterministic fallback and remains included in the aggregate.

This supports two bounded claims: deterministic retrieval now finds most labeled fix files in its
Top-20 pool, and the small model materially improves first-file ordering when the evidence is
available. It still does not establish root-cause accuracy.

See [`docs/benchmark-results.md`](docs/benchmark-results.md) for the protocol, per-tier results,
limitations, and next retrieval improvements.

A fixed-seed comparison found identical localization metrics for GPT-OSS 20B and 120B. The 120B
run was 16.31% slower and used 48.38% more output tokens, so 20B remains the default reranker.
On a separate three-case full-schema smoke test, 120B succeeded on the first attempt in 3/3 cases
versus 2/3 for 20B; this sample is too small to establish a production routing rule.

## Safety and scope

The MVP does not execute generated commands, modify the target repository, post labels, close issues, or create pull requests. The default Agent path is deterministic and offline. The optional LLM path analyzes only supplied Top-K evidence, cannot expand repository access, and still ends at the same human review gate. LLM output is an evidence-linked hypothesis, not a confirmed root cause.

## License

MIT
