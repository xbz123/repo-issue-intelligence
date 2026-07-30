import subprocess
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
    assert "rich.console.C" not in extract_issue_signals(
        issue(
            "Console output",
            "See rich.console.Console and https://example.com/image.png.",
        )
    ).paths
    assert {"streaming", "response", "base", "http", "middleware"} <= signals.terms
    assert {"streaming", "response", "base", "http", "middleware"} <= (
        signals.primary_terms
    )
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


def test_repository_map_records_local_imports_and_called_symbols(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/package/api.py",
        "from .parser import parse_request\n\n"
        "def handle_request():\n"
        "    return parse_request()\n",
    )
    write_source(
        repository,
        "src/package/parser.py",
        "def parse_request():\n    return None\n",
    )

    repository_map = build_repository_map(repository)
    api = next(file for file in repository_map.files if file.path.endswith("api.py"))

    assert api.local_imports == ["src/package/parser.py"]
    assert api.local_import_symbols == {
        "src/package/parser.py": ["parse_request"]
    }
    assert "parse_request" in api.calls
    assert "parse_request" in api.references


def test_locate_candidates_propagates_local_import_evidence(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/package/api.py",
        "from .parser import parse_request\n\n"
        "def handle_request():\n"
        "    return parse_request()\n",
    )
    write_source(
        repository,
        "src/package/parser.py",
        "def parse_request():\n    return None\n",
    )
    record = issue(
        "API request failure",
        "The traceback points to src/package/api.py.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))
    parser = next(
        candidate
        for candidate in candidates
        if candidate.file == "src/package/parser.py"
    )

    assert (
        "Related source calls imported symbols defined here: parse_request"
        in parser.evidence
    )


def test_locate_candidates_maps_matching_test_to_source(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "tests/test_widget.py",
        "from package.widget import remove_children\n\n"
        "def test_reflow():\n"
        "    remove_children()\n",
    )
    write_source(
        repository,
        "src/package/widget.py",
        "def remove_children():\n    return None\n",
    )
    record = issue(
        "Widget reflow fails",
        "The failure occurs in tests/test_widget.py.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))
    widget = next(
        candidate
        for candidate in candidates
        if candidate.file == "src/package/widget.py"
    )

    assert "Matching test imports this source file: tests/test_widget.py" in widget.evidence


def test_locate_candidates_propagates_called_symbol_evidence(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/package/portal.py",
        "def start_portal(backend):\n"
        "    return backend.run_async_from_thread()\n",
    )
    write_source(
        repository,
        "src/package/backend.py",
        "def run_async_from_thread():\n    return None\n",
    )
    record = issue(
        "Portal fails under free threading",
        "The traceback points to src/package/portal.py.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))
    backend = next(
        candidate
        for candidate in candidates
        if candidate.file == "src/package/backend.py"
    )

    assert (
        "Related source calls run_async_from_thread, defined here: "
        "src/package/portal.py"
    ) in backend.evidence


def test_graph_reranking_expands_strong_relations_into_the_candidate_pool(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/seed.py",
        "from .target import target_operation\n\n"
        "def handle_failure():\n"
        "    return target_operation()\n",
    )
    write_source(
        repository,
        "src/target.py",
        "def target_operation():\n    return None\n",
    )
    for index in range(20):
        write_source(
            repository,
            f"src/shared_failure_{index}.py",
            "def shared_failure():\n    return None\n",
        )
    record = issue(
        "Shared failure",
        "The traceback points to src/seed.py.",
    )

    candidates = locate_candidates(
        record,
        build_repository_map(repository),
        limit=20,
    )

    assert len(candidates) == 20
    assert any(candidate.file == "src/target.py" for candidate in candidates)


def test_locate_candidates_uses_non_call_import_references(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/package/api.py",
        "from .result import ResultType\n\n"
        "def handle_failure(value):\n"
        "    return isinstance(value, ResultType)\n",
    )
    write_source(
        repository,
        "src/package/result.py",
        "class ResultType:\n    pass\n",
    )
    record = issue(
        "Handle failure",
        "The traceback points to src/package/api.py.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))
    result = next(
        candidate
        for candidate in candidates
        if candidate.file == "src/package/result.py"
    )

    assert (
        "Related source references imported symbols defined here: ResultType"
        in result.evidence
    )


def test_locate_candidates_uses_only_prior_git_cochanges(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/seed.py",
        "def handle_regression():\n    return None\n",
    )
    write_source(
        repository,
        "src/target.py",
        "def historical_regression():\n    return None\n",
    )
    for index in range(20):
        write_source(
            repository,
            f"src/shared_regression_{index}.py",
            "def shared_regression():\n    return None\n",
        )
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "Test User"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-qm", "initial"],
        check=True,
    )
    for revision in range(3):
        write_source(
            repository,
            "src/seed.py",
            f"def handle_regression():\n    return {revision}\n",
        )
        write_source(
            repository,
            "src/target.py",
            f"def historical_regression():\n    return {revision}\n",
        )
        subprocess.run(
            ["git", "-C", str(repository), "add", "src/seed.py", "src/target.py"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repository), "commit", "-qm", f"revision {revision}"],
            check=True,
        )

    candidates = locate_candidates(
        issue("Shared regression", "The traceback points to src/seed.py."),
        build_repository_map(repository),
        limit=20,
    )
    target = next(
        candidate for candidate in candidates if candidate.file == "src/target.py"
    )

    assert any(
        "Changed with lexical seed files in" in evidence
        for evidence in target.evidence
    )
    assert any(
        "Blame-selected seed line changed with this file" in evidence
        for evidence in target.evidence
    )


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
