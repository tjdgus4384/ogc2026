"""tradeoff.py -- measure the Z1 (tardiness) vs Z3 (preference) exchange on the real set.

Runs the footprint-disjoint greedy (AABB-only, crane+collision auto-safe) under two bay-assignment
policies and reports the FULL objective (w1*Z1 + w2*Z2 + w3*Z3) for each:
  PREF : assign to most-preferred fitting bay (minimizes Z3, current floor.py behavior)
  BAL  : assign to least time-loaded fitting bay (minimizes contention -> Z1, ignores preference)

Goal: on the Z3-heavy instances (low w1/w3), does spending Z3 to cut contention actually lower the
total objective? This grounds the multi-objective weighting (replaces the notes' "ignore Z3").
"""
import sys, json, glob, pathlib, math
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "solution" / "baseline" / "baseline"))
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


def fitting_bays(prob, bi):
    """Return list of (bay_id, orient_idx, aabb) for every bay that can hold block bi (first fitting orient)."""
    blk = prob["blocks"][bi]; bays = prob["bays"]
    out = []
    for j in range(len(bays)):
        for oi in range(len(blk["shape"])):
            ab = foot_aabb(blk, oi)
            if fits(bays[j], *ab):
                out.append((j, oi, ab)); break
    return out


def footprint_greedy(prob, policy):
    """policy in {'pref','bal'}: choose bay per block, then footprint-disjoint bottom-left place + EDD schedule."""
    n = len(prob["blocks"]); bays = prob["bays"]
    active = {j: [] for j in range(len(bays))}      # (X0,Y0,X1,Y1,exit) world AABBs present
    bay_timeload = {j: 0 for j in range(len(bays))}  # sum of processing times assigned (contention proxy)
    asg = []
    order = sorted(range(n), key=lambda i: (prob["blocks"][i]["due_date"], prob["blocks"][i]["processing_time"]))
    for bi in order:
        blk = prob["blocks"][bi]; r = blk["release_time"]; p = blk["processing_time"]
        cands = fitting_bays(prob, bi)
        if policy == "pref":
            cands.sort(key=lambda c: blk["bay_preferences"][c[0]], reverse=True)
        else:  # bal: least time-loaded bay first (break ties by preference)
            cands.sort(key=lambda c: (bay_timeload[c[0]], -blk["bay_preferences"][c[0]]))
        j, oi, (lx0, ly0, lx1, ly1) = cands[0]
        W, H = bays[j]["width"], bays[j]["height"]
        placed = False
        ent_cands = sorted({r} | {ex for (_, _, _, _, ex) in active[j] if ex > r})
        for e in ent_cands:
            present = [a for a in active[j] if a[4] > e]
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
                    ok = all(not (X0 < a[2] - 1e-9 and a[0] < X1 - 1e-9 and
                                  Y0 < a[3] - 1e-9 and a[1] < Y1 - 1e-9) for a in present)
                    if ok:
                        best = (px, py, X0, Y0, X1, Y1); break
                if best:
                    break
            if best:
                px, py, X0, Y0, X1, Y1 = best
                f = e + p
                active[j].append((X0, Y0, X1, Y1, f)); bay_timeload[j] += p
                asg.append((bi, j, px, py, oi, e, f)); placed = True; break
        if not placed:
            e = max(r, max((a[4] for a in active[j]), default=0)); f = e + p
            px, py = max(0, math.ceil(-lx0)), max(0, math.ceil(-ly0))
            active[j].append((px + lx0, py + ly0, px + lx1, py + ly1, f)); bay_timeload[j] += p
            asg.append((bi, j, px, py, oi, e, f))
    return asg


def to_sol(asg):
    ops = {}
    for bi, j, x, y, oi, e, f in asg:
        ops.setdefault(str(int(f)), []).append({"type": "EXIT", "block_id": bi, "bay_id": j})
    for bi, j, x, y, oi, e, f in asg:
        ops.setdefault(str(int(e)), []).append({"type": "ENTRY", "block_id": bi, "bay_id": j,
                                                "x": int(x), "y": int(y), "orient_idx": oi})
    for t in ops:
        ops[t].sort(key=lambda o: (0 if o["type"] == "EXIT" else 1, o["block_id"]))
    return {"operations": {t: ops[t] for t in sorted(ops, key=int)}}


if __name__ == "__main__":
    files = sorted(glob.glob(str(pathlib.Path(__file__).resolve().parent.parent / "data" / "train" / "*.json")),
                   key=lambda p: int(pathlib.Path(p).stem.split("_")[1]))
    print(f"{'inst':8s} {'w1/w3':>6s} | {'PREF: w1Z1':>11s} {'w3Z3':>7s} {'total':>12s} | "
          f"{'BAL: w1Z1':>11s} {'w3Z3':>7s} {'total':>12s} | winner")
    for f in files:
        prob = json.load(open(f)); w = prob["weights"]; name = pathlib.Path(f).stem
        row = {}
        for pol in ("pref", "bal"):
            r = utils.check_feasibility(prob, to_sol(footprint_greedy(prob, pol)))
            if not r["feasible"]:
                row[pol] = None; continue
            row[pol] = (w["w1"] * r["obj1"], w["w3"] * r["obj3"], r["objective"])
        if row["pref"] and row["bal"]:
            win = "PREF" if row["pref"][2] <= row["bal"][2] else "BAL"
            gain = abs(row["pref"][2] - row["bal"][2]) / max(row["pref"][2], row["bal"][2]) * 100
            print(f"{name:8s} {w['w1']/w['w3']:6.1f} | "
                  f"{row['pref'][0]:11.0f} {row['pref'][1]:7.0f} {row['pref'][2]:12.0f} | "
                  f"{row['bal'][0]:11.0f} {row['bal'][1]:7.0f} {row['bal'][2]:12.0f} | {win} +{gain:.0f}%")
        else:
            print(f"{name:8s} INFEAS pref={row['pref'] is not None} bal={row['bal'] is not None}")
