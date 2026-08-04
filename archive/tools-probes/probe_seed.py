"""Measure SEED quality alone, across all 40 instances, for one build.

reconstructOrdered never inherited ctx.eventLocal, so the seeder runs a
different insertion algorithm from the anneal. That flag can only affect
seeding, and an end-to-end run spends minutes annealing on top -- which washes
the seed difference out. Measuring the seed directly is both sharper and
~100x cheaper.

Reports the objective of _seed_free's chosen seed, which is exactly what the
anneal starts from.

Usage: OGC_SOLVER=<dir> python tools/probe_seed.py   -> one JSON line per instance
"""
import json
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOLVER = pathlib.Path(os.environ.get("OGC_SOLVER", ROOT / "solver")).resolve()
sys.path.insert(0, str(SOLVER))
import myalgorithm as MA   # noqa: E402


def main():
    ids = sys.argv[1:] or [str(i) for i in range(1, 41)]
    for pid in ids:
        prob = json.loads((ROOT / "data" / "train" / f"prob_{pid}.json").read_text())
        t0 = time.time()
        try:
            eng = MA._seed_free(prob)
            if eng is None:
                rec = {"inst": f"prob_{pid}", "seed_obj": None, "error": "seed failed"}
            else:
                o = eng.objective()
                rec = {"inst": f"prob_{pid}", "seed_obj": o[3],
                       "z1": o[0], "z2": o[1], "z3": o[2]}
        except BaseException as e:                      # noqa: BLE001
            rec = {"inst": f"prob_{pid}", "seed_obj": None,
                   "error": f"{type(e).__name__}: {e}"}
        rec["solver"] = SOLVER.name
        rec["wall"] = round(time.time() - t0, 2)
        print(json.dumps(rec), flush=True)


if __name__ == "__main__":
    main()
