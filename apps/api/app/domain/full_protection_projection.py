"""Conservative registered FULL commitments layered onto an unchanged MVP curve."""

import json
from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from app.domain.boundary import compute_boundary
from app.domain.boundary_types import (
    BlockingConstraint,
    BoundaryModel,
    BoundaryPoint,
    BoundaryPolicyVersion,
    BoundaryPosition,
    BoundaryProduct,
    BoundaryResult,
    BoundarySnapshot,
)
from app.domain.full_policy_configuration import (
    DatedExpensePolicy,
    PeriodicTransferPolicy,
    SeasonalReservePolicy,
    validate_full_configuration,
)
from app.domain.full_seasonal_protection import (
    SeasonalProtectionBindings,
    derive_seasonal_protection_bindings,
)
from app.domain.policy_configuration import MoneyCents, configuration_hash
from pydantic import Field, StrictBool, StrictInt

ProtectionTemplate = Literal[
    "DatedExpensePolicy", "PeriodicTransferPolicy", "SeasonalReservePolicy"
]


class FullProtectedReference(BoundaryModel):
    policy_id: UUID
    version_id: UUID
    kind: Literal["MVP_POLICY", "FULL_POLICY"]
    content_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    current_confirmed: StrictBool
    evidence_ids: list[UUID]


class FullProtectionPolicySource(BoundaryModel):
    policy_id: UUID
    version_id: UUID
    version_number: Annotated[StrictInt, Field(ge=1)] = 1
    template_name: ProtectionTemplate
    configuration: dict[str, Any]
    content_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    confirmation: dict[str, Any]
    reference_snapshots: list[dict[str, Any]] = Field(default_factory=list)
    confirmed_at: datetime
    valid_from: datetime
    valid_until: datetime | None = None
    effective_status: str
    planning_confirmation_valid: StrictBool
    references_current: StrictBool
    evidence_ids: list[UUID]
    protected_references: list[FullProtectedReference] = Field(default_factory=list)


class FullProtectionProjectionInput(BoundaryModel):
    snapshot: BoundarySnapshot
    boundary_versions: list[BoundaryPolicyVersion]
    positions: list[BoundaryPosition]
    boundary_products: list[BoundaryProduct]
    policies: Annotated[list[FullProtectionPolicySource], Field(max_length=200)]
    reserved_cash_by_account: dict[UUID, MoneyCents] = Field(default_factory=dict)
    full_source_inventory_complete: StrictBool = True
    full_source_issues: list[str] = Field(default_factory=list)


class FullProtectionOccurrence(BoundaryModel):
    occurrence_id: str
    policy_id: UUID
    policy_version_id: UUID
    kind: Literal["DATED_EXPENSE", "PERIODIC_TRANSFER"]
    earliest_due_date: date
    latest_due_date: date
    hypothetical_payment_date: date
    prepare_start_date: date
    protection_starts_today: Literal[True] = True
    conservative_unpaid_cents: MoneyCents
    amount_basis: Literal["REGISTERED_MAX", "REGISTERED_EXACT"]
    original_paid_cents: Literal[None] = None
    settlement_status: Literal["UNSUPPORTED_CONSERVATIVE_UNPAID"] = (
        "UNSUPPORTED_CONSERVATIVE_UNPAID"
    )
    source_account_id: UUID | None
    payee_id: str | None
    evidence_ids: list[UUID]
    overdue: StrictBool
    bank_authority: Literal[False] = False


class FullPolicyProjectionState(BoundaryModel):
    policy_id: UUID
    version_id: UUID
    template_name: ProtectionTemplate
    state: Literal["INCLUDED", "OUTSIDE_HORIZON", "INACTIVE", "ADVICE_ONLY", "UNKNOWN"]
    reasons: list[str]


class FullSourceAccountCheck(BoundaryModel):
    account_id: UUID
    actual_cash_cents: MoneyCents
    goal_owned_cash_cents: MoneyCents
    reserved_cash_cents: MoneyCents
    registered_periodic_required_cents: MoneyCents
    remaining_current_cash_cents: int
    state: Literal["CURRENT_SOURCE_SUFFICIENT", "SOURCE_LIQUIDITY_RISK"]
    future_account_debits_complete: Literal[False] = False


class FullProtectionProjectionResult(BoundaryModel):
    algorithm_version: Literal[
        "registered-full-protection-v1",
        "registered-full-protection-future-dated-history-v2",
        "registered-full-protection-adopted-seasonal-v3",
        "registered-full-protection-ended-seasonal-v4",
    ] = "registered-full-protection-v1"
    planning_only: Literal[True] = True
    bank_authority: Literal[False] = False
    execution_support: Literal["NOT_IMPLEMENTED"] = "NOT_IMPLEMENTED"
    basis: Literal["REGISTERED_UPPER_BOUND_UNPAID_CONDITIONAL_CASH"] = (
        "REGISTERED_UPPER_BOUND_UNPAID_CONDITIONAL_CASH"
    )
    original_execution_view: BoundaryResult
    original_annual_projection: BoundaryResult
    full_annual_projection: BoundaryResult | None
    occurrences: list[FullProtectionOccurrence]
    policy_states: list[FullPolicyProjectionState]
    source_account_checks: list[FullSourceAccountCheck]
    status: Literal["READY", "LIQUIDITY_RISK", "UNKNOWN"]
    full_obligations_complete_within_registered_current_scope: StrictBool
    historical_full_settlement_complete: Literal[False] = False
    seasonal_adopted_adjustment_cents: MoneyCents | None = None
    seasonal_status: Literal[
        "NO_SEASONAL_POLICY",
        "ADVICE_ONLY_NO_ADOPTED_AMOUNT",
        "ADOPTED_PROTECTED",
        "ADOPTED_FLOOR_RELEASED",
        "ADOPTED_AMOUNT_UNKNOWN",
    ]
    future_income_in_current_cash_cents: Literal[0] = 0
    future_income_in_original_execution_cents: Literal[0] = 0
    future_income_status: Literal["NO_REGISTERED_PLANNING_INCOME_SOURCE"] = (
        "NO_REGISTERED_PLANNING_INCOME_SOURCE"
    )
    reasons: list[str]
    input_hash: str


def _calendar_active(source: FullProtectionPolicySource, day: date, zone: ZoneInfo) -> bool:
    # Both endpoints are retained at actual aware instants; valid_until is exclusive.
    beginning = datetime(day.year, day.month, day.day, tzinfo=zone)
    ending = beginning + timedelta(days=1)
    return source.valid_from < ending and (
        source.valid_until is None or source.valid_until > beginning
    )


def _periodic_days(first: date, last: date, due_day: int) -> list[date]:
    count = (last.year - first.year) * 12 + last.month - first.month + 1
    return [
        date(
            first.year + (first.month - 1 + offset) // 12,
            (first.month - 1 + offset) % 12 + 1,
            min(
                due_day,
                monthrange(
                    first.year + (first.month - 1 + offset) // 12,
                    (first.month - 1 + offset) % 12 + 1,
                )[1],
            ),
        )
        for offset in range(count)
    ]


def _verified_future_dated_history(
    source: FullProtectionPolicySource, now: datetime, timezone: str
) -> bool:
    from app.domain.full_future_dated_history import (
        REFERENCE_KIND,
        FutureDatedHistoryProof,
        verify_future_dated_history,
    )

    proofs = [row for row in source.reference_snapshots if row.get("kind") == REFERENCE_KIND]
    if source.template_name != "DatedExpensePolicy" or len(proofs) != 1:
        return False
    try:
        wrapper = proofs[0]
        if set(wrapper) != {"kind", "proof"}:
            return False
        proof = FutureDatedHistoryProof.model_validate_json(json.dumps(wrapper["proof"]))
        return verify_future_dated_history(source, proof, now, timezone)
    except (ValueError, TypeError, KeyError, OverflowError):
        return False


def project_full_protection(data: FullProtectionProjectionInput) -> FullProtectionProjectionResult:
    # The frozen ended-proof validator imports these already-defined source DTOs.
    # Import the opt-in v4 consumer after the DTO module has finished initialization.
    from app.domain.full_seasonal_current_protection import (
        CurrentSeasonalProtectionBindings,
        derive_current_seasonal_protection_bindings,
    )
    from app.domain.full_seasonal_ended_adoption import (
        REFERENCE_KIND as ENDED_SEASONAL_REFERENCE_KIND,
    )

    data = FullProtectionProjectionInput.model_validate(data.model_dump())
    if len({row.policy_id for row in data.policies}) != len(data.policies) or len(
        {row.version_id for row in data.policies}
    ) != len(data.policies):
        raise ValueError("Only one actual current version per FULL policy is supported")
    now = data.snapshot.as_of
    zone = ZoneInfo(data.snapshot.timezone)
    first = now.astimezone(zone).date()
    last = first + timedelta(days=365)
    execution = compute_boundary(
        data.snapshot.model_copy(update={"horizon_days": 90}),
        data.boundary_versions,
        data.positions,
        data.boundary_products,
    )
    annual = compute_boundary(
        data.snapshot.model_copy(update={"horizon_days": 365}),
        data.boundary_versions,
        data.positions,
        data.boundary_products,
    )
    reasons: list[str] = []
    occurrences: list[FullProtectionOccurrence] = []
    states: list[FullPolicyProjectionState] = []
    seasonal = False
    ended_requested = any(
        row.get("kind") == ENDED_SEASONAL_REFERENCE_KIND
        for source in data.policies
        for row in source.reference_snapshots
    )
    seasonal_bindings: SeasonalProtectionBindings | CurrentSeasonalProtectionBindings = (
        derive_current_seasonal_protection_bindings(data.policies, now, data.snapshot.timezone)
        if ended_requested
        else derive_seasonal_protection_bindings(data.policies, now, data.snapshot.timezone)
    )
    reasons.extend(seasonal_bindings.reasons)
    adopted_sources = {proof.policy_id for proof in seasonal_bindings.proofs}
    ended_sources = (
        {proof.policy_id for proof in seasonal_bindings.ended_proofs}
        if isinstance(seasonal_bindings, CurrentSeasonalProtectionBindings)
        else set()
    )
    adopted_sources.update(ended_sources)
    history_sources = {
        row.policy_id
        for row in data.policies
        if row.version_number > 1
        and _verified_future_dated_history(row, now, data.snapshot.timezone)
    }
    algorithm: Literal[
        "registered-full-protection-v1",
        "registered-full-protection-future-dated-history-v2",
        "registered-full-protection-adopted-seasonal-v3",
        "registered-full-protection-ended-seasonal-v4",
    ] = (
        "registered-full-protection-ended-seasonal-v4"
        if ended_requested
        else "registered-full-protection-adopted-seasonal-v3"
        if seasonal_bindings.requested
        else "registered-full-protection-future-dated-history-v2"
        if history_sources
        else "registered-full-protection-v1"
    )
    unknown = (
        annual.status == "INSUFFICIENT_EVIDENCE"
        or not data.full_source_inventory_complete
        or bool(data.full_source_issues)
        or bool(seasonal_bindings.reasons)
    )
    accounts = {row.account_id: row for row in data.snapshot.cash_accounts}
    if not set(data.reserved_cash_by_account) <= set(accounts):
        raise ValueError("Reservations must belong to actual included cash accounts")
    reservation_floor = sum(
        amount
        for identity, amount in data.reserved_cash_by_account.items()
        if accounts[identity].account_type == "CASH"
    )
    for source in sorted(data.policies, key=lambda row: str(row.policy_id)):
        config = validate_full_configuration(source.template_name, source.configuration)
        if (
            configuration_hash(config) != source.content_hash
            or source.confirmed_at > now
            or source.valid_until is not None
            and source.valid_until <= source.valid_from
        ):
            raise ValueError("FULL original configuration, confirmation or validity does not match")
        if (
            source.confirmation.get("accepted") is not True
            or source.confirmation.get("reviewed_hash") != source.content_hash
        ):
            raise ValueError("Exact FULL configuration confirmation is required")
        state: Literal["INCLUDED", "OUTSIDE_HORIZON", "INACTIVE", "ADVICE_ONLY", "UNKNOWN"] = (
            "INCLUDED"
        )
        notes: list[str] = []
        before_count = len(occurrences)
        if source.effective_status in {"ARCHIVED", "REVOKED", "SUSPENDED", "EXPIRED"}:
            state = "INACTIVE"
            notes.append("CURRENT_STATE_STOPS_NEW_FUTURE_COMMITMENTS")
            if (
                source.effective_status != "ARCHIVED"
                and source.template_name != "SeasonalReservePolicy"
                and source.valid_from.astimezone(zone).date() < first
            ):
                unknown = True
                state = "UNKNOWN"
                notes.append("HISTORICAL_FULL_UNPAID_COVERAGE_UNSUPPORTED")
        elif (
            not source.planning_confirmation_valid
            or not source.references_current
            or source.effective_status not in {"ACTIVE", "CONFIRMED"}
        ):
            unknown = True
            state = "UNKNOWN"
            notes.append("CURRENT_FULL_CONFIRMATION_OR_REFERENCES_NOT_PROVEN")
        elif (
            source.version_number > 1
            and source.template_name != "SeasonalReservePolicy"
            and source.policy_id not in history_sources
        ):
            unknown = True
            state = "UNKNOWN"
            notes.append("PRIOR_FULL_VERSION_UNPAID_HISTORY_NOT_PROVEN")
        elif source.template_name == "SeasonalReservePolicy":
            SeasonalReservePolicy.model_validate(config)
            seasonal = True
            if source.policy_id in adopted_sources:
                notes.append(
                    "ORIGINAL_ENDED_ADOPTION_RELEASES_FLOOR_WITHOUT_CASH_CREDIT"
                    if source.policy_id in ended_sources
                    else "ORIGINAL_USER_ADOPTION_PROTECTS_CASH_WITHOUT_PAYMENT"
                )
            elif any(
                row.get("kind") == "VERIFIED_SEASONAL_ADOPTION"
                for row in source.reference_snapshots
            ):
                state = "UNKNOWN"
                notes.append("CURRENT_SEASONAL_ADOPTION_NOT_PROVEN")
            else:
                state = "ADVICE_ONLY"
                notes.append("ADOPTED_EXTRA_AMOUNT_NOT_PROVIDED_NOT_DERIVED_FROM_QUANTILE")
        elif source.template_name == "DatedExpensePolicy":
            dated = DatedExpensePolicy.model_validate(config)
            refs = {row.policy_id: row for row in source.protected_references}
            if (
                len(refs) != len(source.protected_references)
                or set(dated.must_not_reduce_policy_ids) != set(refs)
                or any(not row.current_confirmed for row in refs.values())
            ):
                unknown = True
                state = "UNKNOWN"
                notes.append("MUST_NOT_REDUCE_CURRENT_REFERENCES_NOT_PROVEN")
            else:
                due = dated.window.start
                payment = max(first, due)
                if payment <= last and _calendar_active(
                    source, max(due, source.valid_from.astimezone(zone).date()), zone
                ):
                    occurrences.append(
                        FullProtectionOccurrence(
                            occurrence_id=f"FULL:{source.policy_id}:{source.version_id}:DATED:{due.isoformat()}:{dated.window.end.isoformat()}",
                            policy_id=source.policy_id,
                            policy_version_id=source.version_id,
                            kind="DATED_EXPENSE",
                            earliest_due_date=due,
                            latest_due_date=dated.window.end,
                            hypothetical_payment_date=payment,
                            prepare_start_date=due,
                            conservative_unpaid_cents=dated.amount.max_cents,
                            amount_basis="REGISTERED_MAX",
                            source_account_id=None,
                            payee_id=None,
                            evidence_ids=source.evidence_ids,
                            overdue=due < first,
                        )
                    )
        else:
            periodic = PeriodicTransferPolicy.model_validate(config)
            account = accounts.get(periodic.source_account_id)
            if account is None or account.account_type != "CASH":
                unknown = True
                state = "UNKNOWN"
                notes.append("ACTUAL_PERIODIC_SOURCE_ACCOUNT_NOT_PROVEN")
            else:
                if source.valid_from.astimezone(zone).date() < first.replace(day=1):
                    unknown = True
                    state = "UNKNOWN"
                    notes.append("HISTORICAL_FULL_UNPAID_COVERAGE_UNSUPPORTED")
                amount = (
                    periodic.amount_rule.amount_cents
                    if periodic.amount_rule.kind == "exact"
                    else periodic.amount_rule.max_cents
                )
                for due in _periodic_days(first, last, periodic.due_day):
                    # Current version's dates are used; no future authority or earlier
                    # version is inferred. A known current-month unpaid date is retained.
                    if due > last or not _calendar_active(source, due, zone):
                        continue
                    prepare = max(
                        source.valid_from.astimezone(zone).date(),
                        due - timedelta(days=periodic.prepare_days_before),
                    )
                    occurrences.append(
                        FullProtectionOccurrence(
                            occurrence_id=f"FULL:{source.policy_id}:{source.version_id}:MONTH:{due:%Y-%m}",
                            policy_id=source.policy_id,
                            policy_version_id=source.version_id,
                            kind="PERIODIC_TRANSFER",
                            earliest_due_date=due,
                            latest_due_date=due,
                            hypothetical_payment_date=max(first, due),
                            prepare_start_date=prepare,
                            conservative_unpaid_cents=amount,
                            amount_basis="REGISTERED_EXACT"
                            if periodic.amount_rule.kind == "exact"
                            else "REGISTERED_MAX",
                            source_account_id=periodic.source_account_id,
                            payee_id=periodic.payee_id,
                            evidence_ids=source.evidence_ids,
                            overdue=due < first,
                        )
                    )
        if state == "INCLUDED" and len(occurrences) == before_count:
            state = "OUTSIDE_HORIZON"
            notes.append("NO_REGISTERED_DUE_DATE_IN_CURRENT_365_DAY_HORIZON")
        states.append(
            FullPolicyProjectionState(
                policy_id=source.policy_id,
                version_id=source.version_id,
                template_name=source.template_name,
                state=state,
                reasons=notes,
            )
        )
    if len(occurrences) > 2600:
        raise ValueError("FULL commitment occurrence capacity is 2600")
    occurrences.sort(key=lambda row: (row.hypothetical_payment_date, row.occurrence_id))
    checks = []
    for identity in sorted(
        {row.source_account_id for row in occurrences if row.source_account_id is not None}
    ):
        account = accounts[identity]
        goal_cash = sum(
            row.cash_owned_cents for row in data.snapshot.goals if row.account_id == identity
        )
        reserved = data.reserved_cash_by_account.get(identity, 0)
        required = sum(
            row.conservative_unpaid_cents
            for row in occurrences
            if row.source_account_id == identity
        )
        remaining = account.balance_cents - goal_cash - reserved - required
        checks.append(
            FullSourceAccountCheck(
                account_id=identity,
                actual_cash_cents=account.balance_cents,
                goal_owned_cash_cents=goal_cash,
                reserved_cash_cents=reserved,
                registered_periodic_required_cents=required,
                remaining_current_cash_cents=remaining,
                state="CURRENT_SOURCE_SUFFICIENT" if remaining >= 0 else "SOURCE_LIQUIDITY_RISK",
            )
        )
    full = None
    if not unknown:
        outstanding = {row.occurrence_id: row for row in occurrences}
        paid = 0
        trace: list[BoundaryPoint] = []
        for point in annual.calculation_trace:
            if point.phase == "AFTER_PAYMENT":
                current = [
                    key
                    for key, row in outstanding.items()
                    if row.hypothetical_payment_date == point.date
                ]
                paid += sum(outstanding.pop(key).conservative_unpaid_cents for key in current)
            protection = dict(point.protected_cents_by_reason)
            protection.update(
                {
                    "full_dated_expense": sum(
                        row.conservative_unpaid_cents
                        for row in outstanding.values()
                        if row.kind == "DATED_EXPENSE"
                    ),
                    "full_periodic_transfer": sum(
                        row.conservative_unpaid_cents
                        for row in outstanding.values()
                        if row.kind == "PERIODIC_TRANSFER"
                    ),
                    "pending_cash_reservations": reservation_floor,
                }
            )
            if seasonal_bindings.requested:
                protection["full_seasonal_adopted"] = seasonal_bindings.amount_on(point.date)
            cash = point.cash_cents - paid
            trace.append(
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
        worst = min(trace, key=lambda row: row.margin_cents)
        source_risk = any(row.state == "SOURCE_LIQUIDITY_RISK" for row in checks)
        constraints = []
        if worst.margin_cents < 0:
            constraints.append(
                BlockingConstraint(
                    code="FULL_CASH_DEFICIT",
                    date=worst.date,
                    required_cents=sum(worst.protected_cents_by_reason.values()),
                    available_cents=worst.cash_cents,
                )
            )
        for check in checks:
            if check.state == "SOURCE_LIQUIDITY_RISK":
                constraints.append(
                    BlockingConstraint(
                        code="PERIODIC_SOURCE_LIQUIDITY_LIMIT",
                        entity_id=str(check.account_id),
                        required_cents=check.registered_periodic_required_cents,
                        available_cents=check.actual_cash_cents
                        - check.goal_owned_cash_cents
                        - check.reserved_cash_cents,
                    )
                )
        caps: dict[str, int | None] = {}
        for product in data.boundary_products:
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
            caps[str(product.product_id)] = (
                max(0, min(margins)) if worst.margin_cents >= 0 and not source_risk else 0
            )
        full = BoundaryResult(
            algorithm_version=algorithm,
            status="LIQUIDITY_RISK" if worst.margin_cents < 0 or source_risk else "READY",
            safe_idle_cents=max(0, worst.margin_cents) if not source_risk else 0,
            minimum_margin_cents=worst.margin_cents,
            deficit_cents=max(0, -worst.margin_cents),
            protected_cents_by_reason=trace[0].protected_cents_by_reason,
            max_allocatable_by_product=caps,
            blocking_constraints=constraints,
            calculation_trace=trace,
            boundary_hash=configuration_hash(
                {
                    "algorithm": algorithm,
                    "original_annual_hash": annual.boundary_hash,
                    "input": data.model_dump(mode="json"),
                }
            ),
            calculation_notes=[
                "FULL_MAX_IS_A_CONSERVATIVE_UNPAID_BOUND_NOT_AN_ACTUAL_INVOICE",
                "PREPARE_DAYS_DO_NOT_POSTPONE_CURRENT_PROTECTION",
                "NO_FULL_BANK_AUTHORITY_OR_SETTLED_FUTURE_CASH",
                "FUTURE_ACCOUNT_DEBITS_FROM_MVP_ARE_NOT_INDEPENDENTLY_ALLOCATED",
            ],
        )
    if unknown:
        reasons.append("FULL_ORIGINAL_OR_HISTORICAL_COVERAGE_NOT_PROVEN")
    return FullProtectionProjectionResult(
        algorithm_version=algorithm,
        original_execution_view=execution,
        original_annual_projection=annual,
        full_annual_projection=full,
        occurrences=occurrences,
        policy_states=states,
        source_account_checks=checks,
        status=("READY" if full.status == "READY" else "LIQUIDITY_RISK") if full else "UNKNOWN",
        full_obligations_complete_within_registered_current_scope=not unknown,
        seasonal_status=(
            "ADOPTED_AMOUNT_UNKNOWN"
            if unknown
            else "ADOPTED_FLOOR_RELEASED"
            if ended_sources and not seasonal_bindings.proofs
            else "ADOPTED_PROTECTED"
        )
        if seasonal_bindings.requested
        else ("ADVICE_ONLY_NO_ADOPTED_AMOUNT" if seasonal else "NO_SEASONAL_POLICY"),
        seasonal_adopted_adjustment_cents=(
            seasonal_bindings.amount_on(first)
            if seasonal_bindings.requested and not unknown
            else None
        ),
        reasons=reasons,
        input_hash=configuration_hash(data.model_dump(mode="json")),
    )
