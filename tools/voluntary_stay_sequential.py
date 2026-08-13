#!/usr/bin/env python3
"""Checker-authoritative beam over additional voluntary stay decisions."""
from __future__ import annotations

import argparse
import heapq
import json
from pathlib import Path
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from voluntary_stay_rollout import _checker_record, _flat_hash, _orders  # noqa: E402


def _compact(parent: dict[str, Any]) -> dict[str, Any]:
    return {
        "actions": parent["actions"],
        "exit_targets": parent["targets"],
        "raw_objective": parent["raw_objective"],
        "components": parent["components"],
        "flat_sha256": _flat_hash(parent["flat"]),
        "checker": parent["checker"],
    }


def _run(args: argparse.Namespace) -> None:
    solver = args.solver.resolve()
    sys.path.insert(0, str(solver))
    import myalgorithm as ma

    problem_path = ROOT / "data" / "preliminary_test" / f"prob_{args.instance}.json"
    manifest_path = args.manifest / f"prob_{args.instance}.json"
    problem = json.loads(problem_path.read_text())
    manifest = json.loads(manifest_path.read_text())
    if not manifest.get("interpretation_valid") or not manifest.get("best_action"):
        raise RuntimeError("one-decision manifest is not an eligible causal root")

    n = len(problem["blocks"])
    order = _orders(problem)[manifest["order"]]
    engine, due, _proc = ma._fresh(problem, 4)
    free = [-1] * n

    def construct(targets: list[int]) -> dict[str, Any] | None:
        code = int(engine.reconstruct_ordered_stays(
            free, order, targets, False, 7))
        if code != -1:
            return None
        objective = list(engine.objective())
        return {
            "raw_objective": float(objective[3]),
            "components": objective,
            "flat": list(engine.get_solution()),
        }

    def check_flat(flat: list[int]) -> dict[str, Any]:
        return _checker_record(ma, engine, problem, flat)

    root_action = manifest["best_action"]
    root_targets = [-1] * n
    root_block = int(root_action["block"])
    root_target = int(root_action["target_exit"])
    root_targets[root_block] = root_target
    root_built = construct(root_targets)
    if root_built is None:
        raise RuntimeError("frozen one-decision root no longer constructs")
    if (_flat_hash(root_built["flat"]) != root_action["solution_sha256"] or
            abs(root_built["raw_objective"] -
                float(root_action["objective"])) > 1e-9):
        raise RuntimeError("frozen one-decision root does not replay exactly")
    root_check = check_flat(root_built["flat"])
    expected_check = root_action["checker"]
    if (not root_check["feasible"] or root_check["rescued"] or
            abs(float(root_check["objective"]) -
                float(expected_check["objective"])) > 1e-9):
        raise RuntimeError("frozen one-decision checker result does not replay")
    root = {
        **root_built,
        "targets": root_targets,
        "actions": [{
            "block": root_block,
            "base_exit": int(root_action["base_exit"]),
            "target_exit": root_target,
            "fraction": float(root_action["fraction"]),
        }],
        "checker": root_check,
    }

    started = time.monotonic()
    beam = [root]
    expanded_parents: set[tuple[int, ...]] = set()
    evaluated_children: set[tuple[int, ...]] = {tuple(root_targets)}
    depth_reports = []
    total_nulls = 0
    total_null_exact = 0
    serial = 0

    for depth in range(1, args.depth + 1):
        frontier = [parent for parent in beam
                    if tuple(parent["targets"]) not in expanded_parents]
        if not frontier:
            break
        raw_heap: list[tuple[float, int, dict[str, Any]]] = []
        built_count = failed = raw_wins = raw_losses = 0
        depth_nulls = depth_null_exact = 0

        for parent_index, parent in enumerate(frontier):
            expanded_parents.add(tuple(parent["targets"]))
            current_exit = [int(parent["flat"][7 * block + 5])
                            for block in range(n)]
            for block in range(n):
                if parent["targets"][block] >= 0:
                    continue

                null_targets = list(parent["targets"])
                null_targets[block] = current_exit[block]
                null = construct(null_targets)
                depth_nulls += 1
                if (null is None or null["flat"] != parent["flat"] or
                        null["components"] != parent["components"]):
                    raise RuntimeError(
                        f"stay null failed depth={depth} parent={parent_index} "
                        f"block={block}")
                depth_null_exact += 1

                slack = due[block] - current_exit[block]
                if slack <= 0:
                    continue
                target_values = {
                    due[block],
                    current_exit[block] + max(1, slack // 2),
                }
                for target in sorted(target_values):
                    child_targets = list(parent["targets"])
                    child_targets[block] = target
                    target_key = tuple(child_targets)
                    if target_key in evaluated_children:
                        continue
                    evaluated_children.add(target_key)
                    child = construct(child_targets)
                    if child is None:
                        failed += 1
                        continue
                    built_count += 1
                    if int(child["flat"][7 * block + 5]) != target:
                        raise RuntimeError("child did not retain its exit target")
                    delta = child["raw_objective"] - parent["raw_objective"]
                    raw_wins += int(delta < -1e-9)
                    raw_losses += int(delta > 1e-9)
                    row = {
                        **child,
                        "targets": child_targets,
                        "actions": parent["actions"] + [{
                            "block": block,
                            "base_exit": current_exit[block],
                            "target_exit": target,
                            "fraction": (target - current_exit[block]) / slack,
                        }],
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
        total_null_exact += depth_null_exact
        if depth_nulls == 0 or depth_nulls != depth_null_exact:
            raise RuntimeError(f"incomplete stay-null coverage at depth {depth}")

        top_raw = [entry[2] for entry in raw_heap]
        top_raw.sort(key=lambda child: child["raw_objective"])
        feasible_children = []
        checker_rejected = checker_rescued = 0
        for child in top_raw:
            replay = construct(child["targets"])
            if (replay is None or replay["flat"] != child["flat"] or
                    replay["components"] != child["components"]):
                raise RuntimeError("top-raw child did not replay exactly")
            checked = check_flat(child["flat"])
            child["checker"] = checked
            if not checked["feasible"]:
                checker_rejected += 1
                continue
            # A rescued board is not reproduced by the target vector alone and
            # therefore cannot be a causal parent at the next depth.
            if checked["rescued"]:
                checker_rescued += 1
                continue
            if (abs(float(checked["objective"]) -
                    child["raw_objective"]) > 1e-9):
                raise RuntimeError("raw/checker objective mismatch")
            if (float(checked["objective"]) <
                    child["parent_checker_objective"] - 1e-9):
                feasible_children.append(child)

        pool = beam + feasible_children
        pool.sort(key=lambda parent: float(parent["checker"]["objective"]))
        next_beam = []
        seen_targets = set()
        for parent in pool:
            key = tuple(parent["targets"])
            if key in seen_targets:
                continue
            seen_targets.add(key)
            next_beam.append(parent)
            if len(next_beam) >= args.beam:
                break

        depth_reports.append({
            "depth": depth,
            "frontier_parents": len(frontier),
            "built": built_count,
            "failed": failed,
            "raw_wins": raw_wins,
            "raw_losses": raw_losses,
            "null_tested": depth_nulls,
            "null_byte_identical": depth_null_exact,
            "checker_screened": len(top_raw),
            "checker_rejected": checker_rejected,
            "checker_rescued_not_parented": checker_rescued,
            "strict_improving_children": len(feasible_children),
            "beam": [_compact(parent) for parent in next_beam],
        })
        beam = next_beam
        if not feasible_children:
            break

    best = min(beam, key=lambda parent: float(parent["checker"]["objective"]))
    free_objective = float(manifest["baseline"]["checker"]["objective"])
    one_objective = float(root["checker"]["objective"])
    final_objective = float(best["checker"]["objective"])
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
        "root_free_checker_objective": free_objective,
        "one_step": _compact(root),
        "depths": depth_reports,
        "final": _compact(best),
        "additional_delta_pct": (
            100.0 * (final_objective - one_objective) / one_objective),
        "cumulative_delta_pct": (
            100.0 * (final_objective - free_objective) / free_objective),
        "same_exit_null": {
            "tested": total_nulls,
            "byte_identical": total_null_exact,
            "pass": total_nulls > 0 and total_nulls == total_null_exact,
        },
        "wall_seconds": time.monotonic() - started,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "instance": args.instance,
        "actions": len(best["actions"]),
        "one_step": one_objective,
        "final": final_objective,
        "additional_delta_pct": report["additional_delta_pct"],
        "cumulative_delta_pct": report["cumulative_delta_pct"],
        "null": f"{total_null_exact}/{total_nulls}",
        "wall_seconds": report["wall_seconds"],
    }, sort_keys=True), flush=True)


def _collect(args: argparse.Namespace) -> None:
    rows = []
    for instance in range(3, 7):
        report = json.loads((args.directory / f"prob_{instance}.json").read_text())
        final = report["final"]
        rows.append({
            "instance": instance,
            "actions": len(final["actions"]),
            "checker_feasible": bool(final["checker"]["feasible"]),
            "null_pass": bool(report["same_exit_null"]["pass"]),
            "additional_delta_pct": float(report["additional_delta_pct"]),
            "cumulative_delta_pct": float(report["cumulative_delta_pct"]),
            "wall_seconds": float(report["wall_seconds"]),
        })
    all_valid = all(row["checker_feasible"] and row["null_pass"] for row in rows)
    no_regression = all(row["additional_delta_pct"] <= 1e-12 for row in rows)
    additional_edge = any(
        row["instance"] in (4, 5) and row["additional_delta_pct"] <= -5.0
        for row in rows)
    summary = {
        "schema_version": 1,
        "rows": rows,
        "all_checker_and_null_valid": all_valid,
        "no_board_regression": no_regression,
        "p4_or_p5_additional_five_pct": additional_edge,
        "verdict": ("advance_stay_plateau_test" if all_valid and no_regression
                    else "reject_sequential_stay_construction"),
    }
    (args.directory / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("solver", type=Path)
    run.add_argument("manifest", type=Path)
    run.add_argument("instance", type=int, choices=range(3, 7))
    run.add_argument("output", type=Path)
    run.add_argument("--depth", type=int, default=2)
    run.add_argument("--beam", type=int, default=2)
    run.add_argument("--top", type=int, default=20)
    collect = sub.add_parser("collect")
    collect.add_argument("directory", type=Path)
    args = parser.parse_args()
    if args.command == "run":
        _run(args)
    else:
        _collect(args)


if __name__ == "__main__":
    main()
