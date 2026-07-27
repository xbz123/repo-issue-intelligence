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

### Interfaces

- Typer CLI for synchronization, ranking, duplicate detection, indexing, investigation, and API startup.
- FastAPI endpoints for health checks, issue scoring, issue ranking, and repository indexing.

## Current boundaries

The MVP is deterministic and does not yet include LangGraph, LLM calls, persistent Agent state, tool-call tracing, or a benchmark against historical fix PRs. These are subsequent milestones and must not be claimed as completed functionality.

## Planned Agent workflow

```text
sync_issues
  -> normalize_issue
  -> detect_duplicates
  -> score_priority
  -> route_top_k
  -> build_repository_map
  -> locate_candidates
  -> inspect_git_and_tests
  -> generate_hypotheses
  -> build_reproduction_plan
  -> human_review
```

Each future node should persist inputs, outputs, evidence, confidence, errors, retries, latency, and the next transition. The human review node remains mandatory before any future execution step.
