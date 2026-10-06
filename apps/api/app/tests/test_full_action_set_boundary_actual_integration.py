"""Root-only generated PG candidate; not run by this producer implementation."""

import json
from datetime import timedelta
from uuid import UUID

import pytest
from app.api.dependencies import get_now
from app.db.models import Account, Policy, PolicyVersion
from app.domain.demo_identity import DEMO_USER_ID
from app.domain.external_bank_fact_types import ExternalFactRequest
from app.domain.full_action_set_boundary import GlobalBoundaryObserveRequest
from app.services.audit_chain import current_audit_epoch, verify_audit_chain
from app.services.external_bank_facts import ingest_external_fact
from app.services.full_action_set_boundary_actual import (
    actual_global_boundary_intervention_source,
    observe_actual_global_boundary,
    read_actual_global_boundary_observation,
)
from app.services.policy_lifecycle import suspend_policy
from app.tests.test_full_action_set_boundary_integration import financial_originals
from app.tests.test_full_dynamic_goal_reserve_api import full_dynamic_goal
from app.tests.test_full_projection_api import physical_snapshot
from app.tests.test_goal_api import NOW
from app.tests.test_goal_api import goal_client as goal_client
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_actual_physical_v2_dynamic_goal_silence_crossing_and_zero_financial_writes(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    goal_id, model_id = full_dynamic_goal(client, engine)
    model = client.get(f"/api/v1/goals/{goal_id}/full-model").json()
    assert model["status"] == "VERIFIED" and model["evidence_id"] == model_id
    with Session(engine) as session, session.begin():
        for policy in session.scalars(select(Policy).where(Policy.user_id == DEMO_USER_ID)):
            version = session.scalar(
                select(PolicyVersion)
                .where(PolicyVersion.policy_id == policy.id)
                .order_by(PolicyVersion.version_number.desc())
                .limit(1)
            )
            assert version is not None
            if (
                policy.id != UUID(model["policy_id"])
                and version.configuration["type"]
                in {"recurring_obligation", "goal_saving", "asset_authorization"}
                and policy.status == "ACTIVE"
            ):
                suspend_policy(session, DEMO_USER_ID, policy.id, version.id, NOW)
        cash = session.scalar(
            select(Account)
            .where(Account.user_id == DEMO_USER_ID, Account.account_type == "CASH")
            .order_by(Account.id)
        )
        epoch = current_audit_epoch(session, DEMO_USER_ID)
        assert cash is not None and epoch is not None
        cash_id, epoch_id = cash.id, epoch.id
    previous = None
    api = client.app
    assert isinstance(api, FastAPI)
    for index, amount in enumerate((600000, 100000)):
        # A later real income must sort after the original funding fragment.
        # Simultaneous incomes use UUID order and can change the economic source;
        # that is a legitimate crossing, not a numerical-only observation.
        now = NOW + timedelta(seconds=index)
        api.dependency_overrides[get_now] = lambda selected_now=now: selected_now
        fact = ingest_external_fact(
            engine,
            DEMO_USER_ID,
            ExternalFactRequest(
                user_id=DEMO_USER_ID,
                account_id=cash_id,
                kind="INCOME",
                amount_cents=amount,
                external_ref=f"actual-full-global-payroll-{index}",
                idempotency_key=f"actual-full-global-payroll-{index}",
                counterparty_ref="payroll",
                occurred_at=now,
            ),
            now,
        )
        assert fact.bank_status == "SETTLED" and fact.projection_status == "PROJECTED"
        before = physical_snapshot(engine)
        response = client.get("/api/v1/boundary/actual-action-set/current")
        assert response.status_code == 200, response.text
        snapshot = response.json()
        assert snapshot["global_action_set_complete"] is True, json.dumps(
            {
                "reasons": snapshot["reasons"],
                "unsupported_producers": snapshot["unsupported_producers"],
                "candidates": snapshot["candidates"],
                "table_gaps": [row for row in snapshot["table_coverage"] if not row["complete"]],
            },
            ensure_ascii=False,
        )
        coverage = {row["table"]: row for row in snapshot["table_coverage"]}
        assert "simulated_bank_ledger_heads" not in coverage
        assert coverage["evidence_items"]["actual_count"] > 200
        assert all(
            row["complete"] and row["actual_count"] == row["captured_count"]
            for row in coverage.values()
        )
        assert len(snapshot["candidates"]) == len(snapshot["expected_candidate_keys"])
        target = [
            row for row in snapshot["candidates"] if row["candidate_key"] == "goal:" + goal_id
        ]
        assert (
            len(target) == 1
            and target[0]["state"] == "INCLUDED"
            and target[0]["amount_cents"] == 20000
        )
        assert snapshot["bank_authority"] is False and snapshot["financial_write"] is False
        assert physical_snapshot(engine) == before
        body = GlobalBoundaryObserveRequest(
            expected_epoch_id=epoch_id,
            previous_observation_run_id=previous.observation_run_id if previous else None,
            idempotency_key=f"actual-full-global-observation-{index}",
        )
        observed = observe_actual_global_boundary(engine, DEMO_USER_ID, body, now)
        assert observed.kind == "BoundaryObserved" and not observed.requires_user_attention
        assert observed.global_action_set_complete
        assert financial_originals(physical_snapshot(engine)) == financial_originals(before)
        if previous:
            assert previous.snapshot.action_set_signature == observed.snapshot.action_set_signature
            assert previous.snapshot.financial_input_hash != observed.snapshot.financial_input_hash
        replay = observe_actual_global_boundary(engine, DEMO_USER_ID, body, now)
        assert replay == observed.model_copy(update={"idempotent_replay": True})
        previous = observed
    assert previous is not None
    with Session(engine) as session, session.begin():
        suspend_policy(
            session,
            DEMO_USER_ID,
            UUID(model["policy_id"]),
            UUID(model["base_policy_version_id"]),
            now,
        )
    before = physical_snapshot(engine)
    crossed = observe_actual_global_boundary(
        engine,
        DEMO_USER_ID,
        GlobalBoundaryObserveRequest(
            expected_epoch_id=epoch_id,
            previous_observation_run_id=previous.observation_run_id,
            idempotency_key="actual-full-global-suspension",
        ),
        now,
    )
    assert (
        crossed.kind == "BoundaryCrossed"
        and crossed.requires_user_attention
        and crossed.global_action_set_complete
    )
    with Session(engine) as session:
        assert (
            read_actual_global_boundary_observation(
                session, DEMO_USER_ID, crossed.observation_run_id, now
            )
            == crossed
        )
        source = actual_global_boundary_intervention_source(
            session, DEMO_USER_ID, crossed.observation_run_id, now
        )
        assert source.semantic_key == crossed.semantic_key
        assert verify_audit_chain(session, DEMO_USER_ID, mode="EXACT").status == "VALID"
    assert financial_originals(physical_snapshot(engine)) == financial_originals(before)
