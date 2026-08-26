import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

import repo_issue_intelligence.investigator as investigator_module
import repo_issue_intelligence.repository_index as repository_index_module
from repo_issue_intelligence.investigator import (
    DEFAULT_CANDIDATE_LIMIT,
    _content_matches_identifier,
    _expansion_relation_bonus,
    _identifier_variants,
    _merge_tail_expansions,
    _protected_base_reservation_slots,
    _rerank_relation_bonus,
    _reserve_protected_paths,
    extract_issue_signals,
    investigate,
    locate_candidates,
)
from repo_issue_intelligence.models import FileRecord, IssueRecord
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
        signals.title_terms
    )
    assert {"streaming", "response", "base", "http", "middleware"} <= (
        signals.primary_terms
    )
    assert {"send_denial_response", "RuntimeError"} <= signals.identifiers
    assert {"StreamingResponse", "BaseHTTPMiddleware"} <= signals.primary_identifiers
    assert {"StreamingResponse", "BaseHTTPMiddleware"} <= signals.title_identifiers
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
    assert signals.called_identifiers == ("view.remove_children",)
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


def test_extract_issue_signals_records_ordered_traceback_frames() -> None:
    record = issue(
        "Nested failure",
        '  File "/tmp/project/src/package/api.py", line 8, in outer\n'
        "    inner()\n"
        "  1  /tmp/project/src/package/api.py:12 in inner\n"
        "  at /usr/lib/python3.11/pathlib.py:900 in resolve\n",
    )

    frames = extract_issue_signals(record).traceback_frames

    assert [(frame.path, frame.symbol) for frame in frames] == [
        ("/tmp/project/src/package/api.py", "outer"),
        ("/tmp/project/src/package/api.py", "inner"),
        ("/usr/lib/python3.11/pathlib.py", "resolve"),
    ]


def test_extract_issue_signals_records_unicode_traceback_frames() -> None:
    record = issue(
        "Unicode traceback",
        '  File "/tmp/project/src/package/api.py", line 8, in 包.解析\n'
        "  /tmp/project/src/package/api.py:12 in 模块.e\u0301\n",
    )

    signals = extract_issue_signals(record)

    assert [(frame.path, frame.symbol) for frame in signals.traceback_frames] == [
        ("/tmp/project/src/package/api.py", "包.解析"),
        ("/tmp/project/src/package/api.py", "模块.é"),
    ]
    assert {"包.解析", "模块.é"} <= signals.explicit_identifiers


def test_unicode_prose_after_in_is_not_an_explicit_identifier() -> None:
    signals = extract_issue_signals(
        issue(
            "Locale rendering failure",
            "Rendering fails in 日本語 locale, but the parser is otherwise healthy.",
        )
    )

    assert "日本語" not in signals.explicit_identifiers


def test_fenced_non_call_qualified_unicode_identifier_is_explicit(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/model.py",
        "class 对象:\n    def 处理(self):\n        return None\n",
    )
    record = issue(
        "Callback replacement failure",
        "The failure is in src/model.py:\n```python\n对象.处理 = replacement\n```",
    )

    signals = extract_issue_signals(record)
    candidates = locate_candidates(record, build_repository_map(repository))

    assert "对象.处理" in signals.explicit_identifiers
    assert candidates[0].symbol == "处理"
    assert candidates[0].qualified_symbol == "对象.处理"


def test_extract_issue_signals_records_exact_source_line_references() -> None:
    revision = "a" * 40
    record = issue(
        "Source regression",
        f"See https://github.com/acme/repo/blob/{revision}/"
        "src/package/api.py#L42-L45.\n"
        "Ignore https://github.com/acme/repo/blob/main/src/package/api.py#L9.\n"
        "Ignore ../private.py#L7.\n"
        "The runtime entry point is main.py:8.",
    )

    references = extract_issue_signals(record).source_line_references

    assert [(reference.path, reference.line) for reference in references] == [
        ("src/package/api.py", 42)
    ]
    assert references[0].revision == revision


def test_source_line_references_are_bounded() -> None:
    body = "\n".join(
        f"src/package/module_{index}.py#L1" for index in range(12)
    )

    references = extract_issue_signals(
        issue("Many source links", body)
    ).source_line_references

    assert len(references) == 8
    assert references[-1].path == "src/package/module_7.py"


def test_extract_issue_signals_records_only_bounded_source_snippets() -> None:
    matching_snippet = (
        "except UsageError as e:",
        "if hide_input:",
        'echo(_("Error: The value you entered was invalid."), err=err)',
        "else:",
        'echo(_("Error: {e.message}").format(e=e), err=err)',
        "continue",
    )
    oversized_snippet = "\n".join(
        f"value_{index} = callback_{index}()" for index in range(13)
    )
    record = issue(
        "Prompt error",
        "```python\n"
        + "\n".join(matching_snippet)
        + "\n```\n```python\n"
        + oversized_snippet
        + "\n```",
    )

    snippets = extract_issue_signals(record).source_snippets

    assert snippets == (matching_snippet,)


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


@pytest.mark.parametrize(
    "source",
    [
        "变量data值",
        "édataβ",
        "a\u0301data",
        "data\u0301",
        "a\u203fdata",
    ],
    ids=[
        "cjk-surround",
        "accent-surround",
        "combining-before",
        "combining-after",
        "connector-before",
    ],
)
def test_content_identifier_matching_rejects_unicode_identifier_continuations(
    source: str,
) -> None:
    assert source.isidentifier()
    assert not _content_matches_identifier(source, "data")


@pytest.mark.parametrize("language", ["JavaScript", "TypeScript"])
@pytest.mark.parametrize(
    "source",
    [
        "$data",
        "data$value",
        "prefix\u200cdata",
        "data\u200dvalue",
        "\u037adata",
        "prefix\u309bdata",
        "data\u30fbvalue",
        "prefix\uff65data",
    ],
    ids=[
        "dollar-before",
        "dollar-after",
        "zwnj-before",
        "zwj-after",
        "id-continue-before",
        "other-id-start-before",
        "katakana-middle-dot-after",
        "halfwidth-katakana-middle-dot-before",
    ],
)
def test_content_identifier_matching_uses_ecmascript_continuations(
    language: str,
    source: str,
) -> None:
    assert not _content_matches_identifier(
        source,
        "data",
        language=language,
    )
    assert _content_matches_identifier(
        "const data = object.data;",
        "data",
        language=language,
    )
    assert _content_matches_identifier(
        "prefix\u2e2fdata",
        "data",
        language=language,
    )


@pytest.mark.parametrize("language", ["JavaScript", "TypeScript"])
def test_ecmascript_content_matching_preserves_unicode_spelling(
    language: str,
) -> None:
    decomposed = "e\u0301"

    assert _content_matches_identifier(
        f"const {decomposed} = 1;",
        decomposed,
        language=language,
    )
    assert not _content_matches_identifier(
        "const é = 1;",
        decomposed,
        language=language,
    )


def test_typescript_localization_uses_verbatim_unicode_identifier(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    decomposed = "e\u0301"
    write_source(repository, "src/decomposed.ts", f"const {decomposed} = 1;\n")
    write_source(repository, "src/composed.ts", "const é = 1;\n")

    record = issue(
        "Binding lookup failure",
        f"The backticked `{decomposed}` binding cannot be resolved.",
    )
    candidates = locate_candidates(record, build_repository_map(repository))

    assert candidates[0].file == "src/decomposed.ts"
    decomposed_candidate = next(
        candidate for candidate in candidates if candidate.file == "src/decomposed.ts"
    )
    composed_candidate = next(
        candidate for candidate in candidates if candidate.file == "src/composed.ts"
    )
    assert "Source contains issue identifiers: e\u0301" in decomposed_candidate.evidence
    assert not any(
        evidence.startswith("Source contains issue identifiers:")
        for evidence in composed_candidate.evidence
    )
    assert decomposed_candidate.confidence > composed_candidate.confidence


@pytest.mark.parametrize(
    ("identifier", "fragment_identifier"),
    [
        ("$解析", "解析"),
        ("解析$value", "解析"),
    ],
)
def test_typescript_localization_preserves_dollar_identifiers(
    tmp_path: Path,
    identifier: str,
    fragment_identifier: str,
) -> None:
    repository = tmp_path / "repository"
    write_source(repository, "src/target.ts", f"const {identifier} = 1;\n")
    write_source(
        repository,
        "src/fragment.ts",
        f"const {fragment_identifier} = 1;\n",
    )

    record = issue(
        "Binding lookup failure",
        f"The backticked `{identifier}` binding cannot be resolved.",
    )
    signals = extract_issue_signals(record)
    candidates = locate_candidates(record, build_repository_map(repository))

    assert signals.verbatim_identifiers == frozenset({identifier})
    target = next(candidate for candidate in candidates if candidate.file == "src/target.ts")
    fragment = next(
        candidate for candidate in candidates if candidate.file == "src/fragment.ts"
    )
    assert f"Source contains issue identifiers: {identifier}" in target.evidence
    assert not any(
        evidence.startswith("Source contains issue identifiers:")
        for evidence in fragment.evidence
    )
    assert target.confidence > fragment.confidence


@pytest.mark.parametrize("identifier", ["$state", "namespace.$解析"])
def test_typescript_fenced_code_preserves_embedded_dollar_identifier(
    tmp_path: Path,
    identifier: str,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/target.ts",
        f"{identifier} = replacement;\n",
    )
    fragment_identifier = identifier.rsplit("$", maxsplit=1)[-1]
    write_source(
        repository,
        "src/fragment.ts",
        f"const {fragment_identifier} = replacement;\n",
    )

    record = issue(
        "Binding assignment failure",
        f"```ts\nconst replacement = build();\n{identifier} = replacement;\n```",
    )
    signals = extract_issue_signals(record)
    candidates = locate_candidates(record, build_repository_map(repository))

    assert signals.verbatim_identifiers == frozenset({identifier})
    target = next(candidate for candidate in candidates if candidate.file == "src/target.ts")
    fragment = next(
        candidate for candidate in candidates if candidate.file == "src/fragment.ts"
    )
    assert f"Source contains issue identifiers: {identifier}" in target.evidence
    assert not any(
        evidence.startswith("Source contains issue identifiers:")
        for evidence in fragment.evidence
    )
    assert target.confidence > fragment.confidence


def test_shell_dollar_variables_do_not_become_ecmascript_identifiers() -> None:
    signals = extract_issue_signals(
        issue(
            "Shell environment failure",
            "The `$HOME` value is missing.\n```bash\necho $USER\n```",
        )
    )

    assert not {"$HOME", "$USER"} & set(signals.verbatim_identifiers)


@pytest.mark.parametrize(
    ("extension", "source"),
    [
        ("js", "const $data = 1;\n"),
        ("ts", "const prefix\u200cdata = 1;\n"),
    ],
)
def test_locate_candidates_applies_ecmascript_content_boundaries(
    tmp_path: Path,
    extension: str,
    source: str,
) -> None:
    repository = tmp_path / "repository"
    write_source(repository, f"frontend.{extension}", source)
    write_source(repository, "backend.py", "data = 1\n")

    candidates = {
        candidate.file: candidate
        for candidate in locate_candidates(
            issue("Unexpected lookup", "The `data` lookup fails."),
            build_repository_map(repository),
        )
    }
    identifier_evidence = "Source contains issue identifiers: data"

    assert identifier_evidence in candidates["backend.py"].evidence
    assert identifier_evidence not in candidates[f"frontend.{extension}"].evidence
    assert candidates[f"frontend.{extension}"].lines is None


@pytest.mark.parametrize("root", ["src", "lib"])
def test_repository_map_coexists_src_and_lib_root_modules(
    tmp_path: Path,
    root: str,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "app.py",
        f"from {root} import helper\n"
        "from package.api import handle\n\n"
        "def invoke():\n"
        "    helper()\n"
        "    handle()\n",
    )
    write_source(
        repository,
        f"{root}.py",
        "def helper():\n    return None\n",
    )
    write_source(
        repository,
        f"{root}/package/api.py",
        "def handle():\n    return None\n",
    )

    repository_map = build_repository_map(repository)
    app = next(file for file in repository_map.files if file.path == "app.py")

    assert app.local_imports == [f"{root}.py", f"{root}/package/api.py"]
    assert {
        (call.local_name, call.target_file, call.target_symbol)
        for call in app.resolved_calls
    } == {
        ("helper", f"{root}.py", "helper"),
        ("handle", f"{root}/package/api.py", "handle"),
    }


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


def test_directly_supported_path_enters_base_shortlist() -> None:
    selected = _reserve_protected_paths(
        ["first.py", "second.py", "third.py", "publish.py", "other.py"],
        ["publish.py", "other.py"],
        limit=3,
        reservation_slots=1,
    )

    assert selected == ["first.py", "second.py", "publish.py"]


def test_expanded_pool_reserves_three_directly_supported_paths() -> None:
    ranked = [f"base-{index}.py" for index in range(45)]
    protected = ranked[40:43]

    default_selected = _reserve_protected_paths(
        ranked,
        protected,
        limit=20,
        reservation_slots=_protected_base_reservation_slots(20),
    )
    expanded_selected = _reserve_protected_paths(
        ranked,
        protected,
        limit=40,
        reservation_slots=_protected_base_reservation_slots(40),
    )

    assert default_selected[-1] == protected[0]
    assert default_selected[:-1] == ranked[:19]
    assert expanded_selected[-3:] == protected
    assert expanded_selected[:-3] == ranked[:37]


def test_source_path_reservation_ignores_auxiliary_traceback_paths(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/package/publish.py",
        "class PublishCommand:\n    pass\n",
    )
    traceback_lines: list[str] = []
    for index in range(4):
        relative_path = f"tests/noisy_{index}.py"
        write_source(
            repository,
            relative_path,
            f"def failing_case_{index}():\n    pass\n",
        )
        traceback_lines.append(f'File "{relative_path}", line 1')

    candidates = locate_candidates(
        issue(
            "Publish command ignores no interaction",
            "\n".join(traceback_lines),
        ),
        build_repository_map(repository),
        limit=3,
    )

    assert "src/package/publish.py" in {
        candidate.file for candidate in candidates
    }


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


def test_repository_map_localizes_shipped_json_schema(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/tox/session/cmd/schema.py",
        "def build_schema():\n    return {'deps': ['string']}\n",
    )
    write_source(
        repository,
        "src/tox/tox.schema.json",
        '{"properties": {"deps": {"type": "string"}}}\n',
    )
    write_source(
        repository,
        "src/tox/package-lock.json",
        '{"deps": "not a benchmark source artifact"}\n',
    )

    repository_map = build_repository_map(repository)
    records = {file.path: file for file in repository_map.files}

    assert records["src/tox/tox.schema.json"].language == "JSON Schema"
    assert records["src/tox/tox.schema.json"].symbols == []
    assert "src/tox/package-lock.json" not in records

    candidates = locate_candidates(
        issue(
            "Published tox schema rejects dependency arrays",
            "The `deps` property in `src/tox/tox.schema.json` accepts only strings.",
        ),
        repository_map,
    )

    assert "src/tox/tox.schema.json" in {
        candidate.file for candidate in candidates
    }


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
    assert api.module_import_symbols == {
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


def test_file_record_defaults_module_import_symbols_for_legacy_maps() -> None:
    record = FileRecord.model_validate(
        {
            "path": "src/package/api.py",
            "language": "Python",
        }
    )

    assert record.module_import_symbols == {}


def test_repository_map_records_unshadowed_qualified_module_calls(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/signatures.py",
        "import inspect as introspection\n\n"
        "def get_func_args_dict(func):\n"
        "    return introspection.signature(func)\n",
    )

    repository_map = build_repository_map(repository)
    signatures = repository_map.files[0]

    assert [
        call.model_dump() for call in signatures.qualified_external_calls
    ] == [
        {
            "caller": "get_func_args_dict",
            "target": "inspect.signature",
        }
    ]


def test_repository_map_skips_shadowed_qualified_module_calls(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/signatures.py",
        "import inspect\n\n"
        "def get_func_args_dict(inspect, func):\n"
        "    return inspect.signature(func)\n",
    )

    repository_map = build_repository_map(repository)

    assert repository_map.files[0].qualified_external_calls == []


def test_repository_map_skips_ambiguous_qualified_callers(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/signatures.py",
        "import inspect\n\n"
        "def get_func_args_dict(func):\n"
        "    return inspect.signature(func)\n\n"
        "def get_func_args_dict(func):\n"
        "    return inspect.signature(func)\n",
    )

    repository_map = build_repository_map(repository)

    assert repository_map.files[0].qualified_external_calls == []


def test_repository_map_keeps_single_overload_implementation_call(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/signatures.py",
        "import inspect\n"
        "from typing import overload\n\n"
        "@overload\n"
        "def get_func_args_dict(func: str) -> str: ...\n\n"
        "@overload\n"
        "def get_func_args_dict(func: int) -> int: ...\n\n"
        "def get_func_args_dict(func):\n"
        "    return inspect.signature(func)\n",
    )

    repository_map = build_repository_map(repository)

    assert [
        call.model_dump()
        for call in repository_map.files[0].qualified_external_calls
    ] == [
        {
            "caller": "get_func_args_dict",
            "target": "inspect.signature",
        }
    ]


def test_repository_map_does_not_trust_unrelated_overload_decorator(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/signatures.py",
        "import custom\n"
        "import inspect\n\n"
        "@custom.overload\n"
        "def get_func_args_dict(func):\n"
        "    return inspect.signature(func)\n\n"
        "def get_func_args_dict(func):\n"
        "    return inspect.signature(func)\n",
    )

    repository_map = build_repository_map(repository)

    assert repository_map.files[0].qualified_external_calls == []


def test_repository_map_resolves_leading_function_local_import_call(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/package/api.py",
        "def handle_request():\n"
        '    \"\"\"Parse one request.\"\"\"\n'
        "    from .parser import parse_request\n\n"
        "    return parse_request()\n",
    )
    write_source(
        repository,
        "src/package/parser.py",
        "def parse_request():\n    return None\n",
    )

    repository_map = build_repository_map(repository)
    api = next(
        file
        for file in repository_map.files
        if file.path == "src/package/api.py"
    )

    assert [call.model_dump() for call in api.resolved_calls] == [
        {
            "caller": "handle_request",
            "local_name": "parse_request",
            "target_file": "src/package/parser.py",
            "target_symbol": "parse_request",
        }
    ]
    assert [call.model_dump() for call in api.function_local_import_calls] == [
        {
            "caller": "handle_request",
            "local_name": "parse_request",
            "target_file": "src/package/parser.py",
            "target_symbol": "parse_request",
        }
    ]


def test_direct_function_local_import_relation_cannot_expand_candidate() -> None:
    assert (
        _expansion_relation_bonus(
            4.5,
            "Function-local import calls symbols defined here: parse_request",
        )
        == 0
    )
    assert (
        _expansion_relation_bonus(
            5.0,
            "Two-hop function-local import chain calls symbols defined here: "
            "parse_request",
        )
        == 5.0
    )


def test_reverse_import_relation_can_expand_but_cannot_rerank() -> None:
    evidence = (
        "Imports title-matching source module "
        "src/package/worker/control.py: revoke, revoke_by_headers"
    )

    assert _rerank_relation_bonus(5.0, evidence) == 0
    assert _expansion_relation_bonus(5.0, evidence) == 5.0


def test_reexport_relation_can_expand_but_cannot_rerank() -> None:
    evidence = (
        "Issue-referenced source imports re-exported symbols defined here: "
        "RuntimeManager via src/package/facade/__init__.py"
    )

    assert _rerank_relation_bonus(7.0, evidence) == 0
    assert _expansion_relation_bonus(7.0, evidence) == 7.0


@pytest.mark.parametrize(
    "function_body",
    [
        (
            "def handle_request(callback):\n"
            "    from .parser import parse_request\n"
            "    parse_request = callback\n"
            "    return parse_request()\n"
        ),
        (
            "def handle_request(enabled):\n"
            "    if enabled:\n"
            "        from .parser import parse_request\n"
            "        return parse_request()\n"
            "    return None\n"
        ),
        (
            "def handle_request():\n"
            "    parse_request()\n"
            "    from .parser import parse_request\n"
            "    return None\n"
        ),
        (
            "def handle_request():\n"
            "    from .parser import parse_request\n"
            "    import callbacks as parse_request\n"
            "    return parse_request()\n"
        ),
    ],
    ids=["reassigned", "conditional", "after-use", "plain-import-collision"],
)
def test_repository_map_skips_unsafe_function_local_import_calls(
    tmp_path: Path,
    function_body: str,
) -> None:
    repository = tmp_path / "repository"
    write_source(repository, "src/package/api.py", function_body)
    write_source(
        repository,
        "src/package/parser.py",
        "def parse_request():\n    return None\n",
    )

    repository_map = build_repository_map(repository)
    api = next(
        file
        for file in repository_map.files
        if file.path == "src/package/api.py"
    )

    assert api.resolved_calls == []


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


def test_repository_map_records_conservative_rust_symbols(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "crates/parser/src/lib.rs",
        "pub struct Pep508Error { message: String }\n"
        "pub struct r#type { value: String }\n"
        "pub struct 类型 { value: String }\n"
        "pub unsafe trait UnsafeReporter {}\n"
        "pub auto trait AutoReporter {}\n"
        "pub union ReporterValue { integer: u64 }\n"
        "pub extern fn foreign_reporter() {}\n"
        'pub unsafe extern "C" fn abi_reporter() {}\n'
        "pub fn r#match() {}\n"
        "pub fn 解析() {}\n"
        'unsafe extern "C" {\n'
        "    safe fn exposed();\n"
        "}\n"
        "#[inline] pub fn attributed() {}\n"
        "# [inline] pub fn spaced_attributed() {}\n"
        "#[cfg_attr(any(), allow(dead_code))] pub fn nested_attributed() {}\n"
        "#[cfg(\n"
        '    any(target_os = "linux", target_os = "macos"),\n'
        ")] pub fn multiline_attributed() {}\n"
        "macro_rules! generated {\n"
        "    ($value:expr) => {{\n"
        "        fn ignored_macro_function() {}\n"
        "    }};\n"
        "}\n"
        "macro_rules\n"
        "! generated_multiline {\n"
        "    () => { fn ignored_multiline_macro_rules_function() {} };\n"
        "}\n"
        "generate! {\n"
        "    fn ignored_invocation_function() {}\n"
        "}\n"
        "generate_paren!(\n"
        "    fn ignored_paren_function() {}\n"
        ");\n"
        "generate_bracket![\n"
        "    fn ignored_bracket_function() {}\n"
        "];\n"
        "生成! { fn ignored_unicode_macro_function() {} }\n"
        "::路径::生成![fn ignored_absolute_unicode_macro_function() {}];\n"
        "fn real_with_macro() { generate! { fn ignored_inline_function() {} } }\n"
        "impl Pep508Error {\n"
        "    default type Item = u8;\n"
        "    pub(crate) async fn render_caret(&self) {}\n"
        "}\n"
        "/* outer\n/* nested */\nfn ignored_rust_comment() {}\n*/\n",
    )
    write_source(
        repository,
        "ui/schema-form-input.ts",
        "export function TypeScriptRemainsFileOnly() { return null; }\n",
    )
    write_source(
        repository,
        "ui/schema-form-input.tsx",
        "export function TsxRemainsFileOnly() { return <div>/*</div>; }\n",
    )

    repository_map = build_repository_map(repository)
    rust = next(file for file in repository_map.files if file.language == "Rust")
    typescript = next(
        file
        for file in repository_map.files
        if file.path == "ui/schema-form-input.ts"
    )
    tsx = next(
        file
        for file in repository_map.files
        if file.path == "ui/schema-form-input.tsx"
    )

    assert [(symbol.name, symbol.kind) for symbol in rust.symbols] == [
        ("Pep508Error", "struct"),
        ("type", "struct"),
        ("类型", "struct"),
        ("UnsafeReporter", "trait"),
        ("AutoReporter", "trait"),
        ("ReporterValue", "union"),
        ("foreign_reporter", "function"),
        ("abi_reporter", "function"),
        ("match", "function"),
        ("解析", "function"),
        ("exposed", "function"),
        ("attributed", "function"),
        ("spaced_attributed", "function"),
        ("nested_attributed", "function"),
        ("multiline_attributed", "function"),
        ("real_with_macro", "function"),
        ("Item", "type"),
        ("render_caret", "function"),
    ]
    assert typescript.symbols == []
    assert tsx.symbols == []


def test_repository_map_preserves_rust_lexical_boundaries(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    write_source(
        repository,
        "src/lib.rs",
        "pub/**/fn block_comment_target() {}\n"
        "fn first() {} fn target() {}\n"
        "fn e\u0301() {}\n"
        "e\u0301! {\n"
        "    fn ignored_decomposed_macro_function() {}\n"
        "}\n"
        "discard\n"
        "! {\n"
        "    fn ignored_cross_line_macro_function() {}\n"
        "}\n"
        "pub macro discard($value:tt) {\n"
        "    fn ignored_declarative_macro_function() {}\n"
        "}\n"
        "pub macro body_only\n"
        "{\n"
        "    fn ignored_body_only_macro_function() {}\n"
        "}\n"
        "fn after_macro() {}\n"
        "fn\n"
        "multiline_function() {}\n"
        "struct\n"
        "MultilineType {}\n"
        "if !condition { fn control_flow_target() {} }\n"
        "if !{ fn unary_block_target() {} true } {}\n"
        "try! { fn ignored_legacy_try_macro_function() {} }\n"
        "dyn! { fn ignored_legacy_dyn_macro_function() {} }\n"
        "await! { fn ignored_legacy_await_macro_function() {} }\n"
        "crate::discard! { fn ignored_path_macro_function() {} }\n"
        "union! { fn ignored_reserved_macro_function() {} }\n"
        "r#if! { fn ignored_raw_keyword_macro_function() {} }\n",
    )

    repository_map = build_repository_map(repository)

    assert [
        (symbol.name, symbol.line)
        for symbol in repository_map.files[0].symbols
    ] == [
        ("block_comment_target", 1),
        ("first", 2),
        ("target", 2),
        ("é", 3),
        ("after_macro", 18),
        ("multiline_function", 19),
        ("MultilineType", 21),
        ("control_flow_target", 23),
        ("unary_block_target", 24),
    ]


def test_unicode_rust_symbol_issue_reference_matches(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    write_source(repository, "src/parser.rs", "pub fn 解析() {}\n")

    record = issue(
        "Parser regression",
        "The backticked `解析` function fails for this input.",
    )
    signals = extract_issue_signals(record)
    report = investigate(record, build_repository_map(repository))

    assert "解析" in signals.identifiers
    assert "解析" in signals.explicit_identifiers
    assert "解析" in signals.terms
    candidate = next(item for item in report.candidates if item.file == "src/parser.rs")
    assert candidate.symbol == "解析"


def test_raw_unicode_rust_symbol_issue_reference_normalizes(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    write_source(repository, "src/parser.rs", "pub fn r#解析() {}\n")

    record = issue(
        "Raw identifier parser failure",
        "The backticked `r#解析` function fails for this input.",
    )
    signals = extract_issue_signals(record)
    report = investigate(record, build_repository_map(repository))

    assert "解析" in signals.identifiers
    assert "解析" in signals.explicit_identifiers
    assert "解析" in signals.terms
    candidate = next(item for item in report.candidates if item.file == "src/parser.rs")
    assert candidate.symbol == "解析"


def test_qualified_raw_rust_path_issue_reference_normalizes(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    write_source(repository, "src/parser.rs", "pub fn r#解析() {}\n")

    record = issue(
        "Qualified raw identifier parser failure",
        "The backticked `crate::r#解析` symbol fails for this input.",
    )
    signals = extract_issue_signals(record)
    report = investigate(record, build_repository_map(repository))

    assert "crate.解析" in signals.identifiers
    assert "crate.解析" in signals.explicit_identifiers
    candidate = next(item for item in report.candidates if item.file == "src/parser.rs")
    assert candidate.symbol == "解析"


def test_rust_declarations_are_not_extracted_as_unicode_calls() -> None:
    signals = extract_issue_signals(
        issue(
            "Rust declaration example",
            "```rust\nfn 解析() {}\nstruct 类型(u8);\nfn helper() {}\n```",
        )
    )

    assert signals.called_identifiers == ()
    assert not {"解析", "类型", "helper"} & set(signals.explicit_identifiers)


def test_explicit_unique_rust_type_reference_selects_symbol(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    write_source(
        repository,
        "src/error.rs",
        "pub struct Pep508Error;\npub fn unrelated() {}\n",
    )

    record = issue(
        "Parsing failure",
        "The failure is in `Pep508Error`.",
    )
    report = investigate(record, build_repository_map(repository))

    candidate = next(item for item in report.candidates if item.file == "src/error.rs")
    assert candidate.symbol == "Pep508Error"


def test_rust_macro_definitions_are_scanned_once_per_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/generated.rs",
        "noop!();\n" * 4000 + "pub fn target() {}\n",
    )
    original = repository_index_module._rust_macro_definition_candidates
    calls = 0

    def counting_candidates(value: str) -> list[tuple[int, int, int]]:
        nonlocal calls
        calls += 1
        return original(value)

    monkeypatch.setattr(
        repository_index_module,
        "_rust_macro_definition_candidates",
        counting_candidates,
    )

    repository_map = build_repository_map(repository)

    assert calls == 1
    assert [symbol.name for symbol in repository_map.files[0].symbols] == ["target"]


def test_rust_macro_invocation_scan_is_bounded_by_definitions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    source = "".join(
        f"macro_rules! generated_{index} {{ () => {{}} }}\n"
        for index in range(4000)
    ) + "pub fn target() {}\n"
    write_source(repository, "src/generated.rs", source)
    original = repository_index_module._next_rust_macro_invocation
    scanned_characters = 0

    def counting_invocations(
        value: str,
        start: int,
        stop: int,
    ) -> tuple[int, int] | None:
        nonlocal scanned_characters
        scanned_characters += max(0, min(stop, len(value)) - start)
        return original(value, start, stop)

    monkeypatch.setattr(
        repository_index_module,
        "_next_rust_macro_invocation",
        counting_invocations,
    )

    repository_map = build_repository_map(repository)

    assert scanned_characters <= len(source)
    assert [symbol.name for symbol in repository_map.files[0].symbols] == ["target"]


def test_rust_macro_scan_is_linear_with_mixed_definitions_and_invocations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    pair_count = 2000
    source = "".join(
        f"macro_rules! generated_{index} {{ () => {{}} }}\n"
        f"generated_{index}! {{ fn ignored_{index}() {{}} }}\n"
        for index in range(pair_count)
    ) + "pub fn target() {}\n"
    write_source(repository, "src/generated.rs", source)
    original = repository_index_module._rust_identifier_at
    identifier_lookups = 0

    def counting_identifiers(value: str, index: int) -> tuple[str, int] | None:
        nonlocal identifier_lookups
        identifier_lookups += 1
        return original(value, index)

    monkeypatch.setattr(
        repository_index_module,
        "_rust_identifier_at",
        counting_identifiers,
    )

    repository_map = build_repository_map(repository)

    assert identifier_lookups <= 3 * pair_count + 10
    assert [symbol.name for symbol in repository_map.files[0].symbols] == ["target"]


def test_rust_script_shebang_preserves_first_declaration(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "script.rs",
        "#!/usr/bin/env rust-script\nfn main() {}\n",
    )

    repository_map = build_repository_map(repository)

    assert [
        (symbol.name, symbol.line)
        for symbol in repository_map.files[0].symbols
    ] == [("main", 2)]


@pytest.mark.parametrize(
    "source, expected_line",
    [
        ("\ufefffn main() {}\n", 1),
        ("\ufeff#!/usr/bin/env rust-script\nfn main() {}\n", 2),
    ],
)
def test_rust_utf8_bom_preserves_first_declaration(
    tmp_path: Path,
    source: str,
    expected_line: int,
) -> None:
    repository = tmp_path / "repository"
    write_source(repository, "script.rs", source)

    repository_map = build_repository_map(repository)

    assert [
        (symbol.name, symbol.line)
        for symbol in repository_map.files[0].symbols
    ] == [("main", expected_line)]


def test_rust_declaration_line_assignment_scales_forward(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/generated.rs",
        "".join(f"fn generated_{index}() {{}}\n" for index in range(4000)),
    )

    symbols = build_repository_map(repository).files[0].symbols

    assert len(symbols) == 4000
    assert (symbols[0].name, symbols[0].line) == ("generated_0", 1)
    assert (symbols[-1].name, symbols[-1].line) == ("generated_3999", 4000)


def test_decomposed_unicode_call_normalizes_for_symbol_matching(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    write_source(repository, "src/parser.rs", "pub fn e\u0301() {}\n")

    record = issue(
        "Parser regression",
        "The backticked `e\u0301()` call fails for this input.",
    )
    signals = extract_issue_signals(record)
    report = investigate(record, build_repository_map(repository))

    assert "é" in signals.explicit_identifiers
    assert signals.called_identifiers == ("é",)
    candidate = next(item for item in report.candidates if item.file == "src/parser.rs")
    assert candidate.symbol == "é"


def test_rust_symbol_scanner_respects_literal_boundaries(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "crates/parser/src/literals.rs",
        'const COMMENT_MARKER: &str = "/*";\n'
        "const QUOTE: char = '\"';\n"
        'const QUOTED: &str = "\nfn ignored_quoted_string() {}\n";\n'
        'const EXAMPLE: &str = r#"\nfn ignored_raw_string() {}\n"#;\n'
        'const BYTE_EXAMPLE: &[u8] = br##"\nfn ignored_byte_raw_string() {}\n"##;\n'
        'const C_EXAMPLE: &CStr = cr#"\nfn ignored_c_raw_string() {}\n"#;\n'
        "fn real_rust_function() {}\n",
    )

    repository_map = build_repository_map(repository)
    rust = next(file for file in repository_map.files if file.language == "Rust")

    assert [symbol.name for symbol in rust.symbols] == ["real_rust_function"]


def test_rust_symbol_evidence_can_enter_expanded_candidate_pool(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    for index in range(40):
        write_source(
            repository,
            f"crates/noise-{index:02d}/src/runtime.rs",
            'const MESSAGE: &str = "interpreter cache";\n',
        )
    target = "crates/uv-python/src/interpreter.rs"
    write_source(
        repository,
        target,
        "pub struct Interpreter { executable: String }\n",
    )

    candidates = locate_candidates(
        issue(
            "Stale interpreter cache",
            "The project uses the wrong Python executable.",
        ),
        build_repository_map(repository),
        limit=40,
    )

    assert target in [candidate.file for candidate in candidates]
    selected = next(candidate for candidate in candidates if candidate.file == target)
    assert selected.symbol == "Interpreter"


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


def test_unique_body_symbol_reference_ranks_before_title_semantics(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "download.py",
        "class Downloader:\n"
        "    def _process_response(self):\n"
        "        return None\n\n"
        "    def _attempt_resumes_or_redownloads(self):\n"
        '        """Resume a failed download."""\n'
        "        return None\n",
    )
    record = issue(
        "Incomplete download resumption fails",
        "The exception is raised from _process_response before retrying. "
        "`_attempt_resumes_or_redownloads` is downstream; "
        "_process_response must hand the failure to it.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert candidates[0].qualified_symbol == "Downloader._process_response"


def test_title_scoped_class_call_selects_unique_constructor(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "type_adapter.py",
        "class TypeAdapter:\n"
        "    def __init__(self, value):\n"
        '        """Initialize the type argument."""\n'
        "        self.value = value\n\n"
        "    def validate_python(self, value):\n"
        "        return value\n",
    )
    record = issue(
        "`TypeAdapter` infers an Any type argument",
        "`adapter = TypeAdapter(str | int)` then "
        "`adapter.validate_python('value')` returns Any.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert candidates[0].qualified_symbol == "TypeAdapter.__init__"
    assert (
        "Issue title and code call constructor TypeAdapter.__init__"
        in candidates[0].evidence
    )


def test_body_only_class_call_does_not_override_explicit_method(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "type_adapter.py",
        "class TypeAdapter:\n"
        "    def __init__(self, value):\n"
        "        self.value = value\n\n"
        "    def validate_python(self, value):\n"
        "        return value\n",
    )
    record = issue(
        "Validation returns the wrong value",
        "`adapter = TypeAdapter(str)` then "
        "`adapter.validate_python('value')` fails.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert candidates[0].qualified_symbol == "TypeAdapter.validate_python"
    assert not any(
        evidence.startswith("Issue title and code call constructor ")
        for evidence in candidates[0].evidence
    )


def test_class_call_without_constructor_semantics_is_not_direct_evidence(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "client.py",
        "class Client:\n"
        "    def __init__(self):\n"
        '        """Create the client."""\n'
        "        self.ready = True\n\n"
        "    def request(self):\n"
        "        return None\n",
    )
    record = issue(
        "`Client` request returns the wrong response",
        "The minimal setup is `client = Client()`.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert not any(
        evidence.startswith("Issue title and code call constructor ")
        for candidate in candidates
        for evidence in candidate.evidence
    )


def test_title_method_reference_overrides_same_owner_constructor_call(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "routing.py",
        "class APIRouter:\n"
        "    def __init__(self):\n"
        "        self.routes = []\n\n"
        "    def include_router(self, router):\n"
        "        self.routes.extend(router.routes)\n",
    )
    record = issue(
        "`APIRouter` loses data through `include_router`",
        "```python\n"
        "router = APIRouter()\n"
        "app.include_router(router)\n"
        "```",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert candidates[0].qualified_symbol == "APIRouter.include_router"
    assert not any(
        evidence.startswith("Issue title and code call constructor ")
        for evidence in candidates[0].evidence
    )


def test_qualified_method_reference_overrides_inferred_constructor(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "type_adapter.py",
        "class TypeAdapter:\n"
        "    def __init__(self, value):\n"
        "        self.value = value\n\n"
        "    def validate_python(self, value):\n"
        "        return value\n",
    )
    record = issue(
        "`TypeAdapter` produces an invalid result",
        "`TypeAdapter(str)` then "
        "`TypeAdapter.validate_python(adapter, value)` fails.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert candidates[0].qualified_symbol == "TypeAdapter.validate_python"


def test_ambiguous_class_call_does_not_select_constructors(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    source = (
        "class TypeAdapter:\n"
        "    def __init__(self, value):\n"
        "        self.value = value\n"
    )
    write_source(repository, "first.py", source)
    write_source(repository, "second.py", source)
    record = issue(
        "`TypeAdapter` construction fails",
        "The reproduction calls `TypeAdapter(value)`.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert not any(
        evidence.startswith("Issue title and code call constructor ")
        for candidate in candidates
        for evidence in candidate.evidence
    )


def test_label_only_class_reference_does_not_select_constructor(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "type_adapter.py",
        "class TypeAdapter:\n"
        "    def __init__(self, value):\n"
        "        self.value = value\n\n"
        "    def validate_python(self, value):\n"
        "        return value\n",
    )
    record = issue(
        "Validation returns the wrong value",
        "`adapter = TypeAdapter(str)` then "
        "`adapter.validate_python('value')` fails.",
    ).model_copy(update={"labels": ["TypeAdapter"]})

    candidates = locate_candidates(record, build_repository_map(repository))

    assert candidates[0].qualified_symbol == "TypeAdapter.validate_python"
    assert not any(
        evidence.startswith("Issue title and code call constructor ")
        for evidence in candidates[0].evidence
    )


def test_constructor_overloads_in_one_owner_resolve_as_one_target(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "type_adapter.py",
        "class TypeAdapter:\n"
        "    def __init__(self, value: str):\n"
        "        self.value = value\n\n"
        "    def __init__(self, value: int):\n"
        "        self.value = value\n",
    )
    record = issue(
        "`TypeAdapter` construction fails",
        "The reproduction calls `TypeAdapter(value)`.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert candidates[0].qualified_symbol == "TypeAdapter.__init__"
    assert (
        "Issue title and code call constructor TypeAdapter.__init__"
        in candidates[0].evidence
    )


def test_short_api_identifier_does_not_override_symbol_semantics(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "pipeline.py",
        "def _apply_constraint():\n"
        '    """Build a constraint schema."""\n'
        "    return None\n\n"
        "def ge():\n"
        "    return None\n",
    )
    record = issue(
        "Constraint schema generation fails",
        "The `ge` constraint produces an invalid schema.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert candidates[0].symbol == "_apply_constraint"


def test_fenced_example_mentions_do_not_override_issue_reference(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "pipeline.py",
        "def _apply_constraint():\n"
        '    """Build a constraint schema."""\n'
        "    return None\n\n"
        "def _check_func():\n"
        "    return None\n",
    )
    record = issue(
        "Constraint schema generation fails",
        "The affected function is `_apply_constraint`.\n\n"
        "```python\n"
        "def _check_func():\n"
        "    return None\n\n"
        "_check_func()\n"
        "```",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert candidates[0].symbol == "_apply_constraint"


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


def test_deepest_repository_traceback_frame_selects_symbol(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/poetry/installation/executor.py",
        "class Executor:\n"
        "    def _save_url_reference(self):\n"
        "        return self._create_directory_url_reference()\n\n"
        "    def _create_directory_url_reference(self):\n"
        "        raise ValueError('relative path')\n",
    )
    record = issue(
        "Relative path cannot be expressed as a file URI",
        "The update calls `_save_url_reference` before failing.\n"
        "  2  /tmp/venv/site-packages/poetry/installation/executor.py:3 "
        "in _save_url_reference\n"
        "  1  /tmp/venv/site-packages/poetry/installation/executor.py:6 "
        "in _create_directory_url_reference\n"
        "ValueError: relative path",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert candidates[0].qualified_symbol == (
        "Executor._create_directory_url_reference"
    )
    assert (
        "Traceback frame points to symbol "
        "Executor._create_directory_url_reference"
    ) in candidates[0].evidence


def test_source_line_reference_selects_innermost_symbol(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/package/api.py",
        "class Handler:\n"
        "    def helper(self):\n"
        "        return None\n\n"
        "    def process(self):\n"
        "        raise RuntimeError('failed')\n",
    )
    record = issue(
        "Source regression",
        "See src/package/api.py#L6.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert candidates[0].file == "src/package/api.py"
    assert candidates[0].qualified_symbol == "Handler.process"
    assert (
        "Issue source line points to symbol Handler.process"
        in candidates[0].evidence
    )


def test_path_scoped_source_snippet_selects_enclosing_symbol(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/click/termui.py",
        "def _build_prompt(text):\n"
        "    return text\n\n"
        "def prompt(text, hide_input=False, err=False):\n"
        "    while True:\n"
        "        try:\n"
        "            return text\n"
        "        except UsageError as e:\n"
        "            if hide_input:\n"
        '                echo(_("Error: The value you entered was invalid."), err=err)\n'
        "            else:\n"
        '                echo(_("Error: {e.message}").format(e=e), err=err)\n'
        "            continue\n",
    )
    record = issue(
        "Hidden prompt error",
        "The standard message comes from termui.py.\n"
        "```python\n"
        "except UsageError as e:\n"
        "    if hide_input:\n"
        '        echo(_("Error: The value you entered was invalid."), err=err)\n'
        "    else:\n"
        '        echo(_("Error: {e.message}").format(e=e), err=err)  # noqa: B306\n'
        "    continue\n"
        "```",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert candidates[0].file == "src/click/termui.py"
    assert candidates[0].symbol == "prompt"
    assert (
        "Issue source snippet matches symbol prompt"
        in candidates[0].evidence
    )


def test_ambiguous_path_does_not_scope_source_snippet(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    snippet = (
        "message = 'The value you entered was invalid.'\n"
        "rendered = format_error_message(message)\n"
        "reported_error = rendered or message\n"
    )
    source = (
        "def process_hidden_error():\n"
        + "".join(f"    {line}\n" for line in snippet.splitlines())
    )
    write_source(repository, "src/termui.py", source)
    write_source(repository, "tests/termui.py", snippet)
    record = issue(
        "Hidden prompt error",
        "The failure occurs in termui.py.\n"
        "```python\n"
        "    message = 'The value you entered was invalid.'\n"
        "    rendered = format_error_message(message)\n"
        "    reported_error = rendered or message\n"
        "```",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert not any(
        evidence.startswith("Issue source snippet matches symbol ")
        for candidate in candidates
        for evidence in candidate.evidence
    )


def test_duplicate_symbol_identity_rejects_source_snippet(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/termui.py",
        "def process_hidden_error():\n"
        "    initial = 'first implementation keeps the legacy error'\n"
        "    return initial\n\n"
        "def process_hidden_error():\n"
        "    message = 'The value you entered was invalid.'\n"
        "    rendered = format_error_message(message)\n"
        "    return rendered\n",
    )
    record = issue(
        "Hidden prompt error",
        "The failure occurs in src/termui.py.\n"
        "```python\n"
        "def process_hidden_error():\n"
        "    message = 'The value you entered was invalid.'\n"
        "    rendered = format_error_message(message)\n"
        "    return rendered\n"
        "```",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert not any(
        evidence.startswith("Issue source snippet matches symbol ")
        for candidate in candidates
        for evidence in candidate.evidence
    )


def test_immutable_source_line_uses_referenced_revision(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/package/api.py",
        "class Handler:\n"
        "    def process(self):\n"
        "        raise RuntimeError('failed')\n",
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
        ["git", "-C", str(repository), "commit", "-qm", "referenced"],
        check=True,
    )
    revision = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    write_source(
        repository,
        "src/package/api.py",
        "class Handler:\n"
        "    def helper(self):\n"
        "        return None\n\n"
        "    def process(self):\n"
        "        raise RuntimeError('failed')\n",
    )
    record = issue(
        "Source regression",
        f"See https://github.com/acme/repo/blob/{revision}/"
        "src/package/api.py#L3.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert candidates[0].qualified_symbol == "Handler.process"
    assert (
        "Issue source line points to symbol Handler.process"
        in candidates[0].evidence
    )


def test_bare_traceback_frame_does_not_disambiguate_duplicate_methods(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/package/worker.py",
        "class Primary:\n"
        "    def reset(self):\n"
        "        return None\n\n"
        "class Secondary:\n"
        "    def reset(self):\n"
        "        return None\n",
    )
    record = issue(
        "Worker crashes",
        '  File "/tmp/project/src/package/worker.py", line 3, in reset\n'
        "RuntimeError: crashed",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert not any(
        evidence.startswith("Traceback frame points to symbol ")
        for candidate in candidates
        for evidence in candidate.evidence
    )


def test_traceback_frame_preserves_real_lib_package_prefix(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(repository, "lib/__init__.py", "")
    write_source(
        repository,
        "lib/executor.py",
        "def run():\n"
        "    return None\n",
    )
    record = issue(
        "Executor crash",
        '  File "/tmp/venv/site-packages/executor.py", line 2, in run\n'
        "RuntimeError: crashed",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert not any(
        evidence.startswith("Traceback frame points to symbol ")
        for candidate in candidates
        for evidence in candidate.evidence
    )


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


def test_title_owner_and_method_terms_select_qualified_method(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "parser.py",
        "class ConfigOptionParser:\n"
        "    def error(self, message):\n"
        "        raise ValueError(message)\n\n"
        "    def print_help(self):\n"
        "        return None\n\n"
        "class CustomOptionParser:\n"
        "    def insert_option_group(self):\n"
        "        return None\n\n"
        "class RichPipStreamHandler:\n"
        "    def handle_error(self):\n"
        "        return None\n\n"
        "class PrettyHelpFormatter:\n"
        "    def format_option(self):\n"
        '        """Render Rich usage markup."""\n'
        "        return None\n",
    )
    write_source(
        repository,
        "tests/test_parser.py",
        "class TestOptionParser:\n"
        "    def error(self):\n"
        "        return None\n",
    )
    record = issue(
        "Option errors print usage with Rich markup unrendered",
        "Normal help output is unaffected.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert candidates[0].qualified_symbol == "ConfigOptionParser.error"
    assert (
        "Issue title matches owner and method ConfigOptionParser.error"
        in candidates[0].evidence
    )
    test_candidate = next(
        candidate
        for candidate in candidates
        if candidate.file == "tests/test_parser.py"
    )
    assert not any(
        evidence.startswith("Issue title matches owner and method ")
        for evidence in test_candidate.evidence
    )

    plural_candidates = locate_candidates(
        issue(
            "Options errors print usage with Rich markup unrendered",
            "Normal help output is unaffected.",
        ),
        build_repository_map(repository),
    )

    assert plural_candidates[0].qualified_symbol == "ConfigOptionParser.error"


def test_production_test_prefixed_class_can_use_title_method_evidence(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/package/testclient.py",
        "class TestOptionParser:\n"
        "    def error(self, message):\n"
        "        raise ValueError(message)\n",
    )

    candidates = locate_candidates(
        issue(
            "Option errors print the wrong output",
            "Normal parsing is unaffected.",
        ),
        build_repository_map(repository),
    )

    assert candidates[0].qualified_symbol == "TestOptionParser.error"
    assert (
        "Issue title matches owner and method TestOptionParser.error"
        in candidates[0].evidence
    )


def test_ambiguous_title_owner_and_method_terms_are_not_direct_evidence(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "parser.py",
        "class ConfigOptionParser:\n"
        "    def error(self, message):\n"
        '        """Report a configuration option error."""\n'
        "        raise ValueError(message)\n\n"
        "class RuntimeOptionParser:\n"
        "    def error(self, message):\n"
        '        """Report a runtime option error."""\n'
        "        raise ValueError(message)\n",
    )
    record = issue(
        "Option parser error",
        "The parser reports the wrong message.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert not any(
        evidence.startswith("Issue title matches owner and method ")
        for candidate in candidates
        for evidence in candidate.evidence
    )


def test_generic_method_term_does_not_create_qualified_title_evidence(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "parser.py",
        "class OptionParser:\n"
        "    def handle(self):\n"
        "        return None\n\n"
        "class SystemEnv:\n"
        "    def is_venv(self):\n"
        "        return False\n",
    )
    record = issue(
        "Option parser handle fails",
        "The parser does not complete.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))

    assert not any(
        evidence.startswith("Issue title matches owner and method ")
        for candidate in candidates
        for evidence in candidate.evidence
    )

    env_candidates = locate_candidates(
        issue(
            "Poetry thinks Conda env is active",
            "The environment was deactivated.",
        ),
        build_repository_map(repository),
    )

    assert not any(
        evidence.startswith("Issue title matches owner and method ")
        for candidate in env_candidates
        for evidence in candidate.evidence
    )


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


def test_graph_expands_bounded_reverse_import_from_title_matching_module(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/package/worker/control.py",
        "def revoke_tasks(task_ids):\n"
        "    return task_ids\n\n"
        "def revoke_by_headers(headers):\n"
        "    return headers\n",
    )
    write_source(
        repository,
        "src/package/worker/pidbox.py",
        "from . import control\n\n"
        "class Pidbox:\n"
        "    def reset(self):\n"
        "        return control\n",
    )
    for index in range(25):
        write_source(
            repository,
            f"src/package/worker/open_files_{index}.py",
            "def abort_worker_after_open_files():\n"
            '    """Abort worker after too many open files."""\n'
            "    return None\n",
        )
    record = issue(
        "Worker dies after multiple revokes with too many open files",
        "The worker aborts after repeated remote-control messages.",
    )

    candidates = locate_candidates(
        record,
        build_repository_map(repository),
        limit=20,
    )

    pidbox = next(
        candidate
        for candidate in candidates
        if candidate.file == "src/package/worker/pidbox.py"
    )
    assert any(
        evidence.startswith(
            "Imports title-matching source module "
            "src/package/worker/control.py:"
        )
        for evidence in pidbox.evidence
    )


def test_graph_skips_reverse_import_without_repeated_matching_symbol_term(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/package/worker/control.py",
        "def revoke_tasks(task_ids):\n"
        "    return task_ids\n\n"
        "def worker_dies():\n"
        "    return None\n",
    )
    write_source(
        repository,
        "src/package/worker/pidbox.py",
        "from . import control\n\n"
        "def reset():\n"
        "    return control\n",
    )

    candidates = locate_candidates(
        issue(
            "Worker dies after multiple revokes",
            "The worker aborts after remote-control messages.",
        ),
        build_repository_map(repository),
    )

    assert all(
        not any(
            evidence.startswith("Imports title-matching source module ")
            for evidence in candidate.evidence
        )
        for candidate in candidates
    )


def test_graph_skips_reverse_import_for_wide_or_auxiliary_importers(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/package/worker/control.py",
        "def revoke_tasks(task_ids):\n"
        "    return task_ids\n\n"
        "def revoke_by_headers(headers):\n"
        "    return headers\n",
    )
    for index in range(4):
        write_source(
            repository,
            f"src/package/worker/consumer_{index}.py",
            "from . import control\n\n"
            "def consume():\n"
            "    return control\n",
        )
    write_source(
        repository,
        "t/worker/pidbox.py",
        "from package.worker import control\n\n"
        "def reset():\n"
        "    return control\n",
    )

    candidates = locate_candidates(
        issue(
            "Worker dies after multiple revokes",
            "The worker aborts after remote-control messages.",
        ),
        build_repository_map(repository),
    )

    assert all(
        not any(
            evidence.startswith("Imports title-matching source module ")
            for evidence in candidate.evidence
        )
        for candidate in candidates
    )


def test_graph_skips_reverse_import_outside_title_path_scope(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/package/worker/control.py",
        "def revoke_tasks(task_ids):\n"
        "    return task_ids\n\n"
        "def revoke_by_headers(headers):\n"
        "    return headers\n",
    )
    write_source(
        repository,
        "src/package/api/pidbox.py",
        "from package.worker import control\n\n"
        "def reset():\n"
        "    return control\n",
    )

    candidates = locate_candidates(
        issue(
            "Worker dies after multiple revokes",
            "The worker aborts after remote-control messages.",
        ),
        build_repository_map(repository),
    )

    assert all(
        not any(
            evidence.startswith("Imports title-matching source module ")
            for evidence in candidate.evidence
        )
        for candidate in candidates
    )


def test_graph_skips_reverse_import_scoped_only_by_root_package(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(repository, "pydantic-core/python/pydantic_core/__init__.py", "")
    write_source(
        repository,
        "pydantic-core/python/pydantic_core/control.py",
        "def revoke_tasks(task_ids):\n"
        "    return task_ids\n\n"
        "def revoke_by_headers(headers):\n"
        "    return headers\n",
    )
    write_source(
        repository,
        "pydantic-core/python/pydantic_core/pidbox.py",
        "from . import control\n\n"
        "def reset():\n"
        "    return control\n",
    )

    candidates = locate_candidates(
        issue(
            "Pydantic dies after multiple revokes",
            "The process aborts after remote-control messages.",
        ),
        build_repository_map(repository),
    )

    assert all(
        not any(
            evidence.startswith("Imports title-matching source module ")
            for evidence in candidate.evidence
        )
        for candidate in candidates
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


def test_graph_expands_rare_issue_referenced_qualified_call_peer(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/python.py",
        "import inspect\n\n"
        "def get_func_args_dict(func):\n"
        '    """Return callable arguments."""\n'
        "    return inspect.signature(func)\n",
    )
    write_source(
        repository,
        "src/decorators.py",
        "import inspect\n\n"
        "def _warn_spider_arg(func):\n"
        "    return inspect.signature(func)\n",
    )
    for index in range(25):
        write_source(
            repository,
            f"src/lazy_annotation_{index}.py",
            "def get_func_args_dict_annotation():\n"
            '    """Support lazy annotations safely."""\n'
            "    return None\n",
        )
    record = issue(
        "get_func_args_dict does not support lazy annotations",
        "The crash occurs in `inspect.signature()`.",
    )

    candidates = locate_candidates(
        record,
        build_repository_map(repository),
        limit=20,
    )
    decorators = next(
        candidate
        for candidate in candidates
        if candidate.file == "src/decorators.py"
    )

    assert candidates.index(decorators) >= 17
    assert decorators.symbol == "_warn_spider_arg"
    assert any(
        evidence.startswith(
            "Shares issue-referenced qualified call inspect.signature "
        )
        for evidence in decorators.evidence
    )


@pytest.mark.parametrize(
    ("location", "location_evidence"),
    [
        (
            '  File "/tmp/project/src/decorators.py", line 8, '
            "in handle_traceback\n",
            "Traceback frame points to symbol handle_traceback",
        ),
        (
            "See src/decorators.py#L8\n",
            "Issue source line points to symbol handle_traceback",
        ),
        (
            "See src/decorators.py.\n"
            "```python\n"
            "def handle_traceback():\n"
            '    message = "failed while inspecting the decorated function"\n'
            "    raise RuntimeError(message)\n"
            "```\n",
            "Issue source snippet matches symbol handle_traceback",
        ),
    ],
)
def test_structured_location_overrides_shared_qualified_call_symbol(
    tmp_path: Path,
    location: str,
    location_evidence: str,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/python.py",
        "import inspect\n\n"
        "def get_func_args_dict(func):\n"
        "    return inspect.signature(func)\n",
    )
    write_source(
        repository,
        "src/decorators.py",
        "import inspect\n\n"
        "def _warn_spider_arg(func):\n"
        "    return inspect.signature(func)\n\n"
        "def handle_traceback():\n"
        '    message = "failed while inspecting the decorated function"\n'
        "    raise RuntimeError(message)\n",
    )
    for index in range(25):
        write_source(
            repository,
            f"src/lazy_annotation_{index}.py",
            "def get_func_args_dict_annotation():\n"
            "    return None\n",
        )
    record = issue(
        "get_func_args_dict does not support lazy annotations",
        "The crash occurs in `inspect.signature()`.\n" + location,
    )

    candidates = locate_candidates(
        record,
        build_repository_map(repository),
        limit=20,
    )
    decorators = next(
        candidate
        for candidate in candidates
        if candidate.file == "src/decorators.py"
    )

    assert decorators.symbol == "handle_traceback"
    assert location_evidence in decorators.evidence


@pytest.mark.parametrize(
    ("body", "peer_count"),
    [
        ("The crash occurs while inspecting a signature.", 1),
        ("The crash occurs in `inspect.signature()`.", 3),
    ],
)
def test_graph_skips_unscoped_or_common_qualified_call_peers(
    tmp_path: Path,
    body: str,
    peer_count: int,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/python.py",
        "import inspect\n\n"
        "def get_func_args_dict(func):\n"
        "    return inspect.signature(func)\n",
    )
    for index in range(peer_count):
        write_source(
            repository,
            f"src/peer_{index}.py",
            "import inspect\n\n"
            "def unrelated(func):\n"
            "    return inspect.signature(func)\n",
        )
    for index in range(25):
        write_source(
            repository,
            f"src/lazy_annotation_{index}.py",
            "def get_func_args_dict_annotation():\n"
            '    """Support lazy annotations safely."""\n'
            "    return None\n",
        )
    record = issue(
        "get_func_args_dict does not support lazy annotations",
        body,
    )

    candidates = locate_candidates(
        record,
        build_repository_map(repository),
        limit=20,
    )

    assert all(
        not any(
            evidence.startswith(
                "Shares issue-referenced qualified call "
            )
            for evidence in candidate.evidence
        )
        for candidate in candidates
    )


@pytest.mark.parametrize(
    "seed_path",
    ["tests/test_signatures.py", "examples/signatures.py"],
)
def test_graph_skips_qualified_call_propagation_from_auxiliary_seed(
    tmp_path: Path,
    seed_path: str,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        seed_path,
        "import inspect\n\n"
        "def get_func_args_dict(func):\n"
        "    return inspect.signature(func)\n",
    )
    for index in range(2):
        write_source(
            repository,
            f"src/peer_{index}.py",
            "import inspect\n\n"
            "def unrelated(func):\n"
            "    return inspect.signature(func)\n",
        )
    for index in range(25):
        write_source(
            repository,
            f"src/lazy_annotation_{index}.py",
            "def get_func_args_dict_annotation():\n"
            '    """Support lazy annotations safely."""\n'
            "    return None\n",
        )
    record = issue(
        "get_func_args_dict does not support lazy annotations",
        "The crash occurs in `inspect.signature()`.",
    )

    candidates = locate_candidates(
        record,
        build_repository_map(repository),
        limit=20,
    )

    assert all(
        not any(
            evidence.startswith(
                "Shares issue-referenced qualified call "
            )
            for evidence in candidate.evidence
        )
        for candidate in candidates
    )


@pytest.mark.parametrize(
    ("caller", "add_duplicate"),
    [("get_func_args_dict", True), ("run", False)],
)
def test_graph_skips_unsafe_bare_qualified_call_caller(
    tmp_path: Path,
    caller: str,
    add_duplicate: bool,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/python.py",
        "import inspect\n\n"
        f"def {caller}(func):\n"
        "    return inspect.signature(func)\n",
    )
    if add_duplicate:
        write_source(
            repository,
            "src/duplicate.py",
            f"def {caller}(func):\n"
            "    return func\n",
        )
    write_source(
        repository,
        "src/decorators.py",
        "import inspect\n\n"
        "def _warn_spider_arg(func):\n"
        "    return inspect.signature(func)\n",
    )
    for index in range(25):
        write_source(
            repository,
            f"src/lazy_annotation_{index}.py",
            f"def {caller}_annotation():\n"
            '    """Support lazy annotations safely."""\n'
            "    return None\n",
        )
    record = issue(
        f"{caller} does not support lazy annotations",
        f"The `{caller}` path crashes in `inspect.signature()`.",
    )

    candidates = locate_candidates(
        record,
        build_repository_map(repository),
        limit=20,
    )

    assert all(
        not any(
            evidence.startswith(
                "Shares issue-referenced qualified call "
            )
            for evidence in candidate.evidence
        )
        for candidate in candidates
    )


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


def test_graph_reranking_follows_local_import_into_constructor(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/console.py",
        "def print_json(value):\n"
        "    from .json_render import JSON\n\n"
        "    return JSON(value)\n",
    )
    write_source(
        repository,
        "src/json_render.py",
        "from .highlighter import JSONHighlighter\n\n"
        "class JSON:\n"
        "    def __init__(self, value):\n"
        "        self.text = JSONHighlighter()(value)\n",
    )
    write_source(
        repository,
        "src/highlighter.py",
        "class JSONHighlighter:\n"
        "    def __call__(self, value):\n"
        "        return value\n",
    )
    record = issue(
        "JSON highlighting is incorrect",
        "The traceback points to src/console.py.",
    )

    candidates = locate_candidates(
        record,
        build_repository_map(repository),
    )
    highlighter = next(
        candidate
        for candidate in candidates
        if candidate.file == "src/highlighter.py"
    )

    assert (
        "Two-hop function-local import chain calls symbols defined here: "
        "JSONHighlighter"
    ) in highlighter.evidence


def test_graph_reranking_keeps_mixed_import_calls_as_standard_relations(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/seed.py",
        "from .target import refresh\n\n"
        "refresh()\n\n"
        "def update():\n"
        "    from .target import refresh\n\n"
        "    return refresh()\n",
    )
    write_source(
        repository,
        "src/target.py",
        "def refresh():\n    return None\n",
    )
    record = issue(
        "Refresh fails",
        "The traceback points to src/seed.py.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))
    target = next(
        candidate for candidate in candidates if candidate.file == "src/target.py"
    )

    assert (
        "Related source calls imported symbols defined here: refresh"
        in target.evidence
    )
    assert not any(
        evidence.startswith("Function-local import calls symbols defined here: ")
        for evidence in target.evidence
    )


def test_graph_reranking_does_not_use_function_local_second_hop(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(
        repository,
        "src/seed.py",
        "from .screen import refresh_layout\n\n"
        "def update_layout():\n"
        "    return refresh_layout()\n",
    )
    write_source(
        repository,
        "src/screen.py",
        "def refresh_layout():\n"
        "    from .rebuild import reflow_children\n\n"
        "    return reflow_children()\n",
    )
    write_source(
        repository,
        "src/rebuild.py",
        "def reflow_children():\n    return None\n",
    )
    record = issue(
        "Refresh layout fails",
        "The traceback points to src/seed.py.",
    )

    candidates = locate_candidates(record, build_repository_map(repository))
    rebuild = next(
        candidate
        for candidate in candidates
        if candidate.file == "src/rebuild.py"
    )

    assert not any(
        evidence.startswith("Two-hop source")
        for evidence in rebuild.evidence
    )


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


def test_locate_candidates_follows_unique_package_reexport_from_referenced_path(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(repository, "src/package/__init__.py", "")
    write_source(
        repository,
        "src/package/runtime/reported.py",
        "from package.runtime.facade import RuntimeManager\n\n"
        "def load_runtime():\n"
        "    return RuntimeManager.find_all()\n",
    )
    write_source(
        repository,
        "src/package/runtime/facade/__init__.py",
        "from package.runtime.facade.manager import RuntimeManager\n",
    )
    write_source(
        repository,
        "src/package/runtime/facade/manager.py",
        "class RuntimeManager:\n"
        "    @classmethod\n"
        "    def find_all(cls):\n"
        "        return []\n",
    )

    candidates = locate_candidates(
        issue(
            "Runtime remains active after deactivation",
            "The failing check is in src/package/runtime/reported.py.",
        ),
        build_repository_map(repository),
    )
    manager = next(
        candidate
        for candidate in candidates
        if candidate.file == "src/package/runtime/facade/manager.py"
    )

    assert (
        "Issue-referenced source imports re-exported symbols defined here: "
        "RuntimeManager via src/package/runtime/facade/__init__.py"
        in manager.evidence
    )


def test_locate_candidates_skips_ambiguous_package_reexports(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(repository, "src/package/__init__.py", "")
    write_source(
        repository,
        "src/package/runtime/reported.py",
        "from package.runtime.facade import RuntimeManager\n\n"
        "manager = RuntimeManager\n",
    )
    write_source(
        repository,
        "src/package/runtime/facade/__init__.py",
        "from package.runtime.facade.first import RuntimeManager\n"
        "from package.runtime.facade.second import RuntimeManager\n",
    )
    for module in ("first", "second"):
        write_source(
            repository,
            f"src/package/runtime/facade/{module}.py",
            "class RuntimeManager:\n    pass\n",
        )

    candidates = locate_candidates(
        issue(
            "Runtime remains active after deactivation",
            "The failing check is in src/package/runtime/reported.py.",
        ),
        build_repository_map(repository),
    )

    assert not any(
        evidence.startswith(
            "Issue-referenced source imports re-exported symbols defined here: "
        )
        for candidate in candidates
        for evidence in candidate.evidence
    )


def test_locate_candidates_skips_duplicate_reexport_target_definitions(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(repository, "src/package/__init__.py", "")
    write_source(
        repository,
        "src/package/runtime/reported.py",
        "from package.runtime.facade import RuntimeManager\n\n"
        "manager = RuntimeManager\n",
    )
    write_source(
        repository,
        "src/package/runtime/facade/__init__.py",
        "from package.runtime.facade.manager import RuntimeManager\n",
    )
    write_source(
        repository,
        "src/package/runtime/facade/manager.py",
        "class RuntimeManager:\n    pass\n\n"
        "class RuntimeManager:\n    pass\n",
    )

    candidates = locate_candidates(
        issue(
            "Runtime remains active after deactivation",
            "The failing check is in src/package/runtime/reported.py.",
        ),
        build_repository_map(repository),
    )

    assert not any(
        evidence.startswith(
            "Issue-referenced source imports re-exported symbols defined here: "
        )
        for candidate in candidates
        for evidence in candidate.evidence
    )


def test_locate_candidates_skips_conditional_package_reexports(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(repository, "src/package/__init__.py", "")
    write_source(
        repository,
        "src/package/runtime/reported.py",
        "from package.runtime.facade import RuntimeManager\n\n"
        "manager = RuntimeManager\n",
    )
    write_source(
        repository,
        "src/package/runtime/facade/__init__.py",
        "from typing import TYPE_CHECKING\n\n"
        "if TYPE_CHECKING:\n"
        "    from package.runtime.facade.manager import RuntimeManager\n",
    )
    write_source(
        repository,
        "src/package/runtime/facade/manager.py",
        "class RuntimeManager:\n    pass\n",
    )

    candidates = locate_candidates(
        issue(
            "Runtime remains active after deactivation",
            "The failing check is in src/package/runtime/reported.py.",
        ),
        build_repository_map(repository),
    )

    assert not any(
        evidence.startswith(
            "Issue-referenced source imports re-exported symbols defined here: "
        )
        for candidate in candidates
        for evidence in candidate.evidence
    )


def test_locate_candidates_skips_unused_package_reexports(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(repository, "src/package/__init__.py", "")
    write_source(
        repository,
        "src/package/runtime/reported.py",
        "from package.runtime.facade import RuntimeManager\n",
    )
    write_source(
        repository,
        "src/package/runtime/facade/__init__.py",
        "from package.runtime.facade.manager import RuntimeManager\n",
    )
    write_source(
        repository,
        "src/package/runtime/facade/manager.py",
        "class RuntimeManager:\n    pass\n",
    )

    candidates = locate_candidates(
        issue(
            "Runtime remains active after deactivation",
            "The failing check is in src/package/runtime/reported.py.",
        ),
        build_repository_map(repository),
    )

    assert not any(
        evidence.startswith(
            "Issue-referenced source imports re-exported symbols defined here: "
        )
        for candidate in candidates
        for evidence in candidate.evidence
    )


def test_locate_candidates_skips_reexports_outside_referenced_subsystem(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    write_source(repository, "src/package/__init__.py", "")
    write_source(
        repository,
        "src/package/api/reported.py",
        "from package.facade import RuntimeManager\n\n"
        "manager = RuntimeManager\n",
    )
    write_source(
        repository,
        "src/package/facade/__init__.py",
        "from package.runtime.manager import RuntimeManager\n",
    )
    write_source(
        repository,
        "src/package/runtime/manager.py",
        "class RuntimeManager:\n    pass\n",
    )

    candidates = locate_candidates(
        issue(
            "API remains active after deactivation",
            "The failing check is in src/package/api/reported.py.",
        ),
        build_repository_map(repository),
    )

    assert not any(
        evidence.startswith(
            "Issue-referenced source imports re-exported symbols defined here: "
        )
        for candidate in candidates
        for evidence in candidate.evidence
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


def test_git_cochanges_use_a_fixed_recent_commit_window(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    write_source(repository, "src/seed.py", "def handle_regression():\n    return 0\n")
    write_source(repository, "src/target.py", "def historical_regression():\n    return 0\n")
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
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "cochange"], check=True)
    for revision in range(101):
        write_source(
            repository,
            "src/unrelated.py",
            f"def unrelated():\n    return {revision}\n",
        )
        subprocess.run(
            ["git", "-C", str(repository), "add", "src/unrelated.py"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repository), "commit", "-qm", f"unrelated {revision}"],
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

    assert not any(
        "Changed with lexical seed files in" in evidence
        for evidence in target.evidence
    )


def test_git_cochanges_timeout_without_lazy_fetch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    (repository / ".git").mkdir(parents=True)
    observed: dict[str, object] = {}

    def timeout_run(*args: object, **kwargs: object) -> None:
        observed.update(kwargs)
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr(investigator_module.subprocess, "run", timeout_run)

    relations = investigator_module._history_relations(
        repository,
        ["src/seed.py"],
        {"src/seed.py", "src/target.py"},
        {"src/seed.py": False, "src/target.py": False},
    )

    assert relations == {}
    assert observed["timeout"] == 30
    observed_env = observed["env"]
    assert isinstance(observed_env, dict)
    assert observed_env["GIT_NO_LAZY_FETCH"] == "1"


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

    assert len(report.candidates) == DEFAULT_CANDIDATE_LIMIT

    expanded = investigate(record, build_repository_map(repository), candidate_limit=40)
    assert len(expanded.candidates) == 25


def test_expanded_investigation_reserves_three_supported_paths(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    for index in range(40):
        write_source(
            repository,
            f"src/worker_failure_alpha_beta_gamma_{index:02d}.rs",
            'const MARKERS: &str = "AlphaWorker BetaWorker GammaWorker";\n',
        )
    targets = [
        "src/alpha_worker.rs",
        "src/beta_worker.rs",
        "src/gamma_worker.rs",
    ]
    for target in targets:
        write_source(repository, target, "const TARGET: bool = true;\n")
    record = issue(
        "AlphaWorker BetaWorker GammaWorker failure",
        "The worker selection fails.",
    )
    repository_map = build_repository_map(repository)

    default = investigate(record, repository_map)
    expanded = investigate(record, repository_map, candidate_limit=40)
    default_targets = [
        candidate.file for candidate in default.candidates if candidate.file in targets
    ]
    expanded_targets = [
        candidate.file for candidate in expanded.candidates if candidate.file in targets
    ]

    assert default_targets == targets[:1]
    assert expanded_targets == targets
    assert [candidate.file for candidate in expanded.candidates[-3:]] == targets


def test_investigate_explicit_default_limit_preserves_order(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    for index in range(25):
        write_source(
            repository,
            f"src/token_handler_{index}.py",
            f"def handle_token_{index}():\n    return None\n",
        )
    record = issue("Token handler failure", "Token handling fails.")
    repository_map = build_repository_map(repository)

    implicit = investigate(record, repository_map)
    explicit = investigate(
        record,
        repository_map,
        candidate_limit=DEFAULT_CANDIDATE_LIMIT,
    )

    assert implicit.model_dump() == explicit.model_dump()
