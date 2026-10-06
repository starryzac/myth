"""Independent HTTP/PG probes of economic identity, consent and duplicate obligations."""

from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.api.dependencies import get_demo_user, get_now
from app.db.models import Account, BankOperation, EvidenceItem, User
from app.domain.policy_configuration import configuration_hash
from app.services.boundary import compute_user_boundary
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF
from app.tests.test_boundary_service import confirmed_policy, imported_proof
from app.tests.test_execution_service import transfer_accounts
from app.tests.test_goal_api import NOW, all_tables
from app.tests.test_goal_api import goal_client as goal_client
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def prepared_transfer(client: TestClient, engine: Engine) -> dict[str, Any]:
    source, target = transfer_accounts(engine)
    response = client.post(
        "/api/v1/actions/prepare",
        json={
            "idempotency_key": "independent-transfer",
            "intent": {
                "kind": "transfer_internal",
                "source_account_id": str(source),
                "destination_account_id": str(target),
                "amount_cents": 12345,
            },
        },
    )
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def test_duplicate_range_actions_cannot_pay_the_same_final_amount_twice(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    with Session(engine) as session, session.begin():
        policy_id, _ = confirmed_policy(
            session,
            {
                "type": "recurring_obligation",
                "payee_id": "synthetic-utilities-001",
                "due_day": 4,
                "auto_execute": True,
                "amount_rule": {"kind": "range", "min_cents": 5000, "max_cents": 10000},
            },
        )
        imported_proof(
            session,
            "SIMULATED_RECURRING_SETTLEMENT",
            {
                "protocol": "recurring-settlement-v1",
                "policy_id": str(policy_id),
                "period": "2026-10",
                "paid_cents": 1000,
                "final_total_cents": 8000,
                "payee_id": "synthetic-utilities-001",
                "complete": True,
                "as_of": SEED_AS_OF.isoformat(),
            },
        )
    with Session(engine) as session:
        before = compute_user_boundary(session, DEMO_USER_ID, NOW).boundary
        cash = session.scalars(select(Account).where(Account.account_type == "CASH")).one()
        cash_id, balance = cash.id, cash.balance_cents
    actions = []
    for key in ["range-a", "range-b"]:
        response = client.post(
            "/api/v1/actions/prepare",
            json={
                "idempotency_key": key,
                "intent": {
                    "kind": "pay_recurring",
                    "policy_id": str(policy_id),
                    "period": "2026-10",
                },
            },
        )
        assert response.status_code == 200, response.text
        action = response.json()
        assert action["autonomy_level"] == "ASK_ONCE"
        assert action["effect"]["amount_cents"] == 7000
        assert action["prepared_validation"]["reasons"] == [
            "EXPLICIT_PERIOD_AMOUNT_CONFIRMATION_REQUIRED"
        ]
        confirmation = client.post(
            f"/api/v1/actions/{action['action_id']}/confirm",
            json={
                "effect_hash": action["effect_hash"],
                "accepted": True,
            },
        )
        assert confirmation.status_code == 200, confirmation.text
        actions.append(action)
    first = client.post(f"/api/v1/actions/{actions[0]['action_id']}/execute", json={})
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "SUCCEEDED"
    after_first = all_tables(engine)
    second = client.post(f"/api/v1/actions/{actions[1]['action_id']}/execute", json={})
    assert second.status_code == 409, second.text
    assert all_tables(engine) == after_first
    with Session(engine) as session:
        cash_after = session.get(Account, cash_id)
        assert cash_after is not None and cash_after.balance_cents == balance - 7000
        boundary = compute_user_boundary(session, DEMO_USER_ID, NOW).boundary
        assert boundary.safe_idle_cents == before.safe_idle_cents
        assert len(list(session.scalars(select(BankOperation)))) == 1
        statement = session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.source_type == "SIMULATED_RECURRING_SETTLEMENT",
                EvidenceItem.status == "VALID",
                EvidenceItem.content["policy_id"].as_string() == str(policy_id),
            )
        ).one()
        assert statement.content["paid_cents"] == statement.content["final_total_cents"] == 8000


def test_other_user_cannot_read_confirm_execute_or_reprepare_existing_action(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    action = prepared_transfer(client, engine)
    with Session(engine) as session, session.begin():
        foreign = User(
            id=uuid4(), external_ref="audit-foreign", display_name="另一模拟用户", is_simulated=True
        )
        session.add(foreign)
        session.flush()
        session.expunge(foreign)
    api = client.app
    assert isinstance(api, FastAPI)
    api.dependency_overrides[get_demo_user] = lambda: foreign
    before = all_tables(engine)
    for suffix in ["", "/receipt"]:
        assert client.get(f"/api/v1/actions/{action['action_id']}{suffix}").status_code == 404
    assert (
        client.post(
            f"/api/v1/actions/{action['action_id']}/confirm",
            json={
                "effect_hash": action["effect_hash"],
                "accepted": True,
            },
        ).status_code
        == 404
    )
    assert client.post(f"/api/v1/actions/{action['action_id']}/execute", json={}).status_code == 404
    foreign_prepare = client.post(
        "/api/v1/actions/prepare",
        json={
            "idempotency_key": "foreign-reprepare",
            "intent": {
                "kind": "transfer_internal",
                "source_account_id": action["effect"]["cash_uses"][0]["account_id"],
                "destination_account_id": action["effect"]["destination_account_id"],
                "amount_cents": action["effect"]["amount_cents"],
            },
        },
    )
    assert foreign_prepare.status_code == 409, foreign_prepare.text
    assert all_tables(engine) == before


@pytest.mark.parametrize("fault", ["expired", "rehash_false_consent"])
def test_expired_or_rehashed_false_consent_never_creates_a_bank_operation(
    goal_client: tuple[TestClient, Engine],
    fault: str,
) -> None:
    client, engine = goal_client
    action = prepared_transfer(client, engine)
    response = client.post(
        f"/api/v1/actions/{action['action_id']}/confirm",
        json={
            "effect_hash": action["effect_hash"],
            "accepted": True,
        },
    )
    assert response.status_code == 200, response.text
    if fault == "expired":
        api = client.app
        assert isinstance(api, FastAPI)
        api.dependency_overrides[get_now] = lambda: NOW + timedelta(minutes=15)
    else:
        with Session(engine) as session, session.begin():
            proof = session.scalars(
                select(EvidenceItem).where(
                    EvidenceItem.source_type == "USER_ACTION_CONFIRMATION",
                    EvidenceItem.source_ref == action["action_id"],
                )
            ).one()
            proof.content = {**proof.content, "accepted": False}
            proof.content_hash = configuration_hash(proof.content)
    before = all_tables(engine)
    response = client.post(f"/api/v1/actions/{action['action_id']}/execute", json={})
    assert response.status_code == 409, response.text
    assert all_tables(engine) == before
    with Session(engine) as session:
        assert not list(session.scalars(select(BankOperation)))


def test_prepare_intents_cannot_supply_derived_amount_authority_or_server_clock(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    before = all_tables(engine)
    identity = str(UUID(int=123))
    intents = [
        {"kind": "allocate_goal", "goal_id": identity},
        {"kind": "purchase_asset", "policy_id": identity},
        {"kind": "redeem_asset", "position_id": identity},
        {"kind": "pay_recurring", "policy_id": identity, "period": "2026-10"},
    ]
    for intent in intents:
        for field, value in [
            ("amount_cents", 1),
            ("now", "2020-01-01T00:00:00Z"),
            ("policy_version_ids", []),
            ("autonomy_level", "AUTO_EXECUTE"),
        ]:
            response = client.post(
                "/api/v1/actions/prepare",
                json={
                    "idempotency_key": "cannot-override",
                    "intent": {**intent, field: value},
                },
            )
            assert response.status_code == 422, response.text
    assert all_tables(engine) == before
