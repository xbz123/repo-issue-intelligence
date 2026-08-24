# Real-Project File Localization Benchmark

## Current result: manifest v20 on 200 frozen cases

Manifest v20 contains 200 reviewed Issue/Fix-PR cases across 58 public repositories: 17 main,
11 calibration, and 172 generalization cases. It records 265 production-file targets and 177 reviewed
symbol targets across 143 cases. Every case embeds the complete Issue snapshot, merged same-repository
fix PR, parent of the first ordered PR commit, and reviewed ground truth. Evaluation indexes only
Git-tracked files at the frozen pre-fix commit.

Three complete deterministic v0.27 runs finished 200/200 cases. After excluding timestamps,
elapsed fields, and cache provenance, their candidates, symbols, per-case metrics, tier metrics,
and aggregates were identical. The retained 160 cases were also unchanged from manifest v19.

| Scope | Cases | Recall@1 | Recall@5 | Recall@10 | Recall@20 | MRR | Run 1 / warm analysis per case |
|---|---:|---:|---:|---:|---:|---:|---:|
| Overall | 200/200 | 0.3510 | 0.6567 | 0.7555 | 0.8665 | 0.5396 | 6,619 / 4,435 ms |
| Main | 17/17 | 0.4706 | 0.6176 | 0.7941 | 1.0000 | 0.6160 | 5,744 / 5,702 ms |
| Calibration | 11/11 | 0.3182 | 0.6818 | 0.7273 | 1.0000 | 0.5183 | 1,508 / 1,168 ms |
| Generalization | 172/172 | 0.3413 | 0.6590 | 0.7535 | 0.8448 | 0.5334 | 7,033 / 4,519 ms |

Run 1 recorded 162 repository-map cache hits and 38 misses; runs 2 and 3 recorded 200 hits. Warm
reuse reduced the overall in-process mean by 32.99%, from 6,619 to 4,435 ms per case. Timing is
observational and not part of the reproducibility gate. Measurements start after repository preparation and do not
include clone, fetch, checkout, or Issue retrieval time.

Across the 143 labeled cases, Symbol Recall@1/5/10/20 is
`0.2378/0.3840/0.4155/0.4645`, with MRR `0.3349`. Forty-six production targets are absent from the
deterministic Top-20. Six are retained-suite misses: `paramiko/common.py`, `boto3/compat.py`,
`lib/matplotlib/cbook/__init__.py`, `src/tox/tox_env/python/runner.py`,
`pylint/config/callback_actions.py`, and `tornado/locks.py`. The second batch adds 13 misses across
NumPy's C DLPack implementation, Ruff's shared Rust helper, Ansible's oneline callback, three
Virtualenv seed paths, three pandas Arrow string paths, and all four uv Rust paths. The third batch
adds both Prefect null-form TSX files and Jinja's `idtracking.py`; the fourth adds two more Prefect
TSX files and tox's cross-section resolver. The fifth adds Pylint's class checker, Pandas `isin`,
and Paramiko's dependency declaration. The sixth adds Setuptools
`setuptools/config/_apply_pyprojecttoml.py` and Flake8 `src/flake8/options/config.py`. They remain in
the denominator; the seventh batch adds no miss. The eighth adds Prefect's AnyOf utility, both uv
stale-interpreter-cache files, and three uv check plumbing files. The ninth adds Ruff's printer,
diagnostic export, and stylesheet helper files. The tenth adds both Airflow provider files. The
final direct batch adds uv's PEP 508 formatter, Airflow's Snowflake hook and user-settings UI,
SciPy's Remez C++ guard, and SciPy `signm`. These failures define concrete
retrieval work for the next stage.

## Manifest-v20 DeepSeek pool-40 result

The v0.28 hybrid protocol preserves the deterministic Top-20 as its exact fallback and supplies a
separate Top-40 pool to OpenCode `deepseek-v4-flash`. Each of the 40 snippets receives at most 2,500
characters and 200 source lines under the unchanged 100,000-character total budget. The model may
promote at most three files; unselected base files retain deterministic order.

| Run | Valid ranks | File R@1 | R@5 | R@10 | R@20 | Pool R | MRR | Mean LLM latency |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 200/200 | 0.5772 | 0.7830 | 0.8272 | 0.8790 | 0.8973 | 0.7497 | 5.47 s |
| 2 | 200/200 | 0.5672 | 0.7847 | 0.8288 | 0.8790 | 0.8973 | 0.7467 | 3.18 s |
| 3 | 200/200 | 0.5722 | 0.7855 | 0.8297 | 0.8790 | 0.8973 | 0.7472 | 3.73 s |
| Mean | 600/600 | 0.5722 | 0.7844 | 0.8286 | 0.8790 | 0.8973 | 0.7479 | 4.13 s |

All requests succeeded on their first attempt; fallback, unknown-ID, invalid-rank, HTTP, and
transport failure counts are zero. The three runs used 10,348,059 input tokens and 5,908 output
tokens. File R@20 is stable across runs, while Recall@1 has population standard deviation `0.0041`
and MRR `0.0013`.

The Top-40 pool contains 229/265 production targets and the final Top-20 contains 222/265. Every
run recovers `tornado/locks.py`, `pylint/checkers/classes/class_checker.py`, and
`scipy/linalg/_matfuncs.py` from outside the deterministic Top-20 without losing existing
ground truth. The remaining seven pool-visible misses were not selected, and 36 targets remain
outside the bounded pool. Mean Symbol Recall@1/5/10/20 is
`0.4167/0.4493/0.4587/0.4645`, with symbol MRR `0.4856`.

Only 135/200 complete candidate orders are identical across all repeats; pairwise order changes
affect 41, 48, and 45 cases, and eight cases vary in expected-file reciprocal rank. The result
supports a reliable plain-rank contract and a repeatable three-file pool expansion on this suite,
not deterministic generation or root-cause accuracy.

v0.27 also makes Git co-change evidence reproducible. It scans the 100 most recent commits reachable
from the frozen HEAD and consumes at most 50 commits touching a lexical seed. The previous
path-filtered command could cross a five-second timeout on a cold partial clone but succeed after
the cache warmed, which changed one Virtualenv tail list across repeats. The fixed commit window
removes that wall-clock branch. Compared with v0.26, 37 of the retained 60 candidate lists change
and two retained cases lose a Top-20 file; this regression is reported rather than hidden.

v0.25 recognizes an adjacent owner-to-method phrase in an Issue title only when the owner has at
least two semantic terms, the method contributes a non-owner and non-generic term, the strongest
match is unique within the file, and the symbol is not in a test source path. The relation affects only
within-file symbol selection and is disabled for blame seed selection. This recovered
`src/pip/_internal/cli/parser.py::ConfigOptionParser.error` for
`pip-rich-option-error-usage`. All 50 file lists and the other 49 symbol lists were unchanged from
v0.24. Across the 33 labeled cases, Symbol Recall@1/5/10/20 is
`0.3485/0.5455/0.5758/0.7121`, with MRR `0.4873`.

v0.24 records ordered syntactic calls from inline and fenced Issue code, but infers a constructor
only when the called class is named in the title, the qualified `__init__` identity resolves to one
file, and the title either uses construction wording or has a non-owner term supported by the
constructor docstring. Explicit title methods and complete qualified method references take
priority. Label-only names, unrelated setup calls, duplicate owners, and ambiguous constructors are
skipped. This recovered `pydantic/type_adapter.py::TypeAdapter.__init__` for
`pydantic-typeadapter-union-typing`. All 50 file lists and the other 49 symbol lists were unchanged
from v0.23. Across the 33 labeled cases, Symbol Recall@1/5/10/20 is
`0.3485/0.5455/0.5758/0.6818`, with MRR `0.4858`.

v0.23 uses bounded fenced source excerpts as within-file symbol evidence. Only the first four
eligible blocks are retained; each must contain 3-12 non-empty lines and 60-2,000 characters.
Matching is restricted to Issue path or basename candidates, tolerates Python trailing-comment
differences, and is accepted only when one excerpt occurs once in exactly one eligible file and
resolves to one enclosing symbol. Ambiguous same-basename files and repeated excerpts are skipped.
This recovered `src/click/termui.py::prompt` for `click-hidden-prompt-custom-error`. All 50 file
lists and the other 49 symbol lists were unchanged from v0.22. Across the 33 labeled cases, Symbol
Recall@1/5/10/20 is `0.3182/0.5152/0.5455/0.6515`, with MRR `0.4555`.

v0.22 resolves exact relative `path.py#L...` references and immutable GitHub
`blob/<40-character-commit>/path.py#L...` links to their enclosing qualified symbols. Relative
references use the indexed checkout. Immutable links load the referenced path from the local Git
object and resolve the identity in that historical source before requiring the same identity in the
frozen benchmark checkout. Mutable branch links, unavailable commits, ambiguous paths, and stale
identities are skipped. This recovered `WebSocketsSansIOProtocol.handle_connect` for
`uvicorn-nonascii-websocket-headers` and `EnvManager.get` for `poetry-empty-conda-prefix`. Across
the 33 labeled cases, Symbol Recall@1/5/10/20 is `0.2879/0.4848/0.5152/0.6212`, with MRR `0.4252`.
All 50 candidate-file lists were unchanged from v0.21; 47 symbol lists were unchanged, and no
labeled symbol regressed.

The retained v0.21 policy parses canonical Python and compact numbered traceback frames while
retaining their order.
A frame can select a function only when the path resolves to one repository file and the function
name resolves to one symbol in that file; the deepest such frame wins. Installed paths may omit a
confirmed `src`/`lib` layout prefix only when the stripped suffix is unique. This recovered
`Executor._create_directory_url_reference` for `poetry-relative-directory-url`.

The retained v0.20 baseline records safe module-level import bindings and follows one unique
package re-export hop from an Issue-referenced source path. The facade must be `__init__.py`; seed,
facade, and target must be production files; seed and target must share a non-generic package
subsystem; and both the binding and target definition must resolve uniquely. The seed must actually
read the imported name. Unused,
conditional, shadowed, ambiguous, auxiliary, and cross-subsystem routes are skipped. The relation
can expand and protect a tail candidate but cannot
rerank an existing shortlist. It recovered `src/poetry/utils/env/python/manager.py` at rank 17 for
`poetry-empty-conda-prefix`, raising File Recall@20 and candidate-pool recall from `0.9900` to
`1.0000`. The other 49 candidate and symbol lists were unchanged, and no earlier cutoff regressed.

The retained v0.19 baseline adds a bounded reverse-import expansion for production files inside a
title-matching subsystem. The imported source module must contain at least two functions sharing the same
non-path title term, and it must have one to three in-scope importers. Package roots, tests, docs,
examples, and scripts cannot supply scope. This recovered `celery/worker/pidbox.py` at rank 19 for
`celery-pidbox-reset-fd-leak`, raising File Recall@20 from `0.9700` to `0.9900`; no earlier
Top-20 ground-truth match regressed. The relation can consume a bounded tail expansion slot but
cannot rerank files already in the shortlist. Symbol metrics remained unchanged.

The retained v0.18 baseline indexes qualified calls rooted at unshadowed module imports and
canonicalizes import aliases. It permits tail-only peer expansion when the Issue explicitly
references the full call and a seed caller, the call occurs in two or three production files, and
caller identity is unambiguous or has one non-overload implementation. The relation contributes no
reranking bonus and cannot receive strong Top-10 promotion. It recovered
`scrapy/utils/decorators.py::_warn_spider_arg` at rank 18 for `scrapy-pep649-signature`; the other 49
case outputs were unchanged. At v0.20, the 33 labeled cases had Symbol Recall@1/5/10/20
`0.2273/0.4242/0.4545/0.5606`, with MRR `0.3342`.

The retained v0.17 baseline resolves only unshadowed leading function-local `from` imports and stores those calls as a
separate edge type. Direct edges neither rerank nor qualify as expansions by themselves. A bounded
constructor-aware second hop recovered `rich/highlighter.py` at rank 18; strong Top-10 promotion and
propagation through another function-local import remain disabled.

The retained v0.15 file policy reserves at most one extra non-auxiliary shortlist slot for direct
path or symbol support.
It recovered `src/poetry/console/commands/publish.py`, raising candidate-pool recall from `0.9300`
to `0.9500`. No previously retrieved ground-truth file left Top-20; the pip parser boundary moved
from rank 19 to rank 20.

v0.16 improves within-file selection by preserving exact identifier mention counts outside fenced
reproduction blocks. Candidate-unique bare names must contain at least five non-underscore
characters unless an owner or uniquely resolved path supplies scope. Symbol Recall@1/5/10/20 is
`0.2273/0.4242/0.4545/0.5455`, with MRR `0.3342`. Four cases were recovered (`Pydantic`,
`Werkzeug`, `Celery`, and `pip`), no previously matched symbol regressed, and all 50 file candidate
lists remained unchanged.

## Retained manifest-v8 DeepSeek V4 Flash rank-only result

Three authorized OpenCode `deepseek-v4-flash` runs reranked the retained v0.25 deterministic Top-20
candidate pool. The protocol sends no grammar-constrained response format and accepts exactly one
plain `RANK:` line containing at most three known evidence IDs. All cases, including any fallback,
remain in the metric denominator. Reasoning is disabled, the first completion budget is 8,192
tokens, and only a truncated response receives one 20,000-token retry.

| Variant | Cases | Recall@1 | Recall@5 | Recall@10 | Recall@20 | MRR | Valid ranks |
|---|---:|---:|---:|---:|---:|---:|---:|
| Deterministic v0.25 | 50/50 | 0.4067 | 0.6900 | 0.7800 | 1.0000 | 0.6038 | - |
| DeepSeek run 1 | 50/50 | 0.7567 | 0.8600 | 0.9000 | 1.0000 | 0.8949 | 50/50 |
| DeepSeek run 2 | 50/50 | 0.7267 | 0.8600 | 0.9000 | 1.0000 | 0.8883 | 50/50 |
| DeepSeek run 3 | 50/50 | 0.7267 | 0.8600 | 0.9000 | 1.0000 | 0.8849 | 50/50 |
| Three-run mean | 50/50 | 0.7367 | 0.8600 | 0.9000 | 1.0000 | 0.8894 | 50/50 |

Protocol success was 150/150 with no fallback, invalid rank, unknown evidence ID, grammar error, or
HTTP failure. The three runs made 57, 50, and 50 requests, recorded 2,500,818 input tokens and
163,383 output tokens in total, and reported mean LLM latency of 28.53, 1.62, and 1.46 seconds per
case. The first run's 162,491 output tokens account for nearly all completion usage even though all
three requests set `reasoning_effort=none`; provider token accounting and latency are therefore not
stable enough for a deterministic cost claim.

Only 29/50 full candidate orders were identical across all three repeats. Pairwise order changes
were 21 cases between runs 1 and 2, five between runs 2 and 3, and 20 between runs 1 and 3. Fixed
seed 1337 is best effort, not deterministic model output. Candidate membership remains the same
deterministic Top-20 set, so the LLM cannot recover a file absent from that pool.

On the 33 symbol-labeled cases, all three runs produced Symbol Recall@1/5/10/20 of
`0.6667/0.6970/0.6970/0.7121` with MRR `0.7424`. Symbol Recall@20 is unchanged from deterministic
v0.25 because the rank-only protocol reorders existing file/symbol evidence rather than discovering
new symbols.

Machine-readable artifacts:

- `benchmarks/results/deepseek-v4-flash-pool40-manifest-v20-summary.json`
- `benchmarks/results/deterministic-v0.27-final-200-cases-run1.json`
- `benchmarks/results/deterministic-v0.27-final-200-cases-run2.json`
- `benchmarks/results/deterministic-v0.27-final-200-cases-run3.json`
- `benchmarks/results/deterministic-v0.27-batch11-160-cases-run1.json`
- `benchmarks/results/deterministic-v0.27-batch11-160-cases-run2.json`
- `benchmarks/results/deterministic-v0.27-batch11-160-cases-run3.json`
- `benchmarks/results/deterministic-v0.27-batch10-150-cases-run1.json`
- `benchmarks/results/deterministic-v0.27-batch10-150-cases-run2.json`
- `benchmarks/results/deterministic-v0.27-batch10-150-cases-run3.json`
- `benchmarks/results/deterministic-v0.27-batch9-140-cases-run1.json`
- `benchmarks/results/deterministic-v0.27-batch9-140-cases-run2.json`
- `benchmarks/results/deterministic-v0.27-batch9-140-cases-run3.json`
- `benchmarks/results/deterministic-v0.27-batch8-130-cases-run1.json`
- `benchmarks/results/deterministic-v0.27-batch8-130-cases-run2.json`
- `benchmarks/results/deterministic-v0.27-batch8-130-cases-run3.json`
- `benchmarks/results/deterministic-v0.27-batch7-120-cases-run1.json`
- `benchmarks/results/deterministic-v0.27-batch7-120-cases-run2.json`
- `benchmarks/results/deterministic-v0.27-batch7-120-cases-run3.json`
- `benchmarks/results/deterministic-v0.27-batch6-110-cases-run1.json`
- `benchmarks/results/deterministic-v0.27-batch6-110-cases-run2.json`
- `benchmarks/results/deterministic-v0.27-batch6-110-cases-run3.json`
- `benchmarks/results/deterministic-v0.27-batch5-100-cases-run1.json`
- `benchmarks/results/deterministic-v0.27-batch5-100-cases-run2.json`
- `benchmarks/results/deterministic-v0.27-batch5-100-cases-run3.json`
- `benchmarks/results/deterministic-v0.27-batch4-90-cases-run1.json`
- `benchmarks/results/deterministic-v0.27-batch4-90-cases-run2.json`
- `benchmarks/results/deterministic-v0.27-batch4-90-cases-run3.json`
- `benchmarks/results/deterministic-v0.27-batch3-80-cases-run1.json`
- `benchmarks/results/deterministic-v0.27-batch3-80-cases-run2.json`
- `benchmarks/results/deterministic-v0.27-batch3-80-cases-run3.json`
- `benchmarks/results/deterministic-v0.27-batch2-70-cases-run1.json`
- `benchmarks/results/deterministic-v0.27-batch2-70-cases-run2.json`
- `benchmarks/results/deterministic-v0.27-batch2-70-cases-run3.json`
- `benchmarks/results/deterministic-v0.26-batch1-60-cases-run1.json`
- `benchmarks/results/deterministic-v0.26-batch1-60-cases-run2.json`
- `benchmarks/results/deterministic-v0.26-batch1-60-cases-run3.json`
- `benchmarks/results/deterministic-v0.25-qualified-title-50-cases-run1.json`
- `benchmarks/results/deterministic-v0.25-qualified-title-50-cases-run2.json`
- `benchmarks/results/deterministic-v0.25-qualified-title-50-cases-run3.json`
- `benchmarks/results/hybrid-deepseek-v4-flash-go-v0.25-rank20000-manifest-v8-50-cases-run1.json`
- `benchmarks/results/hybrid-deepseek-v4-flash-go-v0.25-rank20000-manifest-v8-50-cases-run2.json`
- `benchmarks/results/hybrid-deepseek-v4-flash-go-v0.25-rank20000-manifest-v8-50-cases-run3.json`
- `benchmarks/results/deterministic-v0.24-title-constructor-50-cases-run1.json`
- `benchmarks/results/deterministic-v0.24-title-constructor-50-cases-run2.json`
- `benchmarks/results/deterministic-v0.24-title-constructor-50-cases-run3.json`
- `benchmarks/results/deterministic-v0.23-source-snippets-50-cases-run1.json`
- `benchmarks/results/deterministic-v0.23-source-snippets-50-cases-run2.json`
- `benchmarks/results/deterministic-v0.23-source-snippets-50-cases-run3.json`
- `benchmarks/results/deterministic-v0.22-source-lines-50-cases-run1.json`
- `benchmarks/results/deterministic-v0.22-source-lines-50-cases-run2.json`
- `benchmarks/results/deterministic-v0.22-source-lines-50-cases-run3.json`
- `benchmarks/results/deterministic-v0.21-traceback-symbols-50-cases-run1.json`
- `benchmarks/results/deterministic-v0.21-traceback-symbols-50-cases-run2.json`
- `benchmarks/results/deterministic-v0.21-traceback-symbols-50-cases-run3.json`
- `benchmarks/results/deterministic-v0.20-package-reexports-50-cases-run1.json`
- `benchmarks/results/deterministic-v0.20-package-reexports-50-cases-run2.json`
- `benchmarks/results/deterministic-v0.20-package-reexports-50-cases-run3.json`
- `benchmarks/results/deterministic-v0.19-bounded-reverse-imports-50-cases-run1.json`
- `benchmarks/results/deterministic-v0.19-bounded-reverse-imports-50-cases-run2.json`
- `benchmarks/results/deterministic-v0.19-bounded-reverse-imports-50-cases-run3.json`
- `benchmarks/results/deterministic-v0.18-shared-qualified-calls-50-cases-run1.json`
- `benchmarks/results/deterministic-v0.18-shared-qualified-calls-50-cases-run2.json`
- `benchmarks/results/deterministic-v0.18-shared-qualified-calls-50-cases-run3.json`
- `benchmarks/results/deterministic-v0.17-function-local-imports-50-cases-run1.json`
- `benchmarks/results/deterministic-v0.17-function-local-imports-50-cases-run2.json`
- `benchmarks/results/deterministic-v0.17-function-local-imports-50-cases-run3.json`
- `benchmarks/results/deterministic-v0.16-symbol-mentions-50-cases.json`
- `benchmarks/results/deterministic-v0.15-protected-paths-50-cases.json`
- `benchmarks/results/deterministic-v0.13-expanded-50-cases.json`
- `benchmarks/results/hybrid-deepseek-v4-flash-rank-none-v0.14-manifest-v8-run1.json`
- `benchmarks/results/hybrid-deepseek-v4-flash-rank-none-v0.14-manifest-v8-run2.json`

## Candidate-generation coverage

Two hundred nineteen of the 265 reviewed production-file targets appear in the deterministic Top-20.
The reported macro-average File Recall@20 is `0.8665`; the 46 missing targets are grouped in the current
result section. This is benchmark coverage, not a population-level recall estimate.

## Previous 32-case deterministic result

Manifest v7 introduced qualified symbol identities and lexically scoped resolved calls.

| Scope | Cases | Recall@1 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|
| Overall | 32/32 | 0.4479 | 0.7812 | 0.8906 | 0.9844 | 0.6428 |
| Main | 12/12 | 0.5417 | 0.7500 | 0.9167 | 1.0000 | 0.6826 |
| Calibration | 7/7 | 0.2857 | 0.7143 | 0.7857 | 0.9286 | 0.4812 |
| Generalization | 13/13 | 0.4487 | 0.8462 | 0.9231 | 1.0000 | 0.6930 |

Its OpenCode DeepSeek run completed 32/32 cases with 28 valid structured responses and four exact
deterministic fallbacks. File Recall@1/5/10/20 was `0.7135/0.8958/0.9375/0.9844`, with MRR
`0.8547`. This older JSON-analysis protocol is retained only as historical evidence; the current
rank-only protocol is smaller and was reliable in both 50-case runs.

## Current limitations

- The 200-case suite is reviewed but not a balanced population sample; 90 cases were created in
  2026 and repository representation remains uneven.
- Forty-five of 200 cases have multi-file production ground truth. The original 30% aspiration was
  infeasible after manual review rejected or narrowed incomplete automatic multi-file records.
- Deterministic File Recall@20 is `0.8665`; 46 reviewed targets remain outside its Top-20. The
  hybrid Top-40 pool misses 36 targets and its final Top-20 misses 43.
- Symbol Recall@20 is `0.4645`, so within-file localization remains a major bottleneck.
- TypeScript, Rust, and C participate in file localization but have no parsed symbol or cross-language graph.
- Manifest v20 has a three-run rank-only evaluation, but no full hypothesis-quality evaluation.
- Full hypothesis generation reached 140/150 valid final contracts in the current three-run
  real-project evaluation, but run-level success still ranged from 86% to 100%.

## Next experiment

1. Add receiver/type and runtime/backend-dispatch evidence for indirect cross-file calls.
2. Add semantic test-to-source mapping and import-alias resolution.
3. Investigate the 36 targets outside the Top-40 pool, prioritizing non-Python and multi-file paths.
4. Treat `benchmarks/expansion-v200-review-queue-v19.json` as archived provenance; future expansion
   should start from a new discovery pool and target rather than reopening the completed suite.
5. Diagnose the seven pool-visible targets that DeepSeek did not promote and the eight cases whose
   expected-file reciprocal rank varied across repeats.
