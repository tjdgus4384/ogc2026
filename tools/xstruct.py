#!/usr/bin/env python3
"""One-off structure probe for the architecture proposal (08-14).

Measures, from the INSTANCE FILES and the cached shipped boards:
  A. shelf/row structure: y-coordinate histogram per bay
  B. nesting: time-overlapping same-bay pairs that rest-disjoint but
     sweep-block (the pairs the crane rules actually bind on)
  C. temporal rigidity: pairs that NECESSARILY coexist under any punctual
     schedule, and the size of the per-block punctual entry choice set
  D. per-day punctual demand vs bay-width capacity in a 1-D "lane" currency
"""
import json, collections, itertools, sys, statistics as st
from shapely.geometry import Polygon
from shapely.affinity import translate

REPO = "/data_fast/home/tjdgus4384/priv/ogc2026"

def load(p):
    return json.load(open(f"{REPO}/data/preliminary_test/prob_{p}.json"))

def opolys(b, o):
    return [Polygon(L) for L in b["shape"][o]["layers"]]

def bb(b, o):
    xs=[q[0] for L in b["shape"][o]["layers"] for q in L]
    ys=[q[1] for L in b["shape"][o]["layers"] for q in L]
    return max(xs)-min(xs), max(ys)-min(ys)

for p, tl in [(3,240),(4,480),(5,600),(6,900)]:
    d = load(p); B = d["blocks"]; bays = d["bays"]; n = len(B)
    base = json.load(open(f"{REPO}/runs/boards/prob_{p}_tl{tl}_s7_5a1f16a33c21.json"))["base"]
    rec = [base[i*7:i*7+7] for i in range(n)]
    print("="*78)
    print(f"P{p} n={n} bays={[(b['width'],b['height']) for b in bays]}")

    # ---- A. shelf structure -------------------------------------------------
    for j,bay in enumerate(bays):
        rows = [(r[3], r[3]+bb(B[i], r[1])[1]) for i,r in enumerate(rec) if r[0]==j]
        if not rows: continue
        y0 = sorted(a for a,_ in rows)
        H = bay["height"]
        # how many blocks sit flush at y=0 vs flush at top vs interior
        flush0 = sum(1 for a,b_ in rows if a < 0.5)
        flushT = sum(1 for a,b_ in rows if b_ > H-0.5)
        interior = len(rows) - flush0 - flushT
        print(f"  bay{j} H={H}: {len(rows)} placements  y0=0:{flush0}  top-flush:{flushT}  interior:{interior}")

    # ---- B. nesting ---------------------------------------------------------
    geo = {}
    for i,r in enumerate(rec):
        geo[i] = [translate(q, r[2], r[3]) for q in opolys(B[i], r[1])]
    tov=nest=0
    for i in range(n):
        for k in range(i+1,n):
            ri,rk = rec[i],rec[k]
            if ri[0]!=rk[0]: continue
            if not (ri[4]<rk[5] and rk[4]<ri[5]): continue
            tov += 1
            Pi,Pk = geo[i],geo[k]
            rest = any(Pi[l].intersection(Pk[l]).area>1e-9 for l in range(min(len(Pi),len(Pk))))
            sweep = any(Pi[a].intersection(Pk[b_]).area>1e-9
                        for a in range(len(Pi)) for b_ in range(len(Pk)))
            if rest: print("   !! REST VIOLATION", i, k)
            if (not rest) and sweep: nest += 1
    print(f"  time-overlapping same-bay pairs {tov}; NESTED (crane-binding) {nest} "
          f"({100*nest/max(tov,1):.2f}%)")

    # ---- C. temporal rigidity ----------------------------------------------
    R=[b["release_time"] for b in B]; D=[b["due_date"] for b in B]; P=[b["processing_time"] for b in B]
    slack=[D[i]-P[i]-R[i] for i in range(n)]
    nec=0; poss=0
    for i in range(n):
        for k in range(i+1,n):
            # can be disjoint in time under punctual entries?
            disj = (R[i]+P[i] <= D[k]-P[k]) or (R[k]+P[k] <= D[i]-P[i])
            # can they overlap at all?
            ov = (R[i] < D[k]-P[k]+P[k]) and (R[k] < D[i]-P[i]+P[i])
            if not disj: nec += 1
            if ov: poss += 1
    tot=n*(n-1)//2
    print(f"  punctual entry choices per block: mean {st.mean([s+1 for s in slack]):.2f} "
          f"median {st.median([s+1 for s in slack])} max {max(slack)+1}")
    print(f"  NECESSARILY-coexisting pairs (any punctual sched): {nec}/{tot} = {100*nec/tot:.1f}%")

    # ---- D. lane currency ---------------------------------------------------
    # 1-lane width demand: min-over-orientation bbox WIDTH when the block is laid
    # in its widest-height orientation is the wrong currency; use area/H_bay.
    # Instead: per day, sum of min-bbox AREA of punctual-resident blocks (at the
    # levelling optimum this is what tools/level.py minimised) vs sum of bay areas.
    print()
