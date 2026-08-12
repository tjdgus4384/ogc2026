#!/usr/bin/env python3
"""Was there room for the WHOLE STAY, on a day the block could still be on time?

Two measurements disagree. reach_probe brute-forces every integer (bay,
orientation, x, y) with the kernel's own vp oracle and reports NO legal position
for the worst tardy blocks on any day they waited. frag45 rasterises the same
finished board and finds, on those same days, contiguous free regions 3-10x the
block's own footprint -- P5 block 15 waited 48 days while bay 0 sat at 25%
occupancy with 1700 connected free cells against its 149-cell footprint.

Only one of three things can reconcile them:

  STAY      a placement needs the space free for all of [e, e+P), not on one
            day; the single-day snapshot overstates the room;
  CRANE     the free area is there for the whole stay but the crane rule
            forbids the descent anyway (weak on P4/P5, which have 1-2 layers);
  ORACLE    the space is genuinely usable and vp rejects it -- the same class
            of defect as the v6.0 over-strict same-day entry test, and by far
            the largest lever of the three.

This settles STAY. For each candidate entry day in the block's PUNCTUAL window
it intersects the free cells over the entire stay and reports the largest
connected region that survives. A region at least the size of the footprint,
on any punctual entry day, means the room was really there and the answer is
CRANE or ORACLE.

    python3 tools/window_free.py prob_5 runs/base53-s41/prob_5.sol.json [nblocks]
"""
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
from frag45 import poly_cells, largest_component      # noqa: E402


def main():
    prob_id, sol_path = sys.argv[1], sys.argv[2]
    top = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    sol = json.load(open(REPO / sol_path))
    bays, B = prob["bays"], prob["blocks"]

    asg = {}
    for day, ops in sol["operations"].items():
        for op in ops:
            i = op["block_id"]
            if op["type"] == "ENTRY":
                asg.setdefault(i, {})["block_id"] = i
                asg[i].update(bay_id=op["bay_id"], x=op["x"], y=op["y"],
                              orientation=op["orient_idx"], entry_time=int(day))
            else:
                asg.setdefault(i, {})["exit_time"] = int(day)
    A = [a for a in asg.values() if "entry_time" in a]
    for a in A:
        a.setdefault("exit_time",
                     a["entry_time"] + int(B[a["block_id"]]["processing_time"]))

    # Occupancy per (bay, day), in the currency the CRANE rule uses. A new
    # block's bottom layer is blocked by every resident layer j >= 0, i.e. by
    # ALL of them, and upper layers may overhang the footprint (the statement's
    # own example has layer 1 starting at x = -3.04 while layer 0 starts at 0).
    # Counting layer 0 alone understates what a resident sterilises, so the
    # union over layers is what a descending block actually has to miss.
    horizon = max(a["exit_time"] for a in A) + 1
    occ = {(j, d): set() for j in range(len(bays)) for d in range(horizon)}
    occ0 = {(j, d): set() for j in range(len(bays)) for d in range(horizon)}
    for b in A:
        j, W, H = b["bay_id"], int(bays[b["bay_id"]]["width"]), int(bays[b["bay_id"]]["height"])
        sh = B[b["block_id"]]["shape"][b["orientation"]]
        ground = poly_cells(sh["layers"][0], b["x"], b["y"], W, H)
        shadow = set(ground)
        for lay in sh["layers"][1:]:
            shadow |= poly_cells(lay, b["x"], b["y"], W, H)
        for d in range(b["entry_time"], b["exit_time"]):
            occ[(j, d)] |= shadow
            occ0[(j, d)] |= ground

    tardy = sorted((a for a in A
                    if a["exit_time"] > B[a["block_id"]]["due_date"]),
                   key=lambda a: B[a["block_id"]]["due_date"] - a["exit_time"])
    print(f"{prob_id}: {len(tardy)} tardy; the worst {top}, over their FULL stay")

    for a in tardy[:top]:
        i = a["block_id"]
        blk = B[i]
        R, D, P = int(blk["release_time"]), int(blk["due_date"]), int(blk["processing_time"])
        own = min(len(poly_cells(o["layers"][0],
                                 -min(p[0] for p in o["layers"][0]),
                                 -min(p[1] for p in o["layers"][0]), 10 ** 4, 10 ** 4))
                  for o in blk["shape"])
        last = D - P                       # latest entry that is still punctual
        print(f"  blk {i:3d}: R={R} D={D} P={P} -> punctual entry <= {last}; "
              f"actually entered {a['entry_time']} (late {a['exit_time'] - D}d); "
              f"footprint ~{own} cells")
        if last < R:
            print("     cannot be punctual from its own release -- forced")
            continue
        best = (0, None)
        best0 = 0
        for d in range(R, last + 1):
            for j, bay in enumerate(bays):
                W, H = int(bay["width"]), int(bay["height"])
                grid = {(x, y) for x in range(W) for y in range(H)}
                free, ground = set(grid), set(grid)
                for t in range(d, min(d + P, horizon)):
                    free -= occ[(j, t)]
                    ground -= occ0[(j, t)]
                best0 = max(best0, largest_component(ground, W, H))
                if len(free) < own:
                    continue
                big = largest_component(free, W, H)
                if big > best[0]:
                    best = (big, (d, j, len(free)))
        if best[1] is None:
            print(f"     crane-shadow free region never reaches {own} cells on a"
                  f" punctual stay (ignoring layers it would reach {best0})"
                  f"  -> CRANE SHADOW explains it")
        else:
            d, j, nfree = best[1]
            verdict = ("ROOM WAS THERE -> vp ORACLE over-rejects" if best[0] >= own
                       else "still too small -> CRANE SHADOW")
            print(f"     best: entry day {d} bay {j}: {nfree} cells free for the whole"
                  f" stay, largest connected {best[0]} vs {own}"
                  f" (layer-0 only would be {best0})  -> {verdict}")


if __name__ == "__main__":
    main()
