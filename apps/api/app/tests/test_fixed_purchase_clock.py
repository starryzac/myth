"""A fixed purchase uses its immutable arrival upper bound across real service requests."""

from datetime import UTC, datetime, timedelta

import pytest
from app.db.models import AssetPosition, BankOperation, EvidenceItem
from app.domain.policy_configuration import configuration_hash
from app.services.action_contracts import ActionResponse, PrepareActionRequest, PurchaseIntent
from app.services.audit_chain import current_audit_epoch, verify_audit_chain
from app.services.demo_console import prepare_demo_template
from app.services.demo_seed import DEMO_USER_ID, seed_demo
from app.services.execution import execute_action, prepare_action
from app.services.policy_lifecycle import PolicyLifecycleError, confirm_proposal
from app.tests.test_demo_seed import demo_engine as demo_engine
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 4, 1, tzinfo=UTC)


def prepare_fixed(engine: Engine) -> ActionResponse:
    seed_demo(engine)
    with Session(engine) as session:
        epoch = current_audit_epoch(session, DEMO_USER_ID)
        assert epoch is not None
        epoch_id = epoch.id
    template = prepare_demo_template(engine, DEMO_USER_ID, "FIXED_ASSET", epoch_id, NOW)
    assert template.proposal_id is not None
    with Session(engine) as session, session.begin():
        confirmed = confirm_proposal(
            session,
            DEMO_USER_ID,
            template.proposal_id,
            template.configuration_hash,
            True,
            NOW + timedelta(seconds=1),
        )
    return prepare_action(
        engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="w1-fixed-advancing-clock",
            intent=PurchaseIntent(kind="purchase_asset", policy_id=confirmed.policy_id),
        ),
        NOW + timedelta(seconds=2),
    )


def test_fixed_purchase_later_request_honors_original_bound_and_retains_original_plan(
    demo_engine: Engine,
) -> None:
    prepared = prepare_fixed(demo_engine)
    assert prepared.effect.purchase_exit is not None
    assert prepared.effect.latest_arrival_at == (
        prepared.effect.purchase_exit.principal_available_at + timedelta(minutes=15)
    )
    later = NOW + timedelta(seconds=3)
    purchased = execute_action(demo_engine, DEMO_USER_ID, prepared.action_id, later)
    assert purchased.status == "SUCCEEDED" and purchased.receipt is not None
    assert purchased.effect_hash == prepared.effect_hash and purchased.effect == prepared.effect
    assert purchased.receipt.executed_cents == 50_000
    with Session(demo_engine) as session:
        position = session.get(AssetPosition, prepared.effect.position_id)
        assert position is not None and position.purchased_at == later
        assert position.available_at == later + timedelta(days=30)
        assert position.available_at <= prepared.effect.latest_arrival_at
        proof = session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.source_type == "SIMULATED_BANK_POSITION",
                EvidenceItem.content["position_id"].as_string() == str(position.id),
                EvidenceItem.status == "VALID",
            )
        )
        assert proof is not None
        assert proof.content_hash == configuration_hash(proof.content)
        assert proof.content["purchase_exit_plan"] == prepared.effect.purchase_exit.model_dump(
            mode="json"
        )
        original = session.get(BankOperation, prepared.action_id)
        assert original is not None
        assert original.request["effect"] == prepared.effect.model_dump(mode="json")
        assert verify_audit_chain(session, DEMO_USER_ID).status == "VALID"


def test_fixed_purchase_after_bound_refuses_before_independent_bank_effect(
    demo_engine: Engine,
) -> None:
    prepared = prepare_fixed(demo_engine)
    with pytest.raises(PolicyLifecycleError):
        execute_action(
            demo_engine,
            DEMO_USER_ID,
            prepared.action_id,
            NOW + timedelta(seconds=2, minutes=15, microseconds=1),
        )
    with Session(demo_engine) as session:
        assert session.get(BankOperation, prepared.action_id) is None
        assert session.get(AssetPosition, prepared.effect.position_id) is None
