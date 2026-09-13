"""Per-Issue Protocol v2 review validation and atomic persistence."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .agent_store_v2 import AgentStoreV2, StoreError


class Correction(BaseModel):
    """A reviewer correction detached from the immutable model output."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    file: str = Field(min_length=1, max_length=512)
    symbol: str | None = Field(default=None, max_length=256)
    proposed_new_location: bool = False


class ReviewSubmission(BaseModel):
    """A single per-Issue review request."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    decision: Literal["approved", "rejected", "needs_information"]
    notes: str | None = Field(default=None, max_length=512)
    corrections: tuple[Correction, ...] = ()
    evidence_set_id: str | None = None
    selected_attempt_id: str | None = None


def submit_issue_review(
    store: AgentStoreV2,
    *,
    run_id: str,
    issue_number: int,
    principal_id: str,
    idempotency_key: str,
    expected_review_version: int,
    request: ReviewSubmission,
) -> dict:
    """Validate and append one review, or replay its exact idempotent result."""

    request = ReviewSubmission.model_validate(request.model_dump())
    if not principal_id or not idempotency_key:
        raise StoreError("review identity is required")
    if expected_review_version < 0:
        raise StoreError("review version must be nonnegative")
    payload = {
        "decision": request.decision,
        "notes": request.notes,
        "corrections": [item.model_dump(mode="json") for item in request.corrections],
        "evidence_set_id": request.evidence_set_id,
        "selected_attempt_id": request.selected_attempt_id,
        "expected_review_version": expected_review_version,
    }
    return store.append_review(
        run_id=run_id,
        issue_number=issue_number,
        principal_id=principal_id,
        idempotency_key=idempotency_key,
        expected_review_version=expected_review_version,
        evidence_set_id=request.evidence_set_id,
        selected_attempt_id=request.selected_attempt_id,
        decision=request.decision,
        payload=payload,
    )
