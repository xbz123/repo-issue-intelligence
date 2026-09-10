import json
from datetime import UTC, datetime

import pytest
from test_api_v2 import setup_run

from repo_issue_intelligence.evaluation_artifacts import load_evaluation_artifact
from repo_issue_intelligence.legacy_projection import V2ProjectionError, project_v2_run
from repo_issue_intelligence.models import (
    EvidenceAlignment,
    IssueType,
    ReproductionCompleteness,
)
from repo_issue_intelligence.protocol_v2_models import (
    AnalysisHypothesisV2,
    AnalysisV2,
    AttemptRequest,
    AttemptV2,
    EvidenceObservationV2,
    FrozenDict,
    LocalObservation,
    ReportedObservation,
)

NOW = datetime(2026, 9, 7, tzinfo=UTC)


def _successful_attempt(
    *, run_id: str = "run", issue_number: int = 1, attempt_id: str = "attempt-1"
) -> AttemptV2:
    analysis = AnalysisV2(
        summary="The refresh path returns committed state.",
        issue_type=IssueType.BUG,
        affected_component="service.py::refresh_token",
        reproduction_completeness=ReproductionCompleteness.COMPLETE,
        evidence_observations=(
            EvidenceObservationV2(
                evidence_id="E1",
                alignment=EvidenceAlignment.SUPPORTS_ISSUE,
                observation="The entry point is observable.",
            ),
            EvidenceObservationV2(
                evidence_id="E7",
                alignment=EvidenceAlignment.SUPPORTS_ISSUE,
                observation="The committed branch is returned.",
            ),
        ),
        contradictions=(),
        input_evidence_ids=("E1", "E7"),
        primary_evidence_id="E7",
        hypotheses=(
            AnalysisHypothesisV2(
                description="The handler selects stale state.",
                confidence=0.9,
                evidence_ids=("E7", "E1"),
                missing_evidence=(),
                validation_step="Run the existing refresh test.",
            ),
        ),
        needs_more_evidence=False,
    )
    return AttemptV2(
        attempt_id=attempt_id,
        run_id=run_id,
        issue_number=issue_number,
        evidence_set_id="evidence-set-1",
        ordinal=0,
        request=AttemptRequest(model="requested-model", provider="requested-provider"),
        started_at=NOW,
        state="success",
        finished_at=NOW,
        analysis=analysis,
        error=None,
        reported=ReportedObservation(
            provider="reported-provider",
            model="reported-model",
            reasoning_effort="high",
            service_tier="default",
            request_id="request-1",
            system_fingerprint="system-1",
            input_tokens=11,
            output_tokens=22,
        ),
        local=LocalObservation(elapsed_ms=123.4, category="completed"),
    )


def _summary_with_success(summary, attempt: AttemptV2, **issue_updates):
    issue = summary.issues[0].model_copy(
        update={
            "llm_state": "succeeded",
            "selected_analysis_attempt_id": attempt.attempt_id,
            "latest_attempt": attempt,
            "analysis": FrozenDict(attempt.analysis.model_dump(mode="json")),
            **issue_updates,
        }
    )
    return summary.model_copy(
        update={
            "configuration": summary.configuration.model_copy(update={"llm_enabled": True}),
            "issues": (issue, *summary.issues[1:]),
        }
    )


def test_v2_disabled_summary_projects_to_v1(monkeypatch, tmp_path):
    _, store, run = setup_run(monkeypatch, tmp_path)
    projected = project_v2_run(store.get_run_summary(run.run_id))
    assert projected.run_id == run.run_id
    assert projected.status.value == "awaiting_review"
    assert projected.llm_enabled is False
    assert projected.investigations[0].issue.number == 1


def test_v2_mixed_review_is_not_falsely_projected(monkeypatch, tmp_path):
    _, store, run = setup_run(monkeypatch, tmp_path)
    summary = store.get_run_summary(run.run_id)
    with pytest.raises(V2ProjectionError, match="review"):
        project_v2_run(summary.model_copy(update={"status": "PARTIALLY_REVIEWED"}))


def test_v2_success_uses_selected_attempt_and_preserves_v1_fields(monkeypatch, tmp_path):
    _, store, run = setup_run(monkeypatch, tmp_path)
    attempt = _successful_attempt(run_id=run.run_id)
    summary = _summary_with_success(store.get_run_summary(run.run_id), attempt)

    projected = project_v2_run(summary)

    assert projected.llm_enabled is True
    assert [item.issue_number for item in projected.ranked_issues] == [1, 2]
    result = projected.investigations[0].llm_analysis
    assert result is not None
    assert result.provider == "reported-provider"
    assert result.model == "reported-model"
    assert result.input_tokens == 11
    assert result.output_tokens == 22
    assert result.elapsed_ms == 123.4
    assert result.reasoning_effort == "high"
    assert result.service_tier == "default"
    assert result.request_id == "request-1"
    assert result.system_fingerprint == "system-1"
    assert result.analysis.reranked_evidence_ids == ["E7", "E1"]
    assert result.analysis.hypotheses[0].evidence_ids == ["E7", "E1"]


def test_v2_success_does_not_fallback_from_a_mismatched_latest_attempt(monkeypatch, tmp_path):
    _, store, run = setup_run(monkeypatch, tmp_path)
    selected = _successful_attempt(run_id=run.run_id, attempt_id="selected")
    latest = _successful_attempt(run_id=run.run_id, attempt_id="latest")
    summary = _summary_with_success(
        store.get_run_summary(run.run_id), selected, latest_attempt=latest
    )

    with pytest.raises(V2ProjectionError, match="selected"):
        project_v2_run(summary)


def test_v2_success_can_resolve_the_selected_attempt_from_supplied_mapping(monkeypatch, tmp_path):
    _, store, run = setup_run(monkeypatch, tmp_path)
    selected = _successful_attempt(run_id=run.run_id, attempt_id="selected")
    latest = _successful_attempt(run_id=run.run_id, attempt_id="latest")
    summary = _summary_with_success(
        store.get_run_summary(run.run_id), selected, latest_attempt=latest
    )

    projected = project_v2_run(summary, selected_attempts={"selected": selected})

    assert projected.investigations[0].llm_analysis is not None
    assert projected.investigations[0].llm_analysis.model == "reported-model"


@pytest.mark.parametrize(
    "missing", ["provider", "model", "input_tokens", "output_tokens", "elapsed_ms"]
)
def test_v2_success_requires_reported_usage_and_local_elapsed(monkeypatch, tmp_path, missing):
    _, store, run = setup_run(monkeypatch, tmp_path)
    attempt = _successful_attempt(run_id=run.run_id)
    if missing == "elapsed_ms":
        attempt = attempt.model_copy(
            update={"local": attempt.local.model_copy(update={"elapsed_ms": None})}
        )
    else:
        attempt = attempt.model_copy(
            update={"reported": attempt.reported.model_copy(update={missing: None})}
        )
    summary = _summary_with_success(store.get_run_summary(run.run_id), attempt)

    with pytest.raises(V2ProjectionError, match="metadata|observation|usage|elapsed"):
        project_v2_run(summary)


@pytest.mark.parametrize(
    ("status", "review_state", "expected"),
    [
        ("REVIEW_COMPLETED", "approved", "approved"),
        ("REVIEW_COMPLETED", "rejected", "rejected"),
    ],
)
def test_v2_completed_review_maps_only_uniform_decisions(
    monkeypatch, tmp_path, status, review_state, expected
):
    _, store, run = setup_run(monkeypatch, tmp_path)
    summary = store.get_run_summary(run.run_id)
    summary = summary.model_copy(
        update={
            "status": status,
            "issues": tuple(
                issue.model_copy(update={"review_state": review_state})
                for issue in summary.issues
            ),
        }
    )

    assert project_v2_run(summary).status.value == expected


def test_v2_completed_empty_run_is_not_inferred_as_rejected(monkeypatch, tmp_path):
    _, store, run = setup_run(monkeypatch, tmp_path)
    summary = store.get_run_summary(run.run_id).model_copy(
        update={"status": "REVIEW_COMPLETED", "issues": ()}
    )

    with pytest.raises(V2ProjectionError, match="empty|review"):
        project_v2_run(summary)


@pytest.mark.parametrize(
    ("status", "llm_state"),
    [
        ("AWAITING_REVIEW", "needs_information"),
        ("AWAITING_REVIEW", "in_progress"),
        ("INTERRUPTED", "disabled"),
    ],
)
def test_v2_nonrepresentable_states_fail_closed(monkeypatch, tmp_path, status, llm_state):
    _, store, run = setup_run(monkeypatch, tmp_path)
    summary = store.get_run_summary(run.run_id)
    summary = summary.model_copy(
        update={
            "status": status,
            "issues": (
                summary.issues[0].model_copy(
                    update={"llm_state": llm_state, "review_state": "pending"}
                ),
                *summary.issues[1:],
            ),
        }
    )

    with pytest.raises(V2ProjectionError):
        project_v2_run(summary)


def test_unversioned_evaluation_artifact_remains_legacy_unknown(tmp_path):
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({"results": [], "old_field": "kept"}), encoding="utf-8")
    loaded = load_evaluation_artifact(path)
    assert loaded["old_field"] == "kept"


def test_unknown_evaluation_artifact_version_is_refused(tmp_path):
    path = tmp_path / "future.json"
    path.write_text(json.dumps({"protocol": "v99"}), encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported"):
        load_evaluation_artifact(path)
