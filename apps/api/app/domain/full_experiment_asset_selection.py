"""Source-bound GENERAL purchase proposals, without changing P or bank contracts."""

from datetime import datetime, timedelta
from hashlib import sha256
from typing import Annotated, Literal, Self
from uuid import UUID

from app.domain.asset_allocation_types import (
    AssetCashUse,
    AssetProductTerms,
    FixedPrincipalTerms,
    PlannedExit,
    PlannedPrincipalTerms,
)
from app.domain.boundary_types import BoundaryModel, BoundaryResult
from app.domain.full_asset_allocation import (
    FullAssetBatch,
    FullAssetPlanningInput,
    FullAssetPlanningResult,
    _catalog_class,
)
from app.domain.full_asset_execution import FullAssetCatalogueReference, FullAssetPrepareRequest
from app.domain.full_experiment_mechanisms import (
    Candidate,
    ConstraintFacts,
    MechanismDecision,
    MechanismInput,
    Rule,
    World,
    decide_full_mechanism,
    value_digest,
)
from app.domain.policy_configuration import MoneyCents, configuration_hash, validate_configuration
from pydantic import Field, StrictInt, StrictStr, model_validator

Hash = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
PROTOCOL = "full-current-general-mechanism-selection-v1"


class RegisteredFullMechanismRule(BoundaryModel):
    """Private file locator. Its bytes are author input, never a financial grant."""

    original_path: StrictStr
    sha256: Hash


class FullMechanismRuleOriginal(BoundaryModel):
    protocol: Literal["full-general-purchase-rule-original-v1"] = (
        "full-general-purchase-rule-original-v1"
    )
    purpose: Literal["DEVELOPMENT"] = "DEVELOPMENT"
    user_id: UUID
    original_request: FullAssetPrepareRequest
    opportunity_id: Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")]
    rule: Rule
    comparison_days: Annotated[StrictInt, Field(ge=1, le=365)] = 90
    funds_use_date: datetime | None = None
    registered_static_living_cents: MoneyCents = 0
    manual_amount_cents: MoneyCents | None = None

    @model_validator(mode="after")
    def supported_authored_input(self) -> Self:
        if (self.rule.arm_id == "B0") != (self.manual_amount_cents is not None):
            raise ValueError("Only B0 has an explicit registered manual amount")
        if self.funds_use_date is not None and (
            self.funds_use_date.tzinfo is None or self.funds_use_date.utcoffset() is None
        ):
            raise ValueError("An authored funds-use instant must be aware")
        for identity in (self.rule.manual_candidate_id, self.rule.fixed_candidate_id):
            if identity is not None and str(UUID(identity)) != identity:
                raise ValueError("Product choices are canonical actual product UUIDs")
        return self


class FullMechanismProductOriginal(BoundaryModel):
    product: AssetProductTerms
    catalogue: FullAssetCatalogueReference
    candidate: Candidate | None
    proposed_exit: PlannedExit | None
    exclusion_reasons: list[str]


class FullMechanismSelectedPortfolio(BoundaryModel):
    protocol: Literal["full-mechanism-selected-portfolio-v1"] = (
        "full-mechanism-selected-portfolio-v1"
    )
    user_id: UUID
    epoch_id: UUID
    original_request: FullAssetPrepareRequest
    rule_original_sha256: Hash
    current_source_hash: Hash
    mechanism_input_hash: Hash
    selected_product: FullAssetCatalogueReference
    batch: FullAssetBatch
    portfolio_hash: Hash
    bank_authority: Literal[False] = False
    funds_reserved: Literal[False] = False
    preparation_status: Literal["NOT_PREPARED"] = "NOT_PREPARED"

    @model_validator(mode="after")
    def entire_original(self) -> Self:
        if (
            self.epoch_id != self.original_request.expected_epoch_id
            or self.batch.product_id != self.selected_product.product_id
            or self.batch.terms_digest != self.selected_product.terms_digest
            or self.batch.amount_cents <= 0
            or sum(row.amount_cents for row in self.batch.cash_uses) != self.batch.amount_cents
            or len({row.account_id for row in self.batch.cash_uses}) != len(self.batch.cash_uses)
            or self.portfolio_hash
            != configuration_hash(self.model_dump(mode="json", exclude={"portfolio_hash"}))
        ):
            raise ValueError("The complete selected proposal identity/hash differs")
        return self


class FullExperimentAssetSelection(BoundaryModel):
    protocol: Literal["full-current-general-mechanism-selection-v1"] = (
        "full-current-general-mechanism-selection-v1"
    )
    simulation: Literal[True] = True
    user_id: UUID
    epoch_id: UUID
    as_of: datetime
    status: Literal["PROPOSED", "NO_CANDIDATE", "UNKNOWN", "MISSING"]
    original_request: FullAssetPrepareRequest
    rule_original: FullMechanismRuleOriginal
    rule_original_sha256: Hash
    current_source_hash: Hash
    source_evidence_ids: list[UUID]
    source_originals: dict[str, object]
    source_counts: dict[str, Annotated[StrictInt, Field(ge=0)]]
    planning_input: FullAssetPlanningInput | None
    original_p_result: FullAssetPlanningResult | None
    mechanism_input: MechanismInput | None
    mechanism_decision: MechanismDecision | None
    product_originals: list[FullMechanismProductOriginal]
    selected_portfolio: FullMechanismSelectedPortfolio | None
    reasons: list[str]
    limitations: list[str]
    future_income_included_cents: Literal[0] = 0
    bank_authority: Literal[False] = False
    execution_status: Literal["NOT_RUN"] = "NOT_RUN"
    runtime_consumer_status: Literal["NOT_CONNECTED"] = "NOT_CONNECTED"
    actual_metrics: Literal[None] = None
    opportunity_denominator_retained: Literal[True] = True

    @model_validator(mode="after")
    def complete_proposal_binding(self) -> Self:
        raw = self.source_originals.get("rule_original_utf8")
        if (
            self.as_of.tzinfo is None
            or self.as_of.utcoffset() is None
            or self.user_id != self.rule_original.user_id
            or self.original_request != self.rule_original.original_request
            or self.epoch_id != self.original_request.expected_epoch_id
            or not isinstance(raw, str)
            or sha256(raw.encode("utf-8")).hexdigest() != self.rule_original_sha256
            or self.current_source_hash
            != configuration_hash(
                {
                    "user_id": str(self.user_id),
                    "epoch_id": str(self.epoch_id),
                    "as_of": self.as_of.isoformat(),
                    "originals": self.source_originals,
                    "counts": self.source_counts,
                }
            )
        ):
            raise ValueError("Current owner/epoch/rule/source original differs")
        if (self.status == "PROPOSED") != (self.selected_portfolio is not None):
            raise ValueError("Only a proposed actual selection can have a portfolio")
        if self.selected_portfolio is not None:
            selected = self.selected_portfolio
            if (
                self.planning_input is None
                or self.original_p_result is None
                or self.mechanism_input is None
                or self.mechanism_decision is None
                or selected.user_id != self.user_id
                or selected.original_request != self.original_request
                or selected.rule_original_sha256 != self.rule_original_sha256
                or selected.current_source_hash != self.current_source_hash
                or selected.mechanism_input_hash != self.mechanism_decision.input_sha256
                or self.mechanism_decision.input_sha256
                != value_digest(self.mechanism_input.model_dump(mode="json"))
                or self.mechanism_decision.status != "PROPOSAL"
                or self.mechanism_decision.candidate_id != str(selected.batch.product_id)
                or self.mechanism_decision.proposal_amount_cents != selected.batch.amount_cents
                or self.mechanism_decision.proposal_net_simulated_yield_cents
                != selected.batch.net_simulated_yield_cents
                or self.mechanism_input.rule != self.rule_original.rule
                or not any(
                    row.catalogue == selected.selected_product and row.candidate is not None
                    for row in self.product_originals
                )
            ):
                raise ValueError("Selected portfolio differs from the original mechanism decision")
        return self


def original_product_exit(
    product: AssetProductTerms,
    planning: FullAssetPlanningInput,
    days: int,
    funds_use_at: datetime,
) -> PlannedExit:
    """Keep actual late exits visible to B5; do not move maturity to a use date."""
    now = planning.snapshot.as_of
    terms: FixedPrincipalTerms | PlannedPrincipalTerms
    if product.maturity_rule.get("protocol") == "fixed-principal-return-v1":
        terms = FixedPrincipalTerms.model_validate(product.maturity_rule)
        if terms.term_days < product.lock_days or terms.yield_rule.accrual != "UNTIL_MATURITY":
            raise ValueError("CONTRADICTORY_FIXED_TERMS")
        kind: Literal["FIXED_MATURITY", "PLANNED_REDEMPTION"] = "FIXED_MATURITY"
        earning, request_at = terms.term_days, None
        liquidity = terms.term_days + terms.settlement_delay_days
    else:
        terms = PlannedPrincipalTerms.model_validate(product.maturity_rule)
        if (
            product.asset_class not in {"CASH_MGMT_T0", "CASH_MGMT_T1"}
            or terms.yield_rule.accrual != "UNTIL_REDEMPTION_REQUEST"
            or not product.auto_redeem_allowed
            or not planning.configuration.allow_auto_recovery_without_penalty
        ):
            raise ValueError("UNSUPPORTED_OR_UNCONFIRMED_PLANNED_REDEMPTION")
        horizon = min(days, (funds_use_at - now).days)
        earning = horizon - terms.settlement_delay_days
        if planning.valid_until is not None:
            delta = planning.valid_until - now
            micros = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
            earning = min(earning, (micros - 1) // (86400 * 1000000))
        if earning < product.lock_days:
            raise ValueError("NO_CONFIRMED_ORIGINAL_REDEMPTION_AFTER_LOCK")
        kind, request_at = "PLANNED_REDEMPTION", now + timedelta(days=earning)
        liquidity = product.lock_days + terms.settlement_delay_days
    if (
        not 1 <= earning <= 365
        or terms.yield_rule.annual_yield_bps != product.annual_yield_bps
        or terms.settlement_delay_days != product.redemption_delay_days
    ):
        raise ValueError("UNSUPPORTED_OR_CONTRADICTORY_ORIGINAL_EXIT")
    return PlannedExit(
        kind=kind,
        request_at=request_at,
        principal_available_at=now + timedelta(days=earning + terms.settlement_delay_days),
        earning_days=earning,
        liquidity_days=liquidity,
        terms_digest=product.terms_digest,
    )


def _facts(planning: FullAssetPlanningInput, curve: BoundaryResult) -> ConstraintFacts:
    points = curve.calculation_trace
    allowed = {
        "obligations",
        "living",
        "emergency",
        "goal_cash",
        "goal_minimum",
        "full_dated_expense",
        "full_periodic_transfer",
        "pending_cash_reservations",
        "full_seasonal_adopted",
    }
    if not points or any(set(p.protected_cents_by_reason) - allowed for p in points):
        raise ValueError("UNREGISTERED_OR_MISSING_PROTECTION_COMPONENT")
    if any(
        type(v) is not int or v < 0 for p in points for v in p.protected_cents_by_reason.values()
    ):
        raise ValueError("INVALID_ORIGINAL_PROTECTION_INTEGER")

    def maximum(*keys: str) -> int:
        return max(sum(p.protected_cents_by_reason.get(k, 0) for k in keys) for p in points)

    return ConstraintFacts(
        settled_cash_cents=sum(
            a.balance_cents for a in planning.snapshot.cash_accounts if a.account_type == "CASH"
        ),
        active_reserved_cents=sum(planning.exposure.reserved_cash_by_account.values()),
        hard_obligation_cents=maximum(
            "obligations", "full_dated_expense", "full_periodic_transfer", "full_seasonal_adopted"
        ),
        emergency_cents=maximum("emergency"),
        dynamic_living_cents=maximum("living"),
        registered_static_living_cents=0,
        other_goal_protection_cents=maximum("goal_cash", "goal_minimum"),
        current_policy_version_id=planning.policy_version_id,
    )


def select_current_general_purchase(
    planning: FullAssetPlanningInput,
    curve: BoundaryResult,
    mvp_configuration: dict[str, object],
    catalogue: list[FullAssetCatalogueReference],
    original: FullMechanismRuleOriginal,
    rule_sha256: str,
    source_hash: str,
) -> tuple[
    MechanismInput, MechanismDecision, list[FullMechanismProductOriginal], FullAssetBatch | None
]:
    """A current deterministic opportunity; neither a P optimum nor a bank command."""
    mvp = validate_configuration(mvp_configuration)
    body, now, rule = original.original_request, planning.snapshot.as_of, original.rule
    if (
        planning.configuration.goal_id is not None
        or body.goal_id is not None
        or planning.policy_id != body.full_policy_id
        or planning.policy_version_id != body.expected_full_policy_version_id
        or mvp["type"] != "asset_authorization"
        or mvp["scope"] != "general_idle_funds"
    ):
        raise ValueError("UNSUPPORTED_OR_MISMATCHED_GENERAL_SCOPE")
    bindings = {b.product_id: b for b in catalogue}
    if len(bindings) != len(catalogue) or set(bindings) != {
        p.product_id for p in planning.products
    }:
        raise ValueError("COMPLETE_CURRENT_IMMUTABLE_CATALOGUE_REQUIRED")
    facts = _facts(planning, curve).model_copy(
        update={"registered_static_living_cents": original.registered_static_living_cents}
    )
    occupied = planning.exposure.managed_principal_cents + planning.exposure.pending_purchase_cents
    authority = min(
        planning.configuration.single_action_cap_cents,
        mvp["single_action_cap_cents"],
        max(
            0,
            min(planning.configuration.max_auto_managed_cents, mvp["max_auto_managed_cents"])
            - occupied,
        ),
    )
    living = (
        facts.registered_static_living_cents
        if rule.ablation == "DYNAMIC_LIVING_RESERVE"
        else facts.dynamic_living_cents
    )
    goals = 0 if rule.ablation == "MULTI_GOAL_CONSTRAINTS" else facts.other_goal_protection_cents
    protected = facts.hard_obligation_cents + facts.emergency_cents + living + goals
    p_amount = min(
        authority, max(0, facts.settled_cash_cents - facts.active_reserved_cents - protected)
    )
    amount = original.manual_amount_cents if rule.arm_id == "B0" else p_amount
    assert amount is not None
    use_at = original.funds_use_date or now + timedelta(days=original.comparison_days)
    latest: dict[str, int] = {}
    for p in planning.products:
        latest[p.product_code] = max(latest.get(p.product_code, 0), p.version_number)
    records: list[FullMechanismProductOriginal] = []
    for product in sorted(
        planning.products, key=lambda p: (p.product_code, p.version_number, p.product_id)
    ):
        binding = bindings[product.product_id]
        excluded: list[str] = []
        if binding.terms_digest != product.terms_digest:
            raise ValueError("IMMUTABLE_PRODUCT_TERMS_BINDING_DIFFERS")
        if product.version_number != latest[product.product_code]:
            excluded.append("SUPERSEDED_CURRENT_PRODUCT_VERSION")
        if (
            product.created_at > now
            or product.effective_from > now
            or (product.effective_until is not None and product.effective_until <= now)
        ):
            excluded.append("PRODUCT_NOT_CURRENTLY_KNOWN_AND_EFFECTIVE")
        if _catalog_class(product) not in planning.configuration.allowed_asset_classes:
            excluded.append("OUTSIDE_ACTUAL_FULL_PLANNING_PRODUCT_SCOPE")
        if (
            product.principal_fluctuation
            or product.risk_level > planning.configuration.max_principal_risk_level
        ):
            excluded.append("PRINCIPAL_RISK_NOT_SUPPORTED_BY_THIS_PRODUCER")
        if product.lock_days > min(planning.configuration.max_lock_days, mvp["max_lock_days"]):
            excluded.append("LOCK_EXCEEDS_ACTUAL_FULL_OR_MVP_PRODUCT_SCOPE")
        if product.redemption_delay_days > min(
            planning.configuration.max_redemption_delay_days, mvp["max_redemption_delay_days"]
        ):
            excluded.append("DELAY_EXCEEDS_ACTUAL_FULL_OR_MVP_PRODUCT_SCOPE")
        if product.asset_class not in mvp["allowed_asset_classes"]:
            excluded.append("OUTSIDE_ACTUAL_MVP_PRODUCT_SCOPE")
        if not product.auto_purchase_allowed or product.asset_class == "CASH":
            excluded.append("NONPURCHASABLE_OR_CASH_RETAIN_ORIGINAL")
        exit_plan = None
        candidate = None
        try:
            exit_plan = original_product_exit(product, planning, original.comparison_days, use_at)
        except (ValueError, OverflowError) as error:
            excluded.append(str(error))
        if not excluded and exit_plan is not None:
            candidate = Candidate(
                candidate_id=str(product.product_id),
                user_id=original.user_id,
                epoch_id=body.expected_epoch_id,
                kind="PURCHASE_ASSET",
                amount_cents=amount,
                cash_debit_cents=amount,
                authority_cap_cents=authority,
                policy_version_id=planning.policy_version_id,
                goal_allocation_permitted=True,
                evidence_level="USER_CONFIRMED",
                required_evidence_level="USER_CONFIRMED",
                available_at=exit_plan.principal_available_at,
                funds_use_at=use_at,
                lock_until=now + timedelta(days=product.lock_days),
                loss_cents=0,
                simulated_annual_yield_basis_points=product.annual_yield_bps,
                comparison_days=exit_plan.earning_days,
                confirmation_required=True,
                model_confidence_basis_points=None,
                original_refs=sorted({source_hash, rule_sha256, binding.product_record_hash}),
            )
        records.append(
            FullMechanismProductOriginal(
                product=product,
                catalogue=binding,
                candidate=candidate,
                proposed_exit=exit_plan,
                exclusion_reasons=excluded,
            )
        )
    candidates = [r.candidate for r in records if r.candidate is not None]
    signatures = value_digest([c.model_dump(mode="json") for c in candidates])
    inputs = MechanismInput(
        user_id=original.user_id,
        epoch_id=body.expected_epoch_id,
        opportunity_id=original.opportunity_id,
        as_of=now,
        source_status="CURRENT_COMPLETE",
        source_inventory_sha256=source_hash,
        source_refs=sorted({source_hash, rule_sha256}),
        facts=facts,
        candidates=candidates,
        worlds=[
            World(
                world_id="current-deterministic",
                complete_action_signature_sha256=signatures,
                original_refs=[source_hash],
            )
        ],
        questions=[],
        rule=rule,
    )
    decision = decide_full_mechanism(inputs)
    selected = next(
        (
            r
            for r in records
            if r.candidate is not None and r.candidate.candidate_id == decision.candidate_id
        ),
        None,
    )
    batch = None
    cents = decision.proposal_amount_cents
    if (
        selected is not None
        and selected.proposed_exit is not None
        and cents is not None
        and cents > 0
    ):
        cash: list[AssetCashUse] = []
        needed = cents
        for account in sorted(planning.snapshot.cash_accounts, key=lambda a: a.account_id):
            if account.account_type != "CASH":
                continue
            owned = sum(
                g.cash_owned_cents
                for g in planning.snapshot.goals
                if g.account_id == account.account_id
            )
            available = max(
                0,
                account.balance_cents
                - owned
                - planning.exposure.reserved_cash_by_account.get(account.account_id, 0),
            )
            take = min(needed, available)
            if take:
                cash.append(AssetCashUse(account_id=account.account_id, amount_cents=take))
                needed -= take
        if needed:
            raise ValueError("ACTUAL_UNOWNED_CASH_SOURCE_CANNOT_FUND_PROPOSAL")
        p, exit_plan = selected.product, selected.proposed_exit
        if cents < p.minimum_purchase_cents:
            raise ValueError("SELECTED_PROPOSAL_BELOW_ORIGINAL_MINIMUM_PURCHASE")
        batch = FullAssetBatch(
            product_id=p.product_id,
            product_code=p.product_code,
            version_number=p.version_number,
            terms_digest=p.terms_digest,
            amount_cents=cents,
            net_simulated_yield_cents=cents
            * p.annual_yield_bps
            * exit_plan.earning_days
            // 3650000,
            purchase_at=now,
            principal_available_at=exit_plan.principal_available_at,
            exit_plan=exit_plan,
            cash_uses=cash,
        )
    return inputs, decision, records, batch
