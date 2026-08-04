"""Partial-board beam search -- does BUILDING the board better help at all?

Our architecture is greedy-construct then destroy/repair, so nothing ever
builds a board well; the whole budget goes into swapping 3-6% of an
already-committed structure. This probes the other end: keep W partial
BOARDS alive, extend each by placing the next block in one of K
positions, and keep the W cheapest partial boards.

Why this is not the refuted bay-beam (07-31): that one searched ORDER
space and decoded with a greedy placer, and the decoder could not even
reproduce the incumbent's own board from the incumbent's own order
(+228% on P4). Here the state IS the partial board -- every board is
representable, and the only approximation is the beam width.

Kill rule: if beam construction does not beat greedy construction at the
same point (both measured before any annealing), then construction is
not the bottleneck and M2/M3 die together.

  beam.py <prob.json> <out.json> <width> <cands> [order]
"""
import json
import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "solver"))
os.chdir(ROOT)
import myalgorithm as M  # noqa: E402

prob_path, out_path = sys.argv[1], sys.argv[2]
WIDTH = int(sys.argv[3]) if len(sys.argv) > 3 else 8
KCAND = int(sys.argv[4]) if len(sys.argv) > 4 else 6
ORDER = sys.argv[5] if len(sys.argv) > 5 else "due"

p = json.load(open(prob_path))
B, bays, W = p["blocks"], p["bays"], p["weights"]
n, m = len(B), len(bays)
D = [int(round(b["due_date"])) for b in B]
P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
L = [float(b["workload"]) for b in B]
PREF = [[float(v) for v in b["bay_preferences"]] for b in B]
SMAX = [max(pr) for pr in PREF]
w1, w2, w3 = float(W["w1"]), float(W["w2"]), float(W["w3"])
ar = [b["width"] * b["height"] for b in bays]
u = [(sum(ar) / m) / a for a in ar]
HOR = max(D) + max(P) + 40

if ORDER == "due":
    seq = sorted(range(n), key=lambda i: (D[i] - P[i], -L[i]))
elif ORDER == "big":
    seq = sorted(range(n), key=lambda i: -L[i])
else:
    seq = sorted(range(n), key=lambda i: (D[i] - P[i]) - 0.5 * L[i])

eng = M._build_engine(p)
eng.build_occ(HOR)


def z2_of(load):
    v = [u[j] * load[j] for j in range(m)]
    return math.floor(max(v) - min(v))


def cost(flat, load):
    t = sum(max(0, flat[i*7+5] - D[i]) for i in range(n) if flat[i*7+6])
    r = sum(SMAX[i] - PREF[i][flat[i*7]] for i in range(n) if flat[i*7+6])
    return w1 * t + w3 * r + w2 * z2_of(load)


t0 = time.time()
beam = [([0] * (n * 7), [0.0] * m)]
for step, i in enumerate(seq):
    nxt = []
    for flat, load in beam:
        eng.set_solution(flat)
        eng.build_occ(HOR)
        raw = eng.feasible_positions(i, KCAND, 0)
        cands = [tuple(raw[q:q+5]) for q in range(0, len(raw), 5)]
        if not cands:
            nxt.append((flat, load))          # carry it forward unplaced
            continue
        for (j, oi, xx, yy, ee) in cands:
            f2 = list(flat)
            f2[i*7:i*7+7] = [j, oi, xx, yy, ee, ee + P[i], 1]
            l2 = list(load); l2[j] += L[i]
            nxt.append((f2, l2))
    nxt.sort(key=lambda s: cost(s[0], s[1]))
    # de-duplicate identical partial boards before truncating the beam
    seen, keep = set(), []
    for st in nxt:
        k = tuple(st[0])
        if k in seen:
            continue
        seen.add(k); keep.append(st)
        if len(keep) >= WIDTH:
            break
    beam = keep

best = min(beam, key=lambda s: cost(s[0], s[1]))
eng.set_solution(best[0]); eng.build_occ(HOR)
placed = sum(1 for i in range(n) if best[0][i*7+6])
res = {"board": os.path.basename(prob_path), "width": WIDTH, "cands": KCAND,
       "order": ORDER, "n": n, "placed": placed,
       "wall": round(time.time() - t0, 1),
       "beam_obj": eng.objective()[3] if placed == n else None}

# baseline: the engine's own greedy construction, same order, no annealing
eng.set_solution([0] * (n * 7)); eng.build_occ(HOR)
rc = eng.reconstruct_ordered([-1] * n, seq, False, 7)
res["greedy_ok"] = (rc == -1)
if rc == -1:
    res["greedy_obj"] = eng.objective()[3]
    if res["beam_obj"]:
        res["beam_gain_pct"] = round(
            100 * (res["greedy_obj"] - res["beam_obj"]) / res["greedy_obj"], 2)
if res.get("beam_obj") and out_path != "/dev/null":
    eng.set_solution(best[0]); eng.build_occ(HOR)
    sol = M._recs_to_sol(M._extract(eng, n))
    chk = M._check(p, sol)
    res["checker_feasible"] = None if chk is None else bool(chk.get("feasible"))
    if chk is not None and chk.get("feasible"):
        json.dump(sol, open(out_path, "w"))
print(json.dumps(res))
