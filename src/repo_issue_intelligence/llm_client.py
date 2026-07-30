from __future__ import annotations

import json
from collections.abc import Sequence
from enum import StrEnum
from time import perf_counter
from typing import Literal, Protocol

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
)

GROQ_API_BASE_URL = "https://api.groq.com/openai/v1"
OPENCODE_API_BASE_URL = "https://opencode.ai/zen/v1"
OPENCODE_DEFAULT_MODEL = "deepseek-v4-flash-free"
RERANK_SYSTEM_PROMPT = """Rank the supplied repository evidence by how likely each item is to
contain the source location that must change to fix the GitHub issue. Use only evidence IDs from
the input. Return the most relevant IDs first. Do not diagnose a root cause or propose a patch."""
SYSTEM_PROMPT = """You investigate a GitHub issue using only the supplied repository evidence.

Return the requested structured analysis. Candidate locations and hypotheses are not confirmed
root causes. Every hypothesis must cite one or more evidence IDs from the input. Do not invent
files, symbols, stack traces, runtime results, or affected versions. If the evidence is not enough,
set needs_more_evidence to true and explain what is missing.
Return no more than two hypotheses. Omit a weak hypothesis instead of returning one with an empty
evidence_ids list.

Before forming hypotheses, inspect every repository_evidence item line by line and add exactly one
evidence_observation for each evidence ID. Set its alignment to supports_issue, contradicts_issue,
or neutral. Look specifically for guards, exception handlers, fallbacks, return values, and tests.
Treat repository evidence as stronger than an Issue's unverified causal claim. If the code
explicitly implements behavior the Issue says is missing, use contradicts_issue, also include the
conflict in contradictions, and investigate version, routing, deployment, or runtime-path mismatch
instead of claiming the handler is absent. If needs_more_evidence is true, name the concrete
missing artifact in at least one hypothesis's missing_evidence list.

A validation step must be a non-mutating test or inspection, never a proposed code change. Do not
emit or execute shell commands.
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
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


# Compatibility alias for callers that imported the original provider-specific name.
GroqAPIError = LLMProviderError


class LLMProvider(StrEnum):
    GROQ = "groq"
    OPENCODE = "opencode"


class OpenAICompatibleIssueAnalyzer:
    def __init__(
        self,
        api_key: str,
        model: str,
        provider: str,
        base_url: str,
        structured_output: Literal["json_schema", "json_object"],
        max_output_tokens: int = 1_600,
        timeout_seconds: float = 30.0,
        reasoning_effort: str = "low",
        temperature: float = 1.0,
        seed: int | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise ValueError(f"{provider} API key is required")
        if reasoning_effort not in {"low", "medium", "high"}:
            raise ValueError("reasoning_effort must be low, medium, or high")
        if not 0 <= temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")
        self.provider = provider
        self.provider_label = (
            "OpenCode" if provider == LLMProvider.OPENCODE else provider.capitalize()
        )
        self.model = model
        self.structured_output = structured_output
        self.max_output_tokens = max_output_tokens
        self.reasoning_effort = (
            reasoning_effort
            if provider == LLMProvider.GROQ and model.startswith("openai/gpt-oss-")
            else None
        )
        self.temperature = temperature
        self.seed = seed
        self._client = client or httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_seconds,
            trust_env=False,
        )
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _request_structured(
        self,
        system_prompt: str,
        user_payload: dict,
        schema: dict,
        schema_name: str,
    ) -> tuple[str, dict, float]:
        if self.structured_output == "json_object":
            schema_text = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
            example = ""
            if schema_name == "evidence_rerank":
                example = (
                    '\nExample shape: {"summary":"Brief ranking rationale.",'
                    '"reranked_evidence_ids":["E1","E2"]}'
                )
            system_prompt = (
                f"{system_prompt}\nReturn only one minified JSON object, without Markdown, "
                f"that validates against this JSON Schema:\n{schema_text}{example}"
            )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False),
                },
            ],
            "temperature": self.temperature,
        }
        if self.structured_output == "json_schema":
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            }
            payload["max_completion_tokens"] = self.max_output_tokens
        else:
            payload["response_format"] = {"type": "json_object"}
            payload["max_tokens"] = self.max_output_tokens
        if self.seed is not None:
            payload["seed"] = self.seed
        if self.provider == LLMProvider.GROQ and self.model.startswith("openai/gpt-oss-"):
            payload["reasoning_effort"] = self.reasoning_effort

        started = perf_counter()
        try:
            response = self._client.post("/chat/completions", json=payload)
        except httpx.HTTPError as error:
            raise GroqAPIError(
                f"{self.provider_label} request failed: {type(error).__name__}"
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
                    error_message = str(error_payload.get("message") or "").strip()
                except (AttributeError, TypeError, ValueError):
                    error_code = ""
                    error_message = ""
                parts = [value for value in (error_code, error_message[:500]) if value]
                if parts:
                    detail = f": {' - '.join(parts)}"
            raise GroqAPIError(
                f"{self.provider_label} API returned HTTP {response.status_code}{detail}",
                retry_after=retry_after,
            )

        try:
            response_payload = response.json()
            content = response_payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise GroqAPIError(
                f"{self.provider_label} returned an invalid structured response"
            ) from error
        return content, response_payload, elapsed_ms

    def rerank(
        self,
        issue: IssueRecord,
        evidence: Sequence[EvidenceSnippet],
    ) -> EvidenceRerankResult:
        user_payload = {
            "issue": {
                "number": issue.number,
                "title": issue.title,
                "body": issue.body[:3_000],
                "labels": issue.labels,
            },
            "repository_evidence": [
                snippet.model_dump(mode="json") for snippet in evidence
            ],
        }
        content, response_payload, elapsed_ms = self._request_structured(
            RERANK_SYSTEM_PROMPT,
            user_payload,
            EvidenceRerankAnalysis.model_json_schema(),
            "evidence_rerank",
        )
        try:
            analysis = EvidenceRerankAnalysis.model_validate_json(content)
        except ValidationError as error:
            details = ", ".join(
                f"{'.'.join(str(value) for value in item['loc']) or '<root>'}:{item['type']}"
                for item in error.errors(include_input=False, include_url=False)[:5]
            )
            raise GroqAPIError(
                f"{self.provider_label} returned an invalid rerank response ({details})"
            ) from error
        except (TypeError, ValueError) as error:
            raise GroqAPIError(
                f"{self.provider_label} returned an invalid rerank response"
            ) from error

        valid_ids = {snippet.id for snippet in evidence}
        reranked_ids = analysis.reranked_evidence_ids
        if not reranked_ids:
            raise GroqAPIError(
                f"{self.provider_label} returned an empty evidence rerank"
            )
        unknown_ids = set(reranked_ids) - valid_ids
        if unknown_ids:
            raise GroqAPIError(
                f"{self.provider_label} referenced unknown evidence IDs: "
                f"{', '.join(sorted(unknown_ids))}"
            )
        usage = response_payload.get("usage") or {}
        return EvidenceRerankResult(
            provider=self.provider,
            model=self.model,
            request_id=response_payload.get("id"),
            system_fingerprint=response_payload.get("system_fingerprint"),
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            elapsed_ms=elapsed_ms,
            analysis=analysis,
        )

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
                "body": issue.body[:6_000],
                "labels": issue.labels,
            },
            "deterministic_candidates": [
                {
                    "file": candidate.file,
                    "symbol": candidate.symbol,
                    "lines": candidate.lines,
                    "confidence": candidate.confidence,
                    "evidence": candidate.evidence,
                }
                for candidate in report.candidates
            ],
            "repository_evidence": [
                snippet.model_dump(mode="json") for snippet in evidence
            ],
        }
        content, response_payload, elapsed_ms = self._request_structured(
            SYSTEM_PROMPT,
            user_payload,
            LLMAnalysisResponse.model_json_schema(),
            "issue_investigation",
        )
        try:
            analysis = LLMAnalysisResponse.model_validate_json(content)
        except (TypeError, ValueError, ValidationError) as error:
            raise GroqAPIError(
                f"{self.provider_label} returned an invalid structured response"
            ) from error

        analysis = self._normalize_contradictions(analysis)
        self._validate_evidence_references(analysis, evidence)
        usage = response_payload.get("usage") or {}
        return LLMAnalysisResult(
            provider=self.provider,
            model=self.model,
            request_id=response_payload.get("id"),
            system_fingerprint=response_payload.get("system_fingerprint"),
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            elapsed_ms=elapsed_ms,
            analysis=analysis,
        )

    @staticmethod
    def _normalize_contradictions(analysis: LLMAnalysis) -> LLMAnalysis:
        if analysis.contradictions:
            return analysis
        derived = [
            f"{observation.evidence_id}: {observation.observation}"
            for observation in analysis.evidence_observations
            if observation.alignment is EvidenceAlignment.CONTRADICTS_ISSUE
        ]
        if not derived:
            return analysis
        return analysis.model_copy(update={"contradictions": derived})

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
            raise GroqAPIError(
                f"{self.provider_label} did not provide exactly one observation "
                "for every evidence ID"
            )
        if (
            analysis.needs_more_evidence
            and analysis.hypotheses
            and not any(hypothesis.missing_evidence for hypothesis in analysis.hypotheses)
        ):
            raise GroqAPIError(
                f"{self.provider_label} requested more evidence without naming "
                "a missing artifact"
            )
        referenced_ids.update(observed_ids)
        for hypothesis in analysis.hypotheses:
            referenced_ids.update(hypothesis.evidence_ids)
        unknown_ids = referenced_ids - valid_ids
        if unknown_ids:
            unknown = ", ".join(sorted(unknown_ids))
            raise GroqAPIError(
                f"{self.provider_label} cited unknown evidence IDs: {unknown}"
            )


class GroqIssueAnalyzer(OpenAICompatibleIssueAnalyzer):
    def __init__(
        self,
        api_key: str,
        model: str = "openai/gpt-oss-20b",
        max_output_tokens: int = 1_600,
        timeout_seconds: float = 30.0,
        reasoning_effort: str = "low",
        temperature: float = 1.0,
        seed: int | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            model=model,
            provider=LLMProvider.GROQ,
            base_url=GROQ_API_BASE_URL,
            structured_output="json_schema",
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
            reasoning_effort=reasoning_effort,
            temperature=temperature,
            seed=seed,
            client=client,
        )


class OpenCodeIssueAnalyzer(OpenAICompatibleIssueAnalyzer):
    def __init__(
        self,
        api_key: str,
        model: str = OPENCODE_DEFAULT_MODEL,
        max_output_tokens: int = 4_096,
        timeout_seconds: float = 60.0,
        temperature: float = 1.0,
        seed: int | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            model=model,
            provider=LLMProvider.OPENCODE,
            base_url=OPENCODE_API_BASE_URL,
            structured_output="json_object",
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
            temperature=temperature,
            seed=seed,
            client=client,
        )
