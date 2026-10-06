"""One real-PG candidate for root; NOT_RUN until all original guards are installed.

The isolated business clock stays at the registered NOW for the T0 acceptance
sequence. This does not prove live current-instant deadline usability or T1.
"""

from datetime import datetime, timedelta
from uuid import UUID

import pytest
from app.api.dependencies import get_now
from app.db.models import ActionPlan, BankOperation, SimulatedBankPosting
from app.domain.full_recovery_execution import (
    FullRecoveryConfirmation,
    FullRecoveryExecuteRequest,
    FullRecoveryPrepareRequest,
)
from app.services import execution, execution_bank
from app.services.demo_seed import DEMO_USER_ID
from app.services.full_policy_lifecycle import read_full_policy
from app.services.full_recovery_execution import (
    _reader,
    confirm_full_recovery_action,
    execute_full_recovery_action,
    lookup_full_recovery_execution,
    prepare_full_recovery_execution,
    preview_full_recovery_execution,
    require_installed_full_recovery_guards,
)
from app.services.local_actor_sessions import COOKIE_NAME, verify_local_actor_session
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


def test_actual_signed_user_t0_original_bank_response_loss_recovers_once_after_full_revocation(
    annual_client: tuple[TestClient, Engine], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine = annual_client
    require_installed_full_recovery_guards()
    secret = "SYNTHETIC_LOCAL_604_ISOLATED_RISK_ONLY"
    monkeypatch.setenv("BF_LOCAL_USER_SECRET", secret)
    monkeypatch.setenv("BF_LOCAL_SIGNING_KEY", "da" * 32)
    actual_income(engine)
    asset_id, position_id, purchase_receipt = actual_purchase(client, engine, "LIQUID_ASSET")
    full_id = UUID(actual_recovery_confirmation(client, asset_id))
    actual_shrink_to_deficit(client, engine)
    signed = client.post(
        "/api/v1/local-actor/login", json={"username": "bounded-user", "secret": secret}
    )
    assert signed.status_code == 200, signed.text
    token = client.cookies.get(COOKIE_NAME)
    assert token is not None
    principal = verify_local_actor_session(token, DEMO_USER_ID, NOW)
    with _reader(engine) as read:
        full = read_full_policy(read, DEMO_USER_ID, full_id, NOW)
        body = FullRecoveryPrepareRequest(
            policy_id=full_id,
            expected_version_id=full.current_version.version_id,
            expected_epoch_id=full.epoch_id,
            position_id=position_id,
            idempotency_key="604-original-t0-response-loss",
        )
    before = physical_snapshot(engine)
    with _reader(engine) as read:
        preview = preview_full_recovery_execution(read, DEMO_USER_ID, body, NOW)
    assert preview.proof.status == "VERIFIED_SCOPE"
    assert preview.proof.candidate.principal_cents == purchase_receipt["executed_cents"]
    assert preview.proof.deadline_at == NOW and not preview.bank_authority
    assert physical_snapshot(engine) == before
    prepared = prepare_full_recovery_execution(engine, DEMO_USER_ID, body, principal, NOW)
    assert prepared.autonomy_level == "ASK_ONCE" and prepared.status == "PLANNED"
    assert prepared.effect.latest_arrival_at == NOW and prepared.receipt is None
    assert prepared.effect.fee_cents == prepared.effect.loss_cents == 0
    assert prepare_full_recovery_execution(
        engine, DEMO_USER_ID, body, principal, NOW
    ).action_id == (prepared.action_id)
    confirmation = FullRecoveryConfirmation(
        expected_epoch_id=body.expected_epoch_id,
        reviewed_effect_hash=prepared.effect_hash,
        accepted=True,
    )
    confirmed = confirm_full_recovery_action(
        engine, DEMO_USER_ID, prepared.action_id, confirmation, principal, NOW
    )
    assert confirmed.status == "AUTHORIZED" and confirmed.effect_hash == prepared.effect_hash
    assert (
        confirm_full_recovery_action(
            engine, DEMO_USER_ID, prepared.action_id, confirmation, principal, NOW
        ).effect_hash
        == confirmed.effect_hash
    )
    execute_body = FullRecoveryExecuteRequest(
        expected_epoch_id=body.expected_epoch_id, reviewed_effect_hash=prepared.effect_hash
    )
    actual_process = execution_bank.process_operation

    def lose_response(target: Engine, user_id: UUID, action_id: UUID, at: datetime) -> None:
        # Explicit fault only after actual independent bank commit; no outcome injection.
        actual_process(target, user_id, action_id, at)
        raise TimeoutError("604_ACTUAL_BANK_COMMIT_RESPONSE_LOST")

    with monkeypatch.context() as fault:
        fault.setattr(execution, "process_operation", lose_response)
        with pytest.raises(TimeoutError, match="ACTUAL_BANK_COMMIT_RESPONSE_LOST"):
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
        assert len({row.id for row in postings}) == len(postings)
        assert {row.ledger_dimension for row in postings} == {"ECONOMIC"}
        for row in postings:
            assert row.balance_after_cents == row.balance_before_cents + row.delta_cents
        action = session.get(ActionPlan, prepared.action_id)
        assert action is not None and action.status == "UNKNOWN"
    after_bank = physical_snapshot(engine)
    with _reader(engine) as read:
        original = lookup_full_recovery_execution(read, DEMO_USER_ID, body.idempotency_key, NOW)
    assert original.status == "RECORDED" and original.original_request == body
    assert original.action is not None and original.action.receipt is None
    assert original.original_consent is not None and not original.consent_is_current_authority
    assert physical_snapshot(engine) == after_bank
    revoked = client.post(
        f"/api/v1/full-policies/{full_id}/revoke",
        json={
            "expected_version_id": str(body.expected_version_id),
            "reason": "受理后撤销，仅恢复原银行身份，不重新授权",
            "idempotency_key": "604-revoke-after-bank-commit",
        },
    )
    assert revoked.status_code == 200, revoked.text
    later = NOW + timedelta(days=1)
    assert isinstance(client.app, FastAPI)
    client.app.dependency_overrides[get_now] = lambda: later
    renewed = client.post(
        "/api/v1/local-actor/login", json={"username": "bounded-user", "secret": secret}
    )
    assert renewed.status_code == 200, renewed.text
    new_token = client.cookies.get(COOKIE_NAME)
    assert new_token is not None
    new_principal = verify_local_actor_session(new_token, DEMO_USER_ID, later)
    settled = execute_full_recovery_action(
        engine, DEMO_USER_ID, prepared.action_id, execute_body, new_principal, later
    )
    assert settled.status == "SUCCEEDED" and settled.bank_status == "SETTLED"
    assert settled.effect_hash == prepared.effect_hash and settled.receipt is not None
    assert settled.receipt.bank_operation_id == operation_id
    assert settled.receipt.executed_cents == purchase_receipt["executed_cents"]
    assert settled.receipt.fee_cents == settled.receipt.loss_cents == 0
    done = physical_snapshot(engine)
    assert (
        execute_full_recovery_action(
            engine, DEMO_USER_ID, prepared.action_id, execute_body, new_principal, later
        ).receipt
        == settled.receipt
    )
    assert physical_snapshot(engine) == done
