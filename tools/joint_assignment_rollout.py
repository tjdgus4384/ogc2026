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
import statistics
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

    def checker_record(flat: list[int]) -> dict:
        """Apply the same production authority without aborting on rejection."""
        engine.set_solution(flat)
        engine.build_occ(engine.horizon())
        recs = ma._extract(engine, n)
        ranks = list(engine.entry_ranks())
        raw_solution = ma._recs_to_sol(recs, ranks)
        raw = ma._check(prob, raw_solution)
        if raw and raw.get("feasible"):
            return {
                "feasible": True,
                "objective": float(raw["objective"]),
                "rescued": False,
                "solution_sha256": hashlib.sha256(
                    json.dumps(raw_solution, sort_keys=True,
                               separators=(",", ":")).encode()
                ).hexdigest(),
            }
        # Raw deterministic seeds can carry the exact-touch artifact that the
        # production pipeline sends through _gate.  A failed rescue is a
        # rejected deterministic choice, not an instrument crash.
        solution, gated = ma._gate(prob, engine, n, time.time() + 60.0)
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
                           separators=(",", ":")).encode()
            ).hexdigest(),
        }

    started = time.monotonic()
    free = [-1] * n
    baselines = []
    for name, order in seed_orders(prob):
        built = construct(free, order)
        if built is not None:
            obj, flat = built
            checked = checker_record(flat)
            baselines.append({
                "name": name,
                "order": order,
                "objective": obj,
                "flat": flat,
                "checker": checked,
            })
    if not baselines:
        raise RuntimeError("all deterministic baseline orders failed")
    feasible_baselines = [row for row in baselines if row["checker"]["feasible"]]
    if not feasible_baselines:
        raise RuntimeError("all deterministic baseline orders failed the production gate")
    baseline = min(feasible_baselines,
                   key=lambda row: row["checker"]["objective"])
    order_name = baseline["name"]
    order = baseline["order"]
    base_obj = baseline["objective"]
    base_flat = baseline["flat"]
    base_check = baseline["checker"]

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
            locks = [-1] * n
            locks[block] = bay
            built = construct(locks, order)
            if built is None:
                rows.append({"block": block, "bay": bay, "status": "failed",
                             "kind": ("same_bay_null" if bay == base_bays[block]
                                      else "alternative")})
                continue
            obj, flat = built
            if int(flat[7 * block]) != bay:
                raise AssertionError(
                    f"partial assignment ignored for block {block}: {flat[7 * block]} != {bay}")
            rows.append({
                "block": block,
                "bay": bay,
                "status": "built",
                "kind": ("same_bay_null" if bay == base_bays[block]
                         else "alternative"),
                "objective": obj[3],
                "components": list(obj),
                "delta": obj[3] - base_obj[3],
                "delta_pct": 100.0 * (obj[3] - base_obj[3]) / base_obj[3],
                "solution_sha256": flat_hash(flat),
                "flat": flat,
            })

    built_rows = [row for row in rows if row["status"] == "built"]
    alternative_rows = [row for row in built_rows if row["kind"] == "alternative"]
    null_rows = [row for row in built_rows if row["kind"] == "same_bay_null"]
    if not alternative_rows:
        raise RuntimeError("every alternative forced-bay action failed")
    raw_best = min(alternative_rows, key=lambda row: row["objective"])

    compact_rows = []
    for row in sorted(alternative_rows, key=lambda item: item["objective"])[:20]:
        compact_rows.append({key: value for key, value in row.items() if key != "flat"})
    null_identical = sum(row["flat"] == base_flat for row in null_rows)
    null_deltas = [float(row["delta_pct"]) for row in null_rows]
    causal_null_pass = len(null_rows) == n and null_identical == n

    # A failed semantic no-op means the alternative arm is a different RNG
    # draw, not a causal assignment intervention.  Do not dignify its score
    # with an official-checker result; retain only the null diagnostics needed
    # to repair the instrument.
    best = None
    best_check = None
    gated_delta_pct = None
    checked_alternatives = 0
    rejected_alternatives = 0
    if causal_null_pass:
        if (not base_check["rescued"] and
                abs(base_check["objective"] - base_obj[3]) > 1e-6):
            raise AssertionError("baseline kernel/checker objective mismatch")
        # The kill test's fixed candidate vocabulary is the 20 best raw
        # actions.  Apply the official production gate to all 20 and select by
        # gated objective; do not assume that legalization preserves raw rank.
        for row in sorted(alternative_rows,
                          key=lambda item: item["objective"])[:20]:
            checked = checker_record(row["flat"])
            checked_alternatives += 1
            if not checked["feasible"]:
                rejected_alternatives += 1
                continue
            if (not checked["rescued"] and
                    abs(checked["objective"] - row["objective"]) > 1e-6):
                raise AssertionError("candidate kernel/checker objective mismatch")
            if best_check is None or checked["objective"] < best_check["objective"]:
                best = row
                best_check = checked
        if best_check is not None:
            best_locks = [-1] * n
            best_locks[best["block"]] = best["bay"]
            best_replay = construct(best_locks, order)
            if best_replay is None or best_replay[1] != best["flat"]:
                raise AssertionError("best action did not replay exactly")
            gated_delta_pct = (100.0 *
                               (best_check["objective"] - base_check["objective"]) /
                               base_check["objective"])
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
        "baseline_candidates": [
            {
                "name": row["name"],
                "objective": row["objective"][3],
                "components": list(row["objective"]),
                "solution_sha256": flat_hash(row["flat"]),
                "checker": row["checker"],
            }
            for row in baselines
        ],
        "raw_best_action": {
            key: value for key, value in raw_best.items() if key != "flat"
        },
        "best_action": ({key: value for key, value in best.items()
                         if key != "flat"} if best is not None else None),
        "best_checker": best_check,
        "gated_delta_pct": gated_delta_pct,
        "actions": {
            "tested": n * bays,
            "built": len(built_rows),
            "failed": len(rows) - len(built_rows),
            "alternative_wins": sum(row["delta"] < -1e-9 for row in alternative_rows),
            "alternative_ties": sum(abs(row["delta"]) <= 1e-9
                                    for row in alternative_rows),
            "alternative_losses": sum(row["delta"] > 1e-9 for row in alternative_rows),
            "checker_screened": checked_alternatives,
            "checker_rejected": rejected_alternatives,
        },
        "same_bay_null": {
            "tested": n,
            "built": len(null_rows),
            "failed": n - len(null_rows),
            "byte_identical": null_identical,
            "median_delta_pct": (statistics.median(null_deltas)
                                 if null_deltas else None),
            "min_delta_pct": min(null_deltas) if null_deltas else None,
            "max_delta_pct": max(null_deltas) if null_deltas else None,
            "pass": causal_null_pass,
        },
        "deterministic_replay": replay_exact,
        "interpretation_valid": causal_null_pass,
        "top_actions": compact_rows,
        "wall_seconds": time.monotonic() - started,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "problem": problem_path.stem,
        "baseline": base_check["objective"],
        "baseline_order": order_name,
        "best": (best_check["objective"] if best_check else None),
        "raw_delta_pct": (best["delta_pct"] if best else None),
        "gated_delta_pct": report["gated_delta_pct"],
        "action": ([best["block"], best["bay"]] if best else None),
        "wins": report["actions"]["alternative_wins"],
        "tested": report["actions"]["tested"],
        "checker": causal_null_pass and best_check is not None,
        "replay": replay_exact,
        "same_bay_null": f"{null_identical}/{n}",
        "interpretation_valid": causal_null_pass,
        "wall_seconds": report["wall_seconds"],
    }, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
