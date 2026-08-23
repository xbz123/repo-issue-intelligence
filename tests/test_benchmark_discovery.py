from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pytest

from repo_issue_intelligence.benchmark import (
    BenchmarkCase,
    BenchmarkManifest,
    BenchmarkSymbolTarget,
    BenchmarkTier,
)
from repo_issue_intelligence.benchmark_discovery import (
    CandidateRejectionEntry,
    CandidateSelectionEntry,
    CandidateSelectionManifest,
    CandidateStatus,
    audit_candidate,
    build_candidate_review_queue,
    classify_changed_files,
    curate_benchmark_expansion,
    discover_candidates,
    linked_pull_request_numbers,
    load_candidate_sources,
    reviewed_rejection_ids,
    save_curated_expansion,
)
from repo_issue_intelligence.models import IssueRecord

MERGE_SHA = "b" * 40
PRE_FIX_SHA = "a" * 40


def issue(number: int = 42) -> IssueRecord:
    timestamp = datetime(2026, 7, 30, tzinfo=UTC)
    return IssueRecord(
        number=number,
        title="Regression raises an incorrect validation error",
        body=(
            "Steps to reproduce the bug: call the validator and inspect the exception. "
            "Expected behavior is a successful response, but the actual behavior is an error."
        ),
        labels=["bug"],
        comments_count=2,
        created_at=timestamp,
        updated_at=timestamp,
        html_url=f"https://github.com/example/project/issues/{number}",
        author="reporter",
    )


def pull_request(number: int = 43) -> dict:
    timestamp = "2026-07-30T12:00:00Z"
    return {
        "number": number,
        "title": "Fix validator regression",
        "html_url": f"https://github.com/example/project/pull/{number}",
        "merged_at": timestamp,
        "merge_commit_sha": MERGE_SHA,
        "commits": 1,
        "head": {"sha": "c" * 40},
        "base": {
            "sha": PRE_FIX_SHA,
            "repo": {"full_name": "example/project"},
        },
    }


def pull_commits() -> list[dict]:
    return [
        {
            "sha": "c" * 40,
            "parents": [{"sha": PRE_FIX_SHA}],
        }
    ]


def test_linked_pull_request_numbers_keep_only_same_repository() -> None:
    timeline = [
        {
            "event": "cross-referenced",
            "source": {
                "issue": {
                    "number": 43,
                    "repository_url": "https://api.github.com/repos/example/project",
                    "pull_request": {"url": "https://api.github.com/repos/example/project/pulls/43"},
                }
            },
        },
        {
            "event": "cross-referenced",
            "source": {
                "issue": {
                    "number": 99,
                    "repository_url": "https://api.github.com/repos/other/project",
                    "pull_request": {"url": "https://api.github.com/repos/other/project/pulls/99"},
                }
            },
        },
        {
            "event": "connected",
            "subject": {
                "url": "https://api.github.com/repos/example/project/pulls/44",
            },
        },
    ]

    assert linked_pull_request_numbers(timeline, "example/project") == [43, 44]


def test_classify_changed_files_excludes_non_ground_truth_paths() -> None:
    files = classify_changed_files(
        [
            {"filename": "src/validator.py", "status": "modified", "changes": 4},
            {"filename": "tests/test_validator.py", "status": "modified", "changes": 8},
            {"filename": "docs/validation.md", "status": "modified", "changes": 3},
            {"filename": "src/new_validator.py", "status": "added", "changes": 20},
            {"filename": "docs_src/tutorial.py", "status": "modified", "changes": 5},
            {"filename": "testing/example.py", "status": "modified", "changes": 6},
        ]
    )

    assert files[0].eligible_source is True
    assert [item.eligible_source for item in files[1:]] == [
        False,
        False,
        False,
        False,
        False,
    ]
    assert files[3].exclusion_reason == "file does not exist at the pre-fix commit"


def test_audit_candidate_derives_pre_fix_sha_and_requires_review() -> None:
    candidate = audit_candidate(
        "example/project",
        issue(),
        pull_request(),
        [{"filename": "src/validator.py", "status": "modified", "changes": 4}],
        {"sha": MERGE_SHA, "parents": [{"sha": PRE_FIX_SHA}]},
        pull_commits(),
        max_source_files=5,
        suggested_tier=BenchmarkTier.MAIN,
    )

    assert candidate.status is CandidateStatus.NEEDS_REVIEW
    assert candidate.pre_fix_sha == PRE_FIX_SHA
    assert candidate.pre_fix_sha_source == "first_pull_commit_parent"
    assert candidate.expected_files == ["src/validator.py"]
    assert candidate.suggested_tier is BenchmarkTier.MAIN
    assert all(check.passed for check in candidate.audit_checks if check.blocking)


def test_audit_candidate_uses_first_pull_commit_parent_when_merge_parents_reverse() -> None:
    request = pull_request()
    request["commits"] = 2
    request["head"] = {"sha": "d" * 40}
    commits = [
        {"sha": "c" * 40, "parents": [{"sha": PRE_FIX_SHA}]},
        {"sha": "d" * 40, "parents": [{"sha": "c" * 40}]},
    ]

    candidate = audit_candidate(
        "example/project",
        issue(),
        request,
        [{"filename": "src/validator.py", "status": "modified", "changes": 4}],
        {
            "sha": MERGE_SHA,
            "parents": [
                {"sha": "d" * 40},
                {"sha": PRE_FIX_SHA},
            ],
        },
        commits,
        max_source_files=5,
    )

    assert candidate.status is CandidateStatus.NEEDS_REVIEW
    assert candidate.pre_fix_sha == PRE_FIX_SHA
    assert candidate.pre_fix_sha_source == "first_pull_commit_parent"


def test_audit_candidate_rejects_pre_fix_commit_inside_pull_request() -> None:
    leaked_sha = "c" * 40
    candidate = audit_candidate(
        "example/project",
        issue(),
        pull_request(),
        [{"filename": "src/validator.py", "status": "modified", "changes": 4}],
        {"sha": MERGE_SHA, "parents": [{"sha": PRE_FIX_SHA}]},
        [{"sha": leaked_sha, "parents": [{"sha": leaked_sha}]}],
        max_source_files=5,
    )

    assert candidate.status is CandidateStatus.REJECTED
    assert candidate.pre_fix_sha == leaked_sha
    assert any(
        check.code == "pre_fix_outside_pull_commits" and not check.passed
        for check in candidate.audit_checks
    )


def test_audit_candidate_rejects_test_only_pull_request() -> None:
    candidate = audit_candidate(
        "example/project",
        issue(),
        pull_request(),
        [{"filename": "tests/test_validator.py", "status": "modified", "changes": 4}],
        {"sha": MERGE_SHA, "parents": [{"sha": PRE_FIX_SHA}]},
        pull_commits(),
        max_source_files=5,
    )

    assert candidate.status is CandidateStatus.REJECTED
    assert candidate.expected_files == []
    assert any(
        check.code == "production_source_files" and not check.passed
        for check in candidate.audit_checks
    )


class FakeDiscoveryClient:
    def search_closed_linked_issues(self, repository, limit):
        assert repository == "example/project"
        assert limit == 10
        return [issue(42), issue(44)]

    def fetch_issue_timeline(self, repository, issue_number):
        return [
            {
                "source": {
                    "issue": {
                        "number": issue_number + 1,
                        "repository_url": "https://api.github.com/repos/example/project",
                        "pull_request": {
                            "url": (
                                "https://api.github.com/repos/example/project/"
                                f"pulls/{issue_number + 1}"
                            )
                        },
                    }
                }
            }
        ]

    def fetch_issue(self, repository, issue_number):
        return issue(issue_number)

    def fetch_pull_request(self, repository, pull_number):
        return pull_request(pull_number)

    def fetch_pull_request_files(self, repository, pull_number):
        return [{"filename": "src/validator.py", "status": "modified", "changes": 4}]

    def fetch_pull_request_commits(self, repository, pull_number):
        return pull_commits()

    def fetch_commit(self, repository, commit_sha):
        return {"sha": commit_sha, "parents": [{"sha": PRE_FIX_SHA}]}


def test_discovery_stops_after_reviewable_target() -> None:
    catalog = discover_candidates(
        FakeDiscoveryClient(),
        ["example/project"],
        target_per_repository=1,
        scan_limit_per_repository=10,
        suggested_tiers={"example/project": BenchmarkTier.GENERALIZATION},
    )

    assert len(catalog.candidates) == 1
    assert catalog.candidates[0].issue_snapshot.number == 42
    assert catalog.candidates[0].suggested_tier is BenchmarkTier.GENERALIZATION


def test_curate_expansion_accepts_only_explicit_manual_selection(tmp_path: Path) -> None:
    existing_issue = issue(1)
    base_manifest = BenchmarkManifest(
        name="test-benchmark",
        version=2,
        cases=[
            BenchmarkCase(
                id="existing-case",
                tier=BenchmarkTier.MAIN,
                repository="example/project",
                issue_number=1,
                issue_updated_at=existing_issue.updated_at,
                issue_snapshot=existing_issue,
                fix_pr_number=2,
                pre_fix_sha="d" * 40,
                expected_files=["src/existing.py"],
            )
        ],
    )
    candidate = audit_candidate(
        "example/project",
        issue(),
        pull_request(),
        [{"filename": "src/validator.py", "status": "modified", "changes": 4}],
        {"sha": MERGE_SHA, "parents": [{"sha": PRE_FIX_SHA}]},
        pull_commits(),
        max_source_files=5,
        suggested_tier=BenchmarkTier.GENERALIZATION,
    )
    selection = CandidateSelectionManifest(
        name="test-expansion",
        version=1,
        selections=[
            CandidateSelectionEntry(
                candidate_id=candidate.id,
                case_id="validator-regression",
                expected_symbols=[
                    BenchmarkSymbolTarget(
                        file="src/validator.py",
                        symbol="validate_payload",
                    )
                ],
                review_notes=["Confirmed that the PR closes the Issue and changes source code."],
            )
        ],
    )

    curated, expanded = curate_benchmark_expansion(
        base_manifest,
        [candidate],
        selection,
    )

    assert curated.candidates[0].status is CandidateStatus.ACCEPTED
    assert curated.candidates[0].review_notes == [
        "Confirmed that the PR closes the Issue and changes source code."
    ]
    assert expanded.version == 3
    assert [case.id for case in expanded.cases] == [
        "existing-case",
        "validator-regression",
    ]
    assert expanded.cases[-1].expected_symbols == [
        BenchmarkSymbolTarget(
            file="src/validator.py",
            symbol="validate_payload",
        )
    ]
    catalog_output = tmp_path / "accepted-candidates.json"
    save_curated_expansion(
        curated,
        expanded,
        catalog_output=catalog_output,
        manifest_output=tmp_path / "expanded-cases.json",
    )
    assert load_candidate_sources([catalog_output]) == curated.candidates


def test_curate_expansion_can_narrow_audited_expected_files() -> None:
    base_issue = issue(1)
    base_manifest = BenchmarkManifest(
        name="test-benchmark",
        version=2,
        cases=[
            BenchmarkCase(
                id="existing-case",
                tier=BenchmarkTier.MAIN,
                repository="example/project",
                issue_number=1,
                issue_updated_at=base_issue.updated_at,
                issue_snapshot=base_issue,
                fix_pr_number=2,
                pre_fix_sha="d" * 40,
                expected_files=["src/existing.py"],
            )
        ],
    )
    candidate = audit_candidate(
        "example/project",
        issue(),
        pull_request(),
        [
            {"filename": "src/fix.py", "status": "modified", "changes": 4},
            {"filename": "src/unrelated.py", "status": "modified", "changes": 2},
        ],
        {"sha": MERGE_SHA, "parents": [{"sha": PRE_FIX_SHA}]},
        pull_commits(),
        max_source_files=5,
        suggested_tier=BenchmarkTier.GENERALIZATION,
    )
    selection = CandidateSelectionManifest(
        name="narrowed-expansion",
        version=1,
        selections=[
            CandidateSelectionEntry(
                candidate_id=candidate.id,
                case_id="narrowed-case",
                expected_files=["src/fix.py"],
                review_notes=["Only fix.py contains the reviewed behavioral fix."],
            )
        ],
    )

    curated, expanded = curate_benchmark_expansion(
        base_manifest,
        [candidate],
        selection,
    )

    assert curated.candidates[0].expected_files == ["src/fix.py"]
    assert expanded.cases[-1].expected_files == ["src/fix.py"]

    selection.selections[0].expected_files = ["src/not-in-the-patch.py"]
    with pytest.raises(ValueError, match="outside the audited patch"):
        curate_benchmark_expansion(base_manifest, [candidate], selection)


def test_curate_expansion_records_rejections_without_accepting_them() -> None:
    base_issue = issue(1)
    base_manifest = BenchmarkManifest(
        name="test-benchmark",
        version=1,
        cases=[
            BenchmarkCase(
                id="existing-case",
                tier=BenchmarkTier.MAIN,
                repository="existing/project",
                issue_number=1,
                issue_updated_at=base_issue.updated_at,
                issue_snapshot=base_issue,
                fix_pr_number=2,
                pre_fix_sha="d" * 40,
                expected_files=["src/existing.py"],
            )
        ],
    )
    selected = _queue_candidate("selected/project", 11, 101, multi_file=False)
    rejected = _queue_candidate("rejected/project", 12, 102, multi_file=False)
    selection = CandidateSelectionManifest(
        name="reviewed-expansion",
        version=1,
        selections=[
            CandidateSelectionEntry(
                candidate_id=selected.id,
                case_id="selected-case",
                tier=BenchmarkTier.GENERALIZATION,
                review_notes=["Behavioral fix confirmed."],
            )
        ],
        rejections=[
            CandidateRejectionEntry(
                candidate_id=rejected.id,
                reason="The patch only changes tests.",
            )
        ],
    )

    curated, expanded = curate_benchmark_expansion(
        base_manifest,
        [selected, rejected],
        selection,
    )

    assert [candidate.id for candidate in curated.candidates] == [selected.id]
    assert expanded.cases[-1].id == "selected-case"

    replayed_curated, replayed_expanded = curate_benchmark_expansion(
        base_manifest,
        [selected],
        selection,
    )

    assert replayed_curated.candidates == curated.candidates
    assert replayed_expanded == expanded

    queue = build_candidate_review_queue(
        base_manifest,
        [selected, rejected],
        target_total_cases=2,
        reserve_cases=0,
        target_multi_file_share=0,
        excluded_candidate_ids={entry.candidate_id for entry in selection.rejections},
    )
    assert [entry.candidate_id for entry in queue.entries] == [selected.id]

    with pytest.raises(ValueError, match="Unknown excluded candidate IDs"):
        build_candidate_review_queue(
            base_manifest,
            [selected, rejected],
            target_total_cases=2,
            reserve_cases=0,
            target_multi_file_share=0,
            excluded_candidate_ids={"missing-candidate"},
        )

    selection.rejections.append(
        CandidateRejectionEntry(
            candidate_id=selected.id,
            reason="A candidate cannot have both decisions.",
        )
    )
    with pytest.raises(ValueError, match="both selected and rejected"):
        curate_benchmark_expansion(base_manifest, [selected, rejected], selection)


def test_reviewed_rejection_ids_fail_closed_across_manifests() -> None:
    first = CandidateSelectionManifest(
        name="first-review",
        version=1,
        selections=[
            CandidateSelectionEntry(
                candidate_id="selected-candidate",
                case_id="selected-case",
                tier=BenchmarkTier.GENERALIZATION,
                review_notes=["Accepted after review."],
            )
        ],
        rejections=[
            CandidateRejectionEntry(
                candidate_id="rejected-candidate",
                reason="Rejected after review.",
            )
        ],
    )
    duplicate = CandidateSelectionManifest(
        name="duplicate-review",
        version=1,
        selections=[
            CandidateSelectionEntry(
                candidate_id="another-candidate",
                case_id="another-case",
                tier=BenchmarkTier.GENERALIZATION,
                review_notes=["Accepted after review."],
            )
        ],
        rejections=[
            CandidateRejectionEntry(
                candidate_id="rejected-candidate",
                reason="The same rejection must not be repeated.",
            )
        ],
    )
    conflict = duplicate.model_copy(
        update={
            "rejections": [
                CandidateRejectionEntry(
                    candidate_id="selected-candidate",
                    reason="This conflicts with the earlier acceptance.",
                )
            ]
        },
        deep=True,
    )
    rejection_only = CandidateSelectionManifest(
        name="rejection-only-review",
        version=1,
        rejections=[
            CandidateRejectionEntry(
                candidate_id="rejection-only-candidate",
                reason="A review batch may reject every candidate.",
            )
        ],
    )

    assert reviewed_rejection_ids([first]) == frozenset({"rejected-candidate"})
    assert reviewed_rejection_ids([rejection_only]) == frozenset(
        {"rejection-only-candidate"}
    )
    with pytest.raises(ValueError, match="duplicate rejected candidate IDs"):
        reviewed_rejection_ids([first, duplicate])
    with pytest.raises(ValueError, match="across review decisions"):
        reviewed_rejection_ids([first, conflict])
    with pytest.raises(ValueError, match="at least one selected candidate"):
        curate_benchmark_expansion(
            BenchmarkManifest.model_construct(name="test", version=1, cases=[]),
            [],
            rejection_only,
        )


def test_curated_symbols_must_remain_on_expected_files() -> None:
    base_issue = issue(1)
    base_manifest = BenchmarkManifest(
        name="test",
        version=1,
        cases=[
            BenchmarkCase(
                id="existing-case",
                tier=BenchmarkTier.MAIN,
                repository="existing/project",
                issue_number=1,
                issue_updated_at=base_issue.updated_at,
                issue_snapshot=base_issue,
                fix_pr_number=2,
                pre_fix_sha="d" * 40,
                expected_files=["src/existing.py"],
            )
        ],
    )
    selected = _queue_candidate("selected/project", 11, 101, multi_file=True)
    selection = CandidateSelectionManifest(
        name="reviewed-expansion",
        version=1,
        selections=[
            CandidateSelectionEntry(
                candidate_id=selected.id,
                case_id="selected-case",
                expected_files=[selected.expected_files[0]],
                expected_symbols=[
                    BenchmarkSymbolTarget(
                        file=selected.expected_files[-1],
                        symbol="Removed.symbol",
                    )
                ],
                review_notes=["Only the first file contains the behavioral fix."],
            )
        ],
    )

    with pytest.raises(ValueError, match="symbol targets outside expected files"):
        curate_benchmark_expansion(base_manifest, [selected], selection)


def _queue_candidate(
    repository: str,
    issue_number: int,
    pull_number: int,
    *,
    multi_file: bool,
):
    candidate_issue = issue(issue_number).model_copy(
        update={
            "created_at": datetime(2020 + issue_number % 5, 1, 1, tzinfo=UTC),
            "updated_at": datetime(2026, 7, 30, tzinfo=UTC),
        }
    )
    request = pull_request(pull_number)
    request["base"]["repo"]["full_name"] = repository
    request["body"] = f"Fixes #{issue_number}"
    files = [
        {"filename": f"src/module_{issue_number}.py", "status": "modified"}
    ]
    if multi_file:
        files.append(
            {"filename": f"src/helper_{issue_number}.py", "status": "modified"}
        )
    return audit_candidate(
        repository,
        candidate_issue,
        request,
        files,
        {"sha": MERGE_SHA, "parents": [{"sha": PRE_FIX_SHA}]},
        pull_commits(),
        max_source_files=5,
    )


def test_build_candidate_review_queue_balances_and_deduplicates() -> None:
    base_issue = issue(1)
    base_manifest = BenchmarkManifest(
        name="test-benchmark",
        version=8,
        cases=[
            BenchmarkCase(
                id="existing-case",
                tier=BenchmarkTier.MAIN,
                repository="existing/project",
                issue_number=1,
                issue_updated_at=base_issue.updated_at,
                issue_snapshot=base_issue,
                fix_pr_number=2,
                pre_fix_sha="d" * 40,
                expected_files=["src/existing.py"],
            )
        ],
    )
    candidates = [
        _queue_candidate(
            f"owner/project-{repository_index}",
            repository_index * 10 + candidate_index,
            repository_index * 100 + candidate_index,
            multi_file=candidate_index <= 2,
        )
        for repository_index in range(1, 5)
        for candidate_index in range(1, 5)
    ]
    duplicate_pr = _queue_candidate(
        "owner/project-1",
        99,
        101,
        multi_file=True,
    )

    queue = build_candidate_review_queue(
        base_manifest,
        [*candidates, duplicate_pr],
        target_total_cases=9,
        reserve_cases=2,
        max_primary_per_repository=2,
        target_multi_file_share=0.44,
    )

    primary = [entry for entry in queue.entries if entry.priority == "primary"]
    reserves = [entry for entry in queue.entries if entry.priority == "reserve"]
    repository_counts = Counter(entry.repository for entry in primary)
    assert queue.requested_new_cases == 8
    assert len(primary) == 8
    assert len(reserves) == 2
    assert queue.available_reviewable_records == 17
    assert queue.available_unique_cases == 16
    assert queue.primary_multi_file_cases >= 4
    assert queue.primary_repositories == 4
    assert queue.name == "test-benchmark-9-case-review-queue"
    assert max(repository_counts.values()) == 2
    assert len({entry.fix_pr_number for entry in queue.entries}) == len(queue.entries)
    assert [entry.review_order for entry in queue.entries] == list(range(1, 11))
    assert all(entry.pre_fix_sha == PRE_FIX_SHA for entry in queue.entries)
    assert all(
        entry.pre_fix_sha_source == "first_pull_commit_parent"
        for entry in queue.entries
    )
    assert all(entry.status is CandidateStatus.NEEDS_REVIEW for entry in queue.entries)


def test_build_candidate_review_queue_uses_maximum_issue_pr_matching() -> None:
    base_issue = issue(1)
    base_manifest = BenchmarkManifest(
        name="matching-benchmark",
        version=1,
        cases=[
            BenchmarkCase(
                id="existing-case",
                tier=BenchmarkTier.MAIN,
                repository="existing/project",
                issue_number=1,
                issue_updated_at=base_issue.updated_at,
                issue_snapshot=base_issue,
                fix_pr_number=2,
                pre_fix_sha="d" * 40,
                expected_files=["src/existing.py"],
            )
        ],
    )
    candidates = [
        _queue_candidate("owner/project", 11, 101, multi_file=False),
        _queue_candidate("owner/project", 11, 102, multi_file=False),
        _queue_candidate("owner/project", 12, 101, multi_file=False),
    ]

    queue = build_candidate_review_queue(
        base_manifest,
        candidates,
        target_total_cases=3,
        reserve_cases=0,
        max_primary_per_repository=2,
        target_multi_file_share=0,
    )

    primary = [entry for entry in queue.entries if entry.priority == "primary"]
    assert queue.available_unique_cases == 2
    assert len(primary) == 2
    assert len({entry.issue_number for entry in primary}) == 2
    assert len({entry.fix_pr_number for entry in primary}) == 2


def test_build_candidate_review_queue_keeps_quota_feasible_matching_edges() -> None:
    base_issue = issue(1)
    base_manifest = BenchmarkManifest(
        name="matching-quota-benchmark",
        version=1,
        cases=[
            BenchmarkCase(
                id="existing-case",
                tier=BenchmarkTier.MAIN,
                repository="existing/project",
                issue_number=1,
                issue_updated_at=base_issue.updated_at,
                issue_snapshot=base_issue,
                fix_pr_number=2,
                pre_fix_sha="d" * 40,
                expected_files=["src/existing.py"],
            )
        ],
    )
    preferred_single = _queue_candidate(
        "owner/project",
        11,
        101,
        multi_file=False,
    )
    alternate_multi_candidate = _queue_candidate(
        "owner/project",
        12,
        101,
        multi_file=True,
    )
    alternate_multi = alternate_multi_candidate.model_copy(
        update={
            "audit_checks": [
                check.model_copy(update={"passed": False})
                if check.code == "bug_signal"
                else check
                for check in alternate_multi_candidate.audit_checks
            ]
        },
        deep=True,
    )
    independent_multi = _queue_candidate(
        "owner/project",
        13,
        102,
        multi_file=True,
    )

    queue = build_candidate_review_queue(
        base_manifest,
        [preferred_single, alternate_multi, independent_multi],
        target_total_cases=3,
        reserve_cases=0,
        max_primary_per_repository=2,
        target_multi_file_share=0.66,
    )

    primary = [entry for entry in queue.entries if entry.priority == "primary"]
    assert queue.available_unique_cases == 2
    assert queue.available_unique_multi_file_cases == 2
    assert len(primary) == 2
    assert all(entry.multi_file for entry in primary)
    assert {entry.issue_number for entry in primary} == {12, 13}
    assert {entry.fix_pr_number for entry in primary} == {101, 102}


def test_build_candidate_review_queue_preserves_reserve_matching_capacity() -> None:
    base_issue = issue(1)
    base_manifest = BenchmarkManifest(
        name="reserve-capacity-benchmark",
        version=1,
        cases=[
            BenchmarkCase(
                id="existing-case",
                tier=BenchmarkTier.MAIN,
                repository="existing/project",
                issue_number=1,
                issue_updated_at=base_issue.updated_at,
                issue_snapshot=base_issue,
                fix_pr_number=2,
                pre_fix_sha="d" * 40,
                expected_files=["src/existing.py"],
            )
        ],
    )
    candidates = [
        _queue_candidate("owner/project", 11, 101, multi_file=False),
        _queue_candidate("owner/project", 11, 102, multi_file=False),
        _queue_candidate("owner/project", 12, 101, multi_file=False),
    ]

    queue = build_candidate_review_queue(
        base_manifest,
        candidates,
        target_total_cases=2,
        reserve_cases=1,
        max_primary_per_repository=1,
        target_multi_file_share=0,
    )

    assert len(queue.entries) == 2
    assert len({entry.issue_number for entry in queue.entries}) == 2
    assert len({entry.fix_pr_number for entry in queue.entries}) == 2
    primary = next(entry for entry in queue.entries if entry.priority == "primary")
    assert (primary.issue_number, primary.fix_pr_number) != (11, 101)


def test_build_candidate_review_queue_solves_coverage_and_multi_file_together() -> None:
    base_issue = issue(1)
    base_manifest = BenchmarkManifest(
        name="quota-benchmark",
        version=1,
        cases=[
            BenchmarkCase(
                id="existing-case",
                tier=BenchmarkTier.MAIN,
                repository="existing/project",
                issue_number=1,
                issue_updated_at=base_issue.updated_at,
                issue_snapshot=base_issue,
                fix_pr_number=2,
                pre_fix_sha="d" * 40,
                expected_files=["src/existing.py"],
            )
        ],
    )
    preferred_single = _queue_candidate(
        "owner/project-a",
        11,
        101,
        multi_file=False,
    )
    multi_candidate = _queue_candidate(
        "owner/project-a",
        12,
        102,
        multi_file=True,
    )
    lower_ranked_multi = multi_candidate.model_copy(
        update={
            "audit_checks": [
                check.model_copy(update={"passed": False})
                if check.code == "bug_signal"
                else check
                for check in multi_candidate.audit_checks
            ]
        },
        deep=True,
    )
    other_multi = _queue_candidate(
        "owner/project-b",
        21,
        201,
        multi_file=True,
    )

    queue = build_candidate_review_queue(
        base_manifest,
        [preferred_single, lower_ranked_multi, other_multi],
        target_total_cases=3,
        reserve_cases=0,
        max_primary_per_repository=1,
        target_multi_file_share=0.66,
    )

    primary = [entry for entry in queue.entries if entry.priority == "primary"]
    assert len(primary) == 2
    assert all(entry.multi_file for entry in primary)


def test_planning_and_curation_require_complete_blocking_checks() -> None:
    base_issue = issue(1)
    base_manifest = BenchmarkManifest(
        name="audit-benchmark",
        version=1,
        cases=[
            BenchmarkCase(
                id="existing-case",
                tier=BenchmarkTier.MAIN,
                repository="existing/project",
                issue_number=1,
                issue_updated_at=base_issue.updated_at,
                issue_snapshot=base_issue,
                fix_pr_number=2,
                pre_fix_sha="d" * 40,
                expected_files=["src/existing.py"],
            )
        ],
    )
    incomplete = _queue_candidate(
        "owner/project",
        11,
        101,
        multi_file=False,
    ).model_copy(update={"audit_checks": []}, deep=True)

    with pytest.raises(ValueError, match="unique reviewable candidates"):
        build_candidate_review_queue(
            base_manifest,
            [incomplete],
            target_total_cases=2,
        )

    selection = CandidateSelectionManifest(
        name="invalid-selection",
        version=1,
        selections=[
            CandidateSelectionEntry(
                candidate_id=incomplete.id,
                case_id="invalid-case",
                tier=BenchmarkTier.GENERALIZATION,
                review_notes=["This must not bypass missing audit checks."],
            )
        ],
    )
    with pytest.raises(ValueError, match="required blocking checks"):
        curate_benchmark_expansion(base_manifest, [incomplete], selection)


def test_build_candidate_review_queue_rejects_insufficient_pool() -> None:
    base_issue = issue(1)
    base_manifest = BenchmarkManifest(
        name="test-benchmark",
        version=1,
        cases=[
            BenchmarkCase(
                id="existing-case",
                tier=BenchmarkTier.MAIN,
                repository="existing/project",
                issue_number=1,
                issue_updated_at=base_issue.updated_at,
                issue_snapshot=base_issue,
                fix_pr_number=2,
                pre_fix_sha="d" * 40,
                expected_files=["src/existing.py"],
            )
        ],
    )

    try:
        build_candidate_review_queue(
            base_manifest,
            [_queue_candidate("owner/project", 2, 3, multi_file=False)],
            target_total_cases=3,
        )
    except ValueError as error:
        assert "unique reviewable candidates" in str(error)
    else:
        raise AssertionError("Expected an insufficient candidate pool error")
