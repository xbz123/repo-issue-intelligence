import copy
import json
import os
import shutil
import stat
from pathlib import Path

import pytest

from repo_issue_intelligence import result_catalog
from repo_issue_intelligence.result_catalog import (
    CATALOG_PATH,
    END,
    START,
    entry_facts,
    entry_metrics,
    load_catalog,
    publish_catalog,
    render_summary,
    validate_catalog,
)

ROOT = Path(__file__).resolve().parents[1]


def test_current_catalog_preserves_dataset_role_and_audit_history():
    catalog = load_catalog(ROOT)
    assert catalog.datasets["manifest-v20"].role == "regression/development"
    assert catalog.datasets["manifest-v20"].historical_tiers == {
        "main": 17,
        "calibration": 11,
        "generalization": 172,
    }
    current = catalog.entries[catalog.current["candidate_pool"]]
    assert current.completeness == "summary-only"
    assert current.details is None
    assert entry_facts(ROOT, current)["index_version"] == 25
    assert current.supersedes == ("pool-v035-full",)
    assert catalog.entries["pool-v035-full"].completeness == "full"
    assert entry_metrics(ROOT, current) == {"matched": 247, "targets": 267, "misses": 20}
    assert entry_metrics(ROOT, catalog.entries["pool-v035-full"]) == {
        "matched": 246,
        "targets": 267,
        "misses": 21,
    }
    rerank = catalog.entries[catalog.current["hybrid_rerank"]]
    facts = entry_facts(ROOT, rerank)
    assert facts["requested_model"] == "gpt-5.6-luna"
    assert facts["requested_service_tier"] == "fast"
    assert facts["reported_model"] is None
    assert catalog.datasets["manifest-v8"].case_count == 50


@pytest.mark.parametrize(
    "mutation",
    [
        "holdout",
        "tier",
        "missing_pointer",
        "source_escape",
        "wrong_type",
        "old_current",
        "borrow_details",
        "reported_request",
        "missing_file",
        "cycle",
        "unbalanced",
    ],
)
def test_catalog_rejects_drift_and_unverifiable_claims(mutation):
    payload = json.loads((ROOT / CATALOG_PATH).read_text())
    entry = payload["entries"]["pool-v037-summary"]
    if mutation == "holdout":
        payload["datasets"]["manifest-v20"]["role"] = "independent-holdout"
    elif mutation == "tier":
        payload["datasets"]["manifest-v20"]["historical_tiers"]["generalization"] = 171
    elif mutation == "missing_pointer":
        entry["metrics"]["misses"] = "/not_in_artifact"
    elif mutation == "source_escape":
        entry["artifact"] = "../outside.json"
    elif mutation == "wrong_type":
        payload["current"]["candidate_pool"] = payload["current"]["hybrid_rerank"]
    elif mutation == "old_current":
        payload["current"]["candidate_pool"] = "pool-v035-full"
    elif mutation == "borrow_details":
        entry["details"] = payload["entries"]["pool-v035-full"]["artifact"]
    elif mutation == "reported_request":
        payload["entries"][payload["current"]["hybrid_rerank"]]["provenance"]["reported_model"] = (
            "/model"
        )
    elif mutation == "missing_file":
        entry["artifact"] = "benchmarks/results/missing-result.json"
    elif mutation == "cycle":
        old = payload["entries"]["pool-v035-full"]
        payload["entries"]["archived-copy"] = copy.deepcopy(old)
        old["supersedes"] = ["archived-copy"]
        payload["entries"]["archived-copy"]["supersedes"] = ["pool-v035-full"]
    else:
        entry["metrics"]["misses"] = "/candidate_pool_audit/previous_missing_targets"
    with pytest.raises((ValueError, OSError)):
        validate_catalog(ROOT, payload)


@pytest.mark.parametrize(
    "mutation",
    ["attempts_as_valid", "valid_as_failures", "hits_rate", "oversized_cases", "zero_cases"],
)
def test_catalog_rejects_inconsistent_count_relationships(mutation):
    payload = json.loads((ROOT / CATALOG_PATH).read_text())
    agent = payload["entries"][payload["current"]["agent_analysis"]]
    if mutation == "attempts_as_valid":
        agent["metrics"]["valid"] = "/agent_analysis/combined/attempts"
    elif mutation == "valid_as_failures":
        agent["metrics"]["failures"] = agent["metrics"]["valid"]
    elif mutation == "hits_rate":
        payload["entries"][payload["current"]["hypothesis_grounding"]]["metrics"]["hits"] = "/cases"
    elif mutation == "oversized_cases":
        payload["entries"][payload["current"]["hybrid_rerank"]]["metrics"]["cases"] = (
            "/run/production_targets"
        )
    else:
        agent["metrics"]["case_runs"] = agent["metrics"]["failures"]
    with pytest.raises(ValueError):
        validate_catalog(ROOT, payload)


@pytest.mark.parametrize(
    "document,prefix", [("README.md", ""), ("docs/benchmark-results.md", "../")]
)
def test_documented_current_numbers_are_rendered_from_catalog(document, prefix):
    text = (ROOT / document).read_text()
    assert text.count(START) == text.count(END) == 1
    block = text[text.index(START) : text.index(END) + len(END)]
    assert block == render_summary(ROOT, load_catalog(ROOT), prefix=prefix)
    assert "not an independent holdout" in text
    assert "172 generalization cases" not in text
    if document == "README.md":
        assert "-latest.json" not in text


def _copy_inputs(destination):
    payload = json.loads((ROOT / CATALOG_PATH).read_text())
    paths = {CATALOG_PATH}
    paths.update(item["manifest"] for item in payload["datasets"].values())
    paths.update(item["artifact"] for item in payload["entries"].values())
    for path in paths:
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / path, target)
    return payload, {path: (destination / path).read_bytes() for path in paths}


@pytest.mark.parametrize("failure_operation", ["replace", "fchmod"])
def test_publication_is_atomic_and_does_not_overwrite_historical_results(
    tmp_path, monkeypatch, failure_operation
):
    payload, original = _copy_inputs(tmp_path)
    payload["entries"]["pool-v037-summary"]["notes"] += " Updated catalog annotation."

    def interrupted(*args):
        raise OSError("synthetic publication interruption")

    with monkeypatch.context() as fault:
        fault.setattr(result_catalog.os, failure_operation, interrupted)
        with pytest.raises(OSError):
            publish_catalog(tmp_path, payload)
    assert (tmp_path / CATALOG_PATH).read_bytes() == original[CATALOG_PATH]
    assert not list((tmp_path / CATALOG_PATH).parent.glob(".current-results-*"))
    publish_catalog(tmp_path, payload)
    assert (
        load_catalog(tmp_path)
        .entries["pool-v037-summary"]
        .notes.endswith("Updated catalog annotation.")
    )
    for path, contents in original.items():
        if path != CATALOG_PATH:
            assert (tmp_path / path).read_bytes() == contents


def test_incomplete_or_symlinked_source_prevents_catalog_publication(tmp_path):
    payload, original = _copy_inputs(tmp_path)
    artifact = tmp_path / payload["entries"]["pool-v037-summary"]["artifact"]
    artifact.write_text('{"unfinished":')
    with pytest.raises(ValueError):
        publish_catalog(tmp_path, payload)
    assert (tmp_path / CATALOG_PATH).read_bytes() == original[CATALOG_PATH]
    artifact.unlink()  # Only this test's deliberately damaged temporary copy.
    artifact.symlink_to(ROOT / payload["entries"]["pool-v037-summary"]["artifact"])
    with pytest.raises(ValueError, match="symlink"):
        publish_catalog(tmp_path, payload)
    assert (tmp_path / CATALOG_PATH).read_bytes() == original[CATALOG_PATH]


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
@pytest.mark.parametrize("mode", [0o644, 0o640, 0o600, None])
def test_publication_preserves_permission_bits(tmp_path, mode):
    payload, _ = _copy_inputs(tmp_path)
    destination = tmp_path / CATALOG_PATH
    if mode is None:
        destination.unlink()  # This test's copied catalog, to test first publication.
    else:
        destination.chmod(mode)
    publish_catalog(tmp_path, payload)
    assert stat.S_IMODE(destination.stat().st_mode) == (0o644 if mode is None else mode)


@pytest.mark.parametrize(
    "field,value",
    [
        ("retrieval_protocol", 20),
        ("retrieval_protocol", []),
        ("requested_model", 200),
        ("requested_provider", {"name": "not-a-provider-string"}),
        ("reported_model", 200),
        ("reported_provider", {"name": "not-a-provider-string"}),
        ("requested_model", ""),
    ],
)
def test_publication_rejects_provenance_value_types(tmp_path, field, value):
    payload, original = _copy_inputs(tmp_path)
    entry = payload["entries"][payload["current"]["hybrid_rerank"]]
    artifact = tmp_path / entry["artifact"]
    source = json.loads(artifact.read_text())
    source["provenance_probe"] = {"reported": value}
    artifact.write_text(json.dumps(source))
    entry["provenance"][field] = "/provenance_probe/reported"
    with pytest.raises(ValueError):
        publish_catalog(tmp_path, payload)
    assert (tmp_path / CATALOG_PATH).read_bytes() == original[CATALOG_PATH]


@pytest.mark.parametrize("index", ["-1", "01", "+1", " 1", "\u0661"])
def test_metric_pointer_rejects_non_rfc_array_indices(index):
    catalog = load_catalog(ROOT)
    entry = catalog.entries[catalog.current["agent_analysis"]].model_copy(
        update={"metrics": {"attempts": f"/agent_analysis/runs/{index}/attempts"}}
    )
    with pytest.raises(ValueError):
        entry_metrics(ROOT, entry)


def test_metric_pointers_keep_valid_arrays_numeric_keys_and_escapes(tmp_path):
    payload, _ = _copy_inputs(tmp_path)
    catalog = validate_catalog(tmp_path, payload)
    entry = catalog.entries[catalog.current["agent_analysis"]]
    artifact = tmp_path / entry.artifact
    source = json.loads(artifact.read_text())
    source["pointer_probe"] = {"01": 7, "-1": 11, "a/b": 13, "a~b": 17, "bad~2key": 19}
    artifact.write_text(json.dumps(source))
    valid = entry.model_copy(
        update={
            "metrics": {
                "first": "/agent_analysis/runs/0/attempts",
                "second": "/agent_analysis/runs/1/attempts",
                "numeric": "/pointer_probe/01",
                "negative_key": "/pointer_probe/-1",
                "slash": "/pointer_probe/a~1b",
                "tilde": "/pointer_probe/a~0b",
            }
        }
    )
    assert entry_metrics(tmp_path, valid) == {
        "first": 51,
        "second": 50,
        "numeric": 7,
        "negative_key": 11,
        "slash": 13,
        "tilde": 17,
    }
    with pytest.raises(ValueError):
        entry_metrics(
            tmp_path, entry.model_copy(update={"metrics": {"bad": "/pointer_probe/bad~2key"}})
        )


def test_reported_provenance_accepts_observed_strings_and_explicit_unknown(tmp_path):
    payload, _ = _copy_inputs(tmp_path)
    raw_entry = payload["entries"][payload["current"]["hybrid_rerank"]]
    artifact = tmp_path / raw_entry["artifact"]
    source = json.loads(artifact.read_text())
    source["reported"] = {"model": "observed-model", "provider": None}
    artifact.write_text(json.dumps(source))
    raw_entry["provenance"].update(
        {
            "reported_model": "/reported/model",
            "reported_provider": "/reported/provider",
        }
    )
    catalog = validate_catalog(tmp_path, payload)
    facts = entry_facts(tmp_path, catalog.entries[catalog.current["hybrid_rerank"]])
    assert facts["reported_model"] == "observed-model"
    assert facts["reported_provider"] is None


@pytest.mark.parametrize(
    "pointer",
    [
        "/run/cases",
        "/run/legacy_file_cutoff/symbol_cases",
        "/run/file_conditioned_within_file/cases",
        "/run/failed",
    ],
)
def test_symbol_target_denominator_rejects_unrelated_counts(tmp_path, pointer):
    payload, original = _copy_inputs(tmp_path)
    entry = payload["entries"][payload["current"]["symbol_localization"]]
    entry["metrics"]["targets"] = pointer
    with pytest.raises(ValueError, match="[Ss]ymbol"):
        publish_catalog(tmp_path, payload)
    assert (tmp_path / CATALOG_PATH).read_bytes() == original[CATALOG_PATH]


def test_symbol_targets_are_bounded_by_selected_manifest(tmp_path):
    payload, original = _copy_inputs(tmp_path)
    entry = payload["entries"][payload["current"]["symbol_localization"]]
    manifest_path = tmp_path / payload["datasets"][entry["dataset"]]["manifest"]
    manifest = json.loads(manifest_path.read_text())
    for case in manifest["cases"]:
        case["expected_symbols"] = []
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="[Ss]ymbol"):
        publish_catalog(tmp_path, payload)
    assert (tmp_path / CATALOG_PATH).read_bytes() == original[CATALOG_PATH]


def test_symbol_target_tiers_cannot_borrow_other_tiers_capacity(tmp_path):
    payload, original = _copy_inputs(tmp_path)
    entry = payload["entries"][payload["current"]["symbol_localization"]]
    manifest = json.loads(
        (tmp_path / payload["datasets"][entry["dataset"]]["manifest"]).read_text()
    )
    main_limit = sum(
        len(case.get("expected_symbols", []))
        for case in manifest["cases"]
        if case["tier"] == "main"
    )
    artifact = tmp_path / entry["artifact"]
    source = json.loads(artifact.read_text())
    delta = main_limit + 1 - source["by_tier"]["main"]["targets"]
    source["by_tier"]["main"]["targets"] += delta
    source["by_tier"]["generalization"]["targets"] -= delta
    assert sum(tier["targets"] for tier in source["by_tier"].values()) == 160
    artifact.write_text(json.dumps(source))
    with pytest.raises(ValueError, match="[Ss]ymbol"):
        publish_catalog(tmp_path, payload)
    assert (tmp_path / CATALOG_PATH).read_bytes() == original[CATALOG_PATH]


def test_symbol_file_visible_denominator_is_a_subset_not_the_whole_manifest():
    catalog = load_catalog(ROOT)
    entry = catalog.entries[catalog.current["symbol_localization"]]
    manifest = json.loads((ROOT / catalog.datasets[entry.dataset].manifest).read_text())
    eligible = sum(len(case.get("expected_symbols", [])) for case in manifest["cases"])
    metrics = entry_metrics(ROOT, entry)
    assert eligible == 177
    assert metrics["targets"] == 160 < eligible
    assert metrics["recall_at_3"] == 0.5426
    assert metrics["mrr"] == 0.5739
