#!/usr/bin/env python3
"""Large, checker-gated blocker-coalition reconstructions.

Usage: rapid_coalition.py problem.json seed.sol.json out.sol.json receipt.json
       --seconds N --seed S
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
    import sscore
    import utils
except Exception as exc:  # Fail closed: no unchecked output is ever emitted.
    raise SystemExit("required Engine/checker import failed: %s" % exc)


def _engine(problem):
    blocks, bays, weights = problem["blocks"], problem["bays"], problem["weights"]
    eng = sscore.Engine()
    eng.init_problem(
        len(blocks), len(bays),
        [int(math.ceil(b["release_time"] - 1e-9)) for b in blocks],
        [int(round(b["due_date"])) for b in blocks],
        [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in blocks],
        [float(b["workload"]) for b in blocks],
        [[float(x) for x in b["bay_preferences"]] for b in blocks],
        [float(b["width"]) for b in bays], [float(b["height"]) for b in bays],
        float(weights["w1"]), float(weights["w2"]), float(weights["w3"]))
    for i, block in enumerate(blocks):
        for orient in block["shape"]:
            layers = [[list(v) for v in layer] for layer in orient["layers"]]
            rx, ry = layers[0][0]
            eng.add_orient(i, [[(x - rx, y - ry) for x, y in layer] for layer in layers])
    horizon = max(int(round(b["due_date"])) for b in blocks)
    horizon += sum(max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in blocks) + 2
    eng.build_occ(horizon)
    return eng


def _load_solution(eng, doc, n):
    rows = [[-1, 0, 0, 0, 0, 0, 0] for _ in range(n)]
    for time_key, ops in doc.get("operations", {}).items():
        t = int(time_key)
        for op in ops:
            i = int(op["block_id"])
            if not 0 <= i < n:
                raise ValueError("operation block_id out of range")
            if op["type"] == "ENTRY":
                if rows[i][6]:
                    raise ValueError("duplicate ENTRY for block %d" % i)
                rows[i] = [int(op["bay_id"]), int(op["orient_idx"]), int(op["x"]),
                           int(op["y"]), t, 0, 1]
            elif op["type"] == "EXIT":
                if not rows[i][6]:
                    raise ValueError("EXIT before ENTRY for block %d" % i)
                rows[i][5] = t
            else:
                raise ValueError("unknown operation type")
    if any(not r[6] or r[5] <= r[4] for r in rows):
        raise ValueError("seed must place every block with a later EXIT")
    eng.set_solution([x for row in rows for x in row])
    eng.build_occ(eng.horizon())
    return rows


def _to_solution(eng, n):
    ent, ext = {}, {}
    for i in range(n):
        bay, ori, x, y, e, f, placed = eng.get_block(i)
        if not placed:
            raise ValueError("Engine returned unplaced block")
        ent.setdefault(int(e), []).append((i, bay, x, y, ori))
        ext.setdefault(int(f), []).append((i, bay, e))
    operations = {}
    for t in sorted(set(ent) | set(ext)):
        ops = []
        for i, bay, e in sorted(ext.get(t, []), key=lambda z: -z[2]):
            ops.append({"type": "EXIT", "block_id": i, "bay_id": bay})
        for i, bay, x, y, ori in sorted(ent.get(t, [])):
            ops.append({"type": "ENTRY", "block_id": i, "bay_id": bay,
                        "x": x, "y": y, "orient_idx": ori})
        operations[str(t)] = ops
    return {"operations": operations}


def _checked(problem, solution):
    try:
        result = utils.check_feasibility(problem, solution)
    except Exception:
        return None
    if (not isinstance(result, dict) or type(result.get("feasible")) is not bool
            or not isinstance(result.get("violations"), list)):
        return None
    if result["feasible"] and (not isinstance(result.get("objective"), (int, float))
                               or not math.isfinite(result["objective"])):
        return None
    return result


def _components(eng):
    z1, z2, z3, total = eng.objective()
    return {"z1": z1, "z2": z2, "z3": z3, "total": total}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("problem")
    ap.add_argument("seed_solution")
    ap.add_argument("out_solution")
    ap.add_argument("receipt")
    ap.add_argument("--seconds", type=float, required=True)
    ap.add_argument("--seed", type=int, required=True)
    args = ap.parse_args()
    if args.seconds <= 0:
        raise SystemExit("--seconds must be positive")
    problem = json.loads(Path(args.problem).read_text())
    seed_doc = json.loads(Path(args.seed_solution).read_text())
    n = len(problem["blocks"])
    eng = _engine(problem)
    rows = _load_solution(eng, seed_doc, n)
    seed_check = _checked(problem, seed_doc)
    if seed_check is None or not seed_check["feasible"]:
        raise SystemExit("seed is not officially checker-feasible; refusing unchecked reconstruction")
    incumbent = float(seed_check["objective"])
    blocks = problem["blocks"]
    due = [int(round(b["due_date"])) for b in blocks]
    processing = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in blocks]
    prefs = [[float(x) for x in b["bay_preferences"]] for b in blocks]
    regret = [max(p) - p[rows[i][0]] for i, p in enumerate(prefs)]
    tardy = [max(0, rows[i][5] - due[i]) for i in range(n)]
    # Exact accessCohort is the Engine's crane blocker BFS, seeded by the worst
    # priced tardy block. It is supplemented by the same bay/time window and
    # preference-regret ranks so each arm has a distinct coalition mechanism.
    ranked = sorted(range(n), key=lambda i: (-tardy[i], -regret[i], due[i], i))
    seed = ranked[0]
    bay_cost = [sum(tardy[i] for i in range(n) if rows[i][0] == j)
                for j in range(len(problem["bays"]))]
    bottleneck_bay = max(range(len(bay_cost)), key=lambda j: (bay_cost[j], -j))
    exact = eng.access_cohort(seed, n)
    base_pool = list(dict.fromkeys(exact + ranked))
    bay_window = sorted(range(n), key=lambda i: (
        rows[i][0] != bottleneck_bay, abs(rows[i][4] - rows[seed][4]),
        -tardy[i], -regret[i], i))
    whole_bay = [i for i in bay_window if rows[i][0] == bottleneck_bay]
    # This arm deliberately rebuilds every incumbent member of the bottleneck bay.
    whole_bay = list(dict.fromkeys(whole_bay))
    targets = [("coalition_30", max(2, math.ceil(.30 * n)), base_pool),
               ("coalition_60", max(2, math.ceil(.60 * n)), bay_window),
               ("whole_bay", len(whole_bay), whole_bay)]
    deadline = time.monotonic() + args.seconds
    best_doc, best_obj = seed_doc, incumbent
    best_components = _components(eng)
    arm_results = []
    rng = random.Random(args.seed)
    for arm_index, (name, target, pool) in enumerate(targets):
        coalition = sorted(pool[:min(n, target)])
        if len(coalition) < target:
            coalition = sorted(set(coalition) | set(ranked[:target]))
        # Two genuinely global rebuild orderings per arm; non-coalition bay
        # assignments are forced while coalition assignments stay free.
        attempts = []
        for variant in range(2):
            remaining = max(0.0, deadline - time.monotonic())
            if remaining <= 0:
                attempts.append({"status": "deadline"})
                continue
            assignment = [row[0] for row in rows]
            for i in coalition:
                assignment[i] = -1
            cset = set(coalition)
            if variant == 0:
                order = sorted(range(n), key=lambda i: (i not in cset, due[i], -processing[i], i))
            else:
                noise = {i: rng.random() for i in range(n)}
                order = sorted(range(n), key=lambda i: (i not in cset, due[i] - processing[i], noise[i], i))
            eng.set_solution([x for row in rows for x in row])
            eng.build_occ(eng.horizon())
            budget = min(remaining, max(0.05, args.seconds / 6.0))
            rc = eng.reconstruct_ordered(assignment, order, False,
                                         args.seed + 1009 * arm_index + variant, budget)
            if rc != -1:
                attempts.append({"status": "reconstruct_failed", "code": rc})
                continue
            candidate = _to_solution(eng, n)
            check = _checked(problem, candidate)
            if check is None:
                attempts.append({"status": "checker_unavailable"})
                continue
            if not check["feasible"]:
                attempts.append({"status": "infeasible", "violations": check["violations"][:3]})
                continue
            objective = float(check["objective"])
            moved = sum(rows[i][0] != eng.get_block(i)[0] for i in range(n))
            gained = objective < best_obj
            components = _components(eng)
            attempts.append({"status": "feasible", "objective": objective,
                             "components": components, "moved_assignments": moved,
                             "accepted": gained})
            if gained:
                best_doc, best_obj, best_components = candidate, objective, components
        arm_results.append({"arm": name, "destroyed_fraction": len(coalition) / n,
                            "coalition_size": len(coalition), "attempts": attempts})
    final_check = _checked(problem, best_doc)
    if final_check is None or not final_check["feasible"]:
        raise SystemExit("final checker gate failed")
    Path(args.out_solution).write_text(json.dumps(best_doc, separators=(",", ":")))
    Path(args.receipt).write_text(json.dumps({
        "seed": args.seed, "seconds": args.seconds, "seed_objective": incumbent,
        "final_objective": float(final_check["objective"]),
        "improved": float(final_check["objective"]) < incumbent,
        "objective_components": best_components,
        "arms": arm_results}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
