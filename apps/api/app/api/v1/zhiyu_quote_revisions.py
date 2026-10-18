"""Closed append-only quote revision and explicit renewed USER acceptance routes."""

from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID, uuid5

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.v1.zhiyu_assets import (
    EpochRequest,
    LossAcceptanceRequest,
    LossOriginalRequest,
    command_key,
    decode_record,
    failure,
    loss_action_by_key,
    require_closure_guards,
    signed_user,
)
from app.api.v1.zhiyu_catalog import identity, isolated
from app.api.v1.zhiyu_next import EngineDependency
from app.api.v1.zhiyu_policy_review import PrincipalDependency
from app.db.models import ActionPlan, EvidenceItem
from app.domain.boundary_types import BoundaryModel
from app.domain.execution import execution_effect_hash
from app.domain.execution_types import BankCommand, ExecutionEffect
from app.domain.full_asset_execution import Hash
from app.domain.local_actor_session_types import LocalActorPrincipal, require_local_user
from app.domain.policy_configuration import UUIDReference, configuration_hash
from app.domain.recovery_types import RecoveryQuote
from app.domain.zhiyu_quote_revision import (
    LOSS_V2,
    PRICE_V2,
    QuoteRenewalRequest,
    RevisionLossPrepareRequest,
    decode_renewal_command,
)
from app.services.action_contracts import (
    ActionResponse,
    ConfirmActionRequest,
    PrepareActionRequest,
    RedeemIntent,
)
from app.services.audit_chain import audit_read_scope, verify_audit_chain
from app.services.demo_console import _epoch
from app.services.execution import confirm_action, execute_action, get_action
from app.services.full_asset_execution_dispatch import fresh_read
from app.services.historical_read import historical_ledger_scope
from app.services.zhiyu_asset_loss import installed_loss_prepare, read_original_loss_native
from app.services.zhiyu_orchestration import MARKER, _valid, serial_user, store
from app.services.zhiyu_quote_revisions import (
    load_current_revision_quote,
    read_revision_original,
    renew_quote,
    require_no_accepted_redemption,
)
from fastapi import APIRouter, Depends
from pydantic import model_validator
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

PREFIX = "/api/v1/zhiyu-next/assets/lossy-revisions"
router = APIRouter(prefix=PREFIX, tags=["知余续报价"], dependencies=[Depends(isolated)])


def quote_display(quote: RecoveryQuote, now: datetime) -> dict[str, Any]:
    """Server clock display only; it never replaces a native acceptance guard."""
    return {
        "server_as_of": now.isoformat(),
        "quote_validity": {
            "status": "VALID" if quote.request_at <= now < quote.expires_at else "EXPIRED",
            "as_of": now.isoformat(),
            "expires_at": quote.expires_at.isoformat(),
        },
    }


class RevisionPrepareRequest(EpochRequest):
    position_id: UUIDReference
    quote_id: UUIDReference
    client_request_id: UUIDReference


class RevisionIntent(BoundaryModel):
    protocol: Literal["lossy-early-redemption-quote-revision-v2"] = LOSS_V2
    user_id: UUID
    epoch_id: UUID
    original_request: RevisionPrepareRequest
    native_key: str
    principal_at_capture: LocalActorPrincipal
    captured_at: datetime
    bank_authority: Literal[False] = False

    @model_validator(mode="after")
    def signed_original(self) -> Self:
        require_local_user(self.principal_at_capture, self.user_id, self.captured_at)
        if (
            self.epoch_id != self.original_request.expected_epoch_id
            or self.native_key
            != command_key(self.epoch_id, "lossy-revision", self.original_request.client_request_id)
        ):
            raise ValueError("The renewed intent must bind one signed original request")
        return self


class RevisionActionBinding(BoundaryModel):
    protocol: Literal["lossy-early-redemption-quote-revision-v2"] = LOSS_V2
    user_id: UUID
    epoch_id: UUID
    action_id: UUID
    source_request: RevisionPrepareRequest
    effect: ExecutionEffect
    effect_hash: Hash
    quote: RecoveryQuote
    quote_hash: Hash
    quote_evidence_hash: Hash
    bank_authority: Literal[False] = False

    @model_validator(mode="after")
    def exact_quote_effect(self) -> Self:
        effect, quote = self.effect, self.quote
        if (
            self.user_id != effect.user_id
            or self.user_id != quote.user_id
            or self.epoch_id != self.source_request.expected_epoch_id
            or self.action_id != effect.operation_id
            or self.source_request.position_id != quote.position_id
            or self.source_request.quote_id != quote.quote_id
            or effect.position_id != quote.position_id
            or effect.quote_id != quote.quote_id
            or effect.action_type != "REDEEM_ASSET"
            or effect.business_key != f"redeem:{quote.position_id}"
            or effect.amount_cents != quote.principal_cents
            or effect.net_cents != quote.net_cents
            or effect.fee_cents != quote.fee_cents
            or effect.loss_cents != quote.loss_cents
            or effect.loss_cents <= 0
            or effect.product_id != quote.product_id
            or effect.product_version_number != quote.product_version_number
            or effect.terms_digest != quote.terms_digest
            or not quote.request_at <= effect.valid_from < effect.expires_at <= quote.expires_at
            or self.effect_hash != execution_effect_hash(effect)
            or self.quote_hash != configuration_hash(quote.model_dump(mode="json"))
        ):
            raise ValueError("The complete renewed quote and economic effect must agree")
        return self


class SignedRevisionAcceptance(BoundaryModel):
    protocol: Literal["lossy-early-redemption-quote-revision-user-v2"] = (
        "lossy-early-redemption-quote-revision-user-v2"
    )
    user_id: UUID
    epoch_id: UUID
    action_id: UUID
    original_request: LossAcceptanceRequest
    principal_at_acceptance: LocalActorPrincipal
    accepted_at: datetime
    expires_at: datetime
    bank_authority: Literal[False] = False

    @model_validator(mode="after")
    def actual_user(self) -> Self:
        require_local_user(self.principal_at_acceptance, self.user_id, self.accepted_at)
        if (
            self.epoch_id != self.original_request.expected_epoch_id
            or not self.accepted_at < self.expires_at <= self.principal_at_acceptance.expires_at
        ):
            raise ValueError("A renewed acceptance requires a current signed USER")
        return self


def anchored(session: Session, user_id: UUID, kind: str, key: str) -> dict[str, Any] | None:
    epoch = _epoch(session, user_id)
    row = session.get(EvidenceItem, uuid5(epoch, f"{MARKER}:{kind}:{key}"))
    if row is None:
        return None
    if verify_audit_chain(session, user_id, epoch).status != "VALID":
        raise failure("续报价审计原件无法核验")
    return _valid(session, row, user_id, epoch)["payload"]  # type: ignore[no-any-return]


def require_epoch(session: Session, user_id: UUID, epoch_id: UUID) -> None:
    if _epoch(session, user_id) != epoch_id:
        raise failure("续报价原请求跨轮次", "EPOCH_CONFLICT")


def prepare_revision(
    engine: Engine,
    user_id: UUID,
    body: RevisionPrepareRequest,
    actor: LocalActorPrincipal,
    now: datetime,
) -> ActionResponse:
    require_closure_guards()
    signed_user(actor, user_id, now)
    key = command_key(body.expected_epoch_id, "lossy-revision", body.client_request_id)
    with serial_user(engine, user_id):
        with Session(engine) as writer, writer.begin():
            from app.services.execution import _lock_user

            _lock_user(writer, user_id)
            require_epoch(writer, user_id, body.expected_epoch_id)
            command = anchored(
                writer, user_id, "BINDING", f"asset-loss-revision-command:{body.client_request_id}"
            )
            if command is not None and command.get("original_request") != body.model_dump(
                mode="json"
            ):
                raise failure("同一续报价动作原键不能改变请求", "REQUEST_CONFLICT")
            original = anchored(
                writer, user_id, "EVENT", f"asset-loss-revision-intent:{body.quote_id}"
            )
            if original is not None:
                intent = decode_record(RevisionIntent, original)
                if intent.original_request != body or intent.user_id != user_id:
                    raise failure("同一报价不可由另一原键重新准备", "REQUEST_CONFLICT")
            else:
                require_no_accepted_redemption(writer, user_id, body.position_id)
                load_current_revision_quote(writer, user_id, body.position_id, body.quote_id, now)
                intent = RevisionIntent(
                    user_id=user_id,
                    epoch_id=body.expected_epoch_id,
                    original_request=body,
                    native_key=key,
                    principal_at_capture=actor,
                    captured_at=now,
                )
                store(
                    writer,
                    user_id,
                    "EVENT",
                    f"asset-loss-revision-intent:{body.quote_id}",
                    intent.model_dump(mode="json"),
                    now,
                )
            store(
                writer,
                user_id,
                "BINDING",
                f"asset-loss-revision-command:{body.client_request_id}",
                {
                    "protocol": LOSS_V2,
                    "original_request": body.model_dump(mode="json"),
                    "native_key": key,
                    "bank_authority": False,
                },
                now,
            )
        with fresh_read(engine) as read:
            action = loss_action_by_key(read, user_id, key, now)
        if action is None:
            action = installed_loss_prepare(
                engine,
                user_id,
                PrepareActionRequest(
                    idempotency_key=key,
                    intent=RedeemIntent(kind="redeem_asset", position_id=body.position_id),
                ),
                RevisionLossPrepareRequest(**body.model_dump(), native_key=key),
                now,
            )
        with Session(engine) as writer, writer.begin():
            from app.services.execution import _lock_user

            _lock_user(writer, user_id)
            old = anchored(
                writer, user_id, "BINDING", f"asset-loss-revision-action:{action.action_id}"
            )
            if old is not None:
                return original_binding(writer, user_id, action.action_id, now)[1]
            payload, evidence = read_revision_original(writer, user_id, body.quote_id, now)
            binding = RevisionActionBinding(
                user_id=user_id,
                epoch_id=body.expected_epoch_id,
                action_id=action.action_id,
                source_request=body,
                effect=action.effect,
                effect_hash=action.effect_hash,
                quote=payload.quote,
                quote_hash=configuration_hash(payload.quote.model_dump(mode="json")),
                quote_evidence_hash=evidence.content_hash,
            )
            if action.autonomy_level != "ASK_ONCE":
                raise failure("新报价仍必须逐动作明确同意", "ASSET_LOSS_CONSENT_REQUIRED")
            store(
                writer,
                user_id,
                "BINDING",
                f"asset-loss-revision-action:{action.action_id}",
                binding.model_dump(mode="json"),
                now,
            )
        return action


def original_binding(
    session: Session, user_id: UUID, action_id: UUID, now: datetime
) -> tuple[RevisionActionBinding, ActionResponse]:
    raw = anchored(session, user_id, "BINDING", f"asset-loss-revision-action:{action_id}")
    if raw is None:
        raise failure("新报价动作原绑定尚未完整", "ASSET_PARTIAL_BINDING_NOT_FINAL")
    binding = decode_record(RevisionActionBinding, raw)
    action = get_action(session, user_id, action_id, now)
    source = anchored(
        session, user_id, "EVENT", f"asset-loss-revision-intent:{binding.quote.quote_id}"
    )
    if source is None:
        raise failure("缺少新报价原USER意图")
    intent = decode_record(RevisionIntent, source)
    row = session.get(ActionPlan, action_id)
    payload, evidence = read_revision_original(session, user_id, binding.quote.quote_id, now)
    if (
        binding.user_id != user_id
        or binding.epoch_id != _epoch(session, user_id)
        or binding.action_id != action_id
        or action.autonomy_level != "ASK_ONCE"
        or binding.effect != action.effect
        or binding.effect_hash != action.effect_hash
        or binding.quote != payload.quote
        or binding.quote_evidence_hash != evidence.content_hash
        or row is None
        or intent.user_id != user_id
        or intent.original_request != binding.source_request
        or row.idempotency_key != "action:" + configuration_hash({"key": intent.native_key})
    ):
        raise failure("续报价动作、原请求与原生原件不一致")
    native = read_original_loss_native(
        session,
        user_id,
        row,
        BankCommand(effect=action.effect, effect_hash=action.effect_hash),
        now,
    )
    if (
        native.protocol != LOSS_V2
        or native.source.quote_evidence_hash != binding.quote_evidence_hash
    ):
        raise failure("续报价原生数学和报价不一致")
    return binding, action


def original_acceptance(
    session: Session, user_id: UUID, binding: RevisionActionBinding, now: datetime
) -> SignedRevisionAcceptance | None:
    raw = anchored(
        session, user_id, "BINDING", f"asset-loss-revision-acceptance:{binding.action_id}"
    )
    if raw is None:
        return None
    acceptance = decode_record(SignedRevisionAcceptance, raw)
    if (
        acceptance.user_id != user_id
        or acceptance.epoch_id != binding.epoch_id
        or acceptance.action_id != binding.action_id
        or acceptance.original_request.reviewed_effect_hash != binding.effect_hash
        or acceptance.original_request.reviewed_quote_hash != binding.quote_hash
        or acceptance.expires_at > min(binding.quote.expires_at, binding.effect.expires_at)
        or not binding.effect.valid_from <= acceptance.accepted_at <= now
    ):
        raise failure("旧同意不能批准新报价和新动作")
    return acceptance


def view(session: Session, user_id: UUID, action_id: UUID, now: datetime) -> dict[str, Any]:
    binding, action = original_binding(session, user_id, action_id, now)
    acceptance = original_acceptance(session, user_id, binding, now)
    return {
        "protocol": LOSS_V2,
        **quote_display(binding.quote, now),
        "binding": binding.model_dump(mode="json"),
        "action": action.model_dump(mode="json"),
        "original_signed_acceptance": acceptance.model_dump(mode="json") if acceptance else None,
        "bank_authority": False,
        "replacement_allowed": False,
    }


def enforce_revision_acceptance(
    engine: Engine, action: ActionPlan, command: BankCommand, now: datetime
) -> None:
    with fresh_read(engine) as read:
        binding, native = original_binding(read, action.user_id, action.id, now)
        accepted = original_acceptance(read, action.user_id, binding, now)
        if (
            accepted is None
            or not accepted.accepted_at <= now < accepted.expires_at
            or native.autonomy_level != "ASK_ONCE"
            or binding.effect != command.effect
            or binding.effect_hash != command.effect_hash
            or load_current_revision_quote(
                read,
                action.user_id,
                binding.source_request.position_id,
                binding.quote.quote_id,
                now,
            )
            != binding.quote
        ):
            raise failure(
                "首次受理必须是该新报价的真实USER双hash同意", "ASSET_LOSS_CONSENT_REQUIRED"
            )


@router.post("/renew")
def renew(
    body: QuoteRenewalRequest,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    actor: PrincipalDependency,
) -> dict[str, Any]:
    signed_user(actor, user.id, now)
    result = renew_quote(engine, user.id, body, now)
    return {
        **identity(session, user.id, engine),
        "protocol": PRICE_V2,
        **quote_display(result.quote, now),
        "quote_revision": result.model_dump(mode="json"),
        "quote_hash": configuration_hash(result.quote.model_dump(mode="json")),
        "bank_authority": False,
    }


@router.get("/quotes/{quote_id}")
def quote(
    quote_id: UUID,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        payload, evidence = read_revision_original(session, user.id, quote_id, now)
        return {
            **identity(session, user.id, engine),
            "protocol": PRICE_V2,
            **quote_display(payload.quote, now),
            "quote_revision": payload.model_dump(mode="json"),
            "quote_hash": configuration_hash(payload.quote.model_dump(mode="json")),
            "quote_evidence_hash": evidence.content_hash,
            "bank_authority": False,
        }


@router.get("/commands/{client_request_id}")
def renewal_command(
    client_request_id: UUID,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        raw = anchored(
            session, user.id, "BINDING", f"asset-quote-renew-command:{client_request_id}"
        )
        if raw is None:
            return {
                **identity(session, user.id, engine),
                "status": "NOT_FOUND_NOT_FINAL",
                "client_request_id": str(client_request_id),
                "server_as_of": now.isoformat(),
                "quote_validity": None,
                "bank_authority": False,
            }
        original = decode_renewal_command(raw)
        request = original.original_request
        if request.client_request_id != client_request_id or request.expected_epoch_id != _epoch(
            session, user.id
        ):
            raise failure("续报价原command身份不一致")
        payload, _ = read_revision_original(session, user.id, original.quote_id, now)
        return {
            **identity(session, user.id, engine),
            "status": "RECORDED",
            **quote_display(payload.quote, now),
            "client_request_id": str(client_request_id),
            "original_request": {
                "path": PREFIX + "/renew",
                "body": request.model_dump(mode="json"),
            },
            "quote_revision": payload.model_dump(mode="json"),
            "bank_authority": False,
        }


@router.post("/prepare")
def prepare(
    body: RevisionPrepareRequest,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    actor: PrincipalDependency,
) -> dict[str, Any]:
    action = prepare_revision(engine, user.id, body, actor, now)
    with fresh_read(engine) as read:
        result = view(read, user.id, action.action_id, now)
    return {**identity(session, user.id, engine), **result}


@router.get("/prepare-commands/{client_request_id}")
def prepare_command(
    client_request_id: UUID,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        command = anchored(
            session, user.id, "BINDING", f"asset-loss-revision-command:{client_request_id}"
        )
        if command is None:
            return {
                **identity(session, user.id, engine),
                "status": "NOT_FOUND_NOT_FINAL",
                "client_request_id": str(client_request_id),
                "server_as_of": now.isoformat(),
                "quote_validity": None,
                "bank_authority": False,
            }
        body = RevisionPrepareRequest.model_validate_json(
            __import__("json").dumps(command["original_request"])
        )
        if body.client_request_id != client_request_id or body.expected_epoch_id != _epoch(
            session, user.id
        ):
            raise failure("续报价原准备身份不一致")
        if (
            set(command) != {"protocol", "original_request", "native_key", "bank_authority"}
            or command.get("protocol") != LOSS_V2
            or command.get("bank_authority") is not False
            or command.get("native_key")
            != command_key(body.expected_epoch_id, "lossy-revision", client_request_id)
        ):
            raise failure("续报价原准备完整命令不一致")
        action = loss_action_by_key(session, user.id, command["native_key"], now)
        complete = (
            action is not None
            and anchored(
                session, user.id, "BINDING", f"asset-loss-revision-action:{action.action_id}"
            )
            is not None
        )
        result = {
            **identity(session, user.id, engine),
            "status": "RECORDED" if complete else "PARTIAL_NOT_FINAL",
            "server_as_of": now.isoformat(),
            "client_request_id": str(client_request_id),
            "original_request": {"path": PREFIX + "/prepare", "body": body.model_dump(mode="json")},
            "original": view(session, user.id, action.action_id, now)
            if complete and action
            else None,
            "bank_authority": False,
        }
        result["quote_validity"] = (
            result["original"]["quote_validity"] if result["original"] is not None else None
        )
        return result


@router.get("/actions/{action_id}")
def action_original(
    action_id: UUID,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        return {**identity(session, user.id, engine), **view(session, user.id, action_id, now)}


@router.post("/actions/{action_id}/confirm-and-execute")
def confirm_execute(
    action_id: UUID,
    body: LossAcceptanceRequest,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    actor: PrincipalDependency,
) -> dict[str, Any]:
    require_closure_guards()
    signed_user(actor, user.id, now)
    with serial_user(engine, user.id):
        with Session(engine) as writer, writer.begin():
            from app.services.execution import _lock_user

            _lock_user(writer, user.id)
            require_epoch(writer, user.id, body.expected_epoch_id)
            binding, _ = original_binding(writer, user.id, action_id, now)
            if (
                body.reviewed_quote_hash != binding.quote_hash
                or body.reviewed_effect_hash != binding.effect_hash
            ):
                raise failure("必须重新同意该新报价的双hash", "ASSET_CONFIRMATION_MISMATCH")
            old = original_acceptance(writer, user.id, binding, now)
            if old is not None:
                if old.original_request != body:
                    raise failure("原同意不能换键或body", "REQUEST_CONFLICT")
            else:
                load_current_revision_quote(
                    writer, user.id, binding.source_request.position_id, binding.quote.quote_id, now
                )
                accepted = SignedRevisionAcceptance(
                    user_id=user.id,
                    epoch_id=body.expected_epoch_id,
                    action_id=action_id,
                    original_request=body,
                    principal_at_acceptance=actor,
                    accepted_at=now,
                    expires_at=min(
                        binding.quote.expires_at, binding.effect.expires_at, actor.expires_at
                    ),
                )
                store(
                    writer,
                    user.id,
                    "BINDING",
                    f"asset-loss-revision-acceptance:{action_id}",
                    accepted.model_dump(mode="json"),
                    now,
                )
        confirm_action(
            engine,
            user.id,
            action_id,
            ConfirmActionRequest(effect_hash=body.reviewed_effect_hash, accepted=True),
            now,
        )
        execute_action(engine, user.id, action_id, now)
        with fresh_read(engine) as read:
            result = view(read, user.id, action_id, now)
    return {**identity(session, user.id, engine), **result}


@router.post("/actions/{action_id}/execute-original")
def execute_original(
    action_id: UUID,
    body: LossOriginalRequest,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    actor: PrincipalDependency,
) -> dict[str, Any]:
    signed_user(actor, user.id, now)
    with serial_user(engine, user.id):
        with fresh_read(engine) as read:
            require_epoch(read, user.id, body.expected_epoch_id)
            binding, _ = original_binding(read, user.id, action_id, now)
            if (
                original_acceptance(read, user.id, binding, now) is None
                or body.reviewed_quote_hash != binding.quote_hash
                or body.reviewed_effect_hash != binding.effect_hash
            ):
                raise failure("只能用原同意和原双hash恢复原动作", "ASSET_LOSS_CONSENT_REQUIRED")
        execute_action(engine, user.id, action_id, now)
        with fresh_read(engine) as read:
            result = view(read, user.id, action_id, now)
    return {**identity(session, user.id, engine), **result}
