#!/usr/bin/env bash
#SBATCH -J finalcheck
#SBATCH -N 1
#SBATCH -c 8
#SBATCH --mem=24G
#SBATCH -t 00:15:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
# run FROM THE ZIP CONTENT in a clean dir -- the exact bytes the server gets
WORK=$(mktemp -d)
cd "$WORK"
"$PY" -c "
import zipfile; zipfile.ZipFile('$REPO/submissions/[OGC2026_AlgorithmCode]_MSGA.zip').extractall('.')"
ls -la
OGC_SEED=7 "$PY" -u -c "
import json,sys,time
sys.path.insert(0,'.')
import myalgorithm as ma
# P1 canary (known optimum 11280) + P3 (population path + spec lock + gate)
for pid,tl,want in (('prob_1',60,11280.0),('prob_3',240,None)):
    p=json.load(open('$REPO/data/preliminary_test/%s.json'%pid))
    t0=time.time(); sol=ma.algorithm(p,float(tl)); w=time.time()-t0
    r=ma._check(p,sol)
    ok = r and r.get('feasible')
    obj = r.get('objective') if r else None
    tag = 'OK' if ok and (want is None or obj==want) else 'FAIL'
    print(f'[{tag}] {pid}: wall {w:.1f}/{tl} feasible={ok} obj={obj}'
          + (f' (want {want})' if want else ''), flush=True)
"
rm -rf "$WORK"
echo ZIPRUN_DONE
