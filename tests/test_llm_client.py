import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from repo_issue_intelligence.llm_client import (
    GroqAPIError,
    GroqIssueAnalyzer,
    OpenCodeIssueAnalyzer,
)
from repo_issue_intelligence.models import (
    CandidateLocation,
    EvidenceSnippet,
    InvestigationReport,
    IssueRecord,
    LLMAnalysis,
    LLMAnalysisResponse,
    ReproductionPlan,
)


def issue() -> IssueRecord:
    timestamp = datetime(2026, 7, 29, tzinfo=UTC)
    return IssueRecord(
        number=184,
        title="Refresh token validation crashes",
        body="Steps to reproduce are available.",
        labels=["bug"],
        created_at=timestamp,
        updated_at=timestamp,
    )


def report(record: IssueRecord) -> InvestigationReport:
    return InvestigationReport(
        issue=record,
        confirmed_facts=["Issue is reproducible"],
        candidates=[
            CandidateLocation(
                file="auth_service.py",
                symbol="refresh_token",
                lines="1-3",
                confidence=0.8,
                evidence=["Symbol matches refresh token"],
            )
        ],
        hypotheses=[],
        reproduction_plan=ReproductionPlan(
            runtime="Python 3.11",
            setup_commands=[],
            reproduction_steps=[],
            safety_constraints=[],
            open_questions=[],
        ),
        repository_root=Path("/tmp/repository"),
    )


def evidence() -> list[EvidenceSnippet]:
    return [
        EvidenceSnippet(
            id="E1",
            file="auth_service.py",
            symbol="refresh_token",
            lines="1-3",
            content="1: def refresh_token():\n2:     return validate_token()",
        )
    ]


def structured_payload(evidence_id: str = "E1") -> dict:
    return {
        "summary": "Token validation may propagate an exception.",
        "issue_type": "bug",
        "affected_component": "authentication",
        "reproduction_completeness": "partial",
        "evidence_observations": [
            {
                "evidence_id": evidence_id,
                "alignment": "neutral",
                "observation": "The refresh path calls token validation.",
            }
        ],
        "contradictions": [],
        "reranked_evidence_ids": [evidence_id],
        "hypotheses": [
            {
                "description": "The refresh path may not translate validation errors.",
                "confidence": 0.72,
                "evidence_ids": [evidence_id],
                "missing_evidence": ["Runtime stack trace"],
                "validation_step": "Run the existing refresh-token test and inspect the error.",
            }
        ],
        "needs_more_evidence": True,
    }


def test_groq_analyzer_requests_strict_schema_and_parses_usage() -> None:
    captured_request = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_request.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "request-1",
                "system_fingerprint": "fingerprint-1",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(structured_payload()),
                        }
                    }
                ],
                "usage": {"prompt_tokens": 300, "completion_tokens": 120},
            },
        )

    client = httpx.Client(
        base_url="https://api.groq.com/openai/v1",
        transport=httpx.MockTransport(handler),
    )
    analyzer = GroqIssueAnalyzer(
        "test-key",
        temperature=0.1,
        seed=1337,
        client=client,
    )
    record = issue()

    result = analyzer.analyze(record, report(record), evidence())

    assert result.request_id == "request-1"
    assert result.input_tokens == 300
    assert result.output_tokens == 120
    assert result.system_fingerprint == "fingerprint-1"
    assert result.analysis.hypotheses[0].evidence_ids == ["E1"]
    assert captured_request["response_format"]["json_schema"]["strict"] is True
    assert captured_request["reasoning_effort"] == "low"
    assert captured_request["temperature"] == 0.1
    assert captured_request["seed"] == 1337
    schema = captured_request["response_format"]["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["hypotheses"]["maxItems"] == 2


def test_groq_analyzer_uses_minimal_schema_for_evidence_reranking() -> None:
    captured_request = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_request.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "rerank-request",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "Authentication evidence is most relevant.",
                                    "reranked_evidence_ids": ["E1"],
                                }
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )

    client = httpx.Client(
        base_url="https://api.groq.com/openai/v1",
        transport=httpx.MockTransport(handler),
    )
    analyzer = GroqIssueAnalyzer("test-key", client=client)

    result = analyzer.rerank(issue(), evidence())

    schema = captured_request["response_format"]["json_schema"]["schema"]
    assert set(schema["properties"]) == {"summary", "reranked_evidence_ids"}
    assert schema["properties"]["reranked_evidence_ids"]["minItems"] == 1
    assert result.analysis.reranked_evidence_ids == ["E1"]
    assert result.input_tokens == 100
    assert result.output_tokens == 20


def test_opencode_analyzer_uses_openai_compatible_json_object() -> None:
    captured_request = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_request.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "opencode-rerank-request",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "Authentication evidence is most relevant.",
                                    "reranked_evidence_ids": ["E1"],
                                }
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 80, "completion_tokens": 12},
            },
        )

    client = httpx.Client(
        base_url="https://opencode.ai/zen/v1",
        transport=httpx.MockTransport(handler),
    )
    analyzer = OpenCodeIssueAnalyzer(
        "test-key",
        temperature=0.1,
        seed=1337,
        client=client,
    )

    result = analyzer.rerank(issue(), evidence())

    assert captured_request["response_format"] == {"type": "json_object"}
    assert captured_request["max_tokens"] == 4_096
    assert "max_completion_tokens" not in captured_request
    assert "reasoning_effort" not in captured_request
    assert captured_request["seed"] == 1337
    assert "Return only one minified JSON object" in captured_request["messages"][0]["content"]
    assert "reranked_evidence_ids" in captured_request["messages"][0]["content"]
    assert '"reranked_evidence_ids":["E1","E2"]' in captured_request["messages"][0]["content"]
    assert result.provider == "opencode"
    assert result.analysis.reranked_evidence_ids == ["E1"]
    assert result.input_tokens == 80
    assert result.output_tokens == 12


def test_provider_client_ignores_malformed_inherited_no_proxy(monkeypatch) -> None:
    monkeypatch.setenv("NO_PROXY", "::1")

    analyzer = OpenCodeIssueAnalyzer("test-key")

    analyzer.close()


def test_groq_analyzer_rejects_empty_evidence_reranking() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "empty-rerank-request",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "No evidence was ranked.",
                                    "reranked_evidence_ids": [],
                                }
                            )
                        }
                    }
                ],
            },
        )

    client = httpx.Client(
        base_url="https://api.groq.com/openai/v1",
        transport=httpx.MockTransport(handler),
    )
    analyzer = GroqIssueAnalyzer("test-key", client=client)

    with pytest.raises(GroqAPIError, match="invalid rerank response"):
        analyzer.rerank(issue(), evidence())


def test_persisted_analysis_accepts_historical_hypothesis_count() -> None:
    payload = structured_payload()
    payload["hypotheses"] = payload["hypotheses"] * 3

    historical = LLMAnalysis.model_validate(payload)

    assert len(historical.hypotheses) == 3
    with pytest.raises(ValidationError):
        LLMAnalysisResponse.model_validate(payload)


def test_groq_analyzer_rejects_unknown_evidence_id() -> None:
    response_payload = structured_payload()
    response_payload["reranked_evidence_ids"] = ["E999"]
    response_payload["hypotheses"][0]["evidence_ids"] = ["E999"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "request-2",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(response_payload),
                        }
                    }
                ],
            },
        )

    client = httpx.Client(
        base_url="https://api.groq.com/openai/v1",
        transport=httpx.MockTransport(handler),
    )
    analyzer = GroqIssueAnalyzer("test-key", client=client)
    record = issue()

    with pytest.raises(GroqAPIError, match="unknown evidence IDs: E999"):
        analyzer.analyze(record, report(record), evidence())


def test_groq_analyzer_requires_one_observation_per_evidence() -> None:
    response_payload = structured_payload()
    response_payload["evidence_observations"] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "request-coverage",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(response_payload),
                        }
                    }
                ],
            },
        )

    client = httpx.Client(
        base_url="https://api.groq.com/openai/v1",
        transport=httpx.MockTransport(handler),
    )
    analyzer = GroqIssueAnalyzer("test-key", client=client)
    record = issue()

    with pytest.raises(GroqAPIError, match="exactly one observation"):
        analyzer.analyze(record, report(record), evidence())


def test_groq_analyzer_derives_contradiction_from_evidence_alignment() -> None:
    response_payload = structured_payload()
    response_payload["evidence_observations"][0]["alignment"] = "contradicts_issue"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "request-contradiction",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(response_payload),
                        }
                    }
                ],
            },
        )

    client = httpx.Client(
        base_url="https://api.groq.com/openai/v1",
        transport=httpx.MockTransport(handler),
    )
    analyzer = GroqIssueAnalyzer("test-key", client=client)
    record = issue()

    result = analyzer.analyze(record, report(record), evidence())

    assert result.analysis.contradictions == [
        "E1: The refresh path calls token validation."
    ]


def test_groq_analyzer_requires_named_missing_evidence() -> None:
    response_payload = structured_payload()
    response_payload["hypotheses"][0]["missing_evidence"] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "request-missing-evidence",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(response_payload),
                        }
                    }
                ],
            },
        )

    client = httpx.Client(
        base_url="https://api.groq.com/openai/v1",
        transport=httpx.MockTransport(handler),
    )
    analyzer = GroqIssueAnalyzer("test-key", client=client)
    record = issue()

    with pytest.raises(GroqAPIError, match="without naming a missing artifact"):
        analyzer.analyze(record, report(record), evidence())


def test_groq_analyzer_exposes_retry_after_without_response_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"retry-after": "2"},
            json={"error": {"message": "request content must not be propagated"}},
        )

    client = httpx.Client(
        base_url="https://api.groq.com/openai/v1",
        transport=httpx.MockTransport(handler),
    )
    analyzer = GroqIssueAnalyzer("test-key", client=client)
    record = issue()

    with pytest.raises(GroqAPIError, match="HTTP 429") as error:
        analyzer.analyze(record, report(record), evidence())

    assert error.value.retry_after == 2
    assert "request content" not in str(error.value)


def test_groq_analyzer_reports_bounded_structured_error_detail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "code": "json_validate_failed",
                    "message": "Schema generation failed",
                }
            },
        )

    client = httpx.Client(
        base_url="https://api.groq.com/openai/v1",
        transport=httpx.MockTransport(handler),
    )
    analyzer = GroqIssueAnalyzer("test-key", client=client)
    record = issue()

    with pytest.raises(
        GroqAPIError,
        match="HTTP 400: json_validate_failed - Schema generation failed",
    ):
        analyzer.analyze(record, report(record), evidence())


def test_groq_analyzer_wraps_transport_error_without_request_details() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret upstream detail", request=request)

    client = httpx.Client(
        base_url="https://api.groq.com/openai/v1",
        transport=httpx.MockTransport(handler),
    )
    analyzer = GroqIssueAnalyzer("test-key", client=client)
    record = issue()

    with pytest.raises(GroqAPIError, match="Groq request failed: ConnectError") as error:
        analyzer.analyze(record, report(record), evidence())

    assert "secret upstream detail" not in str(error.value)


def test_groq_analyzer_rejects_invalid_reasoning_effort() -> None:
    with pytest.raises(ValueError, match="reasoning_effort must be"):
        GroqIssueAnalyzer("test-key", reasoning_effort="none")
