"""Staged Next asset adapters: original financial engines remain mandatory.

This file is not installed in the application. Root must register the two original
execution/bank acceptance hooks before enabling portfolio or lossy routes.
"""

import json
from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID, uuid5

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.v1.zhiyu_catalog import identity, isolated
from app.api.v1.zhiyu_next import EngineDependency
from app.api.v1.zhiyu_policy_review import PrincipalDependency
from app.db.full_models import (
    FullAssetExecutionBatch,
    FullAssetExecutionConsent,
    FullAssetExecutionPortfolio,
    FullPolicy,
)
from app.db.models import (
    ActionPlan,
    ActionReceipt,
    AssetPosition,
    AssetProduct,
    BankOperation,
    DecisionRun,
    EvidenceItem,
    Policy,
)
from app.domain.boundary_types import BoundaryModel
from app.domain.execution import execution_effect_hash
from app.domain.execution_types import BankCommand, ExecutionEffect
from app.domain.full_asset_execution import (
    FullAssetConfirmRequest,
    FullAssetExecuteRequest,
    FullAssetFrozenPortfolio,
    FullAssetPrepareRequest,
    Hash,
    batch_key,
    portfolio_identity,
)
from app.domain.full_maturity_execution import (
    FullMaturityConfirmation,
    FullMaturityExecuteRequest,
    FullMaturityRequest,
)
from app.domain.full_maturity_replanning import OriginalMaturityEvent
from app.domain.local_actor_session_types import LocalActorPrincipal, require_local_user
from app.domain.policy_configuration import UUIDReference, configuration_hash
from app.domain.recovery_types import RecoveryQuote
from app.services.action_contracts import (
    ActionResponse,
    ConfirmActionRequest,
    PrepareActionRequest,
    RedeemIntent,
)
from app.services.audit_chain import audit_read_scope, verify_audit_chain
from app.services.demo_console import _epoch
from app.services.execution import confirm_action, execute_action, get_action
from app.services.execution_sources import load_execution_quote
from app.services.full_asset_execution import preview_full_asset_execution
from app.services.full_asset_execution_dispatch import (
    confirm_full_asset_execution,
    execute_full_asset_execution,
    fresh_read,
    prepare_full_asset_execution,
)
from app.services.full_asset_execution_store import (
    FullAssetExecutionResponse,
    read_full_asset_execution,
    read_full_asset_execution_by_key,
)
from app.services.full_maturity_execution import (
    confirm_maturity_execution,
    execute_maturity_execution,
    lookup_maturity_execution,
    prepare_maturity_execution,
    preview_maturity_execution,
)
from app.services.full_maturity_replanning import read_original_maturity
from app.services.historical_read import historical_ledger_scope, verify_historical_ledger
from app.services.policy_lifecycle import PolicyLifecycleError, _now
from app.services.product_catalog import verified_catalog_products
from app.services.simulated_bank import validate_bank_projection
from app.services.simulated_redemption_quote import PROTOCOL as PRICE_PROTOCOL
from app.services.simulated_redemption_quote import issue_fixed_early_quote
from app.services.zhiyu_asset_loss import (
    LossNativePrepareRequest,
    installed_loss_prepare,
    read_original_loss_native,
)
from app.services.zhiyu_orchestration import MARKER, _valid, operation, serial_user, store
from app.services.zhiyu_policy_catalog import list_catalog_policies
from fastapi import APIRouter, Depends
from pydantic import StrictBool, model_validator
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

PREFIX = "/api/v1/zhiyu-next/assets"
GUARDS_VERSION = "zhiyu-asset-closure-guards-v1"
PORTFOLIO_ACCEPTANCE = "zhiyu-asset-portfolio-user-v1"
LOSS_PROTOCOL = "lossy-early-redemption-v1"
REINVESTMENT_PROTOCOL = "maturity-settled-reinvestment-v1"
CAPACITY = 1000
router = APIRouter(prefix=PREFIX, tags=["知余资产闭环"], dependencies=[Depends(isolated)])


def original_label(value: str | None, identity: UUID, kind: str) -> str:
    return (
        value.strip()
        if isinstance(value, str) and value.strip()
        else f"原{kind} {str(identity)[:8]}"
    )


def decode_record[Record: BoundaryModel](model: type[Record], data: dict[str, Any]) -> Record:
    """Defaults never repair absent immutable source fields or integer booleans."""
    if (
        set(data) != set(model.model_fields)
        or data.get("bank_authority") is not False
        or (
            "event_is_exclusive_funding_reservation" in data
            and data["event_is_exclusive_funding_reservation"] is not False
        )
        or (
            "accrued_interest_cents" in data
            and (
                type(data["accrued_interest_cents"]) is not int
                or data["accrued_interest_cents"] != 0
            )
        )
    ):
        raise failure("原闭环记录字段不完整或类型变化")
    try:
        return model.model_validate_json(json.dumps(data))
    except (ValueError, TypeError, OverflowError):
        raise failure("原闭环协议或原件字段无法核验") from None


class PortfolioRequest(BoundaryModel):
    expected_epoch_id: UUIDReference
    full_policy_id: UUIDReference
    expected_full_policy_version_id: UUIDReference
    mvp_asset_policy_id: UUIDReference
    expected_mvp_policy_version_id: UUIDReference
    goal_id: UUIDReference | None = None
    expected_goal_policy_version_id: UUIDReference | None = None
    planning_mode: Literal["PORTFOLIO", "FIXED_LADDER"] = "PORTFOLIO"
    client_request_id: UUIDReference

    @model_validator(mode="after")
    def complete_scope(self) -> Self:
        if (self.goal_id is None) != (self.expected_goal_policy_version_id is None):
            raise ValueError("Goal identity and its exact version are inseparable")
        return self


class PortfolioAcceptanceRequest(BoundaryModel):
    expected_epoch_id: UUIDReference
    reviewed_portfolio_hash: Hash
    accepted: StrictBool
    client_request_id: UUIDReference

    @model_validator(mode="after")
    def explicit_user(self) -> Self:
        if self.accepted is not True:
            raise ValueError("Only explicit whole-portfolio USER acceptance is valid")
        return self


class OriginalPortfolioRequest(BoundaryModel):
    expected_epoch_id: UUIDReference
    reviewed_portfolio_hash: Hash


class PortfolioRejectionProof(BoundaryModel):
    protocol: Literal["zhiyu-asset-no-native-preparation-v1"] = (
        "zhiyu-asset-no-native-preparation-v1"
    )
    epoch_id: UUID
    user_id: UUID
    checked_at: datetime
    audit_chain_status: Literal["VALID"] = "VALID"
    requested_native_acceptance_absent: Literal[True] = True


class SignedPortfolioAcceptance(BoundaryModel):
    protocol: Literal["zhiyu-asset-portfolio-user-v1"] = "zhiyu-asset-portfolio-user-v1"
    user_id: UUID
    epoch_id: UUID
    portfolio_id: UUID
    portfolio_hash: Hash
    original_wrapper_request: PortfolioAcceptanceRequest
    original_native_request: FullAssetConfirmRequest
    principal_at_acceptance: LocalActorPrincipal
    accepted_at: datetime
    expires_at: datetime
    bank_authority: Literal[False] = False

    @model_validator(mode="after")
    def bound_original(self) -> Self:
        require_local_user(self.principal_at_acceptance, self.user_id, self.accepted_at)
        native, original = self.original_native_request, self.original_wrapper_request
        if (
            self.epoch_id != native.expected_epoch_id
            or self.epoch_id != original.expected_epoch_id
            or self.portfolio_hash != native.reviewed_portfolio_hash
            or self.portfolio_hash != original.reviewed_portfolio_hash
            or native.accepted is not True
            or original.accepted is not True
            or native.idempotency_key
            != command_key(self.epoch_id, "asset-confirm", original.client_request_id)
            or not self.accepted_at < self.expires_at <= self.principal_at_acceptance.expires_at
        ):
            raise ValueError("Signed USER acceptance must bind one exact original portfolio")
        return self


class EpochRequest(BoundaryModel):
    expected_epoch_id: UUIDReference


class LossPrepareRequest(EpochRequest):
    position_id: UUIDReference
    client_request_id: UUIDReference


class LossAcceptanceRequest(EpochRequest):
    reviewed_quote_hash: Hash
    reviewed_effect_hash: Hash
    accepted: StrictBool
    client_request_id: UUIDReference

    @model_validator(mode="after")
    def explicit_costs(self) -> Self:
        if self.accepted is not True:
            raise ValueError("Explicit USER acceptance must bind original quote and effect")
        return self


class LossOriginalRequest(EpochRequest):
    reviewed_quote_hash: Hash
    reviewed_effect_hash: Hash


class LossIntent(BoundaryModel):
    protocol: Literal["lossy-early-redemption-v1"] = "lossy-early-redemption-v1"
    user_id: UUID
    epoch_id: UUID
    original_request: LossPrepareRequest
    native_key: str
    principal_at_capture: LocalActorPrincipal
    captured_at: datetime
    bank_authority: Literal[False] = False

    @model_validator(mode="after")
    def current_user_source(self) -> Self:
        require_local_user(self.principal_at_capture, self.user_id, self.captured_at)
        if (
            self.epoch_id != self.original_request.expected_epoch_id
            or self.native_key
            != command_key(self.epoch_id, "lossy", self.original_request.client_request_id)
        ):
            raise ValueError("Loss request must retain its exact server-owned key and epoch")
        return self


class LossActionBinding(BoundaryModel):
    protocol: Literal["lossy-early-redemption-v1"] = "lossy-early-redemption-v1"
    user_id: UUID
    epoch_id: UUID
    action_id: UUID
    source_request: LossPrepareRequest
    effect: ExecutionEffect
    effect_hash: Hash
    quote: RecoveryQuote
    quote_hash: Hash
    quote_evidence_hash: Hash
    loss_basis: Literal["principal_cents"] = "principal_cents"
    rounding: Literal["CEIL_CENT"] = "CEIL_CENT"
    accrued_interest_cents: Literal[0] = 0
    foregone_interest_cents: None = None
    interest_treatment: Literal["NO_ACCRUED_INTEREST_NO_YIELD_PAYMENT"] = (
        "NO_ACCRUED_INTEREST_NO_YIELD_PAYMENT"
    )
    bank_authority: Literal[False] = False

    @model_validator(mode="after")
    def whole_original_quote(self) -> Self:
        effect, quote = self.effect, self.quote
        if (
            self.epoch_id != self.source_request.expected_epoch_id
            or self.action_id != effect.operation_id
            or self.user_id != effect.user_id
            or self.user_id != quote.user_id
            or effect.action_type != "REDEEM_ASSET"
            or effect.position_id != self.source_request.position_id
            or effect.position_id != quote.position_id
            or effect.product_id != quote.product_id
            or effect.product_version_number != quote.product_version_number
            or effect.terms_digest != quote.terms_digest
            or effect.quote_id != quote.quote_id
            or effect.amount_cents != quote.principal_cents
            or effect.fee_cents != quote.fee_cents
            or effect.loss_cents != quote.loss_cents
            or effect.net_cents != quote.net_cents
            or effect.fee_cents != 0
            or effect.loss_cents <= 0
            or quote.kind != "EARLY_WITHDRAW"
            or self.effect_hash != execution_effect_hash(effect)
            or self.quote_hash != configuration_hash(quote.model_dump(mode="json"))
            or effect.expires_at > quote.expires_at
        ):
            raise ValueError(
                "Only exact whole-position original principal-loss quotes are supported"
            )
        return self


class SignedLossAcceptance(BoundaryModel):
    protocol: Literal["lossy-early-redemption-v1"] = "lossy-early-redemption-v1"
    user_id: UUID
    epoch_id: UUID
    action_id: UUID
    original_request: LossAcceptanceRequest
    principal_at_acceptance: LocalActorPrincipal
    accepted_at: datetime
    expires_at: datetime
    bank_authority: Literal[False] = False

    @model_validator(mode="after")
    def actual_cost_consent(self) -> Self:
        require_local_user(self.principal_at_acceptance, self.user_id, self.accepted_at)
        if (
            self.epoch_id != self.original_request.expected_epoch_id
            or not self.accepted_at < self.expires_at <= self.principal_at_acceptance.expires_at
        ):
            raise ValueError("Signed loss acceptance requires its original current USER and time")
        return self


class ReinvestmentRequest(EpochRequest):
    maturity_action_id: UUIDReference
    full_policy_id: UUIDReference
    expected_full_policy_version_id: UUIDReference
    mvp_asset_policy_id: UUIDReference
    expected_mvp_policy_version_id: UUIDReference
    expected_goal_policy_version_id: UUIDReference | None = None
    planning_mode: Literal["PORTFOLIO", "FIXED_LADDER"] = "PORTFOLIO"
    client_request_id: UUIDReference


class ReinvestmentIntent(BoundaryModel):
    protocol: Literal["maturity-settled-reinvestment-v1"] = "maturity-settled-reinvestment-v1"
    user_id: UUID
    epoch_id: UUID
    original_request: ReinvestmentRequest
    original_maturity_event: OriginalMaturityEvent
    original_native_request: FullAssetPrepareRequest
    principal_at_capture: LocalActorPrincipal
    captured_at: datetime
    bank_authority: Literal[False] = False
    event_is_exclusive_funding_reservation: Literal[False] = False

    @model_validator(mode="after")
    def received_source_only(self) -> Self:
        require_local_user(self.principal_at_capture, self.user_id, self.captured_at)
        source, native, request = (
            self.original_maturity_event,
            self.original_native_request,
            self.original_request,
        )
        if (
            source.proof_status != "VERIFIED"
            or source.service_receipt_verified is not True
            or source.action_id != request.maturity_action_id
            or source.settled_at is None
            or source.settled_at > self.captured_at
            or self.epoch_id != request.expected_epoch_id
            or self.epoch_id != native.expected_epoch_id
            or native.idempotency_key != reinvestment_key(self.epoch_id, source.action_id)
            or native.full_policy_id != request.full_policy_id
            or native.expected_full_policy_version_id != request.expected_full_policy_version_id
            or native.mvp_asset_policy_id != request.mvp_asset_policy_id
            or native.expected_mvp_policy_version_id != request.expected_mvp_policy_version_id
            or native.goal_id != source.goal_id
            or native.expected_goal_policy_version_id != request.expected_goal_policy_version_id
            or native.planning_mode != request.planning_mode
        ):
            raise ValueError(
                "Only an actual settled receipt can trigger a current exact-scope plan"
            )
        return self


class ReinvestmentBinding(BoundaryModel):
    protocol: Literal["maturity-settled-reinvestment-v1"] = "maturity-settled-reinvestment-v1"
    user_id: UUID
    epoch_id: UUID
    source_action_id: UUID
    source_bank_operation_id: UUID
    source_receipt_id: UUID
    source_originals_hash: Hash
    source_cash_account_id: UUID
    original_request: ReinvestmentRequest
    original_native_request: FullAssetPrepareRequest
    portfolio_id: UUID
    portfolio_hash: Hash
    original_action_ids: list[UUID]
    source_principal_cents: int
    original_purchase_cents: int
    bank_authority: Literal[False] = False
    event_is_exclusive_funding_reservation: Literal[False] = False

    @model_validator(mode="after")
    def one_received_event(self) -> Self:
        if (
            self.source_action_id != self.original_request.maturity_action_id
            or self.epoch_id != self.original_request.expected_epoch_id
            or self.epoch_id != self.original_native_request.expected_epoch_id
            or self.original_native_request.idempotency_key
            != reinvestment_key(self.epoch_id, self.source_action_id)
            or not 1 <= len(self.original_action_ids) <= 4
            or len(set(self.original_action_ids)) != len(self.original_action_ids)
            or not 0 < self.original_purchase_cents <= self.source_principal_cents
        ):
            raise ValueError("One received event must bind one bounded original portfolio")
        return self


class ReinvestmentPortfolioReference(BoundaryModel):
    protocol: Literal["maturity-settled-reinvestment-v1"] = "maturity-settled-reinvestment-v1"
    user_id: UUID
    epoch_id: UUID
    source_action_id: UUID
    portfolio_id: UUID
    source_binding_hash: Hash
    bank_authority: Literal[False] = False


def reinvestment_key(epoch_id: UUID, maturity_action_id: UUID) -> str:
    # Deliberately independent of policy version/client notification UUID. Changing
    # either must never create a second use of the same actual return event.
    return command_key(epoch_id, "maturity-reinvest", maturity_action_id)


def command_key(epoch_id: UUID, family: str, request_id: UUID) -> str:
    return f"zhiyu-next:{epoch_id}:{family}:{request_id}"


def native_portfolio_request(body: PortfolioRequest) -> FullAssetPrepareRequest:
    return FullAssetPrepareRequest(
        **body.model_dump(exclude={"client_request_id"}),
        idempotency_key=command_key(
            body.expected_epoch_id, "asset-prepare", body.client_request_id
        ),
    )


def portfolio_wrapper_request(body: PortfolioRequest) -> dict[str, Any]:
    return {
        "kind": "ASSET_PORTFOLIO_PREPARE",
        "path": PREFIX + "/portfolios/prepare",
        "body": body.model_dump(mode="json"),
    }


def portfolio_wrapper_id(epoch_id: UUID, client_request_id: UUID) -> UUID:
    return uuid5(epoch_id, f"zhiyu-asset-prepare-wrapper:{client_request_id}")


def portfolio_native_present(session: Session, user_id: UUID, body: PortfolioRequest) -> bool:
    """Any accepted parent/child/native bank source prevents a terminal rejection."""
    native = native_portfolio_request(body)
    parent_id = portfolio_identity(user_id, native)
    if session.get(FullAssetExecutionPortfolio, parent_id) is not None:
        return True
    if (
        session.scalar(
            select(FullAssetExecutionPortfolio.id).where(
                FullAssetExecutionPortfolio.user_id == user_id,
                FullAssetExecutionPortfolio.epoch_id == body.expected_epoch_id,
                FullAssetExecutionPortfolio.idempotency_key == native.idempotency_key,
            )
        )
        is not None
        or session.scalar(
            select(FullAssetExecutionConsent.id).where(
                FullAssetExecutionConsent.user_id == user_id,
                FullAssetExecutionConsent.epoch_id == body.expected_epoch_id,
                FullAssetExecutionConsent.idempotency_key == native.idempotency_key,
            )
        )
        is not None
    ):
        return True
    for number in range(1, 5):
        action_id = uuid5(parent_id, f"batch:{number}")
        if any(
            session.get(model, identity) is not None
            for model, identity in (
                (FullAssetExecutionBatch, uuid5(parent_id, f"binding:{number}")),
                (ActionPlan, action_id),
                (DecisionRun, uuid5(action_id, "decision")),
            )
        ):
            return True
        bank_key = batch_key(parent_id, number)
        if any(
            session.scalar(query) is not None
            for query in (
                select(ActionPlan.id).where(
                    ActionPlan.user_id == user_id, ActionPlan.idempotency_key == bank_key
                ),
                select(DecisionRun.id).where(
                    DecisionRun.user_id == user_id, DecisionRun.idempotency_key == bank_key
                ),
                select(BankOperation.id).where(
                    BankOperation.user_id == user_id, BankOperation.idempotency_key == bank_key
                ),
                select(ActionReceipt.id).where(
                    ActionReceipt.user_id == user_id, ActionReceipt.action_plan_id == action_id
                ),
            )
        ):
            return True
    return False


def portfolio_rejection_original(
    session: Session,
    user_id: UUID,
    epoch_id: UUID,
    request_id: UUID,
    now: datetime,
) -> dict[str, Any] | None:
    raw = anchored(session, user_id, "OPERATION", str(portfolio_wrapper_id(epoch_id, request_id)))
    if raw is None:
        return None
    request = raw.get("request")
    if not isinstance(request, dict) or set(request) != {"kind", "path", "body"}:
        raise failure("资产原拒绝缺少完整wrapper请求")
    if not isinstance(request.get("body"), dict) or set(request["body"]) != set(
        PortfolioRequest.model_fields
    ):
        raise failure("资产原拒绝不能用缺失字段重建原请求")
    body = PortfolioRequest.model_validate_json(json.dumps(request["body"]))
    result = raw.get("result")
    if (
        request != portfolio_wrapper_request(body)
        or body.client_request_id != request_id
        or body.expected_epoch_id != epoch_id
        or _epoch(session, user_id) != epoch_id
        or raw.get("operation_status") != "REJECTED"
        or not isinstance(result, dict)
        or set(result) != {"error", "rejection_proof", "bank_authority"}
        or result.get("bank_authority") is not False
        or portfolio_native_present(session, user_id, body)
    ):
        raise failure("原拒绝不能覆盖已存在的原组合、原子动作或银行受理")
    proof_raw = result.get("rejection_proof")
    if (
        not isinstance(proof_raw, dict)
        or set(proof_raw) != set(PortfolioRejectionProof.model_fields)
        or proof_raw.get("requested_native_acceptance_absent") is not True
    ):
        raise failure("原资产拒绝缺少无原生提交证明")
    proof = PortfolioRejectionProof.model_validate_json(json.dumps(proof_raw))
    source = session.get(
        EvidenceItem,
        uuid5(epoch_id, f"{MARKER}:OPERATION:{portfolio_wrapper_id(epoch_id, request_id)}"),
    )
    error = result.get("error")
    if (
        proof.epoch_id != epoch_id
        or proof.user_id != user_id
        or proof.checked_at > now
        or source is None
        or proof.checked_at != source.created_at
        or proof.checked_at != source.observed_at
        or not isinstance(error, dict)
        or set(error) != {"code", "message", "status_code"}
        or type(error["status_code"]) is not int
        or error["status_code"] not in {403, 404, 409, 422}
    ):
        raise failure("原资产拒绝证明所属、时间或错误边界不一致")
    verify_historical_ledger(session, user_id)
    return {
        "status": "REJECTED",
        "original_request": {"path": request["path"], "body": request["body"]},
        **result,
    }


def reject_uncommitted_portfolio(
    engine: Engine,
    user_id: UUID,
    body: PortfolioRequest,
    error: PolicyLifecycleError,
    now: datetime,
) -> None:
    """Caller holds serial_user; failed reads or any native partial are never REJECTED."""
    if error.status_code not in {403, 404, 409, 422}:
        return
    with fresh_read(engine) as read, historical_ledger_scope(read):
        _epoch(read, user_id, body.expected_epoch_id)
        if portfolio_native_present(read, user_id, body):
            return
        if verify_audit_chain(read, user_id).status != "VALID":
            return
        verify_historical_ledger(read, user_id)
    request_id = portfolio_wrapper_id(body.expected_epoch_id, body.client_request_id)
    proof = PortfolioRejectionProof(
        epoch_id=body.expected_epoch_id, user_id=user_id, checked_at=now
    )
    with Session(engine) as writer, writer.begin():
        if operation(writer, user_id, request_id) is None:
            store(
                writer,
                user_id,
                "OPERATION",
                str(request_id),
                {
                    "request": portfolio_wrapper_request(body),
                    "operation_status": "REJECTED",
                    "result": {
                        "error": {
                            "code": error.code,
                            "message": error.message,
                            "status_code": error.status_code,
                        },
                        "rejection_proof": proof.model_dump(mode="json"),
                        "bank_authority": False,
                    },
                },
                now,
            )


def failure(message: str, code: str = "ZHIYU_ASSET_ORIGINAL_UNKNOWN") -> PolicyLifecycleError:
    return PolicyLifecycleError(code, message, 409)


def signed_user(principal: LocalActorPrincipal, user_id: UUID, now: datetime) -> None:
    try:
        require_local_user(principal, user_id, _now(now))
    except ValueError:
        raise PolicyLifecycleError(
            "ASSET_SIGNED_USER_REQUIRED", "需要当前真实USER会话", 403
        ) from None


def require_closure_guards() -> None:
    from app.services import execution, execution_bank

    if any(
        getattr(module, "ZHIYU_ASSET_CLOSURE_GUARDS_VERSION", None) != GUARDS_VERSION
        for module in (execution, execution_bank)
    ):
        raise failure("资产闭环两阶段原银行接缝尚未安装", "ASSET_CLOSURE_NOT_INSTALLED")


def anchored(session: Session, user_id: UUID, kind: str, key: str) -> dict[str, Any] | None:
    epoch_id = _epoch(session, user_id)
    row = session.get(EvidenceItem, uuid5(epoch_id, f"{MARKER}:{kind}:{key}"))
    if row is None:
        return None
    if verify_audit_chain(session, user_id, epoch_id).status != "VALID":
        raise failure("当前资产闭环审计未证明")
    return dict(_valid(session, row, user_id, epoch_id)["payload"])


def original_signed_acceptance(
    session: Session, user_id: UUID, portfolio_id: UUID, now: datetime
) -> SignedPortfolioAcceptance | None:
    data = anchored(session, user_id, "BINDING", f"asset-acceptance:{portfolio_id}")
    if data is None:
        return None
    try:
        value = decode_record(SignedPortfolioAcceptance, data)
        actual = read_full_asset_execution(session, user_id, portfolio_id, now)
        if (
            value.user_id != user_id
            or value.portfolio_id != portfolio_id
            or value.epoch_id != actual.epoch_id
            or value.portfolio_hash != actual.original_portfolio.portfolio_hash
            or value.expires_at > actual.original_portfolio.expires_at
            or not actual.original_portfolio.prepared_at <= value.accepted_at <= now
        ):
            raise ValueError("Original signed request was rebound")
        return value
    except (ValueError, TypeError):
        raise failure("原签名资产确认与完整组合不一致") from None


def drive_original_portfolio(
    engine: Engine, user_id: UUID, portfolio_id: UUID, body: OriginalPortfolioRequest, now: datetime
) -> FullAssetExecutionResponse:
    """At most four native batches; never replace or jump over an unresolved child."""
    require_closure_guards()
    for _ in range(4):
        with fresh_read(engine) as read:
            _epoch(read, user_id, body.expected_epoch_id)
            original = read_full_asset_execution(read, user_id, portfolio_id, now)
            accepted = original_signed_acceptance(read, user_id, portfolio_id, now)
            if (
                accepted is None
                or not original.original_consent_verified
                or original.original_portfolio.portfolio_hash != body.reviewed_portfolio_hash
            ):
                raise failure("需要原签名整体确认及真实原整体consent", "ASSET_CONSENT_NOT_PROVEN")
            if original.state in {"SERVICE_RECEIPTS_VERIFIED", "STOPPED", "RETAINED_HISTORY"}:
                return original
            pending = next(
                (
                    batch
                    for batch in original.batches
                    if batch.original_action is None or batch.original_action.receipt is None
                ),
                None,
            )
            if pending is None or pending.original_action is None:
                raise failure("原组合动作分母或真实回执未证明")
            request = FullAssetExecuteRequest(
                accepted=True,  # Derived from the exact original signed acceptance above.
                expected_epoch_id=body.expected_epoch_id,
                reviewed_portfolio_hash=body.reviewed_portfolio_hash,
                expected_batch_number=pending.batch_number,
                expected_action_id=pending.action_id,
            )
        result = execute_full_asset_execution(engine, user_id, portfolio_id, request, now)
        if result.state in {"UNRESOLVED", "STOPPED", "RETAINED_HISTORY"}:
            return result
    return result


def loss_intent(
    engine: Engine,
    user_id: UUID,
    body: LossPrepareRequest,
    principal: LocalActorPrincipal,
    now: datetime,
) -> LossIntent:
    key = f"asset-loss-intent:{body.position_id}"
    with Session(engine) as writer, writer.begin():
        _epoch(writer, user_id, body.expected_epoch_id)
        prior = anchored(writer, user_id, "EVENT", key)
        if prior is not None:
            original = decode_record(LossIntent, prior)
            if original.user_id != user_id or original.original_request != body:
                raise failure("同一原持仓不能换请求UUID或body重新支取", "REQUEST_CONFLICT")
            return original
        value = LossIntent(
            user_id=user_id,
            epoch_id=body.expected_epoch_id,
            original_request=body,
            native_key=command_key(body.expected_epoch_id, "lossy", body.client_request_id),
            principal_at_capture=principal,
            captured_at=now,
        )
        store(writer, user_id, "EVENT", key, value.model_dump(mode="json"), now)
        return value


def loss_action_by_key(
    session: Session, user_id: UUID, native_key: str, now: datetime
) -> ActionResponse | None:
    key = "action:" + configuration_hash({"key": native_key})
    original = session.scalar(
        select(ActionPlan).where(ActionPlan.user_id == user_id, ActionPlan.idempotency_key == key)
    )
    return get_action(session, user_id, original.id, now) if original is not None else None


def quote_original(
    session: Session, user_id: UUID, action: ActionResponse, now: datetime
) -> tuple[RecoveryQuote, str]:
    effect = action.effect
    evidence = session.get(EvidenceItem, effect.quote_id)
    if (
        evidence is None
        or evidence.user_id != user_id
        or evidence.source_type != "SIMULATED_REDEMPTION_QUOTE"
        or evidence.source_ref != f"{PRICE_PROTOCOL}:{effect.position_id}"
        or evidence.evidence_level != "BANK_CONFIRMED"
        or evidence.status not in {"VALID", "SUPERSEDED"}
        or evidence.created_at > action.prepared_at
        or evidence.observed_at > now
        or evidence.content_hash != configuration_hash(evidence.content)
        or evidence.content.get("price_protocol") != PRICE_PROTOCOL
        or evidence.content.get("rounding") != "CEIL_CENT"
        or evidence.content.get("simulation") is not True
    ):
        raise failure("原服务器支取报价及本金损失计算来源无法核验")
    quote = RecoveryQuote.model_validate_json(json.dumps(evidence.content["quote"]))
    if (
        quote.quote_id != evidence.id
        or quote.user_id != user_id
        or not quote.request_at <= action.prepared_at < quote.expires_at
    ):
        raise failure("原报价时间或所属不一致")
    return quote, evidence.content_hash


def original_loss_binding(
    session: Session, user_id: UUID, action_id: UUID, now: datetime
) -> tuple[LossActionBinding, ActionResponse]:
    data = anchored(session, user_id, "BINDING", f"asset-loss-action:{action_id}")
    if data is None:
        raise failure("原有损支取绑定尚未完整，保留原准备请求", "ASSET_PARTIAL_BINDING_NOT_FINAL")
    binding = decode_record(LossActionBinding, data)
    actual = get_action(session, user_id, action_id, now)
    source = anchored(session, user_id, "EVENT", f"asset-loss-intent:{binding.effect.position_id}")
    if source is None:
        raise failure("原有损支取缺少已审计原请求")
    intent = decode_record(LossIntent, source)
    row = session.get(ActionPlan, action_id)
    quote, quote_hash = quote_original(session, user_id, actual, now)
    if (
        binding.user_id != user_id
        or binding.action_id != action_id
        or binding.epoch_id != _epoch(session, user_id)
        or binding.effect != actual.effect
        or binding.effect_hash != actual.effect_hash
        or binding.quote != quote
        or binding.quote_evidence_hash != quote_hash
        or intent.user_id != user_id
        or intent.original_request != binding.source_request
        or row is None
        or row.idempotency_key != "action:" + configuration_hash({"key": intent.native_key})
    ):
        raise failure("有损支取绑定与原动作或原报价不一致")
    native_original = read_original_loss_native(
        session,
        user_id,
        row,
        BankCommand(effect=actual.effect, effect_hash=actual.effect_hash),
        now,
    )
    if native_original.source.quote_evidence_hash != binding.quote_evidence_hash:
        raise failure("显式有损原生分支与原服务器报价不同")
    return binding, actual


def prepare_original_loss(
    engine: Engine,
    user_id: UUID,
    body: LossPrepareRequest,
    actor: LocalActorPrincipal,
    now: datetime,
) -> ActionResponse:
    require_closure_guards()
    signed_user(actor, user_id, now)
    with serial_user(engine, user_id):
        captured = loss_intent(engine, user_id, body, actor, now)
        with fresh_read(engine) as read:
            actual = loss_action_by_key(read, user_id, captured.native_key, now)
        if actual is None:
            # Publish only the actual simulator price; no client cost or result enters it.
            issue_fixed_early_quote(engine, user_id, body.position_id, now)
            actual = installed_loss_prepare(
                engine,
                user_id,
                PrepareActionRequest(
                    idempotency_key=captured.native_key,
                    intent=RedeemIntent(kind="redeem_asset", position_id=body.position_id),
                ),
                LossNativePrepareRequest(
                    expected_epoch_id=body.expected_epoch_id,
                    position_id=body.position_id,
                    client_request_id=body.client_request_id,
                    native_key=captured.native_key,
                ),
                now,
            )
        with Session(engine) as writer, writer.begin():
            old = anchored(writer, user_id, "BINDING", f"asset-loss-action:{actual.action_id}")
            if old is not None:
                original_loss_binding(writer, user_id, actual.action_id, now)
                return actual
            position = writer.get(AssetPosition, body.position_id)
            if position is None or position.user_id != user_id or position.accrued_yield_cents != 0:
                raise failure("当前本金报价不支持已计提利息结算", "ASSET_INTEREST_NOT_SUPPORTED")
            quote, quote_hash = quote_original(writer, user_id, actual, now)
            binding = LossActionBinding(
                user_id=user_id,
                epoch_id=body.expected_epoch_id,
                action_id=actual.action_id,
                source_request=body,
                effect=actual.effect,
                effect_hash=actual.effect_hash,
                quote=quote,
                quote_hash=configuration_hash(quote.model_dump(mode="json")),
                quote_evidence_hash=quote_hash,
            )
            if actual.autonomy_level != "ASK_ONCE":
                raise failure("有损支取必须是原ASK_ONCE动作", "ASSET_LOSS_CONSENT_REQUIRED")
            store(
                writer,
                user_id,
                "BINDING",
                f"asset-loss-action:{actual.action_id}",
                binding.model_dump(mode="json"),
                now,
            )
        return actual


def original_loss_consent(
    session: Session, user_id: UUID, binding: LossActionBinding, now: datetime
) -> SignedLossAcceptance | None:
    data = anchored(session, user_id, "BINDING", f"asset-loss-acceptance:{binding.action_id}")
    if data is None:
        return None
    accepted = decode_record(SignedLossAcceptance, data)
    if (
        accepted.user_id != user_id
        or accepted.epoch_id != binding.epoch_id
        or accepted.action_id != binding.action_id
        or accepted.original_request.reviewed_effect_hash != binding.effect_hash
        or accepted.original_request.reviewed_quote_hash != binding.quote_hash
        or accepted.expires_at > min(binding.quote.expires_at, binding.effect.expires_at)
        or not binding.effect.valid_from <= accepted.accepted_at <= now
    ):
        raise failure("原有损支取同意未绑定当前原报价和完整effect")
    return accepted


def loss_view(session: Session, user_id: UUID, action_id: UUID, now: datetime) -> dict[str, Any]:
    binding, actual = original_loss_binding(session, user_id, action_id, now)
    accepted = original_loss_consent(session, user_id, binding, now)
    return {
        "protocol": LOSS_PROTOCOL,
        "binding": binding.model_dump(mode="json"),
        "action": actual.model_dump(mode="json"),
        "original_signed_acceptance": accepted.model_dump(mode="json") if accepted else None,
        "bank_authority": False,
        "replacement_allowed": False,
    }


def verified_maturity_source(
    session: Session, user_id: UUID, action_id: UUID, epoch_id: UUID, now: datetime
) -> OriginalMaturityEvent:
    _epoch(session, user_id, epoch_id)
    row = session.get(ActionPlan, action_id)
    if row is None or row.user_id != user_id or row.action_type != "ASSET_MATURITY":
        raise failure("只能使用当前用户原合同到期动作", "MATURITY_SOURCE_NOT_FOUND")
    try:
        original_request = FullMaturityRequest.model_validate_json(
            json.dumps(row.request["full_maturity_execution"]["request"])
        )
        original = lookup_maturity_execution(
            session, user_id, original_request.idempotency_key, now
        )
        if (
            original_request.expected_epoch_id != epoch_id
            or original.action is None
            or original.action.action_id != action_id
            or original.action.epoch_id != epoch_id
            or original.action.original_request != original_request
            or original.action.service_receipt_verified is not True
        ):
            raise ValueError("Current epoch and actual maturity original must match")
        event = read_original_maturity(session, user_id, action_id, now)
        if (
            event.proof_status != "VERIFIED"
            or not event.service_receipt_verified
            or event.settled_at is None
            or event.settled_at > now
            or event.destination_account_id is None
            or event.original_receipt_id is None
            or event.bank_operation_id is None
        ):
            raise ValueError("Original return has no actual received whole principal")
        return event
    except (KeyError, TypeError, ValueError):
        raise failure("原到期兑付、银行和实收回执尚未完整", "MATURITY_RETURN_NOT_PROVEN") from None


def native_reinvestment_request(
    body: ReinvestmentRequest, event: OriginalMaturityEvent
) -> FullAssetPrepareRequest:
    if (event.goal_id is None) != (body.expected_goal_policy_version_id is None):
        raise failure("当前原目标版本必须与实收目的账户范围对应", "MATURITY_SCOPE_MISMATCH")
    return FullAssetPrepareRequest(
        expected_epoch_id=body.expected_epoch_id,
        full_policy_id=body.full_policy_id,
        expected_full_policy_version_id=body.expected_full_policy_version_id,
        mvp_asset_policy_id=body.mvp_asset_policy_id,
        expected_mvp_policy_version_id=body.expected_mvp_policy_version_id,
        goal_id=event.goal_id,
        expected_goal_policy_version_id=body.expected_goal_policy_version_id,
        planning_mode=body.planning_mode,
        idempotency_key=reinvestment_key(body.expected_epoch_id, event.action_id),
    )


def verify_reinvestment_funding(
    event: OriginalMaturityEvent, portfolio: FullAssetFrozenPortfolio
) -> None:
    if (
        event.proof_status != "VERIFIED"
        or event.destination_account_id is None
        or portfolio.original_request.goal_id != event.goal_id
        or not 0 < portfolio.total_purchase_cents <= event.principal_cents
        or any(
            batch.command.effect.goal_id != event.goal_id
            or batch.command.effect.return_account_id != event.destination_account_id
            or any(
                use.account_id != event.destination_account_id
                for use in batch.command.effect.cash_uses
            )
            for batch in portfolio.batches
        )
    ):
        raise failure(
            "本版再配置只支持原实收账户、原用途及不超过实收本金的当前组合",
            "MATURITY_RETURN_FUNDING_NOT_SUPPORTED",
        )


def capture_reinvestment_intent(
    engine: Engine,
    user_id: UUID,
    body: ReinvestmentRequest,
    principal: LocalActorPrincipal,
    now: datetime,
) -> ReinvestmentIntent:
    key = f"asset-maturity-event:{body.maturity_action_id}"
    with fresh_read(engine) as read:
        prior = anchored(read, user_id, "EVENT", key)
        event = (
            verified_maturity_source(
                read, user_id, body.maturity_action_id, body.expected_epoch_id, now
            )
            if prior is None
            else None
        )
    with Session(engine) as writer, writer.begin():
        _epoch(writer, user_id, body.expected_epoch_id)
        data = anchored(writer, user_id, "EVENT", key)
        if data is not None:
            original = decode_record(ReinvestmentIntent, data)
            if original.user_id != user_id or original.original_request.model_dump(
                exclude={"client_request_id"}
            ) != body.model_dump(exclude={"client_request_id"}):
                raise failure("同实收事件不能换策略范围或版本另建组合", "REQUEST_CONFLICT")
            return original
        if event is None:
            raise failure("原实收到期来源快照未证明")
        original = ReinvestmentIntent(
            user_id=user_id,
            epoch_id=body.expected_epoch_id,
            original_request=body,
            original_maturity_event=event,
            original_native_request=native_reinvestment_request(body, event),
            principal_at_capture=principal,
            captured_at=now,
        )
        # A received event is a source locator, not income, reserved cash, or permission.
        store(writer, user_id, "EVENT", key, original.model_dump(mode="json"), now)
        return original


def original_reinvestment(
    session: Session, user_id: UUID, maturity_action_id: UUID, now: datetime
) -> tuple[ReinvestmentBinding, FullAssetExecutionResponse] | None:
    data = anchored(session, user_id, "BINDING", f"asset-maturity-event:{maturity_action_id}")
    if data is None:
        return None
    binding = decode_record(ReinvestmentBinding, data)
    event = verified_maturity_source(session, user_id, maturity_action_id, binding.epoch_id, now)
    actual = read_full_asset_execution(session, user_id, binding.portfolio_id, now)
    verify_reinvestment_funding(event, actual.original_portfolio)
    pointer = anchored(
        session, user_id, "BINDING", f"asset-portfolio-source:{binding.portfolio_id}"
    )
    if pointer is None:
        raise failure("原到期再配置组合来源指针缺失")
    reference = decode_record(ReinvestmentPortfolioReference, pointer)
    if (
        binding.user_id != user_id
        or binding.source_action_id != maturity_action_id
        or binding.source_originals_hash != event.originals_hash
        or binding.source_bank_operation_id != event.bank_operation_id
        or binding.source_receipt_id != event.original_receipt_id
        or binding.source_cash_account_id != event.destination_account_id
        or binding.source_principal_cents != event.principal_cents
        or binding.portfolio_hash != actual.original_portfolio.portfolio_hash
        or binding.original_native_request != actual.original_portfolio.original_request
        or binding.original_action_ids != [batch.action_id for batch in actual.batches]
        or binding.original_purchase_cents != actual.original_portfolio.total_purchase_cents
        or reference.user_id != user_id
        or reference.epoch_id != binding.epoch_id
        or reference.portfolio_id != binding.portfolio_id
        or reference.source_action_id != maturity_action_id
        or reference.source_binding_hash != configuration_hash(binding.model_dump(mode="json"))
    ):
        raise failure("原实收事件、当前组合与一次性来源绑定不同")
    return binding, actual


def prepare_original_reinvestment(
    engine: Engine,
    user_id: UUID,
    body: ReinvestmentRequest,
    principal: LocalActorPrincipal,
    now: datetime,
) -> tuple[ReinvestmentBinding, FullAssetExecutionResponse]:
    require_closure_guards()
    signed_user(principal, user_id, now)
    with serial_user(engine, user_id):
        captured = capture_reinvestment_intent(engine, user_id, body, principal, now)
        with fresh_read(engine) as read:
            old = original_reinvestment(read, user_id, body.maturity_action_id, now)
            if old is not None:
                return old
            event = verified_maturity_source(
                read, user_id, body.maturity_action_id, body.expected_epoch_id, now
            )
            if event != captured.original_maturity_event:
                raise failure("原实收事件在准备前变化")
            lookup = read_full_asset_execution_by_key(
                read,
                user_id,
                body.expected_epoch_id,
                captured.original_native_request.idempotency_key,
                now,
            )
            if lookup.original is None:
                preview = preview_full_asset_execution(
                    read, user_id, captured.original_native_request, now
                )
                if preview.portfolio is None or preview.state != "READY_TO_REVIEW":
                    raise failure(
                        "按当前资产规则无合法组合，保留实际返还现金", "MATURITY_RETAIN_CASH"
                    )
                verify_reinvestment_funding(event, preview.portfolio)
            elif (
                lookup.original.original_portfolio.original_request
                != captured.original_native_request
            ):
                raise failure("原事件组合key已指向另一请求")
        # Native preparation replays the already committed original portfolio after a crash.
        actual = prepare_full_asset_execution(
            engine, user_id, captured.original_native_request, now
        )
        verify_reinvestment_funding(event, actual.original_portfolio)
        assert event.bank_operation_id is not None
        assert event.original_receipt_id is not None
        assert event.destination_account_id is not None
        binding = ReinvestmentBinding(
            user_id=user_id,
            epoch_id=body.expected_epoch_id,
            source_action_id=event.action_id,
            source_bank_operation_id=event.bank_operation_id,
            source_receipt_id=event.original_receipt_id,
            source_originals_hash=event.originals_hash,
            source_cash_account_id=event.destination_account_id,
            original_request=captured.original_request,
            original_native_request=captured.original_native_request,
            portfolio_id=actual.original_portfolio.portfolio_id,
            portfolio_hash=actual.original_portfolio.portfolio_hash,
            original_action_ids=[batch.action_id for batch in actual.batches],
            source_principal_cents=event.principal_cents,
            original_purchase_cents=actual.original_portfolio.total_purchase_cents,
        )
        reference = ReinvestmentPortfolioReference(
            user_id=user_id,
            epoch_id=body.expected_epoch_id,
            source_action_id=event.action_id,
            portfolio_id=binding.portfolio_id,
            source_binding_hash=configuration_hash(binding.model_dump(mode="json")),
        )
        with Session(engine) as writer, writer.begin():
            store(
                writer,
                user_id,
                "BINDING",
                f"asset-maturity-event:{event.action_id}",
                binding.model_dump(mode="json"),
                now,
            )
            store(
                writer,
                user_id,
                "BINDING",
                f"asset-portfolio-source:{binding.portfolio_id}",
                reference.model_dump(mode="json"),
                now,
            )
        return binding, actual


@router.post("/reinvestments/preview")
def reinvestment_preview(
    body: ReinvestmentRequest,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    event = verified_maturity_source(
        session, user.id, body.maturity_action_id, body.expected_epoch_id, now
    )
    result = preview_full_asset_execution(
        session, user.id, native_reinvestment_request(body, event), now
    )
    if result.portfolio is not None:
        verify_reinvestment_funding(event, result.portfolio)
    return {
        **identity(session, user.id, engine),
        "protocol": REINVESTMENT_PROTOCOL,
        "source_event": event.model_dump(mode="json"),
        "preview": result.model_dump(mode="json"),
        "bank_authority": False,
        "event_is_exclusive_funding_reservation": False,
    }


@router.post("/reinvestments/prepare")
def reinvestment_prepare(
    body: ReinvestmentRequest,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    actor: PrincipalDependency,
) -> dict[str, Any]:
    binding, actual = prepare_original_reinvestment(engine, user.id, body, actor, now)
    return {
        **identity(session, user.id, engine),
        "protocol": REINVESTMENT_PROTOCOL,
        "source_binding": binding.model_dump(mode="json"),
        "portfolio": actual.model_dump(mode="json"),
        "replacement_allowed": False,
    }


@router.get("/reinvestments/by-maturity-action/{maturity_action_id}")
def reinvestment_original(
    maturity_action_id: UUID,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        original = original_reinvestment(session, user.id, maturity_action_id, now)
        if original is None:
            data = anchored(session, user.id, "EVENT", f"asset-maturity-event:{maturity_action_id}")
            intent = decode_record(ReinvestmentIntent, data) if data else None
            lookup = (
                read_full_asset_execution_by_key(
                    session,
                    user.id,
                    intent.epoch_id,
                    intent.original_native_request.idempotency_key,
                    now,
                )
                if intent
                else None
            )
            partial = lookup is not None and lookup.original is not None
            return {
                **identity(session, user.id, engine),
                "protocol": REINVESTMENT_PROTOCOL,
                "status": "PARTIAL_BINDING_NOT_FINAL" if partial else "NOT_FOUND_NOT_FINAL",
                "original_intent": intent.model_dump(mode="json") if intent else None,
                "native_lookup": lookup.model_dump(mode="json") if lookup else None,
                "replacement_allowed": False,
                "bank_execute_allowed": False,
            }
        binding, portfolio = original
        return {
            **identity(session, user.id, engine),
            "protocol": REINVESTMENT_PROTOCOL,
            "status": "RECORDED",
            "source_binding": binding.model_dump(mode="json"),
            "portfolio": portfolio.model_dump(mode="json"),
            "replacement_allowed": False,
        }


@router.post("/lossy/positions/{position_id}/quote")
def loss_quote(
    position_id: UUID,
    body: EpochRequest,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    actor: PrincipalDependency,
) -> dict[str, Any]:
    signed_user(actor, user.id, now)
    with serial_user(engine, user.id):
        _epoch(session, user.id, body.expected_epoch_id)
        quote = issue_fixed_early_quote(engine, user.id, position_id, now)
    return {
        **identity(session, user.id, engine),
        "quote": quote.model_dump(mode="json"),
        "quote_hash": configuration_hash(quote.model_dump(mode="json")),
        "loss_basis": "principal_cents",
        "rounding": "CEIL_CENT",
        "interest_treatment": "NO_ACCRUED_INTEREST_NO_YIELD_PAYMENT",
        "bank_authority": False,
    }


@router.post("/lossy/prepare")
def loss_prepare(
    body: LossPrepareRequest,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    actor: PrincipalDependency,
) -> dict[str, Any]:
    actual = prepare_original_loss(engine, user.id, body, actor, now)
    with fresh_read(engine) as read:
        result = loss_view(read, user.id, actual.action_id, now)
    return {**identity(session, user.id, engine), **result}


@router.get("/lossy/actions/{action_id}")
def loss_original(
    action_id: UUID,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        return {**identity(session, user.id, engine), **loss_view(session, user.id, action_id, now)}


@router.get("/lossy/positions/{position_id}/requests/{client_request_id}")
def loss_prepare_original(
    position_id: UUID,
    client_request_id: UUID,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        data = anchored(session, user.id, "EVENT", f"asset-loss-intent:{position_id}")
        if data is None:
            return {
                **identity(session, user.id, engine),
                "protocol": LOSS_PROTOCOL,
                "status": "NOT_FOUND_NOT_FINAL",
                "replacement_allowed": False,
            }
        intent = decode_record(LossIntent, data)
        if (
            intent.user_id != user.id
            or intent.original_request.position_id != position_id
            or intent.original_request.client_request_id != client_request_id
        ):
            raise failure("原支取请求UUID或仓位不一致", "REQUEST_CONFLICT")
        action = loss_action_by_key(session, user.id, intent.native_key, now)
        base = {
            **identity(session, user.id, engine),
            "protocol": LOSS_PROTOCOL,
            "original_request": {
                "path": PREFIX + "/lossy/prepare",
                "body": intent.original_request.model_dump(mode="json"),
            },
            "replacement_allowed": False,
        }
        if action is None:
            return {**base, "status": "NOT_FOUND_NOT_FINAL", "bank_execute_allowed": False}
        if anchored(session, user.id, "BINDING", f"asset-loss-action:{action.action_id}") is None:
            return {
                **base,
                "status": "PARTIAL_BINDING_NOT_FINAL",
                "original_action": action.model_dump(mode="json"),
                "bank_execute_allowed": False,
            }
        return {**base, "status": "RECORDED", **loss_view(session, user.id, action.action_id, now)}


@router.post("/lossy/actions/{action_id}/confirm-and-execute")
def loss_confirm_execute(
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
            _epoch(writer, user.id, body.expected_epoch_id)
            binding, _ = original_loss_binding(writer, user.id, action_id, now)
            if (
                body.reviewed_quote_hash != binding.quote_hash
                or body.reviewed_effect_hash != binding.effect_hash
            ):
                raise failure("同意必须绑定原报价及完整经济后果", "ASSET_CONFIRMATION_MISMATCH")
            prior = original_loss_consent(writer, user.id, binding, now)
            if prior is not None:
                if prior.original_request != body:
                    raise failure("原有损确认不能改变原UUID/body", "REQUEST_CONFLICT")
            else:
                if now >= min(binding.quote.expires_at, binding.effect.expires_at):
                    raise failure("原服务器报价或effect已过期", "ASSET_QUOTE_EXPIRED")
                accepted = SignedLossAcceptance(
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
                    f"asset-loss-acceptance:{action_id}",
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
            result = loss_view(read, user.id, action_id, now)
    return {**identity(session, user.id, engine), **result}


@router.post("/lossy/actions/{action_id}/execute-original")
def loss_continue(
    action_id: UUID,
    body: LossOriginalRequest,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    actor: PrincipalDependency,
) -> dict[str, Any]:
    require_closure_guards()
    signed_user(actor, user.id, now)
    with serial_user(engine, user.id):
        with fresh_read(engine) as read:
            _epoch(read, user.id, body.expected_epoch_id)
            binding, _ = original_loss_binding(read, user.id, action_id, now)
            if (
                original_loss_consent(read, user.id, binding, now) is None
                or binding.quote_hash != body.reviewed_quote_hash
                or binding.effect_hash != body.reviewed_effect_hash
            ):
                raise failure("须以原签名同意恢复同一动作", "ASSET_LOSS_CONSENT_REQUIRED")
        execute_action(engine, user.id, action_id, now)
        with fresh_read(engine) as read:
            result = loss_view(read, user.id, action_id, now)
    return {**identity(session, user.id, engine), **result}


@router.get("/state")
def asset_state(
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    """Verified owned read summaries; acceptance is closed until a new actual permit."""
    with historical_ledger_scope(session), audit_read_scope(session):
        epoch = _epoch(session, user.id)
        if verify_audit_chain(session, user.id, epoch).status != "VALID":
            raise failure("当前资产审计原件未通过核验")
        validate_bank_projection(session, user.id, now)
        catalogue = verified_catalog_products(session, now)
        records = list_catalog_policies(session, user.id, now)
        policies: list[dict[str, Any]] = []
        for record in records.items:
            if record.template_name not in {
                "AssetAuthorizationPolicy",
                "RecoveryPolicy",
                "LongTermGoalPolicy",
            }:
                continue
            policy: FullPolicy | Policy | None
            if record.source_kind == "FULL_POLICY":
                policy = session.get(FullPolicy, record.policy_id)
            else:
                policy = session.get(Policy, record.policy_id)
            if policy is None or policy.user_id != user.id:
                raise failure("资产只读名称与原策略所属不一致")
            policies.append(
                {
                    "name": original_label(policy.name, record.policy_id, "策略"),
                    "template_name": record.template_name,
                    "source_kind": record.source_kind,
                    "dsl_version": record.dsl_version,
                    "policy_id": str(record.policy_id),
                    "current_version_id": str(record.current_version_id),
                    "configuration_hash": record.configuration_hash,
                    "goal_id": str(record.goal_id) if record.goal_id else None,
                    "effective_status": record.effective_status,
                    "planning_confirmation_valid": record.planning_confirmation_valid,
                    "configuration": record.configuration,
                }
            )
        positions = list(
            session.scalars(
                select(AssetPosition)
                .where(AssetPosition.user_id == user.id)
                .order_by(AssetPosition.id)
                .limit(CAPACITY + 1)
            )
        )
        parents = list(
            session.scalars(
                select(FullAssetExecutionPortfolio)
                .where(
                    FullAssetExecutionPortfolio.user_id == user.id,
                    FullAssetExecutionPortfolio.epoch_id == epoch,
                )
                .order_by(FullAssetExecutionPortfolio.created_at, FullAssetExecutionPortfolio.id)
                .limit(CAPACITY + 1)
            )
        )
        if max(len(positions), len(parents), len(policies)) > CAPACITY:
            raise failure("资产只读目录超过原容量", "INPUT_LIMIT_EXCEEDED")
        position_views = []
        for row in positions:
            product = session.get(AssetProduct, row.product_id)
            if product is None:
                raise failure("原持仓产品不存在")
            position_views.append(
                {
                    "position_id": str(row.id),
                    "account_id": str(row.account_id),
                    "product_id": str(row.product_id),
                    "product_name": original_label(product.name, row.product_id, "产品"),
                    "status": row.status,
                    "principal_cents": row.principal_cents,
                    "accrued_yield_cents": row.accrued_yield_cents,
                    "purchased_at": row.purchased_at.isoformat(),
                    "maturity_at": row.maturity_at.isoformat() if row.maturity_at else None,
                    "goal_id": str(row.goal_id) if row.goal_id else None,
                    "original_policy_version_id": str(row.policy_version_id)
                    if row.policy_version_id
                    else None,
                }
            )
        portfolios = []
        for parent in parents:
            actual = read_full_asset_execution(session, user.id, parent.id, now)
            receipts_verified = actual.all_original_service_receipts_verified
            portfolios.append(
                {
                    "portfolio_id": str(parent.id),
                    "epoch_id": str(parent.epoch_id),
                    "portfolio_hash": parent.portfolio_hash,
                    "state": actual.state,
                    "all_original_service_receipts_verified": receipts_verified,
                    "original_consent_verified": actual.original_consent_verified,
                    "has_signed_acceptance": original_signed_acceptance(
                        session, user.id, parent.id, now
                    )
                    is not None,
                }
            )
        products = []
        for item in catalogue.products:
            product = session.get(AssetProduct, item.product_id)
            if product is None:
                raise failure("当前已核目录产品丢失")
            products.append(
                {
                    "product_id": str(item.product_id),
                    "name": original_label(product.name, item.product_id, "产品"),
                    "product_code": item.product_code,
                    "version_number": item.version_number,
                    "asset_class": item.asset_class,
                    "terms_digest": item.terms_digest,
                    "minimum_purchase_cents": item.minimum_purchase_cents,
                    "lock_days": item.lock_days,
                    "redemption_delay_days": item.redemption_delay_days,
                    "annual_yield_bps": item.annual_yield_bps,
                    "early_withdrawal_loss_bps": item.early_withdrawal_loss_bps,
                }
            )
        return {
            **identity(session, user.id, engine),
            "protocol": "zhiyu-next-assets-state-v1",
            "server_as_of": now.isoformat(),
            "source_validation_status": "DEVELOPMENT_PENDING_ACTUAL_ASSET_ACCEPTANCE",
            "catalogue_status": catalogue.status,
            "catalogue_issues": catalogue.issues,
            "policies": policies,
            "products": products,
            "positions": position_views,
            "portfolios": portfolios,
            "position_projection_status": "VERIFIED_CURRENT_BANK_PROJECTION",
            "bank_authority": False,
        }


@router.post("/portfolios/preview")
def portfolio_preview(
    body: PortfolioRequest,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    result = preview_full_asset_execution(session, user.id, native_portfolio_request(body), now)
    return {**identity(session, user.id, engine), "preview": result.model_dump(mode="json")}


@router.post("/portfolios/prepare")
def portfolio_prepare(
    body: PortfolioRequest,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    actor: PrincipalDependency,
) -> dict[str, Any]:
    require_closure_guards()
    signed_user(actor, user.id, now)
    with serial_user(engine, user.id):
        with fresh_read(engine) as read:
            _epoch(read, user.id, body.expected_epoch_id)
            prior = portfolio_rejection_original(
                read, user.id, body.expected_epoch_id, body.client_request_id, now
            )
            if prior is not None:
                if prior["original_request"] != {
                    "path": PREFIX + "/portfolios/prepare",
                    "body": body.model_dump(mode="json"),
                }:
                    raise failure("同一组合原请求不能改配置或原键", "REQUEST_CONFLICT")
                error = prior["error"]
                raise PolicyLifecycleError(error["code"], error["message"], error["status_code"])
        try:
            result = prepare_full_asset_execution(
                engine, user.id, native_portfolio_request(body), now
            )
        except PolicyLifecycleError as error:
            reject_uncommitted_portfolio(engine, user.id, body, error, now)
            raise
    return {**identity(session, user.id, engine), "portfolio": result.model_dump(mode="json")}


@router.get("/portfolios/{portfolio_id}")
def portfolio_original(
    portfolio_id: UUID,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        result = read_full_asset_execution(session, user.id, portfolio_id, now)
        accepted = original_signed_acceptance(session, user.id, portfolio_id, now)
        return {
            **identity(session, user.id, engine),
            "portfolio": result.model_dump(mode="json"),
            "original_signed_acceptance": accepted.model_dump(mode="json") if accepted else None,
        }


@router.get("/portfolio-commands/{epoch_id}/{client_request_id}")
def portfolio_command(
    epoch_id: UUID,
    client_request_id: UUID,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        result = read_full_asset_execution_by_key(
            session,
            user.id,
            epoch_id,
            command_key(epoch_id, "asset-prepare", client_request_id),
            now,
        )
        return {
            **identity(session, user.id, engine),
            **result.model_dump(mode="json"),
            "wrapper_operation": portfolio_rejection_original(
                session, user.id, epoch_id, client_request_id, now
            ),
        }


@router.post("/portfolios/{portfolio_id}/confirm-and-execute")
def portfolio_confirm_execute(
    portfolio_id: UUID,
    body: PortfolioAcceptanceRequest,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    actor: PrincipalDependency,
) -> dict[str, Any]:
    require_closure_guards()
    signed_user(actor, user.id, now)
    native = FullAssetConfirmRequest(
        **body.model_dump(exclude={"client_request_id"}),
        idempotency_key=command_key(
            body.expected_epoch_id, "asset-confirm", body.client_request_id
        ),
    )
    with serial_user(engine, user.id):
        with fresh_read(engine) as read:
            _epoch(read, user.id, body.expected_epoch_id)
            original = read_full_asset_execution(read, user.id, portfolio_id, now)
            prior = original_signed_acceptance(read, user.id, portfolio_id, now)
            if original.original_portfolio.portfolio_hash != body.reviewed_portfolio_hash:
                raise failure("必须接受原完整组合hash", "ASSET_CONFIRMATION_MISMATCH")
        with Session(engine) as writer, writer.begin():
            _epoch(writer, user.id, body.expected_epoch_id)
            if prior is not None:
                if prior.original_wrapper_request != body:
                    raise failure("原确认不能改变原body或请求键", "REQUEST_CONFLICT")
            else:
                if now >= original.original_portfolio.expires_at:
                    raise failure("原组合已过期，不能签署当前首次受理", "EXPIRED_EFFECT")
                accepted = SignedPortfolioAcceptance(
                    user_id=user.id,
                    epoch_id=body.expected_epoch_id,
                    portfolio_id=portfolio_id,
                    portfolio_hash=body.reviewed_portfolio_hash,
                    original_wrapper_request=body,
                    original_native_request=native,
                    principal_at_acceptance=actor,
                    accepted_at=now,
                    expires_at=min(original.original_portfolio.expires_at, actor.expires_at),
                )
                # This is a signed request binding, not a substitute bank grant.
                store(
                    writer,
                    user.id,
                    "BINDING",
                    f"asset-acceptance:{portfolio_id}",
                    accepted.model_dump(mode="json"),
                    now,
                )
        confirm_full_asset_execution(engine, user.id, portfolio_id, native, now)
        result = drive_original_portfolio(
            engine,
            user.id,
            portfolio_id,
            OriginalPortfolioRequest(
                expected_epoch_id=body.expected_epoch_id,
                reviewed_portfolio_hash=body.reviewed_portfolio_hash,
            ),
            now,
        )
    return {**identity(session, user.id, engine), "portfolio": result.model_dump(mode="json")}


@router.post("/portfolios/{portfolio_id}/continue-original")
def portfolio_continue(
    portfolio_id: UUID,
    body: OriginalPortfolioRequest,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    actor: PrincipalDependency,
) -> dict[str, Any]:
    signed_user(actor, user.id, now)
    with serial_user(engine, user.id):
        result = drive_original_portfolio(engine, user.id, portfolio_id, body, now)
    return {**identity(session, user.id, engine), "portfolio": result.model_dump(mode="json")}


@router.post("/maturity/preview")
def maturity_preview(
    body: FullMaturityRequest,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    result = preview_maturity_execution(session, user.id, body, now)
    return {**identity(session, user.id, engine), "maturity": result.model_dump(mode="json")}


@router.post("/maturity/prepare")
def maturity_prepare(
    body: FullMaturityRequest,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    actor: PrincipalDependency,
) -> dict[str, Any]:
    signed_user(actor, user.id, now)
    with serial_user(engine, user.id):
        result = prepare_maturity_execution(engine, user.id, body, actor, now)
    return {**identity(session, user.id, engine), "maturity": result.model_dump(mode="json")}


@router.get("/maturity/by-key/{idempotency_key:path}")
def maturity_original(
    idempotency_key: str,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        result = lookup_maturity_execution(session, user.id, idempotency_key, now)
        return {**identity(session, user.id, engine), **result.model_dump(mode="json")}


@router.post("/maturity/actions/{action_id}/confirm-and-execute")
def maturity_confirm_execute(
    action_id: UUID,
    body: FullMaturityConfirmation,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    actor: PrincipalDependency,
) -> dict[str, Any]:
    signed_user(actor, user.id, now)
    with serial_user(engine, user.id):
        confirm_maturity_execution(engine, user.id, action_id, body, actor, now)
        result = execute_maturity_execution(
            engine,
            user.id,
            action_id,
            FullMaturityExecuteRequest(
                expected_epoch_id=body.expected_epoch_id,
                reviewed_command_hash=body.reviewed_command_hash,
            ),
            actor,
            now,
        )
    return {**identity(session, user.id, engine), "maturity": result.model_dump(mode="json")}


@router.post("/maturity/actions/{action_id}/execute-original")
def maturity_continue(
    action_id: UUID,
    body: FullMaturityExecuteRequest,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    actor: PrincipalDependency,
) -> dict[str, Any]:
    signed_user(actor, user.id, now)
    with serial_user(engine, user.id):
        result = execute_maturity_execution(engine, user.id, action_id, body, actor, now)
    return {**identity(session, user.id, engine), "maturity": result.model_dump(mode="json")}


def enforce_zhiyu_asset_acceptance(
    engine: Engine, session: Session, action: ActionPlan, command: BankCommand, now: datetime
) -> None:
    """Install at both original new-accept seams; existing bank results bypass new grants."""
    if action.user_id != command.effect.user_id or action.id != command.effect.operation_id:
        raise failure("资产动作与银行command原身份不一致")
    if command.effect.action_type == "PURCHASE_ASSET":
        from app.services.execution_bank import has_full_asset_batch_binding

        if not has_full_asset_batch_binding(session, action):
            raise failure(
                "Next新购买必须来自完整原组合，不能普通购买降级", "ASSET_PROTOCOL_REQUIRED"
            )
        marker = action.request.get("full_asset_execution")
        if not isinstance(marker, dict):
            raise failure("原整组marker缺失不能降级为legacy购买")
        try:
            portfolio_id = UUID(marker["portfolio_id"])
        except (ValueError, TypeError, KeyError):
            raise failure("原整组动作绑定缺失") from None
        with fresh_read(engine) as read:
            original = read_full_asset_execution(read, action.user_id, portfolio_id, now)
            accepted = original_signed_acceptance(read, action.user_id, portfolio_id, now)
            if (
                accepted is None
                or not original.original_consent_verified
                or not accepted.accepted_at <= now < accepted.expires_at
                or action.id not in {batch.action_id for batch in original.batches}
                or accepted.portfolio_hash != marker.get("portfolio_hash")
            ):
                raise failure("首次银行受理须已绑定真实USER原整体确认", "ASSET_CONSENT_NOT_PROVEN")
            source = anchored(
                read, action.user_id, "BINDING", f"asset-portfolio-source:{portfolio_id}"
            )
            is_reinvestment = (
                original.original_portfolio.original_request.idempotency_key.startswith(
                    f"zhiyu-next:{original.epoch_id}:maturity-reinvest:"
                )
            )
            if is_reinvestment or source is not None:
                if source is None:
                    raise failure("实收再配置尚缺一次性完整来源绑定")
                reference = decode_record(ReinvestmentPortfolioReference, source)
                bound = original_reinvestment(read, action.user_id, reference.source_action_id, now)
                if bound is None or bound[0].portfolio_id != portfolio_id:
                    raise failure("原实收来源已指向另一组合")
    elif command.effect.action_type == "REDEEM_ASSET" and (
        command.effect.fee_cents or command.effect.loss_cents
    ):
        with fresh_read(engine) as read:
            binding, original_loss = original_loss_binding(read, action.user_id, action.id, now)
            accepted_loss = original_loss_consent(read, action.user_id, binding, now)
            if (
                accepted_loss is None
                or not accepted_loss.accepted_at <= now < accepted_loss.expires_at
                or binding.effect != command.effect
                or binding.effect_hash != command.effect_hash
                or original_loss.autonomy_level != "ASK_ONCE"
                or load_execution_quote(
                    read, action.user_id, binding.source_request.position_id, now
                )
                != binding.quote
            ):
                raise failure("首次银行受理须真实USER同意原完整报价", "ASSET_LOSS_CONSENT_REQUIRED")
    # The original full-portfolio/MVP and complete FULL financial veto guards remain
    # independent and mandatory. Existing bank results recover their original key.
