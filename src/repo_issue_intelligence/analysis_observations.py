"""Safe local analysis observations; missing reports never inherit requested values."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Literal

from pydantic import Field

from .analysis_contract import LLMAnalysisV2
from .models import StrictOutputModel


class AnalysisResultV2(StrictOutputModel):
    protocol: Literal["analysis-v2"] = "analysis-v2"
    prompt_version: str
    analysis: LLMAnalysisV2
    requested: dict[str, object]
    reported: dict[str, object | None]
    local: dict[str, object | None]
    diagnostics: tuple[str, ...] = Field(default=())


def empty_reported() -> dict[str, object | None]:
    return dict.fromkeys(
        (
            "model",
            "provider",
            "service_tier",
            "temperature",
            "seed",
            "reasoning_effort",
            "input_tokens",
            "output_tokens",
            "request_id",
            "response_id",
            "system_fingerprint",
        )
    )


def safe_reported_text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        return None
    if any(character in value for character in ("\n", "\r", "://")) or re.search(
        r"(?i)\bbearer\s+\S+|(?:api[_-]?key|password|secret|authorization|access[_-]?token)\s*[:=]"
        r"|(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{8,}",
        value,
    ):
        return None
    return value


def optional_nonnegative_integer(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def extract_api_observations(
    payload: Mapping[str, object],
    headers: Mapping[str, str],
) -> dict[str, object | None]:
    reported = empty_reported()
    for key in ("model", "provider", "service_tier", "reasoning_effort", "system_fingerprint"):
        reported[key] = safe_reported_text(payload.get(key))
    reported["response_id"] = safe_reported_text(payload.get("id"))
    reported["request_id"] = safe_reported_text(headers.get("x-request-id"))
    seed = payload.get("seed")
    reported["seed"] = seed if type(seed) is int else None
    temperature = payload.get("temperature")
    if type(temperature) in (float, int):
        try:
            if math.isfinite(temperature):
                reported["temperature"] = temperature
        except OverflowError:
            pass
    usage = payload.get("usage")
    if isinstance(usage, Mapping):
        reported["input_tokens"] = optional_nonnegative_integer(usage.get("prompt_tokens"))
        reported["output_tokens"] = optional_nonnegative_integer(usage.get("completion_tokens"))
    return reported


def metadata_diagnostics(
    requested: Mapping[str, object],
    reported: Mapping[str, object | None],
) -> tuple[str, ...]:
    return tuple(
        "reported_model_differs_from_requested" if key == "model" else f"reported_{key}_mismatch"
        for key in ("model", "provider", "temperature", "seed", "reasoning_effort", "service_tier")
        if requested.get(key) is not None
        and reported.get(key) is not None
        and requested[key] != reported[key]
    )
