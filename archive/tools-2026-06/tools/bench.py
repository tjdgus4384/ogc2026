"""
bench.py -- run baseline_greedy on synthetic instances, collect design-relevant metrics.

Metrics per instance:
  feasible, stage, obj, obj1/2/3, elapsed
  LB0 = sum max(0, R+P-D)            (per-block tardiness floor, MODEL F6)
  gap1 = obj1 - LB0                  (tardiness above the floor = what packing/scheduling must recover)
  kstar = realized peak concurrency per bay (max blocks co-present), from the produced operations
  overhang_frac = fraction of blocks with an orientation whose upper layer bbox exceeds layer0 bbox
                  (proxy for Psi strictly-contains Phi -> interlock has room to help)
"""
import sys, os, json, glob, time, io, contextlib, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASE = ROOT / "ogc2026" / "baseline"
sys.path.insert(0, str(BASE))
import utils, baseline_greedy


def lb0(prob):
    s = 0
    for b in prob["blocks"]:
        s += max(0, b["release_time"] + b["processing_time"] - b["due_date"])
    return s


def overhang_frac(prob):
    n = 0
    for b in prob["blocks"]:
        hit = False
        for o in b["shape"]:
            ls = o["layers"]
            if len(ls) < 2:
                continue
            def bb(l):
                xs = [v[0] for v in l]; ys = [v[1] for v in l]
                return max(xs) - min(xs), max(ys) - min(ys)
            w0, h0 = bb(ls[0])
            for up in ls[1:]:
                wu, hu = bb(up)
                if wu > w0 + 1e-6 or hu > h0 + 1e-6:
                    hit = True
        n += 1 if hit else 0
    return round(n / max(1, len(prob["blocks"])), 2)


def realized_kstar(prob, sol):
    # reconstruct entry/exit per bay, sweep time for peak concurrency
    asg = {}
    for ts, ops in sol["operations"].items():
        for op in ops:
            bid = op["block_id"]
            asg.setdefault(bid, {})["bay"] = op["bay_id"]
            if op["type"] == "ENTRY":
                asg[bid]["e"] = int(ts)
            else:
                asg[bid]["f"] = int(ts)
    m = len(prob["bays"])
    peak = [0] * m
    for j in range(m):
        evs = []
        for a in asg.values():
            if a.get("bay") == j and "e" in a and "f" in a:
                evs.append((a["e"], 1)); evs.append((a["f"], -1))
        evs.sort(key=lambda x: (x[0], x[1]))  # exits(-1) before entries(+1) at same t
        cur = 0
        for _, d in evs:
            cur += d; peak[j] = max(peak[j], cur)
    return max(peak) if peak else 0


def run_one(path, timelimit):
    prob = json.loads(pathlib.Path(path).read_text())
    t0 = time.time()
    buf = io.StringIO()
    crashed = None
    try:
        with contextlib.redirect_stdout(buf):
            sol = baseline_greedy.greedyalgorithm(prob, timelimit)
    except Exception as e:
        crashed = f"{type(e).__name__}: {e}"
        sol = None
    el = time.time() - t0
    rec = {"name": prob["name"], "n": len(prob["blocks"]), "m": len(prob["bays"]),
           "regime": prob["name"].split("_")[4], "elapsed": round(el, 2),
           "lb0": lb0(prob), "overhang": overhang_frac(prob), "crash": crashed}
    if sol is None:
        rec.update({"feasible": False, "stage": -1, "obj": None})
        return rec
    r = utils.check_feasibility(prob, sol)
    rec.update({"feasible": r["feasible"], "stage": r["stage"],
                "obj": r["objective"], "obj1": r["obj1"], "obj2": r["obj2"], "obj3": r["obj3"]})
    if r["feasible"]:
        rec["gap1"] = (r["obj1"] - rec["lb0"])
        rec["kstar"] = realized_kstar(prob, sol)
    return rec


if __name__ == "__main__":
    pat = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "data" / "synth" / "*.json")
    tl = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
    files = sorted(glob.glob(pat))
    recs = []
    for i, f in enumerate(files):
        rec = run_one(f, tl)
        recs.append(rec)
        flag = "OK " if rec["feasible"] else ("CRASH" if rec["crash"] else f"INFEAS s{rec['stage']}")
        print(f"[{i+1:3d}/{len(files)}] {flag:8s} n={rec['n']:<4d} m={rec['m']} {rec['regime']:4s} "
              f"oh={rec['overhang']} k*={rec.get('kstar','-')} obj1={rec.get('obj1','-')} "
              f"lb0={rec['lb0']} el={rec['elapsed']}s")
    out = ROOT / "data" / "synth_bench.json"
    out.write_text(json.dumps(recs, indent=1))
    feas = sum(1 for r in recs if r["feasible"])
    crash = sum(1 for r in recs if r["crash"])
    print(f"\n=== {feas}/{len(recs)} feasible, {crash} crash ===  -> {out}")
