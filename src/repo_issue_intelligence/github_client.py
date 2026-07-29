from __future__ import annotations

import re
from datetime import datetime

import httpx

from .models import IssueRecord

REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9_.-]+")


class GitHubClient:
    def __init__(
        self,
        token: str | None,
        api_url: str = "https://api.github.com",
        *,
        transport: httpx.BaseTransport | None = None,
        trust_env: bool = True,
    ) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "repo-issue-intelligence",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.client = httpx.Client(
            base_url=api_url.rstrip("/"),
            headers=headers,
            timeout=30.0,
            transport=transport,
            trust_env=trust_env,
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    @staticmethod
    def _validate_repository(repository: str) -> tuple[str, str]:
        if REPOSITORY_PATTERN.fullmatch(repository) is None:
            raise ValueError("repository must use the owner/name format")
        owner, repo = repository.split("/", maxsplit=1)
        return owner, repo

    @staticmethod
    def _issue_record(item: dict) -> IssueRecord:
        return IssueRecord(
            number=item["number"],
            title=item["title"],
            body=item.get("body") or "",
            labels=[label["name"] for label in item.get("labels", [])],
            comments_count=item.get("comments", 0),
            created_at=datetime.fromisoformat(item["created_at"].replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(item["updated_at"].replace("Z", "+00:00")),
            html_url=item.get("html_url"),
            author=(item.get("user") or {}).get("login"),
        )

    def fetch_issue(self, repository: str, issue_number: int) -> IssueRecord:
        owner, repo = self._validate_repository(repository)
        if issue_number < 1:
            raise ValueError("issue_number must be at least 1")
        response = self.client.get(f"/repos/{owner}/{repo}/issues/{issue_number}")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or "pull_request" in payload:
            raise ValueError("GitHub issue response must describe an issue")
        return self._issue_record(payload)

    def fetch_open_issues(self, repository: str, limit: int = 100) -> list[IssueRecord]:
        owner, repo = self._validate_repository(repository)
        if limit < 1:
            raise ValueError("limit must be at least 1")

        collected: list[IssueRecord] = []
        page = 1
        per_page = 100

        while len(collected) < limit:
            response = self.client.get(
                f"/repos/{owner}/{repo}/issues",
                params={"state": "open", "per_page": per_page, "page": page},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise ValueError("GitHub issues response must be a list")
            if not payload:
                break

            for item in payload:
                # GitHub's issues endpoint also returns pull requests.
                if "pull_request" in item:
                    continue
                collected.append(self._issue_record(item))
                if len(collected) >= limit:
                    break

            if len(payload) < per_page:
                break
            page += 1

        return collected
