# Benchmark Expansion

> **Integrity correction (2026-07-30):** The v0.4 expansion selected valid Issue/Fix-PR pairs and
> reviewed production files, but the original pre-fix derivation was wrong for most cases.
> Manifest v5 and `candidates-v0.7.json` use the parent of the first PR commit. Historical v0.4
> manifests and results are retained for provenance and must not be used as current quality
> evidence.

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

The corrected current manifest has 7 main, 4 calibration, and 9 generalization cases. The
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
  benchmarks/cases-v0.3.json \
  benchmarks/expansion-v0.4-selection.json \
  benchmarks/candidates-v0.4.json \
  --catalog-output benchmarks/candidates/rebuilt-v0.4.json \
  --manifest-output benchmarks/candidates/rebuilt-cases-v0.4.json

rii benchmark benchmarks/cases.json \
  --variant deterministic \
  --output benchmarks/results/deterministic-v0.7-symbol-ground-truth-20-cases.json
```

Raw discovery catalogs are ignored because they are large, mutable review queues. The accepted
catalog, manual selection, frozen manifest, and evaluated results are committed. The v0.4 curation
command above reproduces only the historical expansion flow; current audits must use the corrected
ordered-commit checks and `candidates-v0.7.json`.

## Candidate projects for the next wave

The next target is 30-50 cases without concentrating labels in one framework. These repositories
are candidates only and require the same audit:

| Repository | Intended role | Useful coverage |
|---|---|---|
| `encode/httpx` | Main | client transport, redirects, streaming, proxy behavior |
| `pydantic/pydantic` | Main | validation, schema generation, serialization |
| `sqlalchemy/sqlalchemy` | Generalization | ORM, SQL compilation, dialect-specific call paths |
| `django/django` | Generalization | larger repository and deeper cross-module behavior |
| `pallets/click` | Calibration | compact CLI parsing and option behavior |

Add two or three cases per repository first. Promote a repository to a larger share only after its
initial cases pass checkout validation and add failure modes not already represented. Symbol-level
labels should be added to a smaller high-confidence subset before the suite grows beyond 30 cases.
