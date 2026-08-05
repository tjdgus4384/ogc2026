"""Why is a tardy block tardy? Re-place it alone against the FINAL board.

For each tardy block on a shipped board: remove it, then scan every day from
its release day up to its realised entry and ask the kernel's own exact
placement test whether ANY legal (bay, orientation, position) existed there.

  earlier-legal  -> the search left a free, legal, earlier slot on the table
                    (a search failure, recoverable without moving anyone else)
  locked         -> no single-block move helps; only a joint rearrangement of
                    other blocks could have pulled this entry earlier

Usage: python tools/tardyprobe.py <prob_id> <sol_tag>
Emits one JSON line; no solver state is kept.
"""
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "solver"))
os.chdir(ROOT)
import myalgorithm as M  # noqa: E402

pid = int(sys.argv[1])
tag = sys.argv[2]

p = json.load(open(f"data/preliminary_test/prob_{pid}.json"))
ops = json.load(open(f"runs/{tag}/prob_{pid}.sol.json"))["operations"]
B = p["blocks"]
n = len(B)
P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
D = [int(round(b["due_date"])) for b in B]
R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]

flat = [0] * (n * 7)
for t, day in ops.items():
    for op in day:
        if op["type"] != "ENTRY":
            continue
        i = op["block_id"]
        flat[i * 7 + 0] = op["bay_id"]
        flat[i * 7 + 1] = op["orient_idx"]
        flat[i * 7 + 2] = int(op["x"])
        flat[i * 7 + 3] = int(op["y"])
        flat[i * 7 + 4] = int(t)
        flat[i * 7 + 5] = int(t) + P[i]
        flat[i * 7 + 6] = 1

eng = M._build_engine(p)
hor = max(D) + max(P) + 40
eng.build_occ(hor)
eng.set_solution(flat)
eng.build_occ(hor)

tardy = [i for i in range(n) if flat[i * 7 + 5] > D[i]]
earlier = []
locked = []
recovered_days = 0
for i in tardy:
    entry = flat[i * 7 + 4]
    snap = eng.get_solution()
    eng.set_block(i, 0, 0, 0, 0, 0, 0, False)
    eng.finalize_load()
    eng.build_occ(hor)
    best = None
    for e in range(R[i], entry):
        got = eng.feasible_positions(i, 1, e)
        if got and got[4] == e:
            best = e
            break
    eng.set_solution(snap)
    eng.build_occ(hor)
    if best is None:
        locked.append(i)
    else:
        earlier.append(i)
        recovered_days += min(entry - best, flat[i * 7 + 5] - D[i])

print(json.dumps({
    "prob": pid,
    "tag": tag,
    "n": n,
    "tardy": len(tardy),
    "tardy_days": sum(flat[i * 7 + 5] - D[i] for i in tardy),
    "single_move_earlier": len(earlier),
    "locked": len(locked),
    "recoverable_tardy_days_upper": recovered_days,
}))
