#!/usr/bin/env python3
"""Checker-audit the canonical same-day EXIT order.

For a board whose same-day ENTRY order is topological, the reverse of the
actual ENTRY order is a legal order for peers that also EXIT together: every
later entrant already cleared every earlier resident on the way down, and the
crane geometry is identical on the way up.  Across different entry days the
same argument gives the usual later-entry-first order.

This audit does not score a stochastic solver run.  It rewrites only operation
order on already-feasible boards and asks the pinned official checker whether
the resulting LIFO order is feasible.  It also includes a two-block witness on
which the shipped id tie-break rejects a placement while LIFO is accepted.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import sys


REPO = pathlib.Path(__file__).resolve().parent.parent


def load_module(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def flat_from_solution(prob: dict, solution: dict) -> list[int]:
    n = len(prob["blocks"])
    entries: dict[int, tuple[int, int, int, int, int]] = {}
    exits: dict[int, int] = {}
    for day, events in solution.get("operations", {}).items():
        t = int(day)
        for event in events:
            block = int(event["block_id"])
            if event["type"] == "ENTRY":
                entries[block] = (
                    int(event["bay_id"]), int(event["orient_idx"]),
                    int(event["x"]), int(event["y"]), t,
                )
            elif event["type"] == "EXIT":
                exits[block] = t
    if len(entries) != n or len(exits) != n:
        raise ValueError(f"incomplete solution: entries={len(entries)} exits={len(exits)} n={n}")
    flat: list[int] = []
    for block in range(n):
        bay, ori, x, y, entry = entries[block]
        flat.extend((bay, ori, x, y, entry, exits[block], 1))
    return flat


def lifo_exit_ranks(recs: dict[int, tuple], entry_ranks: list[int]) -> list[int]:
    """Return global per-day ranks preserving LIFO order inside every bay."""
    n = len(recs)
    ranks = [-1] * n
    by_day: dict[int, list[int]] = {}
    for block, rec in recs.items():
        by_day.setdefault(int(rec[5]), []).append(block)
    for ids in by_day.values():
        # Bay is first only to make the audit's order deterministic.  Crane
        # operations in distinct bays commute, so only the per-bay subsequence
        # matters to the checker.
        ids.sort(key=lambda block: (
            int(recs[block][0]), -int(recs[block][4]),
            -int(entry_ranks[block]), block,
        ))
        for rank, block in enumerate(ids):
            ranks[block] = rank
    return ranks


def square(x0: int, y0: int, x1: int, y1: int) -> list[list[int]]:
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def synthetic_problem() -> dict:
    common = {
        "release_time": 1, "due_date": 3, "processing_time": 2,
        "workload": 1, "bay_preferences": [1],
    }
    return {
        # Width 3 leaves exactly one corner for block 1.  This makes the same
        # witness also prove fixed-first fallback semantics: the fixed scan
        # cannot escape to a different normal placement.
        "bays": [{"width": 3, "height": 2}],
        "weights": {"w1": 1, "w2": 1, "w3": 1},
        "blocks": [
            dict(common, shape=[{"orientation": 0,
                                 "layers": [square(0, 0, 1, 1)]}]),
            dict(common, shape=[{"orientation": 0,
                                 "layers": [square(0, 0, 1, 1),
                                            square(2, 0, 3, 1)]}]),
        ],
    }


def audit_synthetic(ma) -> dict:
    prob = synthetic_problem()
    eng = ma._build_engine(prob)
    eng.build_occ(16)
    if not eng.place_at(0, 0, 0, 2, 0, 1):
        raise AssertionError("synthetic first placement failed")
    verdict = list(eng.placement_verdicts(1, 0, 0, 0, 0, 1))
    if verdict != [0, 1]:
        raise AssertionError(f"expected legacy=0 official=1, got {verdict}")
    if not eng.place_at_flexible(1, 0, 0, 0, 0, 1):
        raise AssertionError("synthetic official placement failed")
    recs = ma._extract(eng, 2)
    entry = list(eng.entry_ranks())
    lifo = lifo_exit_ranks(recs, entry)
    checked = ma._check(prob, ma._recs_to_sol(recs, entry, lifo))
    if not checked or not checked.get("feasible"):
        raise AssertionError(f"checker rejected LIFO witness: {checked}")
    # A fresh engine asks the real insertion scan, not the explicit probe.
    # OFF must exhaust the scan and fail; ON must commit the remembered exact
    # candidate only after that same scan is exhausted.
    eng2 = ma._build_engine(prob)
    eng2.build_occ(16)
    if not eng2.place_at(0, 0, 0, 2, 0, 1):
        raise AssertionError("synthetic fallback setup failed")
    inserted = bool(eng2.insert_capped(1, 1, -1))
    treatment = os.environ.get("OGC_EXIT_TWIN") == "1"
    if inserted != treatment:
        raise AssertionError(
            f"fallback mismatch: treatment={treatment} inserted={inserted}")

    return {"legacy_verdict": verdict[0], "official_verdict": verdict[1],
            "entry_ranks": entry, "lifo_exit_ranks": lifo,
            "checker_feasible": True, "treatment": treatment,
            "fallback_inserted": inserted}


def discover_boards(source_repo: pathlib.Path) -> list[tuple[pathlib.Path, pathlib.Path]]:
    pairs: list[tuple[pathlib.Path, pathlib.Path]] = []
    for solution in sorted((REPO / "archive" / "best-boards-2026-08-04").glob("prob_*.sol.json")):
        pid = solution.name.split(".")[0]
        pairs.append((REPO / "data" / "preliminary_test" / f"{pid}.json", solution))
    for dirname in ("shipv6-s7", "shipv6-s11", "shipv61-s7", "v41-s7"):
        for solution in sorted((source_repo / "runs" / dirname).glob("prob_*.sol.json")):
            pid = solution.name.split(".")[0]
            pairs.append((REPO / "data" / "preliminary_test" / f"{pid}.json", solution))
    # De-duplicate physical paths while preserving the deliberately diverse
    # solver/seed corpus.
    seen: set[pathlib.Path] = set()
    out = []
    for pair in pairs:
        key = pair[1].resolve()
        if key not in seen:
            seen.add(key)
            out.append(pair)
    return out


def audit_board(ma, checker, problem_path: pathlib.Path,
                solution_path: pathlib.Path) -> dict:
    prob = json.loads(problem_path.read_text())
    original = json.loads(solution_path.read_text())
    base = checker.check_feasibility(prob, original)
    if not base.get("feasible"):
        raise AssertionError(f"input board is infeasible: {solution_path}: {base}")
    eng = ma._build_engine(prob)
    eng.set_solution(flat_from_solution(prob, original))
    eng.build_occ(eng.horizon())
    n = len(prob["blocks"])
    recs = ma._extract(eng, n)
    entry = list(eng.entry_ranks())
    lifo = lifo_exit_ranks(recs, entry)
    candidate = ma._recs_to_sol(recs, entry, lifo)
    checked = checker.check_feasibility(prob, candidate)
    if not checked.get("feasible"):
        raise AssertionError(f"LIFO failed: {solution_path}: {checked}")
    greedy_fn = getattr(eng, "greedy_exit_ranks", eng.exit_ranks)
    greedy = list(greedy_fn())
    if len(greedy) != n:
        raise AssertionError(f"greedy EXIT oracle found no order: {solution_path}")
    greedy_sol = ma._recs_to_sol(recs, entry, greedy)
    greedy_checked = checker.check_feasibility(prob, greedy_sol)
    if not greedy_checked.get("feasible"):
        raise AssertionError(f"greedy EXIT order failed: {solution_path}: {greedy_checked}")
    return {
        "problem": str(problem_path), "solution": str(solution_path),
        "objective": checked.get("objective"), "n": n,
        "lifo_checker_feasible": True, "greedy_checker_feasible": True,
        "lifo_vs_greedy_rank_differences": sum(a != b for a, b in zip(lifo, greedy)),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("solver_dir", type=pathlib.Path)
    parser.add_argument("--source-repo", type=pathlib.Path, default=REPO)
    parser.add_argument("--output", type=pathlib.Path)
    args = parser.parse_args()

    solver_dir = args.solver_dir.resolve()
    sys.path.insert(0, str(solver_dir))
    import myalgorithm as ma
    checker = load_module(REPO / "solver" / "utils.py", "exit_lifo_official_utils")

    report = {"synthetic": audit_synthetic(ma), "boards": []}
    for problem_path, solution_path in discover_boards(args.source_repo.resolve()):
        row = audit_board(ma, checker, problem_path, solution_path)
        report["boards"].append(row)
        print(f"PASS {solution_path}: obj={row['objective']:.0f} "
              f"rank_diff={row['lifo_vs_greedy_rank_differences']}")
    report["summary"] = {
        "boards": len(report["boards"]),
        "failures": 0,
        "rank_differences": sum(
            row["lifo_vs_greedy_rank_differences"] for row in report["boards"]),
    }
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    print(text)


if __name__ == "__main__":
    main()
