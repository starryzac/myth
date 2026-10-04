"""Public execution service keeps delayed and explicitly priced bank consequences."""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from app.db.models import (
    ActionPlan,
    ActionResourceReservation,
    AssetPosition,
    BankOperation,
    DecisionRun,
    EvidenceItem,
    PolicyVersion,
)
from app.domain.policy_configuration import configuration_hash
from app.services.action_contracts import ConfirmActionRequest, PrepareActionRequest, RedeemIntent
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF
from app.services.execution import confirm_action, execute_action, get_action, prepare_action
from app.services.execution_exposure import refresh_execution_exposure
from app.services.execution_sources import load_execution_quote
from app.services.policy_lifecycle import PolicyLifecycleError, revoke_policy
from app.tests.test_asset_allocation_service import authorization
from app.tests.test_boundary_service import boundary_engine, confirmed_policy, snapshot
from app.tests.test_recovery_service import recovery_fixture
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

__all__ = ["boundary_engine"]
pytestmark = pytest.mark.integration


def test_accepted_t1_get_is_readonly_and_original_execution_settles_after_revocation(
    boundary_engine: Engine,
) -> None:
    policy_id, position_id, principal = recovery_fixture(boundary_engine, delay=1)
    prepared = prepare_action(
        boundary_engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="public-t1",
            intent=RedeemIntent(
                kind="redeem_asset",
                position_id=position_id,
            ),
        ),
        SEED_AS_OF,
    )
    accepted = execute_action(boundary_engine, DEMO_USER_ID, prepared.action_id, SEED_AS_OF)
    assert accepted.bank_status == "ACCEPTED" and accepted.receipt is None
    with Session(boundary_engine) as session, session.begin():
        version = session.scalars(
            select(PolicyVersion).where(
                PolicyVersion.policy_id == policy_id,
            )
        ).one()
        revoke_policy(
            session, DEMO_USER_ID, policy_id, version.id, SEED_AS_OF + timedelta(minutes=1)
        )
    later = SEED_AS_OF + timedelta(days=1)
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        read = get_action(session, DEMO_USER_ID, prepared.action_id, later)
        assert read.bank_status == "ACCEPTED" and read.receipt is None
    assert snapshot(boundary_engine) == before
    settled = execute_action(boundary_engine, DEMO_USER_ID, prepared.action_id, later)
    assert settled.status == "SUCCEEDED" and settled.receipt is not None
    assert settled.receipt.executed_cents == principal
    with Session(boundary_engine) as session:
        assert len(list(session.scalars(select(BankOperation)))) == 1
        assert all(
            r.status == "CONSUMED" for r in session.scalars(select(ActionResourceReservation))
        )
    done = snapshot(boundary_engine)
    assert execute_action(boundary_engine, DEMO_USER_ID, prepared.action_id, later).receipt == (
        settled.receipt
    )
    assert snapshot(boundary_engine) == done


def test_explicit_net_fee_loss_confirmation_executes_once_through_public_service(
    boundary_engine: Engine,
) -> None:
    _, position_id, principal = recovery_fixture(boundary_engine)
    with Session(boundary_engine) as session, session.begin():
        # Trusted historical fixture: the acquisition itself allowed this cost;
        # one-shot consent must not create that missing standing permission.
        _, version_id = confirmed_policy(
            session,
            authorization(allow_early_withdrawal_with_penalty=True),
            SEED_AS_OF - timedelta(days=40),
        )
        position = session.get(AssetPosition, position_id)
        assert position is not None
        proof = session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.source_type == "SIMULATED_BANK_POSITION",
                EvidenceItem.content["position_id"].as_string() == str(position_id),
                EvidenceItem.status == "VALID",
            )
        ).one()
        purchase = session.get(ActionPlan, UUID(proof.content["purchase_action_id"]))
        assert purchase is not None
        run = session.get(DecisionRun, purchase.decision_run_id)
        assert run is not None
        position.policy_version_id = purchase.policy_version_id = version_id
        run.policy_version_ids = [str(version_id)]
        proof.content = {**proof.content, "policy_version_id": str(version_id)}
        proof.content_hash = configuration_hash(proof.content)
        session.flush()
        quote = load_execution_quote(session, DEMO_USER_ID, position_id, SEED_AS_OF)
        quote = quote.model_copy(
            update={"fee_cents": 100, "loss_cents": 1000, "net_cents": principal - 1100}
        )
        from app.services.execution_sources import execution_return_account

        payload = {
            "simulation": True,
            "protocol": "recovery-quote-v1",
            "user_id": str(DEMO_USER_ID),
            "position_id": str(position_id),
            "destination_account_id": str(
                execution_return_account(session, DEMO_USER_ID, position, SEED_AS_OF)
            ),
            "goal_id": None,
            "quote": quote.model_dump(mode="json"),
        }
        session.add(
            EvidenceItem(
                id=uuid4(),
                user_id=DEMO_USER_ID,
                created_at=SEED_AS_OF,
                evidence_level="BANK_CONFIRMED",
                source_type="SIMULATED_REDEMPTION_QUOTE",
                source_ref="public-cost-quote",
                content=payload,
                content_hash=configuration_hash(payload),
                status="VALID",
                valid_from=SEED_AS_OF,
                observed_at=SEED_AS_OF,
            )
        )
        session.flush()
        refresh_execution_exposure(session, DEMO_USER_ID, SEED_AS_OF, uuid4())
    prepared = prepare_action(
        boundary_engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="public-cost",
            intent=RedeemIntent(kind="redeem_asset", position_id=position_id),
        ),
        SEED_AS_OF,
    )
    assert prepared.autonomy_level == "ASK_ONCE"
    assert prepared.effect.fee_cents == 100 and prepared.effect.loss_cents == 1000
    before = snapshot(boundary_engine)
    with pytest.raises(PolicyLifecycleError):
        execute_action(boundary_engine, DEMO_USER_ID, prepared.action_id, SEED_AS_OF)
    assert snapshot(boundary_engine) == before
    confirm_action(
        boundary_engine,
        DEMO_USER_ID,
        prepared.action_id,
        ConfirmActionRequest(effect_hash=prepared.effect_hash, accepted=True),
        SEED_AS_OF,
    )
    result = execute_action(boundary_engine, DEMO_USER_ID, prepared.action_id, SEED_AS_OF)
    assert result.status == "SUCCEEDED" and result.receipt is not None
    assert result.receipt.executed_cents == principal
    assert result.receipt.fee_cents == 100 and result.receipt.loss_cents == 1000
    done = snapshot(boundary_engine)
    assert execute_action(
        boundary_engine, DEMO_USER_ID, prepared.action_id, SEED_AS_OF
    ).receipt == (result.receipt)
    assert snapshot(boundary_engine) == done
