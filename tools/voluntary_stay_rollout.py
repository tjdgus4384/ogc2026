#!/usr/bin/env python3
"""Causal rollout screen for voluntary, on-time stay extension.

The official model allows ``exit - entry >= processing``.  The shipped repair
always commits equality.  This probe keeps the normal deterministic prefix,
extends exactly one already-feasible block within its due date, and then lets
the same greedy kernel construct the suffix.  The intervention can therefore
open legal crane nesting without paying tardiness for the extended block.

This is a construction-mechanism screen, not a production dispatcher.  Every
board uses the same two targets (half of remaining exit slack and the due
date), every semantic no-op must replay byte-identically, and the production
checker is authoritative.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _orders(problem: dict[str, Any]) -> dict[str, list[int]]:
    blocks = problem["blocks"]
    n = len(blocks)
    due = [int(round(b["due_date"])) for b in blocks]
    proc = [max(1, int(math.ceil(b["processing_time"] - 1e-9)))
            for b in blocks]
    release = [int(math.ceil(b["release_time"] - 1e-9)) for b in blocks]
    workload = [float(b["workload"]) for b in blocks]
    rng = random.Random(5)
    return {
        "due": sorted(range(n), key=lambda i: due[i]),
        "slack": sorted(range(n),
                        key=lambda i: (due[i] - proc[i] - release[i],
                                       -workload[i])),
        "workload": sorted(range(n), key=lambda i: (-workload[i], due[i])),
        "due_long": sorted(range(n), key=lambda i: (due[i], -proc[i])),
        "noisy_a": sorted(range(n), key=lambda i: due[i] + rng.uniform(-8, 8)),
        "noisy_b": sorted(range(n), key=lambda i: due[i] + rng.uniform(-8, 8)),
    }


def _flat_hash(flat: list[int]) -> str:
    payload = json.dumps(flat, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _checker_record(ma: Any, engine: Any, problem: dict[str, Any],
                    flat: list[int]) -> dict[str, Any]:
    """Run the same checker/rescue authority used by the production driver."""
    n = len(problem["blocks"])
    engine.set_solution(flat)
    engine.build_occ(engine.horizon())
    raw_solution = ma._recs_to_sol(ma._extract(engine, n),
                                   list(engine.entry_ranks()))
    raw = ma._check(problem, raw_solution)
    if raw and raw.get("feasible"):
        return {
            "feasible": True,
            "objective": float(raw["objective"]),
            "rescued": False,
            "solution_sha256": hashlib.sha256(
                json.dumps(raw_solution, sort_keys=True,
                           separators=(",", ":")).encode()).hexdigest(),
        }

    solution, gated = ma._gate(problem, engine, n, time.time() + 60.0)
    if solution is None or not gated or not gated.get("feasible"):
        raw_viol = (raw or {}).get("violations", [])
        gated_viol = (gated or {}).get("violations", [])
        return {
            "feasible": False,
            "objective": None,
            "rescued": False,
            "raw_stage": (raw or {}).get("stage"),
            "raw_violations": len(raw_viol),
            "raw_first": str(raw_viol[0])[:200] if raw_viol else None,
            "gated_stage": (gated or {}).get("stage"),
            "gated_violations": len(gated_viol),
            "gated_first": (str(gated_viol[0])[:200]
                            if gated_viol else None),
        }
    return {
        "feasible": True,
        "objective": float(gated["objective"]),
        "rescued": True,
        "solution_sha256": hashlib.sha256(
            json.dumps(solution, sort_keys=True,
                       separators=(",", ":")).encode()).hexdigest(),
    }


def _run(args: argparse.Namespace) -> None:
    solver_dir = args.solver_dir.resolve()
    sys.path.insert(0, str(solver_dir))
    import myalgorithm as ma

    problem = json.loads(args.problem.read_text())
    manifest = json.loads(args.manifest.read_text())
    n = len(problem["blocks"])
    order_name = manifest["order"]
    try:
        order = _orders(problem)[order_name]
    except KeyError as exc:
        raise ValueError(f"unknown frozen order {order_name!r}") from exc

    engine, due, _proc = ma._fresh(problem, 4)
    free = [-1] * n

    def construct(targets: list[int]) -> tuple[tuple[float, ...], list[int]] | None:
        code = int(engine.reconstruct_ordered_stays(
            free, order, targets, False, 7))
        if code != -1:
            return None
        return (tuple(float(value) for value in engine.objective()),
                list(engine.get_solution()))

    started = time.monotonic()
    none = [-1] * n
    baseline = construct(none)
    if baseline is None:
        raise RuntimeError("frozen free construction failed")
    base_obj, base_flat = baseline
    base_hash = _flat_hash(base_flat)
    expected = manifest["baseline"]
    if base_hash != expected["solution_sha256"]:
        raise AssertionError("new no-treatment path differs from frozen baseline")
    if list(base_obj) != [float(v) for v in expected["components"]]:
        raise AssertionError("new no-treatment components differ from frozen baseline")
    replay = construct(none)
    replay_exact = (replay is not None and replay[0] == base_obj
                    and replay[1] == base_flat)
    if not replay_exact:
        raise AssertionError("no-treatment construction is not deterministic")
    base_check = _checker_record(ma, engine, problem, base_flat)
    if (not base_check["feasible"] or
            abs(base_check["objective"] -
                float(expected["checker"]["objective"])) > 1e-9):
        raise AssertionError("baseline production gate differs from frozen manifest")

    base_exit = [int(base_flat[7 * block + 5]) for block in range(n)]
    null_exact = 0
    null_failed = 0
    for block in range(n):
        targets = [-1] * n
        targets[block] = base_exit[block]
        built = construct(targets)
        if built is None:
            null_failed += 1
        elif built[0] == base_obj and built[1] == base_flat:
            null_exact += 1

    rows: list[dict[str, Any]] = []
    for block in range(n):
        slack = due[block] - base_exit[block]
        if slack <= 0:
            continue
        targets_to_try = {due[block], base_exit[block] + max(1, slack // 2)}
        for target in sorted(targets_to_try):
            targets = [-1] * n
            targets[block] = target
            built = construct(targets)
            if built is None:
                rows.append({
                    "block": block,
                    "base_exit": base_exit[block],
                    "target_exit": target,
                    "fraction": ((target - base_exit[block]) / slack),
                    "status": "failed",
                })
                continue
            objective, flat = built
            if int(flat[7 * block + 5]) != target:
                raise AssertionError("stay target was not retained")
            rows.append({
                "block": block,
                "base_exit": base_exit[block],
                "target_exit": target,
                "fraction": ((target - base_exit[block]) / slack),
                "status": "built",
                "objective": objective[3],
                "components": list(objective),
                "delta": objective[3] - base_obj[3],
                "delta_pct": 100.0 * (objective[3] - base_obj[3]) / base_obj[3],
                "solution_sha256": _flat_hash(flat),
                "flat": flat,
            })

    built_rows = [row for row in rows if row["status"] == "built"]
    screened = 0
    rejected = 0
    checked_rows: list[dict[str, Any]] = []
    for row in sorted(built_rows, key=lambda item: item["objective"])[:20]:
        checked = _checker_record(ma, engine, problem, row["flat"])
        screened += 1
        compact = {key: value for key, value in row.items() if key != "flat"}
        compact["checker"] = checked
        checked_rows.append(compact)
        if not checked["feasible"]:
            rejected += 1

    feasible_checked = [row for row in checked_rows
                        if row["checker"]["feasible"]]
    best = (min(feasible_checked, key=lambda row: row["checker"]["objective"])
            if feasible_checked else None)
    gated_delta = (100.0 * (best["checker"]["objective"] -
                            base_check["objective"]) / base_check["objective"]
                   if best is not None else None)
    null_pass = null_exact == n and null_failed == 0

    report = {
        "schema_version": 1,
        "problem": str(args.problem.resolve()),
        "solver": str(solver_dir),
        "frozen_manifest": str(args.manifest.resolve()),
        "order": order_name,
        "n": n,
        "baseline": {
            "objective": base_obj[3],
            "components": list(base_obj),
            "solution_sha256": base_hash,
            "checker": base_check,
        },
        "deterministic_replay": replay_exact,
        "same_exit_null": {
            "tested": n,
            "byte_identical": null_exact,
            "failed": null_failed,
            "pass": null_pass,
        },
        "actions": {
            "tested": len(rows),
            "built": len(built_rows),
            "failed": len(rows) - len(built_rows),
            "raw_wins": sum(row["delta"] < -1e-9 for row in built_rows),
            "raw_ties": sum(abs(row["delta"]) <= 1e-9 for row in built_rows),
            "raw_losses": sum(row["delta"] > 1e-9 for row in built_rows),
            "checker_screened": screened,
            "checker_rejected": rejected,
        },
        "best_action": best,
        "gated_delta_pct": gated_delta,
        "interpretation_valid": null_pass and replay_exact,
        "top_actions": checked_rows,
        "wall_seconds": time.monotonic() - started,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "problem": args.problem.stem,
        "baseline": base_check["objective"],
        "best": best["checker"]["objective"] if best else None,
        "gated_delta_pct": gated_delta,
        "action": ([best["block"], best["target_exit"]] if best else None),
        "built": len(built_rows),
        "tested": len(rows),
        "checker_rejected": rejected,
        "same_exit_null": f"{null_exact}/{n}",
        "interpretation_valid": report["interpretation_valid"],
        "wall_seconds": report["wall_seconds"],
    }, sort_keys=True), flush=True)


def _collect(args: argparse.Namespace) -> None:
    reports = []
    for problem in range(3, 7):
        path = args.input_dir / f"prob_{problem}.json"
        reports.append(json.loads(path.read_text()))

    rows = []
    valid = True
    p45_edge = False
    for problem, report in zip(range(3, 7), reports):
        valid = valid and bool(report["interpretation_valid"])
        best = report["best_action"]
        delta = report["gated_delta_pct"]
        selected_delta = min(0.0, float(delta)) if delta is not None else 0.0
        if problem in (4, 5) and selected_delta <= -5.0:
            p45_edge = True
        rows.append({
            "instance": problem,
            "baseline": report["baseline"]["checker"]["objective"],
            "best": best["checker"]["objective"] if best else None,
            "gated_delta_pct": delta,
            "selected_delta_pct": selected_delta,
            "action": ({"block": best["block"],
                        "base_exit": best["base_exit"],
                        "target_exit": best["target_exit"],
                        "fraction": best["fraction"]} if best else None),
            "null_pass": report["same_exit_null"]["pass"],
            "checker_rejected": report["actions"]["checker_rejected"],
        })
    advance = valid and p45_edge
    summary = {
        "schema_version": 1,
        "rows": rows,
        "all_interpretation_valid": valid,
        "p4_or_p5_five_percent_edge": p45_edge,
        "verdict": ("advance_voluntary_stay_rollout" if advance
                    else "reject_voluntary_stay_rollout"),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("solver_dir", type=Path)
    run.add_argument("problem", type=Path)
    run.add_argument("manifest", type=Path)
    run.add_argument("output", type=Path)
    collect = sub.add_parser("collect")
    collect.add_argument("input_dir", type=Path)
    collect.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.command == "run":
        _run(args)
    else:
        _collect(args)


if __name__ == "__main__":
    main()
