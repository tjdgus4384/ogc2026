#!/usr/bin/env python3
"""Common-start fixed-iteration A/B for phase-2 orientation ordering."""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import statistics
import sys
import time


REPO = pathlib.Path(__file__).resolve().parent.parent


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def flat_digest(flat: list[int]) -> str:
    return hashlib.sha256(
        json.dumps(flat, separators=(",", ":")).encode()
    ).hexdigest()


def load_solver(path: pathlib.Path):
    sys.path.insert(0, str(path.resolve()))
    import myalgorithm as ma
    return ma


def problem_path(instance: int) -> pathlib.Path:
    return REPO / "data" / "preliminary_test" / f"prob_{instance}.json"


def checked_record(ma, prob: dict, eng, n: int) -> dict:
    flat = list(eng.get_solution())
    raw_solution = ma._recs_to_sol(
        ma._extract(eng, n), list(eng.entry_ranks()))
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


def base(args) -> None:
    ma = load_solver(args.solver)
    prob = json.loads(problem_path(args.instance).read_text())
    eng, starts = ma._seed_free(prob)
    if eng is None:
        raise RuntimeError(f"common construction failed on P{args.instance}")
    flat = list(eng.get_solution())
    report = {
        "schema_version": 1,
        "instance": args.instance,
        "solver": str(args.solver.resolve()),
        "objective": list(eng.objective()),
        "flat_sha256": flat_digest(flat),
        "distinct_starts": len(starts),
        "flat": flat,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True) + "\n")
    print(json.dumps({k: report[k] for k in
                      ("instance", "objective", "flat_sha256",
                       "distinct_starts")}, sort_keys=True), flush=True)


def run(args) -> None:
    ma = load_solver(args.solver)
    prob = json.loads(problem_path(args.instance).read_text())
    n = len(prob["blocks"])
    common = json.loads(args.base.read_text())
    if int(common["instance"]) != args.instance:
        raise RuntimeError("common-start instance mismatch")
    if flat_digest(common["flat"]) != common["flat_sha256"]:
        raise RuntimeError("common-start hash mismatch")

    eng, _due, _proc = ma._fresh(prob, 4)
    eng.set_solution(common["flat"])
    eng.build_occ(eng.horizon())
    if flat_digest(list(eng.get_solution())) != common["flat_sha256"]:
        raise RuntimeError("engine did not load common start exactly")
    start_objective = list(eng.objective())
    if start_objective != common["objective"]:
        raise RuntimeError("common-start objective mismatch")

    t0 = time.monotonic()
    stats = list(eng.run_alns(
        3600.0, 0.05, 0.15, 0.002, 0.9995, args.seed,
        1000, 0.75, 0.04, 0x3FF, args.iters,
    ))
    elapsed = time.monotonic() - t0
    audit = [int(round(v)) for v in eng.audit_state()]
    if audit[0] != 0:
        raise RuntimeError(f"invalid engine state after ALNS: {audit}")
    search_objective = list(eng.objective())
    search_flat = list(eng.get_solution())
    checked = checked_record(ma, prob, eng, n)
    report = {
        "schema_version": 1,
        "instance": args.instance,
        "seed": args.seed,
        "mode": args.mode,
        "iters": args.iters,
        "solver": str(args.solver.resolve()),
        "common_start": str(args.base.resolve()),
        "start_objective": start_objective,
        "start_flat_sha256": common["flat_sha256"],
        "search_objective": search_objective,
        "search_flat_sha256": flat_digest(search_flat),
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
        "search": search_objective[3],
        "gated": checked["objective"],
        "feasible": checked["feasible"],
        "iters": int(round(stats[0])) if stats else None,
        "elapsed": elapsed,
    }, sort_keys=True), flush=True)


def collect(args) -> None:
    rows = []
    for off_path in sorted(args.directory.glob("p*-s*-off.json")):
        stem = off_path.name[:-len("-off.json")]
        off = json.loads(off_path.read_text())
        on = json.loads((args.directory / f"{stem}-on.json").read_text())
        if (off["instance"], off["seed"], off["iters"]) != (
                on["instance"], on["seed"], on["iters"]):
            raise RuntimeError(f"paired identity mismatch at {stem}")
        if off["start_flat_sha256"] != on["start_flat_sha256"]:
            raise RuntimeError(f"paired start mismatch at {stem}")
        a = off["checker"]
        b = on["checker"]
        delta = (100.0 * (float(b["objective"]) - float(a["objective"])) /
                 float(a["objective"])) if a["feasible"] and b["feasible"] else None
        rows.append({
            "instance": int(off["instance"]),
            "seed": int(off["seed"]),
            "off_feasible": bool(a["feasible"]),
            "on_feasible": bool(b["feasible"]),
            "off": a["objective"],
            "on": b["objective"],
            "delta_pct": delta,
        })
    if len(rows) != 16:
        raise RuntimeError(f"expected 16 pairs, found {len(rows)}")
    all_feasible = all(r["off_feasible"] and r["on_feasible"] for r in rows)
    deltas = [float(r["delta_pct"]) for r in rows
              if r["delta_pct"] is not None]
    instances = {}
    for instance in (3, 4, 5, 6):
        local = [float(r["delta_pct"]) for r in rows
                 if r["instance"] == instance and r["delta_pct"] is not None]
        instances[f"P{instance}"] = {
            "pairs": len(local),
            "median_delta_pct": statistics.median(local) if local else None,
            "wins": sum(d < -1e-12 for d in local),
            "losses": sum(d > 1e-12 for d in local),
            "ties": sum(abs(d) <= 1e-12 for d in local),
        }
    medians = [v["median_delta_pct"] for v in instances.values()]
    passed = bool(
        all_feasible
        and len(deltas) == 16
        and statistics.median(deltas) <= 0.0
        and all(v is not None and v <= 3.0 for v in medians)
        and any(v is not None and v <= -3.0 for v in medians)
    )
    wins = sum(d < -1e-12 for d in deltas)
    losses = sum(d > 1e-12 for d in deltas)
    summary = {
        "all_32_checker_feasible": all_feasible,
        "pairs": len(rows),
        "informative": len(deltas),
        "wins": wins,
        "losses": losses,
        "ties": len(deltas) - wins - losses,
        "median_delta_pct": statistics.median(deltas) if deltas else None,
        "instances": instances,
        "verdict": "advance_replication" if passed else "reject_fixed_screen",
    }
    output = {"schema_version": 1, "rows": rows, "summary": summary}
    (args.directory / "summary.json").write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p_base = sub.add_parser("base")
    p_base.add_argument("solver", type=pathlib.Path)
    p_base.add_argument("instance", type=int, choices=(3, 4, 5, 6))
    p_base.add_argument("output", type=pathlib.Path)
    p_base.set_defaults(func=base)
    p_run = sub.add_parser("run")
    p_run.add_argument("solver", type=pathlib.Path)
    p_run.add_argument("instance", type=int, choices=(3, 4, 5, 6))
    p_run.add_argument("seed", type=int)
    p_run.add_argument("iters", type=int)
    p_run.add_argument("mode", choices=("off", "on"))
    p_run.add_argument("base", type=pathlib.Path)
    p_run.add_argument("output", type=pathlib.Path)
    p_run.set_defaults(func=run)
    p_collect = sub.add_parser("collect")
    p_collect.add_argument("directory", type=pathlib.Path)
    p_collect.set_defaults(func=collect)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
