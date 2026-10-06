"""Explicit future-Dated history preview; the original v1 protocol stays unchanged."""

from datetime import timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from app.domain.boundary_types import BoundaryModel, BoundaryPoint, BoundaryProduct, BoundaryResult
from app.domain.full_future_dated_history import (
    FutureDatedHistoryProof,
    verify_future_dated_history,
)
from app.domain.full_policy_change_impact import (
    CandidateCurve,
    FullPolicyImpactInput,
    GoalPreviewImpact,
    HypotheticalCommitment,
    PositionPreviewImpact,
    _candidate,
    _original,
    _trace,
)
from app.domain.full_policy_configuration import DatedExpensePolicy, validate_full_configuration
from app.domain.full_protection_projection import FullProtectionOccurrence, _calendar_active
from app.domain.policy_configuration import configuration_hash
from pydantic import Field, StrictInt


class FullPolicyHistoryImpactInput(BoundaryModel):
    protocol: Literal["full-policy-history-impact-input-v2"] = "full-policy-history-impact-input-v2"
    user_id: UUID
    epoch_id: UUID
    original_input: FullPolicyImpactInput
    history_proof: FutureDatedHistoryProof | None


class FullPolicyHistoryFinancialImpact(BoundaryModel):
    protocol: Literal["full-policy-financial-impact-history-v2"] = (
        "full-policy-financial-impact-history-v2"
    )
    simulation: Literal[True] = True
    hypothetical: Literal[True] = True
    grants_authority: Literal[False] = False
    writes_policy_or_bank: Literal[False] = False
    receipt_is_current_authority: Literal[False] = False
    future_income_cents: Literal[0] = 0
    horizon_days: Literal[365] = 365
    initial_day_and_365_future_days: Literal[True] = True
    status: Literal["PROJECTED", "UNKNOWN"]
    basis: Literal["VERIFIED_FUTURE_DATED_HISTORY_CONSERVATIVE_REPLACEMENT"] = (
        "VERIFIED_FUTURE_DATED_HISTORY_CONSERVATIVE_REPLACEMENT"
    )
    user_id: UUID
    epoch_id: UUID
    history_proof: FutureDatedHistoryProof | None
    before: BoundaryResult | None
    after: CandidateCurve | None
    delta_safe_idle_cents: StrictInt | None = None
    delta_minimum_margin_cents: StrictInt | None = None
    delta_max_allocatable_by_product: dict[str, int] | None = None
    goals: list[GoalPreviewImpact] = Field(default_factory=list)
    positions: list[PositionPreviewImpact] = Field(default_factory=list)
    candidate_commitments: list[HypotheticalCommitment] = Field(default_factory=list)
    retained_original_occurrence_ids: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    input_hash: str
    limitations: list[str] = Field(default_factory=list)


def _caps(
    trace: list[BoundaryPoint], products: list[BoundaryProduct], risk: bool
) -> dict[str, int]:
    worst = min(point.margin_cents for point in trace)
    result = {}
    for product in products:
        return_day = (
            product.fixed_return.term_days + product.fixed_return.settlement_delay_days
            if product.fixed_return
            else 366
        )
        margins = [
            point.margin_cents
            for point in trace
            if point.day < return_day
            or point.day == return_day
            and point.phase != "AFTER_PRINCIPAL"
        ]
        result[str(product.product_id)] = max(0, min(margins)) if worst >= 0 and not risk else 0
    return result


def project_full_policy_history_change(
    envelope: FullPolicyHistoryImpactInput,
) -> FullPolicyHistoryFinancialImpact:
    """Retain all original facts, verify the raw history, and change only a future hypothesis."""
    envelope = FullPolicyHistoryImpactInput.model_validate(envelope.model_dump())
    data, proof = envelope.original_input, envelope.history_proof
    digest = configuration_hash(envelope.model_dump(mode="json"))
    before, source = data.original.full_annual_projection, data.selected_source
    first = data.as_of.astimezone(ZoneInfo(data.timezone)).date()
    reasons = list(data.source_issues)

    def unknown(*extra: str) -> FullPolicyHistoryFinancialImpact:
        return FullPolicyHistoryFinancialImpact(
            status="UNKNOWN",
            user_id=envelope.user_id,
            epoch_id=envelope.epoch_id,
            history_proof=proof,
            before=before,
            after=None,
            reasons=sorted(set(reasons + list(extra))),
            input_hash=digest,
        )

    if (
        before is None
        or data.original.status == "UNKNOWN"
        or not data.original.full_obligations_complete_within_registered_current_scope
    ):
        reasons.append("ORIGINAL_FULL_ANNUAL_OR_HISTORICAL_UNPAID_NOT_PROVEN")
    elif (
        before.safe_idle_cents is None
        or before.minimum_margin_cents is None
        or any(value is None for value in before.max_allocatable_by_product.values())
    ):
        reasons.append("ORIGINAL_FINANCIAL_VALUES_NOT_PROVEN")
    if source is None or source.template_name != "DatedExpensePolicy" or source.version_number <= 1:
        reasons.append("ONLY_REPEATED_FUTURE_DATED_HISTORY_SUPPORTED")
    elif proof is None or (
        proof.user_id != envelope.user_id
        or proof.epoch_id != envelope.epoch_id
        or not verify_future_dated_history(source, proof, data.as_of, data.timezone)
    ):
        reasons.append("COMPLETE_CURRENT_FUTURE_DATED_HISTORY_NOT_VERIFIED")
    else:
        current = DatedExpensePolicy.model_validate(source.configuration)
        if current.window.start <= first:
            reasons.append("CURRENT_DATED_TODAY_OR_OVERDUE_NOT_PROVEN")
        if source.valid_until is not None and source.valid_until <= data.as_of:
            reasons.append("CURRENT_DATED_VERSION_EXPIRED")
    if not data.candidate_references_verified:
        reasons.append("CANDIDATE_CURRENT_REFERENCES_NOT_PROVEN")
    if before is not None and before.calculation_trace:
        floor = before.calculation_trace[0].protected_cents_by_reason.get(
            "pending_cash_reservations"
        )
        if floor is None or floor < data.active_cash_claims_cents:
            reasons.append("ORIGINAL_ACTIVE_CASH_CLAIM_FLOOR_NOT_COMPLETE")
    if reasons:
        return unknown()
    assert before is not None and source is not None
    assert before.minimum_margin_cents is not None and before.safe_idle_cents is not None
    canonical = validate_full_configuration("DatedExpensePolicy", data.candidate_configuration)
    if canonical != data.candidate_configuration:
        raise ValueError("Candidate must retain the exact strict canonical configuration")
    phases = ("BEFORE_PAYMENT", "AFTER_PAYMENT", "AFTER_PRINCIPAL")
    expected = [(day, first + timedelta(days=day), phase) for day in range(366) for phase in phases]
    if [(point.day, point.date, point.phase) for point in before.calculation_trace] != expected:
        return unknown("ORIGINAL_COMPLETE_1098_PHASE_DENOMINATOR_NOT_VERIFIED")
    if (
        any(
            value < 0
            for p in before.calculation_trace
            for value in p.protected_cents_by_reason.values()
        )
        or len({row.account_id for row in data.cash_accounts}) != len(data.cash_accounts)
        or len({row.goal_id for row in data.goals}) != len(data.goals)
        or len({row.position_id for row in data.positions}) != len(data.positions)
        or len({row.product_id for row in data.products}) != len(data.products)
        or any(row.observed_at > data.as_of for row in data.cash_accounts)
    ):
        return unknown("ORIGINAL_UNIQUE_NONNEGATIVE_FINANCIAL_DENOMINATOR_NOT_VERIFIED")
    annual = data.original.original_annual_projection
    if [(point.day, point.date, point.phase) for point in annual.calculation_trace] != expected:
        return unknown("ORIGINAL_MVP_1098_PHASE_DENOMINATOR_NOT_VERIFIED")
    if sum(
        row.balance_cents for row in data.cash_accounts if row.account_type != "CREDIT_CARD"
    ) != annual.calculation_trace[0].cash_cents or sum(
        row.cash_owned_cents for row in data.goals
    ) > annual.calculation_trace[0].protected_cents_by_reason.get("goal_cash", -1):
        return unknown("CURRENT_CASH_OR_GOAL_FACTS_DISAGREE_WITH_ORIGINAL_ANNUAL")
    states = [row for row in data.original.policy_states if row.policy_id == source.policy_id]
    if (
        len(states) != 1
        or states[0].version_id != source.version_id
        or states[0].state not in {"INCLUDED", "OUTSIDE_HORIZON"}
    ):
        return unknown("SELECTED_CURRENT_VERSION_NOT_ORIGINAL_PROJECTED_VERSION")
    original_configuration = DatedExpensePolicy.model_validate(source.configuration)
    due = original_configuration.window.start
    expected_selected = []
    if due <= first + timedelta(days=365) and _calendar_active(
        source,
        max(due, source.valid_from.astimezone(ZoneInfo(data.timezone)).date()),
        ZoneInfo(data.timezone),
    ):
        expected_selected.append(
            FullProtectionOccurrence(
                occurrence_id=f"FULL:{source.policy_id}:{source.version_id}:DATED:{due.isoformat()}:{original_configuration.window.end.isoformat()}",
                policy_id=source.policy_id,
                policy_version_id=source.version_id,
                kind="DATED_EXPENSE",
                earliest_due_date=due,
                latest_due_date=original_configuration.window.end,
                hypothetical_payment_date=due,
                prepare_start_date=due,
                conservative_unpaid_cents=original_configuration.amount.max_cents,
                amount_basis="REGISTERED_MAX",
                source_account_id=None,
                payee_id=None,
                evidence_ids=source.evidence_ids,
                overdue=False,
            )
        )
    selected_occurrences = [
        row for row in data.original.occurrences if row.policy_id == source.policy_id
    ]
    if selected_occurrences != expected_selected or states[0].state != (
        "INCLUDED" if expected_selected else "OUTSIDE_HORIZON"
    ):
        return unknown("ORIGINAL_DATED_OCCURRENCE_DISAGREES_WITH_VERIFIED_CURRENT_VERSION")
    reservations = before.calculation_trace[0].protected_cents_by_reason[
        "pending_cash_reservations"
    ]
    originals = [_original(row) for row in data.original.occurrences]
    if _trace(annual, originals, reservations) != before.calculation_trace:
        return unknown("ORIGINAL_FULL_CURVE_NOT_RECONSTRUCTIBLE_FOR_THIS_HISTORY_BRANCH")
    accounts = {row.account_id: row for row in data.cash_accounts}
    required_accounts = {
        row.source_account_id for row in originals if row.source_account_id is not None
    }
    checks = data.original.source_account_checks
    if (
        len({row.account_id for row in checks}) != len(checks)
        or {row.account_id for row in checks} != required_accounts
    ):
        return unknown("ORIGINAL_COMPLETE_SOURCE_ACCOUNT_DENOMINATOR_NOT_VERIFIED")
    for check in checks:
        account = accounts.get(check.account_id)
        owned = sum(
            row.cash_owned_cents for row in data.goals if row.account_id == check.account_id
        )
        required = sum(
            row.amount_cents for row in originals if row.source_account_id == check.account_id
        )
        if account is None or account.account_type != "CASH":
            return unknown("ORIGINAL_PERIODIC_SOURCE_ACCOUNT_NOT_VERIFIED")
        remaining = account.balance_cents - owned - check.reserved_cash_cents - required
        if (
            check.actual_cash_cents != account.balance_cents
            or check.goal_owned_cash_cents != owned
            or check.reserved_cash_cents > reservations
            or check.registered_periodic_required_cents != required
            or check.remaining_current_cash_cents != remaining
            or check.state
            != ("SOURCE_LIQUIDITY_RISK" if remaining < 0 else "CURRENT_SOURCE_SUFFICIENT")
        ):
            return unknown("ORIGINAL_PERIODIC_CURRENT_SOURCE_CHECK_DISAGREES")
    if sum(row.reserved_cash_cents for row in checks) > reservations:
        return unknown("ORIGINAL_PERIODIC_RESERVATION_DENOMINATOR_DISAGREES")
    source_risk = any(row.state == "SOURCE_LIQUIDITY_RISK" for row in checks)
    old_worst = min(point.margin_cents for point in before.calculation_trace)
    old_caps = _caps(before.calculation_trace, data.products, source_risk)
    if (
        before.minimum_margin_cents != old_worst
        or before.safe_idle_cents != (max(0, old_worst) if not source_risk else 0)
        or before.max_allocatable_by_product != old_caps
        or before.status != ("LIQUIDITY_RISK" if old_worst < 0 or source_risk else "READY")
        or before.deficit_cents != max(0, -old_worst)
        or before.protected_cents_by_reason != before.calculation_trace[0].protected_cents_by_reason
    ):
        return unknown("ORIGINAL_FINANCIAL_CURVE_METRICS_DISAGREE")
    retained = [
        row for row in originals if row.policy_id != source.policy_id or row.due_date <= first
    ]
    try:
        candidate = _candidate(data, first, first + timedelta(days=365), set())
    except ValueError as error:
        return unknown(str(error))
    trace = _trace(annual, retained + candidate, reservations)
    worst = min(point.margin_cents for point in trace)
    caps = _caps(trace, data.products, source_risk)
    after = CandidateCurve(
        status="LIQUIDITY_RISK" if worst < 0 or source_risk else "READY",
        minimum_margin_cents=worst,
        safe_idle_cents=max(0, worst) if not source_risk else 0,
        max_allocatable_by_product=caps,
        calculation_trace=trace,
        source_account_limitations=[
            f"PERIODIC_SOURCE_LIQUIDITY_RISK:{row.account_id}"
            for row in checks
            if row.state == "SOURCE_LIQUIDITY_RISK"
        ],
        curve_hash=configuration_hash(
            {
                "protocol": "hypothetical-full-curve-history-v2",
                "input_hash": digest,
                "trace": [point.model_dump(mode="json") for point in trace],
            }
        ),
    )
    return FullPolicyHistoryFinancialImpact(
        status="PROJECTED",
        user_id=envelope.user_id,
        epoch_id=envelope.epoch_id,
        history_proof=proof,
        before=before,
        after=after,
        delta_safe_idle_cents=after.safe_idle_cents - before.safe_idle_cents,
        delta_minimum_margin_cents=worst - before.minimum_margin_cents,
        delta_max_allocatable_by_product={
            key: value - old_caps[key] for key, value in caps.items()
        },
        goals=[
            GoalPreviewImpact(
                goal_id=row.goal_id,
                current_owned_cash_cents=row.cash_owned_cents,
                current_owned_principal_cents=row.principal_owned_cents,
                original_evidence_ids=row.evidence_ids,
            )
            for row in data.goals
        ],
        positions=[
            PositionPreviewImpact(
                position_id=row.position_id,
                goal_id=row.goal_id,
                original_recorded_principal_cents=row.principal_cents,
                current_outstanding_principal_cents=0
                if row.status == "REDEEMED"
                else row.principal_cents,
                original_status=row.status,
                original_principal_available_at=row.principal_available_at,
                original_evidence_ids=row.evidence_ids,
            )
            for row in data.positions
        ],
        candidate_commitments=candidate,
        retained_original_occurrence_ids=sorted(row.identity for row in retained),
        input_hash=digest,
        limitations=[
            "ONLY_COMPLETE_VERIFIED_FUTURE_DATED_HISTORY_IS_SUPPORTED",
            "ORIGINAL_V1_INPUTS_MATH_AND_HASH_PROTOCOLS_UNCHANGED",
            "CURRENT_MVP_OTHER_FULL_PROTECTION_AND_CASH_CLAIMS_RETAINED",
            "NO_SETTLEMENT_REWRITE_CANDIDATE_PERMISSION_OR_FUTURE_ACCOUNT_DEBIT_PROOF",
            "FUTURE_GOAL_ALLOCATION_AND_ACTION_GENERATION_NOT_IMPLEMENTED",
            "ACTUAL_CONFIRMATION_REQUIRES_FRESH_SOURCE_AND_NEW_VERSION_RECOMPUTATION",
        ],
    )
