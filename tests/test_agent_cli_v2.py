import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from repo_issue_intelligence import cli
from repo_issue_intelligence.agent_database import create_v2_database
from repo_issue_intelligence.agent_store_v2 import AgentStoreV2
from repo_issue_intelligence.llm_client import OpenAICompatibleIssueAnalyzer

runner = CliRunner()


@pytest.mark.parametrize("command", ["agent-run", "agent-evaluate"])
def test_v2_external_transfer_requires_current_permission_before_builder(
    tmp_path: Path, monkeypatch, command: str
) -> None:
    calls = []

    def forbidden_builder(*args, **kwargs):
        calls.append(True)
        raise AssertionError("No analyzer may be constructed without current permission")

    monkeypatch.setattr(cli, "_build_issue_analyzer", forbidden_builder)
    monkeypatch.setattr(cli, "_build_analysis_evaluator", forbidden_builder)
    args = [command, str(tmp_path / "input.json"), "--protocol", "v2"]
    if command == "agent-run":
        args += ["--repo", str(tmp_path), "--llm"]
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 2
    assert "--allow-external-llm" in result.output
    assert calls == []


@pytest.mark.parametrize("database_kind", ["omitted", "missing", "legacy"])
def test_v2_run_requires_explicit_existing_private_database(tmp_path, database_kind):
    from repo_issue_intelligence.agent_store import AgentStore

    database = tmp_path / "agent.sqlite3"
    if database_kind == "legacy":
        AgentStore(database)
    before = database.read_bytes() if database.exists() else None
    args = [
        "agent-run",
        "examples/issues.json",
        "--repo",
        "examples/demo_repository",
        "--protocol",
        "v2",
        "--output",
        str(tmp_path / "run.json"),
    ]
    if database_kind != "omitted":
        args += ["--database", str(database)]
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 2
    assert "database" in result.output.lower()
    assert (database.read_bytes() if database.exists() else None) == before
    assert not (tmp_path / "run.json").exists()


def private_database(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    database = directory / "v2.sqlite3"
    create_v2_database(database)
    return database


@pytest.mark.parametrize("model_options", [[], ["--llm-model", "inactive-model"]])
def test_v2_disabled_run_and_show_use_summary_without_big_snapshots(tmp_path, model_options):
    database = private_database(tmp_path)
    output = tmp_path / "run.json"
    result = runner.invoke(
        cli.app,
        [
            "agent-run",
            "examples/issues.json",
            "--repo",
            "examples/demo_repository",
            "--protocol",
            "v2",
            "--database",
            str(database),
            "--output",
            str(output),
            *model_options,
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(output.read_text())
    assert payload["protocol"] == "v2"
    assert payload["status"] == "AWAITING_REVIEW"
    assert payload["issues"][0]["llm_state"] == "disabled"
    assert payload["issues"][0]["deterministic_state"] == "succeeded"
    assert payload["snapshot"]["analysis_root"].endswith("examples/demo_repository")
    assert "repository_map" not in payload
    shown = runner.invoke(
        cli.app,
        [
            "agent-show",
            payload["run_id"],
            "--protocol",
            "v2",
            "--database",
            str(database),
        ],
    )
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output) == payload


def demo_checkout(tmp_path, *, unsupported_inside=False):
    root = tmp_path / "repository"
    demo = root / "examples" / "demo_repository"
    shutil.copytree(Path("examples/demo_repository"), demo)
    outside = root / "unrelated"
    outside.mkdir()
    (outside / "filtered.py").write_text("print('unrelated')\n")
    (root / ".gitattributes").write_text("unrelated/*.py filter=unsupported\n")
    pointer = (demo if unsupported_inside else outside) / "pointer.py"
    pointer.write_text(
        "version https://git-lfs.github.com/spec/v1\n" + "oid sha256:" + "a" * 64 + "\nsize 20\n"
    )
    for command in (
        ["init", "-q"],
        ["add", "."],
        [
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "Demo fixture",
        ],
    ):
        subprocess.run(["git", "-C", str(root), *command], check=True, capture_output=True)
    return demo


def fake_api(monkeypatch, handler=None):
    requests = []

    def transport(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if handler is not None:
            return handler(payload)
        evidence = json.loads(payload["messages"][1]["content"])["repository_evidence"]
        response = {
            "summary": "Refresh token handling requires verification.",
            "issue_type": "bug",
            "reproduction_completeness": "partial",
            "evidence_observations": [
                {
                    "evidence_id": item["id"],
                    "alignment": "supports_issue",
                    "observation": "The source contains the relevant path.",
                }
                for item in evidence
            ],
            "hypothesis": {
                "description": "Expired token errors may escape the handler.",
                "confidence": 0.7,
                "evidence_ids": [evidence[0]["id"]],
                "missing_evidence": ["A runtime reproduction"],
            },
        }
        return httpx.Response(
            200,
            json={
                "model": "reported-B",
                "choices": [{"message": {"content": json.dumps(response)}}],
            },
        )

    def builder(*args, **kwargs):
        return OpenAICompatibleIssueAnalyzer(
            "test-key",
            model="requested-A",
            temperature=kwargs.get("temperature", 0.1),
            seed=kwargs.get("seed"),
            client=httpx.Client(
                base_url="https://example.test/v1", transport=httpx.MockTransport(transport)
            ),
        )

    monkeypatch.setattr(cli, "_build_issue_analyzer", builder)
    return requests


@pytest.mark.parametrize("model_source", ["cli", "environment"])
def test_v2_demo_committed_subdirectory_fake_transport_and_ledger(
    tmp_path, monkeypatch, model_source
):
    if model_source == "environment":
        monkeypatch.setenv("LLM_MODEL", "requested-A")
    database = private_database(tmp_path)
    demo = demo_checkout(tmp_path)
    # Committed capture ignores untracked content within the analysis directory.
    (demo / "untracked_decoy.py").write_text("PRIVATE_WORKTREE_DECOY\n")
    requests = fake_api(monkeypatch)
    output = tmp_path / "run.json"
    result = runner.invoke(
        cli.app,
        [
            "agent-run",
            "examples/issues.json",
            "--repo",
            str(demo),
            "--protocol",
            "v2",
            "--database",
            str(database),
            "--output",
            str(output),
            "--llm",
            "--allow-external-llm",
            *(["--llm-model", "requested-A"] if model_source == "cli" else []),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(output.read_text())
    execution = payload["issues"][0]
    assert execution["llm_state"] == "succeeded"
    assert execution["attempt_count"] == 1
    assert payload["configuration"]["parameter_origins"]["requested_model"] == "user_config"
    assert len(requests) == 1
    assert "PRIVATE_WORKTREE_DECOY" not in json.dumps(requests)
    sent = json.loads(requests[0]["messages"][1]["content"])["repository_evidence"]
    store = AgentStoreV2(database)
    _, ledger = store.evidence_lookup(payload["run_id"], execution["issue_number"])
    assert {item["id"]: item["content"] for item in sent} == {
        key: item.content for key, item in ledger.items()
    }
    attempts = store.list_attempts(payload["run_id"], execution["issue_number"])
    assert attempts[0].request.model == "requested-A"
    assert attempts[0].reported.model == "reported-B"
    assert attempts[0].reported.seed is None
    shown = runner.invoke(
        cli.app,
        [
            "agent-show",
            payload["run_id"],
            "--protocol",
            "v2",
            "--database",
            str(database),
        ],
    )
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output) == payload


def test_v2_demo_refuses_in_scope_unsupported_before_sending(tmp_path, monkeypatch):
    database = private_database(tmp_path)
    demo = demo_checkout(tmp_path, unsupported_inside=True)
    requests = fake_api(monkeypatch)
    result = runner.invoke(
        cli.app,
        [
            "agent-run",
            "examples/issues.json",
            "--repo",
            str(demo),
            "--protocol",
            "v2",
            "--database",
            str(database),
            "--llm",
            "--allow-external-llm",
            "--output",
            str(tmp_path / "run.json"),
        ],
    )
    assert result.exit_code == 2
    assert "repository capture refused" in result.output
    assert requests == []


def test_v2_cli_rejects_second_writer_under_public_lock(tmp_path):
    database = private_database(tmp_path)
    with AgentStoreV2(database).writer_lock():
        result = runner.invoke(
            cli.app,
            [
                "agent-run",
                "examples/issues.json",
                "--repo",
                "examples/demo_repository",
                "--protocol",
                "v2",
                "--database",
                str(database),
                "--output",
                str(tmp_path / "run.json"),
            ],
        )
    assert result.exit_code == 2
    assert "single-writer" in result.output
    assert not (tmp_path / "run.json").exists()


@pytest.mark.parametrize("mode,exit_code", [("committed", 2), ("tracked_worktree", 0)])
def test_v2_capture_mode_controls_dirty_tracked_input(tmp_path, mode, exit_code):
    database = private_database(tmp_path)
    demo = demo_checkout(tmp_path)
    (demo / "auth_service.py").write_text("def refresh_token():\n    return None\n")
    output = tmp_path / "run.json"
    result = runner.invoke(
        cli.app,
        [
            "agent-run",
            "examples/issues.json",
            "--repo",
            str(demo),
            "--protocol",
            "v2",
            "--database",
            str(database),
            "--capture-mode",
            mode,
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == exit_code, result.output
    if exit_code:
        assert "repository capture refused" in result.output
        assert not output.exists()
    else:
        payload = json.loads(output.read_text())
        assert payload["snapshot"]["analysis_scope_dirty"] is True
        assert payload["issues"][0]["deterministic_state"] == "succeeded"


@pytest.mark.parametrize("explicit", [False, True])
def test_v2_budget_origins_distinguish_defaults_from_cli_and_environment(
    tmp_path, monkeypatch, explicit
):
    for name in (
        "LLM_MAX_EVIDENCE_CHARS",
        "OPENCODE_MAX_EVIDENCE_CHARS",
        "LLM_MAX_LINES_PER_EVIDENCE",
        "OPENCODE_MAX_LINES_PER_EVIDENCE",
    ):
        monkeypatch.delenv(name, raising=False)
    if explicit:
        monkeypatch.setenv("LLM_MAX_EVIDENCE_CHARS", "4000")
        monkeypatch.setenv("LLM_MAX_LINES_PER_EVIDENCE", "20")
    database = private_database(tmp_path)
    output = tmp_path / "run.json"
    result = runner.invoke(
        cli.app,
        [
            "agent-run",
            "examples/issues.json",
            "--repo",
            "examples/demo_repository",
            "--protocol",
            "v2",
            "--database",
            str(database),
            "--output",
            str(output),
            *(["--max-llm-attempts", "2"] if explicit else []),
        ],
    )
    assert result.exit_code == 0, result.output
    origins = json.loads(output.read_text())["configuration"]["parameter_origins"]
    expected = "user_config" if explicit else "client_default"
    assert {key: origins[key] for key in ("retry_policy", "evidence_chars", "evidence_lines")} == {
        "retry_policy": expected,
        "evidence_chars": expected,
        "evidence_lines": expected,
    }


def test_v1_default_run_show_review_preserve_legacy_database(tmp_path, monkeypatch):
    database = tmp_path / "legacy.sqlite3"
    monkeypatch.setenv("AGENT_DB_PATH", str(database))
    output = tmp_path / "run.json"
    result = runner.invoke(
        cli.app,
        [
            "agent-run",
            "examples/issues.json",
            "--repo",
            "examples/demo_repository",
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(output.read_text())
    assert "investigations" in payload
    assert "protocol" not in payload
    shown = runner.invoke(cli.app, ["agent-show", payload["run_id"]])
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output) == payload
    before = runner.invoke(cli.app, ["agent-db", "inspect", str(database)])
    review = runner.invoke(
        cli.app,
        [
            "agent-review",
            payload["run_id"],
            "--decision",
            "approved",
        ],
    )
    assert review.exit_code == 0, review.output
    after = runner.invoke(cli.app, ["agent-db", "inspect", str(database)])
    assert before.exit_code == after.exit_code == 0
    assert json.loads(after.output) == {"kind": "legacy0", "user_version": 0}
    assert after.output == before.output


def test_v2_fatal_provider_bug_is_sanitized_and_prior_run_remains_readable(tmp_path, monkeypatch):
    import re

    database = private_database(tmp_path)

    def broken_transport(payload):
        raise RuntimeError("PRIVATE_PROVIDER_CREDENTIAL_AND_EVIDENCE")

    requests = fake_api(monkeypatch, broken_transport)
    result = runner.invoke(
        cli.app,
        [
            "agent-run",
            "examples/issues.json",
            "--repo",
            "examples/demo_repository",
            "--protocol",
            "v2",
            "--database",
            str(database),
            "--llm",
            "--allow-external-llm",
            "--output",
            str(tmp_path / "run.json"),
        ],
    )
    assert result.exit_code == 2
    assert len(requests) == 1
    assert "PRIVATE_PROVIDER" not in result.output
    run_id = re.search(r"Run ([a-f0-9-]{36}) remains readable", result.output).group(1)
    shown = runner.invoke(
        cli.app,
        [
            "agent-show",
            run_id,
            "--protocol",
            "v2",
            "--database",
            str(database),
        ],
    )
    assert shown.exit_code == 0, shown.output
    payload = json.loads(shown.output)
    assert payload["status"] == "FAILED"
    assert payload["issues"][0]["deterministic_state"] == "succeeded"
    assert "PRIVATE_PROVIDER" not in shown.output


def test_v2_output_failure_reports_persisted_run_id(tmp_path):
    database = private_database(tmp_path)
    result = runner.invoke(
        cli.app,
        [
            "agent-run",
            "examples/issues.json",
            "--repo",
            "examples/demo_repository",
            "--protocol",
            "v2",
            "--database",
            str(database),
            "--output",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 2
    assert "remains readable with agent-show --protocol v2" in result.output
    assert "IsADirectoryError" not in result.output


@pytest.mark.parametrize("fatal", [False, True])
def test_v2_evaluate_preserves_complete_or_partial_artifact_separately_from_v1(
    tmp_path, monkeypatch, fatal
):
    workspace = tmp_path / "workspaces"
    repository = workspace / "example--demo"
    shutil.copytree(Path("examples/demo_repository"), repository)
    for command in (
        ["init", "-q"],
        ["add", "."],
        [
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "Demo fixture",
        ],
        ["remote", "add", "origin", "https://github.com/example/demo.git"],
    ):
        subprocess.run(["git", "-C", str(repository), *command], check=True, capture_output=True)
    revision = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    record = json.loads(Path("examples/issues.json").read_text())[0]
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "name": "local-cli-v2",
                "version": 1,
                "cases": [
                    {
                        "id": "expired-refresh",
                        "tier": "main",
                        "repository": "example/demo",
                        "issue_number": record["number"],
                        "issue_updated_at": record["updated_at"],
                        "issue_snapshot": record,
                        "fix_pr_number": 200,
                        "pre_fix_sha": revision,
                        "expected_files": ["auth_service.py"],
                    }
                ],
            }
        )
    )

    def broken_transport(payload):
        raise RuntimeError("PRIVATE_EVALUATION_FAILURE")

    requests = fake_api(monkeypatch, broken_transport if fatal else None)
    monkeypatch.chdir(tmp_path)
    v1_output = tmp_path / "benchmarks/results/agent-analysis-latest.json"
    v1_output.parent.mkdir(parents=True)
    v1_output.write_text("V1_ARTIFACT_DO_NOT_OVERWRITE")
    result = runner.invoke(
        cli.app,
        [
            "agent-evaluate",
            str(manifest),
            "--workspace",
            str(workspace),
            "--protocol",
            "v2",
            "--allow-external-llm",
            "--case-id",
            "expired-refresh",
            "--temperature",
            "0.2",
            "--seed",
            "7",
        ],
    )
    assert result.exit_code == (1 if fatal else 0), result.output
    assert "Protocol v2:" in result.output
    assert len(requests) == 1
    payload = json.loads(Path("benchmarks/results/agent-analysis-v2-latest.json").read_text())
    assert payload["protocol"] == "v2"
    assert payload["overall"]["provider_cases"] == 1
    assert payload["completed"] is not fatal
    assert payload["status"] == ("FAILED" if fatal else "COMPLETED")
    assert payload["overall"]["provider_successes"] == (0 if fatal else 1)
    assert payload["results"][0]["attempts"][0]["request"]["temperature"] == 0.2
    assert payload["results"][0]["attempts"][0]["request"]["seed"] == 7
    assert "PRIVATE_EVALUATION_FAILURE" not in json.dumps(payload)
    assert "PRIVATE_EVALUATION_FAILURE" not in result.output
    assert payload["overall"]["persistence_verified"] == 1
    assert v1_output.read_text() == "V1_ARTIFACT_DO_NOT_OVERWRITE"
