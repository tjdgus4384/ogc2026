"""Solve ONE training instance in a fresh process and emit one JSON line.

This is the unit of work for the Slurm array in tools/eval.sbatch. It is
deliberately dumb: one instance, one process, no shared state, so a crash or a
timeout on one instance cannot contaminate another (the June engine had exactly
that failure mode -- see archive/notes-2026-06/SUBMISSIONS.md, p3fix-congested).

Usage:  python tools/run_instance.py <prob_id> <timelimit_seconds> [out.json]
"""
import json
import hashlib
import importlib.util
import os
import pathlib
import resource
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Which build to measure. Defaults to the live solver/; experiments set
# OGC_SOLVER to an exp/<name>/ directory holding its own myalgorithm.py + .so,
# so a candidate is never compared against a mutated baseline.
SOLVER = pathlib.Path(os.environ.get("OGC_SOLVER", ROOT / "solver")).resolve()
sys.path.insert(0, str(SOLVER))
CHECKER_PATH = pathlib.Path(
    os.environ.get("OGC_CHECKER", ROOT / "solver" / "utils.py")).resolve()
_checker_spec = importlib.util.spec_from_file_location("utils", CHECKER_PATH)
if _checker_spec is None or _checker_spec.loader is None:
    raise RuntimeError(f"cannot load canonical checker: {CHECKER_PATH}")
utils = importlib.util.module_from_spec(_checker_spec)
sys.modules["utils"] = utils
_checker_spec.loader.exec_module(utils)

# The candidate imports the already-bound canonical ``utils`` module above;
# a candidate-local utils.py can neither score itself nor alter driver repair.
import myalgorithm    # noqa: E402


def main():
    pid = sys.argv[1]
    tl = float(sys.argv[2])
    out_path = sys.argv[3] if len(sys.argv) > 3 else None

    # OGC_DATA picks the instance set: train (default) or preliminary_test
    # (the REAL P1-P6, published 2026-07-29 with the preliminary report).
    data = os.environ.get("OGC_DATA", "train")
    prob = json.loads((ROOT / "data" / data / f"prob_{pid}.json").read_text())

    t0 = time.time()
    try:
        sol = myalgorithm.algorithm(prob, tl)
        err = None
    except BaseException as e:                       # noqa: BLE001
        sol, err = None, f"{type(e).__name__}: {e}"
    wall = time.time() - t0

    rec = {
        "inst": f"prob_{pid}",
        "solver": SOLVER.name,
        "data": data,
        "seed": int(os.environ.get("OGC_SEED", "7")),
        "checker_sha256": hashlib.sha256(CHECKER_PATH.read_bytes()).hexdigest(),
        "tl": tl,
        "wall": round(wall, 2),
        "overrun": wall > tl,
        "maxrss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        "n": len(prob["blocks"]),
        "m": len(prob["bays"]),
        "error": err,
    }

    if sol is None:
        rec.update(feasible=False, objective=None, obj1=None, obj2=None, obj3=None)
    else:
        res = utils.check_feasibility(prob, sol)
        rec.update(
            feasible=bool(res.get("feasible")),
            objective=res.get("objective"),
            obj1=res.get("obj1"), obj2=res.get("obj2"), obj3=res.get("obj3"),
        )
        if not rec["feasible"]:
            rec["stage"] = res.get("stage")
            rec["violations"] = [str(v) for v in res.get("violations", [])[:3]]

    line = json.dumps(rec)
    print(line, flush=True)
    if out_path:
        pathlib.Path(out_path).write_text(line + "\n")
        if sol is not None and os.environ.get("OGC_SAVE_SOL"):
            pathlib.Path(out_path).with_suffix(".sol.json").write_text(
                json.dumps(sol))


if __name__ == "__main__":
    main()
