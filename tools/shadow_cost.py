#!/usr/bin/env python3
"""How much bay does an orientation STERILISE, versus how much it stands on?

window_free (2026-08-12) measured the crane shadow directly: the connected free
region available to a waiting block collapses by 60-73% once a resident's upper
layers are counted, because a new block's bottom layer has to miss every
resident layer j >= 0, not just their footprints. P4 blocks 2 and 3 missed a
punctual entry by 5 and 4 cells and paid 18 and 14 days of tardiness for it.

So the quantity that decides yard capacity is a resident's SHADOW -- the union
of its layers projected down -- not its ground footprint. A block whose upper
layers overhang sterilises floor it does not even stand on.

The kernel's orientation preference (knobs::oriPref) ranks orientations by
layer-0 BOUNDING BOX area. That is a proxy for the wrong thing twice over: it
uses a bounding box rather than the polygon, and it never looks above layer 0.
This measures what the right quantity would be -- per block, the ground area,
the shadow area, and their ratio across orientations -- so a policy change can
be designed against numbers instead of intuition.

    python3 tools/shadow_cost.py prob_5
"""
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
from frag45 import poly_cells                          # noqa: E402


def areas(orient):
    """(ground cells, shadow cells) for one orientation, at unit resolution."""
    lays = orient["layers"]
    ox = -min(p[0] for p in lays[0])
    oy = -min(p[1] for p in lays[0])
    ground = poly_cells(lays[0], ox, oy, 10 ** 4, 10 ** 4)
    shadow = set(ground)
    for lay in lays[1:]:
        shadow |= poly_cells(lay, ox, oy, 10 ** 4, 10 ** 4)
    return len(ground), len(shadow)


def main():
    prob_id = sys.argv[1]
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    B = prob["blocks"]
    tot_g = tot_s = 0
    spread = []                 # per block: best vs worst shadow over orientations
    bbox_loss = []              # what ranking by bbox costs against ranking by shadow
    for blk in B:
        vals = [areas(o) for o in blk["shape"]]
        shadows = [s for _, s in vals]
        tot_g += vals[0][0]
        tot_s += vals[0][1]
        if len(shadows) > 1:
            spread.append((min(shadows), max(shadows)))
        # what the kernel would pick: smallest layer-0 bbox area
        def bbox_area(o):
            lay = o["layers"][0]
            xs = [p[0] for p in lay]
            ys = [p[1] for p in lay]
            return (max(xs) - min(xs)) * (max(ys) - min(ys))
        pick_bbox = min(range(len(blk["shape"])), key=lambda k: bbox_area(blk["shape"][k]))
        pick_shadow = min(range(len(vals)), key=lambda k: vals[k][1])
        bbox_loss.append(vals[pick_bbox][1] - vals[pick_shadow][1])

    n = len(B)
    print(f"{prob_id}: {n} blocks, {len(B[0]['shape'])} orientations for block 0")
    print(f"   orientation 0: ground {tot_g:,} cells, shadow {tot_s:,} cells"
          f"  -> shadow is {tot_s / max(1, tot_g):.2f}x the footprint")
    if spread:
        gain = sum(hi - lo for lo, hi in spread)
        base = sum(hi for _, hi in spread)
        print(f"   choosing the shadow-minimal orientation instead of the worst"
              f" saves {gain:,} cells ({gain / max(1, base) * 100:.1f}%)")
    worse = [d for d in bbox_loss if d > 0]
    print(f"   the kernel ranks by layer-0 bbox: that picks a worse-shadow"
          f" orientation for {len(worse)}/{n} blocks,"
          f" costing {sum(bbox_loss):,} cells in total"
          f" ({sum(bbox_loss) / max(1, tot_s) * 100:+.1f}% of the shadow it stamps)")


if __name__ == "__main__":
    main()
