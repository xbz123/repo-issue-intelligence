from collections import Counter

from .duplicates import detect_duplicates
from .models import IssueRecord, PriorityResult
from .scoring import score_issue


def rank_issues(issues: list[IssueRecord]) -> list[PriorityResult]:
    counts: Counter[int] = Counter()
    for match in detect_duplicates(issues):
        counts[match.issue_number] += 1
        counts[match.candidate_issue_number] += 1
    results = [score_issue(issue, duplicate_count=counts[issue.number]) for issue in issues]
    return sorted(results, key=lambda result: result.priority_score, reverse=True)
