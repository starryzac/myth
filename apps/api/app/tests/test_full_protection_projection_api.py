"""Single isolated real PG risk candidate; root owns its execution, not full acceptance."""

from datetime import timedelta
from typing import Any

import pytest
from app.db.models import Account
from app.domain.full_protection_projection import ProtectionTemplate
from app.domain.policy_configuration import configuration_hash
from app.services.full_policy_lifecycle import canonical_candidate
from app.tests.test_full_projection import NOW
from app.tests.test_full_projection_api import (
    annual_client as annual_client,
)
from app.tests.test_full_projection_api import physical_snapshot
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def confirm(
    client: TestClient, template: ProtectionTemplate, config: dict[str, Any], key: str
) -> dict[str, Any]:
    result = client.post(
        "/api/v1/full-policies/confirm",
        json={
            "template_name": template,
            "configuration": config,
            "reviewed_hash": configuration_hash(canonical_candidate(template, config)),
            "accepted": True,
            "reason": "隔离真实模拟用户确认的年度规划风险用例",
            "idempotency_key": key,
        },
    )
    assert result.status_code == 200, result.text
    value: dict[str, Any] = result.json()
    assert value["bank_authority"] is False
    return value


def test_actual_full_dated_periodic_curve_binds_confirmation_and_is_readonly_and_conservative(
    annual_client: tuple[TestClient, Engine],
) -> None:
    client, engine = annual_client
    initial = client.get("/api/v1/planning/annual")
    assert initial.status_code == 200, initial.text
    declared = client.post(
        "/api/v1/policy-declarations",
        json={
            "configuration": {
                "type": "emergency_buffer",
                "name": "不得削减的原应急保护",
                "amount_cents": 100,
            },
            "idempotency_key": "full-annual-original-protection",
            "expected_epoch_id": initial.json()["audit"]["epoch_id"],
        },
    )
    assert declared.status_code == 200, declared.text
    accepted = client.post(
        f"/api/v1/policy-proposals/{declared.json()['proposal_id']}/confirm",
        json={
            "accepted": True,
            "reviewed_hash": declared.json()["configuration_hash"],
        },
    )
    assert accepted.status_code == 200, accepted.text
    protected_id = accepted.json()["policy_id"]
    with Session(engine) as session:
        cash = session.scalar(
            select(Account).where(Account.account_type == "CASH").order_by(Account.id)
        )
        assert cash is not None
        cash_id = cash.id
    dated = confirm(
        client,
        "DatedExpensePolicy",
        {
            "type": "dated_expense",
            "name": "已登记窗口支出",
            "window": {"start": "2026-10-12", "end": "2026-10-15"},
            "amount": {"min_cents": 100, "target_cents": 300, "max_cents": 500},
            "must_not_reduce_policy_ids": [str(protected_id)],
        },
        "full-annual-dated",
    )
    periodic = confirm(
        client,
        "PeriodicTransferPolicy",
        {
            "type": "periodic_transfer",
            "name": "已知收款人的周期规划",
            "source_account_id": str(cash_id),
            "payee_id": "synthetic-landlord-001",
            "amount_rule": {"kind": "exact", "amount_cents": 100},
            "due_day": 31,
            "prepare_days_before": 10,
            "single_action_cap_cents": 100,
        },
        "full-annual-periodic",
    )
    confirm(
        client,
        "SeasonalReservePolicy",
        {
            "type": "seasonal_reserve",
            "holiday_code": "SYNTHETIC_NEW_YEAR",
            "window": {"start": "2027-01-01", "end": "2027-01-07"},
            "lookback_days": 365,
            "minimum_historical_windows": 1,
            "quantile": 0.8,
            "essential_categories": ["food"],
            "adjustment_cap_cents": 1000,
        },
        "full-annual-seasonal",
    )
    before = physical_snapshot(engine)
    original = client.get("/api/v1/planning/annual").json()
    response = client.get("/api/v1/planning/full-annual")
    assert response.status_code == 200, response.text
    value = response.json()
    projection = value["projection"]
    assert value["source_issues"] == [] and value["audit"]["status"] == "VALID"
    assert value["audit"]["complete"] is True and value["source_evidence_ids"]
    assert projection["original_execution_view"] == original["execution_view"]
    assert projection["original_annual_projection"] == original["annual_projection"]
    assert projection["full_annual_projection"] is not None
    full = projection["full_annual_projection"]
    assert len(full["calculation_trace"]) == 366 * 3
    assert len(value["daily_checkpoints"]) == 365
    assert full["protected_cents_by_reason"]["full_dated_expense"] == 500
    assert full["protected_cents_by_reason"]["full_periodic_transfer"] == 1200
    assert (
        full["calculation_trace"][0]["cash_cents"]
        == original["annual_projection"]["calculation_trace"][0]["cash_cents"]
    )
    assert (
        full["calculation_trace"][0]["margin_cents"]
        == original["annual_projection"]["calculation_trace"][0]["margin_cents"] - 1700
    )
    occurrences = projection["occurrences"]
    assert len(occurrences) == 13
    assert {row["policy_id"] for row in occurrences} == {dated["policy_id"], periodic["policy_id"]}
    assert all(
        row["original_paid_cents"] is None and row["bank_authority"] is False for row in occurrences
    )
    assert projection["seasonal_adopted_adjustment_cents"] is None
    assert projection["seasonal_status"] == "ADVICE_ONLY_NO_ADOPTED_AMOUNT"
    assert value["future_income"]["included_in_execution_cents"] == 0
    assert value["future_income"]["included_in_planning_cents"] == 0
    assert value["grants_authority"] is False and value["execution_support"] == "NOT_IMPLEMENTED"
    assert value["full_policy_sources"][0]["confirmation"]["accepted"] is True
    assert client.get("/api/v1/planning/full-annual").json() == value
    for query in (
        {"paid_cents": "500"},
        {"future_income_cents": "999999"},
        {"user_id": value["user_id"]},
        {"as_of": (NOW + timedelta(days=1)).isoformat()},
    ):
        assert client.get("/api/v1/planning/full-annual", params=query).status_code == 422
    assert client.get("/api/v1/planning/annual").json() == original
    assert physical_snapshot(engine) == before

    # An actual source row tamper is limited to this owned isolated database.
    with Session(engine) as session, session.begin():
        actual = session.get(Account, cash_id)
        assert actual is not None
        actual.balance_cents += 1
    tampered = physical_snapshot(engine)
    rejected = client.get("/api/v1/planning/full-annual")
    assert rejected.status_code == 200, rejected.text
    unknown = rejected.json()
    assert unknown["projection"]["status"] == "UNKNOWN"
    assert unknown["projection"]["full_annual_projection"] is None
    assert unknown["source_issues"]
    assert all(point["after_principal"] is None for point in unknown["daily_checkpoints"])
    assert physical_snapshot(engine) == tampered
