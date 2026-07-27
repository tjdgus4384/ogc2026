"""asls.py -- Assignment Local Search via INSERTION (Z1-preserving Z2/Z3 descent).

The unified assignment lever for Z1=0 solutions. Earlier attempt RE-PACKED the destination bay with the
greedy oracle -> destroyed the (CP-SAT) Z1=0 packing -> moves rejected. Fix: do NOT re-pack. INSERT the
moved block into the destination bay's EXISTING packing (single placement query, other blocks fixed); accept
only if it lands with NO new tardiness (entry+proc <= due) -> Z1 stays exactly 0. A move that can't insert
cleanly is rejected (conservative: never raises Z1). Source bay only loses a block (always still feasible).

So every accepted move keeps Z1=0 and strictly lowers w2*Z2 + w3*Z3 (true weighted delta, BOTH balance and
preference). Deterministic (worst-objective-share first) -> no added variance. Fast (one insert per probe) ->
works inside a 1-minute budget where per-move CP-SAT (cpsat_z3) cannot converge.

Only meaningful when the input is Z1=0; returns input unchanged otherwise (those need a packing lever).
"""
import time, math
from packer import block_meta, _best_pos
from solve import build_ops


def asls(prob, sol, utils, time_budget=20.0, verbose=False):
    t0 = time.time()
    B = prob["blocks"]; bays = prob["bays"]; nb = len(bays)
    w = prob.get("weights", {}); w2, w3 = w.get("w2", 1), w.get("w3", 1)
    base = utils.check_feasibility(prob, sol)
    if not base["feasible"]:
        return sol
    # Works from ANY Z1: moves are Z1-PRESERVING (a relocate/swap is taken only when the block inserts with
    # NO new tardiness), so existing tardiness is untouched while Z2/Z3 drop. Was gated to Z1=0; relaxing it
    # lets this Z3/Z2 descent also polish large brkga2 results sitting at a small residual Z1 (e.g. prob_20).
    MB = {i: block_meta(B[i], i) for i in range(len(B))}
    for i in MB:
        MB[i]["proc"] = max(1, MB[i]["proc"])
    due = {i: B[i]["due_date"] for i in range(len(B))}
    rel = {i: MB[i]["release"] for i in range(len(B))}
    proc = {i: MB[i]["proc"] for i in range(len(B))}
    wl = {i: B[i]["workload"] for i in range(len(B))}
    smax = {i: max(B[i]["bay_preferences"]) for i in range(len(B))}
    pref = {i: B[i]["bay_preferences"] for i in range(len(B))}
    ORS = {i: [(o[0], o[1], o[2], o[3], o[4]) for o in MB[i]["orients"]] for i in range(len(B))}  # (oi,box)
    OFF = {i: {o[0]: (o[1], o[2], o[3], o[4]) for o in MB[i]["orients"]} for i in range(len(B))}   # oi->box
    areas = [bays[j]["width"] * bays[j]["height"] for j in range(nb)]; avg = sum(areas) / nb
    U = [avg / a for a in areas]

    def fits(i, j):
        W, H = bays[j]["width"], bays[j]["height"]
        return any(math.ceil(-lx0) <= math.floor(W - lx1) and math.ceil(-ly0) <= math.floor(H - ly1)
                   for (lx0, ly0, lx1, ly1) in OFF[i].values())
    fitb = {i: [j for j in range(nb) if fits(i, j)] for i in range(len(B))}

    # current placement per bay: bid -> (x,y,oi,entry,exit); byb: bay -> [bids]
    place = {}; byb = {j: [] for j in range(nb)}; asg = {}
    for tt, ops in sol["operations"].items():
        for o in ops:
            if o["type"] == "ENTRY":
                b = o["block_id"]; asg[b] = o["bay_id"]; byb[o["bay_id"]].append(b)
                place[b] = [o["x"], o["y"], o["orient_idx"], int(tt), None]
    for tt, ops in sol["operations"].items():
        for o in ops:
            if o["type"] == "EXIT":
                place[o["block_id"]][4] = int(tt)

    def active_of(j, exclude=None):
        out = []
        for b in byb[j]:
            if b == exclude:
                continue
            x, y, oi, e, f = place[b]; a = OFF[b][oi]
            out.append((x + a[0], y + a[1], x + a[2], y + a[3], e, f))
        return out

    def insert(j, b, exclude=None):
        """earliest no-tardiness placement of b into bay j's existing packing (minus `exclude`), or None."""
        W, H = int(bays[j]["width"]), int(bays[j]["height"]); p = proc[b]; r = rel[b]
        act = active_of(j, exclude)
        ent = sorted({r} | {a[5] for a in act if a[5] > r})
        for e in ent:
            if e + p > due[b]:
                break                                     # would be tardy -> reject (keep Z1=0)
            present = [(a[0], a[1], a[2], a[3], a[5]) for a in act if a[5] > e and a[4] < e + p]
            for (oi, lx0, ly0, lx1, ly1) in ORS[b]:
                if math.ceil(-lx0) > math.floor(W - lx1) or math.ceil(-ly0) > math.floor(H - ly1):
                    continue
                pos = _best_pos(W, H, lx0, ly0, lx1, ly1, present)
                if pos is not None:
                    return (pos[0], pos[1], oi, e, e + p)
        return None

    loads = [sum(wl[i] for i in byb[j]) for j in range(nb)]

    def z2_of(ld):
        if nb < 2:
            return 0.0
        v = [U[j] * ld[j] for j in range(nb)]
        return math.floor(max(v) - min(v))
    Z2 = z2_of(loads)
    acc = 0
    improved = True
    while improved and time.time() - t0 < time_budget:
        improved = False
        order = sorted(asg, key=lambda i: -(w3 * (smax[i] - pref[i][asg[i]])))   # worst weighted share first
        for i in order:
            if time.time() - t0 > time_budget:
                break
            j1 = asg[i]
            cands = sorted([j for j in fitb[i] if j != j1], key=lambda j: (-pref[i][j], loads[j]))[:3]
            moved = False
            # RELOCATE: insert i into j2 (Z1-preserving); handles Z2 relief + Z3 when j2 has room
            for j2 in cands:
                nl = list(loads); nl[j1] -= wl[i]; nl[j2] += wl[i]
                dZ3 = pref[i][j1] - pref[i][j2]                # = delta Z3 penalty (<0 when j2 preferred)
                if w2 * (z2_of(nl) - Z2) + w3 * dZ3 >= -1e-9:
                    continue                                   # not improving even if it fits
                ins = insert(j2, i)
                if ins is None:
                    continue
                byb[j1].remove(i); byb[j2].append(i); asg[i] = j2
                place[i] = list(ins); loads = nl; Z2 = z2_of(nl)
                acc += 1; improved = moved = True; break
            if moved:
                continue
            # SWAP: i wants more-preferred j2 but it's full -> kick out a k in j2 that prefers j1.
            for j2 in cands:
                if pref[i][j2] <= pref[i][j1]:
                    continue                                   # swap only to gain Z3 for i
                for k in sorted(byb[j2], key=lambda b: pref[b][j1] - pref[b][j2], reverse=True)[:4]:
                    if j1 not in fitb[k]:
                        continue
                    nl = list(loads); nl[j1] += wl[k] - wl[i]; nl[j2] += wl[i] - wl[k]
                    dZ3 = (pref[i][j1] - pref[i][j2]) + (pref[k][j2] - pref[k][j1])
                    if w2 * (z2_of(nl) - Z2) + w3 * dZ3 >= -1e-9:
                        continue
                    ins_i = insert(j2, i, exclude=k)
                    if ins_i is None:
                        continue
                    ins_k = insert(j1, k, exclude=i)
                    if ins_k is None:
                        continue
                    byb[j1].remove(i); byb[j2].remove(k); byb[j1].append(k); byb[j2].append(i)
                    asg[i] = j2; asg[k] = j1
                    place[i] = list(ins_i); place[k] = list(ins_k); loads = nl; Z2 = z2_of(nl)
                    acc += 1; improved = moved = True; break
                if moved:
                    break
    if acc == 0:
        return sol
    pl = {b: (asg[b], place[b][0], place[b][1], place[b][2], place[b][3], place[b][4]) for b in place}
    ts = build_ops(pl); r = utils.check_feasibility(prob, ts)
    if verbose:
        rr = r['objective'] if r['feasible'] else 'INF'
        print(f"  asls -> obj={rr} acc={acc} base={base['objective']:.0f}", flush=True)
    return ts if (r["feasible"] and r["objective"] < base["objective"] - 1e-9) else sol
