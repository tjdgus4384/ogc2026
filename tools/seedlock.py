#!/usr/bin/env python3
"""Is _seed_free's `[-1] * n` replay a live seed-lock regression, or cosmetic?

_seed_free builds every candidate with `lockv` (which carries the driver's
partial preference lock) but replays the winning order WITHOUT it:

    if best[1] is not orders[-1]:
        eng.reconstruct_ordered([-1] * n, best[1], False, 7)

VERSION records exactly this symptom as the v5.5 seed-lock repair, and `git log
-S` dates the current line to v6.0 (08-12), so it may be a reintroduction. That
matters only if the unlocked board actually reaches the search, so measure
three things rather than argue:

  1. does the branch fire at all (is best[1] the last order)?
  2. how far does the engine's returned board drift from the locked seeds[0]?
  3. how many blocks land off their locked bay in the board the engine returns?
"""
import json, math, pathlib, sys
REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "solver"))
import myalgorithm as ma

for pid, ds in (("prob_3","preliminary_test"), ("prob_4","preliminary_test"),
                ("prob_5","preliminary_test"), ("prob_6","preliminary_test")):
    p = json.load(open(REPO/"data"/ds/f"{pid}.json"))
    B = p["blocks"]; n = len(B)
    pref = [b["bay_preferences"] for b in B]
    spread = sorted(range(n), key=lambda i: -(max(pref[i]) - min(pref[i])))
    sat = ma._saturation(p)
    asg = None
    if sat < 0.6:
        asg = [-1]*n
        for i in spread[:int(n*0.4)]:
            asg[i] = max(range(len(pref[i])), key=lambda j: pref[i][j])
    eng, seeds = ma._seed_free(p, asg=asg)
    if eng is None or not seeds:
        print(f"{pid}: seeding failed"); continue
    got = eng.get_solution()
    s0 = seeds[0]
    same = (list(got) == list(s0))
    eng.set_solution(list(got)); eng.build_occ(eng.horizon())
    og = eng.objective()[3]
    eng.set_solution(list(s0)); eng.build_occ(eng.horizon())
    o0 = eng.objective()[3]
    off = viol = 0
    if asg:
        locked = [i for i in range(n) if asg[i] >= 0]
        off = sum(1 for i in locked if got[i*7+6] and got[i*7] != asg[i])
        viol = sum(1 for i in locked if s0[i*7+6] and s0[i*7] != asg[i])
    print(f"{pid}: sat={sat:.3f} lock={'yes' if asg else 'no'}"
          f"  engine board == seeds[0]? {same}"
          f"  obj engine {og:,.0f} vs seeds[0] {o0:,.0f}", flush=True)
    if asg:
        print(f"   locked blocks {len([i for i in range(n) if asg[i]>=0])}:"
              f" off-lock in ENGINE board {off}, in seeds[0] {viol}", flush=True)
