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
# standard 10-instance overfit-guard spread, balanced 5/5 across the two regimes:
#   1-20  (size-scaling, Z1/tardiness-heavy): 3,8,12,16,20  -> sizes 100..300, cpsat(3,12)+brkga(8,16,20)
#   21-40 (weight-variety, Z2/Z3-heavy):      24,27,34,36,38 -> Z3-dom(34,24), congested(27,38), degenerate(36)
SPREAD = [3, 8, 12, 16, 20, 24, 27, 34, 36, 38]


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
    insts = (list(range(1, 41)) if args.inst == "all"
             else SPREAD if args.inst == "spread"
             else [int(x) for x in args.inst.split(",")])
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
        beat = update_best(f"prob_{pid}", rec)            # naturally raise best_known whenever beaten
        print(f"prob_{pid}: obj={rec['obj']} z1={rec.get('z1')} z2={rec.get('z2')} z3={rec.get('z3')} "
              f"({rec['sec']}s){sp}{beat}", flush=True)
    path = os.path.join(RES, f"{args.tag}.json")
    json.dump(out, open(path, "w"), indent=2)
    print(f"saved {path}")
    diff(out["results"], load_ref(args.vs), args.tag, args.vs)


_BK = os.path.join(ROOT, "data", "best_known.json")


def update_best(inst, rec):
    """If this result beats best_known[inst], raise it (best_known = live ceiling, auto-maintained).
    Returns a ' NEW BEST ...' suffix for the log, else ''."""
    if not rec.get("feas") or rec.get("obj") is None:
        return ""
    bk = json.load(open(_BK)) if os.path.exists(_BK) else {}
    old = bk.get(inst)
    if old is None or rec["obj"] < old - 1e-9:
        bk[inst] = rec["obj"]
        json.dump(bk, open(_BK, "w"), indent=0, sort_keys=True)
        return f"  NEW BEST (was {old})"
    return ""


def load_ref(vs):
    if vs == "best":
        bk = json.load(open(os.path.join(ROOT, "data", "best_known.json")))
        return {k: {"obj": v} for k, v in bk.items()}
    p = os.path.join(RES, f"{vs}.json")
    return json.load(open(p))["results"] if os.path.exists(p) else {}


def _variance_band(k):
    p = os.path.join(RES, "variance_profile.json")
    if os.path.exists(p):
        v = json.load(open(p)).get(k, {}).get("spread_pct")
        if v is not None:
            return max(2.0, v)            # treat a delta within the instance's measured noise as a tie
    return 2.0


def diff(cur, ref, tag, vsname):
    print(f"\n{'inst':<10}{tag:>14}{vsname:>14}{'delta%':>9}  flag")
    deltas = []; win = tie = lose = 0
    for k in sorted(cur, key=lambda x: int(x.split("_")[1])):
        a = cur[k].get("obj"); b = ref.get(k, {}).get("obj")
        if not (a and b):
            print(f"{k:<10}{str(a):>14}{str(b):>14}{'-':>9}"); continue
        dpct = (a - b) / b * 100; deltas.append(dpct); band = _variance_band(k)
        flag = "~tie" if abs(dpct) <= band else ("WIN" if dpct < 0 else "REGRESS")
        if flag == "WIN": win += 1
        elif flag == "REGRESS": lose += 1
        else: tie += 1
        print(f"{k:<10}{a:>14.0f}{b:>14.0f}{dpct:>+8.1f}%  {flag}")
    if deltas:
        mean = sum(deltas) / len(deltas)
        print(f"\nAGG vs {vsname}: mean delta {mean:+.1f}%  |  WIN {win}  ~tie {tie}  REGRESS {lose}  "
              f"(of {len(deltas)})")


def cmd_vs(args):
    diff(json.load(open(os.path.join(RES, f"{args.tag}.json")))["results"], load_ref(args.vs), args.tag, args.vs)


def cmd_breakdown(args):
    """Classify each instance's bound from a results snapshot: w1*Z1 vs w2*Z2 vs w3*Z3 shares."""
    res = json.load(open(os.path.join(RES, f"{args.tag}.json")))["results"]
    meta = json.load(open(os.path.join(ROOT, "data", "instances.json")))
    print(f"{'inst':<10}{'w1Z1':>12}{'w2Z2':>10}{'w3Z3':>10}  dominant")
    for k in sorted(res, key=lambda x: int(x.split("_")[1])):
        r = res[k]; w = meta.get(k, {})
        if r.get("z1") is None:
            continue
        t1 = (w.get("w1", 1)) * r["z1"]; t2 = (w.get("w2", 1)) * r["z2"]; t3 = (w.get("w3", 1)) * r["z3"]
        dom = max((t1, "Z1"), (t2, "Z2"), (t3, "Z3"))[1]
        print(f"{k:<10}{t1:>12.0f}{t2:>10.0f}{t3:>10.0f}  {dom}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--tl", type=float, default=120)
    ap.add_argument("--inst", default="spread", help="'spread' (10-instance guard), 'all' (1-40), or comma list")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--vs", default="best", help="reference: 'best' (best_known) or a saved tag name")
    ap.add_argument("--diff-only", action="store_true", help="don't run; just re-diff a saved tag vs --vs")
    ap.add_argument("--breakdown", action="store_true", help="classify bound (Z1/Z2/Z3 shares) of a saved tag")
    a = ap.parse_args()
    (cmd_breakdown if a.breakdown else cmd_vs if a.diff_only else cmd_run)(a)
