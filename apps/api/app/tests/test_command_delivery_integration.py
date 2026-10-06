"""Focused real PG delivery risks; root runs them only on generated bf_test databases."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.db.full_models import CommandDeliveryAttempt, CommandInbox, CommandOutbox
from app.db.models import (
    Account,
    ActionPlan,
    ActionReceipt,
    AuditEpoch,
    BankOperation,
    SimulatedBankPosting,
)
from app.db.session import create_database_engine
from app.services import command_delivery as delivery
from app.services import execution
from app.services.action_contracts import (
    ConfirmActionRequest,
    PrepareActionRequest,
    PurchaseIntent,
    TransferIntent,
)
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, seed_demo
from app.services.execution_bank import BankOperationResult, process_operation
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_asset_allocation_service import authorization
from app.tests.test_boundary_service import boundary_engine, confirmed_policy, snapshot
from app.tests.test_execution_service import transfer_accounts
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

__all__ = ["boundary_engine"]
pytestmark = pytest.mark.integration


class InjectedCrash(BaseException):
    """Abrupt-control fixture skips normal exception bookkeeping, like a dead consumer."""


def prepare(engine: Engine, *, confirmed: bool = True) -> tuple[UUID, UUID, UUID, UUID]:
    source, target = transfer_accounts(engine)
    request = PrepareActionRequest(
        idempotency_key=f"delivery-risk-{uuid4()}",
        intent=TransferIntent(
            kind="transfer_internal",
            source_account_id=source,
            destination_account_id=target,
            amount_cents=10000,
        ),
    )
    action = execution.prepare_action(engine, DEMO_USER_ID, request, SEED_AS_OF)
    if confirmed:
        execution.confirm_action(
            engine,
            DEMO_USER_ID,
            action.action_id,
            ConfirmActionRequest(effect_hash=action.effect_hash, accepted=True),
            SEED_AS_OF,
        )
    with Session(engine) as session:
        row = session.scalar(
            select(CommandOutbox).where(CommandOutbox.action_plan_id == action.action_id)
        )
        assert row is not None
        persisted = session.get(ActionPlan, action.action_id)
        assert persisted is not None and row.request_hash == persisted.request_hash
        assert row.effect_hash == action.effect_hash
        outbox_id = row.id
    return outbox_id, action.action_id, source, target


def economic_rows(engine: Engine) -> dict[str, Any]:
    """Read actual independent bank and account rows, without manufacturing an oracle."""
    with Session(engine) as session:
        return {
            "balances": {row.id: row.balance_cents for row in session.scalars(select(Account))},
            "bank_operations": {
                row.id: (row.status, row.idempotency_key, row.request_hash)
                for row in session.scalars(select(BankOperation))
            },
            "postings": {
                row.id: (row.operation_id, row.ledger_key, row.delta_cents, row.balance_after_cents)
                for row in session.scalars(select(SimulatedBankPosting))
            },
            "receipts": {
                row.id: (row.action_plan_id, row.status, row.executed_cents)
                for row in session.scalars(select(ActionReceipt))
            },
        }


def test_outbox_and_prepared_action_rollback_together_before_prepare_commit(
    boundary_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, target = transfer_accounts(boundary_engine)
    before = snapshot(boundary_engine)
    original = delivery.enqueue_action_in_transaction

    def failure(session: Session, action: ActionPlan, now: datetime) -> CommandOutbox:
        original(session, action, now)
        raise RuntimeError("injected-after-outbox-before-prepare-commit")

    with monkeypatch.context() as patch:
        patch.setattr(delivery, "enqueue_action_in_transaction", failure)
        with pytest.raises(RuntimeError, match="injected-after-outbox"):
            execution.prepare_action(
                boundary_engine,
                DEMO_USER_ID,
                PrepareActionRequest(
                    idempotency_key="atomic-delivery-rollback",
                    intent=TransferIntent(
                        kind="transfer_internal",
                        source_account_id=source,
                        destination_account_id=target,
                        amount_cents=10000,
                    ),
                ),
                SEED_AS_OF,
            )
    assert snapshot(boundary_engine) == before


def test_ask_waits_for_current_original_confirmation_and_duplicate_delivery_is_single_effect(
    boundary_engine: Engine,
) -> None:
    outbox_id, action_id, source, target = prepare(boundary_engine, confirmed=False)
    before = economic_rows(boundary_engine)
    pending = delivery.deliver_command(boundary_engine, DEMO_USER_ID, outbox_id, SEED_AS_OF)
    assert pending.inbox_state == "WAITING_CONFIRMATION" and not pending.service_receipt_verified
    assert economic_rows(boundary_engine) == before
    with Session(boundary_engine) as session:
        action = execution.get_action(session, DEMO_USER_ID, action_id, SEED_AS_OF)
        stored = session.get(ActionPlan, action_id)
        assert stored is not None
        hash_before = stored.request_hash
    execution.confirm_action(
        boundary_engine,
        DEMO_USER_ID,
        action_id,
        ConfirmActionRequest(effect_hash=action.effect_hash, accepted=True),
        SEED_AS_OF,
    )
    completed = delivery.deliver_command(boundary_engine, DEMO_USER_ID, outbox_id, SEED_AS_OF)
    assert completed.inbox_state == "SERVICE_RECEIPT_VERIFIED"
    assert completed.outbox_state == "DELIVERED" and completed.service_receipt_verified
    assert completed.economic_verified is False and len(completed.attempts) == 2
    settled = economic_rows(boundary_engine)
    assert settled["balances"][source] == before["balances"][source] - 10000
    assert settled["balances"][target] == before["balances"][target] + 10000
    assert len(settled["bank_operations"]) == len(settled["receipts"]) == 1
    replay = delivery.deliver_command(
        boundary_engine, DEMO_USER_ID, outbox_id, SEED_AS_OF + timedelta(days=1)
    )
    assert len(replay.attempts) == 2 and replay.service_receipt_verified
    assert economic_rows(boundary_engine) == settled
    with Session(boundary_engine) as session:
        persisted = session.get(ActionPlan, action_id)
        assert persisted is not None and persisted.request_hash == hash_before


@pytest.mark.parametrize("boundary", ["after_receive", "before_ack"])
def test_new_consumer_connection_resumes_durable_attempt_after_crash_without_new_bank_key(
    boundary_engine: Engine, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    outbox_id, action_id, _, _ = prepare(boundary_engine)

    def crash(*args: Any, **kwargs: Any) -> Any:
        raise InjectedCrash(f"injected-{boundary}")

    with monkeypatch.context() as patch:
        patch.setattr(delivery, "_start" if boundary == "after_receive" else "_finish", crash)
        with pytest.raises(InjectedCrash):
            delivery.deliver_command(boundary_engine, DEMO_USER_ID, outbox_id, SEED_AS_OF)
    with Session(boundary_engine) as session:
        attempt = session.scalars(select(CommandDeliveryAttempt)).one()
        assert attempt.state == ("RECEIVED" if boundary == "after_receive" else "PROCESSING")
        assert attempt.finished_at is None
        prior_attempt = attempt.id
        assert len(list(session.scalars(select(BankOperation)))) == (
            0 if boundary == "after_receive" else 1
        )
    original_economics = economic_rows(boundary_engine) if boundary == "before_ack" else None
    restarted = create_database_engine(boundary_engine.url.render_as_string(hide_password=False))
    try:
        resumed = delivery.deliver_command(restarted, DEMO_USER_ID, outbox_id, SEED_AS_OF)
        assert resumed.service_receipt_verified and resumed.economic_verified is False
        assert len(resumed.attempts) == 2 and resumed.attempts[0].attempt_id == prior_attempt
        assert resumed.attempts[0].finished_at is None
        with Session(restarted) as session:
            bank = session.scalars(select(BankOperation)).one()
            assert bank.id == action_id
            persisted = session.get(ActionPlan, action_id)
            assert persisted is not None and bank.idempotency_key == persisted.idempotency_key
        if original_economics is not None:
            assert economic_rows(restarted) == original_economics
    finally:
        restarted.dispose()


@pytest.mark.parametrize("failure", ["lost_bank_response", "projection_failed"])
def test_unknown_with_committed_bank_resumes_original_operation_and_retains_failed_attempt(
    boundary_engine: Engine, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    outbox_id, action_id, _, _ = prepare(boundary_engine)

    def lost(engine: Engine, owner: UUID, action: UUID, now: datetime) -> BankOperationResult:
        process_operation(engine, owner, action, now)
        raise RuntimeError("injected-original-bank-response-loss")

    def failed_projection(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("injected-original-projection-rollback")

    with monkeypatch.context() as patch:
        patch.setattr(
            execution,
            "process_operation" if failure == "lost_bank_response" else "project_execution",
            lost if failure == "lost_bank_response" else failed_projection,
        )
        with pytest.raises(RuntimeError, match="injected-original"):
            delivery.deliver_command(boundary_engine, DEMO_USER_ID, outbox_id, SEED_AS_OF)
    with Session(boundary_engine) as session:
        before = delivery.get_delivery(session, DEMO_USER_ID, outbox_id, SEED_AS_OF)
        assert before.source_action_status == "UNKNOWN"
        assert before.inbox_state == "UNRESOLVED" and before.bank_status == "SETTLED"
        assert not before.service_receipt_verified
        original_postings = {row.id for row in session.scalars(select(SimulatedBankPosting))}
    repaired = delivery.deliver_command(boundary_engine, DEMO_USER_ID, outbox_id, SEED_AS_OF)
    assert repaired.service_receipt_verified and repaired.economic_verified is False
    assert (
        repaired.attempts[0].state == "UNRESOLVED" and repaired.attempts[0].error == "RuntimeError"
    )
    with Session(boundary_engine) as session:
        assert session.scalars(select(BankOperation)).one().id == action_id
        assert {
            row.id for row in session.scalars(select(SimulatedBankPosting))
        } == original_postings
        assert len(list(session.scalars(select(ActionReceipt)))) == 1


def test_unknown_without_bank_does_not_blindly_submit_money_on_redelivery(
    boundary_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    outbox_id, action_id, _, _ = prepare(boundary_engine)
    initial = economic_rows(boundary_engine)

    def fail_before_bank(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("injected-before-any-bank-operation")

    with monkeypatch.context() as patch:
        patch.setattr(execution, "process_operation", fail_before_bank)
        with pytest.raises(RuntimeError):
            delivery.deliver_command(boundary_engine, DEMO_USER_ID, outbox_id, SEED_AS_OF)
    with Session(boundary_engine) as session:
        assert (
            execution.get_action(session, DEMO_USER_ID, action_id, SEED_AS_OF).status == "UNKNOWN"
        )
    retried = delivery.deliver_command(boundary_engine, DEMO_USER_ID, outbox_id, SEED_AS_OF)
    assert retried.inbox_state == "UNRESOLVED" and retried.outbox_state == "PENDING"
    assert retried.bank_status is None and not retried.service_receipt_verified
    assert economic_rows(boundary_engine) == initial


def test_busy_and_other_owner_have_no_attempt_or_financial_side_effect(
    boundary_engine: Engine,
) -> None:
    outbox_id, _, _, _ = prepare(boundary_engine)
    before = snapshot(boundary_engine)
    with boundary_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        key = delivery.advisory_key(DEMO_USER_ID)
        connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
        try:
            busy = delivery.deliver_command(boundary_engine, DEMO_USER_ID, outbox_id, SEED_AS_OF)
            assert busy.busy and busy.attempts == []
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
    assert snapshot(boundary_engine) == before
    with pytest.raises(PolicyLifecycleError) as wrong_owner:
        delivery.deliver_command(boundary_engine, uuid4(), outbox_id, SEED_AS_OF)
    assert wrong_owner.value.status_code == 404 and snapshot(boundary_engine) == before


def test_reset_retains_durable_original_message_and_archived_epoch_cannot_recreate_action(
    boundary_engine: Engine,
) -> None:
    outbox_id, action_id, _, _ = prepare(boundary_engine, confirmed=False)
    with Session(boundary_engine) as session:
        original = session.get(CommandOutbox, outbox_id)
        assert original is not None
        raw = (original.payload, original.payload_hash, original.epoch_id, original.request_hash)
    seed_demo(boundary_engine, reset_key=f"delivery-retained-{uuid4().hex}")
    now = datetime.now(UTC)
    before = economic_rows(boundary_engine)
    result = delivery.deliver_command(boundary_engine, DEMO_USER_ID, outbox_id, now)
    assert result.inbox_state == "STOPPED" and result.blocking_reason == "NO_CURRENT_ACTION"
    assert not result.current_action_available and not result.service_receipt_verified
    assert economic_rows(boundary_engine) == before
    with Session(boundary_engine) as session:
        retained = session.get(CommandOutbox, outbox_id)
        assert retained is not None
        assert (
            retained.payload,
            retained.payload_hash,
            retained.epoch_id,
            retained.request_hash,
        ) == raw
        assert session.get(ActionPlan, action_id) is None
        epoch = session.get(AuditEpoch, retained.epoch_id)
        assert epoch is not None and epoch.status == "SEALED"
        assert len(list(session.scalars(select(CommandInbox)))) == 1


def test_auto_worker_uses_current_original_policy_and_real_bank_pipeline(
    boundary_engine: Engine,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        policy_id, _ = confirmed_policy(session, authorization())
    action = execution.prepare_action(
        boundary_engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="delivery-auto-purchase",
            intent=PurchaseIntent(kind="purchase_asset", policy_id=policy_id),
        ),
        SEED_AS_OF,
    )
    assert action.autonomy_level == "AUTO_EXECUTE" and action.prepared_validation.status == "READY"
    with Session(boundary_engine) as session:
        message = session.scalar(
            select(CommandOutbox).where(CommandOutbox.action_plan_id == action.action_id)
        )
        assert message is not None
        outbox_id = message.id
    result = delivery.deliver_command(boundary_engine, DEMO_USER_ID, outbox_id, SEED_AS_OF)
    assert result.service_receipt_verified and result.economic_verified is False
    with Session(boundary_engine) as session:
        bank = session.scalars(select(BankOperation)).one()
        assert bank.id == action.action_id and bank.status == "SETTLED"
