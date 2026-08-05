"""Solve ONE training instance in a fresh process and emit one JSON line.

This is the unit of work for the Slurm array in tools/eval.sbatch. It is
deliberately dumb: one instance, one process, no shared state, so a crash or a
timeout on one instance cannot contaminate another (the June engine had exactly
that failure mode -- see archive/notes-2026-06/SUBMISSIONS.md, p3fix-congested).

Usage:  python tools/run_instance.py <prob_id> <timelimit_seconds> [out.json]
"""
import hashlib
import importlib.util
import importlib.metadata
import json
import os
import pathlib
import platform
import resource
import signal
import subprocess
import sys
import tempfile
import time
import types

ROOT = pathlib.Path(
    os.environ.get("OGC_ROOT", pathlib.Path(__file__).resolve().parent.parent)
).resolve()
DATA_ROOT = pathlib.Path(
    os.environ.get("OGC_DATA_ROOT", ROOT / "data")).resolve()
SOLVER = pathlib.Path(os.environ.get("OGC_SOLVER", ROOT / "solver")).resolve()
CHECKER_PATH = pathlib.Path(
    os.environ.get("OGC_CHECKER", ROOT / "solver" / "utils.py")).resolve()


def _sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def _experiment_env():
    excluded = {
        "OGC_CHECKER", "OGC_CHECKER_SHA256", "OGC_DATA", "OGC_DATA_ROOT",
        "OGC_DATA_SNAPSHOT_SHA256", "OGC_DIAG", "OGC_HARNESS_SHA256",
        "OGC_ROOT", "OGC_SEED", "OGC_SOLVER",
    }
    return {
        key: value for key, value in sorted(os.environ.items())
        if key.startswith("OGC_") and not key.startswith("OGC_INSTANCE_SHA256_")
        and key not in excluded
    }



def _runtime_identity():
    cpu_model = None
    try:
        for line in pathlib.Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                cpu_model = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    packages = {}
    for name in ("ortools", "shapely", "pybind11"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    identity = {
        "python": sys.version,
        "executable_sha256": _sha(sys.executable),
        "machine": platform.machine(),
        "kernel": platform.release(),
        "cpu_model": cpu_model,
        "packages": packages,
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return identity, hashlib.sha256(encoded).hexdigest()

def _load_checker(expected_hash):
    source = CHECKER_PATH.read_bytes()
    actual = hashlib.sha256(source).hexdigest()
    if expected_hash and actual != expected_hash:
        raise RuntimeError(f"checker drift: expected {expected_hash}, got {actual}")
    module = types.ModuleType("_ogc_authoritative_checker")
    module.__file__ = str(CHECKER_PATH)
    sys.modules[module.__name__] = module
    exec(compile(source, str(CHECKER_PATH), "exec"), module.__dict__)
    return module, actual


def _candidate_main():
    """Candidate-only subprocess. Its imports and mutable state never reach checker."""
    prob_path = pathlib.Path(sys.argv[2])
    tl = float(sys.argv[3])
    sol_path = pathlib.Path(sys.argv[4])
    checker_spec = importlib.util.spec_from_file_location("utils", CHECKER_PATH)
    if checker_spec is None or checker_spec.loader is None:
        raise RuntimeError(f"cannot load candidate checker dependency: {CHECKER_PATH}")
    checker = importlib.util.module_from_spec(checker_spec)
    sys.modules["utils"] = checker
    checker_spec.loader.exec_module(checker)
    sys.path.insert(0, str(SOLVER))
    import myalgorithm
    prob = json.loads(prob_path.read_text())
    sol = myalgorithm.algorithm(prob, tl)
    sol_path.write_text(json.dumps(sol))


def _run_candidate(prob_path, tl):
    fd, name = tempfile.mkstemp(prefix="ogc-solution-", suffix=".json")
    os.close(fd)
    sol_path = pathlib.Path(name)
    try:
        command = [sys.executable, str(pathlib.Path(__file__).resolve()),
                   "--candidate", str(prob_path), str(tl), str(sol_path)]
        proc = subprocess.Popen(
            command, stdout=sys.stderr, stderr=sys.stderr, start_new_session=True)
        try:
            returncode = proc.wait(timeout=max(1.0, tl + 15.0))
        except subprocess.TimeoutExpired as exc:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            raise RuntimeError("candidate subprocess timed out") from exc
        if returncode != 0:
            raise RuntimeError(f"candidate subprocess exited {returncode}")
        return json.loads(sol_path.read_text())
    finally:
        sol_path.unlink(missing_ok=True)


def main():
    pid = sys.argv[1]
    tl = float(sys.argv[2])
    out_path = sys.argv[3] if len(sys.argv) > 3 else None
    data = os.environ.get("OGC_DATA", "train")
    prob_path = DATA_ROOT / data / f"prob_{pid}.json"
    initial_instance_hash = _sha(prob_path)
    expected_instance_hash = os.environ.get(f"OGC_INSTANCE_SHA256_{pid}")
    if expected_instance_hash and initial_instance_hash != expected_instance_hash:
        raise RuntimeError("input drift before candidate execution")
    prob = json.loads(prob_path.read_text())
    checker, checker_hash = _load_checker(os.environ.get("OGC_CHECKER_SHA256"))
    runtime, runtime_hash = _runtime_identity()

    t0 = time.monotonic()
    try:
        sol = _run_candidate(prob_path, tl)
        err = None
    except BaseException as exc:
        sol, err = None, f"{type(exc).__name__}: {exc}"
    wall = time.monotonic() - t0

    try:
        final_instance_hash = _sha(prob_path)
        if final_instance_hash != initial_instance_hash:
            raise RuntimeError("candidate mutated the authoritative input")
    except BaseException as exc:
        sol = None
        err = f"{type(exc).__name__}: {exc}"

    self_usage = resource.getrusage(resource.RUSAGE_SELF)
    child_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    rec = {
        "inst": f"prob_{pid}",
        "solver": SOLVER.name,
        "data": data,
        "seed": int(os.environ.get("OGC_SEED", "7")),
        "checker_sha256": checker_hash,
        "harness_sha256": os.environ.get("OGC_HARNESS_SHA256"),
        "runner_sha256": _sha(__file__),
        "instance_sha256": initial_instance_hash,
        "data_snapshot_sha256": os.environ.get("OGC_DATA_SNAPSHOT_SHA256"),
        "experiment_env": _experiment_env(),
        "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
        "slurm_step_id": os.environ.get("SLURM_STEP_ID"),
        "slurm_partition": os.environ.get("SLURM_JOB_PARTITION"),
        "cpus": len(os.sched_getaffinity(0)),
        "runtime": runtime,
        "runtime_sha256": runtime_hash,
        "tl": tl,
        "wall": round(wall, 2),
        "overrun": wall > tl,
        "maxrss_mb": round(max(self_usage.ru_maxrss, child_usage.ru_maxrss) / 1024, 1),
        "n": len(prob["blocks"]) if prob else None,
        "m": len(prob["bays"]) if prob else None,
        "error": err,
    }

    if sol is None:
        rec.update(feasible=False, objective=None, obj1=None, obj2=None, obj3=None)
    else:
        res = checker.check_feasibility(
            json.loads(json.dumps(prob)), json.loads(json.dumps(sol)))
        rec.update(
            feasible=bool(res.get("feasible")),
            objective=res.get("objective"),
            obj1=res.get("obj1"), obj2=res.get("obj2"), obj3=res.get("obj3"),
        )
        if not rec["feasible"]:
            rec["stage"] = res.get("stage")
            rec["violations"] = [str(v) for v in res.get("violations", [])[:3]]

    if out_path and sol is not None:
        solution_path = pathlib.Path(out_path).with_suffix(".sol.json")
        solution_bytes = json.dumps(
            sol, sort_keys=True, separators=(",", ":")).encode()
        solution_path.write_bytes(solution_bytes)
        rec["solution_sha256"] = hashlib.sha256(solution_bytes).hexdigest()
    line = json.dumps(rec)
    print(line, flush=True)
    if out_path:
        pathlib.Path(out_path).write_text(line + "\n")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--candidate":
        _candidate_main()
    else:
        main()
