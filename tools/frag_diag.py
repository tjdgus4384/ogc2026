"""Fragmentation diagnosis: on how many of a tardy block's waiting days did
some bay have ENOUGH FREE AREA, yet the block still could not enter?

Area says yes + solver said no = geometric admission failure (fragmentation
or crane blocking) -- the recoverable Z1 if placement quality improved.

Usage: python frag_diag.py <prob.json> <sol.json>
"""
import json
import sys
from collections import defaultdict


def bbox_dims(b, oi):
    vs = [v for L in b["shape"][oi]["layers"] for v in L]
    xs = [v[0] for v in vs]; ys = [v[1] for v in vs]
    return max(xs) - min(xs), max(ys) - min(ys)


def main():
    prob = json.load(open(sys.argv[1]))
    sol = json.load(open(sys.argv[2]))
    B, bays = prob["blocks"], prob["bays"]

    place = {}
    for t_str, ops in sol["operations"].items():
        for op in ops:
            bid = op["block_id"]
            e = place.setdefault(bid, [None, None, None, 0])
            if op["type"] == "ENTRY":
                e[0], e[1], e[3] = op["bay_id"], float(t_str), op["orient_idx"]
            else:
                e[2] = float(t_str)

    # per-bay per-day free bbox area under OUR solution
    used = defaultdict(lambda: defaultdict(float))
    for i, b in enumerate(B):
        j, e, f, oi = place[i]
        w, h = bbox_dims(b, oi)
        for t in range(int(e), int(f)):
            used[j][t] += w * h

    cap = [bay["width"] * bay["height"] for bay in bays]

    waits = frag_days = crane_days = 0
    blocked = []
    for i, b in enumerate(B):
        j, e, f, oi = place[i]
        R = b["release_time"]
        if e <= R:
            continue
        # smallest orientation bbox of this block
        a_min = min(bbox_dims(b, o)[0] * bbox_dims(b, o)[1]
                    for o in range(len(b["shape"])))
        fd = 0
        for t in range(int(R), int(e)):
            waits += 1
            # any bay with free area >= the block's smallest bbox, where the
            # bbox also fits the bay dims?
            ok = False
            for jj, bay in enumerate(bays):
                for o in range(len(b["shape"])):
                    w, h = bbox_dims(b, o)
                    if w <= bay["width"] and h <= bay["height"] and \
                            cap[jj] - used[jj][t] >= w * h:
                        ok = True
                        break
                if ok:
                    break
            if ok:
                fd += 1
        frag_days += fd
        if fd:
            blocked.append((fd, int(e) - int(R), i))

    tardy_total = sum(max(0.0, place[i][2] - b["due_date"])
                      for i, b in enumerate(B))
    print(json.dumps({
        "wait_days_total": waits,
        "area_said_yes_days": frag_days,
        "frag_fraction": round(frag_days / max(1, waits), 3),
        "tardy_days_total": round(tardy_total),
    }))
    blocked.sort(reverse=True)
    for fd, wd, i in blocked[:8]:
        print(f"  blk{i}: {fd}/{wd} wait days had area room somewhere")


if __name__ == "__main__":
    main()
