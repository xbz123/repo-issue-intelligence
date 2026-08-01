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

`repository_index.py` scans supported source files and uses Python AST parsing to collect imports,
locally resolved Python import targets, imported symbols, called names, loaded symbol references,
classes, functions, line ranges, entrypoints, runtime files, tests, and framework indicators.
Broad called-name and local-name caller maps remain serialized for compatibility. The authoritative
`resolved_calls` field stores caller identity, local spelling, target repository file, and target
symbol. It is built with Python `symtable` scope information and accepts a direct `ast.Name` call
only when the name resolves to one unconditional module function or one explicit module-level
`from ... import ...` binding. Parameters, assignments, loop/context/exception/match targets,
local imports, nested bindings, closures, ambiguous definitions, statically visible `global`
assignments, or definition-time rebinding make a call unresolved. Dynamic mutation through
reflection is not resolved. `name_calls` and qualified-caller maps are compatibility views derived
from these resolved edges; ranking never falls back to their broader historical forms. A `src/` or
`lib/` prefix is removed only when that directory is a source-layout root without a root
`__init__.py`; top-level `src.py`, `lib.py`, and real `src`/`lib` packages keep their module names.

### Investigation

`investigator.py` ranks candidate files and symbols using explicit evidence signals. Lexical and
bounded content matching define the base pool. Static imports, scope-resolved calls and references,
caller-specific two-hop relations, matching-test imports, and bounded prior Git co-changes add
graph evidence. Graph weights still rerank inside fixed Top-10 bands; up to three expansion
candidates may enter the Top-20. One Top-10 diversity slot is reserved for a two-hop call candidate
only when both hops are exact resolved edges, the first symbol matches a specific title term, and
the path is concrete rather than an abstract/protocol or auxiliary layer. Candidates with an exact
path, specific title-to-path match, path identifier, or primary symbol match cannot be evicted by a
weaker tail expansion. Git evidence uses at most 50 prior commits from 100 fetched ancestors,
blames at most five lines for each of two seed candidates, and ignores broad commits. File scoring
and symbol selection are separate: file scores retain the lexical/graph/history contract, while functions
inside each file are selected using source-scoped direct identifier references and normalized
title-term rarity, with the original lexical match as a fallback. Direct references come from
inline code, fenced examples, tracebacks, and title identifiers; non-call qualified identities are
retained with their original case and dot boundaries. Bare names are direct only when unique in the
final candidate range, constrained by an exact owner, or scoped by a path that resolves to exactly
one repository file. Loose suffix matching remains available for file retrieval, but an ambiguous
basename cannot scope a direct symbol reference. A dotted value is direct only when its complete,
case-preserving qualified identity matches; source-content retrieval applies full identifier
boundaries to bare names and the same full-token boundary to dotted values, without reusing dotted
component terms. Syntactic object calls separately expose their local callee for Issue-text
matching. Repeated unscoped names and unmatched dotted terminals cannot
independently select a symbol. Owner names can disambiguate equivalent method names but do not
contribute semantic title terms or override a different explicitly referenced function. A callee
receives additional evidence only when at least two distinct issue-matching functions in the same
file have exact resolved edges to it. The caller and callee identities are preserved in relation
scoring and evidence. Calls through
`self.method()`, `receiver.method()`, or `module.function()` do not become local edges until a
receiver-aware resolver can prove their target. Older repository maps remain readable, but maps
without `resolved_calls` skip call-relation inference instead of falling back to broad legacy
names. Bounded two-hop propagation follows the exact target function and only that function's
resolved external calls. The
investigator emits confirmed facts, confidence-scored hypotheses, missing evidence, and a
non-executed reproduction plan. Candidate locations are not presented as confirmed root causes.

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

When an operator explicitly enables an OpenAI-compatible provider, two nodes are inserted before
review:

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
budget. `llm_analyze` can call GPT-OSS through Groq or DeepSeek V4 Flash through OpenCode.
Both providers use `/chat/completions`; only their base URL, credential, model, and structured
output capability differ. Groq uses strict JSON Schema output. OpenCode uses `json_object` plus
an explicit schema/example prompt and the same local Pydantic validation.
Every snippet must receive one support/contradiction/neutral observation, and every hypothesis
must cite a supplied evidence ID; missing or unknown IDs fail the node. Contradicting observations
provide a deterministic fallback when the model omits the free-form contradiction list. If the
model requests more evidence, at least one hypothesis must name the missing artifact.
GPT-OSS uses low reasoning effort and a 1,600-token completion budget by default. OpenCode uses a
4,096-token budget and 60-second timeout because reasoning tokens share the completion budget and
observed valid responses can exceed 30 seconds.
The trace records model, request ID, token usage, and latency, but never stores the API key.
Settings may load a primary and fallback Groq credential as `SecretStr` values, but automatic
credential failover is not enabled; operators must select the intended credential explicitly.
The OpenCode credential is also stored as `SecretStr` and selected explicitly with
`--provider opencode`.

Localization evaluation uses a separate, smaller LLM contract. `benchmark.py` checks out each
frozen pre-fix SHA, reusing a locally cached commit without a network request, loads the complete
Issue snapshot from the manifest rather than the live GitHub API, verifies that the labeled fix
files exist, and indexes only paths returned by `git ls-files`. It runs deterministic retrieval
and optionally asks the selected provider only to rerank bounded evidence IDs. Root-cause
hypotheses are intentionally excluded from this benchmark contract so their schema reliability
does not contaminate localization metrics. Retrieval normalizes paths and identifiers, rejects
dotted-name/URL false path matches, gives explicit stack-trace/source-path references the strongest
signal, searches bounded source content, downranks tests and documentation, retains 20 candidates,
and applies bounded graph/history evidence. Compound identifier variants preserve source term
order rather than depending on set iteration. Per-candidate evidence caps preserve candidate
breadth before LLM reranking. Python AST symbols retain both their local name and qualified
class/function ownership. Optional symbol labels are aggregated only across labeled cases; exact
file-plus-symbol matches accept either the backward-compatible local name or the qualified identity
and retain the candidate file rank.

### Benchmark candidate pipeline

`benchmark_discovery.py` separates generated candidates from accepted ground truth:

```text
GitHub closed linked Issues
  -> discover linked fix PRs
  -> load ordered PR commits
  -> derive pre-fix SHA from the first PR commit's parent
  -> reject SHAs inside the fix PR
  -> classify changed production files
  -> blocking and advisory audit checks
  -> needs_review / rejected catalog
  -> committed manual selection with review notes
  -> accepted catalog + next frozen manifest version
```

Blocking checks require a same-repository merged PR, an Issue that predates the fix, ordered PR
commit history, a pre-fix SHA outside the PR commit set, and a bounded non-empty set of existing
production source files. Advisory checks flag weak Issue descriptions, missing bug/diagnostic
signals, and missing textual closing references. Automation never changes a candidate to
`accepted`; only the explicit curation step can do that, and duplicate Issues, fix PRs, and case
IDs are rejected.

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
deterministic and offline; the CLI can optionally add a bounded Groq or OpenCode analysis step. It does
not include background workers, automatic snapshot resume, or generated-command execution.
The current benchmark contains 32 cases across 13 repositories and 17 manually reviewed symbol
targets across 16 cases. This is materially stronger for error analysis but still not statistically
strong enough for a broad quality claim. Manifest versions 2 and 3 are retained only as superseded
historical artifacts because their pre-fix audit was incorrect. Manifest version 5 is retained as
the reproducible input for the corrected 20-case Groq and OpenCode comparison; version 6 is the
retained 32-case expansion and version 7 is the current qualified-symbol suite. LLM hypotheses are
not confirmed root causes. Retrieval has bounded Python static/history relations, function-level
resolved calls, qualified class/function ownership, and a single-best-symbol selector, but not
cross-file control-flow beyond bounded two-hop resolved-name calls, receiver/type resolution, runtime/backend
dispatch, a cross-language graph, semantic test-to-source mapping, multi-symbol ranking, or a
vector index.

## Next workflow extensions

```text
current human_review
  -> add runtime/backend dispatch and multi-symbol ranking
  -> add semantic test-to-source mapping
  -> add cross-language graph evidence
  -> independently review another 8-18 cases after the 32-case suite stabilizes
```

The human review node remains mandatory before any future execution step.
