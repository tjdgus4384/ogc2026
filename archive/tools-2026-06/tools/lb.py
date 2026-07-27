"""lb.py -- lower bound (the compass) for OGC2026, via a valid relaxation solved with CP-SAT.

Relaxation (each step only REMOVES constraints, so its optimum <= the true optimum => a valid LB):
  - drop exact geometry + crane; keep only the NECESSARY area condition: concurrent blocks' layer-0
    areas sum to <= capacity (non-overlap within area C implies this).
  - pool all bays into one cumulative resource of capacity C = sum_j W_j*H_j (pooling only adds room).
  => relaxed problem = single cumulative machine, jobs (release R_i, due D_i, proc P_i, demand a_i),
     minimize total tardiness. CP-SAT's best objective bound on this is a valid LB on Z1.

LB_obj = w1*LB_Z1 + w2*0 + w3*LB_Z3, with LB_Z3 = sum_i (Smax_i - max_{j: i fits j} S_ij) (term-wise min;
sum-of-mins <= min-of-sum, so still a valid global LB). Writes data/lb.json {name: obj_LB}.

Pooled is intentionally loose (v1). If gaps stay large it is the looseness to attack next (per-bay
assignment cumulative = tighter, keeps bay separation).

Usage: python3 tools/lb.py [--tl 10] [--inst all|1,2,..]
"""
import sys, json, glob, pathlib, math
from ortools.sat.python import cp_model

ROOT = pathlib.Path(__file__).resolve().parent.parent
LB_PATH = ROOT / "data" / "lb.json"


def shoelace(poly):
    a = 0.0
    for k in range(len(poly)):
        x1, y1 = poly[k]; x2, y2 = poly[(k + 1) % len(poly)]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def min_layer0_area(blk):
    """Min over orientations of layer-0 polygon area (a valid lower bound on the block's footprint demand)."""
    best = math.inf
    for o in blk["shape"]:
        best = min(best, shoelace(o["layers"][0]))
    return best


def fits_any(blk, bays):
    """Bays the block fits in (some orientation). Used for LB_Z3."""
    out = []
    for j, bay in enumerate(bays):
        ok = False
        for o in blk["shape"]:
            xs = [v[0] for l in o["layers"] for v in l]; ys = [v[1] for l in o["layers"] for v in l]
            w = max(xs) - min(xs); h = max(ys) - min(ys)
            if math.ceil(w) <= bay["width"] and math.ceil(h) <= bay["height"]:
                ok = True; break
        if ok:
            out.append(j)
    return out


def block_fits_bay(blk, bay):
    for o in blk["shape"]:
        xs = [v[0] for l in o["layers"] for v in l]; ys = [v[1] for l in o["layers"] for v in l]
        if math.ceil(max(xs) - min(xs)) <= bay["width"] and math.ceil(max(ys) - min(ys)) <= bay["height"]:
            return True
    return False


def lb_z1_perbay(prob, tl):
    """Per-bay area-cumulative relaxation with the bay-assignment kept as a decision (optional intervals).
    Keeps bay separation + area necessity; drops only geometry+crane => valid LB, far tighter than pooled."""
    blocks = prob["blocks"]; bays = prob["bays"]
    maxR = max(b["release_time"] for b in blocks)
    sumP = sum(b["processing_time"] for b in blocks)
    H = maxR + sumP + 1
    m = cp_model.CpModel()
    tards = []
    per_bay_ivs = [[] for _ in bays]; per_bay_dem = [[] for _ in bays]
    for i, b in enumerate(blocks):
        s = m.NewIntVar(b["release_time"], H, f"s{i}")
        e = m.NewIntVar(b["release_time"] + b["processing_time"], H, f"e{i}")
        t = m.NewIntVar(0, H, f"t{i}")
        m.Add(t >= e - b["due_date"]); tards.append(t)
        a = max(1, int(min_layer0_area(b)))
        presences = []
        for j, bay in enumerate(bays):
            if not block_fits_bay(b, bay):
                continue
            pres = m.NewBoolVar(f"x{i}_{j}")
            iv = m.NewOptionalIntervalVar(s, b["processing_time"], e, pres, f"iv{i}_{j}")
            per_bay_ivs[j].append(iv); per_bay_dem[j].append(a); presences.append(pres)
        m.AddExactlyOne(presences)
    for j, bay in enumerate(bays):
        if per_bay_ivs[j]:
            m.AddCumulative(per_bay_ivs[j], per_bay_dem[j], bay["width"] * bay["height"])
    m.Minimize(sum(tards))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = tl
    solver.parameters.num_search_workers = 4
    status = solver.Solve(m)
    has_sol = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    return solver.BestObjectiveBound(), (solver.ObjectiveValue() if has_sol else None)


# -- DFF-cumulative LB (THEORY N2): stronger-than-area necessary condition for concurrent packing ----
# Maximal dual-feasible functions u:[0,1]->[0,1]. For any set packing in WxH:
#   sum_i u(w_i/W)*v(h_i/H) <= 1   (identity DFF => area; non-trivial DFFs are strictly stronger).
# SCOPE: bounds the FOOTPRINT-DISJOINT optimum (DFF necessity is for axis-aligned rectangle non-overlap).

def dff_identity(x):
    return x

def dff_half(x):
    # f(x)=1 if x>1/2, 1/2 at 1/2, x if x<1/2 -- "two items >1/2 can't share a line" (maximal DFF)
    if x > 0.5 + 1e-12:
        return 1.0
    if x < 0.5 - 1e-12:
        return x
    return 0.5

def dff_fs(k):
    # Fekete-Schepers F^(k): rounds sizes against bin divided into k slots (valid maximal DFF family)
    def f(x):
        if x > 0.5 + 1e-12:
            return 1.0 - (math.floor((1.0 - x) * (k + 1))) / k if k else 1.0
        elif x < 0.5 - 1e-12:
            return (math.floor(x * (k + 1))) / k if k else 0.0
        return 0.5
    return f

DFFS = [dff_identity, dff_half, dff_fs(2), dff_fs(3), dff_fs(5)]
SCALE = 10000


def _orients(blk):
    """(min_x,min_y,max_x,max_y) footprint AABB per orientation."""
    out = []
    for o in blk["shape"]:
        xs = [v[0] for l in o["layers"] for v in l]; ys = [v[1] for l in o["layers"] for v in l]
        out.append((min(xs), min(ys), max(xs), max(ys)))
    return out


def _dff_demand(blk, W, H, u, v):
    """min over orientations of round(SCALE * u(w/W)*v(h/H)) -- conservative (valid) demand, capacity SCALE."""
    best = None
    for (lx0, ly0, lx1, ly1) in _orients(blk):
        w = (lx1 - lx0) / W; h = (ly1 - ly0) / H
        if w > 1 + 1e-9 or h > 1 + 1e-9:
            continue
        d = int(round(SCALE * u(min(1.0, w)) * v(min(1.0, h))))
        best = d if best is None else min(best, d)
    return max(0, best if best is not None else 0)


def lb_z1_dff(prob, tl, u, v):
    """Per-bay-assignment cumulative with DFF demands (capacity SCALE) -> valid LB on footprint-disjoint Z1."""
    blocks = prob["blocks"]; bays = prob["bays"]
    maxR = max(b["release_time"] for b in blocks); sumP = sum(b["processing_time"] for b in blocks)
    H = maxR + sumP + 1
    m = cp_model.CpModel(); tards = []
    ivs = [[] for _ in bays]; dem = [[] for _ in bays]
    for i, b in enumerate(blocks):
        s = m.NewIntVar(b["release_time"], H, f"s{i}")
        e = m.NewIntVar(b["release_time"] + b["processing_time"], H, f"e{i}")
        t = m.NewIntVar(0, H, f"t{i}"); m.Add(t >= e - b["due_date"]); tards.append(t)
        pres = []
        for j, bay in enumerate(bays):
            d = _dff_demand(b, bay["width"], bay["height"], u, v)
            if d <= 0 or not block_fits_bay(b, bay):
                continue
            p = m.NewBoolVar(f"x{i}_{j}")
            iv = m.NewOptionalIntervalVar(s, b["processing_time"], e, p, f"iv{i}_{j}")
            ivs[j].append(iv); dem[j].append(d); pres.append(p)
        if pres:
            m.AddExactlyOne(pres)
    for j in range(len(bays)):
        if ivs[j]:
            m.AddCumulative(ivs[j], dem[j], SCALE)
    m.Minimize(sum(tards))
    solver = cp_model.CpSolver(); solver.parameters.max_time_in_seconds = tl
    solver.parameters.num_search_workers = 2
    solver.Solve(m)
    return solver.BestObjectiveBound()


def lb_z3(prob):
    bays = prob["bays"]; total = 0
    for b in prob["blocks"]:
        fit = fits_any(b, bays)
        smax = max(b["bay_preferences"])
        best = max((b["bay_preferences"][j] for j in fit), default=min(b["bay_preferences"]))
        total += smax - best
    return total


def main():
    tl = 10.0; spec = "all"
    for i, a in enumerate(sys.argv):
        if a == "--tl": tl = float(sys.argv[i + 1])
        if a == "--inst": spec = sys.argv[i + 1]
    files = sorted(glob.glob(str(ROOT / "data" / "train" / "*.json")),
                   key=lambda p: int(pathlib.Path(p).stem.split("_")[1]))
    if spec != "all":
        want = {int(x) for x in spec.split(",")}
        files = [f for f in files if int(pathlib.Path(f).stem.split("_")[1]) in want]
    out = json.loads(LB_PATH.read_text()) if LB_PATH.exists() else {}
    print(f"{'inst':8s} {'w1':>7s} {'LB_Z1':>8s} {'LB_Z3':>6s} {'obj_LB':>13s} {'relaxOpt(Z1)':>12s}")
    for f in files:
        name = pathlib.Path(f).stem; prob = json.loads(pathlib.Path(f).read_text())
        w = prob["weights"]
        z1bound, z1opt = lb_z1_perbay(prob, tl)
        z3 = lb_z3(prob)
        obj_lb = w["w1"] * z1bound + w["w3"] * z3
        out[name] = obj_lb
        print(f"{name:8s} {w['w1']:7d} {z1bound:8.0f} {z3:6d} {obj_lb:13.0f} "
              f"{(f'{z1opt:.0f}' if z1opt else '-'):>12s}")
    LB_PATH.write_text(json.dumps(out, indent=0, sort_keys=True))
    print(f"\nwrote {LB_PATH}")


if __name__ == "__main__":
    main()
