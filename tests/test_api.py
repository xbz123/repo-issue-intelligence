from datetime import UTC, datetime

from fastapi.testclient import TestClient

from repo_issue_intelligence.api import app

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
