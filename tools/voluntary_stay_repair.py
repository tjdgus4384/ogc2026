#!/usr/bin/env python3
"""Paired short kill test for voluntary stays inside ALNS repair."""
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

    problem_path = (ROOT / "data" / "preliminary_test" /
                    f"prob_{args.instance}.json")
    manifest_path = args.manifests / f"prob_{args.instance}.json"
    problem = json.loads(problem_path.read_text())
    manifest = json.loads(manifest_path.read_text())
    n = len(problem["blocks"])
    order = _orders(problem)[manifest["order"]]
    engine, _due, _proc = ma._fresh(problem, 4)
    if int(engine.reconstruct_ordered([-1] * n, order, False, 7)) != -1:
        raise RuntimeError("frozen free construction failed")
    start_objective = list(engine.objective())
    start_flat = list(engine.get_solution())
    expected = manifest["baseline"]
    if (flat_digest(start_flat) != expected["solution_sha256"] or
            start_objective != [float(value)
                                for value in expected["components"]]):
        raise RuntimeError("frozen free construction did not replay")

    start_check = checked_record(ma, problem, engine, n)
    expected_check = expected["checker"]
    if (not start_check["feasible"] or
            bool(start_check["rescued"]) !=
                bool(expected_check.get("rescued", False)) or
            start_check.get("solution_sha256") !=
                expected_check.get("solution_sha256") or
            abs(float(start_check["objective"]) -
                float(expected_check["objective"])) > 1e-9):
        raise RuntimeError(
            "start checker mismatch: "
            f"actual={start_check!r} expected={expected_check!r}")

    engine.set_solution(start_flat)
    engine.build_occ(engine.horizon())
    t0 = time.monotonic()
    common = (3600.0, 0.05, 0.15, 0.002, 0.9995, args.seed,
              100, 0.75, 0.04, 0x3FF, args.iters)
    if args.mode == "reference":
        stats = list(engine.run_alns(*common))
    else:
        stats = list(engine.run_alns(
            *common, args.mode == "stay"))
    elapsed = time.monotonic() - t0
    if not stats or int(round(stats[0])) != args.iters:
        raise RuntimeError(f"iteration contract failed: {stats[:3]}")
    audit = [int(round(value)) for value in engine.audit_state()]
    if audit[0] != 0:
        raise RuntimeError(f"invalid engine state after ALNS: {audit}")
    search_objective = list(engine.objective())
    search_flat = list(engine.get_solution())
    checked = checked_record(ma, problem, engine, n)
    telemetry = None
    if args.mode != "reference":
        if len(stats) < 21:
            raise RuntimeError("candidate build lacks stay telemetry")
        telemetry = {
            "neighbourhoods": int(round(stats[15])),
            "tried": int(round(stats[16])),
            "built": int(round(stats[17])),
            "selected": int(round(stats[18])),
            "rescued": int(round(stats[19])),
            "gain": float(stats[20]),
        }

    report = {
        "schema_version": 1,
        "instance": args.instance,
        "seed": args.seed,
        "mode": args.mode,
        "iters": args.iters,
        "solver": str(solver),
        "manifest": str(manifest_path.resolve()),
        "order": manifest["order"],
        "start_objective": start_objective,
        "start_flat_sha256": flat_digest(start_flat),
        "start_checker": start_check,
        "search_objective": search_objective,
        "search_flat_sha256": flat_digest(search_flat),
        "checker": checked,
        "audit": audit,
        "run_stats": stats,
        "telemetry": telemetry,
        "elapsed": elapsed,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "instance": args.instance,
        "seed": args.seed,
        "mode": args.mode,
        "start": start_objective[3],
        "search": search_objective[3],
        "gated": checked["objective"],
        "feasible": checked["feasible"],
        "telemetry": telemetry,
        "elapsed": elapsed,
    }, sort_keys=True), flush=True)


def _signature(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "instance": report["instance"],
        "seed": report["seed"],
        "iters": report["iters"],
        "start_objective": report["start_objective"],
        "start_flat_sha256": report["start_flat_sha256"],
        "start_checker": report["start_checker"],
        "search_objective": report["search_objective"],
        "search_flat_sha256": report["search_flat_sha256"],
        "checker": report["checker"],
        "audit": report["audit"],
        "run_stats": report["run_stats"][:15],
    }


def _metrics(deltas: list[float]) -> dict[str, Any]:
    wins = sum(value < -1e-12 for value in deltas)
    losses = sum(value > 1e-12 for value in deltas)
    return {
        "pairs": len(deltas),
        "wins": wins,
        "losses": losses,
        "ties": len(deltas) - wins - losses,
        "median_delta_pct": statistics.median(deltas) if deltas else None,
        "mean_delta_pct": statistics.mean(deltas) if deltas else None,
        "worst_delta_pct": max(deltas) if deltas else None,
    }


def _collect(args: argparse.Namespace) -> None:
    rows = []
    aa_mismatches = []
    semantic_null_coverage = []
    semantic_null_mismatches = []
    all_feasible = True
    by_board: dict[int, list[float]] = {instance: []
                                       for instance in range(3, 7)}
    telemetry: dict[int, dict[str, float]] = {
        instance: {key: 0.0 for key in
                   ("neighbourhoods", "tried", "built", "selected",
                    "rescued", "gain")}
        for instance in range(3, 7)}
    slowdowns = []
    for instance in range(3, 7):
        for seed in (7, 11, 23, 41):
            stem = f"p{instance}-s{seed}"
            reference = json.loads(
                (args.directory / f"{stem}-reference.json").read_text())
            control = json.loads(
                (args.directory / f"{stem}-control.json").read_text())
            stay = json.loads(
                (args.directory / f"{stem}-stay.json").read_text())
            if _signature(reference) != _signature(control):
                aa_mismatches.append(stem)
            stay_telemetry = stay["telemetry"] or {}
            if (int(stay_telemetry.get("tried", 0)) > 0 and
                    int(stay_telemetry.get("selected", 0)) == 0):
                semantic_null_coverage.append(stem)
                if _signature(stay) != _signature(control):
                    semantic_null_mismatches.append(stem)
            all_feasible = all_feasible and all(
                bool(report["checker"]["feasible"])
                for report in (reference, control, stay))
            cobj = (float(control["checker"]["objective"])
                    if control["checker"]["feasible"] else None)
            sobj = (float(stay["checker"]["objective"])
                    if stay["checker"]["feasible"] else None)
            delta = (100.0 * (sobj - cobj) / cobj
                     if cobj is not None and sobj is not None else None)
            if delta is not None:
                by_board[instance].append(delta)
            for key, value in stay_telemetry.items():
                telemetry[instance][key] += float(value)
            slowdown = (float(stay["elapsed"]) /
                        max(1e-9, float(control["elapsed"])))
            slowdowns.append(slowdown)
            rows.append({
                "instance": instance,
                "seed": seed,
                "control_feasible": bool(control["checker"]["feasible"]),
                "stay_feasible": bool(stay["checker"]["feasible"]),
                "control": cobj,
                "stay": sobj,
                "delta_pct": delta,
                "slowdown": slowdown,
                "telemetry": stay["telemetry"],
            })

    deltas = [float(row["delta_pct"]) for row in rows
              if row["delta_pct"] is not None]
    overall = _metrics(deltas)
    boards = {f"P{instance}": _metrics(by_board[instance])
              for instance in range(3, 7)}
    p45 = _metrics(by_board[4] + by_board[5])
    telemetry_out = {f"P{instance}": telemetry[instance]
                     for instance in range(3, 7)}
    selected_habitat = all(telemetry[instance]["selected"] > 0
                           for instance in (4, 5, 6))
    board_veto = all(value["median_delta_pct"] is not None and
                     value["median_delta_pct"] <= 2.0
                     for value in boards.values())
    passed = bool(
        not aa_mismatches and not semantic_null_mismatches and
        all_feasible and len(deltas) == 16 and
        selected_habitat and overall["wins"] > overall["losses"] and
        overall["median_delta_pct"] is not None and
        overall["median_delta_pct"] <= -1.0 and
        p45["median_delta_pct"] is not None and
        p45["median_delta_pct"] <= -1.0 and board_veto)
    output = {
        "schema_version": 1,
        "rows": rows,
        "default_off_aa_exact": not aa_mismatches,
        "aa_mismatches": aa_mismatches,
        "semantic_null_coverage": semantic_null_coverage,
        "semantic_null_mismatches": semantic_null_mismatches,
        "all_48_checker_feasible": all_feasible,
        "overall": overall,
        "boards": boards,
        "p45": p45,
        "telemetry": telemetry_out,
        "median_slowdown": statistics.median(slowdowns),
        "verdict": ("advance_sparse_stay_repair"
                    if passed else "reject_exhaustive_stay_repair"),
    }
    (args.directory / "summary.json").write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(json.dumps(output, sort_keys=True), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("solver", type=Path)
    run.add_argument("manifests", type=Path)
    run.add_argument("instance", type=int, choices=range(3, 7))
    run.add_argument("seed", type=int)
    run.add_argument("iters", type=int)
    run.add_argument("mode", choices=("reference", "control", "stay"))
    run.add_argument("output", type=Path)
    collect = sub.add_parser("collect")
    collect.add_argument("directory", type=Path)
    args = parser.parse_args()
    if args.command == "run":
        _run(args)
    else:
        _collect(args)


if __name__ == "__main__":
    main()
