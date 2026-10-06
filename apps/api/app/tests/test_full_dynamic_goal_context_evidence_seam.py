"""Synthetic evidence enrichment risks; current bank integration is separate."""

from uuid import UUID

import pytest
from app.domain.asset_exposure import AssetExposure
from app.domain.execution_types import ExecutionContext, ExecutionEffect
from app.domain.full_dynamic_goal_execution import (
    FullDynamicGoalProof,
    bind_full_dynamic_goal_consumer_context,
    build_full_dynamic_goal_effect,
    validate_full_dynamic_goal_proof,
)
from app.tests.test_full_dynamic_goal_execution import BANK, INCOME, MODEL, NOW, fixture


def original() -> tuple[ExecutionEffect, FullDynamicGoalProof, ExecutionContext]:
    data = fixture()
    exposure = AssetExposure(
        as_of=NOW,
        scope="goal",
        goal_id=data.request.goal_id,
        managed_principal_cents=0,
        pending_purchase_cents=0,
        evidence_ids=[BANK, INCOME, MODEL],
    )
    producer = data.context.model_copy(update={"exposure": exposure})
    effect, proof = build_full_dynamic_goal_effect(
        data.model_copy(update={"context": producer}), UUID(int=200)
    )
    consumer = producer.model_copy(
        update={"exposure": exposure.model_copy(update={"evidence_ids": [BANK, INCOME]})}
    )
    return effect, proof, consumer


def test_only_captured_producer_references_bind_the_unchanged_original_proof() -> None:
    effect, proof, consumer = original()
    before = consumer.model_dump(mode="json")
    original_hash = proof.context_hash
    assert not validate_full_dynamic_goal_proof(effect, consumer, proof)
    bound = bind_full_dynamic_goal_consumer_context(effect, consumer, proof)
    assert bound == proof.inputs.context
    assert validate_full_dynamic_goal_proof(effect, bound, proof)
    assert consumer.model_dump(mode="json") == before and proof.context_hash == original_hash


@pytest.mark.parametrize("field", ["user", "clock", "cash", "consent", "reservation", "income"])
def test_current_financial_scope_or_permission_changes_cannot_be_enriched(field: str) -> None:
    effect, proof, consumer = original()
    snapshot = consumer.snapshot
    if field == "user":
        consumer = consumer.model_copy(update={"user_id": UUID(int=999)})
    elif field == "clock":
        consumer = consumer.model_copy(
            update={"snapshot": snapshot.model_copy(update={"timezone": "Asia/Shanghai"})}
        )
    elif field == "cash":
        cash = snapshot.cash_accounts[0].model_copy(update={"balance_cents": 700001})
        consumer = consumer.model_copy(
            update={
                "snapshot": snapshot.model_copy(
                    update={"cash_accounts": [cash, *snapshot.cash_accounts[1:]]}
                )
            }
        )
    elif field == "consent":
        consumer = consumer.model_copy(update={"requires_confirmation": True})
    elif field == "reservation":
        consumer = consumer.model_copy(update={"reserved_cash_by_account": {UUID(int=5): 1}})
    else:
        consumer = consumer.model_copy(update={"lots": []})
    with pytest.raises(ValueError):
        bind_full_dynamic_goal_consumer_context(effect, consumer, proof)


@pytest.mark.parametrize(
    "kind", ["unknown_consumer", "duplicate", "missing_producer_ref", "bad_proof"]
)
def test_unknown_uncaptured_duplicate_or_invalid_proof_is_refused(kind: str) -> None:
    effect, proof, consumer = original()
    exposure = consumer.exposure
    assert exposure is not None
    if kind == "unknown_consumer":
        consumer = consumer.model_copy(
            update={"exposure": exposure.model_copy(update={"evidence_ids": [BANK, UUID(int=999)]})}
        )
    elif kind == "duplicate":
        consumer = consumer.model_copy(
            update={"exposure": exposure.model_copy(update={"evidence_ids": [BANK, BANK]})}
        )
    elif kind == "missing_producer_ref":
        data = proof.inputs.model_copy(
            update={
                "source_refs": [row for row in proof.inputs.source_refs if row.evidence_id != BANK]
            }
        )
        from app.domain.full_dynamic_goal_execution import derive_full_dynamic_goal_proof

        proof = derive_full_dynamic_goal_proof(data, effect)
    else:
        proof = proof.model_copy(update={"context_hash": "0" * 64})
    with pytest.raises(ValueError):
        bind_full_dynamic_goal_consumer_context(effect, consumer, proof)
