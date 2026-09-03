# Real-Project File Localization Benchmark

## Current result: manifest v20 on 200 frozen cases

Manifest v20 contains 200 reviewed Issue/Fix-PR cases across 58 public repositories: 17 main,
11 calibration, and 172 generalization cases. It records 267 production-file targets and 177 reviewed
symbol targets across 143 cases. Every case embeds the complete Issue snapshot, merged same-repository
fix PR, parent of the first ordered PR commit, and reviewed ground truth. Evaluation indexes only
Git-tracked files at the frozen pre-fix commit.

Three complete v0.31 index-v19 deterministic runs finished 200/200 cases. After excluding
timestamps, elapsed fields, and cache provenance, their candidate-file orders, per-case metrics,
tier metrics, and aggregates were identical. Candidate-symbol lists were identical for 199/200
cases; `uv-check-no-install-project` selected `no_install_project` only in run 3 instead of the
earlier same-file test function, while five immediate single-case repeats reproduced runs 1 and 2.
Conservative Rust declarations now participate in
localization without inferred Rust call edges; XID-compatible identifiers are normalized to NFC,
macro token trees and outer attributes are skipped across line breaks, declarations may span lines,
multiple declarations per line and Rust 2024 safe foreign functions are accepted, and TypeScript,
TSX, C, and C++ remain file-only.

One index-v20 review-validation run also completed 200/200 with zero failures. Candidate-file and
candidate-symbol lists and every per-case metric exactly match index-v19 run 1. Four Ruff maps add
the real `Truthiness` enum that `if !...` had previously caused the macro scanner to hide; the other
map fields are unchanged. A direct audit of those four frozen cases found identical Top-40
candidate reports and evidence inputs. Under the anomaly-triggered repeat policy, no additional
run was required. Its mean analysis time was 8,794 ms per case with seven cache hits and 193 misses.
One index-v21 run then completed 200/200 with zero failures and matched index v20 across all 193
repository maps, candidate files, candidate symbols, and metrics. It adds conservative handling for
keyword-named macros from older Rust editions and a mixed definition/invocation linearity guard;
the frozen suite did not exercise those legacy forms, so no additional run was required. Mean
analysis time was 8,707 ms per case with seven cache hits and 193 misses.
One index-v22 review-validation run completed 200/200 with zero failures after replacing substring
test-file detection with exact test path and filename conventions. A full replay using the old maps
checked all 127 changed-map cases. Thirty-eight complete candidate-file orders changed, but only
`ruff-os-exit-private-member` changed any metric: its first expected file moved from rank 2 to rank
1. Aggregate Recall@1 rose to `0.3577` and MRR to `0.5468`; Recall@5/10/20 and all symbol metrics
were unchanged, with zero per-case metric regressions or Top-20 ground-truth losses. Mean analysis
time was 10,381 ms per case with seven cache hits and 193 misses, so no anomaly-triggered repeat was
required.
One index-v23 review-validation run completed 200/200 with zero failures after adding
separator-based test-directory conventions and serializing the process-global warning-filter
context used by Python 3.11/3.12 parsing. All 200 candidate-file orders, all 200 candidate-symbol
lists, and every per-case and aggregate metric exactly matched index v22. Mean analysis time was
8,850 ms per case with seven cache hits and 193 misses, so no anomaly-triggered repeat was required.
The final index-v25 run completed 200/200 with zero failures after adding unique local-module alias
calls, separately recorded same-class receiver calls, unique exact-stem test-to-source expansion,
and directly supported alternate symbols. An intermediate index-v24 run exposed a Jinja symbol
regression and was rejected; a four-case targeted check verified the receiver-call isolation fix
before the final full run. Two production targets enter Top-20, none leave, five reviewed symbol
targets become retrievable, and no earlier symbol target is lost.
The final dollar-identifier scope guard changes only the frozen signal set for
`poetry-empty-conda-prefix`, where it removes shell `$USER`; a cache-hit targeted rerun exactly
matches the index-v21 full-run candidate files, candidate symbols, and metrics.

The v0.35 retrieval-only follow-up keeps the deterministic Top-20 path unchanged and adds three
bounded Top-40 signal families: normalized long CLI options with compound source-identifier
matching, low-frequency Rust filename stems, and root Python `setup.py` dependency metadata under
release or deprecation context. One 200-case deterministic replay completed 200/200 with all
aggregate file and symbol metrics exactly matching index v25. The accepted full pool audit retrieves
246/267 production
targets and leaves 21 misses, recovering nine prior misses without adding a new miss. An initial
broader audit was rejected after high-cardinality dotted identifiers and cross-language filename
stems displaced three targets; those paths were removed or restricted before the accepted rerun.

The table below preserves the audited three-run index-v19 baseline. The one-run index-v20 through
index-v25 validation results are reported above and are not averaged into these historical tier
values.

| Scope | Cases | Recall@1 | Recall@5 | Recall@10 | Recall@20 | MRR | Three-run mean analysis per case |
|---|---:|---:|---:|---:|---:|---:|---:|
| Overall | 200/200 | 0.3560 | 0.6567 | 0.7493 | 0.8690 | 0.5443 | 6,026 ms |
| Main | 17/17 | 0.4706 | 0.6176 | 0.7941 | 1.0000 | 0.6160 | 7,784 ms |
| Calibration | 11/11 | 0.3182 | 0.6818 | 0.7273 | 1.0000 | 0.5183 | 1,574 ms |
| Generalization | 172/172 | 0.3471 | 0.6590 | 0.7462 | 0.8477 | 0.5389 | 6,137 ms |

Run 1 recorded seven repository-map cache hits and 193 misses after the final index-version change;
runs 2 and 3 recorded 200 hits. Overall mean analysis time was 6,026 ms per case with population
standard deviation 1,892 ms. Timing is observational and not part of the reproducibility gate. Measurements
start after repository preparation and do not include clone, fetch, checkout, or Issue retrieval
time.

All 193 index-v18 and index-v19 maps were identical. Relative to index v18,
`prefect-anyof-copy-new-run` changes one Top-20 tail file under exact ECMAScript spelling, and
`pyo3-reference-pool-dirty-fastpath` selects the explicitly referenced `ReferencePool` type instead
of `get_pool`; aggregate metrics do not change.

Across the 143 labeled cases, current Symbol Recall@1/5/10/20 is
`0.2448/0.4260/0.4505/0.4994`, with MRR `0.3537`; all 177 labels are Python-only. Forty-three
production targets are absent from deterministic Top-20. The five retained-suite misses are
`paramiko/common.py`, `boto3/compat.py`, `src/tox/tox_env/python/runner.py`,
`pylint/config/callback_actions.py`, and `tornado/locks.py`; index v25 recovers the earlier
Matplotlib `cbook/__init__.py` miss. The complete current Top-40 miss taxonomy is recorded in the
index-v25 audit and remains the concrete retrieval backlog.

Those historical `symbol_recall_at_*` values use the candidate file rank for both the primary
symbol and up to two directly supported alternates. They are retained as legacy file-cutoff metrics
for artifact compatibility and are not a flattened global symbol ranking. Starting with the v0.36
metric protocol, each emitted symbol records a `within_file_rank`; result aggregates separately
report file-conditioned symbol Recall@1/3, within-file symbol MRR, and file-visible reviewed symbols
that were not proposed. The expected file must appear in the final candidate list to enter the
file-conditioned denominator. This is an evaluation-only change and does not alter retrieval.

One metric-only replay completed 200/200 cases with 200 repository-map cache hits and preserved all
legacy file and symbol metrics. The final candidate list contained the expected file for 160 of the
177 reviewed symbol targets across 133 cases. Case-macro within-file Recall@1/3 was
`0.5050/0.5426`, and within-file MRR was `0.5739`. Sixty-seven cases contained at least one
file-visible symbol miss, comprising 79 targets; these misses are now explicit rather than hidden
inside a file-rank metric. The compact result is
[`deterministic-symbol-metrics-index-v25-manifest-v20-summary.json`](../benchmarks/results/deterministic-symbol-metrics-index-v25-manifest-v20-summary.json).

The correction adds `src/tox/tox.schema.json` to both tox schema cases and indexes only the
controlled `*.schema.json` form as file-only `JSON Schema`. Both published artifacts are retrieved
inside Top-20. Four candidate lists change relative to v0.27: the two tox cases and two Black cases
whose repository contains `black.schema.json`; no Top-20 ground-truth match regresses.

## Manifest-v20 Codex CLI Luna pool-40 result

One authorized v0.32 run evaluated all 200 frozen public cases through isolated non-interactive
Codex CLI `gpt-5.6-luna` at medium reasoning effort. All 200 requests returned a valid known-ID
rank on the first attempt: protocol success was `1.0000`, with no retry, fallback, execution
failure, malformed structure, or unknown ID.

| Variant | Valid ranks | File R@1 | R@5 | R@10 | R@20 | Pool R | MRR | Mean LLM latency |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Luna run 1 | 200/200 | 0.6147 | 0.8192 | 0.8501 | 0.9007 | 0.9132 | 0.7860 | 6.64 s |
| DeepSeek three-run mean | 595/600 | 0.5747 | 0.8008 | 0.8433 | 0.8965 | 0.9132 | 0.7606 | 6.69 s |
| Deterministic (index v21) | - | 0.3560 | 0.6567 | 0.7493 | 0.8690 | 0.9132 | 0.5443 | - |

Against deterministic retrieval, Luna improves Recall@1/5/10/20 by
`0.2587/0.1625/0.1008/0.0317` and MRR by `0.2417`. Against the historical DeepSeek three-run
mean, it improves those metrics by `0.0400/0.0184/0.0068/0.0042` and MRR by `0.0254`. These are
one-run comparisons, not evidence that Luna has lower variance or a stable model-ordering
advantage.

Luna improves expected-file reciprocal rank in 82 cases, worsens it in 26, and leaves 92
unchanged relative to deterministic. It recovers seven ground-truth files from outside
deterministic Top-20: NumPy `dlpack.c`, pandas `_arrow_string_mixins.py`, Jinja `idtracking.py`,
Pylint `class_checker.py`, Setuptools `_apply_pyprojecttoml.py`, uv `uv-pep508/src/lib.rs`, and
SciPy `_matfuncs.py`. No deterministic Top-20 ground-truth file is displaced; final Top-20 covers
229 of 267 production targets, versus 222 deterministically.

Across 143 symbol-labeled cases, Recall@1/5/10/20 is
`0.4435/0.4528/0.4598/0.4668`, with symbol MRR `0.5049`. Luna uses 4,948,048 input and 30,945
output tokens, with median/p95/max LLM latency `6.42/9.27/13.27` seconds. Codex CLI accounting
includes its agent context and reasoning, so token totals are not directly comparable with the
historical chat-completions protocol. The run had 192 repository-map cache misses; aggregate
analysis latency is not a warm-cache comparison.

Per the one-run-first policy, no repeat was launched because every case succeeded. Stability
therefore remains unmeasured. The compact machine-readable artifact is
`benchmarks/results/gpt-5.6-luna-pool40-manifest-v20-run1-summary.json`; the 1.19 MB raw result
remains outside Git.

## Manifest-v20 DeepSeek Rust-symbol pool-40 result

The v0.31 hybrid protocol preserves the deterministic Top-20 as its exact fallback and supplies a
separate Top-40 pool to OpenCode `deepseek-v4-flash`. The default Top-20 retains one reservation
slot for directly supported paths; only the expanded pool uses three. Each of the 40 snippets
receives at most 2,500 characters and 200 source lines under the unchanged 100,000-character total
budget. The model may promote at most three files; unselected base files retain deterministic order.

| Run | Valid ranks | File R@1 | R@5 | R@10 | R@20 | Pool R | MRR | Mean LLM latency |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 196/200 | 0.5672 | 0.7963 | 0.8455 | 0.8982 | 0.9132 | 0.7546 | 5.61 s |
| 2 | 199/200 | 0.5747 | 0.8047 | 0.8388 | 0.8932 | 0.9132 | 0.7606 | 7.63 s |
| 3 | 200/200 | 0.5822 | 0.8013 | 0.8455 | 0.8982 | 0.9132 | 0.7667 | 6.84 s |
| Mean | 595/600 | 0.5747 | 0.8008 | 0.8433 | 0.8965 | 0.9132 | 0.7606 | 6.69 s |

All 600 case-runs completed. The provider returned 595 valid known-ID ranks: 580 on the first
attempt and 15 after one bounded retry. Five cases used deterministic fallback after two OpenCode
HTTP 500 responses; invalid structure and unknown evidence IDs remained zero across 620 provider
attempts. The runs used 10,070,864 input tokens and 5,846 output tokens. Recall@1 has population
standard deviation `0.0061`, R@20 `0.0024`, and MRR `0.0049`. Across only the 595 valid model
responses, Recall@1/5/10/20 is `0.5745/0.7999/0.8428/0.8956`, with MRR `0.7611`; the overall row
above retains fallbacks in the denominator.

These DeepSeek runs were generated under repository-map index v14. Review expansion through index
v18 preserved every Top-40 report and rerank input. A direct v18/v19 audit found changed Top-40
evidence only for the Prefect and PyO3 cases above; the v19/v20 audit found no further Top-40 input
changes despite the four corrected Ruff maps, and all 193 v20/v21 maps are identical. The DeepSeek
metrics are therefore retained only as historical provenance and were not rerun. The current
hybrid runtime uses Codex CLI `gpt-5.6-luna`; its first complete result is reported above.

The Top-40 pool contains 236/267 production targets and the final Top-20 contains 229, 228, and 229
across the three runs. The declaration index moves uv `crates/uv/src/commands/project/mod.rs` into
deterministic Top-20 and `crates/uv-pep508/src/lib.rs` into Top-40; DeepSeek selects both in every
run. Every run also recovers NumPy `dlpack.c`, pandas `_arrow_string_mixins.py`, `tornado/locks.py`,
Pylint's `class_checker.py`, and SciPy `_matfuncs.py`; runs 1 and 3 additionally recover Setuptools
`_apply_pyprojecttoml.py`. No deterministic Top-20 ground truth is lost. Seven, eight, and seven
pool-visible targets are not selected, while 31 remain outside the bounded pool. Mean Symbol
Recall@1/5/10/20 is `0.4143/0.4528/0.4621/0.4668`, with symbol MRR `0.4870`; Rust symbol quality
is not represented because the reviewed symbol labels are Python-only.

Only 125/200 complete candidate orders are identical across all repeats; pairwise order changes
affect 53, 52, and 56 cases, and 22 cases vary in expected-file reciprocal rank. Compared with
v0.30, mean Recall@1/5/10/20 improves from `0.5622/0.7952/0.8377/0.8890` to
`0.5747/0.8008/0.8433/0.8965`, and MRR from `0.7516` to `0.7606`. The result supports a bounded
retrieval improvement; complete model ordering remains best effort rather than deterministic.

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

- `benchmarks/results/deterministic-json-schema-manifest-v20-summary.json`
- `benchmarks/results/deepseek-v4-flash-pool40-manifest-v20-summary.json`
- `benchmarks/results/pool40-miss-taxonomy-manifest-v20.json`
- `benchmarks/results/candidate-pool-miss-audit-index-v23.json`
- `benchmarks/results/candidate-pool-miss-audit-index-v25.json`
- `benchmarks/results/hypothesis-quality-v1-deepseek-run1-summary.json`
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

Two hundred twenty-four of the 267 reviewed production-file targets appear in the deterministic Top-20.
The reported macro-average File Recall@20 is `0.8732`; the 43 missing targets are grouped in the current
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

## Index-v25 conservative relations and multi-symbol result

The final v0.34/index-v25 deterministic run completed 200/200 with no failures. File
Recall@1/5/10/20 is `0.3577/0.6617/0.7493/0.8732`, with MRR `0.5471`; Symbol
Recall@1/5/10/20 is `0.2448/0.4260/0.4505/0.4994`, with MRR `0.3537`. Relative to index v23,
two reviewed production targets enter Top-20, no earlier Top-20 target leaves, five reviewed symbol
targets become retrievable, and no earlier symbol target is lost. The run had 11 repository-map
cache hits and 189 misses. A prior index-v24 run was rejected after exposing a Jinja symbol
regression; separating same-class receiver calls from the legacy direct-caller vote restored the
target in a four-case check before this final full run. No third run was required.

The current v0.35/index-v25 audit reproduces 246/267 reviewed production targets inside the
deterministic Top-40 pool and records all 21 misses: nine Python, six Rust, five TypeScript, and one
C++ target. Two misses rank 41-60 in the diagnostic wide run, five rank 61-100, four rank 101-200,
and ten rank beyond 200. The wide rank is diagnostic and does not alter production selection.

## Index-v25 Luna Fast hybrid result

The first current-index Codex CLI run used `gpt-5.6-luna`, medium reasoning, and the explicit Fast
service tier. It completed 200/200 cases with 200 valid first-attempt ranks, 200 unique request IDs,
zero retries, zero fallback, and zero execution failures. Repository-map cache coverage was 200/200.
File Recall@1/5/10/20 was `0.6047/0.8017/0.8476/0.9023`, with MRR `0.7746`; 231 of 267 reviewed
production targets appeared in final Top-20. Symbol Recall@1/5/10/20 was
`0.4645/0.4913/0.4983/0.5017`, with MRR `0.5346`.

Relative to index-v25 deterministic retrieval, File Recall@20 improved by `0.0291` and MRR by
`0.2275`; Symbol Recall@20 improved by `0.0023` and symbol MRR by `0.1809`. The run consumed
4,945,880 input tokens and 30,912 output tokens. Mean/median/p95 model latency was
`6,735.076/6,512.896/9,187.713 ms`. Mean model latency was 93.806 ms higher than the older
non-Fast index-v21 Luna run, so this observation does not establish a speed improvement: index,
prompt, CLI version, service load, and run timing differ. No repeat was triggered because every
case succeeded on its first attempt. The compact result is
[`gpt-5.6-luna-fast-pool40-index-v25-manifest-v20-run1-summary.json`](../benchmarks/results/gpt-5.6-luna-fast-pool40-index-v25-manifest-v20-run1-summary.json);
the 1.2 MB raw run remains outside Git.

The first 24-case hypothesis-quality slice completed 24/24 strict analyses and persistence checks.
Reviewed fix evidence was available in 20 cases, while 18 hypotheses cited a reviewed fix file.
Overall file-grounding hit rate was `0.7500`, and conditional hit rate was `0.9000`. A single
reviewer scored 16 hypotheses fully correct, seven plausible but incomplete, and one contradicted
by the reviewed fix mechanism. This is a small, non-independent slice without inter-rater scoring.

## Current limitations

- The 200-case suite is reviewed but not a balanced population sample; 90 cases were created in
  2026 and repository representation remains uneven.
- Forty-seven of 200 cases have multi-file production ground truth. The original 30% aspiration was
  infeasible after manual review rejected or narrowed incomplete automatic multi-file records.
- Deterministic File Recall@20 is `0.8732`; 43 reviewed targets remain outside its Top-20. The
  current Top-40 pool misses 21 targets. The retained historical DeepSeek final Top-20 misses 38,
  39, and 38 targets across its three index-v14 runs and has not been rerun on index v25.
- Symbol Recall@20 is `0.4994`, so within-file localization remains a major bottleneck. The frozen
  manifest has no same-file multi-symbol ground-truth pair even though the schema now supports it.
- Rust has conservative declaration symbols but no parsed call graph; TypeScript, TSX, C, and C++
  remain file-only, and no cross-language graph is inferred.
- Hypothesis quality has only one 24-case evaluation slice selected from manifest v20, with one
  reviewer and no independent retrieval-held-out or inter-rater result.
- Historical v0.25 full hypothesis generation reached 140/150 valid final contracts in its
  three-run real-project evaluation, but run-level success ranged from 86% to 100%.
- The current index-v25 Luna Fast result is one complete run. Its error-triggered repeat gate did
  not fire, so repeat-to-repeat ranking stability remains unmeasured.

## Next experiment

1. Decide whether the two remaining rank-41-60 files (`boto3/compat.py` and
   `paramiko/common.py`) warrant a general patch-design prior; both helpers/constants were introduced
   by the fix and have no corresponding pre-fix static relation.
2. Add bounded runtime/backend dispatch or type-aware receiver evidence beyond the current
   same-class `self`/`cls` rule.
3. Curate held-out same-file multi-symbol ground truth and quantify primary-versus-alternate quality.
4. Investigate parser-backed Rust and TypeScript relations for the 12 non-Python pool misses.
5. Treat `benchmarks/expansion-v200-review-queue-v19.json` as archived provenance; future expansion
   should start from a new discovery pool and target rather than reopening the completed suite.
6. Diagnose the seven or eight pool-visible targets that DeepSeek did not promote and the 22 cases
   whose expected-file reciprocal rank varied across repeats.
