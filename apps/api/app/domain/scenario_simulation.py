"""Bounded counterfactual inputs; every result comes from the original financial engine."""

from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Annotated, Any, Literal
from uuid import UUID

from app.domain.boundary import compute_boundary, compute_policy_change_boundary
from app.domain.boundary_types import (
    BoundaryModel,
    BoundaryPolicyVersion,
    BoundaryPosition,
    BoundaryProduct,
    BoundaryResult,
    BoundarySnapshot,
)
from app.domain.policy_change_types import PolicyChangeAssumption
from app.domain.policy_configuration import (
    UUIDReference,
    configuration_hash,
    validate_configuration,
)
from pydantic import Field, StrictInt, model_validator

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
SmallMoney = Annotated[StrictInt, Field(ge=0, le=10_000_000)]
Horizon = Literal[90, 365]


class CashAssumption(BoundaryModel):
    account_id: UUIDReference
    delta_cents: Annotated[StrictInt, Field(ge=-10_000_000, le=10_000_000)]


class EmergencyAssumption(BoundaryModel):
    policy_id: UUIDReference
    expected_version_id: UUIDReference
    amount_cents: SmallMoney


class ProductAssumption(BoundaryModel):
    product_id: UUIDReference
    expected_version_number: Annotated[StrictInt, Field(ge=1)]
    term_days: Annotated[StrictInt, Field(ge=0, le=365)]
    settlement_delay_days: Annotated[StrictInt, Field(ge=0, le=365)]

    @model_validator(mode="after")
    def bounded_occupancy(self) -> "ProductAssumption":
        if self.term_days + self.settlement_delay_days > 365:
            raise ValueError("Hypothetical product occupancy exceeds 365 days")
        return self


class ScenarioCompareRequest(BoundaryModel):
    expected_epoch_id: UUIDReference
    expected_source_hash: Digest
    expected_engine_hash: Digest
    horizon_days: Horizon = 90
    cash_change: CashAssumption | None = None
    emergency_change: EmergencyAssumption | None = None
    product_change: ProductAssumption | None = None


class ScenarioBasis(BoundaryModel):
    user_id: UUID
    epoch_id: UUID
    source_hash: Digest
    engine_hash: Digest
    snapshot: BoundarySnapshot
    versions: Annotated[list[BoundaryPolicyVersion], Field(max_length=100)]
    positions: Annotated[list[BoundaryPosition], Field(max_length=10000)]
    products: Annotated[list[BoundaryProduct], Field(max_length=100)]


class CashChoice(BoundaryModel):
    account_id: UUID
    balance_cents: StrictInt
    evidence_ids: list[UUID]


class EmergencyChoice(BoundaryModel):
    policy_id: UUID
    version_id: UUID
    configuration_hash: Digest
    amount_cents: StrictInt
    evidence_ids: list[UUID]


class ProductChoice(BoundaryModel):
    product_id: UUID
    version_number: StrictInt
    asset_class: str
    minimum_purchase_cents: StrictInt
    terms_digest: Digest
    term_days: StrictInt
    settlement_delay_days: StrictInt


class SimulationFlags(BoundaryModel):
    simulation: Literal[True] = True
    read_only: Literal[True] = True
    hypothetical_only: Literal[True] = True
    grants_authority: Literal[False] = False
    executes_funds: Literal[False] = False
    writes_facts: Literal[False] = False
    resets_history: Literal[False] = False
    receipt_verified: Literal[False] = False
    economic_verified: Literal[False] = False
    future_income_included_cents: Literal[0] = 0
    execution: Literal["NOT_IMPLEMENTED"] = "NOT_IMPLEMENTED"


class ChangedParameter(BoundaryModel):
    kind: Literal["CASH_BALANCE_DELTA", "EMERGENCY_AMOUNT", "PRODUCT_OCCUPANCY"]
    entity_id: UUID
    original: dict[str, Any]
    hypothetical: dict[str, Any]
    source_is_hypothetical: Literal[True] = True


class ScenarioComparison(SimulationFlags):
    schema_version: Literal["counterfactual-comparison-v1"] = "counterfactual-comparison-v1"
    user_id: UUID
    epoch_id: UUID
    as_of: datetime
    timezone: Literal["Asia/Shanghai", "UTC"]
    local_date: date
    horizon_days: Horizon
    source_hash: Digest
    engine_hash: Digest
    original_request: ScenarioCompareRequest
    request_hash: Digest
    scenario_hash: Digest
    baseline: BoundaryResult
    hypothetical: BoundaryResult
    delta_safe_idle_cents: StrictInt | None
    changed_parameters: list[ChangedParameter]
    limitations: list[str]


LIMITATIONS = [
    "仅原90/365日现金边界反事实；不是全产品收益优化、报价或实际资金执行。",
    "假设余额不是到账收入；原目标归属、持仓本金返还日期及正式事实没有变化。",
    "产品期限仅描述假设今天新配置的占用期；原持仓和合同到期日未修改。",
    "未接未来收入、收益、损失及完整FULL保护模型；未知来源仍保留未知。",
    "本页重置只清参数；显式重放会重新读取当前原件，不冻结授权或服务端时钟。",
]


def choices(
    basis: ScenarioBasis,
) -> tuple[list[CashChoice], list[EmergencyChoice], list[ProductChoice]]:
    excluded = {g.account_id for g in basis.snapshot.goals if g.account_id is not None}
    excluded.update(g.account_id for g in basis.snapshot.unassigned_goal_cash)
    cash = [
        CashChoice(
            account_id=a.account_id, balance_cents=a.balance_cents, evidence_ids=a.evidence_ids
        )
        for a in basis.snapshot.cash_accounts
        if a.account_type == "CASH" and a.account_id not in excluded and a.evidence_ids
    ]
    policies = [
        EmergencyChoice(
            policy_id=v.policy_id,
            version_id=v.version_id,
            configuration_hash=v.content_hash,
            amount_cents=v.configuration["amount_cents"],
            evidence_ids=v.evidence_ids,
        )
        for v in basis.versions
        if v.configuration.get("type") == "emergency_buffer" and v.evidence_ids
    ]
    products = [
        ProductChoice(
            product_id=p.product_id,
            version_number=p.version_number,
            asset_class=p.asset_class,
            minimum_purchase_cents=p.minimum_purchase_cents,
            terms_digest=p.terms_digest,
            term_days=p.fixed_return.term_days,
            settlement_delay_days=p.fixed_return.settlement_delay_days,
        )
        for p in basis.products
        if p.fixed_return is not None and p.terms_digest is not None
    ]
    return cash, policies, products


def _window(
    configuration: dict[str, Any], now: datetime, zone: timezone
) -> tuple[datetime, datetime | None]:
    start_text, end_text = configuration["valid_from"], configuration["valid_until"]
    start = (
        datetime.combine(date.fromisoformat(start_text), time.min, zone).astimezone(UTC)
        if start_text
        else now
    )
    end = (
        datetime.combine(
            date.fromisoformat(end_text) + timedelta(days=1), time.min, zone
        ).astimezone(UTC)
        if end_text
        else None
    )
    return start, end


def compare_scenario(basis: ScenarioBasis, request: ScenarioCompareRequest) -> ScenarioComparison:
    """Deep revalidate, retain unknowns, and never mutate original nested models."""
    basis = ScenarioBasis.model_validate(basis.model_dump())
    request = ScenarioCompareRequest.model_validate(request.model_dump())
    if (request.expected_epoch_id, request.expected_source_hash, request.expected_engine_hash) != (
        basis.epoch_id,
        basis.source_hash,
        basis.engine_hash,
    ):
        raise ValueError("Current epoch, source or engine differs from the reviewed original")
    cash_choices, policy_choices, product_choices = choices(basis)
    snapshot = BoundarySnapshot.model_validate(
        {**basis.snapshot.model_dump(), "horizon_days": request.horizon_days}
    )
    baseline = compute_boundary(snapshot, basis.versions, basis.positions, basis.products)
    hypothetical = BoundarySnapshot.model_validate(snapshot.model_dump())
    products = [BoundaryProduct.model_validate(p.model_dump()) for p in basis.products]
    changed: list[ChangedParameter] = []
    if request.cash_change:
        change = request.cash_change
        selected = next((a for a in cash_choices if a.account_id == change.account_id), None)
        if selected is None or selected.balance_cents + change.delta_cents < 0:
            raise ValueError("Hypothetical cash requires one eligible original non-goal account")
        for index, account in enumerate(hypothetical.cash_accounts):
            if account.account_id == change.account_id:
                hypothetical.cash_accounts[index] = account.model_validate(
                    {
                        **account.model_dump(),
                        "balance_cents": selected.balance_cents + change.delta_cents,
                        "evidence_ids": [],
                    }
                )
        changed.append(
            ChangedParameter(
                kind="CASH_BALANCE_DELTA",
                entity_id=change.account_id,
                original={
                    "balance_cents": selected.balance_cents,
                    "evidence_ids": [str(i) for i in selected.evidence_ids],
                },
                hypothetical={
                    "balance_cents": selected.balance_cents + change.delta_cents,
                    "delta_cents": change.delta_cents,
                },
            )
        )
    if request.product_change:
        product_change = request.product_change
        selected_product = next(
            (
                p
                for p in product_choices
                if p.product_id == product_change.product_id
                and p.version_number == product_change.expected_version_number
            ),
            None,
        )
        if selected_product is None:
            raise ValueError(
                "Hypothetical occupancy requires the exact original fixed-return product"
            )
        for index, product in enumerate(products):
            if product.product_id == product_change.product_id:
                assert product.fixed_return is not None
                terms = product.fixed_return.model_validate(
                    {
                        **product.fixed_return.model_dump(),
                        "term_days": product_change.term_days,
                        "settlement_delay_days": product_change.settlement_delay_days,
                    }
                )
                data = {**product.model_dump(), "fixed_return": terms}
                data["terms_digest"] = configuration_hash(
                    {
                        "kind": "HYPOTHETICAL_PRODUCT_OCCUPANCY",
                        "original_terms_digest": product.terms_digest,
                        "parameters": product_change.model_dump(mode="json"),
                    }
                )
                products[index] = BoundaryProduct.model_validate(data)
        changed.append(
            ChangedParameter(
                kind="PRODUCT_OCCUPANCY",
                entity_id=product_change.product_id,
                original=selected_product.model_dump(mode="json"),
                hypothetical=product_change.model_dump(mode="json"),
            )
        )
    zone = UTC if snapshot.timezone == "UTC" else timezone(timedelta(hours=8))
    if request.emergency_change:
        policy_change = request.emergency_change
        if not any(
            p.policy_id == policy_change.policy_id
            and p.version_id == policy_change.expected_version_id
            for p in policy_choices
        ):
            raise ValueError(
                "Hypothetical reserve requires the exact original confirmed emergency version"
            )
        source = next(
            v for v in basis.versions if v.version_id == policy_change.expected_version_id
        )
        config = validate_configuration(
            {**source.configuration, "amount_cents": policy_change.amount_cents}
        )
        start, end = _window(config, snapshot.as_of, zone)
        assumption = PolicyChangeAssumption(
            user_id=basis.user_id,
            policy_id=source.policy_id,
            source_version_id=source.version_id,
            configuration=config,
            configuration_hash=configuration_hash(config),
            timezone=snapshot.timezone,
            assumed_confirmation_at=snapshot.as_of,
            assumed_valid_from=start,
            assumed_valid_until=end,
            source_status="ACTIVE" if source.valid_from <= snapshot.as_of else "CONFIRMED",
        )
        result = compute_policy_change_boundary(
            hypothetical,
            basis.versions,
            basis.positions,
            products,
            source_version=source,
            assumption=assumption,
        ).boundary
        changed.append(
            ChangedParameter(
                kind="EMERGENCY_AMOUNT",
                entity_id=source.policy_id,
                original={
                    "version_id": str(source.version_id),
                    "configuration_hash": source.content_hash,
                    "amount_cents": source.configuration["amount_cents"],
                },
                hypothetical={
                    "configuration_hash": assumption.configuration_hash,
                    "amount_cents": policy_change.amount_cents,
                },
            )
        )
    else:
        result = compute_boundary(hypothetical, basis.versions, basis.positions, products)
    request_hash = configuration_hash(request.model_dump(mode="json"))
    return ScenarioComparison(
        user_id=basis.user_id,
        epoch_id=basis.epoch_id,
        as_of=snapshot.as_of,
        timezone=snapshot.timezone,
        local_date=snapshot.as_of.astimezone(zone).date(),
        horizon_days=request.horizon_days,
        source_hash=basis.source_hash,
        engine_hash=basis.engine_hash,
        original_request=request,
        request_hash=request_hash,
        scenario_hash=configuration_hash(
            {
                "protocol": "counterfactual-comparison-v1",
                "request_hash": request_hash,
                "as_of": snapshot.as_of.isoformat(),
                "baseline": baseline.boundary_hash,
                "hypothetical": result.boundary_hash,
            }
        ),
        baseline=baseline,
        hypothetical=result,
        delta_safe_idle_cents=None
        if baseline.safe_idle_cents is None or result.safe_idle_cents is None
        else result.safe_idle_cents - baseline.safe_idle_cents,
        changed_parameters=changed,
        limitations=LIMITATIONS,
    )
