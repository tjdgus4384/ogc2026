#!/usr/bin/env python3
"""Checker-gated island coevolution from a problem instance.

Usage: pipeline_island_coevolution.py <problem.json> <out.sol.json> <receipt.json>
       --seconds N --seed S

Each island evolves complete assignment/order/packing genomes.  A genome has no
standing as a solution: every phenotype is reconstructed afresh and admitted
only after the official checker accepts its literal chronological event trace.
"""
import argparse
import json
import math
import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "solver"))
os.chdir(ROOT)
import myalgorithm as M  # noqa: E402


def checked(problem, solution):
    result = M._check(problem, solution)
    if result is None:
        return None
    return result
def chronological(solution):
    keys = list(solution.get("operations", {}))
    return keys == [str(t) for t in sorted(int(key) for key in keys)]



def horizon(problem):
    blocks = problem["blocks"]
    return (max(int(round(x["due_date"])) for x in blocks)
            + max(max(1, int(math.ceil(x["processing_time"] - 1e-9))) for x in blocks)
            + 40)


def eligible_bays(problem):
    """Bays that contain at least one orientation at integral translated origin."""
    out = []
    for block in problem["blocks"]:
        choices = []
        for bay_id, bay in enumerate(problem["bays"]):
            for shape in block["shape"]:
                points = [p for layer in shape["layers"] for p in layer]
                lo_x, hi_x = min(p[0] for p in points), max(p[0] for p in points)
                lo_y, hi_y = min(p[1] for p in points), max(p[1] for p in points)
                x, y = math.ceil(-lo_x), math.ceil(-lo_y)
                if x + hi_x <= bay["width"] + 1e-9 and y + hi_y <= bay["height"] + 1e-9:
                    choices.append(bay_id)
                    break
        if not choices:
            raise ValueError("a block fits no bay")
        out.append(choices)
    return out


def assignment_policy(problem, eligible, name, salt):
    blocks, bays = problem["blocks"], problem["bays"]
    m = len(bays)
    loads = [0.0] * m
    answer = []
    for i, block in enumerate(blocks):
        choices = eligible[i]
        prefs = block["bay_preferences"]
        if name == "free":
            answer.append(-1)
            continue
        if name == "preference":
            bay = max(choices, key=lambda j: (prefs[j], -j))
        elif name == "balanced":
            bay = min(choices, key=lambda j: (loads[j], -prefs[j], j))
        elif name == "cyclic":
            bay = min(choices, key=lambda j: ((j - i - salt) % m, -prefs[j], j))
        else:
            raise ValueError("unknown assignment policy")
        answer.append(bay)
        loads[bay] += float(block["workload"])
    return answer


def order_for(problem, policy, salt):
    blocks = problem["blocks"]
    due = [int(round(b["due_date"])) for b in blocks]
    rel = [int(math.ceil(b["release_time"] - 1e-9)) for b in blocks]
    proc = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in blocks]
    workload = [float(b["workload"]) for b in blocks]
    if policy == "edd":
        return sorted(range(len(blocks)), key=lambda i: (due[i], rel[i], -proc[i], i))
    if policy == "release":
        return sorted(range(len(blocks)), key=lambda i: (rel[i], due[i], -proc[i], i))
    if policy == "heavy":
        return sorted(range(len(blocks)), key=lambda i: (-workload[i], due[i], -proc[i], i))
    if policy == "rotated":
        return sorted(range(len(blocks)), key=lambda i: ((due[i] - salt) % 97, rel[i], i))
    raise ValueError("unknown order policy")


def diversity(population, n):
    if len(population) < 2:
        return 0.0
    pairs = 0
    total = 0
    for i, a in enumerate(population):
        for b in population[:i]:
            total += sum(x != y for x, y in zip(a["assignment"], b["assignment"])) / max(1, n)
            total += sum(x != y for x, y in zip(a["order"], b["order"])) / max(1, n)
            pairs += 2
    return total / pairs


def components(engine):
    z1, z2, z3, total = engine.objective()
    return {"z1": z1, "z2": z2, "z3": z3, "total": total}


def phenotype(problem, genome, limit, n, hor, attempt_seconds):
    engine = M._build_engine(problem)
    if engine is None:
        return None, None, "engine_unavailable", None
    engine.build_occ(hor)
    engine.set_solution([0] * (n * 7))
    engine.build_occ(hor)
    remaining = min(limit - time.monotonic(), attempt_seconds)
    if remaining <= 0:
        return None, None, "deadline", None
    status = engine.reconstruct_ordered(genome["assignment"], genome["order"], False,
                                        genome["geometry_seed"], max(0.001, remaining))
    if status != -1:
        return None, None, "reconstruct_%s" % status, None
    sol = M._recs_to_sol(M._extract(engine, n), engine.ordered_events())
    result = checked(problem, sol)
    if result is None:
        return None, None, "checker_unavailable", None
    if not result["feasible"]:
        return None, result, "checker_rejected", components(engine)
    return sol, result, "feasible", components(engine)


def child(a, b, eligible, bay_count, rng, serial):
    n = len(a["assignment"])
    # Crossover copies whole parent bay shells; unselected shells come from B.
    selected_bays = {rng.randrange(bay_count)}
    for bay in range(bay_count):
        if rng.random() < 0.5:
            selected_bays.add(bay)
    assignment = [a["assignment"][i] if a["assignment"][i] in selected_bays
                  else b["assignment"][i] for i in range(n)]
    cut = rng.randrange(n + 1)
    # Whole event-order chunks, not independent position noise.
    take = set(a["order"][:cut])
    order = [i for i in a["order"] if i in take] + [i for i in b["order"] if i not in take]
    if rng.random() < 0.35:
        i = rng.randrange(n)
        assignment[i] = rng.choice(eligible[i])
    if rng.random() < 0.45 and n > 1:
        i, j = sorted(rng.sample(range(n), 2))
        order[i:j] = reversed(order[i:j])
    return {"assignment": assignment, "order": order,
            "assignment_policy": a["assignment_policy"] + "+" + b["assignment_policy"],
            "order_policy": a["order_policy"] + "+" + b["order_policy"],
            "geometry_policy": "mixed", "geometry_seed": rng.randrange(1, 2**31),
            "serial": serial, "penalty": 0}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("problem")
    ap.add_argument("out")
    ap.add_argument("receipt")
    ap.add_argument("--seconds", required=True, type=float)
    ap.add_argument("--seed", required=True, type=int)
    args = ap.parse_args()
    started = time.monotonic()
    receipt = {"tool": "pipeline_island_coevolution", "seed": args.seed,
               "seconds": args.seconds, "generations": 0,
               "migrations": {"attempted": 0, "feasible": 0},
               "offspring": {"attempted": 0, "feasible": 0, "feasible_rate": 0.0},
               "island_diversity": [], "failures": [], "fallback_used": False}
    def save_receipt():
        receipt["elapsed_seconds"] = round(time.monotonic() - started, 6)
        Path(args.receipt).write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    try:
        if not math.isfinite(args.seconds) or args.seconds <= 0:
            raise ValueError("--seconds must be finite and positive")
        problem = json.loads(Path(args.problem).read_text())
        n = len(problem["blocks"])
        if n == 0 or not problem["bays"]:
            raise ValueError("problem has no blocks or bays")
        eligible = eligible_bays(problem)
        hor = horizon(problem)
        rng = random.Random(args.seed)
        deadline = started + args.seconds
        attempt_seconds = max(0.02, args.seconds / 24.0)
        specs = [("preference", "edd", "tight"), ("balanced", "release", "spread"),
                 ("cyclic", "heavy", "rotated"), ("free", "rotated", "free")]
        islands = [[] for _ in specs]
        serial = 0
        best = None
        for island_id, (assign_name, order_name, geometry_name) in enumerate(specs):
            genome = {"assignment": assignment_policy(problem, eligible, assign_name, island_id),
                      "order": order_for(problem, order_name, island_id),
                      "assignment_policy": assign_name, "order_policy": order_name,
                      "geometry_policy": geometry_name,
                      "geometry_seed": rng.randrange(1, 2**31), "serial": serial, "penalty": 0}
            serial += 1
            sol, result, status, comp = phenotype(problem, genome, deadline, n, hor, attempt_seconds)
            genome.update(solution=sol, objective=(float(result["objective"]) if sol else float("inf")),
                          penalty=0 if sol else 1, components=comp, status=status)
            islands[island_id].append(genome)
            if sol and (best is None or genome["objective"] < best["objective"]):
                best = genome
            if status not in ("feasible", "checker_rejected"):
                receipt["failures"].append({"phase": "initial", "island": island_id, "reason": status})
        # Population size two: elites plus genuinely recombined offspring; migration
        # occurs only as a complete genome, then is reconstructed and checker-gated.
        generation = 0
        while time.monotonic() < deadline:
            generation += 1
            made = False
            for island_id, population in enumerate(islands):
                if time.monotonic() >= deadline:
                    break
                parent_a = min(population, key=lambda g: (g["penalty"], g["objective"], g["serial"]))
                parent_b = max(population, key=lambda g: (g["penalty"], -g["objective"], -g["serial"]))
                genome = child(parent_a, parent_b, eligible, len(problem["bays"]), rng, serial)
                serial += 1
                receipt["offspring"]["attempted"] += 1
                sol, result, status, comp = phenotype(problem, genome, deadline, n, hor, attempt_seconds)
                genome.update(solution=sol, objective=(float(result["objective"]) if sol else float("inf")),
                              penalty=0 if sol else parent_a["penalty"] + 1,
                              components=comp, status=status)
                if sol:
                    receipt["offspring"]["feasible"] += 1
                    if best is None or genome["objective"] < best["objective"]:
                        best = genome
                population.append(genome)
                population.sort(key=lambda g: (g["penalty"], g["objective"], g["serial"]))
                # Keep the elite and the most different realizable alternate.
                elite = population[0]
                alternate = max(population[1:], key=lambda g: (sum(x != y for x, y in zip(elite["assignment"], g["assignment"])) + sum(x != y for x, y in zip(elite["order"], g["order"])), -g["penalty"], -g["objective"])) if len(population) > 1 else elite
                islands[island_id] = [elite, alternate]
                made = True
            if generation % 2 == 0 and time.monotonic() < deadline:
                for island_id in range(len(islands)):
                    source = islands[(island_id - 1) % len(islands)][0]
                    migrant = dict(source, assignment=list(source["assignment"]), order=list(source["order"]),
                                   serial=serial, solution=None, penalty=source["penalty"])
                    serial += 1
                    receipt["migrations"]["attempted"] += 1
                    sol, result, status, comp = phenotype(problem, migrant, deadline, n, hor, attempt_seconds)
                    migrant.update(solution=sol, objective=(float(result["objective"]) if sol else float("inf")),
                                   penalty=0 if sol else source["penalty"] + 1, components=comp, status=status)
                    if sol:
                        receipt["migrations"]["feasible"] += 1
                        if best is None or migrant["objective"] < best["objective"]:
                            best = migrant
                    islands[island_id].append(migrant)
                made = True
            receipt["generations"] = generation
            receipt["island_diversity"].append({"generation": generation,
                "values": [round(diversity(pop, n), 6) for pop in islands]})
            if not made:
                break
        if best is None:
            receipt["fallback_used"] = True
            fallback = M._trivial(problem)
            result = checked(problem, fallback)
            if result is None or not result["feasible"]:
                raise RuntimeError("no checker-certified phenotype or fallback")
            best = {"solution": fallback, "objective": float(result["objective"]),
                    "components": None, "status": "trivial_fallback"}
        # Persist exactly the Engine/checker-gated event sequence.  Reparse it so
        # JSON bucket insertion order is the emitted chronological order, then gate it again.
        if not chronological(best["solution"]):
            raise RuntimeError("Engine emitted non-chronological operation buckets")
        Path(args.out).write_text(json.dumps(best["solution"], separators=(",", ":")) + "\n")
        persisted = json.loads(Path(args.out).read_text())
        if not chronological(persisted):
            raise RuntimeError("persisted operation buckets lost chronological ordering")
        final = checked(problem, persisted)
        if final is None or not final["feasible"]:
            raise RuntimeError("persisted output failed official checker gate")
        receipt["offspring"]["feasible_rate"] = (receipt["offspring"]["feasible"] / receipt["offspring"]["attempted"] if receipt["offspring"]["attempted"] else 0.0)
        receipt["best_objective"] = float(final["objective"])
        receipt["best_components"] = best["components"]
        receipt["best_status"] = best["status"]
        save_receipt()
        return 0
    except Exception as exc:
        receipt["fatal"] = str(exc)
        save_receipt()
        return 2

if __name__ == "__main__":
    sys.exit(main())
