from __future__ import annotations

import json
from enum import StrEnum
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
from .benchmark_analysis import audit_candidate_pool, save_candidate_pool_audit
from .benchmark_discovery import (
    CandidateStatus,
    build_candidate_review_queue,
    curate_benchmark_expansion,
    discover_candidates,
    inspect_candidate,
    load_candidate_selection,
    load_candidate_sources,
    reviewed_rejection_ids,
    save_benchmark_candidate,
    save_candidate_catalog,
    save_candidate_review_queue,
    save_curated_expansion,
)
from .codex_cli import CodexCLIIssueAnalyzer, CodexCLIReranker
from .config import Settings
from .duplicates import detect_duplicates
from .github_client import GitHubClient
from .investigator import investigate
from .llm_client import IssueAnalyzer, OpenAICompatibleIssueAnalyzer
from .models import IssueRecord, ReviewDecision
from .repository_index import build_repository_map, save_repository_map
from .service import rank_issues

app = typer.Typer(no_args_is_help=True)
console = Console()
ANALYSIS_EVALUATION_TIMEOUT_SECONDS = 180.0


class LLMBackend(StrEnum):
    API = "api"
    CODEX_CLI = "codex-cli"


def _llm_backend(settings: Settings, backend: LLMBackend | None) -> LLMBackend:
    try:
        return backend or LLMBackend(settings.llm_backend)
    except ValueError as error:
        raise typer.BadParameter("LLM_BACKEND must be api or codex-cli") from error


def _build_api_analyzer(
    settings: Settings,
    *,
    model: str | None = None,
    base_url: str | None = None,
    provider: str | None = None,
    temperature: float | None = None,
    seed: int | None = None,
    omit_max_tokens: bool = False,
    timeout_seconds: float | None = None,
) -> OpenAICompatibleIssueAnalyzer:
    if settings.llm_api_key is None:
        raise typer.BadParameter(
            "LLM_API_KEY (or legacy OPENCODE_API_KEY) is required for the API backend"
        )
    return OpenAICompatibleIssueAnalyzer(
        api_key=settings.llm_api_key.get_secret_value(),
        base_url=settings.llm_api_base_url if base_url is None else base_url,
        model=settings.llm_model if model is None else model,
        provider=settings.llm_api_provider if provider is None else provider,
        max_output_tokens=(
            None if omit_max_tokens else settings.llm_max_output_tokens
        ),
        timeout_seconds=timeout_seconds or settings.llm_timeout_seconds,
        temperature=(settings.llm_temperature if temperature is None else temperature),
        seed=seed,
        reasoning_effort=settings.llm_reasoning_effort,
        response_format_json=settings.llm_response_format_json,
    )


def _validate_codex_options(
    base_url: str | None,
    provider: str | None,
    temperature: float | None = None,
    seed: int | None = None,
) -> None:
    if base_url is not None:
        raise typer.BadParameter("--llm-base-url is only valid for the API backend")
    if provider is not None:
        raise typer.BadParameter("--llm-provider is only valid for the API backend")
    if temperature not in {None, 0.1}:
        raise typer.BadParameter("--temperature is not supported by codex-cli")
    if seed not in {None, 1337}:
        raise typer.BadParameter("--seed is not supported by codex-cli")


def _build_issue_analyzer(
    settings: Settings,
    backend: LLMBackend | None = None,
    model: str | None = None,
    base_url: str | None = None,
    provider: str | None = None,
    fast: bool = False,
    temperature: float | None = None,
    seed: int | None = None,
    omit_max_tokens: bool = False,
    timeout_seconds: float | None = None,
) -> IssueAnalyzer:
    selected_backend = _llm_backend(settings, backend)
    if selected_backend is LLMBackend.API:
        if fast:
            raise typer.BadParameter("--llm-fast is only valid for codex-cli")
        return _build_api_analyzer(
            settings,
            model=model,
            base_url=base_url,
            provider=provider,
            temperature=temperature,
            seed=seed,
            omit_max_tokens=omit_max_tokens,
            timeout_seconds=timeout_seconds,
        )
    _validate_codex_options(base_url, provider, temperature, seed)
    if omit_max_tokens:
        raise typer.BadParameter("--omit-max-tokens is only valid for the API backend")
    return CodexCLIIssueAnalyzer(
        executable=settings.codex_cli_executable,
        model=settings.codex_cli_model if model is None else model,
        timeout_seconds=timeout_seconds or settings.codex_cli_timeout_seconds,
        reasoning_effort=settings.codex_cli_reasoning_effort,
        service_tier="fast" if fast else None,
    )


def _build_benchmark_reranker(
    settings: Settings | None = None,
    backend: LLMBackend | None = None,
    model: str | None = None,
    base_url: str | None = None,
    provider: str | None = None,
    fast: bool = False,
) -> CodexCLIReranker | OpenAICompatibleIssueAnalyzer:
    settings = settings or Settings()
    selected_backend = backend or LLMBackend.CODEX_CLI
    if selected_backend is LLMBackend.API:
        if fast:
            raise typer.BadParameter("--llm-fast is only valid for codex-cli")
        return _build_api_analyzer(
            settings,
            model=model,
            base_url=base_url,
            provider=provider,
        )
    _validate_codex_options(base_url, provider)
    return CodexCLIReranker(
        executable=settings.codex_cli_executable,
        model=settings.codex_cli_model if model is None else model,
        timeout_seconds=settings.codex_cli_timeout_seconds,
        reasoning_effort=settings.codex_cli_reasoning_effort,
        service_tier="fast" if fast else None,
    )


def _build_analysis_evaluator(
    settings: Settings,
    temperature: float,
    seed: int,
    backend: LLMBackend | None = None,
    model: str | None = None,
    base_url: str | None = None,
    provider: str | None = None,
    fast: bool = False,
    omit_max_tokens: bool = False,
) -> IssueAnalyzer:
    return _build_issue_analyzer(
        settings,
        backend=backend,
        model=model,
        base_url=base_url,
        provider=provider,
        fast=fast,
        temperature=temperature,
        seed=seed,
        omit_max_tokens=omit_max_tokens,
        timeout_seconds=max(
            settings.llm_timeout_seconds
            if _llm_backend(settings, backend) is LLMBackend.API
            else settings.codex_cli_timeout_seconds,
            ANALYSIS_EVALUATION_TIMEOUT_SECONDS,
        ),
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
    llm_backend: Annotated[
        LLMBackend | None,
        typer.Option("--llm-backend", help="Use an API or isolated Codex CLI."),
    ] = None,
    llm_model: Annotated[
        str | None,
        typer.Option("--llm-model", help="Override the selected backend model."),
    ] = None,
    llm_base_url: Annotated[
        str | None,
        typer.Option("--llm-base-url", help="OpenAI-compatible API base URL."),
    ] = None,
    llm_provider: Annotated[
        str | None,
        typer.Option("--llm-provider", help="Provider name recorded for API runs."),
    ] = None,
    llm_fast: Annotated[
        bool,
        typer.Option("--llm-fast", help="Use the Codex CLI Fast service tier."),
    ] = False,
    output: Path = Path("reports/agent-run.json"),
) -> None:
    """Run the synchronous LangGraph workflow up to human review."""
    settings = Settings()
    database = database or settings.agent_db_path
    analyzer = None
    if llm:
        analyzer = _build_issue_analyzer(
            settings,
            backend=llm_backend,
            model=llm_model,
            base_url=llm_base_url,
            provider=llm_provider,
            fast=llm_fast,
        )
    try:
        run = run_agent(
            _load(issues_file),
            repo,
            top_k,
            AgentStore(database),
            llm_analyzer=analyzer,
            max_evidence_chars=settings.llm_max_evidence_chars,
            max_evidence_lines=settings.llm_max_lines_per_evidence,
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
        typer.Option("--temperature", min=0, max=2, help="API backend temperature."),
    ] = 0.1,
    seed: Annotated[int, typer.Option("--seed", help="API backend sampling seed.")] = 1337,
    omit_max_tokens: Annotated[
        bool,
        typer.Option(
            "--omit-max-tokens",
            help="Diagnostic: omit max_tokens and use the provider's server default.",
        ),
    ] = False,
    llm_backend: Annotated[
        LLMBackend | None,
        typer.Option("--llm-backend", help="Use an API or isolated Codex CLI."),
    ] = None,
    llm_model: Annotated[
        str | None,
        typer.Option("--llm-model", help="Override the selected backend model."),
    ] = None,
    llm_base_url: Annotated[
        str | None,
        typer.Option("--llm-base-url", help="OpenAI-compatible API base URL."),
    ] = None,
    llm_provider: Annotated[
        str | None,
        typer.Option("--llm-provider", help="Provider name recorded for API runs."),
    ] = None,
    llm_fast: Annotated[
        bool,
        typer.Option("--llm-fast", help="Use the Codex CLI Fast service tier."),
    ] = False,
) -> None:
    """Evaluate full model analysis through the persisted Agent graph."""
    settings = Settings()
    analyzer = _build_analysis_evaluator(
        settings,
        temperature,
        seed,
        backend=llm_backend,
        model=llm_model,
        base_url=llm_base_url,
        provider=llm_provider,
        fast=llm_fast,
        omit_max_tokens=omit_max_tokens,
    )
    try:
        run = run_agent_analysis_evaluation(
            load_manifest(manifest),
            workspace,
            analyzer,
            case_ids=set(case_id) if case_id else None,
            max_evidence_chars=settings.llm_max_evidence_chars,
            max_lines_per_evidence=settings.llm_max_lines_per_evidence,
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
    if run.overall.hypothesis_quality_cases:
        conditional_rate = (
            run.overall.hypothesis_hit_rate_when_expected_evidence_available
        )
        conditional_text = (
            f"{conditional_rate:.4f}" if conditional_rate is not None else "n/a"
        )
        console.print(
            "File grounding: "
            f"evidence-hit={run.overall.expected_file_evidence_hit_rate:.4f}; "
            "overall-hypothesis-hit="
            f"{run.overall.overall_hypothesis_expected_file_hit_rate:.4f}; "
            f"valid-hypothesis-hit={run.overall.hypothesis_expected_file_hit_rate:.4f}; "
            f"conditional-hypothesis-hit={conditional_text}"
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
            help="Deterministic or model-reranked hybrid variant.",
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
    llm_backend: Annotated[
        LLMBackend | None,
        typer.Option(
            "--llm-backend",
            help="Hybrid backend; defaults to isolated Codex CLI.",
        ),
    ] = None,
    llm_model: Annotated[
        str | None,
        typer.Option("--llm-model", help="Override the selected backend model."),
    ] = None,
    llm_base_url: Annotated[
        str | None,
        typer.Option("--llm-base-url", help="OpenAI-compatible API base URL."),
    ] = None,
    llm_provider: Annotated[
        str | None,
        typer.Option("--llm-provider", help="Provider name recorded for API runs."),
    ] = None,
    llm_fast: Annotated[
        bool,
        typer.Option("--llm-fast", help="Use the Codex CLI Fast service tier."),
    ] = False,
) -> None:
    """Evaluate file localization against historical Issue/Fix-PR pairs."""
    settings = Settings()
    analyzer = None
    if variant is not BenchmarkVariant.DETERMINISTIC:
        analyzer = _build_benchmark_reranker(
            settings,
            backend=llm_backend,
            model=llm_model,
            base_url=llm_base_url,
            provider=llm_provider,
            fast=llm_fast,
        )
    try:
        run = run_benchmark(
            load_manifest(manifest),
            workspace,
            variant,
            analyzer,
            case_ids=set(case_id) if case_id else None,
            max_evidence_chars=settings.llm_max_evidence_chars,
            max_lines_per_evidence=settings.llm_max_lines_per_evidence,
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
    cache_hits = sum(
        result.repository_map_cache_hit is True for result in run.results
    )
    cache_misses = sum(
        result.repository_map_cache_hit is False for result in run.results
    )
    console.print(
        f"Repository-map cache: {cache_hits} hit(s), {cache_misses} miss(es)"
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


@app.command("benchmark-miss-audit")
def benchmark_miss_audit(
    manifest: Path,
    workspace: Path = Path("benchmarks/workspaces"),
    output: Path = Path("benchmarks/results/candidate-pool-miss-audit-latest.json"),
) -> None:
    """Audit reviewed targets missing from the deterministic Top-40 pool."""
    audit = audit_candidate_pool(load_manifest(manifest), workspace)
    save_candidate_pool_audit(audit, output)
    console.print(
        f"Candidate pool: {audit.candidate_pool_matched_targets}/"
        f"{audit.production_targets} target(s) matched; "
        f"{audit.candidate_pool_missing_targets} missing"
    )
    console.print(
        f"Repository-map cache: {audit.repository_map_cache_hits} hit(s), "
        f"{audit.repository_map_cache_misses} miss(es)"
    )
    console.print(f"Saved candidate-pool miss audit to {output}")


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
    base_manifest: Annotated[
        Path | None,
        typer.Option(
            "--base-manifest",
            help="Skip Issues and fix PRs already present in this manifest.",
        ),
    ] = None,
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
        existing = load_manifest(base_manifest) if base_manifest else None
        catalog = discover_candidates(
            client,
            repositories,
            target_per_repository=target_per_repository,
            scan_limit_per_repository=scan_limit_per_repository,
            max_source_files=max_source_files,
            suggested_tiers=_parse_tier_assignments(tier),
            excluded_issue_keys=frozenset(
                (case.repository.lower(), case.issue_number)
                for case in existing.cases
            )
            if existing
            else frozenset(),
            excluded_pull_request_keys=frozenset(
                (case.repository.lower(), case.fix_pr_number)
                for case in existing.cases
            )
            if existing
            else frozenset(),
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


@app.command("benchmark-plan")
def benchmark_plan(
    base_manifest: Path,
    candidate_sources: Annotated[
        list[Path],
        typer.Argument(help="Candidate catalogs or individual candidate audits."),
    ],
    output: Path = Path("benchmarks/expansion-v200-review-queue.json"),
    target_total_cases: Annotated[
        int,
        typer.Option("--target-total-cases", min=1),
    ] = 200,
    reserve_cases: Annotated[
        int,
        typer.Option("--reserve-cases", min=0),
    ] = 30,
    max_primary_per_repository: Annotated[
        int,
        typer.Option("--max-primary-per-repository", min=1),
    ] = 5,
    target_multi_file_share: Annotated[
        float,
        typer.Option("--target-multi-file-share", min=0, max=1),
    ] = 0.30,
    default_tier: Annotated[
        BenchmarkTier,
        typer.Option("--default-tier"),
    ] = BenchmarkTier.GENERALIZATION,
    review_decisions: Annotated[
        list[Path] | None,
        typer.Option(
            "--review-decisions",
            help="Selection manifests whose reviewed rejections must be excluded.",
        ),
    ] = None,
) -> None:
    """Build a deterministic manual-review queue without accepting candidates."""
    try:
        excluded_candidate_ids = reviewed_rejection_ids(
            [load_candidate_selection(path) for path in review_decisions or []]
        )
        queue = build_candidate_review_queue(
            load_manifest(base_manifest),
            load_candidate_sources(candidate_sources),
            target_total_cases=target_total_cases,
            reserve_cases=reserve_cases,
            max_primary_per_repository=max_primary_per_repository,
            target_multi_file_share=target_multi_file_share,
            default_tier=default_tier,
            excluded_candidate_ids=excluded_candidate_ids,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    save_candidate_review_queue(queue, output)
    console.print(
        f"Queued {queue.requested_new_cases} primary and {queue.reserve_cases} "
        f"reserve candidate(s) from {queue.primary_repositories} repositories; "
        f"primary multi-file cases={queue.primary_multi_file_cases}."
    )
    console.print("All entries remain needs_review until explicit manual curation.")
    console.print(f"Saved benchmark review queue to {output}")


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
