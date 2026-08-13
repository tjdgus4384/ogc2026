#!/usr/bin/env python3
"""Structure probe 4: WHERE THE WASTE LIVES.
(a) layer-0 poly / layer-0 bbox per orientation  (is the skew real, or a
    multi-layer union-bbox artifact?)
(b) is layer l+1 contained in layer l?
(c) free-space topology at each bay's peak day: connected components of
    bay \ union(layer-0 polygons), their areas vs block areas
(d) edge-direction histogram of layer-0 polygons (is there a shared lattice?)
(e) max clique of the NECESSARY-coexistence graph (a static packing that any
    punctual schedule must realise)
"""
import json, collections, math, statistics as st
import numpy as np
from shapely.geometry import Polygon, box
from shapely.affinity import translate
from shapely.ops import unary_union

REPO = "/data_fast/home/tjdgus4384/priv/ogc2026"

def L0(b,o): return Polygon(b["shape"][o]["layers"][0])

for p, tl in [(4,480),(5,600)]:
    d = json.load(open(f"{REPO}/data/preliminary_test/prob_{p}.json"))
    B = d["blocks"]; bays = d["bays"]; n = len(B)
    base = json.load(open(f"{REPO}/runs/boards/prob_{p}_tl{tl}_s7_5a1f16a33c21.json"))["base"]
    rec = [base[i*7:i*7+7] for i in range(n)]
    print("="*80); print(f"P{p} n={n}")

    # (a) layer-0 fill of its own layer-0 bbox
    r0=[]; rU=[]
    for b in B:
        best=0; bu=0
        for o in range(len(b["shape"])):
            q=L0(b,o); x0,y0,x1,y1=q.bounds
            best=max(best, q.area/((x1-x0)*(y1-y0)))
            u=unary_union([Polygon(z) for z in b["shape"][o]["layers"]])
            X0,Y0,X1,Y1=u.bounds
            bu=max(bu, q.area/((X1-X0)*(Y1-Y0)))
        r0.append(best); rU.append(bu)
    print(f"  layer0 area / layer0-bbox  (best ori): mean {st.mean(r0):.3f} median {st.median(r0):.3f} min {min(r0):.3f}")
    print(f"  layer0 area / UNION-bbox   (best ori): mean {st.mean(rU):.3f}")

    # (b) containment of upper layers
    cont=tot=0
    for b in B:
        Ls=[Polygon(z) for z in b["shape"][0]["layers"]]
        for l in range(len(Ls)-1):
            tot+=1
            if Ls[l].buffer(1e-9).contains(Ls[l+1]): cont+=1
    print(f"  layer l+1 contained in layer l: {cont}/{tot}")

    # (d) edge direction histogram, layer0 ori 0
    angs=[]
    for b in B:
        q=b["shape"][0]["layers"][0]
        for k in range(len(q)):
            a=q[k]; c=q[(k+1)%len(q)]
            angs.append(round(math.degrees(math.atan2(c[1]-a[1], c[0]-a[0])) % 180))
    h=collections.Counter(angs)
    print("  layer0 edge-direction histogram (deg mod 180), top 12:", h.most_common(12))
    print("  distinct directions:", len(h), " n edges:", len(angs))
    print("  vertices per layer0:", collections.Counter(len(b["shape"][0]["layers"][0]) for b in B).most_common())

    # (c) free-space topology at each bay's peak day
    T=max(r[5] for r in rec)
    for j,bay in enumerate(bays):
        W,H=bay["width"],bay["height"]; A=W*H
        rows=[]
        for t in range(T):
            res=[i for i,r in enumerate(rec) if r[0]==j and r[4]<=t<r[5]]
            if not res: continue
            a=sum(L0(B[i],rec[i][1]).area for i in res)
            rows.append((a,t,res))
        rows.sort(reverse=True)
        a,t,res = rows[0]
        polys=[translate(L0(B[i],rec[i][1]), rec[i][2], rec[i][3]) for i in res]
        occ=unary_union(polys)
        free=box(0,0,W,H).difference(occ)
        comps=[free] if free.geom_type=="Polygon" else list(free.geoms)
        comps=sorted([c.area for c in comps], reverse=True)
        blkA=sorted(L0(B[i],rec[i][1]).area for i in res)
        print(f"  bay{j} {W}x{H} peak t={t} k={len(res)}: occ {a/A:.3f} free {free.area/A:.3f} "
              f"in {len(comps)} components")
        print(f"     component areas (top 8): {[round(c,1) for c in comps[:8]]}   "
              f"blocks: min {blkA[0]:.1f} med {st.median(blkA):.1f} max {blkA[-1]:.1f}")
        print(f"     components >= smallest block area: {sum(1 for c in comps if c>=blkA[0])}"
              f"  total area in those: {sum(c for c in comps if c>=blkA[0])/A:.3f}")

    # (e) necessary-coexistence graph: greedy max clique
    R=[b["release_time"] for b in B]; D=[b["due_date"] for b in B]; P=[b["processing_time"] for b in B]
    adj=[set() for _ in range(n)]
    for i in range(n):
        for k in range(i+1,n):
            if not ((R[i]+P[i] <= D[k]-P[k]) or (R[k]+P[k] <= D[i]-P[i])):
                adj[i].add(k); adj[k].add(i)
    order=sorted(range(n), key=lambda i:-len(adj[i]))
    best=[]
    for s0 in order[:30]:
        cl=[s0]; cand=set(adj[s0])
        while cand:
            v=max(cand, key=lambda u: len(adj[u]&cand))
            cl.append(v); cand &= adj[v]
        if len(cl)>len(best): best=cl
    ba=sum(L0(B[i],0).area for i in best)   # ori-0 layer0 area, lower bound-ish
    bamin=sum(min(L0(B[i],o).area for o in range(len(B[i]["shape"]))) for i in best)
    totbay=sum(b["width"]*b["height"] for b in bays)
    print(f"  necessary-coexistence greedy max clique: {len(best)} blocks; "
          f"layer0 area {bamin:.0f} vs total bay area {totbay} -> density {bamin/totbay:.3f}")
