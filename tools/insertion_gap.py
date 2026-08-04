"""Is the ENGINE'S INSERTION incomplete? (campaign 2, code-audit probe)

insertGreedy scans: phase 1 = the release day (full bay scan), phase 2 =
resident exit events but only via scanLocal (a local scan around the
exiting block; a full bay scan happens only when the bay was area-
rejected and later fits), phase 3 = a sparse sweep with stride 6.

So a punctual day that is neither the release day, nor an exit event
whose freed space happens to be adjacent to the mover, nor on the
stride grid is never examined. feasible_positions, by contrast, walks
EVERY day and every corner candidate.

This probe puts the two side by side on the same board: for each
high-value assignment move, remove the block, ask feasible_positions
for punctual slots in the wanted bay, and compare with what
move_block(tight) -- i.e. insertGreedy -- finds. A non-empty slot list
where move_block fails is a completeness gap in the shipped insertion,
not a property of the instance.

Usage: insertion_gap.py <prob.json> <sol.json> [topN]
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
TOP = int(sys.argv[3]) if len(sys.argv) > 3 else 20

p = json.load(open(prob_path))
ops = json.load(open(sol_path))["operations"]
B, bays, W = p["blocks"], p["bays"], p["weights"]
n, m = len(B), len(bays)
D = [int(round(b["due_date"])) for b in B]
P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
PREF = [[float(v) for v in b["bay_preferences"]] for b in B]
SMAX = [max(pr) for pr in PREF]
L = [float(b["workload"]) for b in B]
ar = [b["width"] * b["height"] for b in bays]
u = [(sum(ar) / m) / a for a in ar]
w1, w2, w3 = float(W["w1"]), float(W["w2"]), float(W["w3"])

flat = [0] * (n * 7)
for t, day in ops.items():
    for o in day:
        i = o["block_id"]
        if o["type"] == "ENTRY":
            flat[i*7:i*7+5] = [o["bay_id"], o["orient_idx"], o["x"], o["y"], int(t)]
            flat[i*7+6] = 1
        else:
            flat[i*7+5] = int(t)

asg = [flat[i*7] for i in range(n)]
S = [0.0] * m
for i in range(n):
    if flat[i*7+6]:
        S[asg[i]] += L[i]

def z2_of(Sx):
    V = [u[j] * Sx[j] for j in range(m)]
    return math.floor(max(V) - min(V))

Z2 = z2_of(S)
tard = [max(0.0, flat[i*7+5] - D[i]) if flat[i*7+6] else 0.0 for i in range(n)]
cands = []
for i in range(n):
    if not flat[i*7+6]:
        continue
    a = asg[i]
    for b in range(m):
        if b == a:
            continue
        S2 = list(S); S2[a] -= L[i]; S2[b] += L[i]
        d = (w2 * (z2_of(S2) - Z2) + w3 * (PREF[i][a] - PREF[i][b])
             - w1 * tard[i])
        if d < -1e-9:
            cands.append((d, i, b))
cands.sort()

eng = M._build_engine(p)
hor = max(D) + max(P) + 40
eng.build_occ(hor)
rows = []
gap = 0
for d, i, b in cands[:TOP]:
    eng.set_solution(flat); eng.build_occ(hor)
    ok_move = eng.move_block(i, b, True, 7)
    eng.set_solution(flat); eng.build_occ(hor)
    part = list(flat); part[i*7+6] = 0
    eng.set_solution(part); eng.build_occ(hor)
    raw = eng.feasible_positions(i, 400, 0)
    slots = [tuple(raw[q:q+5]) for q in range(0, len(raw), 5)]
    punct = [c for c in slots if c[0] == b and c[4] + P[i] <= D[i]]
    if punct and not ok_move:
        gap += 1
    rows.append({"gain": round(d), "block": i, "bay": b,
                 "insertGreedy_tight": bool(ok_move),
                 "full_scan_punctual_slots": len(punct),
                 "earliest_punctual_entry": min((c[4] for c in punct), default=None),
                 "release": int(math.ceil(B[i]["release_time"] - 1e-9)),
                 "latest_punctual": D[i] - P[i]})
eng.set_solution(flat); eng.build_occ(hor)

print(json.dumps({"board": os.path.basename(sol_path),
                  "examined": len(rows),
                  "insertion_gaps": gap,
                  "rows": rows}, indent=1))
