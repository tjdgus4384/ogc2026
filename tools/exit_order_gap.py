#!/usr/bin/env python3
"""Deterministically measure the fixed-vs-flexible same-day EXIT gap.

This deliberately keeps the shipped corner vocabulary.  For each selected
block, remove it from one deterministic complete construction and test every
corner candidate at earlier entry days under both:

  fixed     the shipped descending-entry/id EXIT order
  flexible  any same-day EXIT order accepted by the official checker

Every flexible-only witness is committed temporarily, emitted with the exact
EXIT ranks, and replayed through utils.check_feasibility.  Thus a positive hit
is a checker-certified semantic false reject, not a score draw.

Usage:
  python3 tools/exit_order_gap.py SOLVERDIR prob_4 [nblocks]
"""
from __future__ import annotations

import json
import math
import pathlib
import sys


REPO = pathlib.Path(__file__).resolve().parent.parent


def _square(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _synthetic_problem():
    # Block 0 must enter before block 1, then block 1 must exit before block 0.
    # Ascending-id EXIT order does the opposite and is therefore a strict false
    # reject, while the official checker accepts the reverse order.
    common = {
        "release_time": 0,
        "due_date": 2,
        "processing_time": 2,
        "workload": 1,
        "bay_preferences": [1],
    }
    return {
        "bays": [{"width": 4, "height": 2}],
        "weights": {"w1": 1, "w2": 1, "w3": 1},
        "blocks": [
            dict(common, shape=[{"orientation": 0,
                                 "layers": [_square(0, 0, 1, 1)]}]),
            dict(common, shape=[{"orientation": 0,
                                 "layers": [_square(0, 0, 1, 1),
                                            _square(2, 0, 3, 1)]}]),
        ],
    }


def _extract(ma, eng, n):
    return ma._extract(eng, n)


def _emit_flexible(ma, eng, n):
    exits = eng.exit_ranks()
    if len(exits) != n:
        raise RuntimeError("no feasible complete EXIT ordering")
    return ma._recs_to_sol(_extract(ma, eng, n), eng.entry_ranks(), exits)


def synthetic_check(ma):
    prob = _synthetic_problem()
    eng = ma._build_engine(prob)
    eng.build_occ(16)
    if not eng.place_at(0, 0, 0, 2, 0, 0):
        raise AssertionError("synthetic first placement failed")
    verdict = eng.placement_verdicts(1, 0, 0, 0, 0, 0)
    if verdict != [0, 1]:
        raise AssertionError(f"expected fixed=0 flexible=1, got {verdict}")
    if not eng.place_at_flexible(1, 0, 0, 0, 0, 0):
        raise AssertionError("synthetic flexible placement failed")
    sol = _emit_flexible(ma, eng, 2)
    result = ma._check(prob, sol)
    if not result or not result.get("feasible"):
        raise AssertionError(f"official checker rejected synthetic witness: {result}")
    entries = eng.entry_ranks()
    exits = eng.exit_ranks()
    print("synthetic: PASS fixed=0 flexible=1 checker=feasible "
          f"entry_ranks={entries} exit_ranks={exits}")


def audit_construction(ma, solver_dir, prob_id, max_failures):
    """Read the oracle gap exactly where a punctual fixed insert fails."""
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    blocks = prob["blocks"]
    n = len(blocks)
    # The loose insert needs the same generous safety horizon that the shipped
    # seed ladder eventually reaches on P6-class boards.
    eng, due, proc = ma._fresh(prob, 4)
    release = [int(math.ceil(b["release_time"] - 1e-9)) for b in blocks]
    order = sorted(range(n), key=lambda i: (due[i], -proc[i], i))
    failures = scanned = total_tested = total_flex_only = 0
    print(f"{solver_dir} {prob_id}: construction failure audit")
    for i in order:
        last = due[i] - proc[i]
        if last >= release[i] and eng.insert_capped(i, last, -1):
            continue
        if last < release[i]:
            if not eng.insert_capped(i, -1, -1):
                raise RuntimeError(f"could not place release-inherently-late block {i}")
            continue
        failures += 1
        if scanned < max_failures:
            scanned += 1
            gap = eng.corner_oracle_gap(i, release[i], last, 8)
            tested, fixed_ok, flex_ok, flex_only, fixed_only, truncated = gap[:6]
            total_tested += tested
            total_flex_only += flex_only
            if fixed_only:
                raise AssertionError(
                    f"flexible oracle rejected {fixed_only} fixed witnesses")
            witness = gap[6:11] if flex_only else None
            print(f"  fail {scanned:>2}/{max_failures} block {i:>3}: "
                  f"tested={tested:>7,} fixed={fixed_ok:>5,} "
                  f"flexible={flex_ok:>5,} flex-only={flex_only:>3}"
                  + (f" witness={tuple(witness)}" if witness else "")
                  + (" (prefix)" if truncated else ""))
        if not eng.insert_capped(i, -1, -1):
            raise RuntimeError(f"could not place late block {i}")
    obj = eng.objective()
    print(f"construction summary {prob_id}: punctual-failures={failures} "
          f"scanned={scanned} tested={total_tested:,} "
          f"flexible-only={total_flex_only} final-Z1={obj[0]:,.0f}")
    return total_flex_only


def audit_instance(ma, solver_dir, prob_id, nblocks):
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    blocks = prob["blocks"]
    n = len(blocks)
    eng, due, proc = ma._fresh(prob, 4)
    order = sorted(range(n), key=lambda i: (due[i], -proc[i], i))
    if eng.reconstruct_ordered([-1] * n, order, False, 7) != -1:
        raise RuntimeError("deterministic construction failed")

    base_flat = eng.get_solution()
    base_obj = eng.objective()
    horizon = eng.horizon()
    base_sol = ma._recs_to_sol(_extract(ma, eng, n), eng.entry_ranks())
    base_check = ma._check(prob, base_sol)
    if not base_check or not base_check.get("feasible"):
        # Some raw construction boards hit the known EPS-only checker gap that
        # the full driver gate repairs.  The construction-time differential
        # above remains authoritative; just skip this optional completed-board
        # follow-up instead of confusing the two mechanisms.
        print(f"{solver_dir} {prob_id}: completed-board audit skipped "
              f"(raw fixed board checker stage={None if not base_check else base_check.get('stage')})")
        return 0

    release = [int(round(b["release_time"])) for b in blocks]
    rows = [eng.get_block(i) for i in range(n)]
    candidates = sorted(
        (i for i in range(n) if rows[i][4] > release[i]),
        key=lambda i: (max(0, rows[i][5] - due[i]), rows[i][4] - release[i]),
        reverse=True,
    )[:nblocks]

    print(f"{solver_dir} {prob_id}: base obj={base_obj[3]:,.0f} "
          f"Z1={base_obj[0]:,.0f}; scan {len(candidates)} blocks")
    total_tested = total_flex_only = certified = 0
    for i in candidates:
        eng.set_solution(base_flat)
        eng.build_occ(horizon)
        old = eng.get_block(i)
        eng.set_block(i, old[0], old[1], old[2], old[3], old[4], old[5], False)
        eng.finalize_load()
        eng.build_occ(horizon)

        gap = eng.corner_oracle_gap(i, release[i], old[4] - 1, 8)
        tested, fixed_ok, flex_ok, flex_only, fixed_only, truncated = gap[:6]
        total_tested += tested
        total_flex_only += flex_only
        if fixed_only:
            raise AssertionError(f"flexible oracle rejected {fixed_only} fixed witnesses")
        witness = gap[6:11] if flex_only else None
        status = "GAP" if witness else "none"
        print(f"  block {i:>3} late={max(0, old[5]-due[i]):>3} "
              f"wait={old[4]-release[i]:>3}: tested={tested:>7,} "
              f"fixed={fixed_ok:>5,} flexible={flex_ok:>5,} "
              f"flex-only={flex_only:>3} {status}"
              + (" (prefix)" if truncated else ""))

        if witness:
            bay, ori, x, y, entry = witness
            verdict = eng.placement_verdicts(i, bay, ori, x, y, entry)
            if verdict != [0, 1]:
                raise AssertionError(f"witness changed verdict: {verdict}")
            if not eng.place_at_flexible(i, bay, ori, x, y, entry):
                raise AssertionError("could not commit flexible-only witness")
            sol = _emit_flexible(ma, eng, n)
            result = ma._check(prob, sol)
            objective = eng.objective()
            ok = bool(result and result.get("feasible"))
            print(f"    witness=(bay={bay},ori={ori},x={x},y={y},e={entry}) "
                  f"checker={ok} obj={objective[3]:,.0f} "
                  f"delta={objective[3]-base_obj[3]:+,.0f}")
            if not ok:
                raise AssertionError(f"official checker rejected real witness: {result}")
            certified += 1

    print(f"summary {prob_id}: tested={total_tested:,} "
          f"flexible-only={total_flex_only} checker-certified={certified}")
    return certified


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    solver_dir, prob_id = sys.argv[1:3]
    nblocks = int(sys.argv[3]) if len(sys.argv) > 3 else 20
    sys.path.insert(0, str(REPO / solver_dir))
    import myalgorithm as ma

    synthetic_check(ma)
    audit_construction(ma, solver_dir, prob_id, nblocks)
    audit_instance(ma, solver_dir, prob_id, nblocks)


if __name__ == "__main__":
    main()
