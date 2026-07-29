from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

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


class CandidateStatus(StrEnum):
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"
    ACCEPTED = "accepted"


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
        return first_parent, "merge_first_parent", "Derived from a merge commit first parent."
    if commit_count == 1:
        return first_parent, "single_commit_parent", "Derived from a single-commit fix."
    if merge_sha and head_sha and merge_sha != head_sha:
        return first_parent, "squash_commit_parent", "Derived from a squash commit parent."
    if pull_commits:
        first_commit_parents = pull_commits[0].get("parents") or []
        if first_commit_parents and first_commit_parents[0].get("sha"):
            return (
                str(first_commit_parents[0]["sha"]),
                "first_pull_commit_parent",
                "Derived from the first PR commit; verify a multi-commit rebase manually.",
            )
    return (
        first_parent,
        "ambiguous_single_parent",
        "A multi-commit, single-parent merge requires manual pre-fix SHA verification.",
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
    if pre_fix_source in {"first_pull_commit_parent", "ambiguous_single_parent"}:
        notes.append(pre_fix_detail)
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
        if int(pull_request.get("commits") or 0) > 1:
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
                pull_commits: list[dict] = []
                if int(pull_request.get("commits") or 0) > 1:
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


def save_candidate_catalog(catalog: CandidateCatalog, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(catalog.model_dump(mode="json"), indent=2, ensure_ascii=False),
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
