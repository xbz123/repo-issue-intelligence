from datetime import UTC, datetime
from pathlib import Path

from repo_issue_intelligence.evidence import collect_evidence
from repo_issue_intelligence.investigator import investigate
from repo_issue_intelligence.models import CandidateLocation, IssueRecord
from repo_issue_intelligence.repository_index import build_repository_map


def issue() -> IssueRecord:
    timestamp = datetime(2026, 7, 29, tzinfo=UTC)
    return IssueRecord(
        number=1,
        title="Refresh token validation fails",
        body="The refresh token endpoint raises an exception.",
        created_at=timestamp,
        updated_at=timestamp,
    )


def test_collect_evidence_returns_bounded_numbered_source(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "token_service.py").write_text(
        "def validate_refresh_token(token):\n"
        '    """Validate a refresh token."""\n'
        "    raise ExpiredSignatureError()\n"
        "\n"
        "def refresh_access_token(token):\n"
        "    try:\n"
        "        return validate_refresh_token(token)\n"
        "    except ExpiredSignatureError:\n"
        "        return None\n",
        encoding="utf-8",
    )
    report = investigate(issue(), build_repository_map(repository))

    evidence = collect_evidence(report, max_total_chars=400)

    assert len(evidence) == 1
    assert evidence[0].id == "E1"
    assert evidence[0].file == "token_service.py"
    assert evidence[0].symbol == "validate_refresh_token"
    assert "1: def validate_refresh_token" in evidence[0].content
    assert "8:     except ExpiredSignatureError:" in evidence[0].content
    assert len(evidence[0].content) <= 400


def test_collect_evidence_rejects_paths_outside_repository(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    outside = tmp_path / "secret.py"
    outside.write_text("TOKEN = 'do-not-read'\n", encoding="utf-8")
    report = investigate(issue(), build_repository_map(repository))
    report = report.model_copy(
        update={
            "candidates": [
                CandidateLocation(
                    file="../secret.py",
                    lines="1-1",
                    confidence=0.9,
                    evidence=["Untrusted candidate path"],
                )
            ]
        }
    )

    assert collect_evidence(report) == []


def test_collect_evidence_caps_each_snippet_to_preserve_candidate_breadth(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    for name in ("first.py", "second.py"):
        (repository / name).write_text(
            "\n".join(f"{name}_line_{line}" for line in range(30)),
            encoding="utf-8",
        )
    report = investigate(issue(), build_repository_map(repository))
    report = report.model_copy(
        update={
            "candidates": [
                CandidateLocation(
                    file=name,
                    lines="1-20",
                    confidence=0.9,
                    evidence=["Synthetic candidate"],
                )
                for name in ("first.py", "second.py")
            ]
        }
    )

    evidence = collect_evidence(
        report,
        max_total_chars=400,
        max_chars_per_snippet=200,
    )

    assert [snippet.file for snippet in evidence] == ["first.py", "second.py"]
    assert all(len(snippet.content) <= 200 for snippet in evidence)


def test_collect_evidence_has_no_default_local_size_cap(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    source = "\n".join(f"line_{line}" for line in range(1, 151))
    (repository / "large.py").write_text(source, encoding="utf-8")
    report = investigate(issue(), build_repository_map(repository)).model_copy(
        update={
            "candidates": [
                CandidateLocation(
                    file="large.py",
                    lines="1-150",
                    confidence=0.9,
                    evidence=["Synthetic candidate"],
                )
            ]
        }
    )

    evidence = collect_evidence(report)

    assert len(evidence) == 1
    assert "150: line_150" in evidence[0].content
    assert len(evidence[0].content) > 1_600
