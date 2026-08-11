"""Fail-closed root branch-price-and-check substrate.

This module deliberately has no dependency on the production solver search path.  A
column is useful only after the official checker has accepted the assembled board.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from hashlib import sha256
import itertools
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

SCHEMA_VERSION = "bay-plan-v1"
UNKNOWN = "UNKNOWN"
FEASIBLE = "FEASIBLE"
INFEASIBLE = "INFEASIBLE"
_SCHEMA_DESCRIPTOR = {
    "schema_version": SCHEMA_VERSION,
    "placement_fields": ("block_id", "orient_idx", "x", "y", "entry", "exit"),
    "event_fields": ("time", "phase", "ordinal", "block_id"),
    "rational_fields": ("tardiness", "regret", "raw_load", "normalized_load", "direct_cost"),
}


def schema_sha256() -> str:
    return canonical_sha256(_SCHEMA_DESCRIPTOR)


def as_fraction(value: Any) -> Fraction:
    """Convert a JSON-safe numeric value without silently using binary float math."""
    if isinstance(value, Fraction):
        return value
    if isinstance(value, bool):
        raise TypeError("booleans are not rational values")
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, str):
        return Fraction(value)
    if isinstance(value, float):
        return Fraction(str(value))
    raise TypeError(f"not a rational value: {value!r}")


def fraction_text(value: Fraction | int | str) -> str:
    value = as_fraction(value)
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def canonical_json(value: Any) -> str:
    """Stable compact JSON used for all durable hashes in this prototype."""
    def normalise(item: Any) -> Any:
        if isinstance(item, Fraction):
            return {"$fraction": fraction_text(item)}
        if isinstance(item, Mapping):
            return {str(k): normalise(v) for k, v in sorted(item.items(), key=lambda pair: str(pair[0]))}
        if isinstance(item, (tuple, list)):
            return [normalise(v) for v in item]
        if isinstance(item, (str, int, float, bool)) or item is None:
            return item
        raise TypeError(f"cannot canonically serialize {type(item).__name__}")
    return json.dumps(normalise(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def canonical_sha256(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def instance_sha256(instance: Mapping[str, Any]) -> str:
    return canonical_sha256(instance)


def official_checker_sha256() -> str:
    return sha256((Path(__file__).resolve().parents[1] / "solver" / "utils.py").read_bytes()).hexdigest()


@dataclass(frozen=True, order=True)
class PlacementV1:
    block_id: int
    orient_idx: int
    x: int
    y: int
    entry: int
    exit: int

    def __post_init__(self) -> None:
        if self.block_id < 0 or self.orient_idx < 0 or self.exit < self.entry:
            raise ValueError("invalid placement")

    def to_dict(self) -> dict[str, Any]:
        return {"block_id": self.block_id, "orient_idx": self.orient_idx, "x": self.x,
                "y": self.y, "entry": self.entry, "exit": self.exit}


@dataclass(frozen=True, order=True)
class EventV1:
    time: int
    phase: str
    ordinal: int
    block_id: int

    def __post_init__(self) -> None:
        if self.phase not in ("ENTRY", "EXIT") or self.ordinal < 0 or self.block_id < 0:
            raise ValueError("invalid event")

    def to_dict(self) -> dict[str, Any]:
        return {"time": self.time, "phase": self.phase, "ordinal": self.ordinal, "block_id": self.block_id}


@dataclass(frozen=True)
class BayPlanV1:
    schema_version: str
    instance_sha256: str
    checker_sha256: str
    bay_id: int
    members: tuple[int, ...]
    placements: tuple[PlacementV1, ...]
    events: tuple[EventV1, ...]
    tardiness: Fraction
    regret: Fraction
    raw_load: Fraction
    normalized_load: Fraction
    direct_cost: Fraction
    certificate_metadata: tuple[tuple[str, str], ...] = ()
    plan_signature: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION or self.bay_id < 0:
            raise ValueError("unsupported schema or invalid bay")
        members = tuple(self.members)
        if members != tuple(sorted(set(members))) or any(not isinstance(x, int) or x < 0 for x in members):
            raise ValueError("members must be sorted unique non-negative block IDs")
        if tuple(sorted(p.block_id for p in self.placements)) != members:
            raise ValueError("placements must match members exactly")
        if len({p.block_id for p in self.placements}) != len(self.placements):
            raise ValueError("duplicate placement")
        expected = sorted((EventV1(p.entry, "ENTRY", 0, p.block_id) for p in self.placements),
                          key=lambda e: (e.time, 0, e.ordinal, e.block_id))
        expected += sorted((EventV1(p.exit, "EXIT", 0, p.block_id) for p in self.placements),
                           key=lambda e: (e.time, 0, e.ordinal, e.block_id))
        # Events are a certificate trace, so require precisely one entry and exit per placement.
        if sorted((e.time, e.phase, e.block_id) for e in self.events) != sorted((e.time, e.phase, e.block_id) for e in expected):
            raise ValueError("events must match placement entry/exit times")
        object.__setattr__(self, "members", members)
        object.__setattr__(self, "placements", tuple(sorted(self.placements, key=lambda p: p.block_id)))
        object.__setattr__(self, "events", tuple(sorted(self.events, key=lambda e: (e.time, 0 if e.phase == "EXIT" else 1, e.ordinal, e.block_id))))
        for name in ("tardiness", "regret", "raw_load", "normalized_load", "direct_cost"):
            object.__setattr__(self, name, as_fraction(getattr(self, name)))
        object.__setattr__(self, "certificate_metadata", tuple(sorted((str(k), str(v)) for k, v in self.certificate_metadata)))
        object.__setattr__(self, "plan_signature", canonical_sha256(self.to_dict(include_signature=False)))

    def to_dict(self, include_signature: bool = True) -> dict[str, Any]:
        result = {"schema_version": self.schema_version, "instance_sha256": self.instance_sha256,
                  "checker_sha256": self.checker_sha256, "bay_id": self.bay_id, "members": list(self.members),
                  "placements": [p.to_dict() for p in self.placements], "events": [e.to_dict() for e in self.events],
                  "tardiness": fraction_text(self.tardiness), "regret": fraction_text(self.regret),
                  "raw_load": fraction_text(self.raw_load), "normalized_load": fraction_text(self.normalized_load),
                  "direct_cost": fraction_text(self.direct_cost), "certificate_metadata": list(self.certificate_metadata)}
        if include_signature:
            result["plan_signature"] = self.plan_signature
        return result
    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BayPlanV1":
        plan = cls(
            schema_version=value["schema_version"],
            instance_sha256=value["instance_sha256"],
            checker_sha256=value["checker_sha256"],
            bay_id=int(value["bay_id"]),
            members=tuple(int(v) for v in value["members"]),
            placements=tuple(PlacementV1(**p) for p in value["placements"]),
            events=tuple(EventV1(**e) for e in value["events"]),
            tardiness=as_fraction(value["tardiness"]),
            regret=as_fraction(value["regret"]),
            raw_load=as_fraction(value["raw_load"]),
            normalized_load=as_fraction(value["normalized_load"]),
            direct_cost=as_fraction(value["direct_cost"]),
            certificate_metadata=tuple((str(k), str(v)) for k, v in value.get("certificate_metadata", ())),
        )
        signature = value.get("plan_signature")
        if signature is not None and signature != plan.plan_signature:
            raise ValueError("plan signature does not match canonical plan")
        return plan


def serialize_bay_plan(plan: BayPlanV1) -> str:
    return canonical_json(plan.to_dict())


def deserialize_bay_plan(payload: str) -> BayPlanV1:
    return BayPlanV1.from_dict(json.loads(payload))
@dataclass(frozen=True)
class StructuralCutV1:
    """A checker-derived structural exclusion, never a membership no-good."""

    instance_sha256: str
    checker_sha256: str
    bay_id: int
    kind: str
    placements: tuple[PlacementV1, ...]
    events: tuple[EventV1, ...] = ()
    signature: str = field(init=False)

    def __post_init__(self) -> None:
        if self.kind not in ("placement_pair", "event_shell") or self.bay_id < 0:
            raise ValueError("invalid structural cut")
        placements = tuple(sorted(self.placements, key=lambda placement: placement.block_id))
        if self.kind == "placement_pair" and len(placements) != 2:
            raise ValueError("placement_pair cuts require exactly two placements")
        members = {placement.block_id for placement in placements}
        expected = sorted(
            (time, phase, placement.block_id)
            for placement in placements
            for time, phase in ((placement.entry, "ENTRY"), (placement.exit, "EXIT"))
        )
        actual = sorted((event.time, event.phase, event.block_id) for event in self.events)
        if actual != expected or any(event.block_id not in members for event in self.events):
            raise ValueError("structural-cut events must exactly describe its placements")
        object.__setattr__(self, "placements", placements)
        object.__setattr__(self, "events", tuple(self.events))
        object.__setattr__(self, "signature", canonical_sha256(self.to_dict(include_signature=False)))

    def to_dict(self, include_signature: bool = True) -> dict[str, Any]:
        value = {
            "instance_sha256": self.instance_sha256,
            "checker_sha256": self.checker_sha256,
            "bay_id": self.bay_id,
            "kind": self.kind,
            "placements": [placement.to_dict() for placement in self.placements],
            "events": [event.to_dict() for event in self.events],
        }
        if include_signature:
            value["signature"] = self.signature
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StructuralCutV1":
        cut = cls(
            value["instance_sha256"], value["checker_sha256"], int(value["bay_id"]), value["kind"],
            tuple(PlacementV1(**placement) for placement in value["placements"]),
            tuple(EventV1(**event) for event in value.get("events", ())),
        )
        if value.get("signature", cut.signature) != cut.signature:
            raise ValueError("structural-cut signature does not match canonical cut")
        return cut

    def matches(self, plan: BayPlanV1) -> bool:
        if (self.instance_sha256 != plan.instance_sha256 or self.checker_sha256 != plan.checker_sha256
                or self.bay_id != plan.bay_id):
            return False
        if self.kind == "placement_pair":
            members = {placement.block_id for placement in self.placements}
            return (
                set(self.placements).issubset(set(plan.placements))
                and tuple(event for event in plan.events if event.block_id in members) == self.events
            )
        return self.placements == plan.placements and self.events == plan.events


class StructuralCutPool:
    """Durable, node-independent checker-derived cuts with exact-match queries."""

    def __init__(self, cuts: Iterable[StructuralCutV1] = ()) -> None:
        self._cuts: dict[str, StructuralCutV1] = {}
        for cut in cuts:
            self.add(cut)

    def add(self, cut: StructuralCutV1) -> bool:
        if cut.signature in self._cuts:
            return False
        self._cuts[cut.signature] = cut
        return True

    def matching(self, plan: BayPlanV1) -> tuple[StructuralCutV1, ...]:
        return tuple(cut for _, cut in sorted(self._cuts.items()) if cut.matches(plan))

    def excludes(self, plan: BayPlanV1) -> bool:
        return bool(self.matching(plan))

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": "structural-cut-pool-v1",
                "cuts": [cut.to_dict() for _, cut in sorted(self._cuts.items())]}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StructuralCutPool":
        if value.get("schema_version") != "structural-cut-pool-v1":
            raise ValueError("unsupported structural-cut pool schema")
        return cls(StructuralCutV1.from_dict(cut) for cut in value.get("cuts", ()))

    def __len__(self) -> int:
        return len(self._cuts)


def serialize_structural_cut_pool(pool: StructuralCutPool) -> str:
    return canonical_json(pool.to_dict())


def deserialize_structural_cut_pool(payload: str) -> StructuralCutPool:
    return StructuralCutPool.from_dict(json.loads(payload))


@dataclass(frozen=True)
class StructuralDiagnosis:
    status: str
    cuts: tuple[StructuralCutV1, ...] = ()
    evidence: tuple[tuple[str, str], ...] = ()
    reason: str = ""


def _pair_checker_instance(instance: Mapping[str, Any], bay_id: int, placements: Sequence[PlacementV1]) -> dict[str, Any]:
    """Build a checker-native two-block, one-bay instance with block IDs reindexed."""
    blocks = []
    for placement in placements:
        block = dict(instance["blocks"][placement.block_id])
        preferences = block.get("bay_preferences")
        if preferences is not None:
            block["bay_preferences"] = [preferences[bay_id]]
        blocks.append(block)
    return {**instance, "bays": [dict(instance["bays"][bay_id])], "blocks": blocks}


def _pair_solution(placements: Sequence[PlacementV1], events: Sequence[EventV1]) -> dict[str, Any]:
    remap = {placement.block_id: index for index, placement in enumerate(placements)}
    by_block = {placement.block_id: placement for placement in placements}
    operations: dict[int, list[tuple[int, int, dict[str, Any]]]] = {}
    for event in events:
        placement = by_block[event.block_id]
        block_id = remap[event.block_id]
        operation = ({"type": "ENTRY", "block_id": block_id, "bay_id": 0, "x": placement.x,
                      "y": placement.y, "orient_idx": placement.orient_idx}
                     if event.phase == "ENTRY" else
                     {"type": "EXIT", "block_id": block_id, "bay_id": 0})
        operations.setdefault(event.time, []).append((0 if event.phase == "EXIT" else 1, event.ordinal, operation))
    return {"operations": {
        str(time): [operation for _, _, operation in sorted(items, key=lambda item: item[:2] + (item[2]["block_id"],))]
        for time, items in sorted(operations.items())
    }}


def diagnose_structural_cuts(
    instance: Mapping[str, Any], bay_id: int, proposal: BayPlanV1,
    checker: Callable[..., Mapping[str, Any]] | None = None,
) -> StructuralDiagnosis:
    """Explain a checker-certified infeasible proposal with exact, order-bound cuts."""
    if proposal.bay_id != bay_id:
        raise ValueError("proposal bay differs from requested bay")
    if proposal.instance_sha256 != instance_sha256(instance) or proposal.checker_sha256 != official_checker_sha256():
        return StructuralDiagnosis(UNKNOWN, reason="proposal belongs to another instance or checker")

    proposal_certificate = certify_official(
        _pair_checker_instance(instance, bay_id, proposal.placements),
        _pair_solution(proposal.placements, proposal.events),
        checker,
    )
    if proposal_certificate.status == UNKNOWN:
        return StructuralDiagnosis(UNKNOWN, reason="checker unavailable for full proposal")
    if proposal_certificate.status == FEASIBLE:
        return StructuralDiagnosis(
            FEASIBLE, evidence=(("full_proposal", "checker-feasible"),),
            reason="feasible proposals cannot produce structural cuts",
        )

    pair_cuts: list[StructuralCutV1] = []
    unknowns = 0
    events_by_pair = tuple(proposal.events)
    for pair in itertools.combinations(proposal.placements, 2):
        pair_ids = {placement.block_id for placement in pair}
        pair_events = tuple(event for event in events_by_pair if event.block_id in pair_ids)
        certificate = certify_official(
            _pair_checker_instance(instance, bay_id, pair), _pair_solution(pair, pair_events), checker
        )
        if certificate.status == UNKNOWN:
            unknowns += 1
            continue
        if certificate.status == INFEASIBLE:
            pair_cuts.append(StructuralCutV1(
                proposal.instance_sha256, proposal.checker_sha256, bay_id,
                "placement_pair", pair, pair_events,
            ))
    if unknowns:
        return StructuralDiagnosis(
            UNKNOWN, evidence=(("pair_checks_unknown", str(unknowns)),),
            reason="checker unavailable for at least one independent placement pair",
        )
    if pair_cuts:
        return StructuralDiagnosis(
            INFEASIBLE, tuple(pair_cuts), evidence=(("pair_cuts", str(len(pair_cuts))),)
        )
    return StructuralDiagnosis(
        INFEASIBLE,
        (StructuralCutV1(proposal.instance_sha256, proposal.checker_sha256, bay_id, "event_shell",
                         proposal.placements, proposal.events),),
        evidence=(("event_shell", "full proposal checker-certified infeasible"),),
    )


def reusable_structural_cuts(pool: StructuralCutPool, plan: BayPlanV1) -> tuple[StructuralCutV1, ...]:
    """Optional pricer-facing consumption API: return exact cuts applicable to a candidate."""
    return pool.matching(plan)


def _make_plan(instance: Mapping[str, Any], bay_id: int, placements: Iterable[PlacementV1], *, checker_hash: str | None = None, metadata: Iterable[tuple[str, str]] = (), events: Iterable[EventV1] | None = None) -> BayPlanV1:
    placements = tuple(sorted(placements, key=lambda p: p.block_id))
    blocks, bays = instance["blocks"], instance["bays"]
    area = sum(Fraction(b["width"]) * Fraction(b["height"]) for b in bays) / len(bays)
    raw = sum((as_fraction(blocks[p.block_id]["workload"]) for p in placements), Fraction())
    norm = raw * area / (Fraction(bays[bay_id]["width"]) * Fraction(bays[bay_id]["height"]))
    tardiness = sum((max(Fraction(), Fraction(p.exit) - as_fraction(blocks[p.block_id]["due_date"])) for p in placements), Fraction())
    regret = sum((as_fraction(max(blocks[p.block_id]["bay_preferences"])) - as_fraction(blocks[p.block_id]["bay_preferences"][bay_id]) for p in placements), Fraction())
    plan_events = tuple(events) if events is not None else tuple(
        EventV1(time, phase, ordinal, p.block_id)
        for p in placements
        for time, phase, ordinal in ((p.entry, "ENTRY", 0), (p.exit, "EXIT", 1))
    )
    weights = instance.get("weights", {})
    direct = as_fraction(weights.get("w1", 1)) * tardiness + as_fraction(weights.get("w3", 1)) * regret
    return BayPlanV1(SCHEMA_VERSION, instance_sha256(instance), checker_hash or official_checker_sha256(), bay_id,
                     tuple(p.block_id for p in placements), placements, plan_events, tardiness, regret, raw, norm, direct, tuple(metadata))


@dataclass(frozen=True)
class Certification:
    status: str
    result: Mapping[str, Any]
    reason: str = ""


def certify_official(instance: Mapping[str, Any], solution: Mapping[str, Any], checker: Callable[..., Mapping[str, Any]] | None = None) -> Certification:
    """Invoke the official checker. Import/call failures are UNKNOWN, never acceptance."""
    try:
        if checker is None:
            from solver.utils import check_feasibility as checker
        result = checker(dict(instance), dict(solution))
        if not isinstance(result, Mapping):
            return Certification(UNKNOWN, {}, "checker returned a non-mapping")
        return Certification(FEASIBLE if result.get("feasible") is True else INFEASIBLE, dict(result))
    except Exception as exc:
        return Certification(UNKNOWN, {}, f"official checker error: {type(exc).__name__}: {exc}")


def extract_seed_bay_plans(instance: Mapping[str, Any], solution: Mapping[str, Any], checker: Callable[..., Mapping[str, Any]] | None = None) -> tuple[BayPlanV1, ...]:
    cert = certify_official(instance, solution, checker)
    if cert.status != FEASIBLE:
        raise ValueError(f"cannot extract uncertified solution: {cert.status}: {cert.reason or cert.result.get('violations')}")
    records: dict[int, dict[int, dict[str, Any]]] = {}
    events: dict[int, list[EventV1]] = {}
    for key, ops in solution["operations"].items():
        time = int(key)
        for ordinal, op in enumerate(ops):
            bay_id = int(op["bay_id"])
            block_id = int(op["block_id"])
            if op["type"] == "ENTRY":
                records.setdefault(bay_id, {}).setdefault(block_id, {}).update(
                    orient_idx=op.get("orient_idx", 0), x=op.get("x", 0), y=op.get("y", 0), entry=time)
                events.setdefault(bay_id, []).append(EventV1(time, "ENTRY", ordinal, block_id))
            elif op["type"] == "EXIT":
                records.setdefault(bay_id, {}).setdefault(block_id, {}).update(exit=time)
                events.setdefault(bay_id, []).append(EventV1(time, "EXIT", ordinal, block_id))
    digest = canonical_sha256(cert.result)
    return tuple(
        _make_plan(
            instance,
            bay,
            (PlacementV1(block, int(r["orient_idx"]), int(r["x"]), int(r["y"]), int(r["entry"]), int(r["exit"]))
             for block, r in data.items()),
            metadata=(("checker_result_sha256", digest),),
            events=events.get(bay, ()),
        )
        for bay, data in sorted(records.items())
    )


def assemble_solution(plans: Sequence[BayPlanV1]) -> dict[str, Any]:
    """Merge column event traces, retaining each trace's order within a phase."""
    seen: set[int] = set()
    ops: dict[int, list[tuple[int, int, int, dict[str, Any]]]] = {}
    for column_order, plan in enumerate(plans):
        by_block = {placement.block_id: placement for placement in plan.placements}
        for block_id in plan.members:
            if block_id in seen:
                raise ValueError(f"block {block_id} occurs in multiple columns")
            seen.add(block_id)
        for event in plan.events:
            placement = by_block[event.block_id]
            if event.phase == "ENTRY":
                operation = {"type": "ENTRY", "block_id": event.block_id, "bay_id": plan.bay_id,
                             "x": placement.x, "y": placement.y, "orient_idx": placement.orient_idx}
            else:
                operation = {"type": "EXIT", "block_id": event.block_id, "bay_id": plan.bay_id}
            ops.setdefault(event.time, []).append(
                (0 if event.phase == "EXIT" else 1, column_order, event.ordinal, operation)
            )
    return {"operations": {
        str(time): [operation for _, _, _, operation in sorted(at_time, key=lambda item: item[:3] + (item[3]["block_id"],))]
        for time, at_time in sorted(ops.items())
    }}
assemble_full_board = assemble_solution


def floor_z2(loads: Iterable[Fraction | int | str]) -> int:
    values = [as_fraction(v) for v in loads]
    return 0 if len(values) < 2 else max(
        (abs(a - b).numerator // abs(a - b).denominator)
        for a, b in itertools.combinations(values, 2)
    )
@dataclass(frozen=True)
class CanonicalPrices:
    """Pricer-facing signs for the continuous-spread root surrogate."""
    block_credit: tuple[Fraction, ...]
    normalized_load_price: Fraction = Fraction()
    bay_credit: Fraction = Fraction()

    def __post_init__(self) -> None:
        object.__setattr__(self, "block_credit", tuple(as_fraction(v) for v in self.block_credit))
        object.__setattr__(self, "normalized_load_price", as_fraction(self.normalized_load_price))
        object.__setattr__(self, "bay_credit", as_fraction(self.bay_credit))

    def reduced_cost(self, plan: BayPlanV1) -> Fraction:
        if any(block_id >= len(self.block_credit) for block_id in plan.members):
            raise ValueError("missing block credit")
        return (plan.direct_cost + self.normalized_load_price * plan.normalized_load
                - self.bay_credit - sum((self.block_credit[block_id] for block_id in plan.members), Fraction()))


@dataclass(frozen=True)
class DualResult:
    """Only verified canonical prices leave the LP adapter."""
    status: str
    requests: tuple[Any, ...] = ()
    evidence: tuple[tuple[str, str], ...] = ()
    reason: str = ""


def unavailable_duals(reason: str = "LP dual extraction is unavailable") -> DualResult:
    return DualResult(UNKNOWN, reason=reason)


def canonical_reduced_cost(plan: BayPlanV1, prices: CanonicalPrices) -> Fraction:
    return prices.reduced_cost(plan)




@dataclass(frozen=True)
class MasterResult:
    status: str
    plans: tuple[BayPlanV1, ...] = ()
    objective: Fraction | None = None
    z2: int | None = None
    reason: str = ""


class RestrictedMaster:
    """Small, exact root restricted master; enumeration is capped and fail-closed."""
    def __init__(
        self, instance: Mapping[str, Any], max_combinations: int = 100_000,
        structural_cuts: StructuralCutPool | None = None,
    ) -> None:
        self.instance = instance
        self.max_combinations = max_combinations
        self.structural_cuts = structural_cuts if structural_cuts is not None else StructuralCutPool()
        self._columns: dict[int, dict[str, BayPlanV1]] = {bay: {} for bay in range(len(instance["bays"]))}
    def _all_columns(self) -> tuple[BayPlanV1, ...]:
        return tuple(
            plan for _, bucket in sorted(self._columns.items()) for _, plan in sorted(bucket.items())
        )

    def lp_duals(self) -> DualResult:
        """Solve the continuous-spread root LP and expose self-consistent prices.

        The official checker floors Z2, so these prices are proposal guidance rather
        than a lower bound or optimality certificate for the official objective.
        Objective and load coefficients are scaled from Fraction denominators before
        reaching Gurobi; no float-derived input costs are used.
        """
        columns = self._all_columns()
        if not columns or any(not self._columns[bay] for bay in self._columns):
            return unavailable_duals("at least one bay has no certified column")
        try:
            import gurobipy as gp
            from gurobipy import GRB
            from tools.nfp_pricer import PriceRequest
        except Exception as exc:
            return unavailable_duals(f"Gurobi/pricer unavailable: {type(exc).__name__}: {exc}")
        try:
            def lcm(values: Iterable[int]) -> int:
                from math import gcd
                result = 1
                for value in values:
                    result = result * value // gcd(result, value)
                return result

            load_scale = lcm(plan.normalized_load.denominator for plan in columns)
            weight2 = as_fraction(self.instance.get("weights", {}).get("w2", 1))
            cost_scale = lcm(
                [load_scale * weight2.denominator, *(plan.direct_cost.denominator for plan in columns)]
            )
            model = gp.Model("root_restricted_master_lp")
            model.Params.OutputFlag = 0
            lam = [model.addVar(lb=0.0, name=f"lambda_{index}") for index in range(len(columns))]
            spread = model.addVar(lb=0.0, name="continuous_load_spread")
            cover = [
                model.addConstr(gp.quicksum(lam[index] for index, plan in enumerate(columns) if block in plan.members) == 1,
                                name=f"cover_{block}")
                for block in range(len(self.instance["blocks"]))
            ]
            bay_rows = [
                model.addConstr(gp.quicksum(lam[index] for index, plan in enumerate(columns) if plan.bay_id == bay) == 1,
                                name=f"bay_{bay}")
                for bay in range(len(self.instance["bays"]))
            ]
            loads = [
                gp.quicksum(int(plan.normalized_load * load_scale) * lam[index]
                            for index, plan in enumerate(columns) if plan.bay_id == bay)
                for bay in range(len(self.instance["bays"]))
            ]
            spread_rows: dict[tuple[int, int], Any] = {}
            for left in range(len(loads)):
                for right in range(len(loads)):
                    if left != right:
                        spread_rows[left, right] = model.addConstr(
                            loads[left] - loads[right] <= spread, name=f"spread_{left}_{right}"
                        )
            model.setObjective(
                gp.quicksum(int(plan.direct_cost * cost_scale) * lam[index]
                            for index, plan in enumerate(columns))
                + int(weight2 * cost_scale / load_scale) * spread,
                GRB.MINIMIZE,
            )
            model.optimize()
            if model.Status != GRB.OPTIMAL or model.SolCount <= 0:
                return unavailable_duals(f"LP solve not certified optimal (status={model.Status})")
            block_credit = tuple(as_fraction(str(row.Pi)) / cost_scale for row in cover)
            bay_credit = tuple(as_fraction(str(row.Pi)) / cost_scale for row in bay_rows)
            load_prices: list[Fraction] = []
            for bay in range(len(bay_rows)):
                signed_dual = sum(
                    as_fraction(str(row.Pi)) * (1 if left == bay else -1)
                    for (left, right), row in spread_rows.items() if bay == left or bay == right
                )
                load_prices.append(-signed_dual * load_scale / cost_scale)
            prices = tuple(CanonicalPrices(block_credit, load_prices[bay], bay_credit[bay])
                           for bay in range(len(bay_rows)))
            tolerance = Fraction(1, 10**7)
            for variable, plan in zip(lam, columns):
                expected = canonical_reduced_cost(plan, prices[plan.bay_id])
                observed = as_fraction(str(variable.RC)) / cost_scale
                if abs(expected - observed) > tolerance:
                    return unavailable_duals(
                        f"reduced-cost validation failed for {plan.plan_signature}: {expected} != {observed}"
                    )
            requests = tuple(
                PriceRequest(
                    bay_id=bay,
                    block_credit=prices[bay].block_credit,
                    normalized_load_price=prices[bay].normalized_load_price,
                    bay_credit=prices[bay].bay_credit,
                    together_groups=(),
                    apart_pairs=(),
                    cutoff=Fraction(),
                    deadline_monotonic=float("inf"),
                    seed=0,
                    instance=self.instance,
                )
                for bay in range(len(prices))
            )
            return DualResult(
                FEASIBLE,
                requests=requests,
                evidence=(
                    ("solver", "gurobi"),
                    ("pricing_objective", "continuous-spread-surrogate; official Z2 floor excluded"),
                    ("objective_scale", str(cost_scale)),
                    ("load_scale", str(load_scale)),
                    ("validated_columns", str(len(columns))),
                ),
            )
        except Exception as exc:
            return unavailable_duals(f"Gurobi LP/dual extraction error: {type(exc).__name__}: {exc}")

    def add_column(self, plan: BayPlanV1) -> bool:
        if plan.instance_sha256 != instance_sha256(self.instance) or plan.checker_sha256 != official_checker_sha256():
            raise ValueError("column belongs to another instance or checker")
        if self.structural_cuts.excludes(plan):
            return False
        bucket = self._columns[plan.bay_id]
        if plan.plan_signature in bucket:
            return False
        # A same-cover/same-load column with no lower direct cost is safely dominated.
        for old in bucket.values():
            if old.members == plan.members and old.normalized_load == plan.normalized_load and old.direct_cost <= plan.direct_cost:
                return False
        for signature, old in list(bucket.items()):
            if old.members == plan.members and old.normalized_load == plan.normalized_load and plan.direct_cost < old.direct_cost:
                del bucket[signature]
        bucket[plan.plan_signature] = plan
        return True

    def solve(self) -> MasterResult:
        choices = [tuple(bucket.values()) for _, bucket in sorted(self._columns.items())]
        if any(not options for options in choices):
            return MasterResult(INFEASIBLE, reason="at least one bay has no column")
        examined = 0
        best: tuple[Fraction, tuple[BayPlanV1, ...], int] | None = None
        n_blocks = len(self.instance["blocks"])
        for selected in itertools.product(*choices):
            examined += 1
            if examined > self.max_combinations:
                return MasterResult(UNKNOWN, reason="restricted-master enumeration cap reached")
            members = tuple(block for plan in selected for block in plan.members)
            if tuple(sorted(members)) != tuple(range(n_blocks)) or len(set(members)) != n_blocks:
                continue
            z2 = floor_z2(plan.normalized_load for plan in selected)
            objective = sum((plan.direct_cost for plan in selected), Fraction()) + as_fraction(self.instance.get("weights", {}).get("w2", 1)) * z2
            key = (objective, tuple(plan.plan_signature for plan in selected))
            if best is None or key < (best[0], tuple(plan.plan_signature for plan in best[1])):
                best = (objective, selected, z2)
        if best is None:
            return MasterResult(INFEASIBLE, reason="no exact cover in restricted columns")
        return MasterResult(FEASIBLE, best[1], best[0], best[2])
    def certify(self, result: MasterResult, checker: Callable[..., Mapping[str, Any]] | None = None) -> Certification:
        """Certify a selected full board; UNKNOWN or rejection never becomes a proof."""
        if result.status != FEASIBLE:
            return Certification(result.status if result.status == UNKNOWN else INFEASIBLE, {}, result.reason)
        return certify_official(self.instance, assemble_solution(result.plans), checker)


def run_root_iteration(master: RestrictedMaster, pricer: Callable[[Any], Any], request: Any) -> tuple[MasterResult, Any]:
    """Solve the current RMP, request one price, and accept only a compatible negative column."""
    current = master.solve()
    priced = price_root(pricer, request)
    if getattr(priced, "status", UNKNOWN) == "NEGATIVE_COLUMN":
        plan = getattr(priced, "plan", None)
        if isinstance(plan, BayPlanV1):
            master.add_column(plan)
        else:
            priced = type("UnknownPrice", (), {
                "status": UNKNOWN, "reason": "negative price did not carry BayPlanV1"
            })()
    return current, priced


def price_root(pricer: Callable[[Any], Any], request: Any) -> Any:
    """Adapter boundary: calls a supplied pricer without importing nfp_pricer eagerly."""
    result = pricer(request)
    if getattr(result, "status", UNKNOWN) == "NEGATIVE_COLUMN":
        plan = getattr(result, "plan", None)
        required = ("members", "bay_id", "direct_cost", "normalized_load", "plan_signature")
        if plan is None or any(not hasattr(plan, name) for name in required):
            return type("UnknownPrice", (), {"status": UNKNOWN, "reason": "pricer returned incompatible column"})()
    return result
