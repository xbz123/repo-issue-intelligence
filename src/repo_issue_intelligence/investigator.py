from __future__ import annotations

import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .models import (
    CandidateLocation,
    Hypothesis,
    InvestigationReport,
    IssueRecord,
    RepositoryMap,
    ReproductionPlan,
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
IDENTIFIER_PATTERN = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\b"
)
CALLED_IDENTIFIER_PATTERN = re.compile(
    r"\b((?:[A-Za-z_][A-Za-z0-9_]*\.)*[A-Za-z_][A-Za-z0-9_]*)\s*\("
)
GRAPH_SEED_LIMIT = 8
GRAPH_SEED_MIN_SCORE = 4.0
GRAPH_BONUS_LIMIT = 10.0
GRAPH_RERANK_BAND_SIZE = 10
GRAPH_EXPANSION_MIN_BONUS = 5.0
GRAPH_EXPANSION_SLOTS = 3
GRAPH_STRONG_EXPANSION_SLOTS = 1
GRAPH_DYNAMIC_CALL_MIN_LENGTH = 8
GRAPH_SECOND_HOP_CALL_MIN_LENGTH = 5
GRAPH_STRONG_EXPANSION_PREFIX = "Two-hop source call chain via "
HISTORY_COMMIT_LIMIT = 50
HISTORY_FILE_LIMIT = 50
BLAME_SEED_LIMIT = 2
BLAME_FILE_LIMIT = 20


@dataclass(frozen=True)
class IssueSignals:
    terms: frozenset[str]
    primary_terms: frozenset[str]
    identifiers: frozenset[str]
    primary_identifiers: frozenset[str]
    explicit_identifiers: frozenset[str]
    paths: frozenset[str]


@dataclass(frozen=True)
class SymbolMatch:
    symbol: SymbolRecord
    overlap: frozenset[str]
    local_overlap: frozenset[str]
    primary_identifier_match: bool
    exact_identifier_match: bool
    explicit_identifier_match: bool
    qualified_component_match: bool
    qualified_identifier_match: bool
    semantic_terms: frozenset[str]


def _ordered_terms(value: str) -> list[str]:
    value = value.replace("\\", "/")
    value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    return [
        term
        for term in re.findall(r"[a-z][a-z0-9]{1,}", value.lower())
        if term not in GENERIC_TERMS
    ]


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
    return {variant for variant in variants if len(variant) >= 3}


def _compact_identifier_variants(value: str) -> set[str]:
    compact = re.sub(r"[^a-z0-9]", "", value.lower())
    variants = {compact}
    if compact.endswith("s") and len(compact) > 4:
        variants.add(compact[:-1])
    return {variant for variant in variants if len(variant) >= 4}


def _extract_explicit_identifiers(text: str) -> set[str]:
    code_regions = [
        *CODE_SPAN_PATTERN.findall(text),
        *FENCED_CODE_PATTERN.findall(text),
    ]
    identifiers: set[str] = set()
    for region in code_regions:
        candidate = region.strip()
        if IDENTIFIER_PATTERN.fullmatch(candidate):
            identifiers.add(candidate)
        for match in CALLED_IDENTIFIER_PATTERN.finditer(region):
            prefix = region[max(0, match.start() - 12) : match.start()]
            if re.search(r"\b(?:class|def)\s+$", prefix):
                continue
            called_identifier = match.group(1)
            identifiers.add(called_identifier)
            identifiers.add(called_identifier.rsplit(".", maxsplit=1)[-1])
    return identifiers


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
        primary_terms=frozenset(_terms(primary_text)),
        identifiers=frozenset(_extract_identifiers(text)),
        primary_identifiers=frozenset(_extract_identifiers(primary_text)),
        explicit_identifiers=frozenset(_extract_explicit_identifiers(text)),
        paths=frozenset(paths),
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


def _matches_qualified_identity(identity: str, identifier: str) -> bool:
    candidate = identifier.strip("`'\"()[]{}:,")
    return candidate == identity or candidate.endswith(f".{identity}")


def _match_symbol(symbol: SymbolRecord, signals: IssueSignals) -> SymbolMatch:
    identity = symbol.qualified_name or symbol.name
    local_terms = _terms(symbol.name) | _terms(symbol.docstring or "")
    terms = _terms(identity) | local_terms
    symbol_variants = _identifier_variants(symbol.name)
    symbol_compact_variants = _compact_identifier_variants(symbol.name)
    identity_parts = identity.split(".")
    owner_identity = ".".join(identity_parts[:-1])
    owner_compact_variants = _compact_identifier_variants(owner_identity)
    source_scoped_identifiers = (
        signals.explicit_identifiers | signals.primary_identifiers
    )
    return SymbolMatch(
        symbol=symbol,
        overlap=frozenset(signals.terms & terms),
        local_overlap=frozenset(signals.terms & local_terms),
        primary_identifier_match=any(
            symbol_compact_variants & _compact_identifier_variants(identifier)
            for identifier in signals.primary_identifiers
        ),
        exact_identifier_match=any(
            symbol_variants & _identifier_variants(identifier)
            for identifier in signals.identifiers
        ),
        explicit_identifier_match=any(
            "." not in identifier and identifier == symbol.name
            for identifier in signals.explicit_identifiers
        ),
        qualified_component_match=identity != symbol.name
        and any(
            owner_compact_variants
            & _compact_identifier_variants(identifier)
            for identifier in source_scoped_identifiers
        ),
        qualified_identifier_match=identity != symbol.name
        and any(
            _matches_qualified_identity(identity, identifier)
            for identifier in source_scoped_identifiers
        ),
        semantic_terms=frozenset(_semantic_terms(local_terms)),
    )


def _select_symbol(
    matches: list[SymbolMatch],
    signals: IssueSignals,
    related_callers: dict[str, tuple[str, ...]],
) -> SymbolMatch | None:
    if not matches:
        return None

    functions = [match for match in matches if match.symbol.kind == "function"]

    directly_referenced = [
        match
        for match in functions
        if match.explicit_identifier_match or match.qualified_identifier_match
    ]
    if directly_referenced:
        return max(
            directly_referenced,
            key=lambda match: (
                match.qualified_identifier_match,
                match.explicit_identifier_match,
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
    matches_by_name = {
        match.symbol.name: match
        for match in functions
    }
    relation_scores = {
        called_name: sum(
            min(len(matches_by_name[caller].local_overlap), 4)
            for caller in callers
        )
        for called_name, callers in related_callers.items()
    }

    def semantic_score(
        match: SymbolMatch,
    ) -> tuple[int, float, float, bool, bool, int, int]:
        overlap = primary_terms & match.semantic_terms
        rarity = [1 / term_frequency[term] for term in overlap]
        return (
            relation_scores.get(match.symbol.name, 0),
            max(rarity, default=0),
            sum(rarity),
            not match.symbol.name.startswith("_"),
            match.qualified_component_match,
            -match.symbol.line,
            len(match.local_overlap),
        )

    if functions and max(semantic_score(match)[:3] for match in functions) > (
        0,
        0,
        0,
    ):
        return max(functions, key=semantic_score)

    fallback = max(
        matches,
        key=lambda match: (
            match.primary_identifier_match,
            match.qualified_identifier_match,
            match.exact_identifier_match,
            len(match.local_overlap),
            match.qualified_component_match,
            -match.symbol.line,
        ),
    )
    if not (
        fallback.primary_identifier_match
        or fallback.exact_identifier_match
        or fallback.overlap
    ):
        return None
    return fallback


def _related_symbol_callers(
    matches: list[SymbolMatch],
    symbol_calls: dict[str, list[str]],
) -> dict[str, tuple[str, ...]]:
    functions = {
        match.symbol.name: match
        for match in matches
        if match.symbol.kind == "function"
    }
    callers_by_target: dict[str, set[str]] = {}
    for caller_name, called_names in symbol_calls.items():
        caller = functions.get(caller_name)
        if caller is None or len(caller.local_overlap) < 2:
            continue
        for called_name in set(called_names) & functions.keys():
            if called_name != caller_name:
                callers_by_target.setdefault(called_name, set()).add(caller_name)
    return {
        called_name: tuple(sorted(callers))
        for called_name, callers in callers_by_target.items()
        if len(callers) >= 2
    }


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
                str(HISTORY_COMMIT_LIMIT),
                "--full-diff",
                "--format=%x1e%H",
                "--name-only",
                "HEAD",
                "--",
                *seed_paths,
            ],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if completed.returncode:
        return {}

    counts: Counter[str] = Counter()
    latest_commit: dict[str, str] = {}
    seed_set = set(seed_paths)
    for record in completed.stdout.split("\x1e"):
        lines = [line for line in record.splitlines() if line]
        if len(lines) < 2:
            continue
        commit, *changed_files = lines
        changed = set(changed_files)
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


def _graph_relations(
    repository_map: RepositoryMap,
    base_scores: dict[str, float],
    auxiliary_files: dict[str, bool],
    signals: IssueSignals,
) -> dict[str, list[tuple[float, str]]]:
    files_by_path = {file.path: file for file in repository_map.files}
    symbol_definitions: dict[str, set[str]] = {}
    for file in repository_map.files:
        if file.test_file:
            continue
        for symbol in file.symbols:
            symbol_definitions.setdefault(symbol.name, set()).add(file.path)

    seed_paths = _graph_seed_paths(base_scores, auxiliary_files)

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

    def add_relation(target: str, bonus: float, evidence: str) -> None:
        if target not in base_scores:
            return
        relations.setdefault(target, []).append((bonus, evidence))

    for seed_path in seed_paths:
        seed = files_by_path[seed_path]
        for imported_path in seed.local_imports:
            imported = files_by_path.get(imported_path)
            if imported is None or imported.test_file:
                continue
            called_imports = set(
                seed.local_import_symbols.get(imported_path, [])
            ).intersection(seed.calls)
            referenced_imports = set(
                seed.local_import_symbols.get(imported_path, [])
            ).intersection(seed.references)
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
                add_relation(
                    imported_path,
                    bonus,
                    "Related source calls imported symbols defined here: "
                    + ", ".join(sorted(called_imports)),
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

            for second_hop_path in imported.local_imports:
                second_hop = files_by_path.get(second_hop_path)
                if second_hop is None or second_hop.test_file:
                    continue
                called_second_hop_imports = set(
                    imported.local_import_symbols.get(second_hop_path, [])
                ).intersection(imported.calls)
                referenced_second_hop_imports = set(
                    imported.local_import_symbols.get(second_hop_path, [])
                ).intersection(imported.references)
                if called_second_hop_imports:
                    bonus = (
                        9.0
                        if any(
                            matches_primary_issue(symbol)
                            for symbol in called_second_hop_imports
                        )
                        else 5.0
                    )
                    add_relation(
                        second_hop_path,
                        bonus,
                        "Two-hop source relation calls imported symbols defined "
                        "here: "
                        + ", ".join(sorted(called_second_hop_imports)),
                    )
                elif referenced_second_hop_imports:
                    add_relation(
                        second_hop_path,
                        4.0,
                        "Two-hop source relation references imported symbols "
                        "defined here: "
                        + ", ".join(sorted(referenced_second_hop_imports)),
                    )

        if seed.test_file:
            continue
        strong_first_hops: list[tuple[str, str]] = []
        for called_symbol in seed.calls:
            if len(called_symbol) < GRAPH_DYNAMIC_CALL_MIN_LENGTH:
                continue
            targets = symbol_definitions.get(called_symbol, set()) - {seed_path}
            if not targets or len(targets) > 4:
                continue
            for target in targets:
                bonus = 9.0 if matches_primary_issue(called_symbol) else 2.0
                add_relation(
                    target,
                    bonus,
                    f"Related source calls {called_symbol}, defined here: {seed_path}",
                )
            if len(targets) == 1 and matches_specific_primary_issue(called_symbol):
                target = next(iter(targets))
                target_parts = {
                    Path(part).stem.lstrip("_").lower()
                    for part in Path(target).parts
                }
                if (
                    not auxiliary_files.get(target, True)
                    and not target_parts.intersection(
                        {
                            "abc",
                            "interface",
                            "interfaces",
                            "protocol",
                            "protocols",
                        }
                    )
                ):
                    strong_first_hops.append((called_symbol, target))

        for first_hop_symbol, first_hop_path in sorted(strong_first_hops):
            first_hop = files_by_path[first_hop_path]
            second_hop_calls = first_hop.symbol_calls.get(first_hop_symbol, [])
            for called_symbol in second_hop_calls:
                if len(called_symbol) < GRAPH_SECOND_HOP_CALL_MIN_LENGTH:
                    continue
                targets = symbol_definitions.get(called_symbol, set())
                if targets.intersection({seed_path, first_hop_path}):
                    continue
                if len(targets) != 1:
                    continue
                target = next(iter(targets))
                if auxiliary_files.get(target, True):
                    continue
                add_relation(
                    target,
                    9.0 if matches_primary_issue(called_symbol) else 8.0,
                    f"{GRAPH_STRONG_EXPANSION_PREFIX}{first_hop_symbol} reaches "
                    f"{called_symbol}, defined here: {first_hop_path}",
                )
    return relations


def locate_candidates(
    issue: IssueRecord, repository_map: RepositoryMap, limit: int = 20
) -> list[CandidateLocation]:
    signals = extract_issue_signals(issue)
    keywords = set(signals.terms)
    root = Path(repository_map.root).resolve()
    base_scores: dict[str, float] = {}
    auxiliary_files: dict[str, bool] = {}
    candidates: dict[str, CandidateLocation] = {}
    blame_lines: dict[str, str | None] = {}
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
        symbol_matches = [_match_symbol(symbol, signals) for symbol in file.symbols]
        related_symbol_callers = _related_symbol_callers(
            symbol_matches,
            file.symbol_calls,
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
        blame_match = _select_symbol(symbol_matches, signals, {})
        selected_match = _select_symbol(
            symbol_matches,
            signals,
            related_symbol_callers,
        )
        primary_symbol_match = (
            score_match.primary_identifier_match if score_match else False
        )
        exact_symbol = score_match.exact_identifier_match if score_match else False
        best_overlap = set(score_match.local_overlap) if score_match else set()
        blame_symbol = blame_match.symbol if blame_match else None
        selected_symbol = selected_match.symbol if selected_match else None
        selected_overlap = set(selected_match.overlap) if selected_match else set()
        selected_primary_match = (
            selected_match.primary_identifier_match if selected_match else False
        )
        selected_component_match = (
            selected_match.qualified_component_match if selected_match else False
        )
        selected_exact = (
            selected_match.exact_identifier_match
            or selected_match.qualified_identifier_match
            if selected_match
            else False
        )
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
        selected_symbol_label = (
            selected_symbol.qualified_name or selected_symbol.name
            if selected_symbol
            else None
        )
        if exact_path:
            evidence.append("Issue references this exact source path")
        if path_identifier_hits:
            evidence.append(
                "Path matches issue identifiers: "
                + ", ".join(sorted(path_identifier_hits))
            )
        if path_overlap:
            evidence.append(f"Path matches issue terms: {', '.join(sorted(path_overlap))}")
        if selected_symbol_label and selected_overlap:
            evidence.append(
                f"Symbol {selected_symbol_label} matches: "
                + ", ".join(sorted(selected_overlap))
            )
        if selected_symbol_label and selected_exact:
            evidence.append(f"Issue references symbol {selected_symbol_label}")
        if selected_symbol_label and selected_primary_match:
            evidence.append(
                f"Issue title strongly matches symbol {selected_symbol_label}"
            )
        if selected_symbol_label and selected_component_match:
            evidence.append(
                f"Issue references owning symbol {selected_symbol_label.rsplit('.', 1)[0]}"
            )
        related_callers = (
            related_symbol_callers.get(selected_symbol.name, ())
            if selected_symbol
            else ()
        )
        if selected_symbol and related_callers:
            evidence.append(
                "Issue-matching symbols call "
                f"{selected_symbol.name}: {', '.join(related_callers)}"
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
        base_scores[file.path] = raw_score
        auxiliary_files[file.path] = auxiliary_file
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

    graph_relations = _graph_relations(
        repository_map,
        base_scores,
        auxiliary_files,
        signals,
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
        if strong_relation and strong_relation not in displayed_relations:
            displayed_relations = [*displayed_relations[:1], strong_relation]
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
    base_shortlist = base_order[:limit]
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
            bonus
            for bonus, _ in sorted(
                set(relations),
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

    expansion_paths = sorted(
        expansion_candidates,
        key=lambda path: (
            not strong_expansion(path),
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
    retained_base = [
        *promoted_first_band,
        *deferred_paths,
        *reranked_base[first_band_size:],
    ][: max(0, limit - len(tail_expansions))]
    reranked_paths = [*retained_base, *tail_expansions]
    for path in reranked_paths:
        candidates[path].evidence.extend(
            evidence for _, evidence in blame_relations.get(path, [])[:1]
        )
    return [
        candidates[path]
        for path in reranked_paths
    ]


def investigate(issue: IssueRecord, repository_map: RepositoryMap) -> InvestigationReport:
    candidates = locate_candidates(issue, repository_map)
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
