#!/usr/bin/env python3
"""Checker-authoritative beam over additional causal bay decisions.

The first action is frozen by job 74933.  At every additional depth, each beam
parent is reconstructed from its partial lock vector, one previously-free
block is fixed to one bay, and the complete board is rebuilt.  Explicitly
fixing a block to the bay it already occupies is a causal null and must replay
the parent byte-for-byte.
"""
from __future__ import annotations

import argparse
import heapq
import json
import pathlib
import sys
import time


REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
from assignment_transfer_fixed import (  # noqa: E402
    checked_record,
    flat_digest,
    seed_orders,
)


def compact_parent(parent: dict) -> dict:
    return {
        "actions": parent["actions"],
        "raw_objective": parent["raw_objective"],
        "components": parent["components"],
        "flat_sha256": flat_digest(parent["flat"]),
        "checker": parent["checker"],
    }


def run(args) -> None:
    solver = args.solver.resolve()
    sys.path.insert(0, str(solver))
    import myalgorithm as ma

    problem_path = REPO / "data" / "preliminary_test" / f"prob_{args.instance}.json"
    manifest_path = args.manifest / f"prob_{args.instance}.json"
    prob = json.loads(problem_path.read_text())
    manifest = json.loads(manifest_path.read_text())
    if (not manifest.get("interpretation_valid") or
            not manifest.get("same_bay_null", {}).get("pass") or
            not manifest.get("best_action")):
        raise RuntimeError("root manifest has no causal checker-feasible action")
    n = len(prob["blocks"])
    bays = len(prob["bays"])
    order = seed_orders(prob)[manifest["order"]]
    eng, _due, _proc = ma._fresh(prob, 4)

    def construct(locks: list[int]) -> dict | None:
        code = int(eng.reconstruct_ordered(locks, order, False, 7))
        if code != -1:
            return None
        obj = list(eng.objective())
        flat = list(eng.get_solution())
        return {
            "raw_objective": float(obj[3]),
            "components": obj,
            "flat": flat,
        }

    def check_flat(flat: list[int]) -> dict:
        eng.set_solution(flat)
        eng.build_occ(eng.horizon())
        return checked_record(ma, prob, eng, n)

    action = manifest["best_action"]
    root_locks = [-1] * n
    root_locks[int(action["block"])] = int(action["bay"])
    root_built = construct(root_locks)
    if root_built is None:
        raise RuntimeError("frozen root action no longer constructs")
    if (flat_digest(root_built["flat"]) != action["solution_sha256"] or
            abs(root_built["raw_objective"] - float(action["objective"])) > 1e-6):
        raise RuntimeError("frozen root action does not replay exactly")
    root_check = check_flat(root_built["flat"])
    if (not root_check["feasible"] or
            abs(float(root_check["objective"]) -
                float(manifest["best_checker"]["objective"])) > 1e-6):
        raise RuntimeError("frozen root checker result does not replay")
    root = {
        **root_built,
        "locks": root_locks,
        "actions": [{"block": int(action["block"]),
                     "bay": int(action["bay"])}],
        "checker": root_check,
    }

    started = time.monotonic()
    beam = [root]
    depth_reports = []
    total_nulls = 0
    total_null_identical = 0
    serial = 0
    for depth in range(1, args.depth + 1):
        # Max-heap (negative raw objective) retaining the fixed lowest-raw
        # vocabulary across every beam parent.
        raw_heap: list[tuple[float, int, dict]] = []
        built = failed = raw_wins = raw_losses = 0
        depth_nulls = depth_null_identical = 0
        for parent_index, parent in enumerate(beam):
            parent_bays = [int(parent["flat"][7 * i]) for i in range(n)]
            for block in range(n):
                if parent["locks"][block] >= 0:
                    continue
                for bay in range(bays):
                    child_locks = list(parent["locks"])
                    child_locks[block] = bay
                    child = construct(child_locks)
                    if child is None:
                        failed += 1
                        continue
                    built += 1
                    if bay == parent_bays[block]:
                        depth_nulls += 1
                        same = (child["flat"] == parent["flat"] and
                                child["components"] == parent["components"])
                        depth_null_identical += int(same)
                        if not same:
                            raise RuntimeError(
                                f"causal null failed depth={depth} parent={parent_index} "
                                f"block={block}")
                        continue
                    delta = child["raw_objective"] - parent["raw_objective"]
                    raw_wins += int(delta < -1e-9)
                    raw_losses += int(delta > 1e-9)
                    row = {
                        **child,
                        "locks": child_locks,
                        "actions": parent["actions"] + [
                            {"block": block, "bay": bay}],
                        "parent_index": parent_index,
                        "parent_checker_objective": float(
                            parent["checker"]["objective"]),
                        "raw_delta_from_parent_pct": (
                            100.0 * delta / parent["raw_objective"]),
                    }
                    serial += 1
                    entry = (-child["raw_objective"], serial, row)
                    if len(raw_heap) < args.top:
                        heapq.heappush(raw_heap, entry)
                    elif child["raw_objective"] < -raw_heap[0][0]:
                        heapq.heapreplace(raw_heap, entry)

        total_nulls += depth_nulls
        total_null_identical += depth_null_identical
        if depth_nulls == 0 or depth_null_identical != depth_nulls:
            raise RuntimeError(f"incomplete causal-null coverage at depth {depth}")

        top_raw = [entry[2] for entry in raw_heap]
        top_raw.sort(key=lambda row: row["raw_objective"])
        feasible_children = []
        rejected = 0
        for child in top_raw:
            replay = construct(child["locks"])
            if (replay is None or replay["flat"] != child["flat"] or
                    replay["components"] != child["components"]):
                raise RuntimeError("top-raw child did not replay exactly")
            checked = check_flat(child["flat"])
            child["checker"] = checked
            if not checked["feasible"]:
                rejected += 1
                continue
            # Debt-free rule: an action must improve the official objective of
            # its own parent immediately.  A worse step cannot borrow a hoped-
            # for gain from a later public-board-tuned action.
            if (float(checked["objective"]) <
                    child["parent_checker_objective"] - 1e-9):
                feasible_children.append(child)

        pool = beam + feasible_children
        pool.sort(key=lambda parent: float(parent["checker"]["objective"]))
        next_beam = []
        seen_locks = set()
        for parent in pool:
            key = tuple(parent["locks"])
            if key in seen_locks:
                continue
            seen_locks.add(key)
            next_beam.append(parent)
            if len(next_beam) >= args.beam:
                break
        depth_reports.append({
            "depth": depth,
            "parents": len(beam),
            "built": built,
            "failed": failed,
            "raw_wins": raw_wins,
            "raw_losses": raw_losses,
            "null_tested": depth_nulls,
            "null_byte_identical": depth_null_identical,
            "checker_screened": len(top_raw),
            "checker_rejected": rejected,
            "strict_improving_children": len(feasible_children),
            "beam": [compact_parent(parent) for parent in next_beam],
        })
        beam = next_beam
        if not feasible_children:
            break

    best = min(beam, key=lambda parent: float(parent["checker"]["objective"]))
    one_step = float(root["checker"]["objective"])
    free = float(manifest["baseline"]["checker"]["objective"])
    final = float(best["checker"]["objective"])
    report = {
        "schema_version": 1,
        "instance": args.instance,
        "problem": str(problem_path),
        "solver": str(solver),
        "manifest": str(manifest_path.resolve()),
        "order": manifest["order"],
        "beam_width": args.beam,
        "requested_additional_depth": args.depth,
        "top_raw_vocabulary": args.top,
        "root_free_checker_objective": free,
        "one_step": compact_parent(root),
        "depths": depth_reports,
        "final": compact_parent(best),
        "additional_delta_pct": 100.0 * (final - one_step) / one_step,
        "cumulative_delta_pct": 100.0 * (final - free) / free,
        "same_bay_null": {
            "tested": total_nulls,
            "byte_identical": total_null_identical,
            "pass": total_nulls > 0 and total_nulls == total_null_identical,
        },
        "wall_seconds": time.monotonic() - started,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "instance": args.instance,
        "actions": len(best["actions"]),
        "one_step": one_step,
        "final": final,
        "additional_delta_pct": report["additional_delta_pct"],
        "cumulative_delta_pct": report["cumulative_delta_pct"],
        "null": f"{total_null_identical}/{total_nulls}",
        "wall_seconds": report["wall_seconds"],
    }, sort_keys=True), flush=True)


def collect(args) -> None:
    rows = []
    for instance in (3, 4, 5, 6):
        report = json.loads((args.directory / f"prob_{instance}.json").read_text())
        final_check = report["final"]["checker"]
        rows.append({
            "instance": instance,
            "actions": len(report["final"]["actions"]),
            "checker_feasible": bool(final_check["feasible"]),
            "null_pass": bool(report["same_bay_null"]["pass"]),
            "additional_delta_pct": float(report["additional_delta_pct"]),
            "cumulative_delta_pct": float(report["cumulative_delta_pct"]),
            "wall_seconds": float(report["wall_seconds"]),
        })
    all_valid = all(r["checker_feasible"] and r["null_pass"] for r in rows)
    no_regression = all(r["additional_delta_pct"] <= 1e-12 for r in rows)
    target_edge = any(
        r["instance"] in (4, 5) and r["additional_delta_pct"] <= -5.0
        for r in rows)
    passed = all_valid and no_regression and target_edge
    summary = {
        "rows": rows,
        "all_checker_and_null_valid": all_valid,
        "no_board_regression": no_regression,
        "p4_or_p5_additional_five_pct": target_edge,
        "verdict": "advance_plateau_test" if passed else "reject_sequential_beam",
    }
    (args.directory / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("solver", type=pathlib.Path)
    p_run.add_argument("manifest", type=pathlib.Path)
    p_run.add_argument("instance", type=int, choices=(3, 4, 5, 6))
    p_run.add_argument("output", type=pathlib.Path)
    p_run.add_argument("--depth", type=int, default=2)
    p_run.add_argument("--beam", type=int, default=2)
    p_run.add_argument("--top", type=int, default=20)
    p_run.set_defaults(func=run)
    p_collect = sub.add_parser("collect")
    p_collect.add_argument("directory", type=pathlib.Path)
    p_collect.set_defaults(func=collect)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
