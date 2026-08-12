#!/usr/bin/env python3
"""Does a quarter of the budget already rank the seeds the full budget will?

The median-to-best gap is almost entirely Z1, and that Z1 is not gradual noise:
on P5 the baseline runs 704-808 normally but seed 101 lands 1,237-1,462 on all
three of its runs. The grading server rolls ONE seed once, so on a hidden
instance we get whichever mode that seed is in.

Blind restarts are refuted -- the time curve is steep below 1x, so splitting the
budget loses. An adaptive one spends nothing on the runs that are fine, and
needs exactly one thing to be true: a short prefix has to ORDER the seeds the
way the full run would. This measures that with the REAL pipeline, not a proxy
-- myalgorithm.algorithm() at a quarter TL and at the full TL, same seed, and
the rank correlation between them.

If the correlation is high, a run can be judged at 25% and re-rolled on
evidence. If it is low, the outcome is decided later than any restart could act
and the axis is closed.

Each (seed, budget) runs in its own process on its own 4-core cpuset, the way
the gate does, so the timings are the graded ones.

    python3 tools/earlysignal.py SOLVERDIR prob_5 FULL_TL seeds...
"""
import json
import os
import pathlib
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

REPO = pathlib.Path(__file__).resolve().parent.parent

RUNNER = r'''
import json, sys
solver, prob_path, tl, seed = sys.argv[1:5]
sys.path.insert(0, solver)
prob = json.load(open(prob_path))
import myalgorithm
sol = myalgorithm.algorithm(prob, timelimit=float(tl))
import importlib.util
spec = importlib.util.spec_from_file_location("official_utils", solver + "/utils.py")
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)
res = mod.check_feasibility(prob, sol)
print(json.dumps({"obj": res.get("objective"), "z1": res.get("obj1"),
                  "feasible": res.get("feasible")}))
'''


def run(py, solver, prob_path, tl, seed, cpus):
    env = {"PYTHONNOUSERSITE": "1", "OMP_NUM_THREADS": "4", "HOME": "/tmp",
           "PATH": "/usr/bin:/bin", "OGC_SEED": str(seed)}
    p = subprocess.run(["taskset", "-c", cpus, py, "-c", RUNNER,
                        solver, prob_path, str(tl), str(seed)],
                       capture_output=True, text=True, env=env, timeout=tl + 180)
    out = None
    for line in p.stdout.splitlines():
        try:
            out = json.loads(line)
        except ValueError:
            continue
    return out or {"obj": None, "z1": None, "feasible": False}


def main():
    solver_dir, prob_id, full = sys.argv[1], sys.argv[2], float(sys.argv[3])
    seeds = [int(s) for s in sys.argv[4].split(",")]
    solver = str(REPO / solver_dir)
    prob_path = str(REPO / "data" / "preliminary_test" / f"{prob_id}.json")
    py = str(REPO / ".miniforge" / "envs" / "ogc2026" / "bin" / "python")
    cpus = sorted(os.sched_getaffinity(0))
    slots = [",".join(str(c) for c in cpus[i * 4:i * 4 + 4])
             for i in range(len(cpus) // 4)]
    jobs = [(s, full * 0.25) for s in seeds] + [(s, full) for s in seeds]
    res = {}
    for base in range(0, len(jobs), len(slots)):
        wave = jobs[base:base + len(slots)]
        with ThreadPoolExecutor(max_workers=len(wave)) as pool:
            futs = [pool.submit(run, py, solver, prob_path, tl, sd, slots[i])
                    for i, (sd, tl) in enumerate(wave)]
            for (sd, tl), f in zip(wave, futs):
                res[(sd, round(tl))] = f.result()

    q, F = round(full * 0.25), round(full)
    print(f"{prob_id}: quarter TL {q}s vs full {F}s, {len(seeds)} seeds")
    print(f"   {'seed':>5} {'quarter obj':>14} {'q Z1':>7} {'full obj':>14} {'f Z1':>7}")
    pairs = []
    for sd in seeds:
        a, b = res.get((sd, q), {}), res.get((sd, F), {})
        if not (a.get("feasible") and b.get("feasible")):
            print(f"   {sd:>5}  incomplete")
            continue
        print(f"   {sd:>5} {a['obj']:14,.0f} {a['z1']:7,.0f} "
              f"{b['obj']:14,.0f} {b['z1']:7,.0f}")
        pairs.append((a["obj"], b["obj"], a["z1"], b["z1"]))

    if len(pairs) < 4:
        return

    def spearman(a, b):
        ra = {v: i for i, v in enumerate(sorted(a))}
        rb = {v: i for i, v in enumerate(sorted(b))}
        x, y = [ra[v] for v in a], [rb[v] for v in b]
        m = len(x)
        mx, my = sum(x) / m, sum(y) / m
        num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
        den = (sum((xi - mx) ** 2 for xi in x) * sum((yi - my) ** 2 for yi in y)) ** 0.5
        return num / den if den else 0.0

    print(f"   rank correlation quarter->full   objective {spearman([p[0] for p in pairs], [p[1] for p in pairs]):+.2f}"
          f"   Z1 {spearman([p[2] for p in pairs], [p[3] for p in pairs]):+.2f}")
    # what an oracle that keeps the best quarter-run would have got
    best_q = min(pairs, key=lambda p: p[0])
    med = sorted(p[1] for p in pairs)[len(pairs) // 2]
    print(f"   picking the seed that led at 25% yields {best_q[1]:,.0f} "
          f"vs median {med:,.0f}  ({(best_q[1] - med) / med * 100:+.1f}%)")


if __name__ == "__main__":
    main()
