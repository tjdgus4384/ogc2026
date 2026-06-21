"""packer.py -- per-bay footprint-disjoint packer over time (AABB only, no Shapely).

Keystone fact (MODEL D4): if blocks' footprint AABBs are pairwise disjoint whenever they coexist in
time, then NO layer pair can overlap, so BOTH the collision (C4) and crane (C5) constraints are
automatically satisfied for any operation order. So this packer needs only fast AABB tests and is
structurally always feasible.

pack_bay places blocks in a given priority order; for each block it jointly searches
(orientation, entry-time, position) and commits the EARLIEST feasible entry (entry early => exit early
=> less tardiness), breaking ties toward a compact (low, left) placement so later blocks fit.

Block meta (precomputed once per instance):
  {"id": int, "release": int, "proc": int, "due": int,
   "orients": [(orient_idx, lx0, ly0, lx1, ly1), ...]}   # footprint AABB per orientation, local coords
"""
import math

PLACEMENT = "bl"   # "bl" = bottom-left first-fit (DEFAULT, fast — in-loop packer's job is speed for
                   # assignment search; CP-SAT polish handles packing density). "contact" = max-contact
                   # extreme-point: A/B-tested, net slightly worse (helps congested, hurts assignment-bound,
                   # slower) because CP-polish already captures density. Kept behind the flag for ablation.


def precompute_orients(blk):
    """Return [(orient_idx, lx0, ly0, lx1, ly1), ...] footprint AABBs (all layers) in local coords.
    Deduped: orientations with the same footprint AABB are interchangeable for footprint-disjoint
    packing, so keep one representative per unique (rounded) AABB. Sorted by area ascending (smaller
    footprints fit more places first). Cuts the per-block orientation loop ~8 -> ~2-4."""
    seen = {}
    for oi, o in enumerate(blk["shape"]):
        xs = [v[0] for l in o["layers"] for v in l]
        ys = [v[1] for l in o["layers"] for v in l]
        lx0, ly0, lx1, ly1 = min(xs), min(ys), max(xs), max(ys)
        key = (round(lx0, 3), round(ly0, 3), round(lx1, 3), round(ly1, 3))
        if key not in seen:
            seen[key] = (oi, lx0, ly0, lx1, ly1)
    out = list(seen.values())
    out.sort(key=lambda t: (t[3] - t[1]) * (t[4] - t[2]))
    return out


def block_meta(blk, bid):
    return {"id": bid, "release": blk["release_time"], "proc": blk["processing_time"],
            "due": blk["due_date"], "orients": precompute_orients(blk)}


def _contact(X0, Y0, X1, Y1, W, H, present):
    """Total boundary length the footprint shares with bay walls + present items (snugness merit).
    Higher = tighter nestle = cleaner remaining free space for later blocks."""
    c = 0.0
    if X0 <= 1e-9:
        c += Y1 - Y0
    if X1 >= W - 1e-9:
        c += Y1 - Y0
    if Y0 <= 1e-9:
        c += X1 - X0
    if Y1 >= H - 1e-9:
        c += X1 - X0
    for (a0, b0, a1, b1, _) in present:
        if abs(X1 - a0) <= 1e-9 or abs(X0 - a1) <= 1e-9:
            oy = min(Y1, b1) - max(Y0, b0)
            if oy > 0:
                c += oy
        if abs(Y1 - b0) <= 1e-9 or abs(Y0 - b1) <= 1e-9:
            ox = min(X1, a1) - max(X0, a0)
            if ox > 0:
                c += ox
    return c


def _best_pos(W, H, lx0, ly0, lx1, ly1, present):
    """Extreme-point / best-fit placement: among all feasible corner candidates (bay corner + right/top
    edges of present items = the candidate set for no-gravity packing), pick the one MAXIMIZING contact
    (snuggest nestle), tie-break lowest-then-leftmost. Placement rule is the dominant lever for packing
    quality (Crainic-Perboli-Tadei). Returns (px,py,X0,Y0,X1,Y1) or None."""
    base_x = max(0, math.ceil(-lx0)); base_y = max(0, math.ceil(-ly0))
    xs = {base_x}; ys = {base_y}
    for (a0, b0, a1, b1, _) in present:
        xs.add(math.ceil(a1 - lx0)); ys.add(math.ceil(b1 - ly0))
    xs = sorted(x for x in xs if -1e-9 <= x + lx0 and x + lx1 <= W + 1e-9)
    ys = sorted(y for y in ys if -1e-9 <= y + ly0 and y + ly1 <= H + 1e-9)
    best = None  # (merit, px, py, X0,Y0,X1,Y1)
    for py in ys:
        Y0 = py + ly0; Y1 = py + ly1
        for px in xs:
            X0 = px + lx0; X1 = px + lx1
            ok = True
            for (a0, b0, a1, b1, _) in present:
                if X0 < a1 - 1e-9 and a0 < X1 - 1e-9 and Y0 < b1 - 1e-9 and b0 < Y1 - 1e-9:
                    ok = False; break
            if not ok:
                continue
            if PLACEMENT == "bl":
                return px, py, X0, Y0, X1, Y1          # first feasible in (y,x) order
            merit = (-_contact(X0, Y0, X1, Y1, W, H, present), py, px)
            if best is None or merit < best[0]:
                best = (merit, px, py, X0, Y0, X1, Y1)
    if best is None:
        return None
    return best[1], best[2], best[3], best[4], best[5], best[6]


def pack_bay(W, H, metas, max_ent=64):
    """Place metas (already in priority order) into bay WxH, footprint-disjoint over time.
    Returns list of (id, x, y, orient_idx, entry, exit). Always succeeds (worst case: serialize).
    max_ent caps how many distinct entry-times are probed per block (the dominant cost on congested
    bays); if none of the first max_ent fit, the block enters when the bay fully empties (local
    serialize) -- a rare, cheap fallback that keeps the packer fast without losing feasibility."""
    active = []   # (X0,Y0,X1,Y1,entry,exit) currently-present world AABBs
    out = []
    for mt in metas:
        r, p = mt["release"], mt["proc"]
        # candidate entry times: release, and every active exit >= release (space changes only at exits)
        ent_cands = sorted({r} | {a[5] for a in active if a[5] > r})[:max_ent]
        best = None  # (entry, topy, px, py, oi, X0,Y0,X1,Y1)
        for e in ent_cands:
            # forbid only residents whose [entry,exit) actually OVERLAPS b's window [e, e+p): a resident
            # that enters after b exits (entry >= e+p) does NOT conflict. Checking only exit>e (the old
            # behaviour) over-constrains and pushes blocks later than necessary -> avoidable tardiness.
            present = [(a[0], a[1], a[2], a[3], a[5]) for a in active if a[5] > e and a[4] < e + p]
            for (oi, lx0, ly0, lx1, ly1) in mt["orients"]:
                if math.ceil(-lx0) > math.floor(W - lx1) or math.ceil(-ly0) > math.floor(H - ly1):
                    continue  # orientation cannot fit the bay at all
                pos = _best_pos(W, H, lx0, ly0, lx1, ly1, present)
                if pos is None:
                    continue
                px, py, X0, Y0, X1, Y1 = pos
                cand = (e, Y1, px, py, oi, X0, Y0, X1, Y1)
                if best is None or (cand[0], cand[1]) < (best[0], best[1]):
                    best = cand
            if best is not None and best[0] == e:
                break  # earliest entry achieved at this e; no later e can beat it
        if best is None:
            # fallback: enter when the bay fully empties (bay empty => containment is the only check),
            # using the first orientation that FITS this bay -> always feasible.
            e = max(r, max((a[5] for a in active), default=0))
            chosen = None
            for (oi, lx0, ly0, lx1, ly1) in mt["orients"]:
                if math.ceil(-lx0) <= math.floor(W - lx1) and math.ceil(-ly0) <= math.floor(H - ly1):
                    chosen = (oi, lx0, ly0, lx1, ly1); break
            if chosen is None:
                chosen = mt["orients"][0]   # degenerate: block fits no orientation in this bay
            oi, lx0, ly0, lx1, ly1 = chosen
            px, py = max(0, math.ceil(-lx0)), max(0, math.ceil(-ly0))
            X0, Y0, X1, Y1 = px + lx0, py + ly0, px + lx1, py + ly1
            best = (e, Y1, px, py, oi, X0, Y0, X1, Y1)
        e, _, px, py, oi, X0, Y0, X1, Y1 = best
        f = e + p
        active.append((X0, Y0, X1, Y1, e, f))
        out.append((mt["id"], px, py, oi, e, f))
    return out
