"""Exact re-scheduling of a FROZEN layout (sec 192).

Freeze (bay, ori, x, y) for every block and the geometry disappears: what
remains is a conflict graph plus n entry-time variables, i.e. a pure disjunctive
scheduling problem -- the one layer of this problem that IS exactly solvable.
Our kernel picks entry times by earliest-feasible greed, which is myopic.

Three conflict classes, all from the checker's own rules:
  REST  layer k of i overlaps layer k of j      -> intervals must be disjoint
  ENTRY layer k of i overlaps layer j>=k of the other, no rest conflict
        -> i may not descend while that block is present: e_i not in [e_k, f_k)
  EXIT  symmetric for the ascent: f_i not in (e_k, f_k]
"""
import json, math
from shapely.geometry import Polygon
from shapely import affinity
from ortools.sat.python import cp_model

def layers_at(b, rec):
    ori = rec[3] if len(rec) == 6 else rec[1]
    x = rec[1] if len(rec) == 6 else rec[2]
    y = rec[2] if len(rec) == 6 else rec[3]
    out = []
    for l in b['shape'][ori]['layers']:
        if len(l) < 3:
            out.append(None); continue
        out.append(affinity.translate(Polygon(l).buffer(0), xoff=x, yoff=y))
    return out

def build(p, recs):
    B = p['blocks']; n = len(B)
    P = [max(1, int(math.ceil(b['processing_time'] - 1e-9))) for b in B]
    R = [int(math.ceil(b['release_time'] - 1e-9)) for b in B]
    D = [int(round(b['due_date'])) for b in B]
    LA = [layers_at(B[i], recs[i]) for i in range(n)]
    bay = [recs[i][0] for i in range(n)]
    rest, ent = [], []
    for i in range(n):
        for k in range(i + 1, n):
            if bay[i] != bay[k]:
                continue
            li, lk = LA[i], LA[k]
            r = False
            for a in range(min(len(li), len(lk))):
                if li[a] is not None and lk[a] is not None and \
                        li[a].intersection(lk[a]).area > 1e-9:
                    r = True; break
            if r:
                rest.append((i, k)); continue
            # asymmetric entry/exit blocking: i's layer a vs other's layer b>=a
            def blocks(u, v):
                for a in range(len(u)):
                    if u[a] is None: continue
                    for b2 in range(a, len(v)):
                        if v[b2] is not None and \
                                u[a].intersection(v[b2]).area > 1e-9:
                            return True
                return False
            if blocks(li, lk):
                ent.append((i, k))          # i cannot pass k
            if blocks(lk, li):
                ent.append((k, i))          # k cannot pass i
    return P, R, D, rest, ent

def solve(p, recs, tl=45.0):
    n = len(p['blocks'])
    P, R, D, rest, ent = build(p, recs)
    HOR = max(D) + max(P) + 5
    md = cp_model.CpModel()
    e = [md.NewIntVar(R[i], HOR, '') for i in range(n)]
    f = [md.NewIntVar(R[i] + P[i], HOR + max(P), '') for i in range(n)]
    for i in range(n):
        md.Add(f[i] == e[i] + P[i])
    iv = [md.NewIntervalVar(e[i], P[i], f[i], '') for i in range(n)]
    for i, k in rest:
        md.AddNoOverlap([iv[i], iv[k]])
    for i, k in ent:                     # e_i not in [e_k, f_k)
        b1 = md.NewBoolVar('')
        md.Add(e[i] < e[k]).OnlyEnforceIf(b1)
        md.Add(e[i] >= f[k]).OnlyEnforceIf(b1.Not())
    T = []
    for i in range(n):
        t = md.NewIntVar(0, HOR + max(P), '')
        md.Add(t >= f[i] - D[i]); T.append(t)
    md.Minimize(sum(T))
    sv = cp_model.CpSolver()
    sv.parameters.max_time_in_seconds = tl
    sv.parameters.num_search_workers = 4
    r = sv.Solve(md)
    ok = r in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    if not ok:
        return None, sv.StatusName(r), len(rest), len(ent), None
    return ([ (sv.Value(e[i]), sv.Value(f[i])) for i in range(n) ],
            sv.StatusName(r), len(rest), len(ent), sv.ObjectiveValue())
