#!/usr/bin/env python3
"""Cut a real instance down until an exact solve is possible AND Z1 stays > 0.

Every diagnostic this campaign owns describes our own board, because we have no
solution better than ours to look at. The one oracle we can manufacture is an
instance small enough to solve exactly -- but it has to stay hard: P1 and P2 are
already at the known optimum precisely because their Z1 is 0, so they teach
nothing about tardiness.

So shrink along the axes that reduce the search without removing contention:
keep a contiguous block subset, keep the bays (crane geometry is the point), and
clip the horizon. Then check with the real solver that Z1 is still positive. If
it is, the instance is a candidate oracle; if not, shrink less.

    python3 tools/shrink.py prob_5 25 --bays 1 --tl 30
"""
import argparse
import copy
import json
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent


def build(src, nblk, nbay):
    p = copy.deepcopy(src)
    p["bays"] = p["bays"][:nbay]
    # keep the blocks with the tightest slack -- those are the ones that
    # contend, and a subset of slack-rich blocks would trivially fit
    order = sorted(range(len(p["blocks"])),
                   key=lambda i: (p["blocks"][i]["due_date"]
                                  - p["blocks"][i]["release_time"]
                                  - p["blocks"][i]["processing_time"]))
    keep = sorted(order[:nblk])
    p["blocks"] = [p["blocks"][i] for i in keep]
    for b in p["blocks"]:
        b["bay_preferences"] = b["bay_preferences"][:nbay]
    for k, b in enumerate(p["blocks"]):
        b["id"] = k
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prob")
    ap.add_argument("nblk", type=int)
    ap.add_argument("--bays", type=int, default=1)
    ap.add_argument("--tl", type=float, default=30.0)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    src = json.load(open(REPO / "data" / "preliminary_test" / f"{a.prob}.json"))
    p = build(src, a.nblk, a.bays)
    sys.path.insert(0, str(REPO / "solver"))
    os.environ["OGC_SEED"] = "7"
    import myalgorithm as ma
    sol = ma.algorithm(p, timelimit=a.tl)
    r = ma._check(p, sol)
    n = len(p["blocks"])
    P = [max(1, int(-(-b["processing_time"] // 1))) for b in p["blocks"]]
    D = [int(round(b["due_date"])) for b in p["blocks"]]
    tardy = 0
    late = 0
    for day, ops in sol["operations"].items():
        for op in ops:
            if op["type"] == "ENTRY":
                i = op["block_id"]
                ex = int(day) + P[i]
                if ex > D[i]:
                    tardy += 1
                    late += ex - D[i]
    print(f"{a.prob} -> {n} blocks, {len(p['bays'])} bays, TL {a.tl:.0f}s")
    print(f"   solver: objective {r.get('objective'):,.0f}  feasible={r.get('feasible')}"
          f"   tardy {tardy} blocks / {late} days")
    if tardy == 0:
        print("   Z1 = 0 -- too easy to be an oracle; keep more blocks or fewer bays")
    else:
        print("   Z1 > 0 -- usable as an oracle target")
    if a.out:
        json.dump(p, open(a.out, "w"))
        print(f"   written: {a.out}")


if __name__ == "__main__":
    main()
