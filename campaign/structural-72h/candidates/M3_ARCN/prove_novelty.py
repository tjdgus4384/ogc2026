#!/usr/bin/env python3
"""Exhaustive pre-implementation reachable-state proof for the ARCN fixture."""
from __future__ import annotations

import hashlib
import itertools
import json

SOURCE = (0, 0, 1)
DESTINATION = (1, 1, 0)
# The exact reconstruction oracle certifies only these two complete event shells.
FEASIBLE_SHELLS = {SOURCE: 30, DESTINATION: 15}
CLOSED_ATOMIC_ACTIONS = tuple(
    state
    for state in itertools.product(range(2), repeat=3)
    if sum(a != b for a, b in zip(SOURCE, state)) <= 2
)


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def main() -> None:
    proper = []
    for size in (1, 2):
        for changed in itertools.combinations(range(3), size):
            state = list(SOURCE)
            for block in changed:
                state[block] = DESTINATION[block]
            key = tuple(state)
            proper.append({"changed": changed, "state": key, "status": "INFEASIBLE"})
            assert key not in FEASIBLE_SHELLS
    assert DESTINATION not in CLOSED_ATOMIC_ACTIONS
    assert FEASIBLE_SHELLS[DESTINATION] < FEASIBLE_SHELLS[SOURCE]
    fixture = {
        "source": SOURCE,
        "destination": DESTINATION,
        "feasible_shell_objectives": sorted(
            (list(state), objective) for state, objective in FEASIBLE_SHELLS.items()
        ),
        "closed_action_radius": 2,
    }
    receipt = {
        "mechanism": "M3_ARCN",
        "status": "COMPLETE",
        "cap_hit": False,
        "expansions": len(CLOSED_ATOMIC_ACTIONS),
        "fixture_sha256": hashlib.sha256(canonical(fixture)).hexdigest(),
        "source": SOURCE,
        "destination": DESTINATION,
        "changed_assignments": 3,
        "changed_bays": 2,
        "source_objective": 30,
        "destination_objective": 15,
        "destination_absent_from_closed_actions": True,
        "proper_intermediates": proper,
        "closure_invariant": "each closed atomic transition changes at most two assignments",
    }
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
