"""Execution proof for the A2 fix in tools/globalmip.py (gen2, delta-only).

tools/globalmip.py has THREE separate result-emission blocks that all share
the same shape (compute checker_feasible via M._check, then decide whether
to report gain_vs_best_input_pct and json.dump the board):

  1. SOLVER=="cpsat" path (lines ~254-267)           -- FIXED in 1e1d545
  2. GM_CB=1 Gurobi lazy-callback path (lines ~396-403) -- NOT patched
  3. default Gurobi round-loop path (lines ~457-462)    -- NOT patched

This harness copies each block's exact logic VERBATIM (not edited) and
drives it with a mocked `chk` result to prove, by execution, both that the
cpsat fix works and that the other two paths still lack the gate.
"""
import json

best_obj = 1000.0
final_obj = 800.0  # would be a 20% "gain" if reported


def block_written(path):
    import os
    return os.path.exists(path)


# ---------------------------------------------------------------------------
# BLOCK 1 -- SOLVER=="cpsat" path, copied verbatim from tools/globalmip.py
# (the res["checker_feasible"]/gain_vs_best_input_pct/json.dump lines).
# ---------------------------------------------------------------------------
def block1_cpsat(chk, out_path):
    res = {}
    res["final_obj"] = final_obj
    res["checker_feasible"] = None if chk is None else bool(chk.get("feasible"))
    if res["checker_feasible"]:
        res["gain_vs_best_input_pct"] = round(
            100 * (best_obj - res["final_obj"]) / best_obj, 3)
        json.dump({"sol": "fake"}, open(out_path, "w"))
    else:
        res["gain_vs_best_input_pct"] = None
    return res


# ---------------------------------------------------------------------------
# BLOCK 2 -- GM_CB=1 callback path, copied verbatim (the out[...] lines
# inside `if out["vp_bad"] == 0:`).
# ---------------------------------------------------------------------------
def block2_callback(chk, out_path):
    out = {"vp_bad": 0}
    out["engine_obj"] = final_obj
    if out["vp_bad"] == 0:
        out["checker_feasible"] = None if chk is None else bool(chk.get("feasible"))
        out["checker_obj"] = None if chk is None else chk.get("objective")
        out["gain_vs_best_input_pct"] = round(
            100 * (best_obj - out["engine_obj"]) / best_obj, 3)
        json.dump({"sol": "fake"}, open(out_path, "w"))
    return out


# ---------------------------------------------------------------------------
# BLOCK 3 -- default Gurobi round-loop path, copied verbatim (the
# `if sol_flat is not None:` tail block at file end).
# ---------------------------------------------------------------------------
def block3_default(chk, out_path):
    res = {}
    res["final_obj"] = final_obj
    res["checker_feasible"] = None if chk is None else bool(chk.get("feasible"))
    res["checker_obj"] = None if chk is None else chk.get("objective")
    res["gain_vs_best_input_pct"] = round(
        100 * (best_obj - res["final_obj"]) / best_obj, 3)
    json.dump({"sol": "fake"}, open(out_path, "w"))
    return res


import os
import tempfile

results = {}
tmp = tempfile.mkdtemp()

# ---- Test A: cpsat path, checker_feasible=False -> must NOT write, gain None
pA = os.path.join(tmp, "a.json")
rA = block1_cpsat({"feasible": False, "objective": 999.0}, pA)
results["A_cpsat_infeasible"] = {
    "gain": rA["gain_vs_best_input_pct"],
    "board_written": block_written(pA),
    "expected": "gain=None, board_written=False",
    "pass": rA["gain_vs_best_input_pct"] is None and not block_written(pA),
}

# ---- Test B: cpsat path, chk=None (checker call itself failed) -> same
pB = os.path.join(tmp, "b.json")
rB = block1_cpsat(None, pB)
results["B_cpsat_chk_none"] = {
    "gain": rB["gain_vs_best_input_pct"],
    "board_written": block_written(pB),
    "expected": "gain=None, board_written=False",
    "pass": rB["gain_vs_best_input_pct"] is None and not block_written(pB),
}

# ---- Test C: cpsat path, checker_feasible=True -> normal path still works
pC = os.path.join(tmp, "c.json")
rC = block1_cpsat({"feasible": True, "objective": 800.0}, pC)
results["C_cpsat_feasible_normal_path"] = {
    "gain": rC["gain_vs_best_input_pct"],
    "board_written": block_written(pC),
    "expected": "gain=20.0, board_written=True",
    "pass": rC["gain_vs_best_input_pct"] == 20.0 and block_written(pC),
}

# ---- Test D: callback path (block2), checker_feasible=False -----------
# EXPECTATION under the CLAIMED invariant: gain=None, no write.
# ACTUAL (unpatched code): gain IS computed, board IS written.
pD = os.path.join(tmp, "d.json")
rD = block2_callback({"feasible": False, "objective": 999.0}, pD)
results["D_callback_infeasible_UNPATCHED"] = {
    "gain": rD["gain_vs_best_input_pct"],
    "board_written": block_written(pD),
    "claimed_invariant_expected": "gain=None, board_written=False",
    "actual_matches_claimed_invariant": (
        rD["gain_vs_best_input_pct"] is None and not block_written(pD)
    ),
}

# ---- Test E: default round-loop path (block3), checker_feasible=False --
pE = os.path.join(tmp, "e.json")
rE = block3_default({"feasible": False, "objective": 999.0}, pE)
results["E_default_roundloop_infeasible_UNPATCHED"] = {
    "gain": rE["gain_vs_best_input_pct"],
    "board_written": block_written(pE),
    "claimed_invariant_expected": "gain=None, board_written=False",
    "actual_matches_claimed_invariant": (
        rE["gain_vs_best_input_pct"] is None and not block_written(pE)
    ),
}

print(json.dumps(results, indent=2))
