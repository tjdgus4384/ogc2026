"""overnight_improve.py -- continuous best-known driver for the long horizon.

Loops over instances (worst cost-headroom first) running the full pipeline (improver + LNS-CP-repair) at a
long budget with rotating seeds; records any strictly-better feasible objective into data/best_known.json.
Runs until killed. Cheap insurance: every accepted solution is utils-validated. This squeezes the residual
(long-budget +few% + SA variance across seeds) toward the achievable footprint-disjoint floor.

Usage: python3 explore/overnight_improve.py [per_instance_seconds]
"""
import sys, json, glob, pathlib, time, random
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "solution" / "baseline" / "baseline"))
sys.path.insert(0, str(ROOT / "solution" / "dev"))
import utils, solve

BK = ROOT / "data" / "best_known.json"
PER = float(sys.argv[1]) if len(sys.argv) > 1 else 300.0


def load_bk():
    return json.loads(BK.read_text()) if BK.exists() else {}


def main():
    files = {pathlib.Path(f).stem: f for f in glob.glob(str(ROOT / "data" / "train" / "*.json"))}
    rnd = 0
    while True:
        rnd += 1
        bk = load_bk()
        # worst headroom first: just process by current best-known descending (biggest cost = most to gain)
        order = sorted(files, key=lambda n: -bk.get(n, 0))
        for nm in order:
            prob = json.loads(pathlib.Path(files[nm]).read_text())
            seed = rnd * 97 + hash(nm) % 50
            try:
                import anytime, polypack
                sol = anytime.solve_anytime(prob, PER * 0.82, seed=seed, cp_workers=4)
                sol = polypack.polish_solution(prob, sol, utils, time_budget=PER * 0.18)
                r = utils.check_feasibility(prob, sol)
            except Exception as e:
                print(f"[r{rnd}] {nm} EXC {type(e).__name__}", flush=True); continue
            if not r["feasible"]:
                print(f"[r{rnd}] {nm} INFEAS", flush=True); continue
            bk = load_bk()
            cur = bk.get(nm)
            if cur is None or r["objective"] < cur - 1e-6:
                bk[nm] = r["objective"]; BK.write_text(json.dumps(bk, indent=0, sort_keys=True))
                print(f"[r{rnd}] {nm} NEW BEST {r['objective']:.0f} (was {cur})  total={sum(bk.values()):.0f}", flush=True)
            else:
                print(f"[r{rnd}] {nm} {r['objective']:.0f} (best {cur})", flush=True)


if __name__ == "__main__":
    main()
