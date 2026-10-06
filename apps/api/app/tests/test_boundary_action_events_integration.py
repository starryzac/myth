"""Persistent event observations do not mutate money or replace original decision traces."""

from uuid import uuid4

import pytest
from app.services.action_contracts import PrepareActionRequest, TransferIntent
from app.services.audit_chain import current_audit_epoch, verify_audit_chain
from app.services.boundary_action_events import BoundaryObservationRequest, observe_boundary_actions
from app.services.decision_trace import get_decision_trace
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF
from app.services.execution import prepare_action
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_boundary_service import boundary_engine as boundary_engine
from app.tests.test_boundary_service import snapshot
from app.tests.test_execution_service import transfer_accounts
from app.tests.test_full_goals_api import money_originals
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_actual_amount_change_is_audited_once_and_replayed_without_financial_writes(
    boundary_engine: Engine,
) -> None:
    source, destination = transfer_accounts(boundary_engine)
    actions = [
        prepare_action(
            boundary_engine,
            DEMO_USER_ID,
            PrepareActionRequest(
                idempotency_key=f"boundary-event-{amount}",
                intent=TransferIntent(
                    kind="transfer_internal",
                    source_account_id=source,
                    destination_account_id=destination,
                    amount_cents=amount,
                ),
            ),
            SEED_AS_OF,
        )
        for amount in (100, 200)
    ]
    first, second = (action.decision_run_id for action in actions)
    assert first is not None and second is not None
    with Session(boundary_engine) as session:
        epoch = current_audit_epoch(session, DEMO_USER_ID)
        assert epoch is not None
        epoch_id = epoch.id
        original_hash = get_decision_trace(session, DEMO_USER_ID, first, SEED_AS_OF).trace
        assert original_hash is not None
    before = snapshot(boundary_engine)
    body = BoundaryObservationRequest(
        before_run_id=first, after_run_id=second, expected_epoch_id=epoch_id
    )
    with Session(boundary_engine) as session, session.begin():
        response = observe_boundary_actions(session, DEMO_USER_ID, body, SEED_AS_OF)
        assert response.kind == "BoundaryCrossed"
        assert response.before_action_signature != response.after_action_signature
        assert response.bank_authority is False
    recorded = snapshot(boundary_engine)
    assert money_originals(recorded) == money_originals(before)
    with Session(boundary_engine) as session, session.begin():
        replay = observe_boundary_actions(session, DEMO_USER_ID, body, SEED_AS_OF)
        assert replay.model_dump() == response.model_dump() | {"idempotent_replay": True}
        saved = get_decision_trace(session, DEMO_USER_ID, response.observation_run_id, SEED_AS_OF)
        assert saved.completeness == "COMPLETE" and saved.audit_chain_status == "VALID"
        assert saved.trace is not None
        assert saved.trace.outcome["boundary_observation"]["kind"] == "BoundaryCrossed"
        assert get_decision_trace(session, DEMO_USER_ID, first, SEED_AS_OF).trace == original_hash
        assert verify_audit_chain(session, DEMO_USER_ID).errors == []
    assert snapshot(boundary_engine) == recorded
    with Session(boundary_engine) as session, session.begin():
        same = observe_boundary_actions(
            session, DEMO_USER_ID, body.model_copy(update={"after_run_id": first}), SEED_AS_OF
        )
        assert (
            same.kind == "BoundaryObserved"
            and same.before_action_signature == same.after_action_signature
        )
    unchanged = snapshot(boundary_engine)
    with Session(boundary_engine) as session, session.begin():
        with pytest.raises(PolicyLifecycleError) as stale:
            observe_boundary_actions(
                session,
                DEMO_USER_ID,
                body.model_copy(update={"expected_epoch_id": uuid4()}),
                SEED_AS_OF,
            )
        assert stale.value.code == "STALE_DEMO_EPOCH"
    assert snapshot(boundary_engine) == unchanged
