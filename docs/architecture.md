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

`agent_store.py` persists three SQLite records:

- the current `AgentRun`;
- append-only node traces;
- a state snapshot after every completed node and after a terminal node failure.

The persisted snapshots make intermediate state inspectable. Automatic process-resume from a snapshot is not implemented in this version.

### Interfaces

- Typer CLI for synchronization, ranking, indexing, investigation, Agent runs, human review, and API startup.
- FastAPI endpoints for issue analysis, repository indexing, Agent run creation/query, and review.

## Current boundaries

The MVP uses LangGraph and persistent Agent state, but remains synchronous and deterministic. It does not yet include LLM calls, background workers, automatic snapshot resume, generated-command execution, or a benchmark against historical fix PRs. These capabilities must not be claimed as completed functionality.

## Next workflow extensions

```text
current human_review
  -> stack_trace_and_content_evidence
  -> inspect_git_and_tests
  -> optional_llm_reranking
  -> benchmark
```

The human review node remains mandatory before any future execution step.
