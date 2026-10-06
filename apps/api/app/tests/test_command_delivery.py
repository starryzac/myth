"""Pure message/state/commit-boundary checks; no database or financial proof."""

from copy import deepcopy
from datetime import timedelta
from typing import Any, Literal
from uuid import UUID, uuid5

import pytest
from app.db.full_models import CommandOutbox
from app.db.models import ActionPlan, AuditEpoch
from app.domain.execution import execution_effect_hash, revalidate_execution
from app.domain.execution_types import BankCommand
from app.domain.policy_configuration import configuration_hash
from app.services import command_delivery as delivery
from app.services.action_contracts import ActionReceiptResponse, ActionResponse
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_execution_domain import NOW, OP, USER, context, grant, transfer

EPOCH = UUID(int=100)


def action() -> ActionPlan:
    effect = transfer()
    payload = {
        "intent": {"kind": "transfer_internal"},
        "execution": BankCommand(
            effect=effect, effect_hash=execution_effect_hash(effect)
        ).model_dump(mode="json"),
    }
    return ActionPlan(
        id=OP,
        user_id=USER,
        created_at=NOW,
        idempotency_key="original-test-key",
        request=payload,
        request_hash=configuration_hash(payload),
        status="PLANNED",
    )


def outbox() -> CommandOutbox:
    original = action()
    message = delivery._original_message(original, EPOCH).model_dump(mode="json")
    return CommandOutbox(
        id=uuid5(OP, delivery.PROTOCOL),
        user_id=USER,
        created_at=NOW,
        action_plan_id=OP,
        epoch_id=EPOCH,
        root_id=OP,
        request_hash=original.request_hash,
        effect_hash=message["effect_hash"],
        bank_idempotency_key=original.idempotency_key,
        protocol_version=delivery.PROTOCOL,
        payload=message,
        payload_hash=configuration_hash(message),
        state="PENDING",
        attempt_count=0,
        updated_at=NOW,
    )


def response(
    status: str = "PLANNED", autonomy: str = "AUTO_EXECUTE", bank: str | None = None
) -> ActionResponse:
    effect = transfer()
    return ActionResponse(
        user_id=USER,
        action_id=OP,
        decision_run_id=UUID(int=101),
        status=status,
        autonomy_level=autonomy,
        effect=effect,
        effect_hash=execution_effect_hash(effect),
        prepared_at=NOW,
        as_of=NOW,
        prepared_validation=revalidate_execution(effect, context(), confirmation=grant(effect)),
        bank_status=bank,
    )


def receipt(status: str = "SUCCEEDED") -> ActionReceiptResponse:
    return ActionReceiptResponse(
        receipt_id=uuid5(OP, "receipt"),
        action_id=OP,
        bank_operation_id=OP,
        status=status,
        executed_cents=300,
        fee_cents=0,
        loss_cents=0,
        posting_ids=[UUID(int=102)],
        occurred_at=NOW,
        reconciled_at=NOW,
    )


def test_original_message_copies_only_persisted_identity_and_never_financial_authority() -> None:
    original = action()
    before = deepcopy(original.request)
    message = delivery._original_message(original, EPOCH).model_dump(mode="json")
    assert original.request == before
    assert message["root_id"] == message["action_id"] == message["operation_id"] == str(OP)
    assert message["epoch_id"] == str(EPOCH)
    assert message["request_hash"] == original.request_hash
    assert message["bank_idempotency_key"] == original.idempotency_key
    assert not {"amount", "grant", "confirmation", "authorization", "execution", "receipt"} & set(
        message
    )
    delivery._verify_outbox(outbox(), original)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", UUID(int=200)),
        ("user_id", UUID(int=200)),
        ("action_plan_id", UUID(int=200)),
        ("epoch_id", UUID(int=200)),
        ("root_id", UUID(int=200)),
        ("request_hash", "f" * 64),
        ("effect_hash", "f" * 64),
        ("bank_idempotency_key", "replacement-key"),
        ("protocol_version", "future-protocol"),
        ("payload_hash", "f" * 64),
    ],
)
def test_changed_message_columns_cannot_become_a_new_command(field: str, value: Any) -> None:
    row = outbox()
    setattr(row, field, value)
    with pytest.raises(PolicyLifecycleError, match="消息"):
        delivery._verify_outbox(row, action())


@pytest.mark.parametrize("key", ["authority", "amount_cents", "confirmation", "succeeded"])
def test_rehashed_payload_with_authority_or_result_fields_still_rejects(key: str) -> None:
    row = outbox()
    row.payload = {**row.payload, key: True}
    row.payload_hash = configuration_hash(row.payload)
    with pytest.raises(PolicyLifecycleError):
        delivery._verify_outbox(row, action())


def test_rehashing_rebound_original_action_does_not_match_the_durable_message() -> None:
    original = action()
    original.request = {**original.request, "extra": "changed-intent"}
    original.request_hash = configuration_hash(original.request)
    with pytest.raises(PolicyLifecycleError):
        delivery._verify_outbox(outbox(), original)
    original = action()
    original.request["execution"]["effect"]["amount_cents"] += 1
    original.request_hash = configuration_hash(original.request)
    with pytest.raises(PolicyLifecycleError):
        delivery._original_message(original, EPOCH)


@pytest.mark.parametrize(
    ("status", "autonomy", "bank", "confirmed", "route"),
    [
        ("PLANNED", "AUTO_EXECUTE", None, False, "EXECUTE"),
        ("PLANNED", "ASK_ONCE", None, False, "WAITING_CONFIRMATION"),
        ("AUTHORIZED", "ASK_ONCE", None, True, "EXECUTE"),
        ("AUTHORIZED", "ASK_ONCE", None, False, "WAITING_CONFIRMATION"),
        ("SUBMITTED", "AUTO_EXECUTE", None, False, "EXECUTE"),
        ("UNKNOWN", "AUTO_EXECUTE", None, False, "UNRESOLVED"),
        ("UNKNOWN", "ASK_ONCE", "ACCEPTED", False, "RESUME"),
        ("UNKNOWN", "AUTO_EXECUTE", "SETTLED", False, "RESUME"),
        ("UNKNOWN", "AUTO_EXECUTE", "UNKNOWN", False, "UNRESOLVED"),
        ("PLANNED", "ADVISE_ONLY", None, True, "STOPPED"),
        ("PLANNED", "BLOCKED", None, True, "STOPPED"),
        ("INVALIDATED", "AUTO_EXECUTE", None, True, "STOPPED"),
        ("CANCELLED", "AUTO_EXECUTE", None, True, "STOPPED"),
        ("FAILED", "AUTO_EXECUTE", None, True, "STOPPED"),
        ("SUBMITTED", "AUTO_EXECUTE", "REJECTED", False, "STOPPED"),
        ("SUCCEEDED", "AUTO_EXECUTE", "SETTLED", True, "UNRESOLVED"),
        ("RECONCILED", "AUTO_EXECUTE", "SETTLED", True, "UNRESOLVED"),
        ("unrecognized-status", "AUTO_EXECUTE", None, True, "STOPPED"),
    ],
)
def test_current_actual_status_and_bank_query_determine_delivery_route(
    status: str, autonomy: str, bank: str | None, confirmed: bool, route: str
) -> None:
    assert (
        delivery.delivery_route(response(status, autonomy, bank), confirmation_present=confirmed)
        == route
    )


@pytest.mark.parametrize("status", ["SUCCEEDED", "RECONCILED"])
def test_verified_route_requires_matching_actual_success_receipt(status: str) -> None:
    base = response(status, bank="SETTLED")
    assert (
        delivery.delivery_route(
            base.model_copy(update={"receipt": receipt()}), confirmation_present=False
        )
        == "VERIFIED"
    )
    for bad in (
        receipt("FAILED"),
        receipt("UNKNOWN"),
        receipt().model_copy(update={"action_id": UUID(int=900)}),
        receipt().model_copy(update={"bank_operation_id": UUID(int=900)}),
    ):
        assert (
            delivery.delivery_route(
                base.model_copy(update={"receipt": bad}), confirmation_present=False
            )
            == "UNRESOLVED"
        )


def test_clock_and_lock_identity_are_aware_stable_and_user_scoped() -> None:
    assert delivery._clock(NOW + timedelta(seconds=1)) == NOW + timedelta(seconds=1)
    with pytest.raises(PolicyLifecycleError):
        delivery._clock(NOW.replace(tzinfo=None))
    assert delivery.advisory_key(USER) == delivery.advisory_key(USER)
    assert delivery.advisory_key(USER) != delivery.advisory_key(UUID(int=501))


class FakeProducerSession:
    def __init__(self) -> None:
        self.existing = outbox()

    def in_transaction(self) -> bool:
        return True

    def scalar(self, statement: Any) -> CommandOutbox:
        return self.existing


def test_audit_append_wall_clock_does_not_reject_original_business_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeProducerSession()
    epoch = AuditEpoch(id=EPOCH, user_id=USER, status="OPEN", created_at=NOW + timedelta(days=365))
    monkeypatch.setattr(delivery, "current_audit_epoch", lambda *_: epoch)
    original_hash = session.existing.payload_hash
    actual = delivery.enqueue_action_in_transaction(session, action(), NOW)  # type: ignore[arg-type]
    assert actual is session.existing
    assert actual.payload_hash == original_hash
    assert actual.epoch_id == EPOCH


def test_future_business_action_is_still_rejected_before_publication() -> None:
    original = action()
    original.created_at = NOW + timedelta(seconds=1)
    with pytest.raises(PolicyLifecycleError) as captured:
        delivery.enqueue_action_in_transaction(FakeProducerSession(), original, NOW)  # type: ignore[arg-type]
    assert captured.value.code == "INVALID_DELIVERY_TRANSACTION"


def test_missing_current_owned_open_epoch_still_rejects_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(delivery, "current_audit_epoch", lambda *_: None)
    with pytest.raises(PolicyLifecycleError) as captured:
        delivery.enqueue_action_in_transaction(FakeProducerSession(), action(), NOW)  # type: ignore[arg-type]
    assert captured.value.code == "INVALID_DELIVERY_EPOCH"


class FakeConnection:
    def __init__(self, acquired: Any = True, unlock: Any = True) -> None:
        self.acquired, self.unlock = acquired, unlock
        self.statements: list[str] = []
        self.invalidated = False

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, *args: Any) -> Literal[False]:
        return False

    def execution_options(self, **options: Any) -> "FakeConnection":
        assert options == {"isolation_level": "AUTOCOMMIT"}
        return self

    def scalar(self, statement: Any, bindings: dict[str, Any]) -> Any:
        assert bindings == {"key": delivery.advisory_key(USER)}
        self.statements.append(str(statement))
        return self.acquired if len(self.statements) == 1 else self.unlock

    def invalidate(self) -> None:
        self.invalidated = True


class FakeEngine:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def connect(self) -> FakeConnection:
        return self.connection


@pytest.mark.parametrize("acquired", [True, False])
def test_session_lock_has_no_user_row_lock_and_busy_does_not_unlock_another_worker(
    acquired: bool,
) -> None:
    connection = FakeConnection(acquired)
    with delivery._delivery_lock(FakeEngine(connection), USER) as actual:  # type: ignore[arg-type]
        assert actual is acquired
        assert len(connection.statements) == 1
    assert len(connection.statements) == (2 if acquired else 1)
    assert all("FOR UPDATE" not in sql for sql in connection.statements)


def test_lock_cleanup_failure_invalidates_connection_and_preserves_original_cause() -> None:
    connection = FakeConnection(unlock=False)
    original = RuntimeError("original-failure")
    with pytest.raises(RuntimeError) as captured:
        with delivery._delivery_lock(FakeEngine(connection), USER):  # type: ignore[arg-type]
            raise original
    assert captured.value is original
    assert connection.invalidated is True
    assert "Delivery lock cleanup" in captured.value.__notes__[0]


def test_unlock_failure_without_business_failure_is_visible_not_a_success() -> None:
    connection = FakeConnection(unlock=False)
    with pytest.raises(PolicyLifecycleError):
        with delivery._delivery_lock(FakeEngine(connection), USER):  # type: ignore[arg-type]
            pass
    assert connection.invalidated is True


@pytest.mark.parametrize("invalid", [None, 1, "true"])
def test_nonboolean_lock_response_is_rejected(invalid: Any) -> None:
    connection = FakeConnection(acquired=invalid)
    with pytest.raises(PolicyLifecycleError):
        with delivery._delivery_lock(FakeEngine(connection), USER):  # type: ignore[arg-type]
            pytest.fail("Invalid lock cannot enter delivery")
