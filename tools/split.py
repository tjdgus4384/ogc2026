#!/usr/bin/env python3
"""One long anneal, or k short ones and keep the best? Same total budget.

The one direction the wall does not close is the anneal's own dynamics, and
today's numbers point at a specific candidate. ordmem measured a child at 2x
its parents' objective climbing to within 6% of a FULL run in a quarter of the
budget (P4 8.00M -> 3.19M in 120s vs 3.02M in 480s), and P3's converged board
earning exactly +0.00% from 120s more. So the tail of the budget buys almost
nothing, while independent draws are spread 8-10% apart.

That arithmetic favours splitting: the min of k draws beats the mean by ~1.03
sigma at k=4 (roughly 9% at our spread), against a ~5% penalty for the shorter
anneal. And the portfolio does NOT already do this -- the driver calls
run_portfolio with multiStart=1 and starts=[one board], so all eight threads
begin from the SAME board and differ only by RNG.

Arms, paired on the same instance and replicate, identical total seconds:
    A   1 x B      one long anneal from one seed
    B2  2 x B/2    keep the best
    B4  4 x B/4    keep the best
Each restart gets its OWN seed board (a distinct random insertion order through
reconstruct_ordered), which is what makes the draws independent.

    python3 tools/split.py prob_5 600 200 4
"""
import json
import pathlib
import random
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
THR = 8


def main():
    prob_id, tl = sys.argv[1], float(sys.argv[2])
    B = float(sys.argv[3]) if len(sys.argv) > 3 else 200.0
    REP = int(sys.argv[4]) if len(sys.argv) > 4 else 4
    ds = next(d for d in ("preliminary_test", "train_final", "train")
              if (REPO / "data" / d / f"{prob_id}.json").exists())
    prob = json.load(open(REPO / "data" / ds / f"{prob_id}.json"))
    n = len(prob["blocks"])
    sys.path.insert(0, str(REPO / "solver"))
    import myalgorithm as ma
    eng, D, P = ma._fresh(prob, 4)
    hz = eng.horizon()

    def seed_board(r):
        """a fresh greedy board from a random insertion order"""
        rng = random.Random(r)
        order = list(range(n))
        rng.shuffle(order)
        order.sort(key=lambda i: (D[i] - P[i]) + rng.uniform(-6, 6))
        if eng.reconstruct_ordered([-1] * n, order, False, r & 0xFFFF) != -1:
            return None
        eng.build_occ(hz)
        return eng.get_solution()

    def anneal(board, sec, r):
        eng.set_solution(list(board))
        eng.build_occ(hz)
        eng.run_portfolio(THR, sec, 1, 0.05, 0.15, 0.002, 0.9995, r, 1,
                          [eng.get_solution()])
        return eng.objective()[3]

    print(f"{prob_id}  budget {B:.0f}s  {REP} replicates", flush=True)
    rows = []
    for rep in range(REP):
        base = 1000 * (rep + 1)
        s0 = seed_board(base)
        if s0 is None:
            print(f"   rep{rep}: seed failed", flush=True)
            continue
        a = anneal(s0, B, base)
        out = {"A": a}
        for k in (2, 4):
            best = None
            for j in range(k):
                sj = seed_board(base + 17 * (j + 1)) if j else s0
                if sj is None:
                    continue
                v = anneal(sj, B / k, base + 100 * j)
                best = v if best is None or v < best else best
            out[f"B{k}"] = best
        rows.append(out)
        print(f"   rep{rep}: A {out['A']:,.0f}   B2 {out['B2']:,.0f}"
              f" ({(out['B2']-a)/a*100:+.2f}%)   B4 {out['B4']:,.0f}"
              f" ({(out['B4']-a)/a*100:+.2f}%)", flush=True)
    if not rows:
        return
    for k in ("B2", "B4"):
        d = sorted((r[k] - r["A"]) / r["A"] * 100 for r in rows)
        w = sum(1 for x in d if x < 0)
        med = d[len(d) // 2] if len(d) % 2 else (d[len(d)//2-1]+d[len(d)//2])/2
        print(f"   {prob_id} {k}: {w}W/{len(d)-w}L  median {med:+.2f}%"
              + ("   <-- SPLIT WINS" if med < 0 else ""), flush=True)


if __name__ == "__main__":
    main()
