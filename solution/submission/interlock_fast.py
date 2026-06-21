"""interlock_fast.py -- TRUE interlock bay packer using precomputed NFP regions (fast point tests).

For each ordered block pair (i,k) lazily build & cache Phi_{ik} (collision) and Psi_{i<-k} (i obstructed by
k). Placement / crane checks are then point-in-polygon tests on the offset d = pos_k - pos_i (fast), no
per-candidate Shapely intersection. Conservative NFP (superset) => never false-feasible; final validation
by utils.check_feasibility, fallback to cuboid on any failure.
"""
import math
from shapely.geometry import Point, Polygon
import nfp


def _orient_fit(prob, i, W, H):
    B = prob["blocks"][i]; best = None
    for oi, o in enumerate(B["shape"]):
        xs = [v[0] for l in o["layers"] for v in l]; ys = [v[1] for l in o["layers"] for v in l]
        lx0, ly0, lx1, ly1 = min(xs), min(ys), max(xs), max(ys)
        if math.ceil(-lx0) > math.floor(W - lx1) or math.ceil(-ly0) > math.floor(H - ly1):
            continue
        a = (lx1 - lx0) * (ly1 - ly0)
        if best is None or a < best[0]:
            best = (a, oi, lx0, ly0, lx1, ly1)
    if best is None:
        o = B["shape"][0]; xs = [v[0] for l in o["layers"] for v in l]; ys = [v[1] for l in o["layers"] for v in l]
        best = (0, 0, min(xs), min(ys), max(xs), max(ys))
    return best[1:]


def pack_bay_interlock_fast(prob, bay_id, ids, cap=40, tl=90):
    import time, utils
    t0 = time.time()
    bays = prob["bays"]; B = prob["blocks"]; W, H = bays[bay_id]["width"], bays[bay_id]["height"]
    bay = utils.Bay.from_dict(bays[bay_id], bay_id)
    ori = {i: _orient_fit(prob, i, W, H) for i in ids}                  # (oi,lx0,ly0,lx1,ly1)
    lay = {i: [nfp._poly(l) for l in B[i]["shape"][ori[i][0]]["layers"]] for i in ids}  # ref-coord layer polys
    P = {i: B[i]["processing_time"] for i in ids}
    phi = {}; psi = {}
    def PHI(i, k):
        if (i, k) not in phi:
            phi[(i, k)] = nfp.build_phi(lay[i], lay[k])
        return phi[(i, k)]
    def PSI(i, k):
        if (i, k) not in psi:
            psi[(i, k)] = nfp.build_psi(lay[i], lay[k])
        return psi[(i, k)]

    info = {}   # id -> (entry, x, y)
    for i in sorted(ids, key=lambda i: (B[i]["due_date"], B[i]["processing_time"])):
        if time.time() - t0 > tl:
            return None
        oi, lx0, ly0, lx1, ly1 = ori[i]; R = B[i]["release_time"]
        l0 = B[i]["shape"][oi]["layers"][0]; best = None
        for e in sorted({R} | {info[k][0] + P[k] for k in info if info[k][0] + P[k] > R}):
            if best is not None and e >= best[0]:
                break
            pres = [k for k in info if info[k][0] <= e < info[k][0] + P[k]]
            cand = {(max(0, math.ceil(-lx0)), max(0, math.ceil(-ly0)))}
            for k in pres:
                ko = ori[k][0]; kx, ky = info[k][1], info[k][2]
                for (ax, ay) in B[k]["shape"][ko]["layers"][0]:
                    for (mx, my) in l0:
                        cand.add((math.ceil(kx + ax - mx), math.ceil(ky + ay - my)))
            cl = [(px, py) for (px, py) in cand
                  if px + lx0 >= -1e-9 and px + lx1 <= W + 1e-9 and py + ly0 >= -1e-9 and py + ly1 <= H + 1e-9]
            cl.sort(key=lambda c: (c[1], c[0]))
            for (px, py) in cl[:cap]:
                ok = True
                for k in pres:
                    dx, dy = info[k][1] - px, info[k][2] - py
                    if PHI(i, k).contains(Point(dx, dy)) or PSI(i, k).contains(Point(dx, dy)):
                        ok = False; break
                if not ok:
                    continue
                # SELECTIVE: reject if this placement obstructs a present k (k's exit freezes to >= e+P_i)
                # in a way that makes a not-yet-tardy k newly tardy. (k obstructed by i = PSI(k,i) at -d.)
                fi = e + P[i]; bad = False
                for k in pres:
                    dx, dy = px - info[k][1], py - info[k][2]   # offset of i relative to k
                    if PSI(k, i).contains(Point(dx, dy)):       # k obstructed by i -> k.exit >= fi
                        if fi > B[k]["due_date"] and info[k][0] + P[k] <= B[k]["due_date"]:
                            bad = True; break
                if bad:
                    continue
                best = (e, px, py); break
            if best and best[0] == e:
                break
        if best is None:
            e = max(R, max((info[k][0] + P[k] for k in info), default=0))
            best = (e, max(0, math.ceil(-lx0)), max(0, math.ceil(-ly0)))
        info[i] = (best[0], best[1], best[2])

    # crane-feasible EXIT times via fixed point: i exits >= max(entry+P, exit of any co-present block k
    # that obstructs i's lift, PSI(i,k)). Entry-rejection makes obstruction edges go entry-earlier->later
    # (a DAG) so this converges; non-convergence (a cycle / mutual obstruction) -> None (fallback to cuboid).
    exit_t = {i: info[i][0] + P[i] for i in ids}
    for _ in range(len(ids) + 2):
        changed = False
        for i in ids:
            ei = info[i][0]; new = info[i][0] + P[i]
            for k in ids:
                if k == i:
                    continue
                if ei < exit_t[k] and info[k][0] < exit_t[i]:        # co-present
                    dx, dy = info[k][1] - info[i][1], info[k][2] - info[i][2]
                    if PSI(i, k).contains(Point(dx, dy)):
                        if exit_t[k] > new:
                            new = exit_t[k]
            if new > exit_t[i]:
                exit_t[i] = new; changed = True
        if not changed:
            break
    else:
        return None
    return {i: (bay_id, info[i][1], info[i][2], ori[i][0], info[i][0], exit_t[i]) for i in ids}
