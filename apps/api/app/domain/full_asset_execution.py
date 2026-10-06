"""Finite portfolio consumption contracts over unchanged purchase effects.

These internal inputs are supplied by verified production readers, never by HTTP
facts. A frozen portfolio is a request to confirm; it is not a bank permission.
"""

from datetime import datetime, timedelta
from typing import Annotated, Any, Literal, Self
from uuid import UUID, uuid5

from app.domain.asset_allocation_types import FixedPrincipalTerms
from app.domain.boundary import compute_boundary
from app.domain.boundary_types import BoundaryModel, BoundaryPosition, BoundaryResult
from app.domain.execution import execution_effect_hash, revalidate_execution
from app.domain.execution_types import BankCommand, CashUse, ExecutionContext
from app.domain.full_asset_allocation import FullAssetPlanningResult
from app.domain.full_policy_configuration import AssetAuthorizationPolicy
from app.domain.full_protection_projection import (
    FullProtectionPolicySource,
    FullProtectionProjectionInput,
    project_full_protection,
)
from app.domain.income_ledger import IncomeUse
from app.domain.policy_configuration import (
    UUIDReference,
    configuration_hash,
    validate_configuration,
)
from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator

Hash = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
Key = Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")]
Cents = Annotated[StrictInt, Field(ge=0, le=2**63 - 1)]
PORTFOLIO_NAMESPACE = UUID("8770c713-1586-598a-9c02-5822631b4402")


class FullAssetPrepareRequest(BoundaryModel):
    full_policy_id: UUIDReference
    expected_full_policy_version_id: UUIDReference
    mvp_asset_policy_id: UUIDReference
    expected_mvp_policy_version_id: UUIDReference
    goal_id: UUIDReference | None = None
    expected_goal_policy_version_id: UUIDReference | None = None
    expected_epoch_id: UUIDReference
    idempotency_key: Key
    planning_mode: Literal["PORTFOLIO", "FIXED_LADDER"] = "PORTFOLIO"

    @model_validator(mode="after")
    def complete_goal_identity(self) -> Self:
        if (self.goal_id is None) != (self.expected_goal_policy_version_id is None):
            raise ValueError("Goal identity and its original current version are inseparable")
        if not self.idempotency_key.strip():
            raise ValueError("A nonblank original portfolio key is required")
        return self


class FullAssetConfirmRequest(BoundaryModel):
    accepted: StrictBool
    reviewed_portfolio_hash: Hash
    expected_epoch_id: UUIDReference
    idempotency_key: Key

    @model_validator(mode="after")
    def explicit_acceptance(self) -> Self:
        if self.accepted is not True or not self.idempotency_key.strip():
            raise ValueError("Only explicit acceptance of the whole original portfolio is valid")
        return self


class FullAssetExecuteRequest(BoundaryModel):
    accepted: StrictBool
    reviewed_portfolio_hash: Hash
    expected_epoch_id: UUIDReference
    expected_batch_number: Annotated[StrictInt, Field(ge=1, le=4)]
    expected_action_id: UUIDReference

    @model_validator(mode="after")
    def explicit_original(self) -> Self:
        if self.accepted is not True:
            raise ValueError("Executing a portfolio is an explicit original operation")
        return self


class FullAssetCatalogueReference(BoundaryModel):
    product_id: UUID
    catalogue_version_id: UUID
    product_record_hash: Hash
    terms_digest: Hash


class FullAssetBatchBinding(BoundaryModel):
    protocol: Literal["full-asset-batch-binding-v1"] = "full-asset-batch-binding-v1"
    portfolio_id: UUID
    portfolio_hash: Hash
    epoch_id: UUID
    batch_number: Annotated[StrictInt, Field(ge=1, le=4)]
    client_request_hash: Hash
    command_hash: Hash


class FullAssetExecutionBasis(BoundaryModel):
    user_id: UUID
    epoch_id: UUID
    as_of: datetime
    full_policy_configuration: AssetAuthorizationPolicy
    full_policy_content_hash: Hash
    full_planning_confirmation_valid: StrictBool
    original_planning_response_hash: Hash
    planning: FullAssetPlanningResult
    context: ExecutionContext
    catalogue: list[FullAssetCatalogueReference]
    position_accounts: dict[UUID, UUID]
    batch_income_uses: list[list[IncomeUse]]
    full_protection_sources: list[FullProtectionPolicySource]
    full_protection_inventory_complete: StrictBool
    full_source_issues: list[str]
    source_evidence_ids: list[UUID]
    expires_at: datetime


class FullAssetExecutionBatch(BoundaryModel):
    batch_number: Annotated[StrictInt, Field(ge=1, le=4)]
    action_id: UUID
    bank_idempotency_key: Key
    catalogue: FullAssetCatalogueReference
    command: BankCommand


class FullAssetFrozenPortfolio(BoundaryModel):
    protocol: Literal["full-asset-execution-portfolio-v1"] = "full-asset-execution-portfolio-v1"
    simulation: Literal[True] = True
    portfolio_id: UUID
    user_id: UUID
    epoch_id: UUID
    prepared_at: datetime
    expires_at: datetime
    original_request: FullAssetPrepareRequest
    client_request_hash: Hash
    full_policy_content_hash: Hash
    original_mvp_configuration_hash: Hash
    original_planning_response_hash: Hash
    original_planning_input_hash: Hash
    full_configuration: AssetAuthorizationPolicy
    total_purchase_cents: Cents
    batches: Annotated[list[FullAssetExecutionBatch], Field(min_length=1, max_length=4)]
    combined_original_boundary: BoundaryResult
    combined_full_protection_hash: Hash
    source_evidence_ids: list[UUID]
    portfolio_hash: Hash
    bank_authority: Literal[False] = False
    funds_reserved: Literal[False] = False
    cross_operation_atomicity: Literal["NOT_AVAILABLE"] = "NOT_AVAILABLE"
    financial_experiment_verified: Literal[False] = False

    @model_validator(mode="after")
    def entire_original(self) -> Self:
        values = self.model_dump(mode="json", exclude={"portfolio_hash"})
        request = self.original_request
        expected = portfolio_identity(self.user_id, request)
        if (
            self.portfolio_id != expected
            or self.epoch_id != request.expected_epoch_id
            or self.client_request_hash != configuration_hash(request.model_dump(mode="json"))
            or self.portfolio_hash != configuration_hash(values)
            or self.prepared_at.tzinfo is None
            or self.expires_at.tzinfo is None
            or not self.prepared_at < self.expires_at <= self.prepared_at + timedelta(minutes=15)
            or [b.batch_number for b in self.batches] != list(range(1, len(self.batches) + 1))
            or len({b.command.effect.product_id for b in self.batches}) != len(self.batches)
            or sum(b.command.effect.amount_cents for b in self.batches) != self.total_purchase_cents
            or self.combined_original_boundary.status != "READY"
        ):
            raise ValueError("The entire original portfolio identity, order and hash must bind")
        for batch in self.batches:
            effect = batch.command.effect
            authorities = [request.expected_mvp_policy_version_id]
            if request.expected_goal_policy_version_id is not None:
                authorities.append(request.expected_goal_policy_version_id)
            if (
                batch.action_id != uuid5(self.portfolio_id, f"batch:{batch.batch_number}")
                or batch.action_id != effect.operation_id
                or effect.user_id != self.user_id
                or effect.goal_id != request.goal_id
                or effect.policy_id != request.mvp_asset_policy_id
                or effect.policy_version_id != request.expected_mvp_policy_version_id
                or effect.policy_version_ids != authorities
                or effect.valid_from != self.prepared_at
                or effect.expires_at != self.expires_at
                or effect.action_type != "PURCHASE_ASSET"
                or effect.product_id != batch.catalogue.product_id
                or effect.terms_digest != batch.catalogue.terms_digest
                or batch.bank_idempotency_key != batch_key(self.portfolio_id, batch.batch_number)
                or effect.fee_cents != 0
                or effect.loss_cents != 0
            ):
                raise ValueError("An original batch cannot change scope, product, money or key")
        return self


def portfolio_identity(user_id: UUID, request: FullAssetPrepareRequest) -> UUID:
    return uuid5(
        PORTFOLIO_NAMESPACE,
        f"{user_id}:{request.expected_epoch_id}:{request.idempotency_key}",
    )


def batch_key(portfolio_id: UUID, batch_number: int) -> str:
    return f"full-asset:{portfolio_id}:batch:{batch_number}"


def build_frozen_portfolio(
    request: FullAssetPrepareRequest, basis: FullAssetExecutionBasis
) -> FullAssetFrozenPortfolio:
    """Build exact child effects, then independently project their combined purchase.

    A planned redemption is not committed bank principal: unlike the planning
    preview its future principal availability stays None in this execution check.
    """
    request = FullAssetPrepareRequest.model_validate(request.model_dump())
    basis = FullAssetExecutionBasis.model_validate(basis.model_dump())
    context = basis.context.model_copy(
        update={"snapshot": basis.context.snapshot.model_copy(update={"horizon_days": 365})}
    )
    planning, full = basis.planning, basis.full_policy_configuration
    if (
        basis.epoch_id != request.expected_epoch_id
        or basis.user_id != context.user_id
        or basis.as_of != context.snapshot.as_of
        or not basis.full_planning_confirmation_valid
        or basis.full_source_issues
        or not basis.full_protection_inventory_complete
        or context.source_issues
        or planning.policy_id != request.full_policy_id
        or planning.policy_version_id != request.expected_full_policy_version_id
        or planning.goal_id != request.goal_id
        or full.goal_id != request.goal_id
        or planning.status != "OPTIMAL"
        or not planning.batches
        or len(planning.batches) != len(basis.batch_income_uses)
        or planning.total_purchase_cents is None
        or basis.full_policy_content_hash != configuration_hash(full.model_dump(mode="json"))
        or len({row.product_id for row in basis.catalogue}) != len(basis.catalogue)
    ):
        raise ValueError("FULL_ASSET_EXECUTION_CURRENT_SOURCES_NOT_PROVEN")
    version = next(
        (v for v in context.versions if v.version_id == request.expected_mvp_policy_version_id),
        None,
    )
    if version is None or version.policy_id != request.mvp_asset_policy_id:
        raise ValueError("FULL_ASSET_EXECUTION_EXACT_MVP_PERMISSION_REQUIRED")
    config = validate_configuration(version.configuration)
    if (
        config["type"] != "asset_authorization"
        or config["scope"] != full.scope
        or config.get("goal_id") != (str(request.goal_id) if request.goal_id else None)
        or context.exposure is None
        or context.exposure.goal_id != request.goal_id
    ):
        raise ValueError("FULL_ASSET_EXECUTION_MVP_SCOPE_MISMATCH")
    occupied = context.exposure.managed_principal_cents + context.exposure.pending_purchase_cents
    if occupied + planning.total_purchase_cents > min(
        full.max_auto_managed_cents, config["max_auto_managed_cents"]
    ):
        raise ValueError("FULL_ASSET_EXECUTION_WHOLE_MANAGED_CAP_EXCEEDED")
    identity = portfolio_identity(basis.user_id, request)
    products = {p.product_id: p for p in context.products}
    catalogue = {p.product_id: p for p in basis.catalogue}
    batches: list[FullAssetExecutionBatch] = []
    snapshot, positions = context.snapshot, list(context.positions)
    total_cash: dict[UUID, int] = {}
    total_income: dict[UUID, int] = {}
    for number, planned in enumerate(planning.batches, start=1):
        product, ref = products.get(planned.product_id), catalogue.get(planned.product_id)
        if (
            product is None
            or ref is None
            or product.terms_digest != planned.terms_digest
            or ref.terms_digest != planned.terms_digest
            or product.version_number != planned.version_number
            or product.product_code != planned.product_code
            or planned.purchase_at != basis.as_of
            or planned.amount_cents <= 0
            or planned.amount_cents
            > min(full.single_action_cap_cents, config["single_action_cap_cents"])
            or product.principal_fluctuation
            or planned.product_id not in basis.position_accounts
        ):
            raise ValueError("FULL_ASSET_EXECUTION_ORIGINAL_PRODUCT_OR_BATCH_NOT_PROVEN")
        action_id = uuid5(identity, f"batch:{number}")
        from app.domain.execution_types import ExecutionEffect

        versions = [request.expected_mvp_policy_version_id]
        if request.expected_goal_policy_version_id is not None:
            versions.append(request.expected_goal_policy_version_id)
        cash = [
            CashUse(account_id=u.account_id, amount_cents=u.amount_cents) for u in planned.cash_uses
        ]
        if not cash:
            raise ValueError("FULL_ASSET_EXECUTION_BATCH_HAS_NO_ORIGINAL_CASH")
        effect = ExecutionEffect(
            operation_id=action_id,
            user_id=basis.user_id,
            business_key=f"purchase:{action_id}",
            action_type="PURCHASE_ASSET",
            amount_cents=planned.amount_cents,
            cash_uses=cash,
            income_uses=basis.batch_income_uses[number - 1],
            goal_id=request.goal_id,
            policy_id=request.mvp_asset_policy_id,
            policy_version_id=request.expected_mvp_policy_version_id,
            policy_version_ids=versions,
            product_id=product.product_id,
            product_version_number=product.version_number,
            terms_digest=product.terms_digest,
            position_id=uuid5(action_id, "position"),
            position_account_id=basis.position_accounts[product.product_id],
            return_account_id=cash[0].account_id,
            purchase_exit=planned.exit_plan,
            latest_arrival_at=planned.principal_available_at + (basis.expires_at - basis.as_of),
            valid_from=basis.as_of,
            expires_at=basis.expires_at,
        )
        validation = revalidate_execution(effect, context)
        if validation.status not in {"READY", "CONFIRMATION_REQUIRED"}:
            raise ValueError("FULL_ASSET_EXECUTION_MVP_REJECTED:" + ",".join(validation.reasons))
        for use in cash:
            total_cash[use.account_id] = total_cash.get(use.account_id, 0) + use.amount_cents
        for income_use in effect.income_uses:
            total_income[income_use.fragment_id] = (
                total_income.get(income_use.fragment_id, 0) + income_use.amount_cents
            )
        snapshot = snapshot.model_copy(
            update={
                "cash_accounts": [
                    a.model_copy(
                        update={
                            "balance_cents": a.balance_cents
                            - sum(u.amount_cents for u in cash if u.account_id == a.account_id)
                        }
                    )
                    for a in snapshot.cash_accounts
                ],
                "goals": [
                    g.model_copy(
                        update={
                            "cash_owned_cents": g.cash_owned_cents - effect.amount_cents,
                            "principal_owned_cents": g.principal_owned_cents + effect.amount_cents,
                        }
                    )
                    if g.goal_id == request.goal_id
                    else g
                    for g in snapshot.goals
                ],
            }
        )
        principal_available = None
        if planned.exit_plan.kind == "FIXED_MATURITY":
            terms = FixedPrincipalTerms.model_validate(product.maturity_rule)
            principal_available = basis.as_of + timedelta(
                days=terms.term_days + terms.settlement_delay_days
            )
        assert effect.position_id is not None
        positions.append(
            BoundaryPosition(
                position_id=effect.position_id,
                goal_id=effect.goal_id,
                principal_cents=effect.amount_cents,
                status="HELD",
                principal_available_at=principal_available,
            )
        )
        batches.append(
            FullAssetExecutionBatch(
                batch_number=number,
                action_id=action_id,
                bank_idempotency_key=batch_key(identity, number),
                catalogue=ref,
                command=BankCommand(effect=effect, effect_hash=execution_effect_hash(effect)),
            )
        )
    for account in context.snapshot.cash_accounts:
        owned = sum(
            g.cash_owned_cents for g in context.snapshot.goals if g.account_id == account.account_id
        )
        reserved = context.reserved_cash_by_account.get(account.account_id, 0)
        available = account.balance_cents - owned - reserved
        if request.goal_id is not None:
            goal = next((g for g in context.snapshot.goals if g.goal_id == request.goal_id), None)
            available = (
                goal.cash_owned_cents if goal and goal.account_id == account.account_id else 0
            ) - context.reserved_goal_cash_by_goal.get(request.goal_id, 0)
        if total_cash.get(account.account_id, 0) > available:
            raise ValueError("FULL_ASSET_EXECUTION_WHOLE_SOURCE_CASH_EXCEEDED")
    lots = {row.fragment_id: row.available_cents for row in context.lots}
    if any(amount > lots.get(fragment, 0) for fragment, amount in total_income.items()):
        raise ValueError("FULL_ASSET_EXECUTION_WHOLE_INCOME_FRAGMENT_EXCEEDED")
    combined = compute_boundary(snapshot, context.versions, positions, context.boundary_products)
    protected = project_full_protection(
        FullProtectionProjectionInput(
            snapshot=snapshot,
            boundary_versions=context.versions,
            positions=positions,
            boundary_products=context.boundary_products,
            policies=basis.full_protection_sources,
            reserved_cash_by_account=context.reserved_cash_by_account,
            full_source_inventory_complete=basis.full_protection_inventory_complete,
            full_source_issues=basis.full_source_issues,
        )
    )
    curve = protected.full_annual_projection
    if (
        combined.status != "READY"
        or protected.status == "UNKNOWN"
        or protected.source_account_checks
        or curve is None
        or curve.minimum_margin_cents is None
        or curve.minimum_margin_cents < 0
        or len(curve.calculation_trace) != 1098
    ):
        raise ValueError("FULL_ASSET_EXECUTION_WHOLE_CURRENT_PROTECTION_NOT_PROVEN")
    values: dict[str, Any] = dict(
        portfolio_id=identity,
        user_id=basis.user_id,
        epoch_id=basis.epoch_id,
        prepared_at=basis.as_of,
        expires_at=basis.expires_at,
        original_request=request,
        client_request_hash=configuration_hash(request.model_dump(mode="json")),
        full_policy_content_hash=basis.full_policy_content_hash,
        original_mvp_configuration_hash=configuration_hash(config),
        original_planning_response_hash=basis.original_planning_response_hash,
        original_planning_input_hash=planning.input_hash,
        full_configuration=full,
        total_purchase_cents=planning.total_purchase_cents,
        batches=batches,
        combined_original_boundary=combined,
        combined_full_protection_hash=protected.input_hash,
        source_evidence_ids=sorted(set(basis.source_evidence_ids)),
    )
    # Dump through the typed contract to include its exact literal defaults in the hash.
    candidate = FullAssetFrozenPortfolio.model_construct(**values, portfolio_hash="0" * 64)
    values["portfolio_hash"] = configuration_hash(
        candidate.model_dump(mode="json", exclude={"portfolio_hash"})
    )
    return FullAssetFrozenPortfolio.model_validate(values)
