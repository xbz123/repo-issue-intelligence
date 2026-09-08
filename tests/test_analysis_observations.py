from repo_issue_intelligence.analysis_observations import (
    extract_api_observations,
    metadata_diagnostics,
)


def test_observations_keep_reported_values_and_identifier_roles_separate():
    reported = extract_api_observations(
        {
            "model": "reported-B",
            "service_tier": "default",
            "id": "completion-7",
            "usage": {"prompt_tokens": 0, "completion_tokens": 12},
        },
        {"x-request-id": "http-request-3"},
    )
    assert reported["model"] == "reported-B"
    assert reported["request_id"] == "http-request-3"
    assert reported["response_id"] == "completion-7"
    assert reported["input_tokens"] == 0
    assert reported["output_tokens"] == 12
    assert reported["seed"] is None
    assert reported["temperature"] is None
    assert metadata_diagnostics({"model": "requested-A"}, reported) == (
        "reported_model_differs_from_requested",
    )


def test_missing_invalid_or_unsafe_metadata_stays_unknown():
    reported = extract_api_observations(
        {
            "usage": {"prompt_tokens": True, "completion_tokens": -1},
            "model": "https://host?token=private",
            "seed": "0",
            "temperature": float("nan"),
            "arbitrary_secret": "not retained",
        },
        {"x-request-id": "secret=private"},
    )
    assert all(value is None for value in reported.values())
    assert all(value is None for value in extract_api_observations({}, {}).values())
    assert metadata_diagnostics({"model": "requested-A"}, reported) == ()


def test_oversized_numeric_metadata_does_not_break_valid_analysis():
    assert extract_api_observations({"temperature": 10**1000}, {})["temperature"] is None
