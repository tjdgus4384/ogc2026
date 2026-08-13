"""Regression tests for the official flexible-duration timing model."""

from tools.flexible_time_closure import _emit, _pair_relations, _records, _solve

import utils


def _block(release, processing, due, layers):
    return {
        "release_time": release,
        "processing_time": processing,
        "due_date": due,
        "workload": 1,
        "bay_preferences": [1],
        "shape": [{"orientation": 0, "layers": layers}],
    }


def _one_way_instance():
    # Block 1 is tall: its narrow layer 0 clears block 0, while its wide upper
    # layer obstructs block 0's vertical path.  Therefore block 1 must be the
    # inner stay and block 0 the outer/wrapper.
    short = [[[0, 0], [2, 0], [2, 2], [0, 2]]]
    tall = [
        [[0, 0], [2, 0], [2, 2], [0, 2]],
        [[0, 0], [5, 0], [5, 2], [0, 2]],
    ]
    return {
        "bays": [{"width": 10, "height": 10}],
        "blocks": [
            _block(0, 2, 4, short),
            _block(1, 3, 4, tall),
        ],
        "weights": {"w1": 1, "w2": 0, "w3": 0},
    }


def _nested_solution():
    return {
        "operations": {
            "0": [{"type": "ENTRY", "block_id": 0, "bay_id": 0,
                   "x": 3, "y": 0, "orient_idx": 0}],
            "1": [{"type": "ENTRY", "block_id": 1, "bay_id": 0,
                   "x": 0, "y": 0, "orient_idx": 0}],
            "4": [
                {"type": "EXIT", "block_id": 1, "bay_id": 0},
                {"type": "EXIT", "block_id": 0, "bay_id": 0},
            ],
        }
    }


def test_one_way_direction_and_flexible_duration_gain():
    problem = _one_way_instance()
    incumbent = _nested_solution()
    assert utils.check_feasibility(problem, incumbent)["feasible"]
    recs = _records(problem, incumbent)
    disjoint, nested = _pair_relations(problem, recs)
    assert disjoint == []
    assert nested == [(0, 1, 0)]  # outer short block, inner tall obstructer

    fixed, fixed_solution = _solve(
        problem, recs, disjoint, nested, False, 3.0, 1, 2)
    flex, flex_solution = _solve(
        problem, recs, disjoint, nested, True, 3.0, 1, 2)
    assert fixed["status"] == "OPTIMAL"
    assert flex["status"] == "OPTIMAL"
    assert fixed["cp_z1"] == 1
    assert flex["cp_z1"] == 0
    assert fixed_solution is not None and flex_solution is not None
    assert utils.check_feasibility(problem, fixed_solution)["feasible"]
    assert utils.check_feasibility(problem, flex_solution)["feasible"]
    assert flex["extended_blocks"] == 1


def test_reverse_entry_key_emits_inner_exit_first():
    recs = [
        {"bay": 0, "ori": 0, "x": 3, "y": 0, "e": 0, "f": 4, "rank": 0},
        {"bay": 0, "ori": 0, "x": 0, "y": 0, "e": 1, "f": 4, "rank": 0},
    ]
    emitted = _emit(recs, [0, 1], [4, 4], [0, 3])
    assert [op["block_id"] for op in emitted["operations"]["4"]] == [1, 0]
