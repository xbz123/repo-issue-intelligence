from __future__ import annotations

import re
from difflib import SequenceMatcher

from .models import DuplicateMatch, IssueRecord

STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "to",
    "of",
    "in",
    "on",
    "is",
    "it",
    "for",
    "with",
    "when",
}


def _tokens(issue: IssueRecord) -> set[str]:
    return {
        word for word in re.findall(r"[a-zA-Z0-9_./-]{3,}", issue.text) if word not in STOPWORDS
    }


def similarity(left: IssueRecord, right: IssueRecord) -> tuple[float, list[str]]:
    left_tokens, right_tokens = _tokens(left), _tokens(right)
    union = left_tokens | right_tokens
    jaccard = len(left_tokens & right_tokens) / len(union) if union else 0.0
    title_ratio = SequenceMatcher(None, left.title.lower(), right.title.lower()).ratio()
    shared = sorted(left_tokens & right_tokens, key=len, reverse=True)[:8]
    return round(0.65 * jaccard + 0.35 * title_ratio, 4), shared


def detect_duplicates(issues: list[IssueRecord], threshold: float = 0.55) -> list[DuplicateMatch]:
    matches: list[DuplicateMatch] = []
    for index, issue in enumerate(issues):
        for candidate in issues[index + 1 :]:
            score, shared = similarity(issue, candidate)
            if score >= threshold:
                matches.append(
                    DuplicateMatch(
                        issue_number=issue.number,
                        candidate_issue_number=candidate.number,
                        similarity=score,
                        shared_terms=shared,
                    )
                )
    return sorted(matches, key=lambda match: match.similarity, reverse=True)
