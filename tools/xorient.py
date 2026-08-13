#!/usr/bin/env python3
"""Structure probe 3: ORIENTATION structure.
Are the 8 orientations 45-degree rotations?  How much bbox does a rotated
orientation waste?  Which orientations does the shipped board actually pick,
and what would the peak-day bbox load be if every block took its most
bbox-compact orientation?"""
import json, collections, math, statistics as st
import numpy as np
from shapely.geometry import Polygon
from shapely.affinity import translate
from shapely.ops import unary_union

REPO = "/data_fast/home/tjdgus4384/priv/ogc2026"

def ori_stats(b, o):
    L = [Polygon(q) for q in b["shape"][o]["layers"]]
    u = unary_union(L)
    x0,y0,x1,y1 = u.bounds
    pa = max(p.area for p in L)          # layer-max polygon area (the level.py currency)
    return (x1-x0)*(y1-y0), pa, (x1-x0), (y1-y0)

for p, tl in [(3,240),(4,480),(5,600),(6,900)]:
    d = json.load(open(f"{REPO}/data/preliminary_test/prob_{p}.json"))
    B = d["blocks"]; bays = d["bays"]; n = len(B)
    base = json.load(open(f"{REPO}/runs/boards/prob_{p}_tl{tl}_s7_5a1f16a33c21.json"))["base"]
    rec = [base[i*7:i*7+7] for i in range(n)]
    print("="*80); print(f"P{p} n={n}")

    # first-edge angle of layer0 per orientation -> is it a 45-deg rotation family?
    angs=[]
    for b in B[:50]:
        a=[]
        for s in b["shape"]:
            q=s["layers"][0]
            a.append(math.degrees(math.atan2(q[1][1]-q[0][1], q[1][0]-q[0][0])) % 360)
        if len(a)==8:
            angs.append(tuple(round((a[k]-a[0])%360) for k in range(8)))
    print("  angle offsets (mode over blocks):", collections.Counter(angs).most_common(2)[0] if angs else None)

    stats = [[ori_stats(b,o) for o in range(len(b["shape"]))] for b in B]
    # poly/bbox ratio per orientation
    ratios = [[s[1]/s[0] for s in row] for row in stats]
    chosen = [ratios[i][rec[i][1]] for i in range(n)]
    bestr  = [max(r) for r in ratios]
    worstr = [min(r) for r in ratios]
    print(f"  poly/bbox ratio: chosen mean {st.mean(chosen):.3f}  best-possible mean {st.mean(bestr):.3f}  worst mean {st.mean(worstr):.3f}")
    # how often is the chosen orientation the compact one?
    rank=[sorted(range(len(ratios[i])), key=lambda o:-ratios[i][o]).index(rec[i][1]) for i in range(n)]
    print("  rank of chosen orientation by compactness (0=best):", collections.Counter(rank).most_common())
    # even (axis-aligned?) vs odd orientation index
    print("  chosen orientation index histogram:", collections.Counter(r[1] for r in rec).most_common())

    # peak-day load in three currencies: chosen bbox / most-compact bbox / polygon
    T=max(r[5] for r in rec)
    for j,bay in enumerate(bays):
        W,H=bay["width"],bay["height"]; A=W*H
        rows=[]
        for t in range(T):
            res=[i for i,r in enumerate(rec) if r[0]==j and r[4]<=t<r[5]]
            if not res: continue
            cb=sum(stats[i][rec[i][1]][0] for i in res)/A
            mb=sum(min(s[0] for s in stats[i]) for i in res)/A
            pl=sum(stats[i][rec[i][1]][1] for i in res)/A
            rows.append((cb,mb,pl,t,len(res)))
        rows.sort(reverse=True)
        cb,mb,pl,t,k=rows[0]
        print(f"   bay{j} {W}x{H} peak t={t} k={k}: chosen-bbox {cb:.3f}  min-bbox {mb:.3f}  polygon {pl:.3f}")
