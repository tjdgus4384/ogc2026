#!/usr/bin/env python3
"""Prepare frozen plateaus and run the obstruction-exit-closure paired gate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import resource
import statistics
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from assignment_transfer_fixed import (  # noqa: E402
    checked_record,
    digest,
    flat_digest,
)
from voluntary_stay_rollout import _orders  # noqa: E402


SEEDS = (7, 11, 23, 41)
FIXED_ITERS = 20000
FIXED_CHECKPOINT = 1000
MAX_CANDIDATES_PER_NEIGHBOURHOOD = 8
MAX_PHASE3_WINDOWS_PER_NEIGHBOURHOOD = 4
STAT_NAMES = (
    "neighbourhoods", "candidates", "seeds", "attempts", "built",
    "hard_rejects", "cycle_rejects", "phase3_seeds", "phase3_windows",
    "budget_hits", "suffix_tried", "suffix_built", "selected",
    "accepted", "new_best", "rescued", "extended_blocks",
    "extended_days", "paid_days", "true_gain", "final_official",
    "detector_ns", "suffix_ns",
)
FLOAT_STAT_NAMES = ("true_gain", "detector_ns", "suffix_ns")


def _load_solver(path: Path):
    sys.path.insert(0, str(path.resolve()))
    import myalgorithm as ma
    return ma


def _problem(instance: int) -> tuple[dict[str, Any], Path]:
    path = ROOT / "data" / "preliminary_test" / f"prob_{instance}.json"
    return json.loads(path.read_text()), path


def _raw_record(ma, problem: dict[str, Any], engine, n: int,
                exact_exits: bool) -> dict[str, Any]:
    recs = ma._extract(engine, n)
    entry = list(engine.entry_ranks())
    exits = list(engine.exit_ranks()) if exact_exits else None
    solution = ma._recs_to_sol(recs, entry, exits)
    checked = ma._check(problem, solution)
    feasible = bool(checked and checked.get("feasible"))
    objective = (float(checked["objective"])
                 if feasible and isinstance(checked.get("objective"),
                                            (int, float)) else None)
    violations = (checked or {}).get("violations", [])
    return {
        "feasible": feasible,
        "objective": objective,
        "solution_sha256": digest(solution),
        "flat_sha256": flat_digest(list(engine.get_solution())),
        "exact_exit_order": exact_exits,
        "stage": (checked or {}).get("stage"),
        "violations": len(violations),
        "first_violation": str(violations[0])[:240] if violations else None,
    }


def _audit(engine, flexible: bool) -> list[int]:
    try:
        values = engine.audit_state(flexible)
    except TypeError:  # frozen reference API predates the explicit argument
        values = engine.audit_state()
    return [int(round(value)) for value in values]


def _telemetry(stats: list[float]) -> dict[str, float | int]:
    if len(stats) < 44:
        raise RuntimeError(f"closure build returned only {len(stats)} stats")
    out: dict[str, float | int] = {}
    for offset, name in enumerate(STAT_NAMES, 21):
        value = float(stats[offset])
        out[name] = value if name in FLOAT_STAT_NAMES else int(round(value))
    integer_names = tuple(
        name for name in STAT_NAMES if name not in FLOAT_STAT_NAMES)
    invariants = (
        all(out[name] >= 0 for name in integer_names),
        all(out[name] >= 0.0 for name in FLOAT_STAT_NAMES),
        out["candidates"] <=
            MAX_CANDIDATES_PER_NEIGHBOURHOOD * out["neighbourhoods"],
        out["phase3_windows"] <=
            MAX_PHASE3_WINDOWS_PER_NEIGHBOURHOOD * out["neighbourhoods"],
        out["budget_hits"] <= 2 * out["neighbourhoods"],
        out["seeds"] == out["attempts"],
        out["seeds"] <= out["candidates"],
        out["attempts"] <= out["neighbourhoods"],
        out["built"] <= out["attempts"],
        out["built"] + out["hard_rejects"] + out["cycle_rejects"] <=
            out["attempts"],
        out["phase3_seeds"] <= out["seeds"],
        out["suffix_tried"] <= out["built"],
        out["suffix_built"] <= out["suffix_tried"],
        out["selected"] <= out["suffix_built"],
        out["accepted"] <= out["selected"],
        out["new_best"] <= out["accepted"],
        out["rescued"] <= out["selected"],
        out["final_official"] in (0, 1),
    )
    if not all(invariants):
        raise RuntimeError(f"closure telemetry invariant failed: {out!r}")
    return out


def _has_overlong(flat: list[int], processing: list[int]) -> bool:
    if len(flat) != 7 * len(processing):
        raise RuntimeError("flat solution has the wrong length")
    return any(
        bool(flat[7 * i + 6]) and
        flat[7 * i + 5] - flat[7 * i + 4] > processing[i]
        for i in range(len(processing))
    )


def _prepare(args: argparse.Namespace) -> None:
    ma = _load_solver(args.reference_solver)
    problem, problem_path = _problem(args.instance)
    n = len(problem["blocks"])
    construction_path = args.constructions / f"prob_{args.instance}.json"
    construction = json.loads(construction_path.read_text())
    report_path = args.reference_reports / (
        f"p{args.instance}-s{args.seed}-baseline.json")
    reference = json.loads(report_path.read_text())
    order = _orders(problem)[construction["order"]]

    engine, _due, proc = ma._fresh(problem, 4)
    code = int(engine.reconstruct_ordered_stays(
        [-1] * n, order, [-1] * n, False, 7))
    if code != -1:
        raise RuntimeError(f"frozen construction failed at block {code}")
    start_flat = list(engine.get_solution())
    start_objective = list(engine.objective())
    if (flat_digest(start_flat) != reference["start_flat_sha256"] or
            start_objective != reference["start_objective"]):
        raise RuntimeError("job-75041 start did not replay")

    engine.set_solution(start_flat)
    engine.build_occ(engine.horizon())
    stats = list(engine.run_alns(
        3600.0, 0.05, 0.15, 0.002, 0.9995, args.seed,
        1000, 0.75, 0.04, 0x3FF, FIXED_ITERS,
    ))
    raw_flat = list(engine.get_solution())
    raw_objective = list(engine.objective())
    if (stats != reference["run_stats"] or
            flat_digest(raw_flat) != reference["search_flat_sha256"] or
            raw_objective != reference["search_objective"]):
        raise RuntimeError("job-75041 final trajectory did not replay")

    # Preparation alone may replay the historical rescue.  Every actual gate
    # arm below uses raw checker output and is forbidden from calling _gate.
    checked = checked_record(ma, problem, engine, n)
    if checked != reference["checker"]:
        raise RuntimeError(
            f"job-75041 checker authority mismatch: {checked!r}")
    plateau_flat = list(engine.get_solution())
    engine.build_occ(engine.horizon())
    plateau_raw = _raw_record(ma, problem, engine, n, False)
    audit = _audit(engine, False)
    plateau_objective = list(engine.objective())
    checker_objective = checked.get("objective")
    raw_checker_objective = plateau_raw.get("objective")
    if (not plateau_raw["feasible"] or audit[0] != 0 or
            plateau_raw["flat_sha256"] != checked["flat_sha256"] or
            not isinstance(checker_objective, (int, float)) or
            not isinstance(raw_checker_objective, (int, float)) or
            abs(float(raw_checker_objective) -
                float(plateau_objective[-1])) > 1e-9 or
            abs(float(checker_objective) -
                float(plateau_objective[-1])) > 1e-9 or
            _has_overlong(plateau_flat, list(proc))):
        raise RuntimeError(
            "authoritative plateau mismatch: "
            f"checker={checked}, raw={plateau_raw}, audit={audit}, "
            f"engine_objective={plateau_objective}")

    output = {
        "schema_version": 1,
        "instance": args.instance,
        "seed": args.seed,
        "problem": str(problem_path.resolve()),
        "reference_solver": str(args.reference_solver.resolve()),
        "reference_report": str(report_path.resolve()),
        "construction": str(construction_path.resolve()),
        "order": construction["order"],
        "historical_rescue": bool(checked.get("rescued", False)),
        "objective": plateau_objective,
        "flat_sha256": flat_digest(plateau_flat),
        "flat": plateau_flat,
        "raw_checker": plateau_raw,
        "audit": audit,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "instance": args.instance,
        "seed": args.seed,
        "objective": output["objective"][-1],
        "historical_rescue": output["historical_rescue"],
        "flat_sha256": output["flat_sha256"],
    }, sort_keys=True), flush=True)


def _check_flat(ma, problem: dict[str, Any], flat: list[int],
                exact: bool) -> tuple[dict[str, Any], list[int], list[float]]:
    n = len(problem["blocks"])
    engine, _due, proc = ma._fresh(problem, 4)
    overlong = _has_overlong(flat, list(proc))
    if exact != overlong:
        raise RuntimeError(
            f"official EXIT flag mismatch: exact={exact} overlong={overlong}")
    engine.set_solution(flat)
    engine.build_occ(engine.horizon())
    audit = _audit(engine, exact)
    checked = _raw_record(ma, problem, engine, n, exact)
    return checked, audit, list(engine.objective())


def _run(args: argparse.Namespace) -> None:
    if args.iters != FIXED_ITERS or args.checkpoint != FIXED_CHECKPOINT:
        raise RuntimeError(
            "frozen gate requires exactly "
            f"{FIXED_ITERS} iterations and checkpoint {FIXED_CHECKPOINT}")
    ma = _load_solver(args.solver)
    problem, problem_path = _problem(args.instance)
    n = len(problem["blocks"])
    plateau = json.loads(args.plateau.read_text())
    if (plateau["instance"] != args.instance or plateau["seed"] != args.seed):
        raise RuntimeError("plateau identity mismatch")
    start_flat = [int(value) for value in plateau["flat"]]
    if flat_digest(start_flat) != plateau["flat_sha256"]:
        raise RuntimeError("plateau flat digest mismatch")

    engine, _due, proc = ma._fresh(problem, 4)
    engine.set_solution(start_flat)
    engine.build_occ(engine.horizon())
    start_checker = _raw_record(ma, problem, engine, n, False)
    start_audit = _audit(engine, False)
    start_objective = list(engine.objective())
    if (not start_checker["feasible"] or start_audit[0] != 0 or
            start_objective != plateau["objective"] or
            _has_overlong(start_flat, list(proc))):
        raise RuntimeError("plateau start authority mismatch")

    t0 = time.monotonic()
    if args.mode == "reference":
        stats = list(engine.run_alns(
            3600.0, 0.05, 0.15, 0.002, 0.9995, args.seed,
            100, 0.75, 0.04, 0x3FF, args.iters,
        ))
        checkpoint_flat: list[int] = []
        checkpoint_official = False
    else:
        stats = list(engine.run_alns(
            3600.0, 0.05, 0.15, 0.002, 0.9995, args.seed,
            100, 0.75, 0.04, 0x3FF, args.iters, False,
            args.mode == "closure", args.checkpoint,
        ))
        checkpoint_flat = [int(value)
                           for value in engine.get_checkpoint_solution()]
        checkpoint_official = bool(engine.checkpoint_official())
    elapsed = time.monotonic() - t0
    if int(round(stats[0])) != args.iters:
        raise RuntimeError(f"iteration contract failed: {stats[:3]}")

    telemetry = (_telemetry(stats) if args.mode != "reference" else None)
    final_official = bool(telemetry and telemetry["final_official"])
    final_flat = list(engine.get_solution())
    final_overlong = _has_overlong(final_flat, list(proc))
    if final_official != final_overlong:
        raise RuntimeError(
            "final official EXIT flag mismatch: "
            f"flag={final_official} overlong={final_overlong}")
    final_audit = _audit(engine, final_official)
    final_checker = _raw_record(
        ma, problem, engine, n, final_official)
    final_objective = list(engine.objective())
    if (final_audit[0] != 0 or not final_checker["feasible"] or
            abs(float(final_checker["objective"]) -
                float(final_objective[-1])) > 1e-9):
        raise RuntimeError(
            f"raw final authority failed: {final_audit}, {final_checker}")

    checkpoint = None
    if args.mode != "reference":
        if len(checkpoint_flat) != 7 * n:
            raise RuntimeError("in-run checkpoint was not captured")
        cp_checker, cp_audit, cp_objective = _check_flat(
            ma, problem, checkpoint_flat, checkpoint_official)
        if (cp_audit[0] != 0 or not cp_checker["feasible"] or
                abs(float(cp_checker["objective"]) -
                    float(cp_objective[-1])) > 1e-9):
            raise RuntimeError(
                f"raw checkpoint authority failed: {cp_audit}, {cp_checker}")
        checkpoint = {
            "iteration": args.checkpoint,
            "objective": cp_objective,
            "flat_sha256": flat_digest(checkpoint_flat),
            "official_exit_order": checkpoint_official,
            "checker": cp_checker,
            "audit": cp_audit,
        }

    report = {
        "schema_version": 1,
        "instance": args.instance,
        "seed": args.seed,
        "mode": args.mode,
        "iters": args.iters,
        "checkpoint_iteration": args.checkpoint,
        "solver": str(args.solver.resolve()),
        "problem": str(problem_path.resolve()),
        "plateau": str(args.plateau.resolve()),
        "start_objective": start_objective,
        "start_flat_sha256": flat_digest(start_flat),
        "start_checker": start_checker,
        "start_audit": start_audit,
        "checkpoint": checkpoint,
        "search_objective": final_objective,
        "search_flat_sha256": flat_digest(final_flat),
        "official_exit_order": final_official,
        "checker": final_checker,
        "audit": final_audit,
        "run_stats": stats,
        "telemetry": telemetry,
        "elapsed": elapsed,
        "peak_rss_kib": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "instance": args.instance,
        "seed": args.seed,
        "mode": args.mode,
        "start": start_objective[-1],
        "checkpoint": (checkpoint or {}).get("objective", [None])[-1],
        "final": final_objective[-1],
        "telemetry": telemetry,
        "elapsed": elapsed,
        "peak_rss_kib": report["peak_rss_kib"],
    }, sort_keys=True), flush=True)


def _signature(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "instance": report["instance"],
        "seed": report["seed"],
        "iters": report["iters"],
        "start_objective": report["start_objective"],
        "start_flat_sha256": report["start_flat_sha256"],
        "start_checker": report["start_checker"],
        "start_audit": report["start_audit"],
        "search_objective": report["search_objective"],
        "search_flat_sha256": report["search_flat_sha256"],
        "checker": report["checker"],
        "audit": report["audit"],
        "run_stats_common": report["run_stats"][:15],
    }


def _start_signature(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "problem": report["problem"],
        "plateau": report["plateau"],
        "start_objective": report["start_objective"],
        "start_flat_sha256": report["start_flat_sha256"],
        "start_checker": report["start_checker"],
        "start_audit": report["start_audit"],
    }


def _checkpoint_signature(report: dict[str, Any]) -> dict[str, Any]:
    checkpoint = report["checkpoint"]
    if not isinstance(checkpoint, dict):
        raise RuntimeError("candidate report is missing its checkpoint")
    return {
        "checkpoint_iteration": report["checkpoint_iteration"],
        "iteration": checkpoint["iteration"],
        "objective": checkpoint["objective"],
        "flat_sha256": checkpoint["flat_sha256"],
        "official_exit_order": checkpoint["official_exit_order"],
        "checker": checkpoint["checker"],
        "audit": checkpoint["audit"],
    }


def _validate_report_identity(report: dict[str, Any], mode: str,
                              instance: int, seed: int, stem: str) -> None:
    checkpoint = report.get("checkpoint")
    stats = report.get("run_stats")
    valid = (
        report.get("schema_version") == 1 and
        report.get("mode") == mode and
        report.get("instance") == instance and
        report.get("seed") == seed and
        report.get("iters") == FIXED_ITERS and
        isinstance(stats, list) and len(stats) > 0 and
        float(stats[0]) == float(FIXED_ITERS) and
        report.get("checkpoint_iteration") == FIXED_CHECKPOINT and
        ((mode == "reference" and checkpoint is None and
          report.get("telemetry") is None) or
         (mode != "reference" and isinstance(checkpoint, dict) and
          checkpoint.get("iteration") == FIXED_CHECKPOINT and
          isinstance(report.get("telemetry"), dict)))
    )
    if not valid:
        raise RuntimeError(f"report identity/budget mismatch: {stem}-{mode}")


def _metrics(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    deltas = [float(row[key]) for row in rows]
    wins = sum(delta < -1e-12 for delta in deltas)
    losses = sum(delta > 1e-12 for delta in deltas)
    result: dict[str, Any] = {
        "pairs": len(rows),
        "wins": wins,
        "losses": losses,
        "ties": len(rows) - wins - losses,
        "median_delta_pct": statistics.median(deltas),
        "mean_delta_pct": statistics.mean(deltas),
        "worst_delta_pct": max(deltas),
        "instances": {},
    }
    for instance in range(3, 7):
        local = [float(row[key]) for row in rows
                 if row["instance"] == instance]
        result["instances"][f"P{instance}"] = {
            "pairs": len(local),
            "wins": sum(delta < -1e-12 for delta in local),
            "losses": sum(delta > 1e-12 for delta in local),
            "ties": sum(abs(delta) <= 1e-12 for delta in local),
            "median_delta_pct": statistics.median(local),
        }
    return result


def _numeric_gate(metrics: dict[str, Any]) -> bool:
    target_edge = any(
        metrics["instances"][name]["median_delta_pct"] <= -3.0
        for name in ("P4", "P5")
    )
    return bool(
        metrics["wins"] > metrics["losses"] and
        metrics["median_delta_pct"] <= -2.0 and target_edge and
        all(item["median_delta_pct"] <= 2.0
            for item in metrics["instances"].values())
    )


def _collect(args: argparse.Namespace) -> None:
    rows: list[dict[str, Any]] = []
    aa_mismatches: list[str] = []
    start_mismatches: list[str] = []
    control_telemetry_mismatches: list[str] = []
    null_mismatches: list[str] = []
    all_feasible = True
    totals = {name: 0.0 for name in STAT_NAMES}
    slowdowns: list[float] = []

    for instance in range(3, 7):
        for seed in SEEDS:
            stem = f"p{instance}-s{seed}"
            reports = {
                mode: json.loads((args.directory /
                                  f"{stem}-{mode}.json").read_text())
                for mode in ("reference", "control", "closure")
            }
            for mode, report in reports.items():
                _validate_report_identity(
                    report, mode, instance, seed, stem)
            reference, control, closure = (
                reports["reference"], reports["control"], reports["closure"])
            start = _start_signature(reference)
            if any(_start_signature(report) != start
                   for report in (control, closure)):
                start_mismatches.append(stem)
            if _signature(reference) != _signature(control):
                aa_mismatches.append(stem)

            control_telemetry = _telemetry(
                [float(value) for value in control["run_stats"]])
            telemetry = _telemetry(
                [float(value) for value in closure["run_stats"]])
            if control_telemetry != control["telemetry"]:
                raise RuntimeError(
                    f"stored control telemetry mismatch: {stem}")
            if telemetry != closure["telemetry"]:
                raise RuntimeError(
                    f"stored closure telemetry mismatch: {stem}")
            if (any(float(control_telemetry[name]) != 0.0
                    for name in STAT_NAMES) or
                    bool(control["official_exit_order"]) or
                    bool(control["checkpoint"]["official_exit_order"])):
                control_telemetry_mismatches.append(stem)
            if int(telemetry["accepted"]) == 0 and (
                    _signature(closure) != _signature(control) or
                    _checkpoint_signature(closure) !=
                    _checkpoint_signature(control)):
                null_mismatches.append(stem)
            for name in STAT_NAMES:
                totals[name] += float(telemetry[name])
            all_feasible &= all(report["checker"]["feasible"]
                                for report in reports.values())
            all_feasible &= bool(control["checkpoint"]["checker"]["feasible"])
            all_feasible &= bool(closure["checkpoint"]["checker"]["feasible"])
            c0 = float(control["checker"]["objective"])
            c1 = float(closure["checker"]["objective"])
            p0 = float(control["checkpoint"]["checker"]["objective"])
            p1 = float(closure["checkpoint"]["checker"]["objective"])
            rows.append({
                "instance": instance,
                "seed": seed,
                "control": c0,
                "closure": c1,
                "delta_pct": 100.0 * (c1 - c0) / c0,
                "checkpoint_control": p0,
                "checkpoint_closure": p1,
                "checkpoint_delta_pct": 100.0 * (p1 - p0) / p0,
                "telemetry": telemetry,
                "control_elapsed": control["elapsed"],
                "closure_elapsed": closure["elapsed"],
                "slowdown": closure["elapsed"] / control["elapsed"],
                "control_peak_rss_kib": control["peak_rss_kib"],
                "closure_peak_rss_kib": closure["peak_rss_kib"],
            })
            slowdowns.append(rows[-1]["slowdown"])

    final = _metrics(rows, "delta_pct")
    checkpoint = _metrics(rows, "checkpoint_delta_pct")
    contracts_ok = bool(
        not aa_mismatches and not start_mismatches and
        not control_telemetry_mismatches and not null_mismatches and
        all_feasible)
    checkpoint_pass = contracts_ok and _numeric_gate(checkpoint)
    passed = contracts_ok and _numeric_gate(final)

    if passed:
        mechanism = "plateau_mover"
    elif checkpoint_pass:
        mechanism = "early_transient"
    elif totals["seeds"] == 0:
        mechanism = "dormant_no_one_way_seed"
    elif totals["built"] == 0:
        mechanism = "closure_infeasible"
    elif totals["suffix_built"] == 0:
        mechanism = "suffix_delivery_failed"
    elif totals["selected"] == 0:
        mechanism = "locally_dominated"
    elif totals["accepted"] == 0:
        mechanism = "sa_gated"
    elif totals["new_best"] == 0:
        mechanism = "transient_walk_only"
    else:
        mechanism = "active_but_gate_failed"

    output = {
        "schema_version": 1,
        "fixed_iterations": FIXED_ITERS,
        "fixed_checkpoint_iteration": FIXED_CHECKPOINT,
        "rows": rows,
        "cross_arm_start_exact": not start_mismatches,
        "cross_arm_start_mismatches": start_mismatches,
        "reference_control_exact": not aa_mismatches,
        "reference_control_mismatches": aa_mismatches,
        "control_closure_telemetry_zero":
            not control_telemetry_mismatches,
        "control_closure_telemetry_mismatches":
            control_telemetry_mismatches,
        "semantic_null_exact": not null_mismatches,
        "semantic_null_mismatches": null_mismatches,
        "all_final_and_checkpoint_checker_feasible": all_feasible,
        "contracts_pass": contracts_ok,
        "checkpoint_pass": checkpoint_pass,
        "checkpoint_diagnostic": checkpoint,
        "final": final,
        "telemetry_totals": totals,
        "median_slowdown": statistics.median(slowdowns),
        "mechanism_class": mechanism,
        "verdict": ("advance_equal_wall" if passed
                    else "reject_first_witness_exit_closure"),
    }
    (args.directory / "summary.json").write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(json.dumps(output, sort_keys=True), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare")
    prepare.add_argument("reference_solver", type=Path)
    prepare.add_argument("constructions", type=Path)
    prepare.add_argument("reference_reports", type=Path)
    prepare.add_argument("instance", type=int, choices=range(3, 7))
    prepare.add_argument("seed", type=int, choices=SEEDS)
    prepare.add_argument("output", type=Path)

    run = sub.add_parser("run")
    run.add_argument("solver", type=Path)
    run.add_argument("plateau", type=Path)
    run.add_argument("instance", type=int, choices=range(3, 7))
    run.add_argument("seed", type=int, choices=SEEDS)
    run.add_argument("iters", type=int)
    run.add_argument("mode", choices=("reference", "control", "closure"))
    run.add_argument("output", type=Path)
    run.add_argument("--checkpoint", type=int, default=FIXED_CHECKPOINT)

    collect = sub.add_parser("collect")
    collect.add_argument("directory", type=Path)

    args = parser.parse_args()
    if args.command == "prepare":
        _prepare(args)
    elif args.command == "run":
        _run(args)
    else:
        _collect(args)


if __name__ == "__main__":
    main()
