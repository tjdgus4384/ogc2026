"""asa.py -- UNIFIED assignment search with geompolish (crane-aware) as the decoder.

The whole problem is ONE thing: choose a block->bay ASSIGNMENT minimizing
    w1*Z1(assignment, via TRUE crane-aware packing) + w2*Z2(assignment) + w3*Z3(assignment)
where Z2 = floor(max_{j1!=j2} |u_j1*load_j1 - u_j2*load_j2|), Z3 = Sum_i (max(pref_i) - pref_i[bay_i]),
and Z1 = Sum_j tardiness of bay j when packed crane-aware (geompack). Z2/Z3 are closed-form from the
assignment; Z1 needs the packer. So: local-search the assignment, re-packing ONLY the bays a move touches
(incremental), scoring with the exact objective. One gate-free search for every regime -- congested
(drives Z1) and assignment-dominated (trades Z2/Z3 while the packer keeps Z1 low). Replaces the fragmented
crane-blind assignment methods whose decoders disagreed with the final geompolish.
"""
import math, random, time
from packer import block_meta
from solve import build_ops


def solve_asa(prob, utils, time_budget, seed_sols=None, seed=0):
    import geompack
    if geompack._lib() is None:
        return None
    bays = prob["bays"]; nb = len(bays); braw = prob["blocks"]; n = len(braw)
    if nb < 2 or n == 0:
        return None
    W = prob.get("weights", {}); w1 = W.get("w1", 1.0); w2 = W.get("w2", 1.0); w3 = W.get("w3", 1.0)
    wl = [b.get("workload", 0) for b in braw]
    pref = [b["bay_preferences"] for b in braw]
    smax = [max(p) for p in pref]
    areas = [bays[j]["width"] * bays[j]["height"] for j in range(nb)]
    avg = sum(areas) / nb
    u = [avg / a for a in areas]
    MB = {i: block_meta(braw[i], i) for i in range(n)}
    for i in MB:
        MB[i]["proc"] = max(1, MB[i]["proc"]); MB[i]["due"] = braw[i]["due_date"]; MB[i]["release"] = braw[i].get("release_time", 0)
    gp = geompack.GeomPacker(prob)
    rng = random.Random(seed)

    def _asg_of(sol):
        a = {}
        for tt, ops in sol["operations"].items():
            for o in ops:
                if o["type"] == "ENTRY":
                    a[o["block_id"]] = o["bay_id"]
        return a

    # pick the best feasible seed assignment to start from
    start = None; startobj = float("inf")
    for s in (seed_sols or []):
        try:
            r = utils.check_feasibility(prob, s)
            if r["feasible"] and r["objective"] < startobj:
                startobj = r["objective"]; start = _asg_of(s)
        except Exception:
            pass
    if start is None:
        return None
    asg = dict(start)

    byb = {j: [] for j in range(nb)}
    for i, j in asg.items():
        byb[j].append(i)
    load = [sum(wl[i] for i in byb[j]) for j in range(nb)]
    packed = {}; bayz1 = [0.0] * nb
    for j in range(nb):
        packed[j] = gp.pack_bay(byb[j], bays[j]["width"], bays[j]["height"], MB) if byb[j] else {}
        bayz1[j] = sum(max(0.0, f - MB[b]["due"]) for b, (x, y, oi, e, f) in packed[j].items())
    obj3 = sum(smax[i] - pref[i][asg[i]] for i in range(n))

    def _z2():
        return math.floor(max(abs(u[j1] * load[j1] - u[j2] * load[j2])
                              for j1 in range(nb) for j2 in range(nb) if j1 != j2))

    def _obj():
        return w1 * sum(bayz1) + w2 * _z2() + w3 * obj3

    cur = _obj(); best = cur
    best_packed = {j: dict(packed[j]) for j in range(nb)}; best_asg = dict(asg)
    t0 = time.time(); T = max(1.0, cur * 0.01 + 1.0)
    it = 0
    while time.time() - t0 < time_budget:
        it += 1
        b = rng.randrange(n); o = asg[b]
        # MOVE TYPES: pref-toward (Z3), least-loaded (Z2), random; plus occasional swap.
        r = rng.random()
        if r < 0.34:
            nbay = max(range(nb), key=lambda j: pref[b][j])      # most-preferred bay
        elif r < 0.67:
            nbay = min(range(nb), key=lambda j: load[j])         # least-loaded bay
        else:
            nbay = rng.randrange(nb)
        if nbay == o:
            nbay = (o + 1 + rng.randrange(nb - 1)) % nb
        # apply move, re-pack ONLY bays o and nbay
        byb[o].remove(b); byb[nbay].append(b)
        po = gp.pack_bay(byb[o], bays[o]["width"], bays[o]["height"], MB) if byb[o] else {}
        pn = gp.pack_bay(byb[nbay], bays[nbay]["width"], bays[nbay]["height"], MB)
        z1o = sum(max(0.0, f - MB[bb]["due"]) for bb, (x, y, oi, e, f) in po.items())
        z1n = sum(max(0.0, f - MB[bb]["due"]) for bb, (x, y, oi, e, f) in pn.items())
        oldp_o, oldp_n = packed[o], packed[nbay]
        oldz_o, oldz_n = bayz1[o], bayz1[nbay]
        oldobj3 = obj3
        load[o] -= wl[b]; load[nbay] += wl[b]
        packed[o] = po; packed[nbay] = pn; bayz1[o] = z1o; bayz1[nbay] = z1n
        asg[b] = nbay; obj3 = obj3 + pref[b][o] - pref[b][nbay]
        cand = _obj()
        if cand < cur - 1e-9 or rng.random() < math.exp(-(cand - cur) / max(1e-9, T)):
            cur = cand
            if cand < best - 1e-9:
                best = cand; best_asg = dict(asg)
                best_packed = {j: dict(packed[j]) for j in range(nb)}
        else:                                                    # revert
            byb[nbay].remove(b); byb[o].append(b)
            load[o] += wl[b]; load[nbay] -= wl[b]
            packed[o] = oldp_o; packed[nbay] = oldp_n; bayz1[o] = oldz_o; bayz1[nbay] = oldz_n
            asg[b] = o; obj3 = oldobj3
        T *= 0.9995
    pl = {}
    for j in range(nb):
        for i, (x, y, oi, e, f) in best_packed[j].items():
            pl[i] = (j, x, y, oi, e, f)
    out = build_ops(pl)
    # SAFETY best-of: the per-bay packs can occasionally compose to an infeasible/worse whole (a congested bay
    # whose internal obj-estimate diverged from the validator). Never return worse-or-infeasible than the seed.
    ro = utils.check_feasibility(prob, out)
    if (not ro.get("feasible")) or ro.get("objective", 1e18) > startobj - 1e-9:
        for s in (seed_sols or []):
            r = utils.check_feasibility(prob, s)
            if r.get("feasible") and abs(r.get("objective", 1e18) - startobj) < 1e-6:
                return s
        return None
    return out
