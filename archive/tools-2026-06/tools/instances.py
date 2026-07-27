#!/usr/bin/env python3
"""instances.py -- per-instance metadata + bottleneck map. Pure computation (no solving), fast.

Writes data/instances.json and prints a table. Tells, structurally, which instances are likely Z1-bound
(packing-limited: high area pressure) vs Z2/Z3-bound (assignment-limited: low pressure, big w3). This is
the lever-targeting map: packing improvements move Z1-bound ones, assignment improvements move the rest.

  python3 tools/instances.py
"""
import os, json, math

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def footprint(blk):
    # min AABB area over orientations (smallest footprint the block can take)
    best = None
    for o in blk["shape"]:
        xs = [v[0] for l in o["layers"] for v in l]; ys = [v[1] for l in o["layers"] for v in l]
        a = (max(xs) - min(xs)) * (max(ys) - min(ys))
        best = a if best is None else min(best, a)
    return best or 0


def analyze(pid):
    p = json.load(open(os.path.join(ROOT, "data", "train", f"prob_{pid}.json")))
    B = p["blocks"]; bays = p["bays"]; n = len(B); m = len(bays)
    w = p.get("weights", {}); w1, w2, w3 = w.get("w1", 1), w.get("w2", 1), w.get("w3", 1)
    # NOTE: a time-averaged area-pressure proxy did NOT discriminate (all ~0.05) -- Z1 comes from PEAK
    # concurrency (release/due clustering), not average area. Real bound classification (Z1 vs Z2/Z3) is
    # derived from actual run breakdowns (w1*Z1 vs w2*Z2 vs w3*Z3 shares); see tools/compare.py --breakdown.
    return {"n": n, "m": m, "per_bay": round(n / m, 1), "cpsat_useful": n / m <= 52,
            "w1": w1, "w2": w2, "w3": w3}


def main():
    out = {}
    for pid in range(1, 41):
        out[f"prob_{pid}"] = analyze(pid)
    json.dump(out, open(os.path.join(ROOT, "data", "instances.json"), "w"), indent=1)
    print(f"{'inst':<9}{'n':>4}{'m':>3}{'/bay':>6}{'cpsat':>7}{'w1':>8}{'w2':>5}{'w3':>6}")
    for pid in range(1, 41):
        r = out[f"prob_{pid}"]
        print(f"prob_{pid:<4}{r['n']:>4}{r['m']:>3}{r['per_bay']:>6}{str(r['cpsat_useful']):>7}"
              f"{r['w1']:>8}{r['w2']:>5}{r['w3']:>6}")
    print("\nsaved data/instances.json")


if __name__ == "__main__":
    main()
