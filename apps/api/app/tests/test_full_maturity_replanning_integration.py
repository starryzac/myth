"""Owned native-PG candidate only. Root runs it; collection is not financial evidence."""

from datetime import timedelta
from uuid import UUID

import pytest
from app.domain.full_maturity_replanning import MaturityReplanningRequest
from app.domain.policy_configuration import configuration_hash
from app.services.action_contracts import PrepareActionRequest, PurchaseIntent
from app.services.audit_chain import current_audit_epoch
from app.services.demo_seed import DEMO_USER_ID
from app.services.execution import execute_action, prepare_action
from app.services.full_maturity_replanning import preview_maturity_replanning
from app.services.policy_lifecycle import confirm_proposal, revoke_policy
from app.services.product_catalog import register_current_catalog
from app.services.recovery import run_recovery
from app.services.user_policy_declaration import UserDeclarationRequest, declare_user_policy
from app.tests.test_goal_api import NOW, all_tables
from app.tests.test_goal_api import goal_client as goal_client
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_actual_contract_return_after_revocation_is_not_current_rollover_authority(
    goal_client: tuple[TestClient, Engine],
) -> None:
    """Use original declaration/confirm/purchase/revoke/maturity; no row/hash edits."""
    _, engine = goal_client
    with Session(engine) as session, session.begin():
        epoch = current_audit_epoch(session, DEMO_USER_ID)
        assert epoch is not None
        epoch_id = epoch.id
        register_current_catalog(session, DEMO_USER_ID, NOW)
        declaration = declare_user_policy(
            session,
            DEMO_USER_ID,
            UserDeclarationRequest(
                configuration={
                    "type": "asset_authorization",
                    "scope": "general_idle_funds",
                    "allowed_asset_classes": ["FIXED_DEPOSIT"],
                    "max_auto_managed_cents": 200000,
                    "single_action_cap_cents": 200000,
                    "max_redemption_delay_days": 1,
                    "max_lock_days": 90,
                    "max_principal_risk_level": 0,
                    "allow_auto_recovery_without_penalty": True,
                    "allow_early_withdrawal_with_penalty": False,
                },
                expected_epoch_id=epoch_id,
                idempotency_key="full406-original-fixed-authorization",
            ),
            NOW,
        )
        confirmed = confirm_proposal(
            session,
            DEMO_USER_ID,
            declaration.proposal_id,
            reviewed_hash=declaration.configuration_hash,
            accepted=True,
            now=NOW,
        )
        policy_id, version_id = confirmed.policy_id, confirmed.current_version_id
    prepared = prepare_action(
        engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="full406-original-fixed-purchase",
            intent=PurchaseIntent(kind="purchase_asset", policy_id=policy_id),
        ),
        NOW,
    )
    assert prepared.effect.purchase_exit is not None
    assert prepared.effect.purchase_exit.kind == "FIXED_MATURITY"
    bought = execute_action(engine, DEMO_USER_ID, prepared.action_id, NOW)
    assert bought.status == "SUCCEEDED" and bought.receipt is not None
    with Session(engine) as session, session.begin():
        revoke_policy(session, DEMO_USER_ID, policy_id, version_id, now=NOW + timedelta(minutes=1))
    # The trusted simulation clock advances to this actual original contract return;
    # clients cannot supply a clock to the new preview. No acquisition/terms are rewritten.
    mature_at = prepared.effect.purchase_exit.principal_available_at + timedelta(minutes=1)
    matured = run_recovery(engine, DEMO_USER_ID, "full406-original-contract-return", mature_at)
    assert matured.actions
    mature_action = next(
        row for row in matured.actions if row.position_id == bought.effect.position_id
    )
    assert mature_action.receipt_id is not None
    before = all_tables(engine)
    body = MaturityReplanningRequest(
        maturity_action_id=mature_action.action_id,
        current_asset_policy_id=policy_id,
        expected_epoch_id=epoch_id,
    )
    with (
        engine.connect().execution_options(
            isolation_level="REPEATABLE READ", postgresql_readonly=True
        ) as connection,
        Session(bind=connection) as session,
        session.begin(),
    ):
        result = preview_maturity_replanning(session, DEMO_USER_ID, body, mature_at)
        again = preview_maturity_replanning(session, DEMO_USER_ID, body, mature_at)
        assert result == again
        assert result.event.service_receipt_verified
        assert result.event.bank_operation_id == UUID(
            result.event.originals["bank_requests"][0]["id"]
        )
        assert result.event.originals_hash == configuration_hash(result.event.originals)
        assert result.current_policy.version_id == version_id
        assert result.decision.state == "BLOCKED" and result.decision.candidate is None
        assert result.event.receipt_is_current_authority is False
        assert not result.current_policy.current_bank_authority_verified
        assert result.decision.received_principal_cents == bought.effect.amount_cents
    assert all_tables(engine) == before
    original_replay = run_recovery(
        engine, DEMO_USER_ID, "full406-original-contract-return", mature_at
    )
    assert original_replay.run_id == matured.run_id
    assert all_tables(engine) == before
