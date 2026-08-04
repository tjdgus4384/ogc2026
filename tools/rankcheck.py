"""Score a set of objectives the way the competition actually does.

The competition does not rank by objective. Each problem awards a rank
score (408 for first, 408 ranked teams) and the total is the sum over
the six problems, max 2,448. P1 and P2 are ties at 11,280 / 31,368 for
essentially every team, so all available value sits in P3-P6.

That makes "-0.4% on P6" the wrong unit. This converts objectives into
the unit that decides the standing -- ranks -- using the preliminary
FINAL-STANDING leaderboard (data/leaderboard.json, parsed from §5 of
docs/preliminary_final_report.pdf) as the reference distribution.
CAUTION: final-standing minima are NOT the all-team minima; the campaign
targets are the all-time §3 minima in data/prelim_refs.json (P3 64,695 /
P4 1,908,043 / P5 7,718,486 / P6 25,452,695). The finals run on new
hidden instances, so treat the result as the shape of the competition,
not a prediction.

Usage: rankcheck.py P3 P4 P5 P6      (objective values)
       rankcheck.py --gradient       (ranks bought per 1% improvement)
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
LB = json.load(open("data/leaderboard.json"))
NRANK = 408
PROBS = ("P3", "P4", "P5", "P6")


def rank_of(prob, val):
    """1-based rank this objective would take on that problem"""
    better = sum(1 for r in LB if r[prob] < val)
    return better + 1


def score_of(prob, val):
    return max(1, NRANK + 1 - rank_of(prob, val))


if len(sys.argv) > 1 and sys.argv[1] == "--gradient":
    # how many ranks does a 1% objective improvement buy, at our level?
    cur = {"P3": 78705, "P4": 2974220, "P5": 10403961, "P6": 29364081}
    print(f"{'prob':5s} {'objective':>12s} {'rank':>5s} {'score':>6s}"
          f" {'ranks/1%':>9s} {'ranks/5%':>9s}")
    for p in PROBS:
        v = cur[p]
        r0 = rank_of(p, v)
        g1 = r0 - rank_of(p, v * 0.99)
        g5 = r0 - rank_of(p, v * 0.95)
        print(f"{p:5s} {v:12,d} {r0:5d} {score_of(p, v):6d} {g1:9d} {g5:9d}")
    tot = sum(score_of(p, cur[p]) for p in PROBS) + 2 * NRANK
    print(f"\ntotal (P1/P2 assumed 408 each) = {tot}")
    for r in LB[:8]:
        print(f"  rank {r['rank']:>3d} {r['team'][:22]:22s} total {r['total']}")
    raise SystemExit

vals = dict(zip(PROBS, (float(a) for a in sys.argv[1:5])))
tot = 2 * NRANK
print(f"{'prob':5s} {'objective':>12s} {'rank':>5s} {'score':>6s}")
for p in PROBS:
    s = score_of(p, vals[p])
    tot += s
    print(f"{p:5s} {vals[p]:12,.0f} {rank_of(p, vals[p]):5d} {s:6d}")
print(f"TOTAL {tot}  (rank-1 = 2,440, our preliminary submission = 2,295)")
placed = sum(1 for r in LB if r["total"] > tot) + 1
print(f"would place approximately: rank {placed}")
