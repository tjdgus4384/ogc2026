"""prism_gate.py -- GATE: does the polygon-prism approximation actually cut tardiness vs the cuboid,
on a real congested instance? Packs each bay over time twice, differing ONLY in the feasibility test:
  aabb : footprint AABBs must be disjoint (cuboid, current)
  prism: union-of-layers polygons must be interior-disjoint (AABB pre-filter, then Shapely)
Candidate positions include present blocks' LEFT/BOTTOM edges too, so footprints can interpenetrate
(overhang nesting) — that's where prism beats cuboid. Same orientation (min footprint) in both for a
clean A/B. Reports total Z1 per mode.
"""
import sys, json, pathlib, math
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "solution" / "baseline" / "baseline"))
sys.path.insert(0, str(ROOT / "solution" / "dev"))
from shapely.geometry import Polygon
from shapely.ops import unary_union
from shapely import affinity
import solve

def prep(blk):
    """min-footprint orientation: return (lx0,ly0,lx1,ly1, union_polygon_at_reference)."""
    best = None
    for o in blk["shape"]:
        xs = [v[0] for l in o["layers"] for v in l]; ys = [v[1] for l in o["layers"] for v in l]
        area = (max(xs) - min(xs)) * (max(ys) - min(ys))
        if best is None or area < best[0]:
            polys = [Polygon(l) for l in o["layers"] if len(l) >= 3]
            polys = [p if p.is_valid else p.buffer(0) for p in polys]
            u = unary_union(polys).simplify(0.25)   # fewer vertices -> bound NFP-candidate cost
            best = (area, min(xs), min(ys), max(xs), max(ys), u)
    return best[1], best[2], best[3], best[4], best[5]

def pack(W, H, items, mode):
    """items: list of (id, r, p, due, lx0,ly0,lx1,ly1, upoly). Returns total Z1."""
    active = []  # (X0,Y0,X1,Y1, poly_translated, exit)
    z1 = 0
    for (bid, r, p, due, lx0, ly0, lx1, ly1, upoly) in items:
        ent = sorted({r} | {ex for *_, ex in active if ex > r})
        placed = None
        for e in ent:
            pres = [a for a in active if a[5] > e]
            if mode == "prism" and pres:
                # NFP-vertex candidates: reference positions where the moving polygon's vertices touch
                # present polygons' vertices ({a - b} Minkowski approx) — true snug/nesting positions
                bverts = list(upoly.exterior.coords)
                cand = {(max(0, math.ceil(-lx0)), max(0, math.ceil(-ly0)))}
                for (a0, b0, a1, b1, apoly, _) in pres:
                    for (ax, ay) in apoly.exterior.coords:
                        for (bx, by) in bverts:
                            cand.add((math.ceil(ax - bx), math.ceil(ay - by)))
                cand = [(px, py) for (px, py) in cand
                        if px + lx0 >= -1e-9 and px + lx1 <= W + 1e-9
                        and py + ly0 >= -1e-9 and py + ly1 <= H + 1e-9]
                cand.sort(key=lambda c: (c[1], c[0]))   # bottom-left preference
                pos_list = cand[:1500]                    # cap to bound the gate's cost
            else:
                xs = {max(0, math.ceil(-lx0))}; ys = {max(0, math.ceil(-ly0))}
                for (a0, b0, a1, b1, _, _) in pres:
                    xs.add(math.ceil(a1 - lx0)); ys.add(math.ceil(b1 - ly0))
                xs = sorted(x for x in xs if x + lx0 >= -1e-9 and x + lx1 <= W + 1e-9)
                ys = sorted(y for y in ys if y + ly0 >= -1e-9 and y + ly1 <= H + 1e-9)
                pos_list = [(px, py) for py in ys for px in xs]
            if True:
                for (px, py) in pos_list:
                    X0, Y0, X1, Y1 = px + lx0, py + ly0, px + lx1, py + ly1
                    ok = True
                    for (a0, b0, a1, b1, apoly, _) in pres:
                        if not (X0 < a1 - 1e-9 and a0 < X1 - 1e-9 and Y0 < b1 - 1e-9 and b0 < Y1 - 1e-9):
                            continue  # AABB disjoint -> no overlap either way
                        if mode == "aabb":
                            ok = False; break
                        # prism: AABBs overlap -> check actual polygons
                        mp = affinity.translate(upoly, px, py)
                        if mp.intersection(apoly).area > 1e-9:
                            ok = False; break
                    if ok:
                        mp = affinity.translate(upoly, px, py)
                        active.append((X0, Y0, X1, Y1, mp, e + p))
                        z1 += max(0, e + p - due); placed = True; break
                if placed:
                    break
            if placed:
                break
        if not placed:
            e = max(r, max((ex for *_, ex in active), default=0))
            px, py = max(0, math.ceil(-lx0)), max(0, math.ceil(-ly0))
            mp = affinity.translate(upoly, px, py)
            active.append((px+lx0, py+ly0, px+lx1, py+ly1, mp, e+p)); z1 += max(0, e+p-due)
    return z1

if __name__ == "__main__":
    nm = sys.argv[1] if len(sys.argv) > 1 else "prob_27"
    prob = json.load(open(ROOT / "data" / "train" / f"{nm}.json"))
    metas = [solve.block_meta(prob["blocks"][i], i) for i in range(len(prob["blocks"]))]
    asg = solve._assign(prob, metas, "bal")
    import time
    for mode in ("aabb", "prism"):
        tot = 0; t0 = time.time()
        for j, bay in enumerate(prob["bays"]):
            ids = sorted([i for i in asg if asg[i] == j],
                         key=lambda i: (prob["blocks"][i]["due_date"], prob["blocks"][i]["processing_time"]))
            items = []
            for i in ids:
                b = prob["blocks"][i]; lx0, ly0, lx1, ly1, u = prep(b)
                items.append((i, b["release_time"], b["processing_time"], b["due_date"], lx0, ly0, lx1, ly1, u))
            tot += pack(bay["width"], bay["height"], items, mode)
        print(f"{nm} {mode:6s}: Z1={tot}  ({time.time()-t0:.1f}s)")
