from datetime import UTC, datetime

from repo_issue_intelligence.duplicates import detect_duplicates
from repo_issue_intelligence.models import IssueRecord


def issue(number: int, title: str, body: str) -> IssueRecord:
    timestamp = datetime(2026, 7, 27, tzinfo=UTC)
    return IssueRecord(
        number=number,
        title=title,
        body=body,
        created_at=timestamp,
        updated_at=timestamp,
    )


def test_detects_similar_issue_pair() -> None:
    issues = [
        issue(1, "Expired refresh token returns 500", "Expired JWT crashes refresh endpoint"),
        issue(2, "Refresh endpoint crashes on expired JWT", "Expired token returns HTTP 500"),
        issue(3, "Improve documentation", "Add setup examples"),
    ]

    matches = detect_duplicates(issues, threshold=0.3)

    assert matches
    assert {matches[0].issue_number, matches[0].candidate_issue_number} == {1, 2}
    assert matches[0].shared_terms
