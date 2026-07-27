"""Freeze a finished Slurm eval into results/<tag>.json and print a summary.

Also compares against another frozen run when given --vs, using the leaderboard's
own scoring shape: the competition ranks per instance (score = R - n_b, §3.3),
so what matters is HOW MANY instances improved, not the mean percentage. The
summary reports both, with wins/ties/losses first.

Usage:
    python tools/collect.py <tag> [--vs <other_tag>] [--tol 0.5]
"""
import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_run(tag):
    d = ROOT / "runs" / tag
    if not d.is_dir():
        sys.exit(f"no runs/{tag}/ -- was the job submitted?")
    recs = {}
    for f in sorted(d.glob("prob_*.json")):
        try:
            r = json.loads(f.read_text())
        except Exception:
            continue
        recs[r["inst"]] = r
    meta = {}
    if (d / "META.json").exists():
        meta = json.loads((d / "META.json").read_text())
    return meta, recs


def freeze(tag, meta, recs):
    out = ROOT / "results" / f"{tag}.json"
    if out.exists():
        print(f"results/{tag}.json exists -- not overwriting (frozen).")
        return out
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"meta": meta, "runs": recs}, indent=1, sort_keys=True))
    print(f"froze results/{tag}.json")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tag")
    ap.add_argument("--vs", default=None, help="frozen tag to compare against")
    ap.add_argument("--tol", type=float, default=0.5,
                    help="percent band counted as a tie (default 0.5)")
    ap.add_argument("--expect", type=int, default=40,
                    help="instances required before freezing (default 40)")
    ap.add_argument("--partial", action="store_true",
                    help="inspect an unfinished run without freezing it")
    a = ap.parse_args()

    meta, recs = load_run(a.tag)
    if not recs:
        sys.exit(f"runs/{a.tag}/ has no completed instances yet.")

    expected = a.expect
    if len(recs) < expected and not a.partial:
        sys.exit(f"runs/{a.tag}/ has {len(recs)}/{expected} instances -- refusing "
                 f"to freeze a partial run (it would later read as a complete "
                 f"one). Wait for the array, or pass --partial to inspect.")

    infeas = sorted(k for k, r in recs.items() if not r.get("feasible"))
    overrun = sorted(k for k, r in recs.items() if r.get("overrun"))
    errs = sorted(k for k, r in recs.items() if r.get("error"))

    print(f"tag={a.tag}  tl={meta.get('timelimit')}s  "
          f"commit={meta.get('solver_commit')}"
          f"{'  DIRTY' if meta.get('solver_dirty') else ''}")
    print(f"instances {len(recs)}/40   INFEASIBLE={infeas or '[]'}   "
          f"OVERRUN={overrun or '[]'}   ERROR={errs or '[]'}")
    if recs:
        mw = max(r["wall"] for r in recs.values())
        mm = max(r.get("maxrss_mb") or 0 for r in recs.values())
        print(f"max wall {mw:.1f}s   max rss {mm:.0f} MB")

    if not a.partial:
        freeze(a.tag, meta, recs)

    if not a.vs:
        return
    ref = ROOT / "results" / f"{a.vs}.json"
    if not ref.exists():
        sys.exit(f"no results/{a.vs}.json to compare against")
    old = json.loads(ref.read_text())["runs"]

    win = tie = loss = 0
    rows, deltas = [], []
    for k in sorted(recs, key=lambda s: int(s.split("_")[1])):
        n, o = recs[k], old.get(k)
        if not o or not n.get("feasible") or not o.get("feasible"):
            rows.append((k, None, "n/a"))
            continue
        d = 100.0 * (n["objective"] - o["objective"]) / o["objective"]
        deltas.append(d)
        verdict = "TIE" if abs(d) <= a.tol else ("WIN" if d < 0 else "LOSS")
        win += verdict == "WIN"; tie += verdict == "TIE"; loss += verdict == "LOSS"
        rows.append((k, d, verdict))

    print(f"\nvs {a.vs}   WIN {win} / TIE {tie} / LOSS {loss}"
          f"   (tie band +-{a.tol}%)")
    if deltas:
        print(f"mean delta {sum(deltas)/len(deltas):+.2f}%   "
              f"median {sorted(deltas)[len(deltas)//2]:+.2f}%")
    print("\n  instance      delta   verdict")
    for k, d, v in rows:
        print(f"  {k:<12} {'   n/a' if d is None else f'{d:+7.2f}%'}   {v}")


if __name__ == "__main__":
    main()
