#!/usr/bin/env python3
"""Single shipcheck case against an explicit release final-extract.

Unlike tools/run_instance.py this runner refuses to guess: --solver, --checker,
--data-root and lineage hashes are required, so a receipt can never silently
describe the dirty repo tree. The candidate runs in an isolated subprocess
that imports ONLY the final extract; the official checker replays the
solution in the parent process from the explicit --checker path.

Modes:
    search   myalgorithm.algorithm(prob, tl)
    noort    same, with a meta-path blocker that forces `import ortools` to
             fail inside the candidate subprocess (records activation)
    trivial  myalgorithm._trivial(prob), no search, no time limit
"""
import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
import resource
import signal
import subprocess
import sys
import tempfile
import time

TOOLS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import release_schema as rs

SO_NAME = "sscore.cpython-312-x86_64-linux-gnu.so"

_CANDIDATE_MAIN = r'''
import json
import pathlib
import sys

mode, solver, prob_path, tl, sol_path = (sys.argv[1], sys.argv[2], sys.argv[3],
                                         sys.argv[4], sys.argv[5])
blocked = {"count": 0}
if mode == "noort":
    class _Blocker:
        def find_spec(self, name, path=None, target=None):
            if name == "ortools" or name.startswith("ortools."):
                blocked["count"] += 1
                raise ImportError(f"blocked by release harness: {name}")
            return None
    sys.meta_path.insert(0, _Blocker())

sys.path.insert(0, solver)
import myalgorithm

assert pathlib.Path(myalgorithm.__file__).resolve().is_relative_to(
    pathlib.Path(solver).resolve()), f"import escaped extract: {myalgorithm.__file__}"

prob = json.loads(pathlib.Path(prob_path).read_text())
if mode == "trivial":
    sol = myalgorithm._trivial(prob)
else:
    sol = myalgorithm.algorithm(prob, float(tl))
meta = {"blockerActivations": blocked["count"],
        "module": myalgorithm.__file__}
pathlib.Path(sol_path).write_text(json.dumps({"solution": sol, "meta": meta}))
'''


def load_checker(path):
    source = pathlib.Path(path).read_bytes()
    spec = importlib.util.spec_from_file_location("utils", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["utils"] = module
    spec.loader.exec_module(module)
    return module, hashlib.sha256(source).hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--solver", required=True)
    ap.add_argument("--checker", required=True)
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--instance", required=True, type=int)
    ap.add_argument("--seed", required=True, type=int)
    ap.add_argument("--tl", type=float, default=None)
    ap.add_argument("--mode", required=True, choices=("search", "noort", "trivial"))
    ap.add_argument("--gate-id", required=True)
    ap.add_argument("--case-id", required=True)
    ap.add_argument("--receipt", required=True)
    ap.add_argument("--solution-out", default=None)
    ap.add_argument("--release-zip-sha256", required=True)
    ap.add_argument("--solver-tree-sha256", required=True)
    ap.add_argument("--python", default=sys.executable)
    args = ap.parse_args(argv)

    solver = pathlib.Path(args.solver).resolve()
    checker_path = pathlib.Path(args.checker).resolve()
    prob_path = (pathlib.Path(args.data_root) / args.dataset /
                 f"prob_{args.instance}.json").resolve()

    tree_sha, _ = rs.tree_sha256(solver)
    if tree_sha != args.solver_tree_sha256:
        print(f"CASE FAIL: solver tree drift: {tree_sha}", file=sys.stderr)
        return 2
    so_sha = rs.sha256_file(solver / SO_NAME)
    input_sha = rs.sha256_file(prob_path)
    checker, checker_sha = load_checker(checker_path)
    prob = json.loads(prob_path.read_text())

    fd, sol_tmp = tempfile.mkstemp(prefix="ogc-rel-", suffix=".json")
    os.close(fd)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "OMP_NUM_THREADS": "4",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "OGC_SEED": str(args.seed),
    }
    tl_arg = "0" if args.tl is None else str(args.tl)
    err = None
    payload = None
    t0 = time.monotonic()
    try:
        proc = subprocess.Popen(
            [args.python, "-c", _CANDIDATE_MAIN, args.mode, str(solver),
             str(prob_path), tl_arg, sol_tmp],
            stdout=sys.stderr, stderr=sys.stderr, env=env,
            start_new_session=True)
        budget = 300.0 if args.tl is None else args.tl + 60.0
        try:
            rc = proc.wait(timeout=budget)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            raise RuntimeError("candidate subprocess timed out")
        if rc != 0:
            raise RuntimeError(f"candidate subprocess exited {rc}")
        payload = json.loads(pathlib.Path(sol_tmp).read_text())
    except BaseException as exc:
        err = f"{type(exc).__name__}: {exc}"
    finally:
        wall = time.monotonic() - t0
        pathlib.Path(sol_tmp).unlink(missing_ok=True)

    if rs.sha256_file(prob_path) != input_sha:
        err = err or "candidate mutated the authoritative input"
        payload = None

    child = resource.getrusage(resource.RUSAGE_CHILDREN)
    assertions = []
    result = {"wall": round(wall, 2), "maxRssMb": round(child.ru_maxrss / 1024, 1),
              "cpus": len(os.sched_getaffinity(0)), "error": err,
              "feasible": False, "overrun": bool(args.tl and wall > args.tl),
              "objective": None, "z1": None, "z2": None, "z3": None,
              "solutionSha256": None, "checkerReplayStatus": None,
              "blockerActivations": None}
    if payload is not None:
        sol = payload["solution"]
        result["blockerActivations"] = payload["meta"]["blockerActivations"]
        sol_bytes = json.dumps(sol, separators=(",", ":")).encode()
        result["solutionSha256"] = hashlib.sha256(sol_bytes).hexdigest()
        if args.solution_out:
            pathlib.Path(args.solution_out).parent.mkdir(parents=True, exist_ok=True)
            pathlib.Path(args.solution_out).write_bytes(sol_bytes)
        res = checker.check_feasibility(
            json.loads(json.dumps(prob)), json.loads(json.dumps(sol)))
        result.update(feasible=bool(res.get("feasible")),
                      objective=res.get("objective"),
                      z1=res.get("obj1"), z2=res.get("obj2"), z3=res.get("obj3"),
                      checkerReplayStatus="feasible" if res.get("feasible")
                      else f"infeasible:{res.get('stage')}")

    assertions.append({"id": "no-error", "status": "PASS" if err is None else "FAIL",
                       "detail": err or ""})
    assertions.append({"id": "checker-feasible",
                       "status": "PASS" if result["feasible"] else "FAIL",
                       "detail": f"objective={result['objective']}"})
    assertions.append({"id": "no-overrun",
                       "status": "FAIL" if result["overrun"] else "PASS",
                       "detail": f"wall={result['wall']} tl={args.tl}"})
    if args.mode == "noort":
        ok = bool(result["blockerActivations"])
        assertions.append({"id": "ortools-blocker-active",
                           "status": "PASS" if ok else "FAIL",
                           "detail": f"activations={result['blockerActivations']}"})
    status = "PASS" if all(a["status"] == "PASS" for a in assertions) else "FAIL"

    receipt = {
        "schemaVersion": rs.SCHEMA_VERSION,
        "kind": "e2e-case",
        "status": status,
        "createdAt": rs.now_iso(),
        "gateId": args.gate_id,
        "caseId": args.case_id,
        "tool": {"path": "tools/release_run_instance.py",
                 "sha256": rs.sha256_file(__file__)},
        "lineage": {"releaseZipSha256": args.release_zip_sha256,
                    "solverTreeSha256": tree_sha,
                    "canonicalSoSha256": so_sha,
                    "checkerSha256": checker_sha},
        "case": {"dataset": args.dataset, "instance": args.instance,
                 "inputSha256": input_sha, "mode": args.mode,
                 "seed": args.seed, "tl": args.tl},
        "slurm": {"jobId": os.environ.get("SLURM_JOB_ID", "local"),
                  "stepId": os.environ.get("SLURM_STEP_ID"),
                  "partition": os.environ.get("SLURM_JOB_PARTITION", "local"),
                  "node": os.environ.get("SLURMD_NODENAME")},
        "result": result,
        "assertions": assertions,
    }
    receipt_sha = rs.write_receipt(args.receipt, receipt)
    print(json.dumps({"caseId": args.case_id, "status": status,
                      "objective": result["objective"],
                      "receipt": args.receipt, "receiptSha256": receipt_sha}))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
