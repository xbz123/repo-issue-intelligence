from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from .agent_evaluation import run_agent_analysis_evaluation, save_agent_analysis_run
from .agent_store import AgentStore
from .agent_workflow import run_agent
from .benchmark import (
    BenchmarkTier,
    BenchmarkVariant,
    load_manifest,
    run_benchmark,
    save_benchmark_run,
)
from .benchmark_discovery import (
    CandidateStatus,
    curate_benchmark_expansion,
    discover_candidates,
    inspect_candidate,
    load_candidate_selection,
    load_candidate_sources,
    save_benchmark_candidate,
    save_candidate_catalog,
    save_curated_expansion,
)
from .config import Settings
from .duplicates import detect_duplicates
from .github_client import GitHubClient
from .investigator import investigate
from .llm_client import (
    OPENCODE_ANALYSIS_TIMEOUT_SECONDS,
    OPENCODE_RERANK_TIMEOUT_SECONDS,
    IssueAnalyzer,
    OpenCodeIssueAnalyzer,
)
from .models import IssueRecord, ReviewDecision
from .repository_index import build_repository_map, save_repository_map
from .service import rank_issues

app = typer.Typer(no_args_is_help=True)
console = Console()


def _build_issue_analyzer(
    settings: Settings,
    temperature: float | None = None,
    seed: int | None = None,
) -> IssueAnalyzer:
    if settings.opencode_api_key is None:
        raise typer.BadParameter("OPENCODE_API_KEY is required when --llm is enabled")
    options = {
        "max_output_tokens": settings.opencode_max_output_tokens,
        "timeout_seconds": settings.opencode_timeout_seconds,
    }
    if temperature is not None:
        options["temperature"] = temperature
    if seed is not None:
        options["seed"] = seed
    return OpenCodeIssueAnalyzer(
        api_key=settings.opencode_api_key.get_secret_value(),
        **options,
    )


def _build_benchmark_reranker(
    settings: Settings,
    temperature: float,
    seed: int,
) -> OpenCodeIssueAnalyzer:
    if settings.opencode_api_key is None:
        raise typer.BadParameter("OPENCODE_API_KEY is required for the hybrid benchmark")
    return OpenCodeIssueAnalyzer(
        api_key=settings.opencode_api_key.get_secret_value(),
        max_output_tokens=settings.opencode_max_output_tokens,
        timeout_seconds=max(
            settings.opencode_timeout_seconds,
            OPENCODE_RERANK_TIMEOUT_SECONDS,
        ),
        temperature=temperature,
        seed=seed,
    )


def _build_analysis_evaluator(
    settings: Settings,
    temperature: float,
    seed: int,
    omit_max_tokens: bool = False,
) -> OpenCodeIssueAnalyzer:
    if settings.opencode_api_key is None:
        raise typer.BadParameter("OPENCODE_API_KEY is required for Agent evaluation")
    return OpenCodeIssueAnalyzer(
        api_key=settings.opencode_api_key.get_secret_value(),
        max_output_tokens=(
            None if omit_max_tokens else settings.opencode_max_output_tokens
        ),
        timeout_seconds=max(
            settings.opencode_timeout_seconds,
            OPENCODE_ANALYSIS_TIMEOUT_SECONDS,
        ),
        temperature=temperature,
        seed=seed,
    )


def _load(path: Path) -> list[IssueRecord]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise typer.BadParameter("Issue input must be a JSON array")
    return [IssueRecord.model_validate(item) for item in payload]


@app.command()
def sync(repository: str, output: Path = Path("data/issues.json"), limit: int = 100) -> None:
    """Synchronize open issues from a GitHub repository."""
    client = GitHubClient(Settings().github_token)
    try:
        issues = client.fetch_open_issues(repository, limit)
    finally:
        client.close()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps([item.model_dump(mode="json") for item in issues], indent=2),
        encoding="utf-8",
    )
    console.print(f"Saved {len(issues)} issue(s) to {output}")


@app.command()
def rank(issues_file: Path, output: Path = Path("data/ranked.json")) -> None:
    """Rank issues using explainable severity, urgency, and priority signals."""
    results = rank_issues(_load(issues_file))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps([result.model_dump(mode="json") for result in results], indent=2),
        encoding="utf-8",
    )
    table = Table("Issue", "Priority", "Score", "Severity")
    for result in results[:20]:
        table.add_row(
            f"#{result.issue_number}",
            result.priority,
            str(result.priority_score),
            result.severity,
        )
    console.print(table)


@app.command("duplicates")
def duplicates_command(issues_file: Path, threshold: float = 0.55) -> None:
    """Find likely duplicate issue pairs."""
    console.print_json(
        data=[match.model_dump() for match in detect_duplicates(_load(issues_file), threshold)]
    )


@app.command()
def index(repository_path: Path, output: Path = Path("data/repository-map.json")) -> None:
    """Build a deterministic repository and Python-symbol index."""
    repository_map = build_repository_map(repository_path)
    save_repository_map(repository_map, output)
    console.print(f"Indexed {len(repository_map.files)} files")


@app.command("investigate-issue")
def investigate_issue(
    issues_file: Path,
    issue: Annotated[int, typer.Option("--issue", help="Issue number to investigate.")],
    repo: Annotated[Path, typer.Option("--repo", help="Repository path to inspect.")],
    output: Path = Path("reports/investigation.json"),
) -> None:
    """Generate evidence-backed candidate locations and a reproduction plan."""
    record = next((item for item in _load(issues_file) if item.number == issue), None)
    if record is None:
        raise typer.BadParameter(f"Issue #{issue} was not found")
    report = investigate(record, build_repository_map(repo))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    console.print(f"Saved investigation report to {output}")


@app.command("agent-run")
def agent_run_command(
    issues_file: Path,
    repo: Annotated[Path, typer.Option("--repo", help="Repository path to inspect.")],
    top_k: Annotated[
        int,
        typer.Option("--top-k", min=1, help="Number of ranked issues to investigate."),
    ] = 1,
    database: Annotated[
        Path | None,
        typer.Option("--database", help="SQLite database for Agent run state."),
    ] = None,
    llm: Annotated[
        bool,
        typer.Option("--llm", help="Enable evidence-grounded model analysis."),
    ] = False,
    output: Path = Path("reports/agent-run.json"),
) -> None:
    """Run the synchronous LangGraph workflow up to human review."""
    settings = Settings()
    database = database or settings.agent_db_path
    analyzer = None
    if llm:
        analyzer = _build_issue_analyzer(settings)
    try:
        run = run_agent(
            _load(issues_file),
            repo,
            top_k,
            AgentStore(database),
            llm_analyzer=analyzer,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    finally:
        if analyzer is not None:
            analyzer.close()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(run.model_dump_json(indent=2), encoding="utf-8")
    console.print(
        f"Run {run.run_id} is {run.status}; "
        f"selected issue(s): {', '.join(f'#{number}' for number in run.selected_issue_numbers)}"
    )
    console.print(f"Saved Agent run to {output}")


@app.command("agent-show")
def agent_show_command(
    run_id: str,
    database: Annotated[
        Path | None,
        typer.Option("--database", help="SQLite database for Agent run state."),
    ] = None,
) -> None:
    """Show a persisted Agent run."""
    database = database or Settings().agent_db_path
    run = AgentStore(database).get_run(run_id)
    if run is None:
        raise typer.BadParameter(f"Run {run_id} was not found")
    console.print_json(run.model_dump_json())


@app.command("agent-review")
def agent_review_command(
    run_id: str,
    decision: Annotated[
        ReviewDecision,
        typer.Option("--decision", help="Approve or reject the generated investigation."),
    ],
    notes: Annotated[str | None, typer.Option("--notes", help="Optional review notes.")] = None,
    database: Annotated[
        Path | None,
        typer.Option("--database", help="SQLite database for Agent run state."),
    ] = None,
) -> None:
    """Record the human review decision without executing generated commands."""
    database = database or Settings().agent_db_path
    store = AgentStore(database)
    try:
        run = store.review(run_id, decision, notes)
    except KeyError as error:
        raise typer.BadParameter(f"Run {run_id} was not found") from error
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    console.print(f"Run {run.run_id} is now {run.status}")


@app.command("agent-evaluate")
def agent_evaluate_command(
    manifest: Path,
    case_id: Annotated[
        list[str] | None,
        typer.Option("--case-id", help="Evaluate only this frozen case; repeat as needed."),
    ] = None,
    workspace: Path = Path("benchmarks/workspaces"),
    output: Path = Path("benchmarks/results/agent-analysis-latest.json"),
    llm_delay_seconds: Annotated[
        float,
        typer.Option("--llm-delay-seconds", min=0, help="Delay between provider cases."),
    ] = 0,
    temperature: Annotated[
        float,
        typer.Option("--temperature", min=0, max=2),
    ] = 0.1,
    seed: Annotated[int, typer.Option("--seed")] = 1337,
    omit_max_tokens: Annotated[
        bool,
        typer.Option(
            "--omit-max-tokens",
            help="Diagnostic: omit max_tokens and use the provider's server default.",
        ),
    ] = False,
) -> None:
    """Evaluate full DeepSeek analysis through the persisted Agent graph."""
    settings = Settings()
    analyzer = _build_analysis_evaluator(
        settings,
        temperature,
        seed,
        omit_max_tokens=omit_max_tokens,
    )
    try:
        run = run_agent_analysis_evaluation(
            load_manifest(manifest),
            workspace,
            analyzer,
            case_ids=set(case_id) if case_id else None,
            llm_delay_seconds=llm_delay_seconds,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    finally:
        analyzer.close()
    save_agent_analysis_run(run, output)
    console.print(
        f"Agent analysis: {run.overall.analysis_successes}/{run.overall.cases} valid; "
        f"first-attempt success={run.overall.first_attempt_success_rate:.4f}; "
        f"persisted={run.overall.persistence_verified}/{run.overall.cases}"
    )
    console.print(f"Saved Agent analysis results to {output}")
    if run.overall.failures or run.overall.skipped_no_evidence:
        raise typer.Exit(code=1)


@app.command()
def benchmark(
    manifest: Path,
    variant: Annotated[
        BenchmarkVariant,
        typer.Option(
            "--variant",
            help="Deterministic or DeepSeek V4 Flash hybrid variant.",
        ),
    ] = BenchmarkVariant.DETERMINISTIC,
    case_id: Annotated[
        list[str] | None,
        typer.Option("--case-id", help="Run only the selected case ID; repeat as needed."),
    ] = None,
    workspace: Path = Path("benchmarks/workspaces"),
    output: Path = Path("benchmarks/results/latest.json"),
    llm_delay_seconds: Annotated[
        float,
        typer.Option(
            "--llm-delay-seconds",
            min=0,
            help="Delay between hybrid cases and failed LLM retries.",
        ),
    ] = 0,
    temperature: Annotated[
        float,
        typer.Option(
            "--temperature",
            min=0,
            max=2,
            help="Sampling temperature for reproducible model comparisons.",
        ),
    ] = 0.1,
    seed: Annotated[
        int,
        typer.Option("--seed", help="Best-effort deterministic sampling seed."),
    ] = 1337,
) -> None:
    """Evaluate file localization against historical Issue/Fix-PR pairs."""
    settings = Settings()
    analyzer = None
    if variant is not BenchmarkVariant.DETERMINISTIC:
        analyzer = _build_benchmark_reranker(settings, temperature, seed)
    try:
        run = run_benchmark(
            load_manifest(manifest),
            workspace,
            variant,
            analyzer,
            case_ids=set(case_id) if case_id else None,
            llm_delay_seconds=llm_delay_seconds,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    finally:
        if analyzer is not None:
            analyzer.close()
    save_benchmark_run(run, output)
    console.print(
        f"{run.variant} benchmark: {run.overall.completed}/{run.overall.cases} completed; "
        f"Recall@1={run.overall.file_recall_at_1:.4f}, "
        f"Recall@5={run.overall.file_recall_at_5:.4f}, "
        f"Recall@10={run.overall.file_recall_at_10:.4f}, "
        f"Recall@20={run.overall.file_recall_at_20:.4f}, "
        f"MRR={run.overall.mean_reciprocal_rank:.4f}"
    )
    if run.overall.symbol_cases:
        console.print(
            f"Symbol benchmark: {run.overall.symbol_cases} labeled cases; "
            f"Recall@1={run.overall.symbol_recall_at_1:.4f}, "
            f"Recall@5={run.overall.symbol_recall_at_5:.4f}, "
            f"Recall@10={run.overall.symbol_recall_at_10:.4f}, "
            f"Recall@20={run.overall.symbol_recall_at_20:.4f}, "
            f"MRR={run.overall.mean_symbol_reciprocal_rank:.4f}"
        )
    console.print(f"Saved benchmark results to {output}")
    if run.overall.failed:
        raise typer.Exit(code=1)


def _parse_tier_assignments(values: list[str] | None) -> dict[str, BenchmarkTier]:
    assignments: dict[str, BenchmarkTier] = {}
    for value in values or []:
        repository, separator, tier = value.partition("=")
        if not separator or not repository or not tier:
            raise typer.BadParameter("--tier must use repository=tier")
        try:
            assignments[repository] = BenchmarkTier(tier)
        except ValueError as error:
            choices = ", ".join(item.value for item in BenchmarkTier)
            raise typer.BadParameter(f"tier must be one of: {choices}") from error
    return assignments


@app.command("benchmark-discover")
def benchmark_discover(
    repositories: Annotated[
        list[str],
        typer.Argument(help="GitHub repositories in owner/name form."),
    ],
    output: Path = Path("benchmarks/candidates/latest.json"),
    target_per_repository: Annotated[
        int,
        typer.Option(
            "--target-per-repository",
            min=1,
            help="Stop after this many reviewable candidates per repository.",
        ),
    ] = 5,
    scan_limit_per_repository: Annotated[
        int,
        typer.Option(
            "--scan-limit-per-repository",
            min=1,
            help="Maximum linked closed Issues inspected per repository.",
        ),
    ] = 50,
    max_source_files: Annotated[
        int,
        typer.Option(
            "--max-source-files",
            min=1,
            help="Maximum production source files in a reviewable fix.",
        ),
    ] = 5,
    tier: Annotated[
        list[str] | None,
        typer.Option(
            "--tier",
            help="Suggested benchmark tier as repository=tier; repeat as needed.",
        ),
    ] = None,
) -> None:
    """Discover Issue/Fix-PR pairs for manual benchmark audit."""
    client = GitHubClient(Settings().github_token)
    try:
        catalog = discover_candidates(
            client,
            repositories,
            target_per_repository=target_per_repository,
            scan_limit_per_repository=scan_limit_per_repository,
            max_source_files=max_source_files,
            suggested_tiers=_parse_tier_assignments(tier),
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    finally:
        client.close()
    save_candidate_catalog(catalog, output)
    reviewable = sum(
        candidate.status is CandidateStatus.NEEDS_REVIEW
        for candidate in catalog.candidates
    )
    rejected = sum(
        candidate.status is CandidateStatus.REJECTED
        for candidate in catalog.candidates
    )
    console.print(
        f"Discovered {len(catalog.candidates)} candidate(s): "
        f"{reviewable} need review, {rejected} rejected by blocking checks."
    )
    console.print(f"Saved benchmark candidates to {output}")


@app.command("benchmark-audit")
def benchmark_audit(
    repository: str,
    issue_number: Annotated[int, typer.Argument(min=1)],
    fix_pr_number: Annotated[int, typer.Argument(min=1)],
    output: Path = Path("benchmarks/candidates/audit.json"),
    max_source_files: Annotated[
        int,
        typer.Option("--max-source-files", min=1),
    ] = 5,
    tier: Annotated[
        BenchmarkTier | None,
        typer.Option("--tier", help="Suggested benchmark tier after manual review."),
    ] = None,
) -> None:
    """Audit one explicit Issue/Fix-PR pair without accepting it."""
    client = GitHubClient(Settings().github_token)
    try:
        candidate = inspect_candidate(
            client,
            repository,
            issue_number,
            fix_pr_number,
            max_source_files=max_source_files,
            suggested_tier=tier,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    finally:
        client.close()
    save_benchmark_candidate(candidate, output)
    failed_blocking = [
        check.code
        for check in candidate.audit_checks
        if check.blocking and not check.passed
    ]
    console.print(
        f"Candidate {candidate.id}: {candidate.status}; "
        f"expected_files={len(candidate.expected_files)}; "
        f"blocking_failures={', '.join(failed_blocking) or 'none'}."
    )
    console.print(f"Saved candidate audit to {output}")


@app.command("benchmark-curate")
def benchmark_curate(
    base_manifest: Path,
    selection: Path,
    candidate_sources: Annotated[
        list[Path],
        typer.Argument(help="Candidate catalogs or individual candidate audits."),
    ],
    catalog_output: Path = Path("benchmarks/candidates-v0.4.json"),
    manifest_output: Path = Path("benchmarks/cases-v0.4.json"),
) -> None:
    """Accept manually selected candidates into a new frozen manifest."""
    try:
        curated_catalog, expanded_manifest = curate_benchmark_expansion(
            load_manifest(base_manifest),
            load_candidate_sources(candidate_sources),
            load_candidate_selection(selection),
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    save_curated_expansion(
        curated_catalog,
        expanded_manifest,
        catalog_output=catalog_output,
        manifest_output=manifest_output,
    )
    console.print(
        f"Accepted {len(curated_catalog.candidates)} candidate(s); "
        f"expanded manifest to {len(expanded_manifest.cases)} case(s), "
        f"version {expanded_manifest.version}."
    )
    console.print(f"Saved curated audit to {catalog_output}")
    console.print(f"Saved expanded benchmark manifest to {manifest_output}")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    """Run the FastAPI service."""
    import uvicorn

    uvicorn.run(
        "repo_issue_intelligence.api:app",
        host=host,
        port=port,
        reload=reload,
    )


if __name__ == "__main__":
    app()
