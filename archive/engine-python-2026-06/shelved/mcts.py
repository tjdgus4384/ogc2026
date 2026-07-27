"""mcts.py -- MCTS over block INSERTION ORDER, with a fast cuboid decoder (prototype / A-B test).

Tests whether MCTS search over the construction order beats the SA improver over the SAME space.
NOTE: the decoder is the fast footprint-disjoint (cuboid) packer, so this does NOT remove the cuboid
approximation — it tests the SEARCH, not the geometry. A full-fidelity (polygon+crane) decoder would
optimize the true problem but is too slow for MCTS rollouts.

Tree: node = ordered prefix of placed blocks. Action = pick the next block (restricted to the EXPAND_K
earliest-due unplaced, for tractable branching). Decode(order): place blocks in that order, each into the
bay minimizing incremental (w1*tardiness + w3*pref-penalty) at the earliest footprint-disjoint entry.
Rollout = complete the order by EDD, decode, value = -objective. UCT selection.
"""
import time, math, random
from packer import block_meta, pack_bay

EXPAND_K = 6
C_UCT = 1.4


def _decode(prob, metas, order):
    """Order-driven greedy: assign+place each block (cuboid, footprint-disjoint), return objective."""
    bays = prob["bays"]; nb = len(bays)
    areas = [b["width"] * b["height"] for b in bays]; avg = sum(areas) / nb
    u = [avg / a for a in areas]
    w = prob.get("weights", {}); w1, w2, w3 = w.get("w1", 1), w.get("w2", 1), w.get("w3", 1)
    active = {j: [] for j in range(nb)}     # (X0,Y0,X1,Y1,exit)
    load = [0.0] * nb; place = {}
    import math as _m
    for i in order:
        mt = metas[i]; r, p, due = mt["release"], mt["proc"], mt["due"]
        prefs = prob["blocks"][i]["bay_preferences"]; smax = max(prefs)
        best = None  # (incr_cost, j, x, y, oi, e)
        for j, bay in enumerate(bays):
            W, H = bay["width"], bay["height"]
            pj = _place_one(W, H, mt, active[j])     # earliest footprint-disjoint fit vs active[j]
            if pj is None:
                continue
            x, y, oi, e, X0, Y0, X1, Y1 = pj
            tard = max(0, e + p - due)
            incr = w1 * tard + w3 * (smax - prefs[j])
            if best is None or incr < best[0]:
                best = (incr, j, x, y, oi, e, X0, Y0, X1, Y1)
        if best is None:
            j = max(range(nb), key=lambda j: prefs[j]); bay = bays[j]
            pj = _place_one(bay["width"], bay["height"], mt, active[j], force=True)
            x, y, oi, e, X0, Y0, X1, Y1 = pj; best = (0, j, x, y, oi, e, X0, Y0, X1, Y1)
        _, j, x, y, oi, e, X0, Y0, X1, Y1 = best
        active[j].append((X0, Y0, X1, Y1, e + p)); load[j] += mt["proc"]
        place[i] = (j, x, y, oi, e, e + p)
    # objective
    z1 = sum(max(0, f - metas[i]["due"]) for i, (j, x, y, oi, e, f) in place.items())
    bl = [0.0] * nb
    for i, (j, *_), in place.items():
        bl[j] += prob["blocks"][i]["workload"]
    wl = [u[j] * bl[j] for j in range(nb)]
    z2 = _m.floor(max(wl) - min(wl)) if nb >= 2 else 0
    z3 = sum(max(prob["blocks"][i]["bay_preferences"]) - prob["blocks"][i]["bay_preferences"][j]
             for i, (j, *_), in place.items())
    return w1 * z1 + w2 * z2 + w3 * z3, place


def _place_one(W, H, mt, active, force=False):
    """Earliest footprint-disjoint placement of one block against `active`. Returns (x,y,oi,e,X0,Y0,X1,Y1)."""
    r, p = mt["release"], mt["proc"]
    ent = sorted({r} | {ex for (*_, ex) in active if ex > r})
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
                        return px, py, oi, e, X0, Y0, X1, Y1
    if force or True:
        e = max(r, max((ex for (*_, ex) in active), default=0))
        for (oi, lx0, ly0, lx1, ly1) in mt["orients"]:
            if math.ceil(-lx0) <= math.floor(W - lx1) and math.ceil(-ly0) <= math.floor(H - ly1):
                px, py = max(0, math.ceil(-lx0)), max(0, math.ceil(-ly0))
                return px, py, oi, e, px + lx0, py + ly0, px + lx1, py + ly1
    return None


class Node:
    __slots__ = ("order", "placed", "kids", "n", "q", "untried")
    def __init__(self, order, placed, untried):
        self.order = order; self.placed = placed; self.kids = {}
        self.n = 0; self.q = 0.0; self.untried = untried


def mcts(prob, timelimit, seed=0, verbose=False):
    t0 = time.time(); rng = random.Random(seed)
    n = len(prob["blocks"])
    metas = {i: block_meta(prob["blocks"][i], i) for i in range(n)}
    edd = sorted(range(n), key=lambda i: (metas[i]["due"], metas[i]["proc"]))
    edd_rank = {i: k for k, i in enumerate(edd)}

    def actions(placed):
        rem = [i for i in edd if i not in placed]
        return rem[:EXPAND_K]

    root = Node((), set(), actions(set()))
    best_obj = math.inf; best_place = None

    def rollout(order, placed):
        rem = [i for i in edd if i not in placed]
        full = list(order) + rem
        obj, place = _decode(prob, metas, full)
        return obj, place

    iters = 0
    while time.time() - t0 < timelimit:
        iters += 1
        node = root; path = [root]
        # selection
        while not node.untried and node.kids:
            node = max(node.kids.values(),
                       key=lambda c: (-c.q / max(c.n, 1)) + C_UCT * math.sqrt(math.log(node.n + 1) / max(c.n, 1)))
            path.append(node)
        # expansion
        if node.untried:
            a = node.untried.pop(rng.randrange(len(node.untried)))
            placed2 = set(node.placed); placed2.add(a)
            order2 = node.order + (a,)
            child = Node(order2, placed2, actions(placed2))
            node.kids[a] = child; path.append(child); node = child
        # rollout
        obj, place = rollout(node.order, node.placed)
        if obj < best_obj:
            best_obj = obj; best_place = place
        # backprop
        for nd in path:
            nd.n += 1; nd.q += obj
    if verbose:
        print(f"  mcts iters={iters} best={best_obj:.0f}")
    # build solution
    ops = {}
    for bid, (j, x, y, oi, e, f) in best_place.items():
        ops.setdefault(str(int(f)), []).append({"type": "EXIT", "block_id": bid, "bay_id": j})
    for bid, (j, x, y, oi, e, f) in best_place.items():
        ops.setdefault(str(int(e)), []).append({"type": "ENTRY", "block_id": bid, "bay_id": j,
                                                 "x": int(x), "y": int(y), "orient_idx": oi})
    for t in ops:
        ops[t].sort(key=lambda o: (0 if o["type"] == "EXIT" else 1, o["block_id"]))
    return {"operations": {t: ops[t] for t in sorted(ops, key=int)}}
