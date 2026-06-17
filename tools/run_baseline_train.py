"""Run baseline on each real train instance, write result JSONL incrementally (survives timeout)."""
import sys, json, glob, pathlib, time, io, contextlib
sys.path.insert(0, "tools")
import bench
import baseline_greedy, utils

ROOT = pathlib.Path(".")
TL = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
out = ROOT / "data" / "train_baseline.jsonl"
files = sorted(glob.glob("data/train/*.json"), key=lambda p: int(pathlib.Path(p).stem.split("_")[1]))
with open(out, "w") as fout:
    for f in files:
        name = pathlib.Path(f).stem
        try:
            prob = json.loads(pathlib.Path(f).read_text())
        except Exception as e:
            rec = {"name": name, "error": "unreadable"}
            fout.write(json.dumps(rec) + "\n"); fout.flush()
            print(name, "UNREADABLE", flush=True); continue
        n = len(prob["blocks"])
        t0 = time.time(); buf = io.StringIO(); crash = None; sol = None
        try:
            with contextlib.redirect_stdout(buf):
                sol = baseline_greedy.greedyalgorithm(prob, TL)
        except Exception as e:
            crash = f"{type(e).__name__}: {e}"
        el = time.time() - t0
        rec = {"name": name, "n": n, "m": len(prob["bays"]), "elapsed": round(el, 1),
               "w1": prob["weights"]["w1"], "crash": crash}
        if sol is not None:
            r = utils.check_feasibility(prob, sol)
            rec.update({"feasible": r["feasible"], "stage": r["stage"], "obj": r["objective"],
                        "z1": r["obj1"], "z2": r["obj2"], "z3": r["obj3"],
                        "lb0": bench.lb0(prob),
                        "kstar": bench.realized_kstar(prob, sol) if r["feasible"] else None})
        fout.write(json.dumps(rec) + "\n"); fout.flush()
        st = rec.get("feasible"); z1 = rec.get("z1")
        print(f"{name} n={n} feas={st} z1={z1} k*={rec.get('kstar')} el={el:.0f}s", flush=True)
print("DONE", flush=True)
