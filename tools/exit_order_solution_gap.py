#!/usr/bin/env python3
"""Audit flexible same-day EXIT pulls on an existing complete solution.

Usage:
  python3 tools/exit_order_solution_gap.py SOLVERDIR prob_6 SOLUTION [nblocks]

Only the proven habitat is searched: A is moved to a corner position sharing
entry day, exit day, processing time, and bay with a resident.  Fixed and
flexible oracles are compared on identical candidates.  The best improving
flexible-only move is replayed through the official checker.
"""
from __future__ import annotations

import json
import math
import pathlib
import sys


REPO = pathlib.Path(__file__).resolve().parent.parent


def flat_from_solution(prob, solution):
    n = len(prob["blocks"])
    entries, exits = {}, {}
    for day, events in solution.get("operations", {}).items():
        t = int(day)
        for event in events:
            i = int(event["block_id"])
            if event["type"] == "ENTRY":
                entries[i] = (int(event["bay_id"]), int(event["orient_idx"]),
                              int(event["x"]), int(event["y"]), t)
            elif event["type"] == "EXIT":
                exits[i] = t
    missing = [i for i in range(n) if i not in entries or i not in exits]
    if missing:
        raise ValueError(f"incomplete solution, missing {missing[:8]}")
    flat = []
    for i in range(n):
        bay, ori, x, y, entry = entries[i]
        flat.extend([bay, ori, x, y, entry, exits[i], 1])
    return flat


def emit_flexible(ma, eng, n):
    exits = eng.exit_ranks()
    if len(exits) != n:
        raise RuntimeError("no complete flexible EXIT order")
    return ma._recs_to_sol(ma._extract(eng, n), eng.entry_ranks(), exits)


def main():
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    solver_dir, prob_id, solution_path = sys.argv[1:4]
    nblocks = int(sys.argv[4]) if len(sys.argv) > 4 else 10**9
    sys.path.insert(0, str(REPO / solver_dir))
    import myalgorithm as ma

    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    solution = json.load(open(solution_path))
    checked = ma._check(prob, solution)
    if not checked or not checked.get("feasible"):
        raise RuntimeError(f"input solution is not checker-feasible: {checked}")

    n = len(prob["blocks"])
    eng, due, proc = ma._fresh(prob, 4)
    eng.set_solution(flat_from_solution(prob, solution))
    eng.build_occ(eng.horizon())
    base_flat = eng.get_solution()
    base_obj = eng.objective()
    horizon = eng.horizon()
    release = [int(math.ceil(b["release_time"] - 1e-9))
               for b in prob["blocks"]]
    rows = [eng.get_block(i) for i in range(n)]
    candidates = sorted(
        (i for i in range(n) if rows[i][4] > release[i]),
        key=lambda i: (max(0, rows[i][5] - due[i]), rows[i][4] - release[i]),
        reverse=True,
    )[:nblocks]

    print(f"{pathlib.Path(solution_path).name} {prob_id}: "
          f"base={base_obj[3]:,.0f} Z1={base_obj[0]:,.0f} "
          f"scan={len(candidates)}")
    tested = gaps = improving = certified = 0
    best = None
    for i in candidates:
        eng.set_solution(base_flat)
        eng.build_occ(horizon)
        old = eng.get_block(i)
        eng.set_block(i, old[0], old[1], old[2], old[3], old[4], old[5], False)
        eng.finalize_load()
        eng.build_occ(horizon)
        without = eng.get_solution()
        gap = eng.twin_oracle_gap(i, release[i], old[4] - 1, 64)
        tested += gap[0]
        gaps += gap[3]
        if gap[4]:
            raise AssertionError(f"flexible rejected {gap[4]} fixed placements")
        witnesses = [gap[q:q + 5] for q in range(6, len(gap), 5)]
        local_best = None
        for witness in witnesses:
            eng.set_solution(without)
            eng.build_occ(horizon)
            if not eng.place_at_flexible(i, *witness):
                raise AssertionError(f"could not commit witness {i}: {witness}")
            obj = eng.objective()
            if obj[3] + 1e-9 < base_obj[3] and (
                    local_best is None or obj[3] < local_best[0]):
                local_best = (obj[3], witness, eng.get_solution())
        if local_best is not None:
            improving += 1
            delta = local_best[0] - base_obj[3]
            print(f"  block {i:>3}: flex-only={gap[3]:>2} "
                  f"best={tuple(local_best[1])} delta={delta:+,.0f}")
            if best is None or local_best[0] < best[0]:
                best = (local_best[0], i, local_best[1], local_best[2])

    if best is not None:
        eng.set_solution(best[3])
        eng.build_occ(horizon)
        result = ma._check(prob, emit_flexible(ma, eng, n))
        if not result or not result.get("feasible"):
            raise AssertionError(f"official checker rejected best move: {result}")
        certified = 1
        print(f"  CERTIFIED block={best[1]} witness={tuple(best[2])} "
              f"objective={best[0]:,.0f} delta={best[0]-base_obj[3]:+,.0f}")
    print(f"summary: tested={tested:,} flex-only={gaps} "
          f"improving-blocks={improving} checker-certified-best={certified}")


if __name__ == "__main__":
    main()
