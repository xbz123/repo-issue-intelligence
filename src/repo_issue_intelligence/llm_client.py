from __future__ import annotations

import json
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
    LLMAnalysisResult,
)

GROQ_API_BASE_URL = "https://api.groq.com/openai/v1"
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
    model: str

    def analyze(
        self,
        issue: IssueRecord,
        report: InvestigationReport,
        evidence: Sequence[EvidenceSnippet],
    ) -> LLMAnalysisResult: ...

    def close(self) -> None: ...


class GroqAPIError(RuntimeError):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class GroqIssueAnalyzer:
    def __init__(
        self,
        api_key: str,
        model: str = "openai/gpt-oss-20b",
        max_output_tokens: int = 1_600,
        timeout_seconds: float = 30.0,
        reasoning_effort: str = "low",
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Groq API key is required")
        if reasoning_effort not in {"low", "medium", "high"}:
            raise ValueError("reasoning_effort must be low, medium, or high")
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.reasoning_effort = reasoning_effort
        self._client = client or httpx.Client(
            base_url=GROQ_API_BASE_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_seconds,
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
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            },
            "max_completion_tokens": self.max_output_tokens,
        }
        if self.model.startswith("openai/gpt-oss-"):
            payload["reasoning_effort"] = self.reasoning_effort

        started = perf_counter()
        try:
            response = self._client.post("/chat/completions", json=payload)
        except httpx.HTTPError as error:
            raise GroqAPIError(
                f"Groq request failed: {type(error).__name__}"
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
                f"Groq API returned HTTP {response.status_code}{detail}",
                retry_after=retry_after,
            )

        try:
            response_payload = response.json()
            content = response_payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise GroqAPIError("Groq returned an invalid structured response") from error
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
        except (TypeError, ValueError, ValidationError) as error:
            raise GroqAPIError("Groq returned an invalid rerank response") from error

        valid_ids = {snippet.id for snippet in evidence}
        reranked_ids = analysis.reranked_evidence_ids
        unknown_ids = set(reranked_ids) - valid_ids
        if unknown_ids:
            raise GroqAPIError(
                f"Groq referenced unknown evidence IDs: {', '.join(sorted(unknown_ids))}"
            )
        usage = response_payload.get("usage") or {}
        return EvidenceRerankResult(
            provider="groq",
            model=self.model,
            request_id=response_payload.get("id"),
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
            LLMAnalysis.model_json_schema(),
            "issue_investigation",
        )
        try:
            analysis = LLMAnalysis.model_validate_json(content)
        except (TypeError, ValueError, ValidationError) as error:
            raise GroqAPIError("Groq returned an invalid structured response") from error

        analysis = self._normalize_contradictions(analysis)
        self._validate_evidence_references(analysis, evidence)
        usage = response_payload.get("usage") or {}
        return LLMAnalysisResult(
            provider="groq",
            model=self.model,
            request_id=response_payload.get("id"),
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

    @staticmethod
    def _validate_evidence_references(
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
                "Groq did not provide exactly one observation for every evidence ID"
            )
        if (
            analysis.needs_more_evidence
            and analysis.hypotheses
            and not any(hypothesis.missing_evidence for hypothesis in analysis.hypotheses)
        ):
            raise GroqAPIError(
                "Groq requested more evidence without naming a missing artifact"
            )
        referenced_ids.update(observed_ids)
        for hypothesis in analysis.hypotheses:
            referenced_ids.update(hypothesis.evidence_ids)
        unknown_ids = referenced_ids - valid_ids
        if unknown_ids:
            unknown = ", ".join(sorted(unknown_ids))
            raise GroqAPIError(f"Groq cited unknown evidence IDs: {unknown}")
