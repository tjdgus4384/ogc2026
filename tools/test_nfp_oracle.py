"""Focused safety tests for the conservative NFP pricing oracle.

Run from the repository root with a test runner that can import ``solver``.  The
assertions deliberately compare every oracle-accepted integer position with the
current official checker primitives; false negatives are permitted by the oracle,
false positives are not.
"""
from __future__ import annotations

import unittest

from nfp_oracle import (
    OrientedBlock,
    PlacedBlock,
    contains_reference,
    feasible_for_entry,
    forbidden_entry_sweep,
    forbidden_exit_sweep,
    forbidden_same_layer,
    wall_contact_candidates,
)
from solver.utils import Bay, Block, check_collisions, check_entry, check_exit


def orientation(layers, identifier=0):
    return {"orientation": identifier, "layers": layers}


def block_data(*orientations):
    return {"shape": list(orientations)}


def official_block(identifier, data, x, y, orient_idx=0):
    return Block(identifier, data, x=x, y=y, orient_idx=orient_idx)


class NfpOracleSafetyTest(unittest.TestCase):
    width = height = 12

    def test_invalid_self_intersection_is_rejected_not_repaired(self):
        with self.assertRaises(ValueError):
            OrientedBlock.from_orientation(orientation([
                [[0, 0], [2, 2], [0, 2], [2, 0]],
            ]))

    def assert_entry_acceptance_matches_checker(self, moving_data, moving_orient, fixed_data,
                                               fixed_orient, fixed_position):
        moving = OrientedBlock.from_orientation(moving_data["shape"][moving_orient])
        fixed = PlacedBlock(OrientedBlock.from_orientation(fixed_data["shape"][fixed_orient]), *fixed_position)
        bay = Bay(self.width, self.height)
        fixed_official = official_block(1, fixed_data, *fixed_position, fixed_orient)
        for x in range(self.width + 1):
            for y in range(self.height + 1):
                if not feasible_for_entry(moving, (x, y), [fixed], self.width, self.height):
                    continue
                candidate = official_block(0, moving_data, x, y, moving_orient)
                self.assertFalse(check_entry(bay, [fixed_official], candidate, fast=True), (x, y))
                self.assertFalse(check_exit(bay, [fixed_official, candidate], candidate, fast=True), (x, y))
                self.assertFalse(check_collisions(bay, [fixed_official, candidate]), (x, y))
    def assert_checker_accepts(self, moving_data, fixed_data, position):
        """Differential assertion for official zero-area contact semantics."""
        bay = Bay(self.width, self.height)
        fixed = official_block(1, fixed_data, 4, 4)
        candidate = official_block(0, moving_data, *position)
        self.assertFalse(check_collisions(bay, [fixed, candidate]), position)
        self.assertFalse(check_entry(bay, [fixed], candidate, fast=True), position)
        self.assertFalse(check_exit(bay, [fixed, candidate], candidate, fast=True), position)


    def test_reference_point_and_orientations_never_accept_checker_collision(self):
        # The first orientation has reference (3, 2), not the bounding-box corner.
        moving_data = block_data(
            orientation([[[3, 2], [6, 2], [6, 3], [3, 3]]], 7),
            orientation([[[9, 4], [9, 7], [8, 7], [8, 4]]], 8),
        )
        fixed_data = block_data(orientation([[[1, 1], [4, 1], [4, 4], [1, 4]]]))
        self.assert_entry_acceptance_matches_checker(moving_data, 0, fixed_data, 0, (4, 4))
        self.assert_entry_acceptance_matches_checker(moving_data, 1, fixed_data, 0, (4, 4))
    def test_boundary_contacts_match_checker_and_integer_neighbours(self):
        square = block_data(orientation([[[0, 0], [2, 0], [2, 2], [0, 2]]]))
        moving = OrientedBlock.from_orientation(square["shape"][0])
        fixed = PlacedBlock(OrientedBlock.from_orientation(square["shape"][0]), 4, 4)
        forbidden = forbidden_same_layer(moving, fixed)

        # A shared edge (including collinear segment overlap) and a shared vertex
        # are checker-feasible because every intersection has zero area.
        for contact in ((6, 4), (6, 6), (4, 6)):
            self.assertFalse(contains_reference(forbidden, contact), contact)
            self.assertTrue(feasible_for_entry(moving, contact, [fixed], self.width, self.height))
            self.assert_checker_accepts(square, square, contact)

        # The immediate integer neighbour and an epsilon inside the NFP overlap.
        self.assertTrue(contains_reference(forbidden, (5, 4)))
        self.assertTrue(contains_reference(forbidden, (5.999, 4)))
        self.assertFalse(contains_reference(forbidden, (6.0, 4)))


    def test_directional_sweeps_cover_same_day_entry_and_exit_order(self):
        # Moving layer 0 meets fixed layer 1 only during vertical travel.
        moving_data = block_data(orientation([
            [[0, 0], [2, 0], [2, 2], [0, 2]],
            [[6, 0], [8, 0], [8, 2], [6, 2]],
        ]))
        fixed_data = block_data(orientation([
            [[0, 0], [-3, 0], [-3, 2], [0, 2]],
            [[0, 0], [2, 0], [2, 2], [0, 2]],
        ]))
        moving = OrientedBlock.from_orientation(moving_data["shape"][0])
        fixed = PlacedBlock(OrientedBlock.from_orientation(fixed_data["shape"][0]), 4, 4)
        # Both directional APIs intentionally use current checker j >= k semantics.
        self.assertTrue(contains_reference(forbidden_entry_sweep(moving, fixed), (4, 4)))
        self.assertTrue(contains_reference(forbidden_exit_sweep(moving, fixed), (4, 4)))
        self.assert_entry_acceptance_matches_checker(moving_data, 0, fixed_data, 0, (4, 4))
    def test_reversed_winding_preserves_checker_safe_acceptance(self):
        moving_data = block_data(orientation([[[0, 0], [2, 0], [2, 3], [0, 3]]]))
        # The fixed square has the same footprint but clockwise winding.
        fixed_data = block_data(orientation([[[0, 0], [0, 3], [2, 3], [2, 0]]]))
        self.assert_entry_acceptance_matches_checker(moving_data, 0, fixed_data, 0, (4, 4))

    def test_nonconvex_nfp_never_accepts_same_layer_overlap(self):
        # L-shaped moving footprint exercises triangulated non-convex decomposition.
        moving_data = block_data(orientation([[[0, 0], [4, 0], [4, 1], [1, 1], [1, 4], [0, 4]]]))
        fixed_data = block_data(orientation([[[0, 0], [2, 0], [2, 2], [0, 2]]]))
        moving = OrientedBlock.from_orientation(moving_data["shape"][0])
        fixed = PlacedBlock(OrientedBlock.from_orientation(fixed_data["shape"][0]), 5, 5)
        region = forbidden_same_layer(moving, fixed)
        bay = Bay(self.width, self.height)
        fixed_official = official_block(1, fixed_data, 5, 5)
        for x in range(self.width + 1):
            for y in range(self.height + 1):
                if contains_reference(region, (x, y)):
                    continue
                candidate = official_block(0, moving_data, x, y)
                if not bay.contains_block(candidate):
                    continue
                self.assertFalse(check_collisions(bay, [fixed_official, candidate]), (x, y))
        self.assert_entry_acceptance_matches_checker(moving_data, 0, fixed_data, 0, (5, 5))

    def test_wall_and_contact_candidates_are_independent_and_deterministic(self):
        moving = OrientedBlock.from_orientation(orientation([[[3, 2], [5, 2], [5, 3], [3, 3]]]))
        blocker = PlacedBlock(OrientedBlock.from_orientation(
            orientation([[[0, 0], [2, 0], [2, 2], [0, 2]]])), 5, 5)
        first = wall_contact_candidates(moving, [blocker], self.width, self.height)
        second = wall_contact_candidates(moving, [blocker], self.width, self.height)
        self.assertEqual(first, second)
        self.assertEqual(first, tuple(sorted(first)))
        # The raw first vertex is the official reference, so its wall position is (0, 0).
        self.assertIn((0, 0), first)
        # Contact positions derive from the blocker/NFP, not a supplied incumbent position.
        self.assertIn((3, 4), first)
        self.assertFalse(contains_reference(forbidden_entry_sweep(moving, blocker), (3, 4)))
        with self.assertRaisesRegex(ValueError, "exceeds max_candidates"):
            wall_contact_candidates(moving, [blocker], self.width, self.height, max_candidates=1)


if __name__ == "__main__":
    unittest.main()
