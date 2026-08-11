#!/usr/bin/env python3
"""CLI boundary for the isolated from-scratch constructor extension."""
import argparse
import importlib
import json
import os
import time
from pathlib import Path
from typing import Any


FORBIDDEN_FIELDS = frozenset(("seed", "initial_solution", "incumbent", "solution", "prefix"))


def reject_artifacts(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key.lower() in FORBIDDEN_FIELDS and child not in (None, "", [], {}):
                raise ValueError(f"forbidden inherited artifact at {child_path}")
            reject_artifacts(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            reject_artifacts(child, f"{path}[{index}]")


def read_object(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    reject_artifacts(value)
    return value


def construct_solution(raw: dict[str, Any], deadline_ns: int,
                       mechanism_enabled: bool = True) -> dict[str, Any]:
    reject_artifacts(raw)
    if deadline_ns <= time.monotonic_ns():
        raise ValueError("deadline_ns must be a future monotonic timestamp")
    extension = importlib.import_module("fromscratch_api")
    construct = getattr(extension, "construct", None)
    if not callable(construct):
        raise RuntimeError("no lane-specific construct is linked; C1 cannot emit a solution")
    payload = construct(raw, deadline_ns, mechanism_enabled)
    if not isinstance(payload, dict) or set(payload) != {"operations", "objective", "telemetry"}:
        raise RuntimeError("experimental extension violated complete output contract")
    operations = payload["operations"]
    if not isinstance(operations, dict):
        raise RuntimeError("experimental extension emitted malformed operations")
    operation_count = sum(len(at_time) for at_time in operations.values()
                          if isinstance(at_time, list))
    if operation_count != 2 * len(raw.get("blocks", ())):
        raise RuntimeError("experimental extension did not account for every block")
    return {"operations": operations}


def algorithm(raw: dict[str, Any], timelimit: float) -> dict[str, Any]:
    if isinstance(timelimit, bool) or not isinstance(timelimit, (int, float)) or timelimit <= 0:
        raise ValueError("timelimit must be positive")
    mechanism = os.environ.get("OGC_FROMSCRATCH_MECHANISM", "1")
    if mechanism not in {"0", "1"}:
        raise ValueError("OGC_FROMSCRATCH_MECHANISM must be 0 or 1")
    usable_seconds = max(0.001, float(timelimit) - 0.5)
    deadline_ns = time.monotonic_ns() + int(usable_seconds * 1_000_000_000)
    return construct_solution(raw, deadline_ns, mechanism == "1")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--deadline-ns", required=True, type=int)
    args = parser.parse_args()
    raw = read_object(args.input)
    config = read_object(args.config)
    if config:
        raise ValueError("from-scratch config must be empty; treatment starts raw and empty")
    output = construct_solution(raw, args.deadline_ns)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(output, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
    os.replace(temporary, args.output)


if __name__ == "__main__":
    main()
