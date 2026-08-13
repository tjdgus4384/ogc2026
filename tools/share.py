#!/usr/bin/env python3
"""Sweep the slice-tail share on the boards the finals score actually lives on.

The 2026-08-14 finals row (76,432,335) puts 73% of the total in P2/P7/P8, which
are P6-like: large objective, tardiness-dominated, saturated. On such a board
runs/opsdiag/prob_6.txt reads rate_p = 2983.2 pts/s for the CP re-placement arm
against rate_a = 226.9 for the slice anneal -- a factor of 13 -- while
runs/opsdiag/prob_{4,5}.txt read rate_p = 0.0.

The CP arm only ever runs inside the tail, and the tail is a flat 0.30 * sec
(myalgorithm.py:785) chosen before any of that was measured. So on exactly the
board class that carries the finals, the budget split has never been tested.

Paired on one process per arm at the official TL, so this is the real driver,
not a probe substitute.

    OGC_SHARES=0.30,0.45,0.60 python3 tools/share.py prob_6 900 3
"""
import json, os, pathlib, subprocess, sys, time
REPO = pathlib.Path(__file__).resolve().parent.parent
SH = [x for x in os.environ.get("OGC_SHARES", "0.30,0.45,0.60").split(",")]

pid, tl = sys.argv[1], float(sys.argv[2])
REP = int(sys.argv[3]) if len(sys.argv) > 3 else 3
ds = next(d for d in ("preliminary_test","train_final","train")
          if (REPO/"data"/d/f"{pid}.json").exists())
rows=[]
for rep in range(REP):
    out={}
    for sh in SH:
        env=dict(os.environ, OGC_SLICESHARE=sh, OGC_SEED=str(7+4*rep))
        code=f'''
import json,sys,time,pathlib
sys.path.insert(0,"{REPO}/builds/share")
import myalgorithm as ma
p=json.load(open("{REPO}/data/{ds}/{pid}.json"))
t0=time.time(); sol=ma.algorithm(p,{tl}); w=time.time()-t0
r=ma._check(p,sol)
print(json.dumps({{"obj":r.get("objective") if r else None,
                  "feas":r.get("feasible") if r else None,"wall":w}}))
'''
        r=subprocess.run([str(REPO/".miniforge/envs/ogc2026/bin/python"),"-c",code],
                         capture_output=True,text=True,env=env)
        try: out[sh]=json.loads(r.stdout.strip().splitlines()[-1])
        except Exception: out[sh]={"obj":None,"feas":None,"wall":0}
    rows.append(out)
    b=out[SH[0]]["obj"]
    print(f"   rep{rep} (seed {7+4*rep}): " + "   ".join(
        f"{sh} {out[sh]['obj']:,.0f}" + ("" if sh==SH[0] or not b else
        f" ({(out[sh]['obj']-b)/b*100:+.2f}%)") for sh in SH), flush=True)
for sh in SH[1:]:
    d=sorted((r[sh]["obj"]-r[SH[0]]["obj"])/r[SH[0]]["obj"]*100
             for r in rows if r[sh]["obj"] and r[SH[0]]["obj"])
    if not d: continue
    w=sum(1 for x in d if x<0)
    med=d[len(d)//2] if len(d)%2 else (d[len(d)//2-1]+d[len(d)//2])/2
    print(f"   {pid} share={sh}: {w}W/{len(d)-w}L median {med:+.2f}%"
          + (f"   <-- BEATS {SH[0]}" if med<0 else ""), flush=True)
