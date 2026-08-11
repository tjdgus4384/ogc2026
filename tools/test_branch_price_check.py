import unittest

from fractions import Fraction
from pathlib import Path

from tools.branch_price_check import (
    BayPlanV1, EventV1, PlacementV1, RestrictedMaster, StructuralCutPool, UNKNOWN,
    assemble_solution, certify_official, deserialize_bay_plan, diagnose_structural_cuts,
    extract_seed_bay_plans, floor_z2, instance_sha256, official_checker_sha256,
    schema_sha256, serialize_bay_plan, serialize_structural_cut_pool,
)


def instance():
    return {
        "bays": [{"width": 10, "height": 10}, {"width": 10, "height": 10}],
        "blocks": [
            {"workload": 1, "due_date": 3, "bay_preferences": [3, 1]},
            {"workload": 2, "due_date": 3, "bay_preferences": [3, 1]},
        ],
        "weights": {"w1": 1, "w2": 1, "w3": 1},
    }


def plan(bay, block, entry=0, exit=4, cost=0, load=0):
    p = PlacementV1(block, 0, block, 0, entry, exit)
    return BayPlanV1(
        "bay-plan-v1", instance_sha256(instance()), official_checker_sha256(), bay, (block,), (p,),
        (EventV1(entry, "ENTRY", 0, block), EventV1(exit, "EXIT", 1, block)),
        Fraction(0), Fraction(0), Fraction(load), Fraction(load), Fraction(cost),
    )


def test_schema_and_plan_serialization_are_deterministic():
    assert schema_sha256() == schema_sha256()
    a = plan(0, 0)
    assert a.plan_signature == deserialize_bay_plan(serialize_bay_plan(a)).plan_signature


def test_overlong_stay_is_retained_in_certificate_data():
    a = plan(0, 0, entry=2, exit=200)
    assert a.placements[0].exit - a.placements[0].entry == 198


def test_same_time_merge_orders_exit_before_entry():
    board = assemble_solution((plan(0, 0, 0, 4), plan(1, 1, 4, 8)))
    assert [op["type"] for op in board["operations"]["4"]] == ["EXIT", "ENTRY"]
def test_tied_same_phase_subsequence_is_preserved_for_order_sensitive_checker():
    first = PlacementV1(0, 0, 0, 0, 0, 4)
    second = PlacementV1(1, 0, 1, 0, 0, 4)
    tied = BayPlanV1(
        "bay-plan-v1", instance_sha256(instance()), official_checker_sha256(), 0, (0, 1),
        (first, second),
        (
            EventV1(0, "ENTRY", 1, 0), EventV1(0, "ENTRY", 0, 1),
            EventV1(4, "EXIT", 1, 0), EventV1(4, "EXIT", 0, 1),
        ),
        Fraction(), Fraction(), Fraction(3), Fraction(3), Fraction(),
    )
    board = assemble_solution((tied,))
    assert [op["block_id"] for op in board["operations"]["0"]] == [1, 0]
    order_sensitive_checker = lambda _instance, sol: {
        "feasible": [op["block_id"] for op in sol["operations"]["0"]] == [1, 0]
    }
    assert certify_official(instance(), board, order_sensitive_checker).status == "FEASIBLE"
    assert certify_official(instance(), {
        "operations": {**board["operations"], "0": list(reversed(board["operations"]["0"]))}
    }, order_sensitive_checker).status == "INFEASIBLE"

def test_extraction_assembly_checker_roundtrip():
    solution = {
        "operations": {
            "0": [{"type": "ENTRY", "block_id": 0, "bay_id": 0, "x": 0, "y": 0, "orient_idx": 0}],
            "4": [{"type": "EXIT", "block_id": 0, "bay_id": 0}],
            "5": [{"type": "ENTRY", "block_id": 1, "bay_id": 1, "x": 1, "y": 0, "orient_idx": 0}],
            "9": [{"type": "EXIT", "block_id": 1, "bay_id": 1}],
        }
    }
    checker = lambda _instance, _solution: {"feasible": True, "stage": 5}
    columns = extract_seed_bay_plans(instance(), solution, checker)
    assert assemble_solution(columns) == solution
    assert certify_official(instance(), assemble_solution(columns), checker).status == "FEASIBLE"
def _two_block_plan(entry=0, exit=4):
    placements = (PlacementV1(0, 0, 0, 0, entry, exit), PlacementV1(1, 0, 1, 0, entry, exit))
    return BayPlanV1(
        "bay-plan-v1", instance_sha256(instance()), official_checker_sha256(), 0, (0, 1), placements,
        tuple(EventV1(time, phase, ordinal, placement.block_id)
              for placement in placements
              for time, phase, ordinal in ((placement.entry, "ENTRY", 0), (placement.exit, "EXIT", 1))),
        Fraction(), Fraction(), Fraction(3), Fraction(3), Fraction(),
    )


def test_placement_pair_cut_reuses_only_the_exact_placements():
    proposal = _two_block_plan()
    checker = lambda cut_instance, _solution: {
        "feasible": not (len(cut_instance["blocks"]) == 2
                          and cut_instance["blocks"][0]["workload"] == 1)
    }
    diagnosis = diagnose_structural_cuts(instance(), 0, proposal, checker)
    assert diagnosis.status == "INFEASIBLE"
    assert diagnosis.cuts[0].kind == "placement_pair"
    pool = StructuralCutPool(diagnosis.cuts)
    assert pool.excludes(proposal)
    assert not pool.excludes(_two_block_plan(entry=1, exit=5))
    reordered_events = tuple(
        EventV1(event.time, event.phase, 1 - event.block_id, event.block_id)
        for event in proposal.events
    )
    reordered = BayPlanV1(
        proposal.schema_version, proposal.instance_sha256, proposal.checker_sha256,
        proposal.bay_id, proposal.members, proposal.placements, reordered_events,
        proposal.tardiness, proposal.regret, proposal.raw_load,
        proposal.normalized_load, proposal.direct_cost,
    )
    assert not pool.excludes(reordered)


def test_feasible_proposal_emits_no_structural_cut():
    diagnosis = diagnose_structural_cuts(instance(), 0, _two_block_plan(), lambda *_: {"feasible": True})
    assert diagnosis.status == "FEASIBLE"
    assert diagnosis.cuts == ()


def test_event_shell_is_exact_and_pool_dedupes_and_serializes():
    problem = instance()
    problem["blocks"].append({"workload": 3, "due_date": 3, "bay_preferences": [3, 1]})
    placements = tuple(PlacementV1(block, 0, block, 0, 0, 4) for block in range(3))
    events = tuple(
        EventV1(time, phase, ordinal, placement.block_id)
        for placement in placements
        for time, phase, ordinal in ((placement.entry, "ENTRY", 0), (placement.exit, "EXIT", 1))
    )
    proposal = BayPlanV1(
        "bay-plan-v1", instance_sha256(problem), official_checker_sha256(), 0,
        (0, 1, 2), placements, events,
        Fraction(), Fraction(), Fraction(6), Fraction(6), Fraction(),
    )
    diagnosis = diagnose_structural_cuts(
        problem, 0, proposal,
        lambda cut_instance, _solution: {"feasible": len(cut_instance["blocks"]) == 2},
    )
    assert diagnosis.cuts[0].kind == "event_shell"
    pool = StructuralCutPool()
    assert pool.add(diagnosis.cuts[0])
    assert not pool.add(diagnosis.cuts[0])
    import json
    assert len(StructuralCutPool.from_dict(json.loads(serialize_structural_cut_pool(pool)))) == 1
    assert pool.excludes(proposal)
    reordered_events = tuple(
        EventV1(event.time, event.phase, 2 - event.block_id, event.block_id)
        for event in proposal.events
    )
    assert not pool.excludes(BayPlanV1(
        proposal.schema_version, proposal.instance_sha256, proposal.checker_sha256,
        proposal.bay_id, proposal.members, proposal.placements,
        reordered_events, proposal.tardiness, proposal.regret,
        proposal.raw_load, proposal.normalized_load, proposal.direct_cost,
    ))


def test_structural_diagnostic_checker_exception_emits_no_cut():
    diagnosis = diagnose_structural_cuts(
        instance(), 0, _two_block_plan(), lambda *_: (_ for _ in ()).throw(RuntimeError("down"))
    )
    assert diagnosis.status == UNKNOWN
    assert diagnosis.cuts == ()


def test_submit_harness_freezes_itself_and_counts_expanded_tasks():
    script = Path(__file__).with_name("submit_branch_price_probe.sh").read_text()
    assert '"tools/submit_branch_price_probe.sh"' in script
    assert "squeue -r -h" in script
    assert 'exec 9>"$ROOT/runs/.branch-price-submit.lock"' in script




def test_floor_z2_boundaries_are_exact():
    assert floor_z2((Fraction(0), Fraction(9, 10))) == 0
    assert floor_z2((Fraction(0), Fraction(1))) == 1
    assert floor_z2((Fraction(0), Fraction(11, 10))) == 1


def test_exact_cover_and_duplicate_dominance():
    master = RestrictedMaster(instance())
    assert master.add_column(plan(0, 0, cost=5, load=1))
    assert not master.add_column(plan(0, 0, cost=5, load=1))
    assert master.add_column(plan(1, 1, cost=2, load=2))
    result = master.solve()
    assert result.status == "FEASIBLE"
    assert {p.members[0] for p in result.plans} == {0, 1}


def test_fail_closed_checker_error():
    cert = certify_official(instance(), {"operations": {}}, checker=lambda *_: (_ for _ in ()).throw(RuntimeError("down")))
    assert cert.status == UNKNOWN


class BranchPriceCheckTest(unittest.TestCase):
    test_schema_and_plan_serialization_are_deterministic = staticmethod(test_schema_and_plan_serialization_are_deterministic)
    test_overlong_stay_is_retained_in_certificate_data = staticmethod(test_overlong_stay_is_retained_in_certificate_data)
    test_same_time_merge_orders_exit_before_entry = staticmethod(test_same_time_merge_orders_exit_before_entry)
    test_tied_same_phase_subsequence_is_preserved_for_order_sensitive_checker = staticmethod(test_tied_same_phase_subsequence_is_preserved_for_order_sensitive_checker)
    test_extraction_assembly_checker_roundtrip = staticmethod(test_extraction_assembly_checker_roundtrip)
    test_placement_pair_cut_reuses_only_the_exact_placements = staticmethod(test_placement_pair_cut_reuses_only_the_exact_placements)
    test_event_shell_is_exact_and_pool_dedupes_and_serializes = staticmethod(test_event_shell_is_exact_and_pool_dedupes_and_serializes)
    test_feasible_proposal_emits_no_structural_cut = staticmethod(test_feasible_proposal_emits_no_structural_cut)
    test_submit_harness_freezes_itself_and_counts_expanded_tasks = staticmethod(test_submit_harness_freezes_itself_and_counts_expanded_tasks)
    test_structural_diagnostic_checker_exception_emits_no_cut = staticmethod(test_structural_diagnostic_checker_exception_emits_no_cut)
    test_floor_z2_boundaries_are_exact = staticmethod(test_floor_z2_boundaries_are_exact)
    test_exact_cover_and_duplicate_dominance = staticmethod(test_exact_cover_and_duplicate_dominance)
    test_fail_closed_checker_error = staticmethod(test_fail_closed_checker_error)


if __name__ == "__main__":
    unittest.main()
