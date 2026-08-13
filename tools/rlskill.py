#!/usr/bin/env python3
"""ORDER-FREE LINE-DENSITY BOUND on the RLS tier-1 primitive.

Tier 1 freezes every resident's y and lets only x move (ANY literal
assignment, ANY Bellman-Ford solution).  On any horizontal line y*, the
footprints of the blocks resting in the bay that day are pairwise interior
disjoint, so the total chord length on that line is <= W.  That inequality
is INVARIANT under every x-layout and every literal flip -- no separation
system can evade it.

For each tardy block A, each bay j, orientation o, integer y, punctual day e:
      max over y* in A's span, over t in [e, e+P) : L_{j,t}(y*) + cA(y*) <= W ?
If it fails for every (j,o,y,e) then NO tier-1 re-layout seats A punctually.

INSTRUMENT CHECK first: every placed block, removed from its own bay-days,
must pass the bound at its own (bay,ori,y,e).  Anything else is a bug.
"""
import json
from collections import defaultdict

REPO = "/data_fast/home/tjdgus4384/priv/ogc2026"
STEP = 0.25


def chords(poly, y):
    xs = []
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) == (y2 > y):
            continue
        xs.append(x1 + (x2 - x1) * (y - y1) / (y2 - y1))
    xs.sort()
    return [(xs[k], xs[k + 1]) for k in range(0, len(xs) - 1, 2)]


def union_len(ivs):
    if not ivs:
        return 0.0
    ivs = sorted(ivs)
    tot = 0.0
    a, b = ivs[0]
    for c, d in ivs[1:]:
        if c > b:
            tot += b - a
            a, b = c, d
        else:
            b = max(b, d)
    return tot + b - a


def run(p, tl):
    d = json.load(open("%s/data/preliminary_test/prob_%d.json" % (REPO, p)))
    B, bays = d["blocks"], d["bays"]
    n = len(B)
    base = json.load(open("%s/runs/boards/prob_%d_tl%d_s7_5a1f16a33c21.json"
                          % (REPO, p, tl)))["base"]
    rec = [base[i * 7:i * 7 + 7] for i in range(n)]

    span, cprof = {}, {}
    for i in range(n):
        for o in range(len(B[i]["shape"])):
            lays = B[i]["shape"][o]["layers"]
            ys = [v[1] for L in lays for v in L]
            xs = [v[0] for L in lays for v in L]
            span[(i, o)] = (min(ys), max(ys), min(xs), max(xs))
            pr = {}
            k = int(round(min(ys) / STEP))
            while k * STEP < max(ys):
                y = k * STEP
                ivs = []
                for L in lays:
                    ivs += chords(L, y)
                v = union_len(ivs)
                if v > 0:
                    pr[k] = v
                k += 1
            cprof[(i, o)] = pr

    T = max(r[5] for r in rec) + 1
    tardy = [i for i in range(n) if rec[i][5] > B[i]["due_date"]]

    occ = [[defaultdict(float) for _ in range(T + 2)] for _ in bays]

    def contrib(i, o, y):
        sh = int(round(y / STEP))
        return {k + sh: c for k, c in cprof[(i, o)].items()}

    for i in range(n):
        j, o, x, y, e, f, pl = rec[i]
        if not pl:
            continue
        cc = contrib(i, o, y)
        for t in range(e, f):
            row = occ[j][t]
            for k, c in cc.items():
                row[k] += c

    print("=" * 78)
    print("P%d  n=%d  tardy=%d" % (p, n, len(tardy)))
    worstdens = 0.0
    for j, bay in enumerate(bays):
        W, H = bay["width"], bay["height"]
        rows = []
        peak, pk = 0.0, None
        for t in range(T):
            if not occ[j][t]:
                continue
            mx = max(occ[j][t].values())
            rows.append(mx / W)
            if mx > peak:
                peak, pk = mx, t
        rows.sort()
        worstdens = max(worstdens, peak / W)
        print("  bay%d %3dx%2d  peak line-density %.3f (t=%d)   "
              "day-peak: median %.3f  p75 %.3f" %
              (j, W, H, peak / W, pk, rows[len(rows) // 2],
               rows[int(len(rows) * .75)]))
    print("  INSTRUMENT: max line density over all bay-days = %.4f "
          "(must be <= 1.0)" % worstdens)

    def probe(A, days):
        """min over (bay,ori,y,e in days) of  max slack violation / W."""
        R, D, P = (B[A]["release_time"], B[A]["due_date"],
                   B[A]["processing_time"])
        own = contrib(A, rec[A][1], rec[A][3]) if rec[A][6] else {}
        ob, oe, of = rec[A][0], rec[A][4], rec[A][5]
        best, hit = 1e18, None
        for e in days:
            for j, bay in enumerate(bays):
                W, H = bay["width"], bay["height"]
                for o in range(len(B[A]["shape"])):
                    ylo, yhi, xlo, xhi = span[(A, o)]
                    if yhi - ylo > H + 1e-9 or xhi - xlo > W + 1e-9:
                        continue
                    pr = cprof[(A, o)]
                    for yi in range(0, int(H - (yhi - ylo)) + 1):
                        sh = int(round((yi - ylo) / STEP))
                        worst = -1e18
                        for t in range(e, min(e + P, T)):
                            row = occ[j][t]
                            sub = own if (j == ob and oe <= t < of) else None
                            for k, c in pr.items():
                                kk = k + sh
                                v = row.get(kk, 0.0)
                                if sub:
                                    v -= sub.get(kk, 0.0)
                                nd = v + c - W
                                if nd > worst:
                                    worst = nd
                            if worst > 0:
                                break
                        if worst < best:
                            best, hit = worst, (e, j, o, yi)
                            if best <= 1e-9:
                                return best, hit
        return best, hit

    # ---- instrument validation on ground truth --------------------------
    bad = 0
    for i in range(n):
        if not rec[i][6]:
            continue
        v, _ = probe(i, [rec[i][4]])
        if v > 1e-6:
            bad += 1
    print("  INSTRUMENT: placed blocks failing the bound at their OWN "
          "(bay,ori,y,e): %d of %d  (must be 0)" % (bad, n))

    # ---- the question ---------------------------------------------------
    ok, rows = 0, []
    for A in tardy:
        R, D, P = (B[A]["release_time"], B[A]["due_date"],
                   B[A]["processing_time"])
        v, h = probe(A, list(range(R, D - P + 1)))
        if v <= 1e-9:
            ok += 1
        rows.append((A, rec[A][5] - D, v, h))
    print("  TARDY blocks whose punctual seat is NOT excluded by the "
          "order-free bound: %d of %d" % (ok, len(tardy)))
    rows.sort(key=lambda r: -r[1])
    for A, lt, v, h in rows[:10]:
        st = "passes bound" if v <= 1e-9 else "EXCLUDED, needs %.2f more" % v
        print("    blk %3d late %3d : %s   best@(e,bay,ori,y)=%s"
              % (A, lt, st, h))


for p, tl in [(4, 480), (5, 600)]:
    run(p, tl)
