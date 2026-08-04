"""Reproduce the pipeline stage by stage with the gate's decisions logged.

The harness showed prob_29 @T900 collapsing to _trivial (obj 1.53e9) in BOTH
engine builds while staying feasible at 400k @T300. algorithm() swallows every
failure silently by design, so this mirrors _arm_free/_gate with each checker
verdict printed: where exactly does the ladder fall through?

Usage: OGC_SOLVER=<dir> python tools/probe_gate.py <pid> <tl>
"""
import json
import os
import pathlib
import re
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOLVER = pathlib.Path(os.environ.get("OGC_SOLVER", ROOT / "solver")).resolve()
sys.path.insert(0, str(SOLVER))
import myalgorithm as MA   # noqa: E402
import utils               # noqa: E402


def log(**kw):
    print(json.dumps(kw), flush=True)


def check(prob, sol):
    try:
        r = utils.check_feasibility(prob, sol)
    except Exception as e:                            # noqa: BLE001
        return {"feasible": False, "checker_error": str(e)}
    return r


def main():
    pid, tl = sys.argv[1], float(sys.argv[2])
    prob = json.loads((ROOT / "data" / "train" / f"prob_{pid}.json").read_text())
    n = len(prob["blocks"])
    t0 = time.time()
    deadline = t0 + tl - 2.0

    eng = MA._seed_free(prob)
    if eng is None:
        log(stage="seed", ok=False)
        return
    log(stage="seed", obj=eng.objective()[3], wall=round(time.time()-t0, 1))

    sec = max(2.0, deadline - time.time() - 2.5)
    rounds = 1 if sec > 110 else 2
    eng.run_portfolio(MA._THR, sec, rounds, 0.05, 0.15, 0.002, 0.9995, 7, 1)
    log(stage="portfolio", obj=eng.objective()[3], sec=round(sec, 1),
        wall=round(time.time()-t0, 1))

    # ---- _gate, verbatim logic, verbose ----
    sol = MA._recs_to_sol(MA._extract(eng, n))
    res = check(prob, sol)
    log(stage="gate.raw", feasible=bool(res.get("feasible")),
        checker_stage=res.get("stage"),
        violations=[str(v) for v in res.get("violations", [])[:5]])
    if res.get("feasible"):
        return

    for v in res.get("violations", [])[:6]:
        ids = [int(x) for x in re.findall(r"block (\d+)", str(v))[:2]]
        hit = False
        for bi in ids:
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                           (1, 1), (-1, -1), (1, -1), (-1, 1)):
                if eng.nudge_block(bi, dx, dy):
                    hit = True
                    break
            if hit:
                break
    sol = MA._recs_to_sol(MA._extract(eng, n))
    res = check(prob, sol)
    log(stage="gate.nudged", feasible=bool(res.get("feasible")),
        checker_stage=res.get("stage"),
        violations=[str(v) for v in res.get("violations", [])[:5]])
    if res.get("feasible"):
        return

    for sd in (21, 22, 23, 24):
        ids = []
        for v in res.get("violations", [])[:6]:
            for x in re.findall(r"block (\d+)", str(v)):
                if int(x) not in ids:
                    ids.append(int(x))
        moved = 0
        for bi in ids:
            b = eng.get_block(bi)
            if b[6] and eng.move_block(bi, b[0], False, sd):
                moved += 1
        sol = MA._recs_to_sol(MA._extract(eng, n))
        res = check(prob, sol)
        log(stage=f"gate.move{sd}", moved=moved,
            feasible=bool(res.get("feasible")),
            checker_stage=res.get("stage"),
            violations=[str(v) for v in res.get("violations", [])[:3]])
        if res.get("feasible"):
            return
        if not moved:
            break
    log(stage="gate.FAILED", final_obj=eng.objective()[3])


if __name__ == "__main__":
    main()
