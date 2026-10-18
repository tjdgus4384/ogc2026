#!/usr/bin/env python3
"""Unit/branch smoke for the sealed population candidate (Revision 4 Batch 1).

Runs against runs/release/population-candidate/solver (sealed source + the
isolated-build .so).  Verifies, with the real engine and real instances:

  api       isolated import; production export surface exact (parallel kernel
            + set_pref_p present; scalar bandit / set_exact_events absent)
  branch    non-_pop_here board runs the 15% C++ tail exactly once, ordered
            portfolio/slice -> tail -> assign-descent -> gate;
            _pop_here board keeps the unchanged Python population path (no
            tail call)
  budget    requested tail seconds == 0.15/0.85 x primary allocation within
            tolerance, and the slice window is shifted by the tail
  deadline  tail never runs into the gate reserve; no TL overrun
  rollback  a raising kernel is contained: algorithm() still returns a
            checker-feasible board via the canonical fallback path
  checker   every returned solution replays feasible under the official
            checker bytes shipped in the candidate tree

Writes evidence/candidate-unit.json.  Exit 0 iff every gate PASSes.
"""
import argparse
import hashlib
import importlib.util
import json
import pathlib
import subprocess
import sys
import time

TOOLS = pathlib.Path(__file__).resolve().parent
ROOT = TOOLS.parent
WORK = ROOT / "runs/release/population-candidate"
SOLVER = WORK / "solver"
EV = WORK / "evidence"
SO_NAME = "sscore.cpython-312-x86_64-linux-gnu.so"


def sha(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def tree_sha(d):
    items = [(str(p.relative_to(d)), sha(p))
             for p in sorted(d.rglob("*")) if p.is_file()]
    return hashlib.sha256(json.dumps(items).encode()).hexdigest()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def api_check():
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "import sscore\n"
        "eng = sscore.Engine()\n"
        "need = ['init_problem','add_orient','build_occ','reconstruct_ordered',"
        "'run_portfolio','get_block','get_candidates','objective','nudge_block',"
        "'retime_block','move_block','swap_blocks','set_assignment_lock',"
        "'set_entry_lock','set_block','set_solution','get_solution',"
        "'run_kernel_parallel_bandit','set_pref_p']\n"
        "deny = ['run_kernel_bandit','set_exact_events','run_kernel_memetic',"
        "'run_kernel_pattern_master','run_kernel_parallel_benders',"
        "'run_kernel_mcts_coalition','run_kernel_large_ruin',"
        "'run_kernel_assignment_flow','run_kernel_backbone_repack']\n"
        "missing = [x for x in need if not hasattr(eng, x)]\n"
        "extra = [x for x in deny if hasattr(eng, x)]\n"
        "assert not missing, ('missing', missing)\n"
        "assert not extra, ('forbidden export', extra)\n"
        "print('api OK')\n" % str(SOLVER))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, cwd="/tmp")
    return r.returncode == 0, (r.stdout + r.stderr).strip()[-500:]


class Trace:
    def __init__(self):
        self.diags = []          # (t, tag, kv)
        self.kernel_calls = []   # (t_start, seconds_requested, t_end)


def run_branch(myalg, sscore_mod, prob, tl, trace, kernel_raises=False):
    orig_diag = myalg._diag
    orig_run = sscore_mod.Engine.run_kernel_parallel_bandit

    def diag(tag, **kv):
        trace.diags.append((time.time(), tag, kv))
        return orig_diag(tag, **kv)

    def wrapped(self, seconds, seed):
        t0 = time.time()
        if kernel_raises:
            trace.kernel_calls.append((t0, seconds, t0))
            raise RuntimeError("unit-forced kernel failure")
        out = orig_run(self, seconds, seed)
        trace.kernel_calls.append((t0, seconds, time.time()))
        return out

    myalg._diag = diag
    sscore_mod.Engine.run_kernel_parallel_bandit = wrapped
    try:
        t0 = time.time()
        sol = myalg.algorithm(json.loads(json.dumps(prob)), tl)
        wall = time.time() - t0
    finally:
        myalg._diag = orig_diag
        sscore_mod.Engine.run_kernel_parallel_bandit = orig_run
    return sol, wall, t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", default=str(ROOT / "data"))
    args = ap.parse_args()
    data = pathlib.Path(args.data_root)

    results = {}
    details = {}

    ok, msg = api_check()
    results["apiStatus"] = "PASS" if ok else "FAIL"
    details["api"] = msg

    sys.path.insert(0, str(SOLVER))
    checker = load("utils", SOLVER / "utils.py")
    import sscore  # resolved from SOLVER path
    sscore_mod = sscore
    myalg = load("myalgorithm", SOLVER / "myalgorithm.py")

    prob_a = json.loads((data / "preliminary_test/prob_1.json").read_text())
    prob_b = json.loads((data / "train/prob_5.json").read_text())

    # ---- branch A: non-_pop_here, tail must fire once -------------------
    tr_a = Trace()
    sol_a, wall_a, t0_a = run_branch(myalg, sscore_mod, prob_a, 90.0, tr_a)
    tags_a = [t for _, t, _ in tr_a.diags]
    feas_a = checker.check_feasibility(
        json.loads(json.dumps(prob_a)), json.loads(json.dumps(sol_a)))
    n_a = len(prob_a["blocks"])
    reserve_a = min(20.0, 4.0 + 0.05 * n_a, 0.12 * 90.0)
    D_a = t0_a + 90.0 - 2.0 - reserve_a

    tail_ok = (len(tr_a.kernel_calls) == 1
               and "cpp-parallel-bandit-tail" in tags_a)
    order_ok = False
    if tail_ok:
        i_tail = tags_a.index("cpp-parallel-bandit-tail")
        i_slice = tags_a.index("slice") if "slice" in tags_a else -1
        i_assign = tags_a.index("assign") if "assign" in tags_a else 10**9
        order_ok = i_slice < i_tail < i_assign
    results["branchStatus"] = "PASS" if tail_ok and order_ok else "FAIL"
    details["branchA"] = {
        "tags": tags_a, "kernelCalls": len(tr_a.kernel_calls),
        "wall": round(wall_a, 2), "feasible": bool(feas_a.get("feasible")),
        "objective": feas_a.get("objective")}

    # ---- budget: requested tail = 0.15*B, slice shifted -----------------
    budget_ok = False
    deadline_ok = False
    if tail_ok:
        t_start, req, t_end = tr_a.kernel_calls[0]
        b_est = req / 0.15                    # B implied by the 15% carve
        # B was computed as (deadline - reserve) - t_carve; t_carve is a few
        # seconds after t0 (seeding).  Sanity-band the implied B against the
        # analytic window and check the slice anchor moved by ~req.
        b_hi = (90.0 - 2.0 - reserve_a)
        budget_ok = 0.5 * b_hi <= b_est <= 1.02 * b_hi
        if "slice" in tags_a:
            t_slice = [t for t, tag, _ in tr_a.diags if tag == "slice"][0]
            budget_ok = budget_ok and abs(t_slice - (D_a - req)) <= 6.0
        deadline_ok = (t_end <= D_a + 1.0) and wall_a <= 90.0
    results["budgetStatus"] = "PASS" if budget_ok else "FAIL"
    details["budget"] = {
        "requestedTail": round(tr_a.kernel_calls[0][1], 2) if tail_ok else None,
        "impliedB": round(tr_a.kernel_calls[0][1] / 0.15, 2) if tail_ok else None,
        "analyticBCap": round(90.0 - 2.0 - reserve_a, 2)}

    # ---- branch B: _pop_here keeps the Python population path -----------
    tr_b = Trace()
    sol_b, wall_b, _ = run_branch(myalg, sscore_mod, prob_b, 200.0, tr_b)
    tags_b = [t for _, t, _ in tr_b.diags]
    feas_b = checker.check_feasibility(
        json.loads(json.dumps(prob_b)), json.loads(json.dumps(sol_b)))
    pop_ok = (len(tr_b.kernel_calls) == 0
              and "cpp-parallel-bandit-tail" not in tags_b
              and "population" in tags_b)
    if not pop_ok:
        results["branchStatus"] = "FAIL"
    details["branchB"] = {
        "tags": tags_b, "kernelCalls": len(tr_b.kernel_calls),
        "wall": round(wall_b, 2), "feasible": bool(feas_b.get("feasible")),
        "objective": feas_b.get("objective")}
    deadline_ok = deadline_ok and wall_b <= 200.0
    results["deadlineStatus"] = "PASS" if deadline_ok else "FAIL"

    # ---- rollback: raising kernel is contained --------------------------
    tr_c = Trace()
    sol_c, wall_c, _ = run_branch(myalg, sscore_mod, prob_a, 90.0, tr_c,
                                  kernel_raises=True)
    feas_c = checker.check_feasibility(
        json.loads(json.dumps(prob_a)), json.loads(json.dumps(sol_c)))
    roll_ok = (len(tr_c.kernel_calls) == 1 and bool(feas_c.get("feasible"))
               and wall_c <= 90.0)
    results["rollbackStatus"] = "PASS" if roll_ok else "FAIL"
    details["rollback"] = {
        "kernelRaised": True, "feasible": bool(feas_c.get("feasible")),
        "wall": round(wall_c, 2), "fallbackTags": [
            t for _, t, _ in tr_c.diags if t == "fallback"]}

    # ---- checker replay authority ---------------------------------------
    chk_ok = all(bool(f.get("feasible")) for f in (feas_a, feas_b, feas_c))
    results["checkerStatus"] = "PASS" if chk_ok else "FAIL"

    status = "PASS" if all(v == "PASS" for v in results.values()) else "FAIL"
    receipt = {
        "schemaVersion": 1,
        "ranAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "status": status,
        "sourceTreeSha256": tree_sha(WORK / "source"),
        "soSha256": sha(SOLVER / SO_NAME),
        "checkerSha256": sha(SOLVER / "utils.py"),
        "instances": {"branchA": "preliminary_test/prob_1@90s",
                      "branchB": "train/prob_5@200s",
                      "rollback": "preliminary_test/prob_1@90s"},
        **results,
        "details": details,
    }
    EV.mkdir(parents=True, exist_ok=True)
    (EV / "candidate-unit.json").write_text(
        json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print("unit", status, json.dumps(results))
    sys.exit(0 if status == "PASS" else 1)


if __name__ == "__main__":
    main()
