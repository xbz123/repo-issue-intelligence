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
the path is concrete rather than an abstract/protocol or auxiliary layer. At most one additional
non-auxiliary base-shortlist slot is reserved for an exact path, specific title-to-path match, path
identifier, or primary symbol match. Once selected, those candidates cannot be evicted by a weaker
tail expansion. Git co-change evidence scans a fixed window of the 100 most recent commits reachable
from the frozen HEAD and uses at most 50 commits that touch a lexical seed. It disables lazy network
fetching and has a 30-second local Git safety cap; a timeout or missing object contributes no history
evidence rather than searching an unbounded history;
blames at most five lines for each of two seed candidates, and ignores broad commits. File scoring
and symbol selection are separate: file scores retain the lexical/graph/history contract, while functions
inside each file are selected using source-scoped direct identifier references, exact non-fenced
mention frequency, and normalized title-term rarity, with the original lexical match as a fallback. Direct references come from
inline code, fenced examples, tracebacks, and title identifiers; non-call qualified identities are
retained with their original case and dot boundaries. Bare names are direct only when unique in the
final candidate range and at least five non-underscore characters long, constrained by an exact
owner, or scoped by a path that resolves to exactly one repository file. Fenced reproduction code
can contribute direct candidates but does not add repeated-mention votes. Loose suffix matching remains available for file retrieval, but an ambiguous
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
names. Leading function-local `from` imports are resolved only when they occur after an optional
docstring in the function's initial import block and the imported name is not a parameter, global,
nonlocal, assignment target, later import, or other local binding. These calls are stored separately:
their direct relation cannot rerank or qualify as a candidate expansion by itself. A single
constructor-aware second hop may add a tail candidate, but it cannot receive strong Top-10 promotion
and propagation stops before a second function-local import. Calls rooted at an unshadowed module
import retain their complete qualified target, including canonicalized aliases. The investigator
may use a shared target in the bounded tail expansion only when the Issue explicitly references the
full call and seed caller, two or three production files contain that call, and caller identity is
unambiguous or has exactly one non-overload implementation. Test seeds, common calls, shadowed
module roots, duplicate implementations, and unresolved attribute receivers cannot trigger this
relation. Shared-call evidence contributes no reranking score or strong Top-10 promotion. The
repository map also stores safe module-level imported-symbol bindings. From an exact Issue path,
the investigator may follow one unique `__init__.py` re-export into a production target only when
the source actually reads the imported name, the target definition is unique, and both files share
a non-generic package subsystem. Unused, conditional, shadowed, ambiguous, auxiliary, and
cross-subsystem routes are skipped. Re-export evidence is
expansion-only and tail-protected, so it cannot rerank an existing shortlist. Other bounded two-hop propagation
follows the exact target function and only that function's resolved external calls. The
investigator also preserves ordered Python traceback frames. A frame can influence within-file
symbol selection only when its path resolves to one repository file and its function resolves to
one symbol in that file; the deepest such frame wins. Installed paths can omit a confirmed
`src`/`lib` layout prefix, but real top-level packages and ambiguous suffixes are not stripped. The
investigator also recognizes exact relative `path.py#L...` source references and immutable GitHub
`blob/<40-character-commit>/path.py#L...` links. Relative references use the indexed checkout;
immutable links load that path from the referenced local Git object, resolve the enclosing
qualified symbol in that historical source, and require the identity to remain present in the
indexed file. Mutable branch links, unavailable revisions, ambiguous paths, unparsable historical
source, traversal-style paths, and identities absent from the indexed checkout are skipped. At most
the first eight valid references are evaluated. Source-line evidence affects only within-file
symbol selection and cannot override a resolved traceback frame or change file scores. The
investigator also accepts at most four bounded fenced source excerpts with 3-12 non-empty lines
and 60-2,000 characters. It evaluates them only in files matching an Issue path or basename,
ignores Python trailing-comment differences, and requires the excerpt to occur once in exactly one
eligible file and resolve to one smallest enclosing symbol. Repeated excerpts within a file or
across same-basename files are skipped. The source file must remain inside the repository, be valid
UTF-8 without NUL bytes, and be at most 1 MB. Traceback and exact source-line evidence retain higher
priority, while shared-call inference cannot override an accepted excerpt. Excerpts affect only
within-file symbol selection, not file scores, Git history, or blame.
The investigator can also map a syntactic class call from inline or fenced Issue code to a
constructor when the class is named in the title, its qualified `__init__` identity resolves to one
file, and either the title explicitly describes construction or its non-owner terms are supported
by the constructor docstring. A title method name or complete qualified method reference takes
priority. Duplicate owners, label-only class names, unrelated setup calls, and ambiguous
constructors are skipped. Constructor evidence affects only within-file symbol selection and is
disabled for blame selection.
The investigator can also use an adjacent owner-to-method phrase in the Issue title as qualified
method evidence. The owner must contribute at least two semantic terms, the method must add a
non-owner and non-generic term, and one strongest production method must resolve within the file.
Test-source symbols, tied matches, and generic method terms are rejected. This evidence affects only
within-file symbol selection and is disabled for file scoring and blame selection.
The investigator emits confirmed facts, confidence-scored hypotheses, missing evidence, and a
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

Each node records its input/output summary, status, attempt number, error, and elapsed time. Generic
runtime failures retain one compatibility retry before the run is marked failed. Provider errors
are error-aware: invalid JSON/schema and evidence-contract failures receive one additional strict
attempt, while transport, HTTP 429, and HTTP 5xx errors use bounded exponential backoff. A positive
`retry-after` value is used as the minimum delay, with every wait capped at 30 seconds. Retried
contract output must pass the unchanged schema and evidence checks; malformed output is never
repaired locally.

When an operator explicitly enables OpenCode analysis, two nodes are inserted before review:

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
budget. The default limits are 200 numbered source lines per snippet and 100,000 repository-evidence
characters per request; both are recorded in evaluation artifacts. Direct library callers must
explicitly opt out to run a full-file public-repository diagnostic. `llm_analyze` calls OpenCode DeepSeek V4 Flash through
`https://opencode.ai/zen/go/v1/chat/completions` using `json_object`, an
explicit compact schema prompt, and local Pydantic validation. The provider returns five fields:
summary, Issue type, reproduction completeness, one observation per snippet, and exactly one
evidence-grounded hypothesis. The request omits the duplicate deterministic-candidate list because
the bounded snippets already carry file, symbol, line, and source content. Every snippet must
receive one support/contradiction/neutral observation, and the hypothesis must cite a supplied
evidence ID; missing or unknown IDs fail the node. The client derives the affected component from
the first deterministic snippet, contradictions from observation alignment, retained evidence
order from the supplied IDs, and the more-evidence flag from the hypothesis's named artifacts.
The normalized result still uses the full persisted `LLMAnalysis` model, so historical runs remain
readable.
An Issue with no readable deterministic evidence skips the provider request, retains its
deterministic investigation with `llm_analysis=null`, records the Issue number in
`skipped_no_evidence_issue_numbers`, and continues to the human-review gate without retrying.
OpenCode disables reasoning and uses temperature `0.1`, a 20,000-token budget, and a 180-second
timeout in both the normal runtime and reliability evaluator. An analysis completion that reports
`finish_reason=length` fails as non-retryable `output_truncated`; the workflow does not repeat the
same request with an already exhausted 20,000-token budget.
The trace records model, request ID, token usage, and latency, but never stores the API key.
Settings loads the OpenCode credential as a `SecretStr`. The CLI does not expose provider or model
selection; `--llm` always uses `deepseek-v4-flash`. Issue bodies are not locally truncated, while
the evidence budgets, Top-K selection, tracked-file scope, and sensitive-file exclusion bound what
repository content can enter a request. The provider context window remains a hard external limit.

`agent-evaluate` exercises this complete path against selected frozen benchmark cases. Repository
maps are restricted to `git ls-files` so ignored artifacts in a reused checkout cannot enter the
Agent evidence. A case counts as successful only when the complete local schema and evidence-ID
contract pass, the graph reaches `awaiting_review`, and the final public Agent payload survives a
SQLite JSON round-trip. The result keeps full validated analysis content plus the requested output
ceiling, request attempts, tokens, latency, skip state, and failure category; failures remain in
the aggregate denominator. `--omit-max-tokens` is a diagnostic-only switch that removes the
`max_tokens` request field and records a null ceiling so the provider default can be compared with
the explicit 20,000-token protocol.
This reliability suite is deliberately separate from rank-only localization metrics.
When an HTTP-success response fails JSON/schema or evidence-contract validation, the exception
retains its request ID, system fingerprint, input/output tokens, and provider latency. The
evaluator accumulates this telemetry across failed retries instead of reporting zero usage.
Failure categories distinguish invalid JSON/schema, incomplete observation coverage, and unknown
evidence IDs.

Localization evaluation uses a separate rank-only model contract. `benchmark.py` checks out each
frozen pre-fix SHA, reusing a locally cached commit without a network request, loads the complete
Issue snapshot from the manifest rather than the live GitHub API, verifies that the labeled fix
files exist, and indexes only paths returned by `git ls-files`. Repository maps are cached outside
the checkout and reused only when repository identity, exact SHA, tracked/materialized file scope,
index/cache schema, and complete interpreter identity all match. Cached maps rebind their absolute root to the
current checkout; corrupt or stale entries rebuild through an atomic replacement and cannot change
benchmark success semantics. Per-case results retain the cache hit/miss state so cold and warm
latency remain auditable. It runs deterministic retrieval
and optionally asks OpenCode `deepseek-v4-flash` to rerank selected evidence IDs. The benchmark
does not expose provider or model overrides, and it no longer has a full-analysis variant. DeepSeek
receives a plain chat-completions request without `response_format`; the response contract is one
unique `RANK:` line containing at most three evidence IDs. Reasoning is disabled, output starts at
8,192 tokens and expands once to 20,000 only after truncation. Issue bodies and selected evidence
items have no project-defined character cap. Root-cause hypotheses are
intentionally excluded so schema reliability does not contaminate localization metrics. Retrieval
normalizes paths and identifiers, rejects
dotted-name/URL false path matches, gives explicit stack-trace/source-path references the strongest
signal, searches bounded source content, downranks tests and documentation, retains 20 candidates,
and applies bounded graph/history evidence. Compound identifier variants preserve source term
order rather than depending on set iteration. Python AST symbols retain both their local name and qualified
class/function ownership. Optional symbol labels are aggregated only across labeled cases; exact
file-plus-symbol matches accept either the backward-compatible local name or the qualified identity
and retain the candidate file rank.

The runner retries only transport failures, HTTP 429, and HTTP 5xx responses. Other HTTP errors,
missing or multiple `RANK:` lines, empty ranks, and unknown evidence IDs immediately use the
deterministic fallback. Aggregate output keeps fallback cases in the denominator and records
protocol success rate, successful-rerank MRR, overall MRR, and fallback reasons separately.
Provider attempts, including failed attempts and the truncation retry, contribute to stored token
and latency totals.

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
  full-analysis reliability, benchmark discovery/audit/curation, evaluation, and API startup.
- FastAPI endpoints for issue analysis, repository indexing, Agent run creation/query, and review.

## Current boundaries

The MVP uses LangGraph and persistent Agent state and remains synchronous. Its default path is
deterministic and offline; the CLI can optionally add a bounded OpenCode DeepSeek analysis step. It does
not include background workers, automatic snapshot resume, or generated-command execution.
The current benchmark contains 90 cases across 43 repositories and 87 manually reviewed symbol
targets across 63 cases. This is materially stronger for error analysis but still not statistically
strong enough for a broad quality claim. Manifest versions 2 and 3 are retained only as superseded
historical artifacts because their pre-fix audit was incorrect. Manifest version 5 is retained as
the reproducible input for the corrected 20-case DeepSeek run; version 6 is the retained 32-case
expansion, version 7 is the qualified-symbol suite, version 8 is the retained 50-case expansion,
version 9 is the retained 60-case expansion, version 10 is the retained 70-case batch, and version
11 is the retained 80-case batch, and version 12 is the current 90-case reviewed batch toward the
planned 200-case suite.
LLM hypotheses are
not confirmed root causes. Retrieval has bounded Python static/history relations, function-level
resolved calls, shared qualified external-call evidence, title-scoped expansion-only reverse-import
evidence, qualified class/function ownership, and a single-best-symbol selector, but not
general cross-file control-flow beyond bounded resolved-name/shared-call relations, receiver/type resolution, runtime/backend
dispatch, a cross-language graph, semantic test-to-source mapping, multi-symbol ranking, or a
vector index.

## Next workflow extensions

```text
current human_review
  -> add runtime/backend dispatch and multi-symbol ranking
  -> add semantic test-to-source mapping
  -> add cross-language graph evidence
  -> improve temporal and multi-file balance before adding more cases
```

The human review node remains mandatory before any future execution step.
