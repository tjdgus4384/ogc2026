#!/usr/bin/env python3
"""compare.py -- version-tagged engine eval + diff, for fast performance analysis.

Run the engine on a fixed instance set at a fixed budget, save per-instance results to results/<tag>.json,
and diff against a reference tag (default: best_known). Keeps comparisons controlled instead of cherry-picked.

  python3 tools/compare.py --tag entryaware --tl 120 --inst 3,4,8,15,27,38
  python3 tools/compare.py --tag entryaware --vs best        # re-diff a saved tag vs best_known (no re-run)
  python3 tools/compare.py --tag new --vs old                # diff two saved tags

Determinism note: engine variance is ~1% at fixed budget (measured), so a single run per instance is usually
enough; pass --reps R to run each instance R times and record min/median/spread.
"""
import sys, os, json, time, argparse, statistics

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "solution", "baseline", "baseline"))
sys.path.insert(0, os.path.join(ROOT, "solution", "dev"))
RES = os.path.join(ROOT, "results")


def run_one(prob, tl):
    import myalgorithm, utils
    t = time.time()
    sol = myalgorithm.algorithm(prob, timelimit=tl)
    dt = time.time() - t
    r = utils.check_feasibility(prob, sol)
    return ({"obj": r["objective"], "z1": r.get("obj1"), "z2": r.get("obj2"), "z3": r.get("obj3"),
             "feas": r["feasible"], "sec": round(dt, 1)} if r["feasible"]
            else {"obj": None, "feas": False, "sec": round(dt, 1)})


def cmd_run(args):
    os.makedirs(RES, exist_ok=True)
    insts = [int(x) for x in args.inst.split(",")]
    out = {"tag": args.tag, "tl": args.tl, "reps": args.reps, "results": {}}
    for pid in insts:
        prob = json.load(open(os.path.join(ROOT, "data", "train", f"prob_{pid}.json")))
        runs = [run_one(prob, args.tl) for _ in range(args.reps)]
        objs = [r["obj"] for r in runs if r["feas"]]
        rec = dict(runs[0])
        if len(objs) > 1:
            rec["obj"] = min(objs); rec["median"] = statistics.median(objs)
            rec["spread_pct"] = round((max(objs) - min(objs)) / min(objs) * 100, 1)
        out["results"][f"prob_{pid}"] = rec
        sp = f" spread={rec.get('spread_pct')}%" if "spread_pct" in rec else ""
        print(f"prob_{pid}: obj={rec['obj']} z1={rec.get('z1')} z2={rec.get('z2')} z3={rec.get('z3')} "
              f"({rec['sec']}s){sp}", flush=True)
    path = os.path.join(RES, f"{args.tag}.json")
    json.dump(out, open(path, "w"), indent=2)
    print(f"saved {path}")
    diff(out["results"], load_ref(args.vs), args.tag, args.vs)


def load_ref(vs):
    if vs == "best":
        bk = json.load(open(os.path.join(ROOT, "data", "best_known.json")))
        return {k: {"obj": v} for k, v in bk.items()}
    p = os.path.join(RES, f"{vs}.json")
    return json.load(open(p))["results"] if os.path.exists(p) else {}


def diff(cur, ref, tag, vsname):
    print(f"\n{'inst':<10}{tag:>14}{vsname:>14}{'delta%':>9}")
    for k in sorted(cur, key=lambda x: int(x.split("_")[1])):
        a = cur[k].get("obj"); b = ref.get(k, {}).get("obj")
        d = f"{(a-b)/b*100:+.1f}%" if (a and b) else "-"
        print(f"{k:<10}{str(a):>14}{str(b):>14}{d:>9}")


def cmd_vs(args):
    diff(json.load(open(os.path.join(RES, f"{args.tag}.json")))["results"], load_ref(args.vs), args.tag, args.vs)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--tl", type=float, default=120)
    ap.add_argument("--inst", default="3,4,8,15,27,38")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--vs", default="best", help="reference: 'best' (best_known) or a saved tag name")
    ap.add_argument("--diff-only", action="store_true", help="don't run; just re-diff a saved tag vs --vs")
    a = ap.parse_args()
    (cmd_vs if a.diff_only else cmd_run)(a)
