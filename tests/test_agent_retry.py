import json
import multiprocessing
import os
import shutil
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from test_agent_resume import clean_runtime as clean_runtime
from test_agent_store_v2 import new_store
from test_issue_execution import NOW, issues, repository, successful_response
from typer.testing import CliRunner

from repo_issue_intelligence import cli, run_configuration
from repo_issue_intelligence.agent_store_v2 import AgentStoreV2, StoreConflict, StoreError
from repo_issue_intelligence.codex_cli import CodexCLIIssueAnalyzer
from repo_issue_intelligence.config import Settings
from repo_issue_intelligence.issue_execution import run_agent_v2
from repo_issue_intelligence.llm_client import OpenAICompatibleIssueAnalyzer
from repo_issue_intelligence.private_exports import validate_private_export_output
from repo_issue_intelligence.protocol_v2_models import EngineRuntime, RepositoryCaptureMode
from repo_issue_intelligence.run_configuration import RunConfigurationError


@pytest.mark.parametrize("capture_mode", list(RepositoryCaptureMode))
def test_retry_uses_only_frozen_issue_evidence_and_request_after_checkout_is_removed(
    tmp_path, clean_runtime, capture_mode
):
    from repo_issue_intelligence.agent_resume import retry_issue_llm

    root = repository(tmp_path / "repo")
    store = new_store(tmp_path / "private")
    received = []

    def handler(request):
        received.append(json.loads(json.loads(request.content)["messages"][1]["content"]))
        if len(received) == 1:
            return httpx.Response(400, json={"error": {"message": "synthetic failure"}})
        return successful_response(received[-1]["repository_evidence"])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        analyzer = OpenAICompatibleIssueAnalyzer("test-key", client=client)
        run = run_agent_v2(
            issues(1),
            root,
            1,
            store,
            llm_analyzer=analyzer,
            allow_external_llm=True,
            run_id="run",
            as_of=NOW,
            capture_mode=capture_mode,
        )
        original = store.get_issue("run", 1)
        (failure,) = store.list_attempts("run", 1)
        shutil.rmtree(root)  # This test owns the entire synthetic checkout.
        result = retry_issue_llm("run", 1, store, llm_analyzer=analyzer, allow_external_llm=True)
    assert result.llm_state == "succeeded"
    assert result.deterministic_report == original.deterministic_report
    assert result.evidence_set_id == original.evidence_set_id
    assert store.get_run("run").configuration == run.configuration
    assert store.list_attempts("run", 1)[0] == failure
    assert store.list_attempts("run", 1)[1].request == failure.request
    assert store.list_attempts("run", 1)[1].reported.model == "reported-B"
    assert received == [received[0], received[0]]


def _hold_execution_guard(database, attempt_id, ready, release):
    with AgentStoreV2(Path(database)).execution_lock(attempt_id):
        ready.set()
        assert release.wait(timeout=20)


def _run_until_dispatch(database, root, runtime, ready, release):
    run_configuration.capture_engine_runtime = lambda *a, **kw: EngineRuntime.model_validate(
        runtime
    )

    def handler(request):
        ready.set()
        assert release.wait(timeout=20)
        return httpx.Response(400, json={"error": "synthetic failure"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        run_agent_v2(
            issues(1),
            Path(root),
            1,
            AgentStoreV2(Path(database)),
            llm_analyzer=OpenAICompatibleIssueAnalyzer("test-key", client=client),
            allow_external_llm=True,
            run_id="run",
            as_of=NOW,
        )


@pytest.mark.parametrize("resume_first", [False, True])
def test_process_exit_settles_guarded_attempt_unknown_but_never_automatically_resends(
    tmp_path, clean_runtime, resume_first
):
    from repo_issue_intelligence.agent_resume import resume_agent_run, retry_issue_llm

    store = new_store(tmp_path / "private")
    root = repository(tmp_path / "repo")
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    process = context.Process(
        target=_run_until_dispatch,
        args=(str(store.path), str(root), clean_runtime.model_dump(), ready, release),
    )
    sent = []

    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        sent.append(payload)
        return successful_response(payload["repository_evidence"])

    process.start()
    try:
        assert ready.wait(timeout=15)
        (active,) = store.list_attempts("run", 1)
        assert active.state == "in_progress"
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            analyzer = OpenAICompatibleIssueAnalyzer("test-key", client=client)
            with pytest.raises(StoreConflict):
                retry_issue_llm(
                    "run",
                    1,
                    store,
                    llm_analyzer=analyzer,
                    allow_external_llm=True,
                    recover_unknown=True,
                )
            assert store.get_attempt(active.attempt_id) == active
            process.terminate()  # Only the fake worker created by this test.
            process.join(timeout=5)
            assert not process.is_alive()
            if resume_first:
                resumed = resume_agent_run(
                    "run", store, llm_analyzer=analyzer, allow_external_llm=True
                )
                unknown = store.get_attempt(active.attempt_id)
                assert unknown.state == "unknown"
                assert resumed.status == "INTERRUPTED"
                shown = CliRunner().invoke(
                    cli.app,
                    ["agent-show", "run", "--protocol", "v2", "--database", str(store.path)],
                )
                assert shown.exit_code == 0, shown.output
                assert json.loads(shown.output)["status"] == "INTERRUPTED"
                resumed_again = resume_agent_run(
                    "run", store, llm_analyzer=analyzer, allow_external_llm=True
                )
                assert resumed_again.status == "INTERRUPTED"
                assert store.get_attempt(active.attempt_id) == unknown
                assert sent == []
            else:
                assert store.get_run("run").status == "RUNNING"
            retry_issue_llm(
                "run",
                1,
                store,
                llm_analyzer=analyzer,
                allow_external_llm=True,
                recover_unknown=True,
            )
            assert len(sent) == 1
            assert store.get_run("run").status == "AWAITING_REVIEW"
            if resume_first:
                assert store.get_attempt(active.attempt_id) == unknown
            else:
                assert store.get_attempt(active.attempt_id).state == "unknown"
    finally:
        if process.is_alive():
            process.terminate()
        process.join(timeout=5)


@pytest.mark.parametrize("guard_state", ["held", "missing", "unsafe", "unsupported"])
def test_unknown_recovery_refuses_unproven_local_stop_without_mutation(
    tmp_path, clean_runtime, guard_state
):
    from repo_issue_intelligence.agent_resume import retry_issue_llm

    store = new_store(tmp_path / "private")
    calls = []

    def handler(request):
        calls.append(True)
        raise httpx.ReadTimeout("Unknown remote outcome", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        analyzer = OpenAICompatibleIssueAnalyzer("test-key", client=client)
        if guard_state == "unsupported":
            analyzer.local_execution_scope_v2 = None
        run_agent_v2(
            issues(1),
            repository(tmp_path / "repo"),
            1,
            store,
            llm_analyzer=analyzer,
            allow_external_llm=True,
            run_id="run",
            as_of=NOW,
        )
        (attempt,) = store.list_attempts("run", 1)
        lock = store.path.with_name(store.path.name + f".attempt-{attempt.attempt_id}.lock")
        process = None
        if guard_state == "held":
            context = multiprocessing.get_context("spawn")
            ready, release = context.Event(), context.Event()
            process = context.Process(
                target=_hold_execution_guard,
                args=(str(store.path), attempt.attempt_id, ready, release),
            )
            process.start()
            assert ready.wait(timeout=15)
        elif guard_state == "missing":
            lock.unlink()  # Only the synthetic test's guard.
        elif guard_state == "unsafe":
            lock.chmod(0o644)
        before = store.get_run_summary("run")
        try:
            with pytest.raises(StoreError):
                retry_issue_llm(
                    "run",
                    1,
                    store,
                    llm_analyzer=analyzer,
                    allow_external_llm=True,
                    recover_unknown=True,
                )
            assert store.get_run_summary("run") == before
            assert calls == [True]
        finally:
            if process is not None:
                release.set()
                process.join(timeout=15)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
                assert process.exitcode == 0


@pytest.mark.parametrize("change", ["model", "base_url", "timeout_seconds", "permission"])
def test_retry_refuses_current_configuration_drift_before_dispatch(tmp_path, clean_runtime, change):
    from repo_issue_intelligence.agent_resume import retry_issue_llm

    store = new_store(tmp_path / "private")
    calls = []

    def handler(request):
        calls.append(True)
        return httpx.Response(400, json={"error": "synthetic failure"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        analyzer = OpenAICompatibleIssueAnalyzer("test-key", client=client)
        run_agent_v2(
            issues(1),
            repository(tmp_path / "repo"),
            1,
            store,
            llm_analyzer=analyzer,
            allow_external_llm=True,
            run_id="run",
            as_of=NOW,
        )
        before = store.get_run_summary("run")
        if change != "permission":
            setattr(
                analyzer,
                change,
                {
                    "model": "changed",
                    "base_url": "https://other.test/v1/",
                    "timeout_seconds": 3.0,
                }[change],
            )
        with pytest.raises((RunConfigurationError, ValueError)):
            retry_issue_llm(
                "run",
                1,
                store,
                llm_analyzer=analyzer,
                allow_external_llm=change != "permission",
            )
    assert calls == [True]
    assert store.get_run_summary("run") == before


@pytest.mark.parametrize("recovery_outcome", ["success", "failure"])
@pytest.mark.parametrize("issue_count", [1, 2])
def test_unknown_requires_explicit_recovery_and_preserves_old_terminal(
    tmp_path, clean_runtime, recovery_outcome, issue_count
):
    from repo_issue_intelligence.agent_resume import resume_agent_run, retry_issue_llm

    store = new_store(tmp_path / "private")
    received = []

    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        received.append(payload)
        if payload["issue"]["number"] == 1:
            ordinal = sum(item["issue"]["number"] == 1 for item in received)
            if ordinal == 1:
                raise httpx.ReadTimeout("Remote outcome is uncertain", request=request)
            if ordinal == 2 and recovery_outcome == "failure":
                return httpx.Response(400, json={"error": "synthetic recovery failure"})
        return successful_response(payload["repository_evidence"])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        analyzer = OpenAICompatibleIssueAnalyzer("test-key", client=client)
        run_agent_v2(
            issues(*range(1, issue_count + 1)),
            repository(tmp_path / "repo"),
            issue_count,
            store,
            llm_analyzer=analyzer,
            allow_external_llm=True,
            run_id="run",
            as_of=NOW,
        )
        (unknown,) = store.list_attempts("run", 1)
        assert unknown.state == "unknown"
        with pytest.raises(StoreConflict):
            retry_issue_llm("run", 1, store, llm_analyzer=analyzer, allow_external_llm=True)
        assert len(received) == issue_count
        result = retry_issue_llm(
            "run",
            1,
            store,
            llm_analyzer=analyzer,
            allow_external_llm=True,
            recover_unknown=True,
        )
        if recovery_outcome == "failure":
            assert result.llm_state == "failed"
            assert store.get_run("run").status == "INTERRUPTED"
            shown = CliRunner().invoke(
                cli.app, ["agent-show", "run", "--protocol", "v2", "--database", str(store.path)]
            )
            assert shown.exit_code == 0, shown.output
            assert json.loads(shown.output)["status"] == "INTERRUPTED"
            saved = store.get_run_summary("run")
            with pytest.raises(StoreConflict):
                retry_issue_llm("run", 1, store, llm_analyzer=analyzer, allow_external_llm=True)
            assert store.get_run_summary("run") == saved
            resumed = resume_agent_run("run", store, llm_analyzer=analyzer, allow_external_llm=True)
            assert resumed.status == "INTERRUPTED"
            assert len(received) == issue_count + 1
            result = retry_issue_llm(
                "run",
                1,
                store,
                llm_analyzer=analyzer,
                allow_external_llm=True,
                recover_unknown=True,
            )
    assert result.llm_state == "succeeded"
    assert store.get_run("run").status == "AWAITING_REVIEW"
    attempts = store.list_attempts("run", 1)
    assert [attempt.state for attempt in attempts] == (
        ["unknown", "failure", "success"]
        if recovery_outcome == "failure"
        else ["unknown", "success"]
    )
    first, second = attempts[:2]
    assert first == unknown
    assert second.attempt_id != first.attempt_id
    assert all(item == received[0] for item in received if item["issue"]["number"] == 1)
    if issue_count == 2:
        assert store.get_issue("run", 2).llm_state == "succeeded"
        assert len(store.list_attempts("run", 2)) == 1


@pytest.mark.parametrize("unknown", [False, True])
def test_cli_retry_checks_current_permission_and_exports_privately(
    tmp_path, monkeypatch, clean_runtime, unknown
):
    settings = Settings(_env_file=None, LLM_API_KEY="test-key")
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    calls = []

    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        calls.append(payload)
        if len(calls) == 1:
            if unknown:
                raise httpx.ReadTimeout("synthetic unknown", request=request)
            return httpx.Response(400, json={"error": "synthetic failure"})
        return successful_response(payload["repository_evidence"])

    monkeypatch.setattr(httpx, "HTTPTransport", lambda **kwargs: httpx.MockTransport(handler))
    store = new_store(tmp_path / "private")
    analyzer = cli._build_issue_analyzer(settings)
    try:
        run_agent_v2(
            issues(1),
            repository(tmp_path / "repo"),
            1,
            store,
            llm_analyzer=analyzer,
            allow_external_llm=True,
            run_id="run",
            as_of=NOW,
        )
    finally:
        analyzer.close()
    args = [
        "agent-retry-llm",
        "run",
        "--issue",
        "1",
        "--database",
        str(store.path),
        "--protocol",
        "v2",
    ]
    runner = CliRunner()
    before = store.get_run_summary("run")
    refused = runner.invoke(cli.app, args)
    assert refused.exit_code == 2
    assert store.get_run_summary("run") == before
    assert len(calls) == 1
    if unknown:
        assert runner.invoke(cli.app, [*args, "--allow-external-llm"]).exit_code == 2
        assert len(calls) == 1
        args += ["--recover-unknown"]
    output = tmp_path / "summary.json"
    previous_umask = os.umask(0o022)
    try:
        result = runner.invoke(cli.app, [*args, "--allow-external-llm", "--output", str(output)])
    finally:
        os.umask(previous_umask)
    assert result.exit_code == 0, result.output
    assert output.stat().st_mode & 0o777 == 0o600
    assert json.loads(output.read_text())["issues"][0]["llm_state"] == "succeeded"
    assert len(calls) == 2
    if unknown:
        assert "duplicate remote" in result.output


@pytest.mark.parametrize("alias", ["direct", "symlink", "hardlink", "not_created"])
def test_private_export_cannot_replace_attempt_execution_guard(tmp_path, alias):
    store = new_store(tmp_path / "private")
    identifier = str(uuid4())
    lock = store.path.with_name(store.path.name + f".attempt-{identifier}.lock")
    if alias != "not_created":
        with store.execution_lock(identifier, create=True):
            pass
    output = lock
    if alias in {"symlink", "hardlink"}:
        output = tmp_path / "alias.json"
        if alias == "symlink":
            output.symlink_to(lock)
        else:
            os.link(lock, output)
    with pytest.raises(ValueError, match="sidecars"):
        validate_private_export_output(output, (store.path,))


@pytest.mark.parametrize("state", ["success", "reviewed_failure", "no_evidence"])
def test_retry_refuses_ineligible_issues_without_new_attempt(tmp_path, clean_runtime, state):
    from repo_issue_intelligence.agent_resume import retry_issue_llm

    store = new_store(tmp_path / "private")
    calls = []

    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        calls.append(payload)
        if state == "reviewed_failure":
            return httpx.Response(400, json={"error": "synthetic failure"})
        return successful_response(payload["repository_evidence"])

    records = issues(1)
    if state == "no_evidence":
        records[0] = records[0].model_copy(update={"title": "Nothing relevant", "body": ""})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        analyzer = OpenAICompatibleIssueAnalyzer("test-key", client=client)
        run_agent_v2(
            records,
            repository(tmp_path / "repo"),
            1,
            store,
            llm_analyzer=analyzer,
            allow_external_llm=True,
            run_id="run",
            as_of=NOW,
        )
        if state == "reviewed_failure":
            # Persist an existing review at the database boundary; no PR7B review writer is added.
            issue = store.get_issue("run", 1)
            with closing(sqlite3.connect(store.path)) as connection, connection:
                connection.execute(
                    "INSERT INTO agent_v2_reviews "
                    "(review_id,run_id,issue_number,evidence_set_id,principal_id,idempotency_key,"
                    "operation,expected_review_version,decision,payload_json,"
                    "response_json,created_at) VALUES "
                    "('review','run',1,?,'reviewer','key','review',0,'rejected','{}','{}',?)",
                    (issue.evidence_set_id, NOW.isoformat()),
                )
        before = store.get_run_summary("run")
        sent = len(calls)
        with pytest.raises(StoreConflict):
            retry_issue_llm("run", 1, store, llm_analyzer=analyzer, allow_external_llm=True)
        assert len(calls) == sent
        assert store.get_run_summary("run") == before


@pytest.mark.parametrize("change", ["budget", "origin", "prompt", "runtime"])
def test_retry_validates_frozen_budget_origins_protocol_and_runtime(
    tmp_path, monkeypatch, clean_runtime, change
):
    from repo_issue_intelligence.agent_resume import retry_issue_llm

    store = new_store(tmp_path / "private")
    calls = []

    def handler(request):
        calls.append(True)
        return httpx.Response(400, json={"error": "synthetic failure"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        analyzer = OpenAICompatibleIssueAnalyzer("test-key", client=client)
        run = run_agent_v2(
            issues(1),
            repository(tmp_path / "repo"),
            1,
            store,
            llm_analyzer=analyzer,
            allow_external_llm=True,
            run_id="run",
            as_of=NOW,
        )
        before = store.get_run_summary("run")
        configuration = run.configuration
        if change == "budget":
            configuration = configuration.model_copy(
                update={
                    "budgets": configuration.budgets.model_copy(update={"evidence_chars": 1}),
                }
            )
        elif change == "origin":
            configuration = configuration.model_copy(
                update={
                    "parameter_origins": {
                        **configuration.parameter_origins,
                        "temperature": "user_config",
                    },
                }
            )
        elif change == "prompt":
            configuration = configuration.model_copy(
                update={
                    "protocol": configuration.protocol.model_copy(
                        update={"prompt_version": "changed"}
                    ),
                }
            )
        else:
            current = clean_runtime.model_copy(update={"source_revision": "b" * 40})
            monkeypatch.setattr(run_configuration, "capture_engine_runtime", lambda: current)
        with pytest.raises(RunConfigurationError):
            retry_issue_llm(
                "run",
                1,
                store,
                llm_analyzer=analyzer,
                allow_external_llm=True,
                current_configuration=configuration,
            )
    assert calls == [True]
    assert store.get_run_summary("run") == before


@pytest.mark.parametrize("outcome", ["failure", "unknown", "version_changed"])
def test_codex_retry_obeys_version_and_unproven_subprocess_stop_boundary(
    tmp_path, clean_runtime, outcome
):
    from repo_issue_intelligence.agent_resume import retry_issue_llm

    store = new_store(tmp_path / "private")
    calls = []
    version = "codex-cli 1.2.3"

    def command_run(command, **options):
        if command == ["codex", "--version"]:
            return subprocess.CompletedProcess(command, 0, version + "\n", "")
        payload = json.loads(
            options["input"]
            .split("UNTRUSTED_DATA_BEGIN\n", 1)[1]
            .split("\nUNTRUSTED_DATA_END", 1)[0]
        )
        calls.append(payload)
        if len(calls) == 1:
            if outcome == "unknown":
                raise subprocess.TimeoutExpired(command, options["timeout"])
            return subprocess.CompletedProcess(command, 1, "", "synthetic failure")
        output = Path(command[command.index("--output-last-message") + 1])
        envelope = successful_response(payload["repository_evidence"]).json()
        output.write_text(envelope["choices"][0]["message"]["content"])
        return subprocess.CompletedProcess(command, 0, "", "")

    analyzer = CodexCLIIssueAnalyzer(run_command=command_run, auth_file=tmp_path / "no-auth")
    run_agent_v2(
        issues(1),
        repository(tmp_path / "repo"),
        1,
        store,
        llm_analyzer=analyzer,
        allow_external_llm=True,
        run_id="run",
        as_of=NOW,
    )
    before = store.get_run_summary("run")
    if outcome == "failure":
        retried = retry_issue_llm(
            "run",
            1,
            store,
            llm_analyzer=analyzer,
            allow_external_llm=True,
        )
        assert retried.llm_state == "succeeded"
        assert calls == [calls[0], calls[0]]
    else:
        if outcome == "version_changed":
            version = "codex-cli 9.9.9"
        with pytest.raises((StoreError, RunConfigurationError)):
            retry_issue_llm(
                "run",
                1,
                store,
                llm_analyzer=analyzer,
                allow_external_llm=True,
                recover_unknown=outcome == "unknown",
            )
        assert len(calls) == 1
        assert store.get_run_summary("run") == before


@pytest.mark.parametrize("previous_status", ["FAILED", "INTERRUPTED"])
def test_terminal_retry_clears_stale_control_failure_after_all_work_finishes(
    tmp_path, clean_runtime, previous_status
):
    from repo_issue_intelligence.agent_resume import retry_issue_llm

    store = new_store(tmp_path / "private")
    calls = []

    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        calls.append(payload)
        if len(calls) == 1:
            return httpx.Response(400, json={"error": "synthetic failure"})
        return successful_response(payload["repository_evidence"])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        analyzer = OpenAICompatibleIssueAnalyzer("test-key", client=client)
        run_agent_v2(
            issues(1),
            repository(tmp_path / "repo"),
            1,
            store,
            llm_analyzer=analyzer,
            allow_external_llm=True,
            run_id="run",
            as_of=NOW,
        )
        store.set_run_status("run", previous_status, expected_status="AWAITING_REVIEW")
        result = retry_issue_llm(
            "run",
            1,
            store,
            llm_analyzer=analyzer,
            allow_external_llm=True,
        )
    assert result.llm_state == "succeeded"
    assert store.get_run("run").status == "AWAITING_REVIEW"


@pytest.mark.parametrize("remaining", ["pending", "failed", "unknown"])
def test_terminal_retry_does_not_hide_unfinished_sibling_work(
    tmp_path, monkeypatch, clean_runtime, remaining
):
    from repo_issue_intelligence.agent_resume import retry_issue_llm

    store = new_store(tmp_path / "private")
    sent = []

    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        number = payload["issue"]["number"]
        sent.append(number)
        if number == 2:
            raise httpx.ReadTimeout("synthetic unknown sibling", request=request)
        if sent.count(1) == 1:
            return httpx.Response(400, json={"error": "synthetic failure"})
        return successful_response(payload["repository_evidence"])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        analyzer = OpenAICompatibleIssueAnalyzer("test-key", client=client)
        start = store.set_deterministic_state

        def interrupted_start(run_id, number, state, **kwargs):
            if number == 2:
                if remaining == "failed":
                    start(run_id, number, "failed")
                    raise RuntimeError("synthetic deterministic failure")
                raise KeyboardInterrupt
            return start(run_id, number, state, **kwargs)

        with monkeypatch.context() as fault:
            if remaining != "unknown":
                fault.setattr(store, "set_deterministic_state", interrupted_start)
                with pytest.raises((KeyboardInterrupt, RuntimeError)):
                    run_agent_v2(
                        issues(1, 2),
                        repository(tmp_path / "repo"),
                        2,
                        store,
                        llm_analyzer=analyzer,
                        allow_external_llm=True,
                        run_id="run",
                        as_of=NOW,
                    )
            else:
                run_agent_v2(
                    issues(1, 2),
                    repository(tmp_path / "repo"),
                    2,
                    store,
                    llm_analyzer=analyzer,
                    allow_external_llm=True,
                    run_id="run",
                    as_of=NOW,
                )
        sibling = store.get_issue("run", 2)
        result = retry_issue_llm(
            "run",
            1,
            store,
            llm_analyzer=analyzer,
            allow_external_llm=True,
        )
    assert result.llm_state == "succeeded"
    assert store.get_issue("run", 2) == sibling
    assert store.get_run("run").status == ("FAILED" if remaining == "failed" else "INTERRUPTED")
    assert sent == ([1, 2, 1] if remaining == "unknown" else [1, 1])


@pytest.mark.parametrize(
    "previous_status,error_type,expected_status",
    [
        ("AWAITING_REVIEW", KeyboardInterrupt, "INTERRUPTED"),
        ("AWAITING_REVIEW", SystemExit, "INTERRUPTED"),
        ("AWAITING_REVIEW", RuntimeError, "FAILED"),
        ("RUNNING", RuntimeError, "FAILED"),
        ("FAILED", KeyboardInterrupt, "INTERRUPTED"),
        ("INTERRUPTED", RuntimeError, "FAILED"),
        ("FAILED", RuntimeError, "FAILED"),
    ],
)
def test_retry_exception_records_current_execution_state(
    tmp_path, monkeypatch, clean_runtime, previous_status, error_type, expected_status
):
    from repo_issue_intelligence.agent_resume import retry_issue_llm

    store = new_store(tmp_path / "private")
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(400))) as client:
        analyzer = OpenAICompatibleIssueAnalyzer("test-key", client=client)
        run_agent_v2(
            issues(1),
            repository(tmp_path / "repo"),
            1,
            store,
            llm_analyzer=analyzer,
            allow_external_llm=True,
            run_id="run",
            as_of=NOW,
        )
        store.set_run_status("run", previous_status, expected_status="AWAITING_REVIEW")
        before = store.get_run_summary("run")
        failure = error_type("synthetic retry failure")

        def broken_analysis(*args, **kwargs):
            raise failure

        monkeypatch.setattr(analyzer, "analyze_v2", broken_analysis)
        with pytest.raises(error_type, match="synthetic retry failure") as caught:
            retry_issue_llm("run", 1, store, llm_analyzer=analyzer, allow_external_llm=True)
    assert caught.value is failure
    with store.writer_lock():
        after = store.get_run_summary("run")
    assert after.status == expected_status
    assert after.issues[0].deterministic_report == before.issues[0].deterministic_report
    assert after.issues[0].evidence_set_id == before.issues[0].evidence_set_id
    attempts = store.list_attempts("run", 1)
    assert attempts[0] == before.issues[0].latest_attempt
    assert attempts[-1].state == ("unknown" if expected_status == "INTERRUPTED" else "failure")
    shown = CliRunner().invoke(
        cli.app, ["agent-show", "run", "--protocol", "v2", "--database", str(store.path)]
    )
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output)["status"] == expected_status
