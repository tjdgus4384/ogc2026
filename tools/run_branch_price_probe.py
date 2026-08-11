#!/usr/bin/env python3
"""Fail-closed whole-bay branch-price mechanism probe (Slurm worker entrypoint)."""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import sys
import time
from fractions import Fraction
from pathlib import Path
from typing import Any

from solver.utils import check_feasibility
from tools.branch_price_check import (
    FEASIBLE, UNKNOWN, CanonicalPrices, RestrictedMaster, _make_plan, assemble_solution,
    canonical_reduced_cost, certify_official, extract_seed_bay_plans,
)
from tools.nfp_pricer import NEGATIVE_COLUMN, PriceRequest, price, reduced_cost


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonable(value: Any) -> Any:
    if isinstance(value, Fraction):
        return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return [jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    return value


def checker_evidence(cert: Any) -> dict[str, Any]:
    return {"status": cert.status, "reason": cert.reason, "result": jsonable(dict(cert.result))}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("problem")
    parser.add_argument("seeds", nargs="+", help="at least two seed solution JSON files")
    parser.add_argument("--time-budget", type=float, required=True)
    parser.add_argument("--beam-width", type=int, required=True)
    parser.add_argument("--max-states", type=int, required=True)
    parser.add_argument("--max-candidates", type=int, required=True)
    parser.add_argument("--max-root-rounds", type=int, required=True)
    parser.add_argument("--min-gain-fraction", type=float, required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--solution", required=True)
    args = parser.parse_args()
    if len(args.seeds) < 2:
        parser.error("at least two seed solution paths are required")
    if args.time_budget <= 0 or min(args.beam_width, args.max_states, args.max_candidates, args.max_root_rounds) <= 0 or not 0 <= args.min_gain_fraction < 1:
        parser.error("all bounds and time budget must be positive")
    return args
def certify_column(instance: dict[str, Any], plan: Any) -> Any:
    """Replay the single bay as a re-indexed official-checker subinstance."""
    member_index = {block_id: index for index, block_id in enumerate(plan.members)}
    blocks = []
    for block_id in plan.members:
        block = dict(instance["blocks"][block_id])
        if "bay_preferences" in block:
            block["bay_preferences"] = [block["bay_preferences"][plan.bay_id]]
        blocks.append(block)
    staged: dict[str, list[tuple[int, int, int, dict[str, Any]]]] = {}
    placements = {placement.block_id: placement for placement in plan.placements}
    for event in plan.events:
        placement = placements[event.block_id]
        op = {"type": event.phase, "block_id": member_index[event.block_id], "bay_id": 0}
        if event.phase == "ENTRY":
            op.update(x=placement.x, y=placement.y, orient_idx=placement.orient_idx)
        staged.setdefault(str(event.time), []).append(
            (0 if event.phase == "EXIT" else 1, event.ordinal, member_index[event.block_id], op)
        )
    operations = {
        time: [op for _, _, _, op in sorted(items)]
        for time, items in staged.items()
    }
    subinstance = dict(instance)
    subinstance["bays"] = [dict(instance["bays"][plan.bay_id])]
    subinstance["blocks"] = blocks
    return certify_official(subinstance, {"operations": operations}, check_feasibility)




def main() -> int:
    args = parse_args()
    started = time.monotonic()
    receipt_path = Path(args.receipt)
    receipt: dict[str, Any] = {
        "status": "FAILED", "argv": sys.argv, "config": vars(args), "elapsed_seconds": None,
        "kill_mechanism": True, "kill_reason": "fail-closed until the configured raw-gain gate passes",
        "adopt_mechanism": False, "lp_rounds": [], "pricing": [],
        "pricing_unknown_count": 0, "negative_column_count": 0,
    }
    try:
        problem_path = Path(args.problem)
        seed_paths = [Path(path) for path in args.seeds]
        source_paths = [
            Path(__file__), Path(__file__).with_name("branch_price_check.py"),
            Path(__file__).with_name("nfp_pricer.py"), Path(__file__).with_name("nfp_oracle.py"),
            Path(__file__).resolve().parents[1] / "solver" / "utils.py",
        ]
        receipt["hashes"] = {
            "problem": digest(problem_path), "official_checker": digest(source_paths[-1]),
            "source_modules": {str(path): digest(path) for path in source_paths},
            "seed_solutions": {str(path): digest(path) for path in seed_paths},
        }
        instance = json.loads(problem_path.read_text())
        target_members = math.ceil(0.6 * len(instance["blocks"]) / len(instance["bays"]))
        receipt["target_members_per_generated_column"] = target_members
        try:
            import gurobipy
            import shapely
            receipt["runtime_fingerprint"] = {
                "python_executable": sys.executable,
                "python_sha256": digest(Path(sys.executable)),
                "python_version": sys.version,
                "gurobi_version": ".".join(map(str, gurobipy.gurobi.version())),
                "shapely_version": shapely.__version__,
            }
        except Exception as exc:
            raise RuntimeError(f"runtime fingerprint failed: {type(exc).__name__}: {exc}") from exc
        seeds = [json.loads(path.read_text()) for path in seed_paths]
        seed_certs = [certify_official(instance, solution, check_feasibility) for solution in seeds]
        receipt["seed_checker"] = [checker_evidence(cert) for cert in seed_certs]
        if any(cert.status != FEASIBLE for cert in seed_certs):
            raise RuntimeError("seed certification failed or was UNKNOWN")
        seed_objectives = [cert.result.get("objective") for cert in seed_certs]
        if any(value is None for value in seed_objectives):
            raise RuntimeError("feasible seed lacks raw checker objective")
        best_seed_raw = min(seed_objectives)
        master = RestrictedMaster(instance, max_combinations=args.max_states)
        seed_signatures: set[str] = set()
        seed_placement_tuples: set[tuple[int, int, int, int, int, int]] = set()
        for solution in seeds:
            for plan in extract_seed_bay_plans(instance, solution, check_feasibility):
                seed_signatures.add(plan.plan_signature)
                seed_placement_tuples.update(
                    (placement.block_id, placement.orient_idx, placement.x, placement.y,
                     placement.entry, placement.exit)
                    for placement in plan.placements
                )
                master.add_column(plan)
        receipt["seed_plan_signatures"] = sorted(seed_signatures)
        for bay_id in range(len(instance["bays"])):
            empty = _make_plan(instance, bay_id, ())
            if certify_column(instance, empty).status != FEASIBLE:
                raise RuntimeError(f"empty bay {bay_id} failed certification")
            master.add_column(empty)
        generated_plans: dict[str, Any] = {}
        max_depth_by_bay = {bay_id: 0 for bay_id in range(len(instance["bays"]))}
        for root_round in range(args.max_root_rounds):
            duals = master.lp_duals()
            round_evidence = {
                "round": root_round, "status": duals.status, "reason": duals.reason,
                "evidence": list(duals.evidence),
                "requests": [
                    {
                        "bay_id": request.bay_id,
                        "block_credit": jsonable(request.block_credit),
                        "normalized_load_price": jsonable(request.normalized_load_price),
                        "bay_credit": jsonable(request.bay_credit),
                    }
                    for request in duals.requests
                ],
            }
            receipt["lp_rounds"].append(round_evidence)
            if duals.status != FEASIBLE:
                raise RuntimeError(f"lp_duals failed closed: {duals.status}: {duals.reason}")
            any_added = False
            for request in duals.requests:
                bounded: PriceRequest = dataclasses.replace(
                    request, deadline_monotonic=time.monotonic() + args.time_budget,
                    beam_width=args.beam_width, min_members=target_members,
                    max_states=args.max_states, max_candidates=args.max_candidates, seed=0,
                )
                priced = price(bounded)
                event: dict[str, Any] = {
                    "round": root_round, "bay_id": bounded.bay_id, "status": priced.status,
                    "reported_reduced_cost": jsonable(priced.reduced_cost),
                    "explored_states": priced.explored_states,
                    "novel_placement_count": priced.novel_placement_count,
                    "evidence": list(priced.evidence), "plan_signature": None, "member_count": 0,
                    "novel_tuple_count": 0, "independent_reduced_cost": None, "accepted": False,
                }
                evidence_map = dict(priced.evidence)
                event["max_member_count"] = int(evidence_map.get("max_member_count", 0))
                max_depth_by_bay[bounded.bay_id] = max(
                    max_depth_by_bay[bounded.bay_id], event["max_member_count"]
                )
                if priced.status == UNKNOWN:
                    receipt["pricing_unknown_count"] += 1
                    receipt["pricing"].append(event)
                    continue
                if priced.plan is not None:
                    independent = reduced_cost(priced.plan, bounded)
                    canonical = canonical_reduced_cost(
                        priced.plan,
                        CanonicalPrices(bounded.block_credit, bounded.normalized_load_price, bounded.bay_credit),
                    )
                    event.update(
                        plan_signature=priced.plan.plan_signature,
                        member_count=len(priced.plan.members),
                        novel_tuple_count=sum(
                            (placement.block_id, placement.orient_idx, placement.x, placement.y,
                             placement.entry, placement.exit) not in seed_placement_tuples
                            for placement in priced.plan.placements
                        ),
                        independent_reduced_cost=jsonable(independent),
                        canonical_reduced_cost=jsonable(canonical),
                    )
                    cert = certify_column(instance, priced.plan)
                    event["column_checker"] = checker_evidence(cert)
                    if priced.status == NEGATIVE_COLUMN:
                        receipt["negative_column_count"] += 1
                        if independent >= 0 or canonical != independent:
                            receipt["pricing"].append(event)
                            raise RuntimeError("negative column reduced-cost validation failed")
                        if cert.status != FEASIBLE:
                            receipt["pricing"].append(event)
                            raise RuntimeError("negative column lacks official checker certification evidence")
                        event["accepted"] = master.add_column(priced.plan)
                        if event["accepted"]:
                            generated_plans[priced.plan.plan_signature] = priced.plan
                        any_added = any_added or event["accepted"]
                elif priced.status == NEGATIVE_COLUMN:
                    receipt["pricing"].append(event)
                    raise RuntimeError("negative-column status without plan")
                receipt["pricing"].append(event)
            if not any_added:
                break
        result = master.solve()
        receipt["integer_master"] = {"status": result.status, "reason": result.reason,
                                      "objective": jsonable(result.objective), "z2": result.z2}
        if result.status != FEASIBLE:
            raise RuntimeError(f"integer master failed: {result.status}: {result.reason}")
        candidate = assemble_solution(result.plans)
        full_cert = certify_official(instance, candidate, check_feasibility)
        receipt["full_checker"] = checker_evidence(full_cert)
        receipt["selected_plan_signatures"] = [plan.plan_signature for plan in result.plans]
        if full_cert.status != FEASIBLE:
            raise RuntimeError("assembled full board is not checker-feasible")
        raw_objective = full_cert.result.get("objective")
        if raw_objective is None:
            raise RuntimeError("full checker returned no raw objective")
        if result.objective != Fraction(str(raw_objective)):
            raise RuntimeError(
                f"restricted-master/checker objective mismatch: {result.objective} != {raw_objective}"
            )
        receipt["best_seed_raw_objective"] = best_seed_raw
        receipt["candidate_raw_objective"] = raw_objective
        receipt["raw_gain"] = best_seed_raw - raw_objective
        Path(args.solution).write_text(json.dumps(candidate, sort_keys=True, separators=(",", ":")) + "\n")
        receipt["max_member_depth_by_bay"] = max_depth_by_bay
        receipt["depth_coverage_gate_met"] = all(
            depth >= target_members for depth in max_depth_by_bay.values()
        )
        selected_generated = [
            generated_plans[signature]
            for signature in receipt["selected_plan_signatures"]
            if signature in generated_plans
        ]
        receipt["selected_generated_plan_signatures"] = [
            plan.plan_signature for plan in selected_generated
        ]
        selected_generated_is_material = any(
            len(plan.members) >= target_members
            and sum(
                (placement.block_id, placement.orient_idx, placement.x, placement.y,
                 placement.entry, placement.exit) not in seed_placement_tuples
                for placement in plan.placements
            ) >= math.ceil(len(plan.members) / 2)
            for plan in selected_generated
        )
        receipt["selected_generated_is_material"] = selected_generated_is_material
        gain_fraction = (best_seed_raw - raw_objective) / best_seed_raw if best_seed_raw else 0.0
        receipt["raw_gain_fraction"] = gain_fraction
        receipt["adopt_mechanism"] = (
            gain_fraction >= args.min_gain_fraction and selected_generated_is_material
        )
        if receipt["adopt_mechanism"]:
            receipt["verdict"] = "ADOPT"
            receipt["adopt_reason"] = "configured gain gate passed with a selected material generated column"
            receipt["kill_mechanism"] = False
            receipt["kill_reason"] = ""
        elif receipt["depth_coverage_gate_met"]:
            receipt["verdict"] = "KILL"
            receipt["adopt_reason"] = "gain gate not reached"
            receipt["kill_mechanism"] = True
            receipt["kill_reason"] = "predeclared depth coverage reached without checker-feasible gain"
        else:
            receipt["verdict"] = "INCONCLUSIVE"
            receipt["adopt_reason"] = "bounded search did not establish the required mechanism coverage"
            receipt["kill_mechanism"] = False
            receipt["kill_reason"] = ""
        receipt["status"] = "OK"
        return 0
    except Exception as exc:
        receipt["error"] = f"{type(exc).__name__}: {exc}"
        return 1
    finally:
        receipt["elapsed_seconds"] = time.monotonic() - started
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(jsonable(receipt), sort_keys=True, indent=2) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
