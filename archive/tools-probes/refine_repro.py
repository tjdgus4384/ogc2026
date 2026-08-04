"""Offline repro of the in-TL refine pass from a dumped input.
Usage: refine_repro.py <pnum> <dumpfile> [budget_sec]
Env: OGC_REFFIX=1 to fix-to-hint (find the violated hint variable)."""
import json
import math
import os
import sys
import time

pnum = int(sys.argv[1])
dump = json.load(open(sys.argv[2]))
budget = float(sys.argv[3]) if len(sys.argv) > 3 else 120.0

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, os.environ.get("REFDIR", "solver_rf"))
import myalgorithm as M          # noqa: E402
import refine as RF              # noqa: E402

prob = json.load(open(f"data/preliminary_test/prob_{pnum}.json"))
B = prob["blocks"]
H = (max(int(round(b["due_date"])) for b in B)
     + max(max(1, int(math.ceil(b["processing_time"] - 1e-9)))
           for b in B) + 40)
eng2 = M._build_engine(prob)
eng2.build_occ(H)
out = RF.refine(prob, eng2, dump["cands"], dump["best"],
                time.time() + budget, best_obj=None)
print("refine returned:", "None" if out is None else "solution")
