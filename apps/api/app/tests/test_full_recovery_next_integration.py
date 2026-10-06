"""One root-only real-PG candidate. Collection is not T1/multi-position proof.

Uses the existing isolated T0 business-clock fixture. No test synthesizes bank
outcomes: the response-loss fault occurs only after the original bank commit.
"""

from datetime import timedelta
from uuid import UUID

import pytest
from app.api.dependencies import get_now
from app.db.models import ActionPlan, BankOperation, SimulatedBankPosting
from app.domain.full_recovery_execution import FullRecoveryConfirmation, FullRecoveryExecuteRequest
from app.domain.full_recovery_next import FullRecoveryNextRequest
from app.services import execution, execution_bank
from app.services.demo_seed import DEMO_USER_ID
from app.services.full_policy_lifecycle import read_full_policy
from app.services.full_recovery_execution import (
    _reader,
    confirm_full_recovery_action,
    execute_full_recovery_action,
    require_installed_full_recovery_guards,
)
from app.services.full_recovery_next import (
    lookup_next_whole_recovery,
    prepare_next_whole_recovery,
    preview_next_whole_recovery,
)
from app.services.local_actor_sessions import COOKIE_NAME, verify_local_actor_session
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_full_asset_allocation_api import actual_income
from app.tests.test_full_projection import NOW
from app.tests.test_full_projection_api import annual_client as annual_client
from app.tests.test_full_projection_api import physical_snapshot
from app.tests.test_full_recovery_planning_api import (
    actual_purchase,
    actual_recovery_confirmation,
    actual_shrink_to_deficit,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_actual_next_whole_key_selects_original_t0_and_never_advances_after_unknown_or_revoke(
    annual_client: tuple[TestClient, Engine], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine = annual_client
    require_installed_full_recovery_guards()
    secret = "SYNTHETIC_604_NEXT_ISOLATED_LOCAL_USER"
    monkeypatch.setenv("BF_LOCAL_USER_SECRET", secret)
    monkeypatch.setenv("BF_LOCAL_SIGNING_KEY", "ab" * 32)
    actual_income(engine)
    asset_id, position_id, purchase = actual_purchase(client, engine, "LIQUID_ASSET")
    policy_id = UUID(actual_recovery_confirmation(client, asset_id))
    actual_shrink_to_deficit(client, engine)
    assert (
        client.post(
            "/api/v1/local-actor/login", json={"username": "bounded-user", "secret": secret}
        ).status_code
        == 200
    )
    token = client.cookies.get(COOKIE_NAME)
    assert token is not None
    principal = verify_local_actor_session(token, DEMO_USER_ID, NOW)
    with _reader(engine) as read:
        full = read_full_policy(read, DEMO_USER_ID, policy_id, NOW)
        body = FullRecoveryNextRequest(
            policy_id=policy_id,
            expected_version_id=full.current_version.version_id,
            expected_epoch_id=full.epoch_id,
            idempotency_key="actual-604-next-whole-fixed-root",
        )
    before = physical_snapshot(engine)
    with _reader(engine) as read:
        preview = preview_next_whole_recovery(read, DEMO_USER_ID, body, NOW)
    assert preview.status == "READY_TO_PREPARE" and preview.selection is not None
    assert preview.selection.next_v1_request is not None
    assert preview.selection.next_v1_request.position_id == position_id
    assert preview.planning is not None and preview.planning.plan is not None
    assert preview.selection.current_candidate_denominator == len(preview.planning.plan.candidates)
    assert len(preview.planning.plan.actual_boundary.calculation_trace) == 1098
    assert preview.original_v1_preview is not None
    assert preview.original_v1_preview.proof.deadline_at == NOW
    assert physical_snapshot(engine) == before
    prepared = prepare_next_whole_recovery(engine, DEMO_USER_ID, body, principal, NOW)
    assert prepared.autonomy_level == "ASK_ONCE" and prepared.status == "PLANNED"
    assert prepared.effect.amount_cents == purchase["executed_cents"]
    assert prepared.effect.position_id == position_id and prepared.effect.latest_arrival_at == NOW
    saved = physical_snapshot(engine)
    assert prepare_next_whole_recovery(engine, DEMO_USER_ID, body, principal, NOW) == prepared
    with pytest.raises(PolicyLifecycleError, match="root-key"):
        prepare_next_whole_recovery(
            engine,
            DEMO_USER_ID,
            body.model_copy(update={"expected_version_id": UUID(int=123)}),
            principal,
            NOW,
        )
    assert physical_snapshot(engine) == saved
    confirm_full_recovery_action(
        engine,
        DEMO_USER_ID,
        prepared.action_id,
        FullRecoveryConfirmation(
            expected_epoch_id=body.expected_epoch_id,
            reviewed_effect_hash=prepared.effect_hash,
            accepted=True,
        ),
        principal,
        NOW,
    )
    execute_body = FullRecoveryExecuteRequest(
        expected_epoch_id=body.expected_epoch_id, reviewed_effect_hash=prepared.effect_hash
    )
    native = execution_bank.process_operation

    def lose_response(target: Engine, user_id: UUID, action_id: UUID, at: object) -> None:
        # Actual committed bank result only; never a fabricated receipt or status.
        from datetime import datetime

        assert isinstance(at, datetime)
        native(target, user_id, action_id, at)
        raise TimeoutError("NEXT_WHOLE_ACTUAL_BANK_COMMIT_RESPONSE_LOST")

    with monkeypatch.context() as fault:
        fault.setattr(execution, "process_operation", lose_response)
        with pytest.raises(TimeoutError, match="ACTUAL_BANK_COMMIT"):
            execute_full_recovery_action(
                engine, DEMO_USER_ID, prepared.action_id, execute_body, principal, NOW
            )
    with Session(engine) as session:
        operations = list(
            session.scalars(
                select(BankOperation).where(BankOperation.action_plan_id == prepared.action_id)
            )
        )
        assert len(operations) == 1 and operations[0].status == "SETTLED"
        operation_id = operations[0].id
        postings = list(
            session.scalars(
                select(SimulatedBankPosting).where(
                    SimulatedBankPosting.operation_id == operation_id
                )
            )
        )
        assert postings and sum(row.delta_cents for row in postings) == 0
        action = session.get(ActionPlan, prepared.action_id)
        assert action is not None and action.status == "UNKNOWN"
    unknown_snapshot = physical_snapshot(engine)
    with _reader(engine) as read:
        original = lookup_next_whole_recovery(read, DEMO_USER_ID, body.idempotency_key, NOW)
    assert original.bound_request == body and original.original_v1_lookup.action is not None
    assert original.original_v1_lookup.action.action_id == prepared.action_id
    assert original.original_v1_lookup.action.receipt is None
    assert (
        prepare_next_whole_recovery(engine, DEMO_USER_ID, body, principal, NOW).action_id
        == prepared.action_id
    )
    assert physical_snapshot(engine) == unknown_snapshot
    revoked = client.post(
        f"/api/v1/full-policies/{policy_id}/revoke",
        json={
            "expected_version_id": str(body.expected_version_id),
            "reason": "保持原银行身份，只恢复原件",
            "idempotency_key": "next-whole-revoke-after-original-acceptance",
        },
    )
    assert revoked.status_code == 200, revoked.text
    later = NOW + timedelta(days=1)
    assert isinstance(client.app, FastAPI)
    client.app.dependency_overrides[get_now] = lambda: later
    assert (
        client.post(
            "/api/v1/local-actor/login", json={"username": "bounded-user", "secret": secret}
        ).status_code
        == 200
    )
    token = client.cookies.get(COOKIE_NAME)
    assert token is not None
    renewed = verify_local_actor_session(token, DEMO_USER_ID, later)
    done = execute_full_recovery_action(
        engine, DEMO_USER_ID, prepared.action_id, execute_body, renewed, later
    )
    assert done.status == "SUCCEEDED" and done.receipt is not None
    assert done.receipt.bank_operation_id == operation_id
    stable = physical_snapshot(engine)
    assert (
        prepare_next_whole_recovery(engine, DEMO_USER_ID, body, renewed, later).action_id
        == prepared.action_id
    )
    assert (
        execute_full_recovery_action(
            engine, DEMO_USER_ID, prepared.action_id, execute_body, renewed, later
        ).receipt
        == done.receipt
    )
    assert physical_snapshot(engine) == stable
