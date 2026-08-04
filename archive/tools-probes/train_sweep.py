"""Run one config over a slice of data/train; JSONL results.
Usage: train_sweep.py <mode pop|port> <lo> <hi>"""
import json, math, os, subprocess, sys, time
mode, lo, hi = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
PY = sys.executable
probs = sorted(os.listdir("data/train"),
               key=lambda f: int(f.split("_")[1].split(".")[0]))[lo:hi]
out = open(f"runs/learn/sweep_{mode}_{lo}_{hi}.jsonl", "a")
for pf in probs:
    pn = pf.split(".")[0]
    p = json.load(open(f"data/train/{pf}"))
    n = len(p["blocks"])
    tl = {100: 240, 150: 480, 200: 600}.get(n, 900 if n >= 250 else 480)
    env = dict(os.environ)
    env.update(OGC_SOLVER=f"{ROOT}/solver", OGC_DATA="train",
               OGC_SEED="7", OMP_NUM_THREADS="4",
               OGC_POP="1" if mode == "pop" else "0")
    t0 = time.time()
    r = subprocess.run(
        [PY, "tools/run_instance.py", pn.split("_")[1], str(tl),
         "/dev/null"], env=env, capture_output=True, text=True)
    line = [l for l in r.stdout.splitlines() if '"objective"' in l]
    obj = None
    if line:
        obj = json.loads(line[-1]).get("objective")
    out.write(json.dumps({"prob": pn, "mode": mode, "n": n, "tl": tl,
                          "obj": obj,
                          "wall": round(time.time() - t0, 1)}) + "\n")
    out.flush()
print("done")
