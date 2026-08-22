from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from .benchmark import (
    BenchmarkCase,
    BenchmarkManifest,
    BenchmarkSymbolTarget,
    BenchmarkTier,
)
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
REQUIRED_BLOCKING_CHECK_CODES = frozenset(
    {
        "same_repository",
        "merged_pull_request",
        "issue_precedes_fix",
        "pre_fix_sha_identified",
        "pull_commit_history",
        "pre_fix_outside_pull_commits",
        "production_source_files",
        "bounded_source_files",
    }
)


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
    expected_files: list[str] | None = Field(default=None, min_length=1)
    expected_symbols: list[BenchmarkSymbolTarget] = Field(default_factory=list)
    review_notes: list[str] = Field(min_length=1)


class CandidateRejectionEntry(BaseModel):
    candidate_id: str
    reason: str = Field(min_length=1)


class CandidateSelectionManifest(BaseModel):
    name: str
    version: int = Field(ge=1)
    manifest_name: str | None = None
    selections: list[CandidateSelectionEntry] = Field(default_factory=list)
    rejections: list[CandidateRejectionEntry] = Field(default_factory=list)


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
    pre_fix_sha: str = Field(min_length=1)
    pre_fix_sha_source: str | None = None
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


def _blocking_check_failures(candidate: BenchmarkCandidate) -> list[str]:
    checks_by_code: dict[str, list[CandidateAuditCheck]] = defaultdict(list)
    for check in candidate.audit_checks:
        checks_by_code[check.code].append(check)

    failures: list[str] = []
    for code in sorted(REQUIRED_BLOCKING_CHECK_CODES):
        checks = checks_by_code.get(code, [])
        if len(checks) != 1:
            failures.append(code)
        elif not checks[0].blocking or not checks[0].passed:
            failures.append(code)
    failures.extend(
        check.code
        for check in candidate.audit_checks
        if check.blocking and not check.passed
    )
    return sorted(set(failures))


def _maximum_issue_pr_matching(
    candidates: Sequence[BenchmarkCandidate],
) -> list[BenchmarkCandidate]:
    candidates_by_issue: dict[tuple[str, int], list[BenchmarkCandidate]] = defaultdict(
        list
    )
    for candidate in sorted(candidates, key=_candidate_review_rank):
        issue_key = (candidate.repository.lower(), candidate.issue_snapshot.number)
        candidates_by_issue[issue_key].append(candidate)

    issue_order = sorted(
        candidates_by_issue,
        key=lambda issue_key: (
            _candidate_review_rank(candidates_by_issue[issue_key][0]),
            issue_key,
        ),
    )
    matched_by_pr: dict[tuple[str, int], BenchmarkCandidate] = {}

    def augment(
        issue_key: tuple[str, int],
        visited_prs: set[tuple[str, int]],
    ) -> bool:
        for candidate in candidates_by_issue[issue_key]:
            pr_key = (candidate.repository.lower(), candidate.fix_pr_number)
            if pr_key in visited_prs:
                continue
            visited_prs.add(pr_key)
            previous = matched_by_pr.get(pr_key)
            if previous is None:
                matched_by_pr[pr_key] = candidate
                return True
            previous_issue_key = (
                previous.repository.lower(),
                previous.issue_snapshot.number,
            )
            if augment(previous_issue_key, visited_prs):
                matched_by_pr[pr_key] = candidate
                return True
        return False

    for issue_key in issue_order:
        augment(issue_key, set())

    return sorted(matched_by_pr.values(), key=_candidate_review_rank)


def _reviewable_candidate_edges(
    base_manifest: BenchmarkManifest,
    candidates: Sequence[BenchmarkCandidate],
    excluded_candidate_ids: AbstractSet[str],
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
        and candidate.id not in excluded_candidate_ids
        and candidate.pre_fix_sha
        and candidate.expected_files
        and not _blocking_check_failures(candidate)
    ]

    edges: list[BenchmarkCandidate] = []
    seen_edges: set[tuple[tuple[str, int], tuple[str, int]]] = set()
    for candidate in sorted(reviewable, key=_candidate_review_rank):
        issue_key = (candidate.repository.lower(), candidate.issue_snapshot.number)
        pr_key = (candidate.repository.lower(), candidate.fix_pr_number)
        edge = (issue_key, pr_key)
        if (
            issue_key in existing_issue_keys
            or pr_key in existing_pr_keys
            or edge in seen_edges
        ):
            continue
        seen_edges.add(edge)
        edges.append(candidate)
    return len(reviewable), edges


def _suggested_case_id(candidate: BenchmarkCandidate) -> str:
    repository = re.sub(r"[^a-z0-9]+", "-", candidate.repository.lower()).strip("-")
    return f"{repository}-issue-{candidate.issue_snapshot.number}"


def _review_queue_entry(
    candidate: BenchmarkCandidate,
    priority: CandidateReviewPriority,
    default_tier: BenchmarkTier,
    review_order: int,
) -> CandidateReviewQueueEntry:
    if candidate.pre_fix_sha is None:
        raise ValueError(f"Candidate {candidate.id} is missing a pre-fix SHA")
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
        pre_fix_sha=candidate.pre_fix_sha,
        pre_fix_sha_source=candidate.pre_fix_sha_source,
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


def _repository_primary_options(
    candidates: Sequence[BenchmarkCandidate],
    rank_by_id: dict[str, int],
    max_primary_per_repository: int,
    reserve_cases: int,
) -> list[
    tuple[int, int, int, int, tuple[str, ...], tuple[BenchmarkCandidate, ...]]
]:
    best_options: dict[
        tuple[int, int, int],
        tuple[int, tuple[str, ...], tuple[BenchmarkCandidate, ...]],
    ] = {}

    def collect_options(
        start: int,
        chosen: tuple[BenchmarkCandidate, ...],
        used_issue_numbers: frozenset[int],
        used_pr_numbers: frozenset[int],
        multi_count: int,
        cost: int,
    ) -> None:
        remaining = [
            candidate
            for candidate in candidates
            if candidate.issue_snapshot.number not in used_issue_numbers
            and candidate.fix_pr_number not in used_pr_numbers
        ]
        reserve_capacity = min(
            reserve_cases,
            len(_maximum_issue_pr_matching(remaining)),
        )
        key = (len(chosen), multi_count, reserve_capacity)
        candidate_ids = tuple(candidate.id for candidate in chosen)
        option = (cost, candidate_ids, chosen)
        previous = best_options.get(key)
        if previous is None or option[:2] < previous[:2]:
            best_options[key] = option
        if len(chosen) >= max_primary_per_repository:
            return
        for index in range(start, len(candidates)):
            candidate = candidates[index]
            issue_number = candidate.issue_snapshot.number
            if (
                issue_number in used_issue_numbers
                or candidate.fix_pr_number in used_pr_numbers
            ):
                continue
            collect_options(
                index + 1,
                (*chosen, candidate),
                used_issue_numbers | {issue_number},
                used_pr_numbers | {candidate.fix_pr_number},
                multi_count + (len(candidate.expected_files) > 1),
                cost + rank_by_id[candidate.id],
            )

    collect_options(0, (), frozenset(), frozenset(), 0, 0)
    return [
        (count, multi_count, reserve_capacity, cost, candidate_ids, chosen)
        for (count, multi_count, reserve_capacity), (
            cost,
            candidate_ids,
            chosen,
        ) in best_options.items()
    ]


def _select_primary_candidates(
    ranked: Sequence[BenchmarkCandidate],
    *,
    requested_new_cases: int,
    required_new_multi_file_cases: int,
    max_primary_per_repository: int,
    reserve_cases: int,
) -> list[BenchmarkCandidate]:
    candidates_by_repository: dict[str, list[BenchmarkCandidate]] = defaultdict(list)
    for candidate in ranked:
        candidates_by_repository[candidate.repository.lower()].append(candidate)

    repository_keys = sorted(candidates_by_repository)
    require_repository_coverage = requested_new_cases >= len(repository_keys)
    rank_by_id = {candidate.id: index for index, candidate in enumerate(ranked)}

    # A state stores the best ranked primary tuple for a total count, a capped
    # multi-file count, and enough remaining matching capacity for reserves.
    states: dict[
        tuple[int, int, int],
        tuple[int, tuple[str, ...], tuple[BenchmarkCandidate, ...]],
    ] = {(0, 0, 0): (0, (), ())}
    for repository in repository_keys:
        repository_candidates = candidates_by_repository[repository]
        minimum = 1 if require_repository_coverage else 0
        options = [
            (count, multi_count, reserve_capacity, cost, candidate_ids, chosen)
            for (
                count,
                multi_count,
                reserve_capacity,
                cost,
                candidate_ids,
                chosen,
            ) in _repository_primary_options(
                repository_candidates,
                rank_by_id,
                max_primary_per_repository,
                reserve_cases,
            )
            if count >= minimum
        ]

        next_states: dict[
            tuple[int, int, int],
            tuple[int, tuple[str, ...], tuple[BenchmarkCandidate, ...]],
        ] = {}
        for (total_count, total_multi, total_reserve), state in states.items():
            state_cost, state_ids, state_candidates = state
            for (
                count,
                multi_count,
                reserve_capacity,
                cost,
                candidate_ids,
                chosen,
            ) in options:
                next_count = total_count + count
                if next_count > requested_new_cases:
                    continue
                next_multi = min(
                    required_new_multi_file_cases,
                    total_multi + multi_count,
                )
                next_reserve = min(
                    reserve_cases,
                    total_reserve + reserve_capacity,
                )
                next_ids = (*state_ids, *candidate_ids)
                next_state = (
                    state_cost + cost,
                    next_ids,
                    (*state_candidates, *chosen),
                )
                key = (next_count, next_multi, next_reserve)
                previous = next_states.get(key)
                if previous is None or next_state[:2] < previous[:2]:
                    next_states[key] = next_state
        states = next_states

    final = states.get(
        (requested_new_cases, required_new_multi_file_cases, reserve_cases)
    )
    if final is None:
        raise ValueError(
            "No feasible primary queue satisfies repository coverage, "
            "the per-repository cap, the multi-file quota, and reserve capacity"
        )
    return sorted(final[2], key=_candidate_review_rank)


def build_candidate_review_queue(
    base_manifest: BenchmarkManifest,
    candidates: Sequence[BenchmarkCandidate],
    *,
    target_total_cases: int = 200,
    reserve_cases: int = 30,
    max_primary_per_repository: int = 5,
    target_multi_file_share: float = 0.30,
    default_tier: BenchmarkTier = BenchmarkTier.GENERALIZATION,
    excluded_candidate_ids: AbstractSet[str] = frozenset(),
) -> CandidateReviewQueue:
    """Prioritize candidates without accepting them as benchmark ground truth."""
    unknown_excluded_ids = set(excluded_candidate_ids) - {
        candidate.id for candidate in candidates
    }
    if unknown_excluded_ids:
        raise ValueError(
            "Unknown excluded candidate IDs: "
            + ", ".join(sorted(unknown_excluded_ids))
        )
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
    available_reviewable, edges = _reviewable_candidate_edges(
        base_manifest,
        candidates,
        excluded_candidate_ids,
    )
    ranked = sorted(edges, key=_candidate_review_rank)
    maximum_matching = _maximum_issue_pr_matching(ranked)
    if len(maximum_matching) < requested_new_cases:
        raise ValueError(
            f"Only {len(maximum_matching)} unique reviewable candidates are available; "
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
    unique_multi_file_cases = len(
        _maximum_issue_pr_matching(
            [candidate for candidate in ranked if len(candidate.expected_files) > 1]
        )
    )
    if unique_multi_file_cases < required_new_multi_file_cases:
        raise ValueError(
            f"Only {unique_multi_file_cases} unique multi-file candidates are available; "
            f"{required_new_multi_file_cases} are required"
        )

    selected = _select_primary_candidates(
        ranked,
        requested_new_cases=requested_new_cases,
        required_new_multi_file_cases=required_new_multi_file_cases,
        max_primary_per_repository=max_primary_per_repository,
        reserve_cases=reserve_cases,
    )
    selected_ids = {candidate.id for candidate in selected}
    selected_multi_file_cases = sum(
        len(candidate.expected_files) > 1 for candidate in selected
    )

    selected_issue_keys = {
        (candidate.repository.lower(), candidate.issue_snapshot.number)
        for candidate in selected
    }
    selected_pr_keys = {
        (candidate.repository.lower(), candidate.fix_pr_number)
        for candidate in selected
    }
    remaining = [
        candidate
        for candidate in ranked
        if candidate.id not in selected_ids
        and (candidate.repository.lower(), candidate.issue_snapshot.number)
        not in selected_issue_keys
        and (candidate.repository.lower(), candidate.fix_pr_number)
        not in selected_pr_keys
    ]
    reserves = _maximum_issue_pr_matching(remaining)[:reserve_cases]
    if len(reserves) < reserve_cases:
        raise ValueError(
            f"Only {len(reserves)} unique reserve candidates remain; "
            f"{reserve_cases} are required"
        )
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
        name=f"{base_manifest.name}-{target_total_cases}-case-review-queue",
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
        available_unique_cases=len(maximum_matching),
        available_unique_multi_file_cases=unique_multi_file_cases,
        primary_multi_file_cases=selected_multi_file_cases,
        primary_repositories=len(
            {candidate.repository.lower() for candidate in selected}
        ),
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


def reviewed_rejection_ids(
    decisions: Sequence[CandidateSelectionManifest],
) -> frozenset[str]:
    rejected_ids = [
        rejection.candidate_id
        for decision in decisions
        for rejection in decision.rejections
    ]
    if len(rejected_ids) != len(set(rejected_ids)):
        raise ValueError("Review decisions contain duplicate rejected candidate IDs")
    selected_ids = {
        selection.candidate_id
        for decision in decisions
        for selection in decision.selections
    }
    overlapping_ids = selected_ids.intersection(rejected_ids)
    if overlapping_ids:
        raise ValueError(
            "Candidates cannot be both selected and rejected across review decisions: "
            + ", ".join(sorted(overlapping_ids))
        )
    return frozenset(rejected_ids)


def curate_benchmark_expansion(
    base_manifest: BenchmarkManifest,
    candidates: Sequence[BenchmarkCandidate],
    selection: CandidateSelectionManifest,
) -> tuple[CuratedCandidateCatalog, BenchmarkManifest]:
    if not selection.selections:
        raise ValueError("Curation requires at least one selected candidate")
    candidates_by_id = {candidate.id: candidate for candidate in candidates}
    if len(candidates_by_id) != len(candidates):
        raise ValueError("Candidate sources contain duplicate candidate IDs")
    selected_ids = [entry.candidate_id for entry in selection.selections]
    if len(selected_ids) != len(set(selected_ids)):
        raise ValueError("Selection contains duplicate candidate IDs")
    rejected_ids = [entry.candidate_id for entry in selection.rejections]
    if len(rejected_ids) != len(set(rejected_ids)):
        raise ValueError("Selection contains duplicate rejected candidate IDs")
    overlapping_ids = set(selected_ids).intersection(rejected_ids)
    if overlapping_ids:
        raise ValueError(
            "Candidates cannot be both selected and rejected: "
            + ", ".join(sorted(overlapping_ids))
        )
    unknown_rejected_ids = set(rejected_ids) - set(candidates_by_id)
    if unknown_rejected_ids:
        raise ValueError(
            "Unknown rejected candidate IDs: "
            + ", ".join(sorted(unknown_rejected_ids))
        )
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
        failed_blocking = _blocking_check_failures(candidate)
        if failed_blocking:
            raise ValueError(
                f"Candidate {candidate.id} is missing or failed required blocking checks: "
                + ", ".join(failed_blocking)
            )
        if candidate.pre_fix_sha is None or not candidate.expected_files:
            raise ValueError(f"Candidate {candidate.id} is missing benchmark ground truth")
        expected_files = entry.expected_files or candidate.expected_files
        if len(expected_files) != len(set(expected_files)):
            raise ValueError(
                f"Selection for {candidate.id} contains duplicate expected files"
            )
        unknown_expected_files = set(expected_files) - set(candidate.expected_files)
        if unknown_expected_files:
            raise ValueError(
                f"Selection for {candidate.id} contains files outside the audited patch: "
                + ", ".join(sorted(unknown_expected_files))
            )
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
                    "expected_files": expected_files,
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
                expected_files=expected_files,
                expected_symbols=entry.expected_symbols,
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
