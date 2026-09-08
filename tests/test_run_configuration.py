from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from repo_issue_intelligence.models import IssueRecord
from repo_issue_intelligence.protocol_v2_models import BudgetConfiguration, freeze_run_inputs
from repo_issue_intelligence.run_configuration import (
    RunConfigurationError,
    capture_engine_runtime,
    capture_requested_run_configuration,
    normalize_endpoint,
)
from repo_issue_intelligence.scoring import score_issue

FIXED_TIME = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def _issue(number: int, *, updated_at: datetime = FIXED_TIME) -> IssueRecord:
    return IssueRecord(
        number=number,
        title=f"Issue {number}",
        body="A sufficiently detailed issue body with reproducible steps.",
        labels=["bug"],
        created_at=FIXED_TIME,
        updated_at=updated_at,
    )


def test_requested_configuration_separates_defaults_null_and_omitted() -> None:
    configuration = capture_requested_run_configuration(
        client={
            "backend": "api",
            "provider": "openai-compatible",
            "base_url": "HTTPS://Example.COM:443/v1/",
            "api_key": "must-not-be-read",
        },
        client_defaults={"model": "default-model", "temperature": 0.2},
        requested_model=None,
        request_parameters={"temperature": None, "seed": 7},
        parameter_origins={"requested_model": "user_config"},
        budgets={"max_output_tokens": 128, "evidence_chars": 2048},
        safe_cli_flags=("--json", "--timeout=30"),
        captured_at=FIXED_TIME,
    )

    assert configuration.requested_model is None
    assert configuration.request_parameters["temperature"] is None
    assert configuration.request_parameters["seed"] == 7
    assert configuration.parameter_origins["requested_model"] == "user_config"
    assert configuration.parameter_origins["temperature"] == "user_config"
    assert configuration.parameter_origins["reasoning_effort"] == "omitted"
    assert configuration.parameter_origins["output_tokens"] == "user_config"
    assert configuration.parameter_origins["max_output_tokens"] == "user_config"
    assert configuration.budgets.output_tokens == 128
    assert configuration.budgets.evidence_chars == 2048
    assert configuration.client.endpoint == "https://example.com/v1"
    assert configuration.safe_cli_flags == ("--json", "--timeout=30")
    assert "effective" not in configuration.model_dump(mode="json")
    assert "must-not-be-read" not in configuration.model_dump_json()

    omitted = capture_requested_run_configuration(captured_at=FIXED_TIME)
    explicit_null = capture_requested_run_configuration(
        budgets={"max_output_tokens": None},
        captured_at=FIXED_TIME,
    )
    assert omitted.parameter_origins["output_tokens"] == "omitted"
    assert explicit_null.parameter_origins["output_tokens"] == "user_config"
    assert explicit_null.parameter_origins["max_output_tokens"] == "user_config"


def test_configuration_is_deeply_immutable() -> None:
    configuration = capture_requested_run_configuration(
        request_parameters={"retry_policy": {"backoff": [1, 2]}},
        budgets={"retry_policy": {"categories": ["transport"]}},
        captured_at=FIXED_TIME,
    )

    with pytest.raises(TypeError):
        configuration.request_parameters["new"] = True
    with pytest.raises(TypeError):
        configuration.request_parameters["retry_policy"]["backoff"] = (3,)
    with pytest.raises(TypeError):
        configuration.budgets.retry_policy["new"] = True


@pytest.mark.parametrize(
    "default_name,request_name",
    [("output_tokens", "max_output_tokens"), ("max_output_tokens", "output_tokens")],
)
def test_budget_aliases_follow_source_precedence_before_canonical_selection(
    default_name, request_name
) -> None:
    configuration = capture_requested_run_configuration(
        client_defaults={default_name: 64},
        request_parameters={request_name: 128},
        captured_at=FIXED_TIME,
    )

    assert configuration.budgets.output_tokens == 128
    assert configuration.parameter_origins["output_tokens"] == "user_config"
    assert configuration.parameter_origins[request_name] == "user_config"
    assert configuration.request_parameters == {request_name: 128}

    with pytest.raises(RunConfigurationError, match="Conflicting aliases"):
        capture_requested_run_configuration(
            client_defaults={"output_tokens": 64, "max_output_tokens": 128},
            captured_at=FIXED_TIME,
        )


def test_budget_model_preserves_explicit_null_vs_omitted_origin() -> None:
    omitted = capture_requested_run_configuration(
        budgets=BudgetConfiguration(),
        captured_at=FIXED_TIME,
    )
    explicit_null = capture_requested_run_configuration(
        budgets=BudgetConfiguration(output_tokens=None),
        captured_at=FIXED_TIME,
    )

    assert "output_tokens" not in BudgetConfiguration().model_fields_set
    assert "output_tokens" in BudgetConfiguration(output_tokens=None).model_fields_set
    assert omitted.parameter_origins["output_tokens"] == "omitted"
    assert explicit_null.parameter_origins["output_tokens"] == "user_config"


@pytest.mark.parametrize(
    "parameter,budget",
    [
        ("max_output_tokens", "output_tokens"),
        ("timeout_seconds", "timeout_seconds"),
    ],
)
@pytest.mark.parametrize("requested,limit", [(100, 200), (None, 200), (100, None)])
def test_conflicting_request_and_budget_are_rejected(parameter, budget, requested, limit):
    with pytest.raises(ValueError, match="[Cc]onflicting"):
        capture_requested_run_configuration(
            request_parameters={parameter: requested},
            budgets={budget: limit},
            captured_at=FIXED_TIME,
        )


@pytest.mark.parametrize(
    "parameters,budgets",
    [
        ({"output_tokens": 100}, {"output_tokens": 200}),
        ({"output_tokens": None}, {"output_tokens": 200}),
        ({"output_tokens": 100, "max_output_tokens": 200}, {}),
    ],
)
def test_output_tokens_request_alias_conflicts_with_budget(parameters, budgets):
    with pytest.raises(ValueError, match="[Cc]onflicting"):
        capture_requested_run_configuration(
            request_parameters=parameters,
            budgets=budgets,
            captured_at=FIXED_TIME,
        )


def test_configuration_rejects_credentials_and_unsafe_endpoint() -> None:
    with pytest.raises(RunConfigurationError) as error:
        capture_requested_run_configuration(
            request_parameters={"token": "secret"},
            captured_at=FIXED_TIME,
        )
    assert error.value.code in {"secret_field", "unsupported_parameter"}

    with pytest.raises(RunConfigurationError) as error:
        capture_requested_run_configuration(
            request_parameters={"temperature": SecretStr("not-a-secret-field")},
            captured_at=FIXED_TIME,
        )
    assert error.value.code == "secret_value"

    with pytest.raises(RunConfigurationError) as error:
        normalize_endpoint("https://user:password@example.com/v1")
    assert error.value.code == "endpoint_userinfo"

    with pytest.raises(RunConfigurationError) as error:
        normalize_endpoint("https://example.com/v1?api_key=secret")
    assert error.value.code == "endpoint_query"

    with pytest.raises(RunConfigurationError) as error:
        capture_requested_run_configuration(
            safe_cli_flags=(
                "--base-url=https://u:syntheticSecret@example.com/v1",
            ),
            captured_at=FIXED_TIME,
        )
    assert error.value.code == "secret_cli_flag"
    assert "syntheticSecret" not in str(error.value)


@pytest.mark.parametrize(
    "flags",
    [
        ("--endpoint=https://user:syntheticSecret@example.com/v1",),
        ("--endpoint=ssh://user:syntheticSecret@example.com/repo",),
        ("--endpoint=ftp://user:syntheticSecret@example.com/repo",),
        ("--endpoint=ftp://user%3AsyntheticSecret%40example.com/repo",),
        ("--endpoint", "ssh://user:syntheticSecret@example.com/repo"),
        ("ssh://user:syntheticSecret@example.com/repo",),
        ("//user:syntheticSecret@example.com/repo",),
        ("user:syntheticSecret@example.com/repo",),
    ],
)
def test_cli_flags_reject_userinfo_for_every_url_scheme(flags: tuple[str, ...]) -> None:
    with pytest.raises(RunConfigurationError) as error:
        capture_requested_run_configuration(
            safe_cli_flags=flags,
            captured_at=FIXED_TIME,
        )

    assert error.value.code == "secret_cli_flag"
    assert "syntheticSecret" not in str(error.value)


def test_cli_flags_keep_non_url_label_values() -> None:
    configuration = capture_requested_run_configuration(
        safe_cli_flags=("--label=owner:team@example.com",),
        captured_at=FIXED_TIME,
    )

    assert configuration.safe_cli_flags == ("--label=owner:team@example.com",)


@pytest.mark.parametrize(
    ("source", "policy"),
    [
        ("request_parameters", {"url": "https://user:syntheticSecret@example.com/v1"}),
        ("request_parameters", {"credential": "syntheticSecret"}),
        ("client_defaults", {"url": "https://user:syntheticSecret@example.com/v1"}),
        ("client_defaults", {"credential": "syntheticSecret"}),
        ("client", {"url": "https://user:syntheticSecret@example.com/v1"}),
        ("client", {"credential": "syntheticSecret"}),
        ("budgets", {"url": "https://user:syntheticSecret@example.com/v1"}),
        ("budgets", {"credential": "syntheticSecret"}),
        ("budget_model", {"url": "https://user:syntheticSecret@example.com/v1"}),
        ("budget_model", {"credential": "syntheticSecret"}),
    ],
)
def test_retry_policy_rejects_unapproved_metadata(source: str, policy: dict[str, str]) -> None:
    kwargs: dict[str, object]
    if source == "request_parameters":
        kwargs = {"request_parameters": {"retry_policy": policy}}
    elif source == "client_defaults":
        kwargs = {"client_defaults": {"retry_policy": policy}}
    elif source == "client":
        kwargs = {"client": {"retry_policy": policy}}
    elif source == "budgets":
        kwargs = {"budgets": {"retry_policy": policy}}
    else:
        kwargs = {
            "budgets": BudgetConfiguration.model_construct(retry_policy=policy),
        }

    with pytest.raises(RunConfigurationError) as error:
        capture_requested_run_configuration(captured_at=FIXED_TIME, **kwargs)

    assert error.value.code == "invalid_retry_policy"
    assert "syntheticSecret" not in str(error.value)


def test_retry_policy_keeps_supported_behavior_fields_for_direct_model() -> None:
    configuration = capture_requested_run_configuration(
        budgets=BudgetConfiguration.model_construct(
            retry_policy={"backoff": [1, 2], "categories": ["transport"], "max_attempts": 3}
        ),
        captured_at=FIXED_TIME,
    )

    assert configuration.budgets.retry_policy["backoff"] == (1, 2)
    assert configuration.budgets.retry_policy["categories"] == ("transport",)
    assert configuration.budgets.retry_policy["max_attempts"] == 3


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://example.com/v1%3Fapi_key%3DsyntheticSecret",
        "https://example.com/v1%253Fapi_key%253DsyntheticSecret",
        "https://example.com/v1%23api_key%3DsyntheticSecret",
    ],
)
def test_endpoint_rejects_encoded_query_or_fragment_delimiters(endpoint: str) -> None:
    with pytest.raises(RunConfigurationError) as error:
        normalize_endpoint(endpoint)

    assert error.value.code == "endpoint_query"
    assert "syntheticSecret" not in str(error.value)


def test_endpoint_preserves_safe_encoded_path_spelling() -> None:
    endpoint = "https://example.com/v1%2Fchat"

    assert normalize_endpoint(endpoint) == endpoint
    configuration = capture_requested_run_configuration(
        client={"endpoint": endpoint},
        captured_at=FIXED_TIME,
    )
    assert configuration.client.endpoint == endpoint


def test_cli_flags_preserve_non_url_encoded_values() -> None:
    configuration = capture_requested_run_configuration(
        safe_cli_flags=("--label=abc%3Fdef",),
        captured_at=FIXED_TIME,
    )

    assert configuration.safe_cli_flags == ("--label=abc%3Fdef",)


def test_engine_runtime_uses_imported_source_revision_and_package_dirty_state(
    tmp_path: Path, monkeypatch
) -> None:
    engine_repository = tmp_path / "engine-repository"
    package = engine_repository / "fixture_engine"
    package.mkdir(parents=True)
    init_file = package / "__init__.py"
    sibling_file = package / "sibling.py"
    init_file.write_text("from .sibling import VALUE\n", encoding="utf-8")
    sibling_file.write_text("VALUE = 1\n", encoding="utf-8")

    unrelated_repository = tmp_path / "unrelated-repository"
    unrelated_repository.mkdir()
    (unrelated_repository / "README.md").write_text("unrelated\n", encoding="utf-8")

    def run_git(repository: Path, *args: str) -> None:
        subprocess.run(
            ["git", "-C", str(repository), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    for repository in (engine_repository, unrelated_repository):
        run_git(repository, "init", "-q")
        run_git(repository, "config", "user.name", "Protocol v2 test")
        run_git(repository, "config", "user.email", "protocol-v2-test@example.invalid")

    run_git(engine_repository, "add", "fixture_engine")
    run_git(engine_repository, "commit", "-qm", "initial fixture")
    run_git(unrelated_repository, "add", "README.md")
    run_git(unrelated_repository, "commit", "-qm", "initial unrelated fixture")
    (unrelated_repository / "untracked.py").write_text("unrelated change\n", encoding="utf-8")

    module_prefix = "fixture_engine"
    previous_modules = {
        name: module
        for name, module in sys.modules.items()
        if name == module_prefix or name.startswith(f"{module_prefix}.")
    }
    try:
        monkeypatch.syspath_prepend(str(engine_repository))
        monkeypatch.chdir(unrelated_repository)
        monkeypatch.setattr(sys, "dont_write_bytecode", True)

        clean_runtime = capture_engine_runtime(module_prefix)

        assert clean_runtime.imported_module == module_prefix
        assert clean_runtime.imported_source == init_file.resolve()
        assert clean_runtime.source_root == engine_repository.resolve()
        assert len(clean_runtime.source_revision or "") >= 7
        assert clean_runtime.source_provenance == "git"
        assert clean_runtime.source_dirty is False

        sibling_file.write_text("VALUE = 2\n", encoding="utf-8")
        dirty_runtime = capture_engine_runtime(module_prefix)

        assert dirty_runtime.source_root == engine_repository.resolve()
        assert dirty_runtime.source_revision == clean_runtime.source_revision
        assert dirty_runtime.source_dirty is True
        assert dirty_runtime.python_implementation
        assert dirty_runtime.runtime
    finally:
        for name in list(sys.modules):
            if name == module_prefix or name.startswith(f"{module_prefix}."):
                sys.modules.pop(name)
        sys.modules.update(previous_modules)


def test_freeze_run_inputs_preserves_full_ranking_and_rejects_mismatched_ordinal() -> None:
    issues = [_issue(1), _issue(2, updated_at=datetime(2026, 9, 4, tzinfo=UTC))]
    ranked = [score_issue(issue, as_of=FIXED_TIME) for issue in issues]
    inputs = freeze_run_inputs(
        issues,
        as_of=FIXED_TIME,
        ranked_results=ranked,
        selected_issue_numbers=[ranked[0].issue_number],
        selected_ordinals=[1],
        top_k=1,
    )

    assert inputs.as_of == FIXED_TIME
    assert tuple(item.issue_number for item in inputs.ranked_results) == (1, 2)
    assert inputs.selected_issue_numbers == (ranked[0].issue_number,)
    assert inputs.selected_ordinals == (1,)
    with pytest.raises((TypeError, ValidationError)):
        inputs.issues[0].labels += ("new",)

    with pytest.raises(ValueError, match="selection does not match frozen ranking"):
        freeze_run_inputs(
            issues,
            as_of=FIXED_TIME,
            ranked_results=ranked,
            selected_issue_numbers=[ranked[0].issue_number],
            selected_ordinals=[2],
            top_k=1,
        )


def test_freeze_run_inputs_requires_aware_as_of() -> None:
    issue = _issue(1)
    ranked = [score_issue(issue, as_of=FIXED_TIME)]
    with pytest.raises(ValueError, match="timezone-aware"):
        freeze_run_inputs(
            [issue],
            as_of=datetime(2026, 9, 5, 12, 0),
            ranked_results=ranked,
        )
