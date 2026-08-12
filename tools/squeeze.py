#!/usr/bin/env python3
"""Is tardiness concentrated in the blocks that barely fit the bay?

P6 block 239 waits 102 days. Its layer 0 is 7.78 x 5.60 -- 34 cells in a
2,310-cell bay -- which is why an earlier probe of mine called it small and
concluded the yard had room. It is not small: the block is an inverted pyramid
whose layer 3 spans 18.00 x 13.33, and the bays are only 15, 19 and 20 tall. In
its preferred bay that leaves y in [0, 5.67] and demands an 18-wide corridor
held clear for 29 days.

If that is the shape of the problem, tardiness should track how tightly a block
squeezes into the bay vertically -- not its footprint, not its workload. This
measures the correlation on a finished board: per block, the vertical squeeze
(the tallest layer's y-extent over the best bay height it can use), against
whether and how late it ended up.

The construction orders blocks by (due date, -processing time), which is blind
to this. If squeeze predicts tardiness, the blocks that need the rare positions
are being asked for them last.

    python3 tools/squeeze.py prob_6 runs/base53-s41/prob_6.sol.json
"""
import json
import pathlib
import statistics
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent


def main():
    prob_id, sol_path = sys.argv[1], sys.argv[2]
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    sol = json.load(open(REPO / sol_path))
    bays, B = prob["bays"], prob["blocks"]
    Hmax = max(int(b["height"]) for b in bays)
    Wmax = max(int(b["width"]) for b in bays)

    exit_of, entry_of = {}, {}
    for day, ops in sol["operations"].items():
        for op in ops:
            (entry_of if op["type"] == "ENTRY" else exit_of)[op["block_id"]] = int(day)

    rows = []
    for i, blk in enumerate(B):
        if i not in entry_of:
            continue
        # best (least squeezed) orientation: the one whose tallest layer leaves
        # the most vertical room, since that is what limits where it can sit
        best = 1e9
        for o in blk["shape"]:
            hy = max(max(p[1] for p in L) - min(p[1] for p in L) for L in o["layers"])
            wx = max(max(p[0] for p in L) - min(p[0] for p in L) for L in o["layers"])
            if hy <= Hmax and wx <= Wmax:
                best = min(best, hy / Hmax)
        if best > 1e8:
            continue
        late = max(0, exit_of.get(i, entry_of[i] + int(blk["processing_time"]))
                   - int(blk["due_date"]))
        slack = int(blk["due_date"]) - int(blk["processing_time"]) - int(blk["release_time"])
        rows.append((best, late, slack, i))

    rows.sort()
    n = len(rows)
    print(f"{prob_id}: {n} placed blocks, bay heights "
          f"{[int(b['height']) for b in bays]}")
    print(f"   {'squeeze quintile':>18} {'n':>4} {'tardy':>7} {'mean late':>10} "
          f"{'mean slack':>11}")
    for q in range(5):
        part = rows[q * n // 5:(q + 1) * n // 5]
        if not part:
            continue
        lo, hi = part[0][0], part[-1][0]
        tardy = sum(1 for r in part if r[1] > 0)
        print(f"   {lo:.2f}-{hi:.2f}      {len(part):>4} {tardy:>4} "
              f"({tardy / len(part) * 100:3.0f}%) {statistics.mean(r[1] for r in part):10.1f}"
              f" {statistics.mean(r[2] for r in part):11.1f}")
    tot = sum(r[1] for r in rows)
    top = sum(r[1] for r in rows[-n // 5:])
    print(f"   the most-squeezed fifth carries {top:,} of {tot:,} tardy days "
          f"({top / max(1, tot) * 100:.0f}%)")


if __name__ == "__main__":
    main()
