from datetime import UTC, datetime
from pathlib import Path

from repo_issue_intelligence.benchmark import (
    BenchmarkCase,
    BenchmarkManifest,
    BenchmarkTier,
)
from repo_issue_intelligence.benchmark_discovery import (
    CandidateSelectionEntry,
    CandidateSelectionManifest,
    CandidateStatus,
    audit_candidate,
    classify_changed_files,
    curate_benchmark_expansion,
    discover_candidates,
    linked_pull_request_numbers,
    load_candidate_sources,
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
        "base": {"repo": {"full_name": "example/project"}},
    }


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
        [],
        max_source_files=5,
        suggested_tier=BenchmarkTier.MAIN,
    )

    assert candidate.status is CandidateStatus.NEEDS_REVIEW
    assert candidate.pre_fix_sha == PRE_FIX_SHA
    assert candidate.pre_fix_sha_source == "single_commit_parent"
    assert candidate.expected_files == ["src/validator.py"]
    assert candidate.suggested_tier is BenchmarkTier.MAIN
    assert all(check.passed for check in candidate.audit_checks if check.blocking)


def test_audit_candidate_rejects_test_only_pull_request() -> None:
    candidate = audit_candidate(
        "example/project",
        issue(),
        pull_request(),
        [{"filename": "tests/test_validator.py", "status": "modified", "changes": 4}],
        {"sha": MERGE_SHA, "parents": [{"sha": PRE_FIX_SHA}]},
        [],
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
        raise AssertionError("Single-commit PR should not fetch all commits")

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
        [],
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
    catalog_output = tmp_path / "accepted-candidates.json"
    save_curated_expansion(
        curated,
        expanded,
        catalog_output=catalog_output,
        manifest_output=tmp_path / "expanded-cases.json",
    )
    assert load_candidate_sources([catalog_output]) == curated.candidates
