#!/usr/bin/env python3
"""Temporal-first constructive solver.

Usage: pipeline_temporal_first.py <problem.json> <out.sol.json> <receipt.json>
       --seconds N --seed S

The temporal skeleton (bay assignment plus per-bay event priority) is made
before geometry is attempted.  The native constructor is deliberately used
only as the geometry/event-realisation stage for a fixed skeleton.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "solver"))

try:
    import sscore
    import utils
except Exception as exc:
    raise SystemExit(f"required solver Engine/checker import failed: {exc}")

try:
    from ortools.sat.python import cp_model
except Exception:
    cp_model = None


def atomic_json(path: str, value: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent,
                                    text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, separators=(",", ":"))
            stream.write("\n")
        os.replace(temporary, destination)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def checked(problem: dict[str, Any], board: dict[str, Any]) -> dict[str, Any] | None:
    """Fail closed: malformed or unavailable checker verdicts are not evidence."""
    try:
        verdict = utils.check_feasibility(problem, board)
    except Exception:
        return None
    if not isinstance(verdict, dict) or verdict.get("feasible") is not True:
        return None
    if not isinstance(verdict.get("objective"), (int, float)):
        return None
    if not math.isfinite(float(verdict["objective"])):
        return None
    return verdict


def build_engine(problem: dict[str, Any], horizon: int) -> Any:
    blocks, bays, weights = problem["blocks"], problem["bays"], problem["weights"]
    engine = sscore.Engine()
    engine.init_problem(
        len(blocks), len(bays),
        [int(math.ceil(float(b["release_time"]) - 1e-9)) for b in blocks],
        [int(round(float(b["due_date"]))) for b in blocks],
        [max(1, int(math.ceil(float(b["processing_time"]) - 1e-9))) for b in blocks],
        [float(b["workload"]) for b in blocks],
        [[float(x) for x in b["bay_preferences"]] for b in blocks],
        [float(b["width"]) for b in bays], [float(b["height"]) for b in bays],
        float(weights["w1"]), float(weights["w2"]), float(weights["w3"]),
    )
    for block_id, block in enumerate(blocks):
        for orientation in block["shape"]:
            layers = [[list(point) for point in layer] for layer in orientation["layers"]]
            if not layers or not layers[0] or not layers[0][0]:
                raise ValueError(f"block {block_id}: malformed shape")
            ref_x, ref_y = layers[0][0]
            engine.add_orient(block_id, [[(x - ref_x, y - ref_y) for x, y in layer]
                                          for layer in layers])
    engine.build_occ(horizon)
    return engine


def board_from_engine(engine: Any, count: int) -> dict[str, Any]:
    """Use the Engine's chronological event stream; do not invent same-time order."""
    records: dict[int, tuple[int, int, int, int]] = {}
    for block_id in range(count):
        bay, orient, x, y, _entry, _exit, placed = engine.get_block(block_id)
        if not placed:
            raise ValueError("geometry constructor left a block unplaced")
        records[block_id] = (int(bay), int(orient), int(x), int(y))
    operations: dict[str, list[dict[str, Any]]] = {}
    for event_time, kind, block_id, bay_id in engine.ordered_events():
        block_id, bay_id = int(block_id), int(bay_id)
        row: dict[str, Any] = {"type": "EXIT" if int(kind) == 0 else "ENTRY",
                               "block_id": block_id, "bay_id": bay_id}
        if int(kind) != 0:
            _bay, orient, x, y = records[block_id]
            row.update(x=x, y=y, orient_idx=orient)
        operations.setdefault(str(int(event_time)), []).append(row)
    return {"operations": operations}


def data_vectors(problem: dict[str, Any]) -> tuple[list[int], list[int], list[int], list[list[float]]]:
    blocks = problem["blocks"]
    return (
        [int(math.ceil(float(b["release_time"]) - 1e-9)) for b in blocks],
        [int(round(float(b["due_date"]))) for b in blocks],
        [max(1, int(math.ceil(float(b["processing_time"]) - 1e-9))) for b in blocks],
        [[float(v) for v in b["bay_preferences"]] for b in blocks],
    )


def greedy_skeleton(release: list[int], due: list[int], duration: list[int], prefs: list[list[float]],
                    penalties: list[list[int]], variant: int, rng: random.Random) -> tuple[list[int], list[int]]:
    bays = len(prefs[0])
    loads = [0] * bays
    assignment = [-1] * len(due)
    order = sorted(range(len(due)), key=lambda i: (due[i], -duration[i], release[i], i))
    if variant % 2:
        order = sorted(order, key=lambda i: (due[i] - duration[i], -duration[i], rng.random(), i))
    for i in order:
        best = min(range(bays), key=lambda bay: (
            penalties[i][bay] * 1000 - int(round(prefs[i][bay] * 100)) + loads[bay] * (1 + variant % 3), bay))
        assignment[i] = best
        loads[best] += duration[i]
    return assignment, order


def cpsat_skeleton(release: list[int], due: list[int], duration: list[int], prefs: list[list[float]],
                   penalties: list[list[int]], time_penalties: list[int], variant: int, seed: int,
                   budget: float) -> tuple[list[int], list[int], str] | None:
    if cp_model is None or budget <= 0.03:
        return None
    n, bays = len(due), len(prefs[0])
    horizon = max(max(due), max(release)) + sum(duration) + 1
    model = cp_model.CpModel()
    bay = [model.NewIntVar(0, bays - 1, f"bay_{i}") for i in range(n)]
    start = [model.NewIntVar(release[i] + time_penalties[i], horizon - duration[i], f"start_{i}") for i in range(n)]
    tardy = [model.NewIntVar(0, horizon, f"late_{i}") for i in range(n)]
    choice = [[model.NewBoolVar(f"choice_{i}_{j}") for j in range(bays)] for i in range(n)]
    max_pref = [max(row) for row in prefs]
    pref_cost: list[Any] = []
    for i in range(n):
        model.AddExactlyOne(choice[i])
        model.Add(bay[i] == sum(j * choice[i][j] for j in range(bays)))
        model.Add(tardy[i] >= start[i] + duration[i] - due[i])
        pref_cost.append(sum(int(round(100 * (max_pref[i] - prefs[i][j]))) * choice[i][j]
                             for j in range(bays)))
    # Bounded cumulative scheduling is a hypothesis generator, not a false
    # physical model: geometry remains authoritative.  Different capacities
    # make distinct temporal overlap/event-order hypotheses deterministic.
    capacity = max(1, (n + bays - 1) // bays + (variant % 3) - 1)
    intervals_by_bay: list[list[Any]] = [[] for _ in range(bays)]
    for i in range(n):
        for j in range(bays):
            interval = model.NewOptionalIntervalVar(start[i], duration[i], start[i] + duration[i],
                                                    choice[i][j], f"job_{i}_{j}")
            intervals_by_bay[j].append(interval)
    for intervals in intervals_by_bay:
        model.AddCumulative(intervals, [1] * n, capacity)
    penalty_cost = sum(penalties[i][j] * choice[i][j] for i in range(n) for j in range(bays))
    # Due date is primary; the coefficient bounds all lower-priority terms.
    model.Minimize((10000 + 100 * n) * sum(tardy) + sum(pref_cost) + 100 * penalty_cost)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(0.02, budget)
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = seed + 7919 * variant
    solver.parameters.randomize_search = True
    solver.parameters.log_search_progress = False
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    assignment = [int(solver.Value(bay[i])) for i in range(n)]
    order = sorted(range(n), key=lambda i: (int(solver.Value(start[i])), due[i], -duration[i], i))
    return assignment, order, solver.StatusName(status)


def persist_reload_check(problem: dict[str, Any], destination: str, board: dict[str, Any]) -> dict[str, Any] | None:
    atomic_json(destination, board)
    try:
        reloaded = json.loads(Path(destination).read_text(encoding="utf-8"))
    except Exception:
        return None
    return checked(problem, reloaded)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("problem_json")
    parser.add_argument("out_sol_json")
    parser.add_argument("receipt_json")
    parser.add_argument("--seconds", required=True, type=float)
    parser.add_argument("--seed", required=True, type=int)
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        raise SystemExit("--seconds must be finite and positive")
    if Path(args.out_sol_json).resolve() == Path(args.receipt_json).resolve():
        raise SystemExit("solution and receipt paths must differ")

    started = time.monotonic()
    deadline = started + args.seconds
    problem = json.loads(Path(args.problem_json).read_text(encoding="utf-8"))
    blocks, bays = problem["blocks"], problem["bays"]
    if not blocks or not bays:
        raise SystemExit("problem requires non-empty blocks and bays")
    release, due, duration, prefs = data_vectors(problem)
    horizon = max(max(due) + max(duration) + 40, max(release) + sum(duration) + 1)
    penalties = [[0 for _ in bays] for _ in blocks]
    time_penalties = [0 for _ in blocks]
    rng = random.Random(args.seed)
    best_board: dict[str, Any] | None = None
    best_check: dict[str, Any] | None = None
    skeleton_count = geometry_failures = feedback_iterations = feasible_candidates = 0
    attempts: list[dict[str, Any]] = []
    maximum_attempts = max(2, min(16, int(args.seconds * 2) + 2))

    for variant in range(maximum_attempts):
        remaining = deadline - time.monotonic()
        if remaining <= 0.03:
            break
        cp_budget = min(max(0.03, remaining * 0.30), max(0.05, args.seconds / maximum_attempts))
        scheduled = cpsat_skeleton(release, due, duration, prefs, penalties, time_penalties,
                                   variant, args.seed, cp_budget)
        if scheduled is None:
            assignment, order = greedy_skeleton(
                [release[i] + time_penalties[i] for i in range(len(blocks))],
                due, duration, prefs, penalties, variant, rng)
            source, schedule_status = "greedy", "unavailable_or_no_solution"
        else:
            assignment, order, schedule_status = scheduled
            source = "cp_sat"
        skeleton_count += 1
        engine = build_engine(problem, horizon)
        geometry_budget = max(0.01, deadline - time.monotonic())
        code = engine.reconstruct_ordered(assignment, order, False,
                                          args.seed + 104729 * variant, geometry_budget)
        attempt: dict[str, Any] = {"index": variant, "source": source,
                                   "schedule_status": schedule_status,
                                   "geometry_code": int(code)}
        if code != -1:
            geometry_failures += 1
            feedback_iterations += 1
            for i, bay in enumerate(assignment):
                penalties[i][bay] += 1 + (variant % 3)
            for i in order[:max(1, len(order) // 3)]:
                time_penalties[i] += 1
            attempt["result"] = "geometry_failed"
            attempts.append(attempt)
            continue
        try:
            board = board_from_engine(engine, len(blocks))
        except Exception as exc:
            geometry_failures += 1
            feedback_iterations += 1
            for i, bay in enumerate(assignment):
                penalties[i][bay] += 2
            for i in order[:max(1, len(order) // 3)]:
                time_penalties[i] += 1
            attempt.update(result="event_extraction_failed", error=str(exc))
            attempts.append(attempt)
            continue
        verdict = checked(problem, board)
        if verdict is None:
            geometry_failures += 1
            feedback_iterations += 1
            for i, bay in enumerate(assignment):
                penalties[i][bay] += 2
            for i in order[:max(1, len(order) // 3)]:
                time_penalties[i] += 1
            attempt["result"] = "checker_rejected"
            attempts.append(attempt)
            continue
        feasible_candidates += 1
        objective = float(verdict["objective"])
        attempt.update(result="feasible", objective=objective,
                       objective_components={k: verdict.get(k) for k in ("obj1", "obj2", "obj3")})
        if best_check is None or objective < float(best_check["objective"]):
            # Each winner is persisted, reloaded, and checker-gated before it
            # becomes the in-run incumbent.
            persisted = persist_reload_check(problem, args.out_sol_json, board)
            if persisted is not None:
                best_board, best_check = board, persisted
                attempt["accepted"] = True
            else:
                attempt["accepted"] = False
                attempt["result"] = "persist_reload_checker_rejected"
        else:
            attempt["accepted"] = False
        attempts.append(attempt)

    fallback_used = False
    if best_board is None:
        fallback_used = True
        import myalgorithm
        fallback = myalgorithm._trivial(problem)
        verdict = persist_reload_check(problem, args.out_sol_json, fallback)
        if verdict is None:
            raise SystemExit("checker-certified _trivial fallback could not be persisted")
        best_board, best_check = fallback, verdict
    else:
        # Re-gate the final selected persisted winner, including when a later
        # inferior candidate followed it.
        final = persist_reload_check(problem, args.out_sol_json, best_board)
        if final is None:
            raise SystemExit("final persisted winner failed checker gate")
        best_check = final

    receipt = {
        "seed": args.seed,
        "seconds": args.seconds,
        "cp_sat_available": cp_model is not None,
        "skeleton_count": skeleton_count,
        "geometry_failures": geometry_failures,
        "feedback_iterations": feedback_iterations,
        "feasible_candidates": feasible_candidates,
        "objective": float(best_check["objective"]),
        "objective_components": {k: best_check.get(k) for k in ("obj1", "obj2", "obj3")},
        "elapsed_seconds": time.monotonic() - started,
        "fallback_used": fallback_used,
        "attempts": attempts,
    }
    atomic_json(args.receipt_json, receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
