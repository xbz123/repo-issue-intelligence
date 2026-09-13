import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from test_api_v2 import setup_run

from repo_issue_intelligence.agent_store_v2 import AgentStoreV2, StoreConflict
from repo_issue_intelligence.protocol_v2_models import EvidenceCollectionContext
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


def test_review_replays_before_correction_revalidation_after_sealing(monkeypatch, tmp_path):
    _, store, run = setup_run(monkeypatch, tmp_path)
    # Issue 2 has a report and manifest, but no sealed evidence yet.
    before = store.get_issue(run.run_id, 2)
    request = ReviewSubmission(decision="approved", corrections=(Correction(file="service.py"),))
    arguments = dict(
        run_id=run.run_id,
        issue_number=2,
        principal_id="local-operator",
        idempotency_key="before-sealing",
        expected_review_version=0,
    )
    first = submit_issue_review(store, **arguments, request=request)
    assert submit_issue_review(store, **arguments, request=request) == first
    evidence = store.seal_evidence_set(
        run.run_id,
        2,
        [],
        EvidenceCollectionContext(
            snapshot_commit=run.snapshot.commit_oid, collector_protocol="test"
        ),
    )

    assert submit_issue_review(store, **arguments, request=request) == first
    changed = request.model_copy(update={"corrections": (Correction(file="missing.py"),)})
    with pytest.raises(StoreConflict, match="IDEMPOTENCY_PAYLOAD_MISMATCH"):
        submit_issue_review(store, **arguments, request=changed)
    # A different principal cannot replay someone else's key.
    with pytest.raises(StoreConflict):
        submit_issue_review(store, **{**arguments, "principal_id": "other"}, request=request)
    # New requests must still validate corrections against current state.
    with pytest.raises(StoreConflict, match="correction"):
        submit_issue_review(
            store,
            **{**arguments, "idempotency_key": "after-sealing", "expected_review_version": 1},
            request=request.model_copy(update={"evidence_set_id": evidence.evidence_set_id}),
        )
    after = store.get_issue(run.run_id, 2)
    assert after.review_version == 1
    assert after.deterministic_report == before.deterministic_report
    assert len(store.get_run_summary(run.run_id).issues[1].reviews) == 1


@pytest.mark.parametrize(
    "correction",
    [
        Correction(file="../outside.py", proposed_new_location=True),
        Correction(file="/outside.py", proposed_new_location=True),
        Correction(file="folder\\outside.py", proposed_new_location=True),
        Correction(file="service.py", symbol="refresh_token\nforged"),
        Correction(file="service.py", symbol="refresh_token\rforged"),
        Correction(file="missing.py"),
    ],
)
def test_new_review_keeps_correction_guards(monkeypatch, tmp_path, correction):
    _, store, run = setup_run(monkeypatch, tmp_path)
    issue = store.get_issue(run.run_id, 1)
    with pytest.raises(StoreConflict, match="correction"):
        submit_issue_review(
            store,
            run_id=run.run_id,
            issue_number=1,
            principal_id="local-operator",
            idempotency_key="invalid-new-review",
            expected_review_version=0,
            request=ReviewSubmission(
                decision="approved",
                evidence_set_id=issue.evidence_set_id,
                corrections=(correction,),
            ),
        )
    assert store.get_issue(run.run_id, 1).review_version == 0
    assert store.get_run_summary(run.run_id).reviewed_issues == 0


def test_concurrent_identical_reviews_append_once(monkeypatch, tmp_path):
    _, store, run = setup_run(monkeypatch, tmp_path)
    evidence_id = store.get_issue(run.run_id, 1).evidence_set_id
    barrier = Barrier(2)

    def submit():
        independent_store = AgentStoreV2(store.path)
        barrier.wait(timeout=10)
        return submit_issue_review(
            independent_store,
            run_id=run.run_id,
            issue_number=1,
            principal_id="local-operator",
            idempotency_key="concurrent-review",
            expected_review_version=0,
            request=ReviewSubmission(
                decision="approved",
                evidence_set_id=evidence_id,
                corrections=(Correction(file="service.py"),),
            ),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(submit) for _ in range(2)]
        results = [future.result(timeout=15) for future in futures]
    assert results[0] == results[1]
    assert store.get_issue(run.run_id, 1).review_version == 1
    assert len(store.get_run_summary(run.run_id).issues[0].reviews) == 1
