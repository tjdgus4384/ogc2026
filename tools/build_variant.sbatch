#!/usr/bin/env bash
#SBATCH -J build
#SBATCH -N 1
#SBATCH -c 8
#SBATCH --mem=16G
#SBATCH -t 00:20:00
#
# Compile one candidate solver directory in place, then smoke-test it.
#
#   sbatch -p titan tools/build_variant.sbatch builds/m1
#
# Flags are the shipped ones (builds/b53/build.txt). Never build on the
# gateway, and never while a judgment job is queued -- a .so swapped under a
# running eval once produced a wall=0.01s _trivial fallback.
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="${OGC_PY312:-$REPO/.miniforge/envs/ogc2026/bin/python}"
DIR="$REPO/$1"
cd "$DIR"
# This env ships no python-config, so read EXT_SUFFIX from sysconfig instead:
# an empty suffix silently writes a file named "sscore" and leaves the real
# extension stale, which reads as a successful build of unchanged code.
SUF="$("$PY" -c 'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX"))')"
[ -n "$SUF" ] || { echo "empty EXT_SUFFIX"; exit 1; }
rm -f "sscore$SUF"
g++ -O3 -std=c++17 -funroll-loops -fopenmp -march=x86-64-v3 -ffp-contract=off \
    -flto -shared -fPIC $("$PY" -m pybind11 --includes) src/api.cpp \
    -o "sscore$SUF"
echo "built: $(ls -l "sscore$SUF")"
# Smoke test: P1 and P2 are variance-free floor boards, so a correct build
# reproduces their known optima exactly; P3 at a short TL proves the checker
# still accepts what the engine emits after the change.
OMP_NUM_THREADS=4 OGC_SEED=7 "$PY" - "$DIR" "$REPO" <<'PYEOF'
import importlib.util, json, sys, time
solver, repo = sys.argv[1], sys.argv[2]
sys.path.insert(0, solver)
import myalgorithm
spec = importlib.util.spec_from_file_location("official_utils",
                                              repo + "/solver/utils.py")
u = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = u          # dataclass resolution needs it registered
spec.loader.exec_module(u)
for pid, tl in ((1, 60), (2, 120), (3, 60)):
    prob = json.load(open(f"{repo}/data/preliminary_test/prob_{pid}.json"))
    t0 = time.time()
    sol = myalgorithm.algorithm(prob, timelimit=float(tl))
    res = u.check_feasibility(prob, sol)
    print(f"P{pid} tl={tl} wall={time.time()-t0:.1f}s feasible={res.get('feasible')} "
          f"obj={res.get('objective')} viol={res.get('violations')}")
PYEOF
