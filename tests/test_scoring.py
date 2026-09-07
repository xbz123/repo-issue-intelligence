from datetime import UTC, datetime

import pytest

from repo_issue_intelligence.models import IssueRecord, Priority
from repo_issue_intelligence.scoring import score_issue
from repo_issue_intelligence.service import rank_issues


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


def test_score_rejects_naive_as_of_and_accepts_explicit_alias() -> None:
    issue = make_issue("A detailed issue with a minimal reproduction and stack trace")
    with pytest.raises(ValueError, match="timezone-aware"):
        score_issue(issue, as_of=datetime(2026, 7, 27))
    result = score_issue(issue, as_of=datetime(2026, 7, 27, tzinfo=UTC))
    alias_result = score_issue(issue, now=datetime(2026, 7, 27, tzinfo=UTC))
    assert result == alias_result


def test_rank_issues_uses_one_frozen_as_of_for_all_results() -> None:
    issues = [
        make_issue("Detailed production crash with steps to reproduce"),
        IssueRecord(
            number=2,
            title="Old issue",
            body="A detailed issue body with enough diagnostic context.",
            created_at=datetime(2026, 7, 1, tzinfo=UTC),
            updated_at=datetime(2026, 7, 1, tzinfo=UTC),
        ),
    ]
    results = rank_issues(issues, as_of=datetime(2026, 7, 27, tzinfo=UTC))
    assert len(results) == 2
    assert all(0 < result.factors.recency <= 1 for result in results)
    with pytest.raises(ValueError, match="either as_of or now"):
        rank_issues(
            issues,
            as_of=datetime(2026, 7, 27, tzinfo=UTC),
            now=datetime(2026, 7, 27, tzinfo=UTC),
        )
