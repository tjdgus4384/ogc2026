"""
floor.py -- VALIDATION of the design's two guaranteeable claims (NOT the submission solver):
  (A) serialize floor  -> always feasible (the -1 insurance), prove on real data.
  (B) footprint-disjoint concurrent greedy (AABB only, NO Shapely) -> still always feasible,
      far lower Z1 than serialize, and fast at n=250 (the keystone: footprint-disjoint => crane+collision free).
Both honor the Z1>>Z3 regime: assign to most-preferred bay that fits (Z3 small), schedule EDD.
"""
import sys, json, glob, pathlib, math, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "ogc2026" / "baseline"))
import utils


def foot_aabb(blk, oi):
    xs = []; ys = []
    for l in blk["shape"][oi]["layers"]:
        for x, y in l:
            xs.append(x); ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


def fits(bay, lx0, ly0, lx1, ly1):
    return math.ceil(-lx0) <= math.floor(bay["width"] - lx1) and \
           math.ceil(-ly0) <= math.floor(bay["height"] - ly1)


def choose_bay_orient(prob, bi):
    """Most-preferred bay (Z3) with an orientation that fits; fall back to any fitting bay."""
    blk = prob["blocks"][bi]; bays = prob["bays"]
    order = sorted(range(len(bays)), key=lambda j: blk["bay_preferences"][j], reverse=True)
    for j in order:
        for oi in range(len(blk["shape"])):
            lx0, ly0, lx1, ly1 = foot_aabb(blk, oi)
            if fits(bays[j], lx0, ly0, lx1, ly1):
                return j, oi, (lx0, ly0, lx1, ly1)
    return None


def serialize(prob):
    """(A) one block at a time per bay -> trivially feasible."""
    n = len(prob["blocks"])
    plan = [choose_bay_orient(prob, i) for i in range(n)]
    bay_last = {}
    asg = []
    for bi in sorted(range(n), key=lambda i: (prob["blocks"][i]["due_date"],
                                              prob["blocks"][i]["processing_time"])):
        j, oi, (lx0, ly0, lx1, ly1) = plan[bi]
        blk = prob["blocks"][bi]
        e = max(blk["release_time"], bay_last.get(j, 0))
        f = e + blk["processing_time"]
        bay_last[j] = f
        asg.append((bi, j, max(0, math.ceil(-lx0)), max(0, math.ceil(-ly0)), oi, e, f))
    return asg


def footprint_greedy(prob):
    """(B) concurrent footprint-disjoint (AABB) greedy: place each block at earliest entry + bottom-left
    position whose footprint AABB is disjoint from all present blocks. Footprint-disjoint => crane+collision OK."""
    n = len(prob["blocks"]); bays = prob["bays"]
    plan = [choose_bay_orient(prob, i) for i in range(n)]
    active = {j: [] for j in range(len(bays))}   # (X0,Y0,X1,Y1,exit) world AABBs present
    asg = []
    for bi in sorted(range(n), key=lambda i: (prob["blocks"][i]["due_date"],
                                              prob["blocks"][i]["processing_time"])):
        j, oi, (lx0, ly0, lx1, ly1) = plan[bi]
        blk = prob["blocks"][bi]; r = blk["release_time"]; p = blk["processing_time"]
        W, H = bays[j]["width"], bays[j]["height"]
        w, h = lx1 - lx0, ly1 - ly0
        placed = None
        # candidate entry times: release, and every active exit >= release
        ent_cands = sorted({r} | {ex for (_, _, _, _, ex) in active[j] if ex > r})
        for e in ent_cands:
            present = [a for a in active[j] if a[4] > e]   # still in bay at time e (half-open)
            # candidate bottom-left positions from bay corner + right/top edges of present AABBs
            xs = {max(0, math.ceil(-lx0))} | {math.ceil(a[2] - lx0) for a in present}
            ys = {max(0, math.ceil(-ly0))} | {math.ceil(a[3] - ly0) for a in present}
            best = None
            for px in sorted(xs):
                if px + lx1 > W + 1e-9 or px + lx0 < -1e-9:
                    continue
                for py in sorted(ys):
                    if py + ly1 > H + 1e-9 or py + ly0 < -1e-9:
                        continue
                    X0, Y0, X1, Y1 = px + lx0, py + ly0, px + lx1, py + ly1
                    # AABB-disjoint from all present (shared edge OK -> strict overlap test)
                    ok = all(not (X0 < a[2] - 1e-9 and a[0] < X1 - 1e-9 and
                                  Y0 < a[3] - 1e-9 and a[1] < Y1 - 1e-9) for a in present)
                    if ok:
                        best = (px, py, X0, Y0, X1, Y1); break
                if best:
                    break
            if best:
                px, py, X0, Y0, X1, Y1 = best
                f = e + p
                active[j].append((X0, Y0, X1, Y1, f))
                asg.append((bi, j, px, py, oi, e, f))
                placed = True
                break
        if not placed:
            # fallback: serialize this block after everything in its bay (guaranteed)
            e = max(r, max((a[4] for a in active[j]), default=0))
            f = e + p
            px, py = max(0, math.ceil(-lx0)), max(0, math.ceil(-ly0))
            active[j].append((px + lx0, py + ly0, px + lx1, py + ly1, f))
            asg.append((bi, j, px, py, oi, e, f))
    return asg


def to_sol(asg):
    ops = {}
    for bi, j, x, y, oi, e, f in asg:
        ops.setdefault(str(int(f)), []).append({"type": "EXIT", "block_id": bi, "bay_id": j})
    for bi, j, x, y, oi, e, f in asg:
        ops.setdefault(str(int(e)), []).append({"type": "ENTRY", "block_id": bi, "bay_id": j,
                                                "x": int(x), "y": int(y), "orient_idx": oi})
    # EXIT before ENTRY at each time
    for t in ops:
        ops[t].sort(key=lambda o: (0 if o["type"] == "EXIT" else 1, o["block_id"]))
    # insert keys in ascending time order: check_feasibility reconstructs assignments in dict
    # insertion order, so an EXIT-time key must not precede its block's ENTRY-time key.
    return {"operations": {t: ops[t] for t in sorted(ops, key=int)}}


if __name__ == "__main__":
    files = sorted(glob.glob(str(pathlib.Path(__file__).resolve().parent.parent / "data" / "train" / "*.json")),
                   key=lambda p: int(pathlib.Path(p).stem.split("_")[1]))
    for f in files:
        name = pathlib.Path(f).stem
        try:
            prob = json.loads(pathlib.Path(f).read_text())
        except Exception:
            print(f"{name:9s} UNREADABLE"); continue
        n = len(prob["blocks"])
        out = []
        for label, fn in (("serialize", serialize), ("footprint", footprint_greedy)):
            t0 = time.time(); asg = fn(prob); sol = to_sol(asg)
            r = utils.check_feasibility(prob, sol); el = time.time() - t0
            tag = "FEAS" if r["feasible"] else f"INFEAS s{r['stage']}"
            out.append(f"{label}: {tag} Z1={r['obj1'] if r['feasible'] else '-'} "
                       f"Z3={r['obj3'] if r['feasible'] else '-'} ({el:.1f}s)")
        print(f"{name:9s} n={n:3d} | " + " | ".join(out))
