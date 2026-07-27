"""cpsat_z3.py -- CP-SAT-oracle Z3 descent (the integrated assignment<->per-bay-Z1 lever, AIs' "near-SOTA each").

Diagnosed 2026-06-21: best solutions sit at a MIDDLE point -- moderate Z3 (bay preference) AND Z1=0-packable.
The min-Z3 assignment (ilp) is NOT Z1=0-packable even under CP-SAT (real trade-off, not oracle weakness);
heuristics find Z1=0 but with too-high Z3. So from a Z1=0 solution, push Z3 DOWN (move blocks toward their
preferred bays) while an EXACT per-bay packer (CP-SAT) certifies Z1 stays 0. The accurate oracle is the key:
the earlier greedy/beam Z3-descent failed because the proxy mis-judged packability.

Per move (relocate / swap) re-packs the <=2 touched bays with pack_bay_cpsat (cached by block-set); accept
iff both stay Z1=0 (or do not increase tardiness) and Z3 strictly drops. Long budget + small bays -> many
moves. Best-of + utils validation by the caller. Returns improved sol.
"""
import time, math
from packer import block_meta
from cpsat_packer import pack_bay_cpsat


def cpsat_z3(prob, sol, utils, time_budget=120.0, per_bay_tl=15.0, verbose=False):
    from solve import build_ops
    t0 = time.time()
    B = prob["blocks"]; bays = prob["bays"]; nb = len(bays)
    base = utils.check_feasibility(prob, sol)
    if not base["feasible"]:
        return sol
    MB = {i: block_meta(B[i], i) for i in range(len(B))}
    for i in MB:
        MB[i]["proc"] = max(1, MB[i]["proc"])
    due = {i: B[i]["due_date"] for i in range(len(B))}
    smax = {i: max(B[i]["bay_preferences"]) for i in range(len(B))}
    pref = {i: B[i]["bay_preferences"] for i in range(len(B))}
    ORS = {i: [(o[1], o[2], o[3], o[4]) for o in MB[i]["orients"]] for i in range(len(B))}

    def fits(i, j):
        W, H = bays[j]["width"], bays[j]["height"]
        return any(math.ceil(-lx0) <= math.floor(W - lx1) and math.ceil(-ly0) <= math.floor(H - ly1)
                   for (lx0, ly0, lx1, ly1) in ORS[i])
    fitb = {i: [j for j in range(nb) if fits(i, j)] for i in range(len(B))}

    asg = {}; byb = {j: [] for j in range(nb)}; cur = {}
    ext = {}
    for tt, ops in sol["operations"].items():
        for o in ops:
            if o["type"] == "EXIT":
                ext[o["block_id"]] = int(tt)
    for tt, ops in sol["operations"].items():
        for o in ops:
            if o["type"] == "ENTRY":
                b = o["block_id"]; asg[b] = o["bay_id"]; byb[o["bay_id"]].append(b)
                cur[b] = (o["bay_id"], o["x"], o["y"], o["orient_idx"], int(tt), ext[b])

    cache = {}                                            # frozenset(ids) keyed by bay -> (td, packing)

    cur_place = {}                              # bid -> (x,y,oi,entry): current best placement (warm-start)

    def cpsat_pack(j, ids):
        key = (j, frozenset(ids))
        if key in cache:
            return cache[key]
        if not ids:
            cache[key] = (0.0, []); return cache[key]
        # WARM-START: a per-bay move changes the set by ONE block, so the existing placements are a near-
        # feasible hint -> CP-SAT repairs fast (vs cold ~15s). This is what makes the descent fast enough.
        hint = {i: cur_place[i] for i in ids if i in cur_place}
        out = pack_bay_cpsat(int(bays[j]["width"]), int(bays[j]["height"]), [MB[i] for i in ids], B,
                             tl=min(per_bay_tl, max(2.0, time_budget - (time.time() - t0))), workers=4,
                             hint=(hint or None))
        if not out or len(out) != len(ids):
            cache[key] = (1e18, None); return cache[key]
        td = sum(max(0, f - due[bid]) for (bid, x, y, oi, e, f) in out)
        cache[key] = (td, out); return cache[key]

    # current per-bay tardiness (should be ~0)
    baytd = {j: cpsat_pack(j, byb[j])[0] for j in range(nb)}
    Z3sum = sum(smax[i] - pref[i][asg[i]] for i in asg)
    if verbose:
        print(f"  cpsat_z3 start Z3={Z3sum} bayTD={sum(baytd.values()):.0f}", flush=True)
    blocks_by_z3 = sorted(asg, key=lambda i: -(smax[i] - pref[i][asg[i]]))   # worst-pref first
    bestpack = {j: cpsat_pack(j, byb[j])[1] for j in range(nb)}
    for j in range(nb):                         # seed warm-start placements
        for (bid, x, y, oi, e, f) in (bestpack[j] or []):
            cur_place[bid] = (x, y, oi, e)
    acc = 0; idx = 0
    while time.time() - t0 < time_budget - per_bay_tl:
        # cycle through worst-Z3 blocks
        if idx >= len(blocks_by_z3):
            break
        i = blocks_by_z3[idx]; idx += 1
        j1 = asg[i]
        # candidate target bays: better preference than current, fitting
        cands = [j for j in fitb[i] if j != j1 and pref[i][j] > pref[i][j1]]
        cands.sort(key=lambda j: -pref[i][j])
        moved = False
        for j2 in cands[:2]:
            # RELOCATE i -> j2 (better pref). accept iff both bays stay Z1=0 & Z3 drops.
            td1, p1 = cpsat_pack(j1, [b for b in byb[j1] if b != i])
            td2, p2 = cpsat_pack(j2, byb[j2] + [i])
            if td1 <= baytd[j1] + 1e-9 and td2 <= 1e-9:
                dZ3 = (smax[i] - pref[i][j2]) - (smax[i] - pref[i][j1])
                if dZ3 < -1e-9:
                    byb[j1] = [b for b in byb[j1] if b != i]; byb[j2] = byb[j2] + [i]; asg[i] = j2
                    baytd[j1] = td1; baytd[j2] = td2; bestpack[j1] = p1; bestpack[j2] = p2
                    for (bid, xx, yy, oo, ee, ff) in (p1 or []) + (p2 or []):
                        cur_place[bid] = (xx, yy, oo, ee)
                    Z3sum += dZ3; acc += 1; moved = True; break
        if moved or time.time() - t0 > time_budget - per_bay_tl:
            continue
        # SWAP i (in j1, wants j2) <-> k (in j2): keeps bay cardinality -> works in TIGHT bays where a bare
        # relocate overflows the preferred bay. accept iff both stay Z1=0 & net Z3 drops.
        for j2 in cands[:2]:
            for k in sorted(byb[j2], key=lambda b: pref[b][j1] - pref[b][j2], reverse=True)[:3]:
                if j1 not in fitb[k]:
                    continue
                s1 = [b for b in byb[j1] if b != i] + [k]
                s2 = [b for b in byb[j2] if b != k] + [i]
                td1, p1 = cpsat_pack(j1, s1)
                if td1 > 1e-9:
                    continue
                td2, p2 = cpsat_pack(j2, s2)
                if td2 > 1e-9:
                    continue
                dZ3 = ((smax[i] - pref[i][j2]) + (smax[k] - pref[k][j1])
                       - (smax[i] - pref[i][j1]) - (smax[k] - pref[k][j2]))
                if dZ3 < -1e-9:
                    byb[j1] = s1; byb[j2] = s2; asg[i] = j2; asg[k] = j1
                    baytd[j1] = td1; baytd[j2] = td2; bestpack[j1] = p1; bestpack[j2] = p2
                    for (bid, xx, yy, oo, ee, ff) in (p1 or []) + (p2 or []):
                        cur_place[bid] = (xx, yy, oo, ee)
                    Z3sum += dZ3; acc += 1; moved = True; break
            if moved:
                break
    if acc == 0:
        return sol
    place = {}
    for j in range(nb):
        pk = bestpack[j]
        if pk is None:
            return sol
        for (bid, x, y, oi, e, f) in pk:
            place[bid] = (j, x, y, oi, e, f)
    if len(place) != len(B):
        return sol
    ts = build_ops(place); r = utils.check_feasibility(prob, ts)
    if verbose:
        print(f"  cpsat_z3 -> Z3={Z3sum} obj={r['objective'] if r['feasible'] else 'INF'} acc={acc}", flush=True)
    return ts if (r["feasible"] and r["objective"] < base["objective"] - 1e-9) else sol

