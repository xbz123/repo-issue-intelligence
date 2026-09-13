import json
import shutil

from fastapi.testclient import TestClient
from test_agent_store_v2 import new_store
from test_api_security import AUTH, TOKEN
from test_issue_execution import NOW, issues, repository

from repo_issue_intelligence.api import app
from repo_issue_intelligence.issue_execution import run_agent_v2
from repo_issue_intelligence.protocol_v2_models import EvidenceCollectionContext, EvidenceItemV2


def setup_run(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    root = repository(tmp_path / "repo")
    store = new_store(tmp_path / "private")
    run = run_agent_v2(issues(1, 2), root, 2, store, as_of=NOW)
    store.seal_evidence_set(
        run.run_id,
        1,
        [
            EvidenceItemV2(
                evidence_id="E1",
                ordinal=0,
                candidate_rank=1,
                selection_kind="candidate",
                file="service.py",
                symbol="refresh_token",
                requested_range=(1, 2),
                actual_range=(1, 2),
                char_count=len("def refresh_token():\n    return 'committed'\n"),
                content="def refresh_token():\n    return 'committed'\n",
                collector_protocol="test-v1",
            )
        ],
        EvidenceCollectionContext(
            snapshot_commit=run.snapshot.commit_oid,
            analysis_prefix=run.snapshot.analysis_prefix,
            collector_protocol="test-v1",
        ),
    )
    monkeypatch.setenv("RII_API_TOKEN", TOKEN)
    monkeypatch.setenv("RII_API_V2_DATABASE", str(store.path))
    monkeypatch.setenv("RII_API_ANALYSIS_ROOTS", json.dumps([str(root)]))
    return root, store, run


def test_v2_read_contract_and_retained_evidence(monkeypatch, tmp_path):
    root, store, run = setup_run(monkeypatch, tmp_path)
    before = store.path.read_bytes()
    monkeypatch.setattr(
        type(store),
        "read_evidence",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("metadata pagination must not load evidence content")
        ),
    )
    monkeypatch.setattr(
        type(store),
        "list_attempts",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("attempt pagination must not load the full collection")
        ),
    )
    url = f"/v2/agent/runs/{run.run_id}"
    with TestClient(app, base_url="http://127.0.0.1", headers=AUTH) as client:
        response = client.get(url)
        assert response.status_code == 200
        summary = response.json()
        assert summary["protocol"] == "v2"
        assert summary["status"] == "AWAITING_REVIEW"
        assert summary["issue_count"] == 2
        assert "inputs" not in summary and "issues" not in summary
        assert "manifest" not in summary["snapshot"]
        page = client.get(url + "/issues?limit=1").json()
        assert len(page["items"]) == 1 and page["total"] == 2
        number = page["items"][0]["issue_number"]
        other = client.get(url + f"/issues?limit=1&offset={page['next_offset']}").json()
        assert other["items"][0]["issue_number"] != number
        assert "deterministic_report" not in page["items"][0]
        detail = client.get(url + f"/issues/{number}").json()
        assert detail["deterministic_report"] is not None
        assert detail["analysis"] is None
        evidence_url = url + f"/issues/{number}/evidence"
        metadata = client.get(evidence_url).json()
        assert metadata["items"] and all("content" not in item for item in metadata["items"])
        evidence_id = metadata["items"][0]["evidence_id"]
        set_id = metadata["evidence_set_id"]
        assert client.get(evidence_url + f"/{evidence_id}?evidence_set_id=wrong").status_code == 404
        item = client.get(evidence_url + f"/{evidence_id}?evidence_set_id={set_id}").json()
        assert "committed" in item["content"]
        assert item["evidence_set_id"] == set_id and item["issue_number"] == number
        assert (
            client.get(evidence_url + f"/{evidence_id}?evidence_set_id={set_id}-other").status_code
            == 404
        )
        assert client.get(url + f"/issues/{number}/attempts").json()["items"] == []
        assert client.get(url + "/issues/999/evidence").status_code == 404
        assert client.get(url + "/issues?limit=101").status_code == 413
        assert client.get(url + "/issues?offset=-1").status_code == 422
        shutil.rmtree(root)
        assert (
            client.get(evidence_url + f"/{evidence_id}?evidence_set_id={set_id}").status_code == 200
        )
        monkeypatch.setenv("RII_API_OPERATIONS", '["read"]')
        assert client.get(url).status_code == 200
        assert client.get(evidence_url).status_code == 403
        monkeypatch.setenv("RII_API_ANALYSIS_ROOTS", "[]")
        assert client.get(url).status_code == 403
    assert store.path.read_bytes() == before


def test_v2_api_requires_auth_and_existing_explicit_database(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RII_API_TOKEN", TOKEN)
    missing = tmp_path / "missing.sqlite3"
    monkeypatch.setenv("RII_API_V2_DATABASE", str(missing))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/v2/agent/runs/run").status_code == 401
        assert client.get("/v2/agent/runs/run", headers=AUTH).status_code == 503
    assert not missing.exists()


def test_v2_issue_review_is_authenticated_idempotent_and_versioned(monkeypatch, tmp_path):
    root, store, run = setup_run(monkeypatch, tmp_path)
    issue = store.get_issue(run.run_id, 1)
    assert issue is not None
    url = f"/v2/agent/runs/{run.run_id}/issues/1/reviews"
    payload = {
        "decision": "approved",
        "notes": "verified",
        "corrections": [{"file": "service.py", "symbol": "refresh_token"}],
        "evidence_set_id": issue.evidence_set_id,
        "selected_attempt_id": issue.selected_analysis_attempt_id,
        "expected_review_version": 0,
        "idempotency_key": "api-review-1",
    }
    with TestClient(app, base_url="http://127.0.0.1", headers=AUTH) as client:
        first = client.post(url, json=payload)
        assert first.status_code == 200, first.text
        assert first.json()["review_version"] == 1
        assert client.post(url, json=payload).json() == first.json()
        stale = client.post(
            url,
            json={
                **payload,
                "idempotency_key": "api-review-2",
                "expected_review_version": 0,
            },
        )
        assert stale.status_code == 409
        forged = client.post(url, json={**payload, "principal_id": "forged"})
        assert forged.status_code == 422


def test_v2_query_routes_publish_sparse_openapi_schema(monkeypatch, tmp_path):
    setup_run(monkeypatch, tmp_path)
    with TestClient(app, base_url="http://127.0.0.1", headers=AUTH) as client:
        schema = client.get("/openapi.json").json()
    assert "QueryResponseModel" in schema["components"]["schemas"]
    route = schema["paths"]["/v2/agent/runs/{run_id}"]["get"]
    schema_ref = route["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert schema_ref.endswith("/QueryResponseModel")


def test_review_replay_after_sealing_still_requires_current_authorization(monkeypatch, tmp_path):
    _, store, run = setup_run(monkeypatch, tmp_path)
    url = f"/v2/agent/runs/{run.run_id}/issues/2/reviews"
    payload = {
        "decision": "approved",
        "corrections": [{"file": "service.py"}],
        "expected_review_version": 0,
        "idempotency_key": "review-before-sealing",
    }
    with TestClient(app, base_url="http://127.0.0.1", headers=AUTH) as client:
        first = client.post(url, json=payload)
        assert first.status_code == 200
        store.seal_evidence_set(
            run.run_id,
            2,
            [],
            EvidenceCollectionContext(
                snapshot_commit=run.snapshot.commit_oid, collector_protocol="test"
            ),
        )
        replay = client.post(url, json=payload)
        assert replay.status_code == 200, replay.text
        assert replay.json() == first.json()
        changed = client.post(url, json={**payload, "notes": "different payload"})
        assert changed.status_code == 409
        monkeypatch.setenv("RII_API_TOKEN", "x" * 40)
        assert client.post(url, json=payload).status_code == 401
        monkeypatch.setenv("RII_API_TOKEN", TOKEN)
        monkeypatch.setenv("RII_API_OPERATIONS", '["read"]')
        assert client.post(url, json=payload).status_code == 403
        monkeypatch.setenv("RII_API_OPERATIONS", '["read", "review"]')
        monkeypatch.setenv("RII_API_ANALYSIS_ROOTS", "[]")
        assert client.post(url, json=payload).status_code == 403
    assert store.get_issue(run.run_id, 2).review_version == 1
