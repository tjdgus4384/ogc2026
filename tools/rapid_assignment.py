#!/usr/bin/env python3
"""Bounded CP-SAT assignment screen followed by locked production reconstruction.

Usage: rapid_assignment.py problem.json seed.sol.json out.sol.json receipt.json
       --seconds N --seed S
"""
import argparse
import json
import math
import os
import random
import sys
import time
from fractions import Fraction
from functools import reduce
from math import gcd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, os.environ.get("OGC_SOLVER_DIR", "solver")))
os.chdir(ROOT)


def _lcm(a, b):
    return a // gcd(a, b) * b


def _lcm_all(values):
    return reduce(_lcm, values, 1)


def _frac(value):
    # JSON's decimal spelling is the intended problem datum, not a binary float.
    return Fraction(str(value))


def _polygon_area(poly):
    return abs(sum(_frac(poly[k][0]) * _frac(poly[(k + 1) % len(poly)][1])
                   - _frac(poly[(k + 1) % len(poly)][0]) * _frac(poly[k][1])
                   for k in range(len(poly)))) / 2


def _footprint_area(block):
    areas = []
    for orient in block["shape"]:
        layers = orient.get("layers", [])
        if layers:
            areas.append(max((_polygon_area(layer) for layer in layers), default=Fraction(0)))
    return min(areas) if areas else Fraction(0)


def _seed_assignment(solution, n, m):
    assignment = [-1] * n
    operations = solution.get("operations")
    if not isinstance(operations, dict):
        raise ValueError("seed solution has no operations object")
    for _, rows in operations.items():
        if not isinstance(rows, list):
            raise ValueError("seed operation row is not a list")
        for row in rows:
            if not isinstance(row, dict) or row.get("type") != "ENTRY":
                continue
            i, j = row.get("block_id"), row.get("bay_id")
            if type(i) is not int or type(j) is not int or not (0 <= i < n and 0 <= j < m):
                raise ValueError("seed ENTRY has an invalid block or bay id")
            if assignment[i] != -1 and assignment[i] != j:
                raise ValueError("seed assigns a block to more than one bay")
            assignment[i] = j
    if any(j < 0 for j in assignment):
        raise ValueError("seed does not enter every block")
    return assignment


def _assignment_model(problem, incumbent, radius, seconds, seed):
    """Solve the geometry-free, exactly scored assignment master once."""
    from ortools.sat.python import cp_model

    blocks, bays = problem["blocks"], problem["bays"]
    n, m = len(blocks), len(bays)
    areas = [_frac(b["width"]) * _frac(b["height"]) for b in bays]
    if not areas or any(a <= 0 for a in areas):
        raise ValueError("all bay dimensions must be positive")
    loads = [_frac(b["workload"]) for b in blocks]
    load_den = _lcm_all(x.denominator for x in loads)
    load_int = [int(x * load_den) for x in loads]
    total_area = sum(areas)
    # q[j] is a common-denominator representation of avg_area / area[j].
    normalized_factor = [total_area / (m * a * load_den) for a in areas]
    qden = _lcm_all(x.denominator for x in normalized_factor)
    qcoef = [int(qden * x) for x in normalized_factor]
    pref = [[_frac(v) for v in b["bay_preferences"]] for b in blocks]
    regret = [[max(row) - v for v in row] for row in pref]
    weights = problem.get("weights", {})
    w2, w3 = _frac(weights.get("w2", 1)), _frac(weights.get("w3", 1))
    obj_den = _lcm_all([w2.denominator, w3.denominator] +
                       [x.denominator for row in regret for x in row])
    w2i, w3i = int(w2 * obj_den), int(w3 * obj_den)
    regret_i = [[int(x * obj_den) for x in row] for row in regret]

    model = cp_model.CpModel()
    y = [[model.NewBoolVar("a_%d_%d" % (i, j)) for j in range(m)] for i in range(n)]
    for i in range(n):
        model.AddExactlyOne(y[i])
    if radius is not None:
        model.Add(sum(y[i][incumbent[i]] for i in range(n)) >= n - radius)

    # A necessary area-time relaxation of any geometric schedule.  The horizon
    # matches production's initial occupancy horizon, so it cannot accept a
    # physically impossible aggregate assignment merely due to a short due date.
    horizon = (max(int(round(b["due_date"])) for b in blocks)
               + max(max(1, int(math.ceil(float(b["processing_time"]) - 1e-9))) for b in blocks)
               + 40)
    demand = [_footprint_area(b) * max(1, int(math.ceil(float(b["processing_time"]) - 1e-9)))
              for b in blocks]
    area_den = _lcm_all([x.denominator for x in demand] +
                        [x.denominator for x in areas])
    for j, bay_area in enumerate(areas):
        model.Add(sum(int(demand[i] * area_den) * y[i][j] for i in range(n))
                  <= int(bay_area * horizon * area_den))
        for i in range(n):
            if demand[i] / max(1, int(math.ceil(float(blocks[i]["processing_time"]) - 1e-9))) > bay_area:
                model.Add(y[i][j] == 0)

    # Exact official Z2: normalized loads are integral units / qden, hence this
    # floor is exactly the checker formula rather than a rounded surrogate.
    normalized = [sum(qcoef[j] * load_int[i] * y[i][j] for i in range(n)) for j in range(m)]
    upper = max(qcoef) * sum(load_int)
    zmax = model.NewIntVar(0, upper, "zmax")
    zmin = model.NewIntVar(0, upper, "zmin")
    for value in normalized:
        model.Add(zmax >= value)
        model.Add(zmin <= value)
    z2 = model.NewIntVar(0, max(0, upper // qden), "z2_floor")
    model.Add(qden * z2 <= zmax - zmin)
    model.Add(zmax - zmin <= qden * z2 + qden - 1)
    z3 = sum(regret_i[i][j] * y[i][j] for i in range(n) for j in range(m))
    model.Minimize(w2i * z2 + w3i * z3)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(0.001, seconds)
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = int(seed)
    solver.parameters.cp_model_presolve = True
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, solver.StatusName(status)
    return [next(j for j in range(m) if solver.Value(y[i][j])) for i in range(n)], solver.StatusName(status)


def _write_json(path, doc):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(doc, handle, separators=(",", ":"))
        handle.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("problem_json")
    parser.add_argument("seed_sol_json")
    parser.add_argument("out_sol_json")
    parser.add_argument("receipt_json")
    parser.add_argument("--seconds", required=True, type=float)
    parser.add_argument("--seed", required=True, type=int)
    args = parser.parse_args()
    receipt = {"tool": "rapid_assignment", "seed": args.seed, "seconds": args.seconds,
               "success": False, "candidates": []}
    try:
        if not math.isfinite(args.seconds) or args.seconds <= 0:
            raise ValueError("--seconds must be a positive finite number")
        random.seed(args.seed)
        with open(args.problem_json, encoding="utf-8") as handle:
            problem = json.load(handle)
        with open(args.seed_sol_json, encoding="utf-8") as handle:
            seed_solution = json.load(handle)
        blocks, bays = problem["blocks"], problem["bays"]
        n, m = len(blocks), len(bays)
        incumbent = _seed_assignment(seed_solution, n, m)

        import myalgorithm as M
        seed_check = M._check(problem, seed_solution)
        if seed_check is None or not seed_check.get("feasible"):
            raise RuntimeError("seed failed official checker or checker is unavailable")
        receipt["seed_raw_objective"] = seed_check["objective"]
        receipt["seed_assignment"] = incumbent

        deadline = time.monotonic() + args.seconds
        radii = sorted(set([max(1, n // 12), max(2, n // 6), max(4, n // 3)]))
        requested = [("radius_%d" % r, r) for r in radii] + [("global", None)]
        assignments = [("incumbent", incumbent, "seed")]
        seen = {tuple(incumbent)}
        for index, (label, radius) in enumerate(requested):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            # Spend at most one third of remaining wall time in each master;
            # reconstruction receives the remainder fairly across candidates.
            cp_budget = min(3.0, max(0.05, remaining / max(3, 2 * (len(requested) - index))))
            assignment, status = _assignment_model(problem, incumbent, radius, cp_budget,
                                                    args.seed + 1009 * (index + 1))
            receipt["candidates"].append({"name": label, "phase": "assignment",
                                          "status": status, "radius": radius})
            if assignment is not None and tuple(assignment) not in seen:
                seen.add(tuple(assignment))
                assignments.append((label, assignment, status))

        best_solution, best_check, best_name, best_assignment = seed_solution, seed_check, "incumbent", incumbent
        for index, (name, assignment, master_status) in enumerate(assignments):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            budget = remaining / (len(assignments) - index)
            candidate_end = time.monotonic() + budget
            record = {"name": name, "phase": "geometry", "master_status": master_status,
                      "radius": sum(a != b for a, b in zip(assignment, incumbent)),
                      "seconds": budget}
            eng, starts = M._seed_free(problem, diversify=True, asg=list(assignment),
                                       deadline=time.time() + max(0.001, candidate_end - time.monotonic()))
            if eng is None:
                record["status"] = "seed_failed"
                receipt["candidates"].append(record)
                continue
            # Keep this lock in place for the full production optimization call.
            eng.set_assignment_lock(list(assignment), True)
            optimize_seconds = candidate_end - time.monotonic()
            if optimize_seconds > 0:
                eng.run_portfolio(1, optimize_seconds, 1, 0.05, 0.15, 0.002,
                                  0.9995, args.seed + 7919 * (index + 1), 1, starts)
            solution = M._recs_to_sol(M._extract(eng, n))
            check = M._check(problem, solution)
            if check is None:
                record["status"] = "checker_unavailable"
            elif not check.get("feasible"):
                record["status"] = "checker_infeasible"
                record["violations"] = check.get("violations", [])[:10]
            else:
                realized = _seed_assignment(solution, n, m)
                if realized != assignment:
                    record["status"] = "assignment_lock_violation"
                    record["changed_after_lock"] = [i for i, (a, b) in
                                                    enumerate(zip(assignment, realized)) if a != b]
                else:
                    record["status"] = "feasible"
                    record["raw_objective"] = check["objective"]
                    if check["objective"] < best_check["objective"]:
                        best_solution, best_check = solution, check
                        best_name, best_assignment = name, assignment
            receipt["candidates"].append(record)

        receipt.update({"success": True, "best_candidate": best_name,
                        "best_raw_objective": best_check["objective"],
                        "improvement_raw": seed_check["objective"] - best_check["objective"],
                        "changed_block_ids": [i for i, (a, b) in enumerate(zip(incumbent, best_assignment)) if a != b]})
        _write_json(args.out_sol_json, best_solution)
        with open(args.out_sol_json, encoding="utf-8") as handle:
            persisted_solution = json.load(handle)
        persisted_check = M._check(problem, persisted_solution)
        if (persisted_check is None or not persisted_check.get("feasible")
                or abs(float(persisted_check["objective"]) - float(best_check["objective"])) > 1e-9):
            os.unlink(args.out_sol_json)
            raise ValueError("persisted assignment board failed checker replay")
        _write_json(args.receipt_json, receipt)
        return 0
    except Exception as exc:
        receipt["error"] = "%s: %s" % (type(exc).__name__, exc)
        try:
            _write_json(args.receipt_json, receipt)
        except Exception:
            pass
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
