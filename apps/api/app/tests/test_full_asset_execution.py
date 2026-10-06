"""Literal portfolio consumer contracts; no permission or bank experiment proof."""

from datetime import timedelta
from uuid import UUID

import pytest
from app.domain.boundary_types import BoundaryPolicyVersion
from app.domain.execution_types import ExecutionContext
from app.domain.full_asset_allocation import plan_full_assets
from app.domain.full_asset_execution import (
    FullAssetCatalogueReference,
    FullAssetConfirmRequest,
    FullAssetExecuteRequest,
    FullAssetExecutionBasis,
    FullAssetFrozenPortfolio,
    FullAssetPrepareRequest,
    build_frozen_portfolio,
)
from app.domain.policy_configuration import configuration_hash
from app.tests.test_asset_allocation import NOW, authorization
from app.tests.test_full_asset_allocation import request as planning_request
from pydantic import ValidationError


def literal_basis() -> tuple[FullAssetPrepareRequest, FullAssetExecutionBasis]:
    original = planning_request(cap=400000)
    original = original.model_copy(
        update={"policy_id": UUID(int=601), "policy_version_id": UUID(int=602)}
    )
    planning = plan_full_assets(original)
    assert planning.status == "OPTIMAL" and len(planning.batches) == 2
    mvp = authorization(single_action_cap_cents=400000)
    context = ExecutionContext(
        user_id=UUID(int=700),
        snapshot=original.snapshot,
        versions=[mvp],
        positions=original.positions,
        boundary_products=original.boundary_products,
        products=original.products,
        exposure=original.exposure,
    )
    prepare = FullAssetPrepareRequest(
        full_policy_id=original.policy_id,
        expected_full_policy_version_id=original.policy_version_id,
        mvp_asset_policy_id=mvp.policy_id,
        expected_mvp_policy_version_id=mvp.version_id,
        expected_epoch_id=UUID(int=701),
        idempotency_key="PURE_ONLY_PORTFOLIO:1",
    )
    basis = FullAssetExecutionBasis(
        user_id=context.user_id,
        epoch_id=prepare.expected_epoch_id,
        as_of=NOW,
        full_policy_configuration=original.configuration,
        full_policy_content_hash=configuration_hash(original.configuration.model_dump(mode="json")),
        full_planning_confirmation_valid=True,
        original_planning_response_hash="e" * 64,
        planning=planning,
        context=context,
        catalogue=[
            FullAssetCatalogueReference(
                product_id=p.product_id,
                catalogue_version_id=UUID(int=800 + p.product_id.int),
                product_record_hash="a" * 64,
                terms_digest=p.terms_digest,
            )
            for p in original.products
        ],
        position_accounts={p.product_id: UUID(int=900) for p in original.products},
        batch_income_uses=[[] for _ in planning.batches],
        full_protection_sources=[],
        full_protection_inventory_complete=True,
        full_source_issues=[],
        source_evidence_ids=[],
        expires_at=NOW + timedelta(minutes=15),
    )
    return prepare, basis


def test_true_combined_contract_consumes_two_products_without_changing_old_effect_schema() -> None:
    prepare, basis = literal_basis()
    frozen = build_frozen_portfolio(prepare, basis)
    assert len(frozen.batches) == 2
    # Rounded yields tie over a plateau: the original optimizer then minimizes turnover.
    # The consumer retains this exact original combination, rather than forcing both caps.
    assert frozen.total_purchase_cents == 799792
    assert len(frozen.combined_original_boundary.calculation_trace) == 1098
    assert (
        frozen.batches[0].command.effect.product_id != frozen.batches[1].command.effect.product_id
    )
    assert frozen.batches[0].bank_idempotency_key != frozen.batches[1].bank_idempotency_key
    assert not frozen.bank_authority and not frozen.funds_reserved
    assert frozen.cross_operation_atomicity == "NOT_AVAILABLE"
    assert not frozen.financial_experiment_verified
    assert build_frozen_portfolio(prepare, basis) == frozen
    assert FullAssetFrozenPortfolio.model_validate_json(frozen.model_dump_json()) == frozen


@pytest.mark.parametrize(
    "field", ["amount_cents", "bank_facts", "now", "permissions", "products", "effects", "options"]
)
def test_client_cannot_supply_financial_results_or_selection(field: str) -> None:
    prepare, _ = literal_basis()
    with pytest.raises(ValidationError):
        FullAssetPrepareRequest.model_validate({**prepare.model_dump(), field: 1})


@pytest.mark.parametrize("accepted", [False, 1, "true", None])
def test_confirmation_is_strict_and_complete(accepted: object) -> None:
    with pytest.raises(ValidationError):
        FullAssetConfirmRequest.model_validate(
            {
                "accepted": accepted,
                "reviewed_portfolio_hash": "a" * 64,
                "expected_epoch_id": UUID(int=701),
                "idempotency_key": "original",
            }
        )
    with pytest.raises(ValidationError):
        FullAssetExecuteRequest.model_validate(
            {
                "accepted": accepted,
                "reviewed_portfolio_hash": "a" * 64,
                "expected_epoch_id": UUID(int=701),
                "expected_batch_number": 1,
                "expected_action_id": UUID(int=702),
            }
        )


@pytest.mark.parametrize(
    "risk",
    [
        "scope",
        "epoch",
        "mvp",
        "full-source",
        "inventory",
        "aggregate-cap",
        "catalogue",
        "amount",
        "income-denominator",
    ],
)
def test_missing_original_or_joint_capacity_never_becomes_an_executable_portfolio(
    risk: str,
) -> None:
    prepare, basis = literal_basis()
    if risk == "scope":
        prepare = prepare.model_copy(
            update={"goal_id": UUID(int=55), "expected_goal_policy_version_id": UUID(int=56)}
        )
    if risk == "epoch":
        basis = basis.model_copy(update={"epoch_id": UUID(int=1)})
    if risk == "mvp":
        prepare = prepare.model_copy(update={"expected_mvp_policy_version_id": UUID(int=1)})
    if risk == "full-source":
        basis = basis.model_copy(update={"full_source_issues": ["MISSING_ORIGINAL"]})
    if risk == "inventory":
        basis = basis.model_copy(update={"full_protection_inventory_complete": False})
    if risk == "aggregate-cap":
        basis = basis.model_copy(
            update={
                "full_policy_configuration": basis.full_policy_configuration.model_copy(
                    update={"max_auto_managed_cents": 700000}
                )
            }
        )
    if risk == "catalogue":
        basis = basis.model_copy(update={"catalogue": basis.catalogue[:1]})
    if risk == "amount":
        basis = basis.model_copy(
            update={"planning": basis.planning.model_copy(update={"total_purchase_cents": 700000})}
        )
    if risk == "income-denominator":
        basis = basis.model_copy(update={"batch_income_uses": []})
    with pytest.raises((ValueError, ValidationError)):
        build_frozen_portfolio(prepare, basis)


def test_frozen_whole_hash_and_batch_identity_cannot_be_relabelled() -> None:
    prepare, basis = literal_basis()
    value = build_frozen_portfolio(prepare, basis).model_dump(mode="json")
    value["batches"][1]["batch_number"] = 1
    values = {key: part for key, part in value.items() if key != "portfolio_hash"}
    value["portfolio_hash"] = configuration_hash(values)
    with pytest.raises(ValidationError):
        FullAssetFrozenPortfolio.model_validate(value)


def test_two_individually_feasible_purchases_are_refused_by_whole_hard_protection() -> None:
    prepare, basis = literal_basis()
    # Each original ~400000 batch can leave >400000 cash; together the actual
    # planned ~800000 would leave <400000. No sum of individual maxima is safe.
    emergency = {"type": "emergency_buffer", "amount_cents": 400000}
    from app.domain.policy_configuration import validate_configuration

    emergency = validate_configuration(emergency)
    policy = BoundaryPolicyVersion(
        policy_id=UUID(int=990),
        version_id=UUID(int=991),
        configuration=emergency,
        content_hash=configuration_hash(emergency),
        confirmed_at=NOW - timedelta(days=1),
        valid_from=NOW - timedelta(days=1),
    )
    context = basis.context.model_copy(update={"versions": [*basis.context.versions, policy]})
    with pytest.raises(ValueError, match="WHOLE_CURRENT_PROTECTION"):
        build_frozen_portfolio(prepare, basis.model_copy(update={"context": context}))
