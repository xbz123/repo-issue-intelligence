from datetime import UTC, datetime

from fastapi.testclient import TestClient

from repo_issue_intelligence.agent_store import AgentStore
from repo_issue_intelligence.api import app, get_agent_store

client = TestClient(app)


def issue_payload(number: int = 1) -> dict:
    timestamp = datetime(2026, 7, 27, tzinfo=UTC).isoformat()
    return {
        "number": number,
        "title": "Expired token crashes refresh endpoint",
        "body": "Steps to reproduce are available. Production returns HTTP 500.",
        "labels": ["bug", "regression"],
        "comments_count": 3,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_score_issue() -> None:
    response = client.post("/v1/issues/score", json=issue_payload())
    assert response.status_code == 200
    assert response.json()["issue_number"] == 1


def test_rank_issues() -> None:
    response = client.post(
        "/v1/issues/rank",
        json={"issues": [issue_payload(1), issue_payload(2)]},
    )
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_index_missing_path() -> None:
    response = client.post(
        "/v1/repository/index",
        json={"path": "/path/that/does/not/exist"},
    )
    assert response.status_code == 404


def test_agent_run_query_and_review(tmp_path) -> None:
    store = AgentStore(tmp_path / "agent.sqlite3")
    app.dependency_overrides[get_agent_store] = lambda: store
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "service.py").write_text(
        "def refresh_token():\n    return None\n",
        encoding="utf-8",
    )
    try:
        response = client.post(
            "/v1/agent/runs",
            json={
                "issues": [issue_payload()],
                "repository_path": str(repository),
                "top_k": 1,
            },
        )
        assert response.status_code == 201
        payload = response.json()
        assert payload["status"] == "awaiting_review"
        run_id = payload["run_id"]

        query_response = client.get(f"/v1/agent/runs/{run_id}")
        assert query_response.status_code == 200
        assert query_response.json()["selected_issue_numbers"] == [1]

        review_response = client.post(
            f"/v1/agent/runs/{run_id}/review",
            json={"decision": "approved", "notes": "Reviewed in API test"},
        )
        assert review_response.status_code == 200
        assert review_response.json()["status"] == "approved"
    finally:
        app.dependency_overrides.clear()
