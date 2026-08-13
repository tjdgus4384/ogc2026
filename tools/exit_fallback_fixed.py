#!/usr/bin/env python3
"""Deterministic OFF/SHADOW/ON gate for failed-insert EXIT fallbacks.

All arms start from the exact same serialized construction and run the same
single-thread ALNS seed for a fixed iteration count.  OFF and SHADOW must be
state-identical; SHADOW only prices the first exact fallback in an otherwise
failed insertion.  ON may consume that fallback after the legacy scan is
exhausted.  This isolates mechanism from node speed and from seed construction.
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


def digest(flat: list[int]) -> str:
    raw = json.dumps(flat, separators=(",", ":")).encode()
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
    exact = args.mode == "on"
    xr = list(eng.exit_ranks()) if exact else None
    sol = ma._recs_to_sol(ma._extract(eng, len(prob["blocks"])),
                          list(eng.entry_ranks()), xr)
    checked = ma._check(prob, sol)
    audit = list(eng.audit_state())
    if not checked or not checked.get("feasible") or audit[0] != 0:
        raise RuntimeError(
            f"invalid result mode={args.mode} checker={checked} audit={audit}")
    report = {
        "instance": args.instance,
        "seed": args.seed,
        "mode": args.mode,
        "iters": args.iters,
        "elapsed": elapsed,
        "start_objective": start,
        "objective": list(eng.objective()),
        "checker_objective": checked.get("objective"),
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
        )
        if not aa_equal:
            raise RuntimeError(f"OFF/SHADOW trajectory mismatch at {stem}")
        a = float(off["objective"][3])
        b = float(on["objective"][3])
        rows.append({
            "instance": int(off["instance"]), "seed": int(off["seed"]),
            "off": a, "on": b, "delta_pct": 100.0 * (b - a) / a,
            "shadow_tests": int(round(shadow["run_stats"][8])),
            "shadow_pass": int(round(shadow["run_stats"][9])),
            "on_tests": int(round(on["run_stats"][8])),
            "on_pass": int(round(on["run_stats"][9])),
            "off_elapsed": float(off["elapsed"]),
            "on_elapsed": float(on["elapsed"]),
            "aa_equal": True,
        })

    if not rows:
        raise RuntimeError(f"no result rows in {args.directory}")
    print("instance seed       OFF          ON      delta   shadow pass   ON pass  time")
    for r in rows:
        print(f"P{r['instance']:<7} {r['seed']:<4} {r['off']:>11,.0f} "
              f"{r['on']:>11,.0f} {r['delta_pct']:>+8.3f}% "
              f"{r['shadow_pass']:>6}/{r['shadow_tests']:<6} "
              f"{r['on_pass']:>6}/{r['on_tests']:<6} "
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
            "median_time_ratio": statistics.median(
                r["on_elapsed"] / max(1e-9, r["off_elapsed"]) for r in rr),
        }
        summary["instances"][f"P{instance}"] = item
        print(f"P{instance}: {wins}W/{losses}L/{item['ties']}T  "
              f"median {item['median_delta_pct']:+.3f}%  "
              f"mean {item['mean_delta_pct']:+.3f}%  "
              f"shadow {item['shadow_pass']}/{item['shadow_tests']}  "
              f"time {item['median_time_ratio']:.2f}x")
    all_ds = [r["delta_pct"] for r in rows]
    wins = sum(d < -1e-12 for d in all_ds)
    losses = sum(d > 1e-12 for d in all_ds)
    summary["all"] = {
        "wins": wins, "losses": losses, "ties": len(rows)-wins-losses,
        "median_delta_pct": statistics.median(all_ds),
        "mean_delta_pct": statistics.fmean(all_ds),
        "sign_p": sign_test(wins, losses),
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
