#!/usr/bin/env python3
"""CEILING OF THE RLS TIER-1 PRIMITIVE (frozen resident-resident literals).

Rebuilds the proposer's own 1-D separation system (tools/xslide.py) with a
pure-python port of geometry.hpp's overlap test, then computes BOTH
compactions:
    lo_i = min x_i over the polyhedron      (left compaction)
    hi_i = max x_i over the polyhedron      (right compaction)
For any pair (i,k), max achievable  x_k - x_i  is  hi_k - lo_i.  So the
largest NEW gap the system can open anywhere, under the frozen literals the
tier-1 seat-by-sliding move keeps, is
    G = max over coexisting pairs of (hi_k - lo_i) - (shipped gap)
plus the wall slab already reported in runs/ground/slack1d.log.

If G < the narrowest tardy block's width, no tier-1 re-layout can insert a
tardy block between two residents anywhere in that bay, for any y, any day.

Instrument check: the shipped board must satisfy every constraint, and must
satisfy lo_i <= x_i^ship <= hi_i for every block.
"""
import json, statistics as st

REPO = "/data_fast/home/tjdgus4384/priv/ogc2026"
EPS = 1e-9


# ---------------- geometry.hpp port ----------------------------------------
def cross(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def proper_cross(p1, p2, q1, q2):
    d1 = cross(q1, q2, p1); d2 = cross(q1, q2, p2)
    d3 = cross(p1, p2, q1); d4 = cross(p1, p2, q2)
    return (((d1 > EPS and d2 < -EPS) or (d1 < -EPS and d2 > EPS)) and
            ((d3 > EPS and d4 < -EPS) or (d3 < -EPS and d4 > EPS)))


def on_seg(a, b, p):
    if abs(cross(a, b, p)) > EPS * (abs(b[0] - a[0]) + abs(b[1] - a[1]) + 1):
        return False
    return (min(a[0], b[0]) - EPS < p[0] < max(a[0], b[0]) + EPS and
            min(a[1], b[1]) - EPS < p[1] < max(a[1], b[1]) + EPS)


def strictly_inside(poly, p):
    n = len(poly)
    for i in range(n):
        if on_seg(poly[i], poly[(i + 1) % n], p):
            return False
    inside = False
    j = n - 1
    for i in range(n):
        a, b = poly[i], poly[j]
        if (a[1] > p[1]) != (b[1] > p[1]):
            xint = (b[0] - a[0]) * (p[1] - a[1]) / (b[1] - a[1]) + a[0]
            if p[0] < xint:
                inside = not inside
        j = i
    return inside


def interior_point(poly):
    n = len(poly)
    A = sum(poly[j][0] * poly[i][1] - poly[i][0] * poly[j][1]
            for i, j in ((k, k - 1) for k in range(n)))
    orient = 1.0 if A > 0 else -1.0
    for i in range(n):
        pv, cu, nx = poly[(i - 1) % n], poly[i], poly[(i + 1) % n]
        if orient * cross(pv, cu, nx) > EPS:
            c = [(pv[0] + cu[0] + nx[0]) / 3.0, (pv[1] + cu[1] + nx[1]) / 3.0]
            for _ in range(6):
                if strictly_inside(poly, c):
                    return c
                c = [(c[0] + cu[0]) / 2.0, (c[1] + cu[1]) / 2.0]
    return [sum(v[0] for v in poly) / n, sum(v[1] for v in poly) / n]


def poly_overlap(A, ipA, B, ipB):
    na, nb = len(A), len(B)
    for i in range(na):
        a1, a2 = A[i], A[(i + 1) % na]
        for j in range(nb):
            if proper_cross(a1, a2, B[j], B[(j + 1) % nb]):
                return True
    if strictly_inside(B, ipA) or strictly_inside(A, ipB):
        return True
    for v in A:
        if strictly_inside(B, v):
            return True
    for v in B:
        if strictly_inside(A, v):
            return True
    return False


# ---------------- shape cache ---------------------------------------------
class Shape:
    __slots__ = ("lay", "ip", "lbb", "bb")

    def __init__(self, layers):
        self.lay = [[(float(v[0]), float(v[1])) for v in L] for L in layers]
        self.ip = [interior_point(L) for L in self.lay]
        self.lbb = [(min(v[0] for v in L), min(v[1] for v in L),
                     max(v[0] for v in L), max(v[1] for v in L))
                    for L in self.lay]
        self.bb = (min(b[0] for b in self.lbb), min(b[1] for b in self.lbb),
                   max(b[2] for b in self.lbb), max(b[3] for b in self.lbb))


def union_overlap(sa, sb, dx, dy):
    """union-of-layers footprints overlap when b is at (dx,dy) relative to a"""
    for la in range(len(sa.lay)):
        ax0, ay0, ax1, ay1 = sa.lbb[la]
        for lb in range(len(sb.lay)):
            bx0, by0, bx1, by1 = sb.lbb[lb]
            if not (ax0 < bx1 + dx - EPS and bx0 + dx < ax1 - EPS and
                    ay0 < by1 + dy - EPS and by0 + dy < ay1 - EPS):
                continue
            Bt = [(v[0] + dx, v[1] + dy) for v in sb.lay[lb]]
            ipb = [sb.ip[lb][0] + dx, sb.ip[lb][1] + dy]
            if poly_overlap(sa.lay[la], sa.ip[la], Bt, ipb):
                return True
    return False


def bellman(m, cons, lower):
    """minimal x with x[v] >= x[u]+c and x >= lower; None on positive cycle"""
    x = list(lower)
    for _ in range(4 * m + 4):
        ch = False
        for u, v, c in cons:
            if x[u] + c > x[v] + 1e-9:
                x[v] = x[u] + c
                ch = True
        if not ch:
            return x
    return None


def run(p, tl):
    d = json.load(open("%s/data/preliminary_test/prob_%d.json" % (REPO, p)))
    B, bays = d["blocks"], d["bays"]
    n = len(B)
    base = json.load(open("%s/runs/boards/prob_%d_tl%d_s7_5a1f16a33c21.json"
                          % (REPO, p, tl)))["base"]
    rec = [base[i * 7:i * 7 + 7] for i in range(n)]
    SH = {}
    for i in range(n):
        for o in range(len(B[i]["shape"])):
            SH[(i, o)] = Shape(B[i]["shape"][o]["layers"])

    tardy = [i for i in range(n) if rec[i][5] > B[i]["due_date"]]
    tw = sorted(min(SH[(i, o)].bb[2] - SH[(i, o)].bb[0]
                    for o in range(len(B[i]["shape"]))) for i in tardy)
    print("=" * 78)
    print("P%d  tardy=%d  narrowest tardy min-width %.1f  median %.1f"
          % (p, len(tardy), tw[0], st.median(tw)))

    for j, bay in enumerate(bays):
        W = bay["width"]
        idx = [i for i in range(n) if rec[i][0] == j and rec[i][6]]
        pos = {i: k for k, i in enumerate(idx)}
        m = len(idx)
        cons, pairs, nfroz, nact = [], [], 0, 0
        for a in range(m):
            for b_ in range(a + 1, m):
                i, k = idx[a], idx[b_]
                if not (rec[i][4] < rec[k][5] and rec[k][4] < rec[i][5]):
                    continue
                si, sk = SH[(i, rec[i][1])], SH[(k, rec[k][1])]
                dy = rec[k][3] - rec[i][3]
                if si.bb[3] <= sk.bb[1] + dy + EPS or \
                   sk.bb[3] + dy <= si.bb[1] + EPS:
                    continue
                lo_s = int(si.bb[0] - sk.bb[2]) - 2
                hi_s = int(si.bb[2] - sk.bb[0]) + 2
                bad = {}
                for xx in range(lo_s, hi_s + 1):
                    bad[xx] = union_overlap(si, sk, xx, dy)
                if not any(bad.values()):
                    continue
                nact += 1
                dx0 = rec[k][2] - rec[i][2]
                if bad.get(dx0, False):
                    nfroz += 1
                    cons.append((pos[i], pos[k], dx0))
                    cons.append((pos[k], pos[i], -dx0))
                    pairs.append((pos[i], pos[k], dx0))
                    continue
                xx = dx0
                while xx - 1 >= lo_s and not bad.get(xx - 1, False):
                    xx -= 1
                lo = None if xx <= lo_s else xx
                xx = dx0
                while xx + 1 <= hi_s and not bad.get(xx + 1, False):
                    xx += 1
                hi = None if xx >= hi_s else xx
                if lo is not None:
                    cons.append((pos[i], pos[k], lo))
                if hi is not None:
                    cons.append((pos[k], pos[i], -hi))
                pairs.append((pos[i], pos[k], dx0))

        loc = [(SH[(i, rec[i][1])].bb[0], SH[(i, rec[i][1])].bb[2]) for i in idx]
        Lw = [-l[0] for l in loc]
        Uw = [W - l[1] for l in loc]
        cur = [float(rec[i][2]) for i in idx]
        viol = sum(1 for u, v, c in cons if cur[u] + c > cur[v] + 1e-6)
        lo = bellman(m, cons, Lw)
        # right compaction: z = -x, constraint x[v]-x[u]>=c  ->  z[u]-z[v]>=c
        rz = bellman(m, [(v, u, c) for u, v, c in cons], [-u for u in Uw])
        if lo is None or rz is None:
            print("  bay%d: cycle" % j)
            continue
        hi = [-z for z in rz]
        rng = [hi[k] - lo[k] for k in range(m)]
        inb = sum(1 for k in range(m) if lo[k] - 1e-6 <= cur[k] <= hi[k] + 1e-6)
        growth = [(hi[v] - lo[u]) - (cur[v] - cur[u]) for u, v, _ in pairs]
        growth += [(hi[u] - lo[v]) - (cur[u] - cur[v]) for u, v, _ in pairs]
        growth.sort()
        print("  bay%d %3dx%2d m=%2d pairs=%4d frozen=%d | INSTR viol=%d "
              "in-range=%d/%d" % (j, W, bay["height"], m, nact, nfroz,
                                  viol, inb, m))
        print("      per-block travel hi-lo: mean %.2f med %.2f max %.2f "
              "| blocks with travel >= %.1f : %d/%d"
              % (st.mean(rng), st.median(rng), max(rng), tw[0],
                 sum(1 for r in rng if r >= tw[0]), m))
        print("      max pairwise GAP GROWTH (hi_k-lo_i - shipped): "
              "med %.2f  p90 %.2f  max %.2f  | >= narrowest tardy %.1f : %d/%d"
              % (st.median(growth), growth[int(len(growth) * .9)], max(growth),
                 tw[0], sum(1 for g in growth if g >= tw[0]), len(growth)))


for p, tl in [(4, 480), (5, 600)]:
    run(p, tl)
