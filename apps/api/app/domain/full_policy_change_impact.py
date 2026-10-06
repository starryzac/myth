"""Hypothetical FULL commitments over an unchanged, verified original annual curve.

Candidates are not policy versions, confirmations, settlements, or bank grants.
"""

import calendar
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from app.domain.boundary_types import (
    BoundaryModel,
    BoundaryPoint,
    BoundaryPosition,
    BoundaryProduct,
    BoundaryResult,
    CashFact,
    GoalOwnership,
)
from app.domain.full_policy_configuration import (
    DatedExpensePolicy,
    PeriodicTransferPolicy,
    validate_full_configuration,
)
from app.domain.full_protection_projection import (
    FullProtectionOccurrence,
    FullProtectionPolicySource,
    FullProtectionProjectionResult,
)
from app.domain.policy_configuration import MoneyCents, configuration_hash
from pydantic import Field, StrictBool, StrictInt


class HypotheticalCommitment(BoundaryModel):
    identity: str
    original_occurrence_id: str | None
    policy_id: UUID
    source_kind: Literal["RETAINED_ORIGINAL", "UNCONFIRMED_CANDIDATE"]
    kind: Literal["DATED_EXPENSE", "PERIODIC_TRANSFER"]
    due_date: date
    amount_cents: MoneyCents
    source_account_id: UUID | None
    bank_authority: Literal[False] = False


class GoalPreviewImpact(BoundaryModel):
    goal_id: UUID
    current_owned_cash_cents: MoneyCents
    current_owned_principal_cents: MoneyCents
    current_allocation_delta_cents: Literal[0] = 0
    current_principal_delta_cents: Literal[0] = 0
    future_allocation_cents: None = None
    future_allocation_status: Literal["UNKNOWN_NO_CANDIDATE_GOAL_SOLVER"] = (
        "UNKNOWN_NO_CANDIDATE_GOAL_SOLVER"
    )
    original_evidence_ids: list[UUID]


class PositionPreviewImpact(BoundaryModel):
    position_id: UUID
    goal_id: UUID | None
    original_recorded_principal_cents: MoneyCents
    current_outstanding_principal_cents: MoneyCents
    current_principal_delta_cents: Literal[0] = 0
    original_status: str
    original_principal_available_at: datetime | None
    original_evidence_ids: list[UUID]
    future_disposition_status: Literal["UNKNOWN_NO_CANDIDATE_ACTION_GENERATION"] = (
        "UNKNOWN_NO_CANDIDATE_ACTION_GENERATION"
    )


class CandidateCurve(BoundaryModel):
    status: Literal["READY", "LIQUIDITY_RISK"]
    minimum_margin_cents: StrictInt
    safe_idle_cents: MoneyCents
    max_allocatable_by_product: dict[str, int]
    calculation_trace: Annotated[list[BoundaryPoint], Field(min_length=1098, max_length=1098)]
    source_account_limitations: list[str]
    curve_hash: str
    financial_capacity_is_authority: Literal[False] = False


class FullPolicyImpactInput(BoundaryModel):
    as_of: datetime
    timezone: Literal["Asia/Shanghai", "UTC"]
    selected_source: FullProtectionPolicySource | None
    candidate_configuration: dict[str, Any]
    candidate_valid_from: datetime
    candidate_valid_until: datetime | None
    candidate_references_verified: StrictBool
    original: FullProtectionProjectionResult
    cash_accounts: list[CashFact]
    goals: list[GoalOwnership]
    positions: list[BoundaryPosition] = Field(default_factory=list)
    products: list[BoundaryProduct]
    active_cash_claims_cents: MoneyCents = 0
    source_issues: list[str] = Field(default_factory=list)


class FullPolicyFinancialImpact(BoundaryModel):
    protocol: Literal["full-policy-financial-impact-v1"] = "full-policy-financial-impact-v1"
    simulation: Literal[True] = True
    hypothetical: Literal[True] = True
    grants_authority: Literal[False] = False
    writes_policy_or_bank: Literal[False] = False
    future_income_cents: Literal[0] = 0
    horizon_days: Literal[365] = 365
    initial_day_and_365_future_days: Literal[True] = True
    status: Literal["PROJECTED", "UNKNOWN"]
    basis: Literal["FUTURE_ONLY_CONSERVATIVE_UNPAID_REPLACEMENT"] = (
        "FUTURE_ONLY_CONSERVATIVE_UNPAID_REPLACEMENT"
    )
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


def _original(row: FullProtectionOccurrence) -> HypotheticalCommitment:
    return HypotheticalCommitment(
        identity=row.occurrence_id,
        original_occurrence_id=row.occurrence_id,
        policy_id=row.policy_id,
        source_kind="RETAINED_ORIGINAL",
        kind=row.kind,
        due_date=row.hypothetical_payment_date,
        amount_cents=row.conservative_unpaid_cents,
        source_account_id=row.source_account_id,
    )


def _trace(
    original: BoundaryResult, commitments: list[HypotheticalCommitment], reservation_floor: int
) -> list[BoundaryPoint]:
    outstanding = {row.identity: row for row in commitments}
    if len(outstanding) != len(commitments):
        raise ValueError("Commitment identities must be unique")
    paid = 0
    result = []
    for point in original.calculation_trace:
        if point.phase == "AFTER_PAYMENT":
            payable = [key for key, row in outstanding.items() if row.due_date == point.date]
            paid += sum(outstanding.pop(key).amount_cents for key in payable)
        protection = {
            **point.protected_cents_by_reason,
            "full_dated_expense": sum(
                row.amount_cents for row in outstanding.values() if row.kind == "DATED_EXPENSE"
            ),
            "full_periodic_transfer": sum(
                row.amount_cents for row in outstanding.values() if row.kind == "PERIODIC_TRANSFER"
            ),
            "pending_cash_reservations": reservation_floor,
        }
        cash = point.cash_cents - paid
        result.append(
            BoundaryPoint(
                day=point.day,
                date=point.date,
                phase=point.phase,
                cash_cents=cash,
                protected_cents_by_reason=protection,
                margin_cents=cash - sum(protection.values()),
                obligation_occurrence_ids=sorted(
                    point.obligation_occurrence_ids + list(outstanding)
                ),
                principal_position_ids=point.principal_position_ids,
            )
        )
    return result


def _candidate(
    data: FullPolicyImpactInput, first: date, last: date, frozen_periods: set[str]
) -> list[HypotheticalCommitment]:
    source = data.selected_source
    assert source is not None
    zone = ZoneInfo(data.timezone)
    start = max(first + timedelta(days=1), data.candidate_valid_from.astimezone(zone).date())
    stop = (
        data.candidate_valid_until.astimezone(zone).date() if data.candidate_valid_until else None
    )
    days: list[date] = []
    config = data.candidate_configuration
    kind: Literal["DATED_EXPENSE", "PERIODIC_TRANSFER"]
    if source.template_name == "DatedExpensePolicy":
        typed = DatedExpensePolicy.model_validate(config)
        if typed.window.start <= first:
            raise ValueError("CANDIDATE_DATED_TODAY_OR_HISTORY_NOT_PROVEN")
        days = [typed.window.start]
        amount, account, kind = typed.amount.max_cents, None, "DATED_EXPENSE"
    else:
        periodic = PeriodicTransferPolicy.model_validate(config)
        month = first.replace(day=1)
        while month <= last:
            due = month.replace(
                day=min(periodic.due_day, calendar.monthrange(month.year, month.month)[1])
            )
            if due.strftime("%Y-%m") not in frozen_periods:
                if due <= first and due >= data.candidate_valid_from.astimezone(zone).date():
                    raise ValueError("CANDIDATE_PERIOD_ALREADY_DUE_NOT_PROVEN")
                days.append(due)
            month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
        amount = (
            periodic.amount_rule.amount_cents
            if periodic.amount_rule.kind == "exact"
            else periodic.amount_rule.max_cents
        )
        account, kind = periodic.source_account_id, "PERIODIC_TRANSFER"
    return [
        HypotheticalCommitment(
            identity=f"CANDIDATE:{source.policy_id}:{configuration_hash(config)}:{day.isoformat()}",
            original_occurrence_id=None,
            policy_id=source.policy_id,
            source_kind="UNCONFIRMED_CANDIDATE",
            kind=kind,
            due_date=day,
            amount_cents=amount,
            source_account_id=account,
        )
        for day in days
        if start <= day <= last and (stop is None or day < stop)
    ]


def project_full_policy_change(data: FullPolicyImpactInput) -> FullPolicyFinancialImpact:
    """Replace only future original FULL commitments; never synthesize source confirmation."""
    data = FullPolicyImpactInput.model_validate(data.model_dump())
    digest = configuration_hash(data.model_dump(mode="json"))
    before = data.original.full_annual_projection
    reasons = list(data.source_issues)
    source = data.selected_source
    if before is None or data.original.status == "UNKNOWN":
        reasons.append("ORIGINAL_FULL_ANNUAL_OR_HISTORICAL_UNPAID_NOT_PROVEN")
    elif (
        before.safe_idle_cents is None
        or before.minimum_margin_cents is None
        or any(value is None for value in before.max_allocatable_by_product.values())
    ):
        reasons.append("ORIGINAL_FINANCIAL_VALUES_NOT_PROVEN")
    if source is None:
        reasons.append("TEMPLATE_NOT_SUPPORTED_FOR_FINANCIAL_CHANGE")
    elif source.template_name not in {"DatedExpensePolicy", "PeriodicTransferPolicy"}:
        reasons.append("TEMPLATE_NOT_SUPPORTED_FOR_FINANCIAL_CHANGE")
    elif source.version_number != 1 or source.effective_status not in {"ACTIVE", "CONFIRMED"}:
        reasons.append("CURRENT_OR_PRIOR_VERSION_UNPAID_NOT_PROVEN")
    elif (
        not source.planning_confirmation_valid
        or not source.references_current
        or source.confirmed_at > data.as_of
        or source.confirmation.get("accepted") is not True
        or source.confirmation.get("reviewed_hash") != source.content_hash
    ):
        reasons.append("ORIGINAL_CURRENT_CONFIRMATION_NOT_PROVEN")
    if not data.candidate_references_verified:
        reasons.append("CANDIDATE_CURRENT_REFERENCES_NOT_PROVEN")
    if before is not None and before.calculation_trace:
        original_claim_floor = before.calculation_trace[0].protected_cents_by_reason.get(
            "pending_cash_reservations"
        )
        if original_claim_floor is None or original_claim_floor < data.active_cash_claims_cents:
            reasons.append("ORIGINAL_ACTIVE_CASH_CLAIM_FLOOR_NOT_COMPLETE")
    if reasons:
        return FullPolicyFinancialImpact(
            status="UNKNOWN",
            before=before,
            after=None,
            reasons=sorted(set(reasons)),
            input_hash=digest,
        )
    assert before is not None and source is not None
    assert before.safe_idle_cents is not None and before.minimum_margin_cents is not None
    if configuration_hash(source.configuration) != source.content_hash:
        raise ValueError("Original selected configuration hash differs")
    canonical = validate_full_configuration(source.template_name, data.candidate_configuration)
    if canonical != data.candidate_configuration:
        raise ValueError("Candidate must be the exact original strict canonical configuration")
    first = data.as_of.astimezone(ZoneInfo(data.timezone)).date()
    phases = ("BEFORE_PAYMENT", "AFTER_PAYMENT", "AFTER_PRINCIPAL")
    expected = [(day, first + timedelta(days=day), phase) for day in range(366) for phase in phases]
    if [(row.day, row.date, row.phase) for row in before.calculation_trace] != expected:
        raise ValueError("Original complete annual phase denominator is required")
    if (
        any(
            amount < 0
            for row in before.calculation_trace
            for amount in row.protected_cents_by_reason.values()
        )
        or len({row.account_id for row in data.cash_accounts}) != len(data.cash_accounts)
        or len({row.goal_id for row in data.goals}) != len(data.goals)
        or len({row.position_id for row in data.positions}) != len(data.positions)
        or len({row.product_id for row in data.products}) != len(data.products)
        or any(row.observed_at > data.as_of for row in data.cash_accounts)
    ):
        raise ValueError("Original financial facts must retain a unique nonnegative denominator")
    original_first = data.original.original_annual_projection.calculation_trace[0]
    if (
        sum(row.balance_cents for row in data.cash_accounts if row.account_type != "CREDIT_CARD")
        != original_first.cash_cents
        or sum(row.cash_owned_cents for row in data.goals)
        > original_first.protected_cents_by_reason["goal_cash"]
    ):
        raise ValueError("Cash and goal facts must match the original annual projection")
    selected_states = [
        row for row in data.original.policy_states if row.policy_id == source.policy_id
    ]
    if (
        len(selected_states) != 1
        or selected_states[0].version_id != source.version_id
        or selected_states[0].state not in {"INCLUDED", "OUTSIDE_HORIZON"}
    ):
        raise ValueError("Selected source must match the exact original projected policy version")
    reservations = before.calculation_trace[0].protected_cents_by_reason[
        "pending_cash_reservations"
    ]
    originals = [_original(row) for row in data.original.occurrences]
    if (
        _trace(data.original.original_annual_projection, originals, reservations)
        != before.calculation_trace
    ):
        raise ValueError("Original curve, occurrences and reservation floor disagree")
    retained = [
        row for row in originals if row.policy_id != source.policy_id or row.due_date <= first
    ]
    frozen_periods = {
        row.due_date.strftime("%Y-%m")
        for row in retained
        if row.policy_id == source.policy_id and row.kind == "PERIODIC_TRANSFER"
    }
    try:
        candidate = _candidate(data, first, first + timedelta(days=365), frozen_periods)
    except ValueError as error:
        return FullPolicyFinancialImpact(
            status="UNKNOWN", before=before, after=None, reasons=[str(error)], input_hash=digest
        )
    commitments = retained + candidate
    trace = _trace(data.original.original_annual_projection, commitments, reservations)
    worst = min(row.margin_cents for row in trace)
    accounts = {row.account_id: row for row in data.cash_accounts}
    limitations = []
    source_risk = False
    for identity in {
        row.source_account_id for row in commitments if row.source_account_id is not None
    }:
        account = accounts.get(identity)
        if account is None or account.account_type != "CASH":
            return FullPolicyFinancialImpact(
                status="UNKNOWN",
                before=before,
                after=None,
                reasons=["CANDIDATE_SOURCE_ACCOUNT_NOT_PROVEN"],
                input_hash=digest,
            )
        owned = sum(row.cash_owned_cents for row in data.goals if row.account_id == identity)
        required = sum(row.amount_cents for row in commitments if row.source_account_id == identity)
        if account.balance_cents - owned - reservations < required:
            source_risk = True
            limitations.append(f"PERIODIC_SOURCE_LIQUIDITY_RISK:{identity}")
    caps = {}
    old_caps: dict[str, int] = {}
    for key, value in before.max_allocatable_by_product.items():
        assert value is not None
        old_caps[key] = value
    if {str(row.product_id) for row in data.products} != set(before.max_allocatable_by_product):
        raise ValueError("Original full product denominator must be retained")
    for product in data.products:
        return_day = (
            product.fixed_return.term_days + product.fixed_return.settlement_delay_days
            if product.fixed_return
            else 366
        )
        margins = [
            row.margin_cents
            for row in trace
            if row.day < return_day or row.day == return_day and row.phase != "AFTER_PRINCIPAL"
        ]
        caps[str(product.product_id)] = (
            max(0, min(margins)) if worst >= 0 and not source_risk else 0
        )
    after = CandidateCurve(
        status="LIQUIDITY_RISK" if worst < 0 or source_risk else "READY",
        minimum_margin_cents=worst,
        safe_idle_cents=max(0, worst) if not source_risk else 0,
        max_allocatable_by_product=caps,
        calculation_trace=trace,
        source_account_limitations=limitations,
        curve_hash=configuration_hash(
            {
                "protocol": "hypothetical-full-curve-v1",
                "input_hash": digest,
                "trace": [row.model_dump(mode="json") for row in trace],
            }
        ),
    )
    return FullPolicyFinancialImpact(
        status="PROJECTED",
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
                current_outstanding_principal_cents=(
                    0 if row.status == "REDEEMED" else row.principal_cents
                ),
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
            "TODAY_AND_OVERDUE_ORIGINAL_COMMITMENTS_RETAINED",
            "CURRENT_MVP_AND_OTHER_FULL_PROTECTION_AND_CLAIMS_RETAINED",
            "CURRENT_SOURCE_CHECK_NOT_A_FUTURE_ACCOUNT_DEBIT_OR_EXECUTION_PROOF",
            "NO_SETTLEMENT_REWRITE_OR_CANDIDATE_PERMISSION",
            "ACTUAL_CONFIRMATION_REQUIRES_FRESH_SOURCE_AND_NEW_VERSION_RECOMPUTATION",
            "FUTURE_GOAL_ALLOCATION_AND_ACTION_GENERATION_NOT_IMPLEMENTED",
        ],
    )
