"""Offline, isolated G1 candidate/symbol/metric equivalence over a frozen manifest."""

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

from repo_issue_intelligence.benchmark import BenchmarkVariant, evaluate_case, load_manifest
from repo_issue_intelligence.repository_context import capture_repository_context
from repo_issue_intelligence.repository_view import prepare_repository_view


def git(*arguments):
    return subprocess.run(["git", *arguments], check=True, capture_output=True, timeout=120)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case-id", action="append")
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    cases = [case for case in manifest.cases if not args.case_id or case.id in args.case_id]
    if not cases or (args.case_id and {c.id for c in cases} != set(args.case_id)):
        parser.error("unknown or empty case selection")
    # Never hydrate missing objects, use global filters/hooks, or change cached checkouts.
    os.environ.update(
        GIT_NO_LAZY_FETCH="1",
        GIT_OPTIONAL_LOCKS="0",
        GIT_TERMINAL_PROMPT="0",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
    )
    with args.output.open("x", encoding="utf-8") as report:
        os.fchmod(report.fileno(), 0o600)
        results = []
        for case in cases:
            outcome = {
                "case_id": case.id,
                "repository": case.repository,
                "revision": case.pre_fix_sha,
            }
            try:
                cached = args.cache / case.repository.replace("/", "--")
                git("-C", str(cached), "cat-file", "-e", case.pre_fix_sha + "^{commit}")
                with tempfile.TemporaryDirectory(prefix="rii-g1-rank-") as directory:
                    root = Path(directory) / "repo"
                    git(
                        "clone",
                        "--shared",
                        "--no-checkout",
                        "--no-hardlinks",
                        str(cached),
                        str(root),
                    )
                    git(
                        "-C",
                        str(root),
                        "remote",
                        "set-url",
                        "origin",
                        f"https://github.com/{case.repository}.git",
                    )
                    git(
                        "-C",
                        str(root),
                        "-c",
                        f"core.hooksPath={os.devnull}",
                        "-c",
                        "core.autocrlf=false",
                        "checkout",
                        "--detach",
                        case.pre_fix_sha,
                    )
                    snapshot = capture_repository_context(root)
                    with prepare_repository_view(snapshot) as view:
                        fields = {
                            "candidate_files",
                            "candidate_symbols",
                            "file_recall_at_1",
                            "file_recall_at_5",
                            "file_recall_at_10",
                            "file_recall_at_20",
                            "reciprocal_rank",
                            "symbol_recall_at_1",
                            "symbol_recall_at_5",
                            "symbol_recall_at_10",
                            "symbol_recall_at_20",
                            "symbol_reciprocal_rank",
                            "candidate_pool_recall",
                            "file_conditioned_symbol_targets",
                            "file_conditioned_symbol_recall_at_1",
                            "file_conditioned_symbol_recall_at_3",
                            "within_file_symbol_reciprocal_rank",
                            "expected_file_found_but_symbol_missing",
                            "matched_files_at_5",
                            "matched_files_at_10",
                            "matched_files_at_20",
                        }
                        old = evaluate_case(
                            case, case.issue_snapshot, root, BenchmarkVariant.DETERMINISTIC
                        )
                        new = evaluate_case(
                            case, case.issue_snapshot, view, BenchmarkVariant.DETERMINISTIC
                        )
                        assert fields <= type(old).model_fields.keys()
                        outcome.update(
                            status="equal"
                            if old.model_dump(include=fields) == new.model_dump(include=fields)
                            else "mismatch",
                            candidates=len(old.candidate_files),
                            checkout_metrics=old.model_dump(include=fields),
                            view_metrics=new.model_dump(include=fields),
                        )
            except Exception as error:
                # Do not publish local paths or repository/provider stderr in acceptance reports.
                outcome.update(
                    status="unverified",
                    error_type=type(error).__name__,
                    error_code=getattr(error, "code", None),
                )
            results.append(outcome)
            print(f"{len(results)}/{len(cases)} {case.id}: {outcome['status']}", flush=True)
            # Checkpoint the newly-created report only; never change historical metric artifacts.
            report.seek(0)
            json.dump(
                {
                    "manifest_version": manifest.version,
                    "cases": results,
                    "contract": (
                        "G1.4 candidate order, selected/alternate symbols and metrics; "
                        "not diagnostic prose"
                    ),
                    "complete": len(results) == len(cases),
                },
                report,
                indent=2,
            )
            report.truncate()
            report.flush()
            os.fsync(report.fileno())
    return 0 if all(item["status"] == "equal" for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
