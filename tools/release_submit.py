#!/usr/bin/env python3
"""Snapshot the selected release archive into an immutable final-extract and
submit the exact shipcheck matrix as Slurm jobs (one case = one 4CPU/16GB job).

Provenance is the release archive hash + extract tree hash + checker/data
hashes — never the repo dirty flag. Refuses to run if the run root exists.

    python3 tools/release_submit.py \
        --archive 'runs/release/fallback-v53/[OGC2026_AlgorithmCode]_MSGA.zip' \
        --run-root runs/release/e2e-v53 --data-root data [--dry-run]
"""
import argparse
import json
import pathlib
import subprocess
import sys

TOOLS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import release_schema as rs
import release_matrix

SO_NAME = "sscore.cpython-312-x86_64-linux-gnu.so"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--archive", required=True)
    ap.add_argument("--run-root", required=True)
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--partition", default="titan")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    archive = pathlib.Path(args.archive).resolve()
    run_root = pathlib.Path(args.run_root).resolve()
    data_root = pathlib.Path(args.data_root).resolve()
    if run_root.exists():
        print(f"SUBMIT FAIL: run root exists: {run_root}", file=sys.stderr)
        return 2
    run_root.mkdir(parents=True)
    (run_root / "cases").mkdir()
    (run_root / "logs").mkdir()
    (run_root / "solutions").mkdir()

    archive_sha = rs.sha256_file(archive)
    extract_dir = run_root / "final-extract"
    rc = subprocess.run(
        [sys.executable, str(TOOLS / "release_package.py"), "extract",
         "--archive", str(archive), "--output", str(extract_dir),
         "--receipt", str(run_root / "final-extract.json")],
        capture_output=True, text=True)
    if rc.returncode != 0:
        print(f"SUBMIT FAIL: extract: {rc.stderr}", file=sys.stderr)
        return 2
    tree_sha = json.loads(rc.stdout)["treeSha256"]
    checker_sha = rs.sha256_file(extract_dir / "utils.py")
    so_sha = rs.sha256_file(extract_dir / SO_NAME)

    matrix = release_matrix.build()
    matrix_sha = rs.canonical_hash(matrix)
    inputs = {}
    for case in matrix["cases"]:
        p = data_root / case["dataset"] / f"prob_{case['instance']}.json"
        inputs[f"{case['dataset']}/prob_{case['instance']}.json"] = rs.sha256_file(p)

    jobs = []
    for case in matrix["cases"]:
        case_id = case["caseId"]
        argv_case = [
            "--solver", str(extract_dir),
            "--checker", str(extract_dir / "utils.py"),
            "--data-root", str(data_root),
            "--dataset", case["dataset"],
            "--instance", str(case["instance"]),
            "--seed", str(case["seed"]),
            "--mode", case["mode"],
            "--gate-id", case["gateId"],
            "--case-id", case_id,
            "--receipt", str(run_root / "cases" / f"{case_id}.json"),
            "--solution-out", str(run_root / "solutions" / f"{case_id}.sol.json"),
            "--release-zip-sha256", archive_sha,
            "--solver-tree-sha256", tree_sha,
        ]
        if case["tl"] is not None:
            argv_case += ["--tl", str(case["tl"])]
        args_file = run_root / "cases" / f"{case_id}.args.json"
        args_file.write_text(json.dumps(argv_case, indent=1))
        tl = case["tl"] or 300
        minutes = int(tl / 60) + 10
        cmd = ["sbatch", "--parsable", "-p", args.partition,
               f"--time={minutes}",
               "-o", str(run_root / "logs" / f"{case_id}-%j.log"),
               str(TOOLS / "release_eval.sbatch"), str(args_file)]
        if args.dry_run:
            jobs.append({"caseId": case_id, "jobId": None, "cmd": " ".join(cmd)})
            continue
        out = subprocess.run(cmd, capture_output=True, text=True)
        if out.returncode != 0:
            print(f"SUBMIT FAIL: sbatch {case_id}: {out.stderr}", file=sys.stderr)
            return 2
        jobs.append({"caseId": case_id, "jobId": out.stdout.strip()})

    manifest = {
        "schemaVersion": 2,
        "kind": "run-manifest",
        "createdAt": rs.now_iso(),
        "archive": {"path": str(archive), "sha256": archive_sha},
        "extract": {"path": str(extract_dir), "treeSha256": tree_sha,
                    "canonicalSoSha256": so_sha, "checkerSha256": checker_sha},
        "dataRoot": {"path": str(data_root), "inputs": inputs},
        "matrixSha256": matrix_sha,
        "matrix": matrix,
        "jobs": jobs,
    }
    (run_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"runRoot": str(run_root), "archiveSha256": archive_sha,
                      "treeSha256": tree_sha, "matrixSha256": matrix_sha,
                      "jobs": len(jobs),
                      "jobIds": [j["jobId"] for j in jobs]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
