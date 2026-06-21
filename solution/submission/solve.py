"""solve.py -- glue: assign blocks to bays, pack each bay over time, emit a solution dict.

Architecture (MODEL F1 decomposition): assignment (drives Z2,Z3 + per-bay contention) -> per-bay
footprint-disjoint packing (packer.py, drives Z1) -> operations dict. Timing here is the simple
entry+proc schedule the packer produces; the exact timing LP is a later refinement.

Assignment policies: 'pref' (most-preferred fitting bay, min Z3) and 'bal' (least time-loaded fitting
bay, cuts contention -> Z1). solve() with policy='best' returns whichever yields the lower objective.
"""
import math
from packer import block_meta


def ilp_assign(prob, tl=15.0):
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
        sh = braw[i]["shape"][0]["layers"]
        xs = [v[0] for l in sh for v in l]; ys = [v[1] for l in sh for v in l]
        a = max(1, (max(xs) - min(xs))) * max(1, (max(ys) - min(ys)))
        return int(a * max(1, braw[i].get("processing_time", 1)))

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
    for j in range(m):                                    # energetic area-over-time -> bias to Z1=0-packable
        cm.Add(sum(x[i][j] * aabb_proc(i) for i in range(n)) <= int(areas[j] * MAXT))
    cm.Minimize(w2 * D + w3 * SCALE * o3)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(tl); solver.parameters.num_search_workers = 4
    st = solver.Solve(cm)
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    return {i: next(j for j in range(m) if solver.Value(x[i][j]) == 1) for i in range(n)}


def serialize_floor(prob):
    """Bulletproof always-feasible solution: process blocks one at a time per bay (next ENTRY >= previous
    EXIT in that bay), each at a fitting orientation's minimal valid position. Single occupancy => zero
    spatial overlap => crane+collision trivially satisfied; containment holds because each block is placed
    in a bay+orientation it fits. This is the -1 insurance and the final GUARD fallback. Robust to any
    valid instance; never raises (degenerate block -> placed in largest bay, orient 0, clamped)."""
    bays = prob["bays"]; nb = len(bays)
    bay_last = [0] * nb
    placements = {}
    def safe_meta(i):
        try:
            return block_meta(prob["blocks"][i], i)
        except Exception:
            return {"id": i, "release": prob["blocks"][i].get("release_time", 0),
                    "proc": prob["blocks"][i].get("processing_time", 1),
                    "due": prob["blocks"][i].get("due_date", 0), "orients": [(0, 0.0, 0.0, 1.0, 1.0)]}
    metas = [safe_meta(i) for i in range(len(prob["blocks"]))]
    order = sorted(range(len(metas)), key=lambda i: (metas[i]["due"], metas[i]["proc"]))
    for i in order:
        mt = metas[i]; b = prob["blocks"][i]
        chosen = None
        try:
            for j, bay in enumerate(bays):
                for (oi, lx0, ly0, lx1, ly1) in mt["orients"]:
                    if math.ceil(-lx0) <= math.floor(bay["width"] - lx1) and \
                       math.ceil(-ly0) <= math.floor(bay["height"] - ly1):
                        chosen = (j, oi, lx0, ly0); break
                if chosen:
                    break
        except Exception:
            chosen = None
        if chosen is None:                       # degenerate / fits no bay -> largest bay, orient 0
            j = max(range(nb), key=lambda j: bays[j]["width"] * bays[j]["height"]) if nb else 0
            ors = mt.get("orients") or [(0, 0.0, 0.0, 1.0, 1.0)]   # never index an empty orient list
            oi, lx0, ly0 = ors[0][0], ors[0][1], ors[0][2]
            chosen = (j, oi, lx0, ly0)
        j, oi, lx0, ly0 = chosen
        # f > e always (P>=1 internally; max(1,P) guards a stray P=0 so EXIT != ENTRY)
        e = max(b.get("release_time", 0), bay_last[j]); f = e + max(1, b.get("processing_time", 1))
        bay_last[j] = f
        placements[i] = (j, max(0, math.ceil(-lx0)), max(0, math.ceil(-ly0)), oi, e, f)
    return build_ops(placements)


def _fits(bay, mt):
    for (_, lx0, ly0, lx1, ly1) in mt["orients"]:
        if math.ceil(-lx0) <= math.floor(bay["width"] - lx1) and \
           math.ceil(-ly0) <= math.floor(bay["height"] - ly1):
            return True
    return False


def _fitting_bays(bays, mt):
    return [j for j, bay in enumerate(bays) if _fits(bay, mt)]


def _assign(prob, metas, policy):
    bays = prob["bays"]
    timeload = [0] * len(bays)
    assign = {}
    # assign in EDD order so contention estimate reflects schedule pressure
    order = sorted(range(len(metas)), key=lambda i: (metas[i]["due"], metas[i]["proc"]))
    for i in order:
        mt = metas[i]
        fit = _fitting_bays(bays, mt)
        if not fit:
            fit = list(range(len(bays)))  # degenerate; packer will serialize/fallback
        prefs = prob["blocks"][mt["id"]]["bay_preferences"]
        if policy == "pref":
            j = max(fit, key=lambda j: prefs[j])
        else:  # bal
            j = min(fit, key=lambda j: (timeload[j], -prefs[j]))
        assign[mt["id"]] = j
        timeload[j] += mt["proc"]
    return assign


def _pack_all(prob, metas, assign):
    from packer import pack_bay
    bays = prob["bays"]
    by_bay = {j: [] for j in range(len(bays))}
    for mt in metas:
        by_bay[assign[mt["id"]]].append(mt)
    placements = {}  # id -> (bay, x, y, oi, e, f)
    for j, bay in enumerate(bays):
        grp = sorted(by_bay[j], key=lambda mt: (mt["due"], mt["proc"]))
        for (bid, x, y, oi, e, f) in pack_bay(bay["width"], bay["height"], grp):
            placements[bid] = (j, x, y, oi, e, f)
    return placements


def build_ops(placements):
    ops = {}
    for bid, (j, x, y, oi, e, f) in placements.items():
        ops.setdefault(str(int(f)), []).append({"type": "EXIT", "block_id": bid, "bay_id": j})
    for bid, (j, x, y, oi, e, f) in placements.items():
        ops.setdefault(str(int(e)), []).append({"type": "ENTRY", "block_id": bid, "bay_id": j,
                                                 "x": int(x), "y": int(y), "orient_idx": oi})
    for t in ops:
        ops[t].sort(key=lambda o: (0 if o["type"] == "EXIT" else 1, o["block_id"]))
    return {"operations": {t: ops[t] for t in sorted(ops, key=int)}}


def _pick_assignment(prob, metas):
    """Choose pref vs bal by heuristic objective; return the assignment dict."""
    import utils
    best = None
    for pol in ("pref", "bal"):
        asg = _assign(prob, metas, pol)
        r = utils.check_feasibility(prob, build_ops(_pack_all(prob, metas, asg)))
        if r["feasible"] and (best is None or r["objective"] < best[0]):
            best = (r["objective"], asg)
    return best[1] if best else _assign(prob, metas, "pref")


def solve_cpsat(prob, tl):
    """Assign (heuristic best), then pack each bay with CP-SAT under a per-bay time budget;
    fall back to the heuristic packer for any bay CP-SAT can't model/solve."""
    from cpsat_packer import pack_bay_cpsat
    metas = [block_meta(prob["blocks"][i], i) for i in range(len(prob["blocks"]))]
    assign = _pick_assignment(prob, metas)
    bays = prob["bays"]
    by_bay = {j: [] for j in range(len(bays))}
    for mt in metas:
        by_bay[assign[mt["id"]]].append(mt)
    # split time across bays by block count
    total_blocks = sum(len(v) for v in by_bay.values()) or 1
    placements = {}
    for j, bay in enumerate(bays):
        grp = sorted(by_bay[j], key=lambda mt: (mt["due"], mt["proc"]))
        if not grp:
            continue
        bay_tl = max(2.0, tl * len(grp) / total_blocks)
        res = pack_bay_cpsat(bay["width"], bay["height"], grp, prob["blocks"], bay_tl)
        if res is None:
            from packer import pack_bay
            res = pack_bay(bay["width"], bay["height"], grp)
        for (bid, x, y, oi, e, f) in res:
            placements[bid] = (j, x, y, oi, e, f)
    return build_ops(placements)


def solve_hybrid(prob, t_improve, t_polish):
    """Improver finds assignment+order (the dominant lever), then CP-SAT polishes each bay's packing
    (footprint-disjoint optimum-ish for that assignment), warm-started from the improver's placement.
    t_polish is split across bays by block count. Keeps the improver's bay result if CP-SAT can't beat it."""
    import improve, utils
    from cpsat_packer import pack_bay_cpsat
    sol = improve.improve(prob, t_improve)
    bays = prob["bays"]
    # parse improver assignment + placement
    asg = {}; place = {}
    for t, ops in sol["operations"].items():
        for o in ops:
            if o["type"] == "ENTRY":
                asg[o["block_id"]] = o["bay_id"]
                place[o["block_id"]] = (o["x"], o["y"], o["orient_idx"], int(t))
    metas = {i: block_meta(prob["blocks"][i], i) for i in range(len(prob["blocks"]))}
    by_bay = {j: [] for j in range(len(bays))}
    for i, j in asg.items():
        by_bay[j].append(metas[i])
    total = sum(len(v) for v in by_bay.values()) or 1
    new_place = {}
    for j, bay in enumerate(bays):
        grp = sorted(by_bay[j], key=lambda m: (m["due"], m["proc"]))
        if not grp:
            continue
        imp_z1 = sum(max(0, place[m["id"]][3] + m["proc"] - m["due"]) for m in grp)
        btl = max(2.0, t_polish * len(grp) / total)
        hint = {m["id"]: place[m["id"]] for m in grp}
        res = pack_bay_cpsat(bay["width"], bay["height"], grp, prob["blocks"], btl, hint=hint)
        if res is None:
            res = [(m["id"], place[m["id"]][0], place[m["id"]][1], place[m["id"]][2],
                    place[m["id"]][3], place[m["id"]][3] + m["proc"]) for m in grp]
        cp_z1 = sum(max(0, f - metas[bid]["due"]) for (bid, x, y, oi, e, f) in res)
        if cp_z1 > imp_z1:   # keep improver's bay if CP-SAT polish didn't help
            res = [(m["id"], place[m["id"]][0], place[m["id"]][1], place[m["id"]][2],
                    place[m["id"]][3], place[m["id"]][3] + m["proc"]) for m in grp]
        for (bid, x, y, oi, e, f) in res:
            new_place[bid] = (j, x, y, oi, e, f)
    return build_ops(new_place)


def _orient_off(mt, oi):
    for (o, lx0, ly0, lx1, ly1) in mt["orients"]:
        if o == oi:
            return lx0, ly0, lx1, ly1
    return mt["orients"][0][1:]


def solve_lns(prob, t_improve, t_polish, K=14, rep_tl=2.5, seed=0, t_master=0, seed_assign=None, cp_workers=4):
    """Improver (assignment+order) then LNS polish: repeatedly re-optimize a hot subset (most-tardy
    blocks of the most-tardy bay) with CP-SAT while the rest of that bay stays FIXED as obstacles.
    CP-SAT converges on the small free set, finding local improvements the non-converging full-bay
    polish misses. Accept a repair only if it lowers the subset's tardiness."""
    import time, random, improve, utils
    from cpsat_packer import pack_bay_cpsat
    rng = random.Random(seed)
    t0 = time.time()
    ma = seed_assign
    if ma is None and t_master > 0:
        try:
            import master
            ma = master.master_assignment(prob, t_master)
        except Exception:
            ma = None
    sol = improve.improve(prob, t_improve, seed=seed, master_assign=ma)
    bays = prob["bays"]
    metas = {i: block_meta(prob["blocks"][i], i) for i in range(len(prob["blocks"]))}
    # parse placement: id -> (bay,x,y,oi,e,f)
    place = {}
    for t, ops in sol["operations"].items():
        for o in ops:
            if o["type"] == "ENTRY":
                place[o["block_id"]] = [o["bay_id"], o["x"], o["y"], o["orient_idx"], int(t), None]
            else:
                pass
    for t, ops in sol["operations"].items():
        for o in ops:
            if o["type"] == "EXIT":
                place[o["block_id"]][5] = int(t)

    def box(i):
        # integer CELL box (covers the footprint), consistent with cpsat_packer's offlo=floor/offhi=ceil
        j, x, y, oi, e, f = place[i]
        lx0, ly0, lx1, ly1 = _orient_off(metas[i], oi)
        return (math.floor(x + lx0), math.floor(y + ly0), math.ceil(x + lx1), math.ceil(y + ly1), e, f)

    def tard(i):
        return max(0, place[i][5] - metas[i]["due"])

    by_bay = {j: [] for j in range(len(bays))}
    for i, p in place.items():
        by_bay[p[0]].append(i)

    stale = 0
    while time.time() - t0 < t_improve + t_polish and stale < 3 * len(bays) + 3:
        # pick a bay with tardiness, weighted toward the worst
        bt = [(sum(tard(i) for i in by_bay[j]), j) for j in range(len(bays)) if by_bay[j]]
        bt = [(s, j) for s, j in bt if s > 0]
        if not bt:
            break
        bt.sort(reverse=True)
        j = bt[0][1] if rng.random() < 0.6 else rng.choice(bt)[1]
        tardy = [i for i in by_bay[j] if tard(i) > 0]
        if not tardy:
            stale += 1; continue
        # hot subset: up to K, biased to most-tardy, + a few non-tardy room-neighbors
        tardy.sort(key=tard, reverse=True)
        hot = tardy[:K]
        others = [i for i in by_bay[j] if i not in hot]
        rng.shuffle(others)
        hot += others[:max(0, K - len(hot)) // 2]   # a few movable neighbors for room
        hotset = set(hot)
        fixed = [box(i) for i in by_bay[j] if i not in hotset]
        free = [metas[i] for i in hot]
        hint = {i: (place[i][1], place[i][2], place[i][3], place[i][4]) for i in hot}
        old = sum(tard(i) for i in hot)
        res = pack_bay_cpsat(bays[j]["width"], bays[j]["height"], free, prob["blocks"],
                             rep_tl, hint=hint, fixed=fixed, workers=cp_workers)
        if not res:
            stale += 1; continue
        new = sum(max(0, f - metas[bid]["due"]) for (bid, x, y, oi, e, f) in res)
        if new < old - 1e-9:
            for (bid, x, y, oi, e, f) in res:
                place[bid] = [j, x, y, oi, e, f]
            stale = 0
        else:
            stale += 1
    return build_ops({i: tuple(place[i]) for i in place})


def solve_prism(prob, t_improve, t_polish, t_prism=30, n_bays=2, seed=0):
    """solve_lns, then prism-repack the most-tardy bay(s) (silhouette-disjoint, denser than cuboid) as a
    final polish. Accept a bay's prism re-pack only if it lowers that bay's tardiness."""
    import utils, prism_polish, time
    sol = solve_lns(prob, t_improve, t_polish, seed=seed)
    bays = prob["bays"]; B = prob["blocks"]
    place = {}
    for t, ops in sol["operations"].items():
        for o in ops:
            if o["type"] == "ENTRY":
                place[o["block_id"]] = [o["bay_id"], o["x"], o["y"], o["orient_idx"], int(t), None]
    for t, ops in sol["operations"].items():
        for o in ops:
            if o["type"] == "EXIT":
                place[o["block_id"]][5] = int(t)
    # per-bay tardiness
    bz = {}
    for i, (j, x, y, oi, e, f) in place.items():
        bz[j] = bz.get(j, 0) + max(0, f - B[i]["due_date"])
    worst = [j for j, _ in sorted(bz.items(), key=lambda kv: -kv[1]) if bz[j] > 0][:n_bays]
    if not worst:
        return sol
    sil = prism_polish._silhouettes(prob)
    per = max(8.0, t_prism / max(1, len(worst)))
    for j in worst:
        ids = [i for i in place if place[i][0] == j]
        items = [(i, B[i]["release_time"], B[i]["processing_time"], B[i]["due_date"], place[i][3]) for i in ids]
        old = sum(max(0, place[i][5] - B[i]["due_date"]) for i in ids)
        newpl, newz = prism_polish.repack_bay_prism(bays[j]["width"], bays[j]["height"], items, sil, tl=per)
        if newz < old - 1e-9:
            for i, (x, y, oi, e, f) in newpl.items():
                place[i] = [j, x, y, oi, e, f]
    cand = build_ops({i: tuple(place[i]) for i in place})
    r = utils.check_feasibility(prob, cand)
    return cand if r["feasible"] else sol     # keep cuboid sol if prism re-pack broke feasibility


def solve(prob, policy="best"):
    metas = [block_meta(prob["blocks"][i], i) for i in range(len(prob["blocks"]))]
    if policy in ("pref", "bal"):
        return build_ops(_pack_all(prob, metas, _assign(prob, metas, policy)))
    # best: try both, return lower objective (cheap; uses utils for scoring at call site if available)
    import utils
    best = None
    for pol in ("pref", "bal"):
        sol = build_ops(_pack_all(prob, metas, _assign(prob, metas, pol)))
        r = utils.check_feasibility(prob, sol)
        if r["feasible"] and (best is None or r["objective"] < best[1]):
            best = (sol, r["objective"])
    return best[0] if best else build_ops(_pack_all(prob, metas, _assign(prob, metas, "pref")))
