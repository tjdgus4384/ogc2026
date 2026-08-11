#!/usr/bin/env python3
"""Exact-event schedule-only screen for a checker-feasible incumbent."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import random
import sys
import tempfile
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "solver"))

try:
    import sscore
    import utils
except Exception as exc:  # fail closed: the native engine and checker are required
    raise SystemExit(f"rapid_schedule: required solver import failed: {exc}")


def _load_json(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field}: expected an integer")
    number = int(value)
    if number != value:
        raise ValueError(f"{field}: expected an integer")
    return number


def _checker(problem: dict[str, Any], board: dict[str, Any]) -> dict[str, Any]:
    result = utils.check_feasibility(problem, board)
    if (not isinstance(result, dict) or result.get("feasible") is not True
            or not isinstance(result.get("objective"), (int, float))
            or not math.isfinite(result["objective"])):
        raise ValueError("official checker rejected candidate")
    for key in ("obj1", "obj2", "obj3"):
        if not isinstance(result.get(key), (int, float)) or not math.isfinite(result[key]):
            raise ValueError("official checker returned malformed objective")
    return result


def _engine(problem: dict[str, Any], horizon: int) -> Any:
    blocks = problem.get("blocks")
    bays = problem.get("bays")
    weights = problem.get("weights")
    if not isinstance(blocks, list) or not blocks or not isinstance(bays, list) or not bays:
        raise ValueError("problem must contain non-empty blocks and bays")
    if not isinstance(weights, dict):
        raise ValueError("problem weights are missing")
    eng = sscore.Engine()
    eng.init_problem(
        len(blocks), len(bays),
        [int(math.ceil(float(b["release_time"]) - 1e-9)) for b in blocks],
        [int(round(float(b["due_date"]))) for b in blocks],
        [max(1, int(math.ceil(float(b["processing_time"]) - 1e-9))) for b in blocks],
        [float(b["workload"]) for b in blocks],
        [[float(v) for v in b["bay_preferences"]] for b in blocks],
        [float(b["width"]) for b in bays], [float(b["height"]) for b in bays],
        float(weights["w1"]), float(weights["w2"]), float(weights["w3"]),
    )
    eng.set_exact_events(True)
    for block_id, block in enumerate(blocks):
        shapes = block.get("shape")
        if not isinstance(shapes, list) or not shapes:
            raise ValueError(f"block {block_id}: missing orientations")
        for shape in shapes:
            layers = [[list(point) for point in layer] for layer in shape["layers"]]
            if not layers or not layers[0] or not layers[0][0]:
                raise ValueError(f"block {block_id}: malformed orientation")
            rx, ry = layers[0][0]
            eng.add_orient(block_id, [[(x - rx, y - ry) for x, y in layer] for layer in layers])
    eng.build_occ(horizon)
    return eng


def _seed_flat(problem: dict[str, Any], board: dict[str, Any]) -> tuple[list[int], int]:
    blocks = problem["blocks"]
    operations = board.get("operations")
    if not isinstance(operations, dict):
        raise ValueError("seed has no operations object")
    records: list[list[int] | None] = [None] * len(blocks)
    exits: list[int | None] = [None] * len(blocks)
    max_time = 0
    for raw_time, rows in operations.items():
        event_time = _integer(int(raw_time), "operation time")
        if event_time < 0 or not isinstance(rows, list):
            raise ValueError("malformed operation bucket")
        max_time = max(max_time, event_time)
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("malformed operation")
            block_id = _integer(row.get("block_id"), "block_id")
            bay_id = _integer(row.get("bay_id"), "bay_id")
            if not 0 <= block_id < len(blocks):
                raise ValueError("operation block_id out of range")
            event_type = row.get("type")
            if event_type == "ENTRY":
                if records[block_id] is not None:
                    raise ValueError("duplicate ENTRY")
                records[block_id] = [bay_id, _integer(row.get("orient_idx"), "orient_idx"),
                                     _integer(row.get("x"), "x"), _integer(row.get("y"), "y"), event_time]
            elif event_type == "EXIT":
                if exits[block_id] is not None:
                    raise ValueError("duplicate EXIT")
                exits[block_id] = event_time
            else:
                raise ValueError("unknown operation type")
    flat: list[int] = []
    for block_id, record in enumerate(records):
        if record is None or exits[block_id] is None:
            raise ValueError(f"block {block_id}: incomplete seed schedule")
        bay, orient, x, y, entry = record
        flat.extend((bay, orient, x, y, entry, exits[block_id], 1))
    return flat, max_time


def _board_from_engine(eng: Any, blocks_count: int) -> dict[str, Any]:
    records: dict[int, list[int]] = {}
    for block_id in range(blocks_count):
        bay, orient, x, y, entry, exit_time, placed = eng.get_block(block_id)
        if not placed:
            raise ValueError("engine dropped a block")
        records[block_id] = [bay, x, y, orient, entry, exit_time]
    rows = eng.ordered_events()
    if not rows:
        raise ValueError("engine could not derive a legal event order")
    operations: dict[str, list[dict[str, int | str]]] = {}
    for event_time, kind, block_id, bay_id in rows:
        row: dict[str, int | str] = {"type": "EXIT" if kind == 0 else "ENTRY",
                                     "block_id": int(block_id), "bay_id": int(bay_id)}
        if kind != 0:
            _, x, y, orient, _, _ = records[int(block_id)]
            row.update(x=int(x), y=int(y), orient_idx=int(orient))
        operations.setdefault(str(int(event_time)), []).append(row)
    return {"operations": operations}


def _bay_orders(eng: Any, bay_count: int) -> list[list[tuple[int, int]]]:
    orders = [[] for _ in range(bay_count)]
    for _, kind, block_id, bay_id in eng.ordered_events():
        orders[int(bay_id)].append((int(kind), int(block_id)))
    if any(not order for order in orders):
        # Empty bays are valid; this condition is intentionally only a shape check.
        return orders
    return orders


def _swap_legal(order: list[tuple[int, int]], index: int) -> bool:
    trial = list(order)
    trial[index], trial[index + 1] = trial[index + 1], trial[index]
    entries: dict[int, int] = {}
    for position, (kind, block_id) in enumerate(trial):
        if kind == 1:
            entries[block_id] = position
        elif block_id not in entries or entries[block_id] >= position:
            return False
    return True


def _reschedule(problem: dict[str, Any], flat: list[int], orders: list[list[tuple[int, int]]]) -> list[int]:
    candidate = list(flat)
    blocks = problem["blocks"]
    for order in orders:
        clock = 0
        entries: dict[int, int] = {}
        for kind, block_id in order:
            offset = block_id * 7
            if kind == 1:
                entry = max(clock, int(math.ceil(float(blocks[block_id]["release_time"]) - 1e-9)))
                candidate[offset + 4] = entry
                entries[block_id] = entry
                clock = entry
            else:
                entry = entries.get(block_id)
                if entry is None:
                    raise ValueError("invalid event-order perturbation")
                duration = max(1, int(math.ceil(float(blocks[block_id]["processing_time"]) - 1e-9)))
                exit_time = max(clock, entry + duration)
                candidate[offset + 5] = exit_time
                clock = exit_time
    return candidate


def _atomic_json(path: str, value: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, separators=(",", ":"))
            stream.write("\n")
        os.replace(temporary, destination)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("problem_json")
    parser.add_argument("seed_sol_json")
    parser.add_argument("out_sol_json")
    parser.add_argument("receipt_json")
    parser.add_argument("--seconds", required=True, type=float)
    parser.add_argument("--seed", required=True, type=int)
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        raise ValueError("--seconds must be finite and positive")
    if Path(args.out_sol_json).resolve() == Path(args.receipt_json).resolve():
        raise ValueError("solution and receipt paths must differ")

    started = time.monotonic()
    deadline = started + args.seconds
    problem = _load_json(args.problem_json)
    seed_board = _load_json(args.seed_sol_json)
    seed_check = _checker(problem, seed_board)  # checker authority precedes engine use
    flat, max_time = _seed_flat(problem, seed_board)
    blocks = problem["blocks"]
    durations = [max(1, int(math.ceil(float(b["processing_time"]) - 1e-9))) for b in blocks]
    eng = _engine(problem, max(max_time + sum(durations) + 2, 2))
    eng.set_solution(flat)

    best_board = seed_board
    best_check = seed_check
    best_stage = "seed"
    attempted = 0
    replayed = 1
    exact_loaded = bool(eng.exact_state_ok())
    rng = random.Random(args.seed)

    def consider_current(stage: str) -> None:
        nonlocal best_board, best_check, best_stage, replayed, flat
        board = _board_from_engine(eng, len(blocks))
        replayed += 1
        check = _checker(problem, board)
        if check["objective"] < best_check["objective"] - 1e-9:
            best_board, best_check, best_stage = board, check, stage
            flat = list(eng.get_solution())

    # Compaction is safe only in exact-event mode and retains geometry/event order.
    if exact_loaded and time.monotonic() < deadline:
        eng.compact_event_schedule()
        if eng.exact_state_ok():
            consider_current("compact")
        eng.set_solution(flat)

    attempt_cap = max(8, min(512, int(args.seconds * 16)))
    bay_count = len(problem["bays"])
    while attempted < attempt_cap and time.monotonic() < deadline and exact_loaded:
        eng.set_solution(flat)
        orders = _bay_orders(eng, bay_count)
        choices = [(bay, index) for bay, order in enumerate(orders)
                   for index in range(len(order) - 1) if _swap_legal(order, index)]
        if not choices:
            break
        # A bounded sequence of local exchanges creates genuinely distinct bay orders.
        for _ in range(1 + rng.randrange(3)):
            legal = [(bay, index) for bay, order in enumerate(orders)
                     for index in range(len(order) - 1) if _swap_legal(order, index)]
            if not legal:
                break
            bay, index = rng.choice(legal)
            orders[bay][index], orders[bay][index + 1] = orders[bay][index + 1], orders[bay][index]
        attempted += 1
        candidate = _reschedule(problem, flat, orders)
        eng.set_solution(candidate)
        if not eng.exact_state_ok():
            continue
        eng.compact_event_schedule()
        if not eng.exact_state_ok():
            continue
        try:
            consider_current("order-swap")
        except ValueError:
            # Candidate never becomes a survivor without the checker gate.
            continue

    elapsed = time.monotonic() - started
    receipt = {
        "tool": "rapid_schedule",
        "seed": args.seed,
        "seconds_requested": args.seconds,
        "seconds_elapsed": elapsed,
        "exact_engine_loaded": exact_loaded,
        "attempted_order_count": attempted,
        "official_checker_replays": replayed,
        "seed_objective": seed_check["objective"],
        "best_objective": best_check["objective"],
        "best_stage": best_stage,
        "seed_z1": seed_check["obj1"], "seed_z2": seed_check["obj2"], "seed_z3": seed_check["obj3"],
        "best_z1": best_check["obj1"], "best_z2": best_check["obj2"], "best_z3": best_check["obj3"],
        "z1_delta": best_check["obj1"] - seed_check["obj1"],
        "z2_delta": best_check["obj2"] - seed_check["obj2"],
        "z3_delta": best_check["obj3"] - seed_check["obj3"],
        "feasible": True,
    }
    _atomic_json(args.out_sol_json, best_board)
    with open(args.out_sol_json, encoding="utf-8") as stream:
        persisted_board = json.load(stream)
    persisted_check = _checker(problem, persisted_board)
    if abs(float(persisted_check["objective"]) - float(best_check["objective"])) > 1e-9:
        Path(args.out_sol_json).unlink(missing_ok=True)
        raise ValueError("persisted schedule objective differs from the checker-gated candidate")
    _atomic_json(args.receipt_json, receipt)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"rapid_schedule: {exc}", file=sys.stderr)
        raise SystemExit(2)
