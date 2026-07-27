"""assign.py -- exact block->bay assignment (CP-SAT), v3 standalone."""
import math
from ortools.sat.python import cp_model
from shapely.geometry import Polygon
from shapely.ops import unary_union


def ilp_assign(prob, tl=15.0, relax_energetic=False, forbid=None, set_forbid=None,
               delta=None, window_caps=None):
    """Exact CP-SAT block->bay assignment minimizing w2*Z2 + w3*Z3 (these depend ONLY on the assignment,
    not on positions/timing -- utils:1411-1426). Returns {block_id: bay_id} or None. Packing this to Z1=0
    (improve with fix_assign=True) is near-optimal on small/low-congestion instances: the improver's greedy
    pref-migrate is biased to Z3=0 and misses the Z2<->Z3 tradeoff this finds exactly."""
    try:
        from ortools.sat.python import cp_model
    except Exception:
        return None
    braw = prob["blocks"]; bays = prob["bays"]; n = len(braw); m = len(bays)
    if m < 2 or n == 0:
        return None
    W = prob.get("weights", {}); w2 = W.get("w2", 1); w3 = W.get("w3", 1)
    wl = [b.get("workload", 0) for b in braw]
    pref = [b["bay_preferences"] for b in braw]
    smax = [max(p) for p in pref]
    areas = [bays[j]["width"] * bays[j]["height"] for j in range(m)]
    avg = sum(areas) / m; SCALE = 1000
    U = [round(avg / areas[j] * SCALE) for j in range(m)]

    def aabb_proc(i):
        # TRUE footprint area (polygon union), not bbox: with rectangularity
        # ~0.72 the bbox overestimates ~1.4x, so the energetic cut below could
        # exclude genuinely packable (even optimal) assignments on congested
        # instances. bbox retained only as a fallback when shapely is absent.
        sh = braw[i]["shape"][0]["layers"]
        a = None
        try:
            from shapely.geometry import Polygon as _Pg
            from shapely.ops import unary_union as _uu
            a = _uu([_Pg(l).buffer(0) for l in sh if len(l) >= 3]).area
        except Exception:
            a = None
        if not a or a <= 0:
            xs = [v[0] for l in sh for v in l]
            ys = [v[1] for l in sh for v in l]
            a = max(1, (max(xs) - min(xs))) * max(1, (max(ys) - min(ys)))
        return int(round(a * max(1, braw[i].get("processing_time", 1))))

    MAXT = max(b["due_date"] for b in braw) + max(max(1, b.get("processing_time", 1)) for b in braw)
    cm = cp_model.CpModel()
    x = [[cm.NewBoolVar("") for _ in range(m)] for _ in range(n)]
    for i in range(n):
        cm.Add(sum(x[i]) == 1)
    sw = max(1, sum(wl))
    load = [cm.NewIntVar(0, sw, "") for _ in range(m)]
    for j in range(m):
        cm.Add(load[j] == sum(x[i][j] * wl[i] for i in range(n)))
    WLj = [cm.NewIntVar(0, U[j] * sw, "") for j in range(m)]
    for j in range(m):
        cm.Add(WLj[j] == U[j] * load[j])
    D = cm.NewIntVar(0, max(U) * sw, "")
    for j1 in range(m):
        for j2 in range(m):
            if j1 != j2:
                cm.Add(D >= WLj[j1] - WLj[j2])
    o3 = cm.NewIntVar(0, max(1, sum(smax)), "")
    cm.Add(o3 == sum(x[i][j] * (smax[i] - pref[i][j]) for i in range(n) for j in range(m)))
    if delta is not None:
        # WINDOWED energetic capacity (universal, sec 78 measured sustained
        # density): for every bay j and time window [t1,t2] spanned by
        # release/due values, blocks fully inside the window cannot carry
        # more than delta * bay_area * |window| of area-time energy.
        Rv = [int(math.ceil(braw[i]["release_time"] - 1e-9))
              for i in range(n)]
        Dv = [int(round(braw[i]["due_date"])) for i in range(n)]
        Pv = [max(1, int(math.ceil(braw[i]["processing_time"] - 1e-9)))
              for i in range(n)]
        Ar = [aabb_proc(i) // max(1, Pv[i]) for i in range(n)]
        def _thin(vals, cap=35):
            vs = sorted(set(vals))
            if len(vs) <= cap:
                return vs
            step = (len(vs) - 1) / float(cap - 1)
            return sorted({vs[int(round(k * step))] for k in range(cap)})
        # complexity budget: a SUBSET of valid windows is still sound;
        # unbounded |R|x|D| grids made model BUILD itself explode at
        # server scale (sec 99, P6 exception).
        t1s = _thin(Rv); t2s = _thin(Dv)
        for t1 in t1s:
            for t2 in t2s:
                if t2 - t1 < 3:
                    continue
                terms = []
                for i in range(n):
                    # mandatory-part energy: forced overlap with [t1,t2]
                    ov = Pv[i] - max(0, t1 - Rv[i]) - max(0, Dv[i] - t2)
                    if ov > 0:
                        terms.append((i, Ar[i] * ov))
                if len(terms) < 2:
                    continue
                for j in range(m):
                    cm.Add(sum(x[i][j] * e for i, e in terms)
                           <= int(delta * areas[j] * (t2 - t1)))
    if window_caps:
        # oracle-measured capacity cuts (sec 106): the locked-anneal
        # probe demonstrated bay j cannot pack more than CAP mandatory
        # energy in [t1,t2]; the ILP must reroute the excess at minimum
        # preference cost. RHS comes from geometry, not a constant.
        Rw = [int(math.ceil(braw[i]["release_time"] - 1e-9))
              for i in range(n)]
        Dw = [int(round(braw[i]["due_date"])) for i in range(n)]
        Pw = [max(1, int(math.ceil(braw[i]["processing_time"] - 1e-9)))
              for i in range(n)]
        Aw = [aabb_proc(i) // max(1, Pw[i]) for i in range(n)]
        for (j, t1, t2, cap) in window_caps:
            terms = []
            for i in range(n):
                ov = Pw[i] - max(0, t1 - Rw[i]) - max(0, Dw[i] - t2)
                if ov > 0:
                    terms.append((i, Aw[i] * ov))
            if terms:
                cm.Add(sum(x[i][int(j)] * e for i, e in terms)
                       <= int(cap))
    if not relax_energetic:                               # energetic area-over-time -> bias to Z1=0-packable
        for j in range(m):
            cm.Add(sum(x[i][j] * aabb_proc(i) for i in range(n)) <= int(areas[j] * MAXT))
    for A in (forbid or []):                              # NO-GOOD CUTS (combinatorial Benders): forbid exact
        cm.Add(sum(x[i][A[i]] for i in range(n) if i in A) <= len(A) - 1)   # assignments geompolish proved un-packable
    for (j, S) in (set_forbid or []):                     # SET CUTS: the over-packed bay's block-set can't ALL be
        cm.Add(sum(x[i][j] for i in S) <= len(S) - 1)     # in bay j together (prunes the whole un-packable cluster)
    cm.Minimize(w2 * D + w3 * SCALE * o3)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(tl); solver.parameters.num_search_workers = 4
    st = solver.Solve(cm)
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    return {i: next(j for j in range(m) if solver.Value(x[i][j]) == 1) for i in range(n)}


