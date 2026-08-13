#!/usr/bin/env python3
"""Run one abgate cell: every pair of one seed, concurrently, A/B sequential.

Invoked by tools/abgate.sbatch. One pair = one (instance, seed): variant A and
variant B solve the same instance at the official TL, back to back, on the same
4-core cpuset. The pair delta is therefore free of node, cpuset and co-tenant
effects -- the confounders that made the 08-09 unpaired screen unreproducible.
"""
import json
import os
import pathlib
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from abgate_seal import file_sha256, tree_seal

# Solve in a fresh process, then score with the checker the gate pins for BOTH
# variants, so a candidate can never be judged by its own copy of utils.py.
RUNNER = r'''
import json, sys, time
solver, checker_dir, prob_path, tl = sys.argv[1:5]
sys.path.insert(0, solver)
prob = json.load(open(prob_path))
import myalgorithm
t0 = time.time()
sol = myalgorithm.algorithm(prob, timelimit=float(tl))
wall = time.time() - t0
import importlib.util
spec = importlib.util.spec_from_file_location("official_utils",
                                              checker_dir + "/utils.py")
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)
res = mod.check_feasibility(prob, sol)
print(json.dumps({"wall": wall, "feasible": res.get("feasible"),
                  "objective": res.get("objective"), "obj1": res.get("obj1"),
                  "obj2": res.get("obj2"), "obj3": res.get("obj3")}))
'''


def run_variant(py, solver_dir, checker_dir, prob_path, tl, seed, cpus, extra):
    env = {"PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
           "OMP_NUM_THREADS": "4", "OPENBLAS_NUM_THREADS": "1",
           "MKL_NUM_THREADS": "1", "HOME": "/tmp", "PATH": "/usr/bin:/bin",
           "OGC_SEED": str(seed)}
    env.update(extra)
    cmd = ["taskset", "-c", cpus, py, "-c", RUNNER,
           solver_dir, checker_dir, prob_path, str(tl)]
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=tl + 120, env=env)
    except subprocess.TimeoutExpired:
        return {"error": "hard-timeout", "wall": time.time() - t0}
    if proc.returncode != 0:
        return {"error": proc.stderr[-400:], "wall": time.time() - t0}
    out = None
    for line in proc.stdout.splitlines():
        try:
            out = json.loads(line)
        except ValueError:
            continue
    return out or {"error": "no-json-output", "wall": time.time() - t0}


def run_pair(py, manifest, pair, cpus):
    expected_prob = pair.get("probSha256")
    if expected_prob and file_sha256(pair["probPath"]) != expected_prob:
        raise RuntimeError(f"problem changed after submission: {pair['probPath']}")
    arms = {"A": (manifest["baseDir"], manifest["baseEnv"]),
            "B": (manifest["candDir"], manifest["candEnv"])}
    order = ["A", "B"] if pair["order"] == "AB" else ["B", "A"]
    out = {}
    for label in order:
        solver_dir, extra = arms[label]
        out[label] = run_variant(
            py, solver_dir, manifest["checkerDir"], pair["probPath"],
            pair["tl"], pair["seed"], cpus, extra)
    return out


def main():
    gate, idx, py = pathlib.Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    manifest = json.loads((gate / "manifest.json").read_text())
    harness_seal = manifest.get("harnessSeal")
    if harness_seal and tree_seal(pathlib.Path(__file__).resolve().parent) != harness_seal:
        sys.exit("gate harness changed after submission")
    seals = manifest.get("seals")
    if seals:
        roots = {"base": manifest["baseDir"],
                 "candidate": manifest["candDir"],
                 "checker": manifest["checkerDir"]}
        for name, root in roots.items():
            actual = tree_seal(root)
            if actual != seals[name]:
                before, after = seals[name], actual
                changed = sorted(key for key in before.keys() & after.keys()
                                 if before[key] != after[key])
                missing = sorted(before.keys() - after.keys())
                added = sorted(after.keys() - before.keys())
                sys.exit(f"{name} tree changed after submission: "
                         f"changed={changed}, missing={missing}, added={added}")
    pairs = [p for p in manifest["pairs"] if p["cell"] == idx]
    cpus = sorted(os.sched_getaffinity(0))
    # 4 pinned cores per pair = the grading envelope; disjoint slots per pair.
    slots = [",".join(str(c) for c in cpus[i * 4:i * 4 + 4])
             for i in range(len(cpus) // 4)]
    # titan mixes 48-core and 40-core nodes, so a cell sized for twelve pairs
    # gets only ten slots on ohm or quant. Exiting there silently dropped a
    # whole instance from a verdict (r1 lost its P6 cell that way). Run the
    # pairs in waves of whatever the node actually granted instead.
    if not slots:
        sys.exit(f"cell {idx}: no 4-core slot on this node")
    results = []
    for base in range(0, len(pairs), len(slots)):
        wave = pairs[base:base + len(slots)]
        with ThreadPoolExecutor(max_workers=len(wave)) as pool:
            futures = [pool.submit(run_pair, py, manifest, p, slots[i])
                       for i, p in enumerate(wave)]
            results += [f.result() for f in futures]
    for pair, res in zip(pairs, results):
        receipt = dict(pair, node=os.environ.get("SLURMD_NODENAME"),
                       jobId=os.environ.get("SLURM_JOB_ID"),
                       A=res["A"], B=res["B"])
        (gate / f"pair-{pair['idx']:03d}.json").write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
