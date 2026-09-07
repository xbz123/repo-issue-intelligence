import pytest
from test_agent_store_v2 import create_run, new_store

from repo_issue_intelligence.agent_store_v2 import StoreConflict
from repo_issue_intelligence.models import InvestigationReport, ReproductionPlan
from repo_issue_intelligence.protocol_v2_models import (
    EvidenceCollectionContext,
    EvidenceItemV2,
)


def save_report(store, run_id="run", issue_number=1):
    run = store.get_run(run_id)
    issue = next(issue for issue in run.inputs.issues if issue.number == issue_number)
    report = InvestigationReport(
        issue=issue.to_issue(),
        confirmed_facts=["Observed failure"],
        candidates=[],
        hypotheses=[],
        reproduction_plan=ReproductionPlan(
            runtime="python",
            setup_commands=[],
            reproduction_steps=[],
            safety_constraints=[],
            open_questions=[],
        ),
        repository_root=run.snapshot.analysis_root,
    )
    store.save_deterministic_result(run_id, issue_number, report)
    return report


def evidence_item(**overrides):
    fields = dict(
        evidence_id="E1",
        ordinal=0,
        candidate_rank=2,
        selection_kind="candidate",
        file="src/worker.py",
        symbol="work",
        requested_range=(10, 14),
        actual_range=(10, 11),
        truncation_reason="character_budget",
        char_count=8,
        content="one\ntwo\n",
        collector_protocol="collector-v2",
    )
    return EvidenceItemV2(**(fields | overrides))


def seal(store, *, run_id="run", issue_number=1, items=None, evidence_set_id=None):
    return store.seal_evidence_set(
        run_id,
        issue_number,
        [evidence_item()] if items is None else items,
        EvidenceCollectionContext(snapshot_commit=None, collector_protocol="collector-v2"),
        evidence_set_id=evidence_set_id,
    )


def test_sealed_content_and_metadata_are_exact_provider_input(tmp_path):
    store = new_store(tmp_path / "private")
    create_run(store)
    report = save_report(store)
    sealed = seal(store)
    assert store.read_evidence("run", 1) == sealed
    assert sealed.items[0].requested_range == (10, 14)
    assert sealed.items[0].actual_range == (10, 11)
    assert sealed.items[0].candidate_rank == 2
    assert sealed.items[0].char_count == 8
    assert store.get_issue("run", 1).evidence_set_id == sealed.evidence_set_id

    def fake_provider(input_ids, lookup):
        return [lookup[evidence_id].model_dump() for evidence_id in input_ids]

    ids, lookup = store.evidence_lookup("run", 1)
    assert fake_provider(ids, lookup) == [
        {
            "id": "E1",
            "file": "src/worker.py",
            "symbol": "work",
            "lines": "10-11",
            "content": "one\ntwo\n",
        }
    ]
    with pytest.raises(TypeError):
        lookup["E2"] = lookup["E1"]
    with pytest.raises(ValueError):
        lookup["E1"].content = "changed"
    with pytest.raises(StoreConflict):
        store.save_deterministic_result("run", 1, report)
    with pytest.raises(StoreConflict):
        seal(store)
    assert store.read_evidence("run", 1) == sealed


def test_partial_evidence_insert_rolls_back_and_retries_same_identity(tmp_path):
    store = new_store(tmp_path / "private")
    create_run(store)
    save_report(store)
    with pytest.raises(StoreConflict):
        seal(store, items=[evidence_item(), evidence_item(ordinal=1)], evidence_set_id="atomic")
    assert store.read_evidence("run", 1) is None
    assert store.get_issue("run", 1).evidence_set_id is None
    sealed = seal(
        store,
        items=[evidence_item(evidence_id="E2", ordinal=1), evidence_item()],
        evidence_set_id="atomic",
    )
    assert [item.evidence_id for item in sealed.items] == ["E1", "E2"]
    assert store.evidence_lookup("run", 1)[0] == ("E1", "E2")


@pytest.mark.parametrize(
    "overrides",
    [
        {"char_count": 100},
        {"actual_range": (10, 14)},
        {"requested_range": (12, 14)},
        {"truncation_reason": None},
        {"file": "../secret"},
        {"file": "/absolute/source"},
    ],
)
def test_evidence_metadata_cannot_claim_absent_characters_or_lines(overrides):
    with pytest.raises(ValueError):
        evidence_item(**overrides)
