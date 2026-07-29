from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

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

    client = GitHubClient(
        token=None,
        api_url="https://api.github.test",
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )
    try:
        issues = client.fetch_open_issues("example/project", limit=2)
    finally:
        client.close()

    assert [item.number for item in issues] == [100, 101]
    assert [request.url.params["page"] for request in requests] == ["1", "2"]


@pytest.mark.parametrize(
    "repository",
    [
        "invalid",
        "/project",
        "owner/",
        "owner/project/extra",
        "owner name/project",
        "owner/project name",
    ],
)
def test_rejects_invalid_repository_name(repository: str) -> None:
    client = GitHubClient(token=None, trust_env=False)
    try:
        with pytest.raises(ValueError, match="owner/name"):
            client.fetch_open_issues(repository, limit=1)
    finally:
        client.close()


def test_rejects_non_positive_limit() -> None:
    client = GitHubClient(token=None, trust_env=False)
    try:
        with pytest.raises(ValueError, match="at least 1"):
            client.fetch_open_issues("owner/project", limit=0)
    finally:
        client.close()


def test_fetch_issue_returns_closed_issue() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/example/project/issues/42"
        return httpx.Response(200, json=payload_item(42))

    client = GitHubClient(
        token=None,
        api_url="https://api.github.test",
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )
    try:
        issue = client.fetch_issue("example/project", 42)
    finally:
        client.close()

    assert issue.number == 42
    assert issue.author == "reporter"


def test_fetch_issue_rejects_pull_request_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload_item(42, pull_request=True))

    client = GitHubClient(
        token=None,
        api_url="https://api.github.test",
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )
    try:
        with pytest.raises(ValueError, match="must describe an issue"):
            client.fetch_issue("example/project", 42)
    finally:
        client.close()


def test_fetch_issue_follows_repository_redirect() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                301,
                headers={"location": "/repositories/123/issues/42"},
            )
        return httpx.Response(200, json=payload_item(42))

    client = GitHubClient(
        token=None,
        api_url="https://api.github.test",
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )
    try:
        issue = client.fetch_issue("example/project", 42)
    finally:
        client.close()

    assert issue.number == 42
    assert [request.url.path for request in requests] == [
        "/repos/example/project/issues/42",
        "/repositories/123/issues/42",
    ]
