"""What binds on the real boards: packing density or scheduling choice?

Reads a prob JSON + saved solution (.sol.json) and reports, per bay, the
time profile of area utilisation (sum of resident orient-bbox areas / bay
area), plus the tardy-block structure and per-block preference regret.

Usage: python diag_solution.py data/preliminary_test/prob_4.json \
                               runs/prelim-v21diag-s7/prob_4.sol.json
"""
import json
import sys
from collections import defaultdict


def main():
    prob = json.load(open(sys.argv[1]))
    sol = json.load(open(sys.argv[2]))
    B, bays = prob["blocks"], prob["bays"]

    place = {}                      # bid -> (bay, entry, exit, ori)
    for t_str, ops in sol["operations"].items():
        for op in ops:
            bid = op["block_id"]
            if op["type"] == "ENTRY":
                e = place.get(bid, [None] * 4)
                place[bid] = [op["bay_id"], float(t_str), e[2], op["orient_idx"]]
            else:
                e = place.get(bid, [None] * 4)
                e[2] = float(t_str)
                place[bid] = e

    def bbox_area(b, oi):
        vs = [v for layer in b["shape"][oi]["layers"] for v in layer]
        xs = [v[0] for v in vs]; ys = [v[1] for v in vs]
        return (max(xs) - min(xs)) * (max(ys) - min(ys))

    # per-bay, per-day area occupancy
    occ = defaultdict(lambda: defaultdict(float))
    tardy = []
    regret = []
    for i, b in enumerate(B):
        j, e, f, oi = place[i]
        a = bbox_area(b, oi)
        for t in range(int(e), int(f)):
            occ[j][t] += a
        td = max(0.0, f - b["due_date"])
        if td > 0:
            tardy.append((td, b["processing_time"], a, e - b["release_time"], i))
        pref = b["bay_preferences"]
        regret.append(max(pref) - pref[j])

    for j, bay in enumerate(bays):
        cap = bay["width"] * bay["height"]
        prof = sorted(occ[j].values())
        if not prof:
            continue
        n = len(prof)
        print(f"bay{j} cap={cap:.0f} days={n} "
              f"util p50={prof[n//2]/cap:.2f} p90={prof[int(n*.9)]/cap:.2f} "
              f"max={prof[-1]/cap:.2f}")

    tardy.sort(reverse=True)
    tt = sum(t[0] for t in tardy)
    print(f"tardy: {len(tardy)}/{len(B)} blocks, {tt:.0f} days total")
    print("  top10 (days, P, area, wait=entry-release):")
    for td, p, a, w, i in tardy[:10]:
        print(f"   blk{i}: tardy={td:.0f} P={p:.0f} area={a:.0f} wait={w:.0f}")
    waits = sorted(t[3] for t in tardy)
    if waits:
        print(f"  tardy waits: p50={waits[len(waits)//2]:.0f} "
              f"max={waits[-1]:.0f}")
    print(f"z3: sum={sum(regret):.0f} nonzero={sum(1 for r in regret if r)}"
          f"/{len(B)} max={max(regret):.0f}")


if __name__ == "__main__":
    main()
