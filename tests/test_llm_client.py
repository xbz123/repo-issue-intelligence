import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from repo_issue_intelligence.llm_client import (
    OPENCODE_API_BASE_URL,
    LLMProviderError,
    OpenCodeIssueAnalyzer,
    _nonnegative_int,
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


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, 0),
        (True, 0),
        ({}, 0),
        (float("inf"), 0),
        (-1, 0),
        ("12", 12),
    ],
)
def test_nonnegative_int_rejects_invalid_telemetry(
    value: object,
    expected: int,
) -> None:
    assert _nonnegative_int(value) == expected


@pytest.mark.parametrize(
    "options, message",
    [
        ({"max_output_tokens": 0}, "max_output_tokens must be positive"),
        ({"timeout_seconds": 0}, "timeout_seconds must be positive"),
    ],
)
def test_analyzer_rejects_non_positive_request_limits(
    options: dict,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        OpenCodeIssueAnalyzer("test-key", **options)


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
        "reproduction_completeness": "partial",
        "evidence_observations": [
            {
                "evidence_id": evidence_id,
                "alignment": "neutral",
                "observation": "The refresh path calls token validation.",
            }
        ],
        "hypothesis": {
            "description": "The refresh path may not translate validation errors.",
            "confidence": 0.72,
            "evidence_ids": [evidence_id],
            "missing_evidence": ["Runtime stack trace"],
        },
    }


def test_opencode_analyzer_requests_json_object_and_parses_usage() -> None:
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
        base_url="https://opencode.ai/zen/v1",
        transport=httpx.MockTransport(handler),
    )
    analyzer = OpenCodeIssueAnalyzer(
        "test-key",
        temperature=0.1,
        seed=1337,
        client=client,
    )
    record = issue().model_copy(update={"body": "x" * 7_000})

    result = analyzer.analyze(record, report(record), evidence())

    assert result.request_id == "request-1"
    assert result.input_tokens == 300
    assert result.output_tokens == 120
    assert result.system_fingerprint == "fingerprint-1"
    assert result.analysis.hypotheses[0].evidence_ids == ["E1"]
    assert result.analysis.hypotheses[0].validation_step == (
        "Inspect the cited behavior at auth_service.py::refresh_token, then run the "
        "smallest existing relevant test and compare the result with the Issue without "
        "modifying files."
    )
    assert result.analysis.affected_component == "auth_service.py::refresh_token"
    assert result.analysis.reranked_evidence_ids == ["E1"]
    assert result.analysis.needs_more_evidence is True
    assert result.provider == "opencode"
    assert result.model == "deepseek-v4-flash"
    assert captured_request["response_format"] == {"type": "json_object"}
    assert captured_request["max_tokens"] == 20_000
    assert "max_completion_tokens" not in captured_request
    assert captured_request["reasoning_effort"] == "none"
    assert captured_request["temperature"] == 0.1
    assert captured_request["seed"] == 1337
    system_prompt = captured_request["messages"][0]["content"]
    assert '"additionalProperties":false' in system_prompt
    assert '"hypothesis"' in system_prompt
    assert '"affected_component"' not in system_prompt
    assert '"reranked_evidence_ids"' not in system_prompt
    user_payload = json.loads(captured_request["messages"][1]["content"])
    assert "deterministic_candidates" not in user_payload
    assert len(user_payload["issue"]["body"]) == 7_000
    assert user_payload["repository_evidence"][0]["id"] == "E1"


def test_opencode_analyzer_can_omit_max_tokens() -> None:
    captured_request = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_request.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "request-server-default",
                "choices": [
                    {"message": {"content": json.dumps(structured_payload())}}
                ],
                "usage": {"prompt_tokens": 300, "completion_tokens": 120},
            },
        )

    client = httpx.Client(
        base_url="https://opencode.ai/zen/v1",
        transport=httpx.MockTransport(handler),
    )
    analyzer = OpenCodeIssueAnalyzer(
        "test-key",
        max_output_tokens=None,
        client=client,
    )

    result = analyzer.analyze(issue(), report(issue()), evidence())

    assert result.request_id == "request-server-default"
    assert "max_tokens" not in captured_request


def test_opencode_deepseek_rerank_uses_plain_rank_protocol() -> None:
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
                            "content": "Evidence considered.\nRANK: E1",
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

    result = analyzer.rerank(
        issue().model_copy(update={"body": "x" * 2_100}),
        evidence(),
    )

    assert "response_format" not in captured_request
    assert captured_request["max_tokens"] == 8_192
    assert "max_completion_tokens" not in captured_request
    assert captured_request["reasoning_effort"] == "none"
    assert captured_request["seed"] == 1337
    assert "Return exactly one line" in captured_request["messages"][0]["content"]
    assert "three\nstrongest evidence IDs" in captured_request["messages"][0]["content"]
    assert "RANK: E3,E1,E2" in captured_request["messages"][0]["content"]
    user_payload = json.loads(captured_request["messages"][1]["content"])
    assert len(user_payload["issue"]["body"]) == 2_100
    assert result.provider == "opencode"
    assert result.analysis.reranked_evidence_ids == ["E1"]
    assert result.input_tokens == 80
    assert result.output_tokens == 12
    assert result.attempts == 1


def test_provider_client_ignores_malformed_inherited_no_proxy(monkeypatch) -> None:
    monkeypatch.setenv("NO_PROXY", "::1")

    analyzer = OpenCodeIssueAnalyzer("test-key")

    analyzer.close()


def test_opencode_deepseek_rerank_deduplicates_ids() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "empty-rerank-request",
                "choices": [
                    {
                        "message": {
                            "content": "RANK: E1,E1",
                        }
                    }
                ],
            },
        )

    client = httpx.Client(
        base_url="https://opencode.ai/zen/v1",
        transport=httpx.MockTransport(handler),
    )
    analyzer = OpenCodeIssueAnalyzer("test-key", client=client)

    result = analyzer.rerank(issue(), evidence())

    assert result.analysis.reranked_evidence_ids == ["E1"]


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("No ranking available", "found 0"),
        ("RANK: E1\nRANK: E1", "found 2"),
        ('{"RANK":"E1"}', "found 0"),
    ],
)
def test_opencode_deepseek_rerank_rejects_invalid_rank_lines(
    content: str,
    message: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "invalid-rerank-request",
                "choices": [{"message": {"content": content}}],
            },
        )

    client = httpx.Client(
        base_url="https://opencode.ai/zen/v1",
        transport=httpx.MockTransport(handler),
    )
    analyzer = OpenCodeIssueAnalyzer("test-key", client=client)

    with pytest.raises(LLMProviderError, match=message) as error:
        analyzer.rerank(issue(), evidence())

    assert error.value.retryable is False


def test_opencode_deepseek_rerank_rejects_unknown_evidence_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "unknown-rerank-request",
                "choices": [{"message": {"content": "RANK: E999"}}],
            },
        )

    client = httpx.Client(
        base_url="https://opencode.ai/zen/v1",
        transport=httpx.MockTransport(handler),
    )
    analyzer = OpenCodeIssueAnalyzer("test-key", client=client)

    with pytest.raises(LLMProviderError, match="unknown evidence IDs: E999"):
        analyzer.rerank(issue(), evidence())


def test_opencode_deepseek_rerank_rejects_more_than_three_ids() -> None:
    snippets = [
        EvidenceSnippet(
            id=f"E{index}",
            file=f"module_{index}.py",
            symbol=f"function_{index}",
            lines="1-2",
            content="return None",
        )
        for index in range(1, 5)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "oversized-rerank-request",
                "choices": [{"message": {"content": "RANK: E1,E2,E3,E4"}}],
                "usage": {"prompt_tokens": 40, "completion_tokens": 12},
            },
        )

    client = httpx.Client(
        base_url="https://opencode.ai/zen/v1",
        transport=httpx.MockTransport(handler),
    )
    analyzer = OpenCodeIssueAnalyzer("test-key", client=client)

    with pytest.raises(LLMProviderError, match="more than three") as error:
        analyzer.rerank(issue(), snippets)

    assert error.value.category == "invalid_rank"
    assert error.value.input_tokens == 40
    assert error.value.output_tokens == 12


def test_opencode_deepseek_rerank_classifies_output_truncation() -> None:
    requested_budgets = []

    def handler(request: httpx.Request) -> httpx.Response:
        budget = json.loads(request.content)["max_tokens"]
        requested_budgets.append(budget)
        return httpx.Response(
            200,
            json={
                "id": "truncated-rerank-request",
                "choices": [{"finish_reason": "length", "message": {"content": ""}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": budget},
            },
        )

    client = httpx.Client(
        base_url="https://opencode.ai/zen/v1",
        transport=httpx.MockTransport(handler),
    )
    analyzer = OpenCodeIssueAnalyzer("test-key", client=client)

    with pytest.raises(LLMProviderError, match="exhausted both rank output budgets") as error:
        analyzer.rerank(issue(), evidence())

    assert error.value.retryable is False
    assert error.value.category == "output_truncated"
    assert error.value.attempts == 2
    assert error.value.input_tokens == 20
    assert error.value.output_tokens == 28_192
    assert error.value.elapsed_ms >= 0
    assert requested_budgets == [8_192, 20_000]


def test_opencode_deepseek_rerank_retries_truncation_with_larger_budget() -> None:
    requested_budgets = []

    def handler(request: httpx.Request) -> httpx.Response:
        budget = json.loads(request.content)["max_tokens"]
        requested_budgets.append(budget)
        if budget == 8_192:
            return httpx.Response(
                200,
                json={
                    "id": "truncated-request",
                    "choices": [{"finish_reason": "length", "message": {"content": ""}}],
                    "usage": {"prompt_tokens": 80, "completion_tokens": 8_192},
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "successful-request",
                "choices": [{"finish_reason": "stop", "message": {"content": "RANK: E1"}}],
                "usage": {"prompt_tokens": 80, "completion_tokens": 100},
            },
        )

    client = httpx.Client(
        base_url="https://opencode.ai/zen/v1",
        transport=httpx.MockTransport(handler),
    )
    analyzer = OpenCodeIssueAnalyzer("test-key", client=client)

    result = analyzer.rerank(issue(), evidence())

    assert requested_budgets == [8_192, 20_000]
    assert result.analysis.reranked_evidence_ids == ["E1"]
    assert result.attempts == 2
    assert result.input_tokens == 160
    assert result.output_tokens == 8_292


def test_opencode_deepseek_rerank_marks_grammar_400_non_retryable() -> None:
    captured_request = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_request.update(json.loads(request.content))
        return httpx.Response(
            400,
            json={
                "error": {
                    "code": "upstream_error",
                    "message": "DFLASH does not support grammar constrained decoding",
                }
            },
        )

    client = httpx.Client(
        base_url="https://opencode.ai/zen/v1",
        transport=httpx.MockTransport(handler),
    )
    analyzer = OpenCodeIssueAnalyzer("test-key", client=client)

    with pytest.raises(LLMProviderError, match="HTTP 400") as error:
        analyzer.rerank(issue(), evidence())

    assert "response_format" not in captured_request
    assert error.value.retryable is False
    assert error.value.category == "grammar_unsupported"


def test_opencode_deepseek_rerank_marks_429_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "3"})

    client = httpx.Client(
        base_url="https://opencode.ai/zen/v1",
        transport=httpx.MockTransport(handler),
    )
    analyzer = OpenCodeIssueAnalyzer("test-key", client=client)

    with pytest.raises(LLMProviderError, match="HTTP 429") as error:
        analyzer.rerank(issue(), evidence())

    assert error.value.retryable is True
    assert error.value.retry_after == 3
    assert error.value.category == "rate_limit"


def test_opencode_deepseek_rerank_marks_5xx_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = httpx.Client(
        base_url="https://opencode.ai/zen/v1",
        transport=httpx.MockTransport(handler),
    )
    analyzer = OpenCodeIssueAnalyzer("test-key", client=client)

    with pytest.raises(LLMProviderError, match="HTTP 503") as error:
        analyzer.rerank(issue(), evidence())

    assert error.value.retryable is True
    assert error.value.category == "server_error"


def test_persisted_analysis_accepts_historical_hypothesis_count() -> None:
    response = structured_payload()
    hypothesis = response["hypothesis"]
    hypothesis["validation_step"] = "Run the existing test and inspect the result."
    payload = {
        **response,
        "affected_component": "authentication",
        "contradictions": [],
        "reranked_evidence_ids": ["E1"],
        "hypotheses": [hypothesis] * 3,
        "needs_more_evidence": True,
    }
    payload.pop("hypothesis")

    historical = LLMAnalysis.model_validate(payload)

    assert len(historical.hypotheses) == 3
    with pytest.raises(ValidationError):
        LLMAnalysisResponse.model_validate(payload)


def test_opencode_analyzer_rejects_unknown_evidence_id() -> None:
    response_payload = structured_payload()
    response_payload["hypothesis"]["evidence_ids"] = ["E999"]

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
                "usage": {"prompt_tokens": 301, "completion_tokens": 121},
            },
        )

    client = httpx.Client(
        base_url="https://opencode.ai/zen/v1",
        transport=httpx.MockTransport(handler),
    )
    analyzer = OpenCodeIssueAnalyzer("test-key", client=client)
    record = issue()

    with pytest.raises(LLMProviderError, match="unknown evidence IDs: E999") as error:
        analyzer.analyze(record, report(record), evidence())

    assert error.value.category == "unknown_evidence_id"
    assert error.value.request_id == "request-2"
    assert error.value.input_tokens == 301
    assert error.value.output_tokens == 121
    assert error.value.elapsed_ms >= 0


def test_opencode_analyzer_requires_one_observation_per_evidence() -> None:
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
                "usage": {"prompt_tokens": 302, "completion_tokens": 122},
            },
        )

    client = httpx.Client(
        base_url="https://opencode.ai/zen/v1",
        transport=httpx.MockTransport(handler),
    )
    analyzer = OpenCodeIssueAnalyzer("test-key", client=client)
    record = issue()

    with pytest.raises(LLMProviderError, match="exactly one observation") as error:
        analyzer.analyze(record, report(record), evidence())

    assert error.value.category == "evidence_observation_coverage"
    assert error.value.request_id == "request-coverage"
    assert error.value.input_tokens == 302
    assert error.value.output_tokens == 122


def test_opencode_analyzer_derives_contradiction_from_evidence_alignment() -> None:
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
        base_url="https://opencode.ai/zen/v1",
        transport=httpx.MockTransport(handler),
    )
    analyzer = OpenCodeIssueAnalyzer("test-key", client=client)
    record = issue()

    result = analyzer.analyze(record, report(record), evidence())

    assert result.analysis.contradictions == ["E1: The refresh path calls token validation."]


def test_opencode_analyzer_derives_needs_more_evidence() -> None:
    response_payload = structured_payload()
    response_payload["hypothesis"]["missing_evidence"] = []

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
                "usage": {"prompt_tokens": 303, "completion_tokens": 123},
            },
        )

    client = httpx.Client(
        base_url="https://opencode.ai/zen/v1",
        transport=httpx.MockTransport(handler),
    )
    analyzer = OpenCodeIssueAnalyzer("test-key", client=client)
    record = issue()

    result = analyzer.analyze(record, report(record), evidence())

    assert result.analysis.needs_more_evidence is False
    assert result.analysis.hypotheses[0].missing_evidence == []


def test_opencode_analyzer_derives_validation_from_cited_evidence() -> None:
    snippets = [
        *evidence(),
        EvidenceSnippet(
            id="E2",
            file="validation.py",
            symbol="validate_token",
            lines="8-10",
            content="8: def validate_token():\n9:     raise InvalidToken()",
        ),
    ]
    response_payload = structured_payload("E2")
    response_payload["evidence_observations"].insert(
        0,
        {
            "evidence_id": "E1",
            "alignment": "neutral",
            "observation": "The refresh path calls token validation.",
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "request-cited-validation",
                "choices": [
                    {"message": {"content": json.dumps(response_payload)}}
                ],
            },
        )

    client = httpx.Client(
        base_url="https://opencode.ai/zen/v1",
        transport=httpx.MockTransport(handler),
    )
    analyzer = OpenCodeIssueAnalyzer("test-key", client=client)

    result = analyzer.analyze(issue(), report(issue()), snippets)

    assert result.analysis.hypotheses[0].validation_step.startswith(
        "Inspect the cited behavior at validation.py::validate_token"
    )


def test_opencode_analyzer_preserves_invalid_json_response_telemetry() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "request-invalid-json",
                "system_fingerprint": "fingerprint-invalid-json",
                "choices": [{"message": {"content": '{"summary":'}}],
                "usage": {"prompt_tokens": 304, "completion_tokens": 124},
            },
        )

    client = httpx.Client(
        base_url="https://opencode.ai/zen/v1",
        transport=httpx.MockTransport(handler),
    )
    analyzer = OpenCodeIssueAnalyzer("test-key", client=client)
    record = issue()

    with pytest.raises(LLMProviderError, match="invalid structured response") as error:
        analyzer.analyze(record, report(record), evidence())

    assert error.value.category == "invalid_json"
    assert "root=json_invalid" in str(error.value)
    assert error.value.retryable is True
    assert error.value.request_id == "request-invalid-json"
    assert error.value.system_fingerprint == "fingerprint-invalid-json"
    assert error.value.input_tokens == 304
    assert error.value.output_tokens == 124
    assert error.value.elapsed_ms >= 0


def test_opencode_analyzer_reports_analysis_output_truncation_without_retry() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "request-truncated-analysis",
                "system_fingerprint": "fingerprint-truncated-analysis",
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": '{"summary":'},
                    }
                ],
                "usage": {"prompt_tokens": 504, "completion_tokens": 20_000},
            },
        )

    client = httpx.Client(
        base_url="https://opencode.ai/zen/v1",
        transport=httpx.MockTransport(handler),
    )
    analyzer = OpenCodeIssueAnalyzer("test-key", client=client)

    with pytest.raises(LLMProviderError, match="exhausted") as error:
        analyzer.analyze(issue(), report(issue()), evidence())

    assert error.value.category == "output_truncated"
    assert error.value.retryable is False
    assert error.value.request_id == "request-truncated-analysis"
    assert error.value.system_fingerprint == "fingerprint-truncated-analysis"
    assert error.value.input_tokens == 504
    assert error.value.output_tokens == 20_000


def test_opencode_analyzer_reports_schema_validation_paths_without_content() -> None:
    response_payload = structured_payload()
    response_payload.pop("issue_type")
    response_payload["unexpected_source_content"] = "must not be persisted"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "request-invalid-schema",
                "choices": [
                    {"message": {"content": json.dumps(response_payload)}}
                ],
                "usage": {"prompt_tokens": 305, "completion_tokens": 125},
            },
        )

    client = httpx.Client(
        base_url="https://opencode.ai/zen/v1",
        transport=httpx.MockTransport(handler),
    )
    analyzer = OpenCodeIssueAnalyzer("test-key", client=client)

    with pytest.raises(LLMProviderError, match="invalid structured response") as error:
        analyzer.analyze(issue(), report(issue()), evidence())

    assert error.value.category == "schema_validation"
    assert "issue_type=missing" in str(error.value)
    assert "<unexpected-field>=extra_forbidden" in str(error.value)
    assert "unexpected_source_content" not in str(error.value)
    assert "must not be persisted" not in str(error.value)


def test_opencode_analyzer_exposes_retry_after_without_response_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"retry-after": "2"},
            json={"error": {"message": "request content must not be propagated"}},
        )

    client = httpx.Client(
        base_url="https://opencode.ai/zen/v1",
        transport=httpx.MockTransport(handler),
    )
    analyzer = OpenCodeIssueAnalyzer("test-key", client=client)
    record = issue()

    with pytest.raises(LLMProviderError, match="HTTP 429") as error:
        analyzer.analyze(record, report(record), evidence())

    assert error.value.retry_after == 2
    assert "request content" not in str(error.value)


def test_opencode_analyzer_reports_bounded_structured_error_detail() -> None:
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
        base_url="https://opencode.ai/zen/v1",
        transport=httpx.MockTransport(handler),
    )
    analyzer = OpenCodeIssueAnalyzer("test-key", client=client)
    record = issue()

    with pytest.raises(
        LLMProviderError,
        match="HTTP 400: json_validate_failed - Schema generation failed",
    ):
        analyzer.analyze(record, report(record), evidence())


def test_opencode_analyzer_redacts_provider_urls_from_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "error": {
                    "message": (
                        "Insufficient balance. Manage billing at "
                        "https://opencode.ai/workspace/private-id/billing"
                    )
                }
            },
        )

    client = httpx.Client(
        base_url="https://opencode.ai/zen/v1",
        transport=httpx.MockTransport(handler),
    )
    analyzer = OpenCodeIssueAnalyzer("test-key", client=client)

    with pytest.raises(LLMProviderError, match=r"\[URL redacted\]") as error:
        analyzer.analyze(issue(), report(issue()), evidence())

    assert "private-id" not in str(error.value)


def test_opencode_analyzer_wraps_transport_error_without_request_details() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret upstream detail", request=request)

    client = httpx.Client(
        base_url="https://opencode.ai/zen/v1",
        transport=httpx.MockTransport(handler),
    )
    analyzer = OpenCodeIssueAnalyzer("test-key", client=client)
    record = issue()

    with pytest.raises(LLMProviderError, match="OpenCode request failed: ConnectError") as error:
        analyzer.analyze(record, report(record), evidence())

    assert "secret upstream detail" not in str(error.value)


def test_opencode_analyzer_uses_default_deepseek_model() -> None:
    analyzer = OpenCodeIssueAnalyzer("test-key")

    assert OPENCODE_API_BASE_URL == "https://opencode.ai/zen/go/v1"
    assert analyzer.model == "deepseek-v4-flash"
    analyzer.close()


def test_opencode_analyzer_keeps_legacy_positional_constructor() -> None:
    client = httpx.Client(
        base_url="https://opencode.ai/zen/go/v1/",
        trust_env=False,
    )
    analyzer = OpenCodeIssueAnalyzer("test-key", 512, 12, 0.2, 7, client)

    assert analyzer.max_output_tokens == 512
    assert analyzer.timeout_seconds == 12
    assert analyzer.temperature == 0.2
    assert analyzer.seed == 7
    analyzer.close()
    client.close()


def test_openai_compatible_analyzer_uses_custom_base_url_model_and_provider() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "custom-request",
                "choices": [
                    {"message": {"content": json.dumps(structured_payload())}}
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8},
            },
        )

    analyzer = OpenCodeIssueAnalyzer(
        "test-key",
        base_url="https://gateway.example/v1/chat/completions",
        model="custom-model",
        provider="custom-gateway",
        reasoning_effort=None,
        response_format_json=False,
        client=httpx.Client(
            base_url="https://gateway.example/v1/",
            transport=httpx.MockTransport(handler),
        ),
    )

    result = analyzer.analyze(issue(), report(issue()), evidence())

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert captured["url"] == "https://gateway.example/v1/chat/completions"
    assert payload["model"] == "custom-model"
    assert "reasoning_effort" not in payload
    assert "response_format" not in payload
    assert result.provider == "custom-gateway"
    assert result.model == "custom-model"
    assert result.reasoning_effort is None
    assert result.service_tier is None
    analyzer.close()

    owned_analyzer = OpenCodeIssueAnalyzer(
        "test-key",
        base_url="https://gateway.example/v1/chat/completions",
    )
    assert str(owned_analyzer._client.base_url) == "https://gateway.example/v1/"
    owned_analyzer.close()


@pytest.mark.parametrize(
    "base_url",
    ("", "gateway.example/v1", "ftp://gateway.example/v1", "https://gateway/x?q=1"),
)
def test_openai_compatible_analyzer_rejects_invalid_base_url(base_url: str) -> None:
    with pytest.raises(ValueError, match="API base URL"):
        OpenCodeIssueAnalyzer("test-key", base_url=base_url)
