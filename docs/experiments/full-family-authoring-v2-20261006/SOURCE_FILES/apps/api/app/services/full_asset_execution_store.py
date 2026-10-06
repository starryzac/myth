"""Read immutable portfolio identities and original actions; no new permissions."""

import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from app.db.full_models import (
    FullAssetExecutionBatch,
    FullAssetExecutionConsent,
    FullAssetExecutionPortfolio,
)
from app.db.models import ActionPlan, AuditEpoch, EvidenceItem
from app.domain.boundary_types import BoundaryModel
from app.domain.full_asset_execution import (
    FullAssetBatchBinding,
    FullAssetConfirmRequest,
    FullAssetExecuteRequest,
    FullAssetFrozenPortfolio,
    FullAssetPrepareRequest,
    Hash,
)
from app.domain.policy_configuration import configuration_hash
from app.services.action_contracts import ActionResponse
from app.services.audit_chain import audit_read_scope, row_copy
from app.services.decision_trace import get_decision_trace
from app.services.execution import get_action
from app.services.full_policy_lifecycle import _read_snapshot
from app.services.policy_lifecycle import PolicyLifecycleError, _now
from sqlalchemy import select
from sqlalchemy.orm import Session


class FullAssetBatchView(BoundaryModel):
    batch_number: int
    action_id: UUID
    bank_idempotency_key: str
    original_action: ActionResponse | None
    original_request_hash: Hash | None
    original_trace_verified: bool
    current_action_missing: bool
    receipt_is_current_authority: Literal[False] = False


class FullAssetConsentView(BoundaryModel):
    consent_id: UUID
    user_id: UUID
    epoch_id: UUID
    portfolio_id: UUID
    idempotency_key: str
    original_request: FullAssetConfirmRequest
    request_hash: Hash
    portfolio_hash: Hash
    evidence_id: UUID
    evidence_hash: Hash
    original_evidence: dict[str, Any]
    current_evidence_status: Literal[
        "CURRENT_EVIDENCE_MATCHED", "RETAINED_ORIGINAL_CURRENT_EVIDENCE_MISSING"
    ]
    current_evidence_verified: bool
    receipt_is_current_authority: Literal[False] = False
    current_authority_assessed: Literal[False] = False


class FullAssetExecutionResponse(BoundaryModel):
    schema_version: Literal["full-asset-execution-v1"] = "full-asset-execution-v1"
    simulation: Literal[True] = True
    user_id: UUID
    epoch_id: UUID
    as_of: datetime
    original_portfolio: FullAssetFrozenPortfolio
    original_request_hash: Hash
    state: Literal[
        "PREPARED_UNRESERVED",
        "CONFIRMED_UNRESERVED",
        "PARTIALLY_SETTLED",
        "SERVICE_RECEIPTS_VERIFIED",
        "UNRESOLVED",
        "STOPPED",
        "RETAINED_HISTORY",
    ]
    original_consent_id: UUID | None
    original_consent_evidence_id: UUID | None
    original_consent: FullAssetConsentView | None
    original_consent_verified: bool
    original_consent_evidence_status: Literal[
        "CURRENT_EVIDENCE_MATCHED", "RETAINED_ORIGINAL_CURRENT_EVIDENCE_MISSING", "NOT_RECORDED"
    ]
    current_epoch_open: bool
    batches: list[FullAssetBatchView]
    all_original_service_receipts_verified: bool
    bank_authority: Literal[False] = False
    funds_reserved: Literal[False] = False
    reservation_scope: Literal["WHOLE_UNRESERVED_CHILD_CLAIMS_USE_ORIGINAL_PIPELINE"] = (
        "WHOLE_UNRESERVED_CHILD_CLAIMS_USE_ORIGINAL_PIPELINE"
    )
    cross_operation_atomicity: Literal["NOT_AVAILABLE"] = "NOT_AVAILABLE"
    current_authority_assessed: Literal[False] = False
    receipt_is_current_authority: Literal[False] = False
    economic_experiment_verified: Literal[False] = False


class FullAssetExecutionLookup(BoundaryModel):
    simulation: Literal[True] = True
    user_id: UUID
    epoch_id: UUID
    idempotency_key: str
    status: Literal["RECORDED", "NOT_FOUND_NOT_FINAL"]
    command_kind: Literal["PREPARE", "CONFIRM"] | None
    original_request: FullAssetPrepareRequest | FullAssetConfirmRequest | None
    request_hash: Hash | None
    original: FullAssetExecutionResponse | None
    not_found_is_final: Literal[False] = False
    replacement_allowed: Literal[False] = False


def error(
    message: str, code: str = "FULL_ASSET_EXECUTION_INTEGRITY", status: int = 409
) -> PolicyLifecycleError:
    return PolicyLifecycleError(code, message, status)


def read_portfolio_original(
    session: Session,
    user_id: UUID,
    portfolio_id: UUID,
    now: datetime,
) -> tuple[
    FullAssetExecutionPortfolio, FullAssetFrozenPortfolio, list[FullAssetExecutionBatch], AuditEpoch
]:
    parent = session.get(FullAssetExecutionPortfolio, portfolio_id)
    if parent is None or parent.user_id != user_id:
        raise error("原组合不存在", "NOT_FOUND", 404)
    try:
        portfolio = FullAssetFrozenPortfolio.model_validate_json(json.dumps(parent.portfolio))
    except (ValueError, TypeError) as cause:
        raise error("完整原组合合同或hash不一致") from cause
    epoch = session.get(AuditEpoch, parent.epoch_id)
    if (
        epoch is None
        or epoch.user_id != user_id
        or epoch.status not in {"OPEN", "SEALED"}
        or portfolio.user_id != user_id
        or portfolio.portfolio_id != parent.id
        or portfolio.epoch_id != parent.epoch_id
        or portfolio.prepared_at != parent.created_at
        or parent.created_at > now
        or portfolio.expires_at != parent.expires_at
        or portfolio.portfolio_hash != parent.portfolio_hash
        or parent.idempotency_key != portfolio.original_request.idempotency_key
        or parent.request != portfolio.original_request.model_dump(mode="json")
        or configuration_hash(parent.request) != parent.request_hash
        or parent.request_hash != portfolio.client_request_hash
    ):
        raise error("组合原用户/epoch/原请求与完整原件不同")
    batches = list(
        session.scalars(
            select(FullAssetExecutionBatch)
            .where(
                FullAssetExecutionBatch.portfolio_id == parent.id,
            )
            .order_by(FullAssetExecutionBatch.batch_number)
        )
    )
    if len(batches) != len(portfolio.batches):
        raise error("原批次完整分母已变化")
    for row, batch in zip(batches, portfolio.batches, strict=True):
        if (
            row.user_id != user_id
            or row.epoch_id != parent.epoch_id
            or row.created_at != parent.created_at
            or row.batch_number != batch.batch_number
            or row.action_plan_id != batch.action_id
            or row.bank_idempotency_key != batch.bank_idempotency_key
            or row.command != batch.command.model_dump(mode="json")
            or row.command_hash != configuration_hash(row.command)
            or row.catalogue_version_id != batch.catalogue.catalogue_version_id
            or row.product_record_hash != batch.catalogue.product_record_hash
        ):
            raise error("原批次身份/目录/完整command/hash不一致")
    return parent, portfolio, batches, epoch


def read_consent_original(
    session: Session,
    parent: FullAssetExecutionPortfolio,
    portfolio: FullAssetFrozenPortfolio,
    now: datetime,
    *,
    current: bool,
) -> FullAssetExecutionConsent | None:
    rows = list(
        session.scalars(
            select(FullAssetExecutionConsent).where(
                FullAssetExecutionConsent.portfolio_id == parent.id,
            )
        )
    )
    if not rows:
        return None
    if len(rows) != 1:
        raise error("原组合确认分母冲突")
    row = rows[0]
    body = FullAssetConfirmRequest.model_validate(row.request)
    original = row.original_evidence
    content = original.get("content")
    if (
        row.user_id != parent.user_id
        or row.epoch_id != parent.epoch_id
        or body.expected_epoch_id != parent.epoch_id
        or row.created_at < parent.created_at
        or row.created_at > now
        or body.idempotency_key != row.idempotency_key
        or row.request_hash != configuration_hash(row.request)
        or row.portfolio_hash != parent.portfolio_hash
        or body.reviewed_portfolio_hash != parent.portfolio_hash
        or original.get("id") != str(row.evidence_id)
        or original.get("user_id") != str(parent.user_id)
        or original.get("content_hash") != row.evidence_hash
        or original.get("evidence_level") != "USER_CONFIRMED_ACTION"
        or original.get("source_type") != "USER_FULL_ASSET_PORTFOLIO_CONFIRMATION"
        or original.get("source_ref") != str(parent.id)
        or not isinstance(content, dict)
        or configuration_hash(content) != row.evidence_hash
        or content.get("portfolio_id") != str(parent.id)
        or content.get("portfolio_hash") != parent.portfolio_hash
        or content.get("epoch_id") != str(parent.epoch_id)
        or content.get("accepted") is not True
        or content.get("client_request_hash") != portfolio.client_request_hash
        or content.get("protocol") != "full-asset-portfolio-consent-v1"
        or content.get("simulation") is not True
        or content.get("user_id") != str(parent.user_id)
    ):
        raise error("原整体确认、原actor证据或原hash不同")
    if current:
        proof = session.get(EvidenceItem, row.evidence_id)
        if (
            proof is None
            or row_copy(proof) != original
            or proof.status != "VALID"
            or proof.source_type != "USER_FULL_ASSET_PORTFOLIO_CONFIRMATION"
            or proof.evidence_level != "USER_CONFIRMED_ACTION"
            or proof.source_ref != str(parent.id)
            or not proof.valid_from <= now < portfolio.expires_at
            or proof.valid_to != portfolio.expires_at
        ):
            raise error("当前整体原确认缺失或失效")
    return row


def batch_binding(portfolio: FullAssetFrozenPortfolio, number: int) -> FullAssetBatchBinding:
    batch = portfolio.batches[number - 1]
    return FullAssetBatchBinding(
        portfolio_id=portfolio.portfolio_id,
        portfolio_hash=portfolio.portfolio_hash,
        epoch_id=portfolio.epoch_id,
        batch_number=number,
        client_request_hash=portfolio.client_request_hash,
        command_hash=configuration_hash(batch.command.model_dump(mode="json")),
    )


def verify_original_batch_action(
    session: Session,
    portfolio: FullAssetFrozenPortfolio,
    number: int,
    action: ActionPlan,
    now: datetime,
) -> ActionResponse:
    batch = portfolio.batches[number - 1]
    binding = batch_binding(portfolio, number)
    if (
        action.id != batch.action_id
        or action.user_id != portfolio.user_id
        or action.idempotency_key != batch.bank_idempotency_key
        or configuration_hash(action.request) != action.request_hash
        or action.request.get("full_asset_execution") != binding.model_dump(mode="json")
        or action.request.get("execution") != batch.command.model_dump(mode="json")
        or action.autonomy_level != "ASK_ONCE"
        or action.created_at != portfolio.prepared_at
    ):
        raise error("原组合子动作marker/原经济command或身份已变化")
    result = get_action(session, portfolio.user_id, action.id, now)
    trace = get_decision_trace(session, portfolio.user_id, action.decision_run_id, now)
    if (
        trace.completeness != "COMPLETE"
        or trace.trace is None
        or trace.trace.inputs.get("action_request") != action.request
        or trace.audit_chain_status != "VALID"
    ):
        raise error("原子动作完整请求与原typed轨迹或审计不同")
    return result


def read_full_asset_execution(
    session: Session,
    user_id: UUID,
    portfolio_id: UUID,
    now: datetime,
) -> FullAssetExecutionResponse:
    _read_snapshot(session)
    now = _now(now)
    # A portfolio read verifies several originals in this one session. The
    # existing scope reuses only clean immutable RR/read-only audit proofs and
    # rechecks every retained row; write/dirty sessions still fully verify.
    with audit_read_scope(session):
        return _read_full_asset_execution(session, user_id, portfolio_id, now)


def _read_full_asset_execution(
    session: Session,
    user_id: UUID,
    portfolio_id: UUID,
    now: datetime,
) -> FullAssetExecutionResponse:
    parent, portfolio, _, epoch = read_portfolio_original(session, user_id, portfolio_id, now)
    consent = read_consent_original(session, parent, portfolio, now, current=False)
    consent_proof = session.get(EvidenceItem, consent.evidence_id) if consent else None
    if consent is not None and (
        (consent_proof is None and epoch.status == "OPEN")
        or (consent_proof is not None and row_copy(consent_proof) != consent.original_evidence)
    ):
        raise error("当前原整体确认证据缺失或与保留原件不同")
    views: list[FullAssetBatchView] = []
    for batch in portfolio.batches:
        action = session.get(ActionPlan, batch.action_id)
        if action is None and epoch.status == "OPEN":
            raise error("当前OPEN原组合子动作缺失")
        result = (
            verify_original_batch_action(session, portfolio, batch.batch_number, action, now)
            if action
            else None
        )
        views.append(
            FullAssetBatchView(
                batch_number=batch.batch_number,
                action_id=batch.action_id,
                bank_idempotency_key=batch.bank_idempotency_key,
                original_action=result,
                original_request_hash=action.request_hash if action else None,
                original_trace_verified=result is not None,
                current_action_missing=action is None,
            )
        )
    all_receipts = all(
        v.original_action is not None
        and v.original_action.receipt is not None
        and v.original_action.status in {"SUCCEEDED", "RECONCILED"}
        and v.original_action.bank_status == "SETTLED"
        for v in views
    )
    unresolved = any(
        v.original_action is not None
        and (
            v.original_action.status in {"SUBMITTED", "UNKNOWN"}
            or (v.original_action.bank_status is not None and v.original_action.receipt is None)
        )
        for v in views
    )
    settled = any(
        v.original_action is not None and v.original_action.receipt is not None for v in views
    )
    stopped = any(
        v.original_action is not None
        and v.original_action.status in {"FAILED", "INVALIDATED", "CANCELLED"}
        for v in views
    )
    state: Literal[
        "PREPARED_UNRESERVED",
        "CONFIRMED_UNRESERVED",
        "PARTIALLY_SETTLED",
        "SERVICE_RECEIPTS_VERIFIED",
        "UNRESOLVED",
        "STOPPED",
        "RETAINED_HISTORY",
    ] = (
        "RETAINED_HISTORY"
        if epoch.status != "OPEN"
        else "SERVICE_RECEIPTS_VERIFIED"
        if all_receipts
        else "UNRESOLVED"
        if unresolved
        else "STOPPED"
        if stopped
        else "PARTIALLY_SETTLED"
        if settled
        else "CONFIRMED_UNRESERVED"
        if consent
        else "PREPARED_UNRESERVED"
    )
    return FullAssetExecutionResponse(
        user_id=user_id,
        epoch_id=epoch.id,
        as_of=now,
        original_portfolio=portfolio,
        original_request_hash=parent.request_hash,
        state=state,
        original_consent_id=consent.id if consent else None,
        original_consent_evidence_id=consent.evidence_id if consent else None,
        original_consent=(
            FullAssetConsentView(
                consent_id=consent.id,
                user_id=consent.user_id,
                epoch_id=consent.epoch_id,
                portfolio_id=consent.portfolio_id,
                idempotency_key=consent.idempotency_key,
                original_request=FullAssetConfirmRequest.model_validate(consent.request),
                request_hash=consent.request_hash,
                portfolio_hash=consent.portfolio_hash,
                evidence_id=consent.evidence_id,
                evidence_hash=consent.evidence_hash,
                original_evidence=consent.original_evidence,
                current_evidence_status=(
                    "CURRENT_EVIDENCE_MATCHED"
                    if consent_proof is not None
                    else "RETAINED_ORIGINAL_CURRENT_EVIDENCE_MISSING"
                ),
                current_evidence_verified=consent_proof is not None,
            )
            if consent
            else None
        ),
        original_consent_verified=consent_proof is not None,
        original_consent_evidence_status=(
            "NOT_RECORDED"
            if consent is None
            else "CURRENT_EVIDENCE_MATCHED"
            if consent_proof is not None
            else "RETAINED_ORIGINAL_CURRENT_EVIDENCE_MISSING"
        ),
        current_epoch_open=epoch.status == "OPEN",
        batches=views,
        all_original_service_receipts_verified=all_receipts,
    )


def read_full_asset_execution_by_key(
    session: Session,
    user_id: UUID,
    epoch_id: UUID,
    key: str,
    now: datetime,
) -> FullAssetExecutionLookup:
    _read_snapshot(session)
    if not key.strip() or len(key) > 160:
        raise error("需要完整原组合键", "INVALID_KEY", 422)
    parents = list(
        session.scalars(
            select(FullAssetExecutionPortfolio).where(
                FullAssetExecutionPortfolio.user_id == user_id,
                FullAssetExecutionPortfolio.epoch_id == epoch_id,
                FullAssetExecutionPortfolio.idempotency_key == key,
            )
        )
    )
    consents = list(
        session.scalars(
            select(FullAssetExecutionConsent).where(
                FullAssetExecutionConsent.user_id == user_id,
                FullAssetExecutionConsent.epoch_id == epoch_id,
                FullAssetExecutionConsent.idempotency_key == key,
            )
        )
    )
    if len(parents) + len(consents) > 1:
        raise error("原键存在跨命令歧义，不能猜测原提交", "AMBIGUOUS_ORIGINAL_COMMAND")
    parent = parents[0] if parents else None
    consent = consents[0] if consents else None
    original = None
    if parent:
        original = read_full_asset_execution(session, user_id, parent.id, now)
    elif consent:
        original = read_full_asset_execution(session, user_id, consent.portfolio_id, now)
    if consent and (original is None or original.original_consent_id != consent.id):
        raise error("原确认键与完整原组合确认不一致")
    return FullAssetExecutionLookup(
        user_id=user_id,
        epoch_id=epoch_id,
        idempotency_key=key,
        status="RECORDED" if parent or consent else "NOT_FOUND_NOT_FINAL",
        command_kind="PREPARE" if parent else "CONFIRM" if consent else None,
        original_request=(
            FullAssetPrepareRequest.model_validate(parent.request)
            if parent
            else FullAssetConfirmRequest.model_validate(consent.request)
            if consent
            else None
        ),
        request_hash=parent.request_hash if parent else consent.request_hash if consent else None,
        original=original,
    )


def select_original_batch(
    original: FullAssetExecutionResponse, body: FullAssetExecuteRequest
) -> FullAssetBatchView:
    """Select the requested original identity; a replay never selects a later batch."""
    matches = [b for b in original.batches if b.batch_number == body.expected_batch_number]
    if len(matches) != 1 or matches[0].action_id != body.expected_action_id:
        raise error("执行必须绑定同一原批序/action", "FULL_ASSET_BATCH_IDENTITY_MISMATCH")
    chosen = matches[0]
    if chosen.original_action is None:
        raise error("原批次当前动作缺失")
    for previous in original.batches:
        if previous.batch_number < chosen.batch_number and (
            previous.original_action is None
            or previous.original_action.receipt is None
            or previous.original_action.status not in {"SUCCEEDED", "RECONCILED"}
            or previous.original_action.bank_status != "SETTLED"
        ):
            raise error("先前原批次未决，不能越序执行", "FULL_ASSET_PREVIOUS_BATCH_UNRESOLVED")
    return chosen
