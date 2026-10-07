"""Staged explicit principal-loss branch; original financial engines remain AND gates.

No legacy Recovery configuration, effect algorithm or cost cap is changed. Root
must install this private server branch in the three original execution seams.
"""

import json
import os
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol, Self, cast
from uuid import UUID, uuid5

from app.db.models import (
    ActionPlan,
    AssetPosition,
    AssetProduct,
    DecisionRun,
    EvidenceItem,
    PolicyVersion,
)
from app.domain.asset_allocation_types import AssetProductTerms, FixedPrincipalTerms
from app.domain.boundary_types import BoundaryModel, BoundaryPolicyVersion
from app.domain.decision_trace_types import DecisionTrace
from app.domain.execution import execution_effect_hash, revalidate_execution
from app.domain.execution_types import (
    BankCommand,
    ExecutionContext,
    ExecutionEffect,
    ExecutionValidation,
)
from app.domain.full_asset_execution import Hash
from app.domain.local_actor_session_types import LocalActorPrincipal, require_local_user
from app.domain.policy_configuration import (
    UUIDReference,
    configuration_hash,
    validate_configuration,
)
from app.domain.recovery_types import RecoveryQuote
from app.services.action_contracts import ActionResponse, PrepareActionRequest
from app.services.audit_chain import verify_audit_chain
from app.services.demo_console import _epoch
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.simulated_redemption_quote import PROTOCOL as PRICE_PROTOCOL
from app.services.simulated_redemption_quote import early_loss_cents
from app.services.zhiyu_orchestration import MARKER as RECORD_MARKER
from app.services.zhiyu_orchestration import _valid
from app.zhiyu_next_isolation import require_zhiyu_next_engine
from pydantic import model_validator
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

ALGORITHM = "lossy-early-redemption-v1"
MARKER = "zhiyu_lossy_redemption"
GUARDS_VERSION = "zhiyu-asset-closure-guards-v1"


def requires_zhiyu_asset_closure(engine: Engine) -> bool:
    """Preserve the original Demo; the declared Next engine must match exactly."""
    if os.environ.get("ZHIYU_NEXT_VARIANT") != "zhiyu-next":
        return False
    require_zhiyu_next_engine(engine)
    return True


def failure(message: str) -> PolicyLifecycleError:
    return PolicyLifecycleError("ZHIYU_LOSS_ORIGINAL_INVALID", message, 409)


class LossNativePrepareRequest(BoundaryModel):
    """Private Python input; never added to a public bank or action DTO."""

    protocol: Literal["lossy-early-redemption-v1"] = "lossy-early-redemption-v1"
    expected_epoch_id: UUIDReference
    position_id: UUIDReference
    client_request_id: UUIDReference
    native_key: str

    @model_validator(mode="after")
    def fixed_original_key(self) -> Self:
        if self.native_key != (
            f"zhiyu-next:{self.expected_epoch_id}:lossy:{self.client_request_id}"
        ):
            raise ValueError(
                "Loss native key must bind the server epoch and exact original request"
            )
        return self


class InstalledLossPrepare(Protocol):
    def __call__(
        self,
        engine: Engine,
        user_id: UUID,
        request: PrepareActionRequest,
        now: datetime,
        *,
        _zhiyu_loss_request: LossNativePrepareRequest,
    ) -> ActionResponse: ...


def installed_loss_prepare(
    engine: Engine,
    user_id: UUID,
    request: PrepareActionRequest,
    body: LossNativePrepareRequest,
    now: datetime,
) -> ActionResponse:
    import inspect

    from app.services import execution

    require_zhiyu_next_engine(engine)

    if (
        getattr(execution, "ZHIYU_ASSET_CLOSURE_GUARDS_VERSION", None) != GUARDS_VERSION
        or "_zhiyu_loss_request" not in inspect.signature(execution.prepare_action).parameters
    ):
        raise failure("显式有损原生准备分支尚未安装")
    installed = cast(InstalledLossPrepare, execution.prepare_action)
    return installed(engine, user_id, request, now, _zhiyu_loss_request=body)


class LossPositionSource(BoundaryModel):
    position_id: UUID
    user_id: UUID
    account_id: UUID
    product_id: UUID
    goal_id: UUID | None
    original_policy_version_id: UUID
    principal_cents: int
    accrued_interest_cents: Literal[0]
    purchased_at: datetime
    maturity_at: datetime
    status: Literal["HELD"]


class LossSourceBasis(BoundaryModel):
    """Exact original terms and both formal native authorizations, not a new grant."""

    position: LossPositionSource
    product: AssetProductTerms
    original_authority: BoundaryPolicyVersion
    current_authority: BoundaryPolicyVersion
    early_rule: dict[str, Any]
    quote: RecoveryQuote
    quote_evidence_hash: Hash
    quote_payload: dict[str, Any]
    source_hash: Hash

    @model_validator(mode="after")
    def principal_only_source(self) -> Self:
        position, product, quote = self.position, self.product, self.quote
        terms = FixedPrincipalTerms.model_validate(product.maturity_rule)
        values = self.model_dump(mode="json", exclude={"source_hash"})
        if (
            type(position.principal_cents) is not int
            or position.principal_cents <= 0
            or product.product_id != position.product_id
            or product.asset_class != "FIXED_DEPOSIT"
            or product.principal_fluctuation
            or product.risk_level != 0
            or product.created_at > position.purchased_at
            or product.effective_from > position.purchased_at
            or (
                product.effective_until is not None
                and position.purchased_at >= product.effective_until
            )
            or position.maturity_at != position.purchased_at + timedelta(days=terms.term_days)
            or terms.term_days < product.lock_days
            or terms.settlement_delay_days != product.redemption_delay_days
            or self.early_rule
            != {
                "allowed": True,
                "requires_confirmation_if_loss": True,
                "loss_basis": "principal_cents",
                "simulation": True,
            }
            or any(type(v) is not bool for k, v in self.early_rule.items() if k != "loss_basis")
            or self.original_authority.version_id != position.original_policy_version_id
            or self.original_authority.policy_id != self.current_authority.policy_id
            or not max(self.original_authority.confirmed_at, self.original_authority.valid_from)
            <= position.purchased_at
            or (
                self.original_authority.valid_until is not None
                and position.purchased_at >= self.original_authority.valid_until
            )
            or quote.user_id != position.user_id
            or quote.position_id != position.position_id
            or quote.product_id != position.product_id
            or quote.product_version_number != product.version_number
            or quote.terms_digest != product.terms_digest
            or quote.principal_cents != position.principal_cents
            or quote.fee_cents != 0
            or quote.loss_cents
            != early_loss_cents(position.principal_cents, product.early_withdrawal_loss_bps)
            or quote.net_cents != quote.principal_cents - quote.loss_cents
            or quote.kind != "EARLY_WITHDRAW"
            or not position.purchased_at <= quote.request_at < position.maturity_at
            or quote.expires_at != quote.request_at + timedelta(minutes=15)
            or quote.principal_available_at
            != quote.request_at + timedelta(days=product.redemption_delay_days)
            or quote.quote_id != uuid5(position.position_id, PRICE_PROTOCOL)
            or quote.evidence_ids != [quote.quote_id]
            or configuration_hash(self.quote_payload) != self.quote_evidence_hash
            or self.quote_payload.get("quote") != quote.model_dump(mode="json")
            or self.quote_payload.get("price_protocol") != PRICE_PROTOCOL
            or self.quote_payload.get("rounding") != "CEIL_CENT"
            or self.quote_payload.get("simulation") is not True
            or self.source_hash != configuration_hash(values)
        ):
            raise ValueError("Exact original principal-only terms and price source are required")
        expected_goal = str(position.goal_id) if position.goal_id else None
        for grant in (self.original_authority, self.current_authority):
            config = grant.configuration
            if (
                grant.content_hash != configuration_hash(config)
                or validate_configuration(config) != config
                or config.get("type") != "asset_authorization"
                or config.get("allow_early_withdrawal_with_penalty") is not True
                or config.get("scope") != ("goal" if position.goal_id else "general_idle_funds")
                or config.get("goal_id") != expected_goal
                or position.principal_cents > config["single_action_cap_cents"]
                or product.asset_class not in config["allowed_asset_classes"]
                or product.redemption_delay_days > config["max_redemption_delay_days"]
                or product.lock_days > config["max_lock_days"]
                or product.risk_level > config["max_principal_risk_level"]
            ):
                raise ValueError(
                    "Both original and current native early-exit permission remain mandatory"
                )
        if not max(
            self.current_authority.confirmed_at, self.current_authority.valid_from
        ) <= quote.request_at or (
            self.current_authority.valid_until is not None
            and quote.request_at >= self.current_authority.valid_until
        ):
            raise ValueError(
                "Current formal native authority must be effective at the original price time"
            )
        return self


class LossNativeMarker(BoundaryModel):
    protocol: Literal["lossy-early-redemption-v1"] = "lossy-early-redemption-v1"
    user_id: UUID
    epoch_id: UUID
    original_request: LossNativePrepareRequest
    intent_evidence_id: UUID
    intent_evidence_hash: Hash
    source: LossSourceBasis
    effect_hash: Hash
    bank_authority: Literal[False] = False

    @model_validator(mode="after")
    def immutable_source(self) -> Self:
        if (
            self.epoch_id != self.original_request.expected_epoch_id
            or self.source.position.user_id != self.user_id
            or self.source.position.position_id != self.original_request.position_id
            or self.intent_evidence_id
            != uuid5(
                self.epoch_id,
                f"{RECORD_MARKER}:EVENT:asset-loss-intent:{self.original_request.position_id}",
            )
        ):
            raise ValueError("The exact audited source and original native request must agree")
        return self


def decode_marker(raw: dict[str, Any]) -> LossNativeMarker:
    if set(raw) != set(LossNativeMarker.model_fields) or raw.get("bank_authority") is not False:
        raise failure("原有损支取协议字段缺失或形状改变")
    source = raw.get("source")
    if not isinstance(source, dict) or set(source) != set(LossSourceBasis.model_fields):
        raise failure("原有损支取来源形状改变")
    nested_models: dict[str, type[BoundaryModel]] = {
        "product": AssetProductTerms,
        "original_authority": BoundaryPolicyVersion,
        "current_authority": BoundaryPolicyVersion,
        "quote": RecoveryQuote,
    }
    if (
        any(
            not isinstance(source.get(name), dict) or set(source[name]) != set(model.model_fields)
            for name, model in nested_models.items()
        )
        or not isinstance(raw.get("original_request"), dict)
        or set(raw["original_request"]) != set(LossNativePrepareRequest.model_fields)
    ):
        raise failure("原有损支取完整产品、权限、报价或私有请求字段缺失")
    position = source.get("position")
    if (
        not isinstance(position, dict)
        or set(position) != set(LossPositionSource.model_fields)
        or type(position.get("principal_cents")) is not int
        or type(position.get("accrued_interest_cents")) is not int
    ):
        raise failure("原本金和利息基数类型改变")
    try:
        return LossNativeMarker.model_validate_json(json.dumps(raw))
    except (ValueError, TypeError, KeyError, OverflowError):
        raise failure("原有损支取来源、报价或摘要未通过核验") from None


def verify_loss_effect(marker: LossNativeMarker, effect: ExecutionEffect) -> None:
    position, quote, source = marker.source.position, marker.source.quote, marker.source
    if (
        effect.user_id != marker.user_id
        or effect.action_type != "REDEEM_ASSET"
        or effect.position_id != position.position_id
        or effect.business_key != f"redeem:{position.position_id}"
        or effect.position_account_id != position.account_id
        or effect.goal_id != position.goal_id
        or effect.original_policy_version_id != position.original_policy_version_id
        or effect.policy_id != source.current_authority.policy_id
        or effect.policy_version_id != source.current_authority.version_id
        or source.current_authority.version_id not in effect.policy_version_ids
        or effect.product_id != quote.product_id
        or effect.product_version_number != quote.product_version_number
        or effect.terms_digest != quote.terms_digest
        or effect.quote_id != quote.quote_id
        or effect.amount_cents != quote.principal_cents
        or effect.net_cents != quote.net_cents
        or effect.fee_cents != 0
        or effect.loss_cents != quote.loss_cents
        or effect.loss_cents <= 0
        or effect.cash_uses
        or effect.income_uses
        or str(effect.destination_account_id) != source.quote_payload.get("destination_account_id")
        or source.quote_payload.get("goal_id") != (str(effect.goal_id) if effect.goal_id else None)
        or not quote.request_at <= effect.valid_from < effect.expires_at <= quote.expires_at
        or effect.settlement_delay_days != source.product.redemption_delay_days
        or marker.effect_hash != execution_effect_hash(effect)
    ):
        raise failure("本金、原购入权限、原报价或完整经济后果绑定不一致")


def _version(row: PolicyVersion) -> BoundaryPolicyVersion:
    if row.confirmed_at is None or row.valid_from is None:
        raise failure("原购入或当前正式权限缺少真实生效时间")
    return BoundaryPolicyVersion(
        policy_id=row.policy_id,
        version_id=row.id,
        configuration=row.configuration,
        content_hash=row.content_hash,
        confirmed_at=row.confirmed_at,
        valid_from=row.valid_from,
        valid_until=row.valid_until,
        evidence_ids=[UUID(value) for value in row.evidence_ids],
    )


def read_source_basis(session: Session, user_id: UUID, effect: ExecutionEffect) -> LossSourceBasis:
    position = session.get(AssetPosition, effect.position_id)
    product = session.get(AssetProduct, effect.product_id)
    historical = session.get(PolicyVersion, effect.original_policy_version_id)
    current = session.get(PolicyVersion, effect.policy_version_id)
    quote_row = session.get(EvidenceItem, effect.quote_id)
    if (
        position is None
        or product is None
        or historical is None
        or current is None
        or quote_row is None
        or position.user_id != user_id
        or historical.user_id != user_id
        or current.user_id != user_id
        or position.status != "HELD"
        or position.maturity_at is None
        or position.policy_version_id is None
        or position.accrued_yield_cents != 0
        or quote_row.user_id != user_id
        or quote_row.status != "VALID"
        or quote_row.evidence_level != "BANK_CONFIRMED"
        or quote_row.source_type != "SIMULATED_REDEMPTION_QUOTE"
        or quote_row.source_ref != f"{PRICE_PROTOCOL}:{position.id}"
    ):
        raise failure("原持仓、原条款、原正式权限或服务器报价缺失")
    product_terms = AssetProductTerms(
        **{
            name: getattr(product, name)
            for name in AssetProductTerms.model_fields
            if name not in {"product_id", "terms_digest"}
        },
        product_id=product.id,
        terms_digest=configuration_hash(product.maturity_rule),
    )
    values: dict[str, Any] = {
        "position": LossPositionSource(
            position_id=position.id,
            user_id=user_id,
            account_id=position.account_id,
            product_id=position.product_id,
            goal_id=position.goal_id,
            original_policy_version_id=position.policy_version_id,
            principal_cents=position.principal_cents,
            accrued_interest_cents=0,
            purchased_at=position.purchased_at,
            maturity_at=position.maturity_at,
            status="HELD",
        ).model_dump(mode="json"),
        "product": product_terms.model_dump(mode="json"),
        "original_authority": _version(historical).model_dump(mode="json"),
        "current_authority": _version(current).model_dump(mode="json"),
        "early_rule": product.early_withdrawal_rule,
        "quote": quote_row.content["quote"],
        "quote_evidence_hash": quote_row.content_hash,
        "quote_payload": quote_row.content,
    }
    try:
        return LossSourceBasis.model_validate_json(
            json.dumps({**values, "source_hash": configuration_hash(values)})
        )
    except (ValueError, TypeError, KeyError, OverflowError):
        raise failure("原本金条款和原报价计算未通过核验") from None


def original_intent(
    session: Session, user_id: UUID, body: LossNativePrepareRequest, now: datetime
) -> EvidenceItem:
    if _epoch(session, user_id) != body.expected_epoch_id:
        raise failure("有损支取原请求跨轮次")
    audit = verify_audit_chain(session, user_id, body.expected_epoch_id)
    source_id = uuid5(
        body.expected_epoch_id, f"{RECORD_MARKER}:EVENT:asset-loss-intent:{body.position_id}"
    )
    source = session.get(EvidenceItem, source_id)
    if source is None or audit.status != "VALID":
        raise failure("有损支取缺少完整已审计原请求")
    raw = _valid(session, source, user_id, body.expected_epoch_id)["payload"]
    try:
        actor = LocalActorPrincipal.model_validate_json(json.dumps(raw["principal_at_capture"]))
        captured_at = datetime.fromisoformat(raw["captured_at"].replace("Z", "+00:00"))
        require_local_user(actor, user_id, captured_at)
        if (
            captured_at > now
            or raw.get("protocol") != ALGORITHM
            or raw.get("bank_authority") is not False
            or raw.get("user_id") != str(user_id)
            or raw.get("epoch_id") != str(body.expected_epoch_id)
            or raw.get("native_key") != body.native_key
            or raw.get("original_request")
            != {
                "expected_epoch_id": str(body.expected_epoch_id),
                "position_id": str(body.position_id),
                "client_request_id": str(body.client_request_id),
            }
        ):
            raise ValueError("Original signed source scope changed")
    except (KeyError, TypeError, ValueError):
        raise failure("有损支取原请求未绑定真实USER声明与服务器时间") from None
    return source


def produce_loss_native_marker(
    session: Session,
    user_id: UUID,
    request: PrepareActionRequest,
    body: LossNativePrepareRequest | None,
    effect: ExecutionEffect,
    context: ExecutionContext,
    validation: ExecutionValidation,
    now: datetime,
) -> dict[str, Any] | None:
    """Next-only prepare seam: an ordinary RedeemIntent cannot create a cost branch."""
    if effect.action_type != "REDEEM_ASSET" or not (effect.fee_cents or effect.loss_cents):
        if body is not None:
            raise failure("有损协议不能改变为无损或到期动作")
        return None
    if body is None:
        raise failure("Next有损支取必须走显式新协议，普通RedeemIntent不能降级")
    if (
        body.position_id != effect.position_id
        or request.idempotency_key != body.native_key
        or request.intent.model_dump(mode="json")
        != {"kind": "redeem_asset", "position_id": str(body.position_id)}
        or context.user_id != user_id
        or context.snapshot.as_of != now
        or context.source_issues
        or validation.status != "CONFIRMATION_REQUIRED"
        or validation.effect_hash != execution_effect_hash(effect)
        or validation != revalidate_execution(effect, context)
    ):
        raise failure("原执行重验、缺口改善和ASK_ONCE门未通过")
    intent = original_intent(session, user_id, body, now)
    marker = LossNativeMarker(
        user_id=user_id,
        epoch_id=body.expected_epoch_id,
        original_request=body,
        intent_evidence_id=intent.id,
        intent_evidence_hash=intent.content_hash,
        source=read_source_basis(session, user_id, effect),
        effect_hash=execution_effect_hash(effect),
    )
    verify_loss_effect(marker, effect)
    capture_loss_native(session, marker)
    return marker.model_dump(mode="json")


def capture_loss_native(session: Session, marker: LossNativeMarker) -> None:
    from app.services.decision_recording import capture_evidence, capture_versions, current_capture

    capture = current_capture(session)
    if capture is None:
        raise failure("有损支取缺少原决策输入捕获")
    capture.inputs[MARKER] = marker.model_dump(mode="json")
    capture.algorithms[MARKER] = ALGORITHM
    capture_evidence(
        session, marker.user_id, [marker.intent_evidence_id, marker.source.quote.quote_id]
    )
    capture_versions(
        session,
        marker.user_id,
        [marker.source.original_authority.version_id, marker.source.current_authority.version_id],
    )


def has_loss_native_binding(session: Session, action: ActionPlan) -> bool:
    """Recognition is fail closed across the current request and its original PREPARE."""
    run = session.get(DecisionRun, action.decision_run_id)
    if (
        run is None
        or run.user_id != action.user_id
        or configuration_hash(run.input_snapshot) != run.snapshot_hash
    ):
        if action.action_type == "ASSET_REDEEM":
            raise failure("原赎回PREPARE轨迹缺失，不能降级")
        return False
    raw = run.input_snapshot.get("decision_trace", {})
    if not isinstance(raw, dict):
        raise failure("原赎回PREPARE轨迹形状错误")
    inputs, algorithms = raw.get("inputs", {}), raw.get("algorithm_versions", {})
    planning = inputs.get("planning", {}) if isinstance(inputs, dict) else {}
    original = inputs.get("action_request", {}) if isinstance(inputs, dict) else {}
    return bool(
        MARKER in action.request
        or isinstance(algorithms, dict)
        and algorithms.get(MARKER) == ALGORITHM
        or isinstance(planning, dict)
        and MARKER in planning
        or isinstance(original, dict)
        and MARKER in original
    )


def verify_frozen_loss_native_trace(trace: DecisionTrace) -> LossNativeMarker:
    try:
        request = trace.inputs["action_request"]
        marker = decode_marker(request[MARKER])
        effect = ExecutionEffect.model_validate_json(json.dumps(trace.inputs["effect"]))
        context = ExecutionContext.model_validate_json(
            json.dumps(trace.inputs["execution_context"])
        )
        validation = ExecutionValidation.model_validate_json(
            json.dumps(trace.outcome["validation"])
        )
        from app.domain.execution_types import ConfirmationGrant

        raw_confirmation = trace.inputs.get("confirmation")
        confirmation = (
            ConfirmationGrant.model_validate_json(json.dumps(raw_confirmation))
            if raw_confirmation is not None
            else None
        )
        verified = revalidate_execution(effect, context, confirmation=confirmation)
        verify_loss_effect(marker, effect)
        evidence = {item.id: item for item in trace.sources}
        policies = {item.id: item for item in trace.policies}
        intent = evidence[marker.intent_evidence_id]
        price = evidence[marker.source.quote.quote_id]
        for grant in (marker.source.original_authority, marker.source.current_authority):
            recorded = policies[grant.version_id]
            if (
                recorded.user_id != marker.user_id
                or recorded.policy_id != grant.policy_id
                or recorded.configuration != grant.configuration
                or recorded.configuration_hash != grant.content_hash
                or recorded.captured_configuration_hash != grant.content_hash
                or recorded.configuration_integrity != "VERIFIED"
                or recorded.confirmed_at != grant.confirmed_at
                or recorded.valid_from != grant.valid_from
                or recorded.valid_to != grant.valid_until
            ):
                raise ValueError("Frozen formal native policy originals differ")
        if (
            trace.algorithm_versions.get(MARKER) != ALGORITHM
            or trace.user_id != marker.user_id
            or trace.action_id != effect.operation_id
            or trace.inputs["planning"][MARKER] != marker.model_dump(mode="json")
            or trace.as_of != context.snapshot.as_of
            or trace.outcome.get("autonomy_level") != "ASK_ONCE"
            or request["execution"]
            != BankCommand(effect=effect, effect_hash=marker.effect_hash).model_dump(mode="json")
            or validation != verified
            or validation.status not in {"READY", "CONFIRMATION_REQUIRED"}
            or context.redemption_quote != marker.source.quote
            or marker.source.product not in context.products
            or marker.source.current_authority not in context.versions
            or intent.user_id != marker.user_id
            or intent.evidence_level != "USER_DECLARED"
            or intent.source_type != "ZHIYU_NEXT_EVENT"
            or intent.status_at_decision != "VALID"
            or intent.content_integrity != "VERIFIED"
            or intent.content_hash != marker.intent_evidence_hash
            or intent.captured_content_hash != marker.intent_evidence_hash
            or price.user_id != marker.user_id
            or price.evidence_level != "BANK_CONFIRMED"
            or price.source_type != "SIMULATED_REDEMPTION_QUOTE"
            or price.source_ref != f"{PRICE_PROTOCOL}:{marker.original_request.position_id}"
            or price.status_at_decision != "VALID"
            or price.content_integrity != "VERIFIED"
            or price.content != marker.source.quote_payload
            or price.content_hash != marker.source.quote_evidence_hash
            or price.captured_content_hash != marker.source.quote_evidence_hash
        ):
            raise ValueError("Frozen native loss revalidation differs")
        return marker
    except (KeyError, TypeError, ValueError, AttributeError, PolicyLifecycleError):
        # Original trace/audit readers already translate a ValueError into their
        # closed integrity result; never make a corrupt source look supported.
        raise ValueError("原有损支取轨迹无法完整重演数学、来源和原请求") from None


def read_original_loss_native(
    session: Session,
    user_id: UUID,
    action: ActionPlan,
    command: BankCommand,
    now: datetime,
) -> LossNativeMarker:
    from app.services.decision_trace import get_decision_trace

    trace = get_decision_trace(session, user_id, action.decision_run_id, now)
    if (
        trace.completeness != "COMPLETE"
        or trace.trace is None
        or trace.audit_chain_status != "VALID"
    ):
        raise failure("原有损支取决策和审计未完整核验")
    marker = verify_frozen_loss_native_trace(trace.trace)
    if (
        marker.epoch_id != _epoch(session, user_id)
        or marker.user_id != user_id
        or action.request.get(MARKER) != marker.model_dump(mode="json")
        or action.request_hash != configuration_hash(action.request)
        or action.idempotency_key
        != "action:" + configuration_hash({"key": marker.original_request.native_key})
        or action.autonomy_level != "ASK_ONCE"
        or command.effect_hash != marker.effect_hash
        or command.effect.operation_id != action.id
    ):
        raise failure("原有损支取版本标记或原银行键不同")
    intent = original_intent(session, user_id, marker.original_request, now)
    if intent.id != marker.intent_evidence_id or intent.content_hash != marker.intent_evidence_hash:
        raise failure("原USER声明和源摘要改变")
    verify_loss_effect(marker, command.effect)
    return marker


def recheck_loss_native_source(
    session: Session,
    action: ActionPlan,
    command: BankCommand,
    context: ExecutionContext,
    validation: ExecutionValidation,
    now: datetime,
) -> LossNativeMarker | None:
    """Phase one/first bank AND gate; never used when the original bank key exists."""
    if command.effect.action_type != "REDEEM_ASSET" or not (
        command.effect.fee_cents or command.effect.loss_cents
    ):
        if has_loss_native_binding(session, action):
            raise failure("原有损协议不能转为无损动作")
        return None
    if not has_loss_native_binding(session, action):
        raise failure("有损原协议标记缺失，不能普通执行降级")
    marker = read_original_loss_native(session, action.user_id, action, command, now)
    if (
        not command.effect.valid_from <= now < command.effect.expires_at
        or not marker.source.quote.request_at <= now < marker.source.quote.expires_at
        or context.snapshot.as_of != now
        or context.user_id != action.user_id
        or context.source_issues
        or validation.status != "READY"
        or validation.effect_hash != command.effect_hash
        or read_source_basis(session, action.user_id, command.effect) != marker.source
    ):
        raise failure("当前购入来源、报价或原执行门在首次受理前改变")
    capture_loss_native(session, marker)
    return marker


def verify_loss_native_prepare_replay(
    session: Session,
    user_id: UUID,
    action: ActionPlan,
    request: PrepareActionRequest,
    body: LossNativePrepareRequest,
    now: datetime,
) -> ActionResponse:
    from app.services.execution import get_action

    try:
        command = BankCommand.model_validate_json(json.dumps(action.request["execution"]))
        original = read_original_loss_native(session, user_id, action, command, now)
        if (
            original.original_request != body
            or request.idempotency_key != body.native_key
            or request.intent.model_dump(mode="json")
            != {"kind": "redeem_asset", "position_id": str(body.position_id)}
        ):
            raise ValueError("Replay cannot replace the original private request")
    except (KeyError, ValueError, TypeError):
        raise failure("同一原有损银行键不能替换请求或完整协议") from None
    return get_action(session, user_id, action.id, now)
