#!/usr/bin/env python3
"""Run ONE treatment of ONE matched pair for the population production gate.

Self-contained: verifies every snapshot hash it consumes against the gate
MANIFEST before executing, isolates the solver in a fresh subprocess, then
replays the returned solution through the pinned official checker bytes.
Emits one receipt JSON. Never overwrites an existing receipt.
"""
import argparse
import hashlib
import json
import os
import pathlib
import resource
import signal
import subprocess
import sys
import tempfile
import time
import types


def sha(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def tree_sha(d):
    items = [(str(p.relative_to(d)), sha(p))
             for p in sorted(pathlib.Path(d).rglob("*")) if p.is_file()]
    return hashlib.sha256(json.dumps(items).encode()).hexdigest()


def _candidate_main():
    solver = pathlib.Path(sys.argv[2])
    prob_path = pathlib.Path(sys.argv[3])
    tl = float(sys.argv[4])
    sol_path = pathlib.Path(sys.argv[5])
    checker_path = pathlib.Path(sys.argv[6])
    import importlib.util
    spec = importlib.util.spec_from_file_location("utils", checker_path)
    checker = importlib.util.module_from_spec(spec)
    sys.modules["utils"] = checker
    spec.loader.exec_module(checker)
    sys.path.insert(0, str(solver))
    import myalgorithm
    prob = json.loads(prob_path.read_text())
    sol = myalgorithm.algorithm(prob, tl)
    sol_path.write_text(json.dumps(sol))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate-dir", required=True)
    ap.add_argument("--pair-id", required=True)
    ap.add_argument("--phase", type=int, required=True)
    ap.add_argument("--treatment", choices=["control", "candidate"],
                    required=True)
    ap.add_argument("--instance", type=int, required=True)
    ap.add_argument("--tl", type=float, required=True)
    ap.add_argument("--seed", type=int, required=True)
    args = ap.parse_args()

    gate = pathlib.Path(args.gate_dir)
    manifest = json.loads((gate / "MANIFEST.json").read_text())
    out = gate / "pairs" / args.pair_id / f"{args.phase}-{args.treatment}.json"
    if out.exists():
        print(f"receipt exists, refusing: {out}", file=sys.stderr)
        sys.exit(3)
    out.parent.mkdir(parents=True, exist_ok=True)

    tdir = gate / "snapshot" / args.treatment
    texp = manifest["treatments"][args.treatment]
    actual_tree = tree_sha(tdir)
    input_path = gate / "snapshot/inputs" / f"prob_{args.instance}.json"
    checker_path = gate / "snapshot/checker/utils.py"
    problems = []
    if actual_tree != texp["treeSha256"]:
        problems.append("treatment tree drift")
    so = tdir / "sscore.cpython-312-x86_64-linux-gnu.so"
    if sha(so) != texp["soSha256"]:
        problems.append("treatment .so drift")
    if sha(input_path) != manifest["inputs"][str(args.instance)]:
        problems.append("input drift")
    if sha(checker_path) != manifest["checkerSha256"]:
        problems.append("checker drift")
    if problems:
        rec = {"error": "; ".join(problems), "feasible": False}
        out.write_text(json.dumps(rec))
        print(json.dumps(rec), file=sys.stderr)
        sys.exit(2)

    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("OGC_",))}
    env.update(OMP_NUM_THREADS="4", OPENBLAS_NUM_THREADS="1",
               MKL_NUM_THREADS="1", OGC_SEED=str(args.seed))

    fd, sol_name = tempfile.mkstemp(prefix="gate-sol-", suffix=".json")
    os.close(fd)
    sol_path = pathlib.Path(sol_name)
    err = None
    sol = None
    t0 = time.monotonic()
    try:
        cmd = [sys.executable, str(pathlib.Path(__file__).resolve()),
               "--candidate", str(tdir), str(input_path), str(args.tl),
               str(sol_path), str(checker_path)]
        proc = subprocess.Popen(cmd, stdout=sys.stderr, stderr=sys.stderr,
                                start_new_session=True, env=env)
        try:
            rc = proc.wait(timeout=args.tl + 25.0)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            raise RuntimeError("treatment subprocess timed out")
        if rc != 0:
            raise RuntimeError(f"treatment subprocess exited {rc}")
        sol = json.loads(sol_path.read_text())
    except BaseException as exc:
        err = f"{type(exc).__name__}: {exc}"
    wall = time.monotonic() - t0
    sol_path.unlink(missing_ok=True)

    if sha(input_path) != manifest["inputs"][str(args.instance)]:
        err = err or "input mutated during run"
        sol = None

    # official checker replay with pinned bytes, isolated module
    feas = {}
    if sol is not None:
        src = checker_path.read_bytes()
        mod = types.ModuleType("_gate_checker")
        exec(compile(src, str(checker_path), "exec"), mod.__dict__)
        prob = json.loads(input_path.read_text())
        feas = mod.check_feasibility(
            json.loads(json.dumps(prob)), json.loads(json.dumps(sol)))

    child = resource.getrusage(resource.RUSAGE_CHILDREN)
    rec = {
        "schemaVersion": 1,
        "pairId": args.pair_id,
        "phase": args.phase,
        "treatment": args.treatment,
        "instance": args.instance,
        "tl": args.tl,
        "seed": args.seed,
        "node": os.uname().nodename,
        "affinity": sorted(os.sched_getaffinity(0)),
        "cpus": len(os.sched_getaffinity(0)),
        "slurmJobId": os.environ.get("SLURM_JOB_ID"),
        "slurmArrayTaskId": os.environ.get("SLURM_ARRAY_TASK_ID"),
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "wall": round(wall, 2),
        "overrun": wall > args.tl,
        "maxrssMb": round(child.ru_maxrss / 1024, 1),
        "error": err,
        "feasible": bool(feas.get("feasible")) if sol is not None else False,
        "objective": feas.get("objective"),
        "obj1": feas.get("obj1"), "obj2": feas.get("obj2"),
        "obj3": feas.get("obj3"),
        "solutionSha256": hashlib.sha256(
            json.dumps(sol, separators=(",", ":")).encode()).hexdigest()
            if sol is not None else None,
        "treatmentTreeSha256": actual_tree,
        "treatmentSoSha256": texp["soSha256"],
        "checkerSha256": manifest["checkerSha256"],
        "inputSha256": manifest["inputs"][str(args.instance)],
        "harnessSha256": sha(pathlib.Path(__file__).resolve()),
        "candidateSealSha256": manifest["candidateSealSha256"],
        "closureManifestSha256": manifest["closureManifestSha256"],
    }
    if sol is not None and rec["feasible"]:
        sols_dir = gate / "solutions"
        sols_dir.mkdir(exist_ok=True)
        (sols_dir / f"{args.pair_id}-{args.treatment}.sol.json").write_text(
            json.dumps(sol, separators=(",", ":")))
    out.write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: rec[k] for k in
                      ("pairId", "treatment", "feasible", "objective",
                       "wall", "error")}))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--candidate":
        _candidate_main()
    else:
        main()
