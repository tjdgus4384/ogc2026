#!/usr/bin/env python3
"""Bounded fresh-construction beam solver for the shipyard placement problem.

The solver deliberately constructs schedules from the instance alone.  Each bay is
kept temporally serial, which makes every expanded state crane-safe; beam search
then chooses which block, bay, orientation, location, and entry time to use.
"""
import argparse
import copy
import json
import math
import os
import random
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOLVER_DIR = ROOT / "solver"
if str(SOLVER_DIR) not in sys.path:
    sys.path.insert(0, str(SOLVER_DIR))
import utils  # noqa: E402
import myalgorithm  # noqa: E402


BEAM_WIDTH = 3
BRANCH_WIDTH = 2
REGRET_POOL = 24
MAX_ORIENTS = 3
MAX_POSITIONS = 5


def _integer(value, name):
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return int(math.ceil(float(value) - 1e-9))


def _validate_problem(problem):
    if not isinstance(problem, dict):
        raise ValueError("problem JSON must be an object")
    for key in ("blocks", "bays", "weights"):
        if key not in problem:
            raise ValueError(f"problem missing {key}")
    if not isinstance(problem["blocks"], list) or not problem["blocks"]:
        raise ValueError("problem blocks must be a non-empty list")
    if not isinstance(problem["bays"], list) or not problem["bays"]:
        raise ValueError("problem bays must be a non-empty list")
    weights = problem["weights"]
    if not isinstance(weights, dict) or any(k not in weights for k in ("w1", "w2", "w3")):
        raise ValueError("problem weights must contain w1, w2, w3")
    for name, value in weights.items():
        if name in ("w1", "w2", "w3") and (not isinstance(value, (int, float)) or not math.isfinite(value)):
            raise ValueError(f"weight {name} must be finite")
    for bay_no, bay in enumerate(problem["bays"]):
        if not isinstance(bay, dict) or _integer(bay.get("width"), f"bay {bay_no} width") <= 0 or _integer(bay.get("height"), f"bay {bay_no} height") <= 0:
            raise ValueError(f"invalid bay {bay_no}")
    for block_no, block in enumerate(problem["blocks"]):
        if not isinstance(block, dict):
            raise ValueError(f"invalid block {block_no}")
        for key in ("release_time", "due_date", "processing_time", "workload", "bay_preferences", "shape"):
            if key not in block:
                raise ValueError(f"block {block_no} missing {key}")
        if not isinstance(block["shape"], list) or not block["shape"]:
            raise ValueError(f"block {block_no} has no orientations")
        if not isinstance(block["bay_preferences"], list) or len(block["bay_preferences"]) != len(problem["bays"]):
            raise ValueError(f"block {block_no} has invalid bay_preferences")
        for key in ("release_time", "due_date", "processing_time", "workload"):
            if not isinstance(block[key], (int, float)) or not math.isfinite(block[key]):
                raise ValueError(f"block {block_no} {key} must be finite")
        if any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in block["bay_preferences"]):
            raise ValueError(f"block {block_no} has non-finite bay_preferences")
        if _integer(block["processing_time"], f"block {block_no} processing_time") <= 0:
            raise ValueError(f"block {block_no} has non-positive processing_time")


def _bounds(block, orient):
    try:
        vertices = [point for layer in block["shape"][orient]["layers"] for point in layer]
        xs = [float(point[0]) for point in vertices]
        ys = [float(point[1]) for point in vertices]
    except (KeyError, TypeError, IndexError, ValueError) as exc:
        raise ValueError(f"invalid geometry for orientation {orient}") from exc
    if not xs or not ys or not all(math.isfinite(v) for v in xs + ys):
        raise ValueError(f"empty or non-finite geometry for orientation {orient}")
    return min(xs), min(ys), max(xs), max(ys)


def _positions(block, orient, bay):
    min_x, min_y, max_x, max_y = _bounds(block, orient)
    low_x, low_y = int(math.ceil(-min_x - 1e-9)), int(math.ceil(-min_y - 1e-9))
    high_x = int(math.floor(float(bay["width"]) - max_x + 1e-9))
    high_y = int(math.floor(float(bay["height"]) - max_y + 1e-9))
    if high_x < low_x or high_y < low_y:
        return []
    raw = [(low_x, low_y), (high_x, low_y), (low_x, high_y), (high_x, high_y), ((low_x + high_x) // 2, (low_y + high_y) // 2)]
    result = []
    for point in raw:
        if point not in result:
            result.append(point)
    return result[:MAX_POSITIONS]


def _static_order(problem, rng):
    blocks = problem["blocks"]
    areas = []
    for i, block in enumerate(blocks):
        min_area = min(((_bounds(block, oi)[2] - _bounds(block, oi)[0]) * (_bounds(block, oi)[3] - _bounds(block, oi)[1]) for oi in range(len(block["shape"]))), default=0.0)
        slack = float(block["due_date"]) - float(block["release_time"]) - float(block["processing_time"])
        areas.append((-min_area, slack, rng.random(), i))
    return [item[3] for item in sorted(areas)]


def _candidate_options(problem, state, block_id):
    block = problem["blocks"][block_id]
    weights = problem["weights"]
    total_area = sum(float(b["width"]) * float(b["height"]) for b in problem["bays"])
    avg_area = total_area / len(problem["bays"])
    release = _integer(block["release_time"], "release_time")
    duration = _integer(block["processing_time"], "processing_time")
    due = float(block["due_date"])
    pref_max = max(float(x) for x in block["bay_preferences"])
    candidates = []
    for bay_id, bay in enumerate(problem["bays"]):
        entry = max(release, state["free"][bay_id])
        exit_time = entry + duration
        tardiness = max(0.0, exit_time - due)
        projected = state["loads"][:]
        projected[bay_id] += float(block["workload"])
        normalized = [avg_area / (float(b["width"]) * float(b["height"])) * projected[j] for j, b in enumerate(problem["bays"])]
        imbalance = max(normalized) - min(normalized)
        preference = pref_max - float(block["bay_preferences"][bay_id])
        # Capacity is a forward-looking pressure proxy: earlier reservations in
        # smaller bays consume more normalized work; compact placements retain edge room.
        pressure = projected[bay_id] / max(1.0, float(bay["width"]) * float(bay["height"]))
        for orient in range(min(MAX_ORIENTS, len(block["shape"]))):
            for x, y in _positions(block, orient, bay):
                min_x, min_y, max_x, max_y = _bounds(block, orient)
                left = x + min_x
                bottom = y + min_y
                right = float(bay["width"]) - (x + max_x)
                top = float(bay["height"]) - (y + max_y)
                compactness = min(left, bottom, right, top)
                blocking = duration * (1.0 + pressure)
                score = (float(weights["w1"]) * tardiness + float(weights["w2"]) * imbalance + float(weights["w3"]) * preference + 0.05 * pressure + 0.02 * blocking - 0.002 * compactness)
                candidates.append((score, bay_id, orient, x, y, entry, exit_time, tardiness, pressure, compactness))
    return sorted(candidates, key=lambda item: (item[0], item[5], item[1], item[2], item[3], item[4]))


def _expand(problem, initial_order):
    states = [{"remaining": set(initial_order), "free": [0] * len(problem["bays"]), "loads": [0.0] * len(problem["bays"]), "records": {}, "score": 0.0, "provenance": []}]
    while states and states[0]["remaining"]:
        expanded = []
        for state in states:
            pool = [i for i in initial_order if i in state["remaining"]][:REGRET_POOL]
            choice = None
            for block_id in pool:
                options = _candidate_options(problem, state, block_id)
                if not options:
                    continue
                regret = (options[1][0] if len(options) > 1 else options[0][0] + 1.0) - options[0][0]
                key = (-regret, options[0][0], block_id)
                if choice is None or key < choice[0]:
                    choice = (key, block_id, options)
            if choice is None:
                continue
            _, block_id, options = choice
            for option in options[:BRANCH_WIDTH]:
                score, bay_id, orient, x, y, entry, exit_time, _, _, _ = option
                nxt = {"remaining": set(state["remaining"]), "free": state["free"][:], "loads": state["loads"][:], "records": dict(state["records"]), "score": state["score"] + score, "provenance": state["provenance"] + [block_id]}
                nxt["remaining"].remove(block_id)
                nxt["free"][bay_id] = exit_time
                nxt["loads"][bay_id] += float(problem["blocks"][block_id]["workload"])
                nxt["records"][block_id] = (bay_id, x, y, orient, entry, exit_time)
                expanded.append(nxt)
        states = sorted(expanded, key=lambda state: (state["score"], tuple(state["provenance"])))[:BEAM_WIDTH]
    return states


def _solution(records):
    events = {}
    for block_id, (bay_id, x, y, orient, entry, exit_time) in records.items():
        events.setdefault(entry, []).append({"type": "ENTRY", "block_id": block_id, "bay_id": bay_id, "x": int(x), "y": int(y), "orient_idx": orient})
        events.setdefault(exit_time, []).append({"type": "EXIT", "block_id": block_id, "bay_id": bay_id})
    operations = {}
    for moment in sorted(events):
        # Checker Stage 5 requires exits before entries at equal timestamps.
        operations[str(int(moment))] = sorted(events[moment], key=lambda op: 0 if op["type"] == "EXIT" else 1)
    return {"operations": operations}


def _persist_and_check(problem, solution, output_path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=output_path.name + ".", suffix=".tmp", dir=output_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(solution, stream, separators=(",", ":"), allow_nan=False)
            stream.write("\n")
        with open(temporary, encoding="utf-8") as stream:
            reloaded = json.load(stream)
        if reloaded != solution:
            raise RuntimeError("solution persistence mismatch")
        checked = utils.check_feasibility(problem, reloaded)
        if not checked.get("feasible"):
            raise RuntimeError("persisted solution rejected: " + "; ".join(checked.get("violations", [])))
        os.replace(temporary, output_path)
        return checked
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("problem")
    parser.add_argument("output")
    parser.add_argument("receipt")
    parser.add_argument("--seconds", type=float, required=True)
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        raise ValueError("--seconds must be positive")
    started = time.monotonic()
    with open(args.problem, encoding="utf-8") as stream:
        problem = json.load(stream)
    _validate_problem(problem)
    candidates = []
    starts = max(2, min(4, int(args.seconds // 5) + 1))
    for start_no in range(starts):
        order = _static_order(problem, random.Random(args.seed + 1000003 * start_no))
        candidates.extend(_expand(problem, order))
    candidate_count = len(candidates)
    feasible = []
    for state in candidates:
        candidate = _solution(state["records"])
        check = utils.check_feasibility(problem, candidate)
        if check.get("feasible"):
            feasible.append((float(check["objective"]), candidate, check, state))
    fallback_won = False
    if feasible:
        _, selected, checker, state = min(feasible, key=lambda item: item[0])
        provenance = {"method": "fresh_regret_beam", "beam_width": BEAM_WIDTH, "branch_width": BRANCH_WIDTH, "regret_pool": REGRET_POOL, "construction_order": state["provenance"]}
    else:
        selected = myalgorithm._trivial(problem)
        fallback_won = True
        checker = utils.check_feasibility(problem, selected)
        if not checker.get("feasible"):
            raise RuntimeError("checker rejected fail-safe solution")
        provenance = {"method": "solver.myalgorithm._trivial", "reason": "all fresh candidates rejected"}
    persisted = _persist_and_check(problem, selected, Path(args.output))
    if persisted != checker:
        raise RuntimeError("checker result changed after output persistence")
    receipt = {"candidate_count": candidate_count, "feasible_count": len(feasible), "objective": persisted["objective"], "components": {"obj1": persisted["obj1"], "obj2": persisted["obj2"], "obj3": persisted["obj3"]}, "provenance": provenance, "elapsed_seconds": time.monotonic() - started, "fallback_won": fallback_won, "seed": args.seed}
    receipt_path = Path(args.receipt)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    with open(receipt_path, "w", encoding="utf-8") as stream:
        json.dump(receipt, stream, separators=(",", ":"), allow_nan=False)
        stream.write("\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"pipeline_beam_construct: {exc}", file=sys.stderr)
        sys.exit(2)
