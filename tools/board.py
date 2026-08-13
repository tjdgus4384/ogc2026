#!/usr/bin/env python3
"""Cached solver boards. Compute once, probe many times.

Every structural probe written today -- blocked, fragmargin, liftk, tslice,
basin, force -- began by running the solver at the official TL to obtain the
board it then analysed. That is 480-900s of setup for a few seconds of probing,
paid again on every rerun and every parameter tweak. The board is a pure
function of (instance, TL, seed, solver dir) up to machine speed, so cache it.

    from board import get_board
    base, D, P, eng, hz = get_board("prob_5", 600, 7)

`base` is the flat 7-per-block record array the engine uses, so a probe can
`eng.set_solution(base)` and work immediately. Cache lives in runs/boards/ and
is keyed by everything that changes the answer, including the solver's own
mtime, so a rebuilt kernel invalidates it automatically.

    python3 tools/board.py warm prob_4 480 7 11 13     # precompute in parallel
    python3 tools/board.py list
"""
import hashlib
import json
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
CACHE = REPO / "runs" / "boards"


def _key(prob_id, tl, seed, solver_dir):
    so = pathlib.Path(solver_dir) / "sscore.cpython-312-x86_64-linux-gnu.so"
    stamp = f"{so.stat().st_mtime_ns}" if so.exists() else "noso"
    h = hashlib.sha256(f"{solver_dir}|{stamp}".encode()).hexdigest()[:12]
    return f"{prob_id}_tl{int(tl)}_s{seed}_{h}.json"


def get_board(prob_id, tl, seed=7, solver_dir=None, dataset=None):
    """(base, D, P, eng, hz) -- the solver's board, computed once and cached."""
    solver_dir = solver_dir or str(REPO / "solver")
    if dataset is None:
        for ds in ("preliminary_test", "train_final", "train", "mini"):
            if (REPO / "data" / ds / f"{prob_id}.json").exists():
                dataset = ds
                break
    prob = json.load(open(REPO / "data" / dataset / f"{prob_id}.json"))
    if solver_dir not in sys.path:
        sys.path.insert(0, solver_dir)
    import myalgorithm as ma
    assert ma._PSEED == seed, (
        f"OGC_SEED must be set before import (got _PSEED={ma._PSEED}, want {seed})")
    n = len(prob["blocks"])
    eng, D, P = ma._fresh(prob, 4)
    hz = eng.horizon()

    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / _key(prob_id, tl, seed, solver_dir)
    if path.exists():
        base = json.loads(path.read_text())["base"]
    else:
        sol = ma.algorithm(prob, timelimit=tl)
        base = [0] * (n * 7)
        for day, ops in sol["operations"].items():
            for op in ops:
                if op["type"] == "ENTRY":
                    i = op["block_id"]
                    base[i * 7:i * 7 + 7] = [op["bay_id"], op["orient_idx"],
                                             op["x"], op["y"], int(day),
                                             int(day) + P[i], 1]
        r = ma._check(prob, sol)
        path.write_text(json.dumps({"base": base, "prob": prob_id, "tl": tl,
                                    "seed": seed, "solver": solver_dir,
                                    "objective": r.get("objective"),
                                    "feasible": r.get("feasible")}))
    eng.set_solution(base)
    eng.build_occ(hz)
    return base, D, P, eng, hz, prob


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    if sys.argv[1] == "list":
        CACHE.mkdir(parents=True, exist_ok=True)
        for f in sorted(CACHE.glob("*.json")):
            d = json.loads(f.read_text())
            print(f"{d['prob']:<10s} TL{d['tl']:<5.0f} s{d['seed']:<4d}"
                  f" {d.get('objective', 0):>14,.0f}  {f.name}")
        return
    if sys.argv[1] == "warm":
        prob_id, tl = sys.argv[2], float(sys.argv[3])
        seed = int(sys.argv[4])
        _, _, _, _, _, _ = get_board(prob_id, tl, seed)
        print(f"warm: {prob_id} TL{tl:.0f} s{seed}")
        return
    sys.exit(__doc__)


if __name__ == "__main__":
    main()
