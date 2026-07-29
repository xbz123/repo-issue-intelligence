# Architecture

## Scope

Repo Issue Intelligence uses a two-stage workflow:

1. Apply inexpensive, deterministic analysis to every open issue.
2. Investigate only selected or high-priority issues against a cached repository map.

The current MVP ends at a human review gate. It does not execute generated commands, modify target repositories, post labels, close issues, or create pull requests.

## Components

### GitHub synchronization

`GitHubClient` reads the repository issues endpoint with a stable page size, filters pull requests returned by that endpoint, validates the `owner/name` input, and converts API objects into typed `IssueRecord` values.

### Issue ranking

`scoring.py` keeps severity, urgency, and engineering priority separate. Weighted factors produce a score, while explicit security, data-loss, and release-blocker rules may override the weighted result.

### Duplicate detection

`duplicates.py` combines token Jaccard similarity with title similarity. This implementation is an interpretable baseline and is currently quadratic in the number of issues.

### Repository indexing

`repository_index.py` scans supported source files and uses Python AST parsing to collect imports, classes, functions, line ranges, entrypoints, runtime files, tests, and framework indicators.

### Investigation

`investigator.py` ranks candidate files and symbols using explicit evidence signals. It emits confirmed facts, confidence-scored hypotheses, missing evidence, and a non-executed reproduction plan. Candidate locations are not presented as confirmed root causes.

### Agent runtime

`agent_workflow.py` compiles a LangGraph `StateGraph` with five deterministic nodes:

```text
rank_issues
  -> route_top_k
  -> build_repository_map
  -> investigate_issues
  -> human_review
```

Each node records its input/output summary, status, attempt number, error, and elapsed time. A failed node is retried once before the run is marked failed.

When an operator explicitly enables Groq analysis, two nodes are inserted before review:

```text
rank_issues
  -> route_top_k
  -> build_repository_map
  -> investigate_issues
  -> collect_code_evidence
  -> llm_analyze
  -> human_review
```

`collect_code_evidence` reads only deterministic candidate locations, verifies that resolved
paths remain inside the repository, skips sensitive filenames, and enforces a total character
budget. `llm_analyze` calls `openai/gpt-oss-20b` through Groq with strict JSON Schema output.
Every snippet must receive one support/contradiction/neutral observation, and every hypothesis
must cite a supplied evidence ID; missing or unknown IDs fail the node. Contradicting observations
provide a deterministic fallback when the model omits the free-form contradiction list. If the
model requests more evidence, at least one hypothesis must name the missing artifact.
GPT-OSS uses low reasoning effort and a bounded completion budget by default.
The trace records model, request ID, token usage, and latency, but never stores the API key.

`agent_store.py` persists three SQLite records:

- the current `AgentRun`;
- append-only node traces;
- a state snapshot after every completed node and after a terminal node failure.

The persisted snapshots make intermediate state inspectable. Automatic process-resume from a snapshot is not implemented in this version.

### Interfaces

- Typer CLI for synchronization, ranking, indexing, investigation, Agent runs, human review, and API startup.
- FastAPI endpoints for issue analysis, repository indexing, Agent run creation/query, and review.

## Current boundaries

The MVP uses LangGraph and persistent Agent state and remains synchronous. Its default path is
deterministic and offline; the CLI can optionally add a bounded Groq LLM analysis step. It does
not include background workers, automatic snapshot resume, generated-command execution, or a
completed benchmark against historical fix PRs. LLM hypotheses are not confirmed root causes.

## Next workflow extensions

```text
current human_review
  -> stack_trace evidence
  -> inspect Git history and related tests
  -> multi-model evaluation and routing
  -> historical fix-PR benchmark
```

The human review node remains mandatory before any future execution step.
