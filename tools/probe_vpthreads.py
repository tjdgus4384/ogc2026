"""Throughput of candidate-parallel vp: iters at fixed wall, vp1 vs vp4.

Usage: OGC_SOLVER=<dir> python probe_vpthreads.py <pid> <vpthreads> <wall>
"""
import json
import os
import sys

sys.path.insert(0, os.environ["OGC_SOLVER"])
import myalgorithm as MA  # noqa: E402


def main():
    pid, vpt, wall = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
    prob = json.load(open(
        f"/data_fast/home/tjdgus4384/priv/ogc2026/data/train/prob_{pid}.json"))
    eng, _ = MA._seed_free(prob)
    eng.set_vp_threads(vpt)
    r = eng.run_alns(wall, 0.05, 0.15, 0.002, 0.9995, 7, 0, 1.0, 0.0, 0x1F)
    print(json.dumps({"inst": pid, "vpt": vpt, "iters": int(r[0]),
                      "best": r[4]}))


if __name__ == "__main__":
    main()
