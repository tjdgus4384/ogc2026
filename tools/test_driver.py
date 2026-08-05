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
import utils as U

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

# 3. The terminal serial fallback must be complete and checker-authorized.
for idx, prob in enumerate(probs, 1):
    fallback = M._trivial(prob)
    checked = U.check_feasibility(prob, fallback)
    assert checked["feasible"], f"P{idx} fallback: {checked.get('violations')}"

# 4. Malformed IDs and timestamp aliases fail closed before checker indexing/replay.
bad_id = {"operations": {"0": [
    {"type": "ENTRY", "block_id": -1, "bay_id": 0}
]}}
bad_time = {"operations": {"00": []}}
assert not U.check_feasibility(probs[0], bad_id)["feasible"]
assert not U.check_feasibility(probs[0], bad_time)["feasible"]

print("DRIVER GATE PASS: spec", specs, "exit-order, fallback, checker-input OK")
