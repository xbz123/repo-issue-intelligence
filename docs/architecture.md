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

Historical localization evaluation uses a separate, smaller LLM contract. `benchmark.py` checks
out each frozen pre-fix SHA, loads the complete Issue snapshot from the manifest rather than the
live GitHub API, verifies that the labeled fix files exist, and indexes only paths returned by
`git ls-files`. It runs deterministic retrieval and optionally asks Groq only to rerank the
bounded evidence IDs. Root-cause hypotheses are intentionally excluded from this benchmark
contract so their schema reliability does not contaminate file-ranking metrics. Retrieval
normalizes paths and identifiers, gives explicit stack-trace/source-path references the strongest
signal, searches bounded source content, downranks tests and documentation, and retains 20
candidates. Per-candidate evidence caps preserve candidate breadth before LLM reranking.

### Benchmark candidate pipeline

`benchmark_discovery.py` separates generated candidates from accepted ground truth:

```text
GitHub closed linked Issues
  -> discover linked fix PRs
  -> derive pre-fix SHA
  -> classify changed production files
  -> blocking and advisory audit checks
  -> needs_review / rejected catalog
  -> committed manual selection with review notes
  -> accepted catalog + next frozen manifest version
```

Blocking checks require a same-repository merged PR, an Issue that predates the fix, a derivable
pre-fix SHA, and a bounded non-empty set of existing production source files. Advisory checks flag
weak Issue descriptions, missing bug/diagnostic signals, ambiguous multi-commit history, and
missing textual closing references. Automation never changes a candidate to `accepted`; only the
explicit curation step can do that, and duplicate Issues, fix PRs, and case IDs are rejected.

`agent_store.py` persists three SQLite records:

- the current `AgentRun`;
- append-only node traces;
- a state snapshot after every completed node and after a terminal node failure.

The persisted snapshots make intermediate state inspectable. Automatic process-resume from a snapshot is not implemented in this version.

### Interfaces

- Typer CLI for synchronization, ranking, indexing, investigation, Agent runs, human review,
  benchmark discovery/audit/curation, evaluation, and API startup.
- FastAPI endpoints for issue analysis, repository indexing, Agent run creation/query, and review.

## Current boundaries

The MVP uses LangGraph and persistent Agent state and remains synchronous. Its default path is
deterministic and offline; the CLI can optionally add a bounded Groq LLM analysis step. It does
not include background workers, automatic snapshot resume, or generated-command execution.
The current benchmark contains 20 cases across seven repositories, which is useful for error
analysis but not statistically strong enough for a broad quality claim. The historical nine-case
manifest remains frozen for comparisons. LLM hypotheses are not confirmed root causes. Retrieval
remains lexical/content based; it has no import graph, call graph, test-to-source mapping, or
semantic vector index.

## Next workflow extensions

```text
current human_review
  -> import/call-graph evidence
  -> inspect Git history and related tests
  -> rerun deterministic and Hybrid on manifest v3
  -> add symbol labels and expand to 30-50 cases
```

The human review node remains mandatory before any future execution step.
