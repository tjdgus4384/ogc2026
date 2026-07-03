"""geom_experiment.py -- small experimental solver around the geom decoder.

This is intentionally not wired into myalgorithm.py. It is a compact A/B
surface for the current hypothesis:

  assignment moves should be evaluated by the same crane-aware decoder that
  will realize the final solution.

The solver builds a few cheap diverse seeds, runs geomsearch from the best one,
then returns best-of. If it proves robust on spread tests, the useful pieces can
be promoted into the submission pipeline.
"""

import time


def algorithm(prob_info, timelimit=60):
    import utils
    import solve
    import improve
    import assign_lns
    import geomsearch
    import geompack

    t0 = time.time()
    sols = []

    def take(sol):
        try:
            r = utils.check_feasibility(prob_info, sol)
            if r.get("feasible"):
                sols.append(sol)
        except Exception:
            pass

    try:
        take(solve.serialize_floor(prob_info))
    except Exception:
        pass
    for pol in ("pref", "bal", "best"):
        if time.time() - t0 > timelimit * 0.20:
            break
        try:
            take(solve.solve(prob_info, pol))
        except Exception:
            pass
    if time.time() - t0 < timelimit * 0.35:
        try:
            take(improve.improve(prob_info, max(3.0, timelimit * 0.12)))
        except Exception:
            pass
    if time.time() - t0 < timelimit * 0.45:
        try:
            for s in assign_lns.construct_seeds(
                prob_info, utils,
                time_budget=max(2.0, timelimit * 0.20 - (time.time() - t0)),
                orders=("sharp", "due", "workload"),
            ):
                take(s)
        except Exception:
            pass

    if not sols:
        return {"operations": {}}

    def obj(sol):
        try:
            r = utils.check_feasibility(prob_info, sol)
            return r["objective"] if r.get("feasible") else float("inf")
        except Exception:
            return float("inf")

    best = min(sols, key=obj)
    rem = timelimit * 0.82 - (time.time() - t0)
    if rem > 4.0:
        try:
            g = geomsearch.assign_search(prob_info, best, utils, time_budget=rem, seed=0)
            if obj(g) < obj(best):
                best = g
        except Exception:
            pass

    rem = timelimit * 0.92 - (time.time() - t0)
    if rem > 3.0:
        try:
            gp = geompack.geompolish(prob_info, [best] + sols[:3], utils, time_budget=rem)
            if obj(gp) < obj(best):
                best = gp
        except Exception:
            pass
    return best
