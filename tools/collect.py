"""Validate and atomically freeze a completed Slurm evaluation."""
import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import types

from evidence import (data_hash, manifest_digest, named_files_hash, sha256_bytes,
                      tree_hash, validate_records)

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_run(tag):
    run = ROOT / "runs" / tag
    if not run.is_dir():
        sys.exit(f"no runs/{tag}/ -- was the job submitted?")
    try:
        meta = json.loads((run / "META.json").read_text())
        manifest = json.loads((run / "RUN_MANIFEST.json").read_text())
    except Exception as exc:
        sys.exit(f"runs/{tag}/ has invalid metadata: {exc}")
    records = {}
    for path in sorted(run.glob("prob_*.json")):
        if path.name.endswith(".sol.json"):
            continue
        try:
            rec = json.loads(path.read_text())
        except Exception:
            continue
        if "inst" in rec:
            if rec["inst"] in records:
                sys.exit(f"runs/{tag}/ has duplicate record {rec['inst']}")
            records[rec["inst"]] = rec
    return run, meta, manifest, records


def verify_snapshots(run, meta, manifest):
    errors = []
    expected = manifest.get("expected_instances", [])
    solver = run / "solver_snapshot"
    checker = run / "checker_snapshot.py"
    harness = run / "harness_snapshot"
    data_dir = run / "data_snapshot" / meta.get("data", "")
    actual = {
        "solver_snapshot_sha256": tree_hash(solver),
        "checker_sha256": sha256_bytes(checker.read_bytes()),
        "harness_sha256": named_files_hash(
            [harness / "eval.sbatch", harness / "run_instance.py"]),
        "runner_sha256": sha256_bytes((harness / "run_instance.py").read_bytes()),
        "data_snapshot_sha256": data_hash(data_dir, expected),
    }
    for key, value in actual.items():
        if value != meta.get(key) or value != manifest.get(key):
            errors.append(f"snapshot drift: {key}")
    for inst in expected:
        path = data_dir / f"{inst}.json"
        digest = sha256_bytes(path.read_bytes())
        if digest != manifest.get("instance_sha256", {}).get(inst):
            errors.append(f"snapshot drift: {inst}")
    if manifest.get("manifest_sha256") != manifest_digest(manifest):
        errors.append("run manifest digest mismatch")
    return errors


def replay_solutions(run, meta, manifest, records):
    source = (run / "checker_snapshot.py").read_bytes()
    checker = types.ModuleType("_ogc_freeze_checker")
    checker.__file__ = str(run / "checker_snapshot.py")
    sys.modules[checker.__name__] = checker
    exec(compile(source, checker.__file__, "exec"), checker.__dict__)
    errors = []
    data_dir = run / "data_snapshot" / meta["data"]
    for inst in manifest["expected_instances"]:
        solution_path = run / f"{inst}.sol.json"
        if not solution_path.is_file():
            errors.append(f"{inst}: missing authoritative solution")
            continue
        solution_bytes = solution_path.read_bytes()
        if sha256_bytes(solution_bytes) != records[inst].get("solution_sha256"):
            errors.append(f"{inst}: solution hash mismatch")
            continue
        prob = json.loads((data_dir / f"{inst}.json").read_text())
        solution = json.loads(solution_bytes)
        result = checker.check_feasibility(prob, solution)
        record = records[inst]
        if not result.get("feasible"):
            errors.append(f"{inst}: collector checker rejected solution")
            continue
        for key in ("objective", "obj1", "obj2", "obj3"):
            if result.get(key) != record.get(key):
                errors.append(f"{inst}: replayed {key} mismatch")
    return errors


def scheduler_receipts(job_ids, records, partition):
    command = ["sacct", "-P", "-n", "-j", ",".join(map(str, job_ids)),
               "--format=JobIDRaw,JobName,State,ExitCode,ElapsedRaw,MaxRSS,ReqMem,AllocCPUS,Partition"]
    cp = subprocess.run(command, text=True, capture_output=True, check=False)
    if cp.returncode != 0:
        sys.exit(f"sacct failed: {cp.stderr.strip()}")
    text = cp.stdout
    errors = []
    for job in map(str, job_ids):
        rows = [line for line in text.splitlines()
                if line.split("|", 1)[0] in {job, f"{job}.batch", f"{job}.extern"}
                or line.startswith(f"{job}.")]
        if not rows or not any("|COMPLETED|0:0|" in line for line in rows):
            errors.append(f"job {job} lacks a successful completed accounting row")
    for inst, record in records.items():
        step = f"{record.get('slurm_job_id')}.{record.get('slurm_step_id')}"
        rows = [line.split("|") for line in text.splitlines()
                if line.split("|", 1)[0] == step]
        valid = any(
            len(row) >= 9 and row[2] == "COMPLETED" and row[3] == "0:0"
            and row[6].startswith("16G") and row[7] == "4"
            and row[8] == partition
            for row in rows)
        if not valid:
            errors.append(f"{inst} lacks a compliant successful step {step}")
    if errors:
        sys.exit("; ".join(errors))
    return text


def freeze(tag, meta, manifest, records, receipts):
    out = ROOT / "results" / f"{tag}.json"
    if out.exists():
        sys.exit(f"results/{tag}.json already exists -- frozen evidence is immutable")
    payload = {"meta": meta, "manifest": manifest, "runs": records,
               "scheduler_receipts": receipts}
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["evidence_sha256"] = sha256_bytes(body)
    out.parent.mkdir(exist_ok=True)
    encoded = (json.dumps(payload, indent=1, sort_keys=True) + "\n").encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(out, flags, 0o444)
    except FileExistsError:
        sys.exit(f"results/{tag}.json already exists -- frozen evidence is immutable")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        out.unlink(missing_ok=True)
        raise
    print(f"froze results/{tag}.json evidence={payload['evidence_sha256']}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("tag")
    parser.add_argument("--partial", action="store_true",
                        help="inspect current coverage without freezing")
    args = parser.parse_args()
    run, meta, manifest, records = load_run(args.tag)
    expected = manifest.get("expected_instances", [])
    print(f"tag={args.tag} commit={meta.get('solver_commit')} "
          f"instances={len(records)}/{len(expected)}")
    if args.partial:
        print(f"completed={sorted(records)}")
        return
    required = {"tag", "timelimit", "expected_instances", "data", "pseed",
                "partition", "solver_commit", "solver_dirty",
                "solver_snapshot_sha256", "checker_sha256", "harness_sha256",
                "runner_sha256", "data_snapshot_sha256", "experiment_env",
                "job_ids"}
    missing = sorted(required - set(meta))
    if missing:
        sys.exit(f"metadata missing {missing}")
    if meta["tag"] != args.tag or meta["solver_dirty"]:
        sys.exit("mismatched tag or dirty solver snapshot")
    errors = verify_snapshots(run, meta, manifest)
    errors.extend(validate_records(meta, manifest, records))
    errors.extend(replay_solutions(run, meta, manifest, records))
    if errors:
        sys.exit("refusing freeze:\n  " + "\n  ".join(errors))
    receipts = scheduler_receipts(
        manifest["job_ids"], records, meta["partition"])
    freeze(args.tag, meta, manifest, records, receipts)


if __name__ == "__main__":
    main()
