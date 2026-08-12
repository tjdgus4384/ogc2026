#!/bin/bash
#SBATCH -J jc2seed
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
# jc2 (v1 constructor: preference-aware objective + bottom-left tiebreak)
# vs jc1's v0, at the SEED level where v0 lost P4/P5. Deterministic, minutes.
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/jc2
i=0
for p in prob_4 prob_5 prob_6; do
  c=$((i*8)); OMP_NUM_THREADS=4 taskset -c $c-$((c+7)) \
    $PY -u - "$p" <<'PYEOF' > runs/jc2/$p.txt 2>&1 &
import json, sys, time
pid = sys.argv[1]
prob = json.load(open("data/preliminary_test/%s.json" % pid))
out = {}
for d in ("builds/jc1", "builds/jc2"):
    sys.path.insert(0, d)
    for m in [m for m in list(sys.modules) if m in ("myalgorithm", "sscore", "utils")]:
        del sys.modules[m]
    import myalgorithm as ma
    t0 = time.time()
    jc = ma._jointcon(prob)
    if jc is None:
        out[d] = None
        print("%s: constructor returned None  [%.0fs]" % (d, time.time() - t0))
    else:
        e, D, P = ma._fresh(prob, 4)
        e.set_solution(jc); e.build_occ(e.horizon())
        o = e.objective()
        out[d] = o
        print("%s: %14s  Z1=%.0f Z2=%.0f Z3=%.0f  [%.0fs]"
              % (d, format(o[3], ",.0f"), o[0], o[1], o[2], time.time() - t0))
    sys.path.pop(0)
# and the yardstick
sys.path.insert(0, "solver")
for m in [m for m in list(sys.modules) if m in ("myalgorithm", "sscore", "utils")]:
    del sys.modules[m]
import myalgorithm as ma
eng, seeds = ma._seed_free(prob)
o = eng.objective()
print("seed_free  : %14s  Z1=%.0f" % (format(o[3], ",.0f"), o[0]))
PYEOF
  i=$((i+1))
done
wait
for p in prob_4 prob_5 prob_6; do echo "##### $p"; cat runs/jc2/$p.txt; done
