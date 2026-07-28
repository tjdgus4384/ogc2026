"""Anneal-isolated A/B: run ONE anneal arm from an INJECTED identical seed.

Seeding differences are excluded by construction: both engines receive the
same starting solution via set_solution, so any difference here belongs to the
anneal itself (operator mixture, prefilter cost, acceptance dynamics).

Modes:
  seed <pid> <out.json>                      generate the v0-seed once
  arm  <pid> <seed.json> <arm> <seedv> <T>   run one arm, print one JSON line
       arm = exploit (hcFrac 1.0, bigQ 0.0, mask 0x1F)
           | explore (hcFrac 0.75, bigQ 0.06, mask 0xFF)
Engine chosen by OGC_SOLVER (exp/diag-v0 or exp/diag-v1).
"""
import json
import math
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOLVER = pathlib.Path(os.environ.get("OGC_SOLVER", ROOT / "solver")).resolve()
sys.path.insert(0, str(SOLVER))
import myalgorithm as MA   # noqa: E402


def horizon(prob):
    B = prob["blocks"]
    D = [int(round(b["due_date"])) for b in B]
    P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
    return max(D) + max(P) + 40


def mode_seed(pid, out):
    prob = json.loads((ROOT / "data" / "train" / f"prob_{pid}.json").read_text())
    eng = MA._seed_free(prob)
    if isinstance(eng, tuple):               # newer engines: (eng, starts)
        eng = eng[0]
    o = eng.objective()
    pathlib.Path(out).write_text(json.dumps({
        "inst": f"prob_{pid}", "solver": SOLVER.name, "seed_obj": o[3],
        "flat": eng.get_solution()}))
    print(json.dumps({"inst": f"prob_{pid}", "seed_obj": o[3]}))


ARMS = {"exploit": (1.0, 0.0, 0x1F), "explore": (0.75, 0.06, 0xFF)}


def mode_arm(pid, seedfile, arm, seedv, wall):
    prob = json.loads((ROOT / "data" / "train" / f"prob_{pid}.json").read_text())
    seed = json.loads(pathlib.Path(seedfile).read_text())
    hc, bq, mask = ARMS[arm]
    eng = MA._build_engine(prob)
    eng.set_solution(seed["flat"])
    eng.build_occ(horizon(prob))
    # v0 production ALWAYS annealed with the event-local path (_fresh called
    # set_event_local(True)); without this the v0 arm runs the old slow path
    # and the A/B is confounded.
    if hasattr(eng, "set_event_local"):
        eng.set_event_local(True)
    t0 = time.time()
    r = eng.run_alns(float(wall), 0.05, 0.15, 0.002, 0.9995, int(seedv),
                     0, hc, bq, mask)
    dt = time.time() - t0
    h = eng.kind_hist()
    print(json.dumps({
        "inst": f"prob_{pid}", "solver": SOLVER.name, "arm": arm,
        "seedv": int(seedv), "wall": round(dt, 1),
        "iters": int(r[0]), "accepted": int(r[1]), "improved": int(r[2]),
        "failed": int(r[3]), "best": r[4],
        "kind_first_half": h[:16], "kind_second_half": h[16:],
    }))


def mode_port(pid, seedfile, seedv, wall):
    prob = json.loads((ROOT / "data" / "train" / f"prob_{pid}.json").read_text())
    seed = json.loads(pathlib.Path(seedfile).read_text())
    eng = MA._build_engine(prob)
    eng.set_solution(seed["flat"])
    eng.build_occ(horizon(prob))
    if hasattr(eng, "set_event_local"):
        eng.set_event_local(True)
    t0 = time.time()
    eng.run_portfolio(4, float(wall), 1, 0.05, 0.15, 0.002, 0.9995,
                      int(seedv), 1)
    print(json.dumps({"inst": f"prob_{pid}", "solver": SOLVER.name,
                      "arm": "port4", "seedv": int(seedv),
                      "wall": round(time.time() - t0, 1),
                      "best": eng.objective()[3]}))


if __name__ == "__main__":
    if sys.argv[1] == "seed":
        mode_seed(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == "port":
        mode_port(*sys.argv[2:6])
    else:
        mode_arm(*sys.argv[2:7])
