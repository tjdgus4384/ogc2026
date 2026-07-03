"""eval.py -- the permanent measurement harness for OGC2026.

Run ANY solver on the train set and report feasibility, objective decomposition, our best-known,
the lower bound (if data/lb.json exists), and the gap. This is the instrument every later change is
measured against — no improvement counts unless it shows up here.

Usage:
  python3 tools/eval.py <solver> [--tl 20] [--inst 1,2,5 | --inst all]
  python3 tools/eval.py list                         # list registered solvers

Solvers are callables (prob_info, timelimit) -> solution dict, registered in SOLVERS below.
Best-known objective per instance is persisted to data/best_known.json and updated whenever a run
produces a strictly lower feasible objective, so progress across sessions is cumulative.
"""
import sys, json, glob, pathlib, time, importlib.util

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASELINE = ROOT / "solution" / "baseline" / "baseline"
sys.path.insert(0, str(BASELINE))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "solution" / "dev"))
import utils

BEST_PATH = ROOT / "data" / "best_known.json"
LB_PATH = ROOT / "data" / "lb.json"


# -- solver registry ----------------------------------------------------------
def _baseline(prob, tl):
    import baseline_greedy
    return baseline_greedy.greedyalgorithm(prob, tl)

def _floor_serialize(prob, tl):
    import floor
    return floor.to_sol(floor.serialize(prob))

def _floor_footprint(prob, tl):
    import floor
    return floor.to_sol(floor.footprint_greedy(prob))

def _foot_pref(prob, tl):
    import tradeoff
    return tradeoff.to_sol(tradeoff.footprint_greedy(prob, "pref"))

def _foot_bal(prob, tl):
    import tradeoff
    return tradeoff.to_sol(tradeoff.footprint_greedy(prob, "bal"))

def _foot_best(prob, tl):
    """Per-instance pick of pref vs bal by realized objective (cheap oracle over the two policies)."""
    import tradeoff
    best = None
    for pol in ("pref", "bal"):
        sol = tradeoff.to_sol(tradeoff.footprint_greedy(prob, pol))
        r = utils.check_feasibility(prob, sol)
        if r["feasible"] and (best is None or r["objective"] < best[1]):
            best = (sol, r["objective"])
    return best[0] if best else _floor_footprint(prob, tl)

def _v1(prob, tl):
    import solve
    return solve.solve(prob, "best")

def _v2(prob, tl):
    import improve
    return improve.improve(prob, max(2.0, tl * 0.85))

def _submit(prob, tl):
    import myalgorithm
    return myalgorithm.algorithm(prob, tl)

def _geomexp(prob, tl):
    import geom_experiment
    return geom_experiment.algorithm(prob, tl)

SOLVERS = {
    "baseline": _baseline,
    "serialize": _floor_serialize,
    "footprint": _floor_footprint,
    "pref": _foot_pref,
    "bal": _foot_bal,
    "best2": _foot_best,
    "v1": _v1,
    "v2": _v2,
    "submit": _submit,
    "geomexp": _geomexp,
}


def instances(spec):
    files = sorted(glob.glob(str(ROOT / "data" / "train" / "*.json")),
                   key=lambda p: int(pathlib.Path(p).stem.split("_")[1]))
    if spec in (None, "all"):
        return files
    want = {int(x) for x in spec.split(",")}
    return [f for f in files if int(pathlib.Path(f).stem.split("_")[1]) in want]


def load_json(p):
    return json.loads(pathlib.Path(p).read_text()) if pathlib.Path(p).exists() else {}


def main():
    if len(sys.argv) < 2 or sys.argv[1] == "list":
        print("solvers:", ", ".join(SOLVERS)); return
    solver_name = sys.argv[1]
    tl = 20.0; spec = "all"
    for i, a in enumerate(sys.argv):
        if a == "--tl": tl = float(sys.argv[i + 1])
        if a == "--inst": spec = sys.argv[i + 1]
    solver = SOLVERS[solver_name]
    best = load_json(BEST_PATH); lb = load_json(LB_PATH)

    print(f"solver={solver_name}  tl={tl}s")
    print(f"{'inst':8s} {'n':>4s} {'m':>2s} {'feas':>5s} {'stage':>5s} "
          f"{'Z1':>9s} {'Z2':>5s} {'Z3':>6s} {'obj':>13s} {'best':>13s} {'LB':>11s} {'gap%':>6s} {'sec':>6s}")
    tot_obj = 0.0; n_feas = 0; n_newbest = 0; gaps = []
    for f in instances(spec):
        name = pathlib.Path(f).stem
        prob = json.loads(pathlib.Path(f).read_text())
        n, m = len(prob["blocks"]), len(prob["bays"])
        t0 = time.time()
        try:
            sol = solver(prob, tl); r = utils.check_feasibility(prob, sol)
        except Exception as e:
            print(f"{name:8s} {n:4d} {m:2d}  EXC  {type(e).__name__}: {e}"); continue
        el = time.time() - t0
        if not r["feasible"]:
            print(f"{name:8s} {n:4d} {m:2d} {'NO':>5s} {r['stage']:5d} "
                  f"{'-':>9s} {'-':>5s} {'-':>6s} {'-':>13s} {'':>13s} {'':>11s} {'':>6s} {el:6.1f}")
            continue
        n_feas += 1; obj = r["objective"]; tot_obj += obj
        bk = best.get(name); newbest = bk is None or obj < bk - 1e-6
        if newbest: best[name] = obj; n_newbest += 1
        lbv = lb.get(name); gap = (obj - lbv) / obj * 100 if (lbv and obj > 0) else None
        if gap is not None: gaps.append(gap)
        print(f"{name:8s} {n:4d} {m:2d} {'yes':>5s} {r['stage']:5d} "
              f"{r['obj1']:9.0f} {r['obj2']:5.0f} {r['obj3']:6.0f} {obj:13.0f} "
              f"{(bk if bk else obj):13.0f} {(lbv if lbv else 0):11.0f} "
              f"{(f'{gap:.1f}' if gap is not None else '-'):>6s} {el:6.1f}"
              f"{'  <NEW' if newbest else ''}")
    print(f"\nfeasible {n_feas}/{len(instances(spec))}  total_obj={tot_obj:.0f}  new_best={n_newbest}"
          + (f"  mean_gap={sum(gaps)/len(gaps):.1f}%" if gaps else "  (no LB yet)"))
    pathlib.Path(BEST_PATH).write_text(json.dumps(best, indent=0, sort_keys=True))


if __name__ == "__main__":
    main()
