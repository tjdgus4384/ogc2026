"""Per-bay parallel deep re-optimization (exact decomposition).

With the bay assignment FIXED the objective decomposes exactly: Z2/Z3
are functions of the assignment alone (constants) and Z1 is a sum of
per-block tardiness, while every checker rule is within-bay.  So each
bay is an independent instance, and merging per-bay boards is valid.

This runs the measured-best navigator (the ALNS portfolio) on those
sub-instances IN PARALLEL: m problems of size n/m, each getting the
full remaining wall-clock on its own core.  Effective search depth per
bay rises by roughly m (smaller space) x m (no shared budget), which is
the multiplier the long-run ladder showed is worth 3-18%.

Strictly additive: returns None on any failure; caller keeps its board.
"""
import json
import math
import multiprocessing as mp
import os
import time


def _sub_problem(p, blocks, bay):
    """Slice the instance to one bay and its blocks; pure-Z1 weights."""
    return {
        "name": f"{p.get('name', 'sub')}_b{bay}",
        "bays": [p["bays"][bay]],
        "blocks": [{
            "release_time": p["blocks"][i]["release_time"],
            "due_date": p["blocks"][i]["due_date"],
            "processing_time": p["blocks"][i]["processing_time"],
            "workload": p["blocks"][i]["workload"],
            "bay_preferences": [100],
            "shape": p["blocks"][i]["shape"],
        } for i in blocks],
        "weights": {"w1": p["weights"]["w1"], "w2": 0, "w3": 0},
    }


def _run_bay(args):
    """Child process: anneal one bay sub-instance from its slice of the
    parent board. Returns (bay, [(local_idx, ori, x, y, e)...]) or None."""
    (sub, start_rows, seconds, seed, bay, solver_dir) = args
    try:
        import sys
        if solver_dir not in sys.path:
            sys.path.insert(0, solver_dir)
        import myalgorithm as M
        nb = len(sub["blocks"])
        P = [max(1, int(math.ceil(b["processing_time"] - 1e-9)))
             for b in sub["blocks"]]
        H = (max(int(round(b["due_date"])) for b in sub["blocks"])
             + max(P) + 40)
        eng = M._build_engine(sub)
        eng.build_occ(H)
        flat = [0, 0, 0, 0, 0, 0, 0] * nb
        for (k, o_, x_, y_, e_) in start_rows:
            flat[k * 7:k * 7 + 7] = [0, o_, x_, y_, e_, e_ + P[k], 1]
        eng.set_solution(flat)
        eng.build_occ(H)
        base = eng.objective()[3]
        eng.run_portfolio(1, seconds, 1, 0.05, 0.15, 0.002, 0.9995,
                          seed, 1, [eng.get_solution()], 0)
        got = eng.objective()[3]
        if got >= base - 1e-9:
            return (bay, None, base, got)
        fl = eng.get_solution()
        rows = []
        for k in range(nb):
            if not fl[k * 7 + 6]:
                return (bay, None, base, got)
            rows.append((k, fl[k * 7 + 1], fl[k * 7 + 2],
                         fl[k * 7 + 3], fl[k * 7 + 4]))
        return (bay, rows, base, got)
    except Exception:
        return (bay, None, 0.0, 0.0)


def optimize(p, best_flat, deadline, seed=7, diag=None,
             solver_dir=None):
    t0 = time.time()
    n = len(p["blocks"])
    P = [max(1, int(math.ceil(b["processing_time"] - 1e-9)))
         for b in p["blocks"]]
    if solver_dir is None:
        solver_dir = os.path.dirname(os.path.abspath(__file__))
    by_bay = {}
    for i in range(n):
        if not best_flat[i * 7 + 6]:
            return None
        by_bay.setdefault(best_flat[i * 7], []).append(i)
    if len(by_bay) < 2:
        return None
    secs = deadline - time.time()
    if secs < 20.0:
        return None
    args = []
    for bay, blocks in sorted(by_bay.items()):
        sub = _sub_problem(p, blocks, bay)
        rows = [(k, best_flat[i * 7 + 1], best_flat[i * 7 + 2],
                 best_flat[i * 7 + 3], best_flat[i * 7 + 4])
                for k, i in enumerate(blocks)]
        args.append((sub, rows, secs, seed + bay, bay, solver_dir))
    try:
        with mp.get_context("fork").Pool(len(args)) as pool:
            res = pool.map(_run_bay, args)
    except Exception:
        return None
    out = list(best_flat)
    nimp = 0
    gains = []
    for (bay, rows, base, got) in res:
        blocks = sorted(by_bay[bay])
        gains.append((bay, round(base), round(got)))
        if not rows:
            continue
        nimp += 1
        for (k, o_, x_, y_, e_) in rows:
            i = blocks[k]
            out[i * 7:i * 7 + 7] = [bay, o_, x_, y_, e_,
                                    e_ + P[i], 1]
    if diag:
        diag("baysplit", bays=len(args), improved=nimp,
             gains=gains, wall=round(time.time() - t0, 1))
    return out if nimp else None
