from __future__ import annotations

import re
from pathlib import Path

from .models import EvidenceSnippet, InvestigationReport

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


def _candidate_path(root: Path, relative_path: str) -> Path | None:
    path = (root / relative_path).resolve()
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
    max_lines: int,
    context_lines: int,
) -> tuple[int, int]:
    match = LINE_RANGE.fullmatch(value or "")
    start = int(match.group("start")) if match else 1
    end = int(match.group("end")) if match else start + max_lines - 1
    start = min(max(1, start - context_lines), max(1, line_count))
    end = min(max(start, end + context_lines), line_count, start + max_lines - 1)
    return start, end


def collect_evidence(
    report: InvestigationReport,
    max_total_chars: int = 16_000,
    max_lines_per_snippet: int = 80,
    context_lines: int = 12,
    max_chars_per_snippet: int | None = None,
) -> list[EvidenceSnippet]:
    if max_total_chars < 1:
        raise ValueError("max_total_chars must be positive")
    if max_lines_per_snippet < 1:
        raise ValueError("max_lines_per_snippet must be positive")
    if context_lines < 0:
        raise ValueError("context_lines cannot be negative")
    if max_chars_per_snippet is not None and max_chars_per_snippet < 1:
        raise ValueError("max_chars_per_snippet must be positive")

    root = report.repository_root.expanduser().resolve()
    snippets: list[EvidenceSnippet] = []
    remaining = max_total_chars
    for candidate in report.candidates:
        path = _candidate_path(root, candidate.file)
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
        snippet_limit = remaining
        if max_chars_per_snippet is not None:
            snippet_limit = min(snippet_limit, max_chars_per_snippet)
        if len(content) > snippet_limit:
            content = content[:snippet_limit].rstrip()
        if not content:
            break
        snippets.append(
            EvidenceSnippet(
                id=f"E{len(snippets) + 1}",
                file=candidate.file,
                symbol=candidate.symbol,
                lines=f"{start}-{end}",
                content=content,
            )
        )
        remaining -= len(content)
        if remaining <= 0:
            break
    return snippets
