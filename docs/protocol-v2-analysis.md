# Explicit Analysis V2

R5 PR3 adds a pure local analysis contract and explicit low-level backend methods.
The existing `analyze()` methods, V1 prompts, provider five-field response schema,
rank-only reranking, default Agent workflow and CLI model defaults are unchanged.
PR2B storage merged separately in PR64; this analysis implementation neither imports
it nor requires SQLite. PR4 will connect storage, current transfer permission and
the V2 Agent entrypoints. PR3 alone does not enable G0 or G1.

## Pure contract

`normalize_analysis_v2(response, input_evidence_ids, evidence_lookup)` accepts the
existing `LLMAnalysisResponse`, an ordered sequence of IDs and a standard
`Mapping[str, EvidenceSnippet]`. It validates input identity, unique observations,
complete coverage and all cited IDs before deriving anything. An unknown first ID
such as `[E999, E7]` rejects the entire response; it never silently falls through
to E7.

The first cited ID is primary. `affected_component` and the local validation step
come from that snippet's file/symbol, not the first retrieval candidate. Local V2
output uses `input_evidence_ids` and `primary_evidence_id`, without the misleading
full-analysis `reranked_evidence_ids` field. The rank-only schema is unaffected.

Both backends append the same primary-citation instruction only on the explicit
V2 path. The local prompt version is `analysis-v2-primary-1`; the provider schema
still has exactly five top-level fields and no provider primary field.

## Explicit backend methods

`OpenAICompatibleIssueAnalyzer.analyze_v2(...)` and
`CodexCLIIssueAnalyzer.analyze_v2(...)` take `(issue, report, input_evidence_ids,
evidence_lookup)`. Preflight snapshots the selected snippets before dispatch;
neither backend rereads files or performs another truncation pass. The same pure
normalizer validates the response against the actual sent snapshot.

These library methods do not establish storage seal status, user authorization or
runtime retry policy. Their future caller must supply sealed evidence and current
transfer permission. They do not themselves retry mismatched metadata or enable
the future `agent-run --protocol v2` workflow.

The returned `AnalysisResultV2` separates:

- `requested`: actual chosen request parameters and client identity, never secrets.
- `reported`: a bounded whitelist of observed model/tier/usage/IDs; absent or invalid
  metadata stays null, including absent usage rather than fabricated zeros.
- `local`: actual elapsed time, HTTP status or CLI exit/thread information.
- `diagnostics`: safe mismatch labels, not a request to call the model again.

API response JSON `id` is `reported.response_id`; the `x-request-id` header is
`reported.request_id`. Codex `thread.started.thread_id` is `local.thread_id`, not
either HTTP identifier. CLI model/tier remain unknown unless a recognized event
explicitly reports them. A server's reported model string is not independent
proof of the underlying model or routing.

V2 provider/validation errors carry the same groups in `error.observations` where
available; both V2 boundaries suppress raw provider exception chains, including
traceback output. Model differences use the R5 diagnostic
`reported_model_differs_from_requested`. Timeout classification,
safe retries and terminal attempt persistence belong to later R5 runtime work.

## Historical compatibility

`project_legacy_analysis(raw_payload)` returns a detached wrapper marked `v1` and
`legacy_input_order`, preserving the original payload. An old `model` remains
legacy/requested; it is never reconstructed as reported telemetry.
`project_analysis_v2_to_v1(analysis)` produces an explicitly labeled compatibility
view without mutating V2 or storing another authority. No old database or JSON
artifact is rewritten automatically.

## Validation

Baseline: `ca6792592954f05c3aed76a70f4bc0d98126e845`; implementation branch:
`codex/protocol-v2-pr3`. Local focused tests: 103 passed; full suite: 620 passed,
one existing Starlette deprecation warning. Tests use HTTP MockTransport, fake CLI
execution and an isolated process with Store/SQLite/provider imports blocked.
Provider and rank-only schemas are compared structurally with a complete JSON
snapshot captured from the unchanged baseline models, not a new content hash.
Ruff, changed-region formatting, compileall and diff checks are required as well.

Initial code `565cdc4` passed Python 3.11/3.12 CI with 620 tests each. Independent
Astra review identified a Codex traceback privacy gap and a diagnostic-name mismatch.
Two traceback canary tests reproduced the privacy gap before the V2-only fix;
the revised focused suite passes 105 tests. Code `f935646` passed
[Python 3.11/3.12 CI](https://github.com/xbz123/repo-issue-intelligence/actions/runs/34165531039)
with 622 tests and one existing warning each. Independent Astra Standards and Spec
reviews confirmed both findings closed, with zero remaining issues. R5 3.1–3.8 are
verified in PR65; consult that PR for its merge state. Earlier gates are not substituted for
the revised code's validation.

These checks prove contract behavior and compatibility, not correctness of a root
cause hypothesis, provider routing, a live model call, Store integration or G0/G1.
