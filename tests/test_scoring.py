from datetime import UTC, datetime

from repo_issue_intelligence.models import IssueRecord, Priority
from repo_issue_intelligence.scoring import score_issue


def make_issue(body: str, labels: list[str] | None = None) -> IssueRecord:
    return IssueRecord(
        number=1,
        title="Production failure",
        body=body,
        labels=labels or [],
        comments_count=0,
        created_at=datetime(2026, 7, 1, tzinfo=UTC),
        updated_at=datetime(2026, 7, 26, tzinfo=UTC),
    )


def test_reproducible_data_loss_is_p0() -> None:
    result = score_issue(
        make_issue("Data loss with steps to reproduce and a stack trace"),
        now=datetime(2026, 7, 27, tzinfo=UTC),
    )

    assert result.priority == Priority.P0
    assert result.priority_score >= 90
    assert result.needs_information is False


def test_short_issue_requires_more_information() -> None:
    result = score_issue(
        make_issue("It crashes", labels=["bug"]),
        now=datetime(2026, 7, 27, tzinfo=UTC),
    )

    assert result.needs_information is True
    assert any("diagnostic detail" in reason for reason in result.priority_reasons)
