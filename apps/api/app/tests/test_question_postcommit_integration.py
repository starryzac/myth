"""Actual registered HTTP postcommit observation; receipt replay and GET are zero-write."""

from typing import Any

import pytest
from app.api.dependencies import get_engine, get_now
from app.db.full_models import InterventionInbox, InterventionOutbox
from app.db.models import Account
from app.domain.finite_uncertainty import AccountChoice, FiniteChoice, FinitePlanningVariable
from app.main import create_app
from app.services.action_contracts import PrepareActionRequest, TransferIntent
from app.services.audit_chain import current_audit_epoch
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, seed_demo
from app.services.execution import prepare_action
from app.tests.test_demo_seed import demo_engine as demo_engine
from app.tests.test_execution_service import transfer_accounts
from app.tests.test_finite_uncertainty import amount_variable
from app.tests.test_full_intervention_api import financial_snapshot
from app.tests.test_full_projection_api import physical_snapshot
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_actual_question_http_commit_produces_original_observation_without_delivery(
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
            idempotency_key="postcommit-http-original-base",
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
    body: dict[str, Any] = {
        "base_action_id": str(base.action_id),
        "variables": [
            amount_variable().model_dump(mode="json"),
            destination.model_dump(mode="json"),
        ],
        "expected_epoch_id": str(epoch_id),
        "idempotency_key": "postcommit-http-original-start",
    }
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: demo_engine
    app.dependency_overrides[get_now] = lambda: SEED_AS_OF
    financial = financial_snapshot(demo_engine)
    with TestClient(app) as client:
        started = client.post("/api/v1/finite-planning/sessions", json=body)
        assert started.status_code == 200, started.text
        assert started.headers["X-Question-Intervention-Status"] == "OBSERVED"
        original_receipt = started.json()
        assert original_receipt["pending_question"] is not None
        with Session(demo_engine) as session:
            assert session.scalar(select(func.count()).select_from(InterventionOutbox)) == 1
            assert session.scalar(select(func.count()).select_from(InterventionInbox)) == 0
            message_id = session.scalar(select(InterventionOutbox.id))
        assert message_id is not None
        observed = client.get(f"/api/v1/interventions/{message_id}")
        assert observed.status_code == 200, observed.text
        assert observed.json()["pending"] is True
        assert observed.json()["authority_granted"] is False
        retained = physical_snapshot(demo_engine)
        replayed = client.post("/api/v1/finite-planning/sessions", json=body)
        assert replayed.status_code == 200 and replayed.json() == original_receipt
        assert physical_snapshot(demo_engine) == retained
        session_id = original_receipt["current_revision"]["session_id"]
        read = client.get(f"/api/v1/finite-planning/sessions/{session_id}")
        assert (
            read.status_code == 200
            and read.json()["current_revision"] == original_receipt["current_revision"]
        )
        assert "X-Question-Intervention-Status" not in read.headers
        assert physical_snapshot(demo_engine) == retained
    assert financial_snapshot(demo_engine) == financial
