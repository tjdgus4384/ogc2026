#!/usr/bin/env python3
"""Re-judge every gate ever run, against the metric the contest actually uses.

`abgate collect` reports a MEDIAN paired delta and an exact sign test. That is
the right instrument for a broad shift and the wrong one for this solver, for a
reason the ledger already recorded and I then ignored:

    P3  Z1 = 0, 48/48 stable
    P4  152..202 across seeds
    P5  mostly 704..808, but seed 89 gives 746..1,243 and seed 101 gives
        1,237..1,462 -- three runs, three explosions

The failure is not gradual spread, it is a mode that occasionally ignites. At
w1 = 13,333 a single P5 explosion is 8.7M, the size of the whole P5 gap. And a
candidate that removes a one-in-eight explosion shows up in a 12-pair cell as
ten ties and one enormous win: sign test p ~ 0.5, filed as refuted.

The scoring server runs each hidden instance ONCE. What decides our rank is
therefore the mean and the tail of our draw distribution, not its median. So
every verdict in the ledger was read off an instrument aimed at the wrong
statistic, and the raw pairs are all still on disk.

This recomputes, per gate: median delta and sign test (what we used), mean
delta (what the score is), and the tail counts on both arms.

    python3 tools/tail.py            # distribution of baseline draws, then
                                     # every gate re-judged
    python3 tools/tail.py GATE       # one gate, pair by pair
"""
import collections
import json
import math
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
GATES = REPO / "runs" / "abgate"


def sign_p(w, l):
    n = w + l
    if n == 0:
        return 1.0
    k = min(w, l)
    c = sum(math.comb(n, i) for i in range(k + 1))
    return min(1.0, 2.0 * c / (2 ** n))


def load(tag):
    g = GATES / tag
    man = json.load(open(g / "manifest.json"))
    out = []
    for f in sorted(g.glob("pair-*.json")):
        d = json.load(open(f))
        if not (d.get("A") and d.get("B")):
            continue
        if not (d["A"].get("feasible") and d["B"].get("feasible")):
            continue
        out.append(d)
    return man, out


def main():
    tags = sorted(p.name for p in GATES.iterdir() if (p / "manifest.json").exists())
    if len(sys.argv) > 1:
        tags = [t for t in tags if t == sys.argv[1] or t.startswith(sys.argv[1])]

    # ---------- 1. the baseline draw distribution, pooled over every gate
    draws = collections.defaultdict(list)
    for t in tags if len(sys.argv) > 1 else sorted(
            p.name for p in GATES.iterdir() if (p / "manifest.json").exists()):
        man, prs = load(t)
        if man.get("dataset") != "preliminary_test":
            continue
        base_is_shipped = (man["baseDir"].endswith("/solver")
                           and not man.get("baseEnv"))
        for d in prs:
            if base_is_shipped:
                draws[(d["instance"], d["tl"])].append((d["A"]["objective"],
                                                        d["A"]["obj1"],
                                                        d["seed"], t))
    print("== baseline (shipped solver, no env) draws pooled over all gates")
    print(f"{'inst':>4} {'tl':>5} {'N':>4} {'median':>12} {'mean':>12} "
          f"{'p90':>12} {'max':>12}   {'mean/med':>8}  worst seeds")
    for k in sorted(draws):
        v = sorted(draws[k])
        n = len(v)
        obj = [x[0] for x in v]
        med = obj[n // 2]
        mean = sum(obj) / n
        p90 = obj[min(n - 1, int(n * 0.9))]
        worst = collections.Counter(x[2] for x in v[-max(1, n // 10):])
        print(f"{k[0]:>4} {k[1]:>5} {n:>4} {med:>12,.0f} {mean:>12,.0f} "
              f"{p90:>12,.0f} {obj[-1]:>12,.0f}   {mean/med:>7.3f}   "
              f"{', '.join(str(s) for s, _ in worst.most_common(4))}")

    # ---------- 2. every gate, under both instruments
    print()
    print("== every gate: the instrument we used vs the one the score uses")
    print(f"{'gate':<22} {'inst':>4} {'N':>3} {'W/L':>7} {'signP':>7} "
          f"{'median':>8} {'MEAN':>8}   verdict flip?")
    for t in tags:
        man, prs = load(t)
        if man.get("dataset") != "preliminary_test":
            continue
        by = collections.defaultdict(list)
        for d in prs:
            a, b = d["A"]["objective"], d["B"]["objective"]
            by[d["instance"]].append((b - a) / a * 100.0)
            by[0].append((b - a) / a * 100.0)
        for inst in sorted(by):
            v = sorted(by[inst])
            n = len(v)
            if n < 4:
                continue
            w = sum(1 for x in v if x < -1e-9)
            l = sum(1 for x in v if x > 1e-9)
            med = v[n // 2]
            mean = sum(v) / n
            flip = ""
            if med >= -0.01 and mean < -1.0:
                flip = "<== MEDIAN HID A MEAN WIN"
            elif med <= 0.01 and mean > 1.0:
                flip = "<== median hid a mean loss"
            lbl = f"P{inst}" if inst else "ALL"
            print(f"{t:<22} {lbl:>4} {n:>3} {w:>3}/{l:<3} "
                  f"{sign_p(w,l):>7.4f} {med:>+7.2f}% {mean:>+7.2f}%   {flip}")


if __name__ == "__main__":
    main()
