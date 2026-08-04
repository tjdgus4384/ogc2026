"""Train + LOIO-validate the bay-assignment prior (v0).

Tiny MLP s(block, bay) -> per-block softmax over bays; weighted CE.
Leave-one-instance-out: does the model predict the held-out instance's
best-board assignments better than the argmax-preference baseline?
That is the generalization gate for the whole learning track.
Usage: learn_train.py
"""
import json
import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

rows = [json.loads(l) for l in open("runs/learn/asg_dataset.jsonl")]
by_pn = {}
for r in rows:
    by_pn.setdefault(r["pn"], []).append(r)


def pack(rr):
    X, y, w, grp = [], [], [], []
    for r in rr:
        fb = r["f_blk"]
        for j, fj in enumerate(r["f_bay"]):
            X.append(fb + fj)
        y.append(r["bay"])
        w.append(r["w"])
        grp.append(len(r["f_bay"]))
    return (np.array(X, dtype=np.float32), np.array(y),
            np.array(w, dtype=np.float32), np.array(grp))


DIM = 11
H1 = 16


def init(rng):
    return [rng.normal(0, 0.3, (DIM, H1)).astype(np.float32),
            np.zeros(H1, dtype=np.float32),
            rng.normal(0, 0.3, (H1, 1)).astype(np.float32),
            np.zeros(1, dtype=np.float32)]


def fwd(params, X):
    W1, b1, W2, b2 = params
    h = np.maximum(X @ W1 + b1, 0)
    return (h @ W2 + b2).ravel(), h


def train(rr, epochs=200, lr=0.02, l2=1e-3, seed=0):
    X, y, w, grp = pack(rr)
    rng = np.random.default_rng(seed)
    params = init(rng)
    nb = len(y)
    offs = np.zeros(nb + 1, dtype=int)
    np.cumsum(grp, out=offs[1:])
    for ep in range(epochs):
        s, h = fwd(params, X)
        gs = np.zeros_like(s)
        loss = 0.0
        for k in range(nb):
            a, b = offs[k], offs[k + 1]
            sk = s[a:b]
            sk = sk - sk.max()
            p = np.exp(sk)
            p /= p.sum()
            loss += -w[k] * math.log(max(p[y[k]], 1e-9))
            g = p.copy()
            g[y[k]] -= 1.0
            gs[a:b] = w[k] * g
        W1, b1, W2, b2 = params
        gh = gs[:, None] * W2.T
        gh[h <= 0] = 0
        gW2 = h.T @ gs[:, None] + l2 * W2
        gW1 = X.T @ gh + l2 * W1
        params = [W1 - lr * gW1 / nb, b1 - lr * gh.sum(0) / nb,
                  W2 - lr * gW2 / nb, b2 - lr * gs.sum() / nb]
    return params


def evaluate(params, rr):
    X, y, w, grp = pack(rr)
    s, _ = fwd(params, X)
    offs = np.zeros(len(y) + 1, dtype=int)
    np.cumsum(grp, out=offs[1:])
    hit = base = tot = 0.0
    for k in range(len(y)):
        a, b = offs[k], offs[k + 1]
        pred = int(np.argmax(s[a:b]))
        # baseline: argmax preference = f_bay[...][1]
        prefs = X[a:b, DIM - 2]
        bl = int(np.argmax(prefs))
        hit += w[k] * (pred == y[k])
        base += w[k] * (bl == y[k])
        tot += w[k]
    return float(hit / tot), float(base / tot)


res = {}
for hold in sorted(by_pn):
    tr = [r for pn2, rr in by_pn.items() if pn2 != hold for r in rr]
    params = train(tr)
    acc, bacc = evaluate(params, by_pn[hold])
    res[hold] = (round(acc, 3), round(bacc, 3))
    print(json.dumps({"holdout": hold, "model_acc": round(acc, 3),
                      "argmax_pref_acc": round(bacc, 3)}), flush=True)
wins = sum(1 for a, b in res.values() if a > b)
print(json.dumps({"model_wins": wins, "of": len(res)}))
