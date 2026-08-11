#!/usr/bin/env bash
#SBATCH -J whyfb
#SBATCH -N 1
#SBATCH -c 4
#SBATCH --mem=32G
#SBATCH -t 00:40:00
#
# algorithm() swallows every exception and returns _trivial, so a candidate
# that crashes the solve looks like a merely terrible score. Call _algorithm
# directly to get the traceback, and check the emitted board against the
# official checker either way.
#
#   sbatch -p titan tools/why_fallback.sbatch builds/m1 6 7 300
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
SOLVER="$REPO/$1"; PID="$2"; SEED="$3"; TL="$4"
OMP_NUM_THREADS=4 OGC_SEED="$SEED" OGC_DIAG=1 "$PY" - "$SOLVER" "$REPO" "$PID" "$TL" <<'PYEOF'
import importlib.util, json, sys, time, traceback
solver, repo, pid, tl = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])
sys.path.insert(0, solver)
import myalgorithm as ma
spec = importlib.util.spec_from_file_location("official_utils",
                                              repo + "/solver/utils.py")
u = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = u
spec.loader.exec_module(u)
prob = json.load(open(f"{repo}/data/preliminary_test/prob_{pid}.json"))
t0 = time.time()
try:
    sol = ma._algorithm(prob, timelimit=tl)
except Exception:
    print("=== _algorithm RAISED ===")
    traceback.print_exc()
    raise SystemExit(1)
res = u.check_feasibility(prob, sol)
print(f"P{pid} s{sys.argv[0] and ''}{tl:.0f}s wall={time.time()-t0:.1f} "
      f"feasible={res.get('feasible')} obj={res.get('objective')}")
for v in (res.get("violations") or [])[:8]:
    print("  violation:", v)
PYEOF
