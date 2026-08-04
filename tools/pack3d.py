"""Block-pair 3D orthogonal packing (CP-SAT) -- campaign-3 first build.

Why this encoding. The candidate-variable MIP (globalmip.py) is exact but
its conflict set is quadratic in the VOCABULARY, and at lattice scale the
separation never converges: 700-1,100 cuts per round and vp_bad stuck at
~199 (runs/globalcpsat.log). Here the variables are the BLOCKS, so the
whole no-overlap requirement is one disjunction per block PAIR -- ~11k
rows at n=150 instead of hundreds of millions.

    per block i:  bay j_i, orientation o_i, REFERENCE POINT X_i, Y_i
                  (integers -- exactly what the solution format and the
                  checker consume), entry e_i; the occupied span is
                  [S*X_i + x0(o_i), S*X_i + x1(o_i)] in S-scaled units,
                  where x0/x1 are the orientation's polygon extents
    per pair i<k: different bay  OR  x-separated  OR  y-separated
                  OR  time-separated
    unary:        containment  0 <= x, x + w <= W(bay)  (same for y)
                  release      e_i >= R_i

The reference point, not the left edge, is the decision variable. A
first version optimised the left edge and recovered the reference point
by rounding at output; the checker rounds coordinates too, so that
rounding shifted blocks by up to half a unit and broke exactly the
separation the model had proven (measured: P1 OPTIMAL yet 46 vp
violations). Extents are pre-scaled by S=10000 (the spec allows four
decimal digits), with the left/bottom extent floored and the
right/top ceiled, so every separation the solver proves is still true
after the checker's integer rounding.

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
# BAY-LOCAL MODE: pack3d.py <prob> <out> <sec> <workers> <board.sol.json> <bay>
# Whole boards at n>=150 are hopeless for this encoding (measured), but a
# single bay holds 50-83 blocks -- the size it solved to proven optimality
# on P1 in 69 s -- and invariant 6 says a better arrangement of one bay
# merges exactly into the board. The assignment is frozen to the board's,
# so Z2/Z3 are constant and this is pure tardiness minimisation, run in
# FREE COORDINATES rather than over a harvested candidate vocabulary
# (which is what every refuted CP re-placement arm did).
BOARD = sys.argv[5] if len(sys.argv) > 5 else None
BAYID = int(sys.argv[6]) if len(sys.argv) > 6 else -1

p = json.load(open(prob_path))
base_tard = None
if BOARD:
    ops = json.load(open(BOARD))["operations"]
    ent, ext = {}, {}
    for t, day in ops.items():
        for o in day:
            (ent if o["type"] == "ENTRY" else ext)[o["block_id"]] = (int(t), o)
    keep = [i for i, (_, o) in ent.items() if o["bay_id"] == BAYID]
    keep.sort()
    dd = [int(round(p["blocks"][i]["due_date"])) for i in keep]
    base_tard = sum(max(0, ext[i][0] - d) for i, d in zip(keep, dd))
    p = {"bays": [p["bays"][BAYID]],
         "blocks": [{**p["blocks"][i],
                     "bay_preferences": [p["blocks"][i]["bay_preferences"][BAYID]]}
                    for i in keep],
         "weights": p["weights"]}
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
# Extents are rounded OUTWARD to integers (lo floored, hi ceiled), so a
# separation the solver proves stays true under the checker's integer
# coordinates. Scaling by 10000 to keep the polygon's four decimals was
# tried first and made the model unsolvable -- coordinate domains blew
# out to +-1e9 and CP-SAT returned UNKNOWN on P1 in 100 s where the
# integer version solves in seconds. Outward rounding costs at most one
# unit of density per side; correctness never depended on the decimals.
S = 1
EXT = []
for i in range(n):
    per = []
    for sh in B[i]["shape"]:
        xs = [v[0] for lay in sh["layers"] for v in lay]
        ys = [v[1] for lay in sh["layers"] for v in lay]
        per.append({"x0": int(math.floor(S * min(xs))),
                    "x1": int(math.ceil(S * max(xs))),
                    "y0": int(math.floor(S * min(ys))),
                    "y1": int(math.ceil(S * max(ys)))})
    EXT.append(per)
NORI = [len(e) for e in EXT]
BW = [int(math.floor(b["width"] + 1e-9)) for b in bays]
BH = [int(math.floor(b["height"] + 1e-9)) for b in bays]
maxW, maxH = max(BW), max(BH)

mdl = cp.CpModel()
bay = [mdl.NewIntVar(0, m - 1, f"bay{i}") for i in range(n)]
ori = [mdl.NewIntVar(0, NORI[i] - 1, f"ori{i}") for i in range(n)]
X = [mdl.NewIntVar(-maxW, 2 * maxW, f"X{i}") for i in range(n)]   # ref point
Y = [mdl.NewIntVar(-maxH, 2 * maxH, f"Y{i}") for i in range(n)]
e = [mdl.NewIntVar(R[i], HOR - P[i], f"e{i}") for i in range(n)]
inbay = [[mdl.NewBoolVar(f"b{i}_{j}") for j in range(m)] for i in range(n)]
# S-scaled occupied span, exact under the checker's integer coordinates
xlo = [mdl.NewIntVar(0, maxW, f"xlo{i}") for i in range(n)]
xhi = [mdl.NewIntVar(0, maxW, f"xhi{i}") for i in range(n)]
ylo = [mdl.NewIntVar(0, maxH, f"ylo{i}") for i in range(n)]
yhi = [mdl.NewIntVar(0, maxH, f"yhi{i}") for i in range(n)]

for i in range(n):
    mdl.AddExactlyOne(inbay[i])
    for j in range(m):
        mdl.Add(bay[i] == j).OnlyEnforceIf(inbay[i][j])
        mdl.Add(bay[i] != j).OnlyEnforceIf(inbay[i][j].Not())
    for tag, lo, hi, k0, k1 in (("x", xlo, xhi, "x0", "x1"),
                                ("y", ylo, yhi, "y0", "y1")):
        off_lo = mdl.NewIntVar(-4 * maxW, 4 * maxW, f"{tag}o0_{i}")
        off_hi = mdl.NewIntVar(-4 * maxW, 4 * maxW, f"{tag}o1_{i}")
        mdl.AddElement(ori[i], [EXT[i][o][k0] for o in range(NORI[i])], off_lo)
        mdl.AddElement(ori[i], [EXT[i][o][k1] for o in range(NORI[i])], off_hi)
        base = X[i] if tag == "x" else Y[i]
        mdl.Add(lo[i] == S * base + off_lo)
        mdl.Add(hi[i] == S * base + off_hi)
    # unary: bay containment (release is already the e[i] domain)
    for j in range(m):
        mdl.Add(xlo[i] >= 0).OnlyEnforceIf(inbay[i][j])
        mdl.Add(xhi[i] <= S * BW[j]).OnlyEnforceIf(inbay[i][j])
        mdl.Add(ylo[i] >= 0).OnlyEnforceIf(inbay[i][j])
        mdl.Add(yhi[i] <= S * BH[j]).OnlyEnforceIf(inbay[i][j])

# pairwise separation: different bay OR x OR y OR time
for i in range(n):
    for k in range(i + 1, n):
        lits = []
        db = mdl.NewBoolVar("")
        mdl.Add(bay[i] != bay[k]).OnlyEnforceIf(db)
        mdl.Add(bay[i] == bay[k]).OnlyEnforceIf(db.Not())
        lits.append(db)
        for (a, b2, hi, lo) in ((i, k, xhi, xlo), (k, i, xhi, xlo),
                                (i, k, yhi, ylo), (k, i, yhi, ylo)):
            lit = mdl.NewBoolVar("")
            mdl.Add(hi[a] <= lo[b2]).OnlyEnforceIf(lit)
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
if BOARD:
    res.update({"mode": "bay-local", "bay": BAYID, "incumbent_tardiness": base_tard})
if st in (cp.OPTIMAL, cp.FEASIBLE):
    res["model_obj"] = round(sv.ObjectiveValue() / SC)   # unscale
    res["bound"] = round(sv.BestObjectiveBound() / SC)
    if BOARD:
        res["packed_tardiness"] = sum(sv.Value(tard[i]) for i in range(n))
        if base_tard:
            res["tardiness_delta_pct"] = round(
                100 * (res["packed_tardiness"] - base_tard) / base_tard, 2)
    flat = [0] * (n * 7)
    for i in range(n):
        o = sv.Value(ori[i])
        rx, ry = sv.Value(X[i]), sv.Value(Y[i])   # already the ref point
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
