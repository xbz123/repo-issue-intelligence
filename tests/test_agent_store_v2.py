import json
import sqlite3
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


@pytest.mark.parametrize(
    "parameter,budget",
    [
        ("max_output_tokens", "output_tokens"),
        ("timeout_seconds", "timeout_seconds"),
    ],
)
def test_store_refuses_bypassed_conflicting_configuration_without_creating_run(
    tmp_path,
    parameter,
    budget,
):
    from repo_issue_intelligence.protocol_v2_models import BudgetConfiguration, FrozenDict

    store = new_store(tmp_path / "private")
    run = create_run(store)
    invalid = run.configuration.model_copy(
        update={
            "request_parameters": FrozenDict({parameter: 100}),
            "budgets": BudgetConfiguration(**{budget: 200}),
        }
    )
    with pytest.raises(StoreError, match="Conflicting"):
        store.create_run(run.snapshot, invalid, run.inputs, run_id="invalid")
    assert store.get_run("invalid") is None


@pytest.mark.parametrize(
    "parameter,budget",
    [
        ("max_output_tokens", "output_tokens"),
        ("timeout_seconds", "timeout_seconds"),
    ],
)
def test_matching_and_budget_only_configuration_accepts_exact_attempt(tmp_path, parameter, budget):
    from test_evidence_ledger import save_report, seal

    from repo_issue_intelligence.protocol_v2_models import AttemptRequest

    store = new_store(tmp_path / "private")
    template = create_run(store)
    for index, parameters in enumerate(({parameter: 100}, {})):
        configuration = capture_requested_run_configuration(
            requested_model="requested-A",
            request_parameters=parameters,
            budgets={budget: 100},
        ).model_copy(update={"llm_enabled": True})
        run_id = f"consistent-{index}"
        store.create_run(template.snapshot, configuration, template.inputs, run_id=run_id)
        save_report(store, run_id=run_id)
        evidence = seal(store, run_id=run_id)
        attempt = store.start_attempt(
            run_id,
            1,
            evidence.evidence_set_id,
            AttemptRequest(model="requested-A", **{parameter: 100}),
        )
        assert getattr(attempt.request, parameter) == 100


@pytest.mark.parametrize("parameter", ["timeout_seconds", "max_output_tokens"])
@pytest.mark.parametrize("budget_kind", ["evidence", "empty", "model"])
def test_request_only_budget_omission_survives_store_roundtrip(tmp_path, parameter, budget_kind):
    from test_evidence_ledger import save_report, seal

    from repo_issue_intelligence.protocol_v2_models import AttemptRequest, BudgetConfiguration

    store = new_store(tmp_path / "private")
    template = create_run(store)
    budgets = {"evidence_chars": 1000} if budget_kind == "evidence" else {}
    if budget_kind == "model":
        budgets = BudgetConfiguration()
    config = capture_requested_run_configuration(
        requested_model="requested-A",
        request_parameters={parameter: 100},
        budgets=budgets,
    ).model_copy(update={"llm_enabled": True})
    store.create_run(template.snapshot, config, template.inputs, run_id="request-only")
    save_report(store, run_id="request-only")
    evidence = seal(store, run_id="request-only")
    attempt = store.start_attempt(
        "request-only",
        1,
        evidence.evidence_set_id,
        AttemptRequest(model="requested-A", **{parameter: 100}),
    )
    assert getattr(attempt.request, parameter) == 100


@pytest.mark.parametrize(
    "parameter,budget",
    [
        ("max_output_tokens", "output_tokens"),
        ("output_tokens", "output_tokens"),
        ("timeout_seconds", "timeout_seconds"),
    ],
)
def test_copied_explicit_null_budget_is_rejected_before_store(tmp_path, parameter, budget):
    from repo_issue_intelligence.protocol_v2_models import BudgetConfiguration, RunConfiguration

    store = new_store(tmp_path / "private")
    template = create_run(store)
    config = capture_requested_run_configuration(
        requested_model="requested-A",
        request_parameters={parameter: 100},
        budgets={"evidence_chars": 1000},
    ).model_copy(update={"llm_enabled": True})
    stored = store.create_run(template.snapshot, config, template.inputs, run_id="valid")
    assert stored.configuration == config
    copied = config.model_copy(update={"budgets": BudgetConfiguration(**{budget: None})})
    with pytest.raises(ValueError, match="Conflicting"):
        RunConfiguration.model_validate(copied)
    with pytest.raises(StoreError, match="Conflicting"):
        store.create_run(template.snapshot, copied, template.inputs, run_id="copied-null")
    assert store.get_run("copied-null") is None


@pytest.mark.parametrize("budget", ["output_tokens", "timeout_seconds"])
def test_copied_null_budget_cannot_be_silently_lost_on_store_roundtrip(tmp_path, budget):
    from repo_issue_intelligence.protocol_v2_models import BudgetConfiguration

    store = new_store(tmp_path / "private")
    template = create_run(store)
    copied = template.configuration.model_copy(
        update={"budgets": BudgetConfiguration(**{budget: None})}
    )
    with pytest.raises(StoreError, match="Conflicting"):
        store.create_run(template.snapshot, copied, template.inputs, run_id="copied-null")
    assert store.get_run("copied-null") is None


@pytest.mark.parametrize("parameter", ["max_output_tokens", "timeout_seconds"])
def test_legacy_serialized_null_defaults_remain_omitted(tmp_path, parameter):
    store = new_store(tmp_path / "private")
    template = create_run(store)
    config = capture_requested_run_configuration(request_parameters={parameter: 100}, budgets={})
    # Historical writers emitted every budget default, including omitted nulls.
    payload = json.loads(config.model_dump_json())
    payload["budgets"] = config.budgets.model_dump(mode="json")
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.execute(
            "INSERT INTO agent_v2_runs SELECT ?, parent_run_id, snapshot_json, ?, "
            "inputs_json, selection_json, status, created_at, updated_at "
            "FROM agent_v2_runs WHERE run_id = ?",
            ("legacy", json.dumps(payload), template.run_id),
        )
    restored = store.get_run("legacy").configuration
    assert restored == config
    budget = "output_tokens" if parameter == "max_output_tokens" else parameter
    assert not restored.has_request_budget(budget)


@pytest.mark.parametrize("budget", ["output_tokens", "timeout_seconds"])
@pytest.mark.parametrize("origin", ["omitted", "user_config"])
@pytest.mark.parametrize("value", [None, 100])
def test_markerless_legacy_budget_copy_preserves_omission_contract(tmp_path, budget, origin, value):
    from repo_issue_intelligence.protocol_v2_models import BudgetConfiguration

    store = new_store(tmp_path / "private")
    template = create_run(store)
    payload = json.loads(template.configuration.model_dump_json())
    payload["budgets"] = template.configuration.budgets.model_dump(mode="json")
    for name in ("output_tokens", "timeout_seconds"):
        payload["parameter_origins"].pop(f"budget.{name}")
    payload["parameter_origins"][budget] = origin
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.execute(
            "INSERT INTO agent_v2_runs SELECT ?, parent_run_id, snapshot_json, ?, "
            "inputs_json, selection_json, status, created_at, updated_at "
            "FROM agent_v2_runs WHERE run_id = ?",
            ("legacy", json.dumps(payload), template.run_id),
        )
    legacy = store.get_run("legacy")
    assert legacy.configuration.has_request_budget(budget) == (origin != "omitted")
    copied = legacy.configuration.model_copy(
        update={"budgets": BudgetConfiguration(**{budget: value})}
    )
    if origin == "omitted":
        with pytest.raises(StoreError, match="Conflicting"):
            store.create_run(legacy.snapshot, copied, legacy.inputs, run_id="copy")
        assert store.get_run("copy") is None
    else:
        restored = store.create_run(legacy.snapshot, copied, legacy.inputs, run_id="copy")
        assert restored.configuration.has_request_budget(budget)
        assert budget in restored.configuration.budgets.model_fields_set


@pytest.mark.parametrize("budget", ["output_tokens", "timeout_seconds"])
def test_constructed_non_default_budget_survives_store_roundtrip(tmp_path, budget):
    from repo_issue_intelligence.protocol_v2_models import BudgetConfiguration

    store = new_store(tmp_path / "private")
    template = create_run(store)
    config = capture_requested_run_configuration(
        budgets=BudgetConfiguration.model_construct(_fields_set=set(), **{budget: 100})
    )
    assert config.parameter_origins[f"budget.{budget}"] == "user_config"
    restored = store.create_run(template.snapshot, config, template.inputs, run_id="constructed")
    assert getattr(restored.configuration.budgets, budget) == 100


@pytest.mark.parametrize(
    "field,value",
    [
        ("output_tokens", 0),
        ("output_tokens", -1),
        ("timeout_seconds", 0),
        ("evidence_chars", 0),
        ("evidence_lines", 0),
        ("top_k", 0),
        ("request_alias", 0),
        ("timeout_seconds", float("inf")),
        ("retry_policy", {"backoff": [-1]}),
        ("unsupported_parameter", "model"),
        ("unsupported_parameter", "api_key"),
        ("non_string_parameter", 1),
    ],
)
def test_invalid_copied_configuration_is_rejected_before_persistence(tmp_path, field, value):
    from repo_issue_intelligence.protocol_v2_models import (
        BudgetConfiguration,
        FrozenDict,
        ProtocolConfiguration,
    )

    store = new_store(tmp_path / "private")
    template = create_run(store)
    if field == "top_k":
        update = {"protocol": ProtocolConfiguration.model_construct(top_k=value)}
    elif field == "request_alias":
        update = {"request_parameters": FrozenDict({"output_tokens": value})}
    elif field == "unsupported_parameter":
        update = {"request_parameters": FrozenDict({value: "synthetic-value"})}
    elif field == "non_string_parameter":
        update = {"request_parameters": {value: "synthetic-value"}}
    else:
        if field == "retry_policy":
            value = FrozenDict(value)
        update = {"budgets": BudgetConfiguration.model_construct(**{field: value})}
    invalid = template.configuration.model_copy(update=update)
    with pytest.raises(StoreError):
        store.create_run(template.snapshot, invalid, template.inputs, run_id="invalid")
    assert store.get_run("invalid") is None
    assert store.list_issues("invalid") == ()
    create_run(store, run_id="invalid")


@pytest.mark.parametrize("value", [100, None])
@pytest.mark.parametrize("with_budget", [False, True])
def test_output_tokens_alias_is_bound_to_attempt(tmp_path, with_budget, value):
    from test_evidence_ledger import save_report, seal

    from repo_issue_intelligence.protocol_v2_models import AttemptRequest

    store = new_store(tmp_path / "private")
    template = create_run(store)
    config = capture_requested_run_configuration(
        requested_model="requested-A",
        request_parameters={"output_tokens": value},
        budgets={"output_tokens": value} if with_budget else {},
    ).model_copy(update={"llm_enabled": True})
    store.create_run(template.snapshot, config, template.inputs, run_id="alias")
    save_report(store, run_id="alias")
    evidence = seal(store, run_id="alias")
    with pytest.raises(StoreError):
        store.start_attempt(
            "alias",
            1,
            evidence.evidence_set_id,
            AttemptRequest(model="requested-A", max_output_tokens=200),
        )
    attempt = store.start_attempt(
        "alias",
        1,
        evidence.evidence_set_id,
        AttemptRequest(model="requested-A", max_output_tokens=value),
    )
    assert attempt.request.max_output_tokens == value


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
            reported=ReportedObservation(model="reported-B"),
        ),
    )
    assert result.state == "success"
    assert result.request.model == "requested-A"
    assert result.reported.model == "reported-B"
    assert result.reported.seed is None
    assert result.reported.temperature is None
    assert result.reported.input_tokens is None
    assert result.local is None
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
