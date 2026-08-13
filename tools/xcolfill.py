#!/usr/bin/env python3
"""Structure probe 2: is the packing x-bound or y-bound? Column-fill analysis
on the shipped boards, plus wave structure of R/D, plus per-bay cohort sizes."""
import json, collections, statistics as st
import numpy as np
from shapely.geometry import Polygon, box
from shapely.affinity import translate
from shapely.ops import unary_union

REPO = "/data_fast/home/tjdgus4384/priv/ogc2026"

for p, tl in [(3,240),(4,480),(5,600),(6,900)]:
    d = json.load(open(f"{REPO}/data/preliminary_test/prob_{p}.json"))
    B = d["blocks"]; bays = d["bays"]; n = len(B)
    base = json.load(open(f"{REPO}/runs/boards/prob_{p}_tl{tl}_s7_5a1f16a33c21.json"))["base"]
    rec = [base[i*7:i*7+7] for i in range(n)]
    print("="*80); print(f"P{p} n={n} bays={[(b['width'],b['height']) for b in bays]}")

    geo = {}   # i -> (layer0 poly placed, bbox)
    for i,r in enumerate(rec):
        L = [translate(Polygon(q), r[2], r[3]) for q in B[i]["shape"][r[1]]["layers"]]
        u = unary_union(L)
        geo[i] = (L, u.bounds, u.area, L[0].area)

    T = max(r[5] for r in rec)
    # per bay-day: cohort size, x-span used, column fill profile
    for j,bay in enumerate(bays):
        W,H = bay["width"], bay["height"]
        best=None
        rows=[]
        for t in range(T):
            res=[i for i,r in enumerate(rec) if r[0]==j and r[4]<=t<r[5]]
            if not res: continue
            # column occupancy: for each unit x-column, total covered y length (layer0)
            xs=np.arange(0,W)+0.5
            colcov=np.zeros(W); colspan=np.zeros(W)
            for i in res:
                L,bnds,ua,a0 = geo[i]
                x0,y0,x1,y1 = bnds
                lo,hi = int(np.floor(x0)), int(np.ceil(x1))
                for c in range(max(0,lo), min(W,hi)):
                    colspan[c] = max(colspan[c], 1)
                    colcov[c] += (y1-y0)     # bbox height of this block over that column
            used = colspan.sum()
            fill = (colcov/H)
            rows.append((t, len(res), used/W, fill[colspan>0].mean() if used else 0,
                         sum(geo[i][3] for i in res)/(W*H),
                         sum((geo[i][1][2]-geo[i][1][0])*(geo[i][1][3]-geo[i][1][1]) for i in res)/(W*H)))
        if not rows: continue
        # report the day with max bbox-sum utilisation
        rows.sort(key=lambda r:-r[5])
        t,k,xocc,colf,polyu,bboxu = rows[0]
        med = st.median([r[1] for r in rows])
        print(f"  bay{j} {W}x{H}: cohort median {med:.0f} max {max(r[1] for r in rows)}")
        print(f"    peak-bbox day t={t}: k={k} x-columns occupied {xocc:.2f} "
              f"mean column y-fill {colf:.2f}  layer0-poly util {polyu:.2f}  bbox util {bboxu:.2f}")
    # wave structure
    R=[b["release_time"] for b in B]; D=[b["due_date"] for b in B]
    hR=np.histogram(R, bins=12)[0]; hD=np.histogram(D, bins=12)[0]
    print("  R hist:", list(hR)); print("  D hist:", list(hD))
