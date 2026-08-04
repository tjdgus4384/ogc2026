"""Block-pair 3D orthogonal packing (CP-SAT) -- campaign-3 first build.

Why this encoding. The candidate-variable MIP (globalmip.py) is exact but
its conflict set is quadratic in the VOCABULARY, and at lattice scale the
separation never converges: 700-1,100 cuts per round and vp_bad stuck at
~199 (runs/globalcpsat.log). Here the variables are the BLOCKS, so the
whole no-overlap requirement is one disjunction per block PAIR -- ~11k
rows at n=150 instead of hundreds of millions.

    per block i:  bay j_i, orientation o_i, left edge x_i, bottom edge
                  y_i, entry e_i;  occupancy = the 3D box
                  (x_i, y_i, [e_i, e_i + P_i))
    per pair i<k: different bay  OR  x-separated  OR  y-separated
                  OR  time-separated
    unary:        containment  0 <= x, x + w <= W(bay)  (same for y)
                  release      e_i >= R_i

Soundness (why any solution is checker-feasible by construction):
bbox separation implies same-layer polygon separation, so rule 1 holds;
and a pair that overlaps in time is separated in space, so the crane
sweeps of rules 2-5 never meet an obstacle. The simultaneous-entry /
simultaneous-exit tie-break defect that BUILD.txt discloses cannot arise
here either, for the same reason.

Honest limitation: our incumbent boards interlock (bbox utilisation
103% vs polygon 58-65%), so they are NOT representable here. This model
can only reach what pure bbox packing can reach; its value is (a) a
guaranteed-feasible diverse generator, (b) a baseline that quantifies
what interlocking is worth.

  pack3d.py <prob.json> <out.json> [seconds] [workers]

Pre-registered kill rule: no checker-feasible solution => the encoding
is wrong; feasible but >30% above the incumbent => worthless even as a
generator. Either way, record and stop.
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

prob_path, out_path = sys.argv[1], sys.argv[2]
SEC = float(sys.argv[3]) if len(sys.argv) > 3 else 300.0
WORKERS = int(sys.argv[4]) if len(sys.argv) > 4 else 8

p = json.load(open(prob_path))
B, bays, W = p["blocks"], p["bays"], p["weights"]
n, m = len(B), len(bays)
R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]
D = [int(round(b["due_date"])) for b in B]
P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
L = [float(b["workload"]) for b in B]
PREF = [[float(v) for v in b["bay_preferences"]] for b in B]
SMAX = [max(pr) for pr in PREF]
w1, w2, w3 = float(W["w1"]), float(W["w2"]), float(W["w3"])
HOR = max(D) + max(P) + 40

# integer bbox extents per (block, orientation): width, height, and the
# offset from the reference point to the left/bottom edge
EXT = []
for i in range(n):
    per = []
    for sh in B[i]["shape"]:
        xs = [v[0] for lay in sh["layers"] for v in lay]
        ys = [v[1] for lay in sh["layers"] for v in lay]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        per.append({"w": int(math.ceil(x1 - x0 - 1e-9)),
                    "h": int(math.ceil(y1 - y0 - 1e-9)),
                    "ox": x0, "oy": y0})
    EXT.append(per)
NORI = [len(e) for e in EXT]
BW = [int(math.floor(b["width"] + 1e-9)) for b in bays]
BH = [int(math.floor(b["height"] + 1e-9)) for b in bays]
maxW, maxH = max(BW), max(BH)

mdl = cp.CpModel()
bay = [mdl.NewIntVar(0, m - 1, f"bay{i}") for i in range(n)]
ori = [mdl.NewIntVar(0, NORI[i] - 1, f"ori{i}") for i in range(n)]
x = [mdl.NewIntVar(0, maxW, f"x{i}") for i in range(n)]
y = [mdl.NewIntVar(0, maxH, f"y{i}") for i in range(n)]
e = [mdl.NewIntVar(R[i], HOR - P[i], f"e{i}") for i in range(n)]
wx = [mdl.NewIntVar(1, maxW, f"wx{i}") for i in range(n)]
hy = [mdl.NewIntVar(1, maxH, f"hy{i}") for i in range(n)]
bw = [mdl.NewIntVar(1, maxW, f"bw{i}") for i in range(n)]
bh = [mdl.NewIntVar(1, maxH, f"bh{i}") for i in range(n)]
inbay = [[mdl.NewBoolVar(f"b{i}_{j}") for j in range(m)] for i in range(n)]

for i in range(n):
    mdl.AddExactlyOne(inbay[i])
    for j in range(m):
        mdl.Add(bay[i] == j).OnlyEnforceIf(inbay[i][j])
        mdl.Add(bay[i] != j).OnlyEnforceIf(inbay[i][j].Not())
    mdl.AddElement(ori[i], [EXT[i][o]["w"] for o in range(NORI[i])], wx[i])
    mdl.AddElement(ori[i], [EXT[i][o]["h"] for o in range(NORI[i])], hy[i])
    mdl.AddElement(bay[i], BW, bw[i])
    mdl.AddElement(bay[i], BH, bh[i])
    mdl.Add(x[i] + wx[i] <= bw[i])          # unary: bay containment
    mdl.Add(y[i] + hy[i] <= bh[i])          # (release is the e[i] domain)

# pairwise separation: different bay OR x OR y OR time
for i in range(n):
    for k in range(i + 1, n):
        lits = []
        db = mdl.NewBoolVar("")
        mdl.Add(bay[i] != bay[k]).OnlyEnforceIf(db)
        mdl.Add(bay[i] == bay[k]).OnlyEnforceIf(db.Not())
        lits.append(db)
        for (a, b2, va, vb, wa) in ((i, k, x, x, wx), (k, i, x, x, wx),
                                    (i, k, y, y, hy), (k, i, y, y, hy)):
            lit = mdl.NewBoolVar("")
            mdl.Add(va[a] + wa[a] <= vb[b2]).OnlyEnforceIf(lit)
            lits.append(lit)
        for (a, b2) in ((i, k), (k, i)):
            lit = mdl.NewBoolVar("")
            mdl.Add(e[a] + P[a] <= e[b2]).OnlyEnforceIf(lit)
            lits.append(lit)
        mdl.AddBoolOr(lits)

tard = [mdl.NewIntVar(0, HOR, f"t{i}") for i in range(n)]
for i in range(n):
    mdl.Add(tard[i] >= e[i] + P[i] - D[i])
reg = [mdl.NewIntVar(0, int(max(SMAX)) + 1, f"r{i}") for i in range(n)]
for i in range(n):
    mdl.AddElement(bay[i], [int(round(SMAX[i] - PREF[i][j])) for j in range(m)],
                   reg[i])

# Z2: |max_j u_j*Load_j - min_j u_j*Load_j|, scaled to integers
# Z2 = max_j u_j*Load_j - min_j u_j*Load_j. u_j is fractional, so the
# whole objective is carried in SC-scaled integer units (exactly SC x
# the true objective) rather than rounding the balance term alone.
areas = [BW[j] * BH[j] for j in range(m)]
SC = 100
uu = [int(round(SC * (sum(areas) / m) / areas[j])) for j in range(m)]
big = SC * int(sum(L)) * max(uu) + 1
zmax = mdl.NewIntVar(0, big, "zmax")        # scaled by SC
zmin = mdl.NewIntVar(0, big, "zmin")
for j in range(m):
    load = sum(int(round(L[i])) * inbay[i][j] for i in range(n))
    mdl.Add(zmax >= uu[j] * load)
    mdl.Add(zmin <= uu[j] * load)
mdl.Minimize(SC * int(w1) * sum(tard) + SC * int(w3) * sum(reg)
             + int(w2) * (zmax - zmin))

sv = cp.CpSolver()
sv.parameters.max_time_in_seconds = SEC
sv.parameters.num_search_workers = WORKERS
sv.parameters.log_search_progress = False
t0 = time.time()
st = sv.Solve(mdl)
res = {"board": os.path.basename(prob_path), "n": n, "m": m,
       "status": sv.StatusName(st), "wall": round(time.time() - t0, 1),
       "model_obj": None, "bound": None}
if st in (cp.OPTIMAL, cp.FEASIBLE):
    res["model_obj"] = round(sv.ObjectiveValue() / SC)   # unscale
    res["bound"] = round(sv.BestObjectiveBound() / SC)
    flat = [0] * (n * 7)
    for i in range(n):
        o = sv.Value(ori[i])
        # the checker translates the RAW polygon by (x,y): recover the
        # reference-point coordinate from the left/bottom edge
        rx = int(round(sv.Value(x[i]) - EXT[i][o]["ox"]))
        ry = int(round(sv.Value(y[i]) - EXT[i][o]["oy"]))
        ee = sv.Value(e[i])
        flat[i*7:i*7+7] = [sv.Value(bay[i]), o, rx, ry, ee, ee + P[i], 1]
    eng = M._build_engine(p)
    eng.build_occ(HOR)
    eng.set_solution(flat)
    eng.build_occ(HOR)
    res["engine_obj"] = eng.objective()[3]
    res["vp_bad"] = sum(1 for i in range(n) if not eng.vp_ok(i))
    sol = M._recs_to_sol(M._extract(eng, n))
    chk = M._check(p, sol)
    res["checker_feasible"] = None if chk is None else bool(chk.get("feasible"))
    res["checker_obj"] = None if chk is None else chk.get("objective")
    if chk is not None and chk.get("feasible"):
        json.dump(sol, open(out_path, "w"))
print(json.dumps(res))
