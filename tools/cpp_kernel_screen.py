#!/usr/bin/env python3
"""Checker-gated driver for opt-in C++ solver kernels."""
import argparse
import json
import math
import time
from pathlib import Path

import rapid_schedule as schedule


METHODS = {
    "memetic": "run_kernel_memetic",
    "beam_check": "run_kernel_beam_check",
    "decomp": "run_kernel_decomp",
    "bandit": "run_kernel_bandit",
    "parallel_bandit": "run_kernel_parallel_bandit",
    "repair_scored": "run_repair_scored",
    "repair_exact": "run_repair_exact_coalition",
    "repair_nfp": "run_repair_nfp_contact",
    "repair_lookahead": "run_repair_lookahead",
    "repair_learned": "run_repair_learned",
    "pattern_master": "run_kernel_pattern_master",
    "parallel_benders": "run_kernel_parallel_benders",
    "mcts_coalition": "run_kernel_mcts_coalition",
    "ejection_chain": "run_kernel_ejection_chain",
    "large_ruin": "run_kernel_large_ruin",
    "island_memetic_v2": "run_kernel_island_memetic_v2",
    "assignment_flow": "run_kernel_assignment_flow",
    "event_order": "run_kernel_event_order",
    "assignment_cycles": "run_kernel_assignment_cycles",
    "backbone_pair": "run_kernel_backbone_repack",
    "backbone_all": "run_kernel_backbone_repack",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kernel", choices=tuple(METHODS))
    parser.add_argument("problem_json")
    parser.add_argument("seed_sol_json")
    parser.add_argument("out_sol_json")
    parser.add_argument("receipt_json")
    parser.add_argument("--seconds", required=True, type=float)
    parser.add_argument("--seed", required=True, type=int)
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        raise ValueError("--seconds must be positive and finite")

    problem = schedule._load_json(args.problem_json)
    seed_board = schedule._load_json(args.seed_sol_json)
    seed_check = schedule._checker(problem, seed_board)
    flat, max_time = schedule._seed_flat(problem, seed_board)
    durations = [max(1, int(math.ceil(float(block["processing_time"]) - 1e-9)))
                 for block in problem["blocks"]]
    engine = schedule._engine(problem, max(max_time + sum(durations) + 2, 2))
    engine.set_solution(flat)
    if not engine.exact_state_ok():
        raise ValueError("seed board does not load as an exact Engine state")

    method = getattr(engine, METHODS[args.kernel], None)
    if method is None:
        raise RuntimeError(f"compiled extension lacks {METHODS[args.kernel]}")
    started = time.monotonic()
    if args.kernel == "backbone_pair":
        stats = method(args.seconds, args.seed, 0)
    elif args.kernel == "backbone_all":
        stats = method(args.seconds, args.seed, 1)
    else:
        stats = method(args.seconds, args.seed)
    engine.set_exact_events(True)
    compacted = int(engine.compact_event_schedule())
    stats["post_event_compaction"] = compacted
    elapsed = time.monotonic() - started
    if not engine.exact_state_ok():
        raise ValueError("kernel returned an invalid Engine state")
    board = schedule._board_from_engine(engine, len(problem["blocks"]))
    checked = schedule._checker(problem, board)
    if checked["objective"] > seed_check["objective"] + 1e-9:
        raise ValueError("kernel violated its monotone rollback contract")

    schedule._atomic_json(args.out_sol_json, board)
    persisted = schedule._load_json(args.out_sol_json)
    persisted_check = schedule._checker(problem, persisted)
    if abs(float(persisted_check["objective"]) - float(checked["objective"])) > 1e-9:
        Path(args.out_sol_json).unlink(missing_ok=True)
        raise ValueError("persisted kernel board objective mismatch")
    receipt = {
        "schemaVersion": 1,
        "kernel": args.kernel,
        "seed": args.seed,
        "secondsRequested": args.seconds,
        "secondsElapsed": elapsed,
        "seedObjective": seed_check["objective"],
        "objective": persisted_check["objective"],
        "gain": seed_check["objective"] - persisted_check["objective"],
        "obj1": persisted_check["obj1"],
        "obj2": persisted_check["obj2"],
        "obj3": persisted_check["obj3"],
        "stats": stats,
        "feasible": True,
    }
    schedule._atomic_json(args.receipt_json, receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
