from datetime import UTC, datetime
from pathlib import Path

from repo_issue_intelligence.investigator import (
    extract_issue_signals,
    investigate,
    locate_candidates,
)
from repo_issue_intelligence.models import IssueRecord
from repo_issue_intelligence.repository_index import build_repository_map


def issue(title: str, body: str) -> IssueRecord:
    timestamp = datetime(2026, 7, 30, tzinfo=UTC)
    return IssueRecord(
        number=1,
        title=title,
        body=body,
        created_at=timestamp,
        updated_at=timestamp,
    )


def write_source(repository: Path, relative_path: str, content: str) -> None:
    path = repository / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_extract_issue_signals_normalizes_paths_and_camel_case() -> None:
    record = issue(
        "StreamingResponse fails in BaseHTTPMiddleware",
        'File "E:\\env\\site-packages\\starlette\\responses.py", line 258\n'
        "`send_denial_response` raises RuntimeError.",
    )

    signals = extract_issue_signals(record)

    assert any(path.endswith("starlette/responses.py") for path in signals.paths)
    assert {"streaming", "response", "base", "http", "middleware"} <= signals.terms
    assert {"send_denial_response", "RuntimeError"} <= signals.identifiers
    assert {"StreamingResponse", "BaseHTTPMiddleware"} <= signals.primary_identifiers


def test_locate_candidates_prioritizes_exact_issue_path(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "starlette/middleware/base.py",
        "class BaseHTTPMiddleware:\n    pass\n",
    )
    write_source(
        repository,
        "starlette/applications.py",
        "class Starlette:\n    pass\n",
    )
    record = issue(
        "No response returned in BaseHTTPMiddleware",
        "Apply the fix in a/starlette/middleware/base.py.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert candidates[0].file == "starlette/middleware/base.py"
    assert "Issue references this exact source path" in candidates[0].evidence


def test_locate_candidates_uses_source_content_identifiers(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "typer/core.py",
        "def resolve_option(default, envvar):\n    return envvar or default\n",
    )
    write_source(
        repository,
        "typer/params.py",
        "class OptionInfo:\n    pass\n",
    )
    record = issue(
        "envvar not working for typer.Option",
        "The `envvar` value is ignored by `typer.Option`.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert candidates[0].file == "typer/core.py"
    assert any(
        "Source contains issue identifiers" in evidence
        for evidence in candidates[0].evidence
    )


def test_locate_candidates_prefers_compound_title_identifier(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "typer/core.py",
        "class TyperOption:\n    def resolve_envvar_value(self):\n        return None\n",
    )
    write_source(
        repository,
        "typer/main.py",
        "class Typer:\n    pass\n\ndef command():\n    pass\n",
    )
    record = issue(
        "envvar not working for `typer.Options`",
        "The reproduction uses `app.command` and `typer.Typer`.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert candidates[0].file == "typer/core.py"
    assert "Issue title strongly matches symbol TyperOption" in candidates[0].evidence


def test_investigation_keeps_twenty_candidates_for_reranking(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    for index in range(25):
        write_source(
            repository,
            f"src/token_handler_{index}.py",
            f"def handle_token_{index}():\n    return None\n",
        )
    record = issue("Token handler failure", "Token handling fails.")

    report = investigate(record, build_repository_map(repository))

    assert len(report.candidates) == 20
