"""One root-run actual PG candidate; collection alone proves no financial result."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.api.dependencies import get_engine, get_now
from app.domain.demo_identity import DEMO_USER_ID
from app.main import create_app
from app.services.demo_seed import seed_demo
from app.services.full_policy_lifecycle import (
    FullChangeRequest,
    change_full_policy,
    confirm_full_policy,
)
from app.tests.test_demo_seed import demo_engine as demo_engine
from app.tests.test_full_policy_lifecycle_integration import body
from app.tests.test_full_projection_api import physical_snapshot
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_actual_current_dependencies_drift_complete_denominator_and_zero_writes(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    clock = datetime.now(UTC)
    config = {
        "type": "dated_expense",
        "name": "真实未来依赖目标",
        "window": {
            "start": (clock.date() + timedelta(days=14)).isoformat(),
            "end": (clock.date() + timedelta(days=15)).isoformat(),
        },
        "amount": {"min_cents": 100, "target_cents": 100, "max_cents": 100},
    }
    with Session(demo_engine) as session, session.begin():
        target = confirm_full_policy(
            session,
            DEMO_USER_ID,
            body("DatedExpensePolicy", config, key="dependency-actual-target"),
            clock,
        )
    linked = dict(config, name="真实依赖持有者", must_not_reduce_policy_ids=[str(target.policy_id)])
    with Session(demo_engine) as session, session.begin():
        selected = confirm_full_policy(
            session,
            DEMO_USER_ID,
            body("DatedExpensePolicy", linked, key="dependency-actual-selected"),
            clock,
        )
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: demo_engine
    app.dependency_overrides[get_now] = lambda: clock
    path = f"/api/v1/full-policy-dependencies/{selected.policy_id}"
    with TestClient(app) as client:
        before = physical_snapshot(demo_engine)
        response = client.get(path)
        assert response.status_code == 200, response.text
        result = response.json()
        assert (
            result["status"] == "COMPLETE_CURRENT_DECLARATION_GRAPH"
            and not result["review_required"]
        )
        assert result["current_policy_count"] == result["captured_policy_count"] == 2
        assert result["edges"][0]["status"] == "UNCHANGED"
        assert not result["bank_authority"] and not result["financial_conflict_solver_applied"]
        assert physical_snapshot(demo_engine) == before
        clock += timedelta(seconds=1)
        changed = dict(config, name="真实目标新版本")
        request = body("DatedExpensePolicy", changed, key="dependency-actual-target-change")
        with Session(demo_engine) as session, session.begin():
            change_full_policy(
                session,
                DEMO_USER_ID,
                target.policy_id,
                FullChangeRequest(
                    expected_version_id=target.version_id,
                    configuration=changed,
                    reviewed_hash=request.reviewed_hash,
                    accepted=True,
                    reason="明确调整名称产生新版本",
                    idempotency_key=request.idempotency_key,
                ),
                clock,
            )
        changed_original = physical_snapshot(demo_engine)
        drift = client.get(path)
        assert drift.status_code == 200, drift.text
        current = drift.json()
        assert (
            current["status"] == "COMPLETE_CURRENT_DECLARATION_GRAPH" and current["review_required"]
        )
        edge = current["edges"][0]
        assert edge["status"] == "CHANGED" and edge["target_id"] == str(target.policy_id)
        assert edge["original_binding_hash"] != edge["current_binding_hash"]
        assert current["review_hash"] != result["review_hash"]
        assert not current["all_template_action_rechecks_supported"]
        assert client.get(path + "?actor=USER").status_code == 422
        assert client.get("/api/v1/full-policy-dependencies/" + str(uuid4())).status_code == 404
        assert physical_snapshot(demo_engine) == changed_original
