"""cpsat_packer.py -- per-bay packer via CP-SAT (footprint-disjoint, integer reference coords).

N3 [memory n3-cpsat-packer] measured CP-SAT beats the greedy+SA packer 2-5x per bay. This packs ONE bay's
block-set to minimize total tardiness under footprint-disjoint placement, in utils-native integer
reference coordinates so any model-feasible solution is utils-feasible:
  per orientation o, integer cell extents offlo=floor(lx0), offhi=ceil(lx1) (cells COVER the real
  footprint => conservative => disjoint cells ⇒ disjoint real footprints ⇒ crane+collision free).
Reference x in [ceil(-lx0), W-offhi] guarantees containment of the real polygon. Pairwise:
time-sep ∨ x-sep ∨ y-sep. Warm-started from the heuristic packer.
"""
import math
from ortools.sat.python import cp_model
from packer import pack_bay


def _cells(blk_orients, W, H):
    """Per orientation: (oi, offlox, offhix, offloy, offhiy, xref_lo, xref_hi, yref_lo, yref_hi),
    keeping only orientations that fit the bay. Integer, conservative (cells cover the footprint)."""
    out = []
    for (oi, lx0, ly0, lx1, ly1) in blk_orients:
        offlox, offhix = math.floor(lx0), math.ceil(lx1)
        offloy, offhiy = math.floor(ly0), math.ceil(ly1)
        xlo, xhi = math.ceil(-lx0), W - offhix
        ylo, yhi = math.ceil(-ly0), H - offhiy
        if xlo <= xhi and ylo <= yhi:
            out.append((oi, offlox, offhix, offloy, offhiy, xlo, xhi, ylo, yhi))
    return out


def pack_bay_cpsat(W, H, metas, prob_blocks, tl, workers=4, hint=None, fixed=None, cumulative=True):
    """Return placements [(id,x,y,oi,e,f)] minimizing Σ tardiness for this bay, or None if no model.
    hint (optional) = {id:(x,y,oi,entry)} warm start (e.g. the improver's placement); falls back to the
    heuristic packer's placement when absent.
    fixed (optional) = list of (X0,Y0,X1,Y1,entry,exit) immovable obstacle boxes the free `metas` must
    avoid (footprint-disjoint when time-overlapping) — used by LNS repair (re-optimize a hot subset
    while the rest of the bay stays put). This is where CP-SAT is strongest: small free set, converges."""
    if not metas:
        return []
    cm = cp_model.CpModel()
    maxR = max(m["release"] for m in metas); sumP = sum(m["proc"] for m in metas)
    Hor = maxR + sumP + 1
    V = {}                      # id -> dict of vars
    cellL = {}; cellR = {}; cellB = {}; cellT = {}
    for m in metas:
        i = m["id"]
        cells = _cells(m["orients"], W, H)
        if not cells:
            return None         # block fits no orientation -> let caller fall back
        sel = [cm.NewBoolVar(f"s{i}_{c[0]}") for c in cells]
        cm.AddExactlyOne(sel)
        xlo = min(c[5] for c in cells); xhi = max(c[6] for c in cells)
        ylo = min(c[7] for c in cells); yhi = max(c[8] for c in cells)
        x = cm.NewIntVar(min(0, xlo), max(0, xhi), f"x{i}")
        y = cm.NewIntVar(min(0, ylo), max(0, yhi), f"y{i}")
        # per-orientation reference bounds + cell edges (channelled on sel)
        offlox = cm.NewIntVar(min(c[1] for c in cells), max(c[1] for c in cells), f"olx{i}")
        offhix = cm.NewIntVar(min(c[2] for c in cells), max(c[2] for c in cells), f"ohx{i}")
        offloy = cm.NewIntVar(min(c[3] for c in cells), max(c[3] for c in cells), f"oly{i}")
        offhiy = cm.NewIntVar(min(c[4] for c in cells), max(c[4] for c in cells), f"ohy{i}")
        cm.Add(offlox == sum(b * c[1] for b, c in zip(sel, cells)))
        cm.Add(offhix == sum(b * c[2] for b, c in zip(sel, cells)))
        cm.Add(offloy == sum(b * c[3] for b, c in zip(sel, cells)))
        cm.Add(offhiy == sum(b * c[4] for b, c in zip(sel, cells)))
        for b, c in zip(sel, cells):
            cm.Add(x >= c[5]).OnlyEnforceIf(b); cm.Add(x <= c[6]).OnlyEnforceIf(b)
            cm.Add(y >= c[7]).OnlyEnforceIf(b); cm.Add(y <= c[8]).OnlyEnforceIf(b)
        s = cm.NewIntVar(m["release"], Hor, f"st{i}")
        e = cm.NewIntVar(m["release"] + m["proc"], Hor, f"en{i}")
        cm.Add(e == s + m["proc"])
        t = cm.NewIntVar(0, Hor, f"t{i}"); cm.Add(t >= e - m["due"])
        cL = cm.NewIntVar(-10**6, 10**6, f"cL{i}"); cm.Add(cL == x + offlox)
        cR = cm.NewIntVar(-10**6, 10**6, f"cR{i}"); cm.Add(cR == x + offhix)
        cB = cm.NewIntVar(-10**6, 10**6, f"cB{i}"); cm.Add(cB == y + offloy)
        cT = cm.NewIntVar(-10**6, 10**6, f"cT{i}"); cm.Add(cT == y + offhiy)
        V[i] = dict(sel=sel, cells=cells, x=x, y=y, s=s, e=e, t=t)
        cellL[i], cellR[i], cellB[i], cellT[i] = cL, cR, cB, cT
    ids = [m["id"] for m in metas]
    for a in range(len(ids)):
        for b in range(a + 1, len(ids)):
            i, k = ids[a], ids[b]
            lx = cm.NewBoolVar(""); rx = cm.NewBoolVar(""); dn = cm.NewBoolVar(""); up = cm.NewBoolVar("")
            bt = cm.NewBoolVar(""); bt2 = cm.NewBoolVar("")
            cm.Add(cellR[i] <= cellL[k]).OnlyEnforceIf(lx)
            cm.Add(cellR[k] <= cellL[i]).OnlyEnforceIf(rx)
            cm.Add(cellT[i] <= cellB[k]).OnlyEnforceIf(dn)
            cm.Add(cellT[k] <= cellB[i]).OnlyEnforceIf(up)
            cm.Add(V[i]["e"] <= V[k]["s"]).OnlyEnforceIf(bt)
            cm.Add(V[k]["e"] <= V[i]["s"]).OnlyEnforceIf(bt2)
            cm.AddBoolOr([lx, rx, dn, up, bt, bt2])
    # free blocks must avoid the fixed obstacle boxes (disjoint in x OR y OR time when present)
    for (gX0, gY0, gX1, gY1, gE, gS) in (fixed or []):
        for i in ids:
            lx = cm.NewBoolVar(""); rx = cm.NewBoolVar(""); dn = cm.NewBoolVar(""); up = cm.NewBoolVar("")
            bt = cm.NewBoolVar(""); bt2 = cm.NewBoolVar("")
            cm.Add(cellR[i] <= gX0).OnlyEnforceIf(lx)
            cm.Add(gX1 <= cellL[i]).OnlyEnforceIf(rx)
            cm.Add(cellT[i] <= gY0).OnlyEnforceIf(dn)
            cm.Add(gY1 <= cellB[i]).OnlyEnforceIf(up)
            cm.Add(V[i]["e"] <= gE).OnlyEnforceIf(bt)
            cm.Add(gS <= V[i]["s"]).OnlyEnforceIf(bt2)
            cm.AddBoolOr([lx, rx, dn, up, bt, bt2])
    # REDUNDANT area-cumulative: the model separates integer CELLS (pairwise x/y/time), so at any instant the
    # resident cells are disjoint within W*H => their areas sum to <= W*H. Adding this as a cumulative resource
    # (interval [s,e), demand = min-orientation cell area, capacity = W*H) is a valid necessary condition that
    # propagates strongly -> CP-SAT prunes over-packed time windows fast -> reaches Z1=0 quicker on dense bays.
    if cumulative:
        intervals = []; demands = []
        for m in metas:
            i = m["id"]
            iv = cm.NewIntervalVar(V[i]["s"], m["proc"], V[i]["e"], f"iv{i}")
            amin = min((c[2] - c[1]) * (c[4] - c[3]) for c in V[i]["cells"])   # min cell area (<= selected)
            if amin > 0:
                intervals.append(iv); demands.append(int(amin))
        if intervals:
            cm.AddCumulative(intervals, demands, int(W * H))
    cm.Minimize(sum(V[i]["t"] for i in ids))

    # warm start: external hint (e.g. improver placement) if given, else the heuristic packer
    if hint is None:
        hint = {bid: (x, y, oi, e) for (bid, x, y, oi, e, f) in pack_bay(W, H, metas)}
    for m in metas:
        i = m["id"]
        if i in hint:
            hx, hy, hoi, he = hint[i]
            cm.AddHint(V[i]["x"], int(hx)); cm.AddHint(V[i]["y"], int(hy))
            cm.AddHint(V[i]["s"], int(he - m["proc"]))
            for b, c in zip(V[i]["sel"], V[i]["cells"]):
                if c[0] == hoi:
                    cm.AddHint(b, 1)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = tl
    solver.parameters.num_search_workers = workers
    st = solver.Solve(cm)
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    out = []
    for m in metas:
        i = m["id"]
        oi = next(c[0] for b, c in zip(V[i]["sel"], V[i]["cells"]) if solver.Value(b) == 1)
        s = solver.Value(V[i]["s"])
        out.append((i, solver.Value(V[i]["x"]), solver.Value(V[i]["y"]), oi, s, s + m["proc"]))
    return out
