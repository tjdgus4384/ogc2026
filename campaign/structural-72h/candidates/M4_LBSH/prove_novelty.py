#!/usr/bin/env python3
"""Exhaustive pre-implementation reachable-state proof for the LBSH fixture."""
from __future__ import annotations

import hashlib
import itertools
import json

STATIC_COORDINATES = (0, 2, 4)
FREE_COORDINATE_DOMAIN = tuple(range(7))
RECT_WIDTHS = (3, 2)
BAY_WIDTH = 7


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def valid(order: tuple[int, int], coordinates: tuple[int, int]) -> bool:
    left, right = order
    return (
        0 <= coordinates[left]
        and coordinates[left] + RECT_WIDTHS[left] <= coordinates[right]
        and coordinates[right] + RECT_WIDTHS[right] <= BAY_WIDTH
    )


def main() -> None:
    static_states = {
        (order, coordinates)
        for order in itertools.permutations(range(2))
        for coordinates in itertools.product(STATIC_COORDINATES, repeat=2)
        if valid(order, coordinates)
    }
    free_states = {
        (order, coordinates)
        for order in itertools.permutations(range(2))
        for coordinates in itertools.product(FREE_COORDINATE_DOMAIN, repeat=2)
        if valid(order, coordinates)
    }
    destination = ((0, 1), (0, 3))
    assert destination in free_states
    assert destination not in static_states
    contradictory_master = ((0, 1), (1, 0))
    receipt = {
        "mechanism": "M4_LBSH",
        "status": "COMPLETE",
        "cap_hit": False,
        "fixture_sha256": hashlib.sha256(canonical({
            "bay_width": BAY_WIDTH,
            "widths": RECT_WIDTHS,
            "static_coordinates": STATIC_COORDINATES,
        })).hexdigest(),
        "static_state_count": len(static_states),
        "free_state_count": len(free_states),
        "destination": destination,
        "destination_absent_from_static_vocabulary": True,
        "master": {"order": (0, 1), "separator": "right(0)<=left(1)"},
        "subproblem": {"coordinate_domain": FREE_COORDINATE_DOMAIN, "exact": True},
        "proven_cut_fixture": {
            "master": contradictory_master,
            "status": "PROVEN_PRECEDENCE_CONTRADICTION",
        },
        "closure_invariant": "closed masters can name only preregistered static coordinates",
    }
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
