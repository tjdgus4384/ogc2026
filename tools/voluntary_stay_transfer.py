#!/usr/bin/env python3
"""Fixed-iteration plateau transfer for the frozen stay constructor."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from assignment_transfer_fixed import checked_record, flat_digest  # noqa: E402
from voluntary_stay_rollout import _orders  # noqa: E402


def _run(args: argparse.Namespace) -> None:
    solver = args.solver.resolve()
    sys.path.insert(0, str(solver))
    import myalgorithm as ma

    problem_path = ROOT / "data" / "preliminary_test" / f"prob_{args.instance}.json"
    construction_path = args.constructions / f"prob_{args.instance}.json"
    problem = json.loads(problem_path.read_text())
    construction = json.loads(construction_path.read_text())
    one_manifest = json.loads(Path(construction["manifest"]).read_text())
    final = construction["final"]
    n = len(problem["blocks"])
    order = _orders(problem)[construction["order"]]
    free_targets = [-1] * n
    stay_targets = [int(value) for value in final["exit_targets"]]
    if len(stay_targets) != n:
        raise RuntimeError("frozen target vector has the wrong length")

    engine, _due, _proc = ma._fresh(problem, 4)

    def construct(targets: list[int]) -> tuple[list[float], list[int]]:
        code = int(engine.reconstruct_ordered_stays(
            [-1] * n, order, targets, False, 7))
        if code != -1:
            raise RuntimeError(f"frozen construction failed at block {code}")
        return list(engine.objective()), list(engine.get_solution())

    base_obj, base_flat = construct(free_targets)
    stay_obj, stay_flat = construct(stay_targets)
    expected_base = one_manifest["baseline"]
    if (flat_digest(base_flat) != expected_base["solution_sha256"] or
            base_obj != [float(value) for value in expected_base["components"]]):
        raise RuntimeError("free start differs from the frozen baseline")
    if (flat_digest(stay_flat) != final["flat_sha256"] or
            stay_obj != [float(value) for value in final["components"]]):
        raise RuntimeError("stay start differs from the frozen construction")

    start_obj, start_flat = ((base_obj, base_flat) if args.mode == "baseline"
                             else (stay_obj, stay_flat))
    engine.set_solution(start_flat)
    engine.build_occ(engine.horizon())
    start_check = checked_record(ma, problem, engine, n)
    expected_check = (expected_base["checker"] if args.mode == "baseline"
                      else final["checker"])
    if (not start_check["feasible"] or start_check["rescued"] or
            abs(float(start_check["objective"]) -
                float(expected_check["objective"])) > 1e-9):
        raise RuntimeError("start checker replay mismatch")

    # checked_record is allowed to exercise the rescue ladder.  Search always
    # starts from the exact frozen raw bytes, with no persistent exit lock.
    engine.set_solution(start_flat)
    engine.build_occ(engine.horizon())
    t0 = time.monotonic()
    stats = list(engine.run_alns(
        3600.0, 0.05, 0.15, 0.002, 0.9995, args.seed,
        1000, 0.75, 0.04, 0x3FF, args.iters,
    ))
    elapsed = time.monotonic() - t0
    if not stats or int(round(stats[0])) != args.iters:
        raise RuntimeError(f"iteration contract failed: {stats[:3]}")
    audit = [int(round(value)) for value in engine.audit_state()]
    if audit[0] != 0:
        raise RuntimeError(f"invalid engine state after ALNS: {audit}")
    search_obj = list(engine.objective())
    search_flat = list(engine.get_solution())
    checked = checked_record(ma, problem, engine, n)

    report = {
        "schema_version": 1,
        "instance": args.instance,
        "seed": args.seed,
        "mode": args.mode,
        "iters": args.iters,
        "solver": str(solver),
        "construction": str(construction_path.resolve()),
        "order": construction["order"],
        "actions": final["actions"] if args.mode == "stay" else [],
        "baseline_start_objective": base_obj,
        "stay_start_objective": stay_obj,
        "start_objective": start_obj,
        "start_flat_sha256": flat_digest(start_flat),
        "start_checker": start_check,
        "search_objective": search_obj,
        "search_flat_sha256": flat_digest(search_flat),
        "checker": checked,
        "audit": audit,
        "run_stats": stats,
        "elapsed": elapsed,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "instance": args.instance,
        "seed": args.seed,
        "mode": args.mode,
        "actions": len(report["actions"]),
        "start": start_obj[3],
        "search": search_obj[3],
        "gated": checked["objective"],
        "feasible": checked["feasible"],
        "iters": int(round(stats[0])),
        "elapsed": elapsed,
    }, sort_keys=True), flush=True)


def _baseline_signature(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "instance": report["instance"],
        "seed": report["seed"],
        "iters": report["iters"],
        "start_objective": report["start_objective"],
        "start_flat_sha256": report["start_flat_sha256"],
        "search_objective": report["search_objective"],
        "search_flat_sha256": report["search_flat_sha256"],
        "checker": report["checker"],
        "audit": report["audit"],
        "run_stats": report["run_stats"],
    }


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    informative = [row for row in rows if row["delta_pct"] is not None]
    deltas = [float(row["delta_pct"]) for row in informative]
    wins = sum(delta < -1e-12 for delta in deltas)
    losses = sum(delta > 1e-12 for delta in deltas)
    summary: dict[str, Any] = {
        "pairs": len(rows),
        "informative": len(informative),
        "wins": wins,
        "losses": losses,
        "ties": len(deltas) - wins - losses,
        "median_delta_pct": statistics.median(deltas) if deltas else None,
        "instances": {},
    }
    for instance in range(3, 7):
        local = [float(row["delta_pct"]) for row in informative
                 if row["instance"] == instance]
        summary["instances"][f"P{instance}"] = {
            "pairs": len(local),
            "wins": sum(delta < -1e-12 for delta in local),
            "losses": sum(delta > 1e-12 for delta in local),
            "ties": sum(abs(delta) <= 1e-12 for delta in local),
            "median_delta_pct": statistics.median(local) if local else None,
        }
    return summary


def _collect(args: argparse.Namespace) -> None:
    rows = []
    aa_mismatches = []
    for instance in range(3, 7):
        for seed in (7, 11, 23, 41):
            stem = f"p{instance}-s{seed}"
            baseline = json.loads(
                (args.directory / f"{stem}-baseline.json").read_text())
            stay = json.loads(
                (args.directory / f"{stem}-stay.json").read_text())
            reference = json.loads(
                (args.reference / f"{stem}-baseline.json").read_text())
            identity = (instance, seed, args.iters)
            if any((row["instance"], row["seed"], row["iters"]) != identity
                   for row in (baseline, stay, reference)):
                raise RuntimeError(f"paired identity mismatch at {stem}")
            if _baseline_signature(baseline) != _baseline_signature(reference):
                aa_mismatches.append(stem)
            bcheck = baseline["checker"]
            scheck = stay["checker"]
            bobj = float(bcheck["objective"]) if bcheck["feasible"] else None
            sobj = float(scheck["objective"]) if scheck["feasible"] else None
            rows.append({
                "instance": instance,
                "seed": seed,
                "baseline_feasible": bool(bcheck["feasible"]),
                "stay_feasible": bool(scheck["feasible"]),
                "baseline": bobj,
                "stay": sobj,
                "delta_pct": (100.0 * (sobj - bobj) / bobj
                              if bobj is not None and sobj is not None else None),
            })

    all_feasible = all(row["baseline_feasible"] and row["stay_feasible"]
                       for row in rows)
    summary = _summarize(rows)
    board_medians = [value["median_delta_pct"]
                     for value in summary["instances"].values()]
    target_edge = any(
        summary["instances"][key]["median_delta_pct"] is not None and
        summary["instances"][key]["median_delta_pct"] <= -3.0
        for key in ("P4", "P5"))
    passed = bool(
        not aa_mismatches and all_feasible and summary["informative"] == 16 and
        summary["median_delta_pct"] is not None and
        summary["median_delta_pct"] <= -2.0 and
        summary["wins"] > summary["losses"] and target_edge and
        all(value is not None and value <= 2.0 for value in board_medians))
    output = {
        "schema_version": 1,
        "reference": str(args.reference.resolve()),
        "rows": rows,
        "baseline_aa_exact": not aa_mismatches,
        "baseline_aa_mismatches": aa_mismatches,
        "all_32_checker_feasible": all_feasible,
        "stay": summary,
        "verdict": ("advance_native_stay_selector" if passed
                    else "reject_stay_plateau_transfer"),
    }
    (args.directory / "summary.json").write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(json.dumps(output, sort_keys=True), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("solver", type=Path)
    run.add_argument("constructions", type=Path)
    run.add_argument("instance", type=int, choices=range(3, 7))
    run.add_argument("seed", type=int)
    run.add_argument("iters", type=int)
    run.add_argument("mode", choices=("baseline", "stay"))
    run.add_argument("output", type=Path)
    collect = sub.add_parser("collect")
    collect.add_argument("directory", type=Path)
    collect.add_argument("reference", type=Path)
    collect.add_argument("iters", type=int)
    args = parser.parse_args()
    if args.command == "run":
        _run(args)
    else:
        _collect(args)


if __name__ == "__main__":
    main()
