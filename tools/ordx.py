#!/usr/bin/env python3
"""Crossover on (bay assignment, insertion order) -- the encoding that always realises.

Three routes hit the same wall today, and schedx showed why: the objective lives
on (bay, entry) but REALISATION demands coordinates, and coordinates are
globally entangled, so any crossover that has to name them collides.

The way out is in the objective itself. Z1 = max(0, e+P-D) falls as the entry
day falls; Z2 (bay imbalance) and Z3 (preference regret) depend on the bay
ALONE. So with bays fixed the objective is monotone in the entry days, and the
only reason to enter later is geometric feasibility. The real decision is
therefore

    (bay assignment, insertion order)   -- greedy earliest-day decides the rest

and in THAT encoding a crossover is always realisable: any mix of bays and
ranks is just another sequence to insert. Nothing can collide, because no
coordinate is ever named. It is the one representation that routes around the
entanglement instead of arguing with it.

reconstructOrdered(asg, order, tight, seed) is exactly this primitive and has
been in the kernel all along: asg forces the bay, order fixes the sequence,
-1 means every block seated.

Control: the parents are reconstructed through the SAME greedy, so the
comparison is crossover-vs-parent and not raw-vs-annealed (instrument defect 5,
which cost force.py its first verdict).

    python3 tools/ordx.py prob_5 600 7 11
"""
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent


def enc(flat, n):
    """(bay, rank) per block from a board; rank = order of entry day."""
    bay = [flat[i * 7] if flat[i * 7 + 6] else -1 for i in range(n)]
    e = [flat[i * 7 + 4] if flat[i * 7 + 6] else 10**9 for i in range(n)]
    rk = [0] * n
    for r, i in enumerate(sorted(range(n), key=lambda i: (e[i], i))):
        rk[i] = r
    return bay, rk, e


def build(eng, n, hz, bay, rk):
    """Realise an encoding. Returns (objective, placed) or None."""
    order = sorted(range(n), key=lambda i: (rk[i], i))
    bad = eng.reconstruct_ordered(list(bay), order, False, 7)
    if bad != -1:
        return None
    return eng.objective()[3], sum(eng.get_block(i)[6] for i in range(n))


def main():
    prob_id, tl, sa, sb = sys.argv[1], float(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
    sys.path.insert(0, str(REPO / "tools"))
    from board import get_board
    A, D, P, eng, hz, prob = get_board(prob_id, tl, sa)
    n = len(prob["blocks"])
    hit = list((REPO / "runs" / "boards").glob(f"{prob_id}_tl{int(tl)}_s{sb}_*.json"))
    if not hit:
        sys.exit(f"no cached board for seed {sb}")
    B = json.loads(hit[0].read_text())["base"]

    def obj(flat):
        eng.set_solution(flat)
        eng.build_occ(hz)
        return eng.objective()[3]
    oa, ob = obj(A), obj(B)
    ba, ra, ea = enc(A, n)
    bb, rb, eb = enc(B, n)
    print(f"{prob_id} s{sa}/s{sb}: annealed parents {oa:,.0f} / {ob:,.0f}")
    print(f"   parents agree on {sum(1 for i in range(n) if ba[i]==bb[i])}/{n} bays")

    # control: the SAME greedy on each parent's own encoding
    pa = build(eng, n, hz, ba, ra)
    pb_ = build(eng, n, hz, bb, rb)
    for nm, r, o in (("A", pa, oa), ("B", pb_, ob)):
        print(f"   re-build {nm}: "
              + (f"{r[0]:,.0f} ({r[1]}/{n} placed, {(r[0]-o)/o*100:+.1f}%"
                 f" vs its annealed self)" if r else "FAILED"))
    if not pa or not pb_:
        print("   => encoding does not round-trip; nothing below is meaningful")
        return
    base = min(pa[0], pb_[0])

    plans = []
    T = max(x for x in ea if x < 10**9)
    for t in range(0, T + 1, max(1, T // 30)):
        m = [i for i in range(n) if ea[i] < t]
        plans.append((f"time<{t}", m))
    for j in range(len(prob["bays"])):
        plans.append((f"bayA={j}", [i for i in range(n) if ba[i] == j]))
    plans.append(("A-punctual", [i for i in range(n)
                                 if ea[i] + P[i] <= D[i]]))
    plans.append(("A-tardy", [i for i in range(n) if ea[i] + P[i] > D[i]]))

    best = (None, None, None)
    ok = 0
    for nm, m in plans:
        S = set(m)
        for mode in ("both", "bay", "ord"):
            bx = [ba[i] if (i in S and mode != "ord") else bb[i] for i in range(n)]
            rx = [ra[i] if (i in S and mode != "bay") else rb[i] for i in range(n)]
            r = build(eng, n, hz, bx, rx)
            if r is None or r[1] < n:
                continue
            ok += 1
            if best[0] is None or r[0] < best[0]:
                best = (r[0], f"{nm}/{mode}", (bx, rx))
    print(f"   {len(plans)*3} crossovers, {ok} realised"
          f" ({ok*100//max(1,len(plans)*3)}%)")
    if best[0] is None:
        print("   => none realised")
        return
    d = (best[0] - base) / base * 100
    print(f"   => best {best[0]:,.0f} ({best[1]})  vs best re-built parent"
          f" {d:+.2f}%" + ("   <-- BEATS BOTH" if d < -1e-9 else ""))
    da = (best[0] - min(oa, ob)) / min(oa, ob) * 100
    print(f"      (vs best ANNEALED parent {da:+.2f}% -- expected positive,"
          f" the crossover has not been annealed)")


if __name__ == "__main__":
    main()
