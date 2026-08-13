#!/usr/bin/env python3
"""PRE-TEST for the separation-system architecture (1-D, y frozen).

For every time-overlapping same-bay pair, at their CURRENT y-offset, compute
the EXACT set of integer x-offsets dx at which their union-of-layer footprints
overlap.  Its complement is a union of intervals; the component containing the
board's own dx is the pair's "separation literal", and it gives two difference
constraints  lo <= x_k - x_i <= hi.  Pairs whose current dx is INSIDE the bad
set are nested (crane-bound) and get dx frozen.

Every point of the resulting polyhedron is a layout in which no two coexisting
blocks' footprints touch -- which makes all five crane rules vacuous.  So:
  * is the shipped board a feasible point?  (instrument check on ground truth)
  * how far can ALL blocks slide left at once?  (collective freedom)
  * how wide a slab opens on the right, per day?  (room for a tardy block)
"""
import json, statistics as st, sys
import numpy as np
from shapely.geometry import Polygon
from shapely.affinity import translate
from shapely.ops import unary_union

REPO = "/data_fast/home/tjdgus4384/priv/ogc2026"

def foot(b, o):
    return unary_union([Polygon(q) for q in b["shape"][o]["layers"]])

def sep(Fi, Fk, dy, W):
    """(lo, hi, frozen) for x_k - x_i, from the component containing dx0."""
    Fk = translate(Fk, 0, dy)
    ix0,iy0,ix1,iy1 = Fi.bounds; kx0,ky0,kx1,ky1 = Fk.bounds
    if iy1 <= ky0 + 1e-9 or ky1 <= iy0 + 1e-9: return None          # y-separated
    lo_s = int(np.floor(ix0 - kx1)) - 1; hi_s = int(np.ceil(ix1 - kx0)) + 1
    bad = {}
    for x in range(lo_s, hi_s+1):
        bad[x] = translate(Fk, x, 0).intersection(Fi).area > 1e-9
    return bad, lo_s, hi_s

for p, tl in [(4,480),(5,600)]:
    d = json.load(open(f"{REPO}/data/preliminary_test/prob_{p}.json"))
    B = d["blocks"]; bays = d["bays"]; n = len(B)
    base = json.load(open(f"{REPO}/runs/boards/prob_{p}_tl{tl}_s7_5a1f16a33c21.json"))["base"]
    rec = [base[i*7:i*7+7] for i in range(n)]
    F = {i: foot(B[i], rec[i][1]) for i in range(n)}
    LOC = {i: (F[i].bounds[0], F[i].bounds[2]) for i in range(n)}  # F is LOCAL
    T = max(r[5] for r in rec)
    tardy = [i for i in range(n) if rec[i][5] > B[i]["due_date"]]
    print("="*78); print(f"P{p}: {len(tardy)} tardy blocks, horizon {T}")

    for j, bay in enumerate(bays):
        W, H = bay["width"], bay["height"]
        idx = [i for i in range(n) if rec[i][0] == j]
        pos = {i:k for k,i in enumerate(idx)}; m=len(idx)
        cons=[]; npair=nact=nfroz=0
        for a in range(m):
            for b_ in range(a+1, m):
                i,k = idx[a], idx[b_]
                if not (rec[i][4] < rec[k][5] and rec[k][4] < rec[i][5]): continue
                npair += 1
                r = sep(F[i], F[k], rec[k][3]-rec[i][3], W)
                if r is None: continue
                bad, lo_s, hi_s = r
                if not any(bad.values()): continue
                nact += 1
                dx0 = rec[k][2]-rec[i][2]
                if bad.get(dx0, False):
                    nfroz += 1
                    cons.append((pos[i],pos[k],dx0)); cons.append((pos[k],pos[i],-dx0))
                    continue
                lo = lo_s-10**6
                x=dx0
                while x-1 >= lo_s and not bad.get(x-1,False): x-=1
                lo = -10**6 if x <= lo_s else x
                x=dx0
                while x+1 <= hi_s and not bad.get(x+1,False): x+=1
                hi = 10**6 if x >= hi_s else x
                if lo > -10**5: cons.append((pos[i],pos[k],lo))    # x_k-x_i>=lo
                if hi <  10**5: cons.append((pos[k],pos[i],-hi))   # x_i-x_k>=-hi
        lo_loc=np.array([LOC[i][0] for i in idx]); hi_loc=np.array([LOC[i][1] for i in idx])
        L=-lo_loc; U=W-hi_loc
        cur=np.array([float(rec[i][2]) for i in idx])
        viol=[(u,v,c) for (u,v,c) in cons if cur[u]+c > cur[v]+1e-6]
        print(f"  bay{j} {W}x{H}: {m} blocks, {npair} coexisting pairs, {nact} x-binding, "
              f"{nfroz} nested(frozen); shipped board violates {len(viol)} constraints")
        x=L.copy(); ok=True
        for _ in range(4*m+4):
            ch=False
            for (u,v,c) in cons:
                if x[u]+c > x[v]+1e-9: x[v]=x[u]+c; ch=True
            if not ch: break
        else: ok=False
        if not ok or np.any(x > U+1e-6):
            print("    left-compaction infeasible (cycle or wall)"); continue
        shift=cur-x
        print(f"    left compaction: sum(x) {cur.sum():.0f} -> {x.sum():.0f}   "
              f"mean shift {shift.mean():.2f} max {shift.max():.1f}  moved>0.5: {(shift>0.5).sum()}/{m}  moved>2: {(shift>2).sum()}")
        rows=[]
        for t in range(T):
            rt=[k for k,i in enumerate(idx) if rec[i][4] <= t < rec[i][5]]
            if not rt: continue
            f0=max(cur[k]+hi_loc[k] for k in rt); f1=max(x[k]+hi_loc[k] for k in rt)
            rows.append((t,len(rt),f0,f1,f0-f1,W-f1))
        g=[r[4] for r in rows]; slab=[r[5] for r in rows]
        tw=sorted(min(foot(B[i],o).bounds[2]-foot(B[i],o).bounds[0]
                      for o in range(len(B[i]["shape"]))) for i in tardy)
        print(f"    right frontier gain/day: mean {st.mean(g):.2f} med {st.median(g):.2f} max {max(g):.1f}")
        print(f"    free right slab after/day: mean {st.mean(slab):.2f} med {st.median(slab):.2f} max {max(slab):.1f}"
              f"   (tardy min widths: min {tw[0]:.1f} med {st.median(tw):.1f})")
        print(f"    days whose slab fits the narrowest tardy block: {sum(1 for s0 in slab if s0>=tw[0])}/{len(slab)}"
              f" ; fits the median tardy block: {sum(1 for s0 in slab if s0>=st.median(tw))}/{len(slab)}")
