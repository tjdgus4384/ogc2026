"""beam.py -- beam-search DP over block placement (fixed EDD insertion order, fast cuboid decoder).

Stage k places the k-th block (EDD order) -> "remaining = suffix", no subset explosion. Each beam state
is a full partial placement. Children = candidate placements of the next block (every bay x every
footprint-disjoint bottom-left position at the earliest feasible entry). Rank ALL children by the exact
partial objective g = w1*Z1_partial + w2*Z2_partial + w3*Z3_partial (uses the real weights -> regime
agnostic), keep top B. B=1 reduces to greedy; larger B -> closer to full DP.

Decoder is cuboid (footprint-disjoint AABB) for speed; this tests the SEARCH, not the geometry.
"""
import math
from packer import block_meta


def _cand_positions(W, H, mt, active):
    """All footprint-disjoint bottom-left candidate placements at the EARLIEST feasible entry.
    Returns list of (x, y, oi, e, X0, Y0, X1, Y1)."""
    r, p = mt["release"], mt["proc"]
    ent = sorted({r} | {ex for (*_, ex) in active if ex > r})
    out = []
    for e in ent:
        present = [a for a in active if a[4] > e]
        for (oi, lx0, ly0, lx1, ly1) in mt["orients"]:
            if math.ceil(-lx0) > math.floor(W - lx1) or math.ceil(-ly0) > math.floor(H - ly1):
                continue
            xs = {max(0, math.ceil(-lx0))} | {math.ceil(a[2] - lx0) for a in present}
            ys = {max(0, math.ceil(-ly0))} | {math.ceil(a[3] - ly0) for a in present}
            xs = sorted(x for x in xs if -1e-9 <= x + lx0 and x + lx1 <= W + 1e-9)
            ys = sorted(y for y in ys if -1e-9 <= y + ly0 and y + ly1 <= H + 1e-9)
            for py in ys:
                Y0, Y1 = py + ly0, py + ly1
                for px in xs:
                    X0, X1 = px + lx0, px + lx1
                    if all(not (X0 < a[2] - 1e-9 and a[0] < X1 - 1e-9 and Y0 < a[3] - 1e-9 and a[1] < Y1 - 1e-9)
                           for a in present):
                        out.append((px, py, oi, e, X0, Y0, X1, Y1))
        if out:                      # earliest entry that admits any placement -> stop (tardiness-first)
            break
    return out


class State:
    __slots__ = ("active", "load", "place", "g")
    def __init__(self, active, load, place, g):
        self.active = active; self.load = load; self.place = place; self.g = g


def beam_solve(prob, B=30, max_children_per_bay=8):
    bays = prob["bays"]; nb = len(bays)
    w = prob.get("weights", {}); w1, w2, w3 = w.get("w1", 1), w.get("w2", 1), w.get("w3", 1)
    areas = [b["width"] * b["height"] for b in bays]; avg = sum(areas) / nb
    u = [avg / a for a in areas]
    n = len(prob["blocks"])
    metas = {i: block_meta(prob["blocks"][i], i) for i in range(n)}
    pref = {i: prob["blocks"][i]["bay_preferences"] for i in range(n)}
    smax = {i: max(pref[i]) for i in range(n)}
    wl = {i: prob["blocks"][i]["workload"] for i in range(n)}
    order = sorted(range(n), key=lambda i: (metas[i]["due"], metas[i]["proc"]))

    def g_of(load, place):
        z1 = sum(max(0, f - metas[i]["due"]) for i, (j, x, y, oi, e, f) in place.items())
        wlj = [u[j] * load[j] for j in range(nb)]
        z2 = math.floor(max(wlj) - min(wlj)) if nb >= 2 else 0
        z3 = sum(smax[i] - pref[i][j] for i, (j, x, y, oi, e, f) in place.items())
        return w1 * z1 + w2 * z2 + w3 * z3

    beam = [State([[] for _ in range(nb)], [0.0] * nb, {}, 0.0)]
    for i in order:
        mt = metas[i]
        cand = []   # (g, parent_idx, j, placement)
        for pidx, st in enumerate(beam):
            for j, bay in enumerate(bays):
                pos = _cand_positions(bay["width"], bay["height"], mt, st.active[j])
                pos = pos[:max_children_per_bay]
                for (x, y, oi, e, X0, Y0, X1, Y1) in pos:
                    f = e + mt["proc"]
                    # incremental objective: copy-free estimate via deltas
                    newload = list(st.load); newload[j] += wl[i]
                    newplace = dict(st.place); newplace[i] = (j, x, y, oi, e, f)
                    g = g_of(newload, newplace)
                    cand.append((g, pidx, j, (x, y, oi, e, f, X0, Y0, X1, Y1)))
        if not cand:
            # fallback: force-place into preferred fitting bay (empty-window), keep all beam states
            for st in beam:
                j = max(range(nb), key=lambda j: pref[i][j]); bay = bays[j]
                oi, lx0, ly0, lx1, ly1 = mt["orients"][0]
                e = max(mt["release"], max((ex for (*_, ex) in st.active[j]), default=0))
                x, yy = max(0, math.ceil(-lx0)), max(0, math.ceil(-ly0))
                f = e + mt["proc"]
                st.active[j].append((x+lx0, yy+ly0, x+lx1, yy+ly1, f)); st.load[j] += wl[i]
                st.place[i] = (j, x, yy, oi, e, f); st.g = g_of(st.load, st.place)
            continue
        cand.sort(key=lambda c: c[0])
        newbeam = []
        for (g, pidx, j, pl) in cand[:B]:
            st = beam[pidx]
            x, y, oi, e, f, X0, Y0, X1, Y1 = pl
            active = [list(a) for a in st.active]
            active[j] = st.active[j] + [(X0, Y0, X1, Y1, f)]
            load = list(st.load); load[j] += wl[i]
            place = dict(st.place); place[i] = (j, x, y, oi, e, f)
            newbeam.append(State(active, load, place, g))
        beam = newbeam
    best = min(beam, key=lambda s: s.g)
    place = best.place
    ops = {}
    for bid, (j, x, y, oi, e, f) in place.items():
        ops.setdefault(str(int(f)), []).append({"type": "EXIT", "block_id": bid, "bay_id": j})
    for bid, (j, x, y, oi, e, f) in place.items():
        ops.setdefault(str(int(e)), []).append({"type": "ENTRY", "block_id": bid, "bay_id": j,
                                                 "x": int(x), "y": int(y), "orient_idx": oi})
    for t in ops:
        ops[t].sort(key=lambda o: (0 if o["type"] == "EXIT" else 1, o["block_id"]))
    return {"operations": {t: ops[t] for t in sorted(ops, key=int)}}, best.g
