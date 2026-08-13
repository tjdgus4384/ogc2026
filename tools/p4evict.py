#!/usr/bin/env python3
"""Can the board absorb the displacement a punctual entry costs?

p4block measured the eviction set: who cannot stay at their EXACT placement once
a tardy block takes its punctual slot (P4: 1-8 blocks, mean 4). That is only
half the question -- an evictee may be re-placeable somewhere else on its OWN
entry day, which costs nothing in z1. This runs the whole trade:

  clear the tardy block and every resident of one bay's punctual window
  -> seat the tardy block punctually in that bay
  -> re-insert every cleared resident with insert_capped(k, e_k) so none of
     them may enter LATER than it does today (z1 can only improve for them)
  -> report what fails, and the full objective delta of the rearrangement.

A negative delta means a 5-9 block neighbourhood the anneal is not reaching. A
uniformly positive one says the displacement is genuinely unabsorbable, which is
what "the packing has no room" would look like from the inside.

    python3 tools/p4evict.py [prob_4] [480] [7]
"""
import math
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
from board import get_board                                # noqa: E402


def main():
    pid = sys.argv[1] if len(sys.argv) > 1 else "prob_4"
    tl = float(sys.argv[2]) if len(sys.argv) > 2 else 480.0
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 7
    base, D, P, eng, hz, prob = get_board(pid, tl, seed)
    B = prob["blocks"]
    n = len(B)
    m = len(prob["bays"])
    rec = {i: base[i * 7:i * 7 + 7] for i in range(n)}
    eng.set_solution(base)
    eng.build_occ(hz)
    z0 = eng.objective()
    print(f"# {pid} TL{tl:.0f} s{seed}  base z1={z0[0]:.0f} obj={z0[3]:,.0f}")

    def bbox(i, oi):
        vs = [v for lay in B[i]["shape"][oi]["layers"] for v in lay]
        xs = [v[0] for v in vs]
        ys = [v[1] for v in vs]
        return (max(xs) - min(xs)) * (max(ys) - min(ys))

    tardy = [i for i in range(n) if rec[i][6] and rec[i][5] > D[i]]
    print(f"{'blk':>4}{'late':>5}{'bay':>4}{'clear':>6}{'ontime':>7}"
          f"{'delayed':>8}{'lost':>5}{'dz1':>7}{'dobj':>12}")
    wins = []
    for i in sorted(tardy, key=lambda i: -(rec[i][5] - D[i])):
        punct = D[i] - P[i]
        fs = punct + P[i]
        best = None
        for j in range(m):
            win = [k for k in range(n) if rec[k][6] and k != i
                   and rec[k][0] == j and rec[k][4] < fs and punct < rec[k][5]]
            flat = list(base)
            flat[i * 7 + 6] = 0
            for k in win:
                flat[k * 7 + 6] = 0
            eng.set_solution(flat)
            eng.build_occ(hz)
            if not eng.insert_capped(i, punct, j):
                continue
            # re-seat every cleared resident. First try with a cap at its OWN
            # entry day (no new tardiness); if that fails, let it go free.
            # An unplaced block is simply absent from evaluate(), so a board
            # that drops one is NOT comparable -- those rows are marked FAIL.
            delayed, lost = 0, []
            for k in sorted(win, key=lambda k: -bbox(k, rec[k][1]) * P[k]):
                if eng.insert_capped(k, rec[k][4], -1):
                    continue
                if eng.insert_capped(k, -1, -1):
                    delayed += 1
                else:
                    lost.append(k)
            o = eng.objective()
            row = (j, len(win), delayed, len(lost), o[0] - z0[0], o[3] - z0[3])
            if best is None or (len(lost), row[5]) < (best[3], best[5]):
                best = row
        if best is None:
            print(f"{i:>4}{rec[i][5] - D[i]:>5}   - no bay seats it punctually")
            continue
        j, nw, dl, nl, dz1, dobj = best
        tag = "  DROPPED-BLOCKS (obj not comparable)" if nl else ""
        print(f"{i:>4}{rec[i][5] - D[i]:>5}{j:>4}{nw:>6}{nw - dl - nl:>7}"
              f"{dl:>8}{nl:>5}{dz1:>7.0f}{dobj:>12,.0f}{tag}")
        if nl == 0:
            wins.append((i, dz1, dobj))
    print(f"\ncomplete rearrangements (every cleared block re-seated): "
          f"{len(wins)} of {len(tardy)}")
    for i, dz, d in sorted(wins, key=lambda t: t[2]):
        print(f"   block {i}: dz1 {dz:+.0f} days   dobj {d:+,.0f}")


if __name__ == "__main__":
    main()
