#!/usr/bin/env python3
"""Deterministic one-decision assignment rollout kill test.

The corrected S2 evaluator removed bay actions with the claim that a forced
bay was not expressible through ``reconstruct_ordered``.  The API actually
accepts a per-block partial assignment: -1 leaves a block free and a bay index
forces it.  This probe changes exactly one such decision, rebuilds the entire
board with the same insertion order and fixed internal seed, and scores the
completed board with the true weighted objective.

This is a mechanism test, not an instance dispatcher or an adoption harness.
Every public board is processed by the same exhaustive one-action rule.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import random
import sys
import time


REPO = pathlib.Path(__file__).resolve().parent.parent


def seed_orders(prob: dict) -> list[tuple[str, list[int]]]:
    blocks = prob["blocks"]
    n = len(blocks)
    due = [int(round(b["due_date"])) for b in blocks]
    proc = [max(1, int(math.ceil(b["processing_time"] - 1e-9)))
            for b in blocks]
    release = [int(math.ceil(b["release_time"] - 1e-9)) for b in blocks]
    workload = [float(b["workload"]) for b in blocks]
    rng = random.Random(5)
    return [
        ("due", sorted(range(n), key=lambda i: due[i])),
        ("slack", sorted(range(n),
                         key=lambda i: (due[i] - proc[i] - release[i],
                                        -workload[i]))),
        ("workload", sorted(range(n), key=lambda i: (-workload[i], due[i]))),
        ("due_long", sorted(range(n), key=lambda i: (due[i], -proc[i]))),
        ("noisy_a", sorted(range(n),
                           key=lambda i: due[i] + rng.uniform(-8, 8))),
        ("noisy_b", sorted(range(n),
                           key=lambda i: due[i] + rng.uniform(-8, 8))),
    ]


def flat_hash(flat: list[int]) -> str:
    payload = json.dumps(flat, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("solver_dir", type=pathlib.Path)
    parser.add_argument("problem", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    args = parser.parse_args()

    solver_dir = args.solver_dir.resolve()
    problem_path = args.problem.resolve()
    sys.path.insert(0, str(solver_dir))
    import myalgorithm as ma

    prob = json.loads(problem_path.read_text())
    n = len(prob["blocks"])
    bays = len(prob["bays"])
    engine, _due, _proc = ma._fresh(prob, 4)

    def construct(locks: list[int], order: list[int]):
        code = int(engine.reconstruct_ordered(locks, order, False, 7))
        if code != -1:
            return None
        return tuple(float(v) for v in engine.objective()), list(engine.get_solution())

    started = time.monotonic()
    free = [-1] * n
    baselines = []
    for name, order in seed_orders(prob):
        built = construct(free, order)
        if built is not None:
            obj, flat = built
            baselines.append((obj[3], name, order, obj, flat))
    if not baselines:
        raise RuntimeError("all deterministic baseline orders failed")
    _, order_name, order, base_obj, base_flat = min(baselines, key=lambda row: row[0])

    replay_a = construct(free, order)
    replay_b = construct(free, order)
    if replay_a is None or replay_b is None:
        raise AssertionError("baseline replay failed")
    replay_exact = replay_a[0] == replay_b[0] and replay_a[1] == replay_b[1]
    if not replay_exact or replay_a[1] != base_flat:
        raise AssertionError("reconstruct_ordered is not deterministic")

    base_bays = [int(base_flat[7 * i]) for i in range(n)]
    rows = []
    for block in range(n):
        for bay in range(bays):
            if bay == base_bays[block]:
                continue
            locks = [-1] * n
            locks[block] = bay
            built = construct(locks, order)
            if built is None:
                rows.append({"block": block, "bay": bay, "status": "failed"})
                continue
            obj, flat = built
            if int(flat[7 * block]) != bay:
                raise AssertionError(
                    f"partial assignment ignored for block {block}: {flat[7 * block]} != {bay}")
            rows.append({
                "block": block,
                "bay": bay,
                "status": "built",
                "objective": obj[3],
                "components": list(obj),
                "delta": obj[3] - base_obj[3],
                "delta_pct": 100.0 * (obj[3] - base_obj[3]) / base_obj[3],
                "solution_sha256": flat_hash(flat),
                "flat": flat,
            })

    built_rows = [row for row in rows if row["status"] == "built"]
    if not built_rows:
        raise RuntimeError("every forced-bay action failed")
    best = min(built_rows, key=lambda row: row["objective"])
    best_locks = [-1] * n
    best_locks[best["block"]] = best["bay"]
    best_replay = construct(best_locks, order)
    if best_replay is None or best_replay[1] != best["flat"]:
        raise AssertionError("best action did not replay exactly")

    def checker_record(flat: list[int]) -> dict:
        engine.set_solution(flat)
        engine.build_occ(engine.horizon())
        recs = ma._extract(engine, n)
        ranks = list(engine.entry_ranks())
        result = ma._check(prob, ma._recs_to_sol(recs, ranks))
        if not result or not result.get("feasible"):
            raise AssertionError(f"official checker rejected deterministic board: {result}")
        return {"feasible": True, "objective": float(result["objective"])}

    base_check = checker_record(base_flat)
    best_check = checker_record(best["flat"])
    if abs(base_check["objective"] - base_obj[3]) > 1e-6:
        raise AssertionError("baseline kernel/checker objective mismatch")
    if abs(best_check["objective"] - best["objective"]) > 1e-6:
        raise AssertionError("candidate kernel/checker objective mismatch")

    compact_rows = []
    for row in sorted(built_rows, key=lambda item: item["objective"])[:20]:
        compact_rows.append({key: value for key, value in row.items() if key != "flat"})
    report = {
        "schema_version": 1,
        "problem": str(problem_path),
        "solver": str(solver_dir),
        "n": n,
        "bays": bays,
        "order": order_name,
        "baseline": {
            "objective": base_obj[3],
            "components": list(base_obj),
            "solution_sha256": flat_hash(base_flat),
            "checker": base_check,
        },
        "best_action": {key: value for key, value in best.items() if key != "flat"},
        "best_checker": best_check,
        "actions": {
            "tested": n * max(0, bays - 1),
            "built": len(built_rows),
            "failed": len(rows) - len(built_rows),
            "wins": sum(row["delta"] < -1e-9 for row in built_rows),
            "ties": sum(abs(row["delta"]) <= 1e-9 for row in built_rows),
            "losses": sum(row["delta"] > 1e-9 for row in built_rows),
        },
        "deterministic_replay": replay_exact,
        "top_actions": compact_rows,
        "wall_seconds": time.monotonic() - started,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "problem": problem_path.stem,
        "baseline": base_obj[3],
        "best": best["objective"],
        "delta_pct": best["delta_pct"],
        "action": [best["block"], best["bay"]],
        "wins": report["actions"]["wins"],
        "tested": report["actions"]["tested"],
        "checker": True,
        "replay": replay_exact,
        "wall_seconds": report["wall_seconds"],
    }, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
