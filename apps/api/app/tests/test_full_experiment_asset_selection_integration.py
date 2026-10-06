"""One real source-read candidate; Root alone schedules PG, no arm execution."""

import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.api.dependencies import get_now
from app.db.models import AssetProduct, PolicyVersion
from app.db.settings import REPOSITORY_ROOT
from app.domain.full_asset_execution import FullAssetPrepareRequest
from app.domain.full_experiment_asset_selection import (
    FullExperimentAssetSelection,
    FullMechanismRuleOriginal,
    RegisteredFullMechanismRule,
)
from app.domain.full_experiment_mechanisms import Arm, Rule
from app.services.demo_seed import DEMO_USER_ID
from app.services.full_experiment_asset_selection import read_current_general_purchase_selection
from app.tests.test_asset_allocation_service import authorization
from app.tests.test_full_asset_allocation_api import actual_asset_declaration, actual_income
from app.tests.test_full_projection_api import annual_client as annual_client
from app.tests.test_full_projection_api import physical_snapshot
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def _read(
    engine: Engine, request: FullAssetPrepareRequest, now: datetime, arm: Arm
) -> FullExperimentAssetSelection:
    original = FullMechanismRuleOriginal(
        user_id=DEMO_USER_ID,
        opportunity_id="REAL_SOURCE_READ_RISK_NOT_FORMAL",
        original_request=request,
        rule=Rule(arm_id=arm, threshold_cents=8000000),
    )
    # Original author rule retained before this read. No response-derived values,
    # outcome, model confidence, bank permission, or fabricated evidence.
    relative = ".runtime/general-mechanism-source-risk-" + uuid4().hex + ".json"
    raw = original.model_dump_json().encode()
    with (REPOSITORY_ROOT / relative).open("xb") as stream:
        stream.write(raw)
    locator = RegisteredFullMechanismRule(
        original_path=relative, sha256=hashlib.sha256(raw).hexdigest()
    )
    with Session(engine) as session:
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        session.execute(text("SET TRANSACTION READ ONLY"))
        return read_current_general_purchase_selection(session, DEMO_USER_ID, request, locator, now)


def test_actual_complete_general_source_selects_distinct_rules_readonly_and_catalogue_drift_unknown(
    annual_client: tuple[TestClient, Engine],
) -> None:
    client, engine = annual_client
    now = datetime.now(UTC)
    assert isinstance(client.app, FastAPI)
    client.app.dependency_overrides[get_now] = lambda: now
    actual_income(engine, now=now)
    full_id = actual_asset_declaration(client, engine, now=now)
    epoch_id = client.get("/api/v1/demo/state").json()["epoch_id"]
    declared = client.post(
        "/api/v1/policy-declarations",
        json={
            "configuration": authorization(
                max_auto_managed_cents=2000000, single_action_cap_cents=300000
            ),
            "idempotency_key": "full-mechanism-real-MVP-permission",
            "expected_epoch_id": epoch_id,
            "source_proposal_id": None,
        },
    )
    assert declared.status_code == 200, declared.text
    proposal = declared.json()
    confirmed = client.post(
        f"/api/v1/policy-proposals/{proposal['proposal_id']}/confirm",
        json={"accepted": True, "reviewed_hash": proposal["configuration_hash"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    mvp_id = UUID(confirmed.json()["policy_id"])
    with Session(engine) as session:
        version = session.scalar(
            select(PolicyVersion)
            .where(PolicyVersion.policy_id == mvp_id)
            .order_by(PolicyVersion.version_number.desc())
        )
        assert version is not None
        mvp_version = version.id
    current = client.get(f"/api/v1/full-policies/{full_id}")
    assert current.status_code == 200, current.text
    request = FullAssetPrepareRequest(
        full_policy_id=UUID(full_id),
        expected_full_policy_version_id=UUID(current.json()["current_version"]["version_id"]),
        mvp_asset_policy_id=mvp_id,
        expected_mvp_policy_version_id=mvp_version,
        expected_epoch_id=UUID(epoch_id),
        idempotency_key="full-mechanism-source-read-only",
    )
    before = physical_snapshot(engine)
    p = _read(engine, request, now, "P")
    assert p.status == "PROPOSED", p.model_dump_json()
    assert p.selected_portfolio is not None and p.mechanism_decision is not None
    assert p.original_p_result is not None
    original_response = p.source_originals["original_p_response"]
    assert isinstance(original_response, dict)
    assert original_response["allocation"] == p.original_p_result.model_dump(mode="json")
    assert len(p.product_originals) == p.source_counts["current_effective_products"]
    assert p.source_counts["catalogue_physical_products"] == p.source_counts["catalogue_originals"]
    assert p.source_counts["full_protection_points"] == 1098
    b1 = _read(engine, request, now, "B1")
    assert b1.status == "PROPOSED", b1.model_dump_json()
    assert b1.selected_portfolio is not None and b1.mechanism_input is not None
    assert b1.original_p_result == p.original_p_result
    facts = b1.mechanism_input.facts
    assert b1.selected_portfolio.batch.amount_cents == max(
        0, facts.settled_cash_cents - facts.active_reserved_cents - 8000000
    )
    assert b1.selected_portfolio.batch.amount_cents != p.selected_portfolio.batch.amount_cents
    b4 = _read(engine, request, now, "B4")
    assert b4.status == "MISSING" and b4.selected_portfolio is None
    assert b4.reasons == ["ACTUAL_CONFIDENCE_MODEL_NOT_CALLED"]
    for value in (p, b1, b4):
        assert not value.bank_authority and value.future_income_included_cents == 0
        assert (
            value.runtime_consumer_status == "NOT_CONNECTED" and value.execution_status == "NOT_RUN"
        )
        assert value.actual_metrics is None
    assert physical_snapshot(engine) == before

    with Session(engine) as session, session.begin():
        product = session.get(AssetProduct, p.selected_portfolio.batch.product_id)
        assert product is not None
        product.annual_yield_bps += 1
    tampered = physical_snapshot(engine)
    unknown = _read(engine, request, now, "P")
    assert unknown.status == "UNKNOWN" and unknown.selected_portfolio is None
    assert unknown.reasons and not unknown.bank_authority
    assert physical_snapshot(engine) == tampered
