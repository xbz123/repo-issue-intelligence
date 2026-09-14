"""Source-backed current-result pointers, independent of execution and V2 storage."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import stat
import tempfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CATALOG_PATH = "benchmarks/results/current-results.json"
START = "<!-- current-results:start -->"
END = "<!-- current-results:end -->"
PROVENANCE = {
    "manifest_version",
    "index_version",
    "retrieval_protocol",
    "requested_model",
    "requested_provider",
    "reported_model",
    "reported_provider",
}
PROVENANCE_TYPES = {
    "manifest_version": (int,),
    "index_version": (int,),
    "retrieval_protocol": (str, dict),
    "requested_model": (str,),
    "requested_provider": (str,),
    "reported_model": (str,),
    "reported_provider": (str,),
    "requested_reasoning_effort": (str,),
    "requested_service_tier": (str,),
    "requested_parameters": (dict,),
    "source_commit": (str,),
    "evaluation_protocol": (str,),
    "metric_protocol": (str,),
}
DISPLAY = {
    "file_localization": ("cases", "file_recall_at_20", "mrr"),
    "symbol_localization": ("targets", "recall_at_3", "mrr"),
    "candidate_pool": ("matched", "targets", "misses"),
    "hybrid_rerank": ("cases", "valid", "file_recall_at_20"),
    "agent_analysis": ("case_runs", "valid", "failures"),
    "hypothesis_grounding": ("cases", "hits", "hit_rate"),
}


class CatalogModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Dataset(CatalogModel):
    manifest: str
    version: int = Field(ge=1, strict=True)
    case_count: int = Field(ge=1, strict=True)
    role: Literal["regression/development", "historical-regression"]
    historical_tiers: dict[str, int]


class Entry(CatalogModel):
    evaluation_type: str
    dataset: str
    artifact: str
    completeness: Literal["full", "summary-only"]
    details: str | None
    supersedes: tuple[str, ...] = ()
    provenance: dict[str, str | None]
    metrics: dict[str, str]
    symbol_targets_by_tier: dict[str, str] | None = None
    notes: str


class ResultCatalog(CatalogModel):
    schema_version: Literal[1]
    datasets: dict[str, Dataset]
    entries: dict[str, Entry]
    current: dict[str, str]


def _path(root: Path, relative: str) -> Path:
    path = PurePosixPath(relative)
    if (
        path.is_absolute()
        or not path.parts
        or ".." in path.parts
        or "\\" in relative
        or str(path) != relative
    ):
        raise ValueError("Catalog paths must be canonical repository-relative paths")
    target = root.resolve()
    for part in path.parts:
        target = target / part
        if target.is_symlink():
            raise ValueError("Catalog paths must not traverse symlinks")
    return target


def _json(root: Path, relative: str):
    with _path(root, relative).open(encoding="utf-8") as stream:
        return json.load(stream)


def _pointer(value, pointer: str | None):
    if pointer is None:
        return None
    if not pointer.startswith("/"):
        raise ValueError("Facts require explicit JSON pointers")
    try:
        for token in pointer[1:].split("/"):
            if re.search(r"~(?![01])", token):
                raise ValueError("Invalid JSON Pointer escape")
            token = token.replace("~1", "/").replace("~0", "~")
            if isinstance(value, list):
                if re.fullmatch(r"0|[1-9][0-9]*", token) is None:
                    raise ValueError("Invalid JSON Pointer array index")
                value = value[int(token)]
            else:
                value = value[token]
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise ValueError(f"Unavailable source fact: {pointer}") from error
    return value


def entry_facts(root: Path, entry: Entry) -> dict:
    source = _json(root, entry.artifact)
    return {name: _pointer(source, pointer) for name, pointer in entry.provenance.items()}


def entry_metrics(root: Path, entry: Entry) -> dict:
    source = _json(root, entry.artifact)
    values = {name: _pointer(source, pointer) for name, pointer in entry.metrics.items()}
    if any(
        type(value) not in (int, float) or not math.isfinite(value) for value in values.values()
    ):
        raise ValueError("Metrics must reference finite source numbers")
    return values


def _validate_provenance(facts: dict) -> None:
    if not PROVENANCE <= facts.keys():
        raise ValueError("Provenance must distinguish requested, reported and unknown facts")
    for name, value in facts.items():
        expected = PROVENANCE_TYPES.get(name)
        if expected is None:
            raise ValueError(f"Unsupported provenance field: {name}")
        if value is None and name != "manifest_version":
            continue
        if type(value) not in expected:
            raise ValueError(f"Invalid provenance value type: {name}")
        if isinstance(value, str) and not value.strip():
            raise ValueError(f"Empty provenance identifier: {name}")
        if type(value) is int and value < 1:
            raise ValueError(f"Invalid provenance version: {name}")
        if name == "retrieval_protocol" and value == {}:
            raise ValueError("Empty retrieval protocol")


def validate_catalog(root: Path, payload: dict) -> ResultCatalog:
    catalog = ResultCatalog.model_validate(payload)
    if set(catalog.current) != set(DISPLAY):
        raise ValueError("Each evaluation type needs one current result")
    symbol_limits = {}
    for dataset_id, dataset in catalog.datasets.items():
        manifest = _json(root, dataset.manifest)
        if (
            manifest["version"] != dataset.version
            or len(manifest["cases"]) != dataset.case_count
            or dict(Counter(case["tier"] for case in manifest["cases"])) != dataset.historical_tiers
        ):
            raise ValueError("Dataset metadata must match the frozen manifest")
        if dataset.version == 20 and dataset.role != "regression/development":
            raise ValueError("Manifest v20 is regression/development, not independent holdout")
        limits = Counter()
        for case in manifest["cases"]:
            limits[case["tier"]] += len(case.get("expected_symbols", []))
        symbol_limits[dataset_id] = limits
    superseded = set()
    for identifier, entry in catalog.entries.items():
        if entry.evaluation_type not in DISPLAY or entry.dataset not in catalog.datasets:
            raise ValueError("Unknown evaluation type or dataset")
        if entry.artifact == CATALOG_PATH or entry.details == CATALOG_PATH:
            raise ValueError("The catalog is not a result artifact")
        facts = entry_facts(root, entry)
        _validate_provenance(facts)
        for name in ("reported_model", "reported_provider"):
            pointer = entry.provenance[name]
            if pointer is not None and not any("reported" in part for part in pointer.split("/")):
                raise ValueError("Requested configuration is not a reported observation")
        if facts["manifest_version"] != catalog.datasets[entry.dataset].version:
            raise ValueError("Result and dataset manifest versions differ")
        metrics = entry_metrics(root, entry)
        if not set(DISPLAY[entry.evaluation_type]) <= metrics.keys():
            raise ValueError("Missing summary metric references")
        for name, value in metrics.items():
            if name in {
                "cases",
                "case_runs",
                "valid",
                "failures",
                "matched",
                "targets",
                "misses",
                "hits",
            }:
                if type(value) is not int or value < 0:
                    raise ValueError("Counts must reference nonnegative source integers")
            elif ("recall" in name or "rate" in name or name == "mrr") and not 0 <= value <= 1:
                raise ValueError("Rates must reference source values in [0, 1]")
        if entry.evaluation_type == "candidate_pool" and (
            metrics["matched"] + metrics["misses"] != metrics["targets"]
        ):
            raise ValueError("Candidate pool counts must balance")
        if entry.evaluation_type == "symbol_localization":
            limits = symbol_limits[entry.dataset]
            if metrics["targets"] > sum(limits.values()):
                raise ValueError("Symbol targets exceed the frozen dataset's expected symbols")
            if entry.symbol_targets_by_tier is None or set(entry.symbol_targets_by_tier) != set(
                limits
            ):
                raise ValueError("Symbol targets require source counts for every dataset tier")
            source = _json(root, entry.artifact)
            counts = {
                tier: _pointer(source, pointer)
                for tier, pointer in entry.symbol_targets_by_tier.items()
            }
            if any(
                type(count) is not int or not 0 <= count <= limits[tier]
                for tier, count in counts.items()
            ):
                raise ValueError("Symbol tier targets exceed their eligible expected symbols")
            if sum(counts.values()) != metrics["targets"]:
                raise ValueError("Symbol target denominator must equal source tier totals")
        if (
            "cases" in metrics
            and not 1 <= metrics["cases"] <= catalog.datasets[entry.dataset].case_count
        ):
            raise ValueError("Case counts must fit the declared dataset")
        if entry.evaluation_type == "hybrid_rerank" and metrics["valid"] > metrics["cases"]:
            raise ValueError("Valid ranks cannot exceed cases")
        if entry.evaluation_type == "agent_analysis" and (
            metrics["case_runs"] < 1
            or metrics["case_runs"] % catalog.datasets[entry.dataset].case_count
            or metrics["valid"] + metrics["failures"] != metrics["case_runs"]
        ):
            raise ValueError("Analysis outcomes must partition complete dataset runs")
        if entry.evaluation_type == "hypothesis_grounding" and (
            metrics["hits"] > metrics["cases"]
            or not math.isclose(
                metrics["hit_rate"], metrics["hits"] / metrics["cases"], abs_tol=0.00005
            )
        ):
            raise ValueError("Grounding hit count and rounded hit rate must agree")
        if entry.completeness == "summary-only" and entry.details is not None:
            raise ValueError("Summary-only results must not borrow historical details")
        if entry.completeness == "full":
            if entry.details != entry.artifact:
                raise ValueError("Full results require their own retained details")
            source = _json(root, entry.details)
            if (
                entry.evaluation_type == "candidate_pool"
                and len(source["targets"]) != metrics["misses"]
            ):
                raise ValueError("Full audit must retain every missing target")
        for prior in entry.supersedes:
            previous = catalog.entries.get(prior)
            if (
                prior == identifier
                or previous is None
                or (previous.evaluation_type, previous.dataset)
                != (entry.evaluation_type, entry.dataset)
            ):
                raise ValueError("Supersession must reference the same evaluation and dataset")
            superseded.add(prior)

    def visit(identifier: str, chain: set[str]) -> None:
        if identifier in chain:
            raise ValueError("Supersession must be acyclic")
        for previous in catalog.entries[identifier].supersedes:
            visit(previous, chain | {identifier})

    for identifier in catalog.entries:
        visit(identifier, set())
    for kind, identifier in catalog.current.items():
        if (
            identifier not in catalog.entries
            or identifier in superseded
            or catalog.entries[identifier].evaluation_type != kind
        ):
            raise ValueError("Current pointers must reference unsuperseded matching results")
    return catalog


def load_catalog(root: Path) -> ResultCatalog:
    return validate_catalog(root, _json(root, CATALOG_PATH))


def render_summary(root: Path, catalog: ResultCatalog, *, prefix: str = "") -> str:
    lines = [
        START,
        "",
        "| Evaluation | Source | Dataset role | Completeness | Source metrics |",
        "|---|---|---|---|---|",
    ]
    for kind, identifier in catalog.current.items():
        entry = catalog.entries[identifier]
        metrics = entry_metrics(root, entry)
        text = "; ".join(f"{name}={metrics[name]}" for name in DISPLAY[kind])
        lines.append(
            f"| {kind} | [{identifier}]({prefix}{entry.artifact}) | "
            f"{catalog.datasets[entry.dataset].role} | {entry.completeness} | {text} |"
        )
    return "\n".join([*lines, "", END])


def publish_catalog(root: Path, payload: dict) -> None:
    """Validate already-published artifacts, then atomically replace only the catalog."""
    catalog = validate_catalog(root, payload)
    destination = _path(root, CATALOG_PATH)
    try:
        mode = stat.S_IMODE(destination.stat().st_mode)
    except FileNotFoundError:
        mode = 0o644
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=".current-results-",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(catalog.model_dump_json(indent=2) + "\n")
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--publish", type=Path, help="Validate candidate JSON and replace only current-results.json"
    )
    args = parser.parse_args()
    if args.publish is not None:
        publish_catalog(args.root, json.loads(args.publish.read_text(encoding="utf-8")))
    print(render_summary(args.root, load_catalog(args.root)))


if __name__ == "__main__":
    main()
