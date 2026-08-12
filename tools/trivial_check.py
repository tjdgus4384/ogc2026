#!/usr/bin/env python3
"""Score the last-resort constructor directly, old against new.

_trivial only runs when the checker gate could not legalise anything, but when
it does it decides the whole instance -- measured 5.5e9 on P5 against a normal
1.0e7. It is never exercised by a normal A/B, so compare it head on and verify
both boards against the official checker.

    python3 tools/trivial_check.py builds/m8 builds/f1
"""
import importlib.util
import json
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
RUNNER = r'''
import importlib.util, json, sys
solver, repo, pid = sys.argv[1:4]
sys.path.insert(0, solver)
import myalgorithm as ma
spec = importlib.util.spec_from_file_location("official_utils", repo + "/solver/utils.py")
u = importlib.util.module_from_spec(spec); sys.modules[spec.name] = u
spec.loader.exec_module(u)
prob = json.load(open(f"{repo}/data/preliminary_test/prob_{pid}.json"))
sol = ma._trivial(prob)
res = u.check_feasibility(prob, sol)
print(json.dumps({"feasible": res.get("feasible"), "obj": res.get("objective"),
                  "viol": (res.get("violations") or [])[:1]}))
'''


def main():
    py = str(REPO / ".miniforge/envs/ogc2026/bin/python")
    print(f"{'prob':>5}  {'arm':>10}  {'feasible':>8}  {'objective':>18}")
    for pid in (3, 4, 5, 6):
        vals = {}
        for d in sys.argv[1:]:
            out = subprocess.run([py, "-c", RUNNER, str(REPO / d), str(REPO), str(pid)],
                                 capture_output=True, text=True)
            try:
                r = json.loads(out.stdout.strip().splitlines()[-1])
            except Exception:
                print(f"P{pid} {d}: {out.stderr.strip()[-160:]}")
                continue
            vals[d] = r
            ob = f"{r['obj']:>18,.0f}" if r["obj"] is not None else f"{'INFEASIBLE':>18}"
            print(f"P{pid:>4}  {d.split('/')[-1]:>10}  {str(r['feasible']):>8}  {ob}"
                  + (f"  {str(r['viol'])[:110]}" if r["viol"] else ""))
        if len(vals) == 2:
            a, b = [vals[d]["obj"] for d in sys.argv[1:]]
            if a and b:
                print(f"{'':>5}  {'':>10}  {'':>8}  {(b - a) / a * 100:>17.1f}%")


if __name__ == "__main__":
    main()
