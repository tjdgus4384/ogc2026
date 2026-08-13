#!/usr/bin/env python3
"""Frozen fixed-iteration gate for the control-preserving exit archive."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import statistics
import sys
import time
from typing import Any


HARNESS_ROOT = Path(__file__).resolve().parents[1]
ROOT = Path(os.environ.get("OGC_ROOT", HARNESS_ROOT)).resolve()
sys.path.insert(0, str(HARNESS_ROOT / "tools"))
from assignment_transfer_fixed import digest, flat_digest  # noqa: E402


FIXED_ITERS = 20_000
FIXED_CHECKPOINT = 1_000
PLATEAU_SEEDS = (7, 11, 23, 41)
PROFILE_EVAL_SEEDS = {
    "dev": (7, 11, 23, 41),
    "heldout": (3, 17, 31, 47),
}
MODES = ("reference", "control", "archive")

# Frozen held-out authority.  These are values, not first-seen baselines: an
# internally consistent but wrong binary/problem/start/harness must fail.
EXPECTED_DRIVER_SHA256 = (
    "f122e6d5e4b2acf94ea7253ca0815c3dbb15c0e3093b238a932112123c9d81a6")
EXPECTED_HELPER_SHA256 = (
    "4e36a5e061540da0738a0624b2150023421d71a0af8dc2fb33726065c3f31f83")
EXPECTED_MYALGORITHM_SHA256 = (
    "e53de42538f8dec45dcda13864559bd39c2d86b356e7780e661635e935a24a29")
EXPECTED_CHECKER_SHA256 = (
    "d45aaeafdce8bf80d59d097f655c43313a4951bed43b6628e3b1cf62d4876a94")
EXPECTED_SOLVER = {
    "reference": {
        "source": "9002c73b5831e3e334f93e9a2c41ba31ed8d54c4",
        "binary": "177a09acb6da04ad7851e7381d306561f01d198b89e0adf16140676fb7d0d5a9",
    },
    "candidate": {
        "source": "1dcdef092c7813d2ee7229fe466acb2f1303477d",
        "binary": "a5c384dd1c7d797fbce2c9c7a01e1a30d8abbee0bb26f6fa839f71d919629e9f",
    },
}
INSTANCE_N = {3: 200, 4: 150, 5: 200, 6: 250}
EXPECTED_PROBLEM_SHA256 = {
    3: "c4f47c17910cf7c4a8aceaff74e6a5b884f752c389383fdd95f2ae2ce9a708d4",
    4: "6faa53137e705d44dc469a27e0f090fff07aec6b61712bb0edcaeba983d6d17a",
    5: "823eb0f8211071ceafa56a275e45b0f7d01c3448fb2548c5e4798dd0e723db00",
    6: "43b6103cd1f1be2cbcccaad6fb29da30302e6302d01e1e620c2c67cedb7244b2",
}
EXPECTED_PLATEAU_SHA256 = {
    (3, 7): "4190e5a73867bcf1b6702544de0b5c34e095fe53ce0f5bbe3b4493aa6f431eb4",
    (3, 11): "e5ee6354de980191576679ca2279cbd02e24e7fa9753e329485bab701af78349",
    (3, 23): "8f84e933e25d0d3369350d4bbe7ee1607c1fe0491db5de6a8485d9d8ded5be56",
    (3, 41): "28cc587b216f0cc90b50508d5d49d506986696d48ad2410d000fe0142dad32f9",
    (4, 7): "7b3cee57bee39979fa044fa99b7b3495a919ddda9b9d8423c4222a9dba586e93",
    (4, 11): "f64c4468b0d739321c664e299ee5453d11c910a75385ebc13c1afcba94f0941d",
    (4, 23): "9c6cb013e17e1440ef78b258dc2c13c897468f1f31280ac616790a22e7ffd287",
    (4, 41): "3b9cc827fdc9bf28312a9bedf73deb459b8d9d0c4d8daaa95e29811132dac501",
    (5, 7): "07fbd253d9b4bddbda5b5989617b88a9ac6ede03d36eb8488c99334157ce0b35",
    (5, 11): "84285be693ef3182bd3a68204ab3854501c3fbe0f2880a1b48136f89ceefb78f",
    (5, 23): "654ae86e7a835cdc04f4d70b72b7b5d87520b4be4763317a58adb8580cb893d8",
    (5, 41): "2e1941b6affc7134b2bb33cfcdae22e1be3d5543c564ad5d57d58686cbcbf175",
    (6, 7): "43427bbde27aebde8f7565aa46fb8667497bb84b3b9e96014c6b94208de1aa0f",
    (6, 11): "6e67fde94eb4ff4b10e7806aacbc4b00f04cfac1a004de99ac693d7ff083f305",
    (6, 23): "a9fc73e10927cf8e52cf0b410cf4fe92817ce9813aabdacde9e1abf1be921ec8",
    (6, 41): "1138c43bebd92d44b46284a09e45acc8c402d24f8dc8a98244ce87cd121e4874",
}

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
        # The independent greedy oracle may choose a different legal order
        # when several removals are simultaneously possible.  It is a second
        # existence witness, not a canonical-order equality oracle; the raw
        # checker below remains authoritative for the emitted LIFO ranks.
        if len(greedy) != n or any(rank < 0 for rank in greedy):
            raise RuntimeError("independent EXIT-order search failed")
        groups: dict[tuple[int, int], list[int]] = {}
        for block_id in range(n):
            if flat[7 * block_id + 6]:
                groups.setdefault((flat[7 * block_id],
                                   flat[7 * block_id + 5]), []).append(block_id)
        for ids in groups.values():
            if sorted(greedy[block_id] for block_id in ids) != list(range(len(ids))):
                raise RuntimeError("independent EXIT ranks are not a permutation")
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
    if len(stats) != 50:
        raise RuntimeError(
            f"archive telemetry ABI requires exactly 50 stats, got {len(stats)}")
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
        c["archive_bound_pruned"] <= c["suffix_tried"],
        c["archive_returned"] in (0, 1),
        c["archive_returned"] <= c["archive_updates"],
        c["returned_official"] in (0, 1),
        c["archive_audit_rejects"] <= c["suffix_tried"],
        (c["archive_updates"] != 0 or c["archive_objective"] == 0.0),
        (c["archive_returned"] != 0 or c["archive_gain"] == 0.0),
        (c["archive_returned"] == 0 or c["archive_gain"] > 0.0),
        (c["archive_returned"] == 0 or c["returned_official"] == 1),
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
        "solver_source_commit": (
            (args.solver / "SOURCE_COMMIT").read_text().strip()
            if (args.solver / "SOURCE_COMMIT").is_file() else None),
        "driver_sha256": _sha256_file(Path(__file__).resolve()),
        "helper_sha256": _sha256_file(
            HARNESS_ROOT / "tools" / "assignment_transfer_fixed.py"),
        "myalgorithm_sha256": _sha256_file(args.solver / "myalgorithm.py"),
        "checker_sha256": _sha256_file(args.solver / "utils.py"),
        "problem_sha256": _sha256_file(problem_path),
        "plateau_sha256": _sha256_file(args.plateau),
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


def _validate_solution_record(record: dict[str, Any], label: str,
                              expected_n: int) -> None:
    flat = record.get("flat")
    objective = record.get("objective")
    audit = record.get("audit")
    exact = bool(record.get("overlong"))
    valid = (
        isinstance(flat, list) and len(flat) == 7 * expected_n and
        all(isinstance(value, int) and not isinstance(value, bool)
            for value in flat) and
        isinstance(record.get("flat_sha256"), str) and
        len(record["flat_sha256"]) == 64 and
        record["flat_sha256"] == flat_digest(flat) and
        isinstance(record.get("solution_sha256"), str) and
        len(record["solution_sha256"]) == 64 and
        isinstance(objective, list) and len(objective) == 4 and
        isinstance(record.get("checker_objective"), (int, float)) and
        abs(float(record["checker_objective"]) -
            float(objective[-1])) <= 1e-9 and
        record.get("checker_feasible") is True and
        isinstance(audit, list) and len(audit) > 0 and audit[0] == 0 and
        bool(record.get("official_exit_order")) == exact and
        isinstance(record.get("entry_ranks_sha256"), str) and
        ((not exact and record.get("exit_ranks_sha256") is None and
          record.get("greedy_exit_ranks_sha256") is None) or
         (exact and isinstance(record.get("exit_ranks_sha256"), str) and
          isinstance(record.get("greedy_exit_ranks_sha256"), str)))
    )
    if not valid:
        raise RuntimeError(f"stored solution authority failed: {label}")


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
        len(report["run_stats"]) == (44 if mode == "reference" else 50) and
        int(round(float(report["run_stats"][0]))) == FIXED_ITERS and
        (mode == "reference") == (report.get("telemetry") is None) and
        (mode == "reference") == (report.get("final_rng_state") is None)
    )
    if not valid:
        raise RuntimeError(
            f"report identity failed: P{instance} e{eval_seed} {mode}")
    for field in (
            "start", "control_final", "control_checkpoint",
            "returned_final", "returned_checkpoint"):
        value = report.get(field)
        if not isinstance(value, dict):
            raise RuntimeError(f"report is missing {field}")
        _validate_solution_record(
            value, f"P{instance} e{eval_seed} {mode}:{field}",
            INSTANCE_N[instance])
    authority = EXPECTED_SOLVER[
        "reference" if mode == "reference" else "candidate"]
    expected_provenance = (
        report.get("solver_source_commit") == authority["source"] and
        report.get("solver_extension_sha256") == authority["binary"] and
        report.get("driver_sha256") == EXPECTED_DRIVER_SHA256 and
        report.get("helper_sha256") == EXPECTED_HELPER_SHA256 and
        report.get("myalgorithm_sha256") == EXPECTED_MYALGORITHM_SHA256 and
        report.get("checker_sha256") == EXPECTED_CHECKER_SHA256 and
        report.get("problem_sha256") == EXPECTED_PROBLEM_SHA256[instance] and
        report.get("plateau_sha256") ==
            EXPECTED_PLATEAU_SHA256[(instance, plateau_seed)]
    )
    if not expected_provenance:
        raise RuntimeError(
            f"frozen provenance mismatch: P{instance} e{eval_seed} {mode}")
    if (float(report["returned_final"]["checker_objective"]) >
            float(report["control_final"]["checker_objective"]) + 1e-9 or
            float(report["returned_checkpoint"]["checker_objective"]) >
            float(report["control_checkpoint"]["checker_objective"]) + 1e-9):
        raise RuntimeError(
            f"stored dominance failed: P{instance} e{eval_seed} {mode}")


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
    expected_files = {
        f"p{instance}-e{eval_seed}-ps{plateau_seed}-{mode}.json"
        for instance in range(3, 7)
        for eval_seed, plateau_seed in zip(eval_seeds, PLATEAU_SEEDS)
        for mode in MODES
    }
    actual_files = {path.name for path in directory.glob("*.json")
                    if path.name != "summary.json"}
    if actual_files != expected_files:
        raise RuntimeError(
            "gate report set mismatch: "
            f"missing={sorted(expected_files - actual_files)}, "
            f"extra={sorted(actual_files - expected_files)}")
    reference_binary: str | None = None
    candidate_binary: str | None = None
    reference_provenance: dict[str, Any] | None = None
    candidate_provenance: dict[str, Any] | None = None
    common_provenance: dict[str, Any] | None = None

    for instance in range(3, 7):
        for eval_seed, plateau_seed in zip(eval_seeds, PLATEAU_SEEDS):
            stem = f"p{instance}-e{eval_seed}-ps{plateau_seed}"
            reports = {
                mode: json.loads((directory / f"{stem}-{mode}.json").read_text())
                for mode in MODES
            }
            for mode, report in reports.items():
                _identity(report, mode, instance, eval_seed, plateau_seed)
                binary = report.get("solver_extension_sha256")
                if not isinstance(binary, str) or len(binary) != 64:
                    raise RuntimeError(f"missing solver hash: {stem}-{mode}")
                if mode == "reference":
                    reference_binary = reference_binary or binary
                    if binary != reference_binary:
                        raise RuntimeError("mixed reference binaries in gate")
                    current = {key: report.get(key) for key in (
                        "solver_source_commit", "myalgorithm_sha256",
                        "checker_sha256")}
                    reference_provenance = reference_provenance or current
                    if current != reference_provenance:
                        raise RuntimeError("mixed reference provenance in gate")
                else:
                    candidate_binary = candidate_binary or binary
                    if binary != candidate_binary:
                        raise RuntimeError("mixed candidate binaries in gate")
                    current = {key: report.get(key) for key in (
                        "solver_source_commit", "myalgorithm_sha256",
                        "checker_sha256")}
                    candidate_provenance = candidate_provenance or current
                    if current != candidate_provenance:
                        raise RuntimeError("mixed candidate provenance in gate")
                shared = {key: report.get(key) for key in (
                    "driver_sha256", "helper_sha256", "problem_sha256",
                    "plateau_sha256")}
                # Problem and plateau intentionally vary by cell, while the
                # immutable driver/helper must not. Their per-cell identities
                # are compared across the three arms below.
                fixed_shared = {key: shared[key]
                                for key in ("driver_sha256", "helper_sha256")}
                common_provenance = common_provenance or fixed_shared
                if fixed_shared != common_provenance:
                    raise RuntimeError("mixed harness provenance in gate")
            reference, control, archive = (
                reports["reference"], reports["control"], reports["archive"])
            label = f"P{instance}-e{eval_seed}-ps{plateau_seed}"
            for key in ("problem_sha256", "plateau_sha256"):
                if len({report.get(key) for report in reports.values()}) != 1:
                    raise RuntimeError(f"cross-arm {key} mismatch: {label}")

            start_sig = _solution_signature(reference["start"])
            if any(_solution_signature(report["start"]) != start_sig
                   for report in (control, archive)):
                mismatches["start"].append(label)
            reference_control_equal = (
                _solution_signature(reference["control_final"]) ==
                _solution_signature(control["control_final"]) and
                _solution_signature(reference["control_checkpoint"]) ==
                _solution_signature(control["control_checkpoint"]) and
                reference["run_stats"][:15] == control["run_stats"][:15]
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
            if bool(archive["archive_returned"]):
                if (abs(float(archive_tel["archive_objective"]) -
                        float(archive["returned_final"]["checker_objective"])) >
                        1e-9 or
                    abs(float(archive_tel["archive_gain"]) -
                        (float(archive["control_final"]["checker_objective"]) -
                         float(archive["returned_final"]["checker_objective"]))) >
                        1e-9 or
                    int(archive_tel["returned_official"]) !=
                        int(bool(archive["returned_final"]["overlong"]))):
                    mismatches["telemetry"].append(label)
            elif (_solution_signature(archive["returned_final"]) !=
                  _solution_signature(archive["control_final"]) or
                  float(archive_tel["archive_gain"]) != 0.0):
                mismatches["telemetry"].append(label)
            if (bool(archive["archive_returned"]) !=
                    bool(archive_tel["archive_returned"])):
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
        "reference_binary_sha256": reference_binary,
        "candidate_binary_sha256": candidate_binary,
        "reference_provenance": reference_provenance,
        "candidate_provenance": candidate_provenance,
        "harness_provenance": common_provenance,
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
