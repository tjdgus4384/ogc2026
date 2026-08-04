"""Dataset extractor for the learned bay-assignment prior (v0).

Scans runs/*/prob_N.sol.json with sibling prob_N.log scores; keeps the
top-K distinct boards per instance; emits one JSONL row per
(board, block): block features + per-bay features + chosen bay, with a
board quality weight (rank-based). Output: runs/learn/asg_dataset.jsonl
Usage: learn_extract.py [K]
"""
import glob
import json
import math
import os
import re
import sys

K = int(sys.argv[1]) if len(sys.argv) > 1 else 10
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
os.makedirs("runs/learn", exist_ok=True)


def shoelace(pts):
    s = 0.0
    for k in range(len(pts)):
        x1, y1 = pts[k]
        x2, y2 = pts[(k + 1) % len(pts)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2


out = open("runs/learn/asg_dataset.jsonl", "w")
rows = 0
for pn in range(1, 7):
    prob = json.load(open(f"data/preliminary_test/prob_{pn}.json"))
    B = prob["blocks"]
    n = len(B)
    m = len(prob["bays"])
    CAP = [b["width"] * b["height"] for b in prob["bays"]]
    tot_cap = sum(CAP)
    area = []
    for b in B:
        area.append(max(shoelace(L) for L in b["shape"][0]["layers"]))
    tot_area = sum(area)
    horizon = max(int(round(b["due_date"])) for b in B) or 1

    boards = []
    for sf in glob.glob(f"runs/*/prob_{pn}.sol.json"):
        lf = sf.replace(".sol.json", ".log")
        if not os.path.exists(lf):
            continue
        txt = open(lf, errors="ignore").read()
        mm = re.findall(r'"objective": ([0-9.]+)', txt)
        if not mm:
            continue
        try:
            sol = json.load(open(sf))
        except Exception:
            continue
        boards.append((float(mm[-1]), sf, sol))
    boards.sort(key=lambda t: t[0])
    seen_asg = set()
    kept = 0
    for rank, (score, sf, sol) in enumerate(boards):
        if kept >= K:
            break
        asg = {}
        ent = {}
        for t, ops in sol.get("operations", {}).items():
            for op in ops:
                if op["type"] == "ENTRY":
                    asg[op["block_id"]] = op["bay_id"]
                    ent[op["block_id"]] = int(t)
        if len(asg) != n:
            continue
        key = tuple(asg[i] for i in range(n))
        if key in seen_asg:
            continue
        seen_asg.add(key)
        kept += 1
        wgt = 1.0 / (1.0 + kept)          # rank-decayed weight
        for i in range(n):
            b = B[i]
            pr = b["bay_preferences"]
            row = {
                "pn": pn, "score": score, "w": round(wgt, 4),
                "bay": asg[i],
                "f_blk": [
                    area[i] / tot_area * n,
                    b["workload"] / (sum(x["workload"] for x in B) / n),
                    b["release_time"] / horizon,
                    b["due_date"] / horizon,
                    (b["due_date"] - b["release_time"]
                     - b["processing_time"]) / horizon,
                    b["processing_time"] / horizon,
                    len(b["shape"]),
                    max(pr) - min(pr),
                ],
                "f_bay": [[
                    CAP[j] / tot_cap * m,
                    pr[j] / 100.0,
                    float(j == max(range(m), key=lambda q: pr[q])),
                ] for j in range(m)],
            }
            out.write(json.dumps(row) + "\n")
            rows += 1
print(json.dumps({"rows": rows}))
out.close()
