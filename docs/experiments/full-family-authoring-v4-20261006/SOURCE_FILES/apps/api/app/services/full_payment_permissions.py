"""User-originated fixed payee consent and actual original recurring-payment phases.

The new scope narrows an already confirmed MVP policy. It does not mutate that
policy, a FULL planning version, bank facts, monthly paid observations or hashes.
New bank acceptance must call enforce_full_payment_bank_scope under its user lock.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Literal, Self, cast
from uuid import UUID, uuid5

from app.db.audit_guard import audit_command_guard
from app.db.models import (
    Account,
    ActionPlan,
    ActionReceipt,
    ActionResourceReservation,
    BankOperation,
    DecisionRun,
    EvidenceItem,
    Policy,
    PolicyVersion,
    SimulatedBankPosting,
    SimulatedBankRedemption,
    Transaction,
    User,
)
from app.domain.boundary_types import BoundaryModel
from app.domain.decision_trace import build_trace
from app.domain.execution_types import ExecutionEffect
from app.domain.full_payment_permissions import (
    PROTOCOL,
    SOURCE,
    Hash,
    Key,
    PaymentActionConfirmation,
    PaymentConfirmRequest,
    PaymentExecuteRequest,
    PaymentPrepareRequest,
    PaymentRelationScope,
    PaymentScopeRequest,
    PaymentStartRequest,
    account_payment_identity,
    clock_utc,
    original_payment_action_key,
    payment_command_identity,
    payment_prepare_key,
    payment_scope_hash,
    require_payment_principal,
    validate_payment_bridge,
    verify_original_payment_references,
    verify_payment_effect,
)
from app.domain.full_policy_configuration import PeriodicTransferPolicy
from app.domain.local_actor_session_types import LocalActorPrincipal
from app.domain.policy_configuration import RecurringObligation, configuration_hash
from app.services.action_contracts import (
    ActionResponse,
    ConfirmActionRequest,
    PaymentIntent,
    PrepareActionRequest,
)
from app.services.audit_chain import (
    audit_read_scope,
    current_audit_epoch,
    row_copy,
    verify_audit_chain,
)
from app.services.decision_trace import evidence_copy, get_decision_trace, policy_copy, record_trace
from app.services.execution import confirm_action, get_action, prepare_action
from app.services.execution_planning import plan_execution_effect
from app.services.execution_sources import _counterparty_binding, read_execution_confirmation
from app.services.full_policy_lifecycle import FullPolicyView, _read_snapshot, read_full_policy
from app.services.policy_lifecycle import (
    PolicyLifecycleError,
    _evidence,
    _user,
    is_version_authorized,
)
from app.services.simulated_bank import validate_bank_projection
from pydantic import model_validator
from sqlalchemy import or_, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

CommandKind = Literal["START", "CONFIRM"]
PaymentExecutor = Callable[[Engine, UUID, UUID, datetime], ActionResponse]
BINDING_SOURCE = "FULL_PAYMENT_ACTION_BINDING"
CONSENT_SOURCE = "FULL_PAYMENT_USER_ACTION_CONSENT"
PREPARE_SOURCE = "FULL_PAYMENT_PREPARE_ORIGINAL"


class PaymentCommandOriginal(BoundaryModel):
    protocol: Literal["full-payment-relation-v1"] = "full-payment-relation-v1"
    command_id: UUID
    user_id: UUID
    epoch_id: UUID
    kind: CommandKind
    idempotency_key: Key
    start_command_id: UUID | None
    original_request: dict[str, Any]
    request_hash: Hash
    principal_at_command: LocalActorPrincipal
    scope: PaymentRelationScope
    scope_hash: Hash
    recorded_at: datetime
    creates_original_mvp_permission: Literal[False] = False
    transfers_funds: Literal[False] = False

    @model_validator(mode="after")
    def original_binding(self) -> Self:
        if (
            self.command_id
            != payment_command_identity(self.user_id, self.epoch_id, self.idempotency_key)
            or self.scope.user_id != self.user_id
            or self.scope.epoch_id != self.epoch_id
            or self.scope_hash != payment_scope_hash(self.scope)
            or self.request_hash
            != configuration_hash(
                {"user_id": str(self.user_id), "kind": self.kind, "request": self.original_request}
            )
            or (self.kind == "START") != (self.start_command_id is None)
            or not self.scope.valid_from <= self.recorded_at < self.scope.valid_until
        ):
            raise ValueError("The original payment command cannot change its scope or identity")
        require_payment_principal(
            self.principal_at_command, self.user_id, self.recorded_at, {"USER"}
        )
        body: PaymentStartRequest | PaymentConfirmRequest
        if self.kind == "START":
            body = PaymentStartRequest.model_validate_json(json.dumps(self.original_request))
            if body.model_dump(include=set(PaymentScopeRequest.model_fields)) != (
                _scope_request(self.scope).model_dump()
            ):
                raise ValueError("Initiation cannot replace the original policy identities")
        else:
            # Confirm inputs contain the exact original START identity separately.
            if set(self.original_request) != {"start_command_id", "confirmation"}:
                raise ValueError("Original confirmation shape changed")
            if self.original_request["start_command_id"] != str(self.start_command_id):
                raise ValueError("Confirmation cannot replace the original initiation")
            body = PaymentConfirmRequest.model_validate_json(
                json.dumps(self.original_request["confirmation"])
            )
        if body.expected_epoch_id != self.epoch_id or body.idempotency_key != self.idempotency_key:
            raise ValueError("Original key or epoch changed")
        if isinstance(body, PaymentConfirmRequest) and body.reviewed_scope_hash != self.scope_hash:
            raise ValueError("The actual reviewed payment scope differs")
        return self


class PaymentScopePreview(BoundaryModel):
    simulation: Literal[True] = True
    scope: PaymentRelationScope
    scope_hash: str
    preview_only: Literal[True] = True
    grants_authority: Literal[False] = False
    original_mvp_permission_reused: Literal[True] = True


class FullPaymentActionRecheckResult(BoundaryModel):
    full_policy_id: UUID
    known_adapter: Literal["FIXED_PAYMENT"] = "FIXED_PAYMENT"
    invalidated_action_ids: list[UUID] = []
    inflight_action_ids: list[UUID] = []
    terminal_action_ids: list[UUID] = []
    retained_action_ids: list[UUID] = []
    funds_withdrawn: Literal[False] = False


class PeriodicTransferProjectionBinding(BoundaryModel):
    status: Literal["VERIFIED_CURRENT_RELATION", "NO_CURRENT_DEDICATED_RELATION", "UNKNOWN"]
    full_policy_id: UUID
    full_version_id: UUID
    relation_command_id: UUID | None = None
    relation_evidence_id: UUID | None = None
    relation_evidence_hash: Hash | None = None
    scope_hash: Hash | None = None
    source_evidence_ids: list[UUID] = []
    source_evidence_hashes: dict[str, str] = {}
    issues: list[str] = []
    bank_authority: Literal[False] = False
    changes_original_full_hash_or_flags: Literal[False] = False


class PaymentCommandReceipt(BoundaryModel):
    simulation: Literal[True] = True
    original: PaymentCommandOriginal
    evidence_id: UUID
    evidence_hash: str
    trace_hash: str
    idempotent_replay: bool
    current_scope_status: Literal["CURRENT", "STALE", "UNKNOWN"]
    receipt_is_current_authority: Literal[False] = False
    economic_effect_verified: Literal[False] = False
    transfers_funds: Literal[False] = False


class PaymentCommandLookup(BoundaryModel):
    simulation: Literal[True] = True
    user_id: UUID
    epoch_id: UUID
    idempotency_key: str
    status: Literal["RECORDED", "NOT_FOUND_NOT_FINAL"]
    original: PaymentCommandReceipt | None
    replacement_allowed: Literal[False] = False


class PaymentActionBinding(BoundaryModel):
    protocol: Literal["full-payment-action-binding-v1"] = "full-payment-action-binding-v1"
    user_id: UUID
    epoch_id: UUID
    action_id: UUID
    authorization_id: UUID
    authorization_evidence_id: UUID
    authorization_evidence_hash: Hash
    scope: PaymentRelationScope
    scope_hash: Hash
    period: str
    original_prepare_request: PaymentPrepareRequest
    original_effect_hash: Hash
    recorded_at: datetime

    @model_validator(mode="after")
    def exact_identity(self) -> Self:
        if (
            self.user_id != self.scope.user_id
            or self.epoch_id != self.scope.epoch_id
            or self.authorization_evidence_id != uuid5(self.authorization_id, "evidence")
            or self.scope_hash != payment_scope_hash(self.scope)
            or self.period != self.original_prepare_request.period
            or self.original_prepare_request.expected_epoch_id != self.epoch_id
            or not self.scope.valid_from <= self.recorded_at < self.scope.valid_until
        ):
            raise ValueError("The original action cannot replace its consent or period")
        return self


class PaymentPreparedLookup(BoundaryModel):
    simulation: Literal[True] = True
    user_id: UUID
    authorization_id: UUID
    idempotency_key: str
    status: Literal["RECORDED", "NOT_FOUND_NOT_FINAL"]
    original_binding: PaymentActionBinding | None
    original_action: ActionResponse | None
    replacement_allowed: Literal[False] = False


class PaymentUserActionConsent(BoundaryModel):
    protocol: Literal["full-payment-user-action-consent-v1"] = "full-payment-user-action-consent-v1"
    user_id: UUID
    epoch_id: UUID
    action_id: UUID
    original_effect_hash: Hash
    original_confirmation_evidence_id: UUID
    principal_at_confirmation: LocalActorPrincipal
    confirmed_at: datetime
    accepted: Literal[True] = True

    @model_validator(mode="after")
    def exact_user_consent(self) -> Self:
        require_payment_principal(
            self.principal_at_confirmation, self.user_id, self.confirmed_at, {"USER"}
        )
        if self.original_confirmation_evidence_id != uuid5(
            self.action_id, "confirmation:" + self.original_effect_hash
        ):
            raise ValueError("Signed USER consent cannot replace original effect confirmation")
        return self


class PaymentConsentLookup(BoundaryModel):
    simulation: Literal[True] = True
    action_id: UUID
    status: Literal["RECORDED", "NOT_FOUND_NOT_FINAL"]
    original: PaymentUserActionConsent | None
    original_request: PaymentActionConfirmation | None
    replacement_allowed: Literal[False] = False


class PaymentPreparationOriginal(BoundaryModel):
    protocol: Literal["full-payment-prepare-original-v1"] = "full-payment-prepare-original-v1"
    user_id: UUID
    epoch_id: UUID
    authorization_id: UUID
    authorization_evidence_hash: Hash
    request: PaymentPrepareRequest
    full_payment_action_key: str
    principal_at_prepare: LocalActorPrincipal
    recorded_at: datetime

    @model_validator(mode="after")
    def exact_preparation(self) -> Self:
        require_payment_principal(
            self.principal_at_prepare, self.user_id, self.recorded_at, {"USER", "AGENT", "SYSTEM"}
        )
        if self.request.expected_epoch_id != self.epoch_id or self.full_payment_action_key != (
            original_payment_action_key(self.authorization_id, self.request.idempotency_key)
        ):
            raise ValueError("The original preparation cannot replace the action key or epoch")
        return self


def _preparation_original(
    session: Session,
    user_id: UUID,
    authorization_id: UUID,
    body: PaymentPrepareRequest,
    now: datetime,
) -> PaymentPreparationOriginal | None:
    key = original_payment_action_key(authorization_id, body.idempotency_key)
    identity = uuid5(user_id, "full-payment-prepare-original:" + key)
    proof = session.get(EvidenceItem, identity)
    if proof is None:
        if session.get(DecisionRun, uuid5(identity, "trace")):
            raise _error("原准备命令证据丢失", "PAYMENT_ORIGINAL_INTEGRITY_ERROR")
        return None
    try:
        original = PaymentPreparationOriginal.model_validate_json(json.dumps(proof.content))
        trace = get_decision_trace(session, user_id, uuid5(identity, "trace"), now)
        if original.request != body or original.authorization_id != authorization_id:
            raise _error("原准备键不能更换周期或范围", "IDEMPOTENCY_CONFLICT")
        if (
            original.user_id != user_id
            or proof.user_id != user_id
            or proof.source_type != PREPARE_SOURCE
            or proof.source_ref != key
            or proof.status != "VALID"
            or proof.evidence_level != "USER_DECLARED"
            or proof.content_hash != configuration_hash(proof.content)
            or proof.created_at != original.recorded_at
            or proof.observed_at != original.recorded_at
            or proof.valid_from != original.recorded_at
            or proof.valid_to is not None
            or trace.completeness != "COMPLETE"
            or trace.audit_chain_status != "VALID"
            or trace.trace is None
            or trace.trace.action_id is not None
            or trace.trace.parent_run_id != authorization_id
            or trace.trace.inputs
            != {
                "full_payment_action_key": key,
                "authorization_id": str(authorization_id),
                "request": body.model_dump(mode="json"),
            }
            or trace.trace.outcome
            != {"full_payment_prepare_original": original.model_dump(mode="json")}
            or not any(
                row.id == proof.id and row.content_hash == proof.content_hash
                for row in trace.trace.sources
            )
        ):
            raise ValueError("Original preparation identity, evidence or trace changed")
    except (ValueError, KeyError, TypeError) as error:
        raise _error("原准备命令尚未验真", "PAYMENT_ORIGINAL_INTEGRITY_ERROR") from error
    return original


def _record_preparation_original(
    engine: Engine,
    user_id: UUID,
    authorization: PaymentCommandReceipt,
    body: PaymentPrepareRequest,
    principal: LocalActorPrincipal,
    now: datetime,
) -> None:
    key = original_payment_action_key(authorization.original.command_id, body.idempotency_key)
    identity = uuid5(user_id, "full-payment-prepare-original:" + key)
    with Session(engine) as writer, writer.begin():
        _user(writer, user_id)
        with _reader(engine) as reader:
            existing = _preparation_original(
                reader, user_id, authorization.original.command_id, body, now
            )
            if existing is not None:
                if existing.authorization_evidence_hash != authorization.evidence_hash:
                    raise _error("原准备范围证据被更换", "PAYMENT_ORIGINAL_INTEGRITY_ERROR")
                return
            current = _original(
                reader, user_id, authorization.original.command_id, now, replay=True
            )
            if current is None or current.current_scope_status != "CURRENT":
                raise _error("准备命令登记前原范围失效", "STALE_PAYMENT_AUTHORIZATION")
            source = reader.get(EvidenceItem, current.evidence_id)
            if source is None:
                raise _error("原范围确认原件缺失", "PAYMENT_SOURCE_UNKNOWN")
            source_copy = evidence_copy(source)
        original = PaymentPreparationOriginal(
            user_id=user_id,
            epoch_id=body.expected_epoch_id,
            authorization_id=authorization.original.command_id,
            authorization_evidence_hash=authorization.evidence_hash,
            request=body,
            full_payment_action_key=key,
            principal_at_prepare=principal,
            recorded_at=now,
        )
        content = original.model_dump(mode="json")
        proof = EvidenceItem(
            id=identity,
            user_id=user_id,
            source_type=PREPARE_SOURCE,
            source_ref=key,
            evidence_level="USER_DECLARED",
            status="VALID",
            content=content,
            content_hash=configuration_hash(content),
            created_at=now,
            observed_at=now,
            valid_from=now,
        )
        writer.add(proof)
        writer.flush()
        trace = build_trace(
            run_id=uuid5(identity, "trace"),
            user_id=user_id,
            phase="EVALUATION",
            as_of=now,
            action_id=None,
            parent_run_id=authorization.original.command_id,
            algorithm_versions={"trace": "decision-trace-v1", "full_payment_relation": PROTOCOL},
            inputs={
                "full_payment_action_key": key,
                "authorization_id": str(original.authorization_id),
                "request": body.model_dump(mode="json"),
            },
            sources=[source_copy, evidence_copy(proof)],
            policies=[],
            outcome={"full_payment_prepare_original": content},
        )
        record_trace(writer, trace)


def _action_consent(
    session: Session,
    binding: PaymentActionBinding,
    action: ActionResponse,
    now: datetime,
) -> PaymentUserActionConsent | None:
    proof_id = uuid5(binding.action_id, "full-payment-user-consent")
    proof = session.get(EvidenceItem, proof_id)
    if proof is None:
        if session.get(DecisionRun, uuid5(binding.action_id, "full-payment-user-consent-trace")):
            raise _error("原签名USER同意证据丢失", "PAYMENT_ORIGINAL_INTEGRITY_ERROR")
        return None
    try:
        consent = PaymentUserActionConsent.model_validate_json(json.dumps(proof.content))
        trace = get_decision_trace(
            session,
            binding.user_id,
            uuid5(binding.action_id, "full-payment-user-consent-trace"),
            now,
        )
        grant = read_execution_confirmation(session, action.effect, consent.confirmed_at)
        if (
            consent.user_id != binding.user_id
            or consent.epoch_id != binding.epoch_id
            or consent.action_id != binding.action_id
            or consent.original_effect_hash != binding.original_effect_hash
            or grant is None
            or grant.evidence_id != consent.original_confirmation_evidence_id
            or grant.confirmed_at > consent.confirmed_at
            or not action.effect.valid_from <= consent.confirmed_at < action.effect.expires_at
            or proof.user_id != binding.user_id
            or proof.evidence_level != "USER_CONFIRMED_ACTION"
            or proof.source_type != CONSENT_SOURCE
            or proof.source_ref != str(binding.action_id)
            or proof.status != "VALID"
            or proof.content_hash != configuration_hash(proof.content)
            or proof.created_at != consent.confirmed_at
            or proof.observed_at != consent.confirmed_at
            or proof.valid_from != consent.confirmed_at
            or proof.valid_to != action.effect.expires_at
            or trace.completeness != "COMPLETE"
            or trace.audit_chain_status != "VALID"
            or trace.trace is None
            or trace.trace.action_id != binding.action_id
            or trace.trace.parent_run_id != uuid5(binding.action_id, "full-payment-binding-trace")
            or trace.trace.inputs
            != {
                "reviewed_effect_hash": action.effect_hash,
                "accepted": True,
                "expected_epoch_id": str(binding.epoch_id),
            }
            or trace.trace.outcome
            != {"full_payment_user_action_consent": consent.model_dump(mode="json")}
            or not any(
                row.id == proof_id and row.content_hash == proof.content_hash
                for row in trace.trace.sources
            )
        ):
            raise ValueError("Signed original action confirmation is not complete")
    except (ValueError, KeyError, TypeError) as error:
        raise _error("原签名USER逐次同意尚未验真", "PAYMENT_ORIGINAL_INTEGRITY_ERROR") from error
    return consent


def _current_payee_sources(
    session: Session,
    user_id: UUID,
    payee_id: str,
    now: datetime,
) -> set[UUID]:
    """Validate every current debit for this identity, rather than trust the latest label."""
    rows = list(
        session.scalars(
            select(Transaction).where(
                Transaction.user_id == user_id,
                Transaction.counterparty_ref == payee_id,
                Transaction.direction == "DEBIT",
                Transaction.occurred_at <= now,
                Transaction.observed_at <= now,
            )
        )
    )
    if not rows or len(rows) > 10000 or any(row.evidence_id is None for row in rows):
        raise _error("当前银行收款身份原件不完整或超容量", "PAYMENT_SOURCE_UNKNOWN")
    return {
        _counterparty_binding(session, user_id, payee_id, row.evidence_id, now)
        for row in rows
        if row.evidence_id is not None
    }


@contextmanager
def _reader(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        session.execute(text("SET TRANSACTION READ ONLY"))
        with audit_read_scope(session):
            yield session


def _error(
    message: str, code: str = "FULL_PAYMENT_NOT_READY", status: int = 409
) -> PolicyLifecycleError:
    return PolicyLifecycleError(code, message, status)


def _principal(
    principal: LocalActorPrincipal, user_id: UUID, now: datetime, roles: set[str]
) -> None:
    try:
        require_payment_principal(principal, user_id, now, roles)
    except ValueError as error:
        raise _error(
            "需要当前匹配的服务端本地身份与角色", "PAYMENT_ROLE_NOT_AUTHORIZED", 403
        ) from error


def _scope(
    session: Session,
    user_id: UUID,
    body: PaymentScopeRequest,
    now: datetime,
    *,
    pinned_payee: UUID | None = None,
) -> tuple[PaymentRelationScope, list[EvidenceItem]]:
    _read_snapshot(session)
    epoch = current_audit_epoch(session, user_id)
    if epoch is None or epoch.status != "OPEN" or epoch.id != body.expected_epoch_id:
        raise _error("付款确认必须使用当前开放周期", "STALE_AUDIT_EPOCH")
    audit = verify_audit_chain(session, user_id)
    if audit.status != "VALID":
        raise _error("完整付款来源审计未验真", "PAYMENT_SOURCE_UNKNOWN")
    try:
        validate_bank_projection(session, user_id, now)
    except ValueError as error:
        raise _error("当前账户与原银行账本不一致", "PAYMENT_SOURCE_UNKNOWN") from error
    user = session.get(User, user_id)
    if user is None or not user.is_simulated:
        raise _error("模拟付款用户不存在", "NOT_FOUND", 404)
    full = read_full_policy(session, user_id, body.full_policy_id, now)
    if (
        full.template_name != "PeriodicTransferPolicy"
        or full.epoch_id != epoch.id
        or full.current_version.version_id != body.expected_full_version_id
        or full.effective_status != "ACTIVE"
    ):
        raise _error("完整周期规则的当前版本或确认已变化", "STALE_FULL_PAYMENT_POLICY")
    configuration = PeriodicTransferPolicy.model_validate(full.current_version.configuration)
    policy = session.get(Policy, body.original_policy_id)
    version = session.scalar(
        select(PolicyVersion)
        .where(PolicyVersion.user_id == user_id, PolicyVersion.policy_id == body.original_policy_id)
        .order_by(PolicyVersion.version_number.desc())
        .limit(1)
    )
    if (
        policy is None
        or policy.user_id != user_id
        or version is None
        or version.id != body.expected_original_version_id
        or not is_version_authorized(session, user_id, version.id, now)
        or version.confirmed_at is None
        or version.valid_from is None
    ):
        raise _error("原周期策略必须已真实确认且当前有效", "STALE_ORIGINAL_PAYMENT_POLICY")
    original = RecurringObligation.model_validate(version.configuration)
    try:
        validate_payment_bridge(configuration, original)
    except ValueError as error:
        raise _error(str(error), "PAYMENT_POLICY_MAPPING_MISMATCH") from error
    start = max(
        full.current_version.valid_from,
        full.current_version.confirmed_at,
        version.valid_from,
        version.confirmed_at,
    )
    ends = [
        value
        for value in (full.current_version.valid_until, version.valid_until)
        if value is not None
    ]
    if full.current_version.valid_until is None or not ends:
        raise _error("新的固定收款关系必须有明确有限到期", "PAYMENT_SCOPE_UNBOUNDED")
    end = min(ends)
    if not start <= now < end:
        raise _error("固定收款关系尚未生效或已经到期", "PAYMENT_SCOPE_OUTSIDE_VALIDITY")
    source = session.get(Account, configuration.source_account_id)
    if (
        source is None
        or source.user_id != user_id
        or source.account_type != "CASH"
        or source.currency != "CNY"
    ):
        raise _error("固定付款来源必须是当前用户活期账户")
    # Original FULL CURRENT flags remain unchanged. Revalidate exactly its two
    # original references rather than swapping identity to a newer same-payee debit.
    references = full.current_version.impact_analysis.get("reference_snapshots")
    if not isinstance(references, list):
        raise _error("完整周期原引用分母缺失", "PAYMENT_SOURCE_UNKNOWN")
    original_payees = [
        row for row in references if isinstance(row, dict) and row.get("role") == "payee_source"
    ]
    if len(original_payees) != 1:
        raise _error("原银行收款身份缺失或歧义", "PAYMENT_SOURCE_UNKNOWN")
    try:
        registered_payee = UUID(original_payees[0]["id"])
    except (KeyError, ValueError, TypeError) as error:
        raise _error("原收款身份引用无效", "PAYMENT_SOURCE_UNKNOWN") from error
    if pinned_payee is not None and registered_payee != pinned_payee:
        raise _error("不得把后续交易当新收款关系替换原身份", "PAYMENT_SOURCE_UNKNOWN")
    payee_id = _counterparty_binding(
        session, user_id, configuration.payee_id, registered_payee, now
    )
    current_payees = _current_payee_sources(session, user_id, configuration.payee_id, now)
    payee_proof = session.get(EvidenceItem, payee_id)
    assert payee_proof is not None
    try:
        verify_original_payment_references(
            references, row_copy(source), row_copy(payee_proof), configuration.payee_id
        )
    except (ValueError, KeyError, TypeError) as error:
        raise _error("原银行身份、账户或其它完整引用已变化", "PAYMENT_SOURCE_UNKNOWN") from error
    ids = {
        *full.current_version.evidence_ids,
        *(UUID(value) for value in version.evidence_ids),
        payee_id,
        *current_payees,
    }
    sources = _evidence(session, user_id, [str(value) for value in ids], now, lock=False)
    payee = next(row for row in sources if row.id == payee_id)
    if (
        configuration_hash(configuration.model_dump(mode="json"))
        != full.current_version.content_hash
    ):
        raise _error("完整周期配置摘要已变化", "PAYMENT_SOURCE_UNKNOWN")
    return PaymentRelationScope(
        user_id=user_id,
        epoch_id=epoch.id,
        full_policy_id=full.policy_id,
        full_version_id=full.current_version.version_id,
        full_configuration_hash=full.current_version.content_hash,
        original_policy_id=policy.id,
        original_version_id=version.id,
        original_configuration_hash=version.content_hash,
        payee_id=configuration.payee_id,
        payee_evidence_id=payee.id,
        payee_evidence_hash=payee.content_hash,
        source_account_id=source.id,
        amount_rule=configuration.amount_rule,
        source_account_identity_hash=configuration_hash(account_payment_identity(row_copy(source))),
        due_day=configuration.due_day,
        single_action_cap_cents=configuration.single_action_cap_cents,
        auto_execute=configuration.auto_execute,
        timezone=cast(Literal["UTC", "Asia/Shanghai"], user.timezone),
        valid_from=start,
        valid_until=end,
    ), sources


def _scope_request(scope: PaymentRelationScope) -> PaymentScopeRequest:
    return PaymentScopeRequest(
        expected_epoch_id=scope.epoch_id,
        full_policy_id=scope.full_policy_id,
        expected_full_version_id=scope.full_version_id,
        original_policy_id=scope.original_policy_id,
        expected_original_version_id=scope.original_version_id,
    )


def preview_payment_relation(
    session: Session, user_id: UUID, body: PaymentScopeRequest, now: datetime
) -> PaymentScopePreview:
    now = clock_utc(now)
    with audit_read_scope(session):
        scope, _ = _scope(session, user_id, body, now)
    return PaymentScopePreview(scope=scope, scope_hash=payment_scope_hash(scope))


def verified_periodic_transfer_projection_binding(
    session: Session,
    user_id: UUID,
    full: FullPolicyView,
    now: datetime,
) -> PeriodicTransferProjectionBinding:
    """Fresh narrow proof for protection only; never calls planning or an economic evaluator.

    Original Full reference flags and hashes are preserved. Only an actual current
    signed USER-confirmed relation can validate newer same-payee bank evidence.
    """
    _read_snapshot(session)
    base: dict[str, Any] = {
        "full_policy_id": full.policy_id,
        "full_version_id": full.current_version.version_id,
    }
    if full.template_name != "PeriodicTransferPolicy" or full.effective_status != "ACTIVE":
        return PeriodicTransferProjectionBinding(status="NO_CURRENT_DEDICATED_RELATION", **base)
    with audit_read_scope(session):
        proofs = list(
            session.scalars(
                select(EvidenceItem)
                .where(
                    EvidenceItem.user_id == user_id,
                    EvidenceItem.source_type == SOURCE,
                    EvidenceItem.content["kind"].as_string() == "CONFIRM",
                    EvidenceItem.content["scope"]["full_policy_id"].as_string()
                    == str(full.policy_id),
                    EvidenceItem.content["scope"]["full_version_id"].as_string()
                    == str(full.current_version.version_id),
                )
                .order_by(EvidenceItem.id)
            )
        )
        if len(proofs) > 10000:
            return PeriodicTransferProjectionBinding(
                status="UNKNOWN", issues=["PAYMENT_RELATION_CAPACITY"], **base
            )
        found: list[PaymentCommandReceipt] = []
        try:
            for proof in proofs:
                command = PaymentCommandOriginal.model_validate_json(json.dumps(proof.content))
                original = _original(
                    session,
                    user_id,
                    command.command_id,
                    clock_utc(now),
                    replay=True,
                    required_kind="CONFIRM",
                )
                if original is None:
                    raise ValueError("Related original confirmation is absent")
                if original.current_scope_status == "UNKNOWN":
                    raise ValueError("Current dedicated relation sources are unknown")
                if original.current_scope_status == "CURRENT":
                    if (
                        original.original.scope.full_configuration_hash
                        != full.current_version.content_hash
                    ):
                        raise ValueError(
                            "The passed current FULL version differs from original verified scope"
                        )
                    found.append(original)
            if len({row.original.scope.original_policy_id for row in found}) > 1:
                raise ValueError(
                    "A current FULL cadence cannot map to multiple original obligations"
                )
        except (PolicyLifecycleError, ValueError, KeyError, TypeError):
            return PeriodicTransferProjectionBinding(
                status="UNKNOWN", issues=["PAYMENT_RELATION_SOURCE_UNKNOWN"], **base
            )
        if not found:
            return PeriodicTransferProjectionBinding(status="NO_CURRENT_DEDICATED_RELATION", **base)
        original = found[0]
        _, sources = _scope(
            session,
            user_id,
            _scope_request(original.original.scope),
            now,
            pinned_payee=original.original.scope.payee_evidence_id,
        )
        return PeriodicTransferProjectionBinding(
            status="VERIFIED_CURRENT_RELATION",
            **base,
            relation_command_id=original.original.command_id,
            relation_evidence_id=original.evidence_id,
            relation_evidence_hash=original.evidence_hash,
            scope_hash=original.original.scope_hash,
            source_evidence_ids=sorted(
                {original.evidence_id, *(row.id for row in sources)}, key=str
            ),
            source_evidence_hashes={
                str(original.evidence_id): original.evidence_hash,
                **{str(row.id): row.content_hash for row in sources},
            },
        )


def _record(
    session: Session, original: PaymentCommandOriginal, sources: list[EvidenceItem]
) -> None:
    content = original.model_dump(mode="json")
    proof = EvidenceItem(
        id=uuid5(original.command_id, "evidence"),
        user_id=original.user_id,
        created_at=original.recorded_at,
        evidence_level="USER_DECLARED" if original.kind == "START" else "USER_CONFIRMED_POLICY",
        source_type=SOURCE,
        source_ref=str(original.command_id),
        content=content,
        content_hash=configuration_hash(content),
        status="VALID",
        observed_at=original.recorded_at,
        valid_from=original.recorded_at,
        valid_to=original.scope.valid_until,
    )
    session.add(proof)
    session.flush()
    original_version = session.get(PolicyVersion, original.scope.original_version_id)
    original_policy = session.get(Policy, original.scope.original_policy_id)
    if (
        original_version is None
        or original_policy is None
        or (
            original_version.user_id != original.user_id
            or original_policy.user_id != original.user_id
            or original_version.policy_id != original_policy.id
            or original_version.content_hash != original.scope.original_configuration_hash
        )
    ):
        raise _error("原MVP策略版本原件不一致", "PAYMENT_SOURCE_UNKNOWN")
    trace = build_trace(
        run_id=original.command_id,
        user_id=original.user_id,
        phase="EVALUATION",
        as_of=original.recorded_at,
        action_id=None,
        parent_run_id=original.start_command_id,
        algorithm_versions={"trace": "decision-trace-v1", "full_payment_relation": PROTOCOL},
        inputs={
            "kind": original.kind,
            "original_request": original.original_request,
            "request_hash": original.request_hash,
        },
        sources=[evidence_copy(row) for row in [*sources, proof]],
        policies=[policy_copy(original_version, status_at_decision=original_policy.status)],
        outcome={"payment_relation_command": content},
    )
    record_trace(session, trace)


def _original(
    session: Session,
    user_id: UUID,
    command_id: UUID,
    now: datetime,
    *,
    replay: bool,
    required_kind: CommandKind | None = None,
) -> PaymentCommandReceipt | None:
    proof = session.get(EvidenceItem, uuid5(command_id, "evidence"))
    if proof is None:
        return None
    try:
        original = PaymentCommandOriginal.model_validate_json(json.dumps(proof.content))
        if required_kind is not None and original.kind != required_kind:
            raise ValueError("The parent must be the exact original USER initiation")
        trace = get_decision_trace(session, user_id, command_id, now)
        if (
            original.command_id != command_id
            or original.user_id != user_id
            or proof.user_id != user_id
            or proof.source_type != SOURCE
            or proof.source_ref != str(command_id)
            or proof.status != "VALID"
            or proof.evidence_level
            != ("USER_DECLARED" if original.kind == "START" else "USER_CONFIRMED_POLICY")
            or proof.content_hash != configuration_hash(proof.content)
            or proof.created_at != original.recorded_at
            or proof.observed_at != original.recorded_at
            or proof.valid_from != original.recorded_at
            or proof.valid_to != original.scope.valid_until
            or trace.completeness != "COMPLETE"
            or trace.audit_chain_status != "VALID"
            or trace.trace is None
            or trace.trace.outcome != {"payment_relation_command": original.model_dump(mode="json")}
            or trace.trace.inputs
            != {
                "kind": original.kind,
                "original_request": original.original_request,
                "request_hash": original.request_hash,
            }
            or trace.trace.parent_run_id != original.start_command_id
            or len(trace.trace.policies) != 1
            or trace.trace.policies[0].id != original.scope.original_version_id
            or trace.trace.policies[0].policy_id != original.scope.original_policy_id
            or trace.trace.policies[0].user_id != user_id
            or trace.trace.policies[0].configuration_hash
            != original.scope.original_configuration_hash
            or not any(
                row.id == proof.id and row.content_hash == proof.content_hash
                for row in trace.trace.sources
            )
        ):
            raise ValueError("Original receipt/source/trace changed")
        if original.kind == "CONFIRM":
            assert original.start_command_id is not None
            initiation = _original(
                session, user_id, original.start_command_id, now, replay=True, required_kind="START"
            )
            if (
                initiation is None
                or initiation.original.kind != "START"
                or (
                    initiation.original.scope != original.scope
                    or initiation.original.recorded_at > original.recorded_at
                    or not any(
                        row.id == initiation.evidence_id
                        and row.content_hash == initiation.evidence_hash
                        for row in trace.trace.sources
                    )
                )
            ):
                raise ValueError("Original USER initiation is not in the confirmation chain")
    except (ValueError, TypeError, KeyError) as error:
        raise _error(
            "固定收款原命令、确认或审计被改变", "PAYMENT_ORIGINAL_INTEGRITY_ERROR"
        ) from error
    status: Literal["CURRENT", "STALE", "UNKNOWN"] = "UNKNOWN"
    try:
        scope, _ = _scope(
            session,
            user_id,
            _scope_request(original.scope),
            now,
            pinned_payee=original.scope.payee_evidence_id,
        )
        status = "CURRENT" if payment_scope_hash(scope) == original.scope_hash else "STALE"
    except PolicyLifecycleError as error:
        if error.code.startswith("STALE_") or error.code == "PAYMENT_SCOPE_OUTSIDE_VALIDITY":
            status = "STALE"
    return PaymentCommandReceipt(
        original=original,
        evidence_id=proof.id,
        evidence_hash=proof.content_hash,
        trace_hash=trace.trace.trace_hash,
        idempotent_replay=replay,
        current_scope_status=status,
    )


def read_payment_command(
    session: Session, user_id: UUID, epoch_id: UUID, key: Key, now: datetime
) -> PaymentCommandLookup:
    _read_snapshot(session)
    with audit_read_scope(session):
        original = _original(
            session,
            user_id,
            payment_command_identity(user_id, epoch_id, key),
            clock_utc(now),
            replay=True,
        )
    return PaymentCommandLookup(
        user_id=user_id,
        epoch_id=epoch_id,
        idempotency_key=key,
        status="RECORDED" if original else "NOT_FOUND_NOT_FINAL",
        original=original,
    )


def _write_command(
    engine: Engine,
    user_id: UUID,
    kind: CommandKind,
    request: dict[str, Any],
    body: PaymentStartRequest | PaymentConfirmRequest,
    principal: LocalActorPrincipal,
    now: datetime,
    start_command_id: UUID | None,
) -> PaymentCommandReceipt:
    now = clock_utc(now)
    _principal(principal, user_id, now, {"USER"})
    identity = payment_command_identity(user_id, body.expected_epoch_id, body.idempotency_key)
    digest = configuration_hash({"user_id": str(user_id), "kind": kind, "request": request})
    with audit_command_guard(engine, user_id), Session(engine) as writer, writer.begin():
        _user(writer, user_id)
        with _reader(engine) as reader:
            existing = _original(reader, user_id, identity, now, replay=True)
            if existing:
                if existing.original.request_hash != digest or existing.original.kind != kind:
                    raise _error("原键不能替换原发起或确认请求", "IDEMPOTENCY_CONFLICT")
                return existing
            if isinstance(body, PaymentStartRequest):
                scope_request = PaymentScopeRequest.model_validate(
                    body.model_dump(include=set(PaymentScopeRequest.model_fields))
                )
                scope, sources = _scope(reader, user_id, scope_request, now)
            else:
                assert start_command_id is not None
                start = _original(reader, user_id, start_command_id, now, replay=True)
                if (
                    start is None
                    or start.original.kind != "START"
                    or start.current_scope_status != "CURRENT"
                ):
                    raise _error(
                        "必须有当前匹配的真实USER原发起", "PAYMENT_USER_INITIATION_REQUIRED"
                    )
                if (
                    start.original.epoch_id != body.expected_epoch_id
                    or start.original.scope_hash != body.reviewed_scope_hash
                ):
                    raise _error("请重新明确复核原收款范围", "PAYMENT_SCOPE_REVIEW_MISMATCH")
                scope, sources = _scope(
                    reader,
                    user_id,
                    _scope_request(start.original.scope),
                    now,
                    pinned_payee=start.original.scope.payee_evidence_id,
                )
                if payment_scope_hash(scope) != body.reviewed_scope_hash:
                    raise _error("发起后原金融范围已变化", "PAYMENT_SCOPE_REVIEW_MISMATCH")
                initiation = reader.get(EvidenceItem, start.evidence_id)
                if initiation is None:
                    raise _error("原发起证据缺失", "PAYMENT_SOURCE_UNKNOWN")
                sources.append(initiation)
        original = PaymentCommandOriginal(
            command_id=identity,
            user_id=user_id,
            epoch_id=body.expected_epoch_id,
            kind=kind,
            idempotency_key=body.idempotency_key,
            start_command_id=start_command_id,
            original_request=request,
            request_hash=digest,
            principal_at_command=principal,
            scope=scope,
            scope_hash=payment_scope_hash(scope),
            recorded_at=now,
        )
        _record(writer, original, sources)
    with _reader(engine) as reader:
        result = _original(reader, user_id, identity, now, replay=False)
    if result is None:
        raise _error("已提交原命令尚无法读取，保留原键")
    return result


def start_payment_relation(
    engine: Engine,
    user_id: UUID,
    body: PaymentStartRequest,
    principal: LocalActorPrincipal,
    now: datetime,
) -> PaymentCommandReceipt:
    return _write_command(
        engine, user_id, "START", body.model_dump(mode="json"), body, principal, now, None
    )


def confirm_payment_relation(
    engine: Engine,
    user_id: UUID,
    start_command_id: UUID,
    body: PaymentConfirmRequest,
    principal: LocalActorPrincipal,
    now: datetime,
) -> PaymentCommandReceipt:
    return _write_command(
        engine,
        user_id,
        "CONFIRM",
        {"start_command_id": str(start_command_id), "confirmation": body.model_dump(mode="json")},
        body,
        principal,
        now,
        start_command_id,
    )


def _verify_effect(
    session: Session,
    scope: PaymentRelationScope,
    effect: ExecutionEffect,
    now: datetime,
    *,
    current: bool = True,
) -> None:
    if effect.payee_evidence_id is None:
        raise _error("原付款缺银行收款身份")
    _counterparty_binding(
        session,
        scope.user_id,
        scope.payee_id,
        effect.payee_evidence_id,
        now if current else effect.valid_from,
    )
    try:
        verify_payment_effect(
            scope,
            effect,
            now,
            require_current=current,
            verified_payee_evidence_ids={effect.payee_evidence_id},
        )
    except ValueError as error:
        raise _error(str(error), "PAYMENT_EFFECT_OUTSIDE_SCOPE") from error


def _action_binding(
    session: Session, user_id: UUID, action_id: UUID, now: datetime
) -> PaymentActionBinding:
    proof = session.get(EvidenceItem, uuid5(action_id, "full-payment-binding"))
    if proof is None or proof.user_id != user_id:
        raise _error("未登记原固定收款动作绑定", "NOT_FOUND", 404)
    try:
        binding = PaymentActionBinding.model_validate_json(json.dumps(proof.content))
        if (
            binding.user_id != user_id
            or binding.action_id != action_id
            or binding.scope.user_id != user_id
            or binding.scope.epoch_id != binding.epoch_id
            or binding.scope_hash != payment_scope_hash(binding.scope)
            or proof.source_type != BINDING_SOURCE
            or proof.source_ref != str(action_id)
            or proof.evidence_level != "USER_CONFIRMED_POLICY"
            or proof.status != "VALID"
            or proof.content_hash != configuration_hash(proof.content)
            or proof.observed_at != binding.recorded_at
            or proof.created_at != binding.recorded_at
            or proof.valid_from != binding.recorded_at
            or proof.valid_to != binding.scope.valid_until
            or binding.period != binding.original_prepare_request.period
            or binding.original_prepare_request.expected_epoch_id != binding.epoch_id
        ):
            raise ValueError("Original binding changed")
        authorization = _original(session, user_id, binding.authorization_id, now, replay=True)
        if (
            authorization is None
            or authorization.original.kind != "CONFIRM"
            or (
                authorization.evidence_id != binding.authorization_evidence_id
                or authorization.evidence_hash != binding.authorization_evidence_hash
                or authorization.original.scope != binding.scope
            )
        ):
            raise ValueError("Original dedicated consent changed")
        preparation = _preparation_original(
            session, user_id, binding.authorization_id, binding.original_prepare_request, now
        )
        if (
            preparation is None
            or preparation.authorization_evidence_hash != binding.authorization_evidence_hash
        ):
            raise ValueError("The original submitted preparation is missing or changed")
        trace = get_decision_trace(
            session, user_id, uuid5(action_id, "full-payment-binding-trace"), now
        )
        if (
            trace.completeness != "COMPLETE"
            or trace.audit_chain_status != "VALID"
            or trace.trace is None
            or trace.trace.outcome
            != {"full_payment_action_binding": binding.model_dump(mode="json")}
        ):
            raise ValueError("Original binding audit absent")
        action_row = session.get(ActionPlan, action_id)
        if (
            action_row is None
            or action_row.user_id != user_id
            or (
                action_row.idempotency_key
                != original_payment_action_key(
                    binding.authorization_id, binding.original_prepare_request.idempotency_key
                )
                or action_row.request.get("intent")
                != PaymentIntent(
                    kind="pay_recurring",
                    policy_id=binding.scope.original_policy_id,
                    period=binding.period,
                ).model_dump(mode="json")
                or trace.trace.action_id != action_id
                or trace.trace.parent_run_id != action_row.decision_run_id
                or trace.trace.inputs
                != {
                    "authorization_id": str(binding.authorization_id),
                    "request": binding.original_prepare_request.model_dump(mode="json"),
                }
                or not any(
                    row.id == proof.id and row.content_hash == proof.content_hash
                    for row in trace.trace.sources
                )
            )
        ):
            raise ValueError("Original prepared request or trace anchor changed")
        action = get_action(session, user_id, action_id, now)
        if action.effect_hash != binding.original_effect_hash:
            raise ValueError("Original effect hash changed")
        _verify_effect(session, binding.scope, action.effect, now, current=False)
    except (ValueError, TypeError, KeyError) as error:
        raise _error("原周期付款动作绑定尚未验真", "PAYMENT_ORIGINAL_INTEGRITY_ERROR") from error
    return binding


def read_prepared_payment(
    session: Session,
    user_id: UUID,
    authorization_id: UUID,
    key: Key,
    now: datetime,
) -> PaymentPreparedLookup:
    _read_snapshot(session)
    with audit_read_scope(session):
        action = session.scalar(
            select(ActionPlan).where(
                ActionPlan.user_id == user_id,
                ActionPlan.idempotency_key == original_payment_action_key(authorization_id, key),
            )
        )
        binding = _action_binding(session, user_id, action.id, clock_utc(now)) if action else None
        original = get_action(session, user_id, action.id, now) if action else None
    return PaymentPreparedLookup(
        user_id=user_id,
        authorization_id=authorization_id,
        idempotency_key=key,
        status="RECORDED" if action else "NOT_FOUND_NOT_FINAL",
        original_binding=binding,
        original_action=original,
    )


def prepare_full_payment(
    engine: Engine,
    user_id: UUID,
    authorization_id: UUID,
    body: PaymentPrepareRequest,
    principal: LocalActorPrincipal,
    now: datetime,
) -> ActionResponse:
    now = clock_utc(now)
    _principal(principal, user_id, now, {"USER", "AGENT", "SYSTEM"})
    key = payment_prepare_key(authorization_id, body.idempotency_key)
    with audit_command_guard(engine, user_id):
        with _reader(engine) as reader:
            prior_action = reader.scalar(
                select(ActionPlan).where(
                    ActionPlan.user_id == user_id,
                    ActionPlan.idempotency_key
                    == original_payment_action_key(authorization_id, body.idempotency_key),
                )
            )
            if (
                prior_action is not None
                and reader.get(EvidenceItem, uuid5(prior_action.id, "full-payment-binding"))
                is not None
            ):
                prior = read_prepared_payment(
                    reader, user_id, authorization_id, body.idempotency_key, now
                )
                if (
                    prior.original_binding is None
                    or prior.original_action is None
                    or (prior.original_binding.original_prepare_request != body)
                ):
                    raise _error("原准备键不能换周期或确认范围", "IDEMPOTENCY_CONFLICT")
                return prior.original_action
            authorization = _original(reader, user_id, authorization_id, now, replay=True)
            if (
                authorization is None
                or authorization.original.kind != "CONFIRM"
                or authorization.current_scope_status != "CURRENT"
            ):
                raise _error("固定收款需当前USER明确范围确认")
            scope = authorization.original.scope
            if (
                body.expected_epoch_id != scope.epoch_id
                or principal.role != "USER"
                and not scope.auto_execute
            ):
                raise _error(
                    "Agent/System不能发起未自主授权的外付", "PAYMENT_ROLE_NOT_AUTHORIZED", 403
                )
            request = PrepareActionRequest(
                idempotency_key=key,
                intent=PaymentIntent(
                    kind="pay_recurring", policy_id=scope.original_policy_id, period=body.period
                ),
            )
            # Preflight uses original real facts, not a client amount or fabricated ActionPlan.
            effect = plan_execution_effect(
                reader, user_id, uuid5(authorization_id, key), request.intent, now
            )
            _verify_effect(reader, scope, effect, now)
        _record_preparation_original(engine, user_id, authorization, body, principal, now)
        result = prepare_action(engine, user_id, request, now)
        with Session(engine) as writer, writer.begin():
            _user(writer, user_id)
            prior_proof = writer.get(EvidenceItem, uuid5(result.action_id, "full-payment-binding"))
            if prior_proof is not None:
                with _reader(engine) as reader:
                    binding = _action_binding(reader, user_id, result.action_id, now)
                if (
                    binding.authorization_id != authorization_id
                    or binding.original_prepare_request != body
                ):
                    raise _error("原准备键不能换周期或确认范围", "IDEMPOTENCY_CONFLICT")
                return result
            with _reader(engine) as reader:
                current = _original(reader, user_id, authorization_id, now, replay=True)
                if current is None or current.current_scope_status != "CURRENT":
                    raise _error("准备后规则已变化，原行动保留未执行")
                _verify_effect(reader, scope, result.effect, now)
            binding = PaymentActionBinding(
                user_id=user_id,
                epoch_id=scope.epoch_id,
                action_id=result.action_id,
                authorization_id=authorization_id,
                authorization_evidence_id=authorization.evidence_id,
                authorization_evidence_hash=authorization.evidence_hash,
                scope=scope,
                scope_hash=authorization.original.scope_hash,
                period=body.period,
                original_prepare_request=body,
                original_effect_hash=result.effect_hash,
                recorded_at=now,
            )
            content = binding.model_dump(mode="json")
            proof = EvidenceItem(
                id=uuid5(result.action_id, "full-payment-binding"),
                user_id=user_id,
                created_at=now,
                observed_at=now,
                valid_from=now,
                valid_to=scope.valid_until,
                evidence_level="USER_CONFIRMED_POLICY",
                source_type=BINDING_SOURCE,
                source_ref=str(result.action_id),
                content=content,
                content_hash=configuration_hash(content),
                status="VALID",
            )
            writer.add(proof)
            writer.flush()
            trace = build_trace(
                run_id=uuid5(result.action_id, "full-payment-binding-trace"),
                user_id=user_id,
                phase="EVALUATION",
                as_of=now,
                action_id=result.action_id,
                parent_run_id=result.decision_run_id,
                algorithm_versions={
                    "trace": "decision-trace-v1",
                    "full_payment_relation": PROTOCOL,
                },
                inputs={
                    "authorization_id": str(authorization_id),
                    "request": body.model_dump(mode="json"),
                },
                sources=[evidence_copy(proof)],
                policies=[],
                outcome={"full_payment_action_binding": content},
            )
            record_trace(writer, trace)
        return result


def enforce_full_payment_bank_scope(
    engine: Engine, user_id: UUID, effect: ExecutionEffect, now: datetime
) -> None:
    """Root bank hook: call before new legs while its actual user writer lock is held.

    Legacy actions without this new dedicated binding keep their old permissions.
    A recorded binding is never ignored on stale/full/cap/payee/source failure.
    """
    with _reader(engine) as reader:
        if reader.get(EvidenceItem, uuid5(effect.operation_id, "full-payment-binding")) is None:
            if (
                reader.get(DecisionRun, uuid5(effect.operation_id, "full-payment-binding-trace"))
                is not None
            ):
                raise _error(
                    "原动作绑定证据丢失，不能降为旧付款", "PAYMENT_ORIGINAL_INTEGRITY_ERROR"
                )
            unbound_action = reader.get(ActionPlan, effect.operation_id)
            if (
                unbound_action is not None
                and reader.scalar(
                    select(DecisionRun.id).where(
                        DecisionRun.user_id == user_id,
                        DecisionRun.input_snapshot["decision_trace"]["inputs"][
                            "full_payment_action_key"
                        ].as_string()
                        == unbound_action.idempotency_key,
                    )
                )
                is not None
            ):
                raise _error(
                    "原Full准备尚未完成范围绑定，不能按旧权限受理", "PAYMENT_BINDING_NOT_FINAL"
                )
            return
        binding = _action_binding(reader, user_id, effect.operation_id, clock_utc(now))
        authorization = _original(reader, user_id, binding.authorization_id, now, replay=True)
        if authorization is None or authorization.current_scope_status != "CURRENT":
            raise _error("银行受理前完整规则或明确范围确认已失效", "STALE_PAYMENT_AUTHORIZATION")
        _verify_effect(reader, binding.scope, effect, now)
        action = get_action(reader, user_id, effect.operation_id, now)
        if (
            action.autonomy_level != "AUTO_EXECUTE"
            and _action_consent(reader, binding, action, now) is None
        ):
            raise _error(
                "新的ASK付款必须有原签名USER逐次同意", "PAYMENT_USER_ACTION_CONSENT_REQUIRED"
            )


def confirm_full_payment_action(
    engine: Engine,
    user_id: UUID,
    action_id: UUID,
    body: PaymentActionConfirmation,
    principal: LocalActorPrincipal,
    now: datetime,
) -> ActionResponse:
    now = clock_utc(now)
    _principal(principal, user_id, now, {"USER"})
    with audit_command_guard(engine, user_id):
        with _reader(engine) as reader:
            binding = _action_binding(reader, user_id, action_id, now)
            if (
                binding.epoch_id != body.expected_epoch_id
                or binding.original_effect_hash != body.reviewed_effect_hash
            ):
                raise _error("确认必须对应原周期与原经济后果", "CONFIRMATION_MISMATCH")
            action = get_action(reader, user_id, action_id, now)
            prior = _action_consent(reader, binding, action, now)
            if prior is not None:
                return action
            authorization = _original(reader, user_id, binding.authorization_id, now, replay=True)
            if authorization is None or authorization.current_scope_status != "CURRENT":
                raise _error("逐次确认前原完整范围已失效", "STALE_PAYMENT_AUTHORIZATION")
            _verify_effect(reader, binding.scope, action.effect, now)
        result = confirm_action(
            engine,
            user_id,
            action_id,
            ConfirmActionRequest(effect_hash=body.reviewed_effect_hash, accepted=True),
            now,
        )
        with Session(engine) as writer, writer.begin():
            _user(writer, user_id)
            with _reader(engine) as reader:
                # Preserve a response-loss replay of this exact consent, not a new grant.
                prior = _action_consent(reader, binding, result, now)
                if prior is not None:
                    return result
                grant = read_execution_confirmation(reader, result.effect, now)
                current = _original(reader, user_id, binding.authorization_id, now, replay=True)
                if grant is None or current is None or current.current_scope_status != "CURRENT":
                    raise _error("原确认已保存但新范围变化；保留原行动，不发起银行付款")
                _verify_effect(reader, binding.scope, result.effect, now)
            consent = PaymentUserActionConsent(
                user_id=user_id,
                epoch_id=binding.epoch_id,
                action_id=action_id,
                original_effect_hash=result.effect_hash,
                original_confirmation_evidence_id=grant.evidence_id,
                principal_at_confirmation=principal,
                confirmed_at=now,
            )
            content = consent.model_dump(mode="json")
            proof = EvidenceItem(
                id=uuid5(action_id, "full-payment-user-consent"),
                user_id=user_id,
                evidence_level="USER_CONFIRMED_ACTION",
                source_type=CONSENT_SOURCE,
                source_ref=str(action_id),
                content=content,
                content_hash=configuration_hash(content),
                created_at=now,
                observed_at=now,
                valid_from=now,
                valid_to=result.effect.expires_at,
                status="VALID",
            )
            writer.add(proof)
            writer.flush()
            trace = build_trace(
                run_id=uuid5(action_id, "full-payment-user-consent-trace"),
                user_id=user_id,
                phase="EVALUATION",
                as_of=now,
                action_id=action_id,
                parent_run_id=uuid5(action_id, "full-payment-binding-trace"),
                algorithm_versions={
                    "trace": "decision-trace-v1",
                    "full_payment_relation": PROTOCOL,
                },
                inputs={
                    "reviewed_effect_hash": result.effect_hash,
                    "accepted": True,
                    "expected_epoch_id": str(binding.epoch_id),
                },
                sources=[evidence_copy(proof)],
                policies=[],
                outcome={"full_payment_user_action_consent": content},
            )
            record_trace(writer, trace)
        return result


def execute_full_payment(
    engine: Engine,
    user_id: UUID,
    action_id: UUID,
    body: PaymentExecuteRequest,
    principal: LocalActorPrincipal,
    now: datetime,
    *,
    guarded_executor: PaymentExecutor | None = None,
) -> ActionResponse:
    """A real original execute adapter; missing mandatory bank hook fails before writes."""
    now = clock_utc(now)
    _principal(principal, user_id, now, {"USER", "AGENT", "SYSTEM"})
    if guarded_executor is None:
        raise _error(
            "银行新受理范围守卫尚未接线，不能只靠预览发起实际付款",
            "PAYMENT_BANK_GUARD_NOT_INSTALLED",
            503,
        )
    with audit_command_guard(engine, user_id):
        with _reader(engine) as reader:
            binding = _action_binding(reader, user_id, action_id, now)
            if body.expected_epoch_id != binding.epoch_id:
                raise _error("恢复必须保留原周期与原行动", "STALE_AUDIT_EPOCH")
            result = get_action(reader, user_id, action_id, now)
            bank = reader.scalar(
                select(BankOperation).where(
                    BankOperation.user_id == user_id, BankOperation.action_plan_id == action_id
                )
            )
            if bank is None:
                if principal.role != "USER" and (
                    not binding.scope.auto_execute or result.autonomy_level != "AUTO_EXECUTE"
                ):
                    raise _error(
                        "自主外付只能消费原明确AUTO关系，不能替USER单次确认",
                        "PAYMENT_ROLE_NOT_AUTHORIZED",
                        403,
                    )
                enforce_full_payment_bank_scope(engine, user_id, result.effect, now)
            # Existing bank key reconciles its original bank effect; no fresh consent is invented.
        return guarded_executor(engine, user_id, action_id, now)


def read_full_payment_action(
    session: Session, user_id: UUID, action_id: UUID, now: datetime
) -> ActionResponse:
    _read_snapshot(session)
    with audit_read_scope(session):
        _action_binding(session, user_id, action_id, clock_utc(now))
        return get_action(session, user_id, action_id, now)


def read_payment_action_consent(
    session: Session,
    user_id: UUID,
    action_id: UUID,
    now: datetime,
) -> PaymentConsentLookup:
    _read_snapshot(session)
    with audit_read_scope(session):
        binding = _action_binding(session, user_id, action_id, clock_utc(now))
        action = get_action(session, user_id, action_id, now)
        consent = _action_consent(session, binding, action, now)
    return PaymentConsentLookup(
        action_id=action_id,
        status="RECORDED" if consent else "NOT_FOUND_NOT_FINAL",
        original=consent,
        original_request=PaymentActionConfirmation(
            expected_epoch_id=binding.epoch_id,
            reviewed_effect_hash=binding.original_effect_hash,
            accepted=True,
        )
        if consent
        else None,
    )


def payment_recheck_disposition(
    status: str,
    *,
    scope_changed: bool,
    bank_count: int,
    receipt_count: int,
    posting_count: int,
    claim_count: int,
    legacy_count: int,
    verified_settlement: bool,
) -> str:
    counts = (bank_count, receipt_count, posting_count, claim_count, legacy_count)
    if any(type(count) is not int or count < 0 for count in counts):
        raise ValueError("Complete original integer row denominators are required")
    if verified_settlement and status in {"SUCCEEDED", "RECONCILED"}:
        return "TERMINAL"
    if any(counts) or status in {"SUBMITTED", "UNKNOWN", "SUCCEEDED", "RECONCILED"}:
        return "INFLIGHT"
    if status in {"PLANNED", "AUTHORIZED"}:
        return "INVALIDATE" if scope_changed else "RETAIN"
    return (
        "TERMINAL" if status in {"INVALIDATED", "FAILED", "REJECTED", "CANCELLED"} else "INFLIGHT"
    )


def _settled_payment_claims(
    effect: ExecutionEffect,
    claims: list[ActionResourceReservation],
    now: datetime,
) -> bool:
    expected = {("CASH", str(use.account_id)): use.amount_cents for use in effect.cash_uses}
    expected[("BUSINESS", effect.business_key)] = 1
    if effect.income_uses or len(claims) != len(expected):
        return False  # Current original PAY_RECURRING does not reserve income fragments.
    return all(
        row.user_id == effect.user_id
        and row.action_plan_id == effect.operation_id
        and row.status == "CONSUMED"
        and row.resolved_at is not None
        and row.resolved_at <= now
        and row.id == uuid5(effect.operation_id, f"resource:{row.resource_kind}:{row.resource_key}")
        and expected.get((row.resource_kind, row.resource_key)) == row.amount_cents
        for row in claims
    ) and len({(row.resource_kind, row.resource_key) for row in claims}) == len(expected)


def recheck_full_payment_actions(
    session: Session,
    user_id: UUID,
    full_policy_id: UUID,
    current_version_id: UUID,
    current_status: str,
    command_id: UUID,
    now: datetime,
) -> FullPaymentActionRecheckResult:
    """Known payment adapter only, under the caller's actual User writer lock.

    A verified historical scope is not current authority. New policy version or
    status only invalidates completely unsubmitted actions. Bank originals are
    preserved and are never called zero-effect based on a status string alone.
    """
    from app.services.audit_recording import audit_subject_data, record_action_transition
    from app.services.execution_exposure import refresh_execution_exposure

    now = clock_utc(now)
    _user(session, user_id)
    if verify_audit_chain(session, user_id).status != "VALID":
        raise _error("原动作完整审计未验真，不能声明完成重查", "FULL_ACTION_RECHECK_UNVERIFIED")
    result = FullPaymentActionRecheckResult(full_policy_id=full_policy_id)
    proofs = list(
        session.scalars(
            select(EvidenceItem)
            .where(
                EvidenceItem.user_id == user_id,
                EvidenceItem.source_type.in_([BINDING_SOURCE, PREPARE_SOURCE]),
            )
            .order_by(EvidenceItem.id)
        )
    )
    if len(proofs) > 10000:
        raise _error("专用付款重查原件超容量", "FULL_ACTION_RECHECK_UNVERIFIED")
    scopes: dict[str, PaymentRelationScope] = {}
    binding_actions: dict[str, UUID] = {}
    for proof in proofs:
        try:
            if proof.source_type == BINDING_SOURCE:
                binding = PaymentActionBinding.model_validate_json(json.dumps(proof.content))
                if binding.scope.full_policy_id != full_policy_id:
                    continue
                binding = _action_binding(session, user_id, binding.action_id, now)
                key = original_payment_action_key(
                    binding.authorization_id, binding.original_prepare_request.idempotency_key
                )
                scopes[key], binding_actions[key] = binding.scope, binding.action_id
            else:
                prepared = PaymentPreparationOriginal.model_validate_json(json.dumps(proof.content))
                authority = _original(
                    session,
                    user_id,
                    prepared.authorization_id,
                    now,
                    replay=True,
                    required_kind="CONFIRM",
                )
                if authority is None:
                    raise ValueError("Original user relation is absent")
                if authority.original.scope.full_policy_id != full_policy_id:
                    continue
                verified = _preparation_original(
                    session, user_id, prepared.authorization_id, prepared.request, now
                )
                if (
                    verified is None
                    or verified.authorization_evidence_hash != authority.evidence_hash
                ):
                    raise ValueError("Original preparation relation is absent or changed")
                scopes[verified.full_payment_action_key] = authority.original.scope
        except (ValueError, KeyError, TypeError) as error:
            raise _error("原关联动作或准备原件无效", "FULL_ACTION_RECHECK_UNVERIFIED") from error
    if not scopes:
        return result
    actions = list(
        session.scalars(
            select(ActionPlan)
            .where(
                ActionPlan.user_id == user_id,
                ActionPlan.idempotency_key.in_(scopes),
            )
            .order_by(ActionPlan.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    declarations: dict[UUID, dict[str, Any]] = {}
    for action in actions:
        scope = scopes[action.idempotency_key]
        if (
            action.idempotency_key in binding_actions
            and action.id != binding_actions[action.idempotency_key]
        ):
            raise _error("原准备键换了动作身份", "FULL_ACTION_RECHECK_UNVERIFIED")
        original = get_action(session, user_id, action.id, now)
        trace = get_decision_trace(session, user_id, action.decision_run_id, now)
        if trace.completeness != "COMPLETE" or trace.audit_chain_status != "VALID":
            raise _error("原准备决策未验真", "FULL_ACTION_RECHECK_UNVERIFIED")
        _verify_effect(session, scope, original.effect, now, current=False)
        banks = list(
            session.scalars(
                select(BankOperation).where(
                    or_(BankOperation.action_plan_id == action.id, BankOperation.id == action.id)
                )
            )
        )
        receipts = list(
            session.scalars(select(ActionReceipt).where(ActionReceipt.action_plan_id == action.id))
        )
        legacy = list(
            session.scalars(
                select(SimulatedBankRedemption).where(
                    SimulatedBankRedemption.action_plan_id == action.id
                )
            )
        )
        bank_ids = {action.id, *(row.id for row in banks), *(row.id for row in legacy)}
        postings = list(
            session.scalars(
                select(SimulatedBankPosting).where(SimulatedBankPosting.operation_id.in_(bank_ids))
            )
        )
        claims = list(
            session.scalars(
                select(ActionResourceReservation).where(
                    ActionResourceReservation.action_plan_id == action.id
                )
            )
        )
        verified_settlement = (
            original.status in {"SUCCEEDED", "RECONCILED"}
            and original.bank_status == "SETTLED"
            and original.receipt is not None
            and len(banks) == len(receipts) == 1
            and banks[0].user_id == receipts[0].user_id == user_id
            and not legacy
            and _settled_payment_claims(original.effect, claims, now)
        )  # get_action independently verifies the original receipt and all conserved legs.
        changed = current_status not in {"ACTIVE", "CONFIRMED"} or (
            scope.full_version_id != current_version_id
            or not scope.valid_from <= now < scope.valid_until
        )
        disposition = payment_recheck_disposition(
            action.status,
            scope_changed=changed,
            bank_count=len(banks),
            receipt_count=len(receipts),
            posting_count=len(postings),
            claim_count=len(claims),
            legacy_count=len(legacy),
            verified_settlement=verified_settlement,
        )
        if disposition == "INVALIDATE":
            before_status, before_data = action.status, audit_subject_data(action)
            action.status = "INVALIDATED"
            session.flush()
            record_action_transition(
                session,
                action,
                before_status,
                now,
                reason_code="FULL_FIXED_PAYMENT_SCOPE_INVALIDATED_BEFORE_BANK_ACCEPT",
                cause_ref=f"full-policy-command:{command_id}",
                details={
                    "full_policy_id": str(full_policy_id),
                    "current_full_policy_version_id": str(current_version_id),
                    "original_full_policy_version_id": str(scope.full_version_id),
                    "no_effect_status": "CONFIRMED",
                    "original_bank_operation_count": 0,
                    "original_receipt_count": 0,
                    "original_posting_count": 0,
                    "original_claim_count": 0,
                    "original_legacy_redemption_count": 0,
                    "creates_new_income": False,
                },
                before_data=before_data,
            )
            declarations[action.id] = {"action_id": str(action.id), "state": "NO_EFFECT"}
            result.invalidated_action_ids.append(action.id)
        elif disposition == "INFLIGHT":
            result.inflight_action_ids.append(action.id)
        elif disposition == "TERMINAL":
            result.terminal_action_ids.append(action.id)
        else:
            result.retained_action_ids.append(action.id)
    if declarations:
        refresh_execution_exposure(session, user_id, now, command_id, declarations=declarations)
    return result
