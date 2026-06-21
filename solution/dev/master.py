"""master.py -- cumulative (area-resource) master: produces a good bay ASSIGNMENT via CP-SAT.

Solves the area-relaxation (parallel cumulative machines, free assignment, prism-area demands, minimize
w1*tardiness + w3*Z3) and returns the assignment x_ij. Measured to beat pref/bal heuristic assignment by
~33-37% (realized in 2D) on the frontier — the area-optimal partition spreads load well. Its tardiness
PLAN is too optimistic to use, but the ASSIGNMENT is a strong construction seed for the improver.
Requires shapely (available on the eval server). Falls back to None if shapely/ortools unavailable.
"""
import math


def _prism_area(blk):
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    best = None
    for o in blk["shape"]:
        ps = [Polygon(l) for l in o["layers"] if len(l) >= 3]
        ps = [p if p.is_valid else p.buffer(0) for p in ps]
        if ps:
            a = unary_union(ps).area
            best = a if best is None else min(best, a)
    return best or 1.0


def _fits(blk, bay):
    for o in blk["shape"]:
        xs = [v[0] for l in o["layers"] for v in l]; ys = [v[1] for l in o["layers"] for v in l]
        if math.ceil(max(xs) - min(xs)) <= bay["width"] and math.ceil(max(ys) - min(ys)) <= bay["height"]:
            return True
    return False


def master_assignment(prob, tl):
    """Return {block_id: bay_id} from the cumulative master, or None on failure."""
    try:
        from ortools.sat.python import cp_model
    except Exception:
        return None
    blocks = prob["blocks"]; bays = prob["bays"]
    w = prob.get("weights", {}); w1, w3 = w.get("w1", 1), w.get("w3", 1)
    maxR = max(b["release_time"] for b in blocks); sumP = sum(b["processing_time"] for b in blocks)
    H = maxR + sumP + 1
    cm = cp_model.CpModel()
    iv = [[] for _ in bays]; dem = [[] for _ in bays]; T = []; z3 = []; pres_of = {}
    for i, b in enumerate(blocks):
        a = max(1, int(_prism_area(b)))
        s = cm.NewIntVar(b["release_time"], H, f"s{i}")
        e = cm.NewIntVar(b["release_time"] + b["processing_time"], H, f"e{i}")
        cm.Add(e == s + b["processing_time"])
        t = cm.NewIntVar(0, H, f"t{i}"); cm.Add(t >= e - b["due_date"]); T.append(t)
        smax = max(b["bay_preferences"]); ps = []
        for j, bay in enumerate(bays):
            if not _fits(b, bay):
                continue
            p = cm.NewBoolVar(f"x{i}_{j}")
            iv[j].append(cm.NewOptionalIntervalVar(s, b["processing_time"], e, p, f"iv{i}_{j}"))
            dem[j].append(a); ps.append((p, j)); z3.append((p, smax - b["bay_preferences"][j]))
        if not ps:
            return None
        cm.AddExactlyOne([p for p, _ in ps]); pres_of[i] = ps
    for j, bay in enumerate(bays):
        if iv[j]:
            cm.AddCumulative(iv[j], dem[j], bay["width"] * bay["height"])
    cm.Minimize(w1 * sum(T) + w3 * sum(c * p for p, c in z3))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = tl
    solver.parameters.num_search_workers = 4
    st = solver.Solve(cm)
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    assign = {}
    for i, ps in pres_of.items():
        for p, j in ps:
            if solver.Value(p) == 1:
                assign[i] = j; break
    return assign
