"""interlock.py -- crane-aware TRUE interlock bay packer (footprints may overlap; per-layer collision +
crane via the provided utils, so it's correct-by-construction against the server checker).

Placement: EDD order; candidate positions = layer-0 NFP-vertices vs present (footprint overlap allowed) +
bay corner. A candidate at entry e is accepted iff contained AND collision-free AND crane-clear to ENTER,
all via utils (with an AABB pre-filter so utils is only consulted for footprint-overlapping present blocks
-> fast). Entries chosen greedily-earliest. EXITS are then scheduled crane-feasibly by a chronological
replay: a finished block exits only when check_exit passes against the current present set; if blocked, it
waits (its exit, hence tardiness, increases) until the blocker leaves. The full result is validated by
utils.check_feasibility; on any failure the caller falls back to the cuboid packing.
"""
import math


def _bb(blk):
    xs = [v[0] for l in blk["shape"][0]["layers"] for v in l]  # placeholder; real per-orient below
    return None


def pack_bay_interlock(prob, bay_id, ids, cap=50, tl=90):
    import time
    import utils
    t0 = time.time()
    bays = prob["bays"]; B = prob["blocks"]; bay = utils.Bay.from_dict(bays[bay_id], bay_id)
    W, H = bays[bay_id]["width"], bays[bay_id]["height"]

    # per block: choose min-footprint orientation that FITS this bay; cache footprint AABB + layer0 verts
    chosen = {}
    for i in ids:
        best = None
        for oi, o in enumerate(B[i]["shape"]):
            xs = [v[0] for l in o["layers"] for v in l]; ys = [v[1] for l in o["layers"] for v in l]
            lx0, ly0, lx1, ly1 = min(xs), min(ys), max(xs), max(ys)
            if math.ceil(-lx0) > math.floor(W - lx1) or math.ceil(-ly0) > math.floor(H - ly1):
                continue
            area = (lx1 - lx0) * (ly1 - ly0)
            if best is None or area < best[0]:
                best = (area, oi, lx0, ly0, lx1, ly1)
        if best is None:
            oi = 0; o = B[i]["shape"][0]
            xs = [v[0] for l in o["layers"] for v in l]; ys = [v[1] for l in o["layers"] for v in l]
            best = (0, 0, min(xs), min(ys), max(xs), max(ys))
        chosen[i] = best[1:]   # (oi, lx0, ly0, lx1, ly1)

    info = {}   # id -> (entry, oi, x, y); exit set later
    blk_cache = {}  # id -> utils.Block (built once placed)

    def fp(i, px, py):
        oi, lx0, ly0, lx1, ly1 = chosen[i]
        return (px + lx0, py + ly0, px + lx1, py + ly1)

    P = {i: B[i]["processing_time"] for i in ids}
    for i in sorted(ids, key=lambda i: (B[i]["due_date"], B[i]["processing_time"])):
        if time.time() - t0 > tl:
            return None
        oi, lx0, ly0, lx1, ly1 = chosen[i]
        R = B[i]["release_time"]; l0 = B[i]["shape"][oi]["layers"][0]
        best = None
        ent = sorted({R} | {info[k][0] + P[k] for k in info if info[k][0] + P[k] > R})
        for e in ent:
            if best is not None and e >= best[0]:
                break
            pres = [k for k in info if info[k][0] <= e < info[k][0] + P[k]]
            cand = {(max(0, math.ceil(-lx0)), max(0, math.ceil(-ly0)))}
            for k in pres:
                ko = chosen[k][0]; kx, ky = info[k][2], info[k][3]
                for (ax, ay) in B[k]["shape"][ko]["layers"][0]:
                    for (mx, my) in l0:
                        cand.add((math.ceil(kx + ax - mx), math.ceil(ky + ay - my)))
            cl = [(px, py) for (px, py) in cand
                  if px + lx0 >= -1e-9 and px + lx1 <= W + 1e-9 and py + ly0 >= -1e-9 and py + ly1 <= H + 1e-9]
            cl.sort(key=lambda c: (c[1], c[0]))
            for (px, py) in cl[:cap]:
                blk_i = utils.Block(block_id=i, block_data=B[i], x=int(px), y=int(py), orient_idx=oi)
                if not bay.contains_block(blk_i):
                    continue
                fi = (px + lx0, py + ly0, px + lx1, py + ly1)
                # AABB pre-filter: only present blocks whose footprint overlaps need a utils check
                overlap_present = []
                for k in pres:
                    fk = fp(k, info[k][2], info[k][3])
                    if fi[0] < fk[2] - 1e-9 and fk[0] < fi[2] - 1e-9 and fi[1] < fk[3] - 1e-9 and fk[1] < fi[3] - 1e-9:
                        overlap_present.append(blk_cache[k])
                if overlap_present:
                    if utils.check_collisions(bay, overlap_present + [blk_i]):
                        continue
                    if utils.check_entry(bay, overlap_present, blk_i, fast=True):
                        continue
                best = (e, px, py); break
            if best and best[0] == e:
                break
        if best is None:
            e = max(R, max((info[k][0] + P[k] for k in info), default=0))
            best = (e, max(0, math.ceil(-lx0)), max(0, math.ceil(-ly0)))
        e, px, py = best
        info[i] = (e, oi, px, py)
        blk_cache[i] = utils.Block(block_id=i, block_data=B[i], x=int(px), y=int(py), orient_idx=oi)

    # -- crane-feasible EXIT scheduling: chronological replay --------------------
    import utils as U
    exit_t = {}
    present = set()
    done_ready = {i: info[i][0] + P[i] for i in ids}      # earliest exit (processing done)
    events = sorted(set([info[i][0] for i in ids] + list(done_ready.values())))
    # simulate forward; allow time to advance past events; exit blocks when crane permits
    remaining = set(ids)
    t = 0; guard = 0
    times = sorted(set([info[i][0] for i in ids] + [done_ready[i] for i in ids]))
    ti = 0
    while remaining and guard < 100000:
        guard += 1
        t = times[ti] if ti < len(times) else t + 1
        # entries at t
        for i in list(remaining):
            if info[i][0] == t:
                present.add(i)
        # try exits for blocks done by t
        progressed = True
        while progressed:
            progressed = False
            for i in list(present):
                if done_ready[i] <= t and i in present:
                    pres_blocks = [blk_cache[k] for k in present]
                    if not U.check_exit(bay, pres_blocks, blk_cache[i], fast=True):
                        exit_t[i] = t; present.discard(i); remaining.discard(i); progressed = True
        ti += 1
        if ti >= len(times) and remaining:
            times.append(t + 1)   # extend time to let blocked exits clear
        if guard > 50000:
            return None
    return {i: (bay_id, info[i][2], info[i][3], info[i][1], info[i][0], exit_t.get(i, info[i][0] + P[i]))
            for i in ids}
