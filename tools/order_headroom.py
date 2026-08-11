#!/usr/bin/env python3
"""How much room is there in the intra-day ENTRY order? (measurement, no solve)

The kernel's rule 2/4 demand that EVERY intra-day order of a same-day batch be
legal; the checker demands only that the ONE emitted order replay (utils.py
Stage 2 excludes same-day entrants, Stage 5 validates the listed order). This
script prices that difference on a finished board: for each same-day, same-bay
batch it classifies every pair as

  free       neither blocks the other -- any order works, the kernel was right
  ordered    exactly one direction is blocked -- the kernel rejects placements
             the rules allow, and only one of the two orders may be emitted
  dead       both directions blocked -- genuinely infeasible either way

Cycles among 3+ blocks are reported too, since an order-aware kernel must
reject those (a topological order has to exist).

SELECTION EFFECT -- read the output accordingly. A board built by the strict
kernel can only contain batches that were order-INdependent, so it scores 100%
free by construction (measured: P3 131/131, P4 93/93). That is evidence the
strict rule binds, not evidence the prize is zero; the rejected placements are
where the prize lives and only the engine can enumerate them. On a board from
an order-aware kernel, "ordered > 0" is the direct proof that the new freedom
is actually being used.

    python3 tools/order_headroom.py prob_6 runs/base53-s41/prob_6.sol.json
"""
import collections
import importlib.util
import itertools
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent


def load_checker():
    spec = importlib.util.spec_from_file_location(
        "official_utils", REPO / "solver" / "utils.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def blocked(u, bay, resident, newcomer):
    """Does `resident` resting in the bay block `newcomer`'s descent?"""
    return bool(u.check_entry(bay, [resident], newcomer, fast=True))


def main():
    prob_id, sol_path = sys.argv[1], sys.argv[2]
    u = load_checker()
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    ops = json.load(open(sol_path))["operations"]
    bays = [u.Bay.from_dict(b, i) for i, b in enumerate(prob["bays"])]

    day_bay = collections.defaultdict(list)     # (day, bay) -> [(bid, x, y, ori)]
    for day, evs in ops.items():
        for e in evs:
            if e["type"] == "ENTRY":
                day_bay[(int(day), e["bay_id"])].append(
                    (e["block_id"], e["x"], e["y"], e["orient_idx"]))

    free = ordered = dead = 0
    cyc = 0
    batches = 0
    for (day, bay), members in sorted(day_bay.items()):
        if len(members) < 2:
            continue
        batches += 1
        objs = {bid: u.Block.from_instance(bid, prob, x, y, ori)
                for bid, x, y, ori in members}
        after = collections.defaultdict(set)     # a -> blocks that must follow a
        for a, b in itertools.combinations(objs, 2):
            ab = blocked(u, bays[bay], objs[a], objs[b])   # a resting blocks b
            ba = blocked(u, bays[bay], objs[b], objs[a])
            if ab and ba:
                dead += 1
            elif ab:
                ordered += 1
                after[b].add(a)                  # b must enter before a
            elif ba:
                ordered += 1
                after[a].add(b)
            else:
                free += 1
        # a valid emission order exists iff this precedence graph is acyclic
        indeg = {k: 0 for k in objs}
        for src, dsts in after.items():
            for d in dsts:
                indeg[d] += 1
        queue = [k for k, v in indeg.items() if v == 0]
        seen = 0
        while queue:
            n = queue.pop()
            seen += 1
            for d in after.get(n, ()):
                indeg[d] -= 1
                if indeg[d] == 0:
                    queue.append(d)
        if seen != len(objs):
            cyc += 1

    total = free + ordered + dead
    print(f"{prob_id}: {batches} same-day/same-bay batches, {total} intra-batch pairs")
    if total:
        print(f"  free    {free:5d} ({100*free/total:5.1f}%)  어느 순서든 가능")
        print(f"  ordered {ordered:5d} ({100*ordered/total:5.1f}%)  한 방향만 가능 "
              f"<-- 커널이 잘못 거부하던 것")
        print(f"  dead    {dead:5d} ({100*dead/total:5.1f}%)  어느 순서로도 불가")
    print(f"  순환(위상정렬 불가) 묶음 {cyc}")


if __name__ == "__main__":
    main()
