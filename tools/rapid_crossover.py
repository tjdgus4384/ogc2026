#!/usr/bin/env python3
"""Deterministic, checker-gated multi-parent bay-shell crossover.

Usage:
    rapid_crossover.py <problem.json> <seed-list.txt> <out.sol.json> <receipt.json>
        --seconds N --seed S

The seed list is a newline-delimited list of solution JSON files.  This tool
never treats an engine-only board as valid: inputs and children are gated by
the official Python checker.
"""
import argparse
import itertools
import json
import math
import os
import random
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "solver"))
os.chdir(ROOT)
import myalgorithm as M  # noqa: E402


def load_solution(path, n):
    """Parse a board once, retaining both its records and literal event trace."""
    with open(path) as f:
        sol = json.load(f)
    ops = sol.get("operations")
    if not isinstance(ops, dict):
        raise ValueError("solution has no operations object")
    recs, events = {}, {}
    for key, rows in ops.items():
        t = int(key)
        if not isinstance(rows, list):
            raise ValueError("operation day is not a list")
        clean = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("operation is not an object")
            item = dict(row)
            i = int(item["block_id"])
            if not 0 <= i < n:
                raise ValueError("block_id out of range")
            clean.append(item)
            if item.get("type") == "ENTRY":
                if i in recs:
                    raise ValueError("duplicate ENTRY")
                recs[i] = [int(item["bay_id"]), int(item["x"]), int(item["y"]),
                           int(item["orient_idx"]), t, None]
            elif item.get("type") == "EXIT":
                if i not in recs or recs[i][5] is not None:
                    raise ValueError("EXIT without one ENTRY")
                recs[i][5] = t
        events[t] = clean
    if len(recs) != n or any(v[5] is None for v in recs.values()):
        raise ValueError("incomplete block records")
    return sol, recs, events


def traced_shell_solution(parents, sources, n):
    """Merge whole selected bay traces when their shells form an exact cover."""
    owner = {}
    for bay, pi in enumerate(sources):
        for block, rec in parents[pi]["recs"].items():
            if rec[0] != bay:
                continue
            if block in owner:
                return None
            owner[block] = pi
    if len(owner) != n:
        return None
    out = {}
    for t in sorted({t for p in parents for t in p["events"]}):
        rows = []
        # Keep each selected bay's trace in its parent's original row order.
        for bay, pi in enumerate(sources):
            for row in parents[pi]["events"].get(t, []):
                i = int(row["block_id"])
                if owner.get(i) == pi and parents[pi]["recs"][i][0] == bay:
                    rows.append(dict(row))
        if rows:
            out[str(t)] = rows
    return {"operations": out}
def hybrid_shell_solution(parents, sources, provenance, generated, n):
    """Keep selected shell-member traces while filling only missing members anew."""
    copied = set()
    out = {}
    times = sorted({int(t) for p in parents for t in p["events"]} |
                   {int(t) for t in generated["operations"]})
    for t in times:
        rows = []
        for bay, pi in enumerate(sources):
            for row in parents[pi]["events"].get(t, []):
                i = int(row["block_id"])
                if (provenance[i] == pi and parents[pi]["recs"][i][0] == bay):
                    rows.append(dict(row))
                    copied.add(i)
        for row in generated["operations"].get(str(t), []):
            if int(row["block_id"]) not in copied:
                rows.append(dict(row))
        if rows:
            out[str(t)] = rows
    if len(copied) == n:
        return None
    return {"operations": out}




def assignment_for_shells(parents, sources, n):
    """Resolve repeated/missing shell members into one deterministic assignment."""
    occurrences = [[] for _ in range(n)]
    for bay, pi in enumerate(sources):
        for i, rec in parents[pi]["recs"].items():
            if rec[0] == bay:
                occurrences[i].append((pi, bay))
    best_parent = min(range(len(parents)), key=lambda q: (parents[q]["objective"], q))
    assignment, provenance, duplicate, missing = [], [], 0, 0
    for i, hits in enumerate(occurrences):
        if hits:
            # Prefer the lower-objective parent, then the bay id, for duplicate shells.
            pi, bay = min(hits, key=lambda x: (parents[x[0]]["objective"], x[1], x[0]))
            duplicate += len(hits) - 1
            provenance.append(pi)
            assignment.append(bay)
        else:
            missing += 1
            assignment.append(parents[best_parent]["recs"][i][0])
            provenance.append(best_parent)
    return assignment, provenance, duplicate, missing


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("problem")
    ap.add_argument("seed_list")
    ap.add_argument("out")
    ap.add_argument("receipt")
    ap.add_argument("--seconds", type=float, required=True)
    ap.add_argument("--seed", type=int, required=True)
    args = ap.parse_args()
    started = time.monotonic()
    receipt = {"tool": "rapid_crossover", "seed": args.seed, "seconds": args.seconds,
               "parent_provenance": [], "assignment_distance": [],
               "diversity": {}, "selected_bay_sources": [], "children": [],
               "failure_reasons": []}

    def finish(code):
        receipt["elapsed_seconds"] = round(time.monotonic() - started, 6)
        with open(args.receipt, "w") as f:
            json.dump(receipt, f, sort_keys=True, indent=2)
            f.write("\n")
        return code

    try:
        if not math.isfinite(args.seconds) or args.seconds <= 0:
            raise ValueError("--seconds must be a finite positive value")
        with open(args.problem) as f:
            problem = json.load(f)
        n, m = len(problem["blocks"]), len(problem["bays"])
        with open(args.seed_list) as f:
            names = [line.strip() for line in f if line.strip() and not line.lstrip().startswith("#")]
        if len(names) < 2:
            raise ValueError("seed list must contain at least two boards")
        base = os.path.dirname(os.path.abspath(args.seed_list))
        paths, seen = [], set()
        for name in names:
            path = name if os.path.isabs(name) else os.path.normpath(os.path.join(base, name))
            if path not in seen:
                paths.append(path); seen.add(path)
        parents = []
        for path in paths:
            try:
                sol, recs, events = load_solution(path, n)
                check = M._check(problem, sol)
                if check is None:
                    receipt["failure_reasons"].append({"parent": path, "reason": "checker_unavailable"})
                elif not check["feasible"]:
                    receipt["failure_reasons"].append({"parent": path, "reason": "parent_infeasible",
                                                       "violations": check["violations"][:3]})
                else:
                    parents.append({"path": path, "recs": recs, "events": events,
                                    "objective": float(check["objective"])})
            except Exception as exc:
                receipt["failure_reasons"].append({"parent": path, "reason": "parent_parse_error",
                                                   "detail": str(exc)})
        receipt["parent_provenance"] = [{"index": i, "path": p["path"], "objective": p["objective"]}
                                        for i, p in enumerate(parents)]
        if len(parents) < 2:
            receipt["failure_reasons"].append({"reason": "fewer_than_two_checker_feasible_parents"})
            return finish(2)
        best_parent = min(p["objective"] for p in parents)
        for i, p in enumerate(parents):
            for j in range(i):
                different = sum(p["recs"][b][0] != parents[j]["recs"][b][0] for b in range(n))
                receipt["assignment_distance"].append({"parents": [j, i], "blocks": different,
                                                        "fraction": different / n})
        rng = random.Random(args.seed)
        space = len(parents) ** m
        limit = min(64, max(4, int(args.seconds * 2)))
        if space <= limit:
            source_sets = list(itertools.product(range(len(parents)), repeat=m))
        else:
            source_sets, used = [], set()
            while len(source_sets) < limit:
                choice = tuple(rng.randrange(len(parents)) for _ in range(m))
                if choice not in used:
                    used.add(choice); source_sets.append(choice)
        # Parent-only vectors are not children and must never be emitted.
        source_sets = [s for s in source_sets if len(set(s)) > 1]
        rng.shuffle(source_sets)
        receipt["diversity"] = {"feasible_parent_count": len(parents), "source_combinations": len(source_sets),
                                "assignment_pair_count": len(receipt["assignment_distance"])}
        horizon = max(int(round(b["due_date"])) for b in problem["blocks"]) + max(
            max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in problem["blocks"]) + 40
        engine = M._build_engine(problem)
        if engine is None:
            receipt["failure_reasons"].append({"reason": "production_engine_unavailable"})
            return finish(2)
        engine.build_occ(horizon)
        deadline = started + args.seconds
        best = None
        for ci, sources in enumerate(source_sets):
            if time.monotonic() >= deadline:
                receipt["failure_reasons"].append({"reason": "time_budget_exhausted"})
                break
            assignment, provenance, duplicate, missing = assignment_for_shells(parents, sources, n)
            descriptor = {"index": ci, "sources": list(sources), "unique_sources": len(set(sources)),
                          "duplicate_shell_members": duplicate, "missing_shell_members": missing}
            receipt["selected_bay_sources"].append(descriptor)
            # Exact-cover shell combinations retain literal parent event traces.
            traced = traced_shell_solution(parents, sources, n)
            if traced is not None:
                check = M._check(problem, traced)
                child = {**descriptor, "kind": "exact_cover_trace",
                         "checker_feasible": None if check is None else check["feasible"],
                         "objective": None if check is None else check.get("objective")}
                receipt["children"].append(child)
                if check is not None and check["feasible"] and check["objective"] < best_parent:
                    if best is None or check["objective"] < best[0]:
                        best = (check["objective"], traced, child)
                continue
            # Assignment-aware production reconstruction resolves non-exact shells.
            order = sorted(range(n), key=lambda i: (parents[provenance[i]]["recs"][i][4], i))
            engine.set_solution([0] * (n * 7)); engine.build_occ(horizon)
            remaining = max(0.001, deadline - time.monotonic())
            status = engine.reconstruct_ordered(assignment, order, False, 7 + args.seed + ci, remaining)
            if status != -1:
                receipt["children"].append({**descriptor, "kind": "reconstructed",
                                             "checker_feasible": False, "objective": None,
                                             "failure": "engine_reconstruction_failed"})
                continue
            generated = M._recs_to_sol(M._extract(engine, n), engine.ordered_events())
            # A reconstructed child retains every non-conflicting copied shell's
            # literal event sequence; only missing/duplicate-resolved members
            # receive the production engine's reconstructed trace.
            child_sol = hybrid_shell_solution(parents, sources, provenance, generated, n)
            if child_sol is not None:
                check = M._check(problem, child_sol)
                child = {**descriptor, "kind": "reconstructed_with_shell_traces",
                         "checker_feasible": None if check is None else check["feasible"],
                         "objective": None if check is None else check.get("objective")}
                if check is not None and not check["feasible"]:
                    child["failure"] = "official_checker_rejected"
                    child["violations"] = check["violations"][:3]
                receipt["children"].append(child)
                if check is not None and check["feasible"] and check["objective"] < best_parent:
                    if best is None or check["objective"] < best[0]:
                        best = (check["objective"], child_sol, child)
            check = M._check(problem, generated)
            child = {**descriptor, "kind": "reconstructed",
                     "checker_feasible": None if check is None else check["feasible"],
                     "objective": None if check is None else check.get("objective")}
            if check is not None and not check["feasible"]:
                child["failure"] = "official_checker_rejected"
                child["violations"] = check["violations"][:3]
            receipt["children"].append(child)
            if check is not None and check["feasible"] and check["objective"] < best_parent:
                if best is None or check["objective"] < best[0]:
                    best = (check["objective"], generated, child)
        if best is None:
            receipt["child_objective"] = None
            receipt["failure_reasons"].append({"reason": "no_checker_feasible_raw_objective_improvement"})
            return finish(1)
        receipt["child_objective"] = best[0]
        receipt["winning_child"] = best[2]
        with open(args.out, "w") as f:
            json.dump(best[1], f)
            f.write("\n")
        return finish(0)
    except Exception as exc:
        receipt["failure_reasons"].append({"reason": "fatal", "detail": str(exc)})
        return finish(2)


if __name__ == "__main__":
    sys.exit(main())
