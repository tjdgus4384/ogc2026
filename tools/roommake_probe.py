"""Targeted room-making CP -- the never-tried mechanism (campaign 2).

Diagnosis (assign_diag): every high-value assignment move fails for ONE
reason -- the wanted bay has no PUNCTUAL room. Same physics blocks the
tardiness moves on P4-P6. Every exact-arm variant so far freed a subset
and let CP re-place it FREELY, so CP just reproduced the incumbent
(0 applied, 4/4 boards).

This forces the question instead: the mover MUST sit in the target bay
at a punctual entry; CP then rearranges the blockers around it. Freeing
the bay's overlapping residents is what gives CP a real vocabulary
(their positions come from the partially emptied bay, not the full one).

  roommake_probe.py <prob.json> <sol.json> [topN] [K] [cpsec]

Reports, per candidate: how many punctual slots opened for the mover
after freeing, the CP status, and the objective delta. Kill rule: no
candidate accepted on any board -> the bays are packing-saturated, not
just greedily packed, and the assignment/tardiness gaps are unreachable
by rearrangement at this scale.
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
from ortools.sat.python import cp_model as cp  # noqa: E402

prob_path, sol_path = sys.argv[1], sys.argv[2]
TOP = int(sys.argv[3]) if len(sys.argv) > 3 else 8
K = int(sys.argv[4]) if len(sys.argv) > 4 else 30
CPSEC = float(sys.argv[5]) if len(sys.argv) > 5 else 10.0

p = json.load(open(prob_path))
ops = json.load(open(sol_path))["operations"]
B, bays, W = p["blocks"], p["bays"], p["weights"]
n, m = len(B), len(bays)
D = [int(round(b["due_date"])) for b in B]
R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]
P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
PREF = [[float(v) for v in b["bay_preferences"]] for b in B]
SMAX = [max(pr) for pr in PREF]
L = [float(b["workload"]) for b in B]
ar = [b["width"] * b["height"] for b in bays]
u = [(sum(ar) / m) / a for a in ar]
w1, w2, w3 = float(W["w1"]), float(W["w2"]), float(W["w3"])

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

def z2_of(S):
    V = [u[j] * S[j] for j in range(m)]
    return math.floor(max(V) - min(V))

asg = [flat[i*7] for i in range(n)]
S = [0.0] * m
for i in range(n):
    if flat[i*7+6]:
        S[asg[i]] += L[i]
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
eng.set_solution(flat)
eng.build_occ(hor)
base = eng.objective()[3]
cur = list(flat)
best = base
rows = []
t0 = time.time()

for d, i, b in cands[:TOP]:
    # residents of the target bay whose stay overlaps the mover's
    # punctual-feasible span [R_i, D_i)
    lo, hi = R[i], D[i]
    blockers = [q for q in range(n)
                if cur[q*7+6] and q != i and cur[q*7] == b
                and cur[q*7+4] < hi and cur[q*7+5] > lo]
    blockers.sort(key=lambda q: -(min(hi, cur[q*7+5]) - max(lo, cur[q*7+4])))
    free = [i] + blockers[:K - 1]
    part = list(cur)
    for q in free:
        part[q*7+6] = 0
    eng.set_solution(part); eng.build_occ(hor)
    CAND = {}
    ok_row = {"gain_exact": round(d), "block": i, "to_bay": b,
              "freed": len(free)}
    for q in free:
        raw = eng.feasible_positions(q, 400, 0)
        cs = [tuple(raw[t2:t2+5]) for t2 in range(0, len(raw), 5)]
        if q == i:
            cs = [c for c in cs if c[0] == b and c[4] + P[i] <= D[i]]
            ok_row["mover_punctual_slots"] = len(cs)
            if not cs:
                rows.append({**ok_row, "cp": "NO_SLOT", "dobj": None})
                eng.set_solution(cur); eng.build_occ(hor)
                break
        else:
            c0 = tuple(cur[q*7:q*7+5])
            if c0 not in cs:
                cs.append(c0)
        CAND[q] = cs
    else:
        flatc, idx = [], []
        for q in free:
            for r2, (j, oi, x, y, e) in enumerate(CAND[q]):
                flatc += [q, j, oi, x, y, e]
                idx.append((q, r2))
        prs = eng.pair_conflicts(flatc)
        mdl = cp.CpModel()
        V = {q: [mdl.NewBoolVar(f"z{q}_{r2}") for r2 in range(len(CAND[q]))]
             for q in free}
        for q in free:
            mdl.AddExactlyOne(V[q])
        for t2 in range(0, len(prs), 2):
            (qa, ra), (qb, rb) = idx[prs[t2]], idx[prs[t2 + 1]]
            mdl.AddBoolOr([V[qa][ra].Not(), V[qb][rb].Not()])
        mdl.Minimize(sum(int(w1 * max(0, c[4] + P[q] - D[q])
                             + w3 * (SMAX[q] - PREF[q][c[0]])) * V[q][r2]
                         for q in free for r2, c in enumerate(CAND[q])))
        sv = cp.CpSolver()
        sv.parameters.max_time_in_seconds = CPSEC
        sv.parameters.num_search_workers = 4
        st = sv.Solve(mdl)
        if st not in (cp.OPTIMAL, cp.FEASIBLE):
            rows.append({**ok_row, "cp": sv.StatusName(st), "dobj": None})
            eng.set_solution(cur); eng.build_occ(hor)
            continue
        new = list(part)
        for q in free:
            for r2, c in enumerate(CAND[q]):
                if sv.Value(V[q][r2]):
                    new[q*7:q*7+7] = [c[0], c[1], c[2], c[3], c[4],
                                      c[4] + P[q], 1]
                    break
        eng.set_solution(new); eng.build_occ(hor)
        badv = [q for q in range(n) if new[q*7+6] and not eng.vp_ok(q)]
        v = eng.objective()[3]
        why = None
        if badv:
            # which rule does the CP model miss? ask the OFFICIAL checker
            res = M._check(p, M._recs_to_sol(M._extract(eng, n)))
            if res is not None and not res.get("feasible"):
                why = str(res.get("violations", ["?"])[0])[:200]
            freed_set = set(free)
            why = {"n_bad": len(badv), "bad_in_freed":
                   sum(1 for q in badv if q in freed_set),
                   "bad_frozen": sum(1 for q in badv if q not in freed_set),
                   "checker": why}
        rows.append({**ok_row, "cp": sv.StatusName(st),
                     "vp_bad": bool(badv), "why": why,
                     "dobj": round(v - best)})
        if not badv and v < best - 1e-9:
            best = v; cur = new
        else:
            eng.set_solution(cur); eng.build_occ(hor)

print(json.dumps({"board": os.path.basename(sol_path), "base": base,
                  "best": best,
                  "gain_pct": round(100 * (base - best) / base, 3),
                  "candidates_total": len(cands), "wall": round(time.time() - t0, 1),
                  "rows": rows}, indent=1))
