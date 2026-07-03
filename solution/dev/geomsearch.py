"""geomsearch.py -- directed ASSIGNMENT search with the crane-aware geompack decoder.

The decoder gap (greedy under-packs Z1 by 20-35% on congested) made brkga select assignments by a wrong
fitness. geompack is a FAST crane-aware (union-footprint = crane-clean) bitmap packer, so we can now run a
DIRECTED assignment local search whose moves are evaluated by the TRUE achievable packing: destroy a block
from the worst-tardy bay, reinsert into another bay, RE-PACK only the two affected bays with geompack + a
short per-bay order SA, accept on the true objective (SA). Optimizes assignment AND packing together with an
accurate crane-aware oracle -- the search that crane-blind packers couldn't support.
"""
import time, random, math
from packer import block_meta
from solve import build_ops
from geompack import GeomPacker, _lib


def _meta(prob):
    MB = {i: block_meta(prob["blocks"][i], i) for i in range(len(prob["blocks"]))}
    for i in MB:
        MB[i]["proc"] = max(1, MB[i]["proc"]); MB[i]["due"] = prob["blocks"][i]["due_date"]
        MB[i]["release"] = prob["blocks"][i].get("release_time", 0)
        MB[i]["wl"] = prob["blocks"][i]["workload"]; MB[i]["pref"] = prob["blocks"][i]["bay_preferences"]
    return MB


def assign_search(prob, seed_sol, utils, time_budget, seed=0, sa_per=0.4):
    if _lib() is None:
        return seed_sol
    t0 = time.time()
    bays = prob["bays"]; nb = len(bays); MB = _meta(prob)
    # LARGE-BAY GUARD (P3 timeout safety). A single geom pack on a huge bay is an un-interruptible call that
    # can blow the wall budget (the P3 -1 mode). geompolish has the same guard (CELL_CAP). If any bay exceeds
    # the cap, skip geomsearch entirely (keep the seed) -- its benefit is on normal-sized congested bays.
    if any(int(bays[j]["width"]) * int(bays[j]["height"]) > 60_000 for j in range(nb)):
        return seed_sol
    w = prob.get("weights", {}); w1, w2, w3 = w.get("w1", 1), w.get("w2", 1), w.get("w3", 1)
    gp = GeomPacker(prob); rng = random.Random(seed)
    smax = {i: max(MB[i]["pref"]) for i in MB}
    fitb = {}                                          # bays a block fits (any orient bbox <= bay)
    for i in MB:
        ok = []
        for j in range(nb):
            W, H = bays[j]["width"], bays[j]["height"]
            fits = False
            for oi in range(len(prob["blocks"][i]["shape"])):
                _, ox, oy, rx, ry = gp._orient(i, oi)
                if rx <= W + 1e-6 and ry <= H + 1e-6:
                    fits = True; break
            if fits:
                ok.append(j)
        fitb[i] = ok or list(range(nb))

    byb = {j: [] for j in range(nb)}
    for tt, ops in seed_sol["operations"].items():
        for o in ops:
            if o["type"] == "ENTRY":
                byb[o["bay_id"]].append(o["block_id"])

    def pack_bay(j, ids, budget):
        out, z = gp.sa_bay(ids, bays[j]["width"], bays[j]["height"], MB, budget, rng)
        return out, z
    # initial per-bay packing
    bayp = {}; bayz = {}
    for j in range(nb):
        bayp[j], bayz[j] = pack_bay(j, byb[j], min(sa_per, 2.0)) if byb[j] else ({}, 0.0)

    def loads():
        return [sum(MB[i]["wl"] for i in byb[j]) for j in range(nb)]

    def cur_bay_of():
        out = {}
        for j in range(nb):
            for i in byb[j]:
                out[i] = j
        return out

    def full_obj():
        pl = {}
        for j in range(nb):
            for i, (x, y, oi, e, f) in bayp[j].items():
                pl[i] = (j, x, y, oi, e, f)
        sol = build_ops(pl); r = utils.check_feasibility(prob, sol)
        return (sol, r["objective"]) if r["feasible"] else (None, float("inf"))

    best_sol, best_obj = full_obj()
    cur_obj = best_obj
    while time.time() - t0 < time_budget and best_obj > 0:
        ld = loads()
        asg = cur_bay_of()
        if max(bayz.values()) > 0:
            # Z1-bound: move work out of the currently tardiest bay.
            src = max(range(nb), key=lambda j: bayz[j])
            if not byb[src]:
                break
            i = rng.choice(byb[src])
            dests = [j for j in fitb[i] if j != src]
            rng.shuffle(dests)
        else:
            # Assignment-bound: Z1 is already zero under the geom decoder, so
            # keep searching the same abstraction for Z3/Z2 improvements.
            regret = sorted(
                asg,
                key=lambda b: (max(MB[b]["pref"]) - MB[b]["pref"][asg[b]], MB[b]["wl"]),
                reverse=True,
            )
            pool = regret[:max(8, min(32, len(regret)))]
            if not pool:
                break
            i = rng.choice(pool)
            src = asg[i]
            dests = [j for j in fitb[i] if j != src]
            # Prefer higher preference, then less weighted load. This is only a
            # proposal order; the validator-scored objective still decides.
            dests.sort(key=lambda j: (-MB[i]["pref"][j], ld[j]))
        if not dests:
            continue
        j2 = dests[0] if rng.random() < 0.75 else rng.choice(dests)
        # snapshot for revert
        o_src, o_dst = list(byb[src]), list(byb[j2])
        o_ps, o_zs, o_pd, o_zd = bayp[src], bayz[src], bayp[j2], bayz[j2]
        # trial move i: src -> j2; re-pack ONLY the two affected bays
        byb[src] = [b for b in o_src if b != i]; byb[j2] = o_dst + [i]
        bayp[src], bayz[src] = pack_bay(src, byb[src], sa_per)
        bayp[j2], bayz[j2] = pack_bay(j2, byb[j2], sa_per)
        sol, obj = full_obj()
        if obj < cur_obj - 1e-9 or rng.random() < 0.03:
            cur_obj = obj
            if obj < best_obj - 1e-9:
                best_obj = obj; best_sol = sol
        else:                                          # revert
            byb[src], byb[j2] = o_src, o_dst
            bayp[src], bayz[src] = o_ps, o_zs; bayp[j2], bayz[j2] = o_pd, o_zd
    return best_sol if best_sol is not None else seed_sol
