"""Bind actual original goal input to the complete, conservative FULL curve.

This is a planning adapter. Its inputs come from verified server reads; neither
this module nor the solver turns declaration evidence into bank authority.
"""

from datetime import timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from app.domain.boundary_types import BoundaryModel, BoundaryPoint
from app.domain.full_protection_projection import (
    FullProtectionPolicySource,
    FullProtectionProjectionResult,
)
from app.domain.full_seasonal_current_protection import ALGORITHM as ENDED_SEASONAL_ALGORITHM
from app.domain.full_seasonal_current_protection import (
    CurrentSeasonalProtectionBindings,
    derive_current_seasonal_protection_bindings,
)
from app.domain.full_seasonal_protection import ALGORITHM as SEASONAL_ALGORITHM
from app.domain.full_seasonal_protection import FLOOR as SEASONAL_FLOOR
from app.domain.full_seasonal_protection import (
    SeasonalProtectionBindings,
    derive_seasonal_protection_bindings,
)
from app.domain.multi_goal_allocation import (
    HardProtectionPoint,
    MultiGoalAllocationInput,
    SourceReference,
)
from app.domain.policy_configuration import configuration_hash

ORIGINAL_FLOORS = (
    "obligations",
    "living",
    "emergency",
    "goal_cash",
    "goal_minimum",
)
EXTRA_FLOORS = ("full_dated_expense", "full_periodic_transfer", "pending_cash_reservations")
PHASES = ("BEFORE_PAYMENT", "AFTER_PAYMENT", "AFTER_PRINCIPAL")
POINT_COUNT = 366 * 3


class FullJointBinding(BoundaryModel):
    status: Literal["VERIFIED", "UNKNOWN"]
    original_point_count: int
    full_point_count: int
    bound_point_count: int
    original_input_hash: str
    full_projection_input_hash: str
    verified_source_refs: list[SourceReference]
    reasons: list[str]
    binding_hash: str
    candidate: MultiGoalAllocationInput


def captured_references(original: MultiGoalAllocationInput) -> list[SourceReference]:
    """Keep conflicting hashes visible rather than collapsing them by evidence ID."""
    unique = {
        (row.user_id, row.evidence_id, row.content_hash): row
        for group in (
            *(lot.source_refs for lot in original.income_lots),
            *(goal.source_refs for goal in original.goals),
            *(point.source_refs for point in original.hard_protection_points),
        )
        for row in group
    }
    for lot in original.income_lots:
        row = SourceReference(
            user_id=original.user_id,
            evidence_id=lot.bank_evidence_id,
            content_hash=lot.bank_evidence_hash,
        )
        unique[(row.user_id, row.evidence_id, row.content_hash)] = row
    return [unique[key] for key in sorted(unique)]


def _original_matches(captured: HardProtectionPoint, actual: BoundaryPoint) -> bool:
    fields = (
        captured.obligation_floor_cents,
        captured.living_floor_cents,
        captured.emergency_floor_cents,
        captured.owned_goal_cash_cents,
        captured.other_protection_floor_cents,
    )
    return (
        captured.date == actual.date
        and captured.cash_cents == actual.cash_cents
        and all(
            type(actual.protected_cents_by_reason.get(name, 0)) is int
            and actual.protected_cents_by_reason.get(name, 0) == amount
            for name, amount in zip(ORIGINAL_FLOORS, fields, strict=True)
        )
        and not (set(actual.protected_cents_by_reason) - set(ORIGINAL_FLOORS))
        and actual.margin_cents == captured.remaining_cents
    )


def bind_full_joint_input(
    original: MultiGoalAllocationInput,
    projection: FullProtectionProjectionResult,
    verified_sources: list[SourceReference],
    *,
    source_issues: list[str] | None = None,
    seasonal_proof_sources: list[FullProtectionPolicySource] | None = None,
) -> FullJointBinding:
    """Require every original point and source before consuming extra FULL floors.

    Unknown cases retain the original registered goal denominator. The original
    solver then produces null goal amounts, never a smaller successful problem.
    """
    original = MultiGoalAllocationInput.model_validate(original.model_dump())
    projection = FullProtectionProjectionResult.model_validate(projection.model_dump())
    verified_sources = [
        SourceReference.model_validate(row.model_dump()) for row in verified_sources
    ]
    reasons = set(original.source_issues) | set(source_issues or [])
    fresh = {row.evidence_id: row for row in verified_sources}
    extra_floors: tuple[str, ...] = EXTRA_FLOORS
    seasonal: SeasonalProtectionBindings | CurrentSeasonalProtectionBindings | None = None
    if projection.algorithm_version in {SEASONAL_ALGORITHM, ENDED_SEASONAL_ALGORITHM}:
        extra_floors = (*EXTRA_FLOORS, SEASONAL_FLOOR)
        derive_seasonal = (
            derive_current_seasonal_protection_bindings
            if projection.algorithm_version == ENDED_SEASONAL_ALGORITHM
            else derive_seasonal_protection_bindings
        )
        seasonal = derive_seasonal(
            seasonal_proof_sources or [],
            original.as_of,
            original.timezone,
            expected_user_id=original.user_id,
        )
        if not seasonal.requested or seasonal.reasons:
            reasons.add("SEASONAL_COMPLETE_CURRENT_PROOF_NOT_BOUND")
            reasons.update(seasonal.reasons)
        for identity, digest in (seasonal.evidence() if not seasonal.reasons else {}).items():
            if identity not in fresh or fresh[identity].content_hash != digest:
                reasons.add("SEASONAL_ORIGINAL_SOURCE_NOT_FRESHLY_VERIFIED")
    if not verified_sources or len(fresh) != len(verified_sources):
        reasons.add("FRESH_SOURCE_INVENTORY_MISSING_OR_DUPLICATE")
    if len(verified_sources) > 1000:
        reasons.add("FRESH_SOURCE_REFERENCE_CAPACITY")
    if any(row.user_id != original.user_id for row in verified_sources):
        reasons.add("FRESH_SOURCE_OWNER_MISMATCH")
    if any(fresh.get(row.evidence_id) != row for row in captured_references(original)):
        reasons.add("ORIGINAL_SOURCE_HASH_NOT_FRESHLY_VERIFIED")
    if (
        projection.status == "UNKNOWN"
        or not projection.full_obligations_complete_within_registered_current_scope
    ):
        reasons.add("FULL_PROTECTION_UNKNOWN")
        reasons.update(projection.reasons)
    for check in projection.source_account_checks:
        if check.state == "SOURCE_LIQUIDITY_RISK":
            reasons.add("FULL_CURRENT_SOURCE_ACCOUNT_LIQUIDITY_RISK")
        if not check.future_account_debits_complete:
            reasons.add("FULL_FUTURE_ACCOUNT_DEBITS_NOT_PROVEN")
    if any(row.state == "UNKNOWN" for row in projection.policy_states):
        reasons.add("FULL_POLICY_HISTORY_OR_CURRENT_PROOF_UNKNOWN")
    annual = projection.original_annual_projection.calculation_trace
    full = projection.full_annual_projection
    overlay = full.calculation_trace if full is not None else []
    if (
        len(original.hard_protection_points) != POINT_COUNT
        or len(annual) != POINT_COUNT
        or len(overlay) != POINT_COUNT
    ):
        reasons.add("FULL_365_DAY_POINT_INVENTORY_INCOMPLETE")
    if projection.original_annual_projection.status == "INSUFFICIENT_EVIDENCE":
        reasons.add("ORIGINAL_ANNUAL_PROTECTION_UNKNOWN")
    if len({row.occurrence_id for row in projection.occurrences}) != len(projection.occurrences):
        reasons.add("FULL_OCCURRENCE_INVENTORY_DUPLICATE")
    today = original.as_of.astimezone(ZoneInfo(original.timezone)).date()
    points: list[HardProtectionPoint] = []
    bound = 0
    reservations: int | None = None
    for index, (captured, baseline, layered) in enumerate(
        zip(original.hard_protection_points, annual, overlay, strict=False)
    ):
        day, phase = divmod(index, 3)
        if (
            baseline.day != day
            or baseline.phase != PHASES[phase]
            or baseline.date != today + timedelta(days=day)
            or not _original_matches(captured, baseline)
        ):
            reasons.add("ORIGINAL_365_DAY_POINT_BINDING_MISMATCH")
            continue
        extras = layered.protected_cents_by_reason
        if (
            (layered.day, layered.date, layered.phase)
            != (baseline.day, baseline.date, baseline.phase)
            or any(
                extras.get(key, 0) != baseline.protected_cents_by_reason.get(key, 0)
                for key in ORIGINAL_FLOORS
            )
            or set(extras) - set(ORIGINAL_FLOORS) - set(extra_floors)
            or any(
                type(extras.get(key, 0)) is not int or extras.get(key, 0) < 0
                for key in extra_floors
            )
        ):
            reasons.add("FULL_LAYER_CHANGED_ORIGINAL_PROTECTION")
            continue
        if reservations is None:
            reservations = extras.get("pending_cash_reservations", 0)
        unpaid = {"DATED_EXPENSE": 0, "PERIODIC_TRANSFER": 0}
        paid = 0
        for occurrence in projection.occurrences:
            settled = occurrence.hypothetical_payment_date < layered.date or (
                occurrence.hypothetical_payment_date == layered.date and phase > 0
            )
            if settled:
                paid += occurrence.conservative_unpaid_cents
            else:
                unpaid[occurrence.kind] += occurrence.conservative_unpaid_cents
        if (
            extras.get("pending_cash_reservations", 0) != reservations
            or extras.get("full_dated_expense", 0) != unpaid["DATED_EXPENSE"]
            or extras.get("full_periodic_transfer", 0) != unpaid["PERIODIC_TRANSFER"]
            or layered.cash_cents != baseline.cash_cents - paid
            or layered.margin_cents != layered.cash_cents - sum(extras.values())
            or seasonal is not None
            and extras.get(SEASONAL_FLOOR) != seasonal.amount_on(layered.date)
        ):
            reasons.add("FULL_CONDITIONAL_CASH_OR_FLOOR_BINDING_MISMATCH")
            continue
        bound += 1
        points.append(
            HardProtectionPoint(
                date=captured.date,
                cash_cents=layered.cash_cents,
                obligation_floor_cents=captured.obligation_floor_cents,
                living_floor_cents=captured.living_floor_cents,
                emergency_floor_cents=captured.emergency_floor_cents,
                owned_goal_cash_cents=captured.owned_goal_cash_cents,
                other_protection_floor_cents=captured.other_protection_floor_cents
                + sum(extras.get(key, 0) for key in extra_floors),
                source_refs=verified_sources
                if 0 < len(verified_sources) <= 1000
                else captured.source_refs,
            )
        )
    if bound != POINT_COUNT:
        reasons.add("FULL_POINT_BINDING_NOT_COMPLETE")
    # A failed binding retains the original input, with explicit blocking issues.
    # It never sends a partial subset of successfully bound points to the solver.
    candidate = MultiGoalAllocationInput.model_validate(
        original.model_copy(
            update={
                "hard_protection_points": points
                if not reasons
                else original.hard_protection_points,
                "source_issues": sorted(reasons),
            }
        ).model_dump()
    )
    originals_hash = configuration_hash(original.model_dump(mode="json"))
    binding_hash = configuration_hash(
        {
            "original_input_hash": originals_hash,
            "full_projection": projection.model_dump(mode="json"),
            "verified_sources": [row.model_dump(mode="json") for row in verified_sources],
            "candidate_input_hash": configuration_hash(candidate.model_dump(mode="json")),
            "bound_point_count": bound,
            "reasons": sorted(reasons),
        }
    )
    return FullJointBinding(
        status="UNKNOWN" if reasons else "VERIFIED",
        original_point_count=len(original.hard_protection_points),
        full_point_count=len(overlay),
        bound_point_count=bound,
        original_input_hash=originals_hash,
        full_projection_input_hash=projection.input_hash,
        verified_source_refs=verified_sources,
        reasons=sorted(reasons),
        binding_hash=binding_hash,
        candidate=candidate,
    )
