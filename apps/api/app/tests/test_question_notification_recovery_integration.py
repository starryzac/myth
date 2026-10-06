"""Actual missed-postcommit/reopened-connection candidate; Root runs after held PG."""

import pytest
from app.db.full_models import InterventionInbox, InterventionOutbox
from app.db.models import Account
from app.domain.finite_uncertainty import AccountChoice, FiniteChoice, FinitePlanningVariable
from app.services.action_contracts import PrepareActionRequest, TransferIntent
from app.services.audit_chain import current_audit_epoch
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, seed_demo
from app.services.execution import prepare_action
from app.services.question_intervention_recovery import recover_current_question_notifications
from app.services.question_workflow import (
    QuestionCloseRequest,
    QuestionStartRequest,
    close_question_session,
    start_question_session,
)
from app.tests.test_demo_seed import demo_engine as demo_engine
from app.tests.test_execution_service import transfer_accounts
from app.tests.test_finite_uncertainty import amount_variable
from app.tests.test_full_intervention_api import financial_snapshot
from app.tests.test_full_projection_api import physical_snapshot
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_actual_worker_recovers_missed_question_observation_and_reopened_engine_replays_zero(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    source, target = transfer_accounts(demo_engine)
    with Session(demo_engine) as session:
        goal = session.scalar(
            select(Account.id).where(
                Account.user_id == DEMO_USER_ID, Account.account_type == "GOAL"
            )
        )
        epoch = current_audit_epoch(session, DEMO_USER_ID)
        assert goal is not None and epoch is not None
        epoch_id = epoch.id
    base = prepare_action(
        demo_engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="missed-question-original-base",
            intent=TransferIntent(
                kind="transfer_internal",
                source_account_id=source,
                destination_account_id=target,
                amount_cents=100,
            ),
        ),
        SEED_AS_OF,
    )
    destination = FinitePlanningVariable(
        variable_id="destination",
        field="TRANSFER_DESTINATION",
        choices=[
            FiniteChoice(key="cash", value=AccountChoice(kind="account", account_id=target)),
            FiniteChoice(key="goal", value=AccountChoice(kind="account", account_id=goal)),
        ],
    )
    # Commit the original service without the API postcommit hook. This is the
    # durable gap that the explicitly launched worker must discover independently.
    started = start_question_session(
        demo_engine,
        DEMO_USER_ID,
        QuestionStartRequest(
            base_action_id=base.action_id,
            variables=[amount_variable(), destination],
            expected_epoch_id=epoch_id,
            idempotency_key="missed-question-original-start",
        ),
        SEED_AS_OF,
    )
    assert started.effective_state == "PENDING_ANSWER"
    with Session(demo_engine) as session:
        assert session.scalar(select(func.count()).select_from(InterventionOutbox)) == 0
        assert session.scalar(select(func.count()).select_from(InterventionInbox)) == 0
    original_receipt = started.model_dump(mode="json")
    financial = financial_snapshot(demo_engine)
    first = recover_current_question_notifications(demo_engine, DEMO_USER_ID, SEED_AS_OF)
    assert first.status == "COMPLETE_SCAN" and first.pending_original_sessions == 1
    assert len(first.items) == 1 and first.items[0].status == "OBSERVED"
    assert first.items[0].session_id == started.current_revision.session_id
    assert first.items[0].message_id is not None and first.items[0].idempotency_key is not None
    assert not first.authority_granted and not first.delivered and not first.acknowledged
    assert financial_snapshot(demo_engine) == financial
    assert started.model_dump(mode="json") == original_receipt
    retained = physical_snapshot(demo_engine)
    reopened = create_engine(demo_engine.url, pool_pre_ping=True)
    try:
        second = recover_current_question_notifications(reopened, DEMO_USER_ID, SEED_AS_OF)
        assert second.items == first.items
        assert physical_snapshot(reopened) == retained
        with Session(reopened) as session:
            assert session.scalar(select(func.count()).select_from(InterventionOutbox)) == 1
            assert session.scalar(select(func.count()).select_from(InterventionInbox)) == 0
        closed = close_question_session(
            reopened,
            DEMO_USER_ID,
            started.current_revision.session_id,
            QuestionCloseRequest(
                expected_revision=started.current_revision.revision,
                expected_epoch_id=epoch_id,
                idempotency_key="missed-question-original-close",
            ),
            SEED_AS_OF,
        )
        assert closed.effective_state == "CLOSED"
        after_close = physical_snapshot(reopened)
        final = recover_current_question_notifications(reopened, DEMO_USER_ID, SEED_AS_OF)
        assert final.status == "COMPLETE_SCAN" and final.pending_original_sessions == 0
        assert not final.items and physical_snapshot(reopened) == after_close
        assert financial_snapshot(reopened) == financial
    finally:
        reopened.dispose()
