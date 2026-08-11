"""Focused contract tests for the fail-closed dynamic NFP pricer."""
from __future__ import annotations

from fractions import Fraction
import time
import unittest
from unittest.mock import patch

from tools.branch_price_check import Certification, FEASIBLE, INFEASIBLE, PlacementV1, _make_plan
from tools.nfp_pricer import (
    NEGATIVE_COLUMN,
    UNKNOWN,
    PriceRequest,
    _certified_plan,
    _candidate_placements,
    _event_geometry_ok,
    _timings,
    price,
    reduced_cost,
)


def rectangle(width: int = 1, height: int = 1) -> dict:
    return {"orientation": 0, "layers": [[[0, 0], [width, 0], [width, height], [0, height]]]}


def instance(blocks: int = 2) -> dict:
    return {
        "bays": [{"width": 5, "height": 5}],
        "weights": {"w1": 1, "w3": 1},
        "blocks": [{"release_time": 0, "due_date": 1, "processing_time": 1,
                    "workload": 1, "bay_preferences": [1], "shape": [rectangle()]}
                   for _ in range(blocks)],
    }


def request(**overrides) -> PriceRequest:
    values = dict(bay_id=0, block_credit=(Fraction(5), Fraction(1)),
                  normalized_load_price=Fraction(0), bay_credit=Fraction(0),
                  together_groups=(), apart_pairs=(), cutoff=Fraction(0),
                  deadline_monotonic=time.monotonic() + 10, seed=(), instance=instance())
    values.update(overrides)
    return PriceRequest(**values)


class NfpPricerTest(unittest.TestCase):
    def test_exact_reduced_cost_arithmetic(self):
        problem = instance(1)
        plan = _make_plan(problem, 0, (PlacementV1(0, 0, 0, 0, 0, 1),))
        req = request(block_credit=(Fraction(7, 3),), instance=problem,
                      normalized_load_price=Fraction(1, 3), bay_credit=Fraction(2, 3))
        self.assertEqual(reduced_cost(plan, req), plan.direct_cost + plan.normalized_load / 3 - 3)

    def test_together_and_apart_are_enforced(self):
        req = request(together_groups=((0, 1),), apart_pairs=((0, 1),))
        with patch("tools.nfp_pricer._certified_plan") as certify:
            self.assertEqual(price(req).status, UNKNOWN)
            certify.assert_not_called()

    def test_expired_deadline_is_unknown(self):
        self.assertEqual(price(request(deadline_monotonic=time.monotonic() - 1)).status, UNKNOWN)

    def test_geometry_and_checker_errors_fail_closed(self):
        with patch("tools.nfp_pricer.nfp_oracle.wall_contact_candidates", side_effect=RuntimeError("bad geometry")):
            self.assertEqual(price(request()).status, UNKNOWN)
        with patch("tools.nfp_pricer.certify_official", side_effect=RuntimeError("checker unavailable")):
            self.assertEqual(price(request()).status, UNKNOWN)

    def test_new_nfp_contact_is_not_restricted_by_seed(self):
        req = request(seed=((0, 0, 4, 4),))
        with patch("tools.nfp_pricer.certify_official", return_value=Certification(FEASIBLE, {"feasible": True})):
            result = price(req)
        self.assertEqual(result.status, NEGATIVE_COLUMN)
        self.assertGreater(result.novel_placement_count, 0)

    def test_directional_crane_fixture_rejects_asymmetric_sweep_contact(self):
        problem = instance(2)
        problem["bays"][0] = {"width": 12, "height": 12}
        problem["blocks"][0]["shape"] = [{"orientation": 0, "layers": [
            [[0, 0], [-3, 0], [-3, 2], [0, 2]],
            [[0, 0], [2, 0], [2, 2], [0, 2]],
        ]}]
        problem["blocks"][1]["shape"] = [{"orientation": 0, "layers": [
            [[0, 0], [2, 0], [2, 2], [0, 2]],
            [[6, 0], [8, 0], [8, 2], [6, 2]],
        ]}]
        proposals = _candidate_placements(
            request(instance=problem), 1, (PlacementV1(0, 0, 4, 4, 0, 1),))
        self.assertFalse(any((p.x, p.y) == (4, 4) and p.entry < 1 for p in proposals))
    def test_disjoint_stays_reuse_geometry(self):
        problem = instance(2)
        req = request(instance=problem)
        first = PlacementV1(0, 0, 0, 0, 0, 1)
        second = PlacementV1(1, 0, 0, 0, 1, 2)
        self.assertTrue(_event_geometry_ok(req, second, (first,)))

    def test_timing_alternatives_include_earliest_exit(self):
        problem = instance(1)
        problem["blocks"][0].update(release_time=3, due_date=20, processing_time=4)
        self.assertIn((3, 7), _timings(problem["blocks"][0], ()))

    def test_plan_event_trace_orders_exit_before_entry(self):
        problem = instance(2)
        plan = _make_plan(problem, 0, (
            PlacementV1(0, 0, 0, 0, 0, 2),
            PlacementV1(1, 0, 2, 0, 2, 3),
        ))
        self.assertEqual([(event.phase, event.block_id) for event in plan.events if event.time == 2],
                         [("EXIT", 0), ("ENTRY", 1)])

    def test_beam_reaches_multi_block_depth(self):
        problem = instance(6)
        req = request(instance=problem, block_credit=(Fraction(5),) * 6,
                      beam_width=1, max_states=100)
        calls = []
        def proposals(_request, block_id, placed, *_):
            return (PlacementV1(block_id, 0, 0, 0, block_id * 2, block_id * 2 + 1),), False
        def certified(current_request, placements):
            calls.append(len(placements))
            return _make_plan(current_request.instance, current_request.bay_id, placements)
        with patch("tools.nfp_pricer._candidate_placements_limited", side_effect=proposals), \
             patch("tools.nfp_pricer._certified_plan", side_effect=certified):
            price(req)
        self.assertGreaterEqual(max(calls), 4)
    def test_short_budget_beam_reaches_thirty_serial_members(self):
        problem = instance(30)
        req = request(instance=problem, block_credit=(Fraction(10),) * 30,
                      beam_width=1, unit_branch_width=1, placement_branch_width=1,
                      target_members=30, max_states=40)
        depths = []
        def proposals(_request, block_id, placed, *_):
            return (PlacementV1(block_id, 0, 0, 0, len(placed), len(placed) + 1),), False
        def certified(current_request, placements):
            depths.append(len(placements))
            return _make_plan(current_request.instance, 0, placements)
        with patch("tools.nfp_pricer._candidate_placements_limited", side_effect=proposals), \
             patch("tools.nfp_pricer._certified_plan", side_effect=certified):
            price(req)
        self.assertIn(30, depths)

    def test_nfp_contact_ignores_disjoint_history(self):
        prior = PlacementV1(0, 0, 0, 0, 0, 1)
        calls = []
        def contacts(moving, fixed, width, height, **_):
            calls.append(tuple(fixed))
            return ((0, 0),)
        problem = instance(2)
        with patch("tools.nfp_pricer.nfp_oracle.wall_contact_candidates", side_effect=contacts):
            _candidate_placements(request(instance=problem), 1, (prior,))
        self.assertTrue(calls)
        self.assertIn((), calls)

    def test_overlapping_stay_keeps_dynamic_nfp_blocker(self):
        prior = PlacementV1(0, 0, 0, 0, 0, 5)
        calls = []
        def contacts(moving, fixed, width, height, **_):
            calls.append(tuple(fixed))
            return ((2, 0),)
        problem = instance(2)
        with patch("tools.nfp_pricer.nfp_oracle.wall_contact_candidates", side_effect=contacts):
            _candidate_placements(request(instance=problem), 1, (prior,))
        self.assertTrue(any(fixed for fixed in calls))
    def test_depth_balanced_beam_reaches_campaign_scale_fraction(self):
        problem = instance(10)
        for block_id, block in enumerate(problem["blocks"]):
            block.update(release_time=block_id, due_date=block_id + 1, processing_time=1)
        req = request(
            instance=problem,
            block_credit=(Fraction(100),) * 10,
            beam_width=8,
            unit_branch_width=4,
            placement_branch_width=1,
            min_members=6,
            max_states=5000,
            max_candidates=100,
            deadline_monotonic=time.monotonic() + 20,
        )
        result = price(req)
        self.assertEqual(result.status, NEGATIVE_COLUMN)
        self.assertGreaterEqual(len(result.plan.members), 6)
        self.assertGreaterEqual(int(dict(result.evidence)["max_member_count"]), 6)
    def test_deterministic_output(self):
        with patch("tools.nfp_pricer.certify_official", return_value=Certification(FEASIBLE, {"feasible": True})):
            first, second = price(request()), price(request())
        self.assertEqual((first.status, first.reduced_cost, first.plan.plan_signature),
                         (second.status, second.reduced_cost, second.plan.plan_signature))

    def test_no_false_checker_feasible_claim(self):
        with patch("tools.nfp_pricer.certify_official", return_value=Certification(INFEASIBLE, {"feasible": False})):
            self.assertIsNone(_certified_plan(request(), ()))


if __name__ == "__main__":
    unittest.main()
