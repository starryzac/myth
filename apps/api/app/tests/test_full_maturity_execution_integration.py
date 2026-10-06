"""Single owned PostgreSQL candidate; collection is not actual financial proof.

Trusted fixture clock reaches the ACTUAL purchased fixed contract maturity.
No position, date, price, bank result or receipt is fabricated.
"""

from datetime import datetime, timedelta
from uuid import UUID

import pytest
from app.api.dependencies import get_now
from app.db.models import (
    AssetPosition,
    AssetProduct,
    BankOperation,
    EvidenceItem,
    SimulatedBankPosting,
)
from app.domain.full_maturity_execution import (
    FullMaturityConfirmation,
    FullMaturityExecuteRequest,
    FullMaturityRequest,
)
from app.services import simulated_bank
from app.services.audit_chain import verify_audit_chain
from app.services.demo_seed import DEMO_USER_ID
from app.services.full_maturity_execution import (
    confirm_maturity_execution,
    execute_maturity_execution,
    lookup_maturity_execution,
    prepare_maturity_execution,
    preview_maturity_execution,
    require_installed_guard,
)
from app.services.full_policy_lifecycle import read_full_policy
from app.services.full_recovery_execution import _reader
from app.services.local_actor_sessions import COOKIE_NAME, verify_local_actor_session
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_full_asset_allocation_api import actual_income
from app.tests.test_full_projection_api import annual_client as annual_client
from app.tests.test_full_projection_api import physical_snapshot
from app.tests.test_full_recovery_planning_api import actual_purchase, actual_recovery_confirmation
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_actual_mature_whole_user_ask_unknown_key_revoke_recovery_and_tamper(
    annual_client: tuple[TestClient, Engine], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine = annual_client
    require_installed_guard()
    actual_income(engine)
    asset_id, position_id, bought = actual_purchase(client, engine, "FIXED_ASSET")
    policy_id = UUID(actual_recovery_confirmation(client, asset_id))
    with Session(engine) as read:
        position = read.get(AssetPosition, position_id)
        assert position is not None and position.maturity_at is not None
        product = read.get(AssetProduct, position.product_id)
        assert product is not None
        mature_at = position.maturity_at + timedelta(days=product.redemption_delay_days)
        assert mature_at == position.purchased_at + timedelta(
            days=product.maturity_rule["term_days"] + product.redemption_delay_days
        )
    assert isinstance(client.app, FastAPI)
    client.app.dependency_overrides[get_now] = lambda: mature_at
    secret = "SYNTHETIC_ISOLATED_MATURITY_USER"
    monkeypatch.setenv("BF_LOCAL_USER_SECRET", secret)
    monkeypatch.setenv("BF_LOCAL_SIGNING_KEY", "bc" * 32)
    assert (
        client.post(
            "/api/v1/local-actor/login", json={"username": "bounded-user", "secret": secret}
        ).status_code
        == 200
    )
    token = client.cookies.get(COOKIE_NAME)
    assert token is not None
    principal = verify_local_actor_session(token, DEMO_USER_ID, mature_at)
    with _reader(engine) as read:
        full = read_full_policy(read, DEMO_USER_ID, policy_id, mature_at)
        body = FullMaturityRequest(
            policy_id=policy_id,
            expected_version_id=full.current_version.version_id,
            expected_epoch_id=full.epoch_id,
            position_id=position_id,
            idempotency_key="actual-whole-mature-user-original-key",
        )
    before = physical_snapshot(engine)
    with _reader(engine) as read:
        preview = preview_maturity_execution(read, DEMO_USER_ID, body, mature_at)
    assert preview.proof.status == "VERIFIED_SCOPE", preview.proof.reasons
    assert preview.proof.command is not None
    assert preview.proof.command.kind == "MATURE"
    assert preview.proof.command.principal_cents == bought["executed_cents"]
    assert not preview.proof.projected_cash_is_current_cash
    assert physical_snapshot(engine) == before
    prepared = prepare_maturity_execution(engine, DEMO_USER_ID, body, principal, mature_at)
    assert prepared.status == "PLANNED" and prepared.autonomy_level == "ASK_ONCE"
    saved = physical_snapshot(engine)
    assert prepare_maturity_execution(engine, DEMO_USER_ID, body, principal, mature_at) == prepared
    assert physical_snapshot(engine) == saved
    execute_body = FullMaturityExecuteRequest(
        expected_epoch_id=body.expected_epoch_id,
        reviewed_command_hash=prepared.reviewed_command_hash,
    )
    with pytest.raises(PolicyLifecycleError):
        execute_maturity_execution(
            engine, DEMO_USER_ID, prepared.action_id, execute_body, principal, mature_at
        )
    assert physical_snapshot(engine) == saved
    accepted = confirm_maturity_execution(
        engine,
        DEMO_USER_ID,
        prepared.action_id,
        FullMaturityConfirmation(
            expected_epoch_id=body.expected_epoch_id,
            reviewed_command_hash=prepared.reviewed_command_hash,
            accepted=True,
        ),
        principal,
        mature_at,
    )
    assert accepted.status == "AUTHORIZED" and accepted.original_consent is not None
    native = simulated_bank.process_redemption

    def lose_actual_committed_response(
        target: Engine, user: UUID, action: UUID, at: datetime
    ) -> None:
        native(target, user, action, at)
        raise TimeoutError("ACTUAL_MATURE_BANK_COMMIT_RESPONSE_LOST")

    with monkeypatch.context() as fault:
        fault.setattr(simulated_bank, "process_redemption", lose_actual_committed_response)
        with pytest.raises(TimeoutError, match="ACTUAL_MATURE_BANK_COMMIT"):
            execute_maturity_execution(
                engine, DEMO_USER_ID, prepared.action_id, execute_body, principal, mature_at
            )
    unknown = physical_snapshot(engine)
    with _reader(engine) as read:
        lookup = lookup_maturity_execution(read, DEMO_USER_ID, body.idempotency_key, mature_at)
    assert lookup.action is not None and lookup.action.status == "UNKNOWN"
    assert not lookup.action.service_receipt_verified
    assert lookup.action.original_request == body
    assert lookup.action.original_event["proof_status"] == "UNKNOWN"
    assert physical_snapshot(engine) == unknown
    with Session(engine) as read:
        operations = list(
            read.scalars(
                select(BankOperation).where(BankOperation.action_plan_id == prepared.action_id)
            )
        )
        assert len(operations) == 1 and operations[0].status == "SETTLED"
        operation_id = operations[0].id
        legs = list(
            read.scalars(
                select(SimulatedBankPosting).where(
                    SimulatedBankPosting.operation_id == operation_id
                )
            )
        )
        assert len(legs) == 2 and sum(row.delta_cents for row in legs) == 0
        assert sorted(row.delta_cents for row in legs) == [
            -bought["executed_cents"],
            bought["executed_cents"],
        ]
    revoked = client.post(
        f"/api/v1/full-policies/{policy_id}/revoke",
        json={
            "expected_version_id": str(body.expected_version_id),
            "reason": "仅恢复已受理原银行键",
            "idempotency_key": "maturity-revoke-after-bank-acceptance",
        },
    )
    assert revoked.status_code == 200, revoked.text
    try:
        done = execute_maturity_execution(
            engine, DEMO_USER_ID, prepared.action_id, execute_body, principal, mature_at
        )
    except PolicyLifecycleError:
        # Retain the actual audit refusal; no synthetic receipt or weakened gate.
        with _reader(engine) as read:
            actual_audit = verify_audit_chain(read, DEMO_USER_ID)
        assert actual_audit.status == "VALID", actual_audit.model_dump_json()
        raise
    assert done.status == "SUCCEEDED" and done.service_receipt_verified
    assert done.original_event["bank_operation_id"] == str(operation_id)
    immutable = physical_snapshot(engine)
    assert (
        execute_maturity_execution(
            engine, DEMO_USER_ID, prepared.action_id, execute_body, principal, mature_at
        )
        == done
    )
    assert physical_snapshot(engine) == immutable
    # Deliberate owned tamper: status/last receipt cannot hide changed consent.
    with Session(engine) as writer, writer.begin():
        original = writer.scalar(
            select(EvidenceItem).where(
                EvidenceItem.source_type == "FULL_MATURITY_USER_CONSENT",
                EvidenceItem.source_ref == str(prepared.action_id),
            )
        )
        assert original is not None
        original.content = {**original.content, "reviewed_command_hash": "0" * 64}
    tampered = physical_snapshot(engine)
    with _reader(engine) as read, pytest.raises(PolicyLifecycleError):
        lookup_maturity_execution(read, DEMO_USER_ID, body.idempotency_key, mature_at)
    assert physical_snapshot(engine) == tampered
