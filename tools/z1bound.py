#!/usr/bin/env python3
"""A rigorous lower bound on Z1, by energetic reasoning. Ground truth, at last.

Every diagnostic this campaign owns describes our own board, so none of them can
say how much of our tardiness is waste and how much is forced. A valid lower
bound answers that without anyone else's solution -- it is the one piece of
oracle information we can manufacture ourselves.

The argument, standard energetic reasoning applied to punctuality:

  Suppose every block were punctual: block i occupies its footprint for P_i
  consecutive days somewhere inside [R_i, D_i]. For an interval [t1, t2], the
  LEAST time block i can spend inside it, over all punctual placements, is

      ov_i = max(0, min( P_i,  t2-t1+1,  R_i+P_i-t1,  t2-(D_i-P_i) ))

  (left-shift and right-shift arguments; each term is one way of pushing the
  block out of the window as far as its own window allows). Summing area_i*ov_i
  gives mandatory area-days M. Capacity is C = (total bay area) * (t2-t1+1).

  If M > C the interval is over-subscribed and NO punctual schedule exists.
  Relief comes only from making blocks tardy, and delaying one block by one day
  past its punctual entry removes at most max_i(area_i) area-days from the
  window. So

      tardy_days >= (M - C) / max_i(area_i)          and     Z1 >= w1 * that.

  Area is the layer-0 POLYGON area, which understates what a block really
  sterilises (bbox, crane shadow), so the bound stays valid.

Interpretation against the real submitted minimum's implied tardy days:

  bound close to the target  the winners are near optimal and our gap is real
                             waste we can in principle recover;
  bound above the target     our model of the target is wrong -- recheck before
                             chasing it any further;
  bound near zero            area says nothing here and the gap is purely
                             geometric, so stop reaching for capacity arguments.

    python3 tools/z1bound.py [prob_3 prob_4 ...]
"""
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
# our median Z1 in DAYS and the tardy days the real submitted minimum implies
REF = {"prob_3": (4, 4), "prob_4": (227, 161),
       "prob_5": (785, 580), "prob_6": (4231, 3863)}


def parea(vs):
    a = 0.0
    for i in range(len(vs)):
        x1, y1 = vs[i]
        x2, y2 = vs[(i + 1) % len(vs)]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2


def main():
    ids = sys.argv[1:] or ["prob_3", "prob_4", "prob_5", "prob_6"]
    for pid in ids:
        d = json.load(open(REPO / "data" / "preliminary_test" / f"{pid}.json"))
        B = d["blocks"]
        cap = sum(b["width"] * b["height"] for b in d["bays"])
        w1 = d["weights"]["w1"]
        R, D, P, A = [], [], [], []
        for b in B:
            R.append(int(b["release_time"]))
            D.append(int(b["due_date"]))
            P.append(max(1, int(-(-b["processing_time"] // 1))))
            best = None
            for sh in b["shape"]:
                L = sh["layers"][0]
                xs = [v[0] for v in L]
                ys = [v[1] for v in L]
                bb = (max(xs) - min(xs)) * (max(ys) - min(ys))
                pa = parea(L)
                if best is None or bb < best[1]:
                    best = (pa, bb)
            A.append(best[0])
        n = len(B)
        T = max(D)
        amax = max(A)
        best = (0.0, None)
        for t1 in range(0, T + 1):
            for t2 in range(t1, T + 1):
                L = t2 - t1 + 1
                M = 0.0
                for i in range(n):
                    ov = min(P[i], L, R[i] + P[i] - t1, t2 - (D[i] - P[i]))
                    if ov > 0:
                        M += A[i] * ov
                C = cap * L
                if M > C:
                    days = (M - C) / amax
                    if days > best[0]:
                        best = (days, (t1, t2, M / C))
        ours, tgt = REF.get(pid, (None, None))
        lb = best[0]
        print(f"{pid}: Z1 lower bound {lb:8.1f} tardy days  "
              f"(= {w1 * lb:,.0f} objective)")
        if best[1]:
            t1, t2, ratio = best[1]
            print(f"    binding interval [{t1}, {t2}]  demand/capacity {ratio:.3f}")
        else:
            print("    no interval is over-subscribed -- area forces nothing")
        if ours:
            print(f"    ours {ours:6d} d   target implies {tgt:6d} d   "
                  f"bound {lb:6.1f} d"
                  f"   -> target is {'ABOVE' if tgt >= lb else 'BELOW'} the bound"
                  f", our avoidable share <= {max(0.0, ours - lb):.0f} d")


if __name__ == "__main__":
    main()
