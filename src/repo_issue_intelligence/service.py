from collections import Counter
from datetime import datetime

from .duplicates import detect_duplicates
from .models import IssueRecord, PriorityResult
from .scoring import _aware_as_of, score_issue


def rank_issues(
    issues: list[IssueRecord],
    *,
    as_of: datetime | None = None,
    now: datetime | None = None,
) -> list[PriorityResult]:
    """Rank all Issues against one frozen, timezone-aware observation time."""

    if as_of is not None and now is not None:
        raise ValueError("pass either as_of or now, not both")
    frozen_as_of = _aware_as_of(as_of if as_of is not None else now, name="as_of")
    counts: Counter[int] = Counter()
    for match in detect_duplicates(issues):
        counts[match.issue_number] += 1
        counts[match.candidate_issue_number] += 1
    results = [
        score_issue(issue, duplicate_count=counts[issue.number], as_of=frozen_as_of)
        for issue in issues
    ]
    return sorted(results, key=lambda result: result.priority_score, reverse=True)
