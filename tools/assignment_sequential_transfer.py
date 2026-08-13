#!/usr/bin/env python3
"""Fixed-iteration plateau test for the frozen sequential constructor."""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
import time


REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
from assignment_transfer_fixed import (  # noqa: E402
    checked_record,
    flat_digest,
    seed_orders,
)


def run(args) -> None:
    solver = args.solver.resolve()
    sys.path.insert(0, str(solver))
    import myalgorithm as ma

    problem_path = REPO / "data" / "preliminary_test" / f"prob_{args.instance}.json"
    construction_path = args.constructions / f"prob_{args.instance}.json"
    prob = json.loads(problem_path.read_text())
    construction = json.loads(construction_path.read_text())
    final = construction["final"]
    n = len(prob["blocks"])
    actions = final["actions"]
    if len(actions) < 2:
        raise RuntimeError("sequential report has no additional action")

    locks = [-1] * n
    for action in actions:
        block = int(action["block"])
        bay = int(action["bay"])
        if not 0 <= block < n or not 0 <= bay < len(prob["bays"]):
            raise RuntimeError(f"invalid frozen action: {action}")
        if locks[block] >= 0 and locks[block] != bay:
            raise RuntimeError(f"conflicting frozen action: {action}")
        locks[block] = bay

    orders = seed_orders(prob)
    order_name = construction["order"]
    if order_name not in orders:
        raise RuntimeError(f"unknown frozen order: {order_name}")
    eng, _due, _proc = ma._fresh(prob, 4)
    code = int(eng.reconstruct_ordered(locks, orders[order_name], False, 7))
    if code != -1:
        raise RuntimeError(f"frozen sequential construction failed at block {code}")
    start_components = list(eng.objective())
    start_flat = list(eng.get_solution())
    if (flat_digest(start_flat) != final["flat_sha256"] or
            start_components != final["components"]):
        raise RuntimeError("frozen sequential construction did not replay exactly")

    start_check = checked_record(ma, prob, eng, n)
    expected_check = final["checker"]
    if (not start_check["feasible"] or
            abs(float(start_check["objective"]) -
                float(expected_check["objective"])) > 1e-9):
        raise RuntimeError("frozen sequential checker result did not replay")

    # checked_record may exercise the rescue ladder.  The plateau treatment is
    # the recorded raw construction, with every causal lock released.
    eng.set_solution(start_flat)
    eng.build_occ(eng.horizon())
    eng.set_assignment_lock([])
    t0 = time.monotonic()
    stats = list(eng.run_alns(
        3600.0, 0.05, 0.15, 0.002, 0.9995, args.seed,
        1000, 0.75, 0.04, 0x3FF, args.iters,
    ))
    elapsed = time.monotonic() - t0
    if not stats or int(round(stats[0])) != args.iters:
        raise RuntimeError(f"iteration contract failed: {stats[:3]}")
    audit = [int(round(v)) for v in eng.audit_state()]
    if audit[0] != 0:
        raise RuntimeError(f"invalid engine state after ALNS: {audit}")
    search_components = list(eng.objective())
    search_flat = list(eng.get_solution())
    checked = checked_record(ma, prob, eng, n)

    report = {
        "schema_version": 1,
        "instance": args.instance,
        "seed": args.seed,
        "iters": args.iters,
        "solver": str(solver),
        "construction": str(construction_path.resolve()),
        "order": order_name,
        "actions": actions,
        "start_components": start_components,
        "start_flat_sha256": flat_digest(start_flat),
        "start_checker": start_check,
        "search_components": search_components,
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
        "actions": len(actions),
        "start": start_components[3],
        "search": search_components[3],
        "gated": checked["objective"],
        "feasible": checked["feasible"],
        "iters": int(round(stats[0])),
        "elapsed": elapsed,
    }, sort_keys=True), flush=True)


def summarize(rows: list[dict], key: str) -> dict:
    informative = [row for row in rows if row[key] is not None]
    deltas = [float(row[key]) for row in informative]
    wins = sum(delta < -1e-12 for delta in deltas)
    losses = sum(delta > 1e-12 for delta in deltas)
    result = {
        "informative": len(informative),
        "wins": wins,
        "losses": losses,
        "ties": len(deltas) - wins - losses,
        "median_delta_pct": statistics.median(deltas) if deltas else None,
        "instances": {},
    }
    for instance in (3, 4, 5, 6):
        local = [float(row[key]) for row in informative
                 if row["instance"] == instance]
        result["instances"][f"P{instance}"] = {
            "pairs": len(local),
            "wins": sum(delta < -1e-12 for delta in local),
            "losses": sum(delta > 1e-12 for delta in local),
            "ties": sum(abs(delta) <= 1e-12 for delta in local),
            "median_delta_pct": statistics.median(local) if local else None,
        }
    return result


def collect(args) -> None:
    rows = []
    for instance in (3, 4, 5, 6):
        for seed in (7, 11, 23, 41):
            candidate = json.loads(
                (args.directory / f"p{instance}-s{seed}-sequential.json").read_text())
            baseline = json.loads(
                (args.reference / f"p{instance}-s{seed}-baseline.json").read_text())
            one_step = json.loads(
                (args.reference / f"p{instance}-s{seed}-start.json").read_text())
            ccheck = candidate["checker"]
            bcheck = baseline["checker"]
            ocheck = one_step["checker"]
            cobj = (float(ccheck["objective"])
                    if ccheck.get("feasible") else None)
            bobj = (float(bcheck["objective"])
                    if bcheck.get("feasible") else None)
            oobj = (float(ocheck["objective"])
                    if ocheck.get("feasible") else None)
            rows.append({
                "instance": instance,
                "seed": seed,
                "sequential": cobj,
                "sequential_feasible": bool(ccheck.get("feasible")),
                "baseline": bobj,
                "baseline_feasible": bool(bcheck.get("feasible")),
                "one_step": oobj,
                "one_step_feasible": bool(ocheck.get("feasible")),
                "baseline_delta_pct": (
                    100.0 * (cobj - bobj) / bobj
                    if cobj is not None and bobj is not None else None),
                "one_step_delta_pct": (
                    100.0 * (cobj - oobj) / oobj
                    if cobj is not None and oobj is not None else None),
            })

    baseline_summary = summarize(rows, "baseline_delta_pct")
    one_step_summary = summarize(rows, "one_step_delta_pct")
    all_seq_feasible = all(row["sequential_feasible"] for row in rows)
    all_baseline_feasible = all(row["baseline_feasible"] for row in rows)
    bmed = baseline_summary["median_delta_pct"]
    baseline_board_medians = [
        value["median_delta_pct"]
        for value in baseline_summary["instances"].values()
    ]
    target_board = any(
        baseline_summary["instances"][key]["median_delta_pct"] is not None and
        baseline_summary["instances"][key]["median_delta_pct"] <= -3.0
        for key in ("P4", "P5")
    )
    baseline_pass = bool(
        all_seq_feasible and all_baseline_feasible and
        baseline_summary["informative"] == 16 and bmed is not None and
        bmed <= -2.0 and
        baseline_summary["wins"] > baseline_summary["losses"] and
        all(value is not None and value <= 2.0
            for value in baseline_board_medians) and target_board
    )
    omed = one_step_summary["median_delta_pct"]
    one_board_medians = [
        value["median_delta_pct"]
        for value in one_step_summary["instances"].values()
        if value["median_delta_pct"] is not None
    ]
    one_step_pass = bool(
        all_seq_feasible and omed is not None and omed <= 0.0 and
        all(value <= 2.0 for value in one_board_medians)
    )
    passed = baseline_pass and one_step_pass
    summary = {
        "schema_version": 1,
        "reference": str(args.reference.resolve()),
        "rows": rows,
        "all_sequential_checker_feasible": all_seq_feasible,
        "all_baseline_checker_feasible": all_baseline_feasible,
        "baseline": baseline_summary,
        "one_step": one_step_summary,
        "baseline_pass": baseline_pass,
        "one_step_pass": one_step_pass,
        "verdict": ("advance_native_budget" if passed
                    else "reject_sequential_plateau"),
    }
    (args.directory / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("solver", type=pathlib.Path)
    p_run.add_argument("constructions", type=pathlib.Path)
    p_run.add_argument("instance", type=int, choices=(3, 4, 5, 6))
    p_run.add_argument("seed", type=int)
    p_run.add_argument("iters", type=int)
    p_run.add_argument("output", type=pathlib.Path)
    p_run.set_defaults(func=run)
    p_collect = sub.add_parser("collect")
    p_collect.add_argument("directory", type=pathlib.Path)
    p_collect.add_argument("reference", type=pathlib.Path)
    p_collect.set_defaults(func=collect)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
