"""Pure regression checks for strict frozen-evidence validation."""
import copy
import hashlib
import json

from evidence import manifest_digest, validate_frozen_payload


def payload():
    meta = {
        "tag": "t", "data": "preliminary_test", "pseed": 7,
        "partition": "titan", "timelimit": "official",
        "timelimits": {"prob_3": 240}, "expected_instances": ["prob_3"],
        "solver_snapshot_sha256": "s", "checker_sha256": "c",
        "harness_sha256": "h", "runner_sha256": "r",
        "data_snapshot_sha256": "d", "experiment_env": {},
    }
    manifest = {
        "schema_version": 1, "tag": "t", "expected_instances": ["prob_3"],
        "job_ids": ["42"], "solver_snapshot_sha256": "s",
        "checker_sha256": "c", "harness_sha256": "h",
        "runner_sha256": "r", "data_snapshot_sha256": "d",
        "experiment_env": {}, "instance_sha256": {"prob_3": "i"},
    }
    manifest["manifest_sha256"] = manifest_digest(manifest)
    record = {
        "inst": "prob_3", "data": "preliminary_test", "seed": 7,
        "checker_sha256": "c", "harness_sha256": "h",
        "runner_sha256": "r", "data_snapshot_sha256": "d",
        "experiment_env": {}, "instance_sha256": "i", "solution_sha256": "x",
        "tl": 240,
        "runtime": {"python": "test"}, "runtime_sha256": "runtime",
        "feasible": True, "objective": 1.0, "error": None, "overrun": False,
        "slurm_job_id": "42", "slurm_step_id": "0",
        "slurm_partition": "titan", "cpus": 4, "maxrss_mb": 100,
    }
    out = {"meta": meta, "manifest": manifest, "runs": {"prob_3": record},
           "scheduler_receipts": (
               "42|eval|COMPLETED|0:0|240|100M|16G|4|titan\n"
               "42.0|python|COMPLETED|0:0|240|100M|16G|4|titan\n")}
    body = json.dumps(out, sort_keys=True, separators=(",", ":")).encode()
    out["evidence_sha256"] = hashlib.sha256(body).hexdigest()
    return out


good = payload()
assert validate_frozen_payload(good, require_official=True) == []
for mutate in (
    lambda p: p["runs"].clear(),
    lambda p: p["runs"]["prob_3"].update(objective=float("nan")),
    lambda p: p["runs"]["prob_3"].update(slurm_job_id=None),
    lambda p: p["manifest"].update(expected_instances=[]),
    lambda p: p.update(scheduler_receipts="42|eval|FAILED|1:0|||||"),
):
    bad = copy.deepcopy(good)
    mutate(bad)
    assert validate_frozen_payload(bad, require_official=True)
print("EVIDENCE GATE PASS: coverage, finite objective, Slurm, manifest, digest")
