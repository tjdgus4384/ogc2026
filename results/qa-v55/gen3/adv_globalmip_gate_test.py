"""Execution proof for the gen3 delta in tools/globalmip.py.

Gen2 found: only the SOLVER=='cpsat' emission block was gated on
checker_feasible; the GM_CB Gurobi callback block and the default Gurobi
round-loop tail block still reported a gain and wrote a board on vp_bad==0
alone (G-BLOCKER-1).

The ae01b93 delta introduces a single helper, `_report_gain(res, sol,
best_obj, final_obj, chk, out_path)` (tools/globalmip.py lines 88-103), and
routes all three emission sites through it:
  - cpsat block:            _report_gain(res, sol, best_obj, eng.objective()[3], chk, out_path)   (line 271)
  - GM_CB callback block:   _report_gain(out, sol, best_obj, out["engine_obj"], chk, out_path)     (line 406)
  - default round-loop tail:_report_gain(res, sol, best_obj, eng.objective()[3], chk, out_path)    (line 461)

This harness copies `_report_gain` VERBATIM (not edited) from the current
tools/globalmip.py and re-runs gen2's blocks D and E (the two previously
unpatched call shapes) through it, plus a regression re-check of block C
(cpsat normal path) and a None-checker case that gen2 did not cover.
"""
import json
import os
import tempfile


# ---------------------------------------------------------------------------
# VERBATIM copy of tools/globalmip.py's _report_gain (lines 88-103, ae01b93).
# ---------------------------------------------------------------------------
def _report_gain(res, sol, best_obj, final_obj, chk, out_path):
    """Gain and board emission share ONE gate: the official checker.

    vp-clean is only a conservative mirror of the byte-authority checker, so
    neither an advertised improvement nor a persisted board may outrun
    check_feasibility agreeing. A None verdict (checker unusable) is treated
    as failure, not as permission.
    """
    res["final_obj"] = final_obj
    res["checker_feasible"] = None if chk is None else bool(chk.get("feasible"))
    if res["checker_feasible"]:
        res["gain_vs_best_input_pct"] = round(
            100 * (best_obj - final_obj) / best_obj, 3)
        json.dump(sol, open(out_path, "w"))
    else:
        res["gain_vs_best_input_pct"] = None


def block_written(path):
    return os.path.exists(path)


best_obj = 1000.0
final_obj = 800.0  # would be a 20% "gain" if reported
fake_sol = {"sol": "fake"}

results = {}
tmp = tempfile.mkdtemp()

# ---------------------------------------------------------------------------
# CALL-SITE SHAPES. Each of the three real call sites passes a DIFFERENT
# result dict variable (res for cpsat/default, out for GM_CB) into the SAME
# helper with the same positional signature -- reproduce that exactly rather
# than assume it, since the earlier bug was precisely that two call sites
# repeated the logic by hand instead of sharing it.
# ---------------------------------------------------------------------------

# ---- D': GM_CB callback call-site shape, checker_feasible=False ----------
pD = os.path.join(tmp, "d.json")
outD = {"vp_bad": 0, "engine_obj": final_obj}
_report_gain(outD, fake_sol, best_obj, outD["engine_obj"],
             {"feasible": False, "objective": 999.0}, pD)
results["D_callback_infeasible"] = {
    "gain": outD["gain_vs_best_input_pct"],
    "checker_feasible": outD["checker_feasible"],
    "board_written": block_written(pD),
    "expected": "gain=None, board_written=False",
    "pass": outD["gain_vs_best_input_pct"] is None and not block_written(pD),
}

# ---- D'': GM_CB callback call-site shape, chk=None (checker unusable) ----
pD2 = os.path.join(tmp, "d2.json")
outD2 = {"vp_bad": 0, "engine_obj": final_obj}
_report_gain(outD2, fake_sol, best_obj, outD2["engine_obj"], None, pD2)
results["D2_callback_chk_none"] = {
    "gain": outD2["gain_vs_best_input_pct"],
    "checker_feasible": outD2["checker_feasible"],
    "board_written": block_written(pD2),
    "expected": "gain=None, board_written=False (None must be failure, not permission)",
    "pass": outD2["gain_vs_best_input_pct"] is None and not block_written(pD2),
}

# ---- D''': GM_CB callback call-site shape, checker_feasible=True ----------
pD3 = os.path.join(tmp, "d3.json")
outD3 = {"vp_bad": 0, "engine_obj": final_obj}
_report_gain(outD3, fake_sol, best_obj, outD3["engine_obj"],
             {"feasible": True, "objective": 800.0}, pD3)
results["D3_callback_feasible_normal_path"] = {
    "gain": outD3["gain_vs_best_input_pct"],
    "checker_feasible": outD3["checker_feasible"],
    "board_written": block_written(pD3),
    "expected": "gain=20.0, board_written=True",
    "pass": outD3["gain_vs_best_input_pct"] == 20.0 and block_written(pD3),
}

# ---- E': default round-loop call-site shape, checker_feasible=False ------
pE = os.path.join(tmp, "e.json")
resE = {}
_report_gain(resE, fake_sol, best_obj, final_obj,
             {"feasible": False, "objective": 999.0}, pE)
results["E_default_roundloop_infeasible"] = {
    "gain": resE["gain_vs_best_input_pct"],
    "checker_feasible": resE["checker_feasible"],
    "board_written": block_written(pE),
    "expected": "gain=None, board_written=False",
    "pass": resE["gain_vs_best_input_pct"] is None and not block_written(pE),
}

# ---- E'': default round-loop call-site shape, chk=None -------------------
pE2 = os.path.join(tmp, "e2.json")
resE2 = {}
_report_gain(resE2, fake_sol, best_obj, final_obj, None, pE2)
results["E2_default_roundloop_chk_none"] = {
    "gain": resE2["gain_vs_best_input_pct"],
    "checker_feasible": resE2["checker_feasible"],
    "board_written": block_written(pE2),
    "expected": "gain=None, board_written=False",
    "pass": resE2["gain_vs_best_input_pct"] is None and not block_written(pE2),
}

# ---- E''': default round-loop call-site shape, checker_feasible=True -----
pE3 = os.path.join(tmp, "e3.json")
resE3 = {}
_report_gain(resE3, fake_sol, best_obj, final_obj,
             {"feasible": True, "objective": 800.0}, pE3)
results["E3_default_roundloop_feasible_normal_path"] = {
    "gain": resE3["gain_vs_best_input_pct"],
    "checker_feasible": resE3["checker_feasible"],
    "board_written": block_written(pE3),
    "expected": "gain=20.0, board_written=True",
    "pass": resE3["gain_vs_best_input_pct"] == 20.0 and block_written(pE3),
}

# ---- C: cpsat call-site shape regression re-check (all three cases) ------
pC1 = os.path.join(tmp, "c1.json")
resC1 = {}
_report_gain(resC1, fake_sol, best_obj, final_obj,
             {"feasible": False, "objective": 999.0}, pC1)
results["C1_cpsat_infeasible"] = {
    "gain": resC1["gain_vs_best_input_pct"], "board_written": block_written(pC1),
    "expected": "gain=None, board_written=False",
    "pass": resC1["gain_vs_best_input_pct"] is None and not block_written(pC1),
}

pC2 = os.path.join(tmp, "c2.json")
resC2 = {}
_report_gain(resC2, fake_sol, best_obj, final_obj, None, pC2)
results["C2_cpsat_chk_none"] = {
    "gain": resC2["gain_vs_best_input_pct"], "board_written": block_written(pC2),
    "expected": "gain=None, board_written=False",
    "pass": resC2["gain_vs_best_input_pct"] is None and not block_written(pC2),
}

pC3 = os.path.join(tmp, "c3.json")
resC3 = {}
_report_gain(resC3, fake_sol, best_obj, final_obj,
             {"feasible": True, "objective": 800.0}, pC3)
results["C3_cpsat_feasible_normal_path"] = {
    "gain": resC3["gain_vs_best_input_pct"], "board_written": block_written(pC3),
    "expected": "gain=20.0, board_written=True",
    "pass": resC3["gain_vs_best_input_pct"] == 20.0 and block_written(pC3),
}

results["ALL_PASS"] = all(v["pass"] for v in results.values() if isinstance(v, dict) and "pass" in v)

print(json.dumps(results, indent=2))
