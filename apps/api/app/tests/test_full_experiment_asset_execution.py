"""Pure synthetic consumer risks; not actual bank/arm/safety-metric evidence."""

import hashlib
import json
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid5

import pytest
from app.domain.decision_trace import build_trace
from app.domain.decision_trace_types import TraceEvidence
from app.domain.execution import execution_effect_hash
from app.domain.execution_types import BankCommand, ExecutionContext
from app.domain.full_asset_allocation import plan_full_assets
from app.domain.full_asset_execution import FullAssetCatalogueReference
from app.domain.full_experiment_asset_execution import (
    ALGORITHM,
    MARKER,
    FullExperimentAssetExecutionInput,
    FullExperimentAssetRequest,
    build_full_experiment_asset_effect,
    experiment_asset_bank_key,
    read_frozen_full_experiment_asset_proof,
)
from app.domain.full_experiment_asset_selection import (
    FullExperimentAssetSelection,
    FullMechanismSelectedPortfolio,
    RegisteredFullMechanismRule,
    select_current_general_purchase,
)
from app.domain.full_protection_projection import (
    FullProtectionProjectionInput,
    project_full_protection,
)
from app.domain.policy_configuration import configuration_hash
from app.services.full_experiment_asset_execution import prepare_full_experiment_asset_execution
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_asset_allocation import NOW, authorization
from app.tests.test_full_asset_allocation import request
from app.tests.test_full_experiment_asset_selection import original
from pydantic import ValidationError
from sqlalchemy import create_engine


def fixture(arm: str = "P") -> FullExperimentAssetExecutionInput:
    body = original(arm, rule={"threshold_cents": 800000, "fixed_budget_cents": 850000})
    permission = authorization().model_copy(
        update={
            "policy_id": body.original_request.mvp_asset_policy_id,
            "version_id": body.original_request.expected_mvp_policy_version_id,
        }
    )
    planning = request().model_copy(update={"boundary_versions": [permission]})
    curve = project_full_protection(
        FullProtectionProjectionInput(
            snapshot=planning.snapshot,
            boundary_versions=planning.boundary_versions,
            positions=planning.positions,
            boundary_products=planning.boundary_products,
            policies=[],
        )
    ).full_annual_projection
    assert curve is not None
    catalogue = [
        FullAssetCatalogueReference(
            product_id=p.product_id,
            catalogue_version_id=UUID(int=100 + p.product_id.int),
            product_record_hash=configuration_hash(p.model_dump(mode="json")),
            terms_digest=p.terms_digest,
        )
        for p in planning.products
    ]
    raw = json.dumps(body.model_dump(mode="json"), ensure_ascii=False, indent=2)
    rule_sha = hashlib.sha256(raw.encode()).hexdigest()
    originals: dict[str, Any] = {
        "rule_original_utf8": raw,
        "mvp_version": {"configuration": permission.configuration},
        "full_protection": {
            "projection": {"full_annual_projection": curve.model_dump(mode="json")}
        },
    }
    counts = {"SYNTHETIC_current_products": len(planning.products)}
    source_hash = configuration_hash(
        {
            "user_id": str(body.user_id),
            "epoch_id": str(body.original_request.expected_epoch_id),
            "as_of": NOW.isoformat(),
            "originals": originals,
            "counts": counts,
        }
    )
    inputs, decision, products, batch = select_current_general_purchase(
        planning, curve, permission.configuration, catalogue, body, rule_sha, source_hash
    )
    assert batch is not None
    selected_values = {
        "user_id": str(body.user_id),
        "epoch_id": str(body.original_request.expected_epoch_id),
        "original_request": body.original_request.model_dump(mode="json"),
        "rule_original_sha256": rule_sha,
        "current_source_hash": source_hash,
        "mechanism_input_hash": decision.input_sha256,
        "selected_product": next(
            row.catalogue.model_dump(mode="json")
            for row in products
            if row.product.product_id == batch.product_id
        ),
        "batch": batch.model_dump(mode="json"),
        "protocol": "full-mechanism-selected-portfolio-v1",
        "bank_authority": False,
        "funds_reserved": False,
        "preparation_status": "NOT_PREPARED",
    }
    selection = FullExperimentAssetSelection(
        user_id=body.user_id,
        epoch_id=body.original_request.expected_epoch_id,
        as_of=NOW,
        status="PROPOSED",
        original_request=body.original_request,
        rule_original=body,
        rule_original_sha256=rule_sha,
        current_source_hash=source_hash,
        source_evidence_ids=[UUID(int=909)],
        source_originals=originals,
        source_counts=counts,
        planning_input=planning,
        original_p_result=plan_full_assets(planning),
        mechanism_input=inputs,
        mechanism_decision=decision,
        product_originals=products,
        selected_portfolio=FullMechanismSelectedPortfolio.model_validate_json(
            json.dumps({**selected_values, "portfolio_hash": configuration_hash(selected_values)})
        ),
        reasons=[],
        limitations=["SYNTHETIC_TOOL_ONLY"],
    )
    source_content = {"SYNTHETIC_TOOL_ONLY": True}
    source = TraceEvidence(
        id=UUID(int=909),
        user_id=body.user_id,
        evidence_level="USER_DECLARED",
        source_type="SYNTHETIC_CONSUMER",
        source_ref="SYNTHETIC_TOOL_ONLY",
        content=source_content,
        content_hash=configuration_hash(source_content),
        captured_content_hash=configuration_hash(source_content),
        content_integrity="VERIFIED",
        status_at_decision="VALID",
        observed_at=NOW,
        valid_from=NOW,
    )
    return FullExperimentAssetExecutionInput(
        user_id=body.user_id,
        epoch_id=body.original_request.expected_epoch_id,
        as_of=NOW,
        request=FullExperimentAssetRequest(
            original_request=body.original_request,
            rule_original=RegisteredFullMechanismRule(
                original_path=".runtime/SYNTHETIC_TOOL_ONLY.json", sha256=rule_sha
            ),
        ),
        original_selection=selection,
        rule=body,
        rule_original_utf8=raw,
        current_planning=planning,
        current_curve=curve,
        context=ExecutionContext(
            user_id=body.user_id,
            snapshot=planning.snapshot,
            versions=planning.boundary_versions,
            positions=planning.positions,
            boundary_products=planning.boundary_products,
            products=planning.products,
            exposure=planning.exposure,
            requires_confirmation=True,
        ),
        catalogue=catalogue,
        position_account_id=UUID(int=900),
        income_uses=[],
        expires_at=NOW + timedelta(minutes=15),
        protection_sources=[],
        protection_inventory_complete=True,
        source_issues=[],
        source_originals=[source],
        current_originals={"SYNTHETIC_TOOL_ONLY": True},
    )


@pytest.mark.parametrize(
    "arm,amount", [("B0", 12345), ("B1", 200000), ("B2", 150000), ("B3", 1000000), ("P", 1000000)]
)
def test_actual_pure_selector_difference_is_consumed_without_replacing_old_p(
    arm: str, amount: int
) -> None:
    data = fixture(arm)
    p_original = data.original_selection.original_p_result
    effect, proof = build_full_experiment_asset_effect(data, UUID(int=123))
    assert effect.amount_cents == amount and effect.goal_id is None
    assert proof.current_product_denominator == len(data.current_planning.products) == 2
    assert proof.original_execution_validation["status"] == "CONFIRMATION_REQUIRED"
    assert data.original_selection.original_p_result == p_original
    assert execution_effect_hash(effect) == proof.effect_hash
    assert build_full_experiment_asset_effect(data, UUID(int=123), effect) == (effect, proof)
    assert not proof.bank_authority and not proof.receipt_is_current_authority


@pytest.mark.parametrize(
    "change",
    [
        "scope",
        "version",
        "epoch",
        "rule-hash",
        "source-status",
        "source-content",
        "source-denominator",
        "other-source-unknown",
        "inventory",
        "own-other-action",
        "cash-claim",
    ],
)
def test_fresh_financial_scope_or_partial_originals_never_make_an_effect(change: str) -> None:
    data = fixture()
    if change == "scope":
        data = data.model_copy(
            update={
                "request": data.request.model_copy(
                    update={
                        "original_request": data.request.original_request.model_copy(
                            update={
                                "goal_id": UUID(int=9),
                                "expected_goal_policy_version_id": UUID(int=10),
                            }
                        )
                    }
                )
            }
        )
    elif change == "version":
        data = data.model_copy(update={"context": data.context.model_copy(update={"versions": []})})
    elif change == "epoch":
        data = data.model_copy(update={"epoch_id": UUID(int=7)})
    elif change == "rule-hash":
        data = data.model_copy(update={"rule_original_utf8": data.rule_original_utf8 + " "})
    elif change == "source-status":
        data = data.model_copy(
            update={
                "source_originals": [
                    data.source_originals[0].model_copy(update={"status_at_decision": "UNKNOWN"})
                ]
            }
        )
    elif change == "source-content":
        data = data.model_copy(
            update={
                "source_originals": [
                    data.source_originals[0].model_copy(update={"content": {"changed": True}})
                ]
            }
        )
    elif change == "source-denominator":
        data = data.model_copy(update={"source_originals": []})
    elif change == "other-source-unknown":
        data = data.model_copy(update={"source_issues": ["UNKNOWN_OTHER_ACTION"]})
    elif change == "inventory":
        data = data.model_copy(update={"protection_inventory_complete": False})
    elif change == "own-other-action":
        data = data.model_copy(update={"own_action_id": UUID(int=456)})
    else:
        data = data.model_copy(
            update={
                "context": data.context.model_copy(
                    update={"reserved_cash_by_account": {UUID(int=1): 1}}
                )
            }
        )
    with pytest.raises(ValueError):
        build_full_experiment_asset_effect(data, UUID(int=123))


def test_same_key_does_not_depend_on_new_rule_and_original_effect_cannot_be_reselected() -> None:
    data = fixture("B1")
    effect, _ = build_full_experiment_asset_effect(data, UUID(int=123))
    assert experiment_asset_bank_key(
        data.request.original_request.idempotency_key
    ) == experiment_asset_bank_key(fixture("P").request.original_request.idempotency_key)
    for changed in [
        effect.model_copy(
            update={
                "amount_cents": 1,
                "cash_uses": [effect.cash_uses[0].model_copy(update={"amount_cents": 1})],
            }
        ),
        effect.model_copy(update={"expires_at": NOW + timedelta(minutes=16)}),
    ]:
        with pytest.raises(ValueError):
            build_full_experiment_asset_effect(data, UUID(int=123), changed)


def test_new_frozen_historical_algorithm_reproduces_full_input_and_no_current_io() -> None:
    data = fixture("B1")
    effect, proof = build_full_experiment_asset_effect(data, UUID(int=123))
    fields: dict[str, Any] = {
        "run_id": uuid5(effect.operation_id, "decision"),
        "user_id": data.user_id,
        "action_id": effect.operation_id,
        "phase": "PREPARE",
        "as_of": NOW,
        "algorithm_versions": {MARKER: ALGORITHM},
        "inputs": {
            "planning": {
                MARKER: {
                    "inputs": data.model_dump(mode="json"),
                    "proof": proof.model_dump(mode="json"),
                }
            },
            "action_request": {
                MARKER: {
                    "protocol": ALGORITHM,
                    "purpose": "DEVELOPMENT",
                    "user_id": str(data.user_id),
                    "epoch_id": str(data.epoch_id),
                    "request": data.request.model_dump(mode="json"),
                    "request_hash": configuration_hash(data.request.model_dump(mode="json")),
                    "effect_hash": proof.effect_hash,
                    "original_proof": proof.model_dump(mode="json"),
                },
                "execution": BankCommand(
                    effect=effect, effect_hash=execution_effect_hash(effect)
                ).model_dump(mode="json"),
            },
        },
        "sources": data.source_originals,
        "policies": [],
        "outcome": {},
    }
    trace = build_trace(**fields)
    assert read_frozen_full_experiment_asset_proof(trace) == proof
    fields["inputs"]["planning"][MARKER]["proof"]["current_product_denominator"] = 1
    with pytest.raises(ValueError):
        read_frozen_full_experiment_asset_proof(build_trace(**fields))


@pytest.mark.parametrize(
    "change",
    [
        "missing-marker",
        "new-authority-field",
        "different-key",
        "other-owner",
        "missing-trace-source",
    ],
)
def test_rehashed_historical_trace_cannot_replace_original_private_identity(change: str) -> None:
    data = fixture("B1")
    effect, proof = build_full_experiment_asset_effect(data, UUID(int=123))
    marker: dict[str, Any] = {
        "protocol": ALGORITHM,
        "purpose": "DEVELOPMENT",
        "user_id": str(data.user_id),
        "epoch_id": str(data.epoch_id),
        "request": data.request.model_dump(mode="json"),
        "request_hash": configuration_hash(data.request.model_dump(mode="json")),
        "effect_hash": proof.effect_hash,
        "original_proof": proof.model_dump(mode="json"),
    }
    payload: dict[str, Any] = {
        "execution": BankCommand(effect=effect, effect_hash=proof.effect_hash).model_dump(
            mode="json"
        ),
        MARKER: marker,
    }
    sources = data.source_originals
    if change == "missing-marker":
        payload.pop(MARKER)
    elif change == "new-authority-field":
        marker["grants_authority"] = True
    elif change == "different-key":
        marker["request"]["original_request"]["idempotency_key"] = "different-key"
    elif change == "other-owner":
        marker["user_id"] = str(UUID(int=91))
    else:
        sources = []
    with pytest.raises(ValueError):
        trace = build_trace(
            run_id=__import__("uuid").uuid5(effect.operation_id, "decision"),
            user_id=data.user_id,
            action_id=effect.operation_id,
            phase="PREPARE",
            as_of=NOW,
            algorithm_versions={MARKER: ALGORITHM},
            inputs={
                "planning": {
                    MARKER: {
                        "inputs": data.model_dump(mode="json"),
                        "proof": proof.model_dump(mode="json"),
                    }
                },
                "action_request": payload,
            },
            sources=sources,
            policies=[],
            outcome={},
        )
        read_frozen_full_experiment_asset_proof(trace)


def test_client_money_and_missing_original_pipeline_are_not_fake_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import execution_bank

    monkeypatch.delattr(execution_bank, "FULL_EXPERIMENT_ASSET_GUARDS_VERSION", raising=False)
    data = fixture()
    with pytest.raises(ValidationError):
        FullExperimentAssetRequest.model_validate({**data.request.model_dump(), "amount_cents": 1})
    engine = create_engine("sqlite://")
    try:
        with pytest.raises(PolicyLifecycleError, match="not installed"):
            prepare_full_experiment_asset_execution(engine, data.user_id, data.request, NOW)
    finally:
        engine.dispose()
