"""Adversarial unit test for tools/asgopt.py's per-orientation layer handling.

Does NOT import tools/asgopt.py (it is a top-level script that consumes
sys.argv[1] and immediately launches a CP-SAT optimization over a real
instance file). Instead this copies polygon_area / minimum_layer_area /
infeasible_area_core VERBATIM from tools/asgopt.py (as read on the frozen
commit, sourceHash sha256:993208275a...) and drives them with a synthetic
block whose orientations have DIFFERENT layer counts -- a case that does
not occur in any of the 17,550 blocks across data/**/*.json (verified
separately), so it cannot be exercised on real instances.
"""
import math
from ortools.sat.python import cp_model as cp

# ---- verbatim copy from tools/asgopt.py (lines 43-100) --------------------

def polygon_area(poly):
    return abs(sum(poly[k][0] * poly[(k + 1) % len(poly)][1]
                   - poly[(k + 1) % len(poly)][0] * poly[k][1]
                   for k in range(len(poly)))) / 2

def minimum_layer_area(block, layer):
    areas = []
    for orient in block['shape']:
        if layer >= len(orient['layers']):
            return 0.0
        areas.append(polygon_area(orient['layers'][layer]))
    return min(areas) if areas else 0.0

def footprint_area(block):
    return minimum_layer_area(block, 0)

def infeasible_area_core(B, ar, assigned, bay_index, layer):
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
            continue
        active = sub.NewBoolVar(f'a{i}')
        start = sub.NewIntVar(release, latest, f's{i}')
        interval = sub.NewOptionalIntervalVar(start, duration, start + duration,
                                              active, f'iv{i}')
        assumptions.append(active)
        by_index[active.Index()] = i
        intervals.append(interval)
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

# ---- synthetic instance ----------------------------------------------------
# orientation 0: a big square present at BOTH layer 0 and layer 1.
# orientation 1: the SAME footprint at layer 0, but has NO layer 1 at all
#                (a valid physical shape -- e.g. a single-tier stack in this
#                orientation). len(orient['layers']) differs across
#                orientations for this block.
BIG_SQUARE = [[0, 0], [10, 0], [10, 10], [0, 10]]  # area 100

block_mixed_layers = {
    'release_time': 0.0,
    'processing_time': 5.0,
    'due_date': 100.0,
    'shape': [
        {'layers': [BIG_SQUARE, BIG_SQUARE]},   # orientation 0: layers 0 and 1
        {'layers': [BIG_SQUARE]},               # orientation 1: layer 0 ONLY
    ],
}

ar = [50.0]  # bay area 50 -- smaller than the 100-area footprint, so if the
             # tool wrongly charged 100 demand at layer 1 it would (wrongly)
             # certify infeasibility for a block that CAN occupy layer 1 with
             # zero area under orientation 1.

results = {}

# Test 1: minimum_layer_area at layer=1 must be 0.0 (orientation 1 has no
# layer 1, so the block's minimum possible demand at height 1 is 0 -- it is
# NOT forced to occupy area there).
m1 = minimum_layer_area(block_mixed_layers, 1)
results['minimum_layer_area_layer1'] = m1
results['test1_layer1_is_zero'] = (m1 == 0.0)

# Test 2: infeasible_area_core at layer=1 with ONLY this block assigned must
# NOT report an infeasibility core, because demand<=0 makes the tool `continue`
# (skip) the block entirely rather than add a false cumulative demand.
core_layer1 = infeasible_area_core([block_mixed_layers], ar, [0], 0, 1)
results['core_layer1'] = core_layer1
results['test2_no_false_core_at_layer1'] = (core_layer1 == [])

# Control (sanity that the harness actually detects real infeasibility):
# TWO blocks, both orientations having a 100-area footprint at layer 0
# (bay area is only 50), assigned to the same bay/layer 0 -- this MUST be
# reported infeasible by the same code path, otherwise the harness itself
# is broken and test2's negative result would be meaningless.
block_uniform = {
    'release_time': 0.0,
    'processing_time': 5.0,
    'due_date': 100.0,
    'shape': [
        {'layers': [BIG_SQUARE]},
        {'layers': [BIG_SQUARE]},
    ],
}
core_layer0 = infeasible_area_core(
    [block_uniform, block_uniform], ar, [0, 1], 0, 0)
results['core_layer0_control'] = core_layer0
results['test3_control_detects_real_infeasibility'] = (core_layer0 != [])

import json
print(json.dumps(results, indent=2))
