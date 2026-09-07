"""Pure, opt-in Analysis V2 normalization; no storage or provider dependencies."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from .models import (
    EvidenceAlignment,
    EvidenceSnippet,
    IssueType,
    LLMAnalysisResponse,
    LLMEvidenceObservation,
    LLMHypothesis,
    ReproductionCompleteness,
    StrictOutputModel,
)

EvidenceLookup = Mapping[str, EvidenceSnippet]

ANALYSIS_V2_PROMPT_VERSION = "analysis-v2-primary-1"
PRIMARY_EVIDENCE_INSTRUCTION = (
    "\nOrder hypothesis.evidence_ids by importance. The first cited evidence ID is "
    "the primary evidence used to derive the affected component and validation step.\n"
)


class LLMAnalysisV2(StrictOutputModel):
    summary: str
    issue_type: IssueType
    affected_component: str
    reproduction_completeness: ReproductionCompleteness
    evidence_observations: list[LLMEvidenceObservation]
    contradictions: list[str]
    input_evidence_ids: list[str]
    primary_evidence_id: str
    hypotheses: list[LLMHypothesis]
    needs_more_evidence: bool


def validated_evidence(
    input_evidence_ids: Sequence[str],
    evidence_lookup: EvidenceLookup,
) -> tuple[EvidenceSnippet, ...]:
    """Validate identities and snapshot the exact evidence before a provider call."""
    if (
        not isinstance(input_evidence_ids, Sequence)
        or isinstance(input_evidence_ids, (str, bytes))
        or not input_evidence_ids
        or any(not isinstance(item, str) or not item for item in input_evidence_ids)
        or len(input_evidence_ids) != len(set(input_evidence_ids))
        or not isinstance(evidence_lookup, Mapping)
        or any(
            not isinstance(key, str)
            or not key
            or not isinstance(snippet, EvidenceSnippet)
            or key != snippet.id
            for key, snippet in evidence_lookup.items()
        )
        or any(item not in evidence_lookup for item in input_evidence_ids)
    ):
        raise ValueError("Invalid input evidence")
    return tuple(evidence_lookup[item].model_copy(deep=True) for item in input_evidence_ids)


def normalize_analysis_v2(
    response: LLMAnalysisResponse,
    input_evidence_ids: Sequence[str],
    evidence_lookup: EvidenceLookup,
) -> LLMAnalysisV2:
    evidence = validated_evidence(input_evidence_ids, evidence_lookup)
    evidence_by_id = {snippet.id: snippet for snippet in evidence}
    input_ids = set(evidence_by_id)
    observed_ids = [item.evidence_id for item in response.evidence_observations]
    if set(observed_ids) != input_ids or len(observed_ids) != len(input_ids):
        raise ValueError("Invalid evidence observations")
    cited_ids = response.hypothesis.evidence_ids
    if not cited_ids or len(cited_ids) != len(set(cited_ids)) or not set(cited_ids) <= input_ids:
        raise ValueError("Invalid evidence references")
    primary_id = response.hypothesis.evidence_ids[0]
    primary = evidence_by_id[primary_id]
    location = f"{primary.file}::{primary.symbol}" if primary.symbol else primary.file
    return LLMAnalysisV2(
        summary=response.summary,
        issue_type=response.issue_type,
        affected_component=location,
        reproduction_completeness=response.reproduction_completeness,
        evidence_observations=[
            item.model_copy(deep=True) for item in response.evidence_observations
        ],
        contradictions=[
            f"{item.evidence_id}: {item.observation}"
            for item in response.evidence_observations
            if item.alignment is EvidenceAlignment.CONTRADICTS_ISSUE
        ],
        input_evidence_ids=list(evidence_by_id),
        primary_evidence_id=primary_id,
        hypotheses=[
            LLMHypothesis(
                **response.hypothesis.model_dump(),
                validation_step=(
                    f"Inspect the cited behavior at {location}, then run the smallest existing "
                    "relevant test and compare the result with the Issue without modifying files."
                ),
            )
        ],
        needs_more_evidence=bool(response.hypothesis.missing_evidence),
    )


def project_legacy_analysis(raw_payload: Mapping[str, Any]) -> dict[str, Any]:
    """Label a detached historical view without reinterpreting its original values."""
    payload = deepcopy(dict(raw_payload))
    model = payload.get("model")
    return {
        "protocol_version": "v1",
        "evidence_order_semantics": "legacy_input_order",
        "requested_model": model if isinstance(model, str) else None,
        "reported_model": None,
        "payload": payload,
    }


def project_analysis_v2_to_v1(analysis: LLMAnalysisV2) -> dict[str, Any]:
    """Return an explicitly labeled V1-shaped view, never a second authoritative result."""
    payload = analysis.model_dump(mode="json")
    payload["reranked_evidence_ids"] = payload.pop("input_evidence_ids")
    del payload["primary_evidence_id"]
    return project_legacy_analysis(payload)
