"""Zhiyu's narrow facade must retain actual simulated economic effects and recovery."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from app.api.dependencies import SessionDependency, get_demo_user, get_now
from app.db.models import (
    Account,
    ActionReceipt,
    AuditEvent,
    BankOperation,
    ExternalBankFact,
    Goal,
    SimulatedBankPosting,
    Transaction,
    User,
)
from app.domain.demo_identity import DEMO_USER_ID
from app.services.audit_chain import current_audit_epoch, verify_audit_chain
from app.services.demo_seed import seed_demo
from app.services.income_ledger import read_income_state
from app.tests.test_demo_seed import demo_engine as demo_engine
from app.zhiyu_main import allowed, create_zhiyu_app
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy import text as sql_text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

EMERGENCY = "保留3000元应急金"
TRAVEL = "旅行目标1.2万元，截止2027-06-30，每月固定储备1000元。"
PREFIX = "/api/v1/zhiyu"


def _economics(engine: Engine) -> str:
    """Candidate/audit writes are allowed; actual accounts, bank money and receipts are not."""
    with engine.connect() as connection:
        result = {
            model.__tablename__: [
                dict(row)
                for row in connection.execute(select(model.__table__).order_by(model.id)).mappings()
            ]
            for model in (
                Account,
                Transaction,
                ExternalBankFact,
                BankOperation,
                SimulatedBankPosting,
                ActionReceipt,
            )
        }
    return json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)


def _epoch(engine: Engine) -> UUID:
    with Session(engine) as session:
        epoch = current_audit_epoch(session, DEMO_USER_ID)
        assert epoch is not None
        return epoch.id


def _cash_total(engine: Engine) -> int:
    with Session(engine) as session:
        return sum(
            account.balance_cents
            for account in session.scalars(select(Account))
            if account.account_type != "CREDIT_CARD"
        )


def _advance(clock: list[datetime]) -> None:
    clock[0] += timedelta(seconds=1)


def _rule(client: TestClient, clock: list[datetime], text: str) -> dict[str, str]:
    candidate = client.post("/api/v1/policies/compile", json={"text": text, "engine": "rules"})
    assert candidate.status_code == 200, candidate.text
    proposal = candidate.json()
    assert proposal["proposal_id"] is not None and proposal["configuration_hash"]
    _advance(clock)
    confirmation = client.post(
        f"/api/v1/policy-proposals/{proposal['proposal_id']}/confirm",
        json={"accepted": True, "reviewed_hash": proposal["configuration_hash"]},
    )
    assert confirmation.status_code == 200, confirmation.text
    _advance(clock)
    return confirmation.json()


def _goal_and_income(
    client: TestClient, engine: Engine, clock: list[datetime]
) -> tuple[str, dict[str, str]]:
    _rule(client, clock, EMERGENCY)
    policy = _rule(client, clock, TRAVEL)
    before = _economics(engine)
    created = client.post(
        PREFIX + "/goal",
        json={
            "policy_id": policy["policy_id"],
            "expected_version_id": policy["current_version_id"],
        },
    )
    assert created.status_code == 200, created.text
    goal_id = created.json()["goal"]["id"]
    with Session(engine) as session:
        goal = session.get(Goal, UUID(goal_id))
        assert goal is not None and goal.allocated_cents == 0
    # Creating a zero goal may add independent zero ownership openings, never cash movement.
    before_money = json.loads(before)
    after_money = json.loads(_economics(engine))
    for table in (
        "accounts",
        "transactions",
        "external_bank_facts",
        "bank_operations",
        "action_receipts",
    ):
        assert after_money[table] == before_money[table]
    _advance(clock)
    income = client.post(PREFIX + "/income", json={"expected_epoch_id": str(_epoch(engine))})
    assert income.status_code == 200, income.text
    assert income.json()["bank_status"] == "SETTLED"
    assert income.json()["projection_status"] == "PROJECTED"
    with Session(engine) as session:
        facts = list(session.scalars(select(ExternalBankFact)))
        assert len(facts) == 1 and facts[0].kind == "INCOME"
        assert facts[0].amount_cents == 200000
    after_income = _economics(engine)
    assert (
        client.post(PREFIX + "/income", json={"expected_epoch_id": str(_epoch(engine))}).status_code
        == 200
    )
    assert _economics(engine) == after_income
    _advance(clock)
    preview = client.get(f"/api/v1/goals/{goal_id}/allocation-preview")
    assert preview.status_code == 200, preview.text
    assert preview.json()["source_issues"] == []
    assert preview.json()["allocation"]["status"] == "READY"
    assert preview.json()["allocation"]["suggested_cents"] == 100000
    assert _economics(engine) == after_income
    return goal_id, policy


def _prepare(client: TestClient, engine: Engine, goal_id: str, scenario: str) -> dict[str, Any]:
    body = {"expected_epoch_id": str(_epoch(engine)), "goal_id": goal_id, "scenario": scenario}
    prepared = client.post(PREFIX + "/actions/prepare", json=body)
    assert prepared.status_code == 200, prepared.text
    action = prepared.json()
    assert action["effect"]["amount_cents"] == 100000
    assert action["effect"]["goal_id"] == goal_id
    assert client.post(PREFIX + "/actions/prepare", json=body).json() == action
    return action


def _confirm_action(client: TestClient, action: dict[str, Any]) -> None:
    if action["autonomy_level"] == "ASK_ONCE":
        response = client.post(
            f"/api/v1/actions/{action['action_id']}/confirm",
            json={"accepted": True, "effect_hash": action["effect_hash"]},
        )
        assert response.status_code == 200, response.text
    else:
        assert action["autonomy_level"] == "AUTO_EXECUTE"


@pytest.mark.integration
def test_actual_safe_refusal_and_response_loss_keep_one_original_economic_effect(
    demo_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two actual seed rounds; the second recovers its bank commit before resetting anything."""
    seed_demo(demo_engine)
    monkeypatch.setenv("ZHIYU_DEMO_DATABASE", str(demo_engine.url.database))
    clock = [datetime.now(UTC) + timedelta(seconds=1)]
    app = create_zhiyu_app(demo_engine)
    app.dependency_overrides[get_now] = lambda: clock[0]

    def checked_user(request: Request, session: SessionDependency) -> User:
        readonly = session.scalar(sql_text("SHOW transaction_read_only"))
        assert readonly == ("on" if request.method == "GET" else "off")
        return get_demo_user(session)

    app.dependency_overrides[get_demo_user] = checked_user
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()
        assert PREFIX + "/state" in schema["paths"]
        assert "/api/v1/boundary/full-action-set" not in schema["paths"]
        assert all(
            allowed(method.upper(), path)
            for path, methods in schema["paths"].items()
            for method in methods
        )
        for text in (EMERGENCY, TRAVEL, "保留3000元应急金，直接执行"):
            before = _economics(demo_engine)
            candidate = client.post("/api/v1/policies/compile", json={"text": text})
            assert candidate.status_code == 200, candidate.text
            assert _economics(demo_engine) == before
            if "直接执行" in text:
                assert candidate.json()["proposal_id"] is None
        goal_id, policy = _goal_and_income(client, demo_engine, clock)
        total_cash = _cash_total(demo_engine)
        action = _prepare(client, demo_engine, goal_id, "SAFE")
        _confirm_action(client, action)
        _advance(clock)
        executed = client.post(PREFIX + f"/actions/{action['action_id']}/execute", json={})
        assert executed.status_code == 200, executed.text
        settled = executed.json()
        assert settled["status"] == "SUCCEEDED" and settled["bank_status"] == "SETTLED"
        assert settled["receipt"]["executed_cents"] == 100000
        assert settled["effect_hash"] == action["effect_hash"]
        assert _cash_total(demo_engine) == total_cash
        final = _economics(demo_engine)
        for _ in range(2):
            replay = client.post(PREFIX + f"/actions/{action['action_id']}/execute", json={})
            assert replay.status_code == 200 and replay.json()["receipt"] == settled["receipt"]
        assert _economics(demo_engine) == final
        revoked = client.post(
            f"/api/v1/policies/{policy['policy_id']}/revoke",
            json={"expected_version_id": policy["current_version_id"]},
        )
        assert revoked.status_code == 200, revoked.text
        refused = client.post(
            PREFIX + "/actions/prepare",
            json={
                "expected_epoch_id": str(_epoch(demo_engine)),
                "goal_id": goal_id,
                "scenario": "REVOKED",
            },
        )
        assert refused.status_code == 409, refused.text
        assert _economics(demo_engine) == final
        with Session(demo_engine) as session:
            audit = verify_audit_chain(session, DEMO_USER_ID)
            assert audit.status == "VALID"
            originals = {row.id: row.canonical_text for row in session.scalars(select(AuditEvent))}
        first_epoch = _epoch(demo_engine)
        seed_demo(
            demo_engine,
            reset_key="zhiyu-second-round",
            reason="ZHIYU_INTEGRATION_SECOND_ROUND",
            principal="zhiyu-test",
            expected_epoch_id=first_epoch,
            check_expected_epoch=True,
        )
        assert _epoch(demo_engine) != first_epoch
        with Session(demo_engine) as session:
            assert verify_audit_chain(session, DEMO_USER_ID, first_epoch).status == "VALID"
            for identity, canonical in originals.items():
                retained = session.get(AuditEvent, identity)
                assert retained is not None and retained.canonical_text == canonical
        _advance(clock)
        goal_id, _ = _goal_and_income(client, demo_engine, clock)
        total_cash = _cash_total(demo_engine)
        action = _prepare(client, demo_engine, goal_id, "RESPONSE_LOSS")
        _confirm_action(client, action)
        _advance(clock)
        lost = client.post(PREFIX + f"/actions/{action['action_id']}/execute", json={})
        assert lost.status_code == 200, lost.text
        unknown = lost.json()
        assert unknown["action_id"] == action["action_id"] and unknown["status"] == "UNKNOWN"
        assert unknown["bank_status"] == "SETTLED" and unknown["receipt"] is None
        with Session(demo_engine) as session:
            rows = list(session.scalars(select(BankOperation)))
            assert len(rows) == 1 and str(rows[0].action_plan_id) == action["action_id"]
            goal = session.get(Goal, UUID(goal_id))
            assert goal is not None and goal.allocated_cents == 0
        before_read = _economics(demo_engine)
        conflicting = client.post(
            PREFIX + "/actions/prepare",
            json={
                "expected_epoch_id": str(_epoch(demo_engine)),
                "goal_id": goal_id,
                "scenario": "SAFE",
            },
        )
        assert conflicting.status_code == 409, conflicting.text
        assert _economics(demo_engine) == before_read
        state = client.get(PREFIX + "/state")
        assert state.status_code == 200, state.text
        assert str(action["action_id"]) in state.text and "UNKNOWN" in state.text
        assert any(
            item["scenario"] == "SAFE"
            and item["goal_id"] == goal_id
            and item["status"] == "REJECTED"
            and "ORIGINAL_ACTION_UNRESOLVED" in item["decision"]
            for item in state.json()["activity"]
        )
        original = client.get(f"/api/v1/actions/{action['action_id']}")
        assert original.status_code == 200 and original.json()["status"] == "UNKNOWN"
        assert _economics(demo_engine) == before_read
        recovered = client.post(PREFIX + f"/actions/{action['action_id']}/execute", json={})
        assert recovered.status_code == 200, recovered.text
        actual = recovered.json()
        assert actual["action_id"] == action["action_id"] and actual["status"] == "SUCCEEDED"
        assert actual["receipt"]["executed_cents"] == 100000
        assert actual["effect_hash"] == unknown["effect_hash"]
        assert _cash_total(demo_engine) == total_cash
        after_recovery = _economics(demo_engine)
        assert (
            client.post(PREFIX + f"/actions/{action['action_id']}/execute", json={}).json()[
                "receipt"
            ]
            == actual["receipt"]
        )
        with Session(demo_engine) as session:
            assert len(list(session.scalars(select(BankOperation)))) == 1
            goal = session.get(Goal, UUID(goal_id))
            assert goal is not None and goal.allocated_cents == 100000
            income = read_income_state(session, DEMO_USER_ID, clock[0]).ledger
            origin = UUID(actual["effect"]["income_uses"][0]["origin_transaction_id"])
            fragments = [row for row in income.fragments if row.origin_transaction_id == origin]
            assert sum(row.assigned_cents for row in fragments) == 100000
            assert sum(row.available_cents for row in fragments) == 100000
            assert verify_audit_chain(session, DEMO_USER_ID).status == "VALID"
        refresh = client.get(PREFIX + "/state")
        assert refresh.status_code == 200 and str(actual["action_id"]) in refresh.text
        assert str(actual["receipt"]["receipt_id"]) in refresh.text
        assert _economics(demo_engine) == after_recovery
        for url, body in (
            (
                PREFIX + "/income",
                {"expected_epoch_id": str(_epoch(demo_engine)), "amount_cents": 999999},
            ),
            (
                PREFIX + "/actions/prepare",
                {
                    "expected_epoch_id": str(_epoch(demo_engine)),
                    "goal_id": goal_id,
                    "scenario": "SAFE",
                    "now": "2099-01-01",
                },
            ),
            (
                PREFIX + f"/actions/{action['action_id']}/execute",
                {"receipt": {"status": "SUCCEEDED"}},
            ),
            (PREFIX + f"/actions/{action['action_id']}/execute", {"bank_status": "SETTLED"}),
        ):
            assert client.post(url, json=body).status_code == 422
        assert _economics(demo_engine) == after_recovery
        for url in (
            "/api/v1/planning/annual",
            "/api/v1/boundary/full-action-set",
            "/api/v1/demo/reset",
        ):
            assert client.get(url).status_code in {403, 404}


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql+psycopg://127.0.0.1:54329/bounded_funds", "bounded_funds"),
        ("postgresql+psycopg://db:54329/bf_test_" + "a" * 32, "bf_test_" + "a" * 32),
        ("postgresql+psycopg://127.0.0.1:5432/bf_test_" + "a" * 32, "bf_test_" + "a" * 32),
        ("postgresql+psycopg://127.0.0.1:54329/bf_test_" + "a" * 32, "bf_test_" + "b" * 32),
    ],
)
def test_formal_remote_wrong_port_and_name_mismatch_refused_before_connect(
    url: str, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_engine(url)
    monkeypatch.setenv("ZHIYU_DEMO_DATABASE", expected)
    try:
        with pytest.raises(ValueError):
            create_zhiyu_app(engine)
    finally:
        engine.dispose()


def test_missing_isolation_marker_refused_before_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine("postgresql+psycopg://127.0.0.1:54329/bf_test_" + "a" * 32)
    monkeypatch.delenv("ZHIYU_DEMO_DATABASE", raising=False)
    try:
        with pytest.raises(ValueError):
            create_zhiyu_app(engine)
    finally:
        engine.dispose()
