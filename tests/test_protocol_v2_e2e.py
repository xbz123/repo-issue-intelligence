"""G1 release rehearsals: synthetic inputs only, never a user database/provider."""

import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from test_agent_cli_v2 import demo_checkout
from test_agent_resume import clean_runtime as clean_runtime
from test_agent_store_v2 import new_store
from test_api_security import AUTH, TOKEN
from test_issue_execution import NOW, issues, repository, successful_response
from typer.testing import CliRunner

from repo_issue_intelligence import cli, issue_execution
from repo_issue_intelligence.agent_database import inspect_database
from repo_issue_intelligence.agent_store_migrations import backup_agent_database
from repo_issue_intelligence.agent_store_v2 import AgentStoreV2
from repo_issue_intelligence.api import app
from repo_issue_intelligence.benchmark import BenchmarkCase, BenchmarkVariant, evaluate_case
from repo_issue_intelligence.investigator import investigate
from repo_issue_intelligence.issue_execution import run_agent_v2
from repo_issue_intelligence.legacy_projection import project_legacy_run
from repo_issue_intelligence.models import IssueRecord
from repo_issue_intelligence.private_exports import write_private_json
from repo_issue_intelligence.protocol_v2_models import TracePayloadV2
from repo_issue_intelligence.repository_context import capture_repository_context
from repo_issue_intelligence.repository_index import build_repository_map
from repo_issue_intelligence.repository_view import prepare_repository_view
from repo_issue_intelligence.run_configuration import RunConfigurationError

ROOT = Path(__file__).resolve().parents[1]


def record_gate(record_property, name, value):
    record_property(name, json.dumps(value, sort_keys=True))
    print(name, json.dumps(value, sort_keys=True))


def test_g1_migration_and_lossless_rollback_rehearsal(tmp_path, record_property):
    source = tmp_path / "legacy.sqlite3"
    shutil.copy2(ROOT / "tests/fixtures/protocol_v2/legacy_agent.sqlite3", source)
    source_before = source.read_bytes()
    backup = tmp_path / "legacy-backup.sqlite3"
    backup_agent_database(source, backup)
    backup_before = backup.read_bytes()
    target = tmp_path / "private" / "release.sqlite3"
    runner = CliRunner()
    migrated = runner.invoke(
        cli.app, ["agent-db", "migrate", "--source", str(source), "--destination", str(target)]
    )
    assert migrated.exit_code == 0, migrated.output
    history = project_legacy_run(target, "legacy-success-0001")
    assert history.protocol == "legacy0" and history.evidence_status == "unavailable"
    assert (
        history.raw_report_json == project_legacy_run(source, "legacy-success-0001").raw_report_json
    )
    store = AgentStoreV2(target)
    run = run_agent_v2(issues(1), repository(tmp_path / "repo"), 1, store, as_of=NOW)
    summary = store.get_run_summary(run.run_id)
    exported = tmp_path / "archive" / "new-run.json"
    write_private_json(exported, summary.model_dump_json(), (target,))
    assert json.loads(exported.read_text()) == summary.model_dump(mode="json")
    # Stop writes, archive the whole V2 ledger, then verify on a separate restored copy.
    v2_archive = tmp_path / "archive" / "v2.sqlite3"
    with store.writer_lock():
        backup_agent_database(target, v2_archive)
    restored = AgentStoreV2(v2_archive)
    assert restored.get_run_summary(run.run_id) == summary
    assert (
        project_legacy_run(backup, "legacy-success-0001").raw_report_json == history.raw_report_json
    )
    assert source.read_bytes() == source_before and backup.read_bytes() == backup_before
    assert inspect_database(source).kind.value == "legacy0"
    with closing(sqlite3.connect(target)) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM agent_runs")
    assert (
        runner.invoke(
            cli.app, ["agent-db", "migrate", "--source", str(source), "--destination", str(target)]
        ).exit_code
        != 0
    )
    assert exported.stat().st_mode & 0o777 == v2_archive.stat().st_mode & 0o777 == 0o600
    record_gate(
        record_property,
        "g1_migration_rollback",
        {
            "source_unchanged": True,
            "backup_unchanged": True,
            "legacy_history_readable": True,
            "new_v2_runs_preserved": 1,
            "target_overwrite_refused": True,
        },
    )


def test_g1_clean_tracked_candidate_symbol_and_metric_equivalence(tmp_path, record_property):
    demo = demo_checkout(tmp_path)
    records = [
        IssueRecord.model_validate(item)
        for item in json.loads((ROOT / "examples/issues.json").read_text())
    ]
    snapshot = capture_repository_context(demo)
    original = build_repository_map(demo)
    comparisons = []
    with prepare_repository_view(snapshot) as view:
        frozen = build_repository_map(view)
        for issue in records:
            before, after = investigate(issue, original), investigate(issue, frozen)
            assert before.candidates == after.candidates
            expected = before.candidates[0].file if before.candidates else original.files[0].path
            case = BenchmarkCase(
                id=f"demo-{issue.number}",
                tier="main",
                repository="example/demo",
                issue_number=issue.number,
                issue_updated_at=issue.updated_at,
                issue_snapshot=issue,
                fix_pr_number=1,
                pre_fix_sha=snapshot.commit_oid,
                expected_files=[expected],
            )
            old_metrics = evaluate_case(case, issue, demo, BenchmarkVariant.DETERMINISTIC)
            new_metrics = evaluate_case(case, issue, view, BenchmarkVariant.DETERMINISTIC)
            fields = {
                "candidate_files",
                "candidate_symbols",
                "file_recall_at_1",
                "file_recall_at_5",
                "file_recall_at_10",
                "file_recall_at_20",
                "reciprocal_rank",
            }
            assert fields <= type(old_metrics).model_fields.keys()
            assert old_metrics.model_dump(include=fields) == new_metrics.model_dump(include=fields)
            comparisons.append(
                {
                    "case": case.id,
                    "candidate_count": len(before.candidates),
                    "files_symbols_metrics_equal": True,
                }
            )
        # Scope changes are intentional, not folded into the equivalence claim.
        (demo / "untracked_decoy.py").write_text("def refresh_token(): return 'decoy'\n")
        assert "untracked_decoy.py" not in {item.path for item in build_repository_map(view).files}
    record_gate(
        record_property,
        "g1_ranking_equivalence",
        {
            "dataset": "official-demo-fixed-inputs",
            "cases": comparisons,
            "gold": "synthetic regression targets, not benchmark quality claims",
            "untracked_decoy": "excluded-intentionally",
        },
    )


def test_g1_storage_scale_keeps_map_out_of_snapshots(tmp_path, record_property):
    root = repository(tmp_path / "large-repo", file_count=1000)
    mapping = build_repository_map(root)
    map_bytes = len(mapping.model_dump_json().encode())
    store = new_store(tmp_path / "private")
    run = run_agent_v2(issues(1, 2, 3), root, 3, store, as_of=NOW)
    # Stress the supported diagnostic boundary separately from automatic trace emission.
    for number in (1, 2, 3):
        for _ in range(10):
            store.append_trace(
                run.run_id,
                TracePayloadV2(
                    event="deterministic_completed",
                    issue_number=number,
                    item_count=len(mapping.files),
                ),
            )
    with pytest.raises(ValueError):
        TracePayloadV2.model_validate({"event": "deterministic_completed", "repository_map": {}})
    with closing(sqlite3.connect(store.path)) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM sqlite_master WHERE name='agent_snapshots'"
            ).fetchone()[0]
            == 0
        )
        rows = dict(
            connection.execute(
                "SELECT 'issues', count(*) FROM agent_v2_issues UNION ALL "
                "SELECT 'traces', count(*) FROM agent_v2_traces UNION ALL "
                "SELECT 'attempts', count(*) FROM agent_v2_llm_attempts"
            )
        )
        payloads = [r[0] for r in connection.execute("SELECT payload_json FROM agent_v2_traces")]
        assert all("repository_map" not in p and '"files"' not in p for p in payloads)
        assert sum(len(p.encode()) for p in payloads) < map_bytes
    assert len(store.list_issues(run.run_id)) == 3
    record_gate(
        record_property,
        "g1_storage_scale",
        {
            "source_files": len(mapping.files),
            "serialized_map_bytes": map_bytes,
            "database_bytes": store.path.stat().st_size,
            "row_counts": rows,
            "trace_payload_bytes": sum(len(p.encode()) for p in payloads),
            "legacy_snapshots": 0,
            "synthetic_diagnostic_traces": 30,
        },
    )


@pytest.mark.parametrize("mode", ["mixed", "disabled", "fatal"])
def test_g1_mixed_run_http_cli_review_retry_and_accounting(
    tmp_path, monkeypatch, clean_runtime, record_property, mode
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_API_KEY", "synthetic-key")
    root = repository(tmp_path / "repo")
    store = new_store(tmp_path / "private")
    sent = []

    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        number = payload["issue"]["number"]
        sent.append(number)
        if mode == "fatal" and number == 2:
            raise RunConfigurationError("synthetic fatal error")
        if sent.count(number) == 1:
            if number == 2:
                return httpx.Response(400, json={"error": "synthetic failure"})
            if number == 3:
                raise httpx.ReadTimeout("synthetic unknown", request=request)
        return successful_response(payload["repository_evidence"], metadata=number != 2)

    monkeypatch.setattr(httpx, "HTTPTransport", lambda **kw: httpx.MockTransport(handler))
    analyzer = cli._build_issue_analyzer(cli.Settings()) if mode != "disabled" else None
    records = issues(1, 2, 3, 4)
    records[-1] = records[-1].model_copy(update={"title": "Nothing relevant", "body": ""})
    collect = issue_execution.collect_evidence_v2

    def controlled_no_evidence(report, **kwargs):
        # Inject one empty collector outcome; other Issues use the real sealed collector.
        return [] if report.issue.number == 4 else collect(report, **kwargs)

    monkeypatch.setattr(issue_execution, "collect_evidence_v2", controlled_no_evidence)
    try:
        if mode == "fatal":
            with pytest.raises(RunConfigurationError):
                run_agent_v2(
                    records,
                    root,
                    4,
                    store,
                    llm_analyzer=analyzer,
                    allow_external_llm=True,
                    run_id="release",
                    as_of=NOW,
                )
            assert store.get_run("release").status == "FAILED"
            assert store.get_issue("release", 1).llm_state == "succeeded"
            assert store.list_attempts("release", 3) == ()
        else:
            run_agent_v2(
                records,
                root,
                4,
                store,
                llm_analyzer=analyzer,
                allow_external_llm=analyzer is not None,
                run_id="release",
                as_of=NOW,
            )
    finally:
        if analyzer is not None:
            analyzer.close()
    monkeypatch.setenv("RII_API_TOKEN", TOKEN)
    monkeypatch.setenv("RII_API_V2_DATABASE", str(store.path))
    monkeypatch.setenv("RII_API_ANALYSIS_ROOTS", json.dumps([str(root)]))
    monkeypatch.setenv(
        "RII_API_OPERATIONS", '["read", "review", "evidence", "retry", "recover-unknown"]'
    )
    monkeypatch.setenv(
        "RII_API_EXTERNAL_GRANTS",
        json.dumps(
            [
                {
                    "principal": "local-operator",
                    "analysis_root": str(root),
                    "provider": "opencode",
                    "operation": operation,
                }
                for operation in ("retry", "recover-unknown")
            ]
        ),
    )
    prefix = "/v2/agent/runs/release"
    with TestClient(app, base_url="http://127.0.0.1", headers=AUTH) as client:
        assert client.get(prefix, headers={"Authorization": "Bearer invalid"}).status_code == 401
        if mode == "mixed":
            assert [store.get_issue("release", n).llm_state for n in (1, 2, 3, 4)] == [
                "succeeded",
                "failed",
                "interrupted_unknown",
                "skipped_no_evidence",
            ]
            initial = {n: store.list_attempts("release", n) for n in (1, 2, 3)}
            before = {n: store.get_issue("release", n) for n in (1, 2, 3)}
            item = store.read_evidence("release", 1)
            eid = item.items[0].evidence_id
            evidence_url = (
                prefix + f"/issues/1/evidence/{eid}?evidence_set_id={item.evidence_set_id}"
            )
            assert client.get(evidence_url).status_code == 200
            retried = client.post(prefix + "/issues/2/llm-retry", json={})
            assert retried.status_code == 200
            assert retried.json()["llm_state"] == "succeeded"
            assert client.post(prefix + "/issues/3/llm-retry", json={}).status_code == 409
            recovered = client.post(prefix + "/issues/3/llm-retry", json={"recover_unknown": True})
            assert recovered.status_code == 200
            assert recovered.json()["llm_state"] == "succeeded"
            for number in (1, 2, 3):
                assert store.list_attempts("release", number)[:1] == initial[number]
                current = store.get_issue("release", number)
                assert current.llm_state == "succeeded"
                assert (
                    current.selected_analysis_attempt_id
                    == store.list_attempts("release", number)[-1].attempt_id
                )
                assert current.deterministic_report == before[number].deterministic_report
                assert current.evidence_set_id == before[number].evidence_set_id
            assert [a.state for a in store.list_attempts("release", 2)] == ["failure", "success"]
            assert [a.state for a in store.list_attempts("release", 3)] == ["unknown", "success"]
            assert store.list_attempts("release", 1)[0].request.model != "reported-B"
            assert store.list_attempts("release", 1)[0].reported.model == "reported-B"
            assert store.list_attempts("release", 2)[-1].reported.model is None
            assert store.list_attempts("release", 2)[-1].reported.input_tokens is None
            assert store.list_attempts("release", 2)[-1].reported.output_tokens is None
        if mode != "fatal":
            for number, decision in zip(
                (1, 2, 3, 4), ("approved", "rejected", "needs_information", "approved"), strict=True
            ):
                issue = store.get_issue("release", number)
                payload = {
                    "decision": decision,
                    "expected_review_version": 0,
                    "idempotency_key": f"release-{number}",
                    "evidence_set_id": issue.evidence_set_id,
                    "selected_attempt_id": issue.selected_analysis_attempt_id,
                }
                response = client.post(prefix + f"/issues/{number}/reviews", json=payload)
                assert response.status_code == 200, response.text
                assert (
                    client.post(prefix + f"/issues/{number}/reviews", json=payload).json()
                    == response.json()
                )
            assert store.get_run_summary("release").status == "REVIEW_COMPLETED"
            assert {i.review_state for i in store.get_run_summary("release").issues} == {
                "approved",
                "rejected",
                "needs_information",
            }
        shown = CliRunner().invoke(
            cli.app, ["agent-show", "release", "--protocol", "v2", "--database", str(store.path)]
        )
        assert shown.exit_code == 0, shown.output
        http_summary = client.get(prefix).json()
        assert json.loads(shown.output)["llm_outcomes"] == http_summary["llm_outcomes"]
        assert "synthetic-key" not in shown.output + json.dumps(http_summary)
    assert sent == {"mixed": [1, 2, 3, 2, 3], "disabled": [], "fatal": [1, 2]}[mode]
    record_gate(
        record_property,
        f"g1_e2e_{mode}",
        {
            "status": store.get_run_summary("release").status,
            "dispatch_order": sent,
            "http_cli_accounting_equal": True,
        },
    )
