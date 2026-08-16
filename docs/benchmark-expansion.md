# Benchmark Expansion

> **Integrity correction (2026-07-30):** The v0.4 expansion selected valid Issue/Fix-PR pairs and
> reviewed production files, but the original pre-fix derivation was wrong for most cases.
> Manifest v5 and `candidates-v0.7.json` use the parent of the first PR commit. Historical v0.4
> manifests and results are retained for provenance and must not be used as current quality
> evidence.

## v0.13 50-case expansion outcome

Manifest v8 adds 18 manually reviewed Issue/Fix-PR pairs from eight repositories: Uvicorn,
Celery, Flask, Black, pip, mypy, Poetry, and Scrapy. The current suite contains 50 cases across 21
repositories: 17 main, 11 calibration, and 22 generalization. It has 62 reviewed production-file
targets, including 11 multi-file cases, and 39 reviewed symbol targets across 33 cases.

The committed `candidates-v0.13.json` catalog records all 18 additions as `accepted`, with no
blocking audit failure. `expansion-v0.13-selection.json` contains one unique manual decision per
candidate and case. Each selected PR is merged in the same repository, is recorded by GitHub as a
closing PR for the Issue, and has a reviewed pre-fix SHA equal to the parent of the first ordered
PR commit. All reviewed production files exist at that commit.

| Repository | Cases | Selected Issue / fix PR pairs |
|---|---:|---|
| Uvicorn | 2 | #3035 / #3036, #3040 / #3041 |
| Celery | 3 | #10312 / #10313, #10322 / #10324, #10340 / #10363 |
| Flask | 2 | #5621 / #5632, #5628 / #5630 |
| Black | 2 | #5225 / #5238, #5243 / #5244 |
| pip | 2 | #14079 / #14084, #14136 / #14143 |
| mypy | 2 | #21736 / #21737, #21777 / #21788 |
| Poetry | 3 | #10760 / #10769, #10770 / #10784, #10830 / #10917 |
| Scrapy | 2 | #7759 / #7763, #7796 / #7818 |

Three complete deterministic v0.25 evaluations produced identical candidates, symbols, and metrics
after excluding timestamps and elapsed fields. File Recall@1 is `0.4067`, Recall@5 `0.6900`,
Recall@10 `0.7800`, Recall@20 `1.0000`, and MRR `0.6038`. The 33 labeled cases
reach Symbol Recall@1 `0.3485`, Recall@5 `0.5455`, Recall@10 `0.5758`, Recall@20 `0.7121`, and
MRR `0.4873`. v0.17's scope-safe function-local import edges recovered Rich's `highlighter.py` at
rank 18. v0.18's bounded shared qualified-call evidence recovered
`scrapy/utils/decorators.py::_warn_spider_arg` at rank 18. v0.19's bounded reverse-import evidence
recovered `celery/worker/pidbox.py` at rank 19 without regressing an earlier Top-20 match.
v0.20's same-subsystem package re-export evidence recovered Poetry's
`src/poetry/utils/env/python/manager.py` at rank 17. v0.21's path-scoped ordered traceback evidence
recovered `Executor._create_directory_url_reference`; all 50 candidate-file lists remained
unchanged from v0.20. v0.22's exact source-line evidence recovered
`WebSocketsSansIOProtocol.handle_connect` and `EnvManager.get`; all file lists remained unchanged
from v0.21, 47 symbol lists were unchanged, and no labeled symbol regressed.
v0.23's bounded, path-constrained fenced source excerpt evidence recovered Click's `prompt`; all
50 file lists and the other 49 symbol lists remained unchanged from v0.22.
v0.24's uniquely resolved, title-and-code-grounded constructor evidence recovered Pydantic's
`TypeAdapter.__init__`; all file lists and the other 49 symbol lists remained unchanged from v0.23.
v0.25's bounded adjacent owner-to-method title phrase recovered pip's
`ConfigOptionParser.error`; all file lists and the other 49 symbol lists remained unchanged from
v0.24. Test-source symbols, generic method terms, ambiguous matches, and blame seeds do not consume
this signal.

Two authorized OpenCode `deepseek-v4-flash-free` rank-only runs completed all 50 cases and kept
every case in the denominator. Both returned 50/50 valid ranks with no fallback. File Recall@1 was
`0.6567` and `0.6767`, Recall@5/10/20 was `0.8200/0.8600/0.9300` in both runs, and MRR was
`0.8226` and `0.8326`. Against deterministic retrieval, each run improved 18 case-level reciprocal
ranks, left 28 unchanged, and worsened four. The same 20 candidate files remained available in
every case, while model ordering differed in 14/50 cases across repeats. This supports a bounded
ordering-gain and two-run protocol-reliability claim, not deterministic model output or production
reliability.

The expansion is intentionally labeled an initial 50-case suite rather than a representative
sample. Sixteen of the 18 additions were created in 2026 and two in 2024, and the 11 multi-file
cases remain below the earlier diversity target. The next selection pass should prioritize older
Issue/Fix-PR pairs, multi-file production changes, backend/runtime dispatch, and current Top-20
misses instead of adding more recent single-file cases.

## v0.12 qualified-symbol outcome

The previous qualified-symbol manifest is version 7. It keeps 32 independently reviewed cases across 13
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
  --output benchmarks/results/deterministic-v0.25-qualified-title-50-cases-run1.json
```

Raw discovery catalogs are ignored because they are large, mutable review queues. The accepted
catalog, manual selection, frozen manifest, and evaluated results are committed. Current audits
must use the corrected ordered-commit checks. The current accepted catalog and decisions are
`candidates-v0.13.json` and `expansion-v0.13-selection.json`; the v0.11 and v0.7 catalogs retain
the prior 32-case and corrected 20-case provenance.

## Candidate projects after v0.13

The 50-case threshold is now met across 21 repositories. The next expansion should improve
temporal and structural balance rather than satisfy a larger quota. Discovery also showed why
acceptance remains manual: earlier HTTPX and Django scans produced no reviewable candidates in the
configured window, while the SQLAlchemy review queue was dominated by documentation changes.
Those repositories remain useful future targets only when a concrete pair adds a missing failure
mode.

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

## Pending 200-case review queue

On 2026-08-16, a broader discovery pass inspected 40 additional public repositories. The initial
four catalogs contained 684 audit records, of which 193 passed every blocking check. Targeted
deeper scans were then limited to 12 repositories that had already produced reviewable cases;
zero-yield Django and HTTPX scans were not repeated. Because the discovery passes overlap, record
counts are not case counts.

After de-duplicating by both `(repository, Issue)` and `(repository, fix PR)`, and excluding pairs
already present in manifest v8, the pool contains 199 unique candidates across 38 repositories.
`benchmarks/expansion-v200-review-queue.json` prioritizes:

- 150 primary candidates and 49 reserves;
- no more than five primary candidates per repository;
- at least one primary candidate from every repository represented in the unique pool;
- 54 new multi-file primary candidates, which would raise the suite from 11/50 to 65/200
  multi-file cases (32.5%) if all primary candidates pass review;
- 93 primary Issues created before 2026, including cases from 2013 through 2025;
- 100 primary candidates that pass all four advisory checks for body, bug wording, diagnostics,
  and an explicit closing reference.

This is a review queue, not benchmark ground truth. Every entry is serialized with
`status=needs_review`; the queue schema rejects `accepted`. Before any candidate enters a frozen
manifest, a reviewer must still confirm the Issue/fix relationship, inspect the production diff,
verify every expected file at the recorded pre-fix commit, decide the tier, and add reviewed symbol
ground truth where the fix hunk supports it. Rejected primaries should be replaced from the reserve
queue without weakening the repository cap or the 30% multi-file target.

The raw discovery catalogs remain ignored because they contain repeated, mutable Issue snapshots.
The compact queue is committed so review order, advisory signals, expected files, and diversity
constraints are auditable without claiming that the 200-case manifest already exists.

After the v0.20 package re-export retrieval change, all 62 reviewed production-file targets appear
in the deterministic Top-20. Future expansion should test whether this saturation survives older,
multi-file, and cross-subsystem cases without weakening the frozen-case acceptance rules.
