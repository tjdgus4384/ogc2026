"""ssgs_packer.py -- entry-aware time-driven SSGS packer (A/B vs packer.pack_bay).

Diagnosis (2026-06-21): packer.pack_bay is already a time-driven serial scheduler that commits the
EARLIEST feasible entry per block (entry early => exit early => less tardiness). But its collision check
is CONSERVATIVE: `active` tuples store only (X0,Y0,X1,Y1,exit), and when probing entry e it forbids EVERY
resident with exit > e -- i.e. every footprint still present at the instant e. The correct forbidden set
for a block occupying [e, e+p) is only the residents whose own interval [entry,exit) OVERLAPS [e,e+p):
{ entry < e+p AND exit > e }. A resident that ENTERS after the new block exits (entry >= e+p) does NOT
conflict, yet pack_bay forbids it -> the new block is pushed later than necessary -> avoidable tardiness.
CP-SAT (true no-overlap) does not make this error, which is part of why it out-packs the greedy.

Fix = store entry in `active` and forbid only truly-overlapping residents. Exact same algorithm/speed,
just an exact (instead of conservative) feasibility test. Pairwise disjointness still holds because when
the LATER block of any pair is placed, the overlap test is applied symmetrically.
"""
import math
from packer import _best_pos


def pack_bay_ea(W, H, metas, max_ent=64):
    """Entry-aware variant of packer.pack_bay. Returns [(id,x,y,orient_idx,entry,exit)]."""
    active = []   # (X0,Y0,X1,Y1, entry, exit)
    out = []
    for mt in metas:
        r, p = mt["release"], mt["proc"]
        # space changes only at resident exits; also a resident can free b's window by entering late
        ent_cands = sorted({r} | {ex for (*_, en, ex) in active if ex > r})[:max_ent]
        best = None
        for e in ent_cands:
            # forbid only residents whose [entry,exit) overlaps b's [e, e+p); _best_pos wants 5-tuples
            present = [(a[0], a[1], a[2], a[3], a[5]) for a in active if a[5] > e and a[4] < e + p]
            for (oi, lx0, ly0, lx1, ly1) in mt["orients"]:
                if math.ceil(-lx0) > math.floor(W - lx1) or math.ceil(-ly0) > math.floor(H - ly1):
                    continue
                pos = _best_pos(W, H, lx0, ly0, lx1, ly1, present)
                if pos is None:
                    continue
                px, py, X0, Y0, X1, Y1 = pos
                cand = (e, Y1, px, py, oi, X0, Y0, X1, Y1)
                if best is None or (cand[0], cand[1]) < (best[0], best[1]):
                    best = cand
            if best is not None and best[0] == e:
                break
        if best is None:
            e = max(r, max((ex for (*_, en, ex) in active), default=0))
            chosen = None
            for (oi, lx0, ly0, lx1, ly1) in mt["orients"]:
                if math.ceil(-lx0) <= math.floor(W - lx1) and math.ceil(-ly0) <= math.floor(H - ly1):
                    chosen = (oi, lx0, ly0, lx1, ly1); break
            if chosen is None:
                chosen = mt["orients"][0]
            oi, lx0, ly0, lx1, ly1 = chosen
            px, py = max(0, math.ceil(-lx0)), max(0, math.ceil(-ly0))
            X0, Y0, X1, Y1 = px + lx0, py + ly0, px + lx1, py + ly1
            best = (e, Y1, px, py, oi, X0, Y0, X1, Y1)
        e, _, px, py, oi, X0, Y0, X1, Y1 = best
        f = e + p
        active.append((X0, Y0, X1, Y1, e, f))
        out.append((mt["id"], px, py, oi, e, f))
    return out
