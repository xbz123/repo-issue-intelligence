# Benchmark Expansion

> **Integrity correction (2026-07-30):** The v0.4 expansion selected valid Issue/Fix-PR pairs and
> reviewed production files, but the original pre-fix derivation was wrong for most cases.
> Manifest v5 and `candidates-v0.7.json` use the parent of the first PR commit. Historical v0.4
> manifests and results are retained for provenance and must not be used as current quality
> evidence.

> **Ground-truth correction (2026-08-25):** tox Issues #3939 and #3929 and their fix PRs directly
> modify both the schema generator and the published `src/tox/tox.schema.json`. The final selection,
> candidate audit, and 200-case manifest now retain both material files. Repository indexing accepts
> only the controlled `*.schema.json` form as `JSON Schema`; ordinary JSON and lock files remain out
> of scope. The corrected suite has 267 production targets and 47 multi-file cases. Earlier
> manifest-v20 metrics are superseded by the compact corrected summaries.

## v0.25 direct completion of the 200-case suite

At the user's direction, the expansion skips separate 170/180/190 evaluation releases after the
160-case intermediate and directly audits the remaining pool. Manifest v20 accepts 40 additional
Issue/Fix-PR pairs and reaches 200 frozen cases. The final batch records 21 explicit rejections for
multi-Issue PRs, wrong or partial fixes, test/CI-only changes, documentation or formatting changes,
and incomplete automatic production scope. Four archived queue candidates remain unselected rather
than being mislabeled as ground truth.

The selection standard is evidence-first: a public closed Issue, one canonical merged same-repo
fix PR, the parent of its first ordered commit as pre-fix SHA, complete material production-file
scope, unique Issue/PR identities, and only pre-fix symbols that resolve uniquely inside an expected
file. Tests, docs, changelog, generated outputs, workflows, snapshots, and auxiliary benchmarks are
excluded unless they are the shipped behavior. New or ambiguous symbols and non-Python fixes remain
file-only. Repository and file concentration break ties between equally valid candidates, but no
accepted failure is removed to improve metrics.

The completed suite contains 200 cases across 58 repositories: 17 main, 11 calibration, and 172
generalization cases. It records 267 production-file targets, 47 multi-file cases, and 177 reviewed
symbol targets across 143 cases. Case IDs, `(repository, Issue)`, and `(repository, fix PR)` are all
unique. The final 40 accepted cases add no multi-file fix because the last automatic multi-file
record omitted a material C header; the earlier 30% aspiration is therefore reported as infeasible
under the final ground-truth standard rather than filled with incomplete cases.

Three deterministic v0.31 index-v19 runs completed 200/200 cases with zero failures. After removing
timestamps, elapsed fields, and cache provenance, all candidate-file orders, per-case metrics, tier
metrics, and aggregates were identical; candidate-symbol lists were identical for 199/200 cases.
The repository map adds conservative Rust
declaration symbols without inferred Rust call edges, normalizes XID-compatible identifiers to NFC,
skips attributes and macro token trees across line breaks, recognizes declarations that span lines
and multiple declarations per line, and accepts Rust 2024 safe foreign functions; TypeScript, TSX,
C, and C++ remain file-only.
One index-v20 review-validation run then completed 200/200 with zero failures and matched index-v19
run 1 candidate files, candidate symbols, and metrics exactly. Four Ruff maps restore the real
`Truthiness` enum previously hidden by `if !...`; direct Top-40 comparison found identical candidate
reports and evidence, so no anomaly-triggered repeat was required.
One index-v21 run also completed 200/200 with zero failures and matched index v20 across all 193
maps, candidate lists, and metrics. The frozen suite did not exercise its older-edition keyword
macro support, so no anomaly-triggered repeat was required.
One index-v22 review-validation run completed 200/200 with zero failures. Exact test-path
classification changed 38 candidate-file orders, but a complete old-map replay found only one
metric change: `ruff-os-exit-private-member` improved from expected-file rank 2 to rank 1. There
were no per-case metric regressions and no Top-20 target losses, so no additional run was required.
One index-v23 review-validation run completed 200/200 with zero failures after adding exact
separator-based test-directory conventions and serializing the process-global Python warning
filter context. Candidate-file orders, candidate-symbol lists, and all metrics exactly matched
index v22, so the one-run-first policy did not require another run.
File Recall@1/5/10/20 is `0.3577/0.6567/0.7493/0.8690`, with MRR `0.5468`. Across the
143 symbol-labeled cases, Symbol Recall@1/5/10/20 is `0.2378/0.3840/0.4155/0.4645`, with MRR
`0.3349`; all reviewed symbol labels remain Python-only. Top-20 coverage is 222 of 267 production
targets, and all 45 misses remain in the denominator.

The first v0.31 run recorded seven repository-map cache hits and 193 misses after the final
index-version change; runs 2 and 3 recorded 200/200 hits. The three-run mean analysis time was
6,026 ms per case.
Compared with v0.30, Recall@1, Recall@5, Recall@20, and MRR improve while Recall@10 decreases from
`0.7505` to `0.7493`; one target enters Top-20 and none leave it.
`benchmarks/expansion-v200-review-queue-v19.json`
archives the final 160-to-200 review pool; there is no active expansion queue after manifest v20.

The pre-change Top-40 audit found 36 missed targets: 15 Python, 14 Rust, five TypeScript, one C,
and one C++. Reserving three directly supported paths only in the expanded pool recovered NumPy
`dlpack.c` plus two pandas Arrow-string targets, increasing pool coverage from 231/267 to 234/267
without changing the deterministic Top-20. The v0.31 declaration index then recovers uv
`project/mod.rs` in deterministic Top-20 and `uv-pep508/src/lib.rs` in Top-40, increasing pool
coverage to 236/267 without losing a prior Top-20 target. Three DeepSeek runs select both new uv
targets every time; mean Recall@1/5/10/20 becomes `0.5747/0.8008/0.8433/0.8965`, with MRR
`0.7606`. Across 600 case-runs, 595 return valid model ranks and five use deterministic fallback
after OpenCode HTTP 500 responses; no structural or unknown-ID failures occur. Those model runs
were generated under index v14, and review expansion through index v18 preserved every Top-40 input.
Index v19 leaves all 193 maps unchanged but changes Top-40 evidence for one Prefect and one PyO3
case. Index v20 changes four Ruff maps but not their Top-40 reports or evidence. DeepSeek was not
rerun; index v21 is map-identical to v20. The current hybrid runtime uses Codex CLI
`gpt-5.6-luna`; its first 200-case run completed 200/200 valid first-attempt ranks with no fallback,
File Recall@1/5/10/20 `0.6147/0.8192/0.8501/0.9007`, and MRR `0.7860`. This is a single run, so
stability remains unmeasured. The compact taxonomy is
`benchmarks/results/pool40-miss-taxonomy-manifest-v20.json`. The reproducible index-v23 successor,
`benchmarks/results/candidate-pool-miss-audit-index-v23.json`, confirms 236/267 Top-40 coverage and
records the current 31 misses with language, repository, wide-rank, and evidence metadata.

## v0.23 tenth 200-case expansion batch

Manifest v18 accepts ten reviewed Issue/Fix-PR pairs from tox, urllib3, Ansible, Ruff,
scikit-learn, pandas, Matplotlib, Airflow, and Pylint. It adds ten production-file targets and seven
pre-fix Python symbol targets. Airflow is new to the suite with two different provider modules; all
other accepted cases use files not already represented by that repository's existing cases.

This 150-case audit checkpoint reviews more candidates than the ten selected cases and checks
repository concentration, same-PR duplicate reports, and the remaining multi-file pool. Hypothesis
#4858/#4862 is rejected because one PR fixes three Issues, while Setuptools #3085/#4997 is another
partial pkg_resources-removal step rather than the Issue-closing fix. Multidict #292/#1204 remains
undecided because the automatic scope omits the material `hashtable.h` implementation. Canonical
pandas Issue #66517 is selected once and its superseded duplicate reports are not counted again.
All seven selected symbol targets resolve uniquely in the frozen maps.

The suite now contains 150 cases across 57 repositories: 17 main, 11 calibration, and 122
generalization cases. It records 215 production-file targets, 45 multi-file cases, and 142 reviewed
symbol targets across 108 cases.
The distribution audit confirms no duplicate case ID, Issue, or fix PR. Historical Jinja coverage
is the largest repository group at seven cases; this batch adds no Jinja case, adds Airflow with two
distinct provider modules, and otherwise selects at most one case per repository.

Three deterministic v0.27 runs completed 150/150 cases. After removing timestamps, elapsed fields,
and cache provenance, their candidate files, candidate symbols, per-case metrics, tier metrics, and
aggregates were identical. The retained 140 cases were unchanged from manifest v17. File
Recall@1/5/10/20 is `0.3280/0.6423/0.7340/0.8553`, with MRR `0.5368`. Across the 108 labeled cases,
Symbol Recall@1/5/10/20 is `0.2222/0.3974/0.4205/0.4761`, with MRR `0.3409`. Both new Airflow
provider files are outside Top-20; coverage is 174 of 215 and 41 misses remain in the denominator.

Run 1 reused 141 repository maps and rebuilt nine; runs 2 and 3 recorded 150/150 hits. Warm reuse
reduced mean in-process analysis time from 5,811 to 4,258 ms per case, a 26.72% reduction, without
changing any non-timing result.

The regenerated queue contains 50 primary and 25 reserve candidates across 35 repositories. Manual
review leaves one unique multi-file candidate, so the auditable ceiling is now 46/200 (23%). The
historical 30% aspiration remains provenance, not a reason to accept incomplete production scope.

## v0.22 ninth 200-case expansion batch

Manifest v17 accepts ten reviewed Issue/Fix-PR pairs from Jinja, Sphinx, Yarl, Virtualenv,
Requests, Build, h2, PyO3, Ruff, and tox. It adds 14 production-file targets, including three
multi-file fixes, and eight pre-fix symbol targets across six Python cases. The module-level Jinja
slot restoration, newly added Sphinx visitor, and Rust PyO3/Ruff fixes remain intentionally
file-only.

Review rejects fourteen records after comparing the Issue, ordered PR commits, final production
diff, and pre-fix tree. The rejected set includes wrong or partial PR attribution, documentation or
formatting-only changes, an external-dependency fix represented only by a cache-key bump, and cases
whose automatic file list omits the core new implementation, header, configuration, or embed
module. Multidict #292/#1204 and tox #3939/#3941 remain undecided because their complete material
scope still needs a new audit; they are not ground truth. All eight accepted Python symbol targets
resolve uniquely in the frozen repository maps, including the qualified tox class method.

The suite now contains 140 cases across 56 repositories: 17 main, 11 calibration, and 112
generalization cases. It records 205 production-file targets, 45 multi-file cases, and 135 reviewed
symbol targets across 101 cases.

Three deterministic v0.27 runs completed 140/140 cases. After removing timestamps, elapsed fields,
and cache provenance, their candidate files, candidate symbols, per-case metrics, tier metrics, and
aggregates were identical. The retained 130 cases were unchanged from manifest v16. File
Recall@1/5/10/20 is `0.3300/0.6382/0.7364/0.8593`, with MRR `0.5419`. Across the 101 labeled cases,
Symbol Recall@1/5/10/20 is `0.2178/0.3952/0.4200/0.4794`, with MRR `0.3398`. Ruff's three
production files are the only new Top-20 misses, so coverage is 166 of 205 and 39 misses remain in
the denominator.

All three retained runs recorded 140/140 repository-map cache hits. Their per-case analysis means
were 3,525, 3,513, and 3,591 ms, with a three-run mean of 3,543 ms. Timing is observational only;
normalized ranking and metric output is the reproducibility gate.

The regenerated queue contains 60 primary and 25 reserve candidates across 36 repositories. Manual
review reduced the remaining unique multi-file pool to two cases. The original 60/200 (30%) target
is therefore not feasible without accepting incomplete ground truth; planning now preserves both
remaining candidates and targets the auditable ceiling of 47/200 (23.5%).

## v0.21 eighth 200-case expansion batch

Manifest v16 accepts ten reviewed Issue/Fix-PR pairs from Yarl, Pluggy, attrs, Virtualenv,
Requests, SciPy, Prefect, uv, and NumPy. It adds 15 production-file targets, including three
multi-file fixes, and six pre-fix Python symbol targets. SciPy C, Prefect TypeScript, and uv Rust
cases remain intentionally file-only; NumPy's newly added masked unwrap has no pre-fix symbol.

Review rejects h2 #1308/#1309 because one PR represents three overlapping CONNECT Issues,
scikit-learn #34170/#34657 because the patch is docstring-only, NumPy #30494/#30593 because the
Issue was closed by another PR and material hash-table files are omitted, and Ruff #21870/#27190
because the newly added rule implementation cannot exist in the pre-fix ground truth. Valid Jinja
and tox cases remain undecided to avoid same-file over-sampling and generated-artifact ambiguity.
All six accepted Python symbol targets resolve in their frozen maps.

The suite now contains 130 cases across 53 repositories: 17 main, 11 calibration, and 102
generalization cases. It records 191 production-file targets, 42 multi-file cases, and 127 reviewed
symbol targets across 95 cases.

Three deterministic v0.27 runs completed 130/130 cases. After removing timestamps, elapsed fields,
and cache provenance, their candidate files, candidate symbols, per-case metrics, tier metrics, and
aggregates were identical. The retained 120 cases were unchanged from manifest v15. File
Recall@1/5/10/20 is `0.3400/0.6335/0.7315/0.8562`, with MRR `0.5474`. Across the 95 labeled cases,
Symbol Recall@1/5/10/20 is `0.2263/0.3939/0.4202/0.4833`, with MRR `0.3428`. Six new production
targets are outside Top-20, so coverage is 155 of 191 and 36 misses remain in the denominator.

Run 1 reused 120 repository maps and rebuilt ten; runs 2 and 3 recorded 130/130 hits. Warm reuse
reduced mean in-process analysis time from 4,894 to 3,665 ms per case, a 25.11% reduction, without
changing any non-timing result.

The regenerated queue contains 70 primary and 25 reserve candidates across 37 repositories. Its
primary set includes all 18 remaining multi-file candidates required to reach 60/200 (30%).

## v0.20 seventh 200-case expansion batch

Manifest v15 accepts ten reviewed Issue/Fix-PR pairs from Jinja, Yarl, Boto3, Ansible, attrs,
Multidict, Botocore, Alembic, and h2. It adds 12 production-file targets, including two multi-file
fixes, and ten pre-fix Python symbol targets across eight cases. The Multidict C fix and the
multi-method Botocore Stubber fix remain intentionally file-only.

Review rejects six candidates rather than converting related changes into ground truth. Botocore
#3434/#3575 is a partial packaging-warning fix; pytest-asyncio #1090/#1481 addresses a later type
report rather than the snapshot reproduction; Multidict #1143/#1144 omits the material C header;
NumPy #30494, Sphinx #14238, and Requests #7434 were each closed by a different PR than the proposed
candidate. A valid second attrs case and a Prefect case with stale test-file eligibility remain
undecided instead of being mislabeled as rejections. All ten accepted Python symbol targets resolve
in their frozen repository maps.

The suite now contains 120 cases across 52 repositories: 17 main, 11 calibration, and 92
generalization cases. It records 176 production-file targets, 39 multi-file cases, and 121 reviewed
symbol targets across 89 cases.

Three deterministic v0.27 runs completed 120/120 cases. After removing timestamps, elapsed fields,
and cache provenance, their candidate files, candidate symbols, per-case metrics, tier metrics, and
aggregates were identical. The retained 110 cases were also unchanged from manifest v14. File
Recall@1/5/10/20 is `0.3433/0.6279/0.7321/0.8671`, with MRR `0.5544`. Across the 89 labeled cases,
Symbol Recall@1/5/10/20 is `0.2191/0.3755/0.4036/0.4710`, with MRR `0.3350`. The batch adds no
Top-20 miss: 146 of 176 production targets are retrieved, leaving the previous 30 misses in the
denominator.

Run 1 reused 110 repository maps and rebuilt ten; runs 2 and 3 recorded 120/120 hits. Warm reuse
reduced mean in-process analysis time from 4,724 to 3,576 ms per case, a 24.31% reduction, without
changing any non-timing result.

The regenerated 200-case queue contains 80 primary and 30 reserve candidates across 37
repositories. Its primary set includes the 21 additional multi-file cases required to reach
60/200 (30%). The reserve count decreases from 40 because only 110 remaining unique slots are
needed and available after reviewed decisions; retaining 40 reserves would make the constraints
infeasible even though the multi-file target itself remains feasible.

## v0.19 sixth 200-case expansion batch

Manifest v14 accepts ten reviewed Issue/Fix-PR pairs from Botocore, MarkupSafe, Matplotlib,
Setuptools, Django REST Framework, scikit-learn, h11, Flake8, and Pluggy. It adds 16 production-file
targets, including four multi-file fixes, and 14 pre-fix Python symbol targets across nine cases.
The MarkupSafe C speedups case remains intentionally file-only.

Review rejects Yarl #1458/#1638 because that PR is a documentation-only follow-up and the Issue
was already closed by the behavioral PR #1645. It also rejects h11 #31/#104: the Issue is a
discussion-driven header-casing feature, its automatic file list contains a formatting-only
reader change, and this batch already retains two stronger h11 defect reports. All 14 accepted
Python symbol targets resolve in their frozen repository maps.

The suite now contains 110 cases across 49 repositories: 17 main, 11 calibration, and 82
generalization cases. It records 164 production-file targets, 37 multi-file cases, and 111 reviewed
symbol targets across 81 cases.

Three deterministic v0.27 runs completed 110/110 cases. After removing timestamps, elapsed fields,
and cache provenance, their candidate files, candidate symbols, per-case metrics, tier metrics, and
aggregates were identical. The retained 100 cases were also unchanged from manifest v13. File
Recall@1/5/10/20 is `0.3473/0.6168/0.7168/0.8550`, with MRR `0.5516`. Across the 81 labeled cases,
Symbol Recall@1/5/10/20 is `0.2284/0.3693/0.4002/0.4619`, with MRR `0.3366`. Thirty of 164
production targets remain outside the deterministic Top-20; the new misses are Setuptools
`setuptools/config/_apply_pyprojecttoml.py` and Flake8 `src/flake8/options/config.py`.

Run 1 reused 106 repository maps and rebuilt four; runs 2 and 3 recorded 110/110 hits. Warm reuse
reduced mean in-process analysis time from 4,547 to 3,738 ms per case, a 17.79% reduction, without
changing any non-timing result.

The regenerated 200-case review queue now contains 90 primary and 40 reserve candidates across 38
repositories. Its primary set includes 23 multi-file cases so reaching 200 would preserve the
30% multi-file target.

## v0.18 fifth 200-case expansion batch

Manifest v13 accepts ten reviewed Issue/Fix-PR pairs from Jinja, pytest-asyncio, Requests, Pylint,
Flake8, pandas, Paramiko, urllib3, and uv. It introduces pytest-asyncio, Requests, and urllib3 as
new repositories, adds 13 production-file targets, and records ten pre-fix Python symbol targets
across nine cases. The two-file uv Rust case remains intentionally file-only.

Reviewers narrow urllib3 #4945 from three automatic files to the material `url.py` change because
`request.py` is documentation-only and `socks.py` is auxiliary reuse from another fix. The batch
also rejects Pandas Issue #61676 for PR #66794 because Issue #59609 already represents that same
fix. Paramiko retains `setup.py`: raising the minimum cryptography version is required by the new
runtime APIs rather than being unrelated packaging churn.

The suite now contains 100 cases across 46 repositories: 17 main, 11 calibration, and 72
generalization cases. It records 148 production-file targets, 33 multi-file cases, and 97 reviewed
symbol targets across 72 cases.

Three deterministic v0.27 runs completed 100/100 cases. After removing timestamps, elapsed fields,
and cache provenance, their candidate files, candidate symbols, per-case metrics, tier metrics, and
aggregates were identical. File Recall@1/5/10/20 is `0.3620/0.6318/0.7085/0.8538`, with MRR
`0.5651`. Across the 72 labeled cases, Symbol Recall@1/5/10/20 is
`0.2500/0.3970/0.4178/0.4873`, with MRR `0.3527`. Twenty-eight of 148 production targets remain
outside the deterministic Top-20 and stay in the denominator.

Run 1 rebuilt 98 schema-v2 repository maps and reused two same-SHA entries; runs 2 and 3 recorded
100/100 hits. Warm reuse reduced mean in-process analysis time from 7,526 to 4,426 ms per case, a
41.19% reduction, without changing any non-timing result.

## v0.17 fourth 200-case expansion batch

Manifest v12 accepts ten reviewed Issue/Fix-PR pairs from Prefect, Django REST Framework, h2,
MarkupSafe, Flake8, Jinja, tox, and Alembic. The batch spans eight repositories and Issues created
from 2020 through 2026. It adds ten single-file production targets and seven pre-fix Python symbol
targets; two Prefect TypeScript cases and MarkupSafe's module-level regex fix remain file-only.

Reviewers remove test and Storybook paths from the Prefect cases and reject Tornado #3182/#3523
because its entire patch is under `demos/`. Two other valid Jinja candidates remain undecided in
the queue rather than being mislabeled as rejections. All seven Python symbol targets resolve in
the repository index at their frozen pre-fix commits.

The suite now contains 90 cases across 43 repositories: 17 main, 11 calibration, and 62
generalization cases. It records 135 production-file targets, 31 multi-file cases, and 87 reviewed
symbol targets across 63 cases.

Three deterministic v0.27 runs completed 90/90 cases. After removing timestamps and elapsed
fields, their candidate files, candidate symbols, per-case metrics, tier metrics, and aggregates
were identical. File Recall@1/5/10/20 is `0.3652/0.6372/0.7131/0.8635`, with MRR `0.5704`.
Across the 63 labeled cases, Symbol Recall@1/5/10/20 is
`0.2619/0.4299/0.4537/0.5331`, with MRR `0.3714`. Twenty-five of 135 production targets remain
outside the deterministic Top-20 and stay in the denominator.

## v0.16 third 200-case expansion batch

Manifest v11 accepts ten reviewed Issue/Fix-PR pairs from Paramiko, Prefect, h2, Boto3, Django REST
Framework, h11, Jinja, and Matplotlib. The batch spans eight repositories and Issues created from
2017 through 2026. It adds 14 production-file targets, including three multi-file cases, and eight
pre-fix Python symbol targets; the two Prefect UI cases remain intentionally file-only.

Reviewers removed test paths from the Prefect candidates. The same selection rejects
a pandas candidate whose automatic files omit the material `.pyx` fix, an Ansible change with seven
equally material production files beyond the five-file acceptance boundary, and a Sphinx candidate
whose automatic audit omits the material `.sty` resource. These cases are not narrowed into
incomplete ground truth.

The suite now contains 80 cases across 41 repositories: 17 main, 11 calibration, and 52
generalization cases. It records 125 production-file targets, 31 multi-file cases, and 80 reviewed
symbol targets across 56 cases. All eight Python symbol targets added in this batch resolve in the
repository index at their recorded pre-fix commits.

Three deterministic v0.27 runs completed 80/80 cases. After removing timestamps and elapsed
fields, their candidate files, candidate symbols, per-case metrics, tier metrics, and aggregates
were identical. File Recall@1/5/10/20 is `0.3733/0.6294/0.7148/0.8840`, with MRR `0.5865`.
Across the 56 labeled cases, Symbol Recall@1/5/10/20 is
`0.2768/0.4301/0.4568/0.5461`, with MRR `0.3880`. Twenty-two of 125 production targets remain
outside the deterministic Top-20 and stay in the denominator.

## v0.15 second 200-case expansion batch

Manifest v10 accepts ten more manually reviewed Issue/Fix-PR pairs: Paramiko, Django REST
Framework, NumPy, Pluggy, Ruff, Ansible, Virtualenv, tox, pandas, and uv. Seven repositories are new
to the suite. Reviewers narrowed the automatic patch-derived file lists for Paramiko, NumPy,
Pluggy, and pandas so logging-only, documentation-only, annotation-only, and unrelated files do not
become ground truth. The same decision artifact rejects PyO3's test-only change, SQLAlchemy's
documentation-only resolution, and a Prefect Issue that was reopened after its merged PR.

The current suite contains 70 cases across 38 repositories: 17 main, 11 calibration, and 42
generalization cases. It records 111 production-file targets, 28 multi-file cases, and 72 reviewed
symbol targets across 48 cases. All 16 symbol targets added in this batch resolve in the Python
repository index at their recorded pre-fix commits. Rust and C targets remain intentionally
file-only because the current index does not parse their symbols.

Three deterministic v0.27 runs completed 70/70 cases. After removing timestamps and elapsed
fields, their candidates, symbols, per-case metrics, tier metrics, and aggregates were identical.
File Recall@1/5/10/20 is `0.3624/0.6145/0.7074/0.8960`, with MRR `0.5845`. Across the 48 labeled
cases, Symbol Recall@1/5/10/20 is `0.3021/0.4601/0.4913/0.5955`, with MRR `0.4267`. Nineteen of
111 production targets are outside the deterministic Top-20. The misses remain in the denominator
and show that mixed-language and indirect multi-file cases materially reduce the earlier apparent
candidate-pool saturation.

## v0.14 first 200-case expansion batch

Manifest v9 accepts ten manually reviewed multi-file Issue/Fix-PR pairs from ten repositories:
Paramiko, Boto3, Django REST Framework, Matplotlib, Jinja, Flake8, Packaging, tox, Pylint, and
Tornado. Each public Issue is closed, each same-repository PR is merged and explicitly references
the Issue, and every recorded pre-fix SHA is the parent of the first ordered PR commit outside the
PR commit set. A local frozen checkout confirmed all 24 production-file targets exist at those
commits; tests, documentation, changelog/news, and release metadata remain excluded.

That first batch produced a 60-case suite across 31 repositories: 17 main, 11 calibration, and 32
generalization cases. It has 86 reviewed production-file targets, including 21 multi-file cases,
and 56 reviewed symbol targets across 41 cases. The explicit v0.14 selection records symbol ground
truth alongside file decisions; files with multiple equally material methods or only a constant
change remain intentionally unlabeled. All 17 symbol targets added in this batch were also resolved
against the repository index at their recorded pre-fix commits before acceptance.

Three deterministic v0.26 runs completed 60/60 cases. After removing timestamps and elapsed fields,
their candidates, symbols, per-case metrics, tier metrics, and aggregates were identical. File
Recall@1/5/10/20 is `0.3811/0.6517/0.7517/0.9717`, with MRR `0.6115`. On the 41 labeled cases,
Symbol Recall@1/5/10/20 is `0.3049/0.4756/0.5122/0.6341`, with MRR `0.4345`. Four new production
targets are absent from the deterministic Top-20: `paramiko/common.py`, `boto3/compat.py`,
`lib/matplotlib/cbook/__init__.py`, and `pylint/config/callback_actions.py`. These misses are retained
as expansion evidence and are not filtered out of the benchmark.

## v0.13 50-case expansion outcome

Manifest v8 adds 18 manually reviewed Issue/Fix-PR pairs from eight repositories: Uvicorn,
Celery, Flask, Black, pip, mypy, Poetry, and Scrapy. The retained suite contains 50 cases across 21
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
catalog, rejected-candidate provenance, manual selection, frozen manifest, and evaluated results
are committed. Current audits
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

## Archived final 200-case review queue

On 2026-08-16, a broader discovery pass inspected 40 additional public repositories. The initial
four catalogs contained 684 audit records, of which 193 passed every blocking check. Targeted
deeper scans were then limited to 12 repositories that had already produced reviewable cases;
zero-yield Django and HTTPX scans were not repeated. Because the discovery passes overlap, record
counts are not case counts.

After accepting the 160-case intermediate into manifest v19, maximum-cardinality matching by both
`(repository, Issue)` and `(repository, fix PR)` leaves 67 unique candidates across 32 repositories.
`benchmarks/expansion-v200-review-queue-v19.json` archives:

- 40 primary candidates and 25 reserves;
- no more than five primary candidates per repository;
- at least one primary candidate from every repository represented in the unique pool;
- the last remaining multi-file candidate, which would raise the suite from 45/160 to the auditable
  ceiling of 46/200 (23%) if it passes review;
- 23 primary Issues created before 2026;
- five primary candidates that pass all four advisory checks for body, bug wording, diagnostics,
  and an explicit closing reference.

This archived queue is not benchmark ground truth. Every entry is serialized with
`status=needs_review`; the queue schema rejects `accepted`. Before any candidate enters a frozen
manifest, a reviewer must still confirm the Issue/fix relationship, inspect the production diff,
verify every expected file at the recorded pre-fix commit, decide the tier, and add reviewed symbol
ground truth where the fix hunk supports it. A reviewer may narrow the automatic file list but may
not add an unaudited path. Explicit rejection decisions are fed back into planning so rejected
candidates do not return to later queues. Final accepted and rejected decisions are recorded in
`benchmarks/expansion-v0.25-selection.json`; the 21 rejected audit records required to verify those
IDs are retained in `benchmarks/rejections-v0.25.json`. Replaying the final curation must load both
that file and `benchmarks/candidates-v0.25.json`; an unknown selected or rejected ID fails closed.
Unselected entries remain non-ground-truth provenance.

The raw discovery catalogs remain ignored because they contain repeated, mutable Issue snapshots.
The v19 compact queue is archived so review order, advisory signals, pre-fix SHA provenance,
expected files, and diversity constraints remain auditable now that the 200-case manifest exists.

Historical manifest v13 confirmed that the earlier candidate-pool saturation does not survive broader,
mixed-language, multi-file cases: 28 of 148 reviewed production targets are outside the
deterministic Top-20. Subsequent batches keep those misses in the denominator and must not weaken
the frozen-case acceptance rules to improve the headline metric.
