#!/bin/bash
#SBATCH -J jcdbg2
#SBATCH -N 1
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
OMP_NUM_THREADS=4 taskset -c 0-7 $PY -u - <<'PYEOF'
import json, math, sys, traceback
sys.path.insert(0, 'builds/jc1')
import myalgorithm as ma
prob = json.load(open('data/preliminary_test/prob_6.json'))
cp = ma._cpsat()
B = prob["blocks"]; n = len(B)
R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]
eng, D, P = ma._fresh(prob, 4)
eng.set_solution([0] * (n * 7)); eng.build_occ(eng.horizon())
unplaced = set(range(n)); H = max(D) + 64; day = 0
try:
    while unplaced and day <= H:
        want = [i for i in unplaced if R[i] <= day]
        if not want: day += 1; continue
        CAND, flat, idx = {}, [], []
        for i in want:
            raw = eng.feasible_positions(i, 6, day)
            cs = [tuple(raw[k:k+5]) for k in range(0, len(raw), 5)]
            cs = [c for c in cs if c[4] == day]
            if cs:
                CAND[i] = cs
                for k, c in enumerate(cs):
                    flat += [i, c[0], c[1], c[2], c[3], c[4]]; idx.append((i, k))
        if not CAND: day += 1; continue
        mdl = cp.CpModel(); V, use = {}, {}
        for i in CAND:
            V[i] = [mdl.NewBoolVar("z%d_%d"%(i,k)) for k in range(len(CAND[i]))]
            use[i] = mdl.NewBoolVar("u%d"%i); mdl.Add(sum(V[i]) == use[i])
        prs = eng.pair_conflicts(flat)
        for q in range(0, len(prs), 2):
            (a1,k1),(a2,k2) = idx[prs[q]], idx[prs[q+1]]
            mdl.AddBoolOr([V[a1][k1].Not(), V[a2][k2].Not()])
        wgt = {i: (1000 if (D[i]-P[i])-day <= 0 else max(1, 1000//(1+(D[i]-P[i])-day))) for i in CAND}
        mdl.Maximize(sum(wgt[i]*use[i] for i in CAND))
        sv = cp.CpSolver(); sv.parameters.max_time_in_seconds = 4.0
        sv.parameters.num_search_workers = 2
        st = sv.Solve(mdl)
        if st in (2, 4):
            for i in list(CAND):
                if sv.Value(use[i]):
                    for k, c in enumerate(CAND[i]):
                        if sv.Value(V[i][k]):
                            if eng.place_at(i, c[0], c[1], c[2], c[3], c[4]):
                                unplaced.discard(i)
                            break
        if day % 30 == 0:
            print("day %3d  unplaced %3d  cp status %s" % (day, len(unplaced), st))
        day += 1
    print("END: day", day, "unplaced", len(unplaced))
    if unplaced and len(unplaced) < 15:
        for i in sorted(unplaced):
            print("  stuck blk", i, "R", R[i], "D", D[i], "P", P[i])
except Exception:
    traceback.print_exc()
PYEOF
