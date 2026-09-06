from __future__ import annotations

import math
import re
from datetime import UTC, datetime

from .models import IssueRecord, Priority, PriorityResult, ScoreFactors, Severity, Urgency

CRITICAL_TERMS = {
    "data loss",
    "corruption",
    "remote code execution",
    "rce",
    "credential leak",
    "account takeover",
}
HIGH_TERMS = {
    "security",
    "vulnerability",
    "production down",
    "service unavailable",
    "crash",
    "deadlock",
    "regression",
}
RELEASE_TERMS = {"release blocker", "blocks release", "milestone blocker"}
EXPLOIT_TERMS = {"actively exploited", "in the wild", "proof of concept", "poc available"}
REPRO_TERMS = {"steps to reproduce", "reproducible", "minimal reproduction", "stack trace"}
WORKAROUND_TERMS = {"workaround", "temporary fix"}
AFFECTED_TERMS = {"all users", "many users", "production", "multiple customers"}


def _contains_any(text: str, terms: set[str]) -> list[str]:
    return sorted(term for term in terms if term in text)


def _severity(issue: IssueRecord) -> tuple[Severity, float, list[str]]:
    critical = _contains_any(issue.text, CRITICAL_TERMS)
    high = _contains_any(issue.text, HIGH_TERMS)
    if critical:
        return Severity.CRITICAL, 1.0, [f"Critical impact signal: {critical[0]}"]
    if high:
        return Severity.HIGH, 0.78, [f"High impact signal: {high[0]}"]
    if any(label.lower() in {"bug", "type: bug"} for label in issue.labels):
        return Severity.MEDIUM, 0.52, ["Issue is labeled as a bug"]
    return Severity.LOW, 0.25, ["No high-impact failure signal was detected"]


def _urgency(issue: IssueRecord) -> tuple[Urgency, float, list[str]]:
    if _contains_any(issue.text, EXPLOIT_TERMS | RELEASE_TERMS):
        return Urgency.HIGH, 1.0, ["Exploit or release-blocking urgency signal detected"]
    if "latest release" in issue.text or "production" in issue.text or issue.comments_count >= 10:
        return Urgency.HIGH, 0.8, ["Current production or high-engagement signal detected"]
    if _contains_any(issue.text, WORKAROUND_TERMS):
        return Urgency.LOW, 0.25, ["A workaround appears to be available"]
    return Urgency.MEDIUM, 0.5, ["No explicit deadline or workaround was detected"]


def _aware_as_of(value: datetime | None, *, name: str) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def normalize_as_of(value: datetime | None = None) -> datetime:
    """Return one timezone-aware UTC timestamp for a ranking operation."""

    return _aware_as_of(value, name="as_of")


def score_issue(
    issue: IssueRecord,
    duplicate_count: int = 0,
    now: datetime | None = None,
    *,
    as_of: datetime | None = None,
) -> PriorityResult:
    """Score one Issue at a caller-supplied immutable observation time.

    ``now`` remains a backwards-compatible alias for the V1 callers.  V2
    callers should pass ``as_of`` and use one value for the whole ranking.
    """

    if now is not None and as_of is not None:
        raise ValueError("pass either now or as_of, not both")
    as_of = _aware_as_of(as_of if as_of is not None else now, name="as_of")
    severity, severity_score, reasons = _severity(issue)
    urgency, urgency_score, urgency_reasons = _urgency(issue)
    reasons.extend(urgency_reasons)
    affected_users = (
        0.8 if _contains_any(issue.text, AFFECTED_TERMS) else min(0.7, issue.comments_count / 20)
    )
    reproducibility = 0.8 if _contains_any(issue.text, REPRO_TERMS) else 0.2
    duplicate_factor = min(1.0, math.log2(duplicate_count + 1) / 4) if duplicate_count else 0.0
    release_blocking = 1.0 if _contains_any(issue.text, RELEASE_TERMS) else 0.0
    age_days = max(0.0, (as_of - issue.updated_at.astimezone(UTC)).total_seconds() / 86400)
    recency = math.exp(-age_days / 30)
    factors = ScoreFactors(
        severity=severity_score,
        urgency=urgency_score,
        affected_users=affected_users,
        reproducibility=reproducibility,
        duplicate_count=duplicate_factor,
        release_blocking=release_blocking,
        recency=recency,
    )
    score = 100 * (
        0.30 * severity_score
        + 0.20 * urgency_score
        + 0.15 * affected_users
        + 0.10 * reproducibility
        + 0.10 * duplicate_factor
        + 0.10 * release_blocking
        + 0.05 * recency
    )
    if duplicate_count:
        reasons.append(f"Linked to {duplicate_count} similar issue(s)")
    if release_blocking:
        reasons.append("The issue is a release blocker")
    if reproducibility >= 0.8:
        reasons.append("Reproduction evidence is present")
    exploited = bool(_contains_any(issue.text, EXPLOIT_TERMS))
    data_loss = bool(_contains_any(issue.text, {"data loss", "corruption"}))
    if exploited or (data_loss and reproducibility >= 0.8):
        priority, score = Priority.P0, max(score, 90)
    elif release_blocking or score >= 70:
        priority = Priority.P1
    elif score >= 45:
        priority = Priority.P2
    else:
        priority = Priority.P3
    needs_information = len(re.findall(r"\w+", issue.body)) < 15 and reproducibility < 0.8
    if needs_information:
        reasons.append("Issue body lacks enough diagnostic detail")
    return PriorityResult(
        issue_number=issue.number,
        severity=severity,
        urgency=urgency,
        priority=priority,
        priority_score=round(score, 2),
        priority_reasons=reasons,
        factors=factors,
        needs_information=needs_information,
    )
