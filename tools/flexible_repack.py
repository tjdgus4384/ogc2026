#!/usr/bin/env python3
"""Deterministic fixed-vs-flexible repack kill probe.

This is deliberately an experiment harness, not a production repair.  Both
arms share one immutable free set, one placement vocabulary, one set of exact
geometry relations, one integer objective encoding, one seed, and the same
single-worker wall limit.  The only model difference is

    fixed:  exit_i == entry_i + processing_i
    flex:   exit_i >= entry_i + processing_i

Every block has its incumbent geometry/entry placement in the vocabulary.
Non-free blocks have no other geometry/entry choice, but their exits remain
variables in the flex arm.  An already-overlong checker-feasible incumbent is
accepted: it embeds exactly in flex, while fixed remains the intended
``exit=entry+processing`` restriction on the same placement choices.  Thus an
OPTIMAL flex tie is a negative certificate only over the reported, capped
vocabulary; FEASIBLE without a checked gain is unresolved.

The free set is the whole-board top ``min(24, ceil(sqrt(n)))`` blocks by
weighted personal cost.  Every free block gathers event-corner alternatives
from every bay, sorts them by immediate personal cost and then the raw
placement tuple, and retains exactly up to ``m*ceil(sqrt(n))`` alternatives in
addition to its incumbent.  Full objective is primary; ``sum(exit)`` is the
only deterministic secondary objective.

Required experimental Engine APIs
---------------------------------

``event_corner_candidates(block, bay, entry, cap)``
    The current Engine state is the complete immutable incumbent.  The API
    must internally exclude only the queried block A while retaining every
    other incumbent (including other free blocks) as a resident.  Return flat
    rows ``[bay, ori, x, y, entry]`` (or a ``{"rows": ..., "truncated": bool,
    "tested": int}`` mapping).  Rows must be deterministic, contained in the
    bay, and free of same-layer resting collision.  They must be raw
    ``cornerSetsRB`` candidates: do *not* call ``vpConservative`` or
    ``vpFlexibleExits`` and do not reject a crane-path obstruction, because
    the CP relation model decides disjointness/nesting.

``pair_relations(flat)``
    ``flat`` is ``[block, bay, ori, x, y, entry] * N``.  Return triples
    ``[candidate_a, candidate_b, relation]`` (flat or nested rows), where
    relation 1 is hard-disjoint, 2 means candidate A is the outer/wrapper,
    and 3 means candidate B is the outer/wrapper.  CLEAR pairs are omitted.
    The result must be geometry-only: no current-time or minimum-duration
    overlap filter is permitted.

The shipped ``feasible_positions`` API is intentionally not a fallback.  It
keeps only an earliest day per bay and filters through the fixed-duration
oracle, which would remove the very C5-prime candidates this probe measures.
All CP leaves go through a fresh Engine flexible audit and the raw official
checker; there is no rescue or checker no-good loop.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import platform
import sys
import time
from typing import Any, Iterable, Sequence

from ortools.sat.python import cp_model


ROOT = Path(__file__).resolve().parents[1]
SAFE_INT = (1 << 62) - 1
SCHEMA_VERSION = 1


class ProbeError(RuntimeError):
    """A fail-closed probe/configuration error."""


class ApiContractError(ProbeError):
    """The experimental solver lacks an exact API required by the probe."""


@dataclass(frozen=True, order=True)
class Candidate:
    block: int
    bay: int
    ori: int
    x: int
    y: int
    entry: int

    def relation_row(self) -> list[int]:
        return [self.block, self.bay, self.ori, self.x, self.y, self.entry]


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _fraction(value: Any, label: str) -> Fraction:
    if isinstance(value, bool):
        raise ProbeError(f"{label} must be numeric, not bool")
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ProbeError(f"{label} is not an exact finite decimal: {value!r}") from exc
    if not dec.is_finite():
        raise ProbeError(f"{label} is not finite: {value!r}")
    return Fraction(dec)


def _ceil(value: Fraction) -> int:
    return -((-value.numerator) // value.denominator)


def _floor(value: Fraction) -> int:
    return value.numerator // value.denominator


def _lcm(values: Iterable[int]) -> int:
    result = 1
    for value in values:
        if value <= 0:
            raise ProbeError(f"invalid denominator {value}")
        result = math.lcm(result, value)
        if result > SAFE_INT:
            raise ProbeError("exact rational LCM exceeds the CP-SAT int64 budget")
    return result


def _safe(value: int, label: str) -> int:
    if abs(int(value)) > SAFE_INT:
        raise ProbeError(f"{label} exceeds the fail-closed int64 budget: {value}")
    return int(value)


def _exact_int(value: Any, label: str) -> int:
    frac = _fraction(value, label)
    if frac.denominator != 1:
        raise ProbeError(f"{label} must be an integer, got {value!r}")
    return _safe(frac.numerator, label)


def _fraction_text(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else (
        f"{value.numerator}/{value.denominator}"
    )


def _load_solver(solver_dir: Path):
    solver_dir = solver_dir.resolve()
    sys.path.insert(0, str(solver_dir))
    import myalgorithm as ma  # type: ignore

    if ma.sscore is None:
        raise ApiContractError(f"sscore did not import from {solver_dir}")
    module_path = Path(ma.sscore.__file__).resolve()
    if module_path.parent != solver_dir:
        raise ApiContractError(
            f"sscore imported from {module_path}, not requested {solver_dir}"
        )
    if ma.utils is None:
        raise ApiContractError("raw official checker (solver/utils.py) is unavailable")
    return ma, module_path


def _new_engine(ma, problem: dict[str, Any], horizon: int):
    engine = ma._build_engine(problem)
    if engine is None:
        raise ApiContractError("Engine construction failed")
    engine.build_occ(int(horizon))
    return engine


def _timing(problem: dict[str, Any]) -> tuple[list[int], list[int], list[Fraction]]:
    release: list[int] = []
    processing: list[int] = []
    due: list[Fraction] = []
    for i, block in enumerate(problem["blocks"]):
        release.append(_ceil(_fraction(block["release_time"], f"release[{i}]")))
        processing.append(max(
            1, _ceil(_fraction(block["processing_time"], f"processing[{i}]"))
        ))
        due.append(_fraction(block["due_date"], f"due[{i}]"))
    return release, processing, due


def _read_flat(raw: Any, n: int) -> tuple[list[int], dict[str, Any]]:
    metadata: dict[str, Any] = {}
    if isinstance(raw, dict):
        metadata = {key: value for key, value in raw.items() if key != "flat"}
        raw = raw.get("flat")
    if not isinstance(raw, list) or len(raw) != 7 * n:
        got = len(raw) if isinstance(raw, list) else type(raw).__name__
        raise ProbeError(f"plateau flat must have length {7 * n}, got {got}")
    flat = [_exact_int(value, f"flat[{index}]") for index, value in enumerate(raw)]
    for i in range(n):
        if flat[7 * i + 6] != 1:
            raise ProbeError(
                f"plateau must be a complete board; block {i} placed="
                f"{flat[7 * i + 6]}"
            )
    return flat, metadata


def _problem_numbers(problem: dict[str, Any]) -> dict[str, Any]:
    blocks = problem["blocks"]
    bays = problem["bays"]
    n, m = len(blocks), len(bays)
    if n <= 0 or m <= 0:
        raise ProbeError("problem must contain at least one block and one bay")

    weights = {
        name: _fraction(problem.get("weights", {}).get(name, 1), name)
        for name in ("w1", "w2", "w3")
    }
    if any(value < 0 for value in weights.values()):
        raise ProbeError("negative objective weights are unsupported")

    workload = [
        _fraction(block["workload"], f"workload[{i}]")
        for i, block in enumerate(blocks)
    ]
    if any(value < 0 for value in workload):
        raise ProbeError("negative workload is unsupported")

    preference: list[list[Fraction]] = []
    for i, block in enumerate(blocks):
        row = [
            _fraction(value, f"preference[{i}][{j}]")
            for j, value in enumerate(block["bay_preferences"])
        ]
        if len(row) != m:
            raise ProbeError(
                f"block {i} has {len(row)} preferences for {m} bays"
            )
        preference.append(row)

    area: list[Fraction] = []
    for j, bay in enumerate(bays):
        value = (
            _fraction(bay["width"], f"bay[{j}].width")
            * _fraction(bay["height"], f"bay[{j}].height")
        )
        if value <= 0:
            raise ProbeError(f"bay {j} has non-positive area")
        area.append(value)

    release, processing, due = _timing(problem)
    return {
        "n": n,
        "m": m,
        "weights": weights,
        "workload": workload,
        "preference": preference,
        "smax": [max(row) for row in preference],
        "area": area,
        "release": release,
        "processing": processing,
        "due": due,
    }


def _exact_objective(
    numbers: dict[str, Any], flat: Sequence[int]
) -> dict[str, Fraction]:
    n, m = numbers["n"], numbers["m"]
    z1 = Fraction(0)
    z3 = Fraction(0)
    load = [Fraction(0) for _ in range(m)]
    for i in range(n):
        bay = int(flat[7 * i])
        if not 0 <= bay < m or int(flat[7 * i + 6]) != 1:
            raise ProbeError(f"invalid exact-objective placement for block {i}")
        exit_time = Fraction(int(flat[7 * i + 5]))
        z1 += max(Fraction(0), exit_time - numbers["due"][i])
        load[bay] += numbers["workload"][i]
        z3 += numbers["smax"][i] - numbers["preference"][i][bay]

    avg_area = sum(numbers["area"], Fraction(0)) / m
    normalized = [avg_area * load[j] / numbers["area"][j] for j in range(m)]
    z2 = Fraction(0) if m == 1 else Fraction(
        _floor(max(normalized) - min(normalized))
    )
    weights = numbers["weights"]
    total = weights["w1"] * z1 + weights["w2"] * z2 + weights["w3"] * z3
    return {"z1": z1, "z2": z2, "z3": z3, "total": total}


def _objective_json(value: dict[str, Fraction]) -> dict[str, Any]:
    return {
        key: {"exact": _fraction_text(frac), "float": float(frac)}
        for key, frac in value.items()
    }


def _inspect_flat(
    ma,
    problem: dict[str, Any],
    numbers: dict[str, Any],
    flat: Sequence[int],
    horizon: int,
    canonicalize: bool,
) -> dict[str, Any]:
    candidate = [int(value) for value in flat]
    engine = _new_engine(ma, problem, horizon)
    engine.set_solution(candidate)
    engine.build_occ(horizon)
    canonical_used = False

    # Optional future API.  It mutates the current complete state to the
    # componentwise minimum legal exits and returns bool/None; get_solution is
    # the authority.  If present but incompatible, fail rather than downgrade.
    closure_api = getattr(engine, "canonical_minimum_exit_closure", None)
    if canonicalize and callable(closure_api):
        result = closure_api()
        if result is False:
            raise ProbeError("canonical_minimum_exit_closure rejected a CP leaf")
        candidate = [int(value) for value in engine.get_solution()]
        if len(candidate) != 7 * numbers["n"]:
            raise ApiContractError(
                "canonical_minimum_exit_closure produced a malformed solution"
            )
        engine.build_occ(horizon)
        canonical_used = True

    entry_api = getattr(engine, "entry_ranks", None)
    exit_api = getattr(engine, "exit_ranks", None)
    if not callable(entry_api) or not callable(exit_api):
        raise ApiContractError(
            "Engine.entry_ranks() and Engine.exit_ranks() are required for "
            "raw same-day operation emission"
        )
    try:
        # Both duration arms are judged with the official flexible-exit oracle.
        # ``fixed`` describes only the CP equality f=e+P; it must not silently
        # switch validation back to the historical conservative oracle.
        audit = [int(round(value)) for value in engine.audit_state(True)]
    except TypeError as exc:
        raise ApiContractError(
            "Engine.audit_state(bool flexibleExits) is required"
        ) from exc

    entry_ranks: list[int] = []
    exit_ranks: list[int] = []
    checker: dict[str, Any] | None = None
    raw_solution: dict[str, Any] | None = None
    validation_error: str | None = None
    try:
        entry_ranks = [int(value) for value in entry_api()]
        exit_ranks = [int(value) for value in exit_api()]
        if len(entry_ranks) != numbers["n"] or len(exit_ranks) != numbers["n"]:
            raise ProbeError("entry/exit rank API returned the wrong length")
        if any(value < 0 for value in entry_ranks + exit_ranks):
            raise ProbeError("entry/exit rank API did not find a complete order")
        operations: dict[str, list[dict[str, Any]]] = {}
        entries: dict[int, list[int]] = {}
        exits: dict[int, list[int]] = {}
        for i in range(numbers["n"]):
            entries.setdefault(candidate[7 * i + 4], []).append(i)
            exits.setdefault(candidate[7 * i + 5], []).append(i)
        for event_time in sorted(set(entries) | set(exits)):
            event_ops: list[dict[str, Any]] = []
            for i in sorted(exits.get(event_time, []),
                            key=lambda block: (exit_ranks[block], block)):
                event_ops.append({
                    "type": "EXIT", "block_id": i,
                    "bay_id": candidate[7 * i],
                })
            for i in sorted(entries.get(event_time, []),
                            key=lambda block: (entry_ranks[block], block)):
                event_ops.append({
                    "type": "ENTRY", "block_id": i,
                    "bay_id": candidate[7 * i],
                    "x": candidate[7 * i + 2],
                    "y": candidate[7 * i + 3],
                    "orient_idx": candidate[7 * i + 1],
                })
            operations[str(event_time)] = event_ops
        raw_solution = {"operations": operations}
        checker = ma.utils.check_feasibility(problem, raw_solution)
        if not isinstance(checker, dict):
            raise ApiContractError("raw official checker returned no dict verdict")
    except Exception as exc:  # preserve the rejected leaf; never rescue it
        validation_error = f"{type(exc).__name__}: {exc}"

    engine_objective = [float(value) for value in engine.objective()]
    exact = _exact_objective(numbers, candidate)
    checker_ok = bool(checker and checker.get("feasible"))
    if checker_ok:
        checker_total = checker.get("objective")
        if not isinstance(checker_total, (int, float)) or not math.isfinite(
            float(checker_total)
        ):
            checker_ok = False
            validation_error = "official checker returned a non-finite objective"
        elif abs(float(checker_total) - float(exact["total"])) > 1e-7:
            checker_ok = False
            validation_error = (
                "exact rational objective disagrees with official checker: "
                f"exact={float(exact['total'])}, checker={checker_total}"
            )
        elif abs(float(checker_total) - engine_objective[-1]) > 1e-7:
            checker_ok = False
            validation_error = (
                "engine objective disagrees with official checker: "
                f"engine={engine_objective[-1]}, checker={checker_total}"
            )

    valid = bool(audit and audit[0] == 0 and checker_ok)
    if not valid and validation_error is None:
        first = None if checker is None else (checker.get("violations") or [None])[0]
        validation_error = f"audit={audit}, checker_first={str(first)[:240]}"
    return {
        "valid": valid,
        "flat": candidate,
        "flat_sha256": _digest(candidate),
        "audit": audit,
        "canonical_minimum_closure_used": canonical_used,
        "entry_ranks_sha256": _digest(entry_ranks) if entry_ranks else None,
        "exit_ranks_sha256": _digest(exit_ranks) if exit_ranks else None,
        "solution_sha256": _digest(raw_solution) if raw_solution is not None else None,
        "solution": raw_solution,
        "checker": checker,
        "engine_objective": engine_objective,
        "exact_objective": _objective_json(exact),
        "validation_error": validation_error,
    }


def _weighted_free_set(
    numbers: dict[str, Any], flat: Sequence[int], requested: int
) -> tuple[list[int], list[dict[str, Any]]]:
    n = numbers["n"]
    default_count = min(24, math.ceil(math.sqrt(n)))
    count = default_count if requested <= 0 else min(24, requested)
    count = min(n, count)
    rows: list[tuple[Fraction, Fraction, Fraction, int, Fraction]] = []
    for i in range(n):
        bay = int(flat[7 * i])
        tardy = max(
            Fraction(0), Fraction(int(flat[7 * i + 5])) - numbers["due"][i]
        )
        regret = numbers["smax"][i] - numbers["preference"][i][bay]
        cost = (
            numbers["weights"]["w1"] * tardy
            + numbers["weights"]["w3"] * regret
        )
        rows.append((cost, tardy, numbers["due"][i], i, regret))
    rows.sort(key=lambda row: (-row[0], -row[1], row[2], row[3]))
    chosen = [row[3] for row in rows[:count]]
    chosen_set = set(chosen)
    telemetry = [
        {
            "block": block,
            "weighted_cost": _fraction_text(cost),
            "tardiness": _fraction_text(tardy),
            "due": _fraction_text(due),
            "preference_regret": _fraction_text(regret),
            "selected": block in chosen_set,
        }
        for cost, tardy, due, block, regret in rows
    ]
    return chosen, telemetry


def _event_days(
    block: int,
    bay: int,
    flat: Sequence[int],
    numbers: dict[str, Any],
    horizon: int,
) -> list[int]:
    proc = numbers["processing"][block]
    values = {
        numbers["release"][block],
        _floor(numbers["due"][block] - proc),
        int(flat[7 * block + 4]),
    }
    for resident in range(numbers["n"]):
        if int(flat[7 * resident]) != bay:
            continue
        entry = int(flat[7 * resident + 4])
        exit_time = int(flat[7 * resident + 5])
        values.update((entry, exit_time, entry - proc, exit_time - proc))
    low = numbers["release"][block]
    high = horizon - proc
    return sorted(value for value in values if low <= value <= high)


def _rows5(raw: Any, label: str) -> tuple[list[list[int]], dict[str, Any]]:
    metadata: dict[str, Any] = {}
    if isinstance(raw, dict):
        metadata = {key: value for key, value in raw.items() if key != "rows"}
        raw = raw.get("rows")
    if not isinstance(raw, (list, tuple)):
        raise ApiContractError(f"{label} must return rows or a flat list")
    if raw and isinstance(raw[0], (list, tuple)):
        nested = list(raw)
        if any(len(row) != 5 for row in nested):
            raise ApiContractError(f"{label} returned a non-5-column row")
    else:
        if len(raw) % 5:
            raise ApiContractError(f"{label} flat result is not divisible by 5")
        nested = [raw[index:index + 5] for index in range(0, len(raw), 5)]
    rows = [
        [_exact_int(value, f"{label}[{r}][{c}]") for c, value in enumerate(row)]
        for r, row in enumerate(nested)
    ]
    return rows, metadata


def _build_candidates(
    engine,
    flat: Sequence[int],
    numbers: dict[str, Any],
    free: Sequence[int],
    total_cap: int,
    api_call_cap: int,
    horizon: int,
) -> tuple[dict[int, list[Candidate]], dict[str, Any]]:
    api = getattr(engine, "event_corner_candidates", None)
    if not callable(api):
        raise ApiContractError(
            "missing Engine.event_corner_candidates(block, bay, entry, cap): "
            "on a complete incumbent it must internally exclude only A and "
            "expose deterministic raw cornerSetsRB rows without any fixed/"
            "flexible crane-path feasibility filter; feasible_positions is "
            "not an exact substitute"
        )

    free_set = set(free)
    candidates: dict[int, list[Candidate]] = {}
    telemetry: dict[str, Any] = {"blocks": {}, "api": "event_corner_candidates"}
    generated_total = 0
    for i in range(numbers["n"]):
        original = Candidate(
            i,
            int(flat[7 * i]),
            int(flat[7 * i + 1]),
            int(flat[7 * i + 2]),
            int(flat[7 * i + 3]),
            int(flat[7 * i + 4]),
        )
        if i not in free_set:
            candidates[i] = [original]
            continue

        all_rows: set[Candidate] = set()
        block_meta: dict[str, Any] = {
            "event_days_by_bay": {},
            "calls": [],
        }
        for target_bay in range(numbers["m"]):
            days = _event_days(i, target_bay, flat, numbers, horizon)
            block_meta["event_days_by_bay"][str(target_bay)] = days
            for day in days:
                raw = api(i, target_bay, day, api_call_cap)
                rows, metadata = _rows5(raw, "event_corner_candidates")
                if len(rows) > api_call_cap:
                    raise ApiContractError(
                        f"event_corner_candidates returned {len(rows)} rows with "
                        f"cap {api_call_cap} for block {i}, bay {target_bay}, "
                        f"day {day}"
                    )
                call_rows: set[Candidate] = set()
                for bay, ori, x, y, entry in rows:
                    if bay != target_bay or entry != day:
                        raise ApiContractError(
                            "event_corner_candidates must honor requested bay/day; "
                            f"requested ({target_bay},{day}), got ({bay},{entry})"
                        )
                    call_rows.add(Candidate(i, bay, ori, x, y, entry))
                all_rows.update(call_rows)
                block_meta["calls"].append({
                    "bay": target_bay,
                    "day": day,
                    "returned": len(rows),
                    "unique": len(call_rows),
                    "cap_saturated": len(rows) == api_call_cap,
                    "api_metadata": metadata,
                })

        all_rows.discard(original)

        def candidate_key(candidate: Candidate) -> tuple[Any, ...]:
            tardy = max(
                Fraction(0),
                Fraction(candidate.entry + numbers["processing"][i])
                - numbers["due"][i],
            )
            regret = (
                numbers["smax"][i]
                - numbers["preference"][i][candidate.bay]
            )
            direct = (
                numbers["weights"]["w1"] * tardy
                + numbers["weights"]["w3"] * regret
            )
            # Historical placement tuples use (bay,ori,x,y,entry).  This is
            # the frozen lexicographic tie-break after direct personal cost.
            return (
                direct, candidate.bay, candidate.ori,
                candidate.x, candidate.y, candidate.entry,
            )

        ordered = sorted(all_rows, key=candidate_key)
        chosen = [original, *ordered[:total_cap - 1]]
        candidates[i] = chosen
        generated_total += len(chosen) - 1
        block_meta.update({
            "gathered_unique_non_incumbent": len(all_rows),
            "selected_count": len(chosen),
            "selected_alternatives": len(chosen) - 1,
            "selection_order": "immediate_personal_cost_then_(bay,ori,x,y,entry)",
            "selected_sha256": _digest([asdict(value) for value in chosen]),
        })
        telemetry["blocks"][str(i)] = block_meta

    if generated_total == 0:
        raise ApiContractError(
            "raw event-corner API returned no non-incumbent candidate for the "
            "chosen free set and bay; a negative result would be degenerate"
        )
    telemetry["generated_non_incumbent"] = generated_total
    return candidates, telemetry


def _relations3(raw: Any) -> tuple[list[tuple[int, int, int]], dict[str, Any]]:
    metadata: dict[str, Any] = {}
    if isinstance(raw, dict):
        metadata = {key: value for key, value in raw.items() if key != "rows"}
        raw = raw.get("rows")
    if not isinstance(raw, (list, tuple)):
        raise ApiContractError("pair_relations must return triples or a flat list")
    if raw and isinstance(raw[0], (list, tuple)):
        nested = list(raw)
        if any(len(row) != 3 for row in nested):
            raise ApiContractError("pair_relations returned a non-triple row")
    else:
        if len(raw) % 3:
            raise ApiContractError("pair_relations flat result is not divisible by 3")
        nested = [raw[index:index + 3] for index in range(0, len(raw), 3)]
    rows = [
        tuple(_exact_int(value, f"pair_relations[{r}][{c}]")
              for c, value in enumerate(row))
        for r, row in enumerate(nested)
    ]
    return rows, metadata


def _build_relations(
    engine, candidates: dict[int, list[Candidate]]
) -> tuple[list[Candidate], list[tuple[int, int, int]], dict[str, Any]]:
    api = getattr(engine, "pair_relations", None)
    if not callable(api):
        raise ApiContractError(
            "missing Engine.pair_relations(flat): expected geometry-only triples "
            "rel=1 hard, rel=2 candidate-A outer, rel=3 candidate-B outer"
        )
    flat_candidates = [
        candidate
        for block in range(len(candidates))
        for candidate in candidates[block]
    ]
    payload = [value for candidate in flat_candidates for value in candidate.relation_row()]
    raw = api(payload)
    rows, metadata = _relations3(raw)
    normalized: dict[tuple[int, int], int] = {}
    for a, b, relation in rows:
        if not 0 <= a < len(flat_candidates) or not 0 <= b < len(flat_candidates):
            raise ApiContractError(
                f"pair_relations index out of range: ({a},{b}) of {len(flat_candidates)}"
            )
        if a == b:
            raise ApiContractError("pair_relations returned a self relation")
        if relation not in (1, 2, 3):
            raise ApiContractError(f"unknown pair relation code {relation}")
        if a > b:
            a, b = b, a
            if relation == 2:
                relation = 3
            elif relation == 3:
                relation = 2
        ca, cb = flat_candidates[a], flat_candidates[b]
        if ca.block == cb.block:
            raise ApiContractError("pair_relations related alternatives of one block")
        if ca.bay != cb.bay:
            raise ApiContractError("pair_relations related candidates in different bays")
        old = normalized.get((a, b))
        if old is not None and old != relation:
            raise ApiContractError(
                f"contradictory relations for candidates ({a},{b}): {old}, {relation}"
            )
        normalized[(a, b)] = relation
    result = [(a, b, relation) for (a, b), relation in sorted(normalized.items())]
    return flat_candidates, result, metadata


def _add_relation_or(
    model: cp_model.CpModel,
    selected_a,
    selected_b,
    alternatives: Sequence[Sequence[tuple[Any, str, Any]]],
    prefix: str,
) -> None:
    choices = [model.NewBoolVar(f"{prefix}_alt{index}")
               for index in range(len(alternatives))]
    model.AddBoolOr([selected_a.Not(), selected_b.Not(), *choices])
    for choice, constraints in zip(choices, alternatives):
        for lhs, sense, rhs in constraints:
            if sense == "le":
                model.Add(lhs <= rhs).OnlyEnforceIf(
                    [selected_a, selected_b, choice]
                )
            elif sense == "lt":
                model.Add(lhs < rhs).OnlyEnforceIf(
                    [selected_a, selected_b, choice]
                )
            else:
                raise AssertionError(sense)


def _build_model(
    problem: dict[str, Any],
    numbers: dict[str, Any],
    source_flat: Sequence[int],
    candidates: dict[int, list[Candidate]],
    flat_candidates: Sequence[Candidate],
    relations: Sequence[tuple[int, int, int]],
    horizon: int,
    flexible: bool,
    source_ranks: Sequence[int],
) -> tuple[cp_model.CpModel, dict[str, Any]]:
    del problem  # all numeric data was validated and exactified above
    n, m = numbers["n"], numbers["m"]
    model = cp_model.CpModel()
    choice: dict[tuple[int, int], Any] = {}
    by_object: dict[Candidate, Any] = {}
    entry: list[Any] = []
    exit_: list[Any] = []
    rank: list[Any] = []
    key: list[Any] = []
    extra: list[Any] = []
    rank_base = n + 1

    for i in range(n):
        row = candidates[i]
        variables = [model.NewBoolVar(f"x_{i}_{k}") for k in range(len(row))]
        model.AddExactlyOne(variables)
        for k, (candidate, variable) in enumerate(zip(row, variables)):
            choice[(i, k)] = variable
            by_object[candidate] = variable
            model.AddHint(variable, 1 if k == 0 else 0)
        lo = min(candidate.entry for candidate in row)
        hi = max(candidate.entry for candidate in row)
        e_var = model.NewIntVar(lo, hi, f"e_{i}")
        model.Add(e_var == sum(
            candidate.entry * variables[k] for k, candidate in enumerate(row)
        ))
        f_var = model.NewIntVar(lo + numbers["processing"][i], horizon, f"f_{i}")
        if flexible:
            model.Add(f_var >= e_var + numbers["processing"][i])
        else:
            model.Add(f_var == e_var + numbers["processing"][i])
        r_var = model.NewIntVar(0, n - 1, f"r_{i}")
        k_var = model.NewIntVar(lo * rank_base, hi * rank_base + n - 1, f"key_{i}")
        model.Add(k_var == e_var * rank_base + r_var)
        x_var = model.NewIntVar(0, horizon - lo - numbers["processing"][i], f"extra_{i}")
        model.Add(x_var == f_var - e_var - numbers["processing"][i])
        model.AddHint(e_var, int(source_flat[7 * i + 4]))
        model.AddHint(
            f_var,
            int(source_flat[7 * i + 5]) if flexible
            else int(source_flat[7 * i + 4]) + numbers["processing"][i],
        )
        model.AddHint(r_var, min(n - 1, max(0, int(source_ranks[i]))))
        entry.append(e_var)
        exit_.append(f_var)
        rank.append(r_var)
        key.append(k_var)
        extra.append(x_var)
    model.AddAllDifferent(key)

    flat_choice = [by_object[candidate] for candidate in flat_candidates]
    for rel_index, (a, b, relation) in enumerate(relations):
        ca, cb = flat_candidates[a], flat_candidates[b]
        ia, ib = ca.block, cb.block
        if relation == 1:
            alternatives = (
                ((exit_[ia], "le", entry[ib]),),
                ((exit_[ib], "le", entry[ia]),),
            )
        else:
            outer, inner = (ia, ib) if relation == 2 else (ib, ia)
            alternatives = (
                ((exit_[outer], "le", entry[inner]),),
                ((exit_[inner], "le", entry[outer]),),
                (
                    (entry[outer], "le", entry[inner]),
                    (exit_[inner], "le", exit_[outer]),
                    (key[outer], "lt", key[inner]),
                ),
            )
        _add_relation_or(
            model, flat_choice[a], flat_choice[b], alternatives, f"rel_{rel_index}"
        )

    # Exact Z1 numerators.  A decimal due date remains exact instead of being
    # rounded to the engine's integer convenience representation.
    tardy_num: list[Any] = []
    coefficient_terms: list[tuple[Fraction, Any, int]] = []
    for i in range(n):
        due = numbers["due"][i]
        upper = max(0, due.denominator * horizon - due.numerator)
        _safe(upper, f"tardy numerator upper[{i}]")
        variable = model.NewIntVar(0, upper, f"tardy_num_{i}")
        model.AddMaxEquality(
            variable,
            [0, due.denominator * exit_[i] - due.numerator],
        )
        tardy_num.append(variable)
        coefficient_terms.append((
            numbers["weights"]["w1"] / due.denominator,
            variable,
            upper,
        ))

    # Exact rational normalized loads and floor(max-min).
    avg_area = sum(numbers["area"], Fraction(0)) / m
    normalized_coeff = [
        [avg_area * numbers["workload"][i] / numbers["area"][j]
         for j in range(m)]
        for i in range(n)
    ]
    load_scale = _lcm(
        coefficient.denominator
        for row in normalized_coeff
        for coefficient in row
    )
    load: list[Any] = []
    load_upper: list[int] = []
    for j in range(m):
        terms = []
        upper = 0
        for i in range(n):
            coefficient = int(normalized_coeff[i][j] * load_scale)
            if coefficient < 0:
                raise ProbeError("negative normalized workload coefficient")
            has_bay = False
            for k, candidate in enumerate(candidates[i]):
                if candidate.bay == j:
                    terms.append(coefficient * choice[(i, k)])
                    has_bay = True
            if has_bay:
                upper += coefficient
        _safe(upper, f"normalized load upper[{j}]")
        variable = model.NewIntVar(0, upper, f"load_{j}")
        model.Add(variable == (sum(terms) if terms else 0))
        load.append(variable)
        load_upper.append(upper)

    max_load_bound = max(load_upper, default=0)
    max_load = model.NewIntVar(0, max_load_bound, "max_load")
    min_load = model.NewIntVar(0, max_load_bound, "min_load")
    model.AddMaxEquality(max_load, load)
    model.AddMinEquality(min_load, load)
    load_range = model.NewIntVar(0, max_load_bound, "load_range")
    model.Add(load_range == max_load - min_load)
    z2_upper = max_load_bound // load_scale
    z2 = model.NewIntVar(0, z2_upper, "z2")
    model.AddDivisionEquality(z2, load_range, load_scale)
    coefficient_terms.append((numbers["weights"]["w2"], z2, z2_upper))

    # Exact weighted Z3, expressed directly on the shared candidate choices.
    z3_by_block: list[list[tuple[Fraction, Any]]] = []
    for i in range(n):
        row_terms: list[tuple[Fraction, Any]] = []
        for k, candidate in enumerate(candidates[i]):
            regret = numbers["smax"][i] - numbers["preference"][i][candidate.bay]
            if regret < 0:
                raise ProbeError("negative preference regret")
            row_terms.append((
                numbers["weights"]["w3"] * regret,
                choice[(i, k)],
            ))
        z3_by_block.append(row_terms)

    objective_scale = _lcm(
        [coefficient.denominator for coefficient, _var, _upper in coefficient_terms]
        + [coefficient.denominator
           for row in z3_by_block for coefficient, _var in row]
    )
    integer_terms: list[Any] = []
    primary_upper = 0
    for coefficient, variable, upper in coefficient_terms:
        integer_coefficient = int(coefficient * objective_scale)
        _safe(integer_coefficient, "objective coefficient")
        integer_terms.append(integer_coefficient * variable)
        primary_upper += integer_coefficient * upper
    for row in z3_by_block:
        row_coefficients: list[int] = []
        for coefficient, variable in row:
            integer_coefficient = int(coefficient * objective_scale)
            _safe(integer_coefficient, "Z3 objective coefficient")
            integer_terms.append(integer_coefficient * variable)
            row_coefficients.append(integer_coefficient)
        primary_upper += max(row_coefficients, default=0)
    _safe(primary_upper, "primary objective upper")
    primary = model.NewIntVar(0, primary_upper, "primary_scaled")
    model.Add(primary == (sum(integer_terms) if integer_terms else 0))

    # Frozen secondary: among full-objective ties choose the earliest aggregate
    # exit witness.  This is sum(f), not extension-only sum(f-e-P).
    secondary_upper = n * horizon
    _safe(secondary_upper, "sum(exit) upper")
    lex_scale = secondary_upper + 1
    _safe(primary_upper * lex_scale + secondary_upper, "lex objective upper")
    model.Minimize(primary * lex_scale + sum(exit_))

    return model, {
        "choice": choice,
        "entry": entry,
        "exit": exit_,
        "rank": rank,
        "key": key,
        "extra": extra,
        "primary": primary,
        "tardy_num": tardy_num,
        "z2": z2,
        "objective_scale": objective_scale,
        "load_scale": load_scale,
        "lex_scale": lex_scale,
        "primary_upper": primary_upper,
        "secondary": "sum(exit)",
        "secondary_upper": secondary_upper,
    }


def _solve_arm(
    name: str,
    ma,
    problem: dict[str, Any],
    numbers: dict[str, Any],
    source_flat: Sequence[int],
    candidates: dict[int, list[Candidate]],
    flat_candidates: Sequence[Candidate],
    relations: Sequence[tuple[int, int, int]],
    horizon: int,
    source_ranks: Sequence[int],
    seconds: float,
    seed: int,
) -> dict[str, Any]:
    flexible = name == "flex"
    model, variables = _build_model(
        problem,
        numbers,
        source_flat,
        candidates,
        flat_candidates,
        relations,
        horizon,
        flexible,
        source_ranks,
    )
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = seconds
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = seed
    solver.parameters.log_search_progress = False
    started = time.monotonic()
    status = solver.Solve(model)
    elapsed = time.monotonic() - started
    status_name = solver.StatusName(status)
    report: dict[str, Any] = {
        "arm": name,
        "status": status_name,
        "configured_seconds": seconds,
        "elapsed_seconds": elapsed,
        "workers": 1,
        "seed": seed,
        "branches": solver.NumBranches(),
        "conflicts": solver.NumConflicts(),
        "solver_wall_seconds": solver.WallTime(),
        "objective_value_lex": (
            solver.ObjectiveValue()
            if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None
        ),
        "best_bound_lex": (
            solver.BestObjectiveBound()
            if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None
        ),
    }
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return report

    chosen: list[int] = []
    result_flat = [0] * (7 * numbers["n"])
    for i in range(numbers["n"]):
        selected = [
            k for k in range(len(candidates[i]))
            if solver.Value(variables["choice"][(i, k)])
        ]
        if len(selected) != 1:
            raise ProbeError(f"arm {name} selected {len(selected)} candidates for block {i}")
        k = selected[0]
        chosen.append(k)
        candidate = candidates[i][k]
        result_flat[7 * i:7 * i + 7] = [
            candidate.bay,
            candidate.ori,
            candidate.x,
            candidate.y,
            solver.Value(variables["entry"][i]),
            solver.Value(variables["exit"][i]),
            1,
        ]

    cp_primary_scaled = solver.Value(variables["primary"])
    preclosure_exact = _exact_objective(numbers, result_flat)
    cp_primary_exact = Fraction(
        cp_primary_scaled, variables["objective_scale"]
    )
    objective_encoding_exact = cp_primary_exact == preclosure_exact["total"]
    validation = _inspect_flat(
        ma,
        problem,
        numbers,
        result_flat,
        horizon,
        canonicalize=flexible,
    )
    if not objective_encoding_exact:
        validation["valid"] = False
        validation["validation_error"] = (
            "CP primary objective disagrees with the exact rational leaf: "
            f"cp={_fraction_text(cp_primary_exact)}, "
            f"leaf={_fraction_text(preclosure_exact['total'])}"
        )
    report.update({
        "selected_candidate_indices": chosen,
        "selected_candidate_sha256": _digest(chosen),
        "cp_primary_scaled": cp_primary_scaled,
        "cp_primary_scale": variables["objective_scale"],
        "cp_primary_exact": _fraction_text(cp_primary_exact),
        "preclosure_exact_objective": _objective_json(preclosure_exact),
        "objective_encoding_exact": objective_encoding_exact,
        "cp_z2": solver.Value(variables["z2"]),
        "extended_blocks": sum(
            solver.Value(variable) > 0 for variable in variables["extra"]
        ),
        "total_extra_days": sum(
            solver.Value(variable) for variable in variables["extra"]
        ),
        "sum_exit_days": sum(
            solver.Value(variable) for variable in variables["exit"]
        ),
        "validation": validation,
    })
    return report


def _classification(
    baseline: dict[str, Any], fixed: dict[str, Any], flex: dict[str, Any]
) -> dict[str, Any]:
    base_total = Fraction(
        baseline["exact_objective"]["total"]["exact"]
    )

    def arm_total(arm: dict[str, Any]) -> Fraction | None:
        validation = arm.get("validation")
        if not validation or not validation.get("valid"):
            return None
        return Fraction(validation["exact_objective"]["total"]["exact"])

    fixed_total = arm_total(fixed)
    flex_total = arm_total(flex)
    if ((fixed.get("validation") and not fixed["validation"].get("valid")) or
            (flex.get("validation") and not flex["validation"].get("valid"))):
        verdict = "MODEL_OR_API_MISMATCH"
    elif (
        flex.get("status") == "OPTIMAL"
        and fixed_total is not None
        and flex_total is not None
        and flex_total > fixed_total
    ):
        # Every fixed leaf is legal in flex.  An optimal flex value above a
        # checked fixed leaf contradicts the intended strict-superset model.
        verdict = "MODEL_OR_API_MISMATCH"
    elif flex_total is not None and flex_total < base_total:
        verdict = (
            "FLEX_STRICT_CAUSAL_GAIN"
            if fixed_total is not None and flex_total < fixed_total
            else "FLEX_CHECKED_GAIN"
        )
    elif (
        flex.get("status") == "OPTIMAL"
        and flex_total is not None
        and flex_total == base_total
    ):
        if (
            fixed.get("status") == "OPTIMAL"
            and fixed_total is not None
            and fixed_total == flex_total
        ):
            verdict = "OPTIMAL_NO_C5_ADVANTAGE_IN_VOCABULARY"
        else:
            verdict = "OPTIMAL_NO_FLEX_IMPROVEMENT_IN_VOCABULARY"
    else:
        verdict = "UNRESOLVED"
    return {
        "verdict": verdict,
        "negative_certificate_requires_optimal": True,
        "fixed_checked_delta": (
            _fraction_text(fixed_total - base_total)
            if fixed_total is not None else None
        ),
        "flex_checked_delta": (
            _fraction_text(flex_total - base_total)
            if flex_total is not None else None
        ),
        "flex_minus_fixed": (
            _fraction_text(flex_total - fixed_total)
            if flex_total is not None and fixed_total is not None else None
        ),
        "scope": "reported deterministic free set and capped candidate vocabulary only",
    }


def _run(args: argparse.Namespace) -> dict[str, Any]:
    problem_path = args.problem.resolve()
    plateau_path = args.plateau.resolve()
    solver_path = args.solver.resolve()
    problem = _load_json(problem_path)
    if not isinstance(problem, dict):
        raise ProbeError("problem JSON must be an object")
    numbers = _problem_numbers(problem)
    n, m = numbers["n"], numbers["m"]
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        raise ProbeError("--seconds must be finite and positive")
    if args.free_count < 0:
        raise ProbeError("--free-count must be 0 (auto) or positive")
    alternative_cap = m * math.ceil(math.sqrt(n))
    total_candidate_cap = 1 + alternative_cap
    if args.candidate_cap not in (0, total_candidate_cap):
        raise ProbeError(
            "the frozen probe cap is 1 + m*ceil(sqrt(n)) = "
            f"{total_candidate_cap}; pass --candidate-cap 0 (auto) or exactly "
            f"{total_candidate_cap}"
        )

    plateau_raw = _load_json(plateau_path)
    source_flat, plateau_meta = _read_flat(plateau_raw, n)
    if "instance" in plateau_meta and str(plateau_meta["instance"]) != str(args.instance):
        raise ProbeError(
            f"plateau instance {plateau_meta['instance']!r} != --instance {args.instance!r}"
        )
    if "flat_sha256" in plateau_meta:
        actual = _digest(source_flat)
        if plateau_meta["flat_sha256"] != actual:
            raise ProbeError(
                f"plateau flat digest mismatch: {plateau_meta['flat_sha256']} != {actual}"
            )

    overlong_blocks = [
        i for i in range(n)
        if source_flat[7 * i + 5] >
        source_flat[7 * i + 4] + numbers["processing"][i]
    ]
    short_blocks = [
        i for i in range(n)
        if source_flat[7 * i + 5] <
        source_flat[7 * i + 4] + numbers["processing"][i]
    ]
    if short_blocks:
        raise ProbeError(
            "plateau violates minimum processing duration for blocks "
            f"{short_blocks[:8]}"
        )

    ma, module_path = _load_solver(solver_path)
    horizon = max(
        max(source_flat[7 * i + 5] for i in range(n)),
        max(numbers["release"]) + sum(numbers["processing"]),
    ) + 1
    _safe(horizon * (n + 1) + n, "horizon/rank key bound")
    baseline = _inspect_flat(
        ma,
        problem,
        numbers,
        source_flat,
        horizon,
        # Arbitrary checker-feasible v6.4 incumbents may contain voluntary
        # stays or official same-day EXIT orders rejected by the conservative
        # fixed oracle.  The source authority is therefore always the exact
        # flexible audit plus raw checker.
        canonicalize=False,
    )
    if not baseline["valid"]:
        raise ProbeError(
            f"plateau is not raw-checker/engine feasible: {baseline['validation_error']}"
        )

    free, free_telemetry = _weighted_free_set(
        numbers, source_flat, args.free_count
    )
    vocabulary_engine = _new_engine(ma, problem, horizon)
    vocabulary_engine.set_solution(source_flat)
    vocabulary_engine.build_occ(horizon)

    # Check both exact experimental APIs before doing expensive candidate work
    # so an old binary fails with a complete, actionable contract message.
    missing: list[str] = []
    if not callable(getattr(vocabulary_engine, "event_corner_candidates", None)):
        missing.append(
            "event_corner_candidates(block,bay,entry,cap): on complete incumbent "
            "exclude only A; raw contained/same-layer-free cornerSetsRB rows; "
            "no crane oracle"
        )
    if not callable(getattr(vocabulary_engine, "pair_relations", None)):
        missing.append(
            "pair_relations(flat): geometry-only triples 1=hard,2=Aouter,3=Bouter"
        )
    if missing:
        raise ApiContractError("required Engine API missing: " + "; ".join(missing))

    candidates, candidate_telemetry = _build_candidates(
        vocabulary_engine,
        source_flat,
        numbers,
        free,
        total_candidate_cap,
        alternative_cap,
        horizon,
    )
    flat_candidates, relations, relation_metadata = _build_relations(
        vocabulary_engine, candidates
    )
    event_manifest = {
        "free_blocks": free,
        "candidate_rows": [candidate.relation_row() for candidate in flat_candidates],
        "relations": relations,
        "candidate_cap": total_candidate_cap,
    }
    vocabulary_sha256 = _digest(event_manifest)
    # Re-read canonical source ranks from a fresh full board so vocabulary API
    # calls cannot affect hints even if a future implementation caches orders.
    source_engine = _new_engine(ma, problem, horizon)
    source_engine.set_solution(source_flat)
    source_engine.build_occ(horizon)
    source_ranks = [int(value) for value in source_engine.entry_ranks()]
    if len(source_ranks) != n:
        raise ApiContractError("entry_ranks returned the wrong source length")

    fixed = _solve_arm(
        "fixed", ma, problem, numbers, source_flat, candidates,
        flat_candidates, relations, horizon, source_ranks,
        args.seconds, args.seed,
    )
    flex = _solve_arm(
        "flex", ma, problem, numbers, source_flat, candidates,
        flat_candidates, relations, horizon, source_ranks,
        args.seconds, args.seed,
    )
    for arm in (fixed, flex):
        arm["vocabulary_sha256"] = vocabulary_sha256
        arm["candidate_cap_per_free_block"] = total_candidate_cap
    classification = _classification(baseline, fixed, flex)

    relation_counts = {str(code): sum(row[2] == code for row in relations)
                       for code in (1, 2, 3)}
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": "OK",
        "probe": "paired_fixed_vs_flexible_repack",
        "instance": args.instance,
        "problem": str(problem_path),
        "plateau": str(plateau_path),
        "solver": str(solver_path),
        "parameters": {
            "requested_free_count": args.free_count,
            "effective_free_count": len(free),
            "default_free_rule": "min(24,ceil(sqrt(n)))",
            "free_ranking": (
                "weighted_cost_desc,tardiness_desc,due_asc,block_id_asc"
            ),
            "seconds_per_arm": args.seconds,
            "requested_candidate_cap": args.candidate_cap,
            "candidate_cap_rule": "1+m*ceil(sqrt(n))",
            "candidate_cap_per_free_block_including_original": total_candidate_cap,
            "candidate_alternative_cap_per_free_block": alternative_cap,
            "candidate_bays": "all",
            "seed": args.seed,
            "workers": 1,
        },
        "provenance": {
            "problem_sha256": _file_digest(problem_path),
            "plateau_sha256": _file_digest(plateau_path),
            "plateau_flat_sha256": _digest(source_flat),
            "tool_sha256": _file_digest(Path(__file__).resolve()),
            "myalgorithm_sha256": _file_digest(solver_path / "myalgorithm.py"),
            "sscore_path": str(module_path),
            "sscore_sha256": _file_digest(module_path),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "ortools_module": str(Path(cp_model.__file__).resolve()),
        },
        "dimensions": {"blocks": n, "bays": m, "horizon": horizon},
        "source_duration": {
            "overlong_blocks": overlong_blocks,
            "overlong_count": len(overlong_blocks),
            "exact_flat_embeds_in_flex": True,
            "exact_flat_embeds_in_fixed": not overlong_blocks,
            "fixed_arm_scope": (
                "same geometry/entry choices with exit=entry+processing"
            ),
        },
        "baseline": baseline,
        "free_set": free,
        "free_ranking": free_telemetry,
        "candidate_telemetry": candidate_telemetry,
        "candidate_counts": {str(i): len(candidates[i]) for i in range(n)},
        "flat_candidate_count": len(flat_candidates),
        "relation_count": len(relations),
        "relation_counts": relation_counts,
        "relation_api_metadata": relation_metadata,
        "vocabulary_sha256": vocabulary_sha256,
        "arms_share_vocabulary": True,
        "arms": {"fixed": fixed, "flex": flex},
        "classification": classification,
    }
    if classification["verdict"] == "MODEL_OR_API_MISMATCH":
        report["status"] = "MODEL_OR_API_MISMATCH"
    return report


def _args_json(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "problem": str(args.problem),
        "plateau": str(args.plateau),
        "output": str(args.output),
        "solver": str(args.solver),
        "instance": args.instance,
        "free_count": args.free_count,
        "seconds": args.seconds,
        "candidate_cap": args.candidate_cap,
        "seed": args.seed,
    }


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("problem", type=Path, help="problem JSON")
    parser.add_argument("plateau", type=Path,
                        help="flat list JSON or report containing a flat field")
    parser.add_argument("output", type=Path, help="single JSON report path")
    parser.add_argument("--solver", type=Path, default=ROOT / "solver")
    parser.add_argument("--instance", required=True,
                        help="provenance label only; never selects a rule")
    parser.add_argument("--free-count", type=int, default=0,
                        help="0 uses min(24,ceil(sqrt(n))); positive is capped at 24")
    parser.add_argument("--seconds", type=float, default=60.0,
                        help="identical one-worker wall limit for each arm")
    parser.add_argument(
        "--candidate-cap", type=int, default=0,
        help=("0 uses frozen total 1+m*ceil(sqrt(n)); the only accepted "
              "explicit value is that same total"),
    )
    parser.add_argument("--seed", type=int, default=1)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        report = _run(args)
        code = 3 if report.get("status") == "MODEL_OR_API_MISMATCH" else 0
    except Exception as exc:
        report = {
            "schema_version": SCHEMA_VERSION,
            "status": "ERROR",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "arguments": _args_json(args),
        }
        code = 2
    _write_report(args.output, report)
    print(json.dumps(report, sort_keys=True), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
