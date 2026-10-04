"""Goal creation binds a confirmed policy without moving or claiming existing cash."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.api.dependencies import get_engine, get_now
from app.db.base import Base
from app.db.models import Account, EvidenceItem, Goal, Transaction, User
from app.db.session import create_database_engine
from app.db.testing import temporary_database
from app.domain.demo_identity import DEMO_USER_ID
from app.domain.policy_configuration import configuration_hash
from app.main import create_app
from app.services.demo_seed import seed_demo
from app.services.goal_allocation import LEDGER_SOURCE
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 4, 1, tzinfo=UTC)
TEXT = "买车目标三万元，截止2027年10月1日，每月至少一千八百、建议两千、最多两千五百元。"


@pytest.fixture
def goal_client() -> Iterator[tuple[TestClient, Engine]]:
    with temporary_database() as url:
        config = Config(str(Path(__file__).resolve().parents[4] / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        command.upgrade(config, "head")
        engine = create_database_engine(url)
        seed_demo(engine)
        api = create_app()
        api.dependency_overrides[get_engine] = lambda: engine
        api.dependency_overrides[get_now] = lambda: NOW
        try:
            with TestClient(api) as client:
                yield client, engine
        finally:
            engine.dispose()


def all_tables(engine: Engine) -> str:
    with engine.connect() as connection:
        values = {
            table.name: [
                dict(row)
                for row in connection.execute(select(table).order_by(table.c.id)).mappings()
            ]
            for table in Base.metadata.sorted_tables
        }
    return json.dumps(values, default=str, sort_keys=True)


def confirmed_goal_request(client: TestClient, engine: Engine) -> dict[str, str]:
    proposal = client.post("/api/v1/policies/compile", json={"text": TEXT}).json()
    response = client.post(
        f"/api/v1/policy-proposals/{proposal['proposal_id']}/confirm",
        json={"accepted": True, "reviewed_hash": proposal["configuration_hash"]},
    )
    assert response.status_code == 200
    lifecycle = response.json()
    with Session(engine) as session:
        account = session.scalar(select(Account).where(Account.account_type == "GOAL"))
        assert account is not None
        return {
            "policy_id": lifecycle["policy_id"],
            "expected_version_id": lifecycle["current_version_id"],
            "account_id": str(account.id),
        }


def test_goal_creation_initializes_zero_ownership_and_preserves_all_existing_money(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    assert client.get("/api/v1/goals").status_code == 200
    assert client.get("/api/v1/goals").json()["items"] == []
    body = confirmed_goal_request(client, engine)
    money_before = client.get("/api/v1/accounts/summary").json()
    positions_before = client.get("/api/v1/positions").json()
    assert client.get("/api/v1/boundary").json()["boundary"]["status"] == "INSUFFICIENT_EVIDENCE"
    response = client.post("/api/v1/goals", json=body)
    assert response.status_code == 200
    goal = response.json()["goal"]
    assert goal["policy_id"] == body["policy_id"]
    assert goal["account_id"] == body["account_id"]
    assert goal["allocated_cents"] == 0
    assert goal["monthly_target_cents"] == 200000
    assert client.get("/api/v1/accounts/summary").json() == money_before
    assert client.get("/api/v1/positions").json() == positions_before
    boundary = client.get("/api/v1/boundary").json()
    assert boundary["source_issues"] == []
    assert boundary["boundary"]["status"] == "READY"
    assert boundary["boundary"]["protected_cents_by_reason"]["goal_cash"] == 160000
    assert client.get("/api/v1/goals").json()["items"] == [goal]
    unchanged = all_tables(engine)
    assert client.post("/api/v1/goals", json=body).json() == response.json()
    assert all_tables(engine) == unchanged


def test_creation_retry_does_not_reset_owned_funds_or_rebind_account(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    body = confirmed_goal_request(client, engine)
    created = client.post("/api/v1/goals", json=body)
    assert created.status_code == 200
    with Session(engine) as session, session.begin():
        goal = session.get(Goal, UUID(created.json()["goal"]["id"]))
        assert goal is not None
        # A later imported fact: retrying creation must not rewrite this state.
        goal.allocated_cents = 12345
        alternative = session.scalar(select(Account).where(Account.account_type == "CASH"))
        assert alternative is not None
        other_account_id = str(alternative.id)
    before = all_tables(engine)
    repeated = client.post("/api/v1/goals", json=body)
    assert repeated.status_code == 200
    assert repeated.json()["goal"]["allocated_cents"] == 12345
    assert (
        client.post("/api/v1/goals", json={**body, "account_id": other_account_id}).status_code
        == 409
    )
    assert all_tables(engine) == before


def test_goal_creation_rejects_stale_inactive_foreign_and_client_financial_fields(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    body = confirmed_goal_request(client, engine)
    with Session(engine) as session, session.begin():
        card = session.scalar(select(Account).where(Account.account_type == "CREDIT_CARD"))
        assert card is not None
        card_id = str(card.id)
        foreign_user = User(id=uuid4(), external_ref=str(uuid4()), display_name="另一模拟用户")
        session.add(foreign_user)
        session.flush()
        foreign_account = Account(
            id=uuid4(),
            user_id=foreign_user.id,
            external_ref=str(uuid4()),
            name="其他用户账户",
            account_type="CASH",
            balance_cents=100000,
            observed_at=NOW,
        )
        session.add(foreign_account)
        session.flush()
        foreign_account_id = str(foreign_account.id)
    before = all_tables(engine)
    for modified, expected in (
        ({**body, "expected_version_id": str(uuid4())}, 409),
        ({**body, "policy_id": str(uuid4())}, 404),
        ({**body, "account_id": str(uuid4())}, 404),
        ({**body, "account_id": foreign_account_id}, 404),
        ({**body, "account_id": card_id}, 422),
        ({**body, "allocated_cents": 1}, 422),
        ({**body, "as_of": "2000-01-01"}, 422),
    ):
        assert client.post("/api/v1/goals", json=modified).status_code == expected
    assert all_tables(engine) == before
    stopped = client.post(
        f"/api/v1/policies/{body['policy_id']}/suspend",
        json={"expected_version_id": body["expected_version_id"]},
    )
    assert stopped.status_code == 200
    before = all_tables(engine)
    assert client.post("/api/v1/goals", json=body).status_code == 409
    assert all_tables(engine) == before


def test_allocation_preview_requires_complete_sources_and_is_read_only(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    body = confirmed_goal_request(client, engine)
    created = client.post("/api/v1/goals", json=body)
    assert created.status_code == 200
    goal_id = created.json()["goal"]["id"]
    path = f"/api/v1/goals/{goal_id}/allocation-preview"
    before = all_tables(engine)
    response = client.get(path)
    assert response.status_code == 200
    payload: dict[str, Any] = response.json()
    assert payload["simulation"] is True
    assert payload["allocation"]["status"] == "INSUFFICIENT_EVIDENCE"
    assert payload["allocation"]["preview_only"] is True
    assert payload["allocation"]["lot_allocations"] == []
    assert client.get(path, params={"available_cents": 99999999}).status_code == 422
    assert client.get(path).json() == payload
    assert all_tables(engine) == before


def test_http_preview_allocates_only_verified_available_income_and_preserves_reservations(
    goal_client: tuple[TestClient, Engine],
) -> None:
    from uuid import uuid5

    from app.services.execution_bank import open_execution_anchors
    from app.services.execution_exposure import refresh_execution_exposure
    from app.services.income_ledger import read_income_ledger
    from app.services.simulated_bank import open_simulated_bank

    client, engine = goal_client
    body = confirmed_goal_request(client, engine)
    created = client.post("/api/v1/goals", json=body)
    assert created.status_code == 200
    goal_id = created.json()["goal"]["id"]
    with Session(engine) as session, session.begin():
        # Explicit synthetic import: a new account with its own independent opening.
        # Existing seed bank balances are never rewritten to fit application facts.
        account = Account(
            id=uuid4(),
            user_id=DEMO_USER_ID,
            created_at=NOW,
            external_ref="203-http-income-import",
            name="模拟新增收入账户",
            account_type="CASH",
            bank_code="ICBC",
            currency="CNY",
            balance_cents=400000,
            observed_at=NOW,
        )
        session.add(account)
        session.flush()
        source_account_id = str(account.id)
        starting = 0
        income_id = uuid4()
        for identifier, direction, amount, balance, role in (
            (income_id, "CREDIT", 500000, starting + 500000, "INCOME"),
            (uuid4(), "DEBIT", 100000, starting + 400000, "CONSUMPTION"),
        ):
            content: dict[str, Any] = {
                "simulation": True,
                "transaction_id": str(identifier),
                "account_id": str(account.id),
                "direction": direction,
                "amount_cents": amount,
                "balance_after_cents": balance,
                "occurred_at": NOW.isoformat(),
                "counterparty_ref": "synthetic-http-fixture",
                "economic_role": role,
            }
            proof = EvidenceItem(
                id=uuid4(),
                user_id=DEMO_USER_ID,
                evidence_level="BANK_CONFIRMED",
                source_type="SIMULATED_BANK_TRANSACTION",
                source_ref=str(identifier),
                content=content,
                content_hash=configuration_hash(content),
                valid_from=NOW,
                observed_at=NOW,
                status="VALID",
            )
            session.add(proof)
            session.flush()
            session.add(
                Transaction(
                    id=identifier,
                    user_id=DEMO_USER_ID,
                    account_id=account.id,
                    evidence_id=proof.id,
                    source_ref=str(identifier),
                    direction=direction,
                    amount_cents=amount,
                    balance_after_cents=balance,
                    category="fixture",
                    counterparty_ref="synthetic-http-fixture",
                    occurred_at=NOW,
                    observed_at=NOW,
                )
            )
        balance_content = {
            "simulation": True,
            "user_id": str(DEMO_USER_ID),
            "account_id": str(account.id),
            "account_type": "CASH",
            "currency": "CNY",
            "balance_cents": account.balance_cents,
            "as_of": NOW.isoformat(),
        }
        session.add(
            EvidenceItem(
                id=uuid4(),
                user_id=DEMO_USER_ID,
                created_at=NOW,
                evidence_level="BANK_CONFIRMED",
                source_type="SIMULATED_BANK_BALANCE",
                source_ref="203-http-income-import",
                content=balance_content,
                content_hash=configuration_hash(balance_content),
                valid_from=NOW,
                observed_at=NOW,
                status="VALID",
            )
        )
        open_simulated_bank(
            session,
            DEMO_USER_ID,
            NOW,
            cash_balances={account.id: 400000},
            position_principals={},
        )
        session.flush()
        cash_accounts = list(session.scalars(select(Account).where(Account.account_type == "CASH")))
        evidence = {item.id: item for item in session.scalars(select(EvidenceItem))}
        origins = [
            row
            for row in session.scalars(select(Transaction).where(Transaction.direction == "CREDIT"))
            if row.account_id in {item.id for item in cash_accounts}
            and row.evidence_id is not None
            and evidence[row.evidence_id].content.get("economic_role") == "INCOME"
        ]
        lots = []
        for origin in origins:
            assert origin.evidence_id is not None
            lots.append(
                {
                    "transaction_id": str(origin.id),
                    "account_id": str(origin.account_id),
                    "bank_evidence_id": str(origin.evidence_id),
                    "bank_evidence_hash": evidence[origin.evidence_id].content_hash,
                    "original_cents": origin.amount_cents,
                    "prior_unspent_cents": 400000 if origin.id == income_id else 0,
                    "spent_cents": 100000 if origin.id == income_id else origin.amount_cents,
                    "assigned_cents": 0,
                    "reserved_cents": 100000 if origin.id == income_id else 0,
                    "available_cents": 300000 if origin.id == income_id else 0,
                }
            )
        ledger = {
            "simulation": True,
            "protocol": "new-funds-ledger-v1",
            "user_id": str(DEMO_USER_ID),
            "complete": True,
            "as_of": NOW.isoformat(),
            "scope_account_ids": sorted(str(item.id) for item in cash_accounts),
            "lots": lots,
        }
        session.add(
            EvidenceItem(
                id=uuid4(),
                user_id=DEMO_USER_ID,
                evidence_level="BANK_CONFIRMED",
                source_type=LEDGER_SOURCE,
                source_ref="http-fixture-ledger",
                content=ledger,
                content_hash=configuration_hash(ledger),
                valid_from=NOW,
                observed_at=NOW,
                status="VALID",
            )
        )
        session.flush()
        imported_ledger = read_income_ledger(session, DEMO_USER_ID, NOW)
        open_execution_anchors(session, DEMO_USER_ID, NOW, income_ledger=imported_ledger)
        refresh_execution_exposure(session, DEMO_USER_ID, NOW, income_id)
    before = all_tables(engine)
    path = f"/api/v1/goals/{goal_id}/allocation-preview"
    response = client.get(path)
    assert response.status_code == 200
    result = response.json()
    assert result["source_issues"] == []
    allocation = result["allocation"]
    assert allocation["status"] == "READY"
    assert allocation["suggested_cents"] == 200000
    assert allocation["eligible_new_funds_cents"] == 300000
    assert allocation["lot_allocations"] == [
        {
            "origin_transaction_id": str(income_id),
            "account_id": source_account_id,
            "fragment_id": str(uuid5(income_id, "income-location:" + source_account_id)),
            "amount_cents": 200000,
            "remaining_available_cents": 100000,
        }
    ]
    assert allocation["candidate_boundary"]["status"] == "READY"
    assert allocation["candidate_boundary"]["safe_idle_cents"] == (
        allocation["baseline_boundary"]["safe_idle_cents"] - 20000
    )
    assert client.get(path).json() == result
    assert all_tables(engine) == before
