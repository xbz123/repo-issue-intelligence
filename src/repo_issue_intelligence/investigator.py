from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .models import (
    CandidateLocation,
    Hypothesis,
    InvestigationReport,
    IssueRecord,
    RepositoryMap,
    ReproductionPlan,
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
    rf"[A-Za-z0-9_.-]+\.(?:{SOURCE_SUFFIXES})",
    re.IGNORECASE,
)
CODE_SPAN_PATTERN = re.compile(r"`([^`\n]{1,120})`")
IDENTIFIER_PATTERN = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\b"
)


@dataclass(frozen=True)
class IssueSignals:
    terms: frozenset[str]
    identifiers: frozenset[str]
    primary_identifiers: frozenset[str]
    paths: frozenset[str]


def _terms(value: str) -> set[str]:
    value = value.replace("\\", "/")
    value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    return {
        term
        for term in re.findall(r"[a-z][a-z0-9]{1,}", value.lower())
        if term not in GENERIC_TERMS
    }


def _identifier_variants(value: str) -> set[str]:
    lowered = value.lower().strip("`'\"()[]{}:,")
    parts = _terms(value)
    variants = {lowered}
    if "." in lowered:
        variants.add(lowered.rsplit(".", maxsplit=1)[-1])
    if parts:
        variants.add("_".join(parts))
        variants.add("".join(parts))
    return {variant for variant in variants if len(variant) >= 3}


def _compact_identifier_variants(value: str) -> set[str]:
    compact = re.sub(r"[^a-z0-9]", "", value.lower())
    variants = {compact}
    if compact.endswith("s") and len(compact) > 4:
        variants.add(compact[:-1])
    return {variant for variant in variants if len(variant) >= 4}


def _extract_identifiers(text: str) -> set[str]:
    identifiers = {
        span.strip()
        for span in CODE_SPAN_PATTERN.findall(text)
        if IDENTIFIER_PATTERN.fullmatch(span.strip())
    }
    for identifier in IDENTIFIER_PATTERN.findall(text):
        if (
            "_" in identifier
            or "." in identifier
            or any(character.isupper() for character in identifier[1:])
        ):
            identifiers.add(identifier)
    return identifiers


def extract_issue_signals(issue: IssueRecord) -> IssueSignals:
    text = " ".join([issue.title, issue.body, *issue.labels])
    primary_text = " ".join([issue.title, *issue.labels])
    paths = {
        match.group(0).replace("\\", "/").strip("'\"()[]{}:,")
        for match in PATH_REFERENCE_PATTERN.finditer(text)
    }
    return IssueSignals(
        terms=frozenset(_terms(text)),
        identifiers=frozenset(_extract_identifiers(text)),
        primary_identifiers=frozenset(_extract_identifiers(primary_text)),
        paths=frozenset(paths),
    )


def _path_is_referenced(file_path: str, references: frozenset[str]) -> bool:
    normalized = file_path.lower().replace("\\", "/").lstrip("./")
    return any(
        reference.lower().replace("\\", "/").rstrip("/").endswith(normalized)
        or normalized.endswith(reference.lower().replace("\\", "/").lstrip("./"))
        for reference in references
    )


def _source_content(
    root: Path,
    relative_path: str,
    signals: IssueSignals,
) -> tuple[set[str], set[str], int | None]:
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return set(), set(), None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set(), set(), None
    if "\x00" in text or len(text) > 1_000_000:
        return set(), set(), None

    lowered = text.lower()
    identifier_hits = {
        identifier
        for identifier in signals.identifiers
        if any(variant in lowered for variant in _identifier_variants(identifier))
    }
    content_overlap = {
        term for term in signals.terms if len(term) >= 4 and term in lowered
    }
    first_line = None
    variants = {
        variant
        for identifier in identifier_hits
        for variant in _identifier_variants(identifier)
    }
    if variants:
        first_line = next(
            (
                line_number
                for line_number, line in enumerate(text.splitlines(), start=1)
                if any(variant in line.lower() for variant in variants)
            ),
            None,
        )
    return identifier_hits, content_overlap, first_line


def locate_candidates(
    issue: IssueRecord, repository_map: RepositoryMap, limit: int = 20
) -> list[CandidateLocation]:
    signals = extract_issue_signals(issue)
    keywords = set(signals.terms)
    root = Path(repository_map.root).resolve()
    ranked: list[tuple[float, bool, str, CandidateLocation]] = []
    for file in repository_map.files:
        path_parts = set(Path(file.path).parts)
        auxiliary_file = file.test_file or bool(
            path_parts & {"docs", "docs_src", "examples", "scripts"}
        )
        path_terms = _terms(file.path)
        path_overlap = keywords & path_terms
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
        best_symbol = None
        best_overlap: set[str] = set()
        primary_symbol_match = False
        exact_symbol = False
        for symbol in file.symbols:
            terms = _terms(symbol.name) | _terms(symbol.docstring or "")
            overlap = keywords & terms
            symbol_variants = _identifier_variants(symbol.name)
            symbol_compact_variants = _compact_identifier_variants(symbol.name)
            symbol_primary_match = any(
                symbol_compact_variants
                & _compact_identifier_variants(identifier)
                for identifier in signals.primary_identifiers
            )
            symbol_exact = any(
                symbol_variants & _identifier_variants(identifier)
                for identifier in signals.identifiers
            )
            if (symbol_primary_match, symbol_exact, len(overlap)) > (
                primary_symbol_match,
                exact_symbol,
                len(best_overlap),
            ):
                best_symbol, best_overlap = symbol, overlap
                primary_symbol_match = symbol_primary_match
                exact_symbol = symbol_exact
        content_identifiers, content_overlap, content_line = _source_content(
            root,
            file.path,
            signals,
        )
        raw_score = (
            30.0 * exact_path
            + 18.0 * primary_symbol_match
            + 3.0 * (exact_symbol and not primary_symbol_match)
            + 5.0 * min(len(path_identifier_hits), 2)
            + 2.5 * len(best_overlap)
            + 4.0 * len(path_overlap)
            + 0.7 * len(import_overlap)
            + 3.0 * min(len(content_identifiers), 4)
            + 0.4 * min(len(content_overlap), 8)
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
        if best_symbol and best_overlap:
            evidence.append(f"Symbol {best_symbol.name} matches: {', '.join(sorted(best_overlap))}")
        if best_symbol and exact_symbol:
            evidence.append(f"Issue references symbol {best_symbol.name}")
        if best_symbol and primary_symbol_match:
            evidence.append(
                f"Issue title strongly matches symbol {best_symbol.name}"
            )
        if import_overlap:
            evidence.append(f"Imports match component terms: {', '.join(sorted(import_overlap))}")
        if content_identifiers:
            evidence.append(
                "Source contains issue identifiers: "
                + ", ".join(sorted(content_identifiers))
            )
        if content_overlap:
            evidence.append(
                "Source content matches issue terms: "
                + ", ".join(sorted(content_overlap)[:8])
            )
        ranked.append(
            (
                raw_score,
                auxiliary_file,
                file.path,
                CandidateLocation(
                    file=file.path,
                    symbol=best_symbol.name if best_symbol else None,
                    lines=f"{best_symbol.line}-{best_symbol.end_line or best_symbol.line}"
                    if best_symbol
                    else f"{content_line}-{content_line}"
                    if content_line
                    else None,
                    confidence=round(min(0.98, 0.2 + raw_score / 30), 2),
                    evidence=evidence,
                ),
            )
        )
    return [
        candidate
        for _, _, _, candidate in sorted(
            ranked,
            key=lambda item: (-item[0], item[1], item[2]),
        )[:limit]
    ]


def investigate(issue: IssueRecord, repository_map: RepositoryMap) -> InvestigationReport:
    candidates = locate_candidates(issue, repository_map)
    hypotheses: list[Hypothesis] = []
    for index, candidate in enumerate(candidates[:3], start=1):
        location = candidate.file
        if candidate.symbol:
            location = f"{location}::{candidate.symbol}"
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
