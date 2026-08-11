"""Fail-closed deterministic NFP root pricing for certified whole-bay columns."""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import time
from typing import Any, Mapping, Sequence

from tools import nfp_oracle
from tools.branch_price_check import (
    FEASIBLE, BayPlanV1, PlacementV1, _make_plan, as_fraction,
    assemble_solution, certify_official,
)

NEGATIVE_COLUMN = "NEGATIVE_COLUMN"
NO_NEGATIVE_PROVEN = "NO_NEGATIVE_PROVEN"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class PriceRequest:
    """Immutable inputs for an isolated root bay-pricing call.

    ``beam_width`` is an intentional heuristic bound.  Its use, candidate caps,
    and deadlines make absence results UNKNOWN rather than pricing proofs.
    """
    bay_id: int
    block_credit: tuple[Fraction, ...]
    normalized_load_price: Fraction
    bay_credit: Fraction
    together_groups: tuple[tuple[int, ...], ...]
    apart_pairs: tuple[tuple[int, int], ...]
    cutoff: Fraction
    deadline_monotonic: float
    seed: Any
    instance: Mapping[str, Any]
    beam_width: int = 64
    unit_branch_width: int = 8
    placement_branch_width: int = 2
    target_members: int = 0
    min_members: int = 1
    max_states: int = 10_000
    max_candidates: int = 10_000
    exhaustive: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "block_credit", tuple(as_fraction(value) for value in self.block_credit))
        object.__setattr__(self, "normalized_load_price", as_fraction(self.normalized_load_price))
        object.__setattr__(self, "bay_credit", as_fraction(self.bay_credit))
        object.__setattr__(self, "cutoff", as_fraction(self.cutoff))
        object.__setattr__(self, "together_groups", tuple(tuple(sorted(set(map(int, group)))) for group in self.together_groups))
        object.__setattr__(self, "apart_pairs", tuple(sorted(tuple(sorted(map(int, pair))) for pair in self.apart_pairs)))
        if self.bay_id < 0 or self.beam_width <= 0 or self.unit_branch_width <= 0 or self.placement_branch_width <= 0 or self.min_members < 0 or self.target_members < 0 or self.max_states <= 0 or self.max_candidates <= 0:
            raise ValueError("invalid pricing bounds")
        if len(self.block_credit) != len(self.instance.get("blocks", ())):
            raise ValueError("block_credit must have one value per instance block")
        if self.bay_id >= len(self.instance.get("bays", ())):
            raise ValueError("bay_id is outside instance bays")


@dataclass(frozen=True)
class PriceResult:
    status: str
    plan: BayPlanV1 | None
    reduced_cost: Fraction | None
    valid_lower_bound: Fraction | None
    explored_states: int
    novel_placement_count: int
    evidence: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.status not in (NEGATIVE_COLUMN, NO_NEGATIVE_PROVEN, UNKNOWN):
            raise ValueError("invalid pricing status")
        if self.reduced_cost is not None:
            object.__setattr__(self, "reduced_cost", as_fraction(self.reduced_cost))
        if self.valid_lower_bound is not None:
            object.__setattr__(self, "valid_lower_bound", as_fraction(self.valid_lower_bound))
        object.__setattr__(self, "evidence", tuple(sorted((str(key), str(value)) for key, value in self.evidence)))


def reduced_cost(plan: BayPlanV1, request: PriceRequest) -> Fraction:
    return (as_fraction(plan.direct_cost) + request.normalized_load_price * as_fraction(plan.normalized_load)
            - sum((request.block_credit[block_id] for block_id in plan.members), Fraction()) - request.bay_credit)


def _expired(request: PriceRequest) -> bool:
    return time.monotonic() >= request.deadline_monotonic


def _solo_optimism(request: PriceRequest, block_id: int) -> Fraction:
    bay = request.instance["bays"][request.bay_id]
    average_area = sum((as_fraction(item["width"]) * as_fraction(item["height"])
                        for item in request.instance["bays"]), Fraction()) / len(request.instance["bays"])
    block = request.instance["blocks"][block_id]
    preferences = block.get("bay_preferences", ())
    regret = (as_fraction(max(preferences)) - as_fraction(preferences[request.bay_id])
              if preferences else Fraction())
    load = as_fraction(block["workload"]) * average_area / (as_fraction(bay["width"]) * as_fraction(bay["height"]))
    return as_fraction(request.instance.get("weights", {}).get("w3", 1)) * regret + request.normalized_load_price * load - request.block_credit[block_id]


def _units(request: PriceRequest) -> tuple[tuple[int, ...], ...]:
    n = len(request.block_credit)
    parent = list(range(n))
    def find(item: int) -> int:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item
    def join(left: int, right: int) -> None:
        left, right = find(left), find(right)
        if left != right:
            parent[right] = left
    for group in request.together_groups:
        if not group or any(item < 0 or item >= n for item in group):
            raise ValueError("invalid together group")
        for item in group[1:]:
            join(group[0], item)
    components: dict[int, list[int]] = {}
    for item in range(n):
        components.setdefault(find(item), []).append(item)
    return tuple(sorted((tuple(items) for items in components.values()),
                        key=lambda unit: (sum((_solo_optimism(request, item) for item in unit), Fraction()), unit)))


def _allowed(members: frozenset[int], unit: tuple[int, ...], request: PriceRequest) -> bool:
    proposed = members | frozenset(unit)
    return not any(left in proposed and right in proposed for left, right in request.apart_pairs)


def _seed_signatures(seed: Any) -> set[tuple[Any, ...]]:
    signatures: set[tuple[Any, ...]] = set()
    for item in seed if isinstance(seed, (tuple, list, set, frozenset)) else ():
        if isinstance(item, PlacementV1):
            signatures.add((item.block_id, item.orient_idx, item.x, item.y))
        elif isinstance(item, (tuple, list)) and len(item) >= 4:
            signatures.add(tuple(item[:4]))
        elif isinstance(item, Mapping):
            try:
                signatures.add((item["block_id"], item.get("orient_idx", 0), item["x"], item["y"]))
            except (KeyError, TypeError):
                pass
    return signatures


def _timings(block: Mapping[str, Any], placed: Sequence[PlacementV1]) -> tuple[tuple[int, int], ...]:
    release, due, processing = int(block["release_time"]), int(block["due_date"]), int(block["processing_time"])
    boundaries = {placement.entry for placement in placed} | {placement.exit for placement in placed}
    entries = {release, due - processing} | {time for time in boundaries if time >= release}
    # Explicit serial fallback is essential to deep whole-bay columns.
    entries.add(max([release, *(placement.exit for placement in placed)]))
    result = set()
    for entry in entries:
        if entry < release:
            continue
        exits = {entry + processing, due} | {time for time in boundaries if time >= entry + processing}
        result.update((entry, exit_time) for exit_time in exits if exit_time >= entry + processing)
    return tuple(sorted(result))


def _event_key(placement: PlacementV1, phase: str) -> tuple[int, int, int]:
    """Ordering used by generated plans: exits, then entries, then block id."""
    return (
        placement.exit if phase == "EXIT" else placement.entry,
        0 if phase == "EXIT" else 1,
        placement.block_id,
    )


def _residents_before(placements: Sequence[PlacementV1], moving: PlacementV1,
                      phase: str) -> tuple[PlacementV1, ...]:
    event = _event_key(moving, phase)
    return tuple(
        placement
        for placement in placements
        if placement.block_id != moving.block_id
        and _event_key(placement, "ENTRY") < event <= _event_key(placement, "EXIT")
    )


@dataclass
class _SearchCache:
    request: PriceRequest
    orientations: dict[tuple[int, int], nfp_oracle.OrientedBlock]
    contacts: dict[tuple[int, int, tuple[tuple[int, int, int, int, int, int], ...]], tuple[tuple[int, int], ...]]

    def orientation(self, block_id: int, orient_idx: int) -> nfp_oracle.OrientedBlock:
        key = (block_id, orient_idx)
        if key not in self.orientations:
            self.orientations[key] = nfp_oracle.OrientedBlock.from_orientation(
                self.request.instance["blocks"][block_id]["shape"][orient_idx])
        return self.orientations[key]


def _overlaps(left: PlacementV1, right: PlacementV1) -> bool:
    return left.entry < right.exit and right.entry < left.exit


def _position_proposals(cache: _SearchCache, block_id: int, orient_idx: int,
                        timing: tuple[int, int], placed: Sequence[PlacementV1]) -> tuple[tuple[tuple[int, int], ...], bool]:
    """Use wall-only proposals for serial stays and NFP contacts for overlaps."""
    request = cache.request
    bay = request.instance["bays"][request.bay_id]
    moving = cache.orientation(block_id, orient_idx)
    probe = PlacementV1(block_id, orient_idx, 0, 0, *timing)
    overlapping = tuple(item for item in placed if _overlaps(item, probe))
    fixed_key = tuple((item.block_id, item.orient_idx, item.x, item.y, item.entry, item.exit)
                      for item in overlapping)
    key = (block_id, orient_idx, fixed_key)
    if key not in cache.contacts:
        fixed = tuple(nfp_oracle.PlacedBlock(cache.orientation(item.block_id, item.orient_idx), item.x, item.y)
                      for item in overlapping)
        cache.contacts[key] = nfp_oracle.wall_contact_candidates(
            moving, fixed, int(bay["width"]), int(bay["height"]), max_candidates=request.max_candidates)
    positions = cache.contacts[key]
    return positions[:request.max_candidates], len(positions) > request.max_candidates


def _candidate_geometry_ok(cache: _SearchCache, candidate: PlacementV1,
                           placed: Sequence[PlacementV1]) -> bool:
    """Validate every affected event using the exact generated-plan event order."""
    request = cache.request
    bay = request.instance["bays"][request.bay_id]
    width, height = int(bay["width"]), int(bay["height"])
    all_placements = (*placed, candidate)
    moving = cache.orientation(candidate.block_id, candidate.orient_idx)
    if not nfp_oracle.within_bay(moving, (candidate.x, candidate.y), width, height):
        return False

    # Checking all events is deliberately simple and fail-closed. Existing-only
    # interactions were already accepted, but the candidate can become resident
    # during any of their entry/exit sweeps. Equal-time ties matter: generated
    # plans execute exits, then entries, then ascending block id.
    for operation in all_placements:
        operation_block = cache.orientation(operation.block_id, operation.orient_idx)
        operation_position = (operation.x, operation.y)
        for phase in ("ENTRY", "EXIT"):
            residents = _residents_before(all_placements, operation, phase)
            fixed = tuple(
                nfp_oracle.PlacedBlock(
                    cache.orientation(item.block_id, item.orient_idx), item.x, item.y
                )
                for item in residents
            )
            if any(
                nfp_oracle.contains_reference(
                    nfp_oracle.forbidden_same_layer(operation_block, item),
                    operation_position,
                )
                for item in fixed
            ):
                return False
            sweep_forbidden = (
                nfp_oracle.forbidden_entry_sweep
                if phase == "ENTRY"
                else nfp_oracle.forbidden_exit_sweep
            )
            if any(
                nfp_oracle.contains_reference(
                    sweep_forbidden(operation_block, item), operation_position
                )
                for item in fixed
            ):
                return False
    return True
def _event_geometry_ok(request: PriceRequest, candidate: PlacementV1,
                       placed: Sequence[PlacementV1]) -> bool:
    """Compatibility wrapper for focused geometry tests."""
    return _candidate_geometry_ok(_SearchCache(request, {}, {}), candidate, placed)


def _candidate_placements_limited(request: PriceRequest, block_id: int,
                                  placed: tuple[PlacementV1, ...],
                                  cache: _SearchCache | None = None) -> tuple[tuple[PlacementV1, ...], bool]:
    cache = cache or _SearchCache(request, {}, {})
    block = request.instance["blocks"][block_id]
    proposals: list[PlacementV1] = []
    capped = False
    timings = _timings(block, placed)
    if len(timings) > request.max_candidates:
        timings, capped = timings[:request.max_candidates], True
    for orient_idx in range(len(block["shape"])):
        for timing in timings:
            if _expired(request):
                return tuple(sorted(set(proposals), key=lambda item: (item.entry, item.exit, item.orient_idx, item.x, item.y))), True
            positions, position_cap = _position_proposals(cache, block_id, orient_idx, timing, placed)
            capped |= position_cap
            for x, y in positions:
                candidate = PlacementV1(block_id, orient_idx, x, y, *timing)
                if _candidate_geometry_ok(cache, candidate, placed):
                    proposals.append(candidate)
                    if len(proposals) >= request.max_candidates:
                        return tuple(sorted(set(proposals), key=lambda item: (item.entry, item.exit, item.orient_idx, item.x, item.y))), True
    return tuple(sorted(set(proposals), key=lambda item: (item.entry, item.exit, item.orient_idx, item.x, item.y))), capped


def _candidate_placements(request: PriceRequest, block_id: int,
                          placed: tuple[PlacementV1, ...]) -> tuple[PlacementV1, ...]:
    return _candidate_placements_limited(request, block_id, placed)[0]


def _certified_plan(request: PriceRequest, placements: tuple[PlacementV1, ...]) -> BayPlanV1 | None:
    try:
        plan = _make_plan(request.instance, request.bay_id, placements)
        recomputed = _make_plan(request.instance, request.bay_id, plan.placements)
        if (plan.direct_cost, plan.normalized_load, plan.members, plan.events) != (recomputed.direct_cost, recomputed.normalized_load, recomputed.members, recomputed.events):
            return None
        sub_blocks = []
        for block_id in plan.members:
            block = dict(request.instance["blocks"][block_id])
            if "bay_preferences" in block:
                block["bay_preferences"] = [block["bay_preferences"][request.bay_id]]
            sub_blocks.append(block)
        sub_instance = dict(request.instance)
        sub_instance["bays"] = [dict(request.instance["bays"][request.bay_id])]
        sub_instance["blocks"] = sub_blocks
        remapped = tuple(PlacementV1(index, placement.orient_idx, placement.x, placement.y, placement.entry, placement.exit)
                         for index, placement in enumerate(plan.placements))
        if certify_official(sub_instance, assemble_solution((_make_plan(sub_instance, 0, remapped),))).status != FEASIBLE:
            return None
        return plan
    except Exception:
        return None


def _label_score(request: PriceRequest, placements: tuple[PlacementV1, ...], remaining: Sequence[tuple[int, ...]]) -> Fraction:
    """Exact partial reduced cost plus explicitly heuristic solo optimism."""
    plan = _make_plan(request.instance, request.bay_id, placements)
    heuristic = sum((min(Fraction(), sum((_solo_optimism(request, item) for item in unit), Fraction()))
                     for unit in remaining), Fraction())
    return reduced_cost(plan, request) + heuristic


def price(request: PriceRequest) -> PriceResult:
    if _expired(request):
        return PriceResult(UNKNOWN, None, None, None, 0, 0, (("reason", "deadline before search"),))
    try:
        units, seed = _units(request), _seed_signatures(request.seed)
        target_members = request.target_members or max(
            request.min_members,
            (3 * len(request.instance["blocks"]) + 5 * len(request.instance["bays"]) - 1)
            // (5 * len(request.instance["bays"])),
        )
        target_members = min(target_members, len(request.instance["blocks"]))
        cache = _SearchCache(request, {}, {})
        labels: list[tuple[frozenset[int], tuple[PlacementV1, ...], int]] = [(frozenset(), (), 0)]
        best: tuple[Fraction, BayPlanV1] | None = None
        explored = novel = max_members = 0
        incomplete = True  # Beam/timing proposal bounds never constitute a proof.
        while labels and not _expired(request) and explored < request.max_states:
            next_labels: list[tuple[frozenset[int], tuple[PlacementV1, ...], int]] = []
            for members, placements, start in labels:
                if _expired(request) or explored >= request.max_states:
                    break
                stop = min(len(units), start + request.unit_branch_width)
                for unit_index in range(start, stop):
                    if _expired(request) or explored >= request.max_states:
                        break
                    if not _allowed(members, units[unit_index], request):
                        continue
                    extensions = [placements]
                    for block_id in units[unit_index]:
                        expanded: list[tuple[PlacementV1, ...]] = []
                        for partial in extensions:
                            if _expired(request):
                                break
                            choices, capped = _candidate_placements_limited(request, block_id, partial, cache)
                            incomplete |= capped
                            expanded.extend(
                                partial + (choice,)
                                for choice in choices[:request.placement_branch_width]
                            )
                        extensions = sorted(
                            expanded,
                            key=lambda extension: tuple(
                                (item.block_id, item.entry, item.exit, item.orient_idx, item.x, item.y)
                                for item in extension
                            ),
                        )[:request.beam_width]
                        if not extensions or _expired(request):
                            break
                    for extension in extensions:
                        if explored >= request.max_states or _expired(request):
                            break
                        explored += 1
                        novel += sum(
                            (item.block_id, item.orient_idx, item.x, item.y) not in seed
                            for item in extension[len(placements):]
                        )
                        max_members = max(max_members, len(extension))
                        if len(extension) == target_members:
                            certified = _certified_plan(request, extension)
                            if certified is not None:
                                cost = reduced_cost(certified, request)
                                if cost < request.cutoff and (best is None or (cost, certified.plan_signature) < (best[0], best[1].plan_signature)):
                                    best = (cost, certified)
                        next_labels.append((members | frozenset(units[unit_index]), extension, unit_index + 1))
            next_labels.sort(key=lambda label: (_label_score(request, label[1], units[label[2]:]),
                                                tuple((item.block_id, item.entry, item.exit, item.orient_idx, item.x, item.y) for item in label[1])))
            if len(next_labels) > request.beam_width:
                incomplete = True
            labels = next_labels[:request.beam_width]
        if best is not None:
            return PriceResult(NEGATIVE_COLUMN, best[1], best[0], None, explored, novel,
                               (("certified", "official checker"), ("max_member_count", str(max_members)),
                                ("root", "empty"), ("search", "depth-balanced bounded beam")))
        return PriceResult(UNKNOWN, None, None, None, explored, novel,
                           (("max_member_count", str(max_members)),
                            ("reason", "no negative column found; bounded beam/timing search is not a proof")))
    except Exception as exc:
        return PriceResult(UNKNOWN, None, None, None, 0, 0, (("reason", f"fail-closed: {type(exc).__name__}"),))


price_request = price
