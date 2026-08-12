# Real-Project File Localization Benchmark

## Current result: manifest v8 on 50 frozen cases

Manifest v8 contains 50 reviewed Issue/Fix-PR cases across 21 public Python repositories: 17 main,
11 calibration, and 22 generalization cases. It records 62 production-file targets and 39 reviewed
symbol targets across 33 cases. Every case embeds the complete Issue snapshot, merged same-repository
fix PR, parent of the first ordered PR commit, and reviewed ground truth. Evaluation indexes only
Git-tracked files at the frozen pre-fix commit.

Three complete deterministic v0.16 runs finished 50/50 cases. After excluding timestamps and elapsed
fields, their candidates, symbols, per-case metrics, tier metrics, and aggregates were identical.

| Scope | Cases | Recall@1 | Recall@5 | Recall@10 | Recall@20 | MRR | Analysis per case |
|---|---:|---:|---:|---:|---:|---:|---:|
| Overall | 50/50 | 0.4067 | 0.6900 | 0.7800 | 0.9500 | 0.6027 | 4,780 ms |
| Main | 17/17 | 0.4706 | 0.6176 | 0.7941 | 0.9706 | 0.6159 | 7,409 ms |
| Calibration | 11/11 | 0.3182 | 0.6818 | 0.7273 | 0.9545 | 0.5183 | 1,896 ms |
| Generalization | 22/22 | 0.4015 | 0.7500 | 0.7955 | 0.9318 | 0.6347 | 4,192 ms |

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

## Retained DeepSeek V4 Flash rank-only result

Two authorized OpenCode `deepseek-v4-flash-free` runs reranked the earlier v0.13 deterministic
Top-20 candidate pool. The protocol sends no grammar-constrained response format and accepts
exactly one plain `RANK:` line containing at most three known evidence IDs. All cases, including
any fallback, remain in the metric denominator. These results are not yet paired with v0.16.

| Variant | Cases | Recall@1 | Recall@5 | Recall@10 | Recall@20 | MRR | Valid ranks |
|---|---:|---:|---:|---:|---:|---:|---:|
| Deterministic v0.13 | 50/50 | 0.4067 | 0.6900 | 0.7800 | 0.9300 | 0.6016 | - |
| DeepSeek run 1 | 50/50 | 0.6567 | 0.8200 | 0.8600 | 0.9300 | 0.8226 | 50/50 |
| DeepSeek run 2 | 50/50 | 0.6767 | 0.8200 | 0.8600 | 0.9300 | 0.8326 | 50/50 |
| Two-run mean | 50/50 | 0.6667 | 0.8200 | 0.8600 | 0.9300 | 0.8276 | 50/50 |

Both runs completed every request in one attempt. Fallback, invalid-rank, unknown-ID, and grammar
error counts were zero. Each run recorded 170,521 input tokens; output was 485 and 491 tokens.
Average model latency was `4.13 s` and `4.96 s`. Run-level population standard deviation was
`0.0100` for Recall@1, `0.0050` for MRR, and `0.41 s` for latency.

Against deterministic retrieval, each run improved 18 expected-file reciprocal ranks, left 28
unchanged, and worsened four. Candidate membership remained identical, but ordering changed in
14/50 cases between repeats. Only `pydantic-safe-annotations-metaclass` changed expected-file
rank, from rank 2 to rank 1. Seed 1337 is therefore best effort, not deterministic output.

On the 33 symbol-labeled cases, deterministic Symbol Recall@1/5/10/20 was
`0.1970/0.3636/0.3636/0.4242` with MRR `0.2866`. Both DeepSeek runs produced
`0.3636/0.4242/0.4242/0.4242` with MRR `0.4152`.

Machine-readable artifacts:

- `benchmarks/results/deterministic-v0.16-symbol-mentions-50-cases.json`
- `benchmarks/results/deterministic-v0.15-protected-paths-50-cases.json`
- `benchmarks/results/deterministic-v0.13-expanded-50-cases.json`
- `benchmarks/results/hybrid-deepseek-v4-flash-rank-none-v0.14-manifest-v8-run1.json`
- `benchmarks/results/hybrid-deepseek-v4-flash-rank-none-v0.14-manifest-v8-run2.json`

## Candidate-generation failures

Four cases still miss at least one reviewed production file at Top-20:

- Rich: `highlighter.py`
- Celery: `worker/pidbox.py`
- Poetry: `utils/env/python/manager.py`
- Scrapy: `utils/decorators.py`

Reranking cannot recover files outside the deterministic candidate pool. These misses should be
addressed with retrieval evidence rather than prompt changes.

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

- The suite is not a balanced population sample: 16 of the 18 newest Issues are from 2026.
- Only 11/50 cases have multi-file production ground truth.
- File Recall@20 remains bounded at `0.9500`; four cases contain unretrieved fix files.
- Symbol Recall@20 is `0.5455`, so within-file localization remains a major bottleneck despite the
  12.13-point improvement.
- DeepSeek changed ordering in 14/50 repeated cases despite a fixed best-effort seed.
- Full hypothesis generation has less real-project reliability evidence than rank-only reranking.

## Next experiment

1. Add receiver/type and runtime/backend-dispatch evidence for indirect cross-file calls.
2. Add semantic test-to-source mapping and import-alias resolution.
3. Diagnose the four remaining Top-20 misses individually before expanding prompts.
4. Expand to older and multi-file Issue/Fix-PR cases while preserving manual ground-truth review.
5. Repeat the 50-case rank-only run after retrieval changes and report mean, variation, fallback
   taxonomy, valid-response MRR, and overall MRR.
