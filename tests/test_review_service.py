import json

import pytest
from test_api_v2 import setup_run

from repo_issue_intelligence.agent_store_v2 import StoreConflict
from repo_issue_intelligence.review_service import (
    Correction,
    ReviewSubmission,
    submit_issue_review,
)


def test_review_is_bound_idempotent_and_append_only(monkeypatch, tmp_path):
    root, store, run = setup_run(monkeypatch, tmp_path)
    issue = store.get_issue(run.run_id, 1)
    assert issue is not None
    request = ReviewSubmission(
        decision="approved",
        notes="verified",
        corrections=(Correction(file="service.py", symbol="refresh_token"),),
        evidence_set_id=issue.evidence_set_id,
        selected_attempt_id=issue.selected_analysis_attempt_id,
    )

    first = submit_issue_review(
        store,
        run_id=run.run_id,
        issue_number=1,
        principal_id="local-operator",
        idempotency_key="review-1",
        expected_review_version=0,
        request=request,
    )
    replay = submit_issue_review(
        store,
        run_id=run.run_id,
        issue_number=1,
        principal_id="local-operator",
        idempotency_key="review-1",
        expected_review_version=0,
        request=request,
    )
    assert replay == first
    assert first["review_version"] == 1
    assert first["decision"] == "approved"

    with pytest.raises(StoreConflict, match="IDEMPOTENCY_PAYLOAD_MISMATCH"):
        submit_issue_review(
            store,
            run_id=run.run_id,
            issue_number=1,
            principal_id="local-operator",
            idempotency_key="review-1",
            expected_review_version=0,
            request=request.model_copy(update={"decision": "rejected"}),
        )

    second = submit_issue_review(
        store,
        run_id=run.run_id,
        issue_number=1,
        principal_id="local-operator",
        idempotency_key="review-2",
        expected_review_version=1,
        request=request.model_copy(update={"decision": "rejected", "corrections": ()}),
    )
    assert second["review_version"] == 2
    assert store.get_issue(run.run_id, 1).review_version == 2
    with store._connect() as connection:
        rows = connection.execute(
            "SELECT decision,payload_json FROM agent_v2_reviews "
            "WHERE run_id=? AND issue_number=? ORDER BY expected_review_version",
            (run.run_id, 1),
        ).fetchall()
    assert [row["decision"] for row in rows] == ["approved", "rejected"]
    assert json.loads(rows[0]["payload_json"])["corrections"][0]["file"] == "service.py"


def test_review_rejects_stale_target_and_unknown_correction(monkeypatch, tmp_path):
    _, store, run = setup_run(monkeypatch, tmp_path)
    issue = store.get_issue(run.run_id, 1)
    assert issue is not None
    base = dict(
        decision="approved",
        evidence_set_id=issue.evidence_set_id,
        selected_attempt_id=None,
    )
    with pytest.raises(StoreConflict, match="correction"):
        submit_issue_review(
            store,
            run_id=run.run_id,
            issue_number=1,
            principal_id="local-operator",
            idempotency_key="review-unknown",
            expected_review_version=0,
            request=ReviewSubmission(
                **base,
                corrections=(Correction(file="missing.py", symbol="new"),),
            ),
        )
    with pytest.raises(StoreConflict, match="version"):
        submit_issue_review(
            store,
            run_id=run.run_id,
            issue_number=1,
            principal_id="local-operator",
            idempotency_key="review-stale",
            expected_review_version=1,
            request=ReviewSubmission(**base),
        )
