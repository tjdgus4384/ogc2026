#!/usr/bin/env python3
"""Frozen fixed-iteration gate for the control-preserving exit archive."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import resource
import statistics
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from assignment_transfer_fixed import digest, flat_digest  # noqa: E402


FIXED_ITERS = 20_000
FIXED_CHECKPOINT = 1_000
PLATEAU_SEEDS = (7, 11, 23, 41)
PROFILE_EVAL_SEEDS = {
    "dev": (7, 11, 23, 41),
    "heldout": (3, 17, 31, 47),
}
MODES = ("reference", "control", "archive")

CLOSURE_NAMES = (
    "neighbourhoods", "candidates", "seeds", "attempts", "built",
    "hard_rejects", "cycle_rejects", "phase3_seeds", "phase3_windows",
    "budget_hits", "suffix_tried", "suffix_built", "selected",
    "accepted", "new_best", "rescued", "extended_blocks",
    "extended_days", "paid_days", "true_gain", "returned_official",
    "detector_ns", "suffix_ns",
)
ARCHIVE_NAMES = (
    "bound_pruned", "updates", "returned", "gain", "objective",
    "audit_rejects",
)
FLOAT_STATS = {"true_gain", "detector_ns", "suffix_ns", "gain", "objective"}


def _load_solver(path: Path):
    sys.path.insert(0, str(path.resolve()))
    import myalgorithm as ma
    if ma.sscore is None:
        raise RuntimeError(f"sscore failed to import from {path}")
    module_path = Path(ma.sscore.__file__).resolve()
    if module_path.parent != path.resolve():
        raise RuntimeError(f"sscore imported from {module_path}, not {path}")
    return ma


def _problem(instance: int) -> tuple[dict[str, Any], Path]:
    path = ROOT / "data" / "preliminary_test" / f"prob_{instance}.json"
    return json.loads(path.read_text()), path


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _audit(engine, flexible: bool) -> list[int]:
    try:
        values = engine.audit_state(flexible)
    except TypeError:
        if flexible:
            raise RuntimeError("reference API cannot audit flexible exits")
        values = engine.audit_state()
    return [int(round(value)) for value in values]


def _has_overlong(flat: list[int], processing: list[int]) -> bool:
    if len(flat) != 7 * len(processing):
        raise RuntimeError("flat solution has the wrong length")
    return any(
        bool(flat[7 * i + 6]) and
        flat[7 * i + 5] - flat[7 * i + 4] > processing[i]
        for i in range(len(processing))
    )


def _inspect_flat(ma, problem: dict[str, Any], flat: list[int],
                  official: bool | None = None) -> dict[str, Any]:
    n = len(problem["blocks"])
    engine, _due, proc = ma._fresh(problem, 4)
    flat = [int(value) for value in flat]
    overlong = _has_overlong(flat, list(proc))
    if official is not None and bool(official) != overlong:
        raise RuntimeError(
            f"official flag mismatch: flag={official}, overlong={overlong}")
    engine.set_solution(flat)
    engine.build_occ(engine.horizon())
    audit = _audit(engine, overlong)
    if not audit or audit[0] != 0:
        raise RuntimeError(f"engine audit failed: {audit}")

    entry = list(engine.entry_ranks())
    exits = None
    greedy = None
    if overlong:
        exits = list(engine.exit_ranks())
        greedy_getter = getattr(engine, "greedy_exit_ranks", None)
        if not callable(greedy_getter):
            raise RuntimeError("exact archive emitter lacks greedy differential")
        greedy = list(greedy_getter())
        if exits != greedy:
            raise RuntimeError("canonical and independently searched EXIT ranks differ")
    recs = ma._extract(engine, n)
    solution = ma._recs_to_sol(recs, entry, exits)
    checked = ma._check(problem, solution)
    feasible = bool(checked and checked.get("feasible"))
    objective = (float(checked["objective"])
                 if feasible and isinstance(checked.get("objective"),
                                            (int, float)) else None)
    engine_objective = [float(value) for value in engine.objective()]
    if (not feasible or objective is None or
            abs(objective - engine_objective[-1]) > 1e-9):
        violations = (checked or {}).get("violations", [])
        raise RuntimeError(
            "raw checker authority failed: "
            f"objective={objective}, engine={engine_objective}, "
            f"violation={str(violations[:1])[:240]}")
    return {
        "flat": flat,
        "flat_sha256": flat_digest(flat),
        "solution_sha256": digest(solution),
        "objective": engine_objective,
        "checker_objective": objective,
        "checker_stage": checked.get("stage"),
        "checker_feasible": True,
        "audit": audit,
        "overlong": overlong,
        "official_exit_order": overlong,
        "entry_ranks_sha256": digest(entry),
        "exit_ranks_sha256": digest(exits) if exits is not None else None,
        "greedy_exit_ranks_sha256": (
            digest(greedy) if greedy is not None else None),
    }


def _telemetry(stats: list[float]) -> dict[str, float | int]:
    if len(stats) < 50:
        raise RuntimeError(f"archive build returned only {len(stats)} stats")
    result: dict[str, float | int] = {}
    for index, name in enumerate(CLOSURE_NAMES, 21):
        value = float(stats[index])
        result[name] = value if name in FLOAT_STATS else int(round(value))
    for index, name in enumerate(ARCHIVE_NAMES, 44):
        value = float(stats[index])
        result[f"archive_{name}"] = (
            value if name in FLOAT_STATS else int(round(value)))

    c = result
    integer_values = [value for value in c.values()
                      if isinstance(value, int)]
    float_values = [value for value in c.values()
                    if isinstance(value, float)]
    invariants = (
        all(value >= 0 for value in integer_values),
        all(value >= 0.0 for value in float_values),
        c["candidates"] <= 8 * c["neighbourhoods"],
        c["phase3_windows"] <= 4 * c["neighbourhoods"],
        c["budget_hits"] <= 2 * c["neighbourhoods"],
        c["seeds"] == c["attempts"],
        c["seeds"] <= c["candidates"],
        c["attempts"] <= c["neighbourhoods"],
        c["built"] <= c["attempts"],
        c["built"] + c["hard_rejects"] + c["cycle_rejects"] <=
            c["attempts"],
        c["phase3_seeds"] <= c["seeds"],
        c["suffix_tried"] <= c["built"],
        c["suffix_built"] <= c["suffix_tried"],
        c["selected"] <= c["suffix_built"],
        c["accepted"] <= c["selected"],
        c["new_best"] <= c["accepted"],
        c["rescued"] <= c["selected"],
        c["archive_updates"] <= c["suffix_built"],
        c["archive_returned"] in (0, 1),
        c["archive_returned"] <= c["archive_updates"],
        c["returned_official"] in (0, 1),
        c["archive_audit_rejects"] <= c["suffix_tried"],
    )
    if not all(invariants):
        raise RuntimeError(f"archive telemetry invariant failed: {result!r}")
    return result


def _experimental_zero(telemetry: dict[str, float | int]) -> bool:
    return all(float(value) == 0.0 for value in telemetry.values())


def _live_decisions_zero(telemetry: dict[str, float | int]) -> bool:
    names = (
        "selected", "accepted", "new_best", "rescued", "extended_blocks",
        "extended_days", "paid_days", "true_gain",
    )
    return all(float(telemetry[name]) == 0.0 for name in names)


def _call_run(engine, eval_seed: int, mode: str) -> list[float]:
    prefix = (
        3600.0, 0.05, 0.15, 0.002, 0.9995, eval_seed,
        100, 0.75, 0.04, 0x3FF, FIXED_ITERS, False, False,
        FIXED_CHECKPOINT,
    )
    if mode == "archive":
        return [float(value) for value in engine.run_alns(*prefix, True)]
    return [float(value) for value in engine.run_alns(*prefix)]


def _run(args: argparse.Namespace) -> None:
    if args.iters != FIXED_ITERS or args.checkpoint != FIXED_CHECKPOINT:
        raise RuntimeError("archive gate requires fixed 20000/1000 budgets")
    ma = _load_solver(args.solver)
    problem, problem_path = _problem(args.instance)
    plateau = json.loads(args.plateau.read_text())
    if (plateau.get("schema_version") != 1 or
            plateau.get("instance") != args.instance or
            plateau.get("seed") != args.plateau_seed):
        raise RuntimeError("plateau identity mismatch")
    start_flat = [int(value) for value in plateau["flat"]]
    if flat_digest(start_flat) != plateau.get("flat_sha256"):
        raise RuntimeError("plateau flat digest mismatch")
    start = _inspect_flat(ma, problem, start_flat, False)
    if start["objective"] != [float(value) for value in plateau["objective"]]:
        raise RuntimeError("plateau objective mismatch")

    engine, _due, _proc = ma._fresh(problem, 4)
    engine.set_solution(start_flat)
    engine.build_occ(engine.horizon())
    begin = time.monotonic()
    stats = _call_run(engine, args.eval_seed, args.mode)
    elapsed = time.monotonic() - begin
    if int(round(stats[0])) != FIXED_ITERS:
        raise RuntimeError(f"iteration contract failed: {stats[:5]}")

    live_flat = [int(value) for value in engine.get_solution()]
    checkpoint_flat = [int(value)
                       for value in engine.get_checkpoint_solution()]
    if len(checkpoint_flat) != 7 * len(problem["blocks"]):
        raise RuntimeError("control checkpoint was not captured")
    control = _inspect_flat(ma, problem, live_flat, False)
    checkpoint = _inspect_flat(
        ma, problem, checkpoint_flat, bool(engine.checkpoint_official()))

    telemetry = None
    rng_state = None
    returned = control
    returned_checkpoint = checkpoint
    archive_returned = False
    if args.mode != "reference":
        telemetry = _telemetry(stats)
        control_best = [int(value)
                        for value in engine.get_control_best_solution()]
        if control_best != live_flat:
            raise RuntimeError("live engine solution differs from control sidecar")
        if abs(float(engine.control_best_objective()) -
               control["objective"][-1]) > 1e-9:
            raise RuntimeError("control objective sidecar mismatch")
        rng_state = str(engine.final_rng_state())
        if not rng_state:
            raise RuntimeError("full RNG state was not exported")

        if args.mode == "control":
            if (not _experimental_zero(telemetry) or
                    int(engine.archive_returned()) != 0 or
                    list(engine.get_archive_solution())):
                raise RuntimeError("default-OFF archive telemetry is not zero")
        else:
            if not _live_decisions_zero(telemetry):
                raise RuntimeError("archive changed live closure decision counters")
            archive_returned = bool(engine.archive_returned())
            if archive_returned != bool(telemetry["archive_returned"]):
                raise RuntimeError("archive return flag disagrees with telemetry")
            sidecar = [int(value) for value in engine.get_archive_solution()]
            if archive_returned:
                returned = _inspect_flat(
                    ma, problem, sidecar, bool(engine.returned_official()))
                if returned["objective"][-1] >= control["objective"][-1]:
                    raise RuntimeError("archive selector did not strictly improve")
                if abs(float(telemetry["archive_gain"]) -
                       (control["objective"][-1] -
                        returned["objective"][-1])) > 1e-9:
                    raise RuntimeError("archive gain mismatch")
            elif sidecar or int(engine.returned_official()) != 0:
                raise RuntimeError("non-returned archive leaked a sidecar")

            archive_cp = [int(value) for value in
                          engine.get_archive_checkpoint_solution()]
            returned_checkpoint = _inspect_flat(
                ma, problem, archive_cp,
                bool(engine.archive_checkpoint_official()))
            if (returned_checkpoint["objective"][-1] >
                    checkpoint["objective"][-1] + 1e-9):
                raise RuntimeError("archive checkpoint violated dominance")

    if returned["objective"][-1] > control["objective"][-1] + 1e-9:
        raise RuntimeError("final archive dominance failed")
    module_path = Path(ma.sscore.__file__).resolve()
    report = {
        "schema_version": 2,
        "mode": args.mode,
        "instance": args.instance,
        "eval_seed": args.eval_seed,
        "plateau_seed": args.plateau_seed,
        "iters": FIXED_ITERS,
        "checkpoint_iteration": FIXED_CHECKPOINT,
        "solver": str(args.solver.resolve()),
        "solver_extension": str(module_path),
        "solver_extension_sha256": _sha256_file(module_path),
        "problem": str(problem_path.resolve()),
        "plateau": str(args.plateau.resolve()),
        "start": start,
        "control_final": control,
        "control_checkpoint": checkpoint,
        "returned_final": returned,
        "returned_checkpoint": returned_checkpoint,
        "archive_returned": archive_returned,
        "final_rng_state": rng_state,
        "run_stats": stats,
        "telemetry": telemetry,
        "elapsed": elapsed,
        "peak_rss_kib": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise RuntimeError(f"refusing to overwrite {args.output}")
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "instance": args.instance,
        "eval_seed": args.eval_seed,
        "plateau_seed": args.plateau_seed,
        "mode": args.mode,
        "control": control["objective"][-1],
        "returned": returned["objective"][-1],
        "archive_returned": archive_returned,
        "elapsed": elapsed,
    }, sort_keys=True), flush=True)


def _solution_signature(record: dict[str, Any]) -> dict[str, Any]:
    return {key: record[key] for key in (
        "flat_sha256", "solution_sha256", "objective", "checker_objective",
        "checker_feasible", "audit", "overlong", "official_exit_order",
        "entry_ranks_sha256", "exit_ranks_sha256",
        "greedy_exit_ranks_sha256",
    )}


def _identity(report: dict[str, Any], mode: str, instance: int,
              eval_seed: int, plateau_seed: int) -> None:
    valid = (
        report.get("schema_version") == 2 and report.get("mode") == mode and
        report.get("instance") == instance and
        report.get("eval_seed") == eval_seed and
        report.get("plateau_seed") == plateau_seed and
        report.get("iters") == FIXED_ITERS and
        report.get("checkpoint_iteration") == FIXED_CHECKPOINT and
        isinstance(report.get("run_stats"), list) and
        int(round(float(report["run_stats"][0]))) == FIXED_ITERS and
        (mode == "reference") == (report.get("telemetry") is None) and
        (mode == "reference") == (report.get("final_rng_state") is None)
    )
    if not valid:
        raise RuntimeError(
            f"report identity failed: P{instance} e{eval_seed} {mode}")


def _metrics(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    deltas = [float(row[key]) for row in rows]
    result: dict[str, Any] = {
        "pairs": len(rows),
        "wins": sum(value < -1e-12 for value in deltas),
        "losses": sum(value > 1e-12 for value in deltas),
        "ties": sum(abs(value) <= 1e-12 for value in deltas),
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
            "wins": sum(value < -1e-12 for value in local),
            "losses": sum(value > 1e-12 for value in local),
            "ties": sum(abs(value) <= 1e-12 for value in local),
            "median_delta_pct": statistics.median(local),
        }
    return result


def _heldout_pass(metrics: dict[str, Any]) -> bool:
    p45 = any(metrics["instances"][name]["median_delta_pct"] <= -3.0
              for name in ("P4", "P5"))
    return bool(
        metrics["wins"] > metrics["losses"] and
        metrics["median_delta_pct"] <= -2.0 and p45 and
        all(item["median_delta_pct"] <= 0.0
            for item in metrics["instances"].values())
    )


def _collect(args: argparse.Namespace) -> None:
    if args.profile not in PROFILE_EVAL_SEEDS:
        raise RuntimeError(f"unknown profile {args.profile}")
    eval_seeds = PROFILE_EVAL_SEEDS[args.profile]
    directory = args.directory / args.profile
    rows: list[dict[str, Any]] = []
    mismatches: dict[str, list[str]] = {
        "start": [], "reference_control": [], "archive_control": [],
        "rng": [], "dominance": [], "telemetry": [],
    }
    update_boards: set[int] = set()
    totals = {name: 0.0 for name in
              tuple(CLOSURE_NAMES) + tuple(f"archive_{n}" for n in ARCHIVE_NAMES)}

    for instance in range(3, 7):
        for eval_seed, plateau_seed in zip(eval_seeds, PLATEAU_SEEDS):
            stem = f"p{instance}-e{eval_seed}-ps{plateau_seed}"
            reports = {
                mode: json.loads((directory / f"{stem}-{mode}.json").read_text())
                for mode in MODES
            }
            for mode, report in reports.items():
                _identity(report, mode, instance, eval_seed, plateau_seed)
            reference, control, archive = (
                reports["reference"], reports["control"], reports["archive"])
            label = f"P{instance}-e{eval_seed}-ps{plateau_seed}"

            start_sig = _solution_signature(reference["start"])
            if any(_solution_signature(report["start"]) != start_sig
                   for report in (control, archive)):
                mismatches["start"].append(label)
            reference_control_equal = (
                _solution_signature(reference["control_final"]) ==
                _solution_signature(control["control_final"]) and
                _solution_signature(reference["control_checkpoint"]) ==
                _solution_signature(control["control_checkpoint"]) and
                reference["run_stats"][:21] == control["run_stats"][:21]
            )
            if not reference_control_equal:
                mismatches["reference_control"].append(label)
            archive_control_equal = (
                _solution_signature(archive["control_final"]) ==
                _solution_signature(control["control_final"]) and
                _solution_signature(archive["control_checkpoint"]) ==
                _solution_signature(control["control_checkpoint"]) and
                archive["run_stats"][:21] == control["run_stats"][:21]
            )
            if not archive_control_equal:
                mismatches["archive_control"].append(label)
            if archive["final_rng_state"] != control["final_rng_state"]:
                mismatches["rng"].append(label)

            control_tel = _telemetry([float(v) for v in control["run_stats"]])
            archive_tel = _telemetry([float(v) for v in archive["run_stats"]])
            if (control_tel != control["telemetry"] or
                    archive_tel != archive["telemetry"] or
                    not _experimental_zero(control_tel) or
                    not _live_decisions_zero(archive_tel)):
                mismatches["telemetry"].append(label)
            for name, value in archive_tel.items():
                totals[name] += float(value)
            if int(archive_tel["archive_updates"]) > 0:
                update_boards.add(instance)

            c0 = float(control["control_final"]["checker_objective"])
            c1 = float(archive["returned_final"]["checker_objective"])
            p0 = float(control["control_checkpoint"]["checker_objective"])
            p1 = float(archive["returned_checkpoint"]["checker_objective"])
            if c1 > c0 + 1e-9 or p1 > p0 + 1e-9:
                mismatches["dominance"].append(label)
            rows.append({
                "instance": instance,
                "eval_seed": eval_seed,
                "plateau_seed": plateau_seed,
                "control": c0,
                "returned": c1,
                "delta_pct": 100.0 * (c1 - c0) / c0,
                "checkpoint_control": p0,
                "checkpoint_returned": p1,
                "checkpoint_delta_pct": 100.0 * (p1 - p0) / p0,
                "archive_returned": bool(archive["archive_returned"]),
                "control_elapsed": float(control["elapsed"]),
                "archive_elapsed": float(archive["elapsed"]),
                "slowdown": float(archive["elapsed"]) /
                            float(control["elapsed"]),
                "telemetry": archive_tel,
            })

    contracts_pass = all(not values for values in mismatches.values())
    final = _metrics(rows, "delta_pct")
    checkpoint = _metrics(rows, "checkpoint_delta_pct")
    dev_checks: dict[str, Any] | None = None
    if args.profile == "dev":
        p3 = next(row for row in rows
                  if row["instance"] == 3 and row["eval_seed"] == 23)
        dev_checks = {
            "p3_e23_control_final_100770": p3["control"] == 100770.0,
            "p3_e23_checkpoint_108235":
                p3["checkpoint_control"] == 108235.0,
            "p4_or_p6_archive_update": bool(update_boards & {4, 6}),
        }
        passed = contracts_pass and all(dev_checks.values())
        verdict = "freeze_heldout_gate" if passed else "reject_archive_smoke"
    else:
        passed = contracts_pass and _heldout_pass(final)
        verdict = "advance_equal_wall" if passed else "reject_closure_archive"
    output = {
        "schema_version": 2,
        "profile": args.profile,
        "fixed_iterations": FIXED_ITERS,
        "fixed_checkpoint_iteration": FIXED_CHECKPOINT,
        "seed_mapping": list(zip(eval_seeds, PLATEAU_SEEDS)),
        "rows": rows,
        "mismatches": mismatches,
        "contracts_pass": contracts_pass,
        "dev_checks": dev_checks,
        "checkpoint_diagnostic": checkpoint,
        "final": final,
        "telemetry_totals": totals,
        "archive_update_boards": sorted(update_boards),
        "median_slowdown": statistics.median(row["slowdown"] for row in rows),
        "passed": passed,
        "verdict": verdict,
    }
    summary = directory / "summary.json"
    summary.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(json.dumps(output, sort_keys=True), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("solver", type=Path)
    run.add_argument("plateau", type=Path)
    run.add_argument("instance", type=int, choices=range(3, 7))
    run.add_argument("eval_seed", type=int)
    run.add_argument("plateau_seed", type=int, choices=PLATEAU_SEEDS)
    run.add_argument("mode", choices=MODES)
    run.add_argument("output", type=Path)
    run.add_argument("--iters", type=int, default=FIXED_ITERS)
    run.add_argument("--checkpoint", type=int, default=FIXED_CHECKPOINT)
    collect = sub.add_parser("collect")
    collect.add_argument("directory", type=Path)
    collect.add_argument("profile", choices=tuple(PROFILE_EVAL_SEEDS))
    args = parser.parse_args()
    if args.command == "run":
        _run(args)
    else:
        _collect(args)


if __name__ == "__main__":
    main()
