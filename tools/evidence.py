"""Strict validation shared by Slurm evidence freezing and comparison."""
import hashlib
import json
import math
import pathlib

OFFICIAL_LIMITS = {"prob_1": 60, "prob_2": 120, "prob_3": 240,
                   "prob_4": 480, "prob_5": 600, "prob_6": 900}


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def tree_hash(root):
    root = pathlib.Path(root)
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = str(path.relative_to(root)).encode()
        data = path.read_bytes()
        h.update(len(rel).to_bytes(8, "big")); h.update(rel)
        h.update(len(data).to_bytes(8, "big")); h.update(data)
    return h.hexdigest()


def named_files_hash(paths):
    h = hashlib.sha256()
    for path in paths:
        path = pathlib.Path(path)
        data = path.read_bytes()
        h.update(path.name.encode()); h.update(len(data).to_bytes(8, "big")); h.update(data)
    return h.hexdigest()


def data_hash(data_dir, expected):
    h = hashlib.sha256()
    for inst in expected:
        path = pathlib.Path(data_dir) / f"{inst}.json"
        data = path.read_bytes()
        h.update(path.name.encode()); h.update(len(data).to_bytes(8, "big")); h.update(data)
    return h.hexdigest()


def manifest_digest(manifest):
    body = dict(manifest)
    body.pop("manifest_sha256", None)
    payload = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return sha256_bytes(payload)


def validate_records(meta, manifest, records, require_official=False):
    errors = []
    expected = manifest.get("expected_instances")
    if not isinstance(expected, list) or not expected or len(expected) != len(set(expected)):
        errors.append("manifest expected_instances is missing, empty, or duplicated")
        expected = []
    if set(records) != set(expected):
        errors.append(f"coverage mismatch: expected={expected}, actual={sorted(records)}")
    if manifest.get("manifest_sha256") != manifest_digest(manifest):
        errors.append("manifest digest mismatch")
    if manifest.get("tag") != meta.get("tag"):
        errors.append("manifest/meta tag mismatch")
    if manifest.get("expected_instances") != meta.get("expected_instances"):
        errors.append("manifest/meta expected_instances mismatch")
    for key in ("solver_snapshot_sha256", "checker_sha256", "harness_sha256",
                "runner_sha256", "data_snapshot_sha256", "experiment_env"):
        if key not in meta or manifest.get(key) != meta.get(key):
            errors.append(f"manifest/meta {key} mismatch")
    jobs = {str(x) for x in manifest.get("job_ids", [])}
    if not jobs:
        errors.append("manifest has no Slurm job IDs")
    tl_map = meta.get("timelimits", {})
    instance_hashes = manifest.get("instance_sha256", {})
    for inst in expected:
        rec = records.get(inst, {})
        expected_tl = tl_map.get(inst, meta.get("timelimit"))
        objective = rec.get("objective")
        if rec.get("data") != meta.get("data") or rec.get("seed") != meta.get("pseed"):
            errors.append(f"{inst}: dataset/seed mismatch")
        for key in ("checker_sha256", "harness_sha256", "runner_sha256",
                    "data_snapshot_sha256", "experiment_env"):
            if rec.get(key) != meta.get(key):
                errors.append(f"{inst}: {key} mismatch")
        if rec.get("instance_sha256") != instance_hashes.get(inst):
            errors.append(f"{inst}: input hash mismatch")
        if rec.get("tl") != expected_tl:
            errors.append(f"{inst}: time limit mismatch")
        if require_official and expected_tl != OFFICIAL_LIMITS.get(inst):
            errors.append(f"{inst}: not evaluated at official limit")
        if (not rec.get("feasible") or rec.get("error") or rec.get("overrun")
                or not isinstance(objective, (int, float)) or not math.isfinite(objective)
                or objective < 0):
            errors.append(f"{inst}: non-adoptable result")
        if not isinstance(rec.get("solution_sha256"), str):
            errors.append(f"{inst}: missing solution digest")
        if not isinstance(rec.get("runtime_sha256"), str) or not rec.get("runtime"):
            errors.append(f"{inst}: missing runtime identity")
        if not rec.get("slurm_job_id") or str(rec.get("slurm_job_id")) not in jobs:
            errors.append(f"{inst}: unbound Slurm job")
        if rec.get("slurm_partition") != meta.get("partition"):
            errors.append(f"{inst}: partition mismatch")
        if not rec.get("slurm_step_id"):
            errors.append(f"{inst}: missing Slurm step")
        if rec.get("cpus") != 4:
            errors.append(f"{inst}: expected exactly four CPUs")
        if (rec.get("maxrss_mb") is None or rec["maxrss_mb"] > 16 * 1024):
            errors.append(f"{inst}: memory envelope violated")
    return errors


def scheduler_step_is_compliant(receipts, job_id, step_id, partition):
    """Bind a four-core step to its 16 GiB share of the recorded node allocation."""
    rows = [line.split("|") for line in receipts.splitlines()]
    job_id = str(job_id)
    step = f"{job_id}.{step_id}"
    parent_ok = any(
        len(row) >= 9 and row[0] == job_id and row[2] == "COMPLETED"
        and row[3] == "0:0" and row[6].startswith("160G")
        and row[7] == "40" and row[8] == partition
        for row in rows)
    for row in rows:
        if (len(row) < 9 or row[0] != step or row[2] != "COMPLETED"
                or row[3] != "0:0" or row[7] != "4"):
            continue
        if row[6].startswith("16G") and row[8] == partition:
            return True
        # Slurm commonly leaves step ReqMem/Partition blank. The parent receipt
        # then proves the 160 GiB / 40 CPU allocation from which this exclusive
        # four-CPU step receives the eval.sbatch-declared 16 GiB envelope.
        if not row[6] and row[8] in ("", partition) and parent_ok:
            return True
    return False


def validate_frozen_payload(payload, require_official=False):
    meta = payload.get("meta", {})
    manifest = payload.get("manifest", {})
    records = payload.get("runs", {})
    errors = validate_records(meta, manifest, records, require_official)
    receipts = payload.get("scheduler_receipts", "")
    for job in map(str, manifest.get("job_ids", [])):
        rows = [line for line in receipts.splitlines()
                if line.split("|", 1)[0] == job
                or line.split("|", 1)[0].startswith(f"{job}.")]
        if not rows or not any("|COMPLETED|0:0|" in line for line in rows):
            errors.append(f"job {job}: missing completed scheduler receipt")
    for inst, rec in records.items():
        if not scheduler_step_is_compliant(
                receipts, rec.get("slurm_job_id"), rec.get("slurm_step_id"),
                meta.get("partition")):
            step = f"{rec.get('slurm_job_id')}.{rec.get('slurm_step_id')}"
            errors.append(f"{inst}: missing compliant completed step receipt {step}")
    body = dict(payload)
    digest = body.pop("evidence_sha256", None)
    actual = sha256_bytes(json.dumps(body, sort_keys=True, separators=(",", ":")).encode())
    if digest != actual:
        errors.append("frozen evidence digest mismatch")
    return errors
