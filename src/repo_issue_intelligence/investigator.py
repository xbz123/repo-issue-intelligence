from __future__ import annotations

import ast
import io
import os
import re
import subprocess
import tokenize
import unicodedata
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path

from .models import (
    CandidateLocation,
    CandidateSymbolLocation,
    FileRecord,
    Hypothesis,
    InvestigationReport,
    IssueRecord,
    QualifiedExternalCall,
    RepositoryMap,
    ReproductionPlan,
    ResolvedCall,
    SymbolRecord,
)

GENERIC_TERMS = {
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "been",
    "by",
    "code",
    "com",
    "could",
    "def",
    "details",
    "example",
    "for",
    "github",
    "https",
    "import",
    "issue",
    "error",
    "fails",
    "failure",
    "problem",
    "when",
    "with",
    "from",
    "the",
    "this",
    "that",
    "have",
    "does",
    "has",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "more",
    "none",
    "not",
    "of",
    "on",
    "only",
    "or",
    "print",
    "py",
    "python",
    "return",
    "should",
    "so",
    "than",
    "then",
    "there",
    "to",
    "using",
    "was",
    "were",
    "will",
    "would",
}
SOURCE_SUFFIXES = "py|js|jsx|ts|tsx|java|go|rs|c|cc|cpp"
PATH_REFERENCE_PATTERN = re.compile(
    rf"(?<![A-Za-z0-9_.-])(?:[A-Za-z]:)?[A-Za-z0-9_.~:/\\-]*"
    rf"[A-Za-z0-9_.-]+\.(?:{SOURCE_SUFFIXES})(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
CODE_SPAN_PATTERN = re.compile(r"(?<!`)`([^`\n]{1,120})`(?!`)")
FENCED_CODE_PATTERN = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
COMMAND_LINE_OPTION_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])--(?P<option>[A-Za-z][A-Za-z0-9]*"
    r"(?:-[A-Za-z0-9]+)+)(?![A-Za-z0-9_-])"
)
COMMAND_LINE_IDENTIFIER_LIMIT = 16
ECMASCRIPT_FENCE_PREFIX_PATTERN = re.compile(
    r"^```[ \t]*(?:javascript|js|typescript|ts|jsx|tsx)\b",
    re.IGNORECASE,
)
IDENTIFIER_PATTERN = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\b"
)
CALLED_IDENTIFIER_PATTERN = re.compile(
    r"\b((?:[A-Za-z_][A-Za-z0-9_]*\.)*[A-Za-z_][A-Za-z0-9_]*)\s*\("
)
CANONICAL_TRACEBACK_FRAME_PATTERN = re.compile(
    r'^\s*File\s+["\'](?P<path>.+?\.py)["\'],\s+line\s+\d+,\s+in\s+'
    r"(?P<symbol>\S+)\s*$",
    re.MULTILINE,
)
COMPACT_TRACEBACK_FRAME_PATTERN = re.compile(
    r"^\s*(?:(?:\d+|at)\s+)?(?P<path>.+?\.py):\d+\s+in\s+"
    r"(?P<symbol>\S+)\s*$",
    re.MULTILINE,
)
IMMUTABLE_SOURCE_LINE_REFERENCE_PATTERN = re.compile(
    r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/blob/"
    r"(?P<revision>[0-9a-f]{40})/"
    r"(?P<path>[A-Za-z0-9_.~%+/-]+\.py)#L(?P<line>[1-9][0-9]*)"
    r"(?:-L[1-9][0-9]*)?",
    re.IGNORECASE,
)
PLAIN_SOURCE_LINE_REFERENCE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_./:\\-])"
    r"(?P<path>(?:[A-Za-z0-9_.-]+[\\/])*[A-Za-z0-9_.-]+\.py)"
    r"#L(?P<line>[1-9][0-9]*)(?:-L[1-9][0-9]*)?",
    re.IGNORECASE,
)
SOURCE_LINE_REFERENCE_LIMIT = 8
SOURCE_SNIPPET_LIMIT = 4
SOURCE_SNIPPET_MIN_NONEMPTY_LINES = 3
SOURCE_SNIPPET_MAX_NONEMPTY_LINES = 12
SOURCE_SNIPPET_MIN_CHARACTERS = 60
SOURCE_SNIPPET_MAX_CHARACTERS = 2_000
GRAPH_SEED_LIMIT = 8
GRAPH_SEED_MIN_SCORE = 4.0
GRAPH_BONUS_LIMIT = 10.0
GRAPH_RERANK_BAND_SIZE = 10
GRAPH_EXPANSION_MIN_BONUS = 5.0
GRAPH_EXPANSION_SLOTS = 3
GRAPH_STRONG_EXPANSION_SLOTS = 1
PROTECTED_BASE_RESERVATION_SLOTS = 1
EXPANDED_PROTECTED_BASE_RESERVATION_MIN_LIMIT = 40
EXPANDED_PROTECTED_BASE_RESERVATION_SLOTS = 3
DEFAULT_CANDIDATE_LIMIT = 20
GRAPH_SECOND_HOP_CALL_MIN_LENGTH = 5
GRAPH_STRONG_EXPANSION_PREFIX = "Two-hop source call chain via "
FUNCTION_LOCAL_RELATION_PREFIX = (
    "Function-local import calls symbols defined here: "
)
FUNCTION_LOCAL_TWO_HOP_PREFIX = (
    "Two-hop function-local import chain calls symbols defined here: "
)
SHARED_QUALIFIED_CALL_PREFIX = "Shares issue-referenced qualified call "
SHARED_QUALIFIED_CALL_MAX_FILES = 3
SEMANTIC_TEST_SOURCE_RELATION_PREFIX = (
    "Matching test filename maps uniquely to this source file: "
)
REVERSE_IMPORT_RELATION_PREFIX = "Imports title-matching source module "
REEXPORTED_IMPORT_RELATION_PREFIX = (
    "Issue-referenced source imports re-exported symbols defined here: "
)
PYTHON_DEPENDENCY_METADATA_FILES = {"setup.py"}
DEPENDENCY_METADATA_CONTEXT_TERMS = {
    "dependency",
    "dependencies",
    "deprecated",
    "deprecation",
    "release",
    "requirement",
    "requirements",
    "upgrade",
    "version",
}
GENERIC_SUBSYSTEM_TERMS = {"common", "core", "shared", "util", "utils"}
AUXILIARY_PATH_PARTS = {"docs", "docs_src", "examples", "scripts"}
SPECIFIC_PATH_TERM_MAX_FILES = 3
DIRECT_LOCAL_IDENTIFIER_MIN_LENGTH = 5
GENERIC_QUALIFIED_TITLE_METHOD_TERMS = {
    "build",
    "call",
    "creat",
    "get",
    "handl",
    "init",
    "is",
    "main",
    "make",
    "new",
    "proces",
    "run",
    "set",
}
HISTORY_COMMIT_LIMIT = 50
HISTORY_ANCESTOR_LIMIT = 100
HISTORY_FILE_LIMIT = 50
BLAME_SEED_LIMIT = 2
BLAME_FILE_LIMIT = 20
SYMBOL_EVIDENCE_PREFIXES = (
    "Symbol ",
    "Issue references symbol ",
    "Issue title and code call constructor ",
    "Issue title matches owner and method ",
    "Traceback frame points to symbol ",
    "Issue source line points to symbol ",
    "Issue source snippet matches symbol ",
    "Issue title strongly matches symbol ",
    "Issue references owning symbol ",
    "Issue-matching symbols call ",
)
ECMASCRIPT_LANGUAGES = {"JavaScript", "TypeScript"}
DIRECT_SYMBOL_KINDS = {"function", "struct", "enum", "trait", "type", "union"}
DECLARATION_CALL_PREFIX_PATTERN = re.compile(
    r"\b(?:class|def|fn|struct|enum|trait|type|union|macro)\s+$"
)
UNICODE_ID_CONTINUE_CATEGORIES = {
    "Lu",
    "Ll",
    "Lt",
    "Lm",
    "Lo",
    "Nl",
    "Mn",
    "Mc",
    "Nd",
    "Pc",
}
ECMASCRIPT_IDENTIFIER_START_CATEGORIES = {"Lu", "Ll", "Lt", "Lm", "Lo", "Nl"}
UNICODE_OTHER_ID_START = {"\u2118", "\u212e", "\u309b", "\u309c"}
UNICODE_OTHER_ID_CONTINUE = {"\u00b7", "\u0387", "\u19da", "\u30fb", "\uff65"}
UNICODE_ID_CONTINUE_EXCLUSIONS = {"\u2e2f"}
ECMASCRIPT_IDENTIFIER_CONTINUATION_EXTRAS = {"$", "\u200c", "\u200d"}


@dataclass(frozen=True)
class TracebackFrame:
    path: str
    symbol: str


@dataclass(frozen=True)
class SourceLineReference:
    path: str
    line: int
    revision: str | None = None


@dataclass(frozen=True)
class IssueSignals:
    terms: frozenset[str]
    content_terms: frozenset[str]
    title_terms: frozenset[str]
    raw_title_terms: frozenset[str]
    raw_title_term_sequence: tuple[str, ...]
    primary_terms: frozenset[str]
    identifiers: frozenset[str]
    verbatim_identifiers: frozenset[str]
    title_identifiers: frozenset[str]
    primary_identifiers: frozenset[str]
    explicit_identifiers: frozenset[str]
    command_line_identifiers: frozenset[str]
    called_identifiers: tuple[str, ...]
    identifier_mentions: tuple[str, ...]
    paths: frozenset[str]
    traceback_frames: tuple[TracebackFrame, ...]
    source_line_references: tuple[SourceLineReference, ...]
    source_snippets: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class SymbolMatch:
    symbol: SymbolRecord
    overlap: frozenset[str]
    local_overlap: frozenset[str]
    primary_identifier_match: bool
    exact_identifier_match: bool
    identifier_mention_count: int
    scoped_identifier_match: bool
    explicit_identifier_match: bool
    unscoped_explicit_identifier_match: bool
    unscoped_dotted_identifier_match: bool
    qualified_component_match: bool
    qualified_identifier_match: bool
    qualified_title_semantic_match: bool
    qualified_title_semantic_strength: float
    constructor_call_index: int | None
    traceback_frame_index: int | None
    source_line_reference_index: int | None
    source_snippet_index: int | None
    semantic_terms: frozenset[str]


def _normalized_qualified_identifier(value: str) -> str | None:
    components = [
        component[2:] if component.startswith("r#") else component
        for component in re.split(r"(?:::|\.)", value)
    ]
    normalized = [unicodedata.normalize("NFC", component) for component in components]
    if not normalized or any(
        component == "_" or not component.isidentifier()
        for component in normalized
    ):
        return None
    return ".".join(normalized)


def _unicode_identifier_matches(value: str) -> list[tuple[int, int, str]]:
    matches: list[tuple[int, int, str]] = []
    index = 0
    while index < len(value):
        if index and _unicode_identifier_continue(value[index - 1]):
            index += 1
            continue
        identifier = _qualified_identifier_at(value, index)
        if identifier is None:
            index += 1
            continue
        normalized, cursor = identifier
        if any(not character.isascii() for character in normalized):
            matches.append((index, cursor, normalized))
        index = cursor
    return matches


def _qualified_identifier_at(value: str, index: int) -> tuple[str, int] | None:
    component = _unicode_identifier_at(value, index)
    if component is None:
        return None
    components = [component[0]]
    cursor = component[1]
    while cursor < len(value):
        separator_length = (
            2 if value.startswith("::", cursor) else 1 if value[cursor] == "." else 0
        )
        if not separator_length:
            break
        next_component = _unicode_identifier_at(value, cursor + separator_length)
        if next_component is None:
            break
        components.append(next_component[0])
        cursor = next_component[1]
    return ".".join(components), cursor


def _ecmascript_dollar_identifier_matches(
    value: str,
) -> list[tuple[int, int, str]]:
    matches: list[tuple[int, int, str]] = []
    index = 0
    while index < len(value):
        if index and _is_identifier_continuation(value[index - 1], "JavaScript"):
            index += 1
            continue
        identifier = _ecmascript_qualified_identifier_at(value, index)
        if identifier is None:
            index += 1
            continue
        candidate, cursor = identifier
        if "$" in candidate:
            matches.append((index, cursor, candidate))
        index = cursor
    return matches


def _ecmascript_qualified_identifier_at(
    value: str,
    index: int,
) -> tuple[str, int] | None:
    component = _ecmascript_identifier_at(value, index)
    if component is None:
        return None
    cursor = component
    while cursor < len(value) and value[cursor] == ".":
        next_component = _ecmascript_identifier_at(value, cursor + 1)
        if next_component is None:
            break
        cursor = next_component
    return value[index:cursor], cursor


def _ecmascript_identifier_at(value: str, index: int) -> int | None:
    if index >= len(value) or not _is_ecmascript_identifier_start(value[index]):
        return None
    cursor = index + 1
    while cursor < len(value) and _is_identifier_continuation(
        value[cursor],
        "JavaScript",
    ):
        cursor += 1
    return cursor


def _verbatim_qualified_identifier(
    value: str,
    *,
    allow_ecmascript: bool = False,
) -> str | None:
    if "::" in value or any(
        component.startswith("r#") for component in value.split(".")
    ):
        return None
    if _normalized_qualified_identifier(value) is None and not (
        allow_ecmascript
        and all(
            _is_ecmascript_identifier_component(component)
            for component in value.split(".")
        )
    ):
        return None
    return value


def _is_ecmascript_identifier_component(value: str) -> bool:
    return bool(value) and _is_ecmascript_identifier_start(value[0]) and all(
        _is_identifier_continuation(character, "JavaScript")
        for character in value[1:]
    )


def _is_ecmascript_identifier_start(value: str) -> bool:
    return (
        value in {"$", "_"}
        or unicodedata.category(value) in ECMASCRIPT_IDENTIFIER_START_CATEGORIES
        or value in UNICODE_OTHER_ID_START
    )


def _unicode_identifier_at(value: str, index: int) -> tuple[str, int] | None:
    cursor = index + 2 if value.startswith("r#", index) else index
    if cursor >= len(value) or not _unicode_identifier_start(value[cursor]):
        return None
    start = cursor
    cursor += 1
    while cursor < len(value) and _unicode_identifier_continue(value[cursor]):
        cursor += 1
    identifier = unicodedata.normalize("NFC", value[start:cursor])
    if identifier == "_" or not identifier.isidentifier():
        return None
    return identifier, cursor


def _unicode_identifier_start(value: str) -> bool:
    return value == "_" or value.isidentifier()


def _unicode_identifier_continue(value: str) -> bool:
    return ("a" + value).isidentifier()


def _ordered_terms(value: str) -> list[str]:
    value = value.replace("\\", "/")
    value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    terms = [
        term
        for term in re.findall(r"[a-z][a-z0-9]{1,}", value.lower())
        if term not in GENERIC_TERMS
    ]
    unicode_identifiers: list[str] = []
    whole_identifier = _normalized_qualified_identifier(
        value.strip("`'\"()[]{}:,"),
    )
    if whole_identifier is not None:
        unicode_identifiers.append(whole_identifier)
    for span in CODE_SPAN_PATTERN.findall(value):
        identifier = _normalized_qualified_identifier(span.strip())
        if identifier is not None:
            unicode_identifiers.append(identifier)
    terms.extend(
        component.casefold()
        for identifier in unicode_identifiers
        if any(not character.isascii() for character in identifier)
        for component in identifier.split(".")
        if component.casefold() not in GENERIC_TERMS
    )
    return list(dict.fromkeys(terms))


def _terms(value: str) -> set[str]:
    return set(_ordered_terms(value))


def _identifier_variants(value: str) -> set[str]:
    lowered = value.lower().strip("`'\"()[]{}:,")
    parts = _ordered_terms(value)
    variants = {lowered}
    if "." in lowered:
        variants.add(lowered.rsplit(".", maxsplit=1)[-1])
    if parts:
        variants.add("_".join(parts))
        variants.add("".join(parts))
    return {
        variant
        for variant in variants
        if len(variant) >= 3 or any(not character.isascii() for character in variant)
    }


def _compact_identifier_variants(value: str) -> set[str]:
    compact = re.sub(r"[^a-z0-9]", "", value.lower())
    variants = {compact}
    if compact.endswith("s") and len(compact) > 4:
        variants.add(compact[:-1])
    return {variant for variant in variants if len(variant) >= 4}


def _extract_verbatim_identifiers(text: str) -> set[str]:
    identifiers: set[str] = set()
    for pattern in (CODE_SPAN_PATTERN, FENCED_CODE_PATTERN):
        for match in pattern.finditer(text):
            region = match.group(1)
            stripped_region = region.strip()
            ecmascript_fence = (
                pattern is FENCED_CODE_PATTERN
                and ECMASCRIPT_FENCE_PREFIX_PATTERN.match(match.group(0)) is not None
            )
            candidate = _verbatim_qualified_identifier(
                stripped_region,
                allow_ecmascript=ecmascript_fence
                or (
                    "$" in stripped_region
                    and any(not character.isascii() for character in stripped_region)
                ),
            )
            if candidate is not None:
                identifiers.add(candidate)
            dollar_matches = (
                _ecmascript_dollar_identifier_matches(region)
                if ecmascript_fence
                else []
            )
            identifiers.update(candidate for _, _, candidate in dollar_matches)
            for start, end, _ in _unicode_identifier_matches(region):
                if any(
                    dollar_start <= start and end <= dollar_end
                    for dollar_start, dollar_end, _ in dollar_matches
                ):
                    continue
                if (
                    start > 0
                    and _is_identifier_continuation(region[start - 1], "JavaScript")
                ) or (
                    end < len(region)
                    and _is_identifier_continuation(region[end], "JavaScript")
                ):
                    continue
                candidate = _verbatim_qualified_identifier(region[start:end])
                if candidate is not None:
                    identifiers.add(candidate)
    return identifiers


def _is_declaration_call_prefix(value: str) -> bool:
    return DECLARATION_CALL_PREFIX_PATTERN.search(value) is not None


def _extract_explicit_identifiers(text: str) -> set[str]:
    fenced_regions = FENCED_CODE_PATTERN.findall(text)
    code_regions = [*CODE_SPAN_PATTERN.findall(text), *fenced_regions]
    identifiers: set[str] = set()
    for region in code_regions:
        candidate = region.strip()
        normalized_candidate = _normalized_qualified_identifier(candidate)
        if normalized_candidate is not None:
            identifiers.add(normalized_candidate)
            if "::" in candidate:
                identifiers.add(normalized_candidate.rsplit(".", maxsplit=1)[-1])
        for match in CALLED_IDENTIFIER_PATTERN.finditer(region):
            prefix = region[max(0, match.start() - 12) : match.start()]
            if _is_declaration_call_prefix(prefix):
                continue
            called_identifier = match.group(1)
            identifiers.add(called_identifier)
            identifiers.add(called_identifier.rsplit(".", maxsplit=1)[-1])
        for start, end, called_identifier in _unicode_identifier_matches(region):
            cursor = end
            while cursor < len(region) and region[cursor].isspace():
                cursor += 1
            if cursor >= len(region) or region[cursor] != "(":
                continue
            prefix = region[max(0, start - 12) : start]
            if _is_declaration_call_prefix(prefix):
                continue
            identifiers.add(called_identifier)
            identifiers.add(called_identifier.rsplit(".", maxsplit=1)[-1])
    for region in fenced_regions:
        for start, end, identifier in _unicode_identifier_matches(region):
            if "." not in identifier and not (
                identifier.startswith("__") and identifier.endswith("__")
            ):
                continue
            prefix = region[max(0, start - 12) : start]
            if _is_declaration_call_prefix(prefix):
                continue
            identifiers.add(identifier)
            if "::" in region[start:end]:
                identifiers.add(identifier.rsplit(".", maxsplit=1)[-1])
        for match in IDENTIFIER_PATTERN.finditer(region):
            identifier = match.group(0)
            if "." not in identifier and not (
                identifier.startswith("__") and identifier.endswith("__")
            ):
                continue
            prefix = region[max(0, match.start() - 12) : match.start()]
            if _is_declaration_call_prefix(prefix):
                continue
            identifiers.add(identifier)
    for prefix in re.finditer(r"\bin\s+", text):
        identifier = _qualified_identifier_at(text, prefix.end())
        if identifier is None:
            continue
        normalized = identifier[0]
        if (
            "." in normalized
            or (normalized.startswith("__") and normalized.endswith("__"))
        ):
            identifiers.add(normalized)
    return identifiers


def _extract_command_line_identifiers(text: str) -> set[str]:
    identifiers: list[str] = []
    for match in COMMAND_LINE_OPTION_PATTERN.finditer(text):
        identifier = match.group("option").replace("-", "_")
        if identifier not in identifiers:
            identifiers.append(identifier)
        if len(identifiers) == COMMAND_LINE_IDENTIFIER_LIMIT:
            break
    return set(identifiers)


def _extract_called_identifiers(text: str) -> tuple[str, ...]:
    positioned_regions = [
        (match.start(1), match.group(1))
        for pattern in (CODE_SPAN_PATTERN, FENCED_CODE_PATTERN)
        for match in pattern.finditer(text)
    ]
    positioned_calls: list[tuple[int, str]] = []
    for region_start, region in sorted(positioned_regions):
        for match in CALLED_IDENTIFIER_PATTERN.finditer(region):
            prefix = region[max(0, match.start() - 12) : match.start()]
            if _is_declaration_call_prefix(prefix):
                continue
            positioned_calls.append(
                (region_start + match.start(), match.group(1))
            )
        for start, end, identifier in _unicode_identifier_matches(region):
            cursor = end
            while cursor < len(region) and region[cursor].isspace():
                cursor += 1
            if cursor >= len(region) or region[cursor] != "(":
                continue
            prefix = region[max(0, start - 12) : start]
            if _is_declaration_call_prefix(prefix):
                continue
            positioned_calls.append((region_start + start, identifier))
    return tuple(
        identifier
        for _, identifier in sorted(positioned_calls)
    )


def _extract_identifiers(text: str) -> set[str]:
    identifiers = set()
    for span in CODE_SPAN_PATTERN.findall(text):
        identifier = _normalized_qualified_identifier(span.strip())
        if identifier is not None:
            identifiers.add(identifier)
            if "::" in span:
                identifiers.add(identifier.rsplit(".", maxsplit=1)[-1])
    for identifier in IDENTIFIER_PATTERN.findall(text):
        if (
            "_" in identifier
            or "." in identifier
            or any(character.isupper() for character in identifier[1:])
        ):
            identifiers.add(identifier)
    return identifiers


def _extract_identifier_mentions(text: str) -> tuple[str, ...]:
    text_without_fenced_code = FENCED_CODE_PATTERN.sub(" ", text)
    mentions = [
        match.group(0)
        for match in IDENTIFIER_PATTERN.finditer(text_without_fenced_code)
    ]
    for span in CODE_SPAN_PATTERN.findall(text_without_fenced_code):
        identifier = _normalized_qualified_identifier(span.strip())
        if identifier is not None and any(
            not character.isascii() for character in identifier
        ):
            mentions.append(identifier)
    for match in CALLED_IDENTIFIER_PATTERN.finditer(text_without_fenced_code):
        identifier = match.group(1)
        if "." in identifier:
            mentions.append(identifier.rsplit(".", maxsplit=1)[-1])
    return tuple(mentions)


def _without_dotted_identifiers(text: str) -> str:
    characters = list(text)
    for start, end, identifier in _unicode_identifier_matches(text):
        if "." in identifier:
            characters[start:end] = " " * (end - start)
    return IDENTIFIER_PATTERN.sub(
        lambda match: " " if "." in match.group(0) else match.group(0),
        "".join(characters),
    )


def _extract_traceback_frames(text: str) -> tuple[TracebackFrame, ...]:
    positioned_frames = [
        (match.start(), match)
        for pattern in (
            CANONICAL_TRACEBACK_FRAME_PATTERN,
            COMPACT_TRACEBACK_FRAME_PATTERN,
        )
        for match in pattern.finditer(text)
    ]
    frames: list[TracebackFrame] = []
    for _, match in sorted(positioned_frames, key=lambda positioned: positioned[0]):
        symbol = _normalized_qualified_identifier(match.group("symbol"))
        if symbol is None:
            continue
        frames.append(
            TracebackFrame(
                path=match.group("path").replace("\\", "/"),
                symbol=symbol,
            )
        )
    return tuple(frames)


def _extract_source_line_references(
    text: str,
) -> tuple[SourceLineReference, ...]:
    immutable_matches = list(
        IMMUTABLE_SOURCE_LINE_REFERENCE_PATTERN.finditer(text)
    )
    positioned_references = [
        (
            match.start(),
            SourceLineReference(
                path=match.group("path").replace("\\", "/"),
                line=int(match.group("line")),
                revision=match.group("revision").lower(),
            ),
        )
        for match in immutable_matches
        if _safe_source_line_path(match.group("path"))
    ]
    immutable_spans = [match.span() for match in immutable_matches]
    positioned_references.extend(
        (
            match.start(),
            SourceLineReference(
                path=match.group("path").replace("\\", "/"),
                line=int(match.group("line")),
            ),
        )
        for match in PLAIN_SOURCE_LINE_REFERENCE_PATTERN.finditer(text)
        if _safe_source_line_path(match.group("path"))
        if not any(
            start <= match.start() < end
            for start, end in immutable_spans
        )
    )
    return tuple(
        reference
        for _, reference in sorted(
            positioned_references,
            key=lambda positioned: positioned[0],
        )[:SOURCE_LINE_REFERENCE_LIMIT]
    )


def _safe_source_line_path(path: str) -> bool:
    parts = Path(path.replace("\\", "/")).parts
    return bool(parts) and all(part not in {".", ".."} for part in parts)


def _extract_source_snippets(text: str) -> tuple[tuple[str, ...], ...]:
    snippets: list[tuple[str, ...]] = []
    for region in FENCED_CODE_PATTERN.findall(text):
        lines = [line.strip() for line in region.splitlines()]
        while lines and not lines[0]:
            lines.pop(0)
        while lines and not lines[-1]:
            lines.pop()
        nonempty_lines = [line for line in lines if line]
        character_count = sum(len(line) for line in nonempty_lines)
        if not (
            SOURCE_SNIPPET_MIN_NONEMPTY_LINES
            <= len(nonempty_lines)
            <= SOURCE_SNIPPET_MAX_NONEMPTY_LINES
            and SOURCE_SNIPPET_MIN_CHARACTERS
            <= character_count
            <= SOURCE_SNIPPET_MAX_CHARACTERS
        ):
            continue
        snippets.append(tuple(lines))
        if len(snippets) == SOURCE_SNIPPET_LIMIT:
            break
    return tuple(snippets)


def extract_issue_signals(issue: IssueRecord) -> IssueSignals:
    text = " ".join([issue.title, issue.body, *issue.labels])
    primary_text = " ".join([issue.title, *issue.labels])
    command_line_identifiers = _extract_command_line_identifiers(text)
    explicit_identifiers = _extract_explicit_identifiers(text)
    text_without_dotted_identifiers = _without_dotted_identifiers(text)
    content_terms = _terms(text_without_dotted_identifiers)
    content_terms.update(
        term
        for identifier in explicit_identifiers
        if "." not in identifier
        for term in _terms(identifier)
    )
    paths = {
        match.group(0).replace("\\", "/").strip("'\"()[]{}:,")
        for match in PATH_REFERENCE_PATTERN.finditer(text)
    }
    return IssueSignals(
        terms=frozenset(_terms(text)),
        content_terms=frozenset(content_terms),
        title_terms=frozenset(_terms(issue.title)),
        raw_title_terms=frozenset(_raw_semantic_terms(issue.title)),
        raw_title_term_sequence=_raw_semantic_term_sequence(issue.title),
        primary_terms=frozenset(_terms(primary_text)),
        identifiers=frozenset(_extract_identifiers(text)),
        verbatim_identifiers=frozenset(_extract_verbatim_identifiers(text)),
        title_identifiers=frozenset(_extract_identifiers(issue.title)),
        primary_identifiers=frozenset(_extract_identifiers(primary_text)),
        explicit_identifiers=frozenset(explicit_identifiers),
        command_line_identifiers=frozenset(command_line_identifiers),
        called_identifiers=_extract_called_identifiers(text),
        identifier_mentions=_extract_identifier_mentions(text),
        paths=frozenset(paths),
        traceback_frames=_extract_traceback_frames(issue.body),
        source_line_references=_extract_source_line_references(issue.body),
        source_snippets=_extract_source_snippets(issue.body),
    )


def _semantic_term(term: str) -> str:
    if len(term) > 5 and term.endswith("ized"):
        term = term[:-4]
    elif len(term) > 5 and term.endswith("ing"):
        term = term[:-3]
    elif len(term) > 4 and term.endswith("ed"):
        term = term[:-2]
    elif len(term) > 5 and term.endswith(
        ("ches", "ses", "shes", "xes", "zes")
    ):
        term = term[:-2]
    elif len(term) > 3 and term.endswith("s"):
        term = term[:-1]
    if len(term) > 4 and term.endswith("e"):
        term = term[:-1]
    return term


def _semantic_terms(terms: frozenset[str] | set[str]) -> set[str]:
    return {_semantic_term(term) for term in terms}


def _raw_semantic_terms(value: str) -> set[str]:
    return set(_raw_semantic_term_sequence(value))


def _raw_semantic_term_sequence(value: str) -> tuple[str, ...]:
    value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    return tuple(
        _semantic_term(term)
        for term in re.findall(r"[a-z][a-z0-9]{1,}", value.lower())
    )


def _matches_qualified_identity(identity: str, identifier: str) -> bool:
    candidate = identifier.strip("`'\"()[]{}:,")
    return candidate == identity or candidate.endswith(f".{identity}")


def _match_symbol(
    symbol: SymbolRecord,
    signals: IssueSignals,
    unique_symbol_names: frozenset[str],
    path_scoped: bool,
    test_file: bool = False,
    constructor_call_index: int | None = None,
    traceback_frame_index: int | None = None,
    source_line_reference_index: int | None = None,
    source_snippet_index: int | None = None,
) -> SymbolMatch:
    identity = symbol.qualified_name or symbol.name
    local_terms = _terms(symbol.name) | _terms(symbol.docstring or "")
    terms = _terms(identity) | local_terms
    symbol_variants = _identifier_variants(symbol.name)
    symbol_compact_variants = _compact_identifier_variants(symbol.name)
    identity_parts = identity.split(".")
    owner_identity = ".".join(identity_parts[:-1])
    owner_compact_variants = _compact_identifier_variants(owner_identity)
    owner_semantic_terms = _semantic_terms(_terms(owner_identity))
    owner_title_overlap = owner_semantic_terms & set(signals.raw_title_terms)
    method_semantic_terms = (
        _raw_semantic_terms(symbol.name)
        - GENERIC_QUALIFIED_TITLE_METHOD_TERMS
        - owner_semantic_terms
    )
    method_title_overlap = method_semantic_terms & set(signals.raw_title_terms)
    owner_method_phrase_match = any(
        owner_term in owner_semantic_terms
        and method_term in method_title_overlap
        for owner_term, method_term in zip(
            signals.raw_title_term_sequence,
            signals.raw_title_term_sequence[1:],
            strict=False,
        )
    )
    qualified_title_semantic_match = (
        bool(owner_identity)
        and not test_file
        and len(owner_semantic_terms) >= 2
        and bool(owner_title_overlap)
        and bool(method_title_overlap)
        and owner_method_phrase_match
    )
    source_scoped_identifiers = (
        signals.explicit_identifiers | signals.primary_identifiers
    )
    local_identifiers = {
        identifier
        for identifier in signals.identifiers | signals.explicit_identifiers
        if "." not in identifier
    }
    local_identifier_match = any(
        symbol_variants & _identifier_variants(identifier)
        for identifier in local_identifiers
    )
    exact_local_identifier_match = symbol.name in local_identifiers
    qualified_identifier_match = identity != symbol.name and any(
        _matches_qualified_identity(identity, identifier)
        for identifier in source_scoped_identifiers
    )
    bare_explicit_match = any(
        "." not in identifier and identifier == symbol.name
        for identifier in signals.explicit_identifiers
    )
    exact_owner_match = bool(owner_identity) and any(
        _matches_qualified_identity(owner_identity, identifier)
        or _matches_qualified_identity(
            owner_identity.rsplit(".", maxsplit=1)[-1],
            identifier,
        )
        for identifier in source_scoped_identifiers
    )
    owner_name = owner_identity.rsplit(".", maxsplit=1)[-1]
    class_owner_match = bool(owner_identity) and any(
        "." not in identifier
        and any(character.isupper() for character in identifier[1:])
        and (
            identifier == owner_name
            or (
                identifier.endswith("s")
                and identifier[:-1] == owner_name
            )
        )
        for identifier in signals.identifiers
    )
    scoped_bare_explicit_match = bare_explicit_match and (
        symbol.name in unique_symbol_names
        or path_scoped
        or exact_owner_match
        or class_owner_match
    )
    contextual_symbol_scope = path_scoped or exact_owner_match or class_owner_match
    scoped_identifier_match = exact_local_identifier_match and (
        contextual_symbol_scope
        or (
            len(symbol.name.strip("_")) >= DIRECT_LOCAL_IDENTIFIER_MIN_LENGTH
            and symbol.name in unique_symbol_names
        )
    )
    unscoped_dotted_identifier_match = (
        not qualified_identifier_match
        and not local_identifier_match
        and any(
            "." in identifier
            and (
                identifier.rsplit(".", maxsplit=1)[-1].casefold()
                == symbol.name.casefold()
            )
            for identifier in signals.identifiers | signals.explicit_identifiers
        )
    )
    overlap = signals.terms & terms
    local_overlap = signals.terms & local_terms
    if unscoped_dotted_identifier_match:
        symbol_name_terms = _terms(symbol.name)
        overlap -= symbol_name_terms
        local_overlap -= symbol_name_terms
    return SymbolMatch(
        symbol=symbol,
        overlap=frozenset(overlap),
        local_overlap=frozenset(local_overlap),
        primary_identifier_match=any(
            symbol_compact_variants & _compact_identifier_variants(identifier)
            for identifier in signals.primary_identifiers
        ),
        exact_identifier_match=local_identifier_match,
        identifier_mention_count=sum(
            mention == symbol.name for mention in signals.identifier_mentions
        ),
        scoped_identifier_match=scoped_identifier_match,
        explicit_identifier_match=scoped_bare_explicit_match,
        unscoped_explicit_identifier_match=(
            bare_explicit_match and not scoped_bare_explicit_match
        ),
        unscoped_dotted_identifier_match=unscoped_dotted_identifier_match,
        qualified_component_match=identity != symbol.name
        and any(
            owner_compact_variants
            & _compact_identifier_variants(identifier)
            for identifier in source_scoped_identifiers
        ),
        qualified_identifier_match=qualified_identifier_match,
        qualified_title_semantic_match=qualified_title_semantic_match,
        qualified_title_semantic_strength=(
            len(method_title_overlap) / len(method_semantic_terms)
            if qualified_title_semantic_match and method_semantic_terms
            else 0
        ),
        constructor_call_index=constructor_call_index,
        traceback_frame_index=traceback_frame_index,
        source_line_reference_index=source_line_reference_index,
        source_snippet_index=source_snippet_index,
        semantic_terms=frozenset(_semantic_terms(local_terms)),
    )


def _select_symbol(
    matches: list[SymbolMatch],
    signals: IssueSignals,
    related_callers: dict[str, tuple[str, ...]],
    *,
    use_traceback_frames: bool = True,
    use_source_line_references: bool = True,
    use_source_snippets: bool = True,
    use_constructor_calls: bool = True,
    use_qualified_title_semantics: bool = True,
) -> SymbolMatch | None:
    if not matches:
        return None

    qualified_title_matches = (
        [
            match
            for match in matches
            if match.symbol.kind == "function"
            and match.qualified_title_semantic_match
        ]
        if use_qualified_title_semantics
        else []
    )
    if qualified_title_matches:
        strongest_title_match = max(
            match.qualified_title_semantic_strength
            for match in qualified_title_matches
        )
        strongest_title_matches = [
            match
            for match in qualified_title_matches
            if match.qualified_title_semantic_strength
            == strongest_title_match
        ]
    else:
        strongest_title_matches = []
    if len(strongest_title_matches) != 1:
        strongest_title_matches = []
    strongest_title_identities = {
        _symbol_identity(match.symbol) for match in strongest_title_matches
    }
    if qualified_title_matches or not use_qualified_title_semantics:
        matches = [
            replace(
                match,
                qualified_title_semantic_match=False,
                qualified_title_semantic_strength=0,
            )
            if match.qualified_title_semantic_match
            and (
                not use_qualified_title_semantics
                or _symbol_identity(match.symbol)
                not in strongest_title_identities
            )
            else match
            for match in matches
        ]

    functions = [match for match in matches if match.symbol.kind == "function"]
    directly_selectable = [
        match for match in matches if match.symbol.kind in DIRECT_SYMBOL_KINDS
    ]

    directly_referenced = [
        match
        for match in directly_selectable
        if match.scoped_identifier_match
        or match.qualified_identifier_match
        or (
            use_traceback_frames
            and match.traceback_frame_index is not None
        )
        or (
            use_source_line_references
            and match.source_line_reference_index is not None
        )
        or (use_source_snippets and match.source_snippet_index is not None)
        or (
            use_constructor_calls
            and match.constructor_call_index is not None
        )
    ]
    if directly_referenced:
        return max(
            directly_referenced,
            key=lambda match: (
                use_traceback_frames
                and match.traceback_frame_index is not None,
                (
                    match.traceback_frame_index or -1
                    if use_traceback_frames
                    else -1
                ),
                use_source_line_references
                and match.source_line_reference_index is not None,
                (
                    -(match.source_line_reference_index or 1_000_000)
                    if use_source_line_references
                    else -1_000_000
                ),
                use_source_snippets and match.source_snippet_index is not None,
                (
                    -(match.source_snippet_index or 1_000_000)
                    if use_source_snippets
                    else -1_000_000
                ),
                match.qualified_identifier_match,
                use_constructor_calls
                and match.constructor_call_index is not None,
                (
                    -(match.constructor_call_index or 1_000_000)
                    if use_constructor_calls
                    else -1_000_000
                ),
                match.identifier_mention_count,
                match.explicit_identifier_match,
                match.scoped_identifier_match,
                match.primary_identifier_match,
                len(match.local_overlap),
                match.qualified_component_match,
                -match.symbol.line,
            ),
        )

    primary_terms = _semantic_terms(signals.primary_terms)
    component_terms = _semantic_terms(
        {
            term
            for identifier in signals.primary_identifiers
            for term in _terms(identifier)
        }
    )
    specific_primary_terms = primary_terms - component_terms
    if specific_primary_terms:
        primary_terms = specific_primary_terms
    term_frequency = Counter(
        term
        for match in functions
        for term in match.semantic_terms
    )
    matches_by_identity = {
        _symbol_identity(match.symbol): match
        for match in functions
    }
    relation_scores = {
        called_identity: sum(
            min(len(matches_by_identity[caller].local_overlap), 4)
            for caller in callers
        )
        for called_identity, callers in related_callers.items()
    }

    def semantic_score(
        match: SymbolMatch,
    ) -> tuple[int, float, float, float, bool, bool, bool, int, int]:
        overlap = primary_terms & match.semantic_terms
        if match.unscoped_dotted_identifier_match:
            overlap -= _semantic_terms(_terms(match.symbol.name))
        rarity = [1 / term_frequency[term] for term in overlap]
        return (
            relation_scores.get(_symbol_identity(match.symbol), 0),
            match.qualified_title_semantic_strength,
            max(rarity, default=0),
            sum(rarity),
            match.unscoped_explicit_identifier_match,
            not match.symbol.name.startswith("_"),
            match.qualified_component_match,
            -match.symbol.line,
            len(match.local_overlap),
        )

    if functions and max(semantic_score(match)[:4] for match in functions) > (
        0,
        0.0,
        0,
        0,
    ):
        return max(functions, key=semantic_score)

    fallback = max(
        matches,
        key=lambda match: (
            match.primary_identifier_match,
            match.qualified_identifier_match,
            match.exact_identifier_match
            and not (
                match.unscoped_explicit_identifier_match
                or match.unscoped_dotted_identifier_match
            ),
            (
                len(match.local_overlap)
                if not (
                    match.unscoped_explicit_identifier_match
                    or match.unscoped_dotted_identifier_match
                )
                else 0
            ),
            match.qualified_component_match,
            -match.symbol.line,
        ),
    )
    if not (
        fallback.primary_identifier_match
        or (
            fallback.exact_identifier_match
            and not (
                fallback.unscoped_explicit_identifier_match
                or fallback.unscoped_dotted_identifier_match
            )
        )
        or (
            fallback.overlap
            and not (
                fallback.unscoped_explicit_identifier_match
                or fallback.unscoped_dotted_identifier_match
            )
        )
    ):
        return None
    return fallback


def _symbol_identity(symbol: SymbolRecord) -> str:
    return symbol.qualified_name or symbol.name


def _related_symbol_callers(
    matches: list[SymbolMatch],
    resolved_calls: list[ResolvedCall],
    file_path: str,
    receiver_calls: list[ResolvedCall] | None = None,
) -> dict[str, tuple[str, ...]]:
    functions_by_identity = {
        _symbol_identity(match.symbol): match
        for match in matches
        if match.symbol.kind == "function"
    }
    receiver_call_keys = {
        (
            call.caller,
            call.local_name,
            call.target_file,
            call.target_symbol,
        )
        for call in receiver_calls or []
    }

    callers_by_target: dict[str, set[str]] = {}
    for call in resolved_calls:
        if (
            call.caller,
            call.local_name,
            call.target_file,
            call.target_symbol,
        ) in receiver_call_keys:
            continue
        if call.caller is None or call.target_file != file_path:
            continue
        caller = functions_by_identity.get(call.caller)
        target = functions_by_identity.get(call.target_symbol)
        if caller is None or target is None:
            continue
        if len(caller.local_overlap) < 2:
            continue
        caller_identity = _symbol_identity(caller.symbol)
        target_identity = _symbol_identity(target.symbol)
        if target_identity != caller_identity:
            callers_by_target.setdefault(target_identity, set()).add(
                caller_identity
            )
    return {
        called_identity: tuple(sorted(callers))
        for called_identity, callers in callers_by_target.items()
        if len(callers) >= 2
    }


def _shared_qualified_call_match(
    matches: list[SymbolMatch],
    qualified_calls: list[QualifiedExternalCall],
    relations: list[tuple[float, str]],
) -> SymbolMatch | None:
    related_callers = {
        call.caller
        for call in qualified_calls
        if call.caller is not None
        and any(
            evidence.startswith(
                f"{SHARED_QUALIFIED_CALL_PREFIX}{call.target} "
            )
            for _, evidence in relations
        )
    }
    if len(related_callers) != 1:
        return None
    caller = next(iter(related_callers))
    return next(
        (
            match
            for match in matches
            if _symbol_identity(match.symbol) == caller
        ),
        None,
    )


def _symbol_evidence(
    match: SymbolMatch | None,
    related_callers: dict[str, tuple[str, ...]],
) -> list[str]:
    if match is None:
        return []

    symbol = match.symbol
    label = symbol.qualified_name or symbol.name
    evidence: list[str] = []
    if match.overlap:
        evidence.append(
            f"Symbol {label} matches: "
            + ", ".join(sorted(match.overlap))
        )
    if match.qualified_identifier_match or (
        match.exact_identifier_match
        and not match.unscoped_explicit_identifier_match
    ):
        evidence.append(f"Issue references symbol {label}")
    if match.constructor_call_index is not None:
        evidence.append(f"Issue title and code call constructor {label}")
    if match.qualified_title_semantic_match:
        evidence.append(f"Issue title matches owner and method {label}")
    if match.traceback_frame_index is not None:
        evidence.append(f"Traceback frame points to symbol {label}")
    if match.source_line_reference_index is not None:
        evidence.append(f"Issue source line points to symbol {label}")
    if match.source_snippet_index is not None:
        evidence.append(f"Issue source snippet matches symbol {label}")
    if match.primary_identifier_match:
        evidence.append(f"Issue title strongly matches symbol {label}")
    if match.qualified_component_match:
        evidence.append(
            f"Issue references owning symbol {label.rsplit('.', 1)[0]}"
        )
    callers = related_callers.get(_symbol_identity(symbol), ())
    if callers:
        evidence.append(
            "Issue-matching symbols call "
            f"{symbol.name}: {', '.join(callers)}"
        )
    return evidence


def _alternate_symbol_locations(
    matches: list[SymbolMatch],
    selected_match: SymbolMatch | None,
    signals: IssueSignals,
    related_callers: dict[str, tuple[str, ...]],
    limit: int = 2,
) -> list[CandidateSymbolLocation]:
    if selected_match is None:
        return []
    selected_identity = _symbol_identity(selected_match.symbol)
    remaining = [
        match
        for match in matches
        if _symbol_identity(match.symbol) != selected_identity
    ]
    alternatives: list[CandidateSymbolLocation] = []
    while remaining and len(alternatives) < limit:
        remaining_identities = {
            _symbol_identity(candidate.symbol) for candidate in remaining
        }
        remaining_callers = {
            target: tuple(
                caller for caller in callers if caller in remaining_identities
            )
            for target, callers in related_callers.items()
            if target in remaining_identities
        }
        match = _select_symbol(remaining, signals, remaining_callers)
        if match is None:
            break
        identity = _symbol_identity(match.symbol)
        remaining = [
            candidate
            for candidate in remaining
            if _symbol_identity(candidate.symbol) != identity
        ]
        directly_reportable = (
            match.scoped_identifier_match
            or match.explicit_identifier_match
            or match.qualified_identifier_match
            or match.traceback_frame_index is not None
            or match.source_line_reference_index is not None
            or match.source_snippet_index is not None
            or match.constructor_call_index is not None
        )
        if not directly_reportable:
            continue
        symbol = match.symbol
        alternatives.append(
            CandidateSymbolLocation(
                symbol=symbol.name,
                qualified_symbol=(
                    symbol.qualified_name
                    if symbol.qualified_name != symbol.name
                    else None
                ),
                lines=f"{symbol.line}-{symbol.end_line or symbol.line}",
                evidence=_symbol_evidence(match, related_callers),
            )
        )
    return alternatives


def _path_is_referenced(file_path: str, references: frozenset[str]) -> bool:
    normalized = _normalize_path_reference(file_path)
    return any(
        _normalize_path_reference(reference).endswith(normalized)
        or normalized.endswith(_normalize_path_reference(reference))
        for reference in references
    )


def _normalize_path_reference(value: str) -> str:
    return value.lower().replace("\\", "/").rstrip("/").lstrip("./")


def _symbol_scoped_paths(
    file_paths: list[str],
    references: frozenset[str],
) -> frozenset[str]:
    normalized_paths = {
        path: _normalize_path_reference(path)
        for path in file_paths
    }
    scoped_paths: set[str] = set()
    for reference in references:
        normalized_reference = _normalize_path_reference(reference)
        relative_matches = {
            path
            for path, normalized_path in normalized_paths.items()
            if normalized_reference == normalized_path
        }
        if len(relative_matches) == 1:
            scoped_paths.update(relative_matches)
            continue
        absolute_matches = [
            path
            for path, normalized_path in normalized_paths.items()
            if normalized_reference.endswith(f"/{normalized_path}")
        ]
        if absolute_matches:
            longest_length = max(
                len(normalized_paths[path])
                for path in absolute_matches
            )
            longest_matches = {
                path
                for path in absolute_matches
                if len(normalized_paths[path]) == longest_length
            }
            if len(longest_matches) == 1:
                scoped_paths.update(longest_matches)
            continue
        suffix_matches = {
            path
            for path, normalized_path in normalized_paths.items()
            if normalized_path.endswith(f"/{normalized_reference}")
        }
        if len(suffix_matches) == 1:
            scoped_paths.update(suffix_matches)
    return frozenset(scoped_paths)


def _scoped_traceback_frames(
    file_paths: list[str],
    frames: tuple[TracebackFrame, ...],
) -> dict[str, tuple[tuple[int, str], ...]]:
    python_path_parts = [
        Path(path).parts
        for path in file_paths
        if Path(path).suffix.lower() == ".py"
    ]
    source_roots = {
        root
        for root in {"src", "lib"}
        if any(
            len(parts) > 1 and parts[0] == root
            for parts in python_path_parts
        )
        and (root, "__init__.py") not in python_path_parts
    }
    frames_by_path: dict[str, list[tuple[int, str]]] = {}
    for index, frame in enumerate(frames, start=1):
        scoped_paths = _symbol_scoped_paths(
            file_paths,
            frozenset({frame.path}),
        )
        if not scoped_paths and source_roots:
            normalized_frame_path = _normalize_path_reference(frame.path)
            layout_matches = {
                path
                for path in file_paths
                if Path(path).suffix.lower() == ".py"
                and Path(path).parts[0] in source_roots
                and normalized_frame_path.endswith(
                    "/"
                    + "/".join(Path(path).parts[1:]).lower()
                )
            }
            if len(layout_matches) == 1:
                scoped_paths = frozenset(layout_matches)
        if len(scoped_paths) != 1:
            continue
        path = next(iter(scoped_paths))
        frames_by_path.setdefault(path, []).append((index, frame.symbol))
    return {
        path: tuple(positioned_symbols)
        for path, positioned_symbols in frames_by_path.items()
    }


def _traceback_symbol_positions(
    symbols: list[SymbolRecord],
    frames: tuple[tuple[int, str], ...],
) -> dict[str, int]:
    positions: dict[str, int] = {}
    for index, frame_symbol in frames:
        matching_identities = {
            symbol.qualified_name or symbol.name
            for symbol in symbols
            if (
                frame_symbol == symbol.name
                if "." not in frame_symbol
                else _matches_qualified_identity(
                    symbol.qualified_name or symbol.name,
                    frame_symbol,
                )
            )
        }
        if len(matching_identities) != 1:
            continue
        identity = next(iter(matching_identities))
        positions[identity] = index
    return positions


def _title_constructor_positions_by_path(
    files: list[FileRecord],
    signals: IssueSignals,
) -> dict[str, dict[str, int]]:
    if not signals.called_identifiers or not signals.title_identifiers:
        return {}

    constructors: set[tuple[str, str, str]] = set()
    constructor_terms: dict[tuple[str, str, str], set[str]] = {}
    method_names_by_owner: dict[tuple[str, str], set[str]] = {}
    for file in files:
        for symbol in file.symbols:
            identity = symbol.qualified_name or symbol.name
            if (
                symbol.kind != "function"
                or "." not in identity
            ):
                continue
            owner = identity.rsplit(".", maxsplit=1)[0]
            method_names_by_owner.setdefault(
                (file.path, owner),
                set(),
            ).add(symbol.name)
            if symbol.name == "__init__":
                target = (file.path, identity, owner)
                constructors.add(target)
                constructor_terms.setdefault(target, set()).update(
                    _semantic_terms(_terms(symbol.docstring or ""))
                )

    primary_method_names = {
        identifier
        for identifier in signals.title_identifiers
        if "." not in identifier
    }

    constructors = {
        (path, identity, owner)
        for path, identity, owner in constructors
        if not (
            primary_method_names
            & (method_names_by_owner[(path, owner)] - {"__init__"})
        )
        if (
            any(
                term.startswith(("construct", "initializ", "instantiat"))
                for term in (
                    _semantic_terms(signals.title_terms)
                    - _semantic_terms(_terms(owner))
                )
            )
            or any(
                len(term) >= 5
                for term in (
                    _semantic_terms(signals.title_terms)
                    - _semantic_terms(_terms(owner))
                )
                & constructor_terms[(path, identity, owner)]
            )
        )
    }

    def matches_owner(owner: str, reference: str) -> bool:
        candidate = reference.strip("`'\"()[]{}:,")
        owner_name = owner.rsplit(".", maxsplit=1)[-1]
        return (
            _matches_qualified_identity(owner, candidate)
            or candidate == owner_name
            or candidate.endswith(f".{owner_name}")
        )

    positions_by_path: dict[str, dict[str, int]] = {}
    for index, called_identifier in enumerate(
        signals.called_identifiers,
        start=1,
    ):
        matches = {
            (path, identity)
            for path, identity, owner in constructors
            if matches_owner(owner, called_identifier)
            and any(
                matches_owner(owner, primary_identifier)
                for primary_identifier in signals.title_identifiers
            )
        }
        if len(matches) != 1:
            continue
        path, identity = next(iter(matches))
        positions_by_path.setdefault(path, {}).setdefault(identity, index)
    return positions_by_path


def _source_line_symbol_positions(
    symbols: list[SymbolRecord],
    references: tuple[tuple[int, int], ...],
) -> dict[str, int]:
    positions: dict[str, int] = {}
    for index, line in references:
        containing_symbols = [
            symbol
            for symbol in symbols
            if symbol.end_line is not None
            and symbol.line <= line <= symbol.end_line
        ]
        if not containing_symbols:
            continue
        smallest_span = min(
            (symbol.end_line or symbol.line) - symbol.line
            for symbol in containing_symbols
        )
        matching_identities = {
            symbol.qualified_name or symbol.name
            for symbol in containing_symbols
            if (symbol.end_line or symbol.line) - symbol.line
            == smallest_span
        }
        if len(matching_identities) != 1:
            continue
        positions.setdefault(next(iter(matching_identities)), index)
    return positions


def _symbol_identity_at_source_line(source: str, line: int) -> str | None:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    containing_nodes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.end_lineno is not None
        and node.lineno <= line <= node.end_lineno
    ]
    if not containing_nodes:
        return None
    smallest_span = min(
        (node.end_lineno or node.lineno) - node.lineno
        for node in containing_nodes
    )
    matching_nodes = [
        node
        for node in containing_nodes
        if (node.end_lineno or node.lineno) - node.lineno == smallest_span
    ]
    if len(matching_nodes) != 1:
        return None
    node = matching_nodes[0]
    names = [node.name]
    parent = parents.get(node)
    while parent is not None:
        if isinstance(parent, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append(parent.name)
        parent = parents.get(parent)
    return ".".join(reversed(names))


def _source_at_revision(
    root: Path,
    revision: str,
    relative_path: str,
) -> str | None:
    if not (root / ".git").exists():
        return None
    try:
        completed = subprocess.run(
            ["git", "show", f"{revision}:{relative_path}"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError):
        return None
    if completed.returncode:
        return None
    return completed.stdout


def _source_line_positions_by_path(
    root: Path,
    files: list[FileRecord],
    references: tuple[SourceLineReference, ...],
) -> dict[str, dict[str, int]]:
    file_paths = [file.path for file in files]
    files_by_path = {file.path: file for file in files}
    positions_by_path: dict[str, dict[str, int]] = {}
    for index, reference in enumerate(references, start=1):
        scoped_paths = _symbol_scoped_paths(
            file_paths,
            frozenset({reference.path}),
        )
        if len(scoped_paths) != 1:
            continue
        path = next(iter(scoped_paths))
        file = files_by_path[path]
        if reference.revision is None:
            positions = _source_line_symbol_positions(
                file.symbols,
                ((index, reference.line),),
            )
        else:
            source = _source_at_revision(
                root,
                reference.revision,
                reference.path,
            )
            identity = (
                _symbol_identity_at_source_line(source, reference.line)
                if source is not None
                else None
            )
            current_identities = {
                symbol.qualified_name or symbol.name
                for symbol in file.symbols
            }
            positions = (
                {identity: index}
                if identity is not None and identity in current_identities
                else {}
            )
        path_positions = positions_by_path.setdefault(path, {})
        for identity, position in positions.items():
            path_positions.setdefault(identity, position)
    return positions_by_path


def _source_snippet_ranges(
    root: Path,
    relative_path: str,
    snippets: tuple[tuple[str, ...], ...],
) -> tuple[tuple[int, int, int], ...]:
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return ()
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ()
    if "\x00" in source or len(source) > 1_000_000:
        return ()

    source_lines = _normalize_source_snippet_lines(source.splitlines())
    ranges: list[tuple[int, int, int]] = []
    for index, snippet in enumerate(snippets, start=1):
        normalized_snippet = _normalize_source_snippet_lines(snippet)
        nonempty_lines = [line for line in normalized_snippet if line]
        if (
            len(nonempty_lines) < SOURCE_SNIPPET_MIN_NONEMPTY_LINES
            or sum(len(line) for line in nonempty_lines)
            < SOURCE_SNIPPET_MIN_CHARACTERS
            or len(normalized_snippet) > len(source_lines)
        ):
            continue
        starts = [
            start
            for start in range(len(source_lines) - len(normalized_snippet) + 1)
            if source_lines[start : start + len(normalized_snippet)]
            == normalized_snippet
        ]
        if len(starts) != 1:
            continue
        start = starts[0] + 1
        ranges.append((index, start, start + len(normalized_snippet) - 1))
    return tuple(ranges)


def _normalize_source_snippet_lines(
    lines: tuple[str, ...] | list[str],
) -> tuple[str, ...]:
    raw_lines = tuple(lines)
    comment_columns: dict[int, int] = {}
    tokens = tokenize.generate_tokens(
        io.StringIO("\n".join(raw_lines) + "\n").readline
    )
    try:
        for token in tokens:
            if token.type == tokenize.COMMENT:
                comment_columns.setdefault(token.start[0], token.start[1])
    except (IndentationError, tokenize.TokenError):
        pass
    return tuple(
        (
            line[: comment_columns[index]].strip()
            if index in comment_columns
            else line.strip()
        )
        for index, line in enumerate(raw_lines, start=1)
    )


def _source_snippet_symbol_positions(
    symbols: list[SymbolRecord],
    ranges: tuple[tuple[int, int, int], ...],
) -> dict[str, int]:
    positions: dict[str, int] = {}
    for index, start, end in ranges:
        containing_symbols = [
            symbol
            for symbol in symbols
            if symbol.end_line is not None
            and symbol.line <= start
            and end <= symbol.end_line
        ]
        if not containing_symbols:
            continue
        smallest_span = min(
            (symbol.end_line or symbol.line) - symbol.line
            for symbol in containing_symbols
        )
        matching_symbols = [
            symbol
            for symbol in containing_symbols
            if (symbol.end_line or symbol.line) - symbol.line == smallest_span
        ]
        if len(matching_symbols) != 1:
            continue
        identity = (
            matching_symbols[0].qualified_name or matching_symbols[0].name
        )
        if (
            sum(
                (symbol.qualified_name or symbol.name) == identity
                for symbol in symbols
            )
            != 1
        ):
            continue
        positions.setdefault(identity, index)
    return positions


def _source_snippet_positions_by_path(
    root: Path,
    files: list[FileRecord],
    references: frozenset[str],
    snippets: tuple[tuple[str, ...], ...],
) -> dict[str, dict[str, int]]:
    if not snippets or not references:
        return {}
    referenced_files = [
        file for file in files if _path_is_referenced(file.path, references)
    ]
    ranges_by_snippet: dict[int, list[tuple[FileRecord, int, int]]] = {}
    for file in referenced_files:
        for index, start, end in _source_snippet_ranges(
            root, file.path, snippets
        ):
            ranges_by_snippet.setdefault(index, []).append(
                (file, start, end)
            )

    positions_by_path: dict[str, dict[str, int]] = {}
    for index, matches in ranges_by_snippet.items():
        if len(matches) != 1:
            continue
        file, start, end = matches[0]
        positions = _source_snippet_symbol_positions(
            file.symbols,
            ((index, start, end),),
        )
        if positions:
            positions_by_path[file.path] = positions
    return positions_by_path


def _source_content(
    root: Path,
    relative_path: str,
    signals: IssueSignals,
    language: str,
    *,
    include_structured_identifiers: bool,
) -> tuple[set[str], set[str], set[str], int | None]:
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return set(), set(), set(), None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set(), set(), set(), None
    if "\x00" in text or len(text) > 1_000_000:
        return set(), set(), set(), None

    all_identifiers = signals.identifiers | signals.explicit_identifiers
    if language in ECMASCRIPT_LANGUAGES:
        all_identifiers = {
            identifier for identifier in all_identifiers if identifier.isascii()
        } | set(signals.verbatim_identifiers)
    bare_identifiers = {
        identifier
        for identifier in all_identifiers
        if "." not in identifier
    }
    content_identifiers = bare_identifiers | {
        identifier
        for identifier in all_identifiers
        if "." in identifier
        and identifier.rsplit(".", maxsplit=1)[-1] not in bare_identifiers
    }
    identifier_hits = {
        identifier
        for identifier in content_identifiers
        if _content_matches_identifier(text, identifier, language=language)
    }
    structured_candidates = (
        set(signals.command_line_identifiers)
        if include_structured_identifiers
        else set()
    )
    structured_identifier_hits = {
        identifier
        for identifier in structured_candidates
        if _content_matches_identifier(text, identifier, language=language)
        or _content_structurally_matches_identifier(
            text,
            identifier,
            language=language,
        )
    }
    identifier_hits.update(structured_identifier_hits)
    lowered = text.lower()
    content_overlap = {
        term
        for term in signals.content_terms
        if len(term) >= 4 and term in lowered
    }
    first_line = None
    if identifier_hits:
        first_line = next(
            (
                line_number
                for line_number, line in enumerate(text.splitlines(), start=1)
                if any(
                    _content_matches_identifier(line, identifier, language=language)
                    or (
                        identifier in structured_identifier_hits
                        and _content_structurally_matches_identifier(
                            line,
                            identifier,
                            language=language,
                        )
                    )
                    for identifier in identifier_hits
                )
            ),
            None,
        )
    return (
        identifier_hits,
        structured_identifier_hits,
        content_overlap,
        first_line,
    )


def _content_structurally_matches_identifier(
    text: str,
    identifier: str,
    *,
    language: str | None = None,
) -> bool:
    if language in ECMASCRIPT_LANGUAGES:
        return False
    candidate = identifier.strip("`'\"()[]{}:,").rsplit(".", maxsplit=1)[-1]
    candidate_terms = tuple(_ordered_terms(candidate))
    if len(candidate_terms) < 2:
        return False
    for match in IDENTIFIER_PATTERN.finditer(text):
        source = match.group(0).rsplit(".", maxsplit=1)[-1]
        source_terms = tuple(_ordered_terms(source))
        if len(source_terms) <= len(candidate_terms):
            continue
        width = len(candidate_terms)
        if any(
            source_terms[index : index + width] == candidate_terms
            for index in range(len(source_terms) - width + 1)
        ):
            return True
    return False


def _is_identifier_continuation(
    character: str,
    language: str | None,
) -> bool:
    if not character:
        return False
    if language not in ECMASCRIPT_LANGUAGES:
        return ("_" + character).isidentifier()
    return character not in UNICODE_ID_CONTINUE_EXCLUSIONS and (
        unicodedata.category(character) in UNICODE_ID_CONTINUE_CATEGORIES
        or character in UNICODE_OTHER_ID_START
        or character in UNICODE_OTHER_ID_CONTINUE
        or "\u1369" <= character <= "\u1371"
        or character in ECMASCRIPT_IDENTIFIER_CONTINUATION_EXTRAS
    )


def _has_valid_identifier_boundaries(
    text: str,
    match_start: int,
    match_end: int,
    *,
    reject_dot: bool = False,
    language: str | None = None,
) -> bool:
    before = text[match_start - 1] if match_start > 0 else ""
    after = text[match_end] if match_end < len(text) else ""
    if reject_dot and (before == "." or after == "."):
        return False
    return not (
        _is_identifier_continuation(before, language)
        or _is_identifier_continuation(after, language)
    )


def _content_matches_identifier(
    text: str,
    identifier: str,
    *,
    language: str | None = None,
) -> bool:
    candidate = identifier.strip("`'\"()[]{}:,")
    if not candidate:
        return False
    if language in ECMASCRIPT_LANGUAGES:
        return any(
            _has_valid_identifier_boundaries(
                text,
                match.start(),
                match.end(),
                reject_dot="." in candidate,
                language=language,
            )
            for match in re.finditer(re.escape(candidate), text)
        )
    if "." in candidate:
        return any(
            _has_valid_identifier_boundaries(
                text,
                match.start(),
                match.end(),
                reject_dot=True,
                language=language,
            )
            for match in re.finditer(re.escape(candidate), text)
        )
    lowered = text.lower()
    return any(
        _has_valid_identifier_boundaries(
            lowered,
            match.start(),
            match.end(),
            language=language,
        )
        for variant in _identifier_variants(candidate)
        for match in re.finditer(re.escape(variant), lowered)
    )


def _graph_seed_paths(
    base_scores: dict[str, float],
    auxiliary_files: dict[str, bool],
) -> list[str]:
    return sorted(
        (
            path
            for path, score in base_scores.items()
            if score >= GRAPH_SEED_MIN_SCORE
        ),
        key=lambda path: (
            -base_scores[path],
            auxiliary_files[path],
            path,
        ),
    )[:GRAPH_SEED_LIMIT]


def _normalized_filename_stem(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _normalized_test_stem(path: str) -> str | None:
    stem = Path(path).stem.casefold()
    for prefix in ("test_", "test-"):
        if stem.startswith(prefix):
            stem = stem[len(prefix) :]
            break
    for suffix in ("_test", "-test"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    normalized = _normalized_filename_stem(stem)
    return normalized if len(normalized) >= 4 else None


def _semantic_test_source_matches(
    repository_map: RepositoryMap,
    seed_paths: list[str],
) -> dict[str, str]:
    files_by_path = {file.path: file for file in repository_map.files}
    production_by_stem: dict[tuple[str, str], set[str]] = {}
    for file in repository_map.files:
        if (
            file.test_file
            or bool(set(Path(file.path).parts) & AUXILIARY_PATH_PARTS)
        ):
            continue
        raw_stem = Path(file.path).stem
        if raw_stem == "__init__":
            continue
        stem = _normalized_filename_stem(raw_stem)
        if len(stem) < 4:
            continue
        production_by_stem.setdefault((file.language, stem), set()).add(
            file.path
        )

    matches: dict[str, str] = {}
    for path in seed_paths:
        test_file = files_by_path.get(path)
        if test_file is None or not test_file.test_file:
            continue
        stem = _normalized_test_stem(path)
        if stem is None:
            continue
        targets = production_by_stem.get((test_file.language, stem), set())
        if len(targets) == 1:
            matches[path] = next(iter(targets))
    return matches


def _history_relations(
    root: Path,
    seed_paths: list[str],
    eligible_paths: set[str],
    auxiliary_files: dict[str, bool],
) -> dict[str, list[tuple[float, str]]]:
    if not seed_paths or not (root / ".git").exists():
        return {}
    try:
        completed = subprocess.run(
            [
                "git",
                "log",
                "-n",
                str(HISTORY_ANCESTOR_LIMIT),
                "--full-diff",
                "--format=%x1e%H",
                "--name-only",
                "HEAD",
            ],
            cwd=root,
            check=False,
            capture_output=True,
            env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if completed.returncode:
        return {}

    counts: Counter[str] = Counter()
    latest_commit: dict[str, str] = {}
    seed_set = set(seed_paths)
    seed_commit_count = 0
    for record in completed.stdout.split("\x1e"):
        lines = [line for line in record.splitlines() if line]
        if len(lines) < 2:
            continue
        commit, *changed_files = lines
        changed = set(changed_files)
        if not changed.intersection(seed_set):
            continue
        seed_commit_count += 1
        if seed_commit_count > HISTORY_COMMIT_LIMIT:
            break
        if len(changed) > HISTORY_FILE_LIMIT:
            continue
        for path in changed.intersection(eligible_paths) - seed_set:
            if auxiliary_files[path]:
                continue
            counts[path] += 1
            latest_commit.setdefault(path, commit)

    return {
        path: [
            (
                min(12.0, 5.0 + count),
                f"Changed with lexical seed files in {count} prior commits "
                f"(latest {latest_commit[path][:7]})",
            )
        ]
        for path, count in counts.items()
    }


def _blame_relations(
    root: Path,
    seed_paths: list[str],
    candidate_lines: dict[str, str | None],
    eligible_paths: set[str],
    auxiliary_files: dict[str, bool],
) -> dict[str, list[tuple[float, str]]]:
    relations: dict[str, list[tuple[float, str]]] = {}
    for seed_path in seed_paths[:BLAME_SEED_LIMIT]:
        location = candidate_lines[seed_path]
        if location is None:
            continue
        start_value, _, end_value = location.partition("-")
        if not start_value.isdigit():
            continue
        start_line = int(start_value)
        end_line = int(end_value) if end_value.isdigit() else start_line
        end_line = min(end_line, start_line + 4)
        try:
            blame = subprocess.run(
                [
                    "git",
                    "blame",
                    "--porcelain",
                    "-L",
                    f"{start_line},{end_line}",
                    "HEAD",
                    "--",
                    seed_path,
                ],
                cwd=root,
                check=False,
                capture_output=True,
                env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if blame.returncode:
            continue
        commits = {
            line.partition(" ")[0]
            for line in blame.stdout.splitlines()
            if re.match(r"^[0-9a-f]{40} \d+ \d+", line)
            and set(line.partition(" ")[0]) != {"0"}
        }
        for commit in commits:
            try:
                changed = subprocess.run(
                    [
                        "git",
                        "show",
                        "--format=",
                        "--name-only",
                        "--diff-filter=AM",
                        commit,
                    ],
                    cwd=root,
                    check=False,
                    capture_output=True,
                    env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
                    text=True,
                    timeout=2,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if changed.returncode:
                continue
            changed_paths = {
                path for path in changed.stdout.splitlines() if path
            }
            if len(changed_paths) > BLAME_FILE_LIMIT:
                continue
            for path in changed_paths.intersection(eligible_paths) - {seed_path}:
                if auxiliary_files[path]:
                    continue
                relations.setdefault(path, []).append(
                    (
                        10.0,
                        f"Blame-selected seed line changed with this file in "
                        f"prior commit {commit[:7]}",
                    )
                )
    return relations


def _rerank_relation_bonus(bonus: float, evidence: str) -> float:
    if evidence.startswith(
        (
            FUNCTION_LOCAL_RELATION_PREFIX,
            FUNCTION_LOCAL_TWO_HOP_PREFIX,
            SHARED_QUALIFIED_CALL_PREFIX,
            REVERSE_IMPORT_RELATION_PREFIX,
            REEXPORTED_IMPORT_RELATION_PREFIX,
            SEMANTIC_TEST_SOURCE_RELATION_PREFIX,
        )
    ):
        return 0
    if evidence.startswith("Changed with lexical seed files"):
        return 0
    if "references imported symbols" in evidence:
        return 0
    if evidence.startswith("Related source calls imported symbols"):
        return min(bonus, 8.0)
    if evidence.startswith("Two-hop source relation calls imported symbols"):
        return min(bonus, 5.0)
    if evidence.startswith(GRAPH_STRONG_EXPANSION_PREFIX):
        return min(bonus, 8.0)
    if evidence.startswith("Related source calls ") and ", defined here:" in evidence:
        return min(bonus, 2.0)
    return bonus


def _expansion_relation_bonus(bonus: float, evidence: str) -> float:
    if evidence.startswith(FUNCTION_LOCAL_RELATION_PREFIX):
        return 0
    return bonus


def _graph_relations(
    repository_map: RepositoryMap,
    base_scores: dict[str, float],
    auxiliary_files: dict[str, bool],
    signals: IssueSignals,
    semantic_test_sources: dict[str, str],
) -> dict[str, list[tuple[float, str]]]:
    files_by_path = {file.path: file for file in repository_map.files}
    seed_paths = _graph_seed_paths(base_scores, auxiliary_files)
    qualified_call_files: dict[str, set[str]] = {}
    qualified_caller_files: dict[str, set[str]] = {}
    for file in repository_map.files:
        if file.test_file or set(Path(file.path).parts) & AUXILIARY_PATH_PARTS:
            continue
        for symbol in file.symbols:
            if symbol.kind == "function":
                qualified_caller_files.setdefault(
                    _symbol_identity(symbol), set()
                ).add(file.path)
        for call in file.qualified_external_calls:
            qualified_call_files.setdefault(call.target, set()).add(file.path)

    relations: dict[str, list[tuple[float, str]]] = {}

    def matches_primary_issue(value: str) -> bool:
        related_terms = _terms(value)
        return bool(related_terms & signals.primary_terms) or any(
            len(related) >= 5
            and len(primary) >= 5
            and (related in primary or primary in related)
            for related in related_terms
            for primary in signals.primary_terms
        )

    def matches_specific_primary_issue(value: str) -> bool:
        return any(
            len(related) >= 5
            and len(primary) >= 5
            and (related in primary or primary in related)
            for related in _terms(value)
            for primary in signals.primary_terms
        )

    issue_identifiers = (
        signals.explicit_identifiers | signals.primary_identifiers
    )

    def issue_references_caller(identity: str) -> bool:
        if len(qualified_caller_files.get(identity, set())) != 1:
            return False
        if "." not in identity:
            return (
                len(identity.replace("_", ""))
                >= DIRECT_LOCAL_IDENTIFIER_MIN_LENGTH
                and identity in issue_identifiers
            )
        return any(
            _matches_qualified_identity(identity, identifier)
            for identifier in issue_identifiers
        )

    def add_relation(target: str, bonus: float, evidence: str) -> None:
        if target not in base_scores:
            return
        relations.setdefault(target, []).append((bonus, evidence))

    def concrete_target(path: str) -> bool:
        target_parts = {
            Path(part).stem.lstrip("_").lower()
            for part in Path(path).parts
        }
        return (
            not auxiliary_files.get(path, True)
            and not target_parts.intersection(
                {
                    "abc",
                    "interface",
                    "interfaces",
                    "protocol",
                    "protocols",
                }
            )
        )

    production_files = [
        file
        for file in repository_map.files
        if not file.test_file
        and Path(file.path).parts[0] not in {"t", "test", "tests"}
        and not set(Path(file.path).parts) & AUXILIARY_PATH_PARTS
    ]
    repository_paths = {file.path for file in repository_map.files}
    referenced_source_paths = _symbol_scoped_paths(
        list(repository_paths),
        signals.paths,
    )

    def package_subsystem_terms(path: str) -> set[str]:
        parts = list(Path(path).parts)
        package_root_index = next(
            (
                index
                for index in range(len(parts) - 1)
                if str(Path(*parts[: index + 1], "__init__.py"))
                in repository_paths
            ),
            None,
        )
        if package_root_index is not None:
            parts = parts[package_root_index + 1 : -1]
        else:
            parts = parts[:-1]
            if parts and parts[0] in {"src", "lib"}:
                parts.pop(0)
            if parts:
                parts.pop(0)
        return {
            term
            for term in _terms("/".join(parts))
            if len(term) >= 3 and term not in GENERIC_SUBSYSTEM_TERMS
        }
    importers_by_path: dict[str, set[str]] = {}
    for importer in production_files:
        for imported_path in importer.local_imports:
            if imported_path != importer.path:
                importers_by_path.setdefault(imported_path, set()).add(
                    importer.path
                )

    semantic_title_terms = _semantic_terms(signals.title_terms)
    title_scope_terms_by_path: dict[str, set[str]] = {}

    def title_scope_terms(path: str) -> set[str]:
        if path in title_scope_terms_by_path:
            return title_scope_terms_by_path[path]
        parts = list(Path(path).parts)
        package_root_index = next(
            (
                index
                for index in range(len(parts) - 1)
                if str(Path(*parts[: index + 1], "__init__.py"))
                in repository_paths
            ),
            None,
        )
        if package_root_index is not None:
            parts = parts[package_root_index + 1 :]
        else:
            if parts and parts[0] in {"src", "lib"}:
                parts.pop(0)
            if len(parts) > 1:
                parts.pop(0)
        scoped_path = "/".join([*parts[:-1], Path(parts[-1]).stem])
        scoped_terms = {
            term
            for term in (
                _semantic_terms(_terms(scoped_path)) & semantic_title_terms
            )
            if len(term) >= 5
        }
        title_scope_terms_by_path[path] = scoped_terms
        return scoped_terms

    for source in production_files:
        if source.path not in base_scores:
            continue
        source_scope = title_scope_terms(source.path)
        if not source_scope:
            continue
        symbols_by_term: dict[str, set[str]] = {}
        for symbol in source.symbols:
            if symbol.kind != "function":
                continue
            identity = _symbol_identity(symbol)
            matching_terms = (
                _semantic_terms(_terms(symbol.name)) & semantic_title_terms
            ) - source_scope
            for term in matching_terms:
                if len(term) >= 5:
                    symbols_by_term.setdefault(term, set()).add(identity)
        repeated_symbol_terms = {
            term: identities
            for term, identities in symbols_by_term.items()
            if len(identities) >= 2
        }
        if not repeated_symbol_terms:
            continue
        matching_symbols = sorted(
            set().union(*repeated_symbol_terms.values())
        )
        importers = {
            path
            for path in importers_by_path.get(source.path, set())
            if title_scope_terms(path) & source_scope
        }
        if not 1 <= len(importers) <= 3:
            continue
        bonus = GRAPH_EXPANSION_MIN_BONUS + min(
            2.0,
            0.5 * (len(matching_symbols) - 2),
        )
        displayed_symbols = ", ".join(matching_symbols[:3])
        if len(matching_symbols) > 3:
            displayed_symbols += f" (+{len(matching_symbols) - 3} more)"
        for importer_path in sorted(importers):
            add_relation(
                importer_path,
                bonus,
                REVERSE_IMPORT_RELATION_PREFIX
                + f"{source.path}: {displayed_symbols}",
            )

    for seed_path in seed_paths:
        seed = files_by_path[seed_path]
        seed_is_auxiliary = (
            seed.test_file
            or bool(set(Path(seed.path).parts) & AUXILIARY_PATH_PARTS)
        )
        semantic_source = semantic_test_sources.get(seed_path)
        if semantic_source is not None:
            add_relation(
                semantic_source,
                GRAPH_EXPANSION_MIN_BONUS,
                SEMANTIC_TEST_SOURCE_RELATION_PREFIX + seed_path,
            )
        for call in (
            seed.qualified_external_calls if not seed_is_auxiliary else []
        ):
            if call.caller is None or call.target not in signals.explicit_identifiers:
                continue
            if not issue_references_caller(call.caller):
                continue
            related_files = qualified_call_files.get(call.target, set())
            if not 2 <= len(related_files) <= SHARED_QUALIFIED_CALL_MAX_FILES:
                continue
            for related_path in sorted(related_files - {seed_path}):
                related = files_by_path[related_path]
                related_callers = sorted(
                    {
                        candidate.caller
                        for candidate in related.qualified_external_calls
                        if candidate.target == call.target
                        and candidate.caller is not None
                    }
                )
                if not related_callers:
                    continue
                add_relation(
                    related_path,
                    GRAPH_EXPANSION_MIN_BONUS,
                    f"{SHARED_QUALIFIED_CALL_PREFIX}{call.target} with "
                    f"{call.caller} in {seed_path}: "
                    + ", ".join(related_callers),
                )
        function_local_calls = {
            (
                call.caller,
                call.local_name,
                call.target_file,
                call.target_symbol,
            )
            for call in seed.function_local_import_calls
        }
        calls_by_target: dict[str, set[str]] = {}
        non_function_local_call_targets: set[str] = set()
        for call in seed.resolved_calls:
            if call.target_file != seed_path:
                calls_by_target.setdefault(call.target_file, set()).add(
                    call.target_symbol
                )
                if (
                    call.caller,
                    call.local_name,
                    call.target_file,
                    call.target_symbol,
                ) not in function_local_calls:
                    non_function_local_call_targets.add(call.target_file)

        if seed_path in referenced_source_paths and not seed_is_auxiliary:
            seed_subsystem_terms = package_subsystem_terms(seed_path)
            referenced_seed_symbols = set(seed.references)
            reexport_routes: dict[
                str,
                set[tuple[str, str]],
            ] = {}
            for facade_path, imported_symbols in (
                seed.module_import_symbols.items()
            ):
                if Path(facade_path).name != "__init__.py":
                    continue
                facade = files_by_path.get(facade_path)
                if (
                    facade is None
                    or facade.test_file
                    or bool(
                        set(Path(facade_path).parts) & AUXILIARY_PATH_PARTS
                    )
                ):
                    continue
                facade_definitions = {
                    _symbol_identity(symbol)
                    for symbol in facade.symbols
                }
                for target_path, forwarded_symbols in (
                    facade.module_import_symbols.items()
                ):
                    target = files_by_path.get(target_path)
                    if (
                        target is None
                        or target.test_file
                        or bool(
                            set(Path(target_path).parts)
                            & AUXILIARY_PATH_PARTS
                        )
                        or target_path in {seed_path, facade_path}
                        or not (
                            seed_subsystem_terms
                            & package_subsystem_terms(target_path)
                        )
                    ):
                        continue
                    target_definition_counts = Counter(
                        _symbol_identity(symbol)
                        for symbol in target.symbols
                    )
                    for symbol in (
                        set(imported_symbols)
                        & set(forwarded_symbols)
                        & referenced_seed_symbols
                    ):
                        if (
                            symbol in facade_definitions
                            or target_definition_counts[symbol] != 1
                        ):
                            continue
                        reexport_routes.setdefault(symbol, set()).add(
                            (target_path, facade_path)
                        )

            symbols_by_target: dict[tuple[str, str], set[str]] = {}
            for symbol, routes in reexport_routes.items():
                if len(routes) != 1:
                    continue
                route = next(iter(routes))
                symbols_by_target.setdefault(route, set()).add(symbol)
            for (target_path, facade_path), symbols in sorted(
                symbols_by_target.items()
            ):
                add_relation(
                    target_path,
                    7.0,
                    REEXPORTED_IMPORT_RELATION_PREFIX
                    + f"{', '.join(sorted(symbols))} via {facade_path}",
                )

        for imported_path in seed.local_imports:
            imported = files_by_path.get(imported_path)
            if imported is None or imported.test_file:
                continue
            called_imports = calls_by_target.get(imported_path, set())
            referenced_imports = set(
                seed.resolved_import_references.get(imported_path, [])
            )
            if seed.test_file:
                add_relation(
                    imported_path,
                    3.5,
                    f"Matching test imports this source file: {seed_path}",
                )
            elif called_imports:
                bonus = (
                    9.0
                    if any(matches_primary_issue(symbol) for symbol in called_imports)
                    else 8.0
                )
                evidence_prefix = (
                    FUNCTION_LOCAL_RELATION_PREFIX
                    if imported_path not in non_function_local_call_targets
                    else "Related source calls imported symbols defined here: "
                )
                relation_bonus = (
                    min(bonus, GRAPH_EXPANSION_MIN_BONUS - 0.5)
                    if evidence_prefix == FUNCTION_LOCAL_RELATION_PREFIX
                    else bonus
                )
                add_relation(
                    imported_path,
                    relation_bonus,
                    evidence_prefix + ", ".join(sorted(called_imports)),
                )
            elif referenced_imports:
                bonus = (
                    8.0
                    if any(
                        matches_primary_issue(symbol)
                        for symbol in referenced_imports
                    )
                    else 6.0
                )
                add_relation(
                    imported_path,
                    bonus,
                    "Related source references imported symbols defined here: "
                    + ", ".join(sorted(referenced_imports)),
                )
            else:
                add_relation(
                    imported_path,
                    0.75,
                    f"Related source imports this file: {seed_path}",
                )

        if seed.test_file:
            continue

        external_calls = [
            call
            for call in seed.resolved_calls
            if call.target_file != seed_path
        ]
        for first_call in external_calls:
            first_hop = files_by_path.get(first_call.target_file)
            if first_hop is None or first_hop.test_file:
                continue
            first_hop_symbols = {
                symbol.qualified_name or symbol.name: symbol
                for symbol in first_hop.symbols
            }
            first_call_is_function_local = (
                first_call.caller,
                first_call.local_name,
                first_call.target_file,
                first_call.target_symbol,
            ) in function_local_calls
            target = first_hop_symbols.get(first_call.target_symbol)
            target_callers = {first_call.target_symbol}
            if (
                first_call_is_function_local
                and target is not None
                and target.kind == "class"
            ):
                target_callers.add(f"{first_call.target_symbol}.__init__")
            first_hop_function_local_calls = {
                (
                    call.caller,
                    call.local_name,
                    call.target_file,
                    call.target_symbol,
                )
                for call in first_hop.function_local_import_calls
            }
            second_hop_calls = [
                call
                for call in first_hop.resolved_calls
                if call.caller in target_callers
                and call.target_file not in {seed_path, first_hop.path}
                and (
                    call.caller,
                    call.local_name,
                    call.target_file,
                    call.target_symbol,
                )
                not in first_hop_function_local_calls
            ]
            for second_call in second_hop_calls:
                if len(second_call.target_symbol) < GRAPH_SECOND_HOP_CALL_MIN_LENGTH:
                    continue
                second_hop = files_by_path.get(second_call.target_file)
                if second_hop is None or second_hop.test_file:
                    continue
                bonus = (
                    9.0
                    if matches_primary_issue(second_call.target_symbol)
                    else 5.0
                )
                add_relation(
                    second_call.target_file,
                    bonus,
                    (
                        FUNCTION_LOCAL_TWO_HOP_PREFIX
                        if first_call_is_function_local
                        else (
                            "Two-hop source relation calls imported symbols "
                            "defined here: "
                        )
                    )
                    + second_call.target_symbol,
                )
                if (
                    not first_call_is_function_local
                    and matches_specific_primary_issue(first_call.target_symbol)
                    and concrete_target(first_call.target_file)
                    and concrete_target(second_call.target_file)
                ):
                    add_relation(
                        second_call.target_file,
                        (
                            9.0
                            if matches_primary_issue(second_call.target_symbol)
                            else 8.0
                        ),
                        f"{GRAPH_STRONG_EXPANSION_PREFIX}"
                        f"{first_call.target_symbol} reaches "
                        f"{second_call.target_symbol}, defined here: "
                        f"{first_call.target_file}",
                    )
    return relations


def _merge_tail_expansions(
    ranked_paths: list[str],
    tail_expansions: list[str],
    protected_paths: list[str],
    limit: int,
) -> list[str]:
    accepted_expansions = list(dict.fromkeys(tail_expansions))[:limit]
    retained = ranked_paths[: max(0, limit - len(accepted_expansions))]
    retained_set = set(retained)
    ranked_set = set(ranked_paths)
    protected_set = set(protected_paths)

    for path in protected_paths[:limit]:
        if path in retained_set or path not in ranked_set:
            continue
        replace_index = next(
            (
                index
                for index in range(len(retained) - 1, -1, -1)
                if retained[index] not in protected_set
            ),
            None,
        )
        if replace_index is not None:
            retained_set.remove(retained[replace_index])
            retained[replace_index] = path
            retained_set.add(path)
        elif accepted_expansions:
            accepted_expansions.pop()
            retained.append(path)
            retained_set.add(path)

    return [*retained, *accepted_expansions][:limit]


def _reserve_protected_paths(
    ranked_paths: list[str],
    protected_paths: list[str],
    limit: int,
    reservation_slots: int,
) -> list[str]:
    selected = ranked_paths[:limit]
    selected_set = set(selected)
    protected_set = set(protected_paths)
    missing_protected_paths = [
        path for path in protected_paths if path not in selected_set
    ]
    replaceable_indices = [
        index
        for index in range(len(selected) - 1, -1, -1)
        if selected[index] not in protected_set
    ]
    replace_count = min(
        reservation_slots,
        len(missing_protected_paths),
        len(replaceable_indices),
    )
    replace_indices = replaceable_indices[:replace_count]
    replace_indices.sort()
    reserved_paths = missing_protected_paths[:replace_count]
    for replace_index, path in zip(replace_indices, reserved_paths, strict=True):
        selected[replace_index] = path
    return selected


def _protected_base_reservation_slots(limit: int) -> int:
    if limit >= EXPANDED_PROTECTED_BASE_RESERVATION_MIN_LIMIT:
        return EXPANDED_PROTECTED_BASE_RESERVATION_SLOTS
    return PROTECTED_BASE_RESERVATION_SLOTS


def locate_candidates(
    issue: IssueRecord, repository_map: RepositoryMap, limit: int = 20
) -> list[CandidateLocation]:
    signals = extract_issue_signals(issue)
    expanded_retrieval = (
        limit >= EXPANDED_PROTECTED_BASE_RESERVATION_MIN_LIMIT
    )
    keywords = set(signals.terms)
    root = Path(repository_map.root).resolve()
    base_scores: dict[str, float] = {}
    auxiliary_files: dict[str, bool] = {}
    candidates: dict[str, CandidateLocation] = {}
    blame_lines: dict[str, str | None] = {}
    content_lines: dict[str, int | None] = {}
    symbol_name_counts = Counter(
        symbol.name
        for file in repository_map.files
        for symbol in file.symbols
        if symbol.kind in DIRECT_SYMBOL_KINDS
    )
    unique_symbol_names = frozenset(
        name for name, count in symbol_name_counts.items() if count == 1
    )
    path_term_counts = Counter(
        term
        for file in repository_map.files
        for term in _terms(file.path)
    )
    stem_term_counts = (
        Counter(
            term
            for file in repository_map.files
            for term in _terms(Path(file.path).stem)
        )
        if expanded_retrieval
        else Counter()
    )
    referenced_path_stem_terms = (
        {
            term
            for path in signals.paths
            for term in _terms(Path(path).stem)
        }
        if expanded_retrieval
        else set()
    )
    protected_base_paths: set[str] = set()
    repository_file_paths = [file.path for file in repository_map.files]
    symbol_scoped_paths = _symbol_scoped_paths(
        repository_file_paths,
        signals.paths,
    )
    traceback_frames_by_path = _scoped_traceback_frames(
        repository_file_paths,
        signals.traceback_frames,
    )
    source_line_positions_by_path = _source_line_positions_by_path(
        root,
        repository_map.files,
        signals.source_line_references,
    )
    source_snippet_positions_by_path = _source_snippet_positions_by_path(
        root,
        repository_map.files,
        signals.paths,
        signals.source_snippets,
    )
    constructor_positions_by_path = _title_constructor_positions_by_path(
        repository_map.files,
        signals,
    )
    for file in repository_map.files:
        path_parts = set(Path(file.path).parts)
        auxiliary_file = file.test_file or bool(path_parts & AUXILIARY_PATH_PARTS)
        path_terms = _terms(file.path)
        path_overlap = keywords & path_terms
        specific_stem_overlap = (
            {
                term
                for term in signals.content_terms
                & _terms(Path(file.path).stem)
                if len(term) >= 5
                and term not in GENERIC_SUBSYSTEM_TERMS
                and term not in referenced_path_stem_terms
                and stem_term_counts[term]
                <= SPECIFIC_PATH_TERM_MAX_FILES
            }
            if expanded_retrieval and file.language == "Rust"
            else set()
        )
        primary_path_overlap = {
            term
            for term in signals.primary_terms & path_terms
            if path_term_counts[term] <= SPECIFIC_PATH_TERM_MAX_FILES
        }
        import_overlap = {
            word for word in keywords if any(word in item.lower() for item in file.imports)
        }
        exact_path = _path_is_referenced(file.path, signals.paths)
        path_identifier_hits = {
            identifier
            for identifier in signals.primary_identifiers
            if any(
                variant in re.sub(r"[^a-z0-9]", "", file.path.lower())
                for variant in _compact_identifier_variants(identifier)
            )
        }
        traceback_symbol_positions = _traceback_symbol_positions(
            file.symbols,
            traceback_frames_by_path.get(file.path, ()),
        )
        source_line_symbol_positions = source_line_positions_by_path.get(
            file.path,
            {},
        )
        source_snippet_symbol_positions = source_snippet_positions_by_path.get(
            file.path,
            {},
        )
        constructor_symbol_positions = constructor_positions_by_path.get(
            file.path,
            {},
        )
        symbol_matches = [
            _match_symbol(
                symbol,
                signals,
                unique_symbol_names,
                file.path in symbol_scoped_paths,
                test_file=file.test_file,
                constructor_call_index=constructor_symbol_positions.get(
                    symbol.qualified_name or symbol.name
                ),
                traceback_frame_index=traceback_symbol_positions.get(
                    symbol.qualified_name or symbol.name
                ),
                source_line_reference_index=(
                    source_line_symbol_positions.get(
                        symbol.qualified_name or symbol.name
                    )
                ),
                source_snippet_index=source_snippet_symbol_positions.get(
                    symbol.qualified_name or symbol.name
                ),
            )
            for symbol in file.symbols
        ]
        related_symbol_callers = _related_symbol_callers(
            symbol_matches,
            file.resolved_calls,
            file.path,
            file.receiver_calls,
        )
        score_match: SymbolMatch | None = None
        for match in symbol_matches:
            if (
                match.primary_identifier_match,
                match.exact_identifier_match,
                len(match.local_overlap),
            ) > (
                score_match.primary_identifier_match if score_match else False,
                score_match.exact_identifier_match if score_match else False,
                len(score_match.local_overlap) if score_match else 0,
            ):
                score_match = match
        blame_match = _select_symbol(
            symbol_matches,
            signals,
            {},
            use_traceback_frames=False,
            use_source_line_references=False,
            use_source_snippets=False,
            use_constructor_calls=False,
            use_qualified_title_semantics=False,
        )
        selected_match = _select_symbol(
            symbol_matches,
            signals,
            related_symbol_callers,
        )
        primary_symbol_match = (
            score_match.primary_identifier_match if score_match else False
        )
        if (
            exact_path
            or path_identifier_hits
            or primary_path_overlap
            or primary_symbol_match
        ):
            protected_base_paths.add(file.path)
        exact_symbol = score_match.exact_identifier_match if score_match else False
        best_overlap = set(score_match.local_overlap) if score_match else set()
        blame_symbol = blame_match.symbol if blame_match else None
        selected_symbol = selected_match.symbol if selected_match else None
        (
            content_identifiers,
            structured_content_identifiers,
            content_overlap,
            content_line,
        ) = _source_content(
            root,
            file.path,
            signals,
            file.language,
            include_structured_identifiers=expanded_retrieval,
        )
        dependency_metadata_overlap = (
            {
                term
                for term in signals.title_terms & content_overlap
                if len(term) >= 5
                and term not in DEPENDENCY_METADATA_CONTEXT_TERMS
            }
            if expanded_retrieval
            and file.path in PYTHON_DEPENDENCY_METADATA_FILES
            and file.language == "Python"
            and not auxiliary_file
            and bool(
                signals.title_terms & DEPENDENCY_METADATA_CONTEXT_TERMS
            )
            else set()
        )
        if (
            structured_content_identifiers
            or specific_stem_overlap
            or dependency_metadata_overlap
        ) and not auxiliary_file:
            protected_base_paths.add(file.path)
        raw_score = (
            30.0 * exact_path
            + 18.0 * primary_symbol_match
            + 3.0 * (exact_symbol and not primary_symbol_match)
            + 5.0 * min(len(path_identifier_hits), 2)
            + 2.5 * len(best_overlap)
            + 4.0 * len(path_overlap)
            + 6.0 * min(len(specific_stem_overlap), 1)
            + 6.0 * len(primary_path_overlap)
            + 0.7 * len(import_overlap)
            + 3.0 * min(len(content_identifiers), 4)
            + 6.0 * min(len(structured_content_identifiers), 1)
            + 0.4 * min(len(content_overlap), 8)
            + 8.0 * min(len(dependency_metadata_overlap), 1)
            + (0.5 if not auxiliary_file else -2.0)
        )
        if raw_score <= 0:
            continue
        evidence: list[str] = []
        if exact_path:
            evidence.append("Issue references this exact source path")
        if path_identifier_hits:
            evidence.append(
                "Path matches issue identifiers: "
                + ", ".join(sorted(path_identifier_hits))
            )
        if path_overlap:
            evidence.append(f"Path matches issue terms: {', '.join(sorted(path_overlap))}")
        if specific_stem_overlap:
            evidence.append(
                "Specific Rust filename stem matches issue terms: "
                + ", ".join(sorted(specific_stem_overlap))
            )
        if primary_path_overlap:
            evidence.append(
                "Path matches issue title terms: "
                + ", ".join(sorted(primary_path_overlap))
            )
        evidence.extend(_symbol_evidence(selected_match, related_symbol_callers))
        if import_overlap:
            evidence.append(f"Imports match component terms: {', '.join(sorted(import_overlap))}")
        if content_identifiers:
            evidence.append(
                "Source contains issue identifiers: "
                + ", ".join(sorted(content_identifiers))
            )
        if structured_content_identifiers:
            evidence.append(
                "Source structurally matches issue identifier: "
                + ", ".join(sorted(structured_content_identifiers))
            )
        if content_overlap:
            evidence.append(
                "Source content matches issue terms: "
                + ", ".join(sorted(content_overlap)[:8])
            )
        if dependency_metadata_overlap:
            evidence.append(
                "Python dependency metadata matches issue title terms: "
                + ", ".join(sorted(dependency_metadata_overlap))
            )
        base_scores[file.path] = raw_score
        auxiliary_files[file.path] = auxiliary_file
        content_lines[file.path] = content_line
        blame_lines[file.path] = (
            f"{blame_symbol.line}-{blame_symbol.end_line or blame_symbol.line}"
            if blame_symbol
            else f"{content_line}-{content_line}"
            if content_line
            else None
        )
        candidates[file.path] = CandidateLocation(
            file=file.path,
            symbol=selected_symbol.name if selected_symbol else None,
            qualified_symbol=(
                selected_symbol.qualified_name
                if selected_symbol
                and selected_symbol.qualified_name != selected_symbol.name
                else None
            ),
            lines=(
                f"{selected_symbol.line}-"
                f"{selected_symbol.end_line or selected_symbol.line}"
            )
            if selected_symbol
            else f"{content_line}-{content_line}"
            if content_line
            else None,
            confidence=round(min(0.98, 0.2 + raw_score / 30), 2),
            evidence=evidence,
        )

    semantic_test_sources = _semantic_test_source_matches(
        repository_map,
        _graph_seed_paths(base_scores, auxiliary_files),
    )
    for source_path in sorted(set(semantic_test_sources.values())):
        if source_path in base_scores:
            continue
        base_scores[source_path] = 0
        auxiliary_files[source_path] = False
        content_lines[source_path] = None
        blame_lines[source_path] = None
        candidates[source_path] = CandidateLocation(
            file=source_path,
            confidence=0.2,
            evidence=[],
        )

    graph_relations = _graph_relations(
        repository_map,
        base_scores,
        auxiliary_files,
        signals,
        semantic_test_sources,
    )
    protected_base_paths.update(
        path
        for path, relations in graph_relations.items()
        if not auxiliary_files[path]
        and any(
            evidence.startswith(REEXPORTED_IMPORT_RELATION_PREFIX)
            for _, evidence in relations
        )
    )
    history_relations = _history_relations(
        root,
        _graph_seed_paths(base_scores, auxiliary_files),
        set(base_scores),
        auxiliary_files,
    )
    for path, relations in history_relations.items():
        graph_relations.setdefault(path, []).extend(relations)
    blame_relations = _blame_relations(
        root,
        _graph_seed_paths(base_scores, auxiliary_files),
        blame_lines,
        set(base_scores),
        auxiliary_files,
    )
    final_scores = dict(base_scores)
    for path, relations in graph_relations.items():
        unique_relations = sorted(
            set(relations),
            key=lambda relation: (-relation[0], relation[1]),
        )
        rerank_relations = sorted(
            (
                (_rerank_relation_bonus(bonus, evidence), evidence)
                for bonus, evidence in unique_relations
                if _rerank_relation_bonus(bonus, evidence) > 0
            ),
            key=lambda relation: (-relation[0], relation[1]),
        )
        graph_bonus = min(
            GRAPH_BONUS_LIMIT,
            sum(bonus for bonus, _ in rerank_relations[:2]),
        )
        displayed_relations = unique_relations[:2]
        strong_relation = next(
            (
                relation
                for relation in unique_relations
                if relation[1].startswith(GRAPH_STRONG_EXPANSION_PREFIX)
            ),
            None,
        )
        shared_qualified_relation = next(
            (
                relation
                for relation in unique_relations
                if relation[1].startswith(SHARED_QUALIFIED_CALL_PREFIX)
            ),
            None,
        )
        reexported_import_relation = next(
            (
                relation
                for relation in unique_relations
                if relation[1].startswith(REEXPORTED_IMPORT_RELATION_PREFIX)
            ),
            None,
        )
        if strong_relation and strong_relation not in displayed_relations:
            displayed_relations = [*displayed_relations[:1], strong_relation]
        if (
            shared_qualified_relation
            and shared_qualified_relation not in displayed_relations
        ):
            if strong_relation and strong_relation in displayed_relations:
                displayed_relations = [
                    strong_relation,
                    shared_qualified_relation,
                ]
            else:
                displayed_relations = [
                    *displayed_relations[:1],
                    shared_qualified_relation,
                ]
        if (
            reexported_import_relation
            and reexported_import_relation not in displayed_relations
        ):
            if strong_relation:
                displayed_relations = [
                    strong_relation,
                    reexported_import_relation,
                ]
            elif shared_qualified_relation:
                displayed_relations = [
                    shared_qualified_relation,
                    reexported_import_relation,
                ]
            else:
                displayed_relations = [
                    *displayed_relations[:1],
                    reexported_import_relation,
                ]
        final_scores[path] += graph_bonus
        candidates[path].confidence = round(
            min(0.98, 0.2 + final_scores[path] / 30),
            2,
        )
        candidates[path].evidence.extend(
            evidence for _, evidence in displayed_relations
        )

    base_order = sorted(
        base_scores,
        key=lambda path: (
            -base_scores[path],
            auxiliary_files[path],
            path,
        ),
    )
    reservation_order = [
        path
        for path in base_order
        if path in protected_base_paths and not auxiliary_files[path]
    ]
    base_shortlist = _reserve_protected_paths(
        base_order,
        reservation_order,
        limit,
        _protected_base_reservation_slots(limit),
    )
    reranked_base: list[str] = []
    for start in range(0, len(base_shortlist), GRAPH_RERANK_BAND_SIZE):
        band = base_shortlist[start : start + GRAPH_RERANK_BAND_SIZE]
        reranked_base.extend(
            sorted(
                band,
                key=lambda path: (
                    -final_scores[path],
                    auxiliary_files[path],
                    path,
                ),
            )
        )
    expansion_candidates = (
        path
        for path, relations in graph_relations.items()
        if path not in base_shortlist
        and sum(
            _expansion_relation_bonus(bonus, evidence)
            for bonus, evidence in sorted(
                (
                    relation
                    for relation in set(relations)
                    if _expansion_relation_bonus(*relation) > 0
                ),
                key=lambda relation: (-relation[0], relation[1]),
            )[:2]
        )
        >= GRAPH_EXPANSION_MIN_BONUS
    )

    def strong_expansion(path: str) -> bool:
        return any(
            evidence.startswith(GRAPH_STRONG_EXPANSION_PREFIX)
            for _, evidence in graph_relations[path]
        )

    def shared_qualified_expansion(path: str) -> bool:
        return any(
            evidence.startswith(SHARED_QUALIFIED_CALL_PREFIX)
            for _, evidence in graph_relations[path]
        )

    def reverse_import_expansion(path: str) -> bool:
        return any(
            evidence.startswith(REVERSE_IMPORT_RELATION_PREFIX)
            for _, evidence in graph_relations[path]
        )

    expansion_paths = sorted(
        expansion_candidates,
        key=lambda path: (
            not strong_expansion(path),
            not shared_qualified_expansion(path),
            not reverse_import_expansion(path),
            -max(bonus for bonus, _ in graph_relations[path]),
            -final_scores[path],
            auxiliary_files[path],
            path,
        ),
    )[: min(GRAPH_EXPANSION_SLOTS, limit)]
    promoted_expansions = [
        path for path in expansion_paths if strong_expansion(path)
    ]
    tail_expansions = [
        path for path in expansion_paths if not strong_expansion(path)
    ]
    first_band_size = min(GRAPH_RERANK_BAND_SIZE, limit)
    reserved_promotions = promoted_expansions[
        : min(GRAPH_STRONG_EXPANSION_SLOTS, first_band_size)
    ]
    deferred_promotions = promoted_expansions[len(reserved_promotions):]
    retained_first_band_size = first_band_size - len(reserved_promotions)
    promoted_first_band = [
        *reranked_base[:retained_first_band_size],
        *reserved_promotions,
    ]
    deferred_paths = [
        *reranked_base[retained_first_band_size:first_band_size],
        *deferred_promotions,
    ]
    base_with_promotions = [
        *promoted_first_band,
        *deferred_paths,
        *reranked_base[first_band_size:],
    ]
    protected_order = [
        path for path in reranked_base if path in protected_base_paths
    ]
    reranked_paths = _merge_tail_expansions(
        base_with_promotions,
        tail_expansions,
        protected_order,
        limit,
    )
    files_by_path = {file.path: file for file in repository_map.files}
    candidate_symbol_name_counts = Counter(
        symbol.name
        for path in reranked_paths
        for symbol in files_by_path[path].symbols
        if symbol.kind in DIRECT_SYMBOL_KINDS
    )
    candidate_unique_symbol_names = frozenset(
        name
        for name, count in candidate_symbol_name_counts.items()
        if count == 1
    )
    for path in reranked_paths:
        file = files_by_path[path]
        traceback_symbol_positions = _traceback_symbol_positions(
            file.symbols,
            traceback_frames_by_path.get(file.path, ()),
        )
        source_line_symbol_positions = source_line_positions_by_path.get(
            file.path,
            {},
        )
        source_snippet_symbol_positions = source_snippet_positions_by_path.get(
            file.path,
            {},
        )
        constructor_symbol_positions = constructor_positions_by_path.get(
            file.path,
            {},
        )
        symbol_matches = [
            _match_symbol(
                symbol,
                signals,
                candidate_unique_symbol_names,
                path in symbol_scoped_paths,
                test_file=file.test_file,
                constructor_call_index=constructor_symbol_positions.get(
                    symbol.qualified_name or symbol.name
                ),
                traceback_frame_index=traceback_symbol_positions.get(
                    symbol.qualified_name or symbol.name
                ),
                source_line_reference_index=(
                    source_line_symbol_positions.get(
                        symbol.qualified_name or symbol.name
                    )
                ),
                source_snippet_index=source_snippet_symbol_positions.get(
                    symbol.qualified_name or symbol.name
                ),
            )
            for symbol in file.symbols
        ]
        related_symbol_callers = _related_symbol_callers(
            symbol_matches,
            file.resolved_calls,
            file.path,
            file.receiver_calls,
        )
        selected_match = _select_symbol(
            symbol_matches,
            signals,
            related_symbol_callers,
        )
        if (
            selected_match is None
            or (
                selected_match.traceback_frame_index is None
                and selected_match.source_line_reference_index is None
                and selected_match.source_snippet_index is None
            )
        ):
            selected_match = _shared_qualified_call_match(
                symbol_matches,
                file.qualified_external_calls,
                graph_relations.get(path, []),
            ) or selected_match
        selected_symbol = selected_match.symbol if selected_match else None
        candidate = candidates[path]
        candidate.symbol = selected_symbol.name if selected_symbol else None
        candidate.qualified_symbol = (
            selected_symbol.qualified_name
            if selected_symbol
            and selected_symbol.qualified_name != selected_symbol.name
            else None
        )
        candidate.alternate_symbols = _alternate_symbol_locations(
            symbol_matches,
            selected_match,
            signals,
            related_symbol_callers,
        )
        content_line = content_lines[path]
        candidate.lines = (
            f"{selected_symbol.line}-"
            f"{selected_symbol.end_line or selected_symbol.line}"
            if selected_symbol
            else f"{content_line}-{content_line}"
            if content_line
            else None
        )
        candidate.evidence = [
            evidence
            for evidence in candidate.evidence
            if not evidence.startswith(SYMBOL_EVIDENCE_PREFIXES)
        ]
        candidate.evidence.extend(
            _symbol_evidence(selected_match, related_symbol_callers)
        )
        candidates[path].evidence.extend(
            evidence for _, evidence in blame_relations.get(path, [])[:1]
        )
    return [
        candidates[path]
        for path in reranked_paths
    ]


def investigate(
    issue: IssueRecord,
    repository_map: RepositoryMap,
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
) -> InvestigationReport:
    if candidate_limit < 1:
        raise ValueError("candidate_limit must be positive")
    candidates = locate_candidates(issue, repository_map, limit=candidate_limit)
    hypotheses: list[Hypothesis] = []
    for index, candidate in enumerate(candidates[:3], start=1):
        location = candidate.file
        if candidate.symbol:
            location = (
                f"{location}::{candidate.qualified_symbol or candidate.symbol}"
            )
        hypotheses.append(
            Hypothesis(
                id=f"H{index}",
                description=f"The failure path may originate in {location}",
                confidence=candidate.confidence,
                supporting_evidence=candidate.evidence,
                missing_evidence=[
                    "A failing test or runtime trace is required to confirm causality"
                ],
            )
        )
    if not hypotheses:
        hypotheses = [
            Hypothesis(
                id="H1",
                description="The issue does not map strongly to indexed repository symbols",
                confidence=0.2,
                supporting_evidence=["No path or symbol exceeded the evidence threshold"],
                missing_evidence=["Stack trace", "Affected component", "Minimal reproduction"],
            )
        ]
    plan = ReproductionPlan(
        runtime="Python 3.11"
        if "Python" in repository_map.languages
        else "Repository-defined runtime",
        setup_commands=["uv sync --frozen --extra dev"]
        if "pyproject.toml" in repository_map.runtime_files
        else ["Install dependencies using repository documentation"],
        baseline_command="uv run pytest -q"
        if "pyproject.toml" in repository_map.runtime_files
        else None,
        reproduction_steps=[
            f"Translate issue #{issue.number} into a minimal failing test",
            "Run the smallest relevant test target before changing code",
            "Capture the exception, response, or state difference",
            "Compare actual behavior with expected behavior",
        ],
        safety_constraints=[
            "Do not write outside the repository workspace",
            "Disable external network access when possible",
            "Do not use production credentials or data",
            "Apply a 120-second timeout",
        ],
        open_questions=[
            "Which exact version first exhibited the behavior?",
            "Is a minimal reproduction or stack trace available?",
        ],
    )
    return InvestigationReport(
        issue=issue,
        confirmed_facts=[
            f"Issue #{issue.number} is titled: {issue.title}",
            f"Repository map contains {len(repository_map.files)} indexed source files",
        ],
        candidates=candidates,
        hypotheses=hypotheses,
        reproduction_plan=plan,
        repository_root=Path(repository_map.root),
    )
