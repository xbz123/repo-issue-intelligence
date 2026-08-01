# Benchmark Expansion

> **Integrity correction (2026-07-30):** The v0.4 expansion selected valid Issue/Fix-PR pairs and
> reviewed production files, but the original pre-fix derivation was wrong for most cases.
> Manifest v5 and `candidates-v0.7.json` use the parent of the first PR commit. Historical v0.4
> manifests and results are retained for provenance and must not be used as current quality
> evidence.

## v0.12 qualified-symbol outcome

The current manifest is version 7. It keeps the 32 independently reviewed cases across 13
repositories and adds qualified class/function ownership without changing the frozen Issue,
repository, commit, or file ground truth:

- 12 main, 7 calibration, and 13 generalization cases;
- 17 reviewed symbol targets across 16 cases;
- 32/32 successful frozen-checkout evaluations;
- two complete review-fixed deterministic runs with identical candidate and metric output after
  removing timing fields.

The complete review-fixed selector, content matcher, and call-edge contract change all 32 file
orderings relative to v0.11. File Recall@1 is `0.4479`, Recall@5 `0.7812`, Recall@10 `0.8906`,
Recall@20 `0.9844`, and MRR `0.6428`. Aggregate Symbol Recall@1 is `0.1875`, Recall@5 `0.4688`,
Recall@10 `0.4688`, Recall@20 `0.5938`, and MRR `0.3099`. The
qualified contract can represent Trio's reviewed
`WorkerThread.__init__` method, but the issue does not explicitly reference `__init__`; the
review-fixed selector therefore keeps it as a miss instead of allowing an owner-name match to
override stronger local method evidence. Across all 32 cases, all 32 per-file symbol candidate
lists change after normalizing away qualified-name representation. This includes preventing
`websocket.accept` from degrading to either the Python method `WebSocket.accept` or terminal-name
source-content evidence, rejecting ambiguous basename scope, and preventing an unscoped `__call__`
reference from selecting unrelated implementations across Starlette's candidate files. Qualified
caller edges also prevent repeated methods in one file from sharing calls or creating fabricated
relation evidence. Inference now consumes only direct `ast.Name` calls that lexical scope analysis
resolves to one module function or imported repository symbol. Parameters, assignments, local
imports, loop/context/exception/match targets, nested bindings, closures, statically visible
`global` assignments, definition-time rebinding, `self.method()`, and `receiver.method()` cannot be
reassigned to a same-named function or start a strong two-hop promotion. Broad legacy call fields
remain readable but do not drive ranking.
Bare source-content identifiers now require identifier boundaries, so short names such as `get`,
`set`, `data`, and `run` cannot match `target`, `reset`, `metadata`, or `runner`. Source-layout
inference also preserves top-level `src`/`lib` modules and packages instead of stripping those
names unconditionally.
Specific title-to-path evidence is protected from weaker tail expansion; this recovers
`pydantic/experimental/pipeline.py` without restoring unsafe name propagation.

An authorized OpenCode `deepseek-v4-flash-free` rerank subsequently completed all 32 cases on this
same manifest and deterministic candidate pool. Twenty-eight responses were valid and four used
the deterministic fallback after two `json_invalid` attempts. File Recall@1 reached `0.7135`,
Recall@5 `0.8958`, Recall@10 `0.9375`, Recall@20 `0.9844`, and MRR `0.8547`; on the 16 labeled
cases, Symbol Recall@1 reached `0.4688` and symbol MRR `0.5245`. This is evidence that model
reranking improves ordering within the frozen pool, not that it can recover Rich's missing
`highlighter.py` or replace candidate generation. The full result and fallback audit are recorded
in `docs/benchmark-results.md`.

## v0.11 expansion outcome

Manifest version 6 expanded the corrected 20-case suite to 32 cases and is retained as
`benchmarks/cases-v0.11-32-cases.json`.

The discovery pass screened 186 Issue/Fix-PR candidates from Click, Pydantic, SQLAlchemy, HTTPX,
Django, aiohttp, HTTPCore, Flask, Werkzeug, and Trio. Thirty-two reached `needs_review`; manual
diff and relationship review accepted the following 12:

| Tier | Repository | Issue / fix PR | Expected production files |
|---|---|---|---|
| Calibration | Click | [#2809](https://github.com/pallets/click/issues/2809) / [#3256](https://github.com/pallets/click/pull/3256) | `src/click/termui.py` |
| Calibration | Click | [#2819](https://github.com/pallets/click/issues/2819) / [#3678](https://github.com/pallets/click/pull/3678) | `src/click/core.py` |
| Calibration | Click | [#3360](https://github.com/pallets/click/issues/3360) / [#3434](https://github.com/pallets/click/pull/3434) | `src/click/formatting.py` |
| Main | Pydantic | [#9532](https://github.com/pydantic/pydantic/issues/9532) / [#9570](https://github.com/pydantic/pydantic/pull/9570) | `pydantic/type_adapter.py` |
| Main | Pydantic | [#13465](https://github.com/pydantic/pydantic/issues/13465) / [#13483](https://github.com/pydantic/pydantic/pull/13483) | `pydantic/_internal/_fields.py` |
| Main | Pydantic | [#13507](https://github.com/pydantic/pydantic/issues/13507) / [#13516](https://github.com/pydantic/pydantic/pull/13516) | `pydantic/experimental/pipeline.py` |
| Main | Pydantic | [#13520](https://github.com/pydantic/pydantic/issues/13520) / [#13521](https://github.com/pydantic/pydantic/pull/13521) | `pydantic/_internal/_typing_extra.py` |
| Generalization | aiohttp | [#5303](https://github.com/aio-libs/aiohttp/issues/5303) / [#13170](https://github.com/aio-libs/aiohttp/pull/13170) | `aiohttp/web_response.py` |
| Generalization | aiohttp | [#13099](https://github.com/aio-libs/aiohttp/issues/13099) / [#13137](https://github.com/aio-libs/aiohttp/pull/13137) | `aiohttp/web_request.py` |
| Main | HTTPCore | [#946](https://github.com/encode/httpcore/issues/946) / [#955](https://github.com/encode/httpcore/pull/955) | `httpcore/_synchronization.py` |
| Generalization | Werkzeug | [#3138](https://github.com/pallets/werkzeug/issues/3138) / [#3140](https://github.com/pallets/werkzeug/pull/3140) | `src/werkzeug/serving.py` |
| Generalization | Trio | [#3472](https://github.com/python-trio/trio/issues/3472) / [#3473](https://github.com/python-trio/trio/pull/3473) | `src/trio/_core/_thread_cache.py` |

Ten of the new cases initially received function labels taken from reviewed production hunks.
HTTPCore's fix is an import guard rather than a function, and Trio changes
`WorkerThread.__init__`; manifest v6's unqualified-symbol contract could not distinguish that
method from other `__init__` definitions, so neither case received a misleading symbol label.
HTTPCore's coverage-only `_backends/anyio.py` edit is also excluded from file ground truth.

The v0.11 deterministic result is File Recall@1 `0.4375`, Recall@5 `0.8490`, Recall@10 `0.9062`,
Recall@20 `0.9688`, and MRR `0.6064`. On the 15 symbol-labeled cases, Symbol Recall@1 is `0.2000`,
Recall@5 `0.5000`, Recall@10 `0.5333`, Recall@20 `0.6000`, and MRR `0.2926`. The lower symbol
scores are retained as an honest measurement of the expanded suite's unresolved within-file
localization problem.

## v0.4 outcome

The v0.4 expansion increased the frozen file-localization benchmark from 9 to 20 cases and from
3 to 7 repositories. It added 11 manually reviewed Issue/Fix-PR pairs:

| Tier | Repository | Issue / fix PR | Expected production files |
|---|---|---|---|
| Generalization | AnyIO | [#1203](https://github.com/agronholm/anyio/issues/1203) / [#1211](https://github.com/agronholm/anyio/pull/1211) | `src/anyio/_backends/_asyncio.py` |
| Generalization | AnyIO | [#1220](https://github.com/agronholm/anyio/issues/1220) / [#1224](https://github.com/agronholm/anyio/pull/1224) | `src/anyio/_backends/_asyncio.py`, `src/anyio/from_thread.py` |
| Generalization | AnyIO | [#1231](https://github.com/agronholm/anyio/issues/1231) / [#1232](https://github.com/agronholm/anyio/pull/1232) | `src/anyio/_backends/_trio.py` |
| Main | FastAPI | [#10719](https://github.com/fastapi/fastapi/issues/10719) / [#13920](https://github.com/fastapi/fastapi/pull/13920) | `fastapi/dependencies/utils.py` |
| Main | FastAPI | [#10997](https://github.com/fastapi/fastapi/issues/10997) / [#14616](https://github.com/fastapi/fastapi/pull/14616) | `fastapi/dependencies/utils.py` |
| Main | FastAPI | [#15401](https://github.com/fastapi/fastapi/issues/15401) / [#15077](https://github.com/fastapi/fastapi/pull/15077) | `fastapi/routing.py` |
| Generalization | pytest | [#634](https://github.com/pytest-dev/pytest/issues/634) / [#1766](https://github.com/pytest-dev/pytest/pull/1766) | `_pytest/python.py` |
| Generalization | pytest | [#3862](https://github.com/pytest-dev/pytest/issues/3862) / [#14746](https://github.com/pytest-dev/pytest/pull/14746) | `src/_pytest/fixtures.py` |
| Generalization | pytest | [#14683](https://github.com/pytest-dev/pytest/issues/14683) / [#14694](https://github.com/pytest-dev/pytest/pull/14694) | `src/_pytest/fixtures.py` |
| Calibration | Rich | [#2027](https://github.com/Textualize/rich/issues/2027) / [#2038](https://github.com/Textualize/rich/pull/2038) | `rich/console.py`, `rich/highlighter.py` |
| Calibration | Rich | [#3577](https://github.com/Textualize/rich/issues/3577) / [#4076](https://github.com/Textualize/rich/pull/4076) | `rich/ansi.py` |

The corrected 20-case base manifest has 7 main, 4 calibration, and 9 generalization cases. Its
deterministic runner completed 20/20 cases with Recall@1 0.3000, Recall@5 0.8583, Recall@10
0.8750, Recall@20 1.0000, and MRR 0.5396.

## Acceptance boundary

Generated discovery output is not benchmark ground truth. The pipeline enforces three states:

- `rejected`: at least one blocking audit check failed;
- `needs_review`: blocking checks passed, but a person must verify the relationship and labels;
- `accepted`: the pair appears in an explicit selection manifest with review notes.

A candidate must have:

- a public closed Issue and same-repository merged fix PR;
- a frozen Issue snapshot and ordered fix-PR commit history;
- a pre-fix SHA equal to the parent of the first PR commit and outside the PR commit set;
- at least one production source file that exists at the pre-fix commit;
- no more than five expected production files;
- manual confirmation that the PR fixes the selected Issue;
- manual review of renamed, generated, test, documentation, and added files;
- a unique Issue, fix PR, and case ID within the benchmark.

Advisory checks for bug wording, diagnostics, and textual closing keywords help prioritize review
but do not replace it. GitHub closing relationships and the PR diff remain the stronger evidence.

## Reproducible workflow

```bash
rii benchmark-discover agronholm/anyio fastapi/fastapi pytest-dev/pytest \
  Textualize/rich \
  --target-per-repository 3 \
  --scan-limit-per-repository 50 \
  --output benchmarks/candidates/discovered.json

rii benchmark-audit pytest-dev/pytest 634 1766 \
  --tier generalization \
  --output benchmarks/candidates/pytest-634-pr-1766.json

rii benchmark-curate \
  benchmarks/cases-v0.10-corrected-20-cases.json \
  benchmarks/expansion-v0.11-selection.json \
  benchmarks/candidates-v0.11.json \
  --catalog-output benchmarks/candidates/rebuilt-v0.11.json \
  --manifest-output benchmarks/candidates/rebuilt-cases-v0.11.json

rii benchmark benchmarks/cases.json \
  --variant deterministic \
  --output benchmarks/results/deterministic-v0.12-qualified-symbols-32-cases.json
```

Raw discovery catalogs are ignored because they are large, mutable review queues. The accepted
catalog, manual selection, frozen manifest, and evaluated results are committed. Current audits
must use the corrected ordered-commit checks. The v0.11 accepted catalog is
`candidates-v0.11.json`; `candidates-v0.7.json` remains the corrected audit record for the
20-case base suite.

## Candidate projects after v0.12

The 30-case threshold is now met without concentrating all new labels in one framework. Discovery
also showed why acceptance remains manual: the HTTPX and Django scans produced no reviewable
candidates in the configured window, while the SQLAlchemy review queue was dominated by
documentation changes. Those repositories remain useful future targets, but should not be added
to satisfy a quota.

| Repository | Intended role | Useful coverage |
|---|---|---|
| `encode/httpx` | Main | client transport, redirects, streaming, proxy behavior |
| `pydantic/pydantic` | Main | validation, schema generation, serialization |
| `sqlalchemy/sqlalchemy` | Generalization | ORM, SQL compilation, dialect-specific call paths |
| `django/django` | Generalization | larger repository and deeper cross-module behavior |
| `pallets/click` | Calibration | compact CLI parsing and option behavior |

The next expansion should prioritize repositories or cases that add runtime/backend dispatch,
multi-symbol edits, semantic test-to-source relationships, or cross-language behavior. Promote a
repository to a larger share only after its initial cases pass checkout validation and add failure
modes not already represented.
