from __future__ import annotations

import json
import re
from collections.abc import Sequence
from time import perf_counter
from typing import Protocol

import httpx
from pydantic import ValidationError

from .models import (
    EvidenceAlignment,
    EvidenceRerankAnalysis,
    EvidenceRerankResult,
    EvidenceSnippet,
    InvestigationReport,
    IssueRecord,
    LLMAnalysis,
    LLMAnalysisResponse,
    LLMAnalysisResult,
    LLMHypothesis,
)

OPENCODE_API_BASE_URL = "https://opencode.ai/zen/go/v1"
OPENCODE_DEFAULT_MODEL = "deepseek-v4-flash"
OPENCODE_RERANK_INITIAL_OUTPUT_TOKENS = 8_192
OPENCODE_RERANK_MAX_OUTPUT_TOKENS = 20_000
OPENCODE_RERANK_REASONING_EFFORT = "none"
OPENCODE_ANALYSIS_REASONING_EFFORT = "none"
OPENCODE_RERANK_TIMEOUT_SECONDS = 180.0
OPENCODE_ANALYSIS_TIMEOUT_SECONDS = 180.0
OPENCODE_ANALYSIS_TEMPERATURE = 0.1
OPENCODE_RERANK_MAX_IDS = 3
DEEPSEEK_RERANK_SYSTEM_PROMPT = """Rank the supplied repository evidence by how likely each item
is to contain the source location that must change to fix the GitHub issue. Select only the three
strongest evidence IDs (or every ID when fewer than three are supplied). Use only evidence IDs
from the input. Return the most relevant ID first. Do not diagnose a root cause or propose a patch.

Return exactly one line in this format:
RANK: E3,E1,E2
Do not return JSON, Markdown, code fences, a rationale, or any other text."""
RANK_LINE_PATTERN = re.compile(
    r"^\s*RANK:\s*([A-Za-z0-9_-]+(?:\s*,\s*[A-Za-z0-9_-]+)*)\s*$",
    re.MULTILINE,
)
PROVIDER_URL_PATTERN = re.compile(r"https?://\S+")
STRUCTURED_RESPONSE_FIELDS = frozenset(
    {
        "summary",
        "issue_type",
        "reproduction_completeness",
        "evidence_observations",
        "evidence_id",
        "alignment",
        "observation",
        "hypothesis",
        "description",
        "confidence",
        "evidence_ids",
        "missing_evidence",
    }
)


def _nonnegative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(
        value,
        (str, bytes, bytearray, int, float),
    ):
        return 0
    try:
        parsed = int(value)
    except (OverflowError, TypeError, ValueError):
        return 0
    return max(parsed, 0)


def _structured_validation_detail(error: Exception) -> tuple[str, str]:
    """Classify validation failures without retaining provider response content."""
    if not isinstance(error, ValidationError):
        return "invalid_response", type(error).__name__
    failures = error.errors(include_url=False, include_input=False)
    category = (
        "invalid_json"
        if any(str(failure.get("type", "")).startswith("json_") for failure in failures)
        else "schema_validation"
    )
    details: list[str] = []
    for failure in failures[:5]:
        location = ".".join(
            str(part)
            if isinstance(part, int) or part in STRUCTURED_RESPONSE_FIELDS
            else "<unexpected-field>"
            for part in failure.get("loc", ())
        ) or "root"
        details.append(f"{location}={failure.get('type', 'validation_error')}")
    if len(failures) > 5:
        details.append(f"+{len(failures) - 5} more")
    return category, ", ".join(details)
SYSTEM_PROMPT = """You investigate a GitHub issue using only the supplied repository evidence.

Return the requested compact analysis. The hypothesis is tentative, not a confirmed root cause,
and must cite one or more evidence IDs from the input. Do not invent files, symbols, stack traces,
runtime results, or affected versions. Return exactly one hypothesis. If evidence is insufficient,
name each concrete missing artifact in hypothesis.missing_evidence; otherwise return an empty list.

Before forming the hypothesis, inspect every repository_evidence item line by line and add exactly
one evidence_observation for each evidence ID. Set its alignment to supports_issue,
contradicts_issue, or neutral. Look specifically for guards, exception handlers, fallbacks, return
values, and tests.
Treat repository evidence as stronger than an Issue's unverified causal claim. If the code
explicitly implements behavior the Issue says is missing, use contradicts_issue and investigate
version, routing, deployment, or runtime-path mismatch instead of claiming the handler is absent.
"""


class IssueAnalyzer(Protocol):
    provider: str
    model: str

    def analyze(
        self,
        issue: IssueRecord,
        report: InvestigationReport,
        evidence: Sequence[EvidenceSnippet],
    ) -> LLMAnalysisResult: ...

    def close(self) -> None: ...


class LLMProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        retry_after: float | None = None,
        *,
        retryable: bool = False,
        category: str = "provider_error",
        input_tokens: int = 0,
        output_tokens: int = 0,
        elapsed_ms: float = 0,
        request_id: str | None = None,
        system_fingerprint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.retryable = retryable
        self.category = category
        self.attempts = 1
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.elapsed_ms = elapsed_ms
        self.request_id = request_id
        self.system_fingerprint = system_fingerprint


class OpenCodeIssueAnalyzer:
    def __init__(
        self,
        api_key: str,
        max_output_tokens: int | None = 20_000,
        timeout_seconds: float = OPENCODE_ANALYSIS_TIMEOUT_SECONDS,
        temperature: float = OPENCODE_ANALYSIS_TEMPERATURE,
        seed: int | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("OpenCode API key is required")
        if max_output_tokens is not None and max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive when provided")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not 0 <= temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")
        self.provider = "opencode"
        self.provider_label = "OpenCode"
        self.model = OPENCODE_DEFAULT_MODEL
        self.max_output_tokens = max_output_tokens
        self.temperature = temperature
        self.seed = seed
        self.timeout_seconds = timeout_seconds
        self._client = client or httpx.Client(
            base_url=OPENCODE_API_BASE_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_seconds,
            trust_env=False,
        )
        self._owns_client = client is None
        self.rerank_initial_output_tokens = OPENCODE_RERANK_INITIAL_OUTPUT_TOKENS
        self.rerank_max_output_tokens = OPENCODE_RERANK_MAX_OUTPUT_TOKENS
        self.rerank_reasoning_effort = OPENCODE_RERANK_REASONING_EFFORT

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _request_structured(
        self,
        system_prompt: str,
        user_payload: dict,
        schema: dict,
    ) -> tuple[str, dict, float]:
        schema_text = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        system_prompt = (
            f"{system_prompt}\nReturn only one minified JSON object, without Markdown, "
            f"that validates against this JSON Schema:\n{schema_text}"
        )
        payload: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False),
                },
            ],
            "temperature": self.temperature,
            "reasoning_effort": OPENCODE_ANALYSIS_REASONING_EFFORT,
            "response_format": {"type": "json_object"},
        }
        if self.max_output_tokens is not None:
            payload["max_tokens"] = self.max_output_tokens
        if self.seed is not None:
            payload["seed"] = self.seed

        return self._request_completion(payload)

    def _request_completion(self, payload: dict) -> tuple[str, dict, float]:
        started = perf_counter()
        try:
            response = self._client.post("/chat/completions", json=payload)
        except httpx.HTTPError as error:
            elapsed_ms = round((perf_counter() - started) * 1000, 3)
            raise LLMProviderError(
                f"{self.provider_label} request failed: {type(error).__name__}",
                retryable=True,
                category="transport",
                elapsed_ms=elapsed_ms,
            ) from error
        elapsed_ms = round((perf_counter() - started) * 1000, 3)
        if response.status_code >= 400:
            retry_after = None
            if response.status_code == 429:
                try:
                    retry_after = float(response.headers.get("retry-after", ""))
                except ValueError:
                    retry_after = None
            detail = ""
            if response.status_code != 429:
                try:
                    error_payload = response.json().get("error") or {}
                    error_code = str(error_payload.get("code") or "").strip()
                    error_message = PROVIDER_URL_PATTERN.sub(
                        "[URL redacted]",
                        str(error_payload.get("message") or "").strip(),
                    )
                except (AttributeError, TypeError, ValueError):
                    error_code = ""
                    error_message = ""
                parts = [value for value in (error_code, error_message[:500]) if value]
                if parts:
                    detail = f": {' - '.join(parts)}"
            lowered_detail = detail.lower()
            if response.status_code == 429:
                category = "rate_limit"
            elif response.status_code >= 500:
                category = "server_error"
            elif response.status_code == 400 and (
                "grammar" in lowered_detail or "dflash" in lowered_detail
            ):
                category = "grammar_unsupported"
            else:
                category = f"http_{response.status_code}"
            raise LLMProviderError(
                f"{self.provider_label} API returned HTTP {response.status_code}{detail}",
                retry_after=retry_after,
                retryable=response.status_code == 429 or response.status_code >= 500,
                category=category,
                elapsed_ms=elapsed_ms,
            )

        try:
            response_payload = response.json()
            content = response_payload["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("completion content is not text")
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise LLMProviderError(
                f"{self.provider_label} returned an invalid structured response",
                retryable=True,
                category="invalid_response",
                elapsed_ms=elapsed_ms,
            ) from error
        return content, response_payload, elapsed_ms

    def analyze(
        self,
        issue: IssueRecord,
        report: InvestigationReport,
        evidence: Sequence[EvidenceSnippet],
    ) -> LLMAnalysisResult:
        user_payload = {
            "issue": {
                "number": issue.number,
                "title": issue.title,
                "body": issue.body,
                "labels": issue.labels,
            },
            "repository_evidence": [
                snippet.model_dump(mode="json") for snippet in evidence
            ],
        }
        content, response_payload, elapsed_ms = self._request_structured(
            SYSTEM_PROMPT,
            user_payload,
            LLMAnalysisResponse.model_json_schema(),
        )
        usage = response_payload.get("usage") or {}
        input_tokens = _nonnegative_int(usage.get("prompt_tokens"))
        output_tokens = _nonnegative_int(usage.get("completion_tokens"))
        request_id = response_payload.get("id")
        system_fingerprint = response_payload.get("system_fingerprint")
        choices = response_payload.get("choices") or []
        finish_reason = choices[0].get("finish_reason") if choices else None
        if finish_reason == "length":
            raise LLMProviderError(
                f"{self.provider_label} exhausted the analysis output budget",
                retryable=False,
                category="output_truncated",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                elapsed_ms=elapsed_ms,
                request_id=request_id,
                system_fingerprint=system_fingerprint,
            )
        try:
            analysis = LLMAnalysisResponse.model_validate_json(content)
        except (TypeError, ValueError, ValidationError) as error:
            category, detail = _structured_validation_detail(error)
            raise LLMProviderError(
                f"{self.provider_label} returned an invalid structured response ({detail})",
                retryable=True,
                category=category,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                elapsed_ms=elapsed_ms,
                request_id=request_id,
                system_fingerprint=system_fingerprint,
            ) from error

        normalized_analysis = self._normalize_analysis(analysis, report, evidence)
        try:
            self._validate_evidence_references(normalized_analysis, evidence)
        except LLMProviderError as error:
            error.input_tokens = input_tokens
            error.output_tokens = output_tokens
            error.elapsed_ms = elapsed_ms
            error.request_id = request_id
            error.system_fingerprint = system_fingerprint
            raise
        return LLMAnalysisResult(
            provider=self.provider,
            model=self.model,
            request_id=request_id,
            system_fingerprint=system_fingerprint,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            elapsed_ms=elapsed_ms,
            analysis=normalized_analysis,
        )

    @staticmethod
    def _normalize_analysis(
        response: LLMAnalysisResponse,
        report: InvestigationReport,
        evidence: Sequence[EvidenceSnippet],
    ) -> LLMAnalysis:
        contradictions = [
            f"{observation.evidence_id}: {observation.observation}"
            for observation in response.evidence_observations
            if observation.alignment is EvidenceAlignment.CONTRADICTS_ISSUE
        ]
        if evidence:
            primary_file = evidence[0].file
            primary_symbol = evidence[0].symbol
        elif report.candidates:
            primary_file = report.candidates[0].file
            primary_symbol = (
                report.candidates[0].qualified_symbol or report.candidates[0].symbol
            )
        else:
            primary_file = "unknown"
            primary_symbol = None
        affected_component = (
            f"{primary_file}::{primary_symbol}" if primary_symbol else primary_file
        )
        hypothesis = LLMHypothesis(
            **response.hypothesis.model_dump(),
            validation_step=OpenCodeIssueAnalyzer._validation_step(
                report,
                evidence,
                response.hypothesis.evidence_ids,
            ),
        )
        return LLMAnalysis(
            summary=response.summary,
            issue_type=response.issue_type,
            affected_component=affected_component,
            reproduction_completeness=response.reproduction_completeness,
            evidence_observations=response.evidence_observations,
            contradictions=contradictions,
            reranked_evidence_ids=[snippet.id for snippet in evidence],
            hypotheses=[hypothesis],
            needs_more_evidence=bool(response.hypothesis.missing_evidence),
        )

    @staticmethod
    def _validation_step(
        report: InvestigationReport,
        evidence: Sequence[EvidenceSnippet],
        cited_evidence_ids: Sequence[str],
    ) -> str:
        evidence_by_id = {snippet.id: snippet for snippet in evidence}
        primary = next(
            (
                evidence_by_id[evidence_id]
                for evidence_id in cited_evidence_ids
                if evidence_id in evidence_by_id
            ),
            None,
        )
        if primary is not None:
            location = (
                f"{primary.file}::{primary.symbol}" if primary.symbol else primary.file
            )
        elif report.candidates:
            candidate = report.candidates[0]
            symbol = candidate.qualified_symbol or candidate.symbol
            location = f"{candidate.file}::{symbol}" if symbol else candidate.file
        else:
            location = "the highest-ranked repository location"
        return (
            f"Inspect the cited behavior at {location}, then run the smallest existing "
            "relevant test and compare the result with the Issue without modifying files."
        )

    def _validate_evidence_references(
        self,
        analysis: LLMAnalysis,
        evidence: Sequence[EvidenceSnippet],
    ) -> None:
        valid_ids = {snippet.id for snippet in evidence}
        referenced_ids = set(analysis.reranked_evidence_ids)
        observed_ids = {
            observation.evidence_id for observation in analysis.evidence_observations
        }
        if observed_ids != valid_ids or len(analysis.evidence_observations) != len(evidence):
            raise LLMProviderError(
                f"{self.provider_label} did not provide exactly one observation "
                "for every evidence ID",
                retryable=True,
                category="evidence_observation_coverage",
            )
        referenced_ids.update(observed_ids)
        for hypothesis in analysis.hypotheses:
            referenced_ids.update(hypothesis.evidence_ids)
        unknown_ids = referenced_ids - valid_ids
        if unknown_ids:
            unknown = ", ".join(sorted(unknown_ids))
            raise LLMProviderError(
                f"{self.provider_label} cited unknown evidence IDs: {unknown}",
                retryable=True,
                category="unknown_evidence_id",
            )

    def rerank(
        self,
        issue: IssueRecord,
        evidence: Sequence[EvidenceSnippet],
    ) -> EvidenceRerankResult:
        user_payload = {
            "issue": {
                "number": issue.number,
                "title": issue.title,
                "body": issue.body,
                "labels": issue.labels,
            },
            "repository_evidence": [
                snippet.model_dump(mode="json") for snippet in evidence
            ],
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": DEEPSEEK_RERANK_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False),
                },
            ],
            "temperature": self.temperature,
            "reasoning_effort": self.rerank_reasoning_effort,
        }
        if self.seed is not None:
            payload["seed"] = self.seed
        total_input_tokens = 0
        total_output_tokens = 0
        total_elapsed_ms = 0.0
        rank_lines: list[str] = []
        response_payload: dict = {}
        attempts = 0
        for attempts, output_budget in enumerate(
            (
                OPENCODE_RERANK_INITIAL_OUTPUT_TOKENS,
                self.rerank_max_output_tokens,
            ),
            start=1,
        ):
            payload["max_tokens"] = output_budget
            try:
                content, response_payload, elapsed_ms = self._request_completion(payload)
            except LLMProviderError as error:
                error.attempts = attempts
                error.input_tokens += total_input_tokens
                error.output_tokens += total_output_tokens
                error.elapsed_ms = round(error.elapsed_ms + total_elapsed_ms, 3)
                raise
            usage = response_payload.get("usage") or {}
            total_input_tokens += _nonnegative_int(usage.get("prompt_tokens"))
            total_output_tokens += _nonnegative_int(usage.get("completion_tokens"))
            total_elapsed_ms += elapsed_ms
            rank_lines = RANK_LINE_PATTERN.findall(content)
            finish_reason = (
                (response_payload.get("choices") or [{}])[0].get("finish_reason")
            )
            if rank_lines or finish_reason != "length":
                break

        if len(rank_lines) != 1:
            finish_reason = (
                (response_payload.get("choices") or [{}])[0].get("finish_reason")
            )
            category = "output_truncated" if finish_reason == "length" else "invalid_rank"
            if category == "output_truncated":
                message = (
                    "OpenCode exhausted both rank output budgets before returning a RANK line"
                )
            else:
                message = (
                    "OpenCode returned an invalid rank response "
                    f"(expected one RANK line, found {len(rank_lines)})"
                )
            error = LLMProviderError(
                message,
                category=category,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                elapsed_ms=round(total_elapsed_ms, 3),
                request_id=response_payload.get("id"),
                system_fingerprint=response_payload.get("system_fingerprint"),
            )
            error.attempts = attempts
            raise error
        reranked_ids = list(
            dict.fromkeys(value.strip() for value in rank_lines[0].split(","))
        )
        if len(reranked_ids) > OPENCODE_RERANK_MAX_IDS:
            error = LLMProviderError(
                "OpenCode returned more than three ranked evidence IDs",
                category="invalid_rank",
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                elapsed_ms=round(total_elapsed_ms, 3),
                request_id=response_payload.get("id"),
                system_fingerprint=response_payload.get("system_fingerprint"),
            )
            error.attempts = attempts
            raise error
        valid_ids = {snippet.id for snippet in evidence}
        unknown_ids = set(reranked_ids) - valid_ids
        if unknown_ids:
            error = LLMProviderError(
                "OpenCode referenced unknown evidence IDs: "
                + ", ".join(sorted(unknown_ids)),
                category="unknown_evidence_id",
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                elapsed_ms=round(total_elapsed_ms, 3),
                request_id=response_payload.get("id"),
                system_fingerprint=response_payload.get("system_fingerprint"),
            )
            error.attempts = attempts
            raise error
        analysis = EvidenceRerankAnalysis(reranked_evidence_ids=reranked_ids)
        return EvidenceRerankResult(
            provider=self.provider,
            model=self.model,
            request_id=response_payload.get("id"),
            system_fingerprint=response_payload.get("system_fingerprint"),
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            elapsed_ms=round(total_elapsed_ms, 3),
            attempts=attempts,
            analysis=analysis,
        )
