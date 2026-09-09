"""PR5A tests cross the public Store interface using independent connections."""

import multiprocessing
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from test_agent_store_v2 import create_run, new_store, successful_analysis
from test_evidence_ledger import save_report, seal

from repo_issue_intelligence.agent_store_v2 import AgentStoreV2, StoreConflict
from repo_issue_intelligence.protocol_v2_models import (
    AttemptError,
    AttemptRequest,
    AttemptTerminalFields,
    LocalObservation,
    ReportedObservation,
)


@pytest.mark.parametrize("status", ["FAILED", "INTERRUPTED", "AWAITING_REVIEW"])
def test_attempt_start_refuses_inactive_run_without_creating_attempt(tmp_path, status):
    store = new_store(tmp_path / "private")
    create_run(store)
    save_report(store)
    evidence = seal(store)
    store.set_run_status("run", status, expected_status="RUNNING")

    with pytest.raises(StoreConflict, match="running"):
        store.start_attempt(
            "run",
            1,
            evidence.evidence_set_id,
            AttemptRequest(model="requested-A", temperature=0.2, seed=7),
        )
    assert store.list_attempts("run", 1) == ()
    assert store.get_run("run").status == status


@pytest.mark.parametrize("category", ["interrupted", "outcome_uncertain"])
def test_unknown_attempt_cannot_be_retried_or_reclassified_by_default(tmp_path, category):
    store = new_store(tmp_path / "private")
    create_run(store)
    save_report(store)
    evidence = seal(store)
    request = AttemptRequest(model="requested-A", temperature=0.2, seed=7)
    attempt = store.start_attempt("run", 1, evidence.evidence_set_id, request)
    unknown = store.finalize_attempt(
        attempt.attempt_id,
        AttemptTerminalFields(
            state="unknown", error=AttemptError(category=category, detail="Outcome is unknown")
        ),
    )
    with pytest.raises(StoreConflict, match="unknown"):
        store.start_attempt("run", 1, evidence.evidence_set_id, request)
    with pytest.raises(StoreConflict):
        store.finalize_attempt(
            attempt.attempt_id,
            AttemptTerminalFields(
                state="success",
                analysis=successful_analysis(),
            ),
        )
    assert store.list_attempts("run", 1) == (unknown,)
    assert store.get_issue("run", 1).selected_analysis_attempt_id is None


def _start_in_process(database, issue_number, barrier, release, results):
    store = AgentStoreV2(Path(database))
    evidence = store.read_evidence("run", issue_number)
    barrier.wait(timeout=15)
    try:
        attempt = store.start_attempt(
            "run",
            issue_number,
            evidence.evidence_set_id,
            AttemptRequest(model="requested-A", temperature=0.2, seed=7),
        )
    except StoreConflict:
        results.put(("conflict", issue_number, None))
        return
    # A separate reader observes the committed start before the fake backend runs.
    assert AgentStoreV2(Path(database)).get_attempt(attempt.attempt_id) == attempt
    results.put(("backend", issue_number, attempt.attempt_id))
    assert release.wait(timeout=15)


@pytest.mark.parametrize("issue_numbers", [(1, 1), (1, 2)])
def test_process_start_claims_commit_before_dispatch_without_blocking_other_issues(
    tmp_path, issue_numbers
):
    store = new_store(tmp_path / "private")
    create_run(store)
    for number in set(issue_numbers):
        save_report(store, issue_number=number)
        seal(store, issue_number=number)
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(3)
    release = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_start_in_process,
            args=(
                str(store.path),
                number,
                barrier,
                release,
                results,
            ),
        )
        for number in issue_numbers
    ]
    try:
        for process in processes:
            process.start()
        barrier.wait(timeout=15)
        observed = [results.get(timeout=15), results.get(timeout=15)]
        assert sorted(item[0] for item in observed) == (
            ["backend", "conflict"] if issue_numbers == (1, 1) else ["backend", "backend"]
        )
        for number in set(issue_numbers):
            (attempt,) = store.list_attempts("run", number)
            assert attempt.state == "in_progress"
        assert store.get_run_summary("run") is not None
    finally:
        release.set()
        for process in processes:
            process.join(timeout=15)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        results.close()
        results.join_thread()
    assert [process.exitcode for process in processes] == [0, 0]


def test_busy_start_is_a_bounded_conflict_without_a_partial_attempt(tmp_path):
    store = new_store(tmp_path / "private")
    create_run(store)
    save_report(store)
    evidence = seal(store)
    with closing(sqlite3.connect(store.path)) as competitor:
        competitor.execute("BEGIN IMMEDIATE")
        with pytest.raises(StoreConflict, match="contention"):
            store.start_attempt(
                "run",
                1,
                evidence.evidence_set_id,
                AttemptRequest(model="requested-A", temperature=0.2, seed=7),
            )
    assert store.list_attempts("run", 1) == ()


def _finalize_in_process(database, attempt_id, state, worker, barrier, results):
    store = AgentStoreV2(Path(database))
    terminal = AttemptTerminalFields(
        state=state,
        analysis=successful_analysis() if state == "success" else None,
        error=None
        if state == "success"
        else AttemptError(
            category="outcome_uncertain" if state == "unknown" else "provider",
            detail="Observed local outcome",
        ),
        reported=ReportedObservation(model=f"worker-{worker}"),
        local=LocalObservation(elapsed_ms=worker),
    )
    barrier.wait(timeout=15)
    try:
        completed = store.finalize_attempt(attempt_id, terminal)
    except StoreConflict:
        results.put(("conflict", None))
    else:
        results.put(("finalized", completed.model_dump()))


@pytest.mark.parametrize(
    "states",
    [
        ("success", "success"),
        ("success", "unknown"),
        ("failure", "unknown"),
        ("failure", "failure"),
    ],
)
def test_process_finalizers_publish_exactly_one_terminal_and_preserve_it(tmp_path, states):
    store = new_store(tmp_path / "private")
    create_run(store)
    save_report(store)
    evidence = seal(store)
    original = store.start_attempt(
        "run",
        1,
        evidence.evidence_set_id,
        AttemptRequest(model="requested-A", temperature=0.2, seed=7),
    )
    context = multiprocessing.get_context("spawn")
    barrier, results = context.Barrier(3), context.Queue()
    processes = [
        context.Process(
            target=_finalize_in_process,
            args=(
                str(store.path),
                original.attempt_id,
                state,
                index,
                barrier,
                results,
            ),
        )
        for index, state in enumerate(states)
    ]
    try:
        for process in processes:
            process.start()
        barrier.wait(timeout=15)
        observed = [results.get(timeout=15), results.get(timeout=15)]
    finally:
        for process in processes:
            process.join(timeout=15)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        results.close()
        results.join_thread()
    assert [process.exitcode for process in processes] == [0, 0]
    assert sorted(kind for kind, _ in observed) == ["conflict", "finalized"]
    completed = store.get_attempt(original.attempt_id)
    assert completed.model_dump() == next(data for kind, data in observed if kind == "finalized")
    assert completed.request == original.request
    assert completed.ordinal == original.ordinal
    issue = store.get_issue("run", 1)
    assert issue.selected_analysis_attempt_id == (
        original.attempt_id if completed.state == "success" else None
    )
    assert issue.review_version == 0
    with pytest.raises(StoreConflict):
        store.finalize_attempt(
            original.attempt_id,
            AttemptTerminalFields(
                state="failure",
                error=AttemptError(category="provider", detail="Late result"),
            ),
        )
    assert store.get_attempt(original.attempt_id) == completed
    assert store.get_issue("run", 1) == issue


def _interrupted_process(database, dispatch, finish, results):
    store = AgentStoreV2(Path(database))
    evidence = store.read_evidence("run", 1)
    attempt = store.start_attempt(
        "run",
        1,
        evidence.evidence_set_id,
        AttemptRequest(model="requested-A", temperature=0.2, seed=7),
    )
    results.put(("started", attempt.attempt_id))
    assert dispatch.wait(timeout=15)
    results.put(("backend", attempt.attempt_id))
    assert finish.wait(timeout=15)


@pytest.mark.parametrize("phase", ["after_start", "in_backend"])
def test_process_exit_does_not_reclaim_an_attempt_or_resend(tmp_path, phase):
    store = new_store(tmp_path / "private")
    create_run(store)
    save_report(store)
    evidence = seal(store)
    context = multiprocessing.get_context("spawn")
    dispatch, finish, results = context.Event(), context.Event(), context.Queue()
    process = context.Process(
        target=_interrupted_process, args=(str(store.path), dispatch, finish, results)
    )
    process.start()
    try:
        kind, attempt_id = results.get(timeout=15)
        assert kind == "started"
        if phase == "in_backend":
            dispatch.set()
            assert results.get(timeout=15) == ("backend", attempt_id)
    finally:
        process.terminate()  # Only this test's fake provider process, never a user process.
        process.join(timeout=5)
        results.close()
        results.join_thread()
    assert not process.is_alive()
    original = store.get_attempt(attempt_id)
    assert original.state == "in_progress"
    assert original.finished_at is None
    with pytest.raises(StoreConflict):
        store.start_attempt(
            "run",
            1,
            evidence.evidence_set_id,
            AttemptRequest(model="requested-A", temperature=0.2, seed=7),
        )
    assert store.list_attempts("run", 1) == (original,)
