#!/usr/bin/env python3
"""Is the yard FULL, or is it FRAGMENTED?

reach_probe settled that the worst tardy blocks have no legal position on any
day they waited -- over every bay, orientation and integer position. Two very
different worlds produce that answer:

  FULL        the free area is genuinely smaller than the block; nothing any
              placement policy could do would have helped;
  FRAGMENTED  there is plenty of free area, but it is scattered in pieces
              smaller than the block -- which is a property of the placement
              POLICY (we take the first legal corner candidate, bottom-left
              first), not of the instance.

P4 and P5 -- the two boards carrying the largest gaps -- have only 1-2 layers
per block, so the crane rule there is close to plain 2D non-overlap and the
layer-shadow story cannot be the explanation. This measures which world we are
in: on each day a tardy block waited, it rasterises the occupied cells of every
bay and reports the free area, the largest free connected component, and how
those compare with the waiting block's own footprint.

    python3 tools/frag45.py prob_5 runs/base53-s41/prob_5.sol.json [nblocks]
"""
import collections
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent


def poly_cells(pts, ox, oy, W, H):
    """Integer cells whose centre lies inside the translated polygon."""
    xs = [p[0] + ox for p in pts]
    ys = [p[1] + oy for p in pts]
    lo_x, hi_x = max(0, int(min(xs))), min(W - 1, int(max(xs)) + 1)
    lo_y, hi_y = max(0, int(min(ys))), min(H - 1, int(max(ys)) + 1)
    out = set()
    npt = len(pts)
    for cy in range(lo_y, hi_y + 1):
        py = cy + 0.5
        for cx in range(lo_x, hi_x + 1):
            px = cx + 0.5
            inside = False
            j = npt - 1
            for i in range(npt):
                yi, yj = ys[i], ys[j]
                if (yi > py) != (yj > py):
                    xint = xs[i] + (py - yi) / (yj - yi) * (xs[j] - xs[i])
                    if px < xint:
                        inside = not inside
                j = i
            if inside:
                out.add((cx, cy))
    return out


def largest_component(free, W, H):
    seen = set()
    best = 0
    for cell in free:
        if cell in seen:
            continue
        stack = [cell]
        seen.add(cell)
        size = 0
        while stack:
            x, y = stack.pop()
            size += 1
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                nb = (nx, ny)
                if nb in free and nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        best = max(best, size)
    return best


def main():
    prob_id, sol_path = sys.argv[1], sys.argv[2]
    top = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    sol = json.load(open(REPO / sol_path))
    bays, B = prob["bays"], prob["blocks"]
    # the emitted solution is an operation log, not an assignment table:
    # {"operations": {day: [{type: ENTRY|EXIT, block_id, bay_id, x, y,
    # orient_idx}]}}. Fold it back into one record per block.
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
    assignments = [a for a in asg.values() if "entry_time" in a]
    for a in assignments:
        a.setdefault("exit_time",
                     a["entry_time"] + int(B[a["block_id"]]["processing_time"]))

    tardy = sorted((a for a in assignments
                    if a["exit_time"] > B[a["block_id"]]["due_date"]),
                   key=lambda a: B[a["block_id"]]["due_date"] - a["exit_time"])
    print(f"{prob_id}: {len(tardy)} tardy blocks; inspecting the worst {top}")

    for a in tardy[:top]:
        i = a["block_id"]
        blk = B[i]
        rel, ent = int(blk["release_time"]), int(a["entry_time"])
        # the block's own footprint area, smallest over its orientations
        own = min(len(poly_cells(o["layers"][0], -min(p[0] for p in o["layers"][0]),
                                 -min(p[1] for p in o["layers"][0]), 10 ** 4, 10 ** 4))
                  for o in blk["shape"])
        print(f"  blk {i:3d}: late {a['exit_time'] - blk['due_date']:3d}d, "
              f"waited {ent - rel:3d}d, footprint ~{own} cells")
        for day in (rel, (rel + ent) // 2, max(rel, ent - 1)):
            for j, bay in enumerate(bays):
                W, H = int(bay["width"]), int(bay["height"])
                occ = set()
                for b in assignments:
                    if b["bay_id"] != j or b["block_id"] == i:
                        continue
                    if not (b["entry_time"] <= day < b["exit_time"]):
                        continue
                    sh = B[b["block_id"]]["shape"][b["orientation"]]
                    occ |= poly_cells(sh["layers"][0], b["x"], b["y"], W, H)
                free = {(x, y) for x in range(W) for y in range(H)} - occ
                big = largest_component(free, W, H)
                tot = W * H
                print(f"     day {day:3d} bay {j}: occupied {len(occ) * 100 // tot:3d}%"
                      f"  free {len(free):5d} cells"
                      f"  largest free region {big:5d}"
                      f"  ({'FITS' if big >= own else 'too small'} vs {own})")


if __name__ == "__main__":
    main()
