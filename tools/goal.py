"""Progress toward the goal: ~10% improvement over v1 on almost all instances.

Per instance: baseline = mean objective over the v1 reference draws
(v1e-t300 seeds 7/8/9), candidate = mean over the given candidate tags.
Reports the per-instance delta distribution and how many instances have
reached -10%.

Usage:
    python3 tools/goal.py <cand_tag> [<cand_tag> ...] [--base v1e-t300,v1e-s8-t300,v1e-s9-t300]
"""
import argparse
import json
import pathlib
import statistics as st
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load(tag):
    f = ROOT / "results" / f"{tag}.json"
    if not f.exists():
        sys.exit(f"no results/{tag}.json")
    return {k: v["objective"] for k, v in json.loads(f.read_text())["runs"].items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cands", nargs="+")
    ap.add_argument("--base", default="v1e-t300,v1e-s8-t300,v1e-s9-t300")
    ap.add_argument("--target", type=float, default=-10.0)
    a = ap.parse_args()

    bases = [load(t) for t in a.base.split(",")]
    cands = [load(t) for t in a.cands]
    insts = sorted(bases[0], key=lambda s: int(s.split("_")[1]))

    rows = []
    for k in insts:
        b = st.mean(x[k] for x in bases)
        c = st.mean(x[k] for x in cands if k in x)
        rows.append((k, 100.0 * (c - b) / b))

    hit = sum(1 for _, d in rows if d <= a.target)
    print(f"GOAL: {a.target:.0f}% vs v1 on almost all instances")
    print(f"reached: {hit}/40   improved(<-0.5%): "
          f"{sum(1 for _, d in rows if d < -0.5)}/40   "
          f"mean {st.mean(d for _, d in rows):+.2f}%  "
          f"median {st.median(d for _, d in rows):+.2f}%")
    print(f"cand draws: {len(cands)}  base draws: {len(bases)}\n")
    for k, d in rows:
        bar = "#" * min(40, int(abs(d))) if d < 0 else "-" * min(40, int(abs(d)))
        mark = " <<" if d <= a.target else ""
        print(f"  {k:<9} {d:+7.2f}% {bar}{mark}")


if __name__ == "__main__":
    main()
