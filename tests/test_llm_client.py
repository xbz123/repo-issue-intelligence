import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from repo_issue_intelligence.llm_client import GroqAPIError, GroqIssueAnalyzer
from repo_issue_intelligence.models import (
    CandidateLocation,
    EvidenceSnippet,
    InvestigationReport,
    IssueRecord,
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
                "validation_step": "Add a failing refresh-token test.",
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
    analyzer = GroqIssueAnalyzer("test-key", client=client)
    record = issue()

    result = analyzer.analyze(record, report(record), evidence())

    assert result.request_id == "request-1"
    assert result.input_tokens == 300
    assert result.output_tokens == 120
    assert result.analysis.hypotheses[0].evidence_ids == ["E1"]
    assert captured_request["response_format"]["json_schema"]["strict"] is True
    assert captured_request["reasoning_effort"] == "low"
    schema = captured_request["response_format"]["json_schema"]["schema"]
    assert schema["additionalProperties"] is False


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
