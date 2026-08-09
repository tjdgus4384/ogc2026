#!/usr/bin/env python3
"""Independent, fail-closed audit for the structural campaign.

This module never submits or cancels jobs.  It consumes sealed JSON evidence and
returns a deterministic verdict, making it suitable for H70--H72 query-only
reconciliation and independent replay.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

SCORE_ROWS = 88
BACKGROUND_ROWS = 22
TOTAL_ROWS = SCORE_ROWS + BACKGROUND_ROWS
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "NOT_ADMITTED", "AMBIGUOUS_FENCED"}
LIVE = {"PENDING", "CONFIGURING", "RUNNING", "COMPLETING", "SUSPENDED"}
IDENTITIES = ("source_sha256", "build_sha256", "runtime_sha256", "result_sha256")
SEED_GROUPS = ("discovery", "holdout", "m5", "release")
RESOURCE_BUDGET = {
    "score_node_wall_seconds": 88096,
    "background_node_wall_seconds": 25500,
    "total_node_wall_seconds": 113596,
    "total_core_seconds": 473584,
    "total_gib_seconds": 1651936,
    "max_slurm_jobs": 8,
    "max_slurm_cores": 32,
    "max_slurm_gib": 112,
}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _duplicates(values: Iterable[Any]) -> set[Any]:
    seen: set[Any] = set()
    duplicate: set[Any] = set()
    for value in values:
        if value in seen:
            duplicate.add(value)
        seen.add(value)
    return duplicate


def _error(errors: list[dict[str, str]], code: str, detail: str) -> None:
    errors.append({"code": code, "detail": detail})


def audit_contract(root: Path, contract: dict[str, Any], errors: list[dict[str, str]]) -> None:
    if contract.get("schema_version") != 1:
        _error(errors, "CONTRACT_SCHEMA", "schema_version must be 1")
    if not contract.get("campaign_id"):
        _error(errors, "CONTRACT_ID", "campaign_id is missing")
    protected = contract.get("protected_files")
    if not isinstance(protected, dict) or not protected:
        _error(errors, "PROTECTED_MANIFEST", "protected_files must be a non-empty object")
    else:
        for relative, expected in sorted(protected.items()):
            relative_path = Path(relative)
            if relative_path.is_absolute() or ".." in relative_path.parts or not _is_sha256(expected):
                _error(errors, "PROTECTED_MANIFEST", str(relative))
                continue
            path = root / relative_path
            if not path.is_file():
                _error(errors, "PROTECTED_DRIFT", f"missing:{relative}")
            elif file_digest(path) != expected:
                _error(errors, "PROTECTED_DRIFT", f"hash:{relative}")
    authorities = contract.get("authorities", {})
    for name in ("checker", "reducer", "score_table", "score_tool"):
        value = authorities.get(name)
        if not _is_sha256(value):
            _error(errors, "AUTHORITY_UNSEALED", name)
    budget = contract.get("resource_budget")
    if budget != RESOURCE_BUDGET:
        _error(errors, "RESOURCE_CONTRACT", "resource budget differs from the sealed maximum")
    groups = contract.get("seed_groups", {})
    seen: dict[int, str] = {}
    for group in SEED_GROUPS:
        seeds = groups.get(group)
        if not isinstance(seeds, list) or any(type(seed) is not int for seed in seeds):
            _error(errors, "SEED_CONTRACT", group)
            continue
        if len(seeds) != len(set(seeds)):
            _error(errors, "SEED_DUPLICATE", group)
        for seed in seeds:
            prior = seen.get(seed)
            if prior is not None:
                _error(errors, "SEED_OVERLAP", f"{seed}:{prior}:{group}")
            seen[seed] = group
    for row in contract.get("rows", []):
        if row.get("class") != "score":
            continue
        stage = row.get("stage")
        if stage in SEED_GROUPS and row.get("seed") not in groups.get(stage, []):
            _error(errors, "SEED_PLAN_DRIFT", f"{row.get('row_id')}:{stage}:{row.get('seed')}")
        if row.get("order") not in {"AB", "BA"}:
            _error(errors, "PAIR_ORDER_CONTRACT", str(row.get("row_id")))
        for key in IDENTITIES:
            if not _is_sha256(row.get(key)):
                _error(errors, "IDENTITY_UNSEALED", f"{row.get('row_id')}:{key}")


def audit_ledger(contract: dict[str, Any], ledger: list[dict[str, Any]],
                 errors: list[dict[str, str]], final: bool) -> dict[str, dict[str, Any]]:
    planned = contract.get("rows", [])
    planned_ids = [row.get("row_id") for row in planned]
    if final:
        score_count = sum(row.get("class") == "score" for row in planned)
        background_count = sum(row.get("class") == "background" for row in planned)
        if (score_count, background_count) != (SCORE_ROWS, BACKGROUND_ROWS):
            _error(errors, "LEDGER_SIZE", f"score={score_count},background={background_count}")
    if None in planned_ids or _duplicates(planned_ids):
        _error(errors, "PLAN_ROW_ID", "planned row IDs must be present and unique")
    ledger_ids = [row.get("row_id") for row in ledger]
    if None in ledger_ids or _duplicates(ledger_ids):
        _error(errors, "DUPLICATE_ATTEMPT", "ledger row IDs must be present and unique")
    if set(ledger_ids) != set(planned_ids):
        _error(errors, "MISSING_OR_EXTRA_ROW", "ledger IDs differ from the sealed plan")
    by_id = {row.get("row_id"): row for row in ledger}
    job_ids: list[str] = []
    for planned_row in planned:
        row_id = planned_row.get("row_id")
        row = by_id.get(row_id)
        if row is None:
            continue
        status = row.get("status")
        attempts = row.get("attempts")
        expected_attempts = 0 if status == "NOT_ADMITTED" else 1
        if attempts != expected_attempts:
            _error(errors, "DUPLICATE_ATTEMPT", f"{row_id}:attempts={attempts}")
        if final and status not in TERMINAL:
            _error(errors, "H72_OPEN_ROW", f"{row_id}:{status}")
        if row.get("nodes", 1) != 1:
            _error(errors, "RESOURCE_CAP_INVALID", f"{row_id}:nodes")
        if row.get("array", False):
            _error(errors, "RESOURCE_CAP_INVALID", f"{row_id}:array")
        if row.get("direct_submit", False) or not row.get("controller_admitted", status == "NOT_ADMITTED"):
            _error(errors, "UNAUTHORIZED_JOB", str(row_id))
        job_id = row.get("job_id")
        if status != "NOT_ADMITTED":
            if not job_id:
                _error(errors, "MISSING_JOB_ID", str(row_id))
            else:
                job_ids.append(str(job_id))
        admitted_hour = row.get("admitted_hour")
        if admitted_hour is not None and (type(admitted_hour) not in (int, float) or admitted_hour >= 70):
            _error(errors, "H70_ADMISSION", str(row_id))
        for key in ("class", "stage", "candidate", "problem", "seed", "tl_seconds", "order"):
            if key in planned_row and row.get(key) != planned_row[key]:
                _error(errors, "PLAN_DRIFT", f"{row_id}:{key}")
    if _duplicates(job_ids):
        _error(errors, "DUPLICATE_ATTEMPT", "a Slurm root allocation is bound twice")
    for observation in contract.get("active_observations", []):
        total = observation.get("active_total", 0)
        evaluation = observation.get("active_evaluation", 0)
        background = observation.get("active_background", 0)
        if total > 8 or evaluation > 6 or background > 2:
            _error(errors, "RESOURCE_CAP_INVALID", f"active={total}/{evaluation}/{background}")
    return by_id


def audit_results(contract: dict[str, Any], results: list[dict[str, Any]],
                  ledger_by_id: dict[str, dict[str, Any]], errors: list[dict[str, str]]) -> None:
    expected = {row["row_id"] for row in contract.get("rows", []) if row.get("class") == "score" and
                ledger_by_id.get(row["row_id"], {}).get("status") == "COMPLETED"}
    result_ids = [row.get("row_id") for row in results]
    if _duplicates(result_ids):
        _error(errors, "DUPLICATE_RESULT", "duplicate result row")
    if set(result_ids) != expected:
        _error(errors, "MISSING_OR_EXTRA_RESULT", "completed score rows and results differ")
    plan_by_id = {row["row_id"]: row for row in contract.get("rows", [])}
    authorities = contract.get("authorities", {})
    for result in results:
        row_id = result.get("row_id")
        plan = plan_by_id.get(row_id, {})
        ledger = ledger_by_id.get(row_id, {})
        if result.get("job_id") != ledger.get("job_id"):
            _error(errors, "RESULT_IDENTITY_MISMATCH", f"{row_id}:job_id")
        for key in ("seed", "problem", "tl_seconds"):
            if key in plan and result.get(key) != plan[key]:
                _error(errors, "RESULT_IDENTITY_MISMATCH", f"{row_id}:{key}")
        for key in IDENTITIES:
            expected_hash = plan.get(key)
            if expected_hash is not None and result.get(key) != expected_hash:
                _error(errors, "RESULT_IDENTITY_MISMATCH", f"{row_id}:{key}")
        for key in ("checker", "reducer", "score_table", "score_tool"):
            field = f"{key}_sha256"
            if result.get(field) != authorities.get(key):
                _error(errors, "RESULT_IDENTITY_MISMATCH", f"{row_id}:{field}")
        baseline, treatment = result.get("baseline", {}), result.get("treatment", {})
        for key in ("node", "cpuset", "seed", "tl_seconds"):
            if baseline.get(key) != treatment.get(key):
                _error(errors, "UNMATCHED_PAIR", f"{row_id}:{key}")
        if result.get("order") not in {"AB", "BA"}:
            _error(errors, "UNMATCHED_PAIR", f"{row_id}:order")
        for arm_name, arm in (("baseline", baseline), ("treatment", treatment)):
            objective = arm.get("objective")
            if arm.get("checker_valid") is not True or type(objective) is not int or objective <= 0:
                _error(errors, "CHECKER_INVALID", f"{row_id}:{arm_name}")


def audit_sacct(ledger_by_id: dict[str, dict[str, Any]], sacct: list[dict[str, Any]],
                errors: list[dict[str, str]], final: bool) -> None:
    roots = [row for row in sacct if "." not in str(row.get("job_id", ""))]
    by_job = {str(row.get("job_id")): row for row in roots}
    if len(by_job) != len(roots):
        _error(errors, "SACCT_DUPLICATE", "duplicate root allocation")
    for ledger in ledger_by_id.values():
        if ledger.get("status") == "NOT_ADMITTED":
            continue
        job_id = str(ledger.get("job_id"))
        row = by_job.get(job_id)
        if row is None:
            _error(errors, "SACCT_MISSING", job_id)
            continue
        if row.get("nodes") != 1:
            _error(errors, "RESOURCE_CAP_INVALID", f"sacct:{job_id}:nodes")
        state = str(row.get("state", "")).split("+")[0]
        if final and state in LIVE:
            _error(errors, "EXTERNAL_BLOCKED_LIVE_JOB", job_id)
    if final and any(str(row.get("state", "")).split("+")[0] in LIVE for row in roots):
        _error(errors, "EXTERNAL_BLOCKED_LIVE_JOB", "live campaign allocation at closure")


def audit_bundle(root: Path, bundle: dict[str, Any]) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    contract = bundle.get("contract", {})
    final = bundle.get("phase") == "H72"
    audit_contract(root, contract, errors)
    ledger_by_id = audit_ledger(contract, bundle.get("ledger", []), errors, final)
    audit_results(contract, bundle.get("results", []), ledger_by_id, errors)
    audit_sacct(ledger_by_id, bundle.get("sacct", []), errors, final)
    declared = bundle.get("bundle_sha256")
    unsigned = {key: value for key, value in bundle.items() if key != "bundle_sha256"}
    if declared != digest(unsigned):
        _error(errors, "BUNDLE_HASH", "bundle digest is absent or incorrect")
    codes = {item["code"] for item in errors}
    if "EXTERNAL_BLOCKED_LIVE_JOB" in codes:
        verdict = "EXTERNAL_BLOCKED_LIVE_JOB"
    elif errors:
        verdict = f"CAMPAIGN_INVALID:{errors[0]['code']}"
    elif not final:
        verdict = "IN_PROGRESS"
    else:
        selected = bundle.get("selected_candidate")
        eligible = set(bundle.get("eligible_candidates", []))
        if selected is None:
            verdict = "NO_SURVIVOR"
        elif selected not in eligible or len(eligible) > 1:
            verdict = "CAMPAIGN_INVALID:SELECTION"
        else:
            verdict = f"PROMOTE:{selected}"
    return {"schema_version": 1, "verdict": verdict, "errors": errors,
            "ledger_rows": len(ledger_by_id), "result_rows": len(bundle.get("results", []))}


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay the structural campaign Lane-D audit")
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    bundle = json.loads(args.bundle.read_text())
    report = audit_bundle(args.root.resolve(), bundle)
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["verdict"] in {"IN_PROGRESS", "NO_SURVIVOR"} or report["verdict"].startswith("PROMOTE:") else 2


if __name__ == "__main__":
    raise SystemExit(main())
