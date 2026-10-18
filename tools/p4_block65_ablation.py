#!/usr/bin/env python3
"""Audit the P4-s7 fixed winner after reverting only block 0."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


EXPECTED_REPORT_SHA256 = (
    "b1ea30fe96752749e52cd32460ce14a3dd4e5739e8822d1d5c27af77eb8c9863"
)
EXPECTED_TOTAL = "4503244"
TARGET_BLOCK = 65


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _load_probe(path: Path):
    spec = importlib.util.spec_from_file_location(
        "immutable_flexible_repack_probe", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import immutable probe from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    report_path = args.report.resolve()
    output_path = args.output.resolve()
    if output_path.exists() or output_path.is_symlink():
        raise RuntimeError(f"refusing to overwrite {output_path}")
    report_sha256 = _sha256(report_path)
    if report_sha256 != EXPECTED_REPORT_SHA256:
        raise RuntimeError(
            f"unexpected source report SHA256 {report_sha256}"
        )
    report = _load_json(report_path)
    if (
        report.get("status") != "OK"
        or report.get("instance") != "P4"
        or report.get("plateau_seed") != 7
    ):
        raise RuntimeError("source report is not the frozen P4-s7 result")

    problem_path = Path(report["problem"]).resolve()
    solver_path = Path(report["solver"]).resolve()
    probe_path = solver_path.parent / "tools" / "flexible_repack.py"
    provenance = report["provenance"]
    expected_hashes = {
        problem_path: provenance["problem_sha256"],
        probe_path: provenance["tool_sha256"],
        solver_path / "myalgorithm.py": provenance["myalgorithm_sha256"],
        Path(provenance["sscore_path"]).resolve(): provenance["sscore_sha256"],
        Path(provenance["utils_path"]).resolve(): provenance["utils_sha256"],
    }
    for path, expected in expected_hashes.items():
        actual = _sha256(path)
        if actual != expected:
            raise RuntimeError(f"provenance mismatch for {path}: {actual}")

    baseline = [int(value) for value in report["baseline"]["flat"]]
    fixed_validation = report["arms"]["fixed"]["validation"]
    if not fixed_validation.get("valid"):
        raise RuntimeError("source fixed winner is not checker-valid")
    fixed = [int(value) for value in fixed_validation["flat"]]
    n = len(baseline) // 7
    changed = [
        block for block in range(n)
        if baseline[7 * block:7 * block + 7]
        != fixed[7 * block:7 * block + 7]
    ]
    if changed != [0, TARGET_BLOCK]:
        raise RuntimeError(f"unexpected fixed-winner changed blocks: {changed}")

    hybrid = list(baseline)
    lo = 7 * TARGET_BLOCK
    hybrid[lo:lo + 7] = fixed[lo:lo + 7]
    hybrid_changed = [
        block for block in range(n)
        if baseline[7 * block:7 * block + 7]
        != hybrid[7 * block:7 * block + 7]
    ]
    if hybrid_changed != [TARGET_BLOCK]:
        raise RuntimeError(f"hybrid changed blocks are {hybrid_changed}")

    probe = _load_probe(probe_path)
    problem = _load_json(problem_path)
    numbers = probe._problem_numbers(problem)
    ma, module_path, utils_path = probe._load_solver(solver_path)
    validation = probe._inspect_flat(
        ma,
        problem,
        numbers,
        hybrid,
        int(report["dimensions"]["horizon"]),
        canonicalize=False,
    )
    exact_total = validation["exact_objective"]["total"]["exact"]
    passed = bool(
        validation["valid"]
        and validation["audit"] == [0, -1]
        and validation["checker"].get("feasible")
        and exact_total == EXPECTED_TOTAL
    )
    result = {
        "schema_version": 1,
        "status": "PASS" if passed else "FAIL",
        "experiment": "p4_s7_block65_only_ablation",
        "source_report": str(report_path),
        "source_report_sha256": report_sha256,
        "source_changed_blocks": changed,
        "hybrid_changed_blocks": hybrid_changed,
        "reverted_block": 0,
        "retained_block": TARGET_BLOCK,
        "baseline_block0": baseline[:7],
        "source_fixed_block0": fixed[:7],
        "baseline_block65": baseline[lo:lo + 7],
        "hybrid_block65": hybrid[lo:lo + 7],
        "expected_total": EXPECTED_TOTAL,
        "validation": validation,
        "provenance": {
            "problem": str(problem_path),
            "problem_sha256": _sha256(problem_path),
            "probe": str(probe_path),
            "probe_sha256": _sha256(probe_path),
            "sscore": str(module_path),
            "sscore_sha256": _sha256(module_path),
            "utils": str(utils_path),
            "utils_sha256": _sha256(utils_path),
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps({
        "status": result["status"],
        "audit": validation["audit"],
        "checker_feasible": validation["checker"].get("feasible"),
        "exact_total": exact_total,
        "output": str(output_path),
    }, sort_keys=True), flush=True)
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
