"""Driver-level invariant gate (gateway-safe, no kernel needed, ~seconds).

Run BEFORE any submission or after any myalgorithm.py change:
    python tools/test_driver.py
Executable form of the checks this campaign kept re-deriving by hand:
spec-rule gate and invariant 7's driver half (same-day EXITs emit
later-entry-first -- vp rules 3/5 assume exactly this order; see
docs/PIPELINE.md).
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "solver"))
os.environ.pop("OGC_SEED", None)          # dispatch path, server-like
import myalgorithm as M

probs = [json.load(open(os.path.join(
    ROOT, "data", "preliminary_test", f"prob_{p}.json"))) for p in range(1, 7)]

# 1. spec rule fires exactly on the <=3-bay under-saturated boards
specs = [M._spec_rule(p) for p in probs]
assert specs == [True, True, True, True, False, False], f"spec gate: {specs}"

# 2. invariant 7, driver half: same-day EXITs later-entry-first
recs = {1: [0, 0, 0, 0, 2, 9], 2: [0, 1, 0, 0, 5, 9], 3: [0, 2, 0, 0, 4, 9]}
ops = M._recs_to_sol(recs)["operations"]["9"]
exits = [op["block_id"] for op in ops if op["type"] == "EXIT"]
assert exits == [2, 3, 1], f"EXIT order broke invariant 7: {exits}"

print("DRIVER GATE PASS: spec", specs, "exit-order OK")
