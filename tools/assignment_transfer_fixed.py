#!/usr/bin/env python3
"""Fixed-iteration plateau-transfer test for one causal bay decision.

The construction screen identifies one checker-authoritative bay intervention
per board.  This harness does not search for another intervention.  It replays
the frozen decision and separates three questions on identical ALNS seeds:

* baseline: the selected free construction, with no persistent lock;
* start:    the causal construction, with the lock released before ALNS;
* lock:     the same causal construction, with that one decision held.

The public-board action is an oracle instrument, not a deployable selector.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pathlib
import random
import statistics
import sys
import time


REPO = pathlib.Path(__file__).resolve().parent.parent
MODES = ("baseline", "start", "lock")
RUN_MODES = ("baseline", "start", "guide60", "lock")


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def flat_digest(flat: list[int]) -> str:
    # Match joint_assignment_rollout.py exactly (no sort_keys on a list).
    return hashlib.sha256(
        json.dumps(flat, separators=(",", ":")).encode()
    ).hexdigest()


def seed_orders(prob: dict) -> dict[str, list[int]]:
    blocks = prob["blocks"]
    n = len(blocks)
    due = [int(round(b["due_date"])) for b in blocks]
    proc = [max(1, int(math.ceil(b["processing_time"] - 1e-9)))
            for b in blocks]
    release = [int(math.ceil(b["release_time"] - 1e-9)) for b in blocks]
    workload = [float(b["workload"]) for b in blocks]
    rng = random.Random(5)
    return dict([
        ("due", sorted(range(n), key=lambda i: due[i])),
        ("slack", sorted(range(n),
                         key=lambda i: (due[i] - proc[i] - release[i],
                                        -workload[i]))),
        ("workload", sorted(range(n),
                            key=lambda i: (-workload[i], due[i]))),
        ("due_long", sorted(range(n), key=lambda i: (due[i], -proc[i]))),
        ("noisy_a", sorted(range(n),
                           key=lambda i: due[i] + rng.uniform(-8, 8))),
        ("noisy_b", sorted(range(n),
                           key=lambda i: due[i] + rng.uniform(-8, 8))),
    ])


def checked_record(ma, prob: dict, eng, n: int) -> dict:
    flat = list(eng.get_solution())
    recs = ma._extract(eng, n)
    ranks = list(eng.entry_ranks())
    raw_solution = ma._recs_to_sol(recs, ranks)
    raw = ma._check(prob, raw_solution)
    if raw and raw.get("feasible"):
        return {
            "feasible": True,
            "objective": float(raw["objective"]),
            "rescued": False,
            "solution_sha256": digest(raw_solution),
            "flat_sha256": flat_digest(flat),
        }
    gated_solution, gated = ma._gate(prob, eng, n)
    if (gated_solution is None or not gated or
            not gated.get("feasible") or
            not isinstance(gated.get("objective"), (int, float))):
        raw_viol = (raw or {}).get("violations", [])
        gate_viol = (gated or {}).get("violations", [])
        return {
            "feasible": False,
            "objective": None,
            "rescued": False,
            "raw_stage": (raw or {}).get("stage"),
            "raw_violations": len(raw_viol),
            "raw_first": str(raw_viol[0])[:200] if raw_viol else None,
            "gated_stage": (gated or {}).get("stage"),
            "gated_violations": len(gate_viol),
            "gated_first": str(gate_viol[0])[:200] if gate_viol else None,
            "flat_sha256": flat_digest(flat),
        }
    return {
        "feasible": True,
        "objective": float(gated["objective"]),
        "rescued": True,
        "solution_sha256": digest(gated_solution),
        "flat_sha256": flat_digest(list(eng.get_solution())),
    }


def run(args) -> None:
    if (args.mode == "lock" and
            os.environ.get("OGC_KEEP_ASSIGNMENT_LOCK") != "1"):
        raise RuntimeError(
            "lock arm requires OGC_KEEP_ASSIGNMENT_LOCK=1; shipped ALNS "
            "releases assignment guidance after 60% of the budget")
    if (args.mode == "guide60" and
            os.environ.get("OGC_KEEP_ASSIGNMENT_LOCK") == "1"):
        raise RuntimeError("guide60 arm must retain the shipped 60% release")
    solver = args.solver.resolve()
    sys.path.insert(0, str(solver))
    import myalgorithm as ma

    problem_path = REPO / "data" / "preliminary_test" / f"prob_{args.instance}.json"
    manifest_path = args.manifest / f"prob_{args.instance}.json"
    prob = json.loads(problem_path.read_text())
    manifest = json.loads(manifest_path.read_text())
    n = len(prob["blocks"])
    action = manifest.get("best_action")
    if not manifest.get("interpretation_valid") or not action:
        raise RuntimeError(f"manifest has no valid causal action: {manifest_path}")
    if not manifest.get("same_bay_null", {}).get("pass"):
        raise RuntimeError(f"causal null did not pass: {manifest_path}")

    orders = seed_orders(prob)
    order_name = manifest["order"]
    if order_name not in orders:
        raise RuntimeError(f"unknown frozen order {order_name}")
    order = orders[order_name]
    block = int(action["block"])
    bay = int(action["bay"])
    free = [-1] * n
    locks = [-1] * n
    locks[block] = bay

    eng, _due, _proc = ma._fresh(prob, 4)

    def reconstruct(asg: list[int]) -> tuple[list[float], list[int]]:
        code = int(eng.reconstruct_ordered(asg, order, False, 7))
        if code != -1:
            raise RuntimeError(f"frozen reconstruction failed at block {code}")
        return list(eng.objective()), list(eng.get_solution())

    base_obj, base_flat = reconstruct(free)
    causal_obj, causal_flat = reconstruct(locks)
    expected_base = manifest["baseline"]
    if (flat_digest(base_flat) != expected_base["solution_sha256"] or
            abs(base_obj[3] - float(expected_base["objective"])) > 1e-6):
        raise RuntimeError("baseline does not replay the construction manifest")
    if (flat_digest(causal_flat) != action["solution_sha256"] or
            abs(causal_obj[3] - float(action["objective"])) > 1e-6):
        raise RuntimeError("causal action does not replay the construction manifest")

    start_flat = base_flat if args.mode == "baseline" else causal_flat
    eng.set_solution(start_flat)
    eng.build_occ(eng.horizon())
    eng.set_assignment_lock(
        locks if args.mode in ("guide60", "lock") else [])
    before_bay = int(eng.get_solution()[7 * block])
    if args.mode != "baseline" and before_bay != bay:
        raise RuntimeError("causal start lost the selected bay before search")

    t0 = time.monotonic()
    stats = list(eng.run_alns(
        3600.0, 0.05, 0.15, 0.002, 0.9995, args.seed,
        1000, 0.75, 0.04, 0x3FF, args.iters,
    ))
    elapsed = time.monotonic() - t0
    audit = [int(round(v)) for v in eng.audit_state()]
    if audit[0] != 0:
        raise RuntimeError(f"invalid engine state after ALNS: {audit}")
    search_flat = list(eng.get_solution())
    after_bay = int(search_flat[7 * block])
    selected_action_retained = after_bay == bay
    lock_held = args.mode != "lock" or selected_action_retained
    if args.mode == "lock" and not lock_held:
        raise RuntimeError(
            f"persistent lock leaked: block {block} bay {after_bay} != {bay}")
    search_objective = list(eng.objective())
    checked = checked_record(ma, prob, eng, n)

    report = {
        "schema_version": 1,
        "instance": args.instance,
        "seed": args.seed,
        "mode": args.mode,
        "iters": args.iters,
        "solver": str(solver),
        "manifest": str(manifest_path.resolve()),
        "order": order_name,
        "action": {"block": block, "bay": bay},
        "manifest_delta_pct": float(manifest["gated_delta_pct"]),
        "baseline_start_objective": base_obj,
        "causal_start_objective": causal_obj,
        "start_objective": base_obj if args.mode == "baseline" else causal_obj,
        "start_flat_sha256": flat_digest(start_flat),
        "search_objective": search_objective,
        "search_flat_sha256": flat_digest(search_flat),
        "selected_bay_before": before_bay,
        "selected_bay_after": after_bay,
        "selected_action_retained": selected_action_retained,
        "lock_held": lock_held,
        "checker": checked,
        "audit": audit,
        "run_stats": stats,
        "elapsed": elapsed,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "instance": args.instance,
        "seed": args.seed,
        "mode": args.mode,
        "start": report["start_objective"][3],
        "search": search_objective[3],
        "gated": checked["objective"],
        "feasible": checked["feasible"],
        "lock_held": lock_held,
        "iters": int(round(stats[0])) if stats else None,
        "elapsed": elapsed,
    }, sort_keys=True), flush=True)


def arm_summary(rows: list[dict], arm: str) -> dict:
    informative = [r for r in rows if r[f"{arm}_delta_pct"] is not None]
    deltas = [float(r[f"{arm}_delta_pct"]) for r in informative]
    wins = sum(d < -1e-12 for d in deltas)
    losses = sum(d > 1e-12 for d in deltas)
    summary = {
        "pairs": len(rows),
        "informative": len(informative),
        "wins": wins,
        "losses": losses,
        "ties": len(deltas) - wins - losses,
        "median_delta_pct": statistics.median(deltas) if deltas else None,
        "instances": {},
    }
    for instance in (3, 4, 5, 6):
        local = [float(r[f"{arm}_delta_pct"]) for r in informative
                 if r["instance"] == instance]
        summary["instances"][f"P{instance}"] = {
            "pairs": len(local),
            "median_delta_pct": statistics.median(local) if local else None,
            "wins": sum(d < -1e-12 for d in local),
            "losses": sum(d > 1e-12 for d in local),
            "ties": sum(abs(d) <= 1e-12 for d in local),
        }
    return summary


def arm_pass(summary: dict, all_feasible: bool) -> bool:
    board_medians = {
        int(k[1:]): v["median_delta_pct"]
        for k, v in summary["instances"].items()
    }
    return bool(
        all_feasible
        and summary["informative"] == 16
        and summary["median_delta_pct"] is not None
        and summary["median_delta_pct"] <= -2.0
        and summary["wins"] > summary["losses"]
        and any(board_medians[p] is not None and board_medians[p] <= -3.0
                for p in (3, 4, 5))
        and all(v is not None and v <= 2.0 for v in board_medians.values())
    )


def collect(args) -> None:
    rows = []
    for base_path in sorted(args.directory.glob("p*-s*-baseline.json")):
        stem = base_path.name[:-len("-baseline.json")]
        reports = {
            "baseline": json.loads(base_path.read_text()),
            "start": json.loads((args.directory / f"{stem}-start.json").read_text()),
            "lock": json.loads((args.directory / f"{stem}-lock.json").read_text()),
        }
        base = reports["baseline"]
        if any((r["instance"], r["seed"], r["iters"]) !=
               (base["instance"], base["seed"], base["iters"])
               for r in reports.values()):
            raise RuntimeError(f"arm identity mismatch at {stem}")
        if reports["start"]["start_flat_sha256"] != reports["lock"]["start_flat_sha256"]:
            raise RuntimeError(f"start/lock initial state mismatch at {stem}")
        if not reports["lock"]["lock_held"]:
            raise RuntimeError(f"lock did not hold at {stem}")
        a = base["checker"]
        row = {
            "instance": int(base["instance"]),
            "seed": int(base["seed"]),
            "baseline_feasible": bool(a["feasible"]),
            "baseline": a["objective"],
        }
        for arm in ("start", "lock"):
            b = reports[arm]["checker"]
            row[f"{arm}_feasible"] = bool(b["feasible"])
            row[arm] = b["objective"]
            row[f"{arm}_delta_pct"] = (
                100.0 * (float(b["objective"]) - float(a["objective"])) /
                float(a["objective"])
                if a["feasible"] and b["feasible"] else None
            )
        rows.append(row)
    if len(rows) != 16:
        raise RuntimeError(f"expected 16 seed/board triples, found {len(rows)}")

    all_feasible = all(
        r[f"{mode}_feasible"] for r in rows for mode in MODES
    )
    start_summary = arm_summary(rows, "start")
    lock_summary = arm_summary(rows, "lock")
    start_pass = arm_pass(start_summary, all_feasible)
    lock_pass = arm_pass(lock_summary, all_feasible)
    if lock_pass:
        verdict = "persistent_lock_survives"
    elif start_pass:
        verdict = "start_only_survives"
    elif not all_feasible:
        verdict = "reject_infeasible"
    else:
        verdict = "reject_plateau_transfer"
    output = {
        "schema_version": 1,
        "rows": rows,
        "summary": {
            "all_48_checker_feasible": all_feasible,
            "start": start_summary,
            "lock": lock_summary,
            "start_pass": start_pass,
            "lock_pass": lock_pass,
            "verdict": verdict,
        },
    }
    (args.directory / "summary.json").write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(json.dumps(output["summary"], sort_keys=True), flush=True)


def collect_start(args) -> None:
    """Score valid A/start rows even when the original lock arm failed closed."""
    rows = []
    for base_path in sorted(args.directory.glob("p*-s*-baseline.json")):
        stem = base_path.name[:-len("-baseline.json")]
        base = json.loads(base_path.read_text())
        start = json.loads((args.directory / f"{stem}-start.json").read_text())
        if (base["instance"], base["seed"], base["iters"]) != (
                start["instance"], start["seed"], start["iters"]):
            raise RuntimeError(f"paired identity mismatch at {stem}")
        a, b = base["checker"], start["checker"]
        rows.append({
            "instance": int(base["instance"]),
            "seed": int(base["seed"]),
            "baseline_feasible": bool(a["feasible"]),
            "start_feasible": bool(b["feasible"]),
            "baseline": a["objective"],
            "start": b["objective"],
            "start_delta_pct": (
                100.0 * (float(b["objective"]) - float(a["objective"])) /
                float(a["objective"])
                if a["feasible"] and b["feasible"] else None),
        })
    if len(rows) != 16:
        raise RuntimeError(f"expected 16 start pairs, found {len(rows)}")
    all_feasible = all(
        r["baseline_feasible"] and r["start_feasible"] for r in rows)
    summary = arm_summary(rows, "start")
    passed = arm_pass(summary, all_feasible)
    output = {
        "schema_version": 1,
        "rows": rows,
        "summary": {
            "all_32_checker_feasible": all_feasible,
            "start": summary,
            "start_pass": passed,
            "verdict": "start_only_survives" if passed else "reject_start_only",
        },
    }
    (args.directory / "start-summary.json").write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(json.dumps(output["summary"], sort_keys=True), flush=True)


def baseline_signature(report: dict) -> dict:
    """Trajectory fields that must survive the diagnostic-only rebuild."""
    return {
        "instance": report["instance"],
        "seed": report["seed"],
        "iters": report["iters"],
        "start_objective": report["start_objective"],
        "start_flat_sha256": report["start_flat_sha256"],
        "search_objective": report["search_objective"],
        "search_flat_sha256": report["search_flat_sha256"],
        "checker": report["checker"],
        "audit": report["audit"],
        "run_stats": report["run_stats"],
    }


def collect_persistent(args) -> None:
    """Join a keep-lock rerun to the original baseline/start measurements."""
    rows = []
    aa_mismatches = []
    for new_base_path in sorted(args.directory.glob("p*-s*-baseline.json")):
        stem = new_base_path.name[:-len("-baseline.json")]
        new_base = json.loads(new_base_path.read_text())
        lock = json.loads((args.directory / f"{stem}-lock.json").read_text())
        old_base = json.loads(
            (args.reference / f"{stem}-baseline.json").read_text())
        start = json.loads((args.reference / f"{stem}-start.json").read_text())
        identity = (new_base["instance"], new_base["seed"], new_base["iters"])
        if any((r["instance"], r["seed"], r["iters"]) != identity
               for r in (lock, old_base, start)):
            raise RuntimeError(f"joined arm identity mismatch at {stem}")
        if baseline_signature(new_base) != baseline_signature(old_base):
            aa_mismatches.append(stem)
        if new_base["start_flat_sha256"] == lock["start_flat_sha256"]:
            raise RuntimeError(f"baseline/lock unexpectedly share a start at {stem}")
        if start["start_flat_sha256"] != lock["start_flat_sha256"]:
            raise RuntimeError(f"start/lock initial bytes differ at {stem}")
        if not lock["lock_held"]:
            raise RuntimeError(f"persistent lock did not hold at {stem}")
        a = new_base["checker"]
        row = {
            "instance": int(new_base["instance"]),
            "seed": int(new_base["seed"]),
            "baseline_feasible": bool(a["feasible"]),
            "baseline": a["objective"],
        }
        for arm, report in (("start", start), ("lock", lock)):
            b = report["checker"]
            row[f"{arm}_feasible"] = bool(b["feasible"])
            row[arm] = b["objective"]
            row[f"{arm}_delta_pct"] = (
                100.0 * (float(b["objective"]) - float(a["objective"])) /
                float(a["objective"])
                if a["feasible"] and b["feasible"] else None
            )
        rows.append(row)
    if len(rows) != 16:
        raise RuntimeError(f"expected 16 joined pairs, found {len(rows)}")

    all_start_feasible = all(
        r["baseline_feasible"] and r["start_feasible"] for r in rows)
    all_lock_feasible = all(
        r["baseline_feasible"] and r["lock_feasible"] for r in rows)
    start_summary = arm_summary(rows, "start")
    lock_summary = arm_summary(rows, "lock")
    start_pass = arm_pass(start_summary, all_start_feasible)
    lock_pass = arm_pass(lock_summary, all_lock_feasible)
    if aa_mismatches:
        verdict = "invalid_baseline_aa_mismatch"
    elif lock_pass:
        verdict = "persistent_lock_survives"
    elif start_pass:
        verdict = "start_only_survives"
    elif not all_start_feasible or not all_lock_feasible:
        verdict = "reject_infeasible"
    else:
        verdict = "reject_plateau_transfer"
    output = {
        "schema_version": 1,
        "reference": str(args.reference.resolve()),
        "rows": rows,
        "summary": {
            "baseline_aa_exact": not aa_mismatches,
            "baseline_aa_mismatches": aa_mismatches,
            "all_start_arms_checker_feasible": all_start_feasible,
            "all_lock_arms_checker_feasible": all_lock_feasible,
            "start": start_summary,
            "lock": lock_summary,
            "start_pass": start_pass,
            "lock_pass": lock_pass,
            "verdict": verdict,
        },
    }
    (args.directory / "summary.json").write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(json.dumps(output["summary"], sort_keys=True), flush=True)


def collect_guidance(args) -> None:
    """Join shipped 60%-guidance rows to the original A/start results."""
    rows = []
    for guide_path in sorted(args.directory.glob("p*-s*-guide60.json")):
        stem = guide_path.name[:-len("-guide60.json")]
        guide = json.loads(guide_path.read_text())
        base = json.loads(
            (args.reference / f"{stem}-baseline.json").read_text())
        start = json.loads((args.reference / f"{stem}-start.json").read_text())
        identity = (base["instance"], base["seed"], base["iters"])
        if any((r["instance"], r["seed"], r["iters"]) != identity
               for r in (start, guide)):
            raise RuntimeError(f"joined arm identity mismatch at {stem}")
        if start["start_flat_sha256"] != guide["start_flat_sha256"]:
            raise RuntimeError(f"start/guidance initial bytes differ at {stem}")
        a = base["checker"]
        row = {
            "instance": int(base["instance"]),
            "seed": int(base["seed"]),
            "baseline_feasible": bool(a["feasible"]),
            "baseline": a["objective"],
            "guide60_action_retained": bool(
                guide["selected_action_retained"]),
        }
        for arm, report in (("start", start), ("guide60", guide)):
            b = report["checker"]
            row[f"{arm}_feasible"] = bool(b["feasible"])
            row[arm] = b["objective"]
            row[f"{arm}_delta_pct"] = (
                100.0 * (float(b["objective"]) - float(a["objective"])) /
                float(a["objective"])
                if a["feasible"] and b["feasible"] else None
            )
        rows.append(row)
    if len(rows) != 16:
        raise RuntimeError(f"expected 16 guidance rows, found {len(rows)}")
    all_start_feasible = all(
        r["baseline_feasible"] and r["start_feasible"] for r in rows)
    all_guide_feasible = all(
        r["baseline_feasible"] and r["guide60_feasible"] for r in rows)
    start_summary = arm_summary(rows, "start")
    guide_summary = arm_summary(rows, "guide60")
    start_pass = arm_pass(start_summary, all_start_feasible)
    guide_pass = arm_pass(guide_summary, all_guide_feasible)
    if guide_pass:
        verdict = "guide60_survives"
    elif start_pass:
        verdict = "start_only_survives"
    elif not all_start_feasible or not all_guide_feasible:
        verdict = "reject_infeasible"
    else:
        verdict = "reject_plateau_transfer"
    output = {
        "schema_version": 1,
        "reference": str(args.reference.resolve()),
        "rows": rows,
        "summary": {
            "all_start_arms_checker_feasible": all_start_feasible,
            "all_guide60_arms_checker_feasible": all_guide_feasible,
            "guide60_action_retained": sum(
                r["guide60_action_retained"] for r in rows),
            "start": start_summary,
            "guide60": guide_summary,
            "start_pass": start_pass,
            "guide60_pass": guide_pass,
            "verdict": verdict,
        },
    }
    (args.directory / "summary.json").write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(json.dumps(output["summary"], sort_keys=True), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("solver", type=pathlib.Path)
    p_run.add_argument("manifest", type=pathlib.Path)
    p_run.add_argument("instance", type=int, choices=(3, 4, 5, 6))
    p_run.add_argument("seed", type=int)
    p_run.add_argument("iters", type=int)
    p_run.add_argument("mode", choices=RUN_MODES)
    p_run.add_argument("output", type=pathlib.Path)
    p_run.set_defaults(func=run)
    p_collect = sub.add_parser("collect")
    p_collect.add_argument("directory", type=pathlib.Path)
    p_collect.set_defaults(func=collect)
    p_start = sub.add_parser("collect-start")
    p_start.add_argument("directory", type=pathlib.Path)
    p_start.set_defaults(func=collect_start)
    p_persistent = sub.add_parser("collect-persistent")
    p_persistent.add_argument("directory", type=pathlib.Path)
    p_persistent.add_argument("reference", type=pathlib.Path)
    p_persistent.set_defaults(func=collect_persistent)
    p_guidance = sub.add_parser("collect-guidance")
    p_guidance.add_argument("directory", type=pathlib.Path)
    p_guidance.add_argument("reference", type=pathlib.Path)
    p_guidance.set_defaults(func=collect_guidance)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
