#!/usr/bin/env python3
"""Synthetic end-to-end regression tests for ``tools/flexible_repack.py``.

The tests intentionally use the built ``sscore`` extension rather than a
Python geometry reimplementation.  They cover the geometry-only relation ABI,
the CP fixed/flexible strict-superset model, official entry/exit ordering, the
raw checker, event endpoints/horizon, exact Z2 selection, and the frozen
P4/P5/P6 dual-margin evidence label.

Run from the repository root after building the experimental extension:

    python3 tools/test_flexible_repack.py [--solver solver]
"""

from __future__ import annotations

import argparse
from fractions import Fraction
import math
from pathlib import Path
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import flexible_repack as probe  # noqa: E402


SECONDS = 2.0
SEED = 1


def _shape(layers: list[list[list[float]]]) -> dict[str, Any]:
    return {"orientation": 0, "layers": layers}


def _block(
    release: int,
    processing: int,
    due: int,
    layers: list[list[list[float]]],
    preferences: Sequence[int] | None = None,
    workload: int = 1,
) -> dict[str, Any]:
    return {
        "release_time": release,
        "processing_time": processing,
        "due_date": due,
        "workload": workload,
        "bay_preferences": list(preferences or (1,)),
        "shape": [_shape(layers)],
    }


def _cell(left: float) -> list[list[float]]:
    return [
        [left, 0], [left + 0.4, 0],
        [left + 0.4, 1], [left, 1],
    ]


def _problem(
    blocks: list[dict[str, Any]],
    bay_count: int = 1,
    width: int = 1,
    weights: tuple[int, int, int] = (1, 0, 0),
) -> dict[str, Any]:
    return {
        "bays": [{"width": width, "height": 1} for _ in range(bay_count)],
        "blocks": blocks,
        "weights": {
            "w1": weights[0], "w2": weights[1], "w3": weights[2],
        },
    }


def _one_way_problem() -> dict[str, Any]:
    left, right = _cell(0.0), _cell(1.0)
    return _problem([
        _block(0, 2, 4, [left]),
        _block(1, 3, 4, [right, left]),
    ], width=2)


def _chain_problem(cycle: bool = False) -> dict[str, Any]:
    p0, p1, p2 = _cell(0.0), _cell(1.0), _cell(2.0)
    layers = (
        [[p0, p2], [p1, p0], [p2, p1]]
        if cycle else [[p0], [p1, p0], [p2, p1]]
    )
    blocks = [
        _block(0, 2 if not cycle else 1, 20, layers[0]),
        _block(0, 3 if not cycle else 1, 20, layers[1]),
        _block(0, 4 if not cycle else 1, 20, layers[2]),
    ]
    return _problem(blocks, width=3)


def _candidate(
    block: int, bay: int = 0, entry: int = 0, x: int = 0
) -> probe.Candidate:
    return probe.Candidate(block, bay, 0, x, 0, entry)


def _flat(rows: Sequence[Sequence[int]]) -> list[int]:
    return [value for row in rows for value in row]


def _numbers(problem: dict[str, Any]) -> dict[str, Any]:
    return probe._problem_numbers(problem)


def _engine(ma, problem: dict[str, Any], horizon: int, flat: Sequence[int]):
    engine = probe._new_engine(ma, problem, horizon)
    engine.set_solution(list(flat))
    engine.build_occ(horizon)
    return engine


def _candidate_manifest(
    engine,
    problem: dict[str, Any],
    source: Sequence[int],
    free: Sequence[int],
    horizon: int,
) -> tuple[
    dict[int, list[probe.Candidate]],
    list[probe.Candidate],
    list[tuple[int, int, int]],
    dict[str, Any],
]:
    numbers = _numbers(problem)
    alternative_cap = numbers["m"] * math.ceil(math.sqrt(numbers["n"]))
    candidates, telemetry = probe._build_candidates(
        engine,
        source,
        numbers,
        free,
        1 + alternative_cap,
        alternative_cap,
        horizon,
    )
    flat_candidates, relations = _relations(engine, candidates)
    return candidates, flat_candidates, relations, telemetry


def _relations(
    engine, candidates: dict[int, list[probe.Candidate]]
) -> tuple[list[probe.Candidate], list[tuple[int, int, int]]]:
    flat_candidates, relations, _metadata = probe._build_relations(
        engine, candidates
    )
    return flat_candidates, relations


def _solve(
    name: str,
    ma,
    problem: dict[str, Any],
    source: list[int],
    candidates: dict[int, list[probe.Candidate]],
    horizon: int,
) -> dict[str, Any]:
    numbers = _numbers(problem)
    engine = _engine(ma, problem, horizon, source)
    flat_candidates, relations = _relations(engine, candidates)
    source_ranks = list(engine.entry_ranks())
    return probe._solve_arm(
        name,
        ma,
        problem,
        numbers,
        source,
        candidates,
        flat_candidates,
        relations,
        horizon,
        source_ranks,
        SECONDS,
        SEED,
    )


def _checked_total(arm: dict[str, Any]) -> Fraction:
    assert arm["validation"]["valid"], arm
    return Fraction(arm["validation"]["exact_objective"]["total"]["exact"])


def _selected_flat(arm: dict[str, Any]) -> list[int]:
    return list(arm["validation"]["flat"])


def test_one_way_fixed_two_flex_zero(ma) -> None:
    """The exact C5-prime kill: FIXED=2, FLEX=0, both checker-valid."""

    problem = _one_way_problem()
    # Checker-feasible fallback: outer block 0 enters after inner block 1.
    source = _flat([
        [0, 0, 0, 0, 4, 6, 1],
        [0, 0, 1, 0, 1, 4, 1],
    ])
    candidates = {
        0: [_candidate(0, entry=4), _candidate(0, entry=0)],
        1: [_candidate(1, entry=1, x=1)],
    }
    engine = _engine(ma, problem, 10, source)
    api_candidates, api_flat, api_relations, telemetry = _candidate_manifest(
        engine, problem, source, [0], 10
    )
    assert api_candidates[0][0] == candidates[0][0]
    assert candidates[0][1] in api_candidates[0]
    assert telemetry["blocks"]["0"]["selected_alternatives"] <= 2
    decoded_api = {
        (api_flat[a].block, api_flat[b].block, code)
        for a, b, code in api_relations
    }
    assert decoded_api == {(0, 1, 2)}

    flat_candidates, relations = _relations(engine, candidates)
    assert relations == [(0, 2, 2), (1, 2, 2)]
    assert list(engine.audit_state(True)) == [0, -1]

    fixed = probe._solve_arm(
        "fixed", ma, problem, _numbers(problem), source, candidates,
        flat_candidates, relations, 10, list(engine.entry_ranks()),
        SECONDS, SEED,
    )
    flex = probe._solve_arm(
        "flex", ma, problem, _numbers(problem), source, candidates,
        flat_candidates, relations, 10, list(engine.entry_ranks()),
        SECONDS, SEED,
    )
    assert fixed["status"] == "OPTIMAL", fixed
    assert flex["status"] == "OPTIMAL", flex
    assert _checked_total(fixed) == 2
    assert _checked_total(flex) == 0
    assert fixed["selected_candidate_indices"] == [0, 0]
    assert flex["selected_candidate_indices"] == [1, 0]
    flex_flat = _selected_flat(flex)
    assert flex_flat[4:6] == [0, 4]
    assert flex_flat[11:13] == [1, 4]
    assert flex["extended_blocks"] == 1
    assert flex["objective_encoding_exact"]
    assert fixed["objective_encoding_exact"]

    baseline = probe._inspect_flat(
        ma, problem, _numbers(problem), source, 10, canonicalize=False
    )
    p4 = probe._classification("P4", baseline, fixed, flex)
    numeric_p4 = probe._classification(4, baseline, fixed, flex)
    arbitrary = probe._classification("synthetic", baseline, fixed, flex)
    minimum_stay_flex = {**flex, "extended_blocks": 0}
    assert p4["mechanism_positive"]
    assert numeric_p4["canonical_instance"] == "P4"
    assert numeric_p4["mechanism_positive"]
    assert p4["flex_le_99pct_baseline"]
    assert p4["flex_le_99pct_fixed"]
    assert p4["flex_over_baseline_exact"] == "0"
    assert arbitrary["mechanism_positive"] is False
    assert probe._classification(
        "P4", baseline, fixed, minimum_stay_flex
    )["mechanism_positive"] is False


def test_transitive_three_chain(ma) -> None:
    """Two one-way edges force the least CP wrapper chain to a common exit."""

    problem = _chain_problem()
    source = _flat([
        [0, 0, 0, 0, 7, 9, 1],
        [0, 0, 1, 0, 4, 7, 1],
        [0, 0, 2, 0, 0, 4, 1],
    ])
    candidates = {
        0: [_candidate(0, entry=7), _candidate(0, entry=0)],
        1: [_candidate(1, entry=4, x=1), _candidate(1, entry=1, x=1)],
        2: [_candidate(2, entry=0, x=2), _candidate(2, entry=2, x=2)],
    }
    engine = _engine(ma, problem, 12, source)
    _flat_candidates, relations = _relations(engine, candidates)
    assert relations
    # Every placement pair for blocks 0/1 is 0-outer, and every 1/2 pair is
    # 1-outer; the non-neighbour blocks are geometrically clear.
    flat_candidates, _ = _relations(engine, candidates)
    decoded = [
        (flat_candidates[a].block, flat_candidates[b].block, code)
        for a, b, code in relations
    ]
    assert set(decoded) == {(0, 1, 2), (1, 2, 2)}

    model, variables = probe._build_model(
        problem, _numbers(problem), source, candidates, flat_candidates,
        relations, 12, True, list(engine.entry_ranks()),
    )
    for block in range(3):
        model.Add(variables["choice"][(block, 1)] == 1)
    solver = probe.cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = SECONDS
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = SEED
    status = solver.Solve(model)
    assert solver.StatusName(status) == "OPTIMAL"
    result: list[int] = []
    for block in range(3):
        chosen = candidates[block][1]
        result.extend([
            chosen.bay, chosen.ori, chosen.x, chosen.y,
            solver.Value(variables["entry"][block]),
            solver.Value(variables["exit"][block]), 1,
        ])
    checked = probe._inspect_flat(
        ma, problem, _numbers(problem), result, 12, canonicalize=False
    )
    assert checked["valid"], checked
    assert result[4] == 0 and result[11] == 1 and result[18] == 2
    assert result[5] == result[12] == result[19] == 6
    assert sum(solver.Value(value) > 0 for value in variables["extra"]) == 2
    assert sum(solver.Value(value) for value in variables["extra"]) == 6


def test_same_entry_cycle_rejected(ma) -> None:
    """A directed same-entry cycle cannot receive an AllDifferent key order."""

    problem = _chain_problem(cycle=True)
    source = _flat([
        [0, 0, 0, 0, 0, 1, 1],
        [0, 0, 1, 0, 1, 2, 1],
        [0, 0, 2, 0, 2, 3, 1],
    ])
    candidates = {
        0: [_candidate(0, entry=0)],
        1: [_candidate(1, entry=1, x=1), _candidate(1, entry=0, x=1)],
        2: [_candidate(2, entry=2, x=2), _candidate(2, entry=0, x=2)],
    }
    engine = _engine(ma, problem, 6, source)
    flat_candidates, relations = _relations(engine, candidates)
    assert {
        (flat_candidates[a].block, flat_candidates[b].block, code)
        for a, b, code in relations
    } == {(0, 1, 2), (0, 2, 3), (1, 2, 2)}

    model, variables = probe._build_model(
        problem,
        _numbers(problem),
        source,
        candidates,
        flat_candidates,
        relations,
        6,
        True,
        [0, 0, 0],
    )
    # Preserve the production invariant that every incumbent candidate is in
    # domain, then force the three separate same-entry alternatives.
    model.Add(variables["choice"][(0, 0)] == 1)
    model.Add(variables["choice"][(1, 1)] == 1)
    model.Add(variables["choice"][(2, 1)] == 1)
    solver = probe.cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = SECONDS
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = SEED
    status = solver.Solve(model)
    assert solver.StatusName(status) == "INFEASIBLE"
    assert len(variables["key"]) == 3


def test_event_endpoint_and_horizon(ma) -> None:
    """Half-open endpoint is legal; horizon+1 is a hard API boundary."""

    problem = _one_way_problem()
    source = _flat([
        [0, 0, 0, 0, 4, 6, 1],
        [0, 0, 1, 0, 1, 4, 1],
    ])
    numbers = _numbers(problem)
    horizon = probe._model_horizon(numbers, source)
    assert horizon == 9
    engine = _engine(ma, problem, horizon, source)
    # The complete incumbent includes A, but the API must exclude only A.
    raw = list(engine.event_corner_candidates(0, 0, 0, 8))
    rows = [raw[index:index + 5] for index in range(0, len(raw), 5)]
    assert [0, 0, 0, 0, 0] in rows  # one-way sweep retained, no fixed VP filter

    at_endpoint = list(engine.event_corner_candidates(0, 0, 4, 8))
    endpoint_rows = [
        at_endpoint[index:index + 5]
        for index in range(0, len(at_endpoint), 5)
    ]
    assert [0, 0, 0, 0, 4] in endpoint_rows

    last_entry = horizon - numbers["processing"][0]
    assert list(engine.event_corner_candidates(0, 0, last_entry, 8))
    try:
        engine.event_corner_candidates(0, 0, last_entry + 1, 8)
    except Exception as exc:
        assert "release/horizon" in str(exc)
    else:  # pragma: no cover - hard API contract
        raise AssertionError("event_corner_candidates accepted horizon+1")


def test_two_bay_exact_z2_changes_selection(ma) -> None:
    """Exact floor-Z2 chooses the balancing bay, not the lexicographic bay."""

    square = [[[0, 0], [1, 0], [1, 1], [0, 1]]]
    problem = _problem([
        _block(0, 1, 10, square, preferences=(1, 1), workload=6),
        _block(0, 1, 10, square, preferences=(1, 1), workload=4),
        _block(0, 1, 10, square, preferences=(1, 1), workload=2),
    ], bay_count=2, width=2, weights=(0, 1, 0))
    source = _flat([
        [0, 0, 0, 0, 0, 1, 1],
        [0, 0, 1, 0, 0, 1, 1],
        [1, 0, 0, 0, 0, 1, 1],
    ])
    candidates = {
        0: [_candidate(0, bay=0, entry=0)],
        1: [
            probe.Candidate(1, 0, 0, 1, 0, 0),
            probe.Candidate(1, 1, 0, 1, 0, 0),
        ],
        2: [_candidate(2, bay=1, entry=0)],
    }
    fixed = _solve("fixed", ma, problem, source, candidates, 6)
    assert fixed["status"] == "OPTIMAL", fixed
    assert _checked_total(fixed) == 0
    assert fixed["cp_z2"] == 0
    assert fixed["selected_candidate_indices"] == [0, 1, 0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--solver", type=Path, default=ROOT / "solver")
    args = parser.parse_args()
    ma, _module_path, _utils_path = probe._load_solver(args.solver)
    tests = (
        test_one_way_fixed_two_flex_zero,
        test_transitive_three_chain,
        test_same_entry_cycle_rejected,
        test_event_endpoint_and_horizon,
        test_two_bay_exact_z2_changes_selection,
    )
    for test in tests:
        test(ma)
    print(f"flexible_repack synthetic E2E PASS ({len(tests)} tests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
