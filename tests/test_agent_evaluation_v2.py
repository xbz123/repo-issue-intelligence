import json
import os
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from repo_issue_intelligence.agent_evaluation import (
    AgentAnalysisCaseResult,
    save_agent_analysis_run,
)
from repo_issue_intelligence.agent_evaluation_v2 import (
    AgentAnalysisRunV2,
    aggregate_agent_analysis_v2,
    run_agent_analysis_evaluation_v2,
)
from repo_issue_intelligence.agent_store_v2 import AgentStoreV2
from repo_issue_intelligence.benchmark import BenchmarkCase, BenchmarkManifest
from repo_issue_intelligence.llm_client import OpenCodeIssueAnalyzer
from repo_issue_intelligence.models import IssueRecord


def test_v2_evaluation_requires_current_transfer_permission(tmp_path):
    with pytest.raises(ValueError, match="allow_external_llm"):
        run_agent_analysis_evaluation_v2(None, tmp_path, None)


def manifest_in_workspace(workspace):
    repository = workspace / "example--project"
    repository.mkdir(parents=True)
    (repository / "service.py").write_text("def refresh_token():\n    return 'bad'\n")
    for arguments in (
        ("init", "-q"),
        ("config", "user.email", "tests@example.invalid"),
        ("config", "user.name", "Tests"),
        ("remote", "add", "origin", "https://github.com/example/project.git"),
        ("add", "service.py"),
        ("commit", "-qm", "pre-fix"),
    ):
        subprocess.run(["git", "-C", str(repository), *arguments], check=True, capture_output=True)
    sha = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True
    ).strip()
    (repository / "service.py").write_text("def refresh_token():\n    return 'fixed'\n")
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-qam", "post-fix"],
        check=True,
        capture_output=True,
    )
    now = datetime(2026, 9, 8, tzinfo=UTC)
    return BenchmarkManifest(
        name="v2-local",
        version=1,
        cases=[
            BenchmarkCase(
                id="token",
                tier="main",
                repository="example/project",
                issue_number=1,
                issue_updated_at=now,
                issue_snapshot=IssueRecord(
                    number=1,
                    title="refresh_token returns incorrect token",
                    body="service.py refresh_token returns the wrong value",
                    created_at=now,
                    updated_at=now,
                ),
                fix_pr_number=2,
                pre_fix_sha=sha,
                expected_files=["service.py"],
            )
        ],
    )


def response_for(request):
    evidence = json.loads(json.loads(request.content)["messages"][1]["content"])[
        "repository_evidence"
    ]
    ids = [item["id"] for item in evidence]
    return {
        "summary": "Token may be incorrect",
        "issue_type": "bug",
        "reproduction_completeness": "partial",
        "evidence_observations": [
            {
                "evidence_id": item,
                "alignment": "supports_issue",
                "observation": "Token function is present",
            }
            for item in ids
        ],
        "hypothesis": {
            "description": "Token may be incorrect",
            "confidence": 0.7,
            "evidence_ids": [ids[0]],
            "missing_evidence": ["Runtime trace"],
        },
    }


def test_v2_evaluation_uses_committed_reports_and_response_identity(tmp_path):
    manifest = manifest_in_workspace(tmp_path / "workspace")

    def handler(request):
        assert "return 'bad'" in request.content.decode()
        # Changing the current checkout cannot change sealed evaluation evidence.
        (tmp_path / "workspace" / "example--project" / "service.py").unlink()
        return httpx.Response(
            200,
            headers={"x-request-id": "request-1"},
            json={
                "model": "reported-B",
                "usage": {"prompt_tokens": 12, "completion_tokens": 7},
                "choices": [{"message": {"content": json.dumps(response_for(request))}}],
            },
        )

    with httpx.Client(
        base_url="https://example.test/v1", transport=httpx.MockTransport(handler)
    ) as client:
        analyzer = OpenCodeIssueAnalyzer("test-key", model="requested-A", client=client)
        run = run_agent_analysis_evaluation_v2(
            manifest, tmp_path / "workspace", analyzer, allow_external_llm=True
        )
    result = run.results[0]
    assert run.protocol == "v2"
    assert result.commit_oid == manifest.cases[0].pre_fix_sha
    assert result.persistence_verified is True
    assert result.execution_succeeded is True
    assert result.analysis_succeeded is True
    assert result.expected_file_evidence_recall == 1.0
    assert result.hypothesis_expected_file_recall == 1.0
    assert result.attempts[0].request.model == "requested-A"
    assert result.attempts[0].reported.model == "reported-B"
    assert run.overall.execution_cases == 1
    assert run.overall.provider_cases == 1
    assert run.overall.no_evidence_cases == 0
    assert run.overall.grounding_cases == 1
    assert run.overall.input_tokens == 12
    assert run.overall.output_tokens == 7
    assert result.database_path == f".agent-evaluation-v2/{result.run_id}/agent.sqlite3"
    assert not Path(result.database_path).is_absolute()
    database = tmp_path / "workspace" / result.database_path
    assert database.stat().st_mode & 0o777 == 0o600
    assert database.parent.stat().st_mode & 0o777 == 0o700
    restored = AgentStoreV2(database)
    assert restored.get_run_summary(result.run_id).status == "AWAITING_REVIEW"
    assert restored.list_attempts(result.run_id, 1) == result.attempts
    evidence = restored.read_evidence(result.run_id, 1)
    assert evidence.evidence_set_id == result.evidence_set_id
    assert "return 'bad'" in evidence.items[0].content


def test_v2_no_evidence_has_execution_but_no_provider_or_grounding_denominator(tmp_path):
    manifest = manifest_in_workspace(tmp_path / "workspace")

    def handler(request):
        pytest.fail("Empty sealed evidence must not be sent")

    with httpx.Client(
        base_url="https://example.test/v1", transport=httpx.MockTransport(handler)
    ) as client:
        analyzer = OpenCodeIssueAnalyzer("test-key", client=client)
        run = run_agent_analysis_evaluation_v2(
            manifest,
            tmp_path / "workspace",
            analyzer,
            max_evidence_chars=1,
            allow_external_llm=True,
        )
    result = run.results[0]
    assert result.persistence_verified is True
    assert result.skipped_no_evidence is True
    assert result.attempts == ()
    assert result.analysis is None
    assert run.overall.execution_cases == 1
    assert run.overall.execution_successes == 1
    assert run.overall.no_evidence_eligible_cases == 1
    assert run.overall.no_evidence_cases == 1
    assert run.overall.no_evidence_rate == 1.0
    assert run.overall.provider_cases == 0
    assert run.overall.provider_success_rate is None
    assert run.overall.grounding_cases == 0
    assert run.overall.grounding_hit_rate is None
    assert run.overall.input_tokens is None


@pytest.mark.parametrize(
    "provider_cases",
    [(False, False), (False, True), (True, False), (True, False, True), (True, True)],
)
def test_v2_delay_only_occurs_between_provider_cases(tmp_path, monkeypatch, provider_cases):
    manifest = manifest_in_workspace(tmp_path / "workspace")
    template = manifest.cases[0]
    repository = tmp_path / "workspace" / "example--project"
    (repository / "service.py").write_text("")
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-qam", "empty evidence"],
        check=True,
        capture_output=True,
    )
    empty_sha = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True
    ).strip()
    manifest.cases = [
        template.model_copy(
            update={
                "id": f"case-{index}",
                "pre_fix_sha": template.pre_fix_sha if has_evidence else empty_sha,
            }
        )
        for index, has_evidence in enumerate(provider_cases)
    ]
    events = []
    monkeypatch.setattr(
        "repo_issue_intelligence.agent_evaluation_v2.sleep", lambda delay: events.append(delay)
    )

    def handler(request):
        events.append("send")
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(response_for(request))}}]}
        )

    with httpx.Client(
        base_url="https://example.test/v1", transport=httpx.MockTransport(handler)
    ) as client:
        run = run_agent_analysis_evaluation_v2(
            manifest,
            tmp_path / "workspace",
            OpenCodeIssueAnalyzer("test-key", client=client),
            llm_delay_seconds=7,
            allow_external_llm=True,
        )
    count = sum(provider_cases)
    assert run.overall.provider_cases == count
    assert run.overall.no_evidence_cases == len(provider_cases) - count
    assert events == ([] if count == 0 else ["send"] + [7, "send"] * (count - 1))


def test_v2_missing_observations_stay_null_and_protocols_cannot_mix(tmp_path, monkeypatch):
    manifest = manifest_in_workspace(tmp_path / "workspace")
    manifest.cases.append(manifest.cases[0].model_copy(update={"id": "token-without-metadata"}))
    requests = []
    waits = []
    monkeypatch.setattr("repo_issue_intelligence.agent_evaluation_v2.sleep", waits.append)

    def handler(request):
        requests.append(request)
        metadata = {"model": "reported-B", "usage": {"prompt_tokens": 0, "completion_tokens": 7}}
        return httpx.Response(
            200,
            json={
                **(metadata if len(requests) == 1 else {}),
                "choices": [{"message": {"content": json.dumps(response_for(request))}}],
            },
        )

    with httpx.Client(
        base_url="https://example.test/v1", transport=httpx.MockTransport(handler)
    ) as client:
        analyzer = OpenCodeIssueAnalyzer("test-key", model="requested-A", client=client)
        run = run_agent_analysis_evaluation_v2(
            manifest,
            tmp_path / "workspace",
            analyzer,
            llm_delay_seconds=121,
            allow_external_llm=True,
        )
    first, missing = run.results
    assert first.attempts[0].reported.input_tokens == 0
    assert missing.attempts[0].request.model == "requested-A"
    assert missing.attempts[0].reported.model is None
    assert missing.attempts[0].reported.input_tokens is None
    assert missing.attempts[0].reported.output_tokens is None
    assert run.overall.provider_cases == 2
    assert run.overall.provider_success_rate == 1.0
    assert run.overall.input_tokens is None
    assert run.overall.output_tokens is None
    assert run.overall.input_token_observations == 1
    assert run.overall.output_token_observations == 1
    assert waits == [60, 60, 1]
    missing_only = aggregate_agent_analysis_v2([missing])
    assert missing_only.input_tokens is None
    assert missing_only.input_token_observations == 0
    output = tmp_path / "v2.json"
    output.touch()
    output.chmod(0o644)
    previous_umask = os.umask(0o022)
    try:
        save_agent_analysis_run(run, output)
    finally:
        os.umask(previous_umask)
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert AgentAnalysisRunV2.model_validate_json(output.read_text()) == run
    v1 = AgentAnalysisCaseResult(
        case_id="legacy",
        tier="main",
        repository="example/project",
        issue_number=1,
        pre_fix_sha="a" * 40,
    )
    with pytest.raises(ValueError, match="exclusively V2"):
        aggregate_agent_analysis_v2([first, v1])


@pytest.mark.parametrize("delay", [-1, float("inf"), float("nan")])
def test_v2_evaluation_rejects_invalid_delay_before_preparation(tmp_path, delay):
    with pytest.raises(ValueError, match="llm_delay_seconds"):
        run_agent_analysis_evaluation_v2(
            None,
            tmp_path,
            None,
            llm_delay_seconds=delay,
            allow_external_llm=True,
        )


def test_v2_evaluation_does_not_create_ledger_through_retention_symlink(tmp_path):
    manifest = manifest_in_workspace(tmp_path / "workspace")
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "workspace" / ".agent-evaluation-v2").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        run_agent_analysis_evaluation_v2(
            manifest,
            tmp_path / "workspace",
            None,
            allow_external_llm=True,
        )
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize(
    "invalid_configuration",
    [
        {"max_evidence_chars": 0},
        {"parameter_origins": {"temperature": "invalid-origin"}},
    ],
)
def test_v2_evaluation_propagates_failure_before_an_auditable_run_exists(
    tmp_path,
    invalid_configuration,
):
    manifest = manifest_in_workspace(tmp_path / "workspace")

    def handler(request):
        pytest.fail("Invalid run configuration must not be sent")

    with httpx.Client(
        base_url="https://example.test/v1", transport=httpx.MockTransport(handler)
    ) as client:
        analyzer = OpenCodeIssueAnalyzer("test-key", client=client)
        with pytest.raises(ValueError):
            run_agent_analysis_evaluation_v2(
                manifest,
                tmp_path / "workspace",
                analyzer,
                allow_external_llm=True,
                **invalid_configuration,
            )


def test_v2_provider_failure_keeps_execution_evidence_and_attempt_denominators(tmp_path):
    manifest = manifest_in_workspace(tmp_path / "workspace")

    def handler(request):
        return httpx.Response(
            200,
            json={
                "model": "reported-B",
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
                "choices": [{"message": {"content": "malformed private provider payload"}}],
            },
        )

    with httpx.Client(
        base_url="https://example.test/v1", transport=httpx.MockTransport(handler)
    ) as client:
        analyzer = OpenCodeIssueAnalyzer("test-key", client=client)
        run = run_agent_analysis_evaluation_v2(
            manifest,
            tmp_path / "workspace",
            analyzer,
            max_llm_attempts=2,
            allow_external_llm=True,
        )
    result = run.results[0]
    assert result.persistence_verified is True
    assert result.agent_status == "AWAITING_REVIEW"
    assert result.execution_succeeded is True
    assert result.analysis_succeeded is False
    assert result.expected_file_evidence_recall == 1.0
    assert result.hypothesis_expected_file_recall is None
    assert result.selected_analysis_attempt_id is None
    assert result.attempts[0].state == "failure"
    assert "private provider payload" not in result.model_dump_json()
    assert run.overall.execution_success_rate == 1.0
    assert run.overall.provider_cases == 1
    assert run.overall.provider_attempts == 1
    assert run.overall.provider_successes == 0
    assert run.overall.provider_success_rate == 0.0
    assert run.overall.no_evidence_cases == 0
    assert run.overall.grounding_cases == 0
    assert run.overall.grounding_hit_rate is None
    assert run.overall.evidence_quality_cases == 1
    assert run.overall.input_tokens == 10
    assert run.overall.output_tokens == 2


def test_v2_evaluation_keeps_progress_and_stops_after_persisted_fatal_error(tmp_path):
    manifest = manifest_in_workspace(tmp_path / "workspace")
    manifest.cases.extend(
        [
            manifest.cases[0].model_copy(update={"id": "fatal"}),
            manifest.cases[0].model_copy(update={"id": "must-not-run"}),
        ]
    )
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) > 1:
            raise RuntimeError("adapter programming error secret=private-detail")
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(response_for(request))}}],
            },
        )

    with httpx.Client(
        base_url="https://example.test/v1", transport=httpx.MockTransport(handler)
    ) as client:
        analyzer = OpenCodeIssueAnalyzer("test-key", client=client)
        run = run_agent_analysis_evaluation_v2(
            manifest,
            tmp_path / "workspace",
            analyzer,
            allow_external_llm=True,
        )
    assert run.status == "FAILED"
    assert run.completed is False
    assert run.planned_cases == 3
    assert run.attempted_cases == 2
    assert [result.case_id for result in run.results] == ["token", "fatal"]
    assert run.results[0].analysis_succeeded is True
    failed = run.results[1]
    assert failed.agent_status == "FAILED"
    assert failed.execution_succeeded is False
    assert failed.deterministic_state == "succeeded"
    assert failed.error_category == "execution_fatal"
    assert failed.persistence_verified is True
    assert failed.expected_file_evidence_recall == 1.0
    assert run.overall.execution_cases == 2
    assert run.overall.execution_successes == 1
    assert run.overall.execution_failures == 1
    assert run.overall.execution_success_rate == 0.5
    assert run.overall.grounding_cases == 1
    assert run.overall.error_categories["execution_fatal"] == 1
    assert "private-detail" not in run.model_dump_json()
    restored = AgentStoreV2(tmp_path / "workspace" / failed.database_path)
    assert restored.get_run_summary(failed.run_id).status == "FAILED"
    assert restored.list_attempts(failed.run_id, 1) == failed.attempts
    assert restored.read_evidence(failed.run_id, 1).evidence_set_id == failed.evidence_set_id
    output = tmp_path / "partial-v2.json"
    save_agent_analysis_run(run, output)
    assert AgentAnalysisRunV2.model_validate_json(output.read_text()) == run
