#!/usr/bin/env bash
#SBATCH -J popcand-pair
#SBATCH -p titan
#SBATCH -N 1
#SBATCH -n 1
#SBATCH -c 4
#SBATCH --mem=16G
#SBATCH -t 01:20:00
#
# One matched AB/BA pair of the pre-registered population gate. Reads its
# (instance, tl, seed, order) row from pair-gate/manifest.json by
# SLURM_ARRAY_TASK_ID and runs canonical (A) and sealed candidate (B)
# sequentially on this node's cpuset. Never submit directly; use
# population_pair_gate.py submit.
set -euo pipefail

REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="${OGC_PY312:-$REPO/.miniforge/envs/ogc2026/bin/python}"
WORK="$REPO/runs/release/population-candidate"
GATE="${OGC_PAIR_GATE_DIR:-$WORK/pair-gate}"
IDX="${SLURM_ARRAY_TASK_ID:?array only}"

"$PY" - "$REPO" "$WORK" "$GATE" "$IDX" "$PY" <<'PYEOF'
import json, os, pathlib, subprocess, sys, time
repo, work, gate, idx, py = (pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]),
                             pathlib.Path(sys.argv[3]), int(sys.argv[4]),
                             sys.argv[5])
sys.path.insert(0, str(repo / "tools"))
import release_schema as rs

manifest = json.loads((gate / "manifest.json").read_text())
pair = manifest["pairs"][idx]
inst, tl, seed, order = (pair["instance"], pair["tl"], pair["seed"],
                         pair["order"])
prob_path = repo / manifest["dataset"] / f"prob_{inst}.json"
canonical = work / "base"
candidate = work / "candidate"
# checker authority: canonical extract's utils.py for BOTH variants
checker_dir = canonical

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
                  "objective": res.get("objective"),
                  "obj1": res.get("obj1"), "obj2": res.get("obj2"),
                  "obj3": res.get("obj3")}))
'''

def run(solver_dir, label):
    env = {"PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
           "OMP_NUM_THREADS": "4", "OPENBLAS_NUM_THREADS": "1",
           "MKL_NUM_THREADS": "1", "HOME": "/tmp", "PATH": "/usr/bin:/bin",
           "OGC_SEED": str(seed)}
    t0 = time.time()
    try:
        proc = subprocess.run(
            [py, "-c", RUNNER, str(solver_dir), str(checker_dir),
             str(prob_path), str(tl)],
            capture_output=True, text=True, timeout=tl + 60, env=env)
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

variants = [("A", canonical), ("B", candidate)]
if order == "BA":
    variants = [("B", candidate), ("A", canonical)]
results = {}
for label, solver_dir in variants:
    results[label] = run(solver_dir, label)

receipt = {
    "schemaVersion": 1, "idx": idx, "instance": inst, "tl": tl,
    "seed": seed, "order": order,
    "jobId": os.environ.get("SLURM_JOB_ID"),
    "node": os.environ.get("SLURMD_NODENAME"),
    "createdAt": rs.now_iso(),
    "candidateSealSha256": manifest["candidateSealSha256"],
    "testedTreeSha256": manifest["testedTreeSha256"],
    "testedSoSha256": manifest["testedSoSha256"],
    "A": results["A"], "B": results["B"],
}
(gate / f"pair-{idx:02d}.json").write_text(
    json.dumps(receipt, indent=2, sort_keys=True) + "\n")
print(json.dumps({"idx": idx, "instance": inst, "seed": seed,
                  "A": results["A"].get("objective"),
                  "B": results["B"].get("objective")}))
PYEOF
