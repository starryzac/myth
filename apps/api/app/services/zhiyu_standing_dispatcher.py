"""Concrete SYSTEM dispatcher over original native effects and audited journal.

No executor callbacks, HTTP worlds, client money or fabricated principals. This
module requires installed phase1/first-bank native hooks before any acceptance.
All five actual source/mathematical adapters live in zhiyu_standing_snapshot.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from app.db.models import ActionPlan, ActionReceipt, BankOperation, DecisionRun
from app.domain.execution import ACTION_PLAN_TYPES, execution_effect_hash
from app.domain.execution_types import BankCommand
from app.domain.immutable_joint_archive_scope import immutable_joint_archive_scope
from app.domain.immutable_joint_validation_scope import immutable_joint_validation_scope
from app.domain.policy_configuration import configuration_hash
from app.domain.zhiyu_standing_journal import StandingRun, verify_transition
from app.domain.zhiyu_standing_native import (
    ALGORITHM,
    MARKER,
    DelegatedNativeInput,
    DelegatedNativeProof,
    acceptance_inventory,
    derive_delegated_native_proof,
    original_effects,
)
from app.services.audit_chain import audit_read_scope
from app.services.audit_recording import record_action_created
from app.services.command_delivery import enqueue_action_in_transaction
from app.services.execution import _lock_user, execute_action
from app.services.execution_exposure import refresh_execution_exposure
from app.services.execution_projection import verify_execution_receipt
from app.services.historical_read import historical_ledger_scope
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.recovery_projection import _epochs
from app.services.zhiyu_orchestration import _audit_healthy, serial_user, store
from app.services.zhiyu_standing_recording import (
    binding,
    binding_protocol,
    read_native_proof,
    record_native_proof,
    record_standing_phase_trace,
    recorded_native_replay_scope,
    verify_action_binding,
)
from app.services.zhiyu_standing_snapshot import capture_native_snapshot, settled_originals
from app.services.zhiyu_standing_store import (
    PREFIX,
    original_control,
    original_grant,
    original_runs,
)
from app.zhiyu_next_isolation import require_zhiyu_next_engine
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


@contextmanager
def current_read(engine: Engine) -> Iterator[Session]:
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            with (
                Session(bind=connection) as session,
                audit_read_scope(session),
                historical_ledger_scope(session),
                immutable_joint_archive_scope(),
                immutable_joint_validation_scope(),
                recorded_native_replay_scope(),
            ):
                yield session


def require_native_hooks() -> None:
    from app.services import execution, execution_sources

    if (
        getattr(execution, "STANDING_DELEGATED_GUARDS_VERSION", None) != ALGORITHM
        or getattr(execution_sources, "STANDING_DELEGATED_GUARDS_VERSION", None) != ALGORITHM
    ):
        raise PolicyLifecycleError(
            "STANDING_NATIVE_GUARDS_NOT_INSTALLED", "新SYSTEM委托原生门尚未安装", 409
        )


def original_run(session: Session, user: UUID, grant_id: UUID, key: str) -> StandingRun:
    _audit_healthy(session, user)
    rows = [row for row in original_runs(session, user) if row.source.event_key == key]
    if len(rows) != 1 or rows[0].grant_id != grant_id:
        raise PolicyLifecycleError(
            "STANDING_ORIGINAL_EVENT_MISSING", "原持续事件不存在或归属不同", 409
        )
    return rows[0]


def persist_run(
    session: Session, previous: StandingRun, updates: dict[str, Any], now: datetime
) -> StandingRun:
    payload = (
        previous.model_dump(mode="json")
        | updates
        | {
            "revision": previous.revision + 1,
            "previous_record_hash": previous.record_hash,
            "recorded_at": now.isoformat(),
        }
    )
    current = StandingRun.model_validate_json(json.dumps(payload))
    verify_transition(previous, current)
    store(
        session,
        previous.source.user_id,
        "EVENT",
        PREFIX + "run:" + previous.source.event_key + ":" + str(current.revision),
        current.model_dump(mode="json"),
        now,
    )
    return current


def bank_key(event_key: str, number: int) -> str:
    # Neither PID, grant revision nor polling clock can generate a replacement.
    return "standing:" + event_key + ":" + str(number)


def materialize_original_child(
    session: Session,
    value: DelegatedNativeInput,
    proof: DelegatedNativeProof,
    frozen: DelegatedNativeInput,
    evidence_id: UUID,
    evidence_hash: str,
    number: int,
    now: datetime,
) -> ActionPlan:
    effects = original_effects(frozen.native)
    effect = effects[number - 1]
    key = bank_key(value.original_source.source.event_key, number)
    prior = session.get(ActionPlan, effect.operation_id)
    if prior is not None:
        marker = binding(prior)
        verify_action_binding(prior, frozen, marker)
        if prior.idempotency_key != key:
            raise ValueError("Original delegated bank key differs")
        return prior
    if number != value.settled_prefix + 1 or proof.status != "READY":
        raise ValueError("Only the next original READY child can be prepared")
    effect_hash = execution_effect_hash(effect)
    marker = {
        "protocol": binding_protocol(value.protocol),
        "event_key": value.original_source.source.event_key,
        "grant_id": str(value.grant.grant_id),
        "whole_plan_hash": configuration_hash(frozen.native.model_dump(mode="json")),
        "accepted_proof_evidence_id": str(evidence_id),
        "accepted_proof_evidence_hash": evidence_hash,
        "child_number": number,
        "bank_key": key,
    }
    validation = proof.native_validations[0]
    intent = (
        {"kind": "allocate_goal", "goal_id": str(effect.goal_id)}
        if effect.action_type == "ALLOCATE_GOAL"
        else {"kind": "purchase_asset", "policy_id": str(effect.policy_id)}
        if effect.action_type == "PURCHASE_ASSET"
        else {
            "kind": "pay_recurring",
            "policy_id": str(effect.policy_id),
            "period": effect.liability.period
            if effect.liability is not None and effect.liability.kind == "occurrence"
            else None,
        }
    )
    payload = {
        "intent": intent,
        "execution": BankCommand(effect=effect, effect_hash=effect_hash).model_dump(mode="json"),
        "prepared_validation": validation.model_dump(mode="json"),
        MARKER: marker,
    }
    run = DecisionRun(
        id=uuid5(effect.operation_id, "decision"),
        user_id=value.user_id,
        created_at=now,
        idempotency_key=key,
        trigger_type="ACTION_PREPARE",
        algorithm_version=value.protocol,
        as_of=now,
        input_snapshot={"intent": intent, "effect_hash": effect_hash},
        snapshot_hash=configuration_hash({"intent": intent, "effect_hash": effect_hash}),
        policy_version_ids=[str(row) for row in effect.policy_version_ids],
        evidence_ids=[],
        result={"action_id": str(effect.operation_id)},
        status="PENDING",
        completed_at=None,
    )
    session.add(run)
    session.flush()
    source = effect.cash_uses[0].account_id if effect.cash_uses else effect.position_account_id
    if source is None:
        raise ValueError("Actual native original funding account is missing")
    action = ActionPlan(
        id=effect.operation_id,
        user_id=value.user_id,
        created_at=now,
        decision_run_id=run.id,
        policy_version_id=effect.policy_version_id,
        source_account_id=source,
        destination_account_id=effect.destination_account_id
        if effect.destination_account_id != source
        else None,
        goal_id=effect.goal_id,
        product_id=effect.product_id,
        position_id=None,
        action_type=ACTION_PLAN_TYPES[effect.action_type],
        amount_cents=effect.amount_cents,
        autonomy_level="AUTO_EXECUTE",
        status="PLANNED",
        idempotency_key=key,
        request=payload,
        request_hash=configuration_hash(payload),
        authorized_at=None,
        expires_at=effect.expires_at,
    )
    session.add(action)
    session.flush()
    # Original income snapshot binding is read and stored as in native prepare;
    # no income or resource is reserved until the original native RESERVE phase.
    if effect.income_uses:
        from app.services.income_ledger import read_income_state

        income = read_income_state(session, value.user_id, now)
        action.request = payload | {
            "income_evidence": {"id": str(income.evidence_id), "hash": income.evidence_hash}
        }
        action.request_hash = configuration_hash(action.request)
    record_standing_phase_trace(session, action, value, proof, existing_run=run)
    _epochs(session, value.user_id, now, action.id)
    refresh_execution_exposure(session, value.user_id, now, action.id)
    session.flush()
    record_action_created(session, action, now)
    enqueue_action_in_transaction(session, action, now)
    return action


def _accept(engine: Engine, user: UUID, grant_id: UUID, key: str, now: datetime) -> StandingRun:
    # Original financial native writers share the User row transaction gate.
    # Hold it across the new RR/RO proof and the atomic acceptance/first prepare.
    with Session(engine) as write, write.begin():
        _lock_user(write, user)
        previous = original_run(write, user, grant_id, key)
        if previous.state not in {"OBSERVED", "WAITING_INFORMATION", "WAITING_CONFIRMATION"}:
            return previous
        grant = original_grant(write, user, grant_id)
        control = original_control(write, user, grant_id)
        if control.state != "ACTIVE" or not grant.scope.issued_at <= now < grant.scope.expires_at:
            return persist_run(
                write,
                previous,
                {
                    "state": "CANCELLED_BEFORE_ACCEPTANCE",
                    "reasons": ["CURRENT_STANDING_CONTROL_INACTIVE_OR_EXPIRED"],
                },
                now,
            )
        try:
            with current_read(engine) as read:
                value = capture_native_snapshot(read, user, grant_id, key, now, "PREPARE")
                proof = derive_delegated_native_proof(value)
        except (ValueError, PolicyLifecycleError) as error:
            code = (
                error.code
                if isinstance(error, PolicyLifecycleError)
                else "CURRENT_NATIVE_WHOLE_SOURCE_NOT_VERIFIED"
            )
            return persist_run(
                write, previous, {"state": "WAITING_INFORMATION", "reasons": [code]}, now
            )
        if proof.status != "READY":
            return persist_run(
                write,
                previous,
                {
                    "state": "WAITING_INFORMATION"
                    if proof.status == "UNKNOWN"
                    else "WAITING_CONFIRMATION",
                    "reasons": proof.reasons[:64],
                },
                now,
            )
        acceptance_inventory(value, proof)
        if (
            original_grant(write, user, grant_id) != value.grant
            or original_control(write, user, grant_id) != value.current_control
            or original_runs(write, user) != value.current_runs
        ):
            raise ValueError("Current grant/control/complete budget changed under acceptance lock")
        row = record_native_proof(write, value, proof, now)
        effects = original_effects(value.native)
        accepted = persist_run(
            write,
            previous,
            {
                "state": "ACCEPTED",
                "grant_hash": proof.grant_hash,
                "control_revision_at_acceptance": proof.control_revision,
                "whole_plan_hash": configuration_hash(value.native.model_dump(mode="json")),
                "original_action_ids": [str(effect.operation_id) for effect in effects],
                "original_effect_hashes": proof.ordered_effect_hashes,
                "original_bank_keys": [
                    bank_key(key, number) for number in range(1, len(effects) + 1)
                ],
                "accepted_total_cents": proof.original_total_cents,
                "accounting_day": now.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat(),
                "reasons": [],
            },
            now,
        )
        materialize_original_child(write, value, proof, value, row.id, row.content_hash, 1, now)
        return accepted


def _recover(engine: Engine, user: UUID, grant_id: UUID, key: str, now: datetime) -> StandingRun:
    while True:
        with current_read(engine) as read:
            run = original_run(read, user, grant_id, key)
            if run.state not in {"ACCEPTED", "UNKNOWN"}:
                return run
            first = read.get(ActionPlan, run.original_action_ids[0])
            if first is None:
                raise ValueError("Accepted first original action is missing")
            marker = binding(first)
            frozen, _ = read_native_proof(
                read,
                user,
                UUID(marker["accepted_proof_evidence_id"]),
                marker["accepted_proof_evidence_hash"],
            )
            settled = settled_originals(read, user, original_effects(frozen.native), now)
            number = len(settled) + 1
            receipt_ids = [row["receipt"]["id"] for row in settled]
            if number > len(run.original_action_ids):
                with Session(engine) as write, write.begin():
                    _lock_user(write, user)
                    latest = original_run(write, user, grant_id, key)
                    return persist_run(
                        write,
                        latest,
                        {"state": "SETTLED", "original_receipt_ids": receipt_ids, "reasons": []},
                        now,
                    )
            action_id = run.original_action_ids[number - 1]
            operation = read.scalar(
                select(BankOperation).where(
                    BankOperation.user_id == user, BankOperation.action_plan_id == action_id
                )
            )
        # Existing bank original recovery deliberately precedes expiry/pause and
        # never creates another source, effect, key or accepted budget.
        try:
            if operation is None:
                with Session(engine) as write, write.begin():
                    _lock_user(write, user)
                    latest = original_run(write, user, grant_id, key)
                    with current_read(engine) as read:
                        current = capture_native_snapshot(
                            read, user, grant_id, key, now, "PREPARE", frozen
                        )
                        proof = derive_delegated_native_proof(current)
                    if proof.status != "READY":
                        return persist_run(
                            write,
                            latest,
                            {
                                "state": "UNKNOWN",
                                "original_receipt_ids": receipt_ids,
                                "reasons": proof.reasons[:64],
                            },
                            now,
                        )
                    materialize_original_child(
                        write,
                        current,
                        proof,
                        frozen,
                        UUID(marker["accepted_proof_evidence_id"]),
                        marker["accepted_proof_evidence_hash"],
                        number,
                        now,
                    )
            result = execute_action(engine, user, action_id, now)
            with current_read(engine) as read:
                actual_operation = read.scalar(
                    select(BankOperation).where(
                        BankOperation.user_id == user, BankOperation.action_plan_id == action_id
                    )
                )
                receipt = read.scalar(
                    select(ActionReceipt).where(
                        ActionReceipt.user_id == user, ActionReceipt.action_plan_id == action_id
                    )
                )
                if (
                    result.status not in {"SUCCEEDED", "RECONCILED"}
                    or actual_operation is None
                    or receipt is None
                ):
                    raise PolicyLifecycleError(
                        "STANDING_ORIGINAL_RESULT_UNKNOWN", "原银行动作尚未核实，禁止后续", 409
                    )
                verify_execution_receipt(read, actual_operation, receipt, now)
        except (PolicyLifecycleError, ValueError) as cause:
            reason = cause.code if isinstance(cause, PolicyLifecycleError) else "ValueError"
            with Session(engine) as write, write.begin():
                _lock_user(write, user)
                latest = original_run(write, user, grant_id, key)
                return persist_run(
                    write,
                    latest,
                    {
                        "state": "UNKNOWN",
                        "original_receipt_ids": receipt_ids,
                        "reasons": ["ORIGINAL_RESULT_OR_CURRENT_NATIVE_GATE_NOT_FINAL", reason],
                    },
                    now,
                )


def dispatch_original_event(
    engine: Engine, user_id: UUID, grant_id: UUID, event_key: str, now: datetime
) -> StandingRun:
    require_zhiyu_next_engine(engine, check_connection=True)
    require_native_hooks()
    if (
        now.tzinfo is None
        or now.utcoffset() is None
        or len(event_key) != 64
        or any(char not in "0123456789abcdef" for char in event_key)
    ):
        raise ValueError("Aware server clock and original event digest required")
    with (
        immutable_joint_archive_scope(),
        immutable_joint_validation_scope(),
        recorded_native_replay_scope(),
        serial_user(engine, user_id),
    ):
        with current_read(engine) as read:
            run = original_run(read, user_id, grant_id, event_key)
        if run.state in {"ACCEPTED", "UNKNOWN"}:
            return _recover(engine, user_id, grant_id, event_key, now)
        accepted = _accept(engine, user_id, grant_id, event_key, now)
        return (
            _recover(engine, user_id, grant_id, event_key, now)
            if accepted.state == "ACCEPTED"
            else accepted
        )
