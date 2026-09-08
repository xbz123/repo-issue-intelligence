from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from .models import EvidenceSnippet, InvestigationReport

if TYPE_CHECKING:
    from .protocol_v2_models import EvidenceItemV2
    from .repository_view import RepositoryView

SENSITIVE_FILENAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    "credentials",
    "credentials.json",
    "id_ed25519",
    "id_rsa",
}
LINE_RANGE = re.compile(r"^(?P<start>\d+)-(?P<end>\d+)$")
DEFAULT_MAX_TOTAL_CHARS = 100_000
DEFAULT_MAX_LINES_PER_SNIPPET = 200


def _candidate_path(
    root: Path,
    relative_path: str,
    *,
    allowed_paths: frozenset[str] | None = None,
) -> Path | None:
    candidate = Path(relative_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    normalized = candidate.as_posix()
    if allowed_paths is not None and normalized not in allowed_paths:
        return None
    path = (root / candidate).resolve()
    if not path.is_relative_to(root):
        return None
    if path.name.lower() in SENSITIVE_FILENAMES:
        return None
    if not path.is_file():
        return None
    return path


def _line_range(
    value: str | None,
    line_count: int,
    max_lines: int | None,
    context_lines: int,
) -> tuple[int, int]:
    match = LINE_RANGE.fullmatch(value or "")
    start = int(match.group("start")) if match else 1
    end = int(match.group("end")) if match else line_count
    start = min(max(1, start - context_lines), max(1, line_count))
    end = min(max(start, end + context_lines), line_count)
    if max_lines is not None:
        end = min(end, start + max_lines - 1)
    return start, end


def collect_evidence(
    report: InvestigationReport,
    max_total_chars: int | None = DEFAULT_MAX_TOTAL_CHARS,
    max_lines_per_snippet: int | None = DEFAULT_MAX_LINES_PER_SNIPPET,
    context_lines: int = 12,
    max_chars_per_snippet: int | None = None,
    *,
    repository_view: RepositoryView | None = None,
    view: RepositoryView | None = None,
) -> list[EvidenceSnippet]:
    if max_total_chars is not None and max_total_chars < 1:
        raise ValueError("max_total_chars must be positive")
    if max_lines_per_snippet is not None and max_lines_per_snippet < 1:
        raise ValueError("max_lines_per_snippet must be positive")
    if context_lines < 0:
        raise ValueError("context_lines cannot be negative")
    if max_chars_per_snippet is not None and max_chars_per_snippet < 1:
        raise ValueError("max_chars_per_snippet must be positive")

    if repository_view is not None and view is not None and repository_view is not view:
        raise ValueError("repository_view and view must refer to the same source view")
    active_view = repository_view or view
    if active_view is not None and active_view.closed:
        raise ValueError("repository_view is closed or unavailable")
    root = (
        active_view.materialized_root.expanduser().resolve()
        if active_view is not None
        else report.repository_root.expanduser().resolve()
    )
    allowed_paths = (
        frozenset(active_view.files)
        if active_view is not None
        else None
    )
    snippets: list[EvidenceSnippet] = []
    remaining = max_total_chars
    for candidate in report.candidates:
        path = _candidate_path(root, candidate.file, allowed_paths=allowed_paths)
        if path is None:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "\x00" in text:
            continue
        source_lines = text.splitlines()
        if not source_lines:
            continue
        start, end = _line_range(
            candidate.lines,
            len(source_lines),
            max_lines_per_snippet,
            context_lines,
        )
        numbered_lines = [
            f"{line_number}: {source_lines[line_number - 1]}"
            for line_number in range(start, end + 1)
        ]
        content = "\n".join(numbered_lines)
        snippet_limit = max_chars_per_snippet
        if remaining is not None:
            snippet_limit = (
                remaining if snippet_limit is None else min(remaining, snippet_limit)
            )
        if snippet_limit is not None and len(content) > snippet_limit:
            content = content[:snippet_limit].rstrip()
        if not content:
            continue
        included_end = start + content.count("\n")
        snippets.append(
            EvidenceSnippet(
                id=f"E{len(snippets) + 1}",
                file=candidate.file,
                symbol=candidate.qualified_symbol or candidate.symbol,
                lines=f"{start}-{included_end}",
                content=content,
            )
        )
        if remaining is not None:
            remaining -= len(content)
            if remaining <= 0:
                break
    return snippets


def collect_evidence_v2(
    report: InvestigationReport,
    max_total_chars: int | None = DEFAULT_MAX_TOTAL_CHARS,
    max_lines_per_snippet: int | None = DEFAULT_MAX_LINES_PER_SNIPPET,
    context_lines: int = 12,
    max_chars_per_snippet: int | None = None,
    *,
    repository_view: RepositoryView,
) -> list[EvidenceItemV2]:
    """Collect complete numbered lines from an explicitly captured source view.

    Requested ranges include context but precede budgets. Candidate ranks and
    item ordinals are zero-based; skipped candidates retain their original rank.
    """
    from .protocol_v2_models import EvidenceItemV2
    from .repository_view import RepositoryView

    for name, value in (
        ("max_total_chars", max_total_chars),
        ("max_lines_per_snippet", max_lines_per_snippet),
        ("max_chars_per_snippet", max_chars_per_snippet),
    ):
        if value is not None and (type(value) is not int or value < 1):
            raise ValueError(f"{name} must be a positive integer or None")
    if type(context_lines) is not int or context_lines < 0:
        raise ValueError("context_lines must be a nonnegative integer")
    if not isinstance(repository_view, RepositoryView):
        raise ValueError("repository_view must be a captured RepositoryView")

    if repository_view.closed:
        raise ValueError("repository_view is closed or unavailable")
    root = repository_view.materialized_root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError("repository_view is closed or unavailable")
    allowed_paths = frozenset(repository_view.files)
    items: list[EvidenceItemV2] = []
    remaining = max_total_chars
    for rank, candidate in enumerate(report.candidates):
        if candidate.lines is not None:
            match = LINE_RANGE.fullmatch(candidate.lines)
            if match is None or not 1 <= int(match["start"]) <= int(match["end"]):
                raise ValueError("candidate range must be positive and ordered")
        path = _candidate_path(root, candidate.file, allowed_paths=allowed_paths)
        if path is None:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "\x00" in source or not (source_lines := source.splitlines()):
            continue
        # Context expansion must not turn an entirely stale location into evidence.
        if candidate.lines is not None and int(match["start"]) > len(source_lines):
            continue
        start, requested_end = _line_range(
            candidate.lines,
            len(source_lines),
            None,
            context_lines,
        )
        end = requested_end
        reasons = []
        if max_lines_per_snippet is not None and end - start + 1 > max_lines_per_snippet:
            end = start + max_lines_per_snippet - 1
            reasons.append("line_budget")
        lines: list[str] = []
        char_count = 0
        for number in range(start, end + 1):
            line = f"{number}: {source_lines[number - 1]}"
            next_count = char_count + len(line) + bool(lines)
            if max_chars_per_snippet is not None and next_count > max_chars_per_snippet:
                reasons.append("snippet_char_budget")
            if remaining is not None and next_count > remaining:
                reasons.append("total_char_budget")
            if reasons and reasons[-1] != "line_budget":
                break
            lines.append(line)
            char_count = next_count
        if not lines:
            continue
        items.append(
            EvidenceItemV2(
                evidence_id=f"E{len(items) + 1}",
                ordinal=len(items),
                candidate_rank=rank,
                selection_kind="candidate",
                file=candidate.file,
                symbol=candidate.qualified_symbol or candidate.symbol,
                requested_range=(start, requested_end),
                actual_range=(start, start + len(lines) - 1),
                truncation_reason=",".join(reasons) or None,
                char_count=char_count,
                content="\n".join(lines),
                collector_protocol="v2",
            )
        )
        if remaining is not None:
            remaining -= char_count
            if remaining <= 0:
                break
    return items
