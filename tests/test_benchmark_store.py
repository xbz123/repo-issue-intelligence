import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from repo_issue_intelligence.benchmark import (
    BenchmarkCaseResult,
    BenchmarkTier,
)
from repo_issue_intelligence.benchmark_store import BenchmarkStore


def benchmark_result(
    case_id: str,
    *,
    execution_succeeded: bool = True,
) -> BenchmarkCaseResult:
    return BenchmarkCaseResult(
        case_id=case_id,
        tier=BenchmarkTier.GENERALIZATION,
        repository="example/project",
        issue_number=42,
        issue_url="https://github.com/example/project/issues/42",
        fix_pr_url="https://github.com/example/project/pull/43",
        pre_fix_sha="a" * 40,
        issue_updated_at=datetime(2026, 9, 4, tzinfo=UTC),
        expected_files=["src/example.py"],
        execution_succeeded=execution_succeeded,
        error=None if execution_succeeded else "RuntimeError: failed",
    )


def test_benchmark_store_round_trips_and_replaces_case_results(
    tmp_path: Path,
) -> None:
    store = BenchmarkStore(tmp_path / "state" / "benchmark.sqlite3")
    configuration = {
        "variant": "deterministic",
        "cases": [{"id": "case-a"}, {"id": "case-b"}],
    }

    run_id, created_at = store.create_run(configuration)
    store.save_result(run_id, 1, benchmark_result("case-a"))
    store.save_result(
        run_id,
        2,
        benchmark_result("case-b", execution_succeeded=False),
    )

    assert store.status(run_id) == "running"
    assert list(store.load_results(run_id)) == ["case-a", "case-b"]
    assert store.resume_run(run_id, configuration) == created_at

    store.save_result(run_id, 2, benchmark_result("case-b"))
    assert store.load_results(run_id)["case-b"].execution_succeeded is True

    store.mark_complete(run_id, failed=False)
    assert store.status(run_id) == "completed"


def test_benchmark_store_rejects_unknown_or_mismatched_runs(tmp_path: Path) -> None:
    store = BenchmarkStore(tmp_path / "benchmark.sqlite3")
    run_id, _ = store.create_run({"variant": "deterministic"})

    with pytest.raises(ValueError, match="configuration does not match"):
        store.resume_run(run_id, {"variant": "hybrid"})
    with pytest.raises(KeyError):
        store.resume_run("missing", {"variant": "deterministic"})
    with pytest.raises(KeyError):
        store.load_results("missing")
    with pytest.raises(KeyError):
        store.mark_complete("missing", failed=False)


def test_benchmark_store_rejects_unknown_schema_version(tmp_path: Path) -> None:
    database = tmp_path / "benchmark.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA user_version = 99")

    with pytest.raises(ValueError, match="schema version: 99"):
        BenchmarkStore(database)
