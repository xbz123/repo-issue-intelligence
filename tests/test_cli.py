import json
from datetime import UTC, datetime
from pathlib import Path

from click import unstyle
from typer.testing import CliRunner

from repo_issue_intelligence.benchmark import BenchmarkTier
from repo_issue_intelligence.benchmark_discovery import CandidateCatalog
from repo_issue_intelligence.cli import _build_benchmark_reranker, app
from repo_issue_intelligence.config import Settings
from repo_issue_intelligence.models import LLMAnalysis, LLMAnalysisResult

runner = CliRunner()


def test_investigate_issue_accepts_documented_options(tmp_path: Path) -> None:
    output = tmp_path / "investigation.json"

    result = runner.invoke(
        app,
        [
            "investigate-issue",
            "examples/issues.json",
            "--issue",
            "184",
            "--repo",
            ".",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert output.exists()


def test_agent_run_and_review_commands(tmp_path: Path) -> None:
    database = tmp_path / "agent.sqlite3"
    output = tmp_path / "agent-run.json"

    run_result = runner.invoke(
        app,
        [
            "agent-run",
            "examples/issues.json",
            "--repo",
            "examples/demo_repository",
            "--top-k",
            "1",
            "--database",
            str(database),
            "--output",
            str(output),
        ],
    )

    assert run_result.exit_code == 0, run_result.output
    run_payload = json.loads(output.read_text(encoding="utf-8"))
    run_id = run_payload["run_id"]
    assert run_payload["investigations"][0]["candidates"][0]["file"] == "auth_service.py"

    review_result = runner.invoke(
        app,
        [
            "agent-review",
            run_id,
            "--decision",
            "approved",
            "--notes",
            "CLI review",
            "--database",
            str(database),
        ],
    )

    assert review_result.exit_code == 0, review_result.output


def test_agent_run_llm_requires_api_key(tmp_path: Path, monkeypatch) -> None:
    issues_file = Path("examples/issues.json").resolve()
    repository = Path("examples/demo_repository").resolve()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)

    result = runner.invoke(
        app,
        [
            "agent-run",
            str(issues_file),
            "--repo",
            str(repository),
            "--llm",
            "--database",
            str(tmp_path / "agent.sqlite3"),
        ],
    )

    assert result.exit_code == 2
    assert "OPENCODE_API_KEY is required" in result.output


def test_hybrid_benchmark_requires_opencode_key(tmp_path: Path, monkeypatch) -> None:
    manifest = Path("benchmarks/cases.json").resolve()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)

    result = runner.invoke(
        app,
        [
            "benchmark",
            str(manifest),
            "--variant",
            "hybrid",
        ],
    )

    assert result.exit_code == 2
    assert "OPENCODE_API_KEY is required" in result.output


def test_benchmark_does_not_accept_provider_or_model_overrides() -> None:
    for option, value in (("--provider", "other"), ("--model", "other-model")):
        result = runner.invoke(
            app,
            [
                "benchmark",
                "benchmarks/cases.json",
                "--variant",
                "hybrid",
                option,
                value,
            ],
        )

        output = unstyle(result.output)
        assert result.exit_code == 2
        assert "No such option" in output
        assert option in output


def test_agent_run_does_not_accept_provider_or_model_overrides() -> None:
    for option, value in (("--provider", "other"), ("--model", "other-model")):
        result = runner.invoke(
            app,
            [
                "agent-run",
                "examples/issues.json",
                "--repo",
                "examples/demo_repository",
                "--llm",
                option,
                value,
            ],
        )

        output = unstyle(result.output)
        assert result.exit_code == 2
        assert "No such option" in output
        assert option in output


def test_benchmark_does_not_accept_historical_full_analysis_variant() -> None:
    result = runner.invoke(
        app,
        [
            "benchmark",
            "benchmarks/cases.json",
            "--variant",
            "hybrid-full",
        ],
    )

    assert result.exit_code == 2
    assert "hybrid-full" in result.output


def test_benchmark_reranker_uses_long_read_timeout() -> None:
    settings = Settings(
        opencode_api_key="test-key",
        opencode_timeout_seconds=1,
        _env_file=None,
    )

    analyzer = _build_benchmark_reranker(settings, temperature=0.1, seed=1337)

    assert analyzer.timeout_seconds == 180
    assert analyzer.rerank_initial_output_tokens == 256
    assert analyzer.rerank_max_output_tokens == 1_024
    assert analyzer.rerank_reasoning_effort == "none"
    analyzer.close()


def test_agent_run_llm_uses_injected_analyzer(tmp_path: Path, monkeypatch) -> None:
    class FakeAnalyzer:
        def __init__(
            self,
            api_key,
            max_output_tokens,
            timeout_seconds,
        ):
            assert api_key == "test-key"
            assert max_output_tokens == 4_096
            assert timeout_seconds == 60
            self.model = "deepseek-v4-flash-free"

        def analyze(self, issue, report, evidence):
            return LLMAnalysisResult(
                provider="opencode",
                model=self.model,
                request_id="cli-request",
                input_tokens=100,
                output_tokens=50,
                elapsed_ms=5,
                analysis=LLMAnalysis(
                    summary="Authentication evidence matches the issue.",
                    issue_type="bug",
                    affected_component="authentication",
                    reproduction_completeness="partial",
                    evidence_observations=[
                        {
                            "evidence_id": evidence[0].id,
                            "alignment": "supports_issue",
                            "observation": "The file contains the token validation path.",
                        }
                    ],
                    contradictions=[],
                    reranked_evidence_ids=[evidence[0].id],
                    hypotheses=[
                        {
                            "description": "Validation errors may escape the refresh path.",
                            "confidence": 0.7,
                            "evidence_ids": [evidence[0].id],
                            "missing_evidence": ["Runtime trace"],
                            "validation_step": (
                                "Run the existing refresh-token test and inspect the error."
                            ),
                        }
                    ],
                    needs_more_evidence=True,
                ),
            )

        def close(self):
            return None

    monkeypatch.setenv("OPENCODE_API_KEY", "test-key")
    monkeypatch.setattr(
        "repo_issue_intelligence.cli.OpenCodeIssueAnalyzer",
        FakeAnalyzer,
    )
    output = tmp_path / "agent-run-llm.json"

    result = runner.invoke(
        app,
        [
            "agent-run",
            "examples/issues.json",
            "--repo",
            "examples/demo_repository",
            "--top-k",
            "1",
            "--llm",
            "--database",
            str(tmp_path / "agent.sqlite3"),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["llm_enabled"] is True
    assert payload["investigations"][0]["llm_analysis"]["request_id"] == "cli-request"


def test_benchmark_discover_writes_audit_catalog(tmp_path: Path, monkeypatch) -> None:
    class FakeClient:
        def __init__(self, token):
            self.token = token

        def close(self):
            return None

    def fake_discover(client, repositories, **options):
        assert repositories == ["example/project"]
        assert options["target_per_repository"] == 2
        assert options["suggested_tiers"] == {
            "example/project": BenchmarkTier.GENERALIZATION
        }
        return CandidateCatalog(
            name="test-candidates",
            version=1,
            generated_at=datetime(2026, 7, 30, tzinfo=UTC),
            repositories=repositories,
            search_query="test",
            target_per_repository=2,
            scan_limit_per_repository=10,
            max_source_files=5,
            candidates=[],
        )

    monkeypatch.setattr("repo_issue_intelligence.cli.GitHubClient", FakeClient)
    monkeypatch.setattr(
        "repo_issue_intelligence.cli.discover_candidates",
        fake_discover,
    )
    output = tmp_path / "candidates.json"

    result = runner.invoke(
        app,
        [
            "benchmark-discover",
            "example/project",
            "--target-per-repository",
            "2",
            "--scan-limit-per-repository",
            "10",
            "--tier",
            "example/project=generalization",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(output.read_text(encoding="utf-8"))["name"] == "test-candidates"
