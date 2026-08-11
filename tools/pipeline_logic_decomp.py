#!/usr/bin/env python3
"""Checker-gated logic-based decomposition constructor.

Usage: pipeline_logic_decomp.py problem.json out.sol.json receipt.json
       --seconds N --seed S

The CP-SAT master assigns a bounded rolling coalition to bays and target entry
slots.  The Engine is the geometry/crane subproblem.  A failed realization is
fed back as an explicit assignment no-good; successful realizations are always
certified by the official checker before becoming incumbents.
"""
import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "solver"))
try:
    from ortools.sat.python import cp_model
    import myalgorithm
    import sscore
    import utils
except Exception as exc:
    raise SystemExit("required CP-SAT/Engine/checker import failed: %s" % exc)


def _ints(problem):
    blocks = problem["blocks"]
    return ([int(math.ceil(b["release_time"] - 1e-9)) for b in blocks],
            [int(round(b["due_date"])) for b in blocks],
            [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in blocks])


def _engine(problem):
    blocks, bays, weights = problem["blocks"], problem["bays"], problem["weights"]
    release, due, processing = _ints(problem)
    eng = sscore.Engine()
    eng.init_problem(len(blocks), len(bays), release, due, processing,
                     [float(b["workload"]) for b in blocks],
                     [[float(v) for v in b["bay_preferences"]] for b in blocks],
                     [float(b["width"]) for b in bays], [float(b["height"]) for b in bays],
                     float(weights["w1"]), float(weights["w2"]), float(weights["w3"]))
    for i, block in enumerate(blocks):
        for orient in block["shape"]:
            layers = [[list(v) for v in layer] for layer in orient["layers"]]
            rx, ry = layers[0][0]
            eng.add_orient(i, [[(x - rx, y - ry) for x, y in layer] for layer in layers])
    eng.build_occ(max(due) + sum(processing) + 2)
    return eng


def _checker(problem, solution):
    try:
        result = utils.check_feasibility(problem, solution)
    except Exception:
        return None
    if not isinstance(result, dict) or result.get("feasible") is not True:
        return None
    value = result.get("objective")
    return result if isinstance(value, (int, float)) and math.isfinite(value) else None


def _solution_from_engine(eng, n):
    entries, exits = {}, {}
    for i in range(n):
        bay, orient, x, y, entry, exit_, placed = eng.get_block(i)
        if not placed or exit_ <= entry:
            raise RuntimeError("Engine returned an incomplete board")
        entries.setdefault(int(entry), []).append((i, int(bay), int(x), int(y), int(orient)))
        exits.setdefault(int(exit_), []).append((i, int(bay), int(entry)))
    operations = {}
    # Exit-before-entry preserves the checker's chronological tie convention.
    for tick in sorted(set(entries) | set(exits)):
        ops = [{"type": "EXIT", "block_id": i, "bay_id": bay}
               for i, bay, entry in sorted(exits.get(tick, []), key=lambda x: (-x[2], x[0]))]
        ops += [{"type": "ENTRY", "block_id": i, "bay_id": bay, "x": x, "y": y,
                 "orient_idx": orient}
                for i, bay, x, y, orient in sorted(entries.get(tick, []))]
        operations[str(tick)] = ops
    return {"operations": operations}


def _components(eng):
    z1, z2, z3, total = eng.objective()
    return {"z1": z1, "z2": z2, "z3": z3, "total": total}


def _fits(block, bay):
    for orient in block["shape"]:
        xs = [p[0] for layer in orient["layers"] for p in layer]
        ys = [p[1] for layer in orient["layers"] for p in layer]
        if math.ceil(-min(xs)) + max(xs) <= bay["width"] + 1e-9 and \
           math.ceil(-min(ys)) + max(ys) <= bay["height"] + 1e-9:
            return True
    return False


def _base_assignment(problem):
    """Data-derived assignment only; it is never loaded from a prior board."""
    answer = []
    for block in problem["blocks"]:
        legal = [j for j, bay in enumerate(problem["bays"]) if _fits(block, bay)]
        if not legal:
            raise RuntimeError("a block has no containing bay")
        answer.append(max(legal, key=lambda j: (float(block["bay_preferences"][j]), -j)))
    return answer


def _coalitions(problem, seed):
    _, due, processing = _ints(problem)
    prefs = [b["bay_preferences"] for b in problem["blocks"]]
    ranked = sorted(range(len(due)), key=lambda i: (due[i], -processing[i],
                    -(max(prefs[i]) - min(prefs[i])), i))
    # P4/P5-sized boards are deliberately decomposed into bounded coalitions.
    width = max(8, min(24, max(1, len(ranked) // 5)))
    rng = random.Random(seed)
    groups = [ranked[k:k + width] for k in range(0, len(ranked), width)]
    if len(groups) > 1:
        groups.append(sorted(ranked, key=lambda i: (rng.random(), due[i], i))[:width])
    return groups


def _solve_master(problem, base, coalition, cuts, seconds, seed):
    """Solve a bounded assignment/target-time/order master for one coalition."""
    blocks, bays = problem["blocks"], problem["bays"]
    release, due, processing = _ints(problem)
    model = cp_model.CpModel()
    horizon = max(due) + sum(processing) + 2
    choose = {}
    target = {}
    for i in coalition:
        legal = [j for j, bay in enumerate(bays) if _fits(blocks[i], bay)]
        literals = []
        for j in legal:
            lit = model.NewBoolVar("a_%d_%d" % (i, j))
            choose[i, j] = lit
            literals.append(lit)
        model.AddExactlyOne(literals)
        target[i] = model.NewIntVar(release[i], horizon - processing[i], "t_%d" % i)
    # A coalition's same-bay target slots do not overlap.  This is a useful,
    # explicit scheduling relaxation; geometry remains authoritative.
    for pos, i in enumerate(coalition):
        for k in coalition[pos + 1:]:
            for j in range(len(bays)):
                if (i, j) in choose and (k, j) in choose:
                    q = model.NewBoolVar("same_%d_%d_%d" % (i, k, j))
                    model.AddBoolAnd([choose[i, j], choose[k, j]]).OnlyEnforceIf(q)
                    model.AddBoolOr([choose[i, j].Not(), choose[k, j].Not(), q])
                    before = model.NewBoolVar("before_%d_%d_%d" % (i, k, j))
                    model.Add(target[i] + processing[i] <= target[k]).OnlyEnforceIf([q, before])
                    model.Add(target[k] + processing[k] <= target[i]).OnlyEnforceIf([q, before.Not()])
    for cut in cuts:
        # At least one assignment in a failed geometry pattern must change.
        model.AddBoolOr([choose[i, j].Not() for i, j in cut])
    penalties = []
    for i in coalition:
        tardy = model.NewIntVar(0, horizon, "late_%d" % i)
        model.Add(tardy >= target[i] + processing[i] - due[i])
        penalties.append(1000 * tardy)
        for j in range(len(bays)):
            if (i, j) in choose:
                regret = int(round(100 * (max(blocks[i]["bay_preferences"]) -
                                           blocks[i]["bay_preferences"][j])))
                penalties.append(regret * choose[i, j])
    model.Minimize(sum(penalties))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(0.01, seconds)
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = seed
    solver.parameters.log_search_progress = False
    status = solver.Solve(model)
    status_name = solver.StatusName(status)
    evidence = {"status": status_name, "objective": None, "bound": None}
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, evidence
    assignment = list(base)
    for i in coalition:
        assignment[i] = next(j for j in range(len(bays)) if (i, j) in choose and solver.Value(choose[i, j]))
    order = sorted(range(len(blocks)), key=lambda i: (solver.Value(target[i]) if i in target
                                                       else release[i], due[i], i))
    evidence["objective"] = solver.ObjectiveValue()
    evidence["bound"] = solver.BestObjectiveBound()
    evidence["assignment"] = [[i, assignment[i]] for i in coalition]
    evidence["target_order"] = order
    return (assignment, order), evidence


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("problem")
    parser.add_argument("out_solution")
    parser.add_argument("receipt")
    parser.add_argument("--seconds", required=True, type=float)
    parser.add_argument("--seed", required=True, type=int)
    args = parser.parse_args()
    if args.seconds <= 0:
        raise SystemExit("--seconds must be positive")
    problem = json.loads(Path(args.problem).read_text())
    n = len(problem["blocks"])
    if n == 0:
        raise SystemExit("empty problems are not supported")
    deadline = time.monotonic() + args.seconds
    base = _base_assignment(problem)
    groups = _coalitions(problem, args.seed)
    receipt = {"seed": args.seed, "seconds": args.seconds, "pipeline": "logic-decomposition",
               "master_statuses": [], "cuts": [], "subproblem_attempts": [],
               "feasible_incumbents": [], "fallback_used": False}
    best_doc, best_check, best_components = None, None, None
    rolling = list(base)
    for gi, coalition in enumerate(groups):
        cuts = []
        # A second master solve consumes any failure cut from the first attempt,
        # while retaining a strict global wall-clock bound.
        for iteration in range(2):
            remain = deadline - time.monotonic()
            if remain <= 0:
                break
            proposal, master = _solve_master(problem, rolling, coalition, cuts,
                                              min(remain, max(0.02, args.seconds / (len(groups) * 2))),
                                              args.seed + 1009 * gi + iteration)
            master.update({"coalition": coalition, "iteration": iteration})
            receipt["master_statuses"].append(master)
            if proposal is None:
                break
            assignment, order = proposal
            eng = _engine(problem)
            status = eng.reconstruct_ordered(assignment, order, False,
                                             args.seed + 7919 * gi + iteration,
                                             max(0.001, deadline - time.monotonic()))
            attempt = {"coalition": coalition, "iteration": iteration,
                       "master_assignment": master["assignment"], "engine_code": status}
            if status != -1:
                cut = [(i, assignment[i]) for i in coalition]
                cuts.append(cut)
                receipt["cuts"].append({"kind": "geometry_no_good", "coalition": coalition,
                                        "iteration": iteration, "literals": cut, "engine_code": status})
                attempt["status"] = "geometry_failed_cut_added"
                receipt["subproblem_attempts"].append(attempt)
                continue
            candidate = _solution_from_engine(eng, n)
            check = _checker(problem, candidate)
            if check is None:
                # Checker rejection is also a forbidden master pattern: it is never an incumbent.
                cut = [(i, assignment[i]) for i in coalition]
                cuts.append(cut)
                receipt["cuts"].append({"kind": "checker_conflict_no_good", "coalition": coalition,
                                        "iteration": iteration, "literals": cut})
                attempt["status"] = "checker_rejected_cut_added"
                receipt["subproblem_attempts"].append(attempt)
                continue
            objective = float(check["objective"])
            components = _components(eng)
            accepted = best_check is None or objective < float(best_check["objective"])
            attempt.update({"status": "checker_feasible", "objective": objective,
                            "components": components, "accepted": accepted})
            receipt["subproblem_attempts"].append(attempt)
            receipt["feasible_incumbents"].append({"coalition": coalition, "iteration": iteration,
                                                    "objective": objective, "components": components,
                                                    "accepted": accepted})
            if accepted:
                best_doc, best_check, best_components = candidate, check, components
                rolling = assignment
    if best_doc is None:
        receipt["fallback_used"] = True
        best_doc = myalgorithm._trivial(problem)
        best_check = _checker(problem, best_doc)
        if best_check is None:
            raise SystemExit("trivial fallback was not checker-feasible")
        # Components are unavailable for raw fallback coordinates; retain only
        # checker authority rather than manufacturing values.
        best_components = None
    Path(args.out_solution).write_text(json.dumps(best_doc, separators=(",", ":")))
    persisted = json.loads(Path(args.out_solution).read_text())
    persisted_check = _checker(problem, persisted)
    if persisted_check is None or float(persisted_check["objective"]) != float(best_check["objective"]):
        raise SystemExit("persisted solution failed official checker reload gate")
    receipt.update({"final_objective": float(persisted_check["objective"]),
                    "objective_components": best_components,
                    "persisted_checker_feasible": True,
                    "operation_order": "ascending time; EXIT before ENTRY; deterministic block order"})
    Path(args.receipt).write_text(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
