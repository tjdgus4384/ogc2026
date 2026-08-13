#!/usr/bin/env python3
"""Where does the objective actually live? z1/z2/z3 on the shipped boards.

The whole campaign has chased Z1. If Z2 (bay imbalance) or Z3 (preference
regret) carries a large share AND sits far above its own floor, that is an
unexploited axis rather than a closed one. Both have floors we can compute
exactly and independently of geometry:

  z2  perfectly balanced utilisation-weighted workload -> spread 0
  z3  every block on its most preferred bay            -> regret 0

Neither floor is reachable in general (they fight each other and the packing),
but the DISTANCE to them bounds what any scheduler could still win there.

    python3 tools/zsplit.py
"""
import json, pathlib, sys
REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
from board import get_board

for pid, tl in (("prob_3", 240), ("prob_4", 480), ("prob_5", 600), ("prob_6", 900)):
    base, D, P, eng, hz, prob = get_board(pid, tl, 7)
    eng.set_solution(base); eng.build_occ(hz)
    z1, z2, z3, tot = eng.objective()
    W = prob["weights"]; n = len(prob["blocks"])
    c1, c2, c3 = W["w1"]*z1, W["w2"]*z2, W["w3"]*z3
    print(f"{pid}  total {tot:,.0f}")
    for nm, z, c in (("z1", z1, c1), ("z2", z2, c2), ("z3", z3, c3)):
        print(f"   {nm} raw {z:>12,.2f}   weighted {c:>14,.0f}"
              f"   {100*c/tot:5.1f}% of objective")
    PREF = [[float(v) for v in b["bay_preferences"]] for b in prob["blocks"]]
    bay = [base[i*7] for i in range(n) if base[i*7+6]]
    reg = sum(max(PREF[i]) - PREF[i][base[i*7]]
              for i in range(n) if base[i*7+6])
    onbest = sum(1 for i in range(n) if base[i*7+6]
                 and PREF[i][base[i*7]] == max(PREF[i]))
    tardy = sum(1 for i in range(n) if base[i*7+6]
                and base[i*7+4] + P[i] > D[i])
    print(f"   -> {onbest}/{n} blocks on a most-preferred bay,"
          f" raw regret {reg:,.0f};  {tardy}/{n} tardy")
