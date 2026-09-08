import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from repo_issue_intelligence.evidence import collect_evidence, collect_evidence_v2
from repo_issue_intelligence.investigator import investigate
from repo_issue_intelligence.models import CandidateLocation, IssueRecord
from repo_issue_intelligence.repository_context import capture_repository_context
from repo_issue_intelligence.repository_index import build_repository_map
from repo_issue_intelligence.repository_view import prepare_repository_view


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "source.py").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    for args in (
        ("init", "-q"),
        ("add", "source.py"),
        (
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "initial",
        ),
    ):
        subprocess.run(["git", "-C", str(repository), *args], check=True, capture_output=True)
    return repository


def _report(repository: Path):
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    issue = IssueRecord(
        number=1,
        title="source fails",
        body="source failure",
        created_at=timestamp,
        updated_at=timestamp,
    )
    return investigate(issue, build_repository_map(repository)).model_copy(
        update={
            "candidates": [
                CandidateLocation(file=file, lines="1-4", confidence=0.9, evidence=[])
                for file in ("untracked.py", "source.py")
            ]
        }
    )


def test_v2_collects_whole_lines_with_exact_metadata_from_fixed_view(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    report = _report(repository)
    with prepare_repository_view(capture_repository_context(repository)) as view:
        (repository / "source.py").write_text("changed checkout\n", encoding="utf-8")
        (repository / "untracked.py").write_text("untracked decoy\n", encoding="utf-8")
        items = collect_evidence_v2(
            report,
            repository_view=view,
            context_lines=0,
            max_total_chars=17,
            max_lines_per_snippet=3,
        )
        assert [item.model_dump() for item in items] == [
            {
                "evidence_id": "E1",
                "ordinal": 0,
                "candidate_rank": 1,
                "selection_kind": "candidate",
                "file": "source.py",
                "symbol": None,
                "requested_range": (1, 4),
                "actual_range": (1, 2),
                "truncation_reason": "line_budget,total_char_budget",
                "char_count": 13,
                "content": "1: one\n2: two",
                "collector_protocol": "v2",
            }
        ]
        legacy = collect_evidence(
            report,
            repository_view=view,
            context_lines=0,
            max_total_chars=17,
        )
        assert legacy[0].content == "1: one\n2: two\n3:"


def test_v2_rejects_invalid_budgets_ranges_and_unavailable_view(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    report = _report(repository)
    with prepare_repository_view(capture_repository_context(repository)) as view:
        for kwargs in (
            {"max_total_chars": 0},
            {"max_total_chars": True},
            {"max_chars_per_snippet": -1},
            {"max_chars_per_snippet": 1.5},
            {"max_lines_per_snippet": 0},
            {"max_lines_per_snippet": False},
            {"context_lines": -1},
            {"context_lines": 1.5},
        ):
            with pytest.raises(ValueError):
                collect_evidence_v2(report, repository_view=view, **kwargs)
        for value in ("invalid", "0-4", "4-1"):
            invalid_report = report.model_copy(
                update={
                    "candidates": [
                        report.candidates[1].model_copy(update={"lines": value}),
                    ]
                }
            )
            with pytest.raises(ValueError, match="range"):
                collect_evidence_v2(invalid_report, repository_view=view)
        with pytest.raises(ValueError, match="RepositoryView"):
            collect_evidence_v2(report, repository_view=None)
    with pytest.raises(ValueError, match="view"):
        collect_evidence_v2(report, repository_view=view)


def test_v2_preserves_candidate_order_and_skips_incomplete_first_lines(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    report = _report(repository)
    candidate = report.candidates[1]
    report = report.model_copy(
        update={
            "candidates": [
                report.candidates[0],
                candidate.model_copy(update={"lines": "3-4"}),
                candidate.model_copy(update={"lines": "1-2", "symbol": "one"}),
                candidate.model_copy(update={"lines": "2-4", "qualified_symbol": "source.two"}),
            ]
        }
    )
    with prepare_repository_view(capture_repository_context(repository)) as view:
        items = collect_evidence_v2(
            report,
            repository_view=view,
            context_lines=0,
            max_lines_per_snippet=None,
            max_total_chars=13,
            max_chars_per_snippet=6,
        )
        assert [
            (
                item.evidence_id,
                item.ordinal,
                item.candidate_rank,
                item.symbol,
                item.content,
                item.requested_range,
                item.actual_range,
                item.truncation_reason,
            )
            for item in items
        ] == [
            ("E1", 0, 2, "one", "1: one", (1, 2), (1, 1), "snippet_char_budget"),
            (
                "E2",
                1,
                3,
                "source.two",
                "2: two",
                (2, 4),
                (2, 2),
                "snippet_char_budget,total_char_budget",
            ),
        ]
        assert collect_evidence_v2(report, repository_view=view, max_total_chars=1) == []
        assert collect_evidence_v2(report, repository_view=view, max_chars_per_snippet=1) == []
        complete = collect_evidence_v2(
            report,
            repository_view=view,
            context_lines=1,
            max_total_chars=None,
            max_chars_per_snippet=None,
            max_lines_per_snippet=None,
        )
        assert complete[0].content == "2: two\n3: three\n4: four"
        assert complete[0].requested_range == complete[0].actual_range == (2, 4)
        assert complete[0].truncation_reason is None


@pytest.mark.parametrize("context", [0, 12, 200])
def test_v2_skips_ranges_wholly_beyond_captured_file(tmp_path, context):
    repository = _repository(tmp_path)
    report = _report(repository)
    candidate = report.candidates[1]
    report = report.model_copy(
        update={
            "candidates": [
                candidate.model_copy(update={"lines": "100-110"}),
                candidate.model_copy(update={"lines": "3-4"}),
            ]
        }
    )
    with prepare_repository_view(capture_repository_context(repository)) as view:
        items = collect_evidence_v2(report, repository_view=view, context_lines=context)
    assert len(items) == 1
    assert items[0].candidate_rank == 1
    assert items[0].evidence_id == "E1"


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("collector", [collect_evidence_v2, collect_evidence])
def test_closed_view_rejects_recreated_directory(tmp_path, empty, collector):
    repository = _repository(tmp_path)
    report = _report(repository)
    if empty:
        report = report.model_copy(update={"candidates": []})
    view = prepare_repository_view(capture_repository_context(repository))
    root = view.materialized_root
    view.close()
    root.mkdir()
    try:
        (root / "source.py").write_text("replacement\n", encoding="utf-8")
        with pytest.raises(ValueError, match="closed"):
            collector(report, repository_view=view)
    finally:
        (root / "source.py").unlink()
        root.rmdir()
