"""prism_polish.py -- targeted polygon (prism / silhouette) re-pack of a bay, as a final polish.

Cuboid (footprint AABB) packing wastes the non-convex padding (footprint area ~1.6-2.4x the true polygon
area). A prism placement uses each block's UNION-of-layers polygon (the silhouette) and only requires
those silhouettes to be interior-disjoint when co-resident -> still crane+collision free (EXIT=ENTRY+P),
but denser -> blocks enter earlier -> less tardiness. Shapely cost is bounded by applying this only to the
1-2 most-tardy bays of an already-good solution. Candidate positions = NFP-vertex set {present_vert -
moving_vert} (snug touch positions, incl. nesting under overhangs) with an AABB pre-filter.
"""
import math


def _silhouettes(prob):
    """block_id -> {orient_idx: (union_polygon_at_ref, (lx0,ly0,lx1,ly1))}; lazy shapely."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    out = {}
    for i, b in enumerate(prob["blocks"]):
        d = {}
        for oi, o in enumerate(b["shape"]):
            ps = [Polygon(l) for l in o["layers"] if len(l) >= 3]
            ps = [p if p.is_valid else p.buffer(0) for p in ps]
            if not ps:
                continue
            u = unary_union(ps).simplify(0.2)
            xs = [v[0] for l in o["layers"] for v in l]; ys = [v[1] for l in o["layers"] for v in l]
            d[oi] = (u, (min(xs), min(ys), max(xs), max(ys)))
        out[i] = d
    return out


def repack_bay_prism(W, H, items, sil, tl=None, cap_cand=1200):
    """items: list of (id, R, P, due, orient_idx). Re-pack with prism (silhouette-disjoint) over time.
    Returns (placements {id:(x,y,oi,e,f)}, Z1). orient fixed to the one given per item (the solution's)."""
    from shapely import affinity
    import time
    t0 = time.time()
    active = []  # (X0,Y0,X1,Y1, poly_world, exit)
    place = {}; z1 = 0
    order = sorted(items, key=lambda it: (it[2 + 1], it[2]))  # by due then proc  (it=(id,R,P,due,oi))
    order = sorted(items, key=lambda it: (it[3], it[2]))
    for (bid, R, P, due, oi) in order:
        if oi not in sil[bid]:
            oi = next(iter(sil[bid]))
        upoly, (lx0, ly0, lx1, ly1) = sil[bid][oi]
        placed = None
        ent = sorted({R} | {ex for (*_, ex) in active if ex > R})
        for e in ent:
            if tl and time.time() - t0 > tl:
                break
            pres = [a for a in active if a[5] > e]
            cand = {(max(0, math.ceil(-lx0)), max(0, math.ceil(-ly0)))}
            if pres:
                bv = list(upoly.exterior.coords)
                for (a0, b0, a1, b1, apoly, _) in pres:
                    for (ax, ay) in apoly.exterior.coords:
                        for (mx, my) in bv:
                            cand.add((math.ceil(ax - mx), math.ceil(ay - my)))
            cl = [(px, py) for (px, py) in cand
                  if px + lx0 >= -1e-9 and px + lx1 <= W + 1e-9 and py + ly0 >= -1e-9 and py + ly1 <= H + 1e-9]
            cl.sort(key=lambda c: (c[1], c[0]))
            for (px, py) in cl[:cap_cand]:
                X0, Y0, X1, Y1 = px + lx0, py + ly0, px + lx1, py + ly1
                ok = True
                for (a0, b0, a1, b1, apoly, _) in pres:
                    if not (X0 < a1 - 1e-9 and a0 < X1 - 1e-9 and Y0 < b1 - 1e-9 and b0 < Y1 - 1e-9):
                        continue
                    if affinity.translate(upoly, px, py).intersection(apoly).area > 1e-9:
                        ok = False; break
                if ok:
                    f = e + P
                    active.append((X0, Y0, X1, Y1, affinity.translate(upoly, px, py), f))
                    place[bid] = (px, py, oi, e, f); z1 += max(0, f - due); placed = True; break
            if placed:
                break
        if not placed:
            e = max(R, max((ex for (*_, ex) in active), default=0)); f = e + P
            px, py = max(0, math.ceil(-lx0)), max(0, math.ceil(-ly0))
            active.append((px+lx0, py+ly0, px+lx1, py+ly1, affinity.translate(upoly, px, py), f))
            place[bid] = (px, py, oi, e, f); z1 += max(0, f - due)
    return place, z1
