import json

import httpx
import pytest
from test_analysis_contract import evidence_lookup, response
from test_llm_client import issue, report

from repo_issue_intelligence.analysis_contract import ANALYSIS_V2_PROMPT_VERSION
from repo_issue_intelligence.llm_client import (
    SYSTEM_PROMPT,
    LLMProviderError,
    OpenCodeIssueAnalyzer,
)


def test_api_v2_uses_snapshot_primary_and_keeps_reported_values_separate():
    captured = []
    lookup = evidence_lookup()

    def handler(request):
        captured.append(json.loads(request.content))
        lookup["E7"].file = "later-change.py"
        return httpx.Response(
            200,
            headers={"x-request-id": "http-7"},
            json={
                "id": "completion-3",
                "model": "reported-B",
                "service_tier": "default",
                "usage": {"prompt_tokens": 0, "completion_tokens": 9},
                "choices": [{"message": {"content": response().model_dump_json()}}],
            },
        )

    with httpx.Client(
        base_url="https://example.test/v1", transport=httpx.MockTransport(handler)
    ) as client:
        analyzer = OpenCodeIssueAnalyzer("test-key", model="requested-A", seed=7, client=client)
        record = issue()
        result = analyzer.analyze_v2(record, report(record), ["E1", "E7"], lookup)
    assert len(captured) == 1
    assert result.analysis.primary_evidence_id == "E7"
    assert result.analysis.affected_component == "handler.py::handle"
    assert "reranked_evidence_ids" not in result.analysis.model_dump()
    assert result.prompt_version == ANALYSIS_V2_PROMPT_VERSION
    assert result.requested["model"] == "requested-A"
    assert result.reported["model"] == "reported-B"
    assert result.reported["seed"] is None
    assert result.reported["input_tokens"] == 0
    assert result.reported["response_id"] == "completion-3"
    assert result.reported["request_id"] == "http-7"
    assert result.diagnostics == ("reported_model_differs_from_requested",)
    assert result.local["elapsed_ms"] >= 0
    assert captured[0]["messages"][0]["content"].startswith(SYSTEM_PROMPT)
    assert "primary" in captured[0]["messages"][0]["content"]
    assert (
        json.loads(captured[0]["messages"][1]["content"])["repository_evidence"][-1]["file"]
        == "handler.py"
    )


@pytest.mark.parametrize("malformed", [False, True])
def test_api_v2_unknown_metadata_and_invalid_response_are_not_filled_from_request(malformed):
    def handler(request):
        reply = response()
        if malformed:
            reply.hypothesis.evidence_ids = ["E999", "E7"]
        return httpx.Response(
            200, json={"choices": [{"message": {"content": reply.model_dump_json()}}]}
        )

    with httpx.Client(
        base_url="https://example.test/v1", transport=httpx.MockTransport(handler)
    ) as client:
        analyzer = OpenCodeIssueAnalyzer("test-key", model="requested-A", seed=7, client=client)
        record = issue()
        if malformed:
            with pytest.raises(LLMProviderError) as error:
                analyzer.analyze_v2(record, report(record), ["E1", "E7"], evidence_lookup())
            assert error.value.category == "evidence_validation"
            assert all(value is None for value in error.value.observations["reported"].values())
        else:
            result = analyzer.analyze_v2(record, report(record), ["E1", "E7"], evidence_lookup())
            assert all(value is None for value in result.reported.values())
            assert result.diagnostics == ()


def test_api_v2_rejects_invalid_input_before_transport():
    calls = []
    with httpx.Client(
        base_url="https://example.test/v1", transport=httpx.MockTransport(calls.append)
    ) as client:
        analyzer = OpenCodeIssueAnalyzer("test-key", client=client)
        record = issue()
        with pytest.raises(ValueError):
            analyzer.analyze_v2(record, report(record), ["E1", "E1"], evidence_lookup())
    assert calls == []


@pytest.mark.parametrize("mode", ["http", "truncated", "json", "transport"])
def test_api_v2_errors_keep_safe_observations_without_raw_provider_details(mode):
    def handler(request):
        if mode == "transport":
            raise httpx.ReadTimeout("secret=private diagnostic", request=request)
        payload = {
            "model": "reported-B",
            "id": "response-7",
            "usage": {"prompt_tokens": 0},
            "choices": [{"message": {"content": response().model_dump_json()}}],
        }
        status = 200
        if mode == "http":
            payload["error"] = {"message": "secret=private diagnostic", "code": "server_error"}
            status = 503
        elif mode == "truncated":
            payload["choices"][0]["finish_reason"] = "length"
        else:
            payload["choices"][0]["message"]["content"] = "not json secret=private"
        return httpx.Response(status, json=payload, headers={"x-request-id": "http-9"})

    with httpx.Client(
        base_url="https://example.test/v1", transport=httpx.MockTransport(handler)
    ) as client:
        analyzer = OpenCodeIssueAnalyzer("test-key", client=client)
        record = issue()
        with pytest.raises(LLMProviderError) as error:
            analyzer.analyze_v2(record, report(record), ["E1", "E7"], evidence_lookup())
    assert "private" not in str(error.value)
    assert error.value.observations["local"]["elapsed_ms"] >= 0
    if mode == "transport":
        assert all(value is None for value in error.value.observations["reported"].values())
    else:
        assert error.value.observations["reported"]["model"] == "reported-B"
        assert error.value.observations["reported"]["request_id"] == "http-9"
        assert error.value.observations["reported"]["response_id"] == "response-7"
        assert error.value.observations["reported"]["input_tokens"] == 0
