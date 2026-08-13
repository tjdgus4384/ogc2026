#!/usr/bin/env python3
"""rounds=1 (four basins all the way) vs rounds>1 (diversify, then intensify).

Basin diversity is saturated: the driver builds exactly four distinct starts and
_THR = min(4, cpu_count) round-robins them one thread each on the grading
server's four cores. So depth and diversity can only be traded, never both
bought -- and `rounds` is the knob that trades them. Each round anneals
seconds/rounds and RESTARTS EVERY THREAD FROM THE ROUND'S GLOBAL BEST
(api.cpp), so rounds=2 spends the first half exploring four basins and the
second half with all four threads intensifying the winner.

The driver sets `rounds = 1 if sec > 110 else 2`, so every board we care about
runs rounds=1 and this trade has never been measured at our time limits. ordmem
makes it worth asking: a converged basin earns -0.52% (P4) and +0.00% (P3) from
another 120s, which is exactly the condition under which moving three threads
onto the best basin should pay.

Pairing: one _seed_free call per replicate gives all arms the SAME four starts,
and the arms share the anneal seed, so only `rounds` differs. THR is pinned to
the driver's own _THR so the thread:basin ratio matches the grading server.

    python3 tools/rounds.py prob_5 200 4
"""
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
ARMS = (1, 2, 3)


def main():
    prob_id = sys.argv[1]
    T = float(sys.argv[2]) if len(sys.argv) > 2 else 200.0
    REP = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    ds = next(d for d in ("preliminary_test", "train_final", "train")
              if (REPO / "data" / d / f"{prob_id}.json").exists())
    prob = json.load(open(REPO / "data" / ds / f"{prob_id}.json"))
    sys.path.insert(0, str(REPO / "solver"))
    import myalgorithm as ma
    eng, seeds = ma._seed_free(prob)
    if eng is None or not seeds:
        sys.exit("seeding failed")
    starts = [seeds[0], []] + seeds[1:3]
    hz = eng.horizon()
    print(f"{prob_id}  T={T:.0f}s  THR={ma._THR}  {len(starts)} starts"
          f"  {REP} replicates", flush=True)

    rows = []
    for rep in range(REP):
        out = {}
        for r in ARMS:
            eng.set_solution(list(seeds[0]))
            eng.build_occ(hz)
            eng.run_portfolio(ma._THR, T, r, 0.05, 0.15, 0.002, 0.9995,
                              9000 + rep, 1, starts)
            out[r] = eng.objective()[3]
        rows.append(out)
        print("   rep%d: " % rep + "   ".join(
            f"r{r} {out[r]:,.0f}" + ("" if r == 1 else
                                     f" ({(out[r]-out[1])/out[1]*100:+.2f}%)")
            for r in ARMS), flush=True)
    for r in ARMS[1:]:
        d = sorted((x[r] - x[1]) / x[1] * 100 for x in rows)
        w = sum(1 for v in d if v < 0)
        med = d[len(d)//2] if len(d) % 2 else (d[len(d)//2-1]+d[len(d)//2])/2
        print(f"   {prob_id} rounds={r}: {w}W/{len(d)-w}L median {med:+.2f}%"
              + ("   <-- BEATS rounds=1" if med < 0 else ""), flush=True)


if __name__ == "__main__":
    main()
