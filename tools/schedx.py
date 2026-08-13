#!/usr/bin/env python3
"""Crossover on the SCHEDULE, not on placements, then re-realise the geometry.

xrecomb failed for a precise reason: it recombined PLACEMENTS, and independent
boards share none, so every mix collides and CP returns one whole parent. But
the objective is a function of (bay, entry) alone -- geometry is pure
feasibility (invariant 6). So recombine where the objective lives and let the
geometry be rebuilt.

This is not the refuted relaxation approach either. F5 killed master/realize
because it realised a schedule invented by a relaxation, which collapsed
(29-48 of 200 on P3). Here both parents are REALISED boards, and a crossover of
two realisable schedules is a far smaller step from realisability than an
invented one.

Crossovers tried, all cheap and deterministic:
  time     blocks entering before t take A, the rest take B (sweep t)
  bay      blocks in a given bay take A, the rest take B
  tardy    the tardy blocks take whichever parent has them punctual

Re-realisation forces (bay, entry) per block via insert_capped with the bay
pinned and the entry capped to exactly that day, in entry order. If every block
seats, the board is genuinely new and its objective is the crossover's.

    python3 tools/schedx.py prob_5 600 7 11
"""
import json
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent


def realise(eng, n, hz, plan, P):
    """plan[i] = (bay, entry) or None. Returns objective or None if it fails."""
    eng.set_solution([0] * (n * 7))
    eng.build_occ(hz)
    order = sorted((i for i in range(n) if plan[i]), key=lambda i: plan[i][1])
    for i in order:
        bay, e = plan[i]
        if not eng.insert_capped(i, e, bay):
            return None
    if any(not eng.get_block(i)[6] for i in range(n) if plan[i]):
        return None
    return eng.objective()


def main():
    prob_id = sys.argv[1]
    tl = float(sys.argv[2])
    sa = int(sys.argv[3])
    sb = int(sys.argv[4])
    sys.path.insert(0, str(REPO / "tools"))
    from board import get_board
    A, D, P, eng, hz, prob = get_board(prob_id, tl, sa)
    n = len(prob["blocks"])
    hits = list((REPO / "runs" / "boards").glob(
        f"{prob_id}_tl{int(tl)}_s{sb}_*.json"))
    if not hits:
        sys.exit(f"no cached board for seed {sb}")
    B = json.loads(hits[0].read_text())["base"]

    def obj_of(flat):
        eng.set_solution(flat)
        eng.build_occ(hz)
        return eng.objective()[3]
    oa, ob = obj_of(A), obj_of(B)
    best_parent = min(oa, ob)
    print(f"{prob_id}: parents s{sa} {oa:,.0f}   s{sb} {ob:,.0f}")

    pa = [(A[i * 7], A[i * 7 + 4]) if A[i * 7 + 6] else None for i in range(n)]
    pb = [(B[i * 7], B[i * 7 + 4]) if B[i * 7 + 6] else None for i in range(n)]
    same = sum(1 for i in range(n) if pa[i] == pb[i])
    print(f"   parents share {same}/{n} (bay, entry) pairs"
          f" -- {100*same/n:.0f}%")

    # sanity: each parent's own schedule must re-realise
    for nm, pl in (("A", pa), ("B", pb)):
        o = realise(eng, n, hz, pl, P)
        print(f"   re-realise {nm}: "
              + (f"{o[3]:,.0f}" if o else "FAILED -- forcing cannot rebuild it"))

    T = max(e for _, e in (p for p in pa if p))
    best = (None, None)
    tried = ok = 0
    plans = []
    for t in range(0, T + 1, max(1, T // 40)):
        plans.append((f"time<{t}", [pa[i] if (pa[i] and pa[i][1] < t) else pb[i]
                                    for i in range(n)]))
    for j in range(len(prob["bays"])):
        plans.append((f"bayA={j}", [pa[i] if (pa[i] and pa[i][0] == j) else pb[i]
                                    for i in range(n)]))
    tardyplan = []
    for i in range(n):
        la = pa[i] and pa[i][1] + P[i] > D[i]
        lb = pb[i] and pb[i][1] + P[i] > D[i]
        tardyplan.append(pb[i] if (la and not lb) else pa[i])
    plans.append(("tardy-swap", tardyplan))
    for nm, pl in plans:
        tried += 1
        o = realise(eng, n, hz, pl, P)
        if o is None:
            continue
        ok += 1
        if best[0] is None or o[3] < best[0]:
            best = (o[3], nm)
    eng.set_solution(A)
    eng.build_occ(hz)
    print(f"   {tried} crossovers, {ok} re-realised")
    if best[0] is None:
        print("   => none realised")
    else:
        d = (best[0] - best_parent) / best_parent * 100
        print(f"   => best {best[0]:,.0f} ({best[1]})  vs best parent {d:+.2f}%"
              + ("   <-- BEATS BOTH PARENTS" if d < -1e-9 else ""))


if __name__ == "__main__":
    main()
