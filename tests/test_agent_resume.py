"""PR5B checks the public recovery interface with persisted temporary runs."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from test_agent_store_v2 import new_store
from test_issue_execution import NOW, issues, repository, successful_response
from typer.testing import CliRunner

from repo_issue_intelligence import cli, run_configuration
from repo_issue_intelligence.issue_execution import run_agent_v2
from repo_issue_intelligence.llm_client import OpenAICompatibleIssueAnalyzer
from repo_issue_intelligence.protocol_v2_models import RepositoryCaptureMode
from repo_issue_intelligence.repository_view import DeterministicResumeError
from repo_issue_intelligence.run_configuration import RunConfigurationError


@pytest.fixture
def clean_runtime(monkeypatch):
    # Runtime provenance is an OS/Git observation; exercise dirty/unknown below separately.
    runtime = run_configuration.capture_engine_runtime().model_copy(update={"source_dirty": False})
    monkeypatch.setattr(run_configuration, "capture_engine_runtime", lambda *a, **kw: runtime)
    return runtime


def test_committed_resume_keeps_saved_report_and_original_order(
    tmp_path, monkeypatch, clean_runtime
):
    from repo_issue_intelligence.agent_resume import resume_agent_run

    root = repository(tmp_path / "repo")
    store = new_store(tmp_path / "private")
    save = store.save_deterministic_result

    def interrupted_save(*args, **kwargs):
        save(*args, **kwargs)
        raise KeyboardInterrupt

    with monkeypatch.context() as fault:
        fault.setattr(store, "save_deterministic_result", interrupted_save)
        with pytest.raises(KeyboardInterrupt):
            run_agent_v2(issues(2, 1), root, 2, store, run_id="run", as_of=NOW)
    original = store.get_run("run")
    saved = store.get_issue("run", 2)
    (root / "service.py").write_text("def refresh_token():\n    return 'changed'\n")

    resumed = resume_agent_run("run", store)

    assert resumed.status == "AWAITING_REVIEW"
    assert resumed.inputs == original.inputs
    assert resumed.configuration == original.configuration
    assert [item.issue_number for item in store.list_issues("run")] == [2, 1]
    assert store.get_issue("run", 2) == saved
    assert store.get_issue("run", 1).deterministic_state == "succeeded"
    assert store.list_attempts("run", 2) == ()


@pytest.mark.parametrize("runtime_state", ["dirty", "unknown"])
def test_resume_refuses_unverifiable_engine_even_when_observations_match(
    tmp_path, monkeypatch, clean_runtime, runtime_state
):
    from repo_issue_intelligence.agent_resume import resume_agent_run

    runtime = clean_runtime.model_copy(
        update={"source_dirty": True} if runtime_state == "dirty" else {"source_revision": None}
    )
    monkeypatch.setattr(run_configuration, "capture_engine_runtime", lambda *a, **kw: runtime)
    store = new_store(tmp_path / "private")
    run = run_agent_v2(issues(1), repository(tmp_path / "repo"), 1, store, as_of=NOW)
    store.set_run_status(run.run_id, "INTERRUPTED", expected_status="AWAITING_REVIEW")
    before = store.get_run_summary(run.run_id)
    with pytest.raises(RunConfigurationError, match="clean"):
        resume_agent_run(run.run_id, store)
    assert store.get_run_summary(run.run_id) == before


@pytest.mark.parametrize("state", ["running", "failed"])
def test_resume_retries_only_uncommitted_deterministic_stage(
    tmp_path, monkeypatch, clean_runtime, state
):
    from repo_issue_intelligence.agent_resume import resume_agent_run

    store = new_store(tmp_path / "private")
    root = repository(tmp_path / "repo")
    with monkeypatch.context() as fault:

        def fail(*args, **kwargs):
            raise KeyboardInterrupt if state == "running" else ValueError("synthetic failure")

        fault.setattr("repo_issue_intelligence.issue_execution.investigate", fail)
        with pytest.raises((KeyboardInterrupt, ValueError)):
            run_agent_v2(issues(1), root, 1, store, as_of=NOW, run_id="run")
    assert store.get_issue("run", 1).deterministic_state == state
    resume_agent_run("run", store)
    assert store.get_issue("run", 1).deterministic_state == "succeeded"


@pytest.mark.parametrize("checkpoint", ["deterministic", "evidence", "started", "remote", "result"])
def test_resume_continues_exact_unfinished_stage_without_replaying_attempts(
    tmp_path, monkeypatch, clean_runtime, checkpoint
):
    from repo_issue_intelligence.agent_resume import resume_agent_run

    root = repository(tmp_path / "repo")
    store = new_store(tmp_path / "private")
    received = []

    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        received.append(payload)
        return successful_response(payload["repository_evidence"])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        analyzer = OpenAICompatibleIssueAnalyzer("test-key", client=client)
        with monkeypatch.context() as fault:
            if checkpoint == "remote":
                completed_response = analyzer.analyze_v2

                def interrupted_response(*args, **kwargs):
                    completed_response(*args, **kwargs)
                    raise KeyboardInterrupt

                fault.setattr(analyzer, "analyze_v2", interrupted_response)
            else:
                name = {
                    "deterministic": "save_deterministic_result",
                    "evidence": "seal_evidence_set",
                    "started": "start_attempt",
                    "result": "finalize_attempt",
                }[checkpoint]
                operation = getattr(store, name)

                def interrupted(*args, **kwargs):
                    operation(*args, **kwargs)
                    raise KeyboardInterrupt

                fault.setattr(store, name, interrupted)
            with pytest.raises(KeyboardInterrupt):
                run_agent_v2(
                    issues(1, 2),
                    root,
                    2,
                    store,
                    llm_analyzer=analyzer,
                    allow_external_llm=True,
                    run_id="run",
                    as_of=NOW,
                )
        first = store.get_issue("run", 1)
        attempts = store.list_attempts("run", 1)
        resumed = resume_agent_run("run", store, llm_analyzer=analyzer, allow_external_llm=True)
    assert resumed.status == "AWAITING_REVIEW"
    assert store.get_issue("run", 1).deterministic_report == first.deterministic_report
    assert store.get_issue("run", 2).llm_state == "succeeded"
    if checkpoint in {"deterministic", "evidence"}:
        assert store.get_issue("run", 1).llm_state == "succeeded"
        assert [payload["issue"]["number"] for payload in received] == [1, 2]
    else:
        if checkpoint == "started":
            (settled,) = store.list_attempts("run", 1)
            assert settled.state == "unknown"
            assert settled.attempt_id == attempts[0].attempt_id
            assert settled.request == attempts[0].request
            assert settled.finished_at is not None
        else:
            assert store.list_attempts("run", 1) == attempts
        assert [payload["issue"]["number"] for payload in received] == (
            [2] if checkpoint == "started" else [1, 2]
        )
    if first.evidence_set_id:
        assert store.get_issue("run", 1).evidence_set_id == first.evidence_set_id


def test_cli_resume_requires_opt_in_and_keeps_default_v1_commands(tmp_path, clean_runtime):
    store = new_store(tmp_path / "private")
    run = run_agent_v2(issues(1), repository(tmp_path / "repo"), 1, store, as_of=NOW)
    store.set_run_status(run.run_id, "INTERRUPTED", expected_status="AWAITING_REVIEW")
    runner = CliRunner()
    args = ["agent-resume", run.run_id, "--database", str(store.path)]
    refused = runner.invoke(cli.app, args)
    assert refused.exit_code == 2
    result = runner.invoke(cli.app, [*args, "--protocol", "v2"])
    assert result.exit_code == 0, result.output
    assert store.get_run(run.run_id).status == "AWAITING_REVIEW"
    assert runner.invoke(cli.app, ["agent-show", "--help"]).exit_code == 0
    assert "agent-recover" not in runner.invoke(cli.app, ["--help"]).output


def test_changed_configuration_creates_a_parent_linked_run_without_rewriting_original(tmp_path):
    store = new_store(tmp_path / "private")
    root = repository(tmp_path / "repo")
    parent = run_agent_v2(issues(1), root, 1, store, as_of=NOW)
    original = store.get_run_summary(parent.run_id)
    child = run_agent_v2(
        issues(1),
        root,
        1,
        store,
        as_of=NOW,
        parent_run_id=parent.run_id,
        max_evidence_chars=1234,
    )
    assert child.run_id != parent.run_id
    assert child.parent_run_id == parent.run_id
    assert child.configuration.budgets.evidence_chars == 1234
    assert store.get_run_summary(parent.run_id) == original
    assert store.get_issue(child.run_id, 1).review_version == 0


def test_tracked_worktree_deterministic_resume_is_refused_before_mutation(tmp_path, clean_runtime):
    from repo_issue_intelligence.agent_resume import resume_agent_run

    store = new_store(tmp_path / "private")
    run = run_agent_v2(
        issues(1),
        repository(tmp_path / "repo"),
        1,
        store,
        as_of=NOW,
        capture_mode=RepositoryCaptureMode.TRACKED_WORKTREE,
    )
    store.set_run_status(run.run_id, "INTERRUPTED", expected_status="AWAITING_REVIEW")
    before = store.get_run_summary(run.run_id)
    with pytest.raises(DeterministicResumeError):
        resume_agent_run(run.run_id, store)
    assert store.get_run_summary(run.run_id) == before


def test_clean_engine_resumes_across_real_process_exit_without_runtime_stubs(tmp_path):
    engine = tmp_path / "engine"
    shutil.copytree(
        Path("src"),
        engine / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (engine / ".gitignore").write_text("__pycache__/\n")
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.name", "Protocol tests"],
        ["config", "user.email", "tests@example.invalid"],
        ["add", "."],
        ["commit", "-qm", "Synthetic clean engine"],
    ):
        subprocess.run(["git", "-C", str(engine), *args], capture_output=True, check=True)
    root = repository(tmp_path / "repo")
    store = new_store(tmp_path / "private")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("LLM_", "OPENCODE_", "CODEX_"))
    }
    environment["PYTHONPATH"] = str(engine / "src")
    started = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os,sys\nfrom pathlib import Path\nfrom datetime import UTC,datetime\n"
            "from repo_issue_intelligence.agent_store_v2 import AgentStoreV2\n"
            "from repo_issue_intelligence.issue_execution import run_agent_v2\n"
            "from repo_issue_intelligence.models import IssueRecord\n"
            "class CrashAfterCommit(AgentStoreV2):\n"
            " def save_deterministic_result(self,*args,**kwargs):\n"
            "  super().save_deterministic_result(*args,**kwargs)\n  os._exit(23)\n"
            "now=datetime(2026,9,9,tzinfo=UTC)\n"
            "items=[IssueRecord(number=n,title='refresh_token returns wrong value',"
            "body='service.py refresh_token',created_at=now,updated_at=now) for n in (1,2)]\n"
            "run_agent_v2(items,Path(sys.argv[2]),2,CrashAfterCommit(sys.argv[1]),run_id='run')\n",
            str(store.path),
            str(root),
        ],
        cwd=engine,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert started.returncode == 23, started.stderr
    first = store.get_issue("run", 1)
    assert store.get_run("run").configuration.engine.source_dirty is False
    resumed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from repo_issue_intelligence.cli import app; app()",
            "agent-resume",
            "run",
            "--protocol",
            "v2",
            "--database",
            str(store.path),
        ],
        cwd=engine,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert resumed.returncode == 0, resumed.stderr
    assert store.get_issue("run", 1) == first
    assert store.get_issue("run", 2).deterministic_state == "succeeded"
    assert store.get_run("run").status == "AWAITING_REVIEW"


def test_configuration_reconstruction_rejects_unknown_budget_origin_keys():
    from repo_issue_intelligence.issue_execution import capture_execution_configuration

    with pytest.raises(RunConfigurationError, match="origin"):
        capture_execution_configuration(
            None,
            top_k=1,
            max_evidence_chars=100,
            max_evidence_lines=10,
            max_attempts=1,
            parameter_origins={"budget.unrecognized": "user_config"},
        )
