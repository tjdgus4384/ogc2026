"""Why do exact-gain assignment moves fail? (campaign 2 diagnostic)

The exact-delta descent finds candidates worth up to -8.7% of the P3
objective in ONE relocate, and accepts none. Two possible causes, with
opposite consequences:

  (a) the engine cannot PLACE the mover in the target bay at a punctual
      entry (move_block returns false) -> the lever is room-making:
      re-pack the destination bay so the move becomes feasible;
  (b) the move places but the FULL objective worsens (Z1 rises) -> the
      trade is real and already priced by the anneal -> axis dead.

Reports the split over the top candidates, with per-candidate deltas.
Usage: assign_diag.py <prob.json> <sol.json> [topN]
"""
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "solver"))
os.chdir(ROOT)
import myalgorithm as M  # noqa: E402

prob_path, sol_path = sys.argv[1], sys.argv[2]
TOP = int(sys.argv[3]) if len(sys.argv) > 3 else 25

p = json.load(open(prob_path))
ops = json.load(open(sol_path))["operations"]
B, bays, W = p["blocks"], p["bays"], p["weights"]
n, m = len(B), len(bays)
D = [int(round(b["due_date"])) for b in B]
P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]

flat = [0] * (n * 7)
for t, day in ops.items():
    for op in day:
        i = op["block_id"]
        if op["type"] == "ENTRY":
            flat[i*7:i*7+5] = [op["bay_id"], op["orient_idx"], op["x"],
                               op["y"], int(t)]
            flat[i*7+6] = 1
        else:
            flat[i*7+5] = int(t)

L = [float(b["workload"]) for b in B]
pref = [b["bay_preferences"] for b in B]
ar = [b["width"] * b["height"] for b in bays]
u = [(sum(ar) / m) / a for a in ar]
w2, w3 = float(W["w2"]), float(W["w3"])
asg = [flat[i*7] for i in range(n)]
S = [0.0] * m
for i in range(n):
    if flat[i*7+6]:
        S[asg[i]] += L[i]

def z2_of(Sx):
    V = [u[j] * Sx[j] for j in range(m)]
    return math.floor(max(V) - min(V))

Z2 = z2_of(S)
cands = []
for i in range(n):
    if not flat[i*7+6]:
        continue
    a = asg[i]
    for b in range(m):
        if b == a:
            continue
        S2 = list(S); S2[a] -= L[i]; S2[b] += L[i]
        d = w2 * (z2_of(S2) - Z2) + w3 * (pref[i][a] - pref[i][b])
        if d < -1e-9:
            cands.append((d, i, b))
cands.sort()

eng = M._build_engine(p)
hor = max(D) + max(P) + 40
eng.build_occ(hor)
eng.set_solution(flat)
eng.build_occ(hor)
base = eng.objective()

rows = []
nofit_tight = nofit_loose = worse = better = 0
for d, i, b in cands[:TOP]:
    snap = eng.get_solution()
    ok_t = eng.move_block(i, b, True, 7)
    if ok_t:
        o = eng.objective()
        row = {"gain_exact": round(d), "block": i, "to_bay": b,
               "placed": "tight", "dZ1": o[0] - base[0],
               "dobj": round(o[3] - base[3])}
        if o[3] < base[3] - 1e-9:
            better += 1
        else:
            worse += 1
    else:
        eng.set_solution(snap); eng.build_occ(hor)
        ok_l = eng.move_block(i, b, False, 7)
        if ok_l:
            o = eng.objective()
            row = {"gain_exact": round(d), "block": i, "to_bay": b,
                   "placed": "loose", "dZ1": o[0] - base[0],
                   "dobj": round(o[3] - base[3])}
            nofit_tight += 1
            if o[3] < base[3] - 1e-9:
                better += 1
        else:
            row = {"gain_exact": round(d), "block": i, "to_bay": b,
                   "placed": "NONE", "dZ1": None, "dobj": None}
            nofit_loose += 1
    rows.append(row)
    eng.set_solution(snap); eng.build_occ(hor)

print(json.dumps({
    "board": os.path.basename(sol_path), "base_obj": base[3],
    "candidates_total": len(cands), "examined": len(rows),
    "verdict": {"no_place_at_all": nofit_loose,
                "needed_loose_entry": nofit_tight,
                "placed_but_worse": worse, "placed_and_better": better},
    "rows": rows[:12]}, indent=1))
