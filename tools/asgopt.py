"""Geometry-free assignment optimum: a valid lower bound on any board.

Z2 and Z3 depend only on which bay each block goes to, so minimising
w2*(max_j u_j L_j - min_j u_j L_j) + w3*sum regret over assignments --
with no packing constraint at all -- bounds from below what ANY feasible
board can score on those two terms. On a board where Z1 is already zero
(P3) that bound is the whole objective, so it says exactly how much room
the assignment alone still holds.
"""
import json, math, os, sys
from ortools.sat.python import cp_model as cp
P = sys.argv[1]
p = json.load(open(f'data/preliminary_test/prob_{P}.json'))
B, bays, W = p['blocks'], p['bays'], p['weights']
n, m = len(B), len(bays)
L = [float(b['workload']) for b in B]
PREF = [[float(v) for v in b['bay_preferences']] for b in B]
SM = [max(pr) for pr in PREF]
ar = [b['width']*b['height'] for b in bays]
# SC also sets how finely u_j is represented. The objective is scored with
# floor(Z2), so the master must model that floor exactly (below) rather than
# minimise a continuous range; and u_j must be fine enough that its rounding
# cannot move the floor. 1e-3 left a 4.6e-4 relative error on u, which is the
# only reason the earlier "OPTIMAL" certified a surrogate rather than the
# printed objective.
SC = 100000
u = [int(round(SC*(sum(ar)/m)/a)) for a in ar]
w1, w2, w3 = W['w1'], W['w2'], W['w3']
# CAPACITY-AWARE ASSIGNMENT. The pure Z2/Z3 optimum ignores whether a
# bay can physically hold what it is given -- P6's optimum could not even
# be seeded. Area-time is the currency that fixes it without touching
# geometry: a bay offers area_j per day, and a block needs area_i * P_i
# of that. Polygon areas are used, so no interlocking can shrink the
# demand, which makes the constraint valid rather than heuristic.
CAP = float(__import__('os').environ.get('ASGOPT_CAP', '0'))   # 0 = off
FIT = os.environ.get('ASGOPT_FIT', '0') == '1'
PUNCTUAL = os.environ.get('ASGOPT_PUNCTUAL', '0') == '1'
TAG = os.environ.get('ASGOPT_TAG', '')
BENDERS = os.environ.get('ASGOPT_AREA_BENDERS', '0') == '1'
BASE = os.environ.get('ASGOPT_BASE', '')
RADIUS = int(os.environ.get('ASGOPT_RADIUS', '0'))

def polygon_area(poly):
    return abs(sum(poly[k][0] * poly[(k + 1) % len(poly)][1]
                   - poly[(k + 1) % len(poly)][0] * poly[k][1]
                   for k in range(len(poly)))) / 2

def minimum_layer_area(block, layer):
    """Smallest area this block can occupy at height `layer` over ALL of its
    orientations. docs/MODEL.md fixes layer counts PER ORIENTATION, so an
    orientation with no layer `layer` puts nothing at that height and the
    minimum is 0 -- skipping it would charge demand the block need not pay
    and could manufacture a false infeasibility core."""
    areas = []
    for orient in block['shape']:
        if layer >= len(orient['layers']):
            return 0.0
        areas.append(polygon_area(orient['layers'][layer]))
    return min(areas) if areas else 0.0

def footprint_area(block):
    return minimum_layer_area(block, 0)

def infeasible_area_core(assigned, bay_index, layer):
    """Return a valid energetic infeasibility core, or [] if schedulable."""
    scale = 100
    sub = cp.CpModel()
    assumptions, by_index, intervals, demands = [], {}, [], []
    for i in assigned:
        release = int(math.ceil(B[i]['release_time'] - 1e-9))
        duration = max(1, int(math.ceil(B[i]['processing_time'] - 1e-9)))
        latest = int(round(B[i]['due_date'])) - duration
        if release > latest:
            return [i]
        demand = math.floor(minimum_layer_area(B[i], layer) * scale)
        if demand <= 0:
            continue                      # provably free at this height
        active = sub.NewBoolVar(f'a{i}')
        start = sub.NewIntVar(release, latest, f's{i}')
        interval = sub.NewOptionalIntervalVar(start, duration, start + duration,
                                              active, f'iv{i}')
        assumptions.append(active)
        by_index[active.Index()] = i
        intervals.append(interval)
        # Demand rounds DOWN and capacity rounds UP, so this stays a
        # relaxation of true polygon occupancy and every core it certifies
        # is a genuine impossibility of the real per-layer packing.
        demands.append(demand)
    if not intervals:
        return []
    sub.AddCumulative(intervals, demands,
                      math.ceil(ar[bay_index] * scale))
    sub.AddAssumptions(assumptions)
    checker = cp.CpSolver()
    checker.parameters.max_time_in_seconds = 30
    checker.parameters.num_search_workers = 1
    status = checker.Solve(sub)
    if status != cp.INFEASIBLE:
        return []
    return [by_index[x] for x in checker.SufficientAssumptionsForInfeasibility()]
mdl = cp.CpModel()
y = [[mdl.NewBoolVar('') for j in range(m)] for i in range(n)]
for i in range(n): mdl.AddExactlyOne(y[i])
if BASE and RADIUS > 0:
    base_doc = json.load(open(BASE))
    base_assignment = [-1] * n
    for operations in base_doc['operations'].values():
        for operation in operations:
            if operation['type'] == 'ENTRY':
                base_assignment[operation['block_id']] = operation['bay_id']
    if any(j < 0 for j in base_assignment):
        raise ValueError('base board does not assign every block')
    mdl.Add(sum(y[i][j] for i, j in enumerate(base_assignment))
            >= n - RADIUS)
if PUNCTUAL:
    for i, b in enumerate(B):
        release = int(math.ceil(b['release_time'] - 1e-9))
        due = int(round(b['due_date']))
        duration = max(1, int(math.ceil(b['processing_time'] - 1e-9)))
        if release + duration > due:
            mdl.AddBoolOr([])  # zero-tardiness is impossible for this block
if FIT:
    for i, b in enumerate(B):
        for j, bayj in enumerate(bays):
            fits = False
            for orient in b['shape']:
                xs = [v[0] for layer in orient['layers'] for v in layer]
                ys = [v[1] for layer in orient['layers'] for v in layer]
                if (math.ceil(max(xs)) - math.floor(min(xs)) <= bayj['width']
                        and math.ceil(max(ys)) - math.floor(min(ys)) <= bayj['height']):
                    fits = True
                    break
            if not fits:
                mdl.Add(y[i][j] == 0)
# zmax/zmin live in the SC*SC units of u[j]*ld directly. Multiplying an
# already-large variable by SC again (the first attempt) pushed the model
# past CP-SAT's 64-bit domain and it answered MODEL_INVALID.
SC2 = SC * SC
zmax = mdl.NewIntVar(0, 10**16, 'zmax'); zmin = mdl.NewIntVar(0, 10**16, 'zmin')
for j in range(m):
    ld = sum(int(round(L[i]*SC))*y[i][j] for i in range(n))
    mdl.Add(zmax >= u[j]*ld); mdl.Add(zmin <= u[j]*ld)
# EXACT FLOOR. Z2 is floor(range), so a master that minimises the raw range
# can trade away a fraction of Z2 it never actually pays against real Z3.
# z2f is the scored integer: SC2*z2f <= range <= SC2*z2f + SC2-1.
z2f = mdl.NewIntVar(0, 10**6, 'z2f')
mdl.Add(SC2 * z2f <= zmax - zmin)
mdl.Add(zmax - zmin <= SC2 * z2f + SC2 - 1)
if CAP > 0:
    AREA = [footprint_area(b) for b in B]
    PT = [max(1, int(math.ceil(b['processing_time']-1e-9))) for b in B]
    HOR = max(int(round(b['due_date'])) for b in B) + max(PT)
    for j in range(m):
        mdl.Add(sum(int(AREA[i]*PT[i])*y[i][j] for i in range(n))
                <= int(CAP * ar[j] * HOR))
reg = sum(int(round((SM[i]-PREF[i][j])*SC))*y[i][j] for i in range(n) for j in range(m))
# reg is SC*regret and z2f is the scored integer Z2, so the objective below
# is exactly SC*(w2*Z2 + w3*Z3) -- the same expression the official scorer
# computes, not a continuous stand-in for it.
mdl.Minimize(int(w2)*SC*z2f + int(w3)*reg)
sv = cp.CpSolver()
sv.parameters.max_time_in_seconds = 120
sv.parameters.num_search_workers = 4
cuts = 0
while True:
    st = sv.Solve(mdl)
    if st not in (cp.OPTIMAL, cp.FEASIBLE) or not BENDERS:
        break
    candidate = [next(j for j in range(m) if sv.Value(y[i][j]))
                 for i in range(n)]
    cores = []
    max_layers = max(len(orient['layers'])
                     for block in B for orient in block['shape'])
    for j in range(m):
        assigned = [i for i in range(n) if candidate[i] == j]
        for layer in range(max_layers):
            layer_blocks = [i for i in assigned
                            if minimum_layer_area(B[i], layer) > 0]
            core = infeasible_area_core(layer_blocks, j, layer)
            if core:
                cores.append((j, core))
    if not cores:
        break
    for j, core in cores:
        mdl.Add(sum(y[i][j] for i in core) <= len(core) - 1)
        cuts += 1
if st in (cp.OPTIMAL, cp.FEASIBLE):
    asg = [next(j for j in range(m) if sv.Value(y[i][j])) for i in range(n)]
    S = [0.0]*m
    for i in range(n): S[asg[i]] += L[i]
    v = [(sum(ar)/m)/ar[j]*S[j] for j in range(m)]
    z2 = math.floor(max(v)-min(v))
    z3 = sum(SM[i]-PREF[i][asg[i]] for i in range(n))
    model_z2 = sv.Value(z2f)
    print(json.dumps({"P": P, "status": sv.StatusName(st),
                      "z2": z2, "z3": round(z3,2),
                      "z2z3_objective": round(w2*z2 + w3*z3),
                      "model_z2": model_z2,
                      "z2_agrees_with_model": bool(model_z2 == z2),
                      "asg_hist": [asg.count(j) for j in range(m)],
                      "area_benders_cuts": cuts,
                      "scale": SC}))
    json.dump(asg, open(f'runs/asgopt/p{P}{TAG}.json','w'))
else:
    print(json.dumps({"P": P, "status": sv.StatusName(st)}))
