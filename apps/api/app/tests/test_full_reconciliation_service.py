"""Only synthetic ORM doubles/call-routing risks; actual PG is a separate node."""

from contextlib import nullcontext
from datetime import timedelta
from typing import Any, cast
from uuid import UUID

import pytest
from app.db.models import ActionPlan, ActionReceipt, BankOperation, SimulatedBankPosting, User
from app.domain.audit_chain_types import AuditVerification
from app.domain.execution import execution_effect_hash
from app.domain.execution_types import BankCommand, CashUse, ExecutionEffect
from app.domain.full_reconciliation import ReconciliationInventory, ReconciliationIssue
from app.domain.policy_configuration import configuration_hash
from app.services import full_reconciliation as service
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_full_reconciliation import ENTITY, NOW, USER
from sqlalchemy.orm import Session


def command() -> BankCommand:
    effect = ExecutionEffect(
        operation_id=ENTITY,
        user_id=USER,
        business_key=f"synthetic:{ENTITY}",
        action_type="TRANSFER_INTERNAL",
        amount_cents=107,
        cash_uses=[CashUse(account_id=UUID(int=7004), amount_cents=107)],
        destination_account_id=UUID(int=7005),
        valid_from=NOW,
        expires_at=NOW + timedelta(minutes=15),
    )
    return BankCommand(effect=effect, effect_hash=execution_effect_hash(effect))


def rows(status: str = "SETTLED") -> tuple[ActionPlan, BankOperation, ActionReceipt]:
    bank_command = command()
    request = {"execution": bank_command.model_dump(mode="json")}
    action = ActionPlan(
        id=ENTITY,
        user_id=USER,
        created_at=NOW,
        action_type="TRANSFER_INTERNAL",
        status="UNKNOWN",
        idempotency_key="action:actual-original-shape",
        amount_cents=107,
        request=request,
        request_hash=configuration_hash(request),
    )
    operation = BankOperation(
        id=ENTITY,
        user_id=USER,
        created_at=NOW,
        action_plan_id=ENTITY,
        legacy_redemption_id=None,
        operation_type="TRANSFER_INTERNAL",
        business_key=bank_command.effect.business_key,
        idempotency_key=action.idempotency_key,
        request=bank_command.model_dump(mode="json"),
        request_hash=configuration_hash(bank_command.model_dump(mode="json")),
        status=status,
        requested_at=NOW,
        available_at=NOW,
        settled_at=NOW if status == "SETTLED" else None,
    )
    receipt = ActionReceipt(
        id=UUID(int=7006),
        user_id=USER,
        created_at=NOW,
        action_plan_id=ENTITY,
        status="SUCCEEDED",
        response={},
        attempt_number=1,
        executed_cents=107,
        fee_cents=0,
        loss_cents=0,
        occurred_at=NOW,
    )
    return action, operation, receipt


def route(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def identity(*args: Any, **kwargs: Any) -> Any:
        assert kwargs == {"lock_user": False}
        calls.append("original_identity_no_lock")
        return rows()[0], command().effect

    def legs(*args: Any) -> list[SimulatedBankPosting]:
        calls.append("original_complete_legs")
        return []

    def receipt(*args: Any) -> None:
        calls.append("original_receipt_verifier")

    monkeypatch.setattr(service, "_identity", identity)
    monkeypatch.setattr(service, "_legs", legs)
    monkeypatch.setattr(service, "verify_execution_receipt", receipt)
    return calls


def read_action(
    action: ActionPlan,
    operation: BankOperation | None,
    receipt: ActionReceipt | None,
    *,
    complete: bool = True,
    ledger: bool = True,
) -> Any:
    return service._action(
        cast(Session, object()),
        action,
        [operation] if operation else [],
        [],
        [receipt] if receipt else [],
        [],
        NOW,
        inventory_complete=complete,
        ledger_verified=ledger,
    )


@pytest.mark.parametrize("wrong", ["entity", "dimension"])
def test_equal_amount_from_wrong_bank_head_preserves_raw_ref_but_never_matches(wrong: str) -> None:
    key = f"CASH:{ENTITY}"
    head = SimulatedBankPosting(
        id=UUID(int=7010),
        user_id=USER,
        account_id=ENTITY if wrong == "dimension" else UUID(int=7011),
        position_id=None,
        ledger_key=key,
        ledger_dimension="GOAL_OWNERSHIP" if wrong == "dimension" else "ECONOMIC",
        ledger_metadata={},
        sequence_number=1,
        balance_after_cents=107,
        occurred_at=NOW,
    )
    issues: list[ReconciliationIssue] = []
    value = service._amount(ENTITY, "ACCOUNT_CASH", 107, {key: head}, key, NOW, issues)
    assert value.state == "MISSING" and value.bank_cents is value.difference_cents is None
    assert value.bank_head is not None and value.bank_head.posting_id == head.id
    assert issues[0].code == "BANK_HEAD_IDENTITY_MISMATCH" and issues[0].kind == "INTEGRITY"


def test_goal_bank_ownership_must_bind_actual_goal_account_not_only_goal_id() -> None:
    key, account_id = f"GOAL_CASH:{ENTITY}", UUID(int=7012)
    head = SimulatedBankPosting(
        id=UUID(int=7010),
        user_id=USER,
        account_id=account_id,
        position_id=None,
        ledger_key=key,
        ledger_dimension="GOAL_OWNERSHIP",
        ledger_metadata={"goal_id": str(ENTITY), "account_id": str(account_id)},
        sequence_number=1,
        balance_after_cents=107,
        occurred_at=NOW,
    )
    issues: list[ReconciliationIssue] = []
    assert (
        service._amount(
            ENTITY, "GOAL_CASH", 107, {key: head}, key, NOW, issues, goal_account_id=account_id
        ).state
        == "MATCHED"
    )
    value = service._amount(
        ENTITY, "GOAL_CASH", 107, {key: head}, key, NOW, issues, goal_account_id=UUID(int=7013)
    )
    assert value.state == "MISSING" and value.bank_cents is None
    assert any(issue.code == "BANK_HEAD_IDENTITY_MISMATCH" for issue in issues)


def test_actual_bank_settled_missing_receipt_keeps_original_unknown_and_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = route(monkeypatch)
    action, operation, _ = rows()
    value = read_action(action, operation, None)
    assert value.state == "BANK_SETTLED_APPLICATION_UNRESOLVED"
    assert value.original_action_status == action.status == "UNKNOWN"
    assert value.original_idempotency_key == action.idempotency_key
    assert value.actual_executed_cents == 107 and value.service_receipt_verified is False
    assert value.complete_settlement_legs_verified and calls == [
        "original_identity_no_lock",
        "original_complete_legs",
    ]
    assert value.retry_or_repair_performed is False


def test_service_receipt_requires_both_original_verifiers_not_success_strings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = route(monkeypatch)
    action, operation, receipt = rows()
    value = read_action(action, operation, receipt)
    assert value.state == "SERVICE_RECEIPT_VERIFIED" and value.service_receipt_verified
    assert calls == [
        "original_identity_no_lock",
        "original_complete_legs",
        "original_receipt_verifier",
    ]


@pytest.mark.parametrize("function", ["_identity", "_legs", "verify_execution_receipt"])
def test_original_integrity_failure_requires_review_and_never_financial_success(
    monkeypatch: pytest.MonkeyPatch, function: str
) -> None:
    route(monkeypatch)

    def fail(*args: Any, **kwargs: Any) -> None:
        raise PolicyLifecycleError(
            "BANK_RECONCILIATION_REQUIRED", "synthetic actual verifier failure", 409
        )

    monkeypatch.setattr(service, function, fail)
    action, operation, receipt = rows()
    value = read_action(action, operation, receipt)
    assert value.state == "MANUAL_REVIEW_REQUIRED" and not value.service_receipt_verified
    assert value.issues[-1].kind == "INTEGRITY"


@pytest.mark.parametrize("status", ["ACCEPTED", "UNKNOWN"])
def test_pending_original_bank_is_never_failed_or_zero_effect(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    calls = route(monkeypatch)
    action, operation, _ = rows(status)
    value = read_action(action, operation, None)
    assert value.state == "PENDING_BANK" and value.actual_executed_cents is None
    assert calls == ["original_identity_no_lock"]


def test_settled_string_without_ledger_original_does_not_invoke_receipt_or_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = route(monkeypatch)
    action, operation, receipt = rows()
    value = read_action(action, operation, receipt, ledger=False)
    assert value.state == "UNKNOWN" and value.actual_executed_cents is None
    assert value.complete_settlement_legs_verified is False and not value.service_receipt_verified
    assert calls == ["original_identity_no_lock"]


def test_rejected_original_bank_row_and_zero_legs_are_distinct_from_unknown_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = route(monkeypatch)
    action, operation, _ = rows("REJECTED")
    value = read_action(action, operation, None)
    assert value.state == "BANK_REJECTION_VERIFIED" and value.actual_executed_cents == 0
    assert not value.service_receipt_verified and calls == ["original_identity_no_lock"]


def test_unimplemented_full_action_preserves_all_raw_refs_and_stays_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = route(monkeypatch)
    action, operation, receipt = rows()
    action.action_type = "TRANSFER_TO_CONFIRMED_PAYEE"
    value = read_action(action, operation, receipt)
    assert value.state == "UNKNOWN" and value.actual_executed_cents is None
    assert value.bank_operation_ids == [operation.id] and value.receipt_ids == [receipt.id]
    assert value.issues[0].code == "ACTION_TYPE_NOT_SUPPORTED" and not calls


def test_missing_bank_unknown_is_not_final_and_capacity_preserves_row_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route(monkeypatch)
    action, operation, receipt = rows()
    absent = read_action(action, None, None)
    assert absent.state == "NO_EFFECT_OBSERVED_NOT_FINAL" and absent.actual_executed_cents is None
    incomplete = read_action(action, operation, receipt, complete=False)
    assert incomplete.state == "UNKNOWN" and incomplete.receipt_ids == [receipt.id]
    assert incomplete.issues[0].code == "ACTION_INVENTORY_INCOMPLETE"


def test_duplicate_bank_or_receipt_never_counts_as_success(monkeypatch: pytest.MonkeyPatch) -> None:
    route(monkeypatch)
    action, operation, receipt = rows()
    value = service._action(
        cast(Session, object()),
        action,
        [operation, operation],
        [],
        [receipt, receipt],
        [],
        NOW,
        inventory_complete=True,
        ledger_verified=True,
    )
    assert value.state == "MANUAL_REVIEW_REQUIRED" and not value.service_receipt_verified


def test_original_request_hash_or_effect_owner_mismatch_is_retained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route(monkeypatch)
    action, operation, receipt = rows()
    action.request_hash = "0" * 64
    value = read_action(action, operation, receipt)
    assert value.state == "MANUAL_REVIEW_REQUIRED"
    assert any(row.code == "ACTION_REQUEST_HASH_MISMATCH" for row in value.issues)


class EmptySession:
    no_autoflush = nullcontext()

    def scalar(self, query: Any) -> User:
        return User(id=USER, is_simulated=True)


def setup(monkeypatch: pytest.MonkeyPatch) -> Session:
    """Synthetic API/report shape only; all verifiers are explicit doubles."""
    session = cast(Session, EmptySession())
    monkeypatch.setattr(service, "_read_snapshot", lambda session: None)
    monkeypatch.setattr(service, "historical_ledger_scope", lambda session: nullcontext())
    monkeypatch.setattr(
        service,
        "_load",
        lambda session, model, user, limit: (
            [],
            ReconciliationInventory(
                table=model.__tablename__, actual_count=0, captured_count=0, complete=True
            ),
        ),
    )
    monkeypatch.setattr(
        service,
        "verify_audit_chain",
        lambda *args, **kwargs: AuditVerification(
            user_id=USER,
            epoch_id=UUID(int=7007),
            status="VALID",
            chain_status="VALID",
            reference_status="VALID",
            checkpoint_status="NOT_REQUESTED",
            actual_count=0,
            expected_count=0,
            actual_tail_id=None,
            actual_tail_hash=None,
            expected_tail_id=None,
            expected_tail_hash=None,
            verified_through_sequence=0,
        ),
    )
    monkeypatch.setattr(service, "ledger_heads", lambda session, user: {})
    monkeypatch.setattr(service, "validate_bank_projection", lambda *args, **kwargs: None)
    return session


def test_complete_synthetic_report_never_claims_economic_proof_or_current_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = setup(monkeypatch)
    value = service.full_reconciliation(session, USER, NOW)
    assert value.state == "MATCHED"
    assert len(value.inventory) == 8 and all(row.complete for row in value.inventory)
    assert (
        not value.economic_verified
        and not value.executes_funds
        and not value.repairs_performed
        and not value.grants_authority
    )


def test_capacity_retains_actual_denominator_and_never_verifies_partial_bank_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = setup(monkeypatch)

    def load(session: Session, model: Any, user_id: UUID, limit: int) -> Any:
        return [], ReconciliationInventory(
            table=model.__tablename__, actual_count=limit + 1, captured_count=0, complete=False
        )

    def never(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("partial snapshot must never be verified")

    monkeypatch.setattr(service, "_load", load)
    monkeypatch.setattr(service, "ledger_heads", never)
    monkeypatch.setattr(service, "validate_bank_projection", never)
    value = service.full_reconciliation(session, USER, NOW)
    assert value.state == "UNKNOWN" and not value.bank_ledger_verified
    assert len(value.inventory) == 8 and len(value.uncovered) == 8
    assert value.inventory[-1].actual_count == service.POSTING_LIMIT + 1


def test_readonly_scope_or_non_simulated_owner_rejects_before_financial_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = setup(monkeypatch)

    def reject(session: Session) -> None:
        raise PolicyLifecycleError("INVALID_READ_SNAPSHOT", "requires actual RRRO", 409)

    monkeypatch.setattr(service, "_read_snapshot", reject)
    with pytest.raises(PolicyLifecycleError, match="RRRO"):
        service.full_reconciliation(session, USER, NOW)
