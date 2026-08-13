#!/usr/bin/env python3
"""Use a fixed 10%-horizon rollout to rank frozen causal construction depths."""
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

MODES = ("free", "one", "sequential")


def start_spec(prob: dict, manifest: dict, sequential: dict, mode: str):
    n = len(prob["blocks"])
    locks = [-1] * n
    if mode == "one":
        actions = [manifest["best_action"]]
        expected = {
            "flat_sha256": manifest["best_action"]["solution_sha256"],
            "components": manifest["best_action"]["components"],
            "checker": manifest["best_checker"],
        }
    elif mode == "sequential":
        actions = sequential["final"]["actions"]
        expected = sequential["final"]
    elif mode == "free":
        actions = []
        expected = {
            "flat_sha256": manifest["baseline"]["solution_sha256"],
            "components": manifest["baseline"]["components"],
            "checker": manifest["baseline"]["checker"],
        }
    else:
        raise ValueError(mode)
    for action in actions:
        block = int(action["block"])
        bay = int(action["bay"])
        if locks[block] >= 0 and locks[block] != bay:
            raise RuntimeError(f"conflicting action in {mode}: {action}")
        locks[block] = bay
    return locks, expected


def run(args) -> None:
    solver = args.solver.resolve()
    sys.path.insert(0, str(solver))
    import myalgorithm as ma

    problem_path = REPO / "data" / "preliminary_test" / f"prob_{args.instance}.json"
    manifest_path = args.manifest / f"prob_{args.instance}.json"
    sequential_path = args.sequential / f"prob_{args.instance}.json"
    prob = json.loads(problem_path.read_text())
    manifest = json.loads(manifest_path.read_text())
    sequential = json.loads(sequential_path.read_text())
    if manifest["order"] != sequential["order"]:
        raise RuntimeError("construction orders differ")
    order = seed_orders(prob)[manifest["order"]]
    n = len(prob["blocks"])
    rows = []

    for mode in MODES:
        eng, _due, _proc = ma._fresh(prob, 4)
        locks, expected = start_spec(prob, manifest, sequential, mode)
        code = int(eng.reconstruct_ordered(locks, order, False, 7))
        if code != -1:
            raise RuntimeError(f"{mode} start failed at block {code}")
        components = list(eng.objective())
        flat = list(eng.get_solution())
        if (flat_digest(flat) != expected["flat_sha256"] or
                components != expected["components"]):
            raise RuntimeError(f"{mode} start did not replay exactly")
        start_checked = checked_record(ma, prob, eng, n)
        expected_checker = expected["checker"]
        if (not start_checked["feasible"] or
                abs(float(start_checked["objective"]) -
                    float(expected_checker["objective"])) > 1e-9):
            raise RuntimeError(f"{mode} start checker did not replay")

        # Restore raw frozen bytes after the audit gate and release every lock.
        eng.set_solution(flat)
        eng.build_occ(eng.horizon())
        eng.set_assignment_lock([])
        t0 = time.monotonic()
        stats = list(eng.run_alns(
            3600.0, 0.05, 0.15, 0.002, 0.9995, args.seed,
            1000, 0.75, 0.04, 0x3FF, args.iters,
        ))
        elapsed = time.monotonic() - t0
        if not stats or int(round(stats[0])) != args.iters:
            raise RuntimeError(f"{mode} iteration contract failed: {stats[:3]}")
        audit = [int(round(value)) for value in eng.audit_state()]
        if audit[0] != 0:
            raise RuntimeError(f"{mode} engine audit failed: {audit}")
        checked = checked_record(ma, prob, eng, n)
        rows.append({
            "mode": mode,
            "start_components": components,
            "start_flat_sha256": flat_digest(flat),
            "checker": checked,
            "search_components": list(eng.objective()),
            "search_flat_sha256": flat_digest(list(eng.get_solution())),
            "run_stats": stats,
            "audit": audit,
            "elapsed": elapsed,
        })

    eligible = [row for row in rows if row["checker"].get("feasible")]
    if not eligible:
        selected = None
    else:
        priority = {mode: index for index, mode in enumerate(MODES)}
        selected = min(
            eligible,
            key=lambda row: (float(row["checker"]["objective"]),
                             priority[row["mode"]]),
        )["mode"]
    report = {
        "schema_version": 1,
        "instance": args.instance,
        "seed": args.seed,
        "iters": args.iters,
        "solver": str(solver),
        "manifest": str(manifest_path.resolve()),
        "sequential": str(sequential_path.resolve()),
        "order": manifest["order"],
        "modes": rows,
        "selected": selected,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "instance": args.instance,
        "seed": args.seed,
        "selected": selected,
        "short": {row["mode"]: row["checker"].get("objective")
                  for row in rows},
    }, sort_keys=True), flush=True)


def mode_long(reference: pathlib.Path, sequential: pathlib.Path,
              instance: int, seed: int, mode: str) -> tuple[float | None, bool]:
    if mode == "sequential":
        record = json.loads(
            (sequential / f"p{instance}-s{seed}-sequential.json").read_text())
    else:
        suffix = "baseline" if mode == "free" else "start"
        record = json.loads(
            (reference / f"p{instance}-s{seed}-{suffix}.json").read_text())
    checked = record["checker"]
    feasible = bool(checked.get("feasible"))
    return ((float(checked["objective"]) if feasible else None), feasible)


def board_summary(rows: list[dict], key: str) -> dict:
    result = {}
    for instance in (3, 4, 5, 6):
        values = [float(row[key]) for row in rows
                  if row["instance"] == instance and row[key] is not None]
        result[f"P{instance}"] = {
            "pairs": len(values),
            "wins": sum(value < -1e-12 for value in values),
            "losses": sum(value > 1e-12 for value in values),
            "ties": sum(abs(value) <= 1e-12 for value in values),
            "median": statistics.median(values) if values else None,
        }
    return result


def collect(args) -> None:
    rows = []
    for instance in (3, 4, 5, 6):
        for seed in (7, 11, 23, 41):
            short = json.loads(
                (args.directory / f"p{instance}-s{seed}.json").read_text())
            selected = short["selected"]
            long = {}
            feasible = {}
            for mode in MODES:
                long[mode], feasible[mode] = mode_long(
                    args.reference, args.long_sequential, instance, seed, mode)
            selected_obj = long.get(selected) if selected is not None else None
            selected_feasible = bool(selected is not None and feasible[selected])
            baseline = long["free"]
            oracle = min(value for value in long.values() if value is not None)
            rows.append({
                "instance": instance,
                "seed": seed,
                "selected": selected,
                "short": {row["mode"]: row["checker"].get("objective")
                          for row in short["modes"]},
                "short_feasible": {row["mode"]: bool(
                    row["checker"].get("feasible")) for row in short["modes"]},
                "long": long,
                "long_feasible": feasible,
                "selected_long": selected_obj,
                "selected_long_feasible": selected_feasible,
                "delta_from_free_pct": (
                    100.0 * (selected_obj - baseline) / baseline
                    if selected_obj is not None and baseline is not None else None),
                "oracle": oracle,
                "regret_pct": (
                    100.0 * (selected_obj - oracle) / oracle
                    if selected_obj is not None else None),
            })

    deltas = [float(row["delta_from_free_pct"]) for row in rows
              if row["delta_from_free_pct"] is not None]
    regrets = [float(row["regret_pct"]) for row in rows
               if row["regret_pct"] is not None]
    delta_boards = board_summary(rows, "delta_from_free_pct")
    regret_boards = board_summary(rows, "regret_pct")
    free_short_valid = all(row["short_feasible"]["free"] for row in rows)
    selected_long_valid = all(row["selected_long_feasible"] for row in rows)
    target_edge = any(delta_boards[key]["median"] is not None and
                      delta_boards[key]["median"] <= -3.0
                      for key in ("P4", "P5"))
    score_pass = bool(
        free_short_valid and selected_long_valid and len(deltas) == 16 and
        statistics.median(deltas) <= -2.0 and
        sum(value < -1e-12 for value in deltas) >
        sum(value > 1e-12 for value in deltas) and
        all(value["median"] is not None and value["median"] <= 2.0
            for value in delta_boards.values()) and target_edge
    )
    prediction_pass = bool(
        selected_long_valid and len(regrets) == 16 and
        statistics.median(regrets) <= 2.0 and
        all(value["median"] is not None and value["median"] <= 5.0
            for value in regret_boards.values())
    )
    summary = {
        "schema_version": 1,
        "rows": rows,
        "free_short_controls_feasible": free_short_valid,
        "all_selected_long_feasible": selected_long_valid,
        "selected_vs_free": {
            "wins": sum(value < -1e-12 for value in deltas),
            "losses": sum(value > 1e-12 for value in deltas),
            "ties": sum(abs(value) <= 1e-12 for value in deltas),
            "median": statistics.median(deltas) if deltas else None,
            "instances": delta_boards,
        },
        "prediction_regret": {
            "median": statistics.median(regrets) if regrets else None,
            "instances": regret_boards,
        },
        "score_pass": score_pass,
        "prediction_pass": prediction_pass,
        "verdict": ("advance_disjoint_replication"
                    if score_pass and prediction_pass
                    else "reject_depth_rollout_selector"),
    }
    (args.directory / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("solver", type=pathlib.Path)
    p_run.add_argument("manifest", type=pathlib.Path)
    p_run.add_argument("sequential", type=pathlib.Path)
    p_run.add_argument("instance", type=int, choices=(3, 4, 5, 6))
    p_run.add_argument("seed", type=int)
    p_run.add_argument("iters", type=int)
    p_run.add_argument("output", type=pathlib.Path)
    p_run.set_defaults(func=run)
    p_collect = sub.add_parser("collect")
    p_collect.add_argument("directory", type=pathlib.Path)
    p_collect.add_argument("reference", type=pathlib.Path)
    p_collect.add_argument("long_sequential", type=pathlib.Path)
    p_collect.set_defaults(func=collect)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
