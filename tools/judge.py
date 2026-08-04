"""The judgment entry point. Three verdicts, one tool, sources annotated.

  judge.py p36 TAG [TAG...]   position verdict: P3-P6 official-TL objectives
                              -> gap to the ALL-TIME prelim minima (report §3,
                              data/prelim_refs.json) + rank/score conversion
                              against the FINAL-STANDING leaderboard (§5,
                              data/leaderboard.json). Reads runs/TAG/prob_N.json
                              (fresh) or results/TAG.json (frozen).
  judge.py pair TAGA TAGB     robustness verdict: per-instance paired
                              comparison of two frozen sweeps (results/*.json,
                              typically train_final x T600), sign test
                              included. B vs A: negative = B better.
  judge.py rank P3 P4 P5 P6   bare rank conversion (ex-rankcheck).

Sources: data/prelim_refs.json  = §3 all-time per-problem minima -- the
                                  campaign target line (NOT the final table).
         data/leaderboard.json  = §5 final-standing rows (347 teams) -- rank
                                  shape only; its minima sit ABOVE §3's.
Discipline: 1%-scale deltas are draw noise (L2); adopt on 10%-scale or
statistically clear wins (sign test), multi-seed for engine changes.
"""
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
REFS = json.load(open(ROOT / "data" / "prelim_refs.json"))
LB = json.load(open(ROOT / "data" / "leaderboard.json"))
NRANK = 408  # ranked teams in the prelim (report §1); LB holds the 347 full rows
PROBS = ("P3", "P4", "P5", "P6")


def rank_of(prob, val):
    return sum(1 for r in LB if r[prob] < val) + 1


def score_of(prob, val):
    return max(1, NRANK + 1 - rank_of(prob, val))


def load_p36(tag):
    """{P3: rec, ...} from runs/<tag>/ (fresh) or results/<tag>.json (frozen)."""
    out = {}
    rd = ROOT / "runs" / tag
    if rd.is_dir():
        for p in (3, 4, 5, 6):
            f = rd / f"prob_{p}.json"
            if f.exists():
                out[f"P{p}"] = json.loads(f.read_text())
        if out:
            return out
    f = ROOT / "results" / f"{tag}.json"
    if f.exists():
        frozen = json.loads(f.read_text())
        meta = frozen.get("meta", {})
        src = meta.get("data") or meta.get("OGC_DATA")
        if src and src != "preliminary_test":
            print(f"WARNING {tag}: frozen run used data={src}, not preliminary_test "
                  f"-- P3-P6 there are NOT the prelim boards; gap/rank is meaningless")
        runs = frozen["runs"]
        for p in (3, 4, 5, 6):
            if f"prob_{p}" in runs:
                out[f"P{p}"] = runs[f"prob_{p}"]
    return out


def cmd_p36(tags):
    amin = {p: REFS["alltime_min"][p]["objective"] for p in PROBS}
    for tag in tags:
        recs = load_p36(tag)
        if not recs:
            print(f"{tag}: no P3-P6 records in runs/ or results/")
            continue
        tot = 2 * NRANK  # P1/P2 floor-tie assumption
        print(f"== {tag}")
        print(f"   {'prob':4s} {'objective':>12s} {'gap-to-min':>10s} {'rank':>5s} "
              f"{'score':>5s} {'obj1':>7s} {'wall':>7s}")
        for p in PROBS:
            r = recs.get(p)
            if r is None or not r.get("feasible"):
                print(f"   {p:4s} {'INFEASIBLE/missing':>12s}")
                continue
            v = r["objective"]
            gap = (v - amin[p]) / amin[p] * 100
            s = score_of(p, v)
            tot += s
            print(f"   {p:4s} {v:12,.0f} {gap:+9.1f}% {rank_of(p, v):5d} {s:5d} "
                  f"{r.get('obj1', float('nan')):7,.0f} {r.get('wall', 0):6.0f}s")
        placed = sum(1 for r in LB if r["total"] > tot) + 1
        print(f"   TOTAL score {tot} (~rank {placed}; §5 shape, rank-1=2,440)")
    print(f"targets = §3 all-time minima "
          f"{{P3 {amin['P3']:,} / P4 {amin['P4']:,} / P5 {amin['P5']:,} / P6 {amin['P6']:,}}}")


def _runs_of(tag):
    f = ROOT / "results" / f"{tag}.json"
    if not f.exists():
        sys.exit(f"results/{tag}.json not found (freeze with tools/collect.py first)")
    return json.loads(f.read_text())["runs"]


def cmd_pair(ta, tb):
    A, B = _runs_of(ta), _runs_of(tb)
    common = sorted(set(A) & set(B), key=lambda k: int(k.split("_")[1]))
    diffs = []
    bad = []
    for k in common:
        a, b = A[k].get("objective"), B[k].get("objective")
        if not A[k].get("feasible") or not B[k].get("feasible") or not a:
            bad.append(k)
            continue
        diffs.append((k, (b - a) / a * 100))
    W = sum(1 for _, d in diffs if d < -1e-9)
    L = sum(1 for _, d in diffs if d > 1e-9)
    T = len(diffs) - W - L
    n = W + L
    # two-sided binomial sign test, p0=0.5, ties dropped
    p = (sum(math.comb(n, i) for i in range(0, min(W, L) + 1)) * 2 / 2**n) if n else 1.0
    p = min(1.0, p)
    ds = sorted(d for _, d in diffs)
    mean = sum(ds) / len(ds) if ds else float("nan")
    med = ds[len(ds) // 2] if ds else float("nan")
    print(f"pair: {tb} vs {ta}  (negative = {tb} better)")
    print(f"  n={len(diffs)}  W/L/T={W}/{L}/{T}  mean={mean:+.2f}%  median={med:+.2f}%  "
          f"sign-test p={p:.3f}")
    movers = sorted(diffs, key=lambda kv: kv[1])
    for k, d in movers[:3]:
        print(f"    {k:<9s} {d:+7.2f}%")
    if len(movers) > 6:
        print("    ...")
    for k, d in movers[-3:]:
        print(f"    {k:<9s} {d:+7.2f}%")
    if bad:
        print(f"  EXCLUDED (infeasible/missing): {', '.join(bad)}")
    verdict = ("CLEAR WIN" if p < 0.05 and mean < 0 else
               "CLEAR LOSS" if p < 0.05 and mean > 0 else "WASH (draw noise)")
    print(f"  verdict: {verdict}  [discipline: adopt only clear or 10%-scale wins]")


def cmd_rank(vals):
    tot = 2 * NRANK
    print(f"{'prob':5s} {'objective':>12s} {'rank':>5s} {'score':>6s}")
    for p, v in zip(PROBS, vals):
        s = score_of(p, v)
        tot += s
        print(f"{p:5s} {v:12,.0f} {rank_of(p, v):5d} {s:6d}")
    placed = sum(1 for r in LB if r["total"] > tot) + 1
    print(f"TOTAL {tot}  (~rank {placed}; rank-1 = 2,440, our prelim = 2,295)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "p36" and args:
        cmd_p36(args)
    elif cmd == "pair" and len(args) == 2:
        cmd_pair(*args)
    elif cmd == "rank" and len(args) == 4:
        cmd_rank([float(a) for a in args])
    else:
        sys.exit(__doc__)
