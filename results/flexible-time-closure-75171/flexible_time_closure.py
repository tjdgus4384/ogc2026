#!/usr/bin/env python3
"""Exact fixed-geometry timing audit with the official flexible stay rule.

The historical ``exsched.py`` experiment fixed ``exit = entry + processing``.
That removes the one-way crane-nesting schedules allowed by the official rule
``exit - entry >= processing``.  This tool freezes only the geometric shell
(bay, orientation, x, y), derives every pair relation with the official
checker geometry, and solves two deterministic timing models:

``fixed``
    ``exit = entry + processing`` (the historical restricted model).

``flex``
    ``exit - entry >= processing`` (the official duration freedom).

For a one-way pair where resident ``inner`` obstructs moving ``outer``, the
three legal alternatives are outer-before-inner, inner-before-outer, or
``inner`` time-nested inside ``outer``.  A same-day entry rank makes the last
alternative strict at the operation level and rejects directed entry cycles.
Exits are emitted in reverse entry-key order, the legal LIFO unwind.  Every CP
candidate is finally checked by ``solver/utils.py``; CP values alone are never
reported as official-feasible evidence.

This is a ceiling/audit for a *fixed* geometric shell.  A zero result does not
refute a repair that changes suffix geometry after an obstruction closure.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "solver"))

from ortools.sat.python import cp_model  # noqa: E402
import utils  # noqa: E402


def _load(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _records(problem: dict[str, Any], solution: dict[str, Any]) -> list[dict[str, int]]:
    n = len(problem["blocks"])
    recs: list[dict[str, int] | None] = [None] * n
    exits: list[int | None] = [None] * n
    entry_rank = [0] * n

    for t_text in sorted(solution.get("operations", {}), key=lambda x: int(x)):
        t = int(t_text)
        phase_rank = {"EXIT": 0, "ENTRY": 0}
        for op in solution["operations"][t_text]:
            kind = op.get("type")
            if kind not in phase_rank:
                raise ValueError(f"unknown operation type {kind!r} at t={t}")
            block = int(op["block_id"])
            if not 0 <= block < n:
                raise ValueError(f"block id {block} out of range")
            if kind == "ENTRY":
                if recs[block] is not None:
                    raise ValueError(f"duplicate ENTRY for block {block}")
                recs[block] = {
                    "bay": int(op["bay_id"]),
                    "ori": int(op["orient_idx"]),
                    "x": int(round(op["x"])),
                    "y": int(round(op["y"])),
                    "e": t,
                    "f": -1,
                }
                entry_rank[block] = phase_rank[kind]
            else:
                if exits[block] is not None:
                    raise ValueError(f"duplicate EXIT for block {block}")
                exits[block] = t
            phase_rank[kind] += 1

    out: list[dict[str, int]] = []
    for block in range(n):
        rec = recs[block]
        if rec is None or exits[block] is None:
            raise ValueError(f"block {block} does not have exactly one ENTRY and EXIT")
        rec["f"] = int(exits[block])
        rec["rank"] = entry_rank[block]
        out.append(rec)
    return out


def _pair_relations(problem: dict[str, Any], recs: list[dict[str, int]]) -> tuple[list[tuple[int, int]], list[tuple[int, int, int]]]:
    """Return hard-disjoint pairs and one-way (outer, inner, bay) pairs."""

    by_bay: list[list[int]] = [[] for _ in problem["bays"]]
    blocks: list[utils.Block] = []
    for i, rec in enumerate(recs):
        by_bay[rec["bay"]].append(i)
        blocks.append(utils.Block(
            block_id=i,
            block_data=problem["blocks"][i],
            x=rec["x"],
            y=rec["y"],
            orient_idx=rec["ori"],
        ))

    disjoint: list[tuple[int, int]] = []
    nested: list[tuple[int, int, int]] = []
    for bay_id, ids in enumerate(by_bay):
        bay = utils.Bay.from_dict(problem["bays"][bay_id], bay_id)
        for aa in range(len(ids)):
            i = ids[aa]
            if not bay.contains_block(blocks[i]):
                raise ValueError(f"fixed block {i} is outside bay {bay_id}")
            for bb in range(aa + 1, len(ids)):
                j = ids[bb]
                rest = bool(utils.check_collisions(bay, [blocks[i], blocks[j]]))
                # b_ij: j obstructs moving i.  Containment was checked above,
                # so a non-empty result here is a genuine pair obstruction.
                b_ij = bool(utils.check_entry(
                    bay, [blocks[j]], blocks[i], fast=True))
                b_ji = bool(utils.check_entry(
                    bay, [blocks[i]], blocks[j], fast=True))
                if rest or (b_ij and b_ji):
                    disjoint.append((i, j))
                elif b_ij:
                    nested.append((i, j, bay_id))  # j is inner in i
                elif b_ji:
                    nested.append((j, i, bay_id))  # i is inner in j
    return disjoint, nested


def _add_or(model: cp_model.CpModel, clauses: list[tuple[cp_model.IntVar, str, cp_model.IntVar]]) -> None:
    choices = [model.NewBoolVar("") for _ in clauses]
    model.AddBoolOr(choices)
    for choose, (lhs, sense, rhs) in zip(choices, clauses):
        if sense == "le":
            model.Add(lhs <= rhs).OnlyEnforceIf(choose)
        elif sense == "lt":
            model.Add(lhs < rhs).OnlyEnforceIf(choose)
        else:  # pragma: no cover - internal invariant
            raise AssertionError(sense)


def _build_model(
    problem: dict[str, Any],
    recs: list[dict[str, int]],
    disjoint: list[tuple[int, int]],
    nested: list[tuple[int, int, int]],
    flexible: bool,
) -> tuple[cp_model.CpModel, dict[str, Any]]:
    n = len(recs)
    proc = [max(1, int(math.ceil(float(b["processing_time"]) - 1e-9)))
            for b in problem["blocks"]]
    release = [int(math.ceil(float(b["release_time"]) - 1e-9))
               for b in problem["blocks"]]
    due = [int(round(float(b["due_date"]))) for b in problem["blocks"]]

    # Serialising every bay after the latest release is a constructive upper
    # bound, independent of public-instance scale or a fitted slack constant.
    horizon = max(release) + sum(proc)
    rank_base = n + 1
    model = cp_model.CpModel()
    entry = [model.NewIntVar(release[i], horizon - proc[i], f"e{i}")
             for i in range(n)]
    exit_ = [model.NewIntVar(release[i] + proc[i], horizon, f"f{i}")
             for i in range(n)]
    rank = [model.NewIntVar(0, n - 1, f"r{i}") for i in range(n)]
    key = [model.NewIntVar(release[i] * rank_base,
                           horizon * rank_base + n, f"k{i}")
           for i in range(n)]
    for i in range(n):
        if flexible:
            model.Add(exit_[i] - entry[i] >= proc[i])
        else:
            model.Add(exit_[i] == entry[i] + proc[i])
        model.Add(key[i] == entry[i] * rank_base + rank[i])
    model.AddAllDifferent(key)

    # Same-layer collision or mutual crane obstruction: coexistence is
    # impossible, but equality is legal because EXIT precedes ENTRY.
    for i, j in disjoint:
        _add_or(model, [(exit_[i], "le", entry[j]),
                        (exit_[j], "le", entry[i])])

    # `inner` obstructs moving `outer`.  Coexistence is legal only when inner
    # is time-nested inside outer.  The key inequality chooses the same-day
    # ENTRY order and makes a directed same-day cycle infeasible.
    for outer, inner, _bay in nested:
        choices = [model.NewBoolVar("") for _ in range(3)]
        model.AddBoolOr(choices)
        model.Add(exit_[outer] <= entry[inner]).OnlyEnforceIf(choices[0])
        model.Add(exit_[inner] <= entry[outer]).OnlyEnforceIf(choices[1])
        model.Add(entry[outer] <= entry[inner]).OnlyEnforceIf(choices[2])
        model.Add(exit_[inner] <= exit_[outer]).OnlyEnforceIf(choices[2])
        model.Add(key[outer] < key[inner]).OnlyEnforceIf(choices[2])

    tardy = [model.NewIntVar(0, horizon, f"t{i}") for i in range(n)]
    for i in range(n):
        model.Add(tardy[i] >= exit_[i] - due[i])
    model.Minimize(sum(tardy))

    # The checker-valid input is a useful deterministic incumbent hint for the
    # flexible model and usually for the fixed model as well.
    for i, rec in enumerate(recs):
        model.AddHint(entry[i], rec["e"])
        model.AddHint(exit_[i], rec["f"] if flexible else rec["e"] + proc[i])
        model.AddHint(rank[i], min(n - 1, rec["rank"]))

    return model, {
        "entry": entry,
        "exit": exit_,
        "rank": rank,
        "key": key,
        "proc": proc,
        "horizon": horizon,
    }


def _emit(recs: list[dict[str, int]], entry: list[int], exit_: list[int],
          key: list[int]) -> dict[str, Any]:
    operations: dict[str, list[dict[str, int | str]]] = {}
    times = sorted(set(entry) | set(exit_))
    for t in times:
        ops: list[dict[str, int | str]] = []
        # Reverse entry key is the exact LIFO unwind for nested one-way pairs.
        for i in sorted((q for q, f in enumerate(exit_) if f == t),
                        key=lambda q: (-key[q], q)):
            ops.append({"type": "EXIT", "block_id": i,
                        "bay_id": recs[i]["bay"]})
        for i in sorted((q for q, e in enumerate(entry) if e == t),
                        key=lambda q: (key[q], q)):
            rec = recs[i]
            ops.append({"type": "ENTRY", "block_id": i,
                        "bay_id": rec["bay"], "x": rec["x"],
                        "y": rec["y"], "orient_idx": rec["ori"]})
        operations[str(t)] = ops
    return {"operations": operations}


def _solve(
    problem: dict[str, Any],
    recs: list[dict[str, int]],
    disjoint: list[tuple[int, int]],
    nested: list[tuple[int, int, int]],
    flexible: bool,
    seconds: float,
    seed: int,
    checker_rounds: int,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    model, var = _build_model(problem, recs, disjoint, nested, flexible)
    started = time.monotonic()
    last: dict[str, Any] = {}
    verified_solution: dict[str, Any] | None = None
    for attempt in range(max(1, checker_rounds)):
        remaining = seconds - (time.monotonic() - started)
        if remaining <= 0.05:
            break
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = remaining
        solver.parameters.num_search_workers = 1
        solver.parameters.random_seed = seed
        solver.parameters.log_search_progress = False
        status = solver.Solve(model)
        status_name = solver.StatusName(status)
        last = {
            "status": status_name,
            "elapsed": time.monotonic() - started,
            "branches": solver.NumBranches(),
            "conflicts": solver.NumConflicts(),
            "best_bound_z1": (solver.BestObjectiveBound()
                              if status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
                              else None),
            "checker_attempt": attempt + 1,
        }
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            break

        entry = [solver.Value(v) for v in var["entry"]]
        exit_ = [solver.Value(v) for v in var["exit"]]
        key = [solver.Value(v) for v in var["key"]]
        candidate = _emit(recs, entry, exit_, key)
        checked = utils.check_feasibility(problem, candidate)
        extra = [exit_[i] - entry[i] - var["proc"][i]
                 for i in range(len(recs))]
        last.update({
            "cp_z1": solver.ObjectiveValue(),
            "checker": checked,
            "extended_blocks": sum(v > 0 for v in extra),
            "total_extra_days": sum(extra),
            "max_extra_days": max(extra, default=0),
        })
        if checked.get("feasible"):
            verified_solution = candidate
            break

        # Defensive exact candidate no-good.  The pair/event model is intended
        # to match the checker; any rejection remains visible in the report and
        # can never become evidence merely because CP liked it.
        values = entry + exit_ + key
        variables = var["entry"] + var["exit"] + var["key"]
        model.AddForbiddenAssignments(variables, [values])

    last["elapsed"] = time.monotonic() - started
    return last, verified_solution


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("problem", type=Path)
    parser.add_argument("solution", type=Path)
    parser.add_argument("--seconds", type=float, default=60.0,
                        help="deterministic one-worker limit per model")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--checker-rounds", type=int, default=3)
    parser.add_argument("--models", nargs="+", choices=("fixed", "flex"),
                        default=("fixed", "flex"))
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    problem = _load(args.problem)
    source_solution = _load(args.solution)
    source_check = utils.check_feasibility(problem, source_solution)
    if not source_check.get("feasible"):
        raise SystemExit(json.dumps({
            "error": "input solution is not checker-feasible",
            "checker": source_check,
        }))

    recs = _records(problem, source_solution)
    relation_started = time.monotonic()
    disjoint, nested = _pair_relations(problem, recs)
    relation_elapsed = time.monotonic() - relation_started
    report: dict[str, Any] = {
        "schema_version": 1,
        "problem": str(args.problem.resolve()),
        "solution": str(args.solution.resolve()),
        "input_checker": source_check,
        "n": len(recs),
        "relation_seconds": relation_elapsed,
        "hard_disjoint_pairs": len(disjoint),
        "one_way_pairs": len(nested),
        "models": {},
    }

    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
    for name in args.models:
        result, candidate = _solve(
            problem, recs, disjoint, nested,
            flexible=(name == "flex"), seconds=args.seconds,
            seed=args.seed, checker_rounds=args.checker_rounds,
        )
        if candidate is not None and args.output_dir:
            out = args.output_dir / f"{args.solution.stem}-{name}.sol.json"
            with out.open("w", encoding="utf-8") as fh:
                json.dump(candidate, fh, separators=(",", ":"))
            result["candidate_path"] = str(out.resolve())
        report["models"][name] = result

    if args.output_dir:
        summary = args.output_dir / f"{args.solution.stem}-summary.json"
        with summary.open("w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, sort_keys=True)
            fh.write("\n")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
