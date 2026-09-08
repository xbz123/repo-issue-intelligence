import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from repo_issue_intelligence.analysis_contract import (
    normalize_analysis_v2,
    project_analysis_v2_to_v1,
    project_legacy_analysis,
    validated_evidence,
)
from repo_issue_intelligence.models import (
    EvidenceRerankAnalysis,
    EvidenceSnippet,
    LLMAnalysis,
    LLMAnalysisResponse,
)


def evidence_lookup() -> dict[str, EvidenceSnippet]:
    return {
        "E1": EvidenceSnippet(
            id="E1", file="first.py", symbol="first", lines="1-2", content="pass"
        ),
        "E7": EvidenceSnippet(
            id="E7", file="handler.py", symbol="handle", lines="7-9", content="return None"
        ),
    }


def response() -> LLMAnalysisResponse:
    return LLMAnalysisResponse.model_validate(
        {
            "summary": "The runtime path needs verification.",
            "issue_type": "bug",
            "reproduction_completeness": "partial",
            "evidence_observations": [
                {"evidence_id": "E1", "alignment": "neutral", "observation": "Not the handler."},
                {
                    "evidence_id": "E7",
                    "alignment": "contradicts_issue",
                    "observation": "Has a guard.",
                },
            ],
            "hypothesis": {
                "description": "The deployed version may differ.",
                "confidence": 0.5,
                "evidence_ids": ["E7", "E1"],
                "missing_evidence": ["Deployed version"],
            },
        }
    )


def test_primary_and_local_fields_follow_first_citation_not_input_order() -> None:
    analysis = normalize_analysis_v2(response(), ["E1", "E7"], evidence_lookup())

    assert analysis.input_evidence_ids == ["E1", "E7"]
    assert analysis.primary_evidence_id == "E7"
    assert analysis.affected_component == "handler.py::handle"
    assert analysis.hypotheses[0].validation_step == (
        "Inspect the cited behavior at handler.py::handle, then run the smallest existing "
        "relevant test and compare the result with the Issue without modifying files."
    )
    assert analysis.contradictions == ["E7: Has a guard."]
    assert analysis.needs_more_evidence is True
    assert "reranked_evidence_ids" not in analysis.model_dump()


@pytest.mark.parametrize("cited_ids", [["E999", "E7"], ["E7", "E999"], ["E7", "E7"], []])
def test_every_citation_is_validated_before_primary_derivation(cited_ids: list[str]) -> None:
    reply = response()
    reply.hypothesis.evidence_ids = cited_ids
    with pytest.raises(ValueError, match="^Invalid evidence references$"):
        normalize_analysis_v2(reply, ["E1", "E7"], evidence_lookup())


@pytest.mark.parametrize("observed_ids", [["E1"], ["E1", "E1"], ["E1", "E999"]])
def test_observations_cover_every_input_exactly_once(observed_ids: list[str]) -> None:
    reply = response()
    reply.evidence_observations = [
        reply.evidence_observations[0].model_copy(update={"evidence_id": evidence_id})
        for evidence_id in observed_ids
    ]
    with pytest.raises(ValueError, match="^Invalid evidence observations$"):
        normalize_analysis_v2(reply, ["E1", "E7"], evidence_lookup())


def test_lookup_only_evidence_cannot_be_cited() -> None:
    lookup = evidence_lookup()
    lookup["E999"] = lookup["E7"].model_copy(update={"id": "E999"})
    reply = response()
    reply.hypothesis.evidence_ids = ["E999", "E7"]
    with pytest.raises(ValueError, match="^Invalid evidence references$"):
        normalize_analysis_v2(reply, ["E1", "E7"], lookup)


@pytest.mark.parametrize("case", ["duplicate", "missing", "mismatch", "extra_mismatch", "empty"])
def test_preflight_rejects_invalid_input_identity(case: str) -> None:
    ids = ["E1", "E7"]
    lookup = evidence_lookup()
    if case == "duplicate":
        ids.append("E1")
    elif case == "missing":
        del lookup["E1"]
    elif case == "mismatch":
        lookup["E1"] = lookup["E7"]
    elif case == "extra_mismatch":
        lookup["E999"] = lookup["E7"]
    else:
        ids = []
    with pytest.raises(ValueError, match="^Invalid input evidence$"):
        validated_evidence(ids, lookup)
    with pytest.raises(ValueError, match="^Invalid input evidence$"):
        normalize_analysis_v2(response(), ids, lookup)


def test_preflight_snapshots_snippets_and_input_order() -> None:
    lookup = evidence_lookup()
    ids = ["E7", "E1"]
    snapshots = validated_evidence(ids, lookup)
    lookup["E7"].file = "mutated.py"
    ids.reverse()
    assert isinstance(snapshots, tuple)
    assert [snippet.id for snippet in snapshots] == ["E7", "E1"]
    assert snapshots[0].file == "handler.py"


@pytest.mark.parametrize("ids", [7, {"E1", "E7"}, ["E1", []], "E1"])
def test_preflight_rejects_malformed_or_unordered_ids_with_bounded_error(ids: object) -> None:
    with pytest.raises(ValueError, match="^Invalid input evidence$"):
        validated_evidence(ids, evidence_lookup())


def test_normalization_does_not_share_mutable_provider_values() -> None:
    reply = response()
    analysis = normalize_analysis_v2(reply, ["E1", "E7"], evidence_lookup())
    reply.evidence_observations[0].observation = "mutated"
    reply.hypothesis.evidence_ids.reverse()
    assert analysis.evidence_observations[0].observation == "Not the handler."
    assert analysis.hypotheses[0].evidence_ids == ["E7", "E1"]


def test_legacy_projection_preserves_original_values_and_labels_request_model() -> None:
    legacy = {
        "model": "requested-old-model",
        "analysis": {
            "reranked_evidence_ids": ["E1", "E7"],
            "affected_component": "first.py::first",
            "hypotheses": [{"evidence_ids": ["E7"]}],
        },
        "historical_unknown_field": {"original": True},
    }
    projection = project_legacy_analysis(legacy)
    assert projection == {
        "protocol_version": "v1",
        "evidence_order_semantics": "legacy_input_order",
        "requested_model": "requested-old-model",
        "reported_model": None,
        "payload": legacy,
    }
    projection["payload"]["analysis"]["reranked_evidence_ids"].reverse()
    assert legacy["analysis"]["reranked_evidence_ids"] == ["E1", "E7"]
    assert legacy["analysis"]["affected_component"] == "first.py::first"


def test_v2_projection_explicitly_labels_input_order_without_mutating_analysis() -> None:
    analysis = normalize_analysis_v2(response(), ["E1", "E7"], evidence_lookup())
    before = analysis.model_dump(mode="json")
    projection = project_analysis_v2_to_v1(analysis)
    assert projection["protocol_version"] == "v1"
    assert projection["evidence_order_semantics"] == "legacy_input_order"
    assert projection["requested_model"] is None
    assert projection["reported_model"] is None
    legacy = LLMAnalysis.model_validate(projection["payload"])
    assert legacy.reranked_evidence_ids == ["E1", "E7"]
    assert legacy.affected_component == "handler.py::handle"
    projection["payload"]["hypotheses"][0]["evidence_ids"].reverse()
    assert analysis.model_dump(mode="json") == before


def test_provider_and_rank_only_schemas_match_pre_pr3_snapshots() -> None:
    snapshot = json.loads(
        (Path(__file__).parent / "fixtures/protocol_v2/provider_schema.json").read_text()
    )
    assert LLMAnalysisResponse.model_json_schema() == snapshot["full_analysis"]
    assert EvidenceRerankAnalysis.model_json_schema() == snapshot["rank_only"]


def test_normalizer_works_with_storage_and_provider_imports_blocked() -> None:
    source = Path(__file__).resolve().parents[1] / "src"
    script = """
import builtins
import json
import sys

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name == "sqlite3" or name.startswith("sqlite3.") or any(
        part in name.split(".") for part in (
            "agent_store", "agent_store_migrations", "llm_client", "codex_cli"
        )
    ):
        raise AssertionError("Storage or provider import attempted")
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
from repo_issue_intelligence.analysis_contract import normalize_analysis_v2
from repo_issue_intelligence.models import EvidenceSnippet, LLMAnalysisResponse
payload = json.loads(sys.stdin.read())
lookup = {key: EvidenceSnippet.model_validate(value) for key, value in payload["lookup"].items()}
result = normalize_analysis_v2(
    LLMAnalysisResponse.model_validate(payload["response"]), ["E1", "E7"], lookup
)
assert result.primary_evidence_id == "E7"
assert "sqlite3" not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        input=json.dumps(
            {
                "response": response().model_dump(mode="json"),
                "lookup": {key: snippet.model_dump() for key, snippet in evidence_lookup().items()},
            }
        ),
        env={**os.environ, "PYTHONPATH": str(source)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
