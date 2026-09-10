# PR7A: V2 query and compatibility boundaries

This stage adds authenticated, read-only V2 projections on top of the existing
`AgentStoreV2`. It does not change the default V1 protocol, write paths, review
or retry services, or database schema.

`GET /v2/agent/runs/{run_id}` returns provenance, configuration and aggregate
state without full inputs, repository manifests, maps or evidence content.
Issue and attempt lists are bounded and metadata-only by default. Deterministic
Issue detail is available from the retained report; a sealed evidence list never
contains source content. Evidence content requires both an explicit evidence ID
and its current evidence-set ID, and is checked against the same run/Issue
binding. A missing, moved, or revoked analysis root is refused before data is
returned; the retained evidence itself does not require the checkout to remain.

List responses use `{protocol, run_id, items, total, limit, offset, next_offset}`;
Issue detail additionally exposes deterministic and selected-analysis fields,
not a duplicate result table. Evidence responses add `issue_number`,
`evidence_set_id` and `sealed_at`; items are only ID/rank/file/symbol/range/
truncation metadata. `limit` is 1–100 and `offset` is nonnegative. Store queries
page before returning records; metadata-only evidence queries do not SELECT
source `content`. All endpoints reuse PR6 token/Host/Origin/scope checks.

The six routes are run summary, `/issues`, `/issues/{issue_number}`,
`/issues/{issue_number}/evidence`,
`/issues/{issue_number}/evidence/{evidence_id}` and
`/issues/{issue_number}/attempts`. Sensitive database selection is exclusively
the server's `RII_API_V2_DATABASE`; requests cannot choose a database path.
Unknown resources return 404; unsafe/unavailable stores return 503;
authorization denial returns 401/403; over-limit pagination is rejected.

The explicit `agent-query` CLI uses the same read helpers. Summary, issue,
attempt and evidence metadata are the defaults. Source content requires the
explicit `--view evidence --issue --evidence-id --evidence-set-id
--include-content` selection and private atomic output protection.

The V1 adapter projects only deterministic runs with disabled/no-evidence LLM
state and a single representable review state. Mixed, pending-after-partial,
`needs_information`, active/unknown LLM states and other non-representable
states fail with an upgrade error rather than becoming a false approval or
successful analysis. Unversioned evaluation artifacts remain opaque legacy
JSON; known V2 artifacts are validated and unknown future protocol versions are
rejected.

Validation uses synthetic repositories/databases and no provider calls. Full
query/API/CLI/Store regression evidence is kept with the PR7A worktree; source
content and retained analyses remain sensitive artifacts.
