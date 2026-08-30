from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from .benchmark import (
    HYBRID_CANDIDATE_POOL_LIMIT,
    REPOSITORY_MAP_CACHE_DIRECTORY,
    BenchmarkCase,
    BenchmarkManifest,
    _load_or_build_repository_map,
    prepare_repository,
    tracked_repository_files,
)
from .investigator import investigate
from .repository_index import REPOSITORY_MAP_INDEX_VERSION

WIDE_CANDIDATE_LIMIT = 2_000


class CandidatePoolMiss(BaseModel):
    case_id: str
    repository: str
    file: str
    language: str
    wide_candidate_rank: int | None = None
    evidence: list[str] = Field(default_factory=list)


class CandidatePoolMissAudit(BaseModel):
    protocol: str
    manifest_name: str
    manifest_version: int
    repository_map_index_version: int
    candidate_pool_limit: int
    wide_candidate_limit: int
    created_at: datetime
    cases: int
    repositories: int
    production_targets: int
    candidate_pool_matched_targets: int
    candidate_pool_missing_targets: int
    repository_map_cache_hits: int
    repository_map_cache_misses: int
    language_counts: dict[str, int]
    repository_counts: dict[str, int]
    wide_candidate_rank_buckets: dict[str, int]
    targets: list[CandidatePoolMiss]


def _wide_rank_bucket(rank: int | None) -> str:
    if rank is None:
        return "not_retrieved"
    if rank <= 60:
        return "41-60"
    if rank <= 100:
        return "61-100"
    if rank <= 200:
        return "101-200"
    return "201+"


def _case_pool_misses(
    case: BenchmarkCase,
    repository_map,
) -> list[CandidatePoolMiss]:
    pool = investigate(
        case.issue_snapshot,
        repository_map,
        candidate_limit=HYBRID_CANDIDATE_POOL_LIMIT,
    ).candidates
    pool_paths = {candidate.file for candidate in pool}
    missing_paths = [path for path in case.expected_files if path not in pool_paths]
    if not missing_paths:
        return []

    wide = investigate(
        case.issue_snapshot,
        repository_map,
        candidate_limit=WIDE_CANDIDATE_LIMIT,
    ).candidates
    wide_by_path = {
        candidate.file: (rank, candidate)
        for rank, candidate in enumerate(wide, start=1)
    }
    language_by_path = {file.path: file.language for file in repository_map.files}
    return [
        CandidatePoolMiss(
            case_id=case.id,
            repository=case.repository,
            file=path,
            language=language_by_path.get(path, "Unknown"),
            wide_candidate_rank=(
                wide_by_path[path][0] if path in wide_by_path else None
            ),
            evidence=(
                wide_by_path[path][1].evidence if path in wide_by_path else []
            ),
        )
        for path in missing_paths
    ]


def audit_candidate_pool(
    manifest: BenchmarkManifest,
    workspace: Path,
) -> CandidatePoolMissAudit:
    workspace = workspace.expanduser().resolve()
    cache_root = workspace / REPOSITORY_MAP_CACHE_DIRECTORY
    misses: list[CandidatePoolMiss] = []
    cache_hits = 0
    cache_misses = 0
    for case in manifest.cases:
        repository_root = prepare_repository(case, workspace)
        repository_map, cache_hit = _load_or_build_repository_map(
            case,
            repository_root,
            tracked_repository_files(repository_root),
            cache_root,
        )
        cache_hits += cache_hit
        cache_misses += not cache_hit
        misses.extend(_case_pool_misses(case, repository_map))

    production_targets = sum(len(case.expected_files) for case in manifest.cases)
    languages = Counter(target.language for target in misses)
    repositories = Counter(target.repository for target in misses)
    buckets = Counter(_wide_rank_bucket(target.wide_candidate_rank) for target in misses)
    return CandidatePoolMissAudit(
        protocol="candidate-pool-miss-audit-v1",
        manifest_name=manifest.name,
        manifest_version=manifest.version,
        repository_map_index_version=REPOSITORY_MAP_INDEX_VERSION,
        candidate_pool_limit=HYBRID_CANDIDATE_POOL_LIMIT,
        wide_candidate_limit=WIDE_CANDIDATE_LIMIT,
        created_at=datetime.now(UTC),
        cases=len(manifest.cases),
        repositories=len({case.repository for case in manifest.cases}),
        production_targets=production_targets,
        candidate_pool_matched_targets=production_targets - len(misses),
        candidate_pool_missing_targets=len(misses),
        repository_map_cache_hits=cache_hits,
        repository_map_cache_misses=cache_misses,
        language_counts=dict(sorted(languages.items())),
        repository_counts=dict(sorted(repositories.items())),
        wide_candidate_rank_buckets={
            bucket: buckets.get(bucket, 0)
            for bucket in ("41-60", "61-100", "101-200", "201+", "not_retrieved")
        },
        targets=misses,
    )


def save_candidate_pool_audit(audit: CandidatePoolMissAudit, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(audit.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
