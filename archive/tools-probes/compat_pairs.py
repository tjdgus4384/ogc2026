"""Kernel-as-oracle pairwise compatibility for the recombination model.
For each same-bay bbox-overlapping pattern pair, test BOTH temporal
orders on a 2-block mini-board via the engine's audit (all 5 crane
rules). Output: npz-style JSON {key: [ok_i_first, ok_j_first]}.
Usage: compat_pairs.py <pnum>"""
import glob
import json
import math
import os
import sys

MENU = int(os.environ.get("MENU", "6"))
XFER = int(os.environ.get("XFER", "8"))

sys.path.insert(0, "solver")
import myalgorithm as M

pnum = int(sys.argv[1])
prob = json.load(open(f"data/preliminary_test/prob_{pnum}.json"))
n = len(prob["blocks"])
P = [max(1, int(math.ceil(b["processing_time"] - 1e-9)))
     for b in prob["blocks"]]
D = [int(round(b["due_date"])) for b in prob["blocks"]]
H = max(D) + max(P) + 40

def bbox(blk, oi):
    vs = [v for L in prob["blocks"][blk]["shape"][oi]["layers"] for v in L]
    xs = [v[0] for v in vs]; ys = [v[1] for v in vs]
    return min(xs), min(ys), max(xs), max(ys)

menus = [dict() for _ in range(n)]
for f in glob.glob(f"runs/*/prob_{pnum}.sol.json"):
    try:
        sol = json.load(open(f))
    except Exception:
        continue
    for t, ops in sol.get("operations", {}).items():
        for op in ops:
            if op["type"] == "ENTRY":
                k = (op["bay_id"], op["orient_idx"], op["x"], op["y"])
                menus[op["block_id"]][k] = \
                    menus[op["block_id"]].get(k, 0) + 1
# menus: top-10 by frequency UNION current-best board placements
import os
_hp = os.environ.get("HINT", f"runs/mipboards/prob_{pnum}.sol.json")
if not os.path.exists(_hp):
    _hp = f"runs/ph1-s7/prob_{pnum}.sol.json"
best = json.load(open(_hp))
bestpat = {}
beste = {}
for t, ops in best["operations"].items():
    for op in ops:
        if op["type"] == "ENTRY":
            bestpat[op["block_id"]] = (op["bay_id"], op["orient_idx"],
                                       op["x"], op["y"])
            beste[op["block_id"]] = int(t)
bayW = [b["width"] for b in prob["bays"]]
bayH = [b["height"] for b in prob["bays"]]

def bbx(blk, oi):
    vs = [v for L in prob["blocks"][blk]["shape"][oi]["layers"] for v in L]
    xs = [v[0] for v in vs]; ys = [v[1] for v in vs]
    return min(xs), min(ys), max(xs), max(ys)

pats = []
for i in range(n):
    top = [k for k, _ in sorted(menus[i].items(),
                                key=lambda t: -t[1])[:MENU]]
    if i in bestpat and bestpat[i] not in top:
        top.append(bestpat[i])
    pats.append(top)

# VOCABULARY ENRICHMENT v2 (jitter proved value-less on P3): CROSS-BLOCK
# position transfer -- reuse the global pool of verified anchor points
# (bay, x, y) with THIS block's orientations; structurally new vocabulary.
pool = sorted({(b_, x_, y_) for row in pats for (b_, o_, x_, y_) in row})
import random as _rnd
_rnd.seed(7)
for i in range(n):
    seen = set(pats[i])
    extra = []
    nor = len(prob["blocks"][i]["shape"])
    for (b_, x_, y_) in _rnd.sample(pool, min(len(pool), 120)):
        o_ = _rnd.randrange(nor)
        x0, y0, x1, y1 = bbx(i, o_)
        k = (b_, o_, x_, y_)
        if k in seen:
            continue
        if x0 + x_ < 0 or y0 + y_ < 0 or \
                x1 + x_ > bayW[b_] or y1 + y_ > bayH[b_]:
            continue
        seen.add(k)
        extra.append(k)
        if len(extra) >= XFER:
            break
    pats[i] = pats[i] + extra

# GENERATOR X2 (FO-dense): for REGRETFUL blocks (pref argmax != hint
# bay), dense grid positions in their argmax bay (stride DSTRIDE).
XGEN2 = int(os.environ.get("XGEN2", "0"))
if XGEN2 > 0:
    DS = int(os.environ.get("DSTRIDE", "3"))
    for i in range(n):
        pr = prob["blocks"][i]["bay_preferences"]
        tb = max(range(len(pr)), key=lambda j: pr[j])
        if i in bestpat and pr[bestpat[i][0]] >= pr[tb]:
            continue
        seen = set(pats[i])
        extra = []
        nor = len(prob["blocks"][i]["shape"])
        for o_ in range(nor):
            x0, y0, x1, y1 = bbx(i, o_)
            px = -int(x0)
            while x1 + px <= bayW[tb] and len(extra) < XGEN2:
                py = -int(y0)
                while y1 + py <= bayH[tb] and len(extra) < XGEN2:
                    k = (tb, o_, px, py)
                    if k not in seen:
                        seen.add(k)
                        extra.append(k)
                    py += DS
                px += DS
            if len(extra) >= XGEN2:
                break
        pats[i] = pats[i] + extra

# GENERATOR X3 (bay-coverage): guarantee every block has >= 2 patterns
# in EVERY bay (grid stride DSTRIDE) -- opens the full Z2/Z3 assignment
# trade space that observation-derived menus cannot express.
XGEN3 = int(os.environ.get("XGEN3", "0"))
if XGEN3 > 0:
    DS3 = int(os.environ.get("DSTRIDE", "5"))
    m_ = len(prob["bays"])
    for i in range(n):
        seen = set(pats[i])
        nor = len(prob["blocks"][i]["shape"])
        for tb in range(m_):
            have = sum(1 for (b_, o_, x_, y_) in pats[i] if b_ == tb)
            if have >= 2:
                continue
            extra = []
            for o_ in range(nor):
                x0, y0, x1, y1 = bbx(i, o_)
                px = -int(x0)
                while x1 + px <= bayW[tb] and len(extra) < XGEN3:
                    py = -int(y0)
                    while y1 + py <= bayH[tb] and len(extra) < XGEN3:
                        k = (tb, o_, px, py)
                        if k not in seen:
                            seen.add(k)
                            extra.append(k)
                        py += DS3
                    px += DS3
                if len(extra) >= XGEN3:
                    break
            pats[i] = pats[i] + extra

# EXTRAPOS: force-inject every placement of an external board (e.g.
# the lookahead-reservation build -- possibly checker-INFEASIBLE as a
# schedule; certification prices each pair exactly, so only its
# POSITIONS matter). This is how reservation-structure enters the
# certified vocabulary.
_xp = os.environ.get("EXTRAPOS", "")
if _xp:
    _added = 0
    for _p1 in _xp.split(":"):
        if not os.path.exists(_p1):
            continue
        _xb = json.load(open(_p1))
        for t, ops in _xb["operations"].items():
            for op in ops:
                if op["type"] != "ENTRY":
                    continue
                i = op["block_id"]
                k = (op["bay_id"], op["orient_idx"], op["x"], op["y"])
                if k not in pats[i]:
                    pats[i] = pats[i] + [k]
                    _added += 1
    print(json.dumps({"extrapos_added": _added}), flush=True)

# GENERATOR X4 (raster-NFP contact): for regretful blocks, polygon-
# contact leftmost-x positions in their argmax-pref bay, slid against
# the INCUMBENT residents present during the block's stay window.
# Contact positions are the sparse smart subset of the stride-1 grid
# (an optimal packing always exists with every piece in contact); the
# nine-proof closure only covered stride>=2 grids. Generation is
# cell-center-raster approximate -- pair certification stays exact.
XGEN4 = int(os.environ.get("XGEN4", "0"))
if XGEN4 > 0:
    def _pip(px, py, poly):
        c = False
        for k_ in range(len(poly)):
            ax, ay = poly[k_]; bx2, by2 = poly[(k_ + 1) % len(poly)]
            if (ay > py) != (by2 > py):
                xint = ax + (py - ay) * (bx2 - ax) / (by2 - ay)
                if px < xint:
                    c = not c
        return c

    _rcache = {}
    def _raster(blk, oi):
        key = (blk, oi)
        if key in _rcache:
            return _rcache[key]
        x0, y0, x1, y1 = bbx(blk, oi)
        cells = []
        lays = prob["blocks"][blk]["shape"][oi]["layers"]
        for cx in range(int(math.floor(x0)), int(math.ceil(x1))):
            for cy in range(int(math.floor(y0)), int(math.ceil(y1))):
                if any(_pip(cx + 0.5, cy + 0.5, L) for L in lays):
                    cells.append((cx, cy))
        _rcache[key] = cells
        return cells

    def _occgrid(tb, e0, f0, skip):
        occ = [[False] * bayW[tb] for _ in range(bayH[tb])]
        for r, (rb, ro, rx, ry) in bestpat.items():
            if r == skip or rb != tb:
                continue
            er = beste[r]; fr = er + P[r]
            if er >= f0 or fr <= e0:
                continue
            for (cx, cy) in _raster(r, ro):
                gx, gy = rx + cx, ry + cy
                if 0 <= gx < bayW[tb] and 0 <= gy < bayH[tb]:
                    occ[gy][gx] = True
        return occ

    def _contacts(i, tb, occ, seen, cap):
        extra = []
        nor = len(prob["blocks"][i]["shape"])
        for o_ in range(nor):
            x0, y0, x1, y1 = bbx(i, o_)
            cells = _raster(i, o_)
            ylo = int(math.ceil(-y0)); yhi = int(math.floor(bayH[tb] - y1))
            xlo = int(math.ceil(-x0)); xhi = int(math.floor(bayW[tb] - x1))
            for y_ in range(ylo, yhi + 1):
                if len(extra) >= cap:
                    break
                for x_ in range(xlo, xhi + 1):
                    hit = False
                    for (cx, cy) in cells:
                        gx, gy = x_ + cx, y_ + cy
                        if 0 <= gx < bayW[tb] and 0 <= gy < bayH[tb]                                 and occ[gy][gx]:
                            hit = True
                            break
                    if not hit:
                        k = (tb, o_, x_, y_)
                        if k not in seen:
                            seen.add(k)
                            extra.append(k)
                        break
            if len(extra) >= cap:
                break
        return extra

    H_ = max(beste[r] + P[r] for r in beste) if beste else 1
    prefbays = set()
    for i in range(n):
        if i not in bestpat:
            continue
        pr = prob["blocks"][i]["bay_preferences"]
        tb = max(range(len(pr)), key=lambda j: pr[j])
        if pr[bestpat[i][0]] >= pr[tb]:
            continue
        prefbays.add(tb)
        seen = set(pats[i])
        extra = []
        # multi-window contact mining: patterns are entry-free in the
        # CP, so contact sets from OTHER stay windows are just as
        # legal -- the pref bay may only have room at other times.
        R_ = int(round(prob["blocks"][i]["release_time"]))
        ws = {beste[i]}
        for w_ in range(4):
            ws.add(min(R_ + w_ * max(P[i], (H_ - R_) // 4 + 1), H_ - 1))
        for e0 in sorted(ws):
            if len(extra) >= XGEN4:
                break
            occ = _occgrid(tb, e0, e0 + P[i], i)
            extra += _contacts(i, tb, occ, seen,
                               len(extra) + max(4, XGEN4 // 4))
        pats[i] = pats[i] + extra[:XGEN4]
    # XGEN4T (tardy-block early windows): a tardy block's entry can
    # only move earlier if some position is free during [R_i, R_i+P_i);
    # mine contacts there, in its incumbent bay AND its argmax-pref bay.
    # Tardiness dominates P4-P6 objectives; this is the obj1 analogue of
    # the regret-side mining above.
    XG4T = int(os.environ.get("XGEN4T", "0"))
    if XG4T > 0:
        for i in range(n):
            if i not in bestpat:
                continue
            R_ = int(round(prob["blocks"][i]["release_time"]))
            if beste[i] <= R_:
                continue
            pr = prob["blocks"][i]["bay_preferences"]
            tbs = {bestpat[i][0],
                   max(range(len(pr)), key=lambda j: pr[j])}
            seen = set(pats[i])
            extra = []
            for tb2 in tbs:
                if len(extra) >= XG4T:
                    break
                for e0 in (R_, (R_ + beste[i]) // 2):
                    if len(extra) >= XG4T:
                        break
                    occ = _occgrid(tb2, e0, e0 + P[i], i)
                    extra += _contacts(i, tb2, occ, seen, XG4T // 2)
            pats[i] = pats[i] + extra[:XG4T]
    # make-room: residents of any regret-target bay get in-bay contact
    # positions (recompaction opens the pocket the regretful block
    # needs; the CP prices every move exactly).
    XG4R = int(os.environ.get("XGEN4R", str(max(4, XGEN4 // 4))))
    for r, (rb, ro, rx, ry) in bestpat.items():
        if rb not in prefbays:
            continue
        seen = set(pats[r])
        occ = _occgrid(rb, beste[r], beste[r] + P[r], r)
        pats[r] = pats[r] + _contacts(r, rb, occ, seen, XG4R)

# GENERATOR X5 (targeted dense): blocks listed in DENSELIST get a
# stride-DSTRIDE grid over the bays listed in DENSEBAYS (comma ids) --
# the local-dense vocabulary for tardy-unblock mini-CPs. Scoped, so
# the pair count stays tractable.
X5 = os.environ.get("DENSELIST", "")
if X5:
    _dl = {int(x) for x in X5.split(",")}
    _db = [int(x) for x in os.environ.get("DENSEBAYS", "").split(",")
           if x != ""]
    _ds = int(os.environ.get("DSTRIDE", "2"))
    _cap = int(os.environ.get("XGEN5", "40"))
    for i in _dl:
        seen = set(pats[i])
        extra = []
        nor = len(prob["blocks"][i]["shape"])
        for tb in (_db or range(len(prob["bays"]))):
            for o_ in range(nor):
                x0, y0, x1, y1 = bbx(i, o_)
                px = -int(x0)
                while x1 + px <= bayW[tb] and len(extra) < _cap:
                    py = -int(y0)
                    while y1 + py <= bayH[tb] and len(extra) < _cap:
                        k = (tb, o_, px, py)
                        if k not in seen:
                            seen.add(k)
                            extra.append(k)
                        py += _ds
                    px += _ds
            if len(extra) >= _cap:
                break
        pats[i] = pats[i] + extra
# GENERATOR X1 (beyond the corner family): edge-offset positions -- for
# each anchor pattern in the pool, positions abutting its bbox edges at
# NON-CORNER offsets (the corner-scan can never produce these).
XGEN = int(os.environ.get("XGEN", "0"))
if XGEN > 0:
    allb = [(b_, bbx2) for row_i in range(n)
            for (b_, o_, x_, y_) in pats[row_i][:2]
            for bbx2 in [(bbx(row_i, o_)[0] + x_, bbx(row_i, o_)[1] + y_,
                          bbx(row_i, o_)[2] + x_, bbx(row_i, o_)[3] + y_)]]
    _rnd.seed(11)
    for i in range(n):
        seen = set(pats[i])
        extra = []
        nor = len(prob["blocks"][i]["shape"])
        for (b_, (ax0, ay0, ax1, ay1)) in _rnd.sample(
                allb, min(len(allb), 80)):
            o_ = _rnd.randrange(nor)
            x0, y0, x1, y1 = bbx(i, o_)
            w, h = x1 - x0, y1 - y0
            off = _rnd.randrange(1, max(2, int(h)))
            cands = [(ax1 - x0, ay0 - y0 + off),
                     (ax0 - x1, ay0 - y0 + off),
                     (ax0 - x0 + off, ay1 - y0),
                     (ax0 - x0 + off, ay0 - y1)]
            for (px, py) in cands:
                px, py = int(px), int(py)
                k = (b_, o_, px, py)
                if k in seen:
                    continue
                if x0 + px < 0 or y0 + py < 0 or \
                        x1 + px > bayW[b_] or y1 + py > bayH[b_]:
                    continue
                seen.add(k)
                extra.append(k)
                break
            if len(extra) >= XGEN:
                break
        pats[i] = pats[i] + extra
json.dump([[list(k) for k in row] for row in pats],
          open(f"runs/pats_p{pnum}.json", "w"))

pbb = [[(b_,) + (bbox(i, o_)[0] + x_, bbox(i, o_)[1] + y_,
                 bbox(i, o_)[2] + x_, bbox(i, o_)[3] + y_)
        for (b_, o_, x_, y_) in pats[i]] for i in range(n)]

eng = M._build_engine(prob)
eng.build_occ(H)
empty = [0, 0, 0, 0, 0, 0, 0] * n

import utils as _utils

def ok_at(i, pi, j, pj, ei, ej):
    """fast vp verdict (exact for strict orders)."""
    b1, o1, x1, y1 = pats[i][pi]
    b2, o2, x2, y2 = pats[j][pj]
    flat = list(empty)
    flat[i*7:i*7+7] = [b1, o1, x1, y1, ei, ei + P[i], 1]
    flat[j*7:j*7+7] = [b2, o2, x2, y2, ej, ej + P[j], 1]
    eng.set_solution(flat)
    return eng.vp_ok(i) and eng.vp_ok(j)

def ok_at_checker(i, pi, j, pj, ei, ej):
    """checker-exact verdict (used for tie combos on hint pairs)."""
    b1, o1, x1, y1 = pats[i][pi]
    b2, o2, x2, y2 = pats[j][pj]
    recs = {i: [b1, x1, y1, o1, ei, ei + P[i]],
            j: [b2, x2, y2, o2, ej, ej + P[j]]}
    return bool(_utils.check_feasibility(
        prob, M._recs_to_sol(recs)).get("feasible"))

def combo_witness(i, j, re_, rf):
    """Analytic witness: ej = 5, ei = 5 + delta with sign(delta) = re_
    and sign(delta + P_i - P_j) = rf, coexisting. None iff impossible."""
    dp = P[i] - P[j]
    cands = []
    if re_ == 0:
        cands = [0]
    elif re_ == 1:
        cands = [1, -dp, -dp + 1, -dp - 1]
    else:
        cands = [-1, -dp, -dp + 1, -dp - 1]
    for delta in cands:
        if (delta > 0) - (delta < 0) != re_:
            continue
        s = delta + dp
        if (s > 0) - (s < 0) != rf:
            continue
        base = 5 + max(0, -delta)
        ei, ej = base + delta, base
        if not (ei < ej + P[j] and ej < ei + P[i]):
            continue
        return ei, ej
    return None

def ov(a, b):
    return a[1] < b[3] and b[1] < a[3] and a[2] < b[4] and b[2] < a[4]

compat = {}
tested = 0
for i in range(n):
    for j in range(i + 1, n):
        for ki, bi in enumerate(pbb[i]):
            for kj, bj in enumerate(pbb[j]):
                if bi[0] != bj[0] or not ov(bi, bj):
                    continue
                v = []
                for re_ in (-1, 0, 1):
                    for rf in (-1, 0, 1):
                        wt = combo_witness(i, j, re_, rf)
                        v.append(None if wt is None
                                 else ok_at(i, ki, j, kj, *wt))
                compat[f"{i},{ki},{j},{kj}"] = v
                tested += 1
# HINT TIE PASS: the incumbent may legally use tie topologies that vp
# conservatively rejects; certify exactly those with the checker.
fixed_ties = 0
for i in bestpat:
    for j in bestpat:
        if j <= i or bestpat[i][0] != bestpat[j][0]:
            continue
        ei, ej = beste[i], beste[j]
        fi, fj = ei + P[i], ej + P[j]
        if not (ei < fj and ej < fi):
            continue
        re_ = (ei > ej) - (ei < ej)
        rf = (fi > fj) - (fi < fj)
        if re_ != 0 and rf != 0:
            continue
        ki = pats[i].index(bestpat[i])
        kj = pats[j].index(bestpat[j])
        key = f"{i},{ki},{j},{kj}"
        if key not in compat:
            continue
        idx = (re_ + 1) * 3 + (rf + 1)
        if compat[key][idx] is not True:
            if ok_at_checker(i, ki, j, kj, ei, ej):
                compat[key][idx] = True
                fixed_ties += 1
print(json.dumps({"hint_tie_certified": fixed_ties}), flush=True)

# TIEALL: certify tie combos for EVERY pair with a strict-True verdict
# (the model otherwise forbids the whole tie space).
if os.environ.get("TIEALL"):
    done = 0
    for key, v in compat.items():
        if not any(v[t] is True for t in (0, 2, 6, 8)):
            continue
        i, ki, j, kj = map(int, key.split(","))
        for idx, (re_, rf) in enumerate(
                [(a, b) for a in (-1, 0, 1) for b in (-1, 0, 1)]):
            if re_ != 0 and rf != 0:
                continue
            if v[idx] is True:
                continue
            wt = combo_witness(i, j, re_, rf)
            if wt is None:
                continue
            if ok_at_checker(i, ki, j, kj, *wt):
                v[idx] = True
                done += 1
    print(json.dumps({"tie_certified_all": done}), flush=True)
# AUDIT mode: sample strict-combo False verdicts, recheck with the
# checker-exact oracle. vp-True is safe (conservative approval); any
# checker-legal flip here means the ENTIRE certified feasible region
# is smaller than the checker's -- the systematic-gap hypothesis.
AUDIT = int(os.environ.get("AUDIT", "0"))
if AUDIT > 0:
    import random as _r2
    _r2.seed(7)
    falses = [(k2, idx) for k2, v2 in compat.items()
              for idx, val in enumerate(v2)
              if val is False and idx // 3 != 1 and idx % 3 != 1]
    _r2.shuffle(falses)
    flip = same = 0
    for k2, idx in falses[:AUDIT]:
        i2, ki2, j2, kj2 = map(int, k2.split(","))
        re2, rf2 = idx // 3 - 1, idx % 3 - 1
        wt = combo_witness(i2, j2, re2, rf2)
        if wt is None:
            continue
        if ok_at_checker(i2, ki2, j2, kj2, *wt):
            flip += 1
        else:
            same += 1
    print(json.dumps({"audit_false_total": len(falses),
                      "sampled": min(AUDIT, len(falses)),
                      "checker_legal_FLIPS": flip,
                      "confirmed_false": same}), flush=True)
# AUDIT2: the NESTING class specifically -- pairs whose footprint
# bboxes overlap but whose per-layer bboxes are disjoint at every
# shared layer (layer-compatible nesting candidates). The flat-heavy
# vocabulary made AUDIT's uniform sample blind to this class.
AUDIT2 = int(os.environ.get("AUDIT2", "0"))
if AUDIT2 > 0:
    def _laybb(blk, oi, l):
        lays = prob["blocks"][blk]["shape"][oi]["layers"]
        if l >= len(lays):
            return None
        vs = lays[l]
        xs = [v[0] for v in vs]; ys = [v[1] for v in vs]
        return min(xs), min(ys), max(xs), max(ys)

    def _nestpair(k2):
        i2, ki2, j2, kj2 = map(int, k2.split(","))
        b1, o1, x1, y1 = pats[i2][ki2]
        b2, o2, x2, y2 = pats[j2][kj2]
        L1 = len(prob["blocks"][i2]["shape"][o1]["layers"])
        L2 = len(prob["blocks"][j2]["shape"][o2]["layers"])
        shared = False
        for l in range(max(L1, L2)):
            a1 = _laybb(i2, o1, l)
            a2 = _laybb(j2, o2, l)
            if a1 is None or a2 is None:
                continue
            shared = True
            if not (a1[0]+x1 < a2[2]+x2 and a2[0]+x2 < a1[2]+x1 and
                    a1[1]+y1 < a2[3]+y2 and a2[1]+y2 < a1[3]+y1):
                continue
            return False        # same-layer overlap -> not a nest
        return shared

    import random as _r3
    _r3.seed(7)
    cands = [(k2, idx) for k2, v2 in compat.items()
             for idx, val in enumerate(v2)
             if val is False and idx // 3 != 1 and idx % 3 != 1]
    _r3.shuffle(cands)
    flip = same = seen2 = 0
    for k2, idx in cands:
        if seen2 >= AUDIT2:
            break
        if not _nestpair(k2):
            continue
        seen2 += 1
        i2, ki2, j2, kj2 = map(int, k2.split(","))
        re2, rf2 = idx // 3 - 1, idx % 3 - 1
        wt = combo_witness(i2, j2, re2, rf2)
        if wt is None:
            continue
        if ok_at_checker(i2, ki2, j2, kj2, *wt):
            flip += 1
        else:
            same += 1
    print(json.dumps({"audit2_nest_sampled": seen2,
                      "nest_FLIPS": flip,
                      "nest_confirmed_false": same}), flush=True)
json.dump(compat, open(f"runs/compat_p{pnum}.json", "w"))
ok_any = sum(1 for v in compat.values() if any(x for x in v if x))
print(json.dumps({"pairs": tested, "coexist_ok_any": ok_any}), flush=True)
