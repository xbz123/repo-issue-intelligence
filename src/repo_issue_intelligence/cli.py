from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from .agent_store import AgentStore
from .agent_workflow import run_agent
from .config import Settings
from .duplicates import detect_duplicates
from .github_client import GitHubClient
from .investigator import investigate
from .models import IssueRecord, ReviewDecision
from .repository_index import build_repository_map, save_repository_map
from .service import rank_issues

app = typer.Typer(no_args_is_help=True)
console = Console()


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
    output: Path = Path("reports/agent-run.json"),
) -> None:
    """Run the synchronous LangGraph workflow up to human review."""
    database = database or Settings().agent_db_path
    try:
        run = run_agent(_load(issues_file), repo, top_k, AgentStore(database))
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
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
