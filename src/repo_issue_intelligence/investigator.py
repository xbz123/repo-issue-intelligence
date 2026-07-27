from __future__ import annotations

import re
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
    "issue",
    "error",
    "fails",
    "failure",
    "problem",
    "when",
    "with",
    "from",
    "this",
    "that",
    "have",
    "does",
}


def _keywords(issue: IssueRecord) -> set[str]:
    return {
        word
        for word in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", issue.text)
        if word not in GENERIC_TERMS
    }


def locate_candidates(
    issue: IssueRecord, repository_map: RepositoryMap, limit: int = 5
) -> list[CandidateLocation]:
    keywords = _keywords(issue)
    ranked: list[tuple[float, CandidateLocation]] = []
    for file in repository_map.files:
        path_terms = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", file.path.lower()))
        path_overlap = keywords & path_terms
        import_overlap = {
            word for word in keywords if any(word in item.lower() for item in file.imports)
        }
        best_symbol = None
        best_overlap: set[str] = set()
        for symbol in file.symbols:
            terms = set(symbol.name.lower().split("_")) | {symbol.name.lower()}
            terms |= set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", (symbol.docstring or "").lower()))
            overlap = keywords & terms
            if len(overlap) > len(best_overlap):
                best_symbol, best_overlap = symbol, overlap
        raw_score = 2.0 * len(best_overlap) + 1.3 * len(path_overlap) + 0.7 * len(import_overlap)
        if raw_score <= 0:
            continue
        evidence: list[str] = []
        if path_overlap:
            evidence.append(f"Path matches issue terms: {', '.join(sorted(path_overlap))}")
        if best_symbol and best_overlap:
            evidence.append(f"Symbol {best_symbol.name} matches: {', '.join(sorted(best_overlap))}")
        if import_overlap:
            evidence.append(f"Imports match component terms: {', '.join(sorted(import_overlap))}")
        ranked.append(
            (
                raw_score,
                CandidateLocation(
                    file=file.path,
                    symbol=best_symbol.name if best_symbol else None,
                    lines=f"{best_symbol.line}-{best_symbol.end_line or best_symbol.line}"
                    if best_symbol
                    else None,
                    confidence=round(min(0.95, 0.25 + raw_score / 12), 2),
                    evidence=evidence,
                ),
            )
        )
    return [
        candidate for _, candidate in sorted(ranked, key=lambda item: item[0], reverse=True)[:limit]
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
