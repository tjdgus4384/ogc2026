#!/usr/bin/env python3
"""Energetic lower bound on forced tardiness, per instance.

Block i occupies area_i for P_i consecutive days somewhere in [R_i, D_i] if it
is to be punctual. Its unavoidable presence inside a window [t1, t2] is the
classic energetic quantity

    e_i(t1,t2) = max(0, P_i - max(0, t1 - R_i) - max(0, D_i - t2))

-- what is left of its processing once it is pushed as far out of the window as
its release and due date allow. Summing area_i * e_i over all blocks and
comparing to capacity * (t2 - t1) bounds how much area-time the window is
short. One day of tardiness on block i buys back at most area_i of that, so

    Z1 >= max over windows of  (demand - supply) / max_i area_i   over the
                               blocks that contribute to the window

This ignores geometry, crane access and bay assignment, so whatever it reports
is a floor no solver can beat. Comparing it with our Z1 and with the all-team
minimum's implied Z1 says how much of the remaining gap is packing loss and how
much the instance simply forbids.

    python3 tools/energetic_bound.py 3 4 5 6
"""
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
REFS = json.load(open(REPO / "data" / "prelim_refs.json"))


def poly_area(verts):
    s = 0.0
    for k in range(len(verts)):
        x1, y1 = verts[k]
        x2, y2 = verts[(k + 1) % len(verts)]
        s += x1 * y2 - x2 * y1
    return abs(s) * 0.5


def block_area(b):
    return min(max(poly_area(layer) for layer in o["layers"]) for o in b["shape"])


def main():
    for pid in sys.argv[1:]:
        p = json.load(open(REPO / "data" / "preliminary_test" / f"prob_{pid}.json"))
        B = p["blocks"]
        cap = sum(bb["width"] * bb["height"] for bb in p["bays"])
        area = [block_area(b) for b in B]
        R = [b["release_time"] for b in B]
        D = [b["due_date"] for b in B]
        P = [b["processing_time"] for b in B]
        marks = sorted({int(x) for x in R} | {int(x) for x in D})
        best = (0.0, None)
        for t1 in marks:
            for t2 in marks:
                if t2 <= t1:
                    continue
                demand = 0.0
                amax = 0.0
                for i in range(len(B)):
                    e = P[i] - max(0.0, t1 - R[i]) - max(0.0, D[i] - t2)
                    e = min(e, t2 - t1, P[i])
                    if e <= 0:
                        continue
                    demand += area[i] * e
                    amax = max(amax, area[i])
                supply = cap * (t2 - t1)
                if demand <= supply or amax <= 0:
                    continue
                bound = (demand - supply) / amax
                if bound > best[0]:
                    best = (bound, (t1, t2, demand, supply))
        w1 = p["weights"]["w1"]
        tgt = REFS["alltime_min"][f"P{pid}"]["objective"]
        print(f"P{pid}: energetic Z1 >= {best[0]:.0f} days", end="")
        if best[1]:
            t1, t2, d, s = best[1]
            print(f"   (window [{t1},{t2}]: {d:,.0f} vs {s:,.0f} area-days, "
                  f"{100 * d / s:.0f}% loaded)")
        else:
            print("   (no window overloads)")
        print(f"       all-team minimum {tgt:,} -> its Z1 <= {tgt / w1:.0f} days "
              f"if Z2/Z3 were free")


if __name__ == "__main__":
    main()
