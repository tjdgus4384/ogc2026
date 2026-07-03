"""layer_stack.py -- safe LIFO interlock prototype.

This is a correctness-first prototype for the possible next large step beyond
union-footprint packing. It allows one-way crane interlocks by using a stack
discipline inside each bay:

  * entries follow the chosen order,
  * a new block may enter if utils.check_entry says it is clear of all active
    residents,
  * exits are emitted in reverse entry order for each stack batch.

If a later block obstructs an earlier resident, the earlier resident simply
stays until that later block exits. This is the smallest safe way to exploit
layer structure without writing an exact temporal model yet.

The code is intentionally slow and conservative. It is for experiments and for
deciding whether a C per-layer kernel is worth building.
"""

from __future__ import annotations

import math
import time


def _block_from(prob, bid, x, y, oi):
    import utils
    return utils.Block(block_id=bid, block_data=prob["blocks"][bid], x=int(x), y=int(y), orient_idx=int(oi))


def _candidate_points(bay, active):
    xs = {0}
    ys = {0}
    for b in active:
        x0, y0, x1, y1 = b.bounding_rect()
        xs.add(math.ceil(x1))
        ys.add(math.ceil(y1))
    return sorted(xs), sorted(ys)


def _try_place(prob, bay_id, bid, active, deadline=None):
    import utils
    bay = utils.Bay.from_dict(prob["bays"][bay_id], bay_id)
    blk_data = prob["blocks"][bid]
    xs, ys = _candidate_points(bay, active)
    best = None
    for oi in range(len(blk_data["shape"])):
        probe0 = _block_from(prob, bid, 0, 0, oi)
        bx0, by0, bx1, by1 = probe0.bounding_rect()
        base_x = max(0, math.ceil(-bx0))
        base_y = max(0, math.ceil(-by0))
        cand_x = sorted({base_x} | {max(base_x, x + base_x) for x in xs})
        cand_y = sorted({base_y} | {max(base_y, y + base_y) for y in ys})
        for y in cand_y:
            if deadline and time.time() > deadline:
                return best
            for x in cand_x:
                nb = _block_from(prob, bid, x, y, oi)
                bb = nb.bounding_rect()
                if bb[2] > bay.width + 1e-9 or bb[3] > bay.height + 1e-9:
                    continue
                if utils.check_entry(bay, active, nb, fast=True):
                    continue
                key = (bb[3], y, x, oi)
                if best is None or key < best[0]:
                    best = (key, x, y, oi, nb)
            # bottom-left enough for this prototype
            if best is not None and best[0][1] == y:
                break
    return best


def _close_batch(batch, proc, due, ops, placements):
    """Assign reverse exits for one LIFO batch and append operations."""
    next_exit = None
    for rec in reversed(batch):
        bid, bay, x, y, oi, e = rec
        f = e + proc[bid] if next_exit is None else max(e + proc[bid], next_exit)
        next_exit = f
        placements[bid] = (bay, x, y, oi, e, f)
        ops.setdefault(str(int(f)), []).append({"type": "EXIT", "block_id": bid, "bay_id": bay})


def _pack_bay_stack(prob, bay_id, order, deadline=None):
    proc = {i: max(1, int(prob["blocks"][i].get("processing_time", 1))) for i in order}
    release = {i: int(prob["blocks"][i].get("release_time", 0)) for i in order}
    due = {i: int(prob["blocks"][i].get("due_date", 0)) for i in order}
    active = []
    batch = []
    placements = {}
    ops = {}
    cur_e = 0

    def close():
        nonlocal active, batch, cur_e
        if not batch:
            return
        _close_batch(batch, proc, due, ops, placements)
        cur_e = max(placements[b][5] for b, *_ in batch)
        active = []
        batch = []

    for bid in order:
        if deadline and time.time() > deadline:
            close()
            return placements, ops
        pl = _try_place(prob, bay_id, bid, active, deadline=deadline)
        if pl is None:
            close()
            pl = _try_place(prob, bay_id, bid, active, deadline=deadline)
        if pl is None:
            # Last-resort singleton at origin-ish; caller validation will reject
            # if this ever happens. Valid contest instances should fit singly.
            x = y = oi = 0
            nb = _block_from(prob, bid, x, y, oi)
        else:
            _, x, y, oi, nb = pl
        e = max(release[bid], cur_e)
        cur_e = e
        batch.append((bid, bay_id, int(x), int(y), int(oi), int(e)))
        active.append(nb)
        ops.setdefault(str(int(e)), []).append({
            "type": "ENTRY", "block_id": bid, "bay_id": bay_id,
            "x": int(x), "y": int(y), "orient_idx": int(oi),
        })
    close()
    return placements, ops


def polish_solution(prob, sol, utils, time_budget=8.0):
    """Try stack repacking each tardy bay; keep only if the whole solution improves."""
    t0 = time.time()
    base = utils.check_feasibility(prob, sol)
    if not base.get("feasible"):
        return sol

    entries = {}
    exits = {}
    by_bay = {j: [] for j in range(len(prob["bays"]))}
    for tt, ops in sol.get("operations", {}).items():
        for op in ops:
            if op.get("type") == "ENTRY":
                bid = op["block_id"]
                entries[bid] = (op["bay_id"], op["x"], op["y"], op["orient_idx"], int(tt))
                by_bay[op["bay_id"]].append((int(tt), bid))
            elif op.get("type") == "EXIT":
                exits[op["block_id"]] = int(tt)

    placements = {
        bid: (bay, x, y, oi, e, exits[bid])
        for bid, (bay, x, y, oi, e) in entries.items()
        if bid in exits
    }
    due = {i: int(prob["blocks"][i]["due_date"]) for i in range(len(prob["blocks"]))}

    def bay_tard(j):
        return sum(max(0, placements[i][5] - due[i]) for _, i in by_bay[j])

    changed = False
    for j in sorted(by_bay, key=bay_tard, reverse=True):
        if time.time() - t0 > time_budget:
            break
        if bay_tard(j) <= 0 or not by_bay[j]:
            continue
        order = [i for _, i in sorted(by_bay[j])]
        rem = time_budget - (time.time() - t0)
        newp, _ = _pack_bay_stack(prob, j, order, deadline=time.time() + max(0.5, rem))
        if len(newp) != len(order):
            continue
        old_td = bay_tard(j)
        new_td = sum(max(0, newp[i][5] - due[i]) for i in newp)
        if new_td < old_td:
            for i in order:
                placements[i] = newp[i]
            changed = True

    if not changed:
        return sol

    # Custom operation builder: preserve reverse EXIT order for equal-time stack exits.
    ops = {}
    for bid, (j, x, y, oi, e, f) in placements.items():
        ops.setdefault(str(int(e)), []).append({
            "type": "ENTRY", "block_id": bid, "bay_id": j,
            "x": int(x), "y": int(y), "orient_idx": int(oi),
        })
        ops.setdefault(str(int(f)), []).append({"type": "EXIT", "block_id": bid, "bay_id": j})
    for t, arr in ops.items():
        exits_t = [o for o in arr if o["type"] == "EXIT"]
        entries_t = [o for o in arr if o["type"] == "ENTRY"]
        # For equal exits, deeper stack blocks tend to have earlier entry; exiting
        # later-entered blocks first is the LIFO-safe order.
        exits_t.sort(key=lambda o: entries[o["block_id"]][4] if o["block_id"] in entries else 0, reverse=True)
        entries_t.sort(key=lambda o: entries[o["block_id"]][4] if o["block_id"] in entries else 0)
        ops[t] = exits_t + entries_t
    cand = {"operations": {t: ops[t] for t in sorted(ops, key=int)}}
    r = utils.check_feasibility(prob, cand)
    if r.get("feasible") and r["objective"] < base["objective"] - 1e-9:
        return cand
    return sol
