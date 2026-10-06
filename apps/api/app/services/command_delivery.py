"""Durable local delivery of original simulated actions; messages grant no authority.

Receiving, processing, the existing three financial phases, and acknowledging commit
separately. A session advisory lock spans these phases without holding a user row lock.
There is no automatic background worker and no alternative bank execution pipeline.
"""

import hashlib
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4, uuid5

from app.db.audit_guard import audit_command_guard, transaction_gate
from app.db.full_models import CommandDeliveryAttempt, CommandInbox, CommandOutbox
from app.db.models import ActionPlan, AuditEpoch, BankOperation, User
from app.domain.execution import execution_effect_hash
from app.domain.execution_types import BankCommand
from app.domain.policy_configuration import configuration_hash
from app.services.action_contracts import ActionResponse
from app.services.audit_chain import current_audit_epoch
from app.services.execution import execute_action, get_action
from app.services.execution_sources import read_execution_confirmation
from app.services.policy_lifecycle import PolicyLifecycleError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

PROTOCOL = "action-delivery-v1"
CONSUMER = "original-simulated-execution-v1"
InboxState = Literal[
    "RECEIVED",
    "PROCESSING",
    "WAITING_CONFIRMATION",
    "UNRESOLVED",
    "SERVICE_RECEIPT_VERIFIED",
    "STOPPED",
    "FAILED",
]
DeliveryRoute = Literal[
    "EXECUTE", "RESUME", "WAITING_CONFIRMATION", "UNRESOLVED", "STOPPED", "VERIFIED"
]


class OriginalActionMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    protocol: Literal["action-delivery-v1"] = "action-delivery-v1"
    user_id: UUID
    action_id: UUID
    epoch_id: UUID
    root_id: UUID
    operation_id: UUID
    request_hash: str = Field(pattern="^[0-9a-f]{64}$")
    effect_hash: str = Field(pattern="^[0-9a-f]{64}$")
    bank_idempotency_key: str = Field(min_length=1, max_length=160)


class AttemptView(BaseModel):
    attempt_id: UUID
    attempt_number: int
    state: str
    started_at: datetime
    finished_at: datetime | None
    source_action_status: str | None
    result: dict[str, Any] | None
    error: str | None


class DeliveryView(BaseModel):
    simulation: Literal[True] = True
    outbox_id: UUID
    root_id: UUID
    action_id: UUID
    epoch_id: UUID
    payload_hash: str
    outbox_state: str
    inbox_state: str | None
    source_action_status: str
    bank_status: str | None
    current_action_available: bool
    blocking_reason: str | None
    service_receipt_verified: bool
    economic_verified: Literal[False] = False
    attempts: list[AttemptView]
    last_error: str | None
    busy: bool = False


class DeliveryList(BaseModel):
    simulation: Literal[True] = True
    items: list[DeliveryView]
    economic_verified: Literal[False] = False


def _error(code: str, message: str) -> PolicyLifecycleError:
    return PolicyLifecycleError(code, message, 409)


def _clock(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise _error("INVALID_CLOCK", "命令投递需要带时区的服务器时间")
    return now.astimezone(UTC)


def _lock_owner(session: Session, user_id: UUID) -> None:
    transaction_gate(session, user_id)
    owner = session.scalar(select(User).where(User.id == user_id).with_for_update())
    if owner is None or not owner.is_simulated:
        raise PolicyLifecycleError("NOT_FOUND", "模拟用户不存在", 404)


def _original_message(action: ActionPlan, epoch_id: UUID) -> OriginalActionMessage:
    try:
        if configuration_hash(action.request) != action.request_hash:
            raise ValueError("Changed original request")
        command = BankCommand.model_validate_json(json.dumps(action.request["execution"]))
        effect = command.effect
        if (
            effect.user_id != action.user_id
            or effect.operation_id != action.id
            or execution_effect_hash(effect) != command.effect_hash
        ):
            raise ValueError("Changed original effect")
        return OriginalActionMessage(
            user_id=action.user_id,
            action_id=action.id,
            epoch_id=epoch_id,
            root_id=action.id,
            operation_id=effect.operation_id,
            request_hash=action.request_hash,
            effect_hash=command.effect_hash,
            bank_idempotency_key=action.idempotency_key,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise _error("INVALID_DELIVERY_SOURCE", "消息必须绑定原持久动作与经济后果") from error


def _verify_message(outbox: CommandOutbox) -> OriginalActionMessage:
    try:
        message = OriginalActionMessage.model_validate_json(json.dumps(outbox.payload))
        if (
            message.user_id != outbox.user_id
            or message.action_id != outbox.action_plan_id
            or message.root_id != outbox.action_plan_id
            or message.operation_id != outbox.action_plan_id
            or message.epoch_id != outbox.epoch_id
            or outbox.root_id != outbox.action_plan_id
            or outbox.id != uuid5(outbox.action_plan_id, PROTOCOL)
            or outbox.protocol_version != PROTOCOL
            or message.request_hash != outbox.request_hash
            or message.effect_hash != outbox.effect_hash
            or message.bank_idempotency_key != outbox.bank_idempotency_key
            or message.model_dump(mode="json") != outbox.payload
            or configuration_hash(outbox.payload) != outbox.payload_hash
        ):
            raise ValueError("Changed transport binding")
        return message
    except (TypeError, ValueError) as error:
        raise _error("INVALID_DELIVERY_SOURCE", "消息原件身份或哈希无效") from error


def _verify_outbox(outbox: CommandOutbox, action: ActionPlan) -> None:
    _verify_message(outbox)
    expected = _original_message(action, outbox.epoch_id).model_dump(mode="json")
    if (
        outbox.user_id != action.user_id
        or outbox.action_plan_id != action.id
        or outbox.root_id != action.id
        or outbox.id != uuid5(action.id, PROTOCOL)
        or outbox.protocol_version != PROTOCOL
        or outbox.request_hash != action.request_hash
        or outbox.effect_hash != expected["effect_hash"]
        or outbox.bank_idempotency_key != action.idempotency_key
        or outbox.payload != expected
        or outbox.payload_hash != configuration_hash(expected)
    ):
        raise _error("INVALID_DELIVERY_SOURCE", "消息身份或哈希与原动作不一致")


def enqueue_action_in_transaction(
    session: Session, action: ActionPlan, now: datetime
) -> CommandOutbox:
    """Producer seam: caller owns the original user lock and preparation transaction."""
    now = _clock(now)
    if not session.in_transaction() or action.created_at > now:
        raise _error("INVALID_DELIVERY_TRANSACTION", "消息必须与原动作同事务保存")
    epoch = current_audit_epoch(session, action.user_id)
    # The epoch's appended_at wall clock is distinct from the trusted business clock.
    # current_audit_epoch already selects this owner and the OPEN status.
    if epoch is None:
        raise _error("INVALID_DELIVERY_EPOCH", "消息缺少原动作当前审计周期")
    payload = _original_message(action, epoch.id).model_dump(mode="json")
    existing = session.scalar(
        select(CommandOutbox).where(CommandOutbox.action_plan_id == action.id)
    )
    if existing is not None:
        if existing.epoch_id != epoch.id:
            raise _error("INVALID_DELIVERY_EPOCH", "不能在新周期复用旧消息的原动作身份")
        _verify_outbox(existing, action)
        return existing
    outbox = CommandOutbox(
        id=uuid5(action.id, PROTOCOL),
        user_id=action.user_id,
        created_at=now,
        action_plan_id=action.id,
        epoch_id=epoch.id,
        protocol_version=PROTOCOL,
        root_id=action.id,
        request_hash=action.request_hash,
        effect_hash=payload["effect_hash"],
        bank_idempotency_key=action.idempotency_key,
        payload=payload,
        payload_hash=configuration_hash(payload),
        state="PENDING",
        attempt_count=0,
        published_at=None,
        acknowledged_at=None,
        updated_at=now,
        last_error=None,
    )
    session.add(outbox)
    session.flush()
    return outbox


def enqueue_action(engine: Engine, user_id: UUID, action_id: UUID, now: datetime) -> DeliveryView:
    """Explicitly enqueue an already persisted action without changing its payload."""
    now = _clock(now)
    with audit_command_guard(engine, user_id):
        with Session(engine) as session, session.begin():
            _lock_owner(session, user_id)
            get_action(session, user_id, action_id, now)
            action = session.get(ActionPlan, action_id)
            assert action is not None
            outbox = enqueue_action_in_transaction(session, action, now)
            return get_delivery(session, user_id, outbox.id, now)


def _load(
    session: Session, user_id: UUID, outbox_id: UUID
) -> tuple[CommandOutbox, ActionPlan | None]:
    outbox = session.get(CommandOutbox, outbox_id)
    if outbox is None or outbox.user_id != user_id:
        raise PolicyLifecycleError("NOT_FOUND", "命令消息不存在", 404)
    _verify_message(outbox)
    epoch = session.get(AuditEpoch, outbox.epoch_id)
    if epoch is None or epoch.user_id != user_id:
        raise _error("INVALID_DELIVERY_SOURCE", "消息的原审计周期不存在")
    current_epoch = current_audit_epoch(session, user_id)
    if epoch.status != "OPEN" or current_epoch is None or current_epoch.id != epoch.id:
        return outbox, None
    action = session.get(ActionPlan, outbox.action_plan_id)
    if action is None:
        return outbox, None
    if action.user_id != user_id:
        raise _error("INVALID_DELIVERY_SOURCE", "消息的原动作不属于该用户")
    _verify_outbox(outbox, action)
    return outbox, action


def _inbox(session: Session, outbox: CommandOutbox) -> CommandInbox | None:
    row = session.scalar(
        select(CommandInbox).where(
            CommandInbox.outbox_id == outbox.id, CommandInbox.consumer_ref == CONSUMER
        )
    )
    if row is not None and (
        row.user_id != outbox.user_id
        or row.payload_hash != outbox.payload_hash
        or row.id != uuid5(outbox.id, CONSUMER)
    ):
        raise _error("INVALID_DELIVERY_SOURCE", "收件记录不属于原消息")
    return row


def get_delivery(
    session: Session, user_id: UUID, outbox_id: UUID, now: datetime, *, busy: bool = False
) -> DeliveryView:
    now = _clock(now)
    outbox, action = _load(session, user_id, outbox_id)
    current = get_action(session, user_id, action.id, now) if action is not None else None
    inbox = _inbox(session, outbox)
    attempts = list(
        session.scalars(
            select(CommandDeliveryAttempt)
            .where(
                CommandDeliveryAttempt.user_id == user_id,
                CommandDeliveryAttempt.outbox_id == outbox.id,
            )
            .order_by(CommandDeliveryAttempt.attempt_number)
        )
    )
    if inbox is None and attempts:
        raise _error("INVALID_DELIVERY_SOURCE", "投递尝试缺少原收件记录")
    for attempt in attempts:
        if inbox is None or attempt.inbox_id != inbox.id:
            raise _error("INVALID_DELIVERY_SOURCE", "投递尝试关联了其他收件记录")
    if inbox is not None and (
        len(attempts) != inbox.attempt_count
        or inbox.attempt_count != outbox.attempt_count
        or not attempts
        or attempts[-1].id != inbox.attempt_id
        or [row.attempt_number for row in attempts] != list(range(1, len(attempts) + 1))
    ):
        raise _error("INVALID_DELIVERY_SOURCE", "持久尝试编号不完整")
    verified = (
        current is not None
        and current.status in {"SUCCEEDED", "RECONCILED"}
        and current.bank_status == "SETTLED"
        and current.receipt is not None
        and current.receipt.status == "SUCCEEDED"
    )
    return DeliveryView(
        outbox_id=outbox.id,
        root_id=outbox.root_id,
        action_id=outbox.action_plan_id,
        epoch_id=outbox.epoch_id,
        payload_hash=outbox.payload_hash,
        outbox_state=outbox.state,
        inbox_state=inbox.state if inbox is not None else None,
        source_action_status=current.status if current is not None else "NO_CURRENT_ACTION",
        bank_status=current.bank_status if current is not None else None,
        current_action_available=current is not None,
        blocking_reason=None if current is not None else "NO_CURRENT_ACTION",
        service_receipt_verified=verified,
        attempts=[
            AttemptView(
                attempt_id=row.id,
                attempt_number=row.attempt_number,
                state=row.state,
                started_at=row.started_at,
                finished_at=row.finished_at,
                source_action_status=row.source_action_status,
                result=row.result,
                error=row.error,
            )
            for row in attempts
        ],
        last_error=inbox.last_error if inbox is not None else outbox.last_error,
        busy=busy,
    )


def list_deliveries(
    session: Session, user_id: UUID, now: datetime, *, limit: int = 50
) -> DeliveryList:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise _error("INVALID_DELIVERY_LIMIT", "消息读取数量必须为1至100")
    identities = list(
        session.scalars(
            select(CommandOutbox.id)
            .where(CommandOutbox.user_id == user_id)
            .order_by(CommandOutbox.created_at.desc(), CommandOutbox.id)
            .limit(limit)
        )
    )
    return DeliveryList(
        items=[get_delivery(session, user_id, identity, now) for identity in identities]
    )


def advisory_key(user_id: UUID) -> int:
    return int.from_bytes(
        hashlib.sha256(b"action-delivery-v1\0" + user_id.bytes + CONSUMER.encode()).digest()[:8],
        "big",
        signed=True,
    )


@contextmanager
def _delivery_lock(engine: Engine, user_id: UUID) -> Iterator[bool]:
    """A separate connection holds no row lock while the original services commit."""
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        key = advisory_key(user_id)
        acquired = connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
        if type(acquired) is not bool:
            connection.invalidate()
            raise _error("INVALID_DELIVERY_LOCK", "无法核验投递互斥锁")
        try:
            yield acquired
        finally:
            if acquired:
                original_error = sys.exc_info()[1]
                try:
                    unlocked = connection.scalar(
                        text("SELECT pg_advisory_unlock(:key)"), {"key": key}
                    )
                    if unlocked is not True:
                        raise _error("INVALID_DELIVERY_LOCK", "投递互斥锁释放失败")
                except Exception as cleanup_error:
                    # Never return a session-level lock to the connection pool.
                    connection.invalidate()
                    if original_error is None:
                        raise
                    original_error.add_note(
                        f"Delivery lock cleanup: {type(cleanup_error).__name__}"
                    )


def _receive(engine: Engine, user_id: UUID, outbox_id: UUID, now: datetime) -> UUID | None:
    with Session(engine) as session, session.begin():
        _lock_owner(session, user_id)
        outbox, action = _load(session, user_id, outbox_id)
        inbox = _inbox(session, outbox)
        if (
            action is not None
            and inbox is not None
            and inbox.state in {"SERVICE_RECEIPT_VERIFIED", "STOPPED"}
        ):
            # A fresh read verifies the original receipt; a stored state string is insufficient.
            view = get_delivery(session, user_id, outbox_id, now)
            if inbox.state == "SERVICE_RECEIPT_VERIFIED" and not view.service_receipt_verified:
                raise _error("INVALID_DELIVERY_SOURCE", "已处理消息缺少可核原银行回执")
            return None
        if outbox.state == "STOPPED":
            return None
        identity = uuid4()
        if inbox is None:
            inbox = CommandInbox(
                id=uuid5(outbox.id, CONSUMER),
                user_id=user_id,
                created_at=now,
                outbox_id=outbox.id,
                consumer_ref=CONSUMER,
                payload_hash=outbox.payload_hash,
                attempt_id=identity,
                state="RECEIVED",
                attempt_count=0,
                received_at=now,
                started_at=None,
                finished_at=None,
                updated_at=now,
                source_action_status=action.status if action is not None else "NO_CURRENT_ACTION",
                result=None,
                last_error=None,
            )
            session.add(inbox)
            session.flush()
        elif now < inbox.updated_at:
            raise _error("INVALID_CLOCK", "不能将投递记录回写到过去")
        inbox.attempt_count += 1
        inbox.attempt_id, inbox.state, inbox.updated_at = identity, "RECEIVED", now
        inbox.started_at, inbox.finished_at, inbox.result, inbox.last_error = None, None, None, None
        outbox.attempt_count += 1
        outbox.published_at = outbox.published_at or now
        outbox.updated_at, outbox.state, outbox.last_error = now, "PENDING", None
        session.add(
            CommandDeliveryAttempt(
                id=identity,
                user_id=user_id,
                created_at=now,
                outbox_id=outbox.id,
                inbox_id=inbox.id,
                attempt_number=inbox.attempt_count,
                state="RECEIVED",
                started_at=now,
                finished_at=None,
                source_action_status=action.status if action is not None else "NO_CURRENT_ACTION",
                result=None,
                error=None,
            )
        )
        session.flush()
        return identity


def _attempt(
    session: Session, outbox: CommandOutbox, attempt_id: UUID
) -> tuple[CommandInbox, CommandDeliveryAttempt]:
    inbox = _inbox(session, outbox)
    attempt = session.get(CommandDeliveryAttempt, attempt_id)
    if (
        inbox is None
        or inbox.attempt_id != attempt_id
        or attempt is None
        or attempt.user_id != outbox.user_id
        or attempt.inbox_id != inbox.id
        or attempt.outbox_id != outbox.id
        or attempt.attempt_number != inbox.attempt_count
    ):
        raise _error("INVALID_DELIVERY_SOURCE", "本次投递身份不匹配")
    return inbox, attempt


def delivery_route(current: ActionResponse, *, confirmation_present: bool) -> DeliveryRoute:
    """Classify actual freshly read service results, without authorizing new economics."""
    if current.status in {"SUCCEEDED", "RECONCILED"}:
        if (
            current.bank_status == "SETTLED"
            and current.receipt is not None
            and current.receipt.status == "SUCCEEDED"
            and current.receipt.action_id == current.action_id
            and current.receipt.bank_operation_id == current.effect.operation_id
        ):
            return "VERIFIED"
        return "UNRESOLVED"
    if (
        current.status in {"INVALIDATED", "CANCELLED", "FAILED"}
        or current.bank_status == "REJECTED"
    ):
        return "STOPPED"
    if current.bank_status in {"ACCEPTED", "SETTLED"}:
        return "RESUME"
    if current.status == "UNKNOWN" or current.bank_status is not None:
        return "UNRESOLVED"
    if current.status not in {"PLANNED", "AUTHORIZED", "SUBMITTED"}:
        return "STOPPED"
    if current.autonomy_level == "ASK_ONCE":
        return "EXECUTE" if confirmation_present else "WAITING_CONFIRMATION"
    return "EXECUTE" if current.autonomy_level == "AUTO_EXECUTE" else "STOPPED"


def _start(
    engine: Engine, user_id: UUID, outbox_id: UUID, attempt_id: UUID, now: datetime
) -> DeliveryRoute:
    with Session(engine) as session, session.begin():
        _lock_owner(session, user_id)
        outbox, action = _load(session, user_id, outbox_id)
        inbox, attempt = _attempt(session, outbox, attempt_id)
        if inbox.state != "RECEIVED" or attempt.state != "RECEIVED":
            raise _error("INVALID_DELIVERY_STATE", "投递尝试不能重复开始")
        if action is None:
            inbox.state, inbox.started_at, inbox.updated_at = "PROCESSING", now, now
            inbox.source_action_status = "NO_CURRENT_ACTION"
            attempt.state, attempt.source_action_status = "PROCESSING", "NO_CURRENT_ACTION"
            return "STOPPED"
        current = get_action(session, user_id, action.id, now)
        if current.bank_status is not None:
            operation = session.scalar(
                select(BankOperation).where(
                    BankOperation.user_id == user_id, BankOperation.action_plan_id == action.id
                )
            )
            if (
                operation is None
                or operation.id != action.id
                or operation.request != action.request["execution"]
                or operation.request_hash != configuration_hash(operation.request)
                or operation.idempotency_key != action.idempotency_key
                or operation.business_key != current.effect.business_key
            ):
                raise _error("INVALID_DELIVERY_SOURCE", "原银行操作绑定不完整")
        confirmation = False
        if current.bank_status is None and current.autonomy_level == "ASK_ONCE":
            confirmation = read_execution_confirmation(session, current.effect, now) is not None
        route = delivery_route(current, confirmation_present=confirmation)
        inbox.state, inbox.started_at, inbox.updated_at = "PROCESSING", now, now
        inbox.source_action_status = current.status
        attempt.state, attempt.source_action_status = "PROCESSING", current.status
        return route


def _finish(
    engine: Engine,
    user_id: UUID,
    outbox_id: UUID,
    attempt_id: UUID,
    now: datetime,
    *,
    deferred: DeliveryRoute | None = None,
    error: Exception | None = None,
) -> DeliveryView:
    """Separate acknowledgement transaction; verify persisted service results afresh."""
    with Session(engine) as session, session.begin():
        _lock_owner(session, user_id)
        outbox, action = _load(session, user_id, outbox_id)
        inbox, attempt = _attempt(session, outbox, attempt_id)
        if inbox.state not in {"RECEIVED", "PROCESSING"} or attempt.finished_at is not None:
            raise _error("INVALID_DELIVERY_STATE", "投递尝试不能重复确认")
        view = get_delivery(session, user_id, outbox_id, now)
        if view.service_receipt_verified:
            state: InboxState = "SERVICE_RECEIPT_VERIFIED"
        elif deferred == "WAITING_CONFIRMATION":
            state = "WAITING_CONFIRMATION"
        elif (
            deferred == "STOPPED"
            or action is None
            or action.status in {"INVALIDATED", "CANCELLED", "FAILED"}
        ):
            state = "STOPPED"
        elif action.status in {"UNKNOWN", "SUBMITTED"} or view.bank_status is not None:
            state = "UNRESOLVED"
        elif error is not None:
            state = "FAILED"
        else:
            state = "UNRESOLVED"
        error_code = (
            error.code
            if isinstance(error, PolicyLifecycleError)
            else type(error).__name__
            if error is not None
            else None
        )
        result = {
            "action_id": str(outbox.action_plan_id),
            "root_id": str(outbox.root_id),
            "attempt_id": str(attempt_id),
            "source_action_status": view.source_action_status,
            "bank_status": view.bank_status,
            "service_receipt_verified": view.service_receipt_verified,
            "economic_verified": False,
            "deferred_reason": deferred,
            "original_error_code": error_code,
        }
        inbox.state, inbox.finished_at, inbox.updated_at = state, now, now
        inbox.source_action_status, inbox.result, inbox.last_error = (
            view.source_action_status,
            result,
            error_code,
        )
        attempt.state, attempt.finished_at = state, now
        attempt.source_action_status, attempt.result, attempt.error = (
            view.source_action_status,
            result,
            error_code,
        )
        outbox.updated_at, outbox.last_error = now, error_code
        if state in {"SERVICE_RECEIPT_VERIFIED", "STOPPED"}:
            outbox.state = "DELIVERED" if state == "SERVICE_RECEIPT_VERIFIED" else "STOPPED"
            outbox.acknowledged_at = now
        session.flush()
        return get_delivery(session, user_id, outbox_id, now)


def deliver_command(engine: Engine, user_id: UUID, outbox_id: UUID, now: datetime) -> DeliveryView:
    """Explicit local consumer. Every attempt uses the same original action and bank key."""
    now = _clock(now)
    with audit_command_guard(engine, user_id), _delivery_lock(engine, user_id) as acquired:
        if not acquired:
            with Session(engine) as session, session.begin():
                return get_delivery(session, user_id, outbox_id, now, busy=True)
        attempt_id = _receive(engine, user_id, outbox_id, now)
        if attempt_id is None:
            with Session(engine) as session, session.begin():
                return get_delivery(session, user_id, outbox_id, now)
        try:
            route = _start(engine, user_id, outbox_id, attempt_id, now)
            if route in {"EXECUTE", "RESUME"}:
                # No Session/user row lock spans the original financial service.
                with Session(engine) as session, session.begin():
                    outbox, action = _load(session, user_id, outbox_id)
                    if action is None:
                        raise _error("NO_CURRENT_ACTION", "原动作已归档，不能重建资金动作")
                    action_id = outbox.action_plan_id
                execute_action(engine, user_id, action_id, now)
                return _finish(engine, user_id, outbox_id, attempt_id, now)
            return _finish(engine, user_id, outbox_id, attempt_id, now, deferred=route)
        except Exception as original_error:
            try:
                _finish(engine, user_id, outbox_id, attempt_id, now, error=original_error)
            except Exception as recording_error:
                original_error.add_note(
                    f"Delivery acknowledgement: {type(recording_error).__name__}"
                )
            raise
