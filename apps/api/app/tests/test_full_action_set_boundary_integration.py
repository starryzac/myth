"""Root-run generated PostgreSQL candidate; not executed by this feature author."""

import json
from typing import Any
from uuid import UUID

import pytest
from app.db.models import Account, Policy, PolicyVersion
from app.domain.demo_identity import DEMO_USER_ID
from app.domain.external_bank_fact_types import ExternalFactRequest
from app.domain.full_action_set_boundary import (
    GlobalBoundaryObservation,
    GlobalBoundaryObserveRequest,
)
from app.services.audit_chain import current_audit_epoch, verify_audit_chain
from app.services.external_bank_facts import ingest_external_fact
from app.services.full_action_set_boundary import (
    global_boundary_intervention_source,
    observe_global_boundary,
    read_global_boundary_observation,
)
from app.services.policy_lifecycle import PolicyLifecycleError, suspend_policy
from app.tests.test_full_projection_api import physical_snapshot
from app.tests.test_goal_api import NOW, confirmed_goal_request
from app.tests.test_goal_api import goal_client as goal_client
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def financial_originals(value: str) -> dict[str, Any]:
    # The observation is allowed to append only DecisionRun and its audit data.
    # All other actual physical tables, not just a fixed money subset, must match.
    metadata = {
        "decision_runs",
        "audit_events",
        "audit_subject_snapshots",
        "audit_heads",
        "audit_epochs",
    }
    return {name: rows for name, rows in json.loads(value).items() if name not in metadata}


def test_actual_finite_set_numeric_silence_crossing_replay_and_zero_financial_writes(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    body = confirmed_goal_request(client, engine)
    created = client.post("/api/v1/goals", json=body)
    assert created.status_code == 200, created.text
    goal = created.json()["goal"]
    with Session(engine) as session, session.begin():
        policies = list(session.scalars(select(Policy).where(Policy.user_id == DEMO_USER_ID)))
        for policy in policies:
            version = session.scalar(
                select(PolicyVersion)
                .where(PolicyVersion.policy_id == policy.id)
                .order_by(PolicyVersion.version_number.desc())
                .limit(1)
            )
            assert version is not None
            if (
                str(policy.id) != goal["policy_id"]
                and version.configuration["type"]
                in {"recurring_expense", "goal_saving", "asset_allocation"}
                and policy.status == "ACTIVE"
            ):
                suspend_policy(session, DEMO_USER_ID, policy.id, version.id, NOW)
        account = session.scalar(
            select(Account)
            .where(Account.user_id == DEMO_USER_ID, Account.account_type == "CASH")
            .order_by(Account.id)
        )
        epoch = current_audit_epoch(session, DEMO_USER_ID)
        assert account is not None and epoch is not None
        cash_id, epoch_id = account.id, epoch.id
    observations: list[GlobalBoundaryObservation] = []
    for index, amount in enumerate((600000, 100000)):
        result = ingest_external_fact(
            engine,
            DEMO_USER_ID,
            ExternalFactRequest(
                user_id=DEMO_USER_ID,
                account_id=cash_id,
                kind="INCOME",
                amount_cents=amount,
                external_ref=f"actual-global-source-{index}",
                idempotency_key=f"actual-global-source-{index}",
                counterparty_ref="payroll",
                occurred_at=NOW,
            ),
            NOW,
        )
        assert result.bank_status == "SETTLED" and result.projection_status == "PROJECTED"
        before = physical_snapshot(engine)
        current = client.get("/api/v1/boundary/action-set/current")
        assert current.status_code == 200, current.text
        value = current.json()
        assert value["status"] == "COMPLETE" and value["global_action_set_complete"] is True, (
            json.dumps(value, ensure_ascii=False)
        )
        assert value["arbitrary_manual_intents_covered"] is False
        actual_goal = next(
            row for row in value["candidates"] if row["candidate_key"] == "goal:" + goal["id"]
        )
        assert actual_goal["state"] == "INCLUDED" and actual_goal["amount_cents"] == 200000
        assert value["bank_authority"] is False and value["financial_write"] is False
        for field in ("amount_cents", "now", "user_id", "authority", "facts"):
            assert (
                client.get(
                    "/api/v1/boundary/action-set/current", params={field: "fake"}
                ).status_code
                == 422
            )
        assert physical_snapshot(engine) == before
        request = GlobalBoundaryObserveRequest(
            expected_epoch_id=epoch_id,
            previous_observation_run_id=observations[-1].observation_run_id
            if observations
            else None,
            idempotency_key=f"actual-global-observation-{index}",
        )
        original = observe_global_boundary(engine, DEMO_USER_ID, request, NOW)
        assert original.kind == "BoundaryObserved" and not original.requires_user_attention
        assert original.global_action_set_complete
        after = physical_snapshot(engine)
        assert financial_originals(after) == financial_originals(before)
        observations.append(original)
        if index:
            first, second = observations
            assert second.snapshot.action_set_signature == first.snapshot.action_set_signature
            assert second.snapshot.financial_input_hash != first.snapshot.financial_input_hash
        replay = observe_global_boundary(engine, DEMO_USER_ID, request, NOW)
        assert replay == original.model_copy(update={"idempotent_replay": True})
        with Session(engine) as session:
            assert (
                read_global_boundary_observation(
                    session, DEMO_USER_ID, original.observation_run_id, NOW
                )
                == original
            )
        assert physical_snapshot(engine) == after
    first, second = observations
    with Session(engine) as session, session.begin():
        suspend_policy(
            session, DEMO_USER_ID, UUID(goal["policy_id"]), UUID(goal["policy_version_id"]), NOW
        )
    before = physical_snapshot(engine)
    third = observe_global_boundary(
        engine,
        DEMO_USER_ID,
        GlobalBoundaryObserveRequest(
            expected_epoch_id=epoch_id,
            previous_observation_run_id=second.observation_run_id,
            idempotency_key="actual-global-suspend-crossing",
        ),
        NOW,
    )
    assert (
        third.kind == "BoundaryCrossed"
        and third.requires_user_attention
        and third.global_action_set_complete
    )
    assert third.snapshot.action_set_signature != second.snapshot.action_set_signature
    assert all(row.state == "EXCLUDED" for row in third.snapshot.candidates)
    with Session(engine) as session:
        source = global_boundary_intervention_source(
            session, DEMO_USER_ID, third.observation_run_id, NOW
        )
        assert source.semantic_key == third.semantic_key and source.attention is True
        audit = verify_audit_chain(session, DEMO_USER_ID, mode="EXACT")
        assert audit.status == "VALID" and audit.errors == []
    after = physical_snapshot(engine)
    assert financial_originals(before) == financial_originals(after)
    with pytest.raises(PolicyLifecycleError) as error:
        observe_global_boundary(
            engine,
            DEMO_USER_ID,
            request.model_copy(update={"previous_observation_run_id": None}),
            NOW,
        )
    assert error.value.code == "IDEMPOTENCY_CONFLICT"
    assert physical_snapshot(engine) == after
