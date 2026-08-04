"""Mode-selector (pop vs port) trainer over the 40-instance sweep.

Reads runs/learn/sweep_*.jsonl (one row per instance x mode with the
official-config objective), joins per instance, and LOIO-validates a
tiny classifier choosing the better mode from instance meta features.
Baselines: always-port, always-pop, and the current handmade sec-rule
(pop iff 150 <= sec <= 250, sec ~ TL-derived).
Gate: the learned selector must beat ALL baselines on held-out mean
regret (loss vs the per-instance better mode) to ship.
Usage: selector_train.py
"""
import glob
import json
import math
import os

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

rows = []
for f in glob.glob("runs/learn/sweep_*.jsonl"):
    for line in open(f):
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
per = {}
for r in rows:
    if r.get("obj") is None:
        continue
    per.setdefault(r["prob"], {})[r["mode"]] = (r["obj"], r["tl"],
                                                r["n"])
meta = json.load(open("data/instances.json"))
data = []
for prob, mm in per.items():
    if "pop" not in mm or "port" not in mm:
        continue
    src = meta.get(prob)
    if src is None:
        continue
    opop, tl, n = mm["pop"]
    oport, _, _ = mm["port"]
    f = [src["n"] / 250, src["m"] / 5, src["per_bay"] / 80,
         math.log10(max(src["w1"], 1)) / 5, src["w2"] / 10,
         math.log10(max(src["w3"], 1)) / 3,
         src["w3"] / (src["w1"] + 1e-9) * 100, tl / 900]
    better = min(opop, oport)
    data.append({"prob": prob, "f": f, "tl": tl,
                 "loss_pop": (opop - better) / better,
                 "loss_port": (oport - better) / better})
print(json.dumps({"paired_instances": len(data)}))
if len(data) < 15:
    print("insufficient data -- rerun when sweep completes")
    raise SystemExit


def ridge(X, y, lam=0.3):
    X = np.array(X, dtype=float)
    y = np.array(y, dtype=float)
    X = np.hstack([X, np.ones((len(X), 1))])
    A = X.T @ X + lam * np.eye(X.shape[1])
    return np.linalg.solve(A, X.T @ y)


costs = {"model": [], "all_pop": [], "all_port": [], "sec_rule": []}
for hold in data:
    tr = [d for d in data if d is not hold]
    W = ridge([d["f"] for d in tr],
              [d["loss_pop"] - d["loss_port"] for d in tr])
    x = np.array(hold["f"] + [1.0])
    pred_pop_minus_port = float(x @ W)
    pick = "pop" if pred_pop_minus_port < 0 else "port"
    costs["model"].append(hold[f"loss_{pick}"])
    costs["all_pop"].append(hold["loss_pop"])
    costs["all_port"].append(hold["loss_port"])
    # handmade rule: sec ~ tl - reserve; pop iff 150 <= sec <= 250
    sec = hold["tl"] - min(20.0, 0.12 * hold["tl"])
    rule = "pop" if 150 <= sec <= 250 else "port"
    costs["sec_rule"].append(hold[f"loss_{rule}"])
res = {k: round(100 * sum(v) / len(v), 3) for k, v in costs.items()}
res["model_wins_vs_rule"] = sum(
    1 for a, b in zip(costs["model"], costs["sec_rule"]) if a < b)
res["ties"] = sum(
    1 for a, b in zip(costs["model"], costs["sec_rule"]) if a == b)
print(json.dumps(res))
Wf = ridge([d["f"] for d in data],
           [d["loss_pop"] - d["loss_port"] for d in data])
json.dump([round(float(w), 6) for w in Wf],
          open("runs/learn/selector_weights.json", "w"))
print("final weights saved")
