from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from .benchmark import BenchmarkCase, BenchmarkManifest, BenchmarkTier
from .github_client import REPOSITORY_PATTERN
from .models import IssueRecord
from .repository_index import LANGUAGE_BY_SUFFIX

PULL_NUMBER_PATTERN = re.compile(r"/(?:pull|pulls)/(\d+)(?:$|[/?#])")
BUG_TERMS = {"bug", "regression", "crash", "incorrect", "error", "broken"}
DIAGNOSTIC_TERMS = {
    "actual behavior",
    "expected behavior",
    "exception",
    "reproduce",
    "reproduction",
    "steps",
    "traceback",
}
EXCLUDED_PATH_PARTS = {
    ".github",
    "benchmark",
    "benchmarks",
    "changelog",
    "changes",
    "doc",
    "docs",
    "example",
    "examples",
    "generated",
    "news",
    "script",
    "scripts",
    "test",
    "tests",
    "vendor",
    "vendored",
}
EXCLUDED_PATH_PREFIXES = (
    "benchmark",
    "doc",
    "example",
    "test",
)
ADVISORY_CHECK_WEIGHTS = {
    "issue_has_body": 1,
    "bug_signal": 2,
    "diagnostic_signal": 2,
    "closing_reference": 3,
    "unique_fix_pr": 1,
}


class CandidateStatus(StrEnum):
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"
    ACCEPTED = "accepted"


class CandidateReviewPriority(StrEnum):
    PRIMARY = "primary"
    RESERVE = "reserve"


class CandidateAuditCheck(BaseModel):
    code: str
    passed: bool
    blocking: bool
    detail: str


class CandidateChangedFile(BaseModel):
    path: str
    status: str
    previous_path: str | None = None
    additions: int = 0
    deletions: int = 0
    changes: int = 0
    eligible_source: bool
    exclusion_reason: str | None = None


class BenchmarkCandidate(BaseModel):
    id: str
    repository: str
    suggested_tier: BenchmarkTier | None = None
    issue_snapshot: IssueRecord
    fix_pr_number: int = Field(ge=1)
    fix_pr_title: str
    fix_pr_url: str
    fix_pr_merged_at: datetime | None = None
    merge_commit_sha: str | None = None
    pre_fix_sha: str | None = None
    pre_fix_sha_source: str | None = None
    expected_files: list[str] = Field(default_factory=list)
    changed_files: list[CandidateChangedFile] = Field(default_factory=list)
    audit_checks: list[CandidateAuditCheck] = Field(default_factory=list)
    status: CandidateStatus
    review_notes: list[str] = Field(default_factory=list)


class CandidateCatalog(BaseModel):
    name: str
    version: int = Field(ge=1)
    generated_at: datetime
    repositories: list[str]
    search_query: str
    target_per_repository: int
    scan_limit_per_repository: int
    max_source_files: int
    candidates: list[BenchmarkCandidate]


class CandidateSelectionEntry(BaseModel):
    candidate_id: str
    case_id: str
    tier: BenchmarkTier | None = None
    review_notes: list[str] = Field(min_length=1)


class CandidateSelectionManifest(BaseModel):
    name: str
    version: int = Field(ge=1)
    manifest_name: str | None = None
    selections: list[CandidateSelectionEntry] = Field(min_length=1)


class CuratedCandidateCatalog(BaseModel):
    name: str
    version: int = Field(ge=1)
    curated_at: datetime
    base_manifest_name: str
    base_manifest_version: int
    candidates: list[BenchmarkCandidate] = Field(min_length=1)


class CandidateReviewQueueEntry(BaseModel):
    review_order: int = Field(ge=1)
    candidate_id: str
    priority: CandidateReviewPriority
    repository: str
    issue_number: int = Field(ge=1)
    fix_pr_number: int = Field(ge=1)
    issue_title: str
    issue_created_at: datetime
    suggested_case_id: str
    suggested_tier: BenchmarkTier
    expected_files: list[str] = Field(min_length=1)
    multi_file: bool
    advisory_score: int = Field(ge=0)
    passed_advisory_checks: list[str]
    review_flags: list[str]
    status: Literal[CandidateStatus.NEEDS_REVIEW] = CandidateStatus.NEEDS_REVIEW


class CandidateReviewQueue(BaseModel):
    name: str
    version: int = Field(ge=1)
    generated_at: datetime
    base_manifest_name: str
    base_manifest_version: int
    target_total_cases: int = Field(ge=1)
    base_case_count: int = Field(ge=0)
    requested_new_cases: int = Field(ge=1)
    reserve_cases: int = Field(ge=0)
    max_primary_per_repository: int = Field(ge=1)
    target_multi_file_share: float = Field(ge=0, le=1)
    required_new_multi_file_cases: int = Field(ge=0)
    available_reviewable_records: int = Field(ge=0)
    available_unique_cases: int = Field(ge=0)
    available_unique_multi_file_cases: int = Field(ge=0)
    primary_multi_file_cases: int = Field(ge=0)
    primary_repositories: int = Field(ge=0)
    entries: list[CandidateReviewQueueEntry] = Field(min_length=1)


class DiscoveryGitHubClient(Protocol):
    def search_closed_linked_issues(
        self,
        repository: str,
        limit: int,
    ) -> list[IssueRecord]: ...

    def fetch_issue(self, repository: str, issue_number: int) -> IssueRecord: ...

    def fetch_issue_timeline(
        self,
        repository: str,
        issue_number: int,
    ) -> list[dict]: ...

    def fetch_pull_request(self, repository: str, pull_number: int) -> dict: ...

    def fetch_pull_request_files(
        self,
        repository: str,
        pull_number: int,
    ) -> list[dict]: ...

    def fetch_pull_request_commits(
        self,
        repository: str,
        pull_number: int,
    ) -> list[dict]: ...

    def fetch_commit(self, repository: str, commit_sha: str) -> dict: ...


def _repository_from_api_url(value: str) -> str | None:
    marker = "/repos/"
    if marker not in value:
        return None
    suffix = value.split(marker, maxsplit=1)[1]
    parts = suffix.split("/")
    if len(parts) < 2:
        return None
    return f"{parts[0]}/{parts[1]}"


def linked_pull_request_numbers(timeline: Sequence[dict], repository: str) -> list[int]:
    numbers: set[int] = set()
    for event in timeline:
        source_issue = (event.get("source") or {}).get("issue") or {}
        if source_issue.get("pull_request"):
            source_repository = _repository_from_api_url(
                str(source_issue.get("repository_url") or "")
            )
            if source_repository and source_repository.lower() == repository.lower():
                number = source_issue.get("number")
                if isinstance(number, int) and number > 0:
                    numbers.add(number)

        subject = event.get("subject") or {}
        urls = [
            subject.get("url"),
            subject.get("html_url"),
            event.get("url"),
        ]
        for value in urls:
            if not isinstance(value, str):
                continue
            source_repository = _repository_from_api_url(value)
            if source_repository and source_repository.lower() != repository.lower():
                continue
            match = PULL_NUMBER_PATTERN.search(value)
            if match:
                numbers.add(int(match.group(1)))
    return sorted(numbers)


def _is_excluded_source_path(path: str) -> str | None:
    parts = tuple(part.lower() for part in Path(path).parts)
    filename = parts[-1] if parts else ""
    if any(
        part in EXCLUDED_PATH_PARTS
        or any(part.startswith(prefix) for prefix in EXCLUDED_PATH_PREFIXES)
        for part in parts[:-1]
    ):
        return "test, documentation, example, generated, or vendored path"
    if filename == "conftest.py" or filename.startswith("test_") or filename.endswith("_test.py"):
        return "test file"
    if Path(path).suffix.lower() not in LANGUAGE_BY_SUFFIX:
        return "unsupported source suffix"
    return None


def classify_changed_files(files: Sequence[dict]) -> list[CandidateChangedFile]:
    classified: list[CandidateChangedFile] = []
    for item in files:
        path = str(item.get("filename") or "")
        status = str(item.get("status") or "")
        reason = _is_excluded_source_path(path)
        if status == "added":
            reason = reason or "file does not exist at the pre-fix commit"
        elif status == "renamed":
            reason = reason or "renamed file requires manual ground-truth review"
        elif status not in {"modified", "removed"}:
            reason = reason or f"unsupported pull-request file status: {status or 'missing'}"
        classified.append(
            CandidateChangedFile(
                path=path,
                status=status,
                previous_path=item.get("previous_filename"),
                additions=int(item.get("additions") or 0),
                deletions=int(item.get("deletions") or 0),
                changes=int(item.get("changes") or 0),
                eligible_source=reason is None,
                exclusion_reason=reason,
            )
        )
    return classified


def _derive_pre_fix_sha(
    pull_request: dict,
    merge_commit: dict | None,
    pull_commits: Sequence[dict],
) -> tuple[str | None, str | None, str]:
    if pull_commits:
        first_commit_parents = pull_commits[0].get("parents") or []
        if first_commit_parents and first_commit_parents[0].get("sha"):
            return (
                str(first_commit_parents[0]["sha"]),
                "first_pull_commit_parent",
                "Derived from the parent of the first PR commit.",
            )
    if merge_commit is None:
        return None, None, "The merge commit could not be loaded."
    parents = merge_commit.get("parents") or []
    merge_sha = str(pull_request.get("merge_commit_sha") or "")
    head_sha = str(((pull_request.get("head") or {}).get("sha")) or "")
    commit_count = int(pull_request.get("commits") or 0)
    if not parents:
        return None, None, "The merge commit has no parent."
    first_parent = str(parents[0].get("sha") or "")
    if not first_parent:
        return None, None, "The merge commit first parent is missing."
    if len(parents) >= 2:
        base_sha = str(((pull_request.get("base") or {}).get("sha")) or "")
        parent_shas = {str(parent.get("sha") or "") for parent in parents}
        if base_sha and base_sha in parent_shas:
            return (
                base_sha,
                "merge_base_parent",
                "Derived from the PR base SHA found among merge parents.",
            )
        return (
            None,
            None,
            "The merge commit parent order is ambiguous without PR commit history.",
        )
    if commit_count == 1:
        return first_parent, "single_commit_parent", "Derived from a single-commit fix."
    if merge_sha and head_sha and merge_sha != head_sha:
        return first_parent, "squash_commit_parent", "Derived from a squash commit parent."
    return (
        None,
        None,
        "A multi-commit pull request requires its ordered commit history.",
    )


def _parse_github_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _has_closing_reference(body: str, issue_number: int) -> bool:
    keyword = r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)"
    direct = re.compile(rf"(?i)\b{keyword}\s*:?\s*#{issue_number}\b")
    url = re.compile(
        rf"(?i)\b{keyword}\s*:?\s*https://github\.com/[^/\s]+/[^/\s]+/"
        rf"issues/{issue_number}\b"
    )
    return bool(direct.search(body) or url.search(body))


def audit_candidate(
    repository: str,
    issue: IssueRecord,
    pull_request: dict,
    pull_files: Sequence[dict],
    merge_commit: dict | None,
    pull_commits: Sequence[dict],
    *,
    max_source_files: int,
    suggested_tier: BenchmarkTier | None = None,
) -> BenchmarkCandidate:
    pull_number = int(pull_request["number"])
    changed_files = classify_changed_files(pull_files)
    expected_files = sorted(
        changed_file.path for changed_file in changed_files if changed_file.eligible_source
    )
    pre_fix_sha, pre_fix_source, pre_fix_detail = _derive_pre_fix_sha(
        pull_request,
        merge_commit,
        pull_commits,
    )
    pull_commit_shas = {
        str(commit.get("sha") or "")
        for commit in pull_commits
        if commit.get("sha")
    }
    base_repository = str(
        (((pull_request.get("base") or {}).get("repo") or {}).get("full_name")) or ""
    )
    merged_at = _parse_github_datetime(pull_request.get("merged_at"))
    labels = {label.lower() for label in issue.labels}
    issue_text = f"{issue.title}\n{issue.body}".lower()
    checks = [
        CandidateAuditCheck(
            code="same_repository",
            passed=base_repository.lower() == repository.lower(),
            blocking=True,
            detail=f"PR base repository: {base_repository or 'missing'}",
        ),
        CandidateAuditCheck(
            code="merged_pull_request",
            passed=merged_at is not None,
            blocking=True,
            detail=f"merged_at={pull_request.get('merged_at') or 'missing'}",
        ),
        CandidateAuditCheck(
            code="issue_precedes_fix",
            passed=merged_at is not None and issue.created_at <= merged_at,
            blocking=True,
            detail=(
                f"issue_created_at={issue.created_at.isoformat()}, "
                f"pr_merged_at={merged_at.isoformat() if merged_at else 'missing'}"
            ),
        ),
        CandidateAuditCheck(
            code="pre_fix_sha_identified",
            passed=pre_fix_sha is not None,
            blocking=True,
            detail=pre_fix_detail,
        ),
        CandidateAuditCheck(
            code="pull_commit_history",
            passed=bool(pull_commits),
            blocking=True,
            detail=f"{len(pull_commits)} ordered PR commit(s) loaded",
        ),
        CandidateAuditCheck(
            code="pre_fix_outside_pull_commits",
            passed=pre_fix_sha is not None and pre_fix_sha not in pull_commit_shas,
            blocking=True,
            detail=(
                "The pre-fix SHA must be the parent of the first PR commit, "
                "not a commit inside the fix PR."
            ),
        ),
        CandidateAuditCheck(
            code="production_source_files",
            passed=bool(expected_files),
            blocking=True,
            detail=f"{len(expected_files)} eligible production source file(s)",
        ),
        CandidateAuditCheck(
            code="bounded_source_files",
            passed=1 <= len(expected_files) <= max_source_files,
            blocking=True,
            detail=f"{len(expected_files)} eligible file(s); maximum is {max_source_files}",
        ),
        CandidateAuditCheck(
            code="issue_has_body",
            passed=len(issue.body.strip()) >= 40,
            blocking=False,
            detail=f"{len(issue.body.strip())} body character(s)",
        ),
        CandidateAuditCheck(
            code="bug_signal",
            passed=any(term in label for term in BUG_TERMS for label in labels)
            or any(term in issue_text for term in BUG_TERMS),
            blocking=False,
            detail=f"labels={', '.join(issue.labels) or 'none'}",
        ),
        CandidateAuditCheck(
            code="diagnostic_signal",
            passed=any(term in issue_text for term in DIAGNOSTIC_TERMS)
            or (len(issue.body) >= 200 and "```" in issue.body),
            blocking=False,
            detail="Issue should contain reproduction, expected/actual behavior, or an error.",
        ),
        CandidateAuditCheck(
            code="closing_reference",
            passed=_has_closing_reference(
                (
                    f"{pull_request.get('title') or ''}\n"
                    f"{pull_request.get('body') or ''}"
                ),
                issue.number,
            ),
            blocking=False,
            detail="PR body should explicitly close, fix, or resolve the Issue.",
        ),
    ]
    rejected = any(not check.passed for check in checks if check.blocking)
    notes = [
        check.detail
        for check in checks
        if not check.passed and not check.blocking
    ]
    return BenchmarkCandidate(
        id=(
            f"{repository.replace('/', '-').lower()}-issue-{issue.number}"
            f"-pr-{pull_number}"
        ),
        repository=repository,
        suggested_tier=suggested_tier,
        issue_snapshot=issue,
        fix_pr_number=pull_number,
        fix_pr_title=str(pull_request.get("title") or ""),
        fix_pr_url=str(
            pull_request.get("html_url")
            or f"https://github.com/{repository}/pull/{pull_number}"
        ),
        fix_pr_merged_at=pull_request.get("merged_at"),
        merge_commit_sha=pull_request.get("merge_commit_sha"),
        pre_fix_sha=pre_fix_sha,
        pre_fix_sha_source=pre_fix_source,
        expected_files=expected_files,
        changed_files=changed_files,
        audit_checks=checks,
        status=CandidateStatus.REJECTED if rejected else CandidateStatus.NEEDS_REVIEW,
        review_notes=notes,
    )


def inspect_candidate(
    client: DiscoveryGitHubClient,
    repository: str,
    issue_number: int,
    pull_number: int,
    *,
    max_source_files: int = 5,
    suggested_tier: BenchmarkTier | None = None,
) -> BenchmarkCandidate:
    if REPOSITORY_PATTERN.fullmatch(repository) is None:
        raise ValueError("repository must use the owner/name format")
    if issue_number < 1:
        raise ValueError("issue_number must be at least 1")
    if pull_number < 1:
        raise ValueError("pull_number must be at least 1")
    if max_source_files < 1:
        raise ValueError("max_source_files must be at least 1")
    issue = client.fetch_issue(repository, issue_number)
    pull_request = client.fetch_pull_request(repository, pull_number)
    base_repository = str(
        (((pull_request.get("base") or {}).get("repo") or {}).get("full_name")) or ""
    )
    pull_files: list[dict] = []
    merge_commit = None
    pull_commits: list[dict] = []
    if pull_request.get("merged_at") and base_repository.lower() == repository.lower():
        pull_files = client.fetch_pull_request_files(repository, pull_number)
        merge_commit_sha = pull_request.get("merge_commit_sha")
        if merge_commit_sha:
            merge_commit = client.fetch_commit(repository, str(merge_commit_sha))
        pull_commits = client.fetch_pull_request_commits(repository, pull_number)
    return audit_candidate(
        repository,
        issue,
        pull_request,
        pull_files,
        merge_commit,
        pull_commits,
        max_source_files=max_source_files,
        suggested_tier=suggested_tier,
    )


def discover_candidates(
    client: DiscoveryGitHubClient,
    repositories: Sequence[str],
    *,
    target_per_repository: int = 5,
    scan_limit_per_repository: int = 50,
    max_source_files: int = 5,
    suggested_tiers: dict[str, BenchmarkTier] | None = None,
) -> CandidateCatalog:
    if target_per_repository < 1:
        raise ValueError("target_per_repository must be at least 1")
    if scan_limit_per_repository < target_per_repository:
        raise ValueError("scan_limit_per_repository must be at least target_per_repository")
    if max_source_files < 1:
        raise ValueError("max_source_files must be at least 1")
    normalized_repositories = list(dict.fromkeys(repositories))
    if not normalized_repositories:
        raise ValueError("At least one repository is required")
    for repository in normalized_repositories:
        if REPOSITORY_PATTERN.fullmatch(repository) is None:
            raise ValueError("repository must use the owner/name format")

    candidates: list[BenchmarkCandidate] = []
    for repository in normalized_repositories:
        accepted_for_review = 0
        reviewable_pull_numbers: set[int] = set()
        issues = client.search_closed_linked_issues(
            repository,
            limit=scan_limit_per_repository,
        )
        for search_issue in issues:
            timeline = client.fetch_issue_timeline(repository, search_issue.number)
            pull_numbers = linked_pull_request_numbers(timeline, repository)
            if not pull_numbers:
                continue
            issue = client.fetch_issue(repository, search_issue.number)
            for pull_number in pull_numbers:
                pull_request = client.fetch_pull_request(repository, pull_number)
                base_repository = str(
                    (
                        ((pull_request.get("base") or {}).get("repo") or {}).get(
                            "full_name"
                        )
                    )
                    or ""
                )
                if (
                    not pull_request.get("merged_at")
                    or base_repository.lower() != repository.lower()
                ):
                    candidates.append(
                        audit_candidate(
                            repository,
                            issue,
                            pull_request,
                            [],
                            None,
                            [],
                            max_source_files=max_source_files,
                            suggested_tier=(suggested_tiers or {}).get(repository),
                        )
                    )
                    continue
                pull_files = client.fetch_pull_request_files(repository, pull_number)
                merge_commit_sha = pull_request.get("merge_commit_sha")
                merge_commit = (
                    client.fetch_commit(repository, str(merge_commit_sha))
                    if merge_commit_sha
                    else None
                )
                pull_commits = client.fetch_pull_request_commits(
                    repository,
                    pull_number,
                )
                candidate = audit_candidate(
                    repository,
                    issue,
                    pull_request,
                    pull_files,
                    merge_commit,
                    pull_commits,
                    max_source_files=max_source_files,
                    suggested_tier=(suggested_tiers or {}).get(repository),
                )
                if candidate.status is CandidateStatus.NEEDS_REVIEW:
                    if pull_number in reviewable_pull_numbers:
                        detail = (
                            f"PR #{pull_number} already has another reviewable Issue; "
                            "select only one case during manual audit."
                        )
                        candidate.audit_checks.append(
                            CandidateAuditCheck(
                                code="unique_fix_pr",
                                passed=False,
                                blocking=False,
                                detail=detail,
                            )
                        )
                        candidate.review_notes.append(detail)
                    else:
                        reviewable_pull_numbers.add(pull_number)
                        accepted_for_review += 1
                candidates.append(candidate)
                if accepted_for_review >= target_per_repository:
                    break
            if accepted_for_review >= target_per_repository:
                break

    candidates.sort(
        key=lambda candidate: (
            candidate.repository.lower(),
            candidate.issue_snapshot.number,
            candidate.fix_pr_number,
        )
    )
    return CandidateCatalog(
        name="real-project-benchmark-candidates",
        version=1,
        generated_at=datetime.now(UTC),
        repositories=normalized_repositories,
        search_query="is:issue is:closed linked:pr sort:updated-desc",
        target_per_repository=target_per_repository,
        scan_limit_per_repository=scan_limit_per_repository,
        max_source_files=max_source_files,
        candidates=candidates,
    )


def _advisory_score(candidate: BenchmarkCandidate) -> int:
    return sum(
        ADVISORY_CHECK_WEIGHTS.get(check.code, 0)
        for check in candidate.audit_checks
        if not check.blocking and check.passed
    )


def _candidate_review_rank(candidate: BenchmarkCandidate) -> tuple:
    passed_codes = {
        check.code
        for check in candidate.audit_checks
        if not check.blocking and check.passed
    }
    return (
        -_advisory_score(candidate),
        "closing_reference" not in passed_codes,
        "diagnostic_signal" not in passed_codes,
        len(candidate.expected_files) == 1,
        candidate.issue_snapshot.created_at.isoformat(),
        candidate.repository.lower(),
        candidate.issue_snapshot.number,
        candidate.fix_pr_number,
    )


def _unique_reviewable_candidates(
    base_manifest: BenchmarkManifest,
    candidates: Sequence[BenchmarkCandidate],
) -> tuple[int, list[BenchmarkCandidate]]:
    existing_issue_keys = {
        (case.repository.lower(), case.issue_number) for case in base_manifest.cases
    }
    existing_pr_keys = {
        (case.repository.lower(), case.fix_pr_number) for case in base_manifest.cases
    }
    reviewable = [
        candidate
        for candidate in candidates
        if candidate.status is CandidateStatus.NEEDS_REVIEW
        and candidate.pre_fix_sha
        and candidate.expected_files
        and all(check.passed for check in candidate.audit_checks if check.blocking)
    ]
    unique: list[BenchmarkCandidate] = []
    used_issue_keys = set(existing_issue_keys)
    used_pr_keys = set(existing_pr_keys)
    for candidate in sorted(reviewable, key=_candidate_review_rank):
        issue_key = (candidate.repository.lower(), candidate.issue_snapshot.number)
        pr_key = (candidate.repository.lower(), candidate.fix_pr_number)
        if issue_key in used_issue_keys or pr_key in used_pr_keys:
            continue
        used_issue_keys.add(issue_key)
        used_pr_keys.add(pr_key)
        unique.append(candidate)
    return len(reviewable), unique


def _suggested_case_id(candidate: BenchmarkCandidate) -> str:
    repository = re.sub(r"[^a-z0-9]+", "-", candidate.repository.lower()).strip("-")
    return f"{repository}-issue-{candidate.issue_snapshot.number}"


def _review_queue_entry(
    candidate: BenchmarkCandidate,
    priority: CandidateReviewPriority,
    default_tier: BenchmarkTier,
    review_order: int,
) -> CandidateReviewQueueEntry:
    advisory_checks = [
        check for check in candidate.audit_checks if not check.blocking
    ]
    return CandidateReviewQueueEntry(
        review_order=review_order,
        candidate_id=candidate.id,
        priority=priority,
        repository=candidate.repository,
        issue_number=candidate.issue_snapshot.number,
        fix_pr_number=candidate.fix_pr_number,
        issue_title=candidate.issue_snapshot.title,
        issue_created_at=candidate.issue_snapshot.created_at,
        suggested_case_id=_suggested_case_id(candidate),
        suggested_tier=candidate.suggested_tier or default_tier,
        expected_files=candidate.expected_files,
        multi_file=len(candidate.expected_files) > 1,
        advisory_score=_advisory_score(candidate),
        passed_advisory_checks=sorted(
            check.code for check in advisory_checks if check.passed
        ),
        review_flags=sorted(
            f"failed:{check.code}" for check in advisory_checks if not check.passed
        ),
    )


def build_candidate_review_queue(
    base_manifest: BenchmarkManifest,
    candidates: Sequence[BenchmarkCandidate],
    *,
    target_total_cases: int = 200,
    reserve_cases: int = 30,
    max_primary_per_repository: int = 5,
    target_multi_file_share: float = 0.30,
    default_tier: BenchmarkTier = BenchmarkTier.GENERALIZATION,
) -> CandidateReviewQueue:
    """Prioritize candidates without accepting them as benchmark ground truth."""
    base_case_count = len(base_manifest.cases)
    if target_total_cases <= base_case_count:
        raise ValueError("target_total_cases must exceed the base manifest case count")
    if reserve_cases < 0:
        raise ValueError("reserve_cases must be non-negative")
    if max_primary_per_repository < 1:
        raise ValueError("max_primary_per_repository must be at least 1")
    if not 0 <= target_multi_file_share <= 1:
        raise ValueError("target_multi_file_share must be between 0 and 1")

    requested_new_cases = target_total_cases - base_case_count
    available_reviewable, unique = _unique_reviewable_candidates(
        base_manifest,
        candidates,
    )
    ranked = sorted(unique, key=_candidate_review_rank)
    if len(ranked) < requested_new_cases:
        raise ValueError(
            f"Only {len(ranked)} unique reviewable candidates are available; "
            f"{requested_new_cases} are required"
        )

    existing_multi_file_cases = sum(
        len(case.expected_files) > 1 for case in base_manifest.cases
    )
    target_multi_file_cases = math.ceil(target_total_cases * target_multi_file_share)
    required_new_multi_file_cases = max(
        0,
        target_multi_file_cases - existing_multi_file_cases,
    )
    unique_multi_file_cases = sum(len(candidate.expected_files) > 1 for candidate in ranked)
    if unique_multi_file_cases < required_new_multi_file_cases:
        raise ValueError(
            f"Only {unique_multi_file_cases} unique multi-file candidates are available; "
            f"{required_new_multi_file_cases} are required"
        )

    selected: list[BenchmarkCandidate] = []
    selected_ids: set[str] = set()
    repository_counts: Counter[str] = Counter()

    def select(candidate: BenchmarkCandidate) -> bool:
        repository = candidate.repository.lower()
        if (
            candidate.id in selected_ids
            or repository_counts[repository] >= max_primary_per_repository
            or len(selected) >= requested_new_cases
        ):
            return False
        selected.append(candidate)
        selected_ids.add(candidate.id)
        repository_counts[repository] += 1
        return True

    best_by_repository: dict[str, BenchmarkCandidate] = {}
    for candidate in ranked:
        best_by_repository.setdefault(candidate.repository.lower(), candidate)
    for candidate in sorted(best_by_repository.values(), key=_candidate_review_rank):
        select(candidate)

    selected_multi_file_cases = sum(
        len(candidate.expected_files) > 1 for candidate in selected
    )
    for candidate in ranked:
        if selected_multi_file_cases >= required_new_multi_file_cases:
            break
        if len(candidate.expected_files) > 1 and select(candidate):
            selected_multi_file_cases += 1

    for candidate in ranked:
        select(candidate)

    if len(selected) < requested_new_cases:
        raise ValueError(
            f"The per-repository cap permits only {len(selected)} primary candidates; "
            f"{requested_new_cases} are required"
        )
    selected_multi_file_cases = sum(
        len(candidate.expected_files) > 1 for candidate in selected
    )
    if selected_multi_file_cases < required_new_multi_file_cases:
        raise ValueError(
            f"The primary queue contains {selected_multi_file_cases} multi-file cases; "
            f"{required_new_multi_file_cases} are required"
        )

    remaining = [candidate for candidate in ranked if candidate.id not in selected_ids]
    reserves = remaining[:reserve_cases]
    selected.sort(key=_candidate_review_rank)
    ordered_candidates = [
        *((candidate, CandidateReviewPriority.PRIMARY) for candidate in selected),
        *((candidate, CandidateReviewPriority.RESERVE) for candidate in reserves),
    ]
    entries = [
        _review_queue_entry(candidate, priority, default_tier, review_order)
        for review_order, (candidate, priority) in enumerate(
            ordered_candidates,
            start=1,
        )
    ]
    return CandidateReviewQueue(
        name="real-project-benchmark-v200-review-queue",
        version=1,
        generated_at=datetime.now(UTC),
        base_manifest_name=base_manifest.name,
        base_manifest_version=base_manifest.version,
        target_total_cases=target_total_cases,
        base_case_count=base_case_count,
        requested_new_cases=requested_new_cases,
        reserve_cases=len(reserves),
        max_primary_per_repository=max_primary_per_repository,
        target_multi_file_share=target_multi_file_share,
        required_new_multi_file_cases=required_new_multi_file_cases,
        available_reviewable_records=available_reviewable,
        available_unique_cases=len(ranked),
        available_unique_multi_file_cases=unique_multi_file_cases,
        primary_multi_file_cases=selected_multi_file_cases,
        primary_repositories=len(repository_counts),
        entries=entries,
    )


def save_candidate_catalog(catalog: CandidateCatalog, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(catalog.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def save_candidate_review_queue(queue: CandidateReviewQueue, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(queue.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def save_benchmark_candidate(candidate: BenchmarkCandidate, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(candidate.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def load_candidate_sources(paths: Sequence[Path]) -> list[BenchmarkCandidate]:
    candidates: list[BenchmarkCandidate] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Candidate source must be a JSON object: {path}")
        if "candidates" in payload:
            catalog = (
                CuratedCandidateCatalog.model_validate(payload)
                if "curated_at" in payload
                else CandidateCatalog.model_validate(payload)
            )
            candidates.extend(catalog.candidates)
        else:
            candidates.append(BenchmarkCandidate.model_validate(payload))
    return candidates


def load_candidate_selection(path: Path) -> CandidateSelectionManifest:
    return CandidateSelectionManifest.model_validate_json(path.read_text(encoding="utf-8"))


def curate_benchmark_expansion(
    base_manifest: BenchmarkManifest,
    candidates: Sequence[BenchmarkCandidate],
    selection: CandidateSelectionManifest,
) -> tuple[CuratedCandidateCatalog, BenchmarkManifest]:
    candidates_by_id = {candidate.id: candidate for candidate in candidates}
    if len(candidates_by_id) != len(candidates):
        raise ValueError("Candidate sources contain duplicate candidate IDs")
    selected_ids = [entry.candidate_id for entry in selection.selections]
    if len(selected_ids) != len(set(selected_ids)):
        raise ValueError("Selection contains duplicate candidate IDs")
    case_ids = [entry.case_id for entry in selection.selections]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("Selection contains duplicate benchmark case IDs")

    existing_case_ids = {case.id for case in base_manifest.cases}
    existing_issue_keys = {
        (case.repository.lower(), case.issue_number) for case in base_manifest.cases
    }
    existing_pr_keys = {
        (case.repository.lower(), case.fix_pr_number) for case in base_manifest.cases
    }
    curated_candidates: list[BenchmarkCandidate] = []
    new_cases: list[BenchmarkCase] = []
    for entry in selection.selections:
        candidate = candidates_by_id.get(entry.candidate_id)
        if candidate is None:
            raise ValueError(f"Unknown candidate ID: {entry.candidate_id}")
        failed_blocking = [
            check.code
            for check in candidate.audit_checks
            if check.blocking and not check.passed
        ]
        if failed_blocking:
            raise ValueError(
                f"Candidate {candidate.id} has blocking failures: "
                + ", ".join(failed_blocking)
            )
        if candidate.pre_fix_sha is None or not candidate.expected_files:
            raise ValueError(f"Candidate {candidate.id} is missing benchmark ground truth")
        tier = entry.tier or candidate.suggested_tier
        if tier is None:
            raise ValueError(f"Candidate {candidate.id} requires a benchmark tier")
        issue_key = (candidate.repository.lower(), candidate.issue_snapshot.number)
        pr_key = (candidate.repository.lower(), candidate.fix_pr_number)
        if entry.case_id in existing_case_ids:
            raise ValueError(f"Benchmark case ID already exists: {entry.case_id}")
        if issue_key in existing_issue_keys:
            raise ValueError(f"Benchmark Issue already exists: {candidate.id}")
        if pr_key in existing_pr_keys:
            raise ValueError(f"Benchmark fix PR already exists: {candidate.id}")
        existing_case_ids.add(entry.case_id)
        existing_issue_keys.add(issue_key)
        existing_pr_keys.add(pr_key)
        curated_candidates.append(
            candidate.model_copy(
                update={
                    "suggested_tier": tier,
                    "status": CandidateStatus.ACCEPTED,
                    "review_notes": entry.review_notes,
                },
                deep=True,
            )
        )
        new_cases.append(
            BenchmarkCase(
                id=entry.case_id,
                tier=tier,
                repository=candidate.repository,
                issue_number=candidate.issue_snapshot.number,
                issue_updated_at=candidate.issue_snapshot.updated_at,
                issue_snapshot=candidate.issue_snapshot,
                fix_pr_number=candidate.fix_pr_number,
                pre_fix_sha=candidate.pre_fix_sha,
                expected_files=candidate.expected_files,
            )
        )

    curated_catalog = CuratedCandidateCatalog(
        name=selection.name,
        version=selection.version,
        curated_at=datetime.now(UTC),
        base_manifest_name=base_manifest.name,
        base_manifest_version=base_manifest.version,
        candidates=curated_candidates,
    )
    expanded_manifest = BenchmarkManifest(
        name=selection.manifest_name or base_manifest.name,
        version=base_manifest.version + 1,
        cases=[*base_manifest.cases, *new_cases],
    )
    return curated_catalog, expanded_manifest


def save_curated_expansion(
    catalog: CuratedCandidateCatalog,
    manifest: BenchmarkManifest,
    *,
    catalog_output: Path,
    manifest_output: Path,
) -> None:
    catalog_output.parent.mkdir(parents=True, exist_ok=True)
    manifest_output.parent.mkdir(parents=True, exist_ok=True)
    catalog_output.write_text(
        json.dumps(catalog.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    manifest_output.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
