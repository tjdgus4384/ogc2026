"""polypack.py -- polygon-level (prism) bay packer via fast integer BITMAP collision + solution polish.

The footprint-disjoint AABB packer wastes ~32% of each block's bounding box (poly/AABB ~ 0.685). Real
feasibility (utils) is polygon-level per layer; the pairwise "fully crane+collision clean" condition is
provably equivalent to UNION-polygon disjointness (prism). Packing at polygon level cuts per-bay tardiness
~37-53% vs the greedy AABB packer (measured) while keeping EXIT=ENTRY+P (no crane freeze -- unlike
interlock, which overlaps and is worse).

Speed: each block's union footprint is rasterized once to a CONSERVATIVE integer mask (cell set if the
polygon intersects the unit cell -> bitmap-free implies polygon-free, safe). Placement = numpy bitmap
bottom-left, microseconds per test (Shapely/NFP were 70-120s/bay; bitmap is ~4s and caches masks).

Public:
  polypack_bay(W, H, sub_metas, braw, time_budget) -> [(bid,x,y,oi,e,f)]   (priority order preserved)
  polish_solution(prob, sol, utils, time_budget)    -> sol'  (re-pack each bay; keep feasible improvements)
"""
import math, time
import numpy as np
from shapely.geometry import Polygon, box as _shbox
from shapely.ops import unary_union

_U = {}      # (bid, oi) -> union Polygon, local coords (ref = layer0[0] at origin)
_MASK = {}   # (bid, oi) -> (bool mask [h,w], ox, oy)  at RES subcells/unit
RES = 1      # raster resolution (subcells per unit). RES=2 packs ~52% better PER BAY in isolation but is
             # ~5x slower (8s vs 1.3s/bay) -> within the polish budget it can't finish and ends up WORSE
             # end-to-end (prob_26 pipeline: RES=1 23.6M vs RES=2 30.4M). Keep RES=1: fast, robust, the
             # polish always completes. (RES=2 would only pay off at very large budgets w/ warmed masks.)


def _union_poly(braw, bid, oi):
    import utils
    layers = utils._resolve_layers(braw[bid]["shape"][oi]["layers"])
    if not layers:
        return Polygon()
    ref = layers[0][0]
    ps = []
    for l in layers:
        if len(l) >= 3:
            p = Polygon([(x - ref[0], y - ref[1]) for (x, y) in l])
            if not p.is_valid:
                p = p.buffer(0)
            if not p.is_empty:
                ps.append(p)
    return unary_union(ps) if ps else Polygon()


def U(braw, bid, oi):
    k = (bid, oi)
    if k not in _U:
        _U[k] = _union_poly(braw, bid, oi)
    return _U[k]


def mask(braw, bid, oi):
    """Conservative integer footprint: cell (gx,gy) set if the union polygon intersects that unit cell.
    bitmap-free => polygon-free (safe over-approximation). Returns (bool[h,w], ox, oy). Cached."""
    k = (bid, oi)
    if k not in _MASK:
        u = U(braw, bid, oi)
        if u.is_empty:
            _MASK[k] = (np.zeros((1, 1), bool), 0, 0)
        else:
            minx, miny, maxx, maxy = u.bounds
            ox, oy = math.floor(minx), math.floor(miny)
            w = max(1, int(math.ceil(maxx) - ox)) * RES; h = max(1, int(math.ceil(maxy) - oy)) * RES
            m = np.zeros((h, w), bool)
            s = 1.0 / RES
            for gy in range(h):
                for gx in range(w):
                    cx, cy = ox + gx * s, oy + gy * s
                    if u.intersects(_shbox(cx, cy, cx + s, cy + s)):
                        m[gy, gx] = True
            _MASK[k] = (m, ox, oy)
    return _MASK[k]


def _first_fit(occ, m, ox, oy, W, H):
    """Vectorized bottom-left on the RES grid: lowest-then-leftmost INTEGER ref (x,y) where footprint mask
    m does not overlap occupancy occ. Only integer refs (subcell index multiple of RES). Returns (x,y)/None."""
    h, w = m.shape; Hr, Wr = H * RES, W * RES
    if h > Hr or w > Wr:
        return None
    try:
        from numpy.lib.stride_tricks import sliding_window_view
        win = sliding_window_view(occ, (h, w))
        bad = (win & m).any(axis=(2, 3))                # overlap per subcell top-left (Y0,X0)
        # integer-ref positions only: footprint origin (x+ox,y+oy) integer => subcell index multiple of RES
        free = np.argwhere(~bad[::RES, ::RES])          # indices in stride-RES space (= integer refs)
        if free.shape[0] == 0:
            return None
        Y0, X0 = int(free[0, 0]) * RES, int(free[0, 1]) * RES
        return (X0 // RES - ox, Y0 // RES - oy)
    except Exception:
        for Y0 in range(0, Hr - h + 1, RES):
            seg = occ[Y0:Y0 + h]
            for X0 in range(0, Wr - w + 1, RES):
                if not (seg[:, X0:X0 + w] & m).any():
                    return (X0 // RES - ox, Y0 // RES - oy)
        return None


def polypack_bay(W, H, sub, braw, time_budget=1e9):
    """sub = block_meta dicts in priority order. Polygon-level packing via bitmap collision; bottom-left;
    EXIT = ENTRY + P. Returns [(bid, x, y, oi, e, f)]."""
    W, H = int(W), int(H)
    # LARGE-BAY GUARD: the bitmap sliding-window scan is O(H*W*h*w) per block -> explodes on large bays
    # (e.g. 1500x400 made the pipeline overrun the time limit -> server -1; likely the P3 cause). Above a
    # cell cap, fall back to the fast, bay-size-INDEPENDENT AABB packer (time-safe; just no polygon gain).
    if H * W * RES * RES > 60000:
        from packer import pack_bay
        return pack_bay(W, H, sub)
    t0 = time.time()
    placed = []
    for mt in sub:
        bid = mt["id"]; P = mt["proc"]; r = mt["release"]
        n_orient = len(braw[bid]["shape"])
        oris = range(n_orient)
        # NOTE: capping orientations/entry-candidates for speed WRECKS quality on congested bays
        # (forces serialize fallback; prob_38 K=80 went +304%). Keep full search; polish does one
        # pack/bay so ~4s/bay is acceptable. In-search use (thousands of repacks) is NOT viable here.
        ent_cands = sorted({r} | {f for (_, _, _, _, e, f) in placed if f > r})
        best = None
        for e in ent_cands:
            f = e + P
            present = [(pb, px, py, poi) for (pb, px, py, poi, pe, pf) in placed if pf > e and pe < f]
            occ = np.zeros((H * RES, W * RES), bool)
            for (pb, px, py, poi) in present:
                m, ox, oy = mask(braw, pb, poi); h, w = m.shape
                X0, Y0 = (px + ox) * RES, (py + oy) * RES
                xs, xe = max(0, X0), min(W * RES, X0 + w); ys, ye = max(0, Y0), min(H * RES, Y0 + h)
                if xs < xe and ys < ye:
                    occ[ys:ye, xs:xe] |= m[ys - Y0:ye - Y0, xs - X0:xe - X0]
            for oi in oris:
                m, ox, oy = mask(braw, bid, oi)
                pos = _first_fit(occ, m, ox, oy, W, H)
                if pos is not None:
                    best = (e, pos[0], pos[1], oi); break
            if best:
                break
            if time.time() - t0 > time_budget:
                break
        if best is None:
            e = max(r, max((f for (*_, f) in placed), default=0))
            o0 = mt["orients"][0]
            best = (e, max(0, math.ceil(-o0[1])), max(0, math.ceil(-o0[2])), o0[0])
        e, x, y, oi = best
        placed.append((bid, x, y, oi, e, e + P))
    return placed


def _bay_tard(packed, metas):
    return sum(max(0, f - metas[bid]["due"]) for (bid, x, y, oi, e, f) in packed)


def polypack_bay_opt(W, H, ids, metas, braw, rng, time_budget=1e9, rounds=40):
    """Polygon-pack a bay, optimizing the block ORDER for polygon density (the AABB-optimal order is not
    the polygon-optimal one). Tries the given order + EDD + random perturbations of tardy blocks; keeps the
    lowest-tardiness packing. polypack_bay is ~ms with cached masks, so many rounds are cheap."""
    t0 = time.time()
    base = list(ids)
    best_order = base
    best_pack = polypack_bay(W, H, [metas[i] for i in base], braw, time_budget=time_budget)
    best_t = _bay_tard(best_pack, metas)
    edd = sorted(ids, key=lambda i: (metas[i]["due"], metas[i]["proc"]))
    for cand in [edd]:
        pk = polypack_bay(W, H, [metas[i] for i in cand], braw, time_budget=time_budget)
        if _bay_tard(pk, metas) < best_t:
            best_t, best_order, best_pack = _bay_tard(pk, metas), cand, pk
    r = 0
    while r < rounds and time.time() - t0 < time_budget and best_t > 0:
        r += 1
        order = list(best_order)
        # perturb: move a random tardy block earlier (cuts its entry/tardiness)
        tardy = [k for k, i in enumerate(order) if best_pack and any(b == i and f > metas[i]["due"]
                 for (b, x, y, oi, e, f) in best_pack)]
        if not tardy:
            break
        k = rng.choice(tardy)
        j = rng.randint(0, k)
        order.insert(j, order.pop(k))
        pk = polypack_bay(W, H, [metas[i] for i in order], braw, time_budget=time_budget - (time.time() - t0))
        t = _bay_tard(pk, metas)
        if t < best_t:
            best_t, best_order, best_pack = t, order, pk
    return best_pack


def polish_solution(prob, sol, utils, time_budget=120.0):
    """Re-pack each bay's blocks at polygon level (preserving the solution's optimized entry-order).
    Replace a bay only if the whole solution stays feasible and the objective improves."""
    import random
    from packer import block_meta
    from solve import build_ops
    rng = random.Random(12345)
    t0 = time.time()
    braw = prob["blocks"]; bays = prob["bays"]
    metas = {i: block_meta(braw[i], i) for i in range(len(braw))}
    place = {}
    for t, ops in sol["operations"].items():
        for o in ops:
            if o["type"] == "ENTRY":
                place[o["block_id"]] = [o["bay_id"], o["x"], o["y"], o["orient_idx"], int(t), None]
    for t, ops in sol["operations"].items():
        for o in ops:
            if o["type"] == "EXIT":
                place[o["block_id"]][5] = int(t)
    base = utils.check_feasibility(prob, sol)
    if not base["feasible"]:
        return sol
    best_sol = sol; best_obj = base["objective"]
    cur_place = {i: list(place[i]) for i in place}
    by_bay = {j: [] for j in range(len(bays))}
    for i, p in place.items():
        by_bay[p[0]].append(i)

    def bay_tard(j):
        return sum(max(0, place[i][5] - metas[i]["due"]) for i in by_bay[j])
    order = sorted(range(len(bays)), key=lambda j: -bay_tard(j))
    for j in order:
        if time.time() - t0 > time_budget:
            break
        ids = sorted(by_bay[j], key=lambda i: (cur_place[i][4], metas[i]["due"]))  # keep optimized order
        # single-pass beat order-search here (anytime's order already good); keep it simple + fast.
        packed = polypack_bay(bays[j]["width"], bays[j]["height"], [metas[i] for i in ids], braw,
                              time_budget=time_budget - (time.time() - t0))
        trial = {i: list(cur_place[i]) for i in cur_place}
        for (bid, x, y, oi, e, f) in packed:
            trial[bid] = [j, x, y, oi, e, f]
        ts = build_ops({i: tuple(trial[i]) for i in trial})
        r = utils.check_feasibility(prob, ts)
        if r["feasible"] and r["objective"] < best_obj - 1e-9:
            best_obj = r["objective"]; best_sol = ts; cur_place = trial

    # -- PHASE 2: polygon-aware REASSIGNMENT. Polygon bays hold ~32% more, so the AABB-derived assignment
    # is too conservative -- move worst-tardy blocks to other bays and re-polypack both, keep full-obj
    # improvements. Measured -10% over polish-only on prob_26 (beats best-known). Few moves, time-bounded. --
    import os as _os
    if len(bays) >= 2 and _os.environ.get("POLISH_NOREASSIGN") != "1":   # toggle for A/B measurement
        fit = {}
        for i in metas:
            fb = []
            for j, bay in enumerate(bays):
                for (_, lx0, ly0, lx1, ly1) in metas[i]["orients"]:
                    if math.ceil(-lx0) <= math.floor(bay["width"] - lx1) and \
                       math.ceil(-ly0) <= math.floor(bay["height"] - ly1):
                        fb.append(j); break
            fit[i] = fb or list(range(len(bays)))

        def _tard(pl, i):
            return max(0, pl[i][5] - metas[i]["due"])

        while time.time() - t0 < time_budget:
            tardy = sorted((i for i in cur_place if _tard(cur_place, i) > 0),
                           key=lambda i: _tard(cur_place, i), reverse=True)
            if not tardy:
                break
            improved = False
            for b in tardy[:8]:
                j = cur_place[b][0]
                for jp in [x for x in fit[b] if x != j]:
                    if time.time() - t0 > time_budget:
                        break
                    trial = {i: list(cur_place[i]) for i in cur_place}
                    trial[b][0] = jp
                    for bay_j in (j, jp):
                        bids = sorted((i for i in trial if trial[i][0] == bay_j),
                                      key=lambda i: (cur_place[i][4], metas[i]["due"]))
                        bl = max(1.0, time_budget - (time.time() - t0))
                        for (bid, x, y, oi, e, f) in polypack_bay(bays[bay_j]["width"], bays[bay_j]["height"],
                                                                  [metas[i] for i in bids], braw, time_budget=bl):
                            trial[bid] = [bay_j, x, y, oi, e, f]
                    ts = build_ops({i: tuple(trial[i]) for i in trial})
                    r = utils.check_feasibility(prob, ts)
                    if r["feasible"] and r["objective"] < best_obj - 1e-9:
                        best_obj = r["objective"]; best_sol = ts; cur_place = trial; improved = True; break
                if improved or time.time() - t0 > time_budget:
                    break
            if not improved:
                break
    return best_sol
