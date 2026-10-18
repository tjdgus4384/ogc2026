#!/usr/bin/env python3
"""Submit the ONE pre-registered 24-pair population production gate.

Hard prerequisites (fail closed, submit zero jobs otherwise):
  * candidate-seal.json exists with gateEligible=true and intact hashes
  * the compliant v5.3 fallback is ready (receipt path passed by the
    operator; recorded in the gate manifest)
  * the gate tag has never been used (no existing snapshot/result)

Builds content-addressed snapshots of control (canonical extract), candidate
(sealed source + tested .so), inputs, checker and harness; writes
MANIFEST.json; submits one slurm array 0-23%8 (max 8 concurrent pairs).
"""
import argparse
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import time

TOOLS = pathlib.Path(__file__).resolve().parent
ROOT = TOOLS.parent
WORK = ROOT / "runs/release/population-candidate"
GATE = ROOT / "runs/release/population-gate"
RESULT = ROOT / "results/population-production-gate-v1.json"

SEEDS = [7, 19, 31, 41, 42, 59, 83, 113]
AB_SEEDS = {7, 31, 42, 83}
INSTANCES = {4: 480.0, 5: 600.0, 6: 900.0}


def sha(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def tree_sha(d):
    items = [(str(p.relative_to(d)), sha(p))
             for p in sorted(pathlib.Path(d).rglob("*")) if p.is_file()]
    return hashlib.sha256(json.dumps(items).encode()).hexdigest()


def fail(msg):
    print(f"REFUSE: {msg}", file=sys.stderr)
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fallback-receipt", required=True,
                    help="path to the compliant v5.3 fallback receipt "
                         "(Lane B); recorded in the manifest")
    ap.add_argument("--partition", default="titan")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if RESULT.exists():
        fail(f"gate result already frozen: {RESULT}")
    if GATE.exists():
        fail(f"gate snapshot already exists (one-shot rule): {GATE}")

    seal_path = WORK / "evidence/candidate-seal.json"
    if not seal_path.exists():
        fail("no candidate-seal.json; run prepare seal first")
    seal = json.loads(seal_path.read_text())
    if not seal.get("approval", {}).get("gateEligible"):
        fail(f"candidate not gate-eligible: "
             f"{seal.get('approval', {}).get('reason')}")

    fb = pathlib.Path(args.fallback_receipt)
    if not fb.exists():
        fail(f"fallback receipt not found: {fb}")
    fb_data = json.loads(fb.read_text())
    if fb_data.get("status") not in ("PASS", "ready", "eligible"):
        fail(f"fallback receipt status not ready: {fb_data.get('status')}")

    # sealed candidate runtime tree
    cand_dir = WORK / "solver"
    cand_tree = tree_sha(cand_dir)
    so = cand_dir / "sscore.cpython-312-x86_64-linux-gnu.so"
    if sha(so) != seal["treatment"]["soSha256"]:
        fail("candidate .so does not match seal")
    src_plus_so = seal["treatment"]
    # candidate runtime tree = sealed source + tested so; verify sources
    for p in sorted((WORK / "source").rglob("*")):
        if p.is_file():
            rel = p.relative_to(WORK / "source")
            if sha(cand_dir / rel) != sha(p):
                fail(f"candidate runtime tree drifted from seal: {rel}")

    # control snapshot from canonical zip extract (includes canonical .so)
    policy = json.loads((TOOLS / "population_closure_policy.json").read_text())
    zip_path = ROOT / policy["canonical"]["zipPath"]
    if not zip_path.exists():
        zip_path = pathlib.Path(policy["lineageRoot"]).parent / \
            policy["canonical"]["zipPath"]
    if sha(zip_path) != policy["canonical"]["zipSha256"]:
        fail("canonical zip drift")

    GATE.mkdir(parents=True)
    snap = GATE / "snapshot"
    (snap / "control").mkdir(parents=True)
    subprocess.run(["unzip", "-q", str(zip_path), "-d",
                    str(snap / "control")], check=True)
    shutil.copytree(cand_dir, snap / "candidate")
    (snap / "inputs").mkdir()
    inputs = {}
    for inst in INSTANCES:
        src = ROOT / f"data/preliminary_test/prob_{inst}.json"
        shutil.copyfile(src, snap / "inputs" / f"prob_{inst}.json")
        inputs[str(inst)] = sha(src)
    (snap / "checker").mkdir()
    ctrl_utils = snap / "control/utils.py"
    cand_utils = snap / "candidate/utils.py"
    if sha(ctrl_utils) != sha(cand_utils):
        fail("control/candidate checker bytes differ")
    shutil.copyfile(ctrl_utils, snap / "checker/utils.py")
    (snap / "harness").mkdir()
    shutil.copyfile(TOOLS / "pair_release_run.py",
                    snap / "harness/pair_release_run.py")

    matrix = []
    for inst, tl in INSTANCES.items():
        for seed in SEEDS:
            order = (["control", "candidate"] if seed in AB_SEEDS
                     else ["candidate", "control"])
            matrix.append({"pairId": f"p{inst}-s{seed}",
                           "instance": inst, "tl": tl, "seed": seed,
                           "order": order})
    assert len(matrix) == 24

    manifest = {
        "schemaVersion": 1,
        "tag": "population-production-gate-v1",
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "candidateSealSha256": sha(seal_path),
        "closureManifestSha256": seal["closure"]["manifestSha256"],
        "fallbackReceipt": {"path": str(fb), "sha256": sha(fb),
                            "status": fb_data.get("status")},
        "treatments": {
            "control": {"treeSha256": tree_sha(snap / "control"),
                        "soSha256": sha(snap / "control" /
                                        "sscore.cpython-312-x86_64-linux-gnu.so"),
                        "zipSha256": policy["canonical"]["zipSha256"]},
            "candidate": {"treeSha256": tree_sha(snap / "candidate"),
                          "soSha256": seal["treatment"]["soSha256"],
                          "sourceTreeSha256": src_plus_so["treeSha256"]},
        },
        "testedTreeSha256": tree_sha(snap / "candidate"),
        "testedSoSha256": seal["treatment"]["soSha256"],
        "inputs": inputs,
        "checkerSha256": sha(snap / "checker/utils.py"),
        "harnessSha256": sha(snap / "harness/pair_release_run.py"),
        "matrix": matrix,
        "maxConcurrent": 8,
    }
    (GATE / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    subprocess.run(["chmod", "-R", "a-w", str(snap)], check=True)

    if args.dry_run:
        print("dry-run: snapshot + manifest ready, no jobs submitted")
        return
    r = subprocess.run(
        ["sbatch", "--array=0-23%8", "-p", args.partition,
         str(TOOLS / "pair_release.sbatch"), str(GATE)],
        capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"sbatch failed: {r.stderr}")
    job = r.stdout.strip().split()[-1]
    (GATE / "SUBMITTED.json").write_text(json.dumps(
        {"slurmJobId": job, "submittedAt": time.strftime(
            "%Y-%m-%dT%H:%M:%S%z")}) + "\n")
    print(f"submitted array job {job} (24 pairs, max 8 concurrent)")


if __name__ == "__main__":
    main()
