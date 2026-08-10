#!/usr/bin/env python3
"""Single exact authority for structural-72h admission, reduction, and closure."""
from __future__ import annotations

import argparse
import fcntl
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
CONTRACT_PATH = ROOT / "program_contract.json"
LIVE_PATH = ROOT / "ledger.live.json"
LOCK_PATH = ROOT / ".controller.lock"
REPO_ROOT = ROOT.parents[1]
ACTIVE = {"PENDING", "CONFIGURING", "RUNNING", "COMPLETING", "SUSPENDED", "LEASED"}
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "NOT_ADMITTED", "AMBIGUOUS_FENCED"}
PRECEDENCE = (
    ("RESOURCE_CAP_INVALID", "PROTECTED_DRIFT"),
    ("UNAUTHORIZED", "DUPLICATE_ATTEMPT", "LIVE_H72_ALLOCATION"),
    ("SOURCE_IDENTITY", "BUILD_IDENTITY", "RUNTIME_IDENTITY", "RESULT_IDENTITY", "SCORE_TOOL_IDENTITY"),
    ("HARDWARE_MISMATCH", "TL_MISMATCH", "CPUSET_MISMATCH", "NODE_MISMATCH", "MEMORY_MISMATCH", "OVERRUN"),
    ("CHECKER_INFEASIBLE", "CHECKER_ERROR", "F0_DISAGREEMENT", "EXCEPTION", "FORBIDDEN_FALLBACK"),
    ("MISSING_MEMBER", "CANCELLED_MEMBER", "INCOMPLETE_MEMBER"),
)


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def contract() -> dict[str, Any]:
    return load_json(CONTRACT_PATH)


def score_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    caps = {"P3": 562, "P4": 1042, "P5": 1282, "P6": 1882}
    def add(gate: str, candidates: Iterable[str], seeds: Iterable[int], deadline: int, causal: bool = False) -> None:
        for candidate in candidates:
            for seed in seeds:
                for problem in caps:
                    wall = 142 if causal else caps[problem]
                    rows.append(row(f"score-{gate}-{candidate}-{problem}-s{seed}", "evaluation", gate, wall, deadline,
                                    candidate=candidate, seed=seed, problem=problem))
    add("causal", ("M1", "M2", "M3", "M4"), (101,), 34, True)
    add("discovery", ("M1", "M2", "M3", "M4"), (107, 109), 44)
    add("holdout", ("SURVIVOR_A", "SURVIVOR_B"), (127, 131, 137), 58)
    add("m5", ("M5",), (149, 151, 157), 64)
    add("release", ("WINNER",), (7,), 70)
    return rows


def row(row_id: str, job_class: str, gate: str, wall: int, deadline: int, **fields: Any) -> dict[str, Any]:
    return {"id": row_id, "class": job_class, "gate": gate, "wall_seconds": wall,
            "deadline_hour": deadline, "state": "UNCLAIMED", "attempts": 0, **fields}


def background_rows() -> list[dict[str, Any]]:
    specs = [
        ("program-smoke", 1, 600, 8), ("f0-unit", 1, 1800, 8),
        ("inventory-fixtures", 1, 1200, 8), ("reducer-fixtures", 1, 900, 8),
        ("candidate-compile", 4, 1200, 30), ("candidate-novelty", 4, 1800, 30),
        ("candidate-integration", 4, 900, 30), ("m5-build", 1, 1200, 54),
        ("m5-policy", 1, 900, 54), ("m5-smoke", 1, 600, 54),
        ("release-build", 1, 1200, 66), ("release-unit", 1, 900, 66),
        ("release-equality", 1, 600, 66),
    ]
    rows: list[dict[str, Any]] = []
    for name, count, wall, deadline in specs:
        for index in range(1, count + 1):
            suffix = f"-{index}" if count > 1 else ""
            rows.append(row(f"background-{name}{suffix}", "background", name, wall, deadline))
    return rows


def maximum_ledger(start_ns: int) -> dict[str, Any]:
    rows = score_rows() + background_rows()
    assert len(score_rows()) == 88 and len(background_rows()) == 22 and len(rows) == 110
    assert sum(item["wall_seconds"] for item in rows) == 113596
    return {"schema_version": 1, "campaign_uuid": contract()["campaign_uuid"],
            "start_ns": start_ns, "fenced": False, "promotion_disabled": False,
            "faults": [], "rows": rows}


def verify_protected(base: Path) -> list[str]:
    faults = []
    for relative, expected in contract()["protected"].items():
        path = base / relative
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "MISSING"
        if actual != expected:
            faults.append("PROTECTED_DRIFT")
    return faults


def exact_median(values: list[Fraction]) -> Fraction:
    if not values:
        raise ValueError("median requires values")
    ordered = sorted(values)
    center = len(ordered) // 2
    return ordered[center] if len(ordered) % 2 else (ordered[center - 1] + ordered[center]) / 2


def pair_status(flags: Iterable[str]) -> str:
    present = set(flags)
    for group in PRECEDENCE:
        for code in group:
            if code in present:
                return code
    return "VALID_PAIR"


def reduce_gate(pairs: list[dict[str, Any]], gate: str) -> dict[str, Any]:
    expected = 8 if gate == "discovery" else 12
    if len(pairs) != expected:
        return {"gate": gate, "pass": False, "status": "MISSING_MEMBER"}
    ordered = sorted(pairs, key=lambda item: (int(item["problem"][1:]), int(item["seed"]), item["candidate"].encode()))
    statuses = [pair_status(item.get("flags", ())) for item in ordered]
    if any(status != "VALID_PAIR" for status in statuses):
        return {"gate": gate, "pass": False, "status": next(status for status in statuses if status != "VALID_PAIR"),
                "pair_statuses": statuses}
    ratios = [Fraction(int(item["treatment"]) - int(item["control"]), int(item["control"])) for item in ordered]
    wins = sum(int(item["treatment"]) < int(item["control"]) for item in ordered)
    by_problem = {problem: [ratio for ratio, item in zip(ratios, ordered) if item["problem"] == problem]
                  for problem in ("P3", "P4", "P5", "P6")}
    mean = sum(ratios, Fraction()) / len(ratios)
    medians = {problem: exact_median(values) for problem, values in by_problem.items()}
    active_problems = len({item["problem"] for item in ordered if int(item.get("causal_events", 0)) > 0})
    if gate == "discovery":
        passed = mean <= Fraction(-1, 50) and wins >= 5 and all(value <= Fraction(3, 100) for value in medians.values()) and active_problems >= 2
    else:
        score_by_seed = {
            seed: sum(int(item["score_delta"]) for item in ordered if int(item["seed"]) == seed)
            for seed in sorted({int(item["seed"]) for item in ordered})
        }
        score_by_problem = {
            problem: exact_median([Fraction(int(item["score_delta"]))
                                   for item in ordered if item["problem"] == problem])
            for problem in by_problem
        }
        score_shape = exact_median([Fraction(value) for value in score_by_seed.values()]) >= 8
        score_shape &= all(value >= 0 for value in score_by_problem.values())
        passed = (mean <= Fraction(-1, 50) and exact_median(ratios) <= Fraction(-1, 100) and wins >= 10
                  and all(value <= 0 for value in medians.values()) and all(value <= Fraction(1, 20) for value in ratios)
                  and active_problems >= 2 and any(bool(item.get("strict_board")) for item in ordered)
                  and score_shape)
    def text(value: Fraction) -> str:
        return f"{value.numerator}/{value.denominator}"
    return {"gate": gate, "pass": passed, "status": "PASS" if passed else "GATE_FAIL",
            "mean": text(mean), "median": text(exact_median(ratios)), "wins": wins,
            "per_problem_median": {key: text(value) for key, value in medians.items()},
            "root": digest(ordered)}

def winner_order(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = []
    for candidate in candidates:
        discovery = -Fraction(candidate["discovery_mean"])
        holdout = -Fraction(candidate["holdout_mean"])
        if candidate.get("kind") == "M5":
            robust = min(*(Fraction(value) for value in candidate["parent_robust"]),
                         -Fraction(candidate["m5_mean"]))
        else:
            robust = min(discovery, holdout)
        ranked.append({**candidate, "robust": f"{robust.numerator}/{robust.denominator}"})
    return sorted(ranked, key=lambda item: (-Fraction(item["robust"]),
                                            -Fraction(item["worst_improvement"]),
                                            int(item["allocated_core_ns"]),
                                            item["id"].encode("utf-8")))


def validate_novelty(witness: dict[str, Any]) -> list[str]:
    required = {"mechanism", "abi_hash", "source_hash", "f0_hash", "inventory_hash", "tool_hash",
                "closure_ledger_hash", "fixture_hash", "source_state", "destination_state", "changed_blocks",
                "changed_dimensions", "exact_event_trace", "baseline_status", "baseline_cap_hit", "destination_absent",
                "candidate_trace", "proper_intermediates", "f0_audit", "utils_result", "rollback_hash", "off_hash",
                "closure_rows"}
    faults = [f"MISSING:{key}" for key in sorted(required - witness.keys())]
    if witness.get("baseline_status") != "COMPLETE": faults.append("BASELINE_NOT_COMPLETE")
    if witness.get("baseline_cap_hit") is not False: faults.append("BASELINE_CAP_HIT_OR_UNKNOWN")
    if witness.get("destination_absent") is not True: faults.append("DESTINATION_NOT_EXCLUDED")
    if witness.get("f0_audit") != "PASS" or witness.get("utils_result") != "PASS": faults.append("AUDIT_FAILED")
    if not witness.get("closure_rows") or any(row.get("status") != "DIFFERENTIATED" for row in witness.get("closure_rows", [])):
        faults.append("CLOSURE_UNKNOWN")
    dimensions = set(witness.get("changed_dimensions", []))
    minimum = 3 if witness.get("mechanism") == "M3" else 2
    if len(dimensions) < minimum: faults.append("INSUFFICIENT_COUPLING")
    return faults


def cap_check(observed: dict[str, int]) -> list[str]:
    limits = contract()["caps"]
    return [f"CAP_EXCEEDED:{key}" for key, value in observed.items() if key not in limits or value > int(limits[key])]


def allocation_union(ledger: dict[str, Any], observed: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    by_job = {str(row.get("job_id")): row for row in ledger["rows"] if row.get("job_id")}
    union: dict[str, dict[str, Any]] = {}
    faults: list[str] = []
    campaign_uuid = ledger["campaign_uuid"]
    for item in observed:
        job_id = str(item["job_id"]).split(".")[0]
        marked = campaign_uuid in json.dumps(item, sort_keys=True)
        if job_id in by_job or marked or bool(item.get("identity_match")):
            union[job_id] = item
            if job_id not in by_job:
                faults.append("UNAUTHORIZED")
            if item.get("array") or int(item.get("nodes", 1)) != 1:
                faults.append("RESOURCE_CAP_INVALID")
    for row in ledger["rows"]:
        if row["state"] not in ACTIVE:
            continue
        job_id = str(row.get("job_id") or f"lease:{row['id']}")
        if job_id not in union:
            union[job_id] = {"job_id": job_id, "state": row["state"],
                             "class": row["class"], "lease_unreconciled": True}
    return list(union.values()), sorted(set(faults))


class Controller:
    def __enter__(self) -> "Controller":
        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.lock = LOCK_PATH.open("a+")
        fcntl.flock(self.lock, fcntl.LOCK_EX)
        return self

    def __exit__(self, *_: Any) -> None:
        fcntl.flock(self.lock, fcntl.LOCK_UN)
        self.lock.close()

    def load(self) -> dict[str, Any]:
        return load_json(LIVE_PATH)

    def save(self, ledger: dict[str, Any]) -> None:
        temporary = LIVE_PATH.with_suffix(".tmp")
        payload = canonical(ledger)
        with temporary.open("wb") as stream:
            stream.write(payload); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, LIVE_PATH)
        directory = os.open(ROOT, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)

    def initialize(self, start_ns: int) -> dict[str, Any]:
        if LIVE_PATH.exists():
            raise RuntimeError("ledger already initialized")
        ledger = maximum_ledger(start_ns)
        ledger["faults"] = verify_protected(REPO_ROOT)
        ledger["promotion_disabled"] = bool(ledger["faults"])
        self.save(ledger)
        return ledger

    def admit(self, row_id: str, script: Path, observed: list[dict[str, Any]], now_ns: int, dry_run: bool) -> dict[str, Any]:
        ledger = self.load()
        rows = {item["id"]: item for item in ledger["rows"]}
        if row_id not in rows: raise RuntimeError("unknown ledger row")
        target = rows[row_id]
        if ledger["fenced"] or ledger["promotion_disabled"]: raise RuntimeError("campaign fenced")
        if target["attempts"] != 0 or target["state"] != "UNCLAIMED": raise RuntimeError("duplicate attempt")
        elapsed = Fraction(now_ns - int(ledger["start_ns"]), 1_000_000_000)
        latest_submit = Fraction(int(target["deadline_hour"]) * 3600 - int(target["wall_seconds"]) - 320)
        if elapsed > latest_submit or elapsed + target["wall_seconds"] > 70 * 3600:
            target["state"] = "NOT_ADMITTED"; target["diagnostic"] = "LATEST_SUBMIT_MISSED"; self.save(ledger)
            return target
        union, faults = allocation_union(ledger, observed)
        live = [item for item in union if item.get("state") in ACTIVE]
        class_live = [item for item in live if item.get("class") == target["class"]]
        class_limit = 6 if target["class"] == "evaluation" else 2
        if faults or len(live) >= 8 or len(class_live) >= class_limit:
            if faults or len(live) > 8: ledger["promotion_disabled"] = True
            ledger["faults"] = sorted(set(ledger["faults"] + faults))
            self.save(ledger); raise RuntimeError("admission denied")
        target["attempts"] = 1; target["state"] = "LEASED"; target["lease_ns"] = now_ns
        self.save(ledger)
        if dry_run: return target
        memory = "16G" if target["class"] == "evaluation" else "8G"
        command = ["sbatch", "--parsable", "--nodes=1", "--ntasks=1", "--cpus-per-task=4",
                   f"--mem={memory}", f"--time={target['wall_seconds']}",
                   f"--comment={ledger['campaign_uuid']}", f"--job-name={ledger['campaign_uuid']}:{row_id}", str(script)]
        try:
            completed = subprocess.run(command, check=True, text=True, capture_output=True, timeout=20)
            target["job_id"] = completed.stdout.strip().split(";")[0]
            target["state"] = "PENDING"
        except Exception as error:
            target["state"] = "FAILED"; target["diagnostic"] = type(error).__name__
        self.save(ledger)
        return target

    def close(self, observed: list[dict[str, Any]], now_ns: int, cancel: bool) -> dict[str, Any]:
        ledger = self.load()
        elapsed = Fraction(now_ns - int(ledger["start_ns"]), 1_000_000_000)
        union, faults = allocation_union(ledger, observed)
        live = [item for item in union if item.get("state") in ACTIVE]
        if elapsed >= 70 * 3600:
            ledger["fenced"] = True
            if cancel:
                for item in live:
                    subprocess.run(["scancel", str(item["job_id"])], check=False, timeout=20)
        if elapsed >= 71 * 3600 and live:
            faults.append("LIVE_H72_ALLOCATION")
        if elapsed >= 72 * 3600:
            for item in ledger["rows"]:
                if item["state"] not in TERMINAL:
                    item["state"] = "AMBIGUOUS_FENCED" if item.get("job_id") else "NOT_ADMITTED"
            if live: ledger["promotion_disabled"] = True
        ledger["faults"] = sorted(set(ledger["faults"] + faults))
        ledger["promotion_disabled"] |= bool(set(faults) & {"RESOURCE_CAP_INVALID", "PROTECTED_DRIFT", "UNAUTHORIZED", "LIVE_H72_ALLOCATION"})
        ledger["union_root"] = digest(sorted(union, key=lambda item: str(item["job_id"])))
        self.save(ledger)
        return ledger


def read_observed(path: str | None) -> list[dict[str, Any]]:
    return load_json(Path(path)) if path else []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("init"); initialize.add_argument("--start-ns", type=int, default=time.time_ns())
    admit = commands.add_parser("admit"); admit.add_argument("row_id"); admit.add_argument("script", type=Path)
    admit.add_argument("--observed"); admit.add_argument("--now-ns", type=int, default=time.time_ns()); admit.add_argument("--dry-run", action="store_true")
    close = commands.add_parser("close"); close.add_argument("--observed"); close.add_argument("--now-ns", type=int, default=time.time_ns()); close.add_argument("--cancel", action="store_true")
    reduce = commands.add_parser("reduce"); reduce.add_argument("gate", choices=("discovery", "holdout", "m5")); reduce.add_argument("pairs")
    novelty = commands.add_parser("novelty"); novelty.add_argument("witness")
    verify = commands.add_parser("verify-protected"); verify.add_argument("base", type=Path, nargs="?", default=ROOT.parents[1])
    args = parser.parse_args(argv)
    if args.command == "init":
        with Controller() as controller: output = controller.initialize(args.start_ns)
    elif args.command == "admit":
        with Controller() as controller: output = controller.admit(args.row_id, args.script, read_observed(args.observed), args.now_ns, args.dry_run)
    elif args.command == "close":
        with Controller() as controller: output = controller.close(read_observed(args.observed), args.now_ns, args.cancel)
    elif args.command == "reduce": output = reduce_gate(load_json(Path(args.pairs)), "holdout" if args.gate == "m5" else args.gate)
    elif args.command == "novelty":
        faults = validate_novelty(load_json(Path(args.witness))); output = {"pass": not faults, "faults": faults}
    else:
        faults = verify_protected(args.base); output = {"pass": not faults, "faults": faults}
    sys.stdout.buffer.write(canonical(output))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
