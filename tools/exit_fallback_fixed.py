#!/usr/bin/env python3
"""Deterministic OFF/SHADOW/ON gate for exact EXIT candidate routing.

All arms start from the exact same serialized construction and run the same
single-thread ALNS seed for a fixed iteration count.  OFF and SHADOW must be
state-identical; SHADOW prices the first exact-only candidate against the first
later legacy candidate but preserves the legacy choice.  ON may select a
strictly cheaper exact candidate, or consume it after the legacy scan is
exhausted.  This isolates mechanism from node speed and seed construction.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pathlib
import statistics
import sys
import time


REPO = pathlib.Path(__file__).resolve().parent.parent


def load_solver(solver: pathlib.Path):
    sys.path.insert(0, str(solver.resolve()))
    import myalgorithm as ma
    return ma


def problem_path(instance: int) -> pathlib.Path:
    return REPO / "data" / "preliminary_test" / f"prob_{instance}.json"


def digest(value) -> str:
    raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()


def cmd_base(args) -> None:
    ma = load_solver(args.solver)
    prob = json.loads(problem_path(args.instance).read_text())
    eng, _starts = ma._seed_free(prob)
    if eng is None:
        raise RuntimeError("common fixed-oracle construction failed")
    flat = list(eng.get_solution())
    report = {
        "instance": args.instance,
        "objective": list(eng.objective()),
        "flat_sha256": digest(flat),
        "flat": flat,
    }
    args.output.write_text(json.dumps(report, sort_keys=True) + "\n")


def cmd_run(args) -> None:
    ma = load_solver(args.solver)
    prob = json.loads(problem_path(args.instance).read_text())
    base = json.loads(args.base.read_text())
    eng, _due, _proc = ma._fresh(prob, 4)
    eng.set_solution(base["flat"])
    eng.build_occ(eng.horizon())
    start = list(eng.objective())
    t0 = time.monotonic()
    stats = list(eng.run_alns(
        3600.0, 0.05, 0.15, 0.002, 0.9995, args.seed,
        1000, 0.75, 0.04, 0x3FF, args.iters,
    ))
    elapsed = time.monotonic() - t0
    flat = list(eng.get_solution())
    search_objective = list(eng.objective())
    exact = args.mode == "on"
    xr = list(eng.exit_ranks()) if exact else None
    sol = ma._recs_to_sol(ma._extract(eng, len(prob["blocks"])),
                          list(eng.entry_ranks()), xr)
    raw_checked = ma._check(prob, sol)
    audit = list(eng.audit_state())
    if audit[0] != 0:
        raise RuntimeError(
            f"invalid engine state mode={args.mode} audit={audit}")
    gate_t0 = time.monotonic()
    gated_sol, gated = ma._gate(prob, eng, len(prob["blocks"]))
    gate_elapsed = time.monotonic() - gate_t0
    if (gated_sol is None or not gated or
            not gated.get("feasible") or
            not isinstance(gated.get("objective"), (int, float))):
        raise RuntimeError(
            f"production gate failed mode={args.mode} raw={raw_checked} "
            f"gate={gated}")
    report = {
        "instance": args.instance,
        "seed": args.seed,
        "mode": args.mode,
        "iters": args.iters,
        "elapsed": elapsed,
        "start_objective": start,
        "objective": search_objective,
        "raw_checker_feasible": bool(raw_checked and raw_checked.get("feasible")),
        "raw_checker_stage": None if raw_checked is None else raw_checked.get("stage"),
        "gated_objective": gated.get("objective"),
        "gated_solution_sha256": digest(gated_sol),
        "gate_elapsed": gate_elapsed,
        "flat_sha256": digest(flat),
        "audit": audit,
        "run_stats": stats,
    }
    args.output.write_text(json.dumps(report, sort_keys=True) + "\n")


def sign_test(wins: int, losses: int) -> float:
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2**n)


def stat_count(report: dict, index: int) -> int:
    """Read an appended diagnostic while retaining old-artifact support."""
    stats = report.get("run_stats", [])
    return int(round(stats[index])) if index < len(stats) else 0


def cmd_collect(args) -> None:
    rows = []
    for off_path in sorted(args.directory.glob("p*-s*-off.json")):
        stem = off_path.name[:-len("-off.json")]
        off = json.loads(off_path.read_text())
        shadow = json.loads((args.directory / f"{stem}-shadow.json").read_text())
        on = json.loads((args.directory / f"{stem}-on.json").read_text())
        # Timing and diagnostic counters may differ; all trajectory/state
        # observables through badPhase must not.
        aa_equal = (
            off["flat_sha256"] == shadow["flat_sha256"]
            and off["objective"] == shadow["objective"]
            and off["run_stats"][:8] == shadow["run_stats"][:8]
            and off["gated_solution_sha256"] == shadow["gated_solution_sha256"]
            and off["gated_objective"] == shadow["gated_objective"]
        )
        if not aa_equal:
            raise RuntimeError(f"OFF/SHADOW trajectory mismatch at {stem}")
        a = float(off["gated_objective"])
        b = float(on["gated_objective"])
        rows.append({
            "instance": int(off["instance"]), "seed": int(off["seed"]),
            "off": a, "on": b, "delta_pct": 100.0 * (b - a) / a,
            "shadow_tests": stat_count(shadow, 8),
            "shadow_pass": stat_count(shadow, 9),
            "on_tests": stat_count(on, 8),
            "on_pass": stat_count(on, 9),
            "shadow_compared": stat_count(shadow, 10),
            "shadow_dominant": stat_count(shadow, 11),
            "on_compared": stat_count(on, 10),
            "on_dominant": stat_count(on, 11),
            "on_rescued": stat_count(on, 12),
            "off_elapsed": float(off["elapsed"]),
            "shadow_elapsed": float(shadow["elapsed"]),
            "on_elapsed": float(on["elapsed"]),
            "aa_equal": True,
        })

    if not rows:
        raise RuntimeError(f"no result rows in {args.directory}")
    print("instance seed       OFF          ON      delta  shadow pass  cmp/dom  rescue  time")
    for r in rows:
        print(f"P{r['instance']:<7} {r['seed']:<4} {r['off']:>11,.0f} "
              f"{r['on']:>11,.0f} {r['delta_pct']:>+8.3f}% "
              f"{r['shadow_pass']:>5}/{r['shadow_tests']:<6} "
              f"{r['shadow_compared']:>4}/{r['shadow_dominant']:<4} "
              f"{r['on_rescued']:>6} "
              f"{r['on_elapsed']/max(1e-9,r['off_elapsed']):>5.2f}x")

    summary = {"pairs": len(rows), "aa_mismatches": 0, "instances": {}}
    for instance in sorted({r["instance"] for r in rows}):
        rr = [r for r in rows if r["instance"] == instance]
        ds = [r["delta_pct"] for r in rr]
        wins = sum(d < -1e-12 for d in ds)
        losses = sum(d > 1e-12 for d in ds)
        item = {
            "pairs": len(rr), "wins": wins, "losses": losses,
            "ties": len(rr) - wins - losses,
            "median_delta_pct": statistics.median(ds),
            "mean_delta_pct": statistics.fmean(ds),
            "sign_p": sign_test(wins, losses),
            "shadow_tests": sum(r["shadow_tests"] for r in rr),
            "shadow_pass": sum(r["shadow_pass"] for r in rr),
            "shadow_compared": sum(r["shadow_compared"] for r in rr),
            "shadow_dominant": sum(r["shadow_dominant"] for r in rr),
            "on_rescued": sum(r["on_rescued"] for r in rr),
            "median_shadow_time_ratio": statistics.median(
                r["shadow_elapsed"] / max(1e-9, r["off_elapsed"]) for r in rr),
            "median_time_ratio": statistics.median(
                r["on_elapsed"] / max(1e-9, r["off_elapsed"]) for r in rr),
        }
        summary["instances"][f"P{instance}"] = item
        print(f"P{instance}: {wins}W/{losses}L/{item['ties']}T  "
              f"median {item['median_delta_pct']:+.3f}%  "
              f"mean {item['mean_delta_pct']:+.3f}%  "
              f"shadow {item['shadow_pass']}/{item['shadow_tests']}  "
              f"cmp/dom {item['shadow_compared']}/{item['shadow_dominant']}  "
              f"rescue {item['on_rescued']}  "
              f"time {item['median_time_ratio']:.2f}x")
    all_ds = [r["delta_pct"] for r in rows]
    wins = sum(d < -1e-12 for d in all_ds)
    losses = sum(d > 1e-12 for d in all_ds)
    summary["all"] = {
        "wins": wins, "losses": losses, "ties": len(rows)-wins-losses,
        "changed": wins + losses,
        "median_delta_pct": statistics.median(all_ds),
        "mean_delta_pct": statistics.fmean(all_ds),
        "sign_p": sign_test(wins, losses),
        "shadow_compared": sum(r["shadow_compared"] for r in rows),
        "shadow_dominant": sum(r["shadow_dominant"] for r in rows),
        "on_rescued": sum(r["on_rescued"] for r in rows),
        "median_shadow_time_ratio": statistics.median(
            r["shadow_elapsed"] / max(1e-9, r["off_elapsed"]) for r in rows),
    }
    print(f"ALL: {wins}W/{losses}L/{len(rows)-wins-losses}T  "
          f"median {summary['all']['median_delta_pct']:+.3f}%  "
          f"p={summary['all']['sign_p']:.4f}")
    (args.directory / "summary.json").write_text(
        json.dumps({"rows": rows, "summary": summary}, indent=2,
                   sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    base = sub.add_parser("base")
    base.add_argument("solver", type=pathlib.Path)
    base.add_argument("instance", type=int)
    base.add_argument("output", type=pathlib.Path)
    base.set_defaults(func=cmd_base)

    run = sub.add_parser("run")
    run.add_argument("solver", type=pathlib.Path)
    run.add_argument("instance", type=int)
    run.add_argument("seed", type=int)
    run.add_argument("iters", type=int)
    run.add_argument("mode", choices=("off", "shadow", "on"))
    run.add_argument("base", type=pathlib.Path)
    run.add_argument("output", type=pathlib.Path)
    run.set_defaults(func=cmd_run)

    collect = sub.add_parser("collect")
    collect.add_argument("directory", type=pathlib.Path)
    collect.set_defaults(func=cmd_collect)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
