"""One actual read-only family candidate; only Root may schedule isolated PG."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.api.dependencies import get_now
from app.services.demo_seed import DEMO_USER_ID
from app.services.full_action_set_asset_producers import capture_asset_family
from app.tests.test_asset_allocation_service import authorization
from app.tests.test_full_asset_allocation_api import actual_asset_declaration, actual_income
from app.tests.test_full_projection_api import annual_client as annual_client
from app.tests.test_full_projection_api import physical_snapshot
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_actual_immutable_whole_asset_family_replays_without_any_financial_or_metadata_write(
    annual_client: tuple[TestClient, Engine],
) -> None:
    client, engine = annual_client
    now = datetime.now(UTC)
    assert isinstance(client.app, FastAPI)
    client.app.dependency_overrides[get_now] = lambda: now
    actual_income(engine)
    full_id = actual_asset_declaration(client, engine)
    epoch = client.get("/api/v1/demo/state").json()["epoch_id"]
    declaration = client.post(
        "/api/v1/policy-declarations",
        json={
            "configuration": authorization(max_auto_managed_cents=2000000),
            "idempotency_key": "global-assets-actual-original-MVP",
            "expected_epoch_id": epoch,
            "source_proposal_id": None,
        },
    )
    assert declaration.status_code == 200, declaration.text
    proposal = declaration.json()
    confirmed = client.post(
        f"/api/v1/policy-proposals/{proposal['proposal_id']}/confirm",
        json={"accepted": True, "reviewed_hash": proposal["configuration_hash"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    mvp_id = confirmed.json()["policy_id"]
    before = physical_snapshot(engine)
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            with Session(bind=connection) as session:
                captured = capture_asset_family(session, DEMO_USER_ID, now)
                key = f"full-asset:{full_id}:{mvp_id}:PORTFOLIO"
                target = next(row for row in captured.result.candidates if row.candidate_key == key)
                assert target.state == "INCLUDED", captured.result.model_dump(mode="json")
                assert target.amount_cents is not None and target.amount_cents > 0
                assert target.autonomy_level == "ASK_ONCE" and target.signature is not None
                original = next(
                    row for row in captured.inputs.producers if row.candidate_key == key
                )
                assert (
                    original.actual_preview is not None
                    and original.actual_preview.portfolio is not None
                )
                assert original.actual_preview.portfolio.total_purchase_cents == target.amount_cents
                assert original.full_policy_id == UUID(full_id)
                assert not captured.result.bank_authority and not captured.result.financial_write
                assert not session.new and not session.dirty and not session.deleted
    assert physical_snapshot(engine) == before
