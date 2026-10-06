"""Two actual PG risk candidates; root executes them after mandatory shared wiring.

Fresh isolated default seed, actual signed USER token and actual empty simulator
payment-history observation. No fabricated payee, bill, receipt or successful leg.
"""

from datetime import timedelta
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

import pytest
from app.db.models import Account, BankOperation, EvidenceItem, SimulatedBankPosting
from app.domain.policy_configuration import configuration_hash
from app.services.demo_seed import DEMO_USER_ID
from app.services.full_policy_lifecycle import canonical_candidate
from app.services.scenario_runner import ScenarioRunner
from app.tests.test_boundary_service import confirmed_policy
from app.tests.test_full_projection import NOW
from app.tests.test_full_projection_api import annual_client as annual_client
from app.tests.test_full_projection_api import physical_snapshot
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("auto_execute", [True, False])
def test_actual_signed_user_relation_uses_real_monthly_bank_payment_and_original_key_recovery(
    annual_client: tuple[TestClient, Engine],
    monkeypatch: pytest.MonkeyPatch,
    auto_execute: bool,
) -> None:
    client, engine = annual_client
    secret = "SYNTHETIC_LOCAL_SESSION_RISK_ONLY_606"
    monkeypatch.setenv("BF_LOCAL_USER_SECRET", secret)
    monkeypatch.setenv("BF_LOCAL_SIGNING_KEY", "cd" * 32)
    local = NOW.astimezone(ZoneInfo("Asia/Shanghai"))
    common = {
        "payee_id": "synthetic-landlord-001",
        "amount_rule": {"kind": "exact", "amount_cents": 100},
        "due_day": local.day,
        "auto_execute": auto_execute,
        "prepare_days_before": 0,
    }
    with Session(engine) as session, session.begin():
        account = session.scalar(
            select(Account)
            .where(Account.user_id == DEMO_USER_ID, Account.account_type == "CASH")
            .order_by(Account.id)
        )
        assert account is not None
        source_id = account.id
        original_id, original_version = confirmed_policy(
            session, common | {"type": "recurring_obligation"}, NOW
        )
    # Trusted isolated observer reads actual absence for this newly declared policy;
    # this is an explicit simulation fact seam, not a guessed zero liability.
    runner = ScenarioRunner(engine, DEMO_USER_ID)
    period = local.strftime("%Y-%m")
    observation = runner.observe_recurring_payment(original_id, period, NOW)
    assert observation["observation"]["paid_cents"] == 0
    assert observation["observation"]["observation_protocol"] == "scenario-empty-bank-payment-v1"
    config = common | {
        "type": "periodic_transfer",
        "name": "606真实隔离固定关系",
        "source_account_id": str(source_id),
        "single_action_cap_cents": 100,
        "valid_until": (local.date() + timedelta(days=60)).isoformat(),
    }
    created = client.post(
        "/api/v1/full-policies/confirm",
        json={
            "template_name": "PeriodicTransferPolicy",
            "configuration": config,
            "reviewed_hash": configuration_hash(
                canonical_candidate("PeriodicTransferPolicy", config)
            ),
            "accepted": True,
            "reason": "明确模拟周期与原MVP范围相同",
            "idempotency_key": "606-full-model",
        },
    )
    assert created.status_code == 200, created.text
    full = created.json()
    assert full["bank_authority"] is False
    full_id, full_version = (
        full["policy_id"],
        full["version_id"],
    )
    epoch = full["epoch_id"]
    body = {
        "expected_epoch_id": epoch,
        "full_policy_id": full_id,
        "expected_full_version_id": full_version,
        "original_policy_id": str(original_id),
        "expected_original_version_id": str(original_version),
        "idempotency_key": "606-user-start",
    }
    assert client.post("/api/v1/full-payment-relations/start", json=body).status_code == 401
    logged = client.post(
        "/api/v1/local-actor/login", json={"username": "bounded-user", "secret": secret}
    )
    assert logged.status_code == 200, logged.text
    assert logged.json()["principal"]["human_identity_verified"] is False
    started = client.post("/api/v1/full-payment-relations/start", json=body)
    assert started.status_code == 200, started.text
    start = started.json()["original"]
    confirmed = client.post(
        f"/api/v1/full-payment-relations/starts/{start['command_id']}/confirm",
        json={
            "expected_epoch_id": epoch,
            "reviewed_scope_hash": start["scope_hash"],
            "accepted": True,
            "reason": "服务端已验证的模拟USER明确复核原身份范围",
            "idempotency_key": "606-user-confirm",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    authorization = confirmed.json()["original"]["command_id"]
    before_reads = physical_snapshot(engine)
    lookup = client.get(f"/api/v1/full-payment-relations/commands/{epoch}/by-key/606-user-confirm")
    assert (
        lookup.status_code == 200
        and lookup.json()["original"]["original"] == confirmed.json()["original"]
    ), lookup.text
    assert lookup.json()["original"]["current_scope_status"] == "CURRENT"
    assert physical_snapshot(engine) == before_reads
    prepare_body = {
        "expected_epoch_id": epoch,
        "period": period,
        "idempotency_key": "606-original-payment",
    }
    prepared = client.post(
        f"/api/v1/full-payment-relations/authorizations/{authorization}/prepare", json=prepare_body
    )
    assert prepared.status_code == 200, prepared.text
    action = prepared.json()
    assert (
        action["effect"]["amount_cents"] == 100
        and action["effect"]["payee_id"] == common["payee_id"]
    )
    assert action["effect"]["policy_version_id"] == str(original_version)
    assert action["effect"]["cash_uses"] == [{"account_id": str(source_id), "amount_cents": 100}]
    if not auto_execute:
        # Old DemoUser-only confirmation cannot satisfy the new signed USER proof.
        old_consent = client.post(
            f"/api/v1/actions/{action['action_id']}/confirm",
            json={"effect_hash": action["effect_hash"], "accepted": True},
        )
        assert old_consent.status_code == 200, old_consent.text
        blocked = client.post(
            f"/api/v1/full-payment-relations/actions/{action['action_id']}/execute",
            json={"expected_epoch_id": epoch},
        )
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["error"]["code"] == "PAYMENT_USER_ACTION_CONSENT_REQUIRED"
        user_consent = client.post(
            f"/api/v1/full-payment-relations/actions/{action['action_id']}/confirm",
            json={
                "expected_epoch_id": epoch,
                "reviewed_effect_hash": action["effect_hash"],
                "accepted": True,
            },
        )
        assert user_consent.status_code == 200, user_consent.text
        consent_lookup = client.get(
            f"/api/v1/full-payment-relations/actions/{action['action_id']}/user-consent"
        )
        assert consent_lookup.status_code == 200 and consent_lookup.json()["status"] == "RECORDED"
        assert consent_lookup.json()["original"]["principal_at_confirmation"]["role"] == "USER"
    else:
        assert action["autonomy_level"] == "AUTO_EXECUTE"
    executed = client.post(
        f"/api/v1/full-payment-relations/actions/{action['action_id']}/execute",
        json={"expected_epoch_id": epoch},
    )
    assert executed.status_code == 200, executed.text
    result = executed.json()
    assert result["status"] == "SUCCEEDED" and result["bank_status"] == "SETTLED"
    assert result["receipt"]["executed_cents"] == 100
    assert action["effect"]["income_uses"] == []
    with Session(engine) as session:
        postings = list(
            session.scalars(
                select(SimulatedBankPosting).where(
                    SimulatedBankPosting.operation_id == UUID(action["action_id"])
                )
            )
        )
        liability_id = uuid5(DEMO_USER_ID, "liability:" + action["effect"]["business_key"])
        payee_ref = common["payee_id"]
        assert isinstance(payee_ref, str)
        payee_id = uuid5(DEMO_USER_ID, payee_ref)
        assert len(postings) == len(result["receipt"]["posting_ids"]) == 4
        assert {str(row.id) for row in postings} == set(result["receipt"]["posting_ids"])
        assert {(row.ledger_dimension, row.ledger_key, row.delta_cents) for row in postings} == {
            ("ECONOMIC", f"CASH:{source_id}", -100),
            ("ECONOMIC", f"PAYEE:{payee_id}", 100),
            ("LIABILITY", f"LIABILITY_DUE:{liability_id}", -100),
            ("LIABILITY", f"LIABILITY_PAID:{liability_id}", 100),
        }
        for dimension in ("ECONOMIC", "LIABILITY"):
            assert (
                sum(row.delta_cents for row in postings if row.ledger_dimension == dimension) == 0
            )
        assert all(
            row.balance_before_cents + row.delta_cents == row.balance_after_cents
            and row.previous_posting_id is not None
            for row in postings
        )
    after = physical_snapshot(engine)
    replay = client.post(
        f"/api/v1/full-payment-relations/actions/{action['action_id']}/execute",
        json={"expected_epoch_id": epoch},
    )
    assert replay.status_code == 200 and replay.json()["receipt"] == result["receipt"]
    prepared_replay = client.post(
        f"/api/v1/full-payment-relations/authorizations/{authorization}/prepare", json=prepare_body
    )
    assert (
        prepared_replay.status_code == 200
        and prepared_replay.json()["action_id"] == action["action_id"]
    )
    current = client.get(f"/api/v1/full-payment-relations/commands/{epoch}/by-key/606-user-confirm")
    assert (
        current.status_code == 200
        and current.json()["original"]["current_scope_status"] == "CURRENT"
    )
    assert current.json()["original"]["original"] == confirmed.json()["original"]
    original_full = client.get(f"/api/v1/full-policies/{full_id}").json()
    assert original_full["reference_validation"] == "CHANGED_OR_UNAVAILABLE"
    assert original_full["planning_confirmation_valid"] is False
    assert original_full["current_version"]["content_hash"] == full["configuration_hash"]
    assert physical_snapshot(engine) == after
    with Session(engine) as session:
        operations = list(
            session.scalars(
                select(BankOperation).where(
                    BankOperation.action_plan_id == UUID(action["action_id"])
                )
            )
        )
        assert len(operations) == 1 and operations[0].status == "SETTLED"
        pinned = session.get(EvidenceItem, UUID(start["scope"]["payee_evidence_id"]))
        assert pinned is not None and pinned.content_hash == start["scope"]["payee_evidence_hash"]
