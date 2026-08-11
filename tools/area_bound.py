#!/usr/bin/env python3
"""Lower-bound the tardiness that AREA alone forces, per instance.

A block that is released at R and due at D must occupy its footprint for P
consecutive days. For any window [a, b], every block with R_i >= a and D_i <= b
has to run entirely inside it if it is to be punctual, so those blocks demand
sum(area_i * P_i) area-days from a window that only supplies C * (b - a). Any
excess has to spill past b, and one day of tardiness on block i relieves at
most area_i, giving

    Z1 >= max over windows of  (demand - supply) / max_area_of_those_blocks

This ignores geometry and crane access entirely, so it is a floor: the real
optimum is at least this. Comparing it with our Z1 and with the all-team
minimum's implied Z1 splits the remaining gap into "the instance forbids it"
and "our packing wastes it".

    python3 tools/area_bound.py 3 4 5 6
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
    """Smallest footprint over orientations; a block's footprint is its widest
    layer, since layers stack vertically."""
    best = None
    for o in b["shape"]:
        a = max(poly_area(layer) for layer in o["layers"])
        best = a if best is None else min(best, a)
    return best


def main():
    for pid in sys.argv[1:]:
        p = json.load(open(REPO / "data" / "preliminary_test" / f"prob_{pid}.json"))
        B = p["blocks"]
        cap = sum(bb["width"] * bb["height"] for bb in p["bays"])
        area = [block_area(b) for b in B]
        R = [b["release_time"] for b in B]
        D = [b["due_date"] for b in B]
        P = [b["processing_time"] for b in B]
        days = sorted({int(x) for x in R} | {int(x) for x in D})
        best = (0.0, None)
        for a in days:
            inside = [i for i in range(len(B)) if R[i] >= a]
            if not inside:
                continue
            for b in days:
                if b <= a:
                    continue
                sel = [i for i in inside if D[i] <= b]
                if not sel:
                    continue
                demand = sum(area[i] * P[i] for i in sel)
                supply = cap * (b - a)
                if demand <= supply:
                    continue
                bound = (demand - supply) / max(area[i] for i in sel)
                if bound > best[0]:
                    best = (bound, (a, b, len(sel), demand, supply))
        w1 = p["weights"]["w1"]
        tgt = REFS["alltime_min"][f"P{pid}"]["objective"]
        print(f"P{pid}: area-forced Z1 >= {best[0]:.0f} days"
              + (f"   (window [{best[1][0]},{best[1][1]}], {best[1][2]} blocks, "
                 f"demand {best[1][3]:,.0f} vs supply {best[1][4]:,.0f} area-days)"
                 if best[1] else "   (no window overflows)"))
        print(f"       w1={w1}  all-team minimum {tgt:,} -> if Z2/Z3 were free, "
              f"its Z1 <= {tgt / w1:.0f} days")


if __name__ == "__main__":
    main()
