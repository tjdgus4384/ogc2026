#!/usr/bin/env python3
"""Intra-day ENTRY digraph on a shipped board.

The checker's rule 2b (core.hpp:167-192) allows a same-day, same-bay pair to
block ONE WAY -- the blocked one simply enters first. It is infeasible only if
the directed relation has a CYCLE.

`Engine::pairConflicts` (api.cpp:376-386) instead declares a conflict whenever
EITHER direction is blocked. So every CP-SAT model in this campaign (slice arm,
repack2, bayscratch, exactrepair, headroom, jointcon) has been solving over a
constraint set that forbids one-way same-day pairs the oracle admits.

builds/pc2 relaxes that to `d1 && d2` (both directions) -- but adds no
acyclicity constraint, so it becomes UNSOUND on batches of 3+: a 3-cycle of
one-way edges passes every pairwise test and the checker rejects the board.

This counts, on our own checker-feasible boards:
    group sizes, one-way edges, two-way (true) conflicts,
    and how many groups have enough one-way structure for a cycle to exist.

    python3 tools/batchgraph.py prob_4 prob_5 prob_6 prob_3
"""
import collections
import json
import pathlib
import sys

from shapely.geometry import Polygon

REPO = pathlib.Path(__file__).resolve().parent.parent
TL = {"prob_3": 240, "prob_4": 480, "prob_5": 600, "prob_6": 900}


def load(pid, seed=7):
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{pid}.json"))
    tag = f"{pid}_tl{TL[pid]}_s{seed}_5a1f16a33c21.json"
    board = json.load(open(REPO / "runs" / "boards" / tag))
    return prob, board["base"], board


def layers_at(prob, i, oi, x, y):
    """World-coordinate layer polygons, kernel normalisation (myalgorithm:95)."""
    L = prob["blocks"][i]["shape"][oi]["layers"]
    rx, ry = L[0][0]
    return [Polygon([(vx - rx + x, vy - ry + y) for vx, vy in lay])
            for lay in L]


def blocks_descent(resident, newcomer):
    """Does `resident` (already resting) block `newcomer`'s crane descent?

    crane::entryOK / utils.check_entry: newcomer layer k is obstructed by
    resident layer j >= k, positive-area overlap only.
    """
    for k, pk in enumerate(newcomer):
        for j in range(k, len(resident)):
            if pk.intersection(resident[j]).area > 1e-9:
                return True
    return False


def report(pid):
    prob, base, meta = load(pid)
    n = len(prob["blocks"])
    groups = collections.defaultdict(list)
    for i in range(n):
        bay, oi, x, y, e, f, pl = base[i * 7:i * 7 + 7]
        if pl:
            groups[(bay, e)].append((i, oi, x, y))

    geo = {}
    sizes = collections.Counter()
    one_way = two_way = 0
    cyc_capable = 0          # groups where a directed cycle is possible
    edges_by_group = []
    for key, mem in sorted(groups.items()):
        sizes[len(mem)] += 1
        if len(mem) < 2:
            continue
        for (i, oi, x, y) in mem:
            if i not in geo:
                geo[i] = layers_at(prob, i, oi, x, y)
        edges = set()
        for a in range(len(mem)):
            for b in range(len(mem)):
                if a == b:
                    continue
                ia, ib = mem[a][0], mem[b][0]
                if blocks_descent(geo[ia], geo[ib]):
                    edges.add((ia, ib))          # ia blocks ib -> ib first
        g1 = g2 = 0
        for a in range(len(mem)):
            for b in range(a + 1, len(mem)):
                ia, ib = mem[a][0], mem[b][0]
                fw, bw = (ia, ib) in edges, (ib, ia) in edges
                if fw and bw:
                    g2 += 1
                elif fw or bw:
                    g1 += 1
        one_way += g1
        two_way += g2
        if len(mem) >= 3 and g1 >= 2:
            cyc_capable += 1
        if g1 or g2:
            edges_by_group.append((key, len(mem), g1, g2))

    tot = sum(sizes.values())
    multi = sum(c for s, c in sizes.items() if s >= 2)
    big = sum(c for s, c in sizes.items() if s >= 3)
    print(f"{pid}  obj {meta['objective']:,.0f}  feasible={meta['feasible']}")
    print(f"  (bay,day) entry groups: {tot}   size>=2: {multi}   size>=3: {big}"
          f"   max {max(sizes)}")
    print(f"  same-day pairs blocking ONE way  : {one_way}"
          f"    <- allowed by the checker, FORBIDDEN by pairConflicts")
    print(f"  same-day pairs blocking BOTH ways: {two_way}"
          f"    <- true conflicts (2-cycles)")
    print(f"  groups where a 3+ cycle is possible (size>=3, >=2 one-way edges):"
          f" {cyc_capable}    <- pc2 without ranks is unsound here")
    for key, sz, g1, g2 in edges_by_group[:12]:
        print(f"     bay {key[0]} day {key[1]:>3}  size {sz}"
              f"  one-way {g1}  two-way {g2}")


if __name__ == "__main__":
    for pid in (sys.argv[1:] or ["prob_4"]):
        report(pid)
        print()
