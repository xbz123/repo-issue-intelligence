from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import repo_issue_intelligence.benchmark_analysis as analysis_module
from repo_issue_intelligence.benchmark import BenchmarkCase, BenchmarkManifest, BenchmarkTier
from repo_issue_intelligence.benchmark_analysis import (
    CandidatePoolMissAudit,
    audit_candidate_pool,
    save_candidate_pool_audit,
)
from repo_issue_intelligence.models import CandidateLocation, FileRecord, IssueRecord, RepositoryMap


def _manifest() -> BenchmarkManifest:
    timestamp = datetime(2026, 8, 31, tzinfo=UTC)
    issue = IssueRecord(
        number=1,
        title="Parser loses a target",
        body="The parser should update the missing target.",
        created_at=timestamp,
        updated_at=timestamp,
    )
    return BenchmarkManifest(
        name="candidate-pool-audit-test",
        version=1,
        cases=[
            BenchmarkCase(
                id="parser-target",
                tier=BenchmarkTier.GENERALIZATION,
                repository="example/project",
                issue_number=1,
                issue_updated_at=timestamp,
                issue_snapshot=issue,
                fix_pr_number=2,
                pre_fix_sha="a" * 40,
                expected_files=["hit.py", "miss.py"],
            )
        ],
    )


def _candidate(path: str, evidence: list[str] | None = None) -> CandidateLocation:
    return CandidateLocation(
        file=path,
        confidence=0.5,
        evidence=evidence or ["lexical match"],
    )


def test_candidate_pool_audit_records_only_targets_outside_top40(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository_map = RepositoryMap(
        root=str(tmp_path),
        languages={"Python": 2},
        frameworks=[],
        entrypoints=[],
        test_directories=[],
        runtime_files=[],
        files=[
            FileRecord(path="hit.py", language="Python"),
            FileRecord(path="miss.py", language="Python"),
        ],
    )
    base = [
        _candidate("hit.py"),
        *[_candidate(f"base-decoy-{index}.py") for index in range(19)],
    ]
    expanded = [_candidate(f"wide-decoy-{index}.py") for index in range(40)]
    wide = [*expanded, _candidate("miss.py", ["resolved call evidence"])]

    def fake_investigate(issue, repository_map, candidate_limit=20):
        if candidate_limit == 20:
            return SimpleNamespace(candidates=base)
        if candidate_limit == 40:
            return SimpleNamespace(candidates=expanded)
        return SimpleNamespace(candidates=wide)

    monkeypatch.setattr(analysis_module, "investigate", fake_investigate)
    monkeypatch.setattr(
        analysis_module,
        "prepare_repository",
        lambda case, workspace: tmp_path,
    )
    monkeypatch.setattr(
        analysis_module,
        "tracked_repository_files",
        lambda root: ["hit.py", "miss.py"],
    )
    monkeypatch.setattr(
        analysis_module,
        "_load_or_build_repository_map",
        lambda case, root, files, cache: (repository_map, True),
    )

    audit = audit_candidate_pool(_manifest(), tmp_path / "workspace")

    assert audit.production_targets == 2
    assert audit.candidate_pool_matched_targets == 1
    assert audit.candidate_pool_missing_targets == 1
    assert audit.repository_map_cache_hits == 1
    assert audit.repository_map_cache_misses == 0
    assert audit.language_counts == {"Python": 1}
    assert audit.repository_counts == {"example/project": 1}
    assert audit.wide_candidate_rank_buckets == {
        "1-40-displaced": 0,
        "41-60": 1,
        "61-100": 0,
        "101-200": 0,
        "201+": 0,
        "not_retrieved": 0,
    }
    assert audit.targets[0].file == "miss.py"
    assert audit.targets[0].wide_candidate_rank == 41
    assert audit.targets[0].evidence == ["resolved call evidence"]

    output = tmp_path / "audit.json"
    save_candidate_pool_audit(audit, output)
    assert CandidatePoolMissAudit.model_validate_json(
        output.read_text(encoding="utf-8")
    ) == audit


def test_candidate_pool_audit_separates_displaced_top40_target(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository_map = RepositoryMap(
        root=str(tmp_path),
        languages={"Python": 2},
        frameworks=[],
        entrypoints=[],
        test_directories=[],
        runtime_files=[],
        files=[
            FileRecord(path="hit.py", language="Python"),
            FileRecord(path="miss.py", language="Python"),
        ],
    )
    base = [
        _candidate("hit.py"),
        *[_candidate(f"base-decoy-{index}.py") for index in range(19)],
    ]
    expanded = [_candidate(f"wide-decoy-{index}.py") for index in range(40)]
    wide = [
        *[_candidate(f"wide-decoy-{index}.py") for index in range(20)],
        _candidate("miss.py", ["displaced target evidence"]),
        *[_candidate(f"wide-decoy-{index}.py") for index in range(20, 40)],
    ]

    def fake_investigate(issue, repository_map, candidate_limit=20):
        if candidate_limit == 20:
            return SimpleNamespace(candidates=base)
        if candidate_limit == 40:
            return SimpleNamespace(candidates=expanded)
        return SimpleNamespace(candidates=wide)

    monkeypatch.setattr(analysis_module, "investigate", fake_investigate)
    monkeypatch.setattr(
        analysis_module,
        "prepare_repository",
        lambda case, workspace: tmp_path,
    )
    monkeypatch.setattr(
        analysis_module,
        "tracked_repository_files",
        lambda root: ["hit.py", "miss.py"],
    )
    monkeypatch.setattr(
        analysis_module,
        "_load_or_build_repository_map",
        lambda case, root, files, cache: (repository_map, True),
    )

    audit = audit_candidate_pool(_manifest(), tmp_path / "workspace")

    assert audit.candidate_pool_missing_targets == 1
    assert audit.targets[0].wide_candidate_rank == 21
    assert audit.wide_candidate_rank_buckets["1-40-displaced"] == 1
    assert audit.wide_candidate_rank_buckets["41-60"] == 0
