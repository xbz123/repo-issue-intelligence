import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from repo_issue_intelligence.agent_store_migrations import apply_v2_schema
from repo_issue_intelligence.agent_store_v2 import AgentStoreV2, StoreConflict, StoreError
from repo_issue_intelligence.models import IssueRecord
from repo_issue_intelligence.protocol_v2_models import RepositorySnapshot, freeze_run_inputs
from repo_issue_intelligence.run_configuration import capture_requested_run_configuration
from repo_issue_intelligence.scoring import score_issue

NOW = datetime(2026, 9, 7, tzinfo=UTC)


def new_store(directory: Path) -> AgentStoreV2:
    directory.mkdir(mode=0o700)
    path = directory / "agent.sqlite3"
    path.touch(mode=0o600)
    with closing(sqlite3.connect(path)) as connection:
        apply_v2_schema(connection)
    return AgentStoreV2(path)


def create_run(store: AgentStoreV2, *, run_id: str = "run", llm_enabled: bool = True):
    issues = [
        IssueRecord(number=number, title=f"Issue {number}", created_at=NOW, updated_at=NOW)
        for number in (1, 2)
    ]
    inputs = freeze_run_inputs(
        issues,
        as_of=NOW,
        ranked_results=[score_issue(issue, as_of=NOW) for issue in issues],
        selected_issue_numbers=(2, 1),
    )
    configuration = capture_requested_run_configuration(
        captured_at=NOW,
        requested_model="requested-A",
        request_parameters={"temperature": 0.2, "seed": 7},
    ).model_copy(update={"llm_enabled": llm_enabled})
    return store.create_run(
        RepositorySnapshot(git_root=Path("/repo"), analysis_root=Path("/repo"), captured_at=NOW),
        configuration,
        inputs,
        inputs.selection,
        run_id=run_id,
    )


def test_run_is_frozen_and_issues_keep_selected_order(tmp_path):
    store = new_store(tmp_path / "private")
    run = create_run(store)
    assert store.get_run(run.run_id) == run
    assert [issue.issue_number for issue in store.list_issues(run.run_id)] == [2, 1]
    assert store.get_issue(run.run_id, 1).llm_state == "pending"
    with pytest.raises(TypeError):
        run.configuration.request_parameters["temperature"] = 1
    with pytest.raises(StoreConflict):
        create_run(store)
    assert store.get_run("absent") is None


def test_foreground_writer_lock_refuses_second_process_but_keeps_readers(tmp_path):
    store = new_store(tmp_path / "private")
    create_run(store)
    command = [
        sys.executable,
        "-c",
        "from pathlib import Path\n"
        "from repo_issue_intelligence.agent_store_v2 import AgentStoreV2, StoreConflict\n"
        "import sys\n"
        "store = AgentStoreV2(Path(sys.argv[1]))\n"
        "assert store.get_run('run') is not None\n"
        "try:\n"
        "    with store.writer_lock():\n"
        "        print('acquired')\n"
        "except StoreConflict:\n"
        "    print('refused')\n",
        str(store.path),
    ]
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    with store.writer_lock():
        create_run(store, run_id="committed-under-lock")
        child = subprocess.run(command, capture_output=True, text=True, check=True, env=environment)
        assert child.stdout.strip() == "refused"
        assert store.get_run("committed-under-lock") is not None
    child = subprocess.run(command, capture_output=True, text=True, check=True, env=environment)
    assert child.stdout.strip() == "acquired"


def successful_analysis():
    from repo_issue_intelligence.protocol_v2_models import AnalysisV2

    return AnalysisV2(
        summary="Work path may fail",
        issue_type="bug",
        affected_component="src/worker.py::work",
        reproduction_completeness="partial",
        input_evidence_ids=("E1",),
        primary_evidence_id="E1",
        evidence_observations=(
            {
                "evidence_id": "E1",
                "alignment": "supports_issue",
                "observation": "Work path is present",
            },
        ),
        contradictions=(),
        hypotheses=(
            {
                "description": "Work path may fail",
                "confidence": 0.5,
                "evidence_ids": ("E1",),
                "missing_evidence": (),
                "validation_step": "Reproduce work failure",
            },
        ),
        needs_more_evidence=False,
    )


def test_attempt_terminal_row_is_once_and_selected_analysis_is_derived(tmp_path):
    from test_evidence_ledger import save_report, seal

    from repo_issue_intelligence.protocol_v2_models import (
        AttemptError,
        AttemptRequest,
        AttemptTerminalFields,
        LocalObservation,
        ReportedObservation,
    )

    store = new_store(tmp_path / "private")
    create_run(store)
    save_report(store)
    evidence = seal(store)
    request = AttemptRequest(model="requested-A", temperature=0.2, seed=7)
    first = store.start_attempt("run", 1, evidence.evidence_set_id, request)
    with pytest.raises(StoreConflict):
        store.start_attempt("run", 1, evidence.evidence_set_id, request)
    store.finalize_attempt(
        first.attempt_id,
        AttemptTerminalFields(
            state="unknown",
            error=AttemptError(category="interrupted", detail="Local interruption"),
        ),
    )
    second = store.start_attempt("run", 1, evidence.evidence_set_id, request)
    result = store.finalize_attempt(
        second.attempt_id,
        AttemptTerminalFields(
            state="success",
            analysis=successful_analysis(),
            reported=ReportedObservation(model="reported-B", response_id="response-7"),
            local=LocalObservation(http_status=200, thread_id=None),
        ),
    )
    assert result.state == "success"
    assert result.request.model == "requested-A"
    assert result.reported.model == "reported-B"
    assert result.reported.seed is None
    assert result.reported.temperature is None
    assert result.reported.input_tokens is None
    assert result.reported.response_id == "response-7"
    assert result.local.http_status == 200
    assert result.local.thread_id is None
    issue = store.get_issue("run", 1)
    assert issue.selected_analysis_attempt_id == second.attempt_id
    assert issue.llm_state == "succeeded"
    assert issue.attempt_count == 2
    assert issue.analysis["primary_evidence_id"] == "E1"
    with pytest.raises(StoreConflict):
        store.finalize_attempt(
            first.attempt_id,
            AttemptTerminalFields(
                state="success",
                analysis=successful_analysis(),
            ),
        )
    assert store.get_issue("run", 1) == issue
    assert [attempt.state for attempt in store.list_attempts("run", 1)] == ["unknown", "success"]
    assert store.get_attempt("absent") is None
    summary = store.get_run_summary("run")
    assert summary.protocol == "v2"
    assert [item.issue_number for item in summary.issues] == [2, 1]
    assert summary.llm_outcomes == {"pending": 1, "succeeded": 1}
    assert summary.issues[1].latest_attempt == result
    assert summary.issues[1].diagnostics == ("reported_model_differs_from_requested",)
    assert summary.issues[1].review_state == "pending"
    assert summary.issues[1].reviews == ()
    assert summary.reviewed_issues == 0
    assert store.get_run_summary("absent") is None


def test_store_refuses_missing_legacy_unsafe_and_replaced_targets(tmp_path):
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(StoreError):
        from repo_issue_intelligence.agent_store_v2 import AgentStoreV2

        AgentStoreV2(missing)
    assert not missing.exists()
    store = new_store(tmp_path / "private")
    store.path.chmod(0o644)
    with pytest.raises(StoreError):
        store.get_run("absent")
    assert store.path.stat().st_mode & 0o777 == 0o644
    store.path.chmod(0o600)
    store.path.rename(store.path.with_suffix(".retained"))
    replacement = new_store(tmp_path / "replacement")
    replacement.path.rename(store.path)
    with pytest.raises(StoreError):
        store.get_run("absent")


def test_run_summary_reads_one_database_snapshot_during_finalization(tmp_path, monkeypatch):
    from test_evidence_ledger import save_report, seal

    from repo_issue_intelligence.protocol_v2_models import AttemptRequest, AttemptTerminalFields

    store = new_store(tmp_path / "private")
    create_run(store)
    save_report(store)
    evidence = seal(store)
    attempt = store.start_attempt(
        "run",
        1,
        evidence.evidence_set_id,
        AttemptRequest(model="requested-A", temperature=0.2, seed=7),
    )
    connect = sqlite3.connect
    with closing(connect(store.path)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
    finalized = False

    def finalize_between_reads(sql):
        nonlocal finalized
        if not finalized and "ORDER BY ordinal DESC LIMIT 1" in sql:
            finalized = True
            store.finalize_attempt(
                attempt.attempt_id,
                AttemptTerminalFields(state="success", analysis=successful_analysis()),
            )

    def observed_connection(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_trace_callback(finalize_between_reads)
        return connection

    monkeypatch.setattr(sqlite3, "connect", observed_connection)
    summary = store.get_run_summary("run")
    assert finalized
    issue = summary.issues[1]
    assert issue.llm_state == "in_progress"
    assert issue.latest_attempt.state == "in_progress"
    assert issue.selected_analysis_attempt_id is None
    assert store.get_run_summary("run").issues[1].llm_state == "succeeded"


def test_run_summary_derives_review_completion_without_changing_control_status(tmp_path):
    from test_evidence_ledger import save_report

    store = new_store(tmp_path / "private")
    create_run(store, llm_enabled=False)
    for number in (1, 2):
        save_report(store, issue_number=number)
    store.set_run_status("run", "AWAITING_REVIEW", expected_status="RUNNING")
    # Seed already-persisted review records through the database boundary;
    # the future PR7B review service is deliberately not part of this reader test.
    for number, status in ((1, "PARTIALLY_REVIEWED"), (2, "REVIEW_COMPLETED")):
        with closing(sqlite3.connect(store.path)) as connection, connection:
            connection.execute(
                "INSERT INTO agent_v2_reviews "
                "(review_id,run_id,issue_number,principal_id,idempotency_key,operation,"
                "expected_review_version,decision,payload_json,response_json,created_at) "
                "VALUES (?, 'run', ?, 'reviewer', ?, 'review', 0, 'rejected', '{}', '{}', ?)",
                (f"review-{number}", number, f"key-{number}", NOW.isoformat()),
            )
        summary = store.get_run_summary("run")
        assert summary.status == status
        assert summary.reviewed_issues == number
        assert store.get_run("run").status == "AWAITING_REVIEW"


def test_trace_is_small_closed_and_issue_bound(tmp_path):
    from test_evidence_ledger import save_report, seal

    from repo_issue_intelligence.protocol_v2_models import TracePayloadV2

    store = new_store(tmp_path / "private")
    create_run(store)
    save_report(store)
    evidence = seal(store)
    trace = store.append_trace(
        "run",
        TracePayloadV2(
            event="evidence_sealed",
            issue_number=1,
            evidence_set_id=evidence.evidence_set_id,
            item_count=1,
        ),
    )
    assert store.list_traces("run") == (trace,)
    with pytest.raises(ValueError):
        store.append_trace("run", {"event": "evidence_sealed", "source": "private content"})
    with pytest.raises(StoreError):
        store.append_trace(
            "run",
            TracePayloadV2(
                event="evidence_sealed",
                issue_number=2,
                evidence_set_id=evidence.evidence_set_id,
            ),
        )
    assert len(store.list_traces("run")) == 1


def test_attempt_configuration_is_frozen_and_terminal_validation_rolls_back(tmp_path, monkeypatch):
    from test_evidence_ledger import save_report, seal

    from repo_issue_intelligence.protocol_v2_models import AttemptRequest, AttemptTerminalFields

    store = new_store(tmp_path / "private")
    create_run(store)
    save_report(store)
    evidence = seal(store)
    for request in (
        AttemptRequest(model="changed-B", temperature=0.2, seed=7),
        AttemptRequest(model="requested-A", temperature=None, seed=7),
        AttemptRequest(model="requested-A", temperature=0.2, seed=7, service_tier="priority"),
        AttemptRequest(model="requested-A", temperature=0.2),
    ):
        with pytest.raises(StoreError):
            store.start_attempt("run", 1, evidence.evidence_set_id, request)
    assert store.list_attempts("run", 1) == ()
    request = AttemptRequest(model="requested-A", temperature=0.2, seed=7)
    with pytest.raises(StoreConflict):
        store.start_attempt("run", 2, evidence.evidence_set_id, request)
    attempt = store.start_attempt("run", 1, evidence.evidence_set_id, request)
    invalid = successful_analysis().model_copy(update={"affected_component": "unrelated.py"})
    with pytest.raises(StoreError):
        store.finalize_attempt(
            attempt.attempt_id, AttemptTerminalFields(state="success", analysis=invalid)
        )

    real_connect = sqlite3.connect

    def deny_selected_pointer(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connection.set_authorizer(
            lambda action, table, column, *_: (
                sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_UPDATE
                and table == "agent_v2_issues"
                and column == "selected_analysis_attempt_id"
                else sqlite3.SQLITE_OK
            )
        )
        return connection

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", deny_selected_pointer)
        with pytest.raises(StoreError):
            store.finalize_attempt(
                attempt.attempt_id,
                AttemptTerminalFields(
                    state="success",
                    analysis=successful_analysis(),
                ),
            )
    assert store.get_attempt(attempt.attempt_id) == attempt
    assert store.get_issue("run", 1).selected_analysis_attempt_id is None


def test_minimal_state_updates_are_conditional_without_touching_inputs(tmp_path):
    store = new_store(tmp_path / "private")
    run = create_run(store)
    assert store.set_deterministic_state("run", 1, "running").deterministic_state == "running"
    assert store.set_deterministic_state("run", 1, "failed").deterministic_state == "failed"
    with pytest.raises(StoreConflict):
        store.set_deterministic_state("run", 1, "running")
    updated = store.set_run_status("run", "FAILED", expected_status="RUNNING")
    assert updated.status == "FAILED"
    assert updated.inputs == run.inputs
    with pytest.raises(StoreConflict):
        store.set_run_status("run", "INTERRUPTED", expected_status="RUNNING")


def test_failure_null_receipts_empty_evidence_and_sensitive_metadata(tmp_path, caplog):
    from test_evidence_ledger import save_report, seal

    from repo_issue_intelligence.protocol_v2_models import (
        AttemptError,
        AttemptRequest,
        AttemptTerminalFields,
        ReportedObservation,
    )

    store = new_store(tmp_path / "private")
    create_run(store)
    save_report(store)
    empty = seal(store, items=[])
    assert store.get_issue("run", 1).llm_state == "skipped_no_evidence"
    request = AttemptRequest(model="requested-A", temperature=0.2, seed=7)
    with pytest.raises(StoreConflict):
        store.start_attempt("run", 1, empty.evidence_set_id, request)
    save_report(store, issue_number=2)
    evidence = seal(store, issue_number=2)
    attempt = store.start_attempt("run", 2, evidence.evidence_set_id, request)
    result = store.finalize_attempt(
        attempt.attempt_id,
        AttemptTerminalFields(
            state="failure",
            error=AttemptError(category="transport", detail="Connection failed"),
            reported=ReportedObservation(input_tokens=0),
        ),
    )
    assert result.reported.input_tokens == 0
    assert result.reported.output_tokens is None
    assert store.get_issue("run", 2).selected_analysis_attempt_id is None
    assert store.get_issue("run", 2).llm_state == "failed"
    for fields in (
        {"state": "success", "analysis": {"outcome": "success"}},
        {
            "state": "failure",
            "error": {"category": "transport", "detail": "failed", "status": "failure"},
        },
        {"state": "unknown", "error": {"category": "transport", "detail": "failed"}},
    ):
        with pytest.raises(ValueError):
            AttemptTerminalFields.model_validate(fields)
    for secret in (
        "api_key=super-private",
        "Bearer super-private",
        "https://host/path?token=private",
    ):
        with pytest.raises(ValueError) as error:
            AttemptError(category="provider", detail=secret)
        assert secret not in str(error.value)
        assert secret not in caplog.text
    assert (
        AttemptRequest(model="ordinary-secret-research-model").model
        == "ordinary-secret-research-model"
    )


def test_constructor_rejects_non_v2_and_symlink_and_existing_store_rejects_schema_change(tmp_path):
    private = tmp_path / "private"
    store = new_store(private)
    link = private / "link.sqlite3"
    link.symlink_to(store.path)
    with pytest.raises(StoreError):
        AgentStoreV2(link)
    unknown = private / "empty.sqlite3"
    unknown.touch(mode=0o600)
    with pytest.raises(StoreError):
        AgentStoreV2(unknown)
    assert unknown.stat().st_size == 0
    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute("PRAGMA user_version=3")
    with pytest.raises(StoreError):
        store.get_run("absent")


def test_issue_projection_reads_pointer_and_attempts_from_one_snapshot(tmp_path, monkeypatch):
    from test_evidence_ledger import save_report, seal

    from repo_issue_intelligence.protocol_v2_models import AttemptRequest, AttemptTerminalFields

    store = new_store(tmp_path / "private")
    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
    create_run(store)
    save_report(store)
    evidence = seal(store)
    attempt = store.start_attempt(
        "run",
        1,
        evidence.evidence_set_id,
        AttemptRequest(model="requested-A", temperature=0.2, seed=7),
    )
    real_connect = sqlite3.connect
    finalized = False

    def finalize_during_read(statement):
        nonlocal finalized
        if not finalized and statement.startswith(
            "SELECT * FROM agent_v2_llm_attempts WHERE run_id"
        ):
            finalized = True
            store.finalize_attempt(
                attempt.attempt_id,
                AttemptTerminalFields(
                    state="success",
                    analysis=successful_analysis(),
                ),
            )

    def concurrent_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connection.set_trace_callback(finalize_during_read)
        return connection

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", concurrent_connect)
        observed = store.get_issue("run", 1)
    assert finalized
    assert observed.llm_state == "in_progress"
    assert observed.selected_analysis_attempt_id is None
    assert store.get_issue("run", 1).llm_state == "succeeded"
