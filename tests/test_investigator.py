import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from repo_issue_intelligence.investigator import (
    _content_matches_identifier,
    _identifier_variants,
    _merge_tail_expansions,
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
    assert "send_denial_response" in signals.explicit_identifiers


def test_extract_issue_signals_records_called_identifiers_from_code_blocks() -> None:
    record = issue(
        "Height not refreshed after removing children",
        "```python\ndef compose():\n    return view.remove_children()\n```\n"
        "Expected ASGI message `websocket.accept`.",
    )

    signals = extract_issue_signals(record)

    assert {"view.remove_children", "remove_children"} <= (
        signals.explicit_identifiers
    )
    assert "compose" not in signals.explicit_identifiers
    assert "accept" not in signals.explicit_identifiers
    assert "accept" not in signals.content_terms


def test_extract_issue_signals_records_non_call_qualified_identifiers() -> None:
    record = issue(
        "Worker callback loses context",
        "```python\ncallback = WorkerThread.__init__\n```\n"
        "Traceback: ... in trio.WorkerThread.__init__",
    )

    signals = extract_issue_signals(record)

    assert {
        "WorkerThread.__init__",
        "trio.WorkerThread.__init__",
    } <= signals.explicit_identifiers
    assert "__init__" not in signals.explicit_identifiers


def test_identifier_variants_preserve_source_term_order() -> None:
    variants = _identifier_variants("is_alt_screen")

    assert "alt_screen" in variants
    assert "altscreen" in variants
    assert "screen_alt" not in variants


@pytest.mark.parametrize(
    ("identifier", "source"),
    [
        ("get", "def compute_target():\n    return None\n"),
        ("set", "def reset():\n    return None\n"),
        ("data", "metadata = {}\n"),
        ("run", "runner = callback\n"),
    ],
)
def test_content_identifier_matching_rejects_substrings(
    identifier: str,
    source: str,
) -> None:
    assert not _content_matches_identifier(source, identifier)


def test_content_identifier_matching_accepts_identifier_boundaries() -> None:
    assert _content_matches_identifier("def get():\n    return None\n", "get")
    assert _content_matches_identifier("response = client.get()\n", "get")
    assert _content_matches_identifier("def get_value():\n    return None\n", "get_value")


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


def test_locate_candidates_records_title_to_path_evidence(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/package/ansi.py",
        "def decode(value):\n"
        "    return value\n",
    )
    write_source(
        repository,
        "src/package/console.py",
        "def render_line(value):\n"
        "    return value\n",
    )

    candidates = locate_candidates(
        issue(
            "ANSI trailing newline is removed",
            "Console output loses the final line break.",
        ),
        build_repository_map(repository),
    )
    ansi = next(
        candidate
        for candidate in candidates
        if candidate.file == "src/package/ansi.py"
    )

    assert "Path matches issue title terms: ansi" in ansi.evidence


def test_tail_expansions_do_not_evict_directly_supported_base_path() -> None:
    merged = _merge_tail_expansions(
        ["first.py", "second.py", "direct.py"],
        ["expanded.py"],
        ["direct.py"],
        limit=3,
    )

    assert merged == ["first.py", "direct.py", "expanded.py"]


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
    assert candidates[0].symbol == "resolve_envvar_value"


def test_locate_candidates_reranks_functions_within_selected_file(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "typer/core.py",
        "class TyperOption:\n"
        "    pass\n\n"
        "def value_from_envvar(value):\n"
        "    return value\n\n"
        "def _typer_format_options(options):\n"
        "    return options\n",
    )
    write_source(
        repository,
        "typer/main.py",
        "class Typer:\n    pass\n",
    )
    record = issue(
        "envvar not working for `typer.Options`",
        "The envvar value is ignored by typer.Option.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert candidates[0].file == "typer/core.py"
    assert candidates[0].symbol == "value_from_envvar"


def test_locate_candidates_normalizes_morphology_for_symbol_reranking(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "typer/rich_utils.py",
        "def _make_rich_text(text):\n"
        '    \"\"\"Return styled text.\"\"\"\n'
        "    return text\n\n"
        "def _get_parameter_help(text):\n"
        '    \"\"\"Build help text for a parameter.\"\"\"\n'
        "    return text\n",
    )
    record = issue(
        "Help width is miscalculated for stylized text",
        "The option help frame is misaligned.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert candidates[0].symbol == "_make_rich_text"


def test_locate_candidates_preserves_plural_line_semantics(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "rich/ansi.py",
        "def decode(lines):\n"
        '    \"\"\"Decode an iterable of ANSI lines.\"\"\"\n'
        "    return lines\n\n"
        "def decode_line(line):\n"
        '    \"\"\"Decode one ANSI line.\"\"\"\n'
        "    return line\n",
    )
    record = issue(
        "Trailing line break removed by `Text.from_ansi`",
        "ANSI text loses its final newline.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert candidates[0].symbol == "decode"


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
    assert api.name_calls == ["parse_request"]
    assert api.symbol_calls == {"handle_request": ["parse_request"]}
    assert api.qualified_symbol_calls == {
        "handle_request": ["parse_request"]
    }
    assert [
        call.model_dump()
        for call in api.resolved_calls
    ] == [
        {
            "caller": "handle_request",
            "local_name": "parse_request",
            "target_file": "src/package/parser.py",
            "target_symbol": "parse_request",
        }
    ]
    assert "parse_request" in api.references


@pytest.mark.parametrize(
    ("module_path", "import_name"),
    [
        ("src.py", "src"),
        ("lib.py", "lib"),
        ("lib/__init__.py", "lib"),
    ],
    ids=["src-module", "lib-module", "lib-package"],
)
def test_repository_map_preserves_top_level_src_and_lib_modules(
    tmp_path: Path,
    module_path: str,
    import_name: str,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "app.py",
        f"from {import_name} import helper\n\n"
        "def handle_request():\n"
        "    return helper()\n",
    )
    write_source(
        repository,
        module_path,
        "def helper():\n    return None\n",
    )

    repository_map = build_repository_map(repository)
    app = next(file for file in repository_map.files if file.path == "app.py")

    assert app.local_imports == [module_path]
    assert [
        call.model_dump()
        for call in app.resolved_calls
    ] == [
        {
            "caller": "handle_request",
            "local_name": "helper",
            "target_file": module_path,
            "target_symbol": "helper",
        }
    ]


@pytest.mark.parametrize("root_package", ["src", "lib"])
def test_repository_map_preserves_real_src_and_lib_packages(
    tmp_path: Path,
    root_package: str,
) -> None:
    repository = tmp_path / "repository"
    write_source(repository, f"{root_package}/__init__.py", "")
    write_source(
        repository,
        f"{root_package}/helpers.py",
        "def helper():\n    return None\n",
    )
    write_source(
        repository,
        "app.py",
        f"from {root_package}.helpers import helper\n\n"
        "def handle_request():\n"
        "    return helper()\n",
    )

    repository_map = build_repository_map(repository)
    app = next(file for file in repository_map.files if file.path == "app.py")

    assert app.local_imports == [f"{root_package}/helpers.py"]
    assert [
        (call.target_file, call.target_symbol)
        for call in app.resolved_calls
    ] == [(f"{root_package}/helpers.py", "helper")]


def test_repository_map_keeps_standard_src_layout_imports(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/package/api.py",
        "from package.parser import parse_request\n\n"
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
    assert [
        (call.target_file, call.target_symbol)
        for call in api.resolved_calls
    ] == [("src/package/parser.py", "parse_request")]


def test_repository_map_records_qualified_method_names(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/package/thread_cache.py",
        "class Unrelated:\n"
        "    def __init__(self):\n"
        "        pass\n\n"
        "class WorkerThread:\n"
        "    def __init__(self):\n"
        "        pass\n",
    )

    repository_map = build_repository_map(repository)
    source = next(
        file
        for file in repository_map.files
        if file.path == "src/package/thread_cache.py"
    )

    assert {
        symbol.qualified_name
        for symbol in source.symbols
        if symbol.name == "__init__"
    } == {"Unrelated.__init__", "WorkerThread.__init__"}


def test_duplicate_method_call_edges_do_not_leak_into_symbol_selection(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/package/worker.py",
        "class Cache:\n"
        "    def refresh(self):\n"
        "        return rebuild()\n\n"
        "class Worker:\n"
        "    def refresh(self):\n"
        '        """Retry failed request."""\n'
        "        return None\n\n"
        "def retry_failed_request():\n"
        '    """Retry failed request."""\n'
        "    return rebuild()\n\n"
        "def rebuild():\n"
        "    return None\n",
    )
    repository_map = build_repository_map(repository)
    source = next(
        file
        for file in repository_map.files
        if file.path == "src/package/worker.py"
    )

    assert source.qualified_symbol_calls == {
        "Cache.refresh": ["rebuild"],
        "retry_failed_request": ["rebuild"],
    }

    candidates = locate_candidates(
        issue(
            "Worker retry failed request",
            "The worker refresh path does not retry the failed request.",
        ),
        repository_map,
    )

    assert candidates[0].qualified_symbol == "Worker.refresh"
    assert not any(
        "Issue-matching symbols call rebuild" in evidence
        for evidence in candidates[0].evidence
    )

    legacy_repository_map = repository_map.model_copy(deep=True)
    legacy_repository_map.files[0].qualified_symbol_calls = {}
    legacy_repository_map.files[0].resolved_calls = []
    legacy_candidates = locate_candidates(
        issue(
            "Worker retry failed request",
            "The worker refresh path does not retry the failed request.",
        ),
        legacy_repository_map,
    )

    assert legacy_candidates[0].qualified_symbol == "Worker.refresh"
    assert not any(
        "Issue-matching symbols call rebuild" in evidence
        for evidence in legacy_candidates[0].evidence
    )


def test_duplicate_qualified_caller_identity_does_not_create_call_edge(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/package/worker.py",
        "class Worker:\n"
        "    def refresh_failed_request(self):\n"
        "        return rebuild()\n\n"
        "    def refresh_failed_request(self):\n"
        "        return None\n\n"
        "def rebuild():\n"
        "    return None\n",
    )

    repository_map = build_repository_map(repository)
    source = next(
        file
        for file in repository_map.files
        if file.path == "src/package/worker.py"
    )

    assert not any(
        call.caller == "Worker.refresh_failed_request"
        for call in source.resolved_calls
    )


def test_method_call_relation_evidence_uses_qualified_callers(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/package/retry.py",
        "class Cache:\n"
        "    def retry_failed_request(self):\n"
        "        return rebuild()\n\n"
        "class Worker:\n"
        "    def refresh_failed_request(self):\n"
        "        return rebuild()\n\n"
        "def rebuild():\n"
        "    return None\n",
    )

    candidates = locate_candidates(
        issue(
            "Retry failed request",
            "Both retry paths fail before rebuilding state.",
        ),
        build_repository_map(repository),
    )

    assert candidates[0].symbol == "rebuild"
    assert (
        "Issue-matching symbols call rebuild: "
        "Cache.retry_failed_request, Worker.refresh_failed_request"
        in candidates[0].evidence
    )


def test_attribute_calls_do_not_resolve_to_same_named_local_function(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/package/retry.py",
        "class Cache:\n"
        "    def retry_failed_request(self):\n"
        "        return self.rebuild()\n\n"
        "class Worker:\n"
        "    def refresh_failed_request(self):\n"
        "        return backend.rebuild()\n\n"
        "def rebuild():\n"
        "    return None\n",
    )
    repository_map = build_repository_map(repository)
    source = next(
        file
        for file in repository_map.files
        if file.path == "src/package/retry.py"
    )

    assert source.calls == ["rebuild"]
    assert source.name_calls == []
    assert source.qualified_symbol_calls == {}

    candidates = locate_candidates(
        issue(
            "Retry failed request",
            "Both retry paths fail before rebuilding state.",
        ),
        repository_map,
    )

    assert candidates[0].symbol != "rebuild"
    assert not any(
        "Issue-matching symbols call rebuild" in evidence
        for evidence in candidates[0].evidence
    )


def test_parameter_calls_do_not_resolve_to_same_named_module_function(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/package/retry.py",
        "def retry_failed_request(rebuild):\n"
        "    return rebuild()\n\n"
        "def refresh_failed_request(rebuild):\n"
        "    return rebuild()\n\n"
        "def rebuild():\n"
        "    return None\n",
    )
    repository_map = build_repository_map(repository)
    source = next(
        file
        for file in repository_map.files
        if file.path == "src/package/retry.py"
    )

    assert source.name_calls == []
    assert source.qualified_symbol_calls == {}

    candidates = locate_candidates(
        issue(
            "Retry failed request",
            "Both retry paths fail before rebuilding state.",
        ),
        repository_map,
    )

    assert candidates[0].symbol != "rebuild"
    assert not any(
        "Issue-matching symbols call rebuild" in evidence
        for evidence in candidates[0].evidence
    )


def test_assigned_callable_does_not_resolve_to_same_named_module_function(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/package/retry.py",
        "def retry_failed_request(callback):\n"
        "    rebuild = callback\n"
        "    return rebuild()\n\n"
        "def refresh_failed_request(callback):\n"
        "    rebuild = callback\n"
        "    return rebuild()\n\n"
        "def rebuild():\n"
        "    return None\n",
    )
    repository_map = build_repository_map(repository)
    source = next(
        file
        for file in repository_map.files
        if file.path == "src/package/retry.py"
    )

    assert source.name_calls == []
    assert source.qualified_symbol_calls == {}

    candidates = locate_candidates(
        issue(
            "Retry failed request",
            "Both retry paths fail before rebuilding state.",
        ),
        repository_map,
    )

    assert candidates[0].symbol != "rebuild"
    assert not any(
        "Issue-matching symbols call rebuild" in evidence
        for evidence in candidates[0].evidence
    )


def test_shadowed_import_call_does_not_create_cross_file_relation(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/package/api.py",
        "from .backend import rebuild\n\n"
        "def retry_failed_request(rebuild):\n"
        "    return rebuild()\n",
    )
    write_source(
        repository,
        "src/package/backend.py",
        "def rebuild():\n"
        "    return None\n",
    )
    repository_map = build_repository_map(repository)
    api = next(
        file
        for file in repository_map.files
        if file.path == "src/package/api.py"
    )

    assert api.name_calls == []
    assert api.qualified_symbol_calls == {}

    candidates = locate_candidates(
        issue(
            "Retry failed request",
            "The traceback points to src/package/api.py.",
        ),
        repository_map,
    )
    backend = next(
        candidate
        for candidate in candidates
        if candidate.file == "src/package/backend.py"
    )

    assert not any(
        "calls imported symbols" in evidence
        or "references imported symbols" in evidence
        for evidence in backend.evidence
    )


def test_import_alias_resolves_to_the_exact_exported_function(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/package/api.py",
        "from .parser import parse_request as parse\n\n"
        "def handle_request():\n"
        "    return parse()\n",
    )
    write_source(
        repository,
        "src/package/parser.py",
        "def parse_request():\n"
        "    return None\n",
    )

    repository_map = build_repository_map(repository)
    api = next(
        file
        for file in repository_map.files
        if file.path == "src/package/api.py"
    )

    assert [
        call.model_dump()
        for call in api.resolved_calls
    ] == [
        {
            "caller": "handle_request",
            "local_name": "parse",
            "target_file": "src/package/parser.py",
            "target_symbol": "parse_request",
        }
    ]

    candidates = locate_candidates(
        issue(
            "API request failure",
            "The traceback points to src/package/api.py.",
        ),
        repository_map,
    )
    parser = next(
        candidate
        for candidate in candidates
        if candidate.file == "src/package/parser.py"
    )

    assert (
        "Related source calls imported symbols defined here: parse_request"
        in parser.evidence
    )


def test_unimported_name_does_not_resolve_to_same_named_external_function(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/package/api.py",
        "def handle_request():\n"
        "    return parse_request()\n",
    )
    write_source(
        repository,
        "src/package/parser.py",
        "def parse_request():\n"
        "    return None\n",
    )
    repository_map = build_repository_map(repository)
    api = next(
        file
        for file in repository_map.files
        if file.path == "src/package/api.py"
    )

    assert api.resolved_calls == []

    candidates = locate_candidates(
        issue(
            "API request failure",
            "The traceback points to src/package/api.py.",
        ),
        repository_map,
    )
    parser = next(
        candidate
        for candidate in candidates
        if candidate.file == "src/package/parser.py"
    )

    assert not any(
        "Related source calls" in evidence
        for evidence in parser.evidence
    )


@pytest.mark.parametrize(
    "caller_source",
    [
        (
            "def caller(callbacks):\n"
            "    for rebuild in callbacks:\n"
            "        return rebuild()\n"
        ),
        (
            "def caller(context):\n"
            "    with context as rebuild:\n"
            "        return rebuild()\n"
        ),
        (
            "def caller():\n"
            "    try:\n"
            "        raise RuntimeError\n"
            "    except RuntimeError as rebuild:\n"
            "        return rebuild()\n"
        ),
        (
            "def caller():\n"
            "    def rebuild():\n"
            "        return None\n"
            "    return rebuild()\n"
        ),
        (
            "def caller():\n"
            "    from .backend import rebuild\n"
            "    return rebuild()\n"
        ),
        (
            "def caller():\n"
            "    global rebuild\n"
            "    return rebuild()\n"
        ),
        (
            "def caller(callbacks):\n"
            "    return [rebuild() for rebuild in callbacks]\n"
        ),
        (
            "def caller(value):\n"
            "    match value:\n"
            "        case {'callback': rebuild}:\n"
            "            return rebuild()\n"
            "    return None\n"
        ),
    ],
    ids=[
        "for-target",
        "with-target",
        "except-target",
        "nested-function",
        "local-import",
        "declared-global",
        "comprehension-target",
        "match-target",
    ],
)
def test_lexical_bindings_are_not_resolved_to_module_function(
    tmp_path: Path,
    caller_source: str,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/package/retry.py",
        caller_source
        + "\n"
        + "def rebuild():\n"
        + "    return None\n",
    )
    write_source(
        repository,
        "src/package/backend.py",
        "def rebuild():\n"
        "    return None\n",
    )

    repository_map = build_repository_map(repository)
    source = next(
        file
        for file in repository_map.files
        if file.path == "src/package/retry.py"
    )

    assert not any(
        call.caller == "caller" and call.target_symbol == "rebuild"
        for call in source.resolved_calls
    )


def test_free_variable_call_is_not_resolved_to_module_function(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/package/retry.py",
        "def outer(rebuild):\n"
        "    def caller():\n"
        "        return rebuild()\n"
        "    return caller\n\n"
        "def outer_nonlocal(callback):\n"
        "    rebuild = callback\n"
        "    def caller():\n"
        "        nonlocal rebuild\n"
        "        return rebuild()\n"
        "    return caller\n\n"
        "def rebuild():\n"
        "    return None\n",
    )

    repository_map = build_repository_map(repository)
    source = next(
        file
        for file in repository_map.files
        if file.path == "src/package/retry.py"
    )

    assert not any(
        call.caller == "outer.caller" and call.target_symbol == "rebuild"
        for call in source.resolved_calls
    )
    assert not any(
        call.caller == "outer_nonlocal.caller"
        and call.target_symbol == "rebuild"
        for call in source.resolved_calls
    )


def test_global_mutation_invalidates_module_function_resolution(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/package/retry.py",
        "def replace_rebuild(callback):\n"
        "    global rebuild\n"
        "    rebuild = callback\n\n"
        "def caller():\n"
        "    return rebuild()\n\n"
        "def rebuild():\n"
        "    return None\n",
    )

    repository_map = build_repository_map(repository)
    source = next(
        file
        for file in repository_map.files
        if file.path == "src/package/retry.py"
    )

    assert not any(
        call.caller == "caller" and call.target_symbol == "rebuild"
        for call in source.resolved_calls
    )


@pytest.mark.parametrize(
    "definition_time_rebinding",
    [
        "def configure(value=(rebuild := (lambda: None))):\n"
        "    return value\n",
        "def decorator(value):\n"
        "    return value\n\n"
        "@(rebuild := decorator)\n"
        "class Config:\n"
        "    pass\n",
    ],
    ids=["function-default", "class-decorator"],
)
def test_definition_time_rebinding_invalidates_module_function_resolution(
    tmp_path: Path,
    definition_time_rebinding: str,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/package/retry.py",
        "def rebuild():\n"
        "    return None\n\n"
        + definition_time_rebinding
        + "\n"
        + "def caller():\n"
        + "    return rebuild()\n",
    )

    repository_map = build_repository_map(repository)
    source = next(
        file
        for file in repository_map.files
        if file.path == "src/package/retry.py"
    )

    assert not any(
        call.caller == "caller" and call.target_symbol == "rebuild"
        for call in source.resolved_calls
    )


def test_class_and_static_methods_resolve_unshadowed_module_function(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/package/retry.py",
        "class Worker:\n"
        "    @classmethod\n"
        "    def retry_failed_request(cls):\n"
        "        return rebuild()\n\n"
        "    @staticmethod\n"
        "    def refresh_failed_request():\n"
        "        return rebuild()\n\n"
        "def rebuild():\n"
        "    return None\n",
    )

    repository_map = build_repository_map(repository)
    source = next(
        file
        for file in repository_map.files
        if file.path == "src/package/retry.py"
    )

    assert {
        (call.caller, call.target_symbol)
        for call in source.resolved_calls
    } == {
        ("Worker.refresh_failed_request", "rebuild"),
        ("Worker.retry_failed_request", "rebuild"),
    }


def test_locate_candidates_uses_class_context_to_disambiguate_methods(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/package/thread_cache.py",
        "class Unrelated:\n"
        "    def __init__(self):\n"
        "        pass\n\n"
        "class WorkerThread:\n"
        "    def __init__(self):\n"
        "        self.context = None\n\n"
        "    def _work(self):\n"
        "        return self.context\n",
    )
    record = issue(
        "ThreadCache workers leak context",
        "The `__init__` method on `WorkerThreads` retains the spawning context.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert candidates[0].symbol == "__init__"
    assert candidates[0].qualified_symbol == "WorkerThread.__init__"
    assert any(
        "Symbol WorkerThread.__init__ matches" in evidence
        for evidence in candidates[0].evidence
    )


def test_qualified_owner_match_does_not_treat_body_method_as_class_match(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "typer/core.py",
        "class TyperOption:\n"
        "    def value_from_envvar(self):\n"
        "        return None\n\n"
        "class TyperCommand:\n"
        "    def main(self):\n"
        "        return None\n",
    )
    record = issue(
        "envvar not working for `typer.Options`",
        "The example calls app.command and uses __main__.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert candidates[0].symbol == "value_from_envvar"
    assert candidates[0].qualified_symbol == "TyperOption.value_from_envvar"


def test_explicit_local_symbol_reference_ranks_before_owner_match(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "cache.py",
        "class Cache:\n"
        "    def clear(self):\n"
        "        return None\n\n"
        "def flush():\n"
        "    return None\n",
    )
    record = issue(
        "Flush bug",
        "The `Cache` invokes `flush`.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert candidates[0].symbol == "flush"
    assert candidates[0].qualified_symbol is None


def test_duplicate_local_symbol_reference_is_scoped_to_its_owner(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "responses.py",
        "class Response:\n"
        "    async def __call__(self):\n"
        "        return None\n\n"
        "def stream_response():\n"
        "    return None\n",
    )
    write_source(
        repository,
        "routing.py",
        "class BaseRoute:\n"
        "    async def __call__(self):\n"
        "        return None\n\n"
        "def resolve_route():\n"
        "    return None\n",
    )
    write_source(
        repository,
        "middleware.py",
        "class Middleware:\n"
        "    async def __call__(self):\n"
        "        return None\n\n"
        "def unrelated_helper():\n"
        "    return None\n",
    )
    record = issue(
        "Streaming response route failure",
        "The `Response` implementation of `__call__` fails while streaming.",
    )

    candidates = {
        candidate.file: candidate
        for candidate in locate_candidates(record, build_repository_map(repository))
    }

    assert candidates["responses.py"].qualified_symbol == "Response.__call__"
    assert candidates["routing.py"].symbol == "resolve_route"
    assert candidates["middleware.py"].symbol is None


def test_duplicate_local_symbol_reference_is_scoped_to_its_path(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "alpha.py",
        "def send():\n"
        "    return None\n\n"
        "def alpha_handler():\n"
        "    return None\n",
    )
    write_source(
        repository,
        "beta.py",
        "def send():\n"
        "    return None\n\n"
        "def beta_handler():\n"
        "    return None\n",
    )
    record = issue(
        "Transport failure",
        'The `send` function fails.\nFile "/repo/alpha.py", line 1',
    )

    candidates = {
        candidate.file: candidate
        for candidate in locate_candidates(record, build_repository_map(repository))
    }

    assert candidates["alpha.py"].symbol == "send"
    assert candidates["beta.py"].symbol is None


def test_ambiguous_basename_does_not_scope_bare_symbol_reference(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/base.py",
        "def send():\n"
        "    return None\n\n"
        "def source_handler():\n"
        "    return None\n",
    )
    write_source(
        repository,
        "tests/base.py",
        "def send():\n"
        "    return None\n\n"
        "def test_handler():\n"
        "    return None\n",
    )
    record = issue(
        "Handler transport failure",
        "The `send` function fails in `base.py`.",
    )

    candidates = {
        candidate.file: candidate
        for candidate in locate_candidates(record, build_repository_map(repository))
    }

    assert candidates["src/base.py"].symbol != "send"
    assert candidates["tests/base.py"].symbol != "send"

    scoped_record = issue(
        "Handler transport failure",
        "The `send` function fails in `src/base.py`.",
    )
    scoped_candidates = {
        candidate.file: candidate
        for candidate in locate_candidates(
            scoped_record,
            build_repository_map(repository),
        )
    }

    assert scoped_candidates["src/base.py"].symbol == "send"
    assert scoped_candidates["tests/base.py"].symbol != "send"


def test_bare_symbol_can_be_unique_in_final_candidate_range(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "layout.py",
        "class Widget:\n"
        "    def remove_children(self):\n"
        '        """Remove layout children and refresh height."""\n'
        "        return None\n",
    )
    write_source(
        repository,
        "tree.py",
        "class Tree:\n"
        "    def remove_children(self):\n"
        '        """Remove child tree nodes."""\n'
        "        return None\n",
    )
    record = issue(
        "Height not refreshed after removing children",
        "```python\nawait view.remove_children()\n```",
    )

    candidates = locate_candidates(
        record,
        build_repository_map(repository),
        limit=1,
    )

    assert candidates[0].file == "layout.py"
    assert candidates[0].qualified_symbol == "Widget.remove_children"
    assert (
        "Issue references symbol Widget.remove_children"
        in candidates[0].evidence
    )


def test_fenced_qualified_symbol_reference_disambiguates_methods(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "thread_cache.py",
        "class Unrelated:\n"
        "    def __init__(self):\n"
        "        pass\n\n"
        "class WorkerThread:\n"
        "    def __init__(self):\n"
        "        self.context = None\n",
    )
    record = issue(
        "Worker callback loses context",
        "```python\ncallback = WorkerThread.__init__\n```",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert candidates[0].qualified_symbol == "WorkerThread.__init__"


def test_owner_terms_do_not_change_title_semantic_ranking(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "starlette/requests.py",
        "def cookie_parser(cookie):\n"
        '    \"\"\"Parse a cookie header.\"\"\"\n'
        "    return cookie\n\n"
        "class Request:\n"
        "    async def send_push_promise(self):\n"
        "        return None\n",
    )
    record = issue(
        "SessionMiddleware sends a new set-cookie for every request",
        "The cookie should not be replaced when session data is unchanged.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert candidates[0].symbol == "cookie_parser"
    assert candidates[0].qualified_symbol is None


def test_protocol_event_does_not_match_python_qualified_symbol(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "starlette/websockets.py",
        "class WebSocket:\n"
        "    async def accept(self):\n"
        "        return None\n\n"
        "    async def send_denial_response(self):\n"
        "        return None\n",
    )
    record = issue(
        "Can not use `send_denial_response`",
        "Expected ASGI message `websocket.accept`.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert candidates[0].symbol == "send_denial_response"
    assert candidates[0].qualified_symbol == "WebSocket.send_denial_response"
    assert not any(
        "Issue references symbol WebSocket.accept" in evidence
        for evidence in candidates[0].evidence
    )


def test_protocol_event_does_not_fall_back_to_local_method(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "starlette/websockets.py",
        "class WebSocket:\n"
        "    async def accept(self):\n"
        "        return None\n\n"
        "    async def close(self):\n"
        "        return None\n\n"
        "    async def send_denial_response(self):\n"
        "        return None\n",
    )
    record = issue(
        "ASGI handshake failure",
        "Expected message `websocket.accept`.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert all(candidate.symbol != "accept" for candidate in candidates)
    assert not any(
        "Issue references symbol WebSocket.accept" in evidence
        for candidate in candidates
        for evidence in candidate.evidence
    )
    assert not any(
        "Source contains issue identifiers: websocket.accept" in evidence
        for candidate in candidates
        for evidence in candidate.evidence
    )
    assert not any(
        evidence.startswith("Source content matches issue terms:")
        and "accept" in evidence
        for candidate in candidates
        for evidence in candidate.evidence
    )

    title_candidates = locate_candidates(
        issue(
            "ASGI `websocket.accept` handshake failure",
            "The protocol event is rejected.",
        ),
        build_repository_map(repository),
    )

    assert all(candidate.symbol != "accept" for candidate in title_candidates)

    call_candidates = locate_candidates(
        issue(
            "Denial response call fails",
            "```python\nawait ws.send_denial_response()\n```",
        ),
        build_repository_map(repository),
    )

    assert any(
        "Source contains issue identifiers: send_denial_response" in evidence
        for candidate in call_candidates
        for evidence in candidate.evidence
    )


def test_exact_python_qualified_reference_matches_with_case_and_boundaries(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "starlette/websockets.py",
        "class WebSocket:\n"
        "    async def accept(self):\n"
        "        return None\n\n"
        "    async def send_denial_response(self):\n"
        "        return None\n",
    )
    record = issue(
        "`WebSocket.accept` fails",
        "The Python method raises before accepting the connection.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert candidates[0].symbol == "accept"
    assert candidates[0].qualified_symbol == "WebSocket.accept"


def test_locate_candidates_propagates_within_file_call_evidence(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/package/compositor.py",
        "def reflow(children):\n"
        '    \"\"\"Reflow children widgets after removal.\"\"\"\n'
        "    return _arrange_root(children)\n\n"
        "def reflow_visible(children):\n"
        '    \"\"\"Reflow visible children widgets after removal.\"\"\"\n'
        "    return _arrange_root(children)\n\n"
        "def _arrange_root(children):\n"
        '    \"\"\"Arrange the root layout.\"\"\"\n'
        "    return children\n\n"
        "def __contains__(widget):\n"
        '    \"\"\"Check the previous refresh.\"\"\"\n'
        "    return False\n",
    )
    record = issue(
        "Height not refreshed after removing children",
        "The child widgets are removed but the container keeps its old height.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert candidates[0].symbol == "_arrange_root"
    assert (
        "Issue-matching symbols call _arrange_root: reflow, reflow_visible"
        in candidates[0].evidence
    )


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


def test_locate_candidates_skips_unresolved_attribute_call_evidence(
    tmp_path: Path,
) -> None:
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
    ) not in backend.evidence


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


def test_graph_reranking_promotes_bounded_two_hop_call_chain(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/seed.py",
        "from .screen import refresh_layout\n\n"
        "def update_height():\n"
        "    return refresh_layout()\n",
    )
    write_source(
        repository,
        "src/screen.py",
        "from .compositor import reflow_children\n\n"
        "def refresh_layout():\n"
        "    return reflow_children()\n",
    )
    write_source(
        repository,
        "src/compositor.py",
        "def reflow_children(children):\n"
        "    return arrange_children(children)\n\n"
        "def refresh_children(children):\n"
        "    return children\n\n"
        "def remove_children(children):\n"
        "    return children\n\n"
        "def arrange_children(children):\n"
        "    return children\n",
    )
    write_source(
        repository,
        "src/height_children_refresh.py",
        "def refresh_height(children):\n"
        "    return refresh_children(children)\n",
    )
    write_source(
        repository,
        "src/remove_children_height.py",
        "def update_height(children):\n"
        "    return remove_children(children)\n",
    )
    for index in range(20):
        write_source(
            repository,
            f"src/height_refresh_{index}.py",
            "def refresh_height():\n    return None\n",
        )
    record = issue(
        "Height not refreshed after removing children",
        "The traceback points to src/seed.py.",
    )

    candidates = locate_candidates(
        record,
        build_repository_map(repository),
        limit=20,
    )
    compositor_rank = next(
        index
        for index, candidate in enumerate(candidates, start=1)
        if candidate.file == "src/compositor.py"
    )
    compositor = candidates[compositor_rank - 1]

    assert compositor_rank <= 10
    assert (
        "Two-hop source call chain via refresh_layout reaches "
        "reflow_children, defined here: src/screen.py"
    ) in compositor.evidence


def test_graph_reranking_skips_ambiguous_first_hop_callers(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/seed.py",
        "def update_height():\n"
        "    return refresh_layout()\n",
    )
    write_source(
        repository,
        "src/screen.py",
        "class Cache:\n"
        "    def refresh_layout(self):\n"
        "        return rebuild_layout()\n\n"
        "class Worker:\n"
        "    def refresh_layout(self):\n"
        "        return None\n",
    )
    write_source(
        repository,
        "src/rebuild.py",
        "def rebuild_layout():\n"
        "    return None\n",
    )
    record = issue(
        "Refresh layout fails",
        "The traceback points to src/seed.py.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert all(
        not any(
            evidence.startswith("Two-hop source call chain via ")
            for evidence in candidate.evidence
        )
        for candidate in candidates
    )


def test_graph_reranking_skips_unresolved_attribute_first_hop(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/seed.py",
        "def update(view):\n"
        "    return view.refresh_layout()\n",
    )
    write_source(
        repository,
        "src/screen.py",
        "class Cache:\n"
        "    def refresh_layout(self):\n"
        "        return rebuild_layout()\n",
    )
    write_source(
        repository,
        "src/rebuild.py",
        "def rebuild_layout():\n"
        "    return None\n",
    )
    record = issue(
        "Refresh layout fails",
        "The traceback points to src/seed.py.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert all(
        not any(
            evidence.startswith("Two-hop source call chain via ")
            for evidence in candidate.evidence
        )
        for candidate in candidates
    )


def test_graph_reranking_skips_callback_parameter_first_hop(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/seed.py",
        "def update(refresh_layout):\n"
        "    return refresh_layout()\n",
    )
    write_source(
        repository,
        "src/screen.py",
        "def refresh_layout():\n"
        "    return reflow_children()\n",
    )
    write_source(
        repository,
        "src/rebuild.py",
        "def reflow_children():\n"
        "    return None\n",
    )
    record = issue(
        "Refresh layout fails",
        "The traceback points to src/seed.py.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert all(
        not any(
            evidence.startswith("Related source calls refresh_layout")
            or evidence.startswith("Two-hop source call chain via ")
            for evidence in candidate.evidence
        )
        for candidate in candidates
    )


def test_graph_reranking_does_not_propagate_through_abstract_layer(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/backend.py",
        "from ._abc import start_soon\n\n"
        "def start_task():\n"
        "    return start_soon()\n",
    )
    write_source(
        repository,
        "src/_abc.py",
        "from .asyncio_backend import create_task\n\n"
        "def start_soon():\n"
        "    return create_task()\n",
    )
    write_source(
        repository,
        "src/asyncio_backend.py",
        "def create_task():\n"
        "    return None\n",
    )
    record = issue(
        "Task start fails on Trio",
        "The traceback points to src/backend.py.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert all(
        not any(
            evidence.startswith("Two-hop source call chain via ")
            for evidence in candidate.evidence
        )
        for candidate in candidates
    )


def test_graph_reranking_does_not_resolve_local_callee_to_unrelated_file(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/seed.py",
        "from .screen import refresh_layout\n\n"
        "def update_height():\n"
        "    return refresh_layout()\n",
    )
    write_source(
        repository,
        "src/screen.py",
        "def refresh_layout():\n"
        "    return arrange_children()\n\n"
        "def arrange_children():\n"
        "    return None\n",
    )
    write_source(
        repository,
        "src/unrelated.py",
        "def arrange_children():\n"
        "    return None\n",
    )
    record = issue(
        "Height refresh fails after removing children",
        "The traceback points to src/seed.py.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))
    unrelated = next(
        candidate
        for candidate in candidates
        if candidate.file == "src/unrelated.py"
    )

    assert all(
        not evidence.startswith("Two-hop source call chain via ")
        for evidence in unrelated.evidence
    )


def test_graph_reranking_does_not_propagate_through_auxiliary_file(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/seed.py",
        "from docs.refresh import refresh_layout\n\n"
        "def update_layout():\n"
        "    return refresh_layout()\n",
    )
    write_source(
        repository,
        "docs/refresh.py",
        "from src.compositor import reflow_content\n\n"
        "def refresh_layout():\n"
        "    return reflow_content()\n",
    )
    write_source(
        repository,
        "src/compositor.py",
        "def reflow_content():\n"
        "    return None\n",
    )
    record = issue(
        "Layout refresh fails",
        "The traceback points to src/seed.py.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))
    compositor = next(
        candidate
        for candidate in candidates
        if candidate.file == "src/compositor.py"
    )

    assert all(
        not evidence.startswith("Two-hop source call chain via ")
        for evidence in compositor.evidence
    )


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
