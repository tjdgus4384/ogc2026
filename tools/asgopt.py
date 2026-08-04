"""Geometry-free assignment optimum: a valid lower bound on any board.

Z2 and Z3 depend only on which bay each block goes to, so minimising
w2*(max_j u_j L_j - min_j u_j L_j) + w3*sum regret over assignments --
with no packing constraint at all -- bounds from below what ANY feasible
board can score on those two terms. On a board where Z1 is already zero
(P3) that bound is the whole objective, so it says exactly how much room
the assignment alone still holds.
"""
import json, math, sys
from ortools.sat.python import cp_model as cp
P = sys.argv[1]
p = json.load(open(f'data/preliminary_test/prob_{P}.json'))
B, bays, W = p['blocks'], p['bays'], p['weights']
n, m = len(B), len(bays)
L = [float(b['workload']) for b in B]
PREF = [[float(v) for v in b['bay_preferences']] for b in B]
SM = [max(pr) for pr in PREF]
ar = [b['width']*b['height'] for b in bays]
SC = 1000
u = [int(round(SC*(sum(ar)/m)/a)) for a in ar]
w1, w2, w3 = W['w1'], W['w2'], W['w3']
# CAPACITY-AWARE ASSIGNMENT. The pure Z2/Z3 optimum ignores whether a
# bay can physically hold what it is given -- P6's optimum could not even
# be seeded. Area-time is the currency that fixes it without touching
# geometry: a bay offers area_j per day, and a block needs area_i * P_i
# of that. Polygon areas are used, so no interlocking can shrink the
# demand, which makes the constraint valid rather than heuristic.
CAP = float(__import__('os').environ.get('ASGOPT_CAP', '0'))   # 0 = off
mdl = cp.CpModel()
y = [[mdl.NewBoolVar('') for j in range(m)] for i in range(n)]
for i in range(n): mdl.AddExactlyOne(y[i])
zmax = mdl.NewIntVar(0, 10**12, ''); zmin = mdl.NewIntVar(0, 10**12, '')
for j in range(m):
    ld = sum(int(round(L[i]*SC))*y[i][j] for i in range(n))
    mdl.Add(zmax*SC >= u[j]*ld); mdl.Add(zmin*SC <= u[j]*ld)
if CAP > 0:
    AREA = []
    for i in range(n):
        sh = B[i]['shape'][0]['layers']
        xs = [v[0] for lay in sh for v in lay]; ys = [v[1] for lay in sh for v in lay]
        AREA.append(max(1.0, (max(xs)-min(xs))*(max(ys)-min(ys))))
    PT = [max(1, int(math.ceil(b['processing_time']-1e-9))) for b in B]
    HOR = max(int(round(b['due_date'])) for b in B) + max(PT)
    for j in range(m):
        mdl.Add(sum(int(AREA[i]*PT[i])*y[i][j] for i in range(n))
                <= int(CAP * ar[j] * HOR))
reg = sum(int(round((SM[i]-PREF[i][j])*SC))*y[i][j] for i in range(n) for j in range(m))
# both terms are already SC-scaled: zmax/zmin from u*L (SC*SC / SC) and
# reg from SC*regret, so the objective is exactly SC*(w2*Z2 + w3*Z3).
mdl.Minimize(int(w2)*(zmax-zmin) + int(w3)*reg)
sv = cp.CpSolver(); sv.parameters.max_time_in_seconds = 120
sv.parameters.num_search_workers = 4
st = sv.Solve(mdl)
if st in (cp.OPTIMAL, cp.FEASIBLE):
    asg = [next(j for j in range(m) if sv.Value(y[i][j])) for i in range(n)]
    S = [0.0]*m
    for i in range(n): S[asg[i]] += L[i]
    v = [(sum(ar)/m)/ar[j]*S[j] for j in range(m)]
    z2 = math.floor(max(v)-min(v))
    z3 = sum(SM[i]-PREF[i][asg[i]] for i in range(n))
    print(json.dumps({"P": P, "status": sv.StatusName(st),
                      "z2": z2, "z3": round(z3,2),
                      "z2z3_objective": round(w2*z2 + w3*z3),
                      "asg_hist": [asg.count(j) for j in range(m)]}))
    json.dump(asg, open(f'runs/asgopt/p{P}.json','w'))
else:
    print(json.dumps({"P": P, "status": sv.StatusName(st)}))
