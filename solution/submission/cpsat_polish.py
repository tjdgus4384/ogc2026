"""cpsat_polish.py -- per-bay EXACT(CP-SAT) re-pack polish. THE oracle-strength lever.

Diagnosed 2026-06-21: the beam packer (bottom-left greedy over orders) UNDER-packs -- on prob_4 it leaves
85 days of bay tardiness that CP-SAT eliminates to 2 (prob_4 bay1 49 blocks: beam 33 -> cpsat 0). So our
engine's per-bay Z1 was inflated by a weak oracle, blocking the low-Z3 sweet spot. With the real time limits
(minutes-to-30min) + a fast CPU (+ Gurobi on the server), exact per-bay packing (~10-20s/bay) is affordable.

This re-packs each TARDY bay of a solution with pack_bay_cpsat (same per-bay assignment -> Z2/Z3 unchanged;
bays independent -> safe), warm-started from the current packing, and keeps per-bay improvements (best-of).
Returns an improved sol. Never regresses (best-of + utils validation by the caller).
"""
import time
from packer import block_meta
from cpsat_packer import pack_bay_cpsat


def cpsat_polish(prob, sol, utils, time_budget=120.0, per_bay_tl=20.0, verbose=False):
    from solve import build_ops
    t0 = time.time()
    braw = prob["blocks"]; bays = prob["bays"]
    base = utils.check_feasibility(prob, sol)
    if not base["feasible"]:
        return sol
    MB = {i: block_meta(braw[i], i) for i in range(len(braw))}
    for i in MB:
        MB[i]["proc"] = max(1, MB[i]["proc"])
    due = {i: braw[i]["due_date"] for i in range(len(braw))}
    cur = {}; byb = {}
    for tt, ops in sol["operations"].items():
        for o in ops:
            if o["type"] == "ENTRY":
                b = o["block_id"]
                cur[b] = [o["bay_id"], o["x"], o["y"], o["orient_idx"], int(tt), None]
                byb.setdefault(o["bay_id"], []).append(b)
    for tt, ops in sol["operations"].items():
        for o in ops:
            if o["type"] == "EXIT":
                cur[o["block_id"]][5] = int(tt)

    def bay_tard(ids):
        return sum(max(0, cur[i][5] - due[i]) for i in ids)
    order = sorted(byb, key=lambda j: -bay_tard(byb[j]))    # worst-tardy bay first
    best_obj = base["objective"]; changed = False
    for j in order:
        if time.time() - t0 > time_budget:
            break
        ids = byb[j]
        if bay_tard(ids) <= 0 or len(ids) < 2:
            continue
        metas = [MB[i] for i in ids]
        # warm-start hint from the current packing (entry order + positions)
        hint = {i: (cur[i][1], cur[i][2], cur[i][3], cur[i][4]) for i in ids}
        tl = min(per_bay_tl, time_budget - (time.time() - t0))
        try:
            out = pack_bay_cpsat(int(bays[j]["width"]), int(bays[j]["height"]), metas, braw,
                                 tl=tl, workers=4, hint=hint)
        except Exception:
            out = None
        if not out or len(out) != len(ids):
            continue
        new_td = sum(max(0, f - due[bid]) for (bid, x, y, oi, e, f) in out)
        if new_td < bay_tard(ids) - 1e-9:
            for (bid, x, y, oi, e, f) in out:
                cur[bid] = [j, x, y, oi, e, f]
            changed = True
            if verbose:
                print(f"  cpsat_polish bay{j}: {bay_tard(ids):.0f} -> {new_td:.0f}", flush=True)
    if not changed:
        return sol
    ts = build_ops({i: tuple(cur[i]) for i in cur})
    r = utils.check_feasibility(prob, ts)
    return ts if (r["feasible"] and r["objective"] < best_obj - 1e-9) else sol
