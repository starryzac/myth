"""Root-only generated-db absence/readonly candidate; no fabricated expired adoption."""

from datetime import timedelta

import pytest
from app.db.models import User
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import audit_read_scope, verify_audit_chain
from app.services.full_policy_lifecycle import canonical_candidate
from app.services.full_seasonal_ended_adoption import read_current_ended_seasonal_adoption
from app.tests.test_full_projection import NOW
from app.tests.test_full_projection_api import annual_client as annual_client
from app.tests.test_full_projection_api import physical_snapshot
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_actual_ended_read_without_original_adoption_is_null_and_all_physical_rows_unchanged(
    annual_client: tuple[TestClient, Engine],
) -> None:
    client, engine = annual_client
    config = {
        "type": "seasonal_reserve",
        "name": "到期原件缺失不可猜零",
        "holiday_code": "NATIONAL_DAY",
        "window": {"start": "2026-10-04", "end": "2026-10-07"},
        "lookback_days": 1096,
        "minimum_historical_windows": 2,
        "quantile": 0.8,
        "essential_categories": ["food", "transport", "daily_necessities"],
        "adjustment_cap_cents": 500000,
        "requires_confirmation": True,
        "advice_only": True,
    }
    created = client.post(
        "/api/v1/full-policies/confirm",
        json={
            "template_name": "SeasonalReservePolicy",
            "configuration": config,
            "reviewed_hash": configuration_hash(
                canonical_candidate("SeasonalReservePolicy", config)
            ),
            "accepted": True,
            "reason": "明确策略声明，未采纳任何统计金额",
            "idempotency_key": "ended-adoption-absence-policy",
        },
    )
    assert created.status_code == 200, created.text
    from uuid import UUID

    policy_id = UUID(created.json()["policy_id"])
    before = physical_snapshot(engine)
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin(), Session(connection) as session:
            session.execute(text("SET TRANSACTION READ ONLY"))
            user = session.scalar(select(User).where(User.is_simulated.is_(True)))
            assert user is not None
            with audit_read_scope(session):
                result = read_current_ended_seasonal_adoption(
                    session, user.id, policy_id, NOW + timedelta(days=4)
                )
                assert result.status == "NO_ORIGINAL_ADOPTION"
                assert result.registered_adoption_evidence_count == 0
                assert result.current_floor_cents is None and result.original_adopted_cents is None
                assert result.inputs is not None and result.inputs.audit.status == "VALID"
                assert not result.cash_balance_changed and not result.financial_execution_performed
                assert verify_audit_chain(session, user.id, result.epoch_id).status == "VALID"
    assert physical_snapshot(engine) == before
