from __future__ import annotations

from datetime import UTC, datetime

import httpx

from repo_issue_intelligence.github_client import GitHubClient


def payload_item(number: int, *, pull_request: bool = False) -> dict:
    timestamp = datetime(2026, 7, 27, tzinfo=UTC).isoformat().replace("+00:00", "Z")
    item = {
        "number": number,
        "title": f"Issue {number}",
        "body": "Details",
        "labels": [],
        "comments": 0,
        "created_at": timestamp,
        "updated_at": timestamp,
        "html_url": f"https://github.com/example/project/issues/{number}",
        "user": {"login": "reporter"},
    }
    if pull_request:
        item["pull_request"] = {"url": "https://api.github.com/example"}
    return item


def test_pagination_uses_stable_page_size_and_filters_pull_requests() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        page = int(request.url.params["page"])
        assert request.url.params["per_page"] == "100"
        if page == 1:
            return httpx.Response(
                200,
                json=[payload_item(number, pull_request=number < 100) for number in range(1, 101)],
            )
        if page == 2:
            return httpx.Response(200, json=[payload_item(101)])
        return httpx.Response(200, json=[])

    client = GitHubClient(token=None)
    client.client.close()
    client.client = httpx.Client(
        base_url="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )
    try:
        issues = client.fetch_open_issues("example/project", limit=2)
    finally:
        client.close()

    assert [item.number for item in issues] == [100, 101]
    assert [request.url.params["page"] for request in requests] == ["1", "2"]


def test_rejects_invalid_repository_name() -> None:
    client = GitHubClient(token=None)
    try:
        try:
            client.fetch_open_issues("invalid", limit=1)
        except ValueError as error:
            assert "owner/name" in str(error)
        else:
            raise AssertionError("Expected ValueError")
    finally:
        client.close()
