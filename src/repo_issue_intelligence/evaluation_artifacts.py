"""Version-aware read adapter for evaluation JSON artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .agent_evaluation import AgentAnalysisRun
from .agent_evaluation_v2 import AgentAnalysisRunV2


def load_evaluation_artifact(path: Path) -> AgentAnalysisRun | AgentAnalysisRunV2 | dict[str, Any]:
    """Load a known V1/V2 artifact; preserve unversioned legacy JSON as unknown."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Evaluation artifact must contain a JSON object")
    protocol = payload.get("protocol")
    if protocol == "v2":
        return AgentAnalysisRunV2.model_validate(payload)
    if protocol is None:
        return payload
    if protocol != "v1":
        raise ValueError("Unsupported evaluation artifact protocol")
    return AgentAnalysisRun.model_validate(payload)
