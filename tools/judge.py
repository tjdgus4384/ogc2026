"""The judgment entry point. Three verdicts, one tool, sources annotated.

  judge.py p36 TAG [TAG...]   position verdict: P3-P6 official-TL objectives
                              -> gap to the ALL-TIME prelim minima (report §3,
                              data/prelim_refs.json) + rank/score conversion
                              against the FINAL-STANDING leaderboard (§5,
                              data/leaderboard.json). Reads only immutable
                              results/TAG.json evidence.
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


def _load_frozen(tag):
    f = ROOT / "results" / f"{tag}.json"
    if not f.exists():
        sys.exit(f"results/{tag}.json not found (freeze with tools/collect.py first)")
    payload = json.loads(f.read_text())
    return payload.get("meta", {}), payload.get("runs", {})


def load_p36(tag):
    """Load a complete, frozen, preliminary-test P3-P6 evidence set."""
    meta, runs = _load_frozen(tag)
    if meta.get("data") != "preliminary_test":
        sys.exit(f"{tag}: data={meta.get('data')!r}, expected preliminary_test")
    out = {}
    for p in (3, 4, 5, 6):
        r = runs.get(f"prob_{p}")
        if (not r or not r.get("feasible") or r.get("error")
                or r.get("overrun")):
            sys.exit(f"{tag}: prob_{p} is missing or non-adoptable")
        out[f"P{p}"] = r
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


def _evidence_of(tag):
    return _load_frozen(tag)


def cmd_pair(ta, tb):
    meta_a, A = _evidence_of(ta)
    meta_b, B = _evidence_of(tb)
    comparable = ("data", "data_snapshot_sha256", "timelimit", "pseed",
                  "checker_sha256", "harness_sha256", "instances")
    missing = [k for k in comparable if k not in meta_a or k not in meta_b]
    mismatch = [k for k in comparable if meta_a.get(k) != meta_b.get(k)]
    if meta_a.get("timelimits") != meta_b.get("timelimits"):
        mismatch.append("timelimits")
    if missing or mismatch:
        sys.exit(f"non-comparable evidence: missing={missing}, mismatch={mismatch}")
    if set(A) != set(B):
        sys.exit("non-comparable evidence: instance coverage differs")
    common = sorted(A, key=lambda k: int(k.split("_")[1]))
    diffs = []
    for k in common:
        a, b = A[k].get("objective"), B[k].get("objective")
        if any((not R.get("feasible") or R.get("error") or R.get("overrun"))
               for R in (A[k], B[k])) or not a or b is None:
            sys.exit(f"non-adoptable paired record: {k}")
        diffs.append((k, (b - a) / a * 100))
    W = sum(1 for _, d in diffs if d < -1.0)
    L = sum(1 for _, d in diffs if d > 1.0)
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
    shown = movers if len(movers) <= 6 else movers[:3] + movers[-3:]
    for idx, (k, d) in enumerate(shown):
        if len(movers) > 6 and idx == 3:
            print("    ...")
        print(f"    {k:<9s} {d:+7.2f}%")
    verdict = ("CLEAR WIN" if p < 0.05 and W > L else
               "CLEAR LOSS" if p < 0.05 and L > W else "WASH (draw noise)")
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
