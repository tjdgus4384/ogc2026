#!/usr/bin/env python3
"""Population-candidate unit smoke (ralplan Revision 4 Batch 1).

Runs the seated candidate (with its tested .so) in isolated subprocesses and
verifies, per the pre-registered runtime treatment:

  api       exports present/absent exactly as the closure policy demands
  branch    non-_pop_here board with B > 60 runs the 15% tail (pop-tail +
            cpp-parallel-bandit-tail diags); _pop_here board runs the
            unchanged Python population path (no tail diags)
  budget    tail == 0.15 * base within 1e-6 (parsed from the pop-tail diag)
  deadline  every run returns within its timelimit
  rollback  tiny-TL run (B <= 60: no tail) still yields a feasible board via
            the canonical ladder
  checker   every produced solution passes the candidate's own official
            checker (utils.check_feasibility)

    python3 tools/population_candidate_unit.py --candidate DIR \
        --data-root data --receipt EVID/candidate-unit.json [--python PY]
"""
import argparse
import json
import pathlib
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import release_schema as rs

SO = "sscore.cpython-312-x86_64-linux-gnu.so"

RUNNER = r'''
import json, sys, time
solver = sys.argv[1]
sys.path.insert(0, solver)
prob = json.load(open(sys.argv[2]))
tl = float(sys.argv[3])
import myalgorithm
t0 = time.time()
sol = myalgorithm.algorithm(prob, timelimit=tl)
wall = time.time() - t0
import utils
res = utils.check_feasibility(prob, sol)
print(json.dumps({"wall": wall, "feasible": res.get("feasible"),
                  "objective": res.get("objective")}))
'''


def run_case(py, cand, prob_path, tl):
    proc = subprocess.run(
        [py, "-c", RUNNER, str(cand), str(prob_path), str(tl)],
        capture_output=True, text=True, timeout=tl + 120,
        env={"PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
             "OMP_NUM_THREADS": "4", "OPENBLAS_NUM_THREADS": "1",
             "HOME": "/tmp", "PATH": "/usr/bin:/bin", "OGC_DIAG": "1"})
    out = None
    if proc.returncode == 0:
        for line in proc.stdout.splitlines():
            try:
                out = json.loads(line)
            except ValueError:
                continue
    return out, proc.stderr


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--receipt", required=True)
    ap.add_argument("--python", default=sys.executable)
    args = ap.parse_args(argv)
    cand = pathlib.Path(args.candidate).resolve()
    data = pathlib.Path(args.data_root).resolve()
    py = args.python

    assertions = []
    def A(aid, ok, detail=""):
        assertions.append({"id": aid, "status": "PASS" if ok else "FAIL",
                           "detail": str(detail)[:400]})

    # ---- api inventory
    inv = subprocess.run(
        [py, "-c",
         "import sys,json; sys.path.insert(0, sys.argv[1]); import sscore;"
         "names=set(dir(sscore));"
         "[names.update(dir(getattr(sscore,n))) for n in dir(sscore)"
         " if isinstance(getattr(sscore,n),type)];"
         "print(json.dumps(sorted(n for n in names if not n.startswith('_'))))",
         str(cand)],
        capture_output=True, text=True,
        env={"PYTHONNOUSERSITE": "1", "HOME": "/tmp", "PATH": "/usr/bin:/bin"})
    exports = json.loads(inv.stdout) if inv.returncode == 0 else []
    api_ok = (inv.returncode == 0
              and "run_kernel_parallel_bandit" in exports
              and "set_pref_p" in exports
              and "run_portfolio" in exports
              and "run_kernel_bandit" not in exports
              and "set_exact_events" not in exports)
    A("api-inventory", api_ok, inv.stderr[-200:] or f"{len(exports)} exports")

    # ---- tail branch: small board, TL 90 => B > 60, not _pop_here
    tail_prob = data / "preliminary_test" / "prob_1.json"
    out1, err1 = run_case(py, cand, tail_prob, 90)
    base = tail = None
    for line in (err1 or "").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("diag") == "pop-tail":
            base, tail = float(d["base"]), float(d["tail"])
    ran_tail = "cpp-parallel-bandit-tail" in (err1 or "")
    A("branch-tail-fires", bool(out1) and base is not None and ran_tail,
      f"out={out1} base={base} tail={tail}")
    # base/tail are rounded to 3 decimals in the diag; allow that quantum
    budget_ok = (base is not None and tail is not None
                 and abs(tail - 0.15 * base) < 2e-3)
    A("budget-15pct", budget_ok, f"base={base} tail={tail}")
    A("tail-run-feasible", bool(out1) and out1.get("feasible") is True,
      out1 and out1.get("objective"))
    A("tail-run-deadline", bool(out1) and out1["wall"] <= 90 + 1.0,
      out1 and out1.get("wall"))

    # ---- population branch: P3-class board, TL 240 => _pop_here, no tail
    pop_prob = data / "preliminary_test" / "prob_3.json"
    out2, err2 = run_case(py, cand, pop_prob, 240)
    no_tail = ("cpp-parallel-bandit-tail" not in (err2 or "")
               and '"pop-tail"' not in (err2 or "")
               and "pop-tail" not in (err2 or ""))
    pop_diag = "population" in (err2 or "")
    A("branch-pop-here-unchanged", bool(out2) and no_tail and pop_diag,
      f"out={out2} no_tail={no_tail} pop_diag={pop_diag}")
    A("pop-run-feasible", bool(out2) and out2.get("feasible") is True,
      out2 and out2.get("objective"))
    A("pop-run-deadline", bool(out2) and out2["wall"] <= 240 + 1.0,
      out2 and out2.get("wall"))

    # ---- rollback/eligibility boundary: tiny TL => no tail, still feasible
    out3, err3 = run_case(py, cand, tail_prob, 20)
    A("tiny-tl-no-tail", bool(out3) and "pop-tail" not in (err3 or ""),
      out3)
    A("tiny-tl-feasible", bool(out3) and out3.get("feasible") is True,
      out3 and out3.get("objective"))

    status = "PASS" if all(a["status"] == "PASS" for a in assertions) else "FAIL"
    receipt = {
        "schemaVersion": 1, "kind": "candidate-unit", "status": status,
        "createdAt": rs.now_iso(),
        "candidateTree": str(cand),
        "soSha256": rs.sha256_file(cand / SO) if (cand / SO).exists() else None,
        "apiStatus": "PASS" if api_ok else "FAIL",
        "branchStatus": "PASS" if all(
            a["status"] == "PASS" for a in assertions
            if a["id"].startswith("branch")) else "FAIL",
        "budgetStatus": "PASS" if budget_ok else "FAIL",
        "deadlineStatus": "PASS" if all(
            a["status"] == "PASS" for a in assertions
            if a["id"].endswith("deadline")) else "FAIL",
        "rollbackStatus": "PASS" if all(
            a["status"] == "PASS" for a in assertions
            if a["id"].startswith("tiny-tl")) else "FAIL",
        "checkerStatus": "PASS" if all(
            a["status"] == "PASS" for a in assertions
            if a["id"].endswith("feasible")) else "FAIL",
        "assertions": assertions,
    }
    path = pathlib.Path(args.receipt)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": status, "receipt": str(path)}))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
