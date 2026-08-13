#!/usr/bin/env python3
"""Deterministic kill test for phase-2 orientation-order wiring."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import random
import statistics
import sys
import time


REPO = pathlib.Path(__file__).resolve().parent.parent


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def seed_orders(prob: dict) -> list[tuple[str, list[int]]]:
    blocks = prob["blocks"]
    n = len(blocks)
    due = [int(round(b["due_date"])) for b in blocks]
    proc = [max(1, int(math.ceil(b["processing_time"] - 1e-9)))
            for b in blocks]
    release = [int(math.ceil(b["release_time"] - 1e-9)) for b in blocks]
    workload = [float(b["workload"]) for b in blocks]
    rng = random.Random(5)
    return [
        ("due", sorted(range(n), key=lambda i: due[i])),
        ("slack", sorted(range(n),
                         key=lambda i: (due[i] - proc[i] - release[i],
                                        -workload[i]))),
        ("workload", sorted(range(n), key=lambda i: (-workload[i], due[i]))),
        ("due_long", sorted(range(n), key=lambda i: (due[i], -proc[i]))),
        ("noisy_a", sorted(range(n),
                           key=lambda i: due[i] + rng.uniform(-8, 8))),
        ("noisy_b", sorted(range(n),
                           key=lambda i: due[i] + rng.uniform(-8, 8))),
    ]


def run(args) -> None:
    sys.path.insert(0, str(args.solver.resolve()))
    import myalgorithm as ma

    problem_path = REPO / "data" / "preliminary_test" / f"prob_{args.instance}.json"
    prob = json.loads(problem_path.read_text())
    n = len(prob["blocks"])
    engine, _due, _proc = ma._fresh(prob, 4)
    free = [-1] * n

    def construct(order: list[int], seed: int):
        code = int(engine.reconstruct_ordered(free, order, False, seed))
        stats = [int(v) for v in engine.reconstruct_stats()]
        if code != -1:
            return {"status": "failed", "block": code, "phase2": stats}
        return {
            "status": "built",
            "objective": [float(v) for v in engine.objective()],
            "flat": list(engine.get_solution()),
            "phase2": stats,
        }

    def checker_record(flat: list[int]) -> dict:
        engine.set_solution(flat)
        engine.build_occ(engine.horizon())
        recs = ma._extract(engine, n)
        ranks = list(engine.entry_ranks())
        raw_solution = ma._recs_to_sol(recs, ranks)
        raw = ma._check(prob, raw_solution)
        if raw and raw.get("feasible"):
            return {
                "feasible": True,
                "objective": float(raw["objective"]),
                "rescued": False,
                "solution_sha256": digest(raw_solution),
            }
        solution, gated = ma._gate(prob, engine, n, time.time() + 60.0)
        if solution is None or not gated or not gated.get("feasible"):
            raise RuntimeError(f"production gate failed: raw={raw} gated={gated}")
        return {
            "feasible": True,
            "objective": float(gated["objective"]),
            "rescued": True,
            "solution_sha256": digest(solution),
        }

    rows = []
    started = time.monotonic()
    for seed in (7, 11):
        for name, order in seed_orders(prob):
            first = construct(order, seed)
            second = construct(order, seed)
            deterministic = first == second
            if not deterministic:
                raise RuntimeError(
                    f"non-deterministic reconstruction: P{args.instance} {name} s{seed}")
            row = {
                "seed": seed,
                "order": name,
                "status": first["status"],
                "deterministic": True,
                "phase2": first["phase2"],
            }
            if first["status"] == "built":
                flat = first["flat"]
                checked = checker_record(flat)
                if abs(checked["objective"] - first["objective"][3]) > 1e-6 \
                        and not checked["rescued"]:
                    raise RuntimeError("kernel/checker objective mismatch")
                row.update({
                    "objective": first["objective"],
                    "flat_sha256": digest(flat),
                    "checker": checked,
                })
            else:
                row["failed_block"] = first["block"]
            rows.append(row)

    report = {
        "schema_version": 1,
        "instance": args.instance,
        "mode": args.mode,
        "solver": str(args.solver.resolve()),
        "problem": str(problem_path),
        "rows": rows,
        "summary": {
            "cases": len(rows),
            "built": sum(r["status"] == "built" for r in rows),
            "failed": sum(r["status"] != "built" for r in rows),
            "phase2_calls": sum(r["phase2"][0] for r in rows),
            "phase2_success": sum(r["phase2"][1] for r in rows),
            "rescued": sum(bool(r.get("checker", {}).get("rescued")) for r in rows),
            "wall_seconds": time.monotonic() - started,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"instance": args.instance, "mode": args.mode,
                      **report["summary"]}, sort_keys=True), flush=True)


def collect(args) -> None:
    pairs = []
    for instance in (3, 4, 5, 6):
        off = json.loads((args.directory / f"p{instance}-off.json").read_text())
        on = json.loads((args.directory / f"p{instance}-on.json").read_text())
        off_rows = {(r["seed"], r["order"]): r for r in off["rows"]}
        on_rows = {(r["seed"], r["order"]): r for r in on["rows"]}
        if off_rows.keys() != on_rows.keys():
            raise RuntimeError(f"case mismatch on P{instance}")
        for key in sorted(off_rows):
            a, b = off_rows[key], on_rows[key]
            if a["status"] != b["status"]:
                raise RuntimeError(f"construction status mismatch P{instance} {key}")
            if a["status"] != "built":
                continue
            if not a["checker"]["feasible"] or not b["checker"]["feasible"]:
                raise RuntimeError(f"checker rejection P{instance} {key}")
            oa = float(a["checker"]["objective"])
            ob = float(b["checker"]["objective"])
            pairs.append({
                "instance": instance,
                "seed": key[0],
                "order": key[1],
                "off": oa,
                "on": ob,
                "delta_pct": 100.0 * (ob - oa) / oa,
                "raw_changed": a["flat_sha256"] != b["flat_sha256"],
                "off_phase2": a["phase2"],
                "on_phase2": b["phase2"],
            })

    changed = [r for r in pairs if r["raw_changed"]]
    wins = sum(r["delta_pct"] < -1e-12 for r in changed)
    losses = sum(r["delta_pct"] > 1e-12 for r in changed)
    ties = len(changed) - wins - losses
    per_instance = {}
    for instance in (3, 4, 5, 6):
        rows = [r for r in pairs if r["instance"] == instance]
        cc = [r for r in rows if r["raw_changed"]]
        per_instance[f"P{instance}"] = {
            "pairs": len(rows),
            "changed": len(cc),
            "wins": sum(r["delta_pct"] < -1e-12 for r in cc),
            "losses": sum(r["delta_pct"] > 1e-12 for r in cc),
            "ties": sum(abs(r["delta_pct"]) <= 1e-12 for r in cc),
            "median_delta_pct": statistics.median(
                [r["delta_pct"] for r in cc]) if cc else None,
            "phase2_calls_off": sum(r["off_phase2"][0] for r in rows),
            "phase2_success_off": sum(r["off_phase2"][1] for r in rows),
            "phase2_calls_on": sum(r["on_phase2"][0] for r in rows),
            "phase2_success_on": sum(r["on_phase2"][1] for r in rows),
        }
    verdict = ("dormant" if not changed else
               "all_changed_strict_loss" if wins == 0 and ties == 0 else
               "reachable_continue")
    report = {
        "schema_version": 1,
        "pairs": pairs,
        "summary": {
            "pairs": len(pairs),
            "changed": len(changed),
            "wins": wins,
            "losses": losses,
            "ties": ties,
            "median_changed_delta_pct": statistics.median(
                [r["delta_pct"] for r in changed]) if changed else None,
            "verdict": verdict,
            "instances": per_instance,
        },
    }
    (args.directory / "summary.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report["summary"], sort_keys=True), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("solver", type=pathlib.Path)
    p_run.add_argument("instance", type=int)
    p_run.add_argument("mode", choices=("off", "on"))
    p_run.add_argument("output", type=pathlib.Path)
    p_run.set_defaults(func=run)
    p_collect = sub.add_parser("collect")
    p_collect.add_argument("directory", type=pathlib.Path)
    p_collect.set_defaults(func=collect)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
