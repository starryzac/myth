"""Read original settled maturity receipts, then replan actual current scope in RR/RO."""

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from app.db.models import (
    ActionPlan,
    ActionReceipt,
    AssetPosition,
    BankOperation,
    Policy,
    PolicyVersion,
    SimulatedBankRedemption,
)
from app.domain.asset_allocation_types import AssetAllocationResult
from app.domain.boundary_types import BoundaryModel
from app.domain.full_asset_allocation import FullAssetPlanningResult
from app.domain.full_maturity_replanning import (
    CurrentMaturityPolicy,
    Digest,
    MaturityDecision,
    MaturityReplanningRequest,
    OriginalMaturityEvent,
    decide_maturity_replanning,
)
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.services.asset_allocation import preview_asset_allocation
from app.services.asset_exposure_import import _scope
from app.services.audit_chain import row_copy
from app.services.boundary import BoundarySourceIssue
from app.services.dashboard_helpers import current_epoch_audit
from app.services.dashboard_types import DashboardAuditCard
from app.services.financial_read import finalize_financial_context, load_verified_financial_context
from app.services.full_asset_allocation import read_full_asset_allocation
from app.services.full_policy_lifecycle import _read_snapshot, read_full_policy
from app.services.historical_read import historical_ledger_scope
from app.services.policy_lifecycle import PolicyLifecycleError, _now, is_version_authorized
from app.services.product_catalog import VerifiedCatalogProducts, verified_catalog_products
from app.services.recovery_receipt_integrity import verify_recovery_receipt
from app.services.simulated_bank import BankRequest
from sqlalchemy import select
from sqlalchemy.orm import Session

ENGINE_FILES = (
    "domain/full_maturity_replanning.py",
    "services/full_maturity_replanning.py",
    "api/v1/full_maturity_replanning.py",
    "domain/asset_allocation.py",
    "domain/full_asset_allocation.py",
    "services/asset_allocation.py",
    "services/full_asset_allocation.py",
    "services/product_catalog.py",
    "services/financial_read.py",
    "services/recovery_receipt_integrity.py",
    "services/policy_lifecycle.py",
    "services/full_policy_lifecycle.py",
)


class MaturityReplanningResponse(BoundaryModel):
    schema_version: Literal["verified-current-maturity-replanning-v1"] = (
        "verified-current-maturity-replanning-v1"
    )
    simulation: Literal[True] = True
    read_only: Literal[True] = True
    bank_authority: Literal[False] = False
    executes_funds: Literal[False] = False
    writes_facts: Literal[False] = False
    dedicated_decision_recorded: Literal[False] = False
    current_prepare_consumes_reviewed_decision_hash: Literal[False] = False
    user_id: UUID
    epoch_id: UUID
    as_of: datetime
    original_request: MaturityReplanningRequest
    event: OriginalMaturityEvent
    current_policy: CurrentMaturityPolicy
    decision: MaturityDecision
    legacy_allocation: AssetAllocationResult | None
    full_allocation: FullAssetPlanningResult | None
    catalogue: VerifiedCatalogProducts | None
    audit: DashboardAuditCard
    source_evidence_ids: list[UUID]
    source_issues: list[BoundarySourceIssue]
    engine_hash: Digest
    engine_files: dict[str, str]
    source_hash: Digest
    limitations: list[str]


def engine_sources() -> tuple[str, dict[str, str]]:
    root = Path(__file__).resolve().parents[1]
    files = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in ENGINE_FILES}
    return configuration_hash(files), files


def read_original_maturity(
    session: Session, user_id: UUID, action_id: UUID, now: datetime
) -> OriginalMaturityEvent:
    action = session.get(ActionPlan, action_id)
    if action is None or action.user_id != user_id:
        raise PolicyLifecycleError("NOT_FOUND", "当前用户原到期动作不存在", 404)
    if action.action_type != "ASSET_MATURITY" or action.position_id is None:
        raise PolicyLifecycleError("NOT_ORIGINAL_MATURITY", "只能读取原合同到期返还动作", 422)
    banks = list(
        session.scalars(
            select(SimulatedBankRedemption).where(
                SimulatedBankRedemption.action_plan_id == action.id
            )
        )
    )
    receipts = list(
        session.scalars(select(ActionReceipt).where(ActionReceipt.action_plan_id == action.id))
    )
    operations = list(
        session.scalars(select(BankOperation).where(BankOperation.action_plan_id == action.id))
    )
    position = session.get(AssetPosition, action.position_id)
    bank = banks[0] if len(banks) == 1 else None
    receipt = receipts[0] if len(receipts) == 1 else None
    operation = operations[0] if len(operations) == 1 else None
    originals: dict[str, Any] = {
        "action": row_copy(action),
        "bank_requests": [row_copy(row) for row in banks],
        "receipts": [row_copy(row) for row in receipts],
        "bank_operations": [row_copy(row) for row in operations],
        "position": row_copy(position) if position is not None else None,
    }
    issues: list[str] = []
    proof: Literal["VERIFIED", "NOT_RECEIVED", "UNKNOWN", "INVALID"] = "UNKNOWN"
    destination = None
    try:
        command = BankRequest.model_validate(action.request["bank_request"])
        destination = command.destination_account_id
        if (
            command.kind != "MATURE"
            or command.user_id != user_id
            or command.position_id != action.position_id
            or command.product_id != action.product_id
            or command.goal_id != action.goal_id
            or action.request_hash != configuration_hash(action.request)
            or action.created_at > now
            or len(banks) > 1
            or len(receipts) > 1
            or len(operations) > 1
            or (bank is not None and operation is not None and bank.id != operation.id)
        ):
            raise ValueError("The exact original maturity action is inconsistent")
        if bank is not None and bank.status == "SETTLED" and receipt is not None:
            if position is None or position.status != "REDEEMED":
                raise ValueError("Original returned principal projection is not complete")
            verify_recovery_receipt(session, bank, receipt, now)
            proof = "VERIFIED"
        elif operation is not None and operation.status == "SETTLED":
            issues.append("BANK_MAY_HAVE_SETTLED_BUT_APPLICATION_RECEIPT_UNRESOLVED")
        elif bank is None or bank.status in {"PENDING", "REJECTED"}:
            if receipt is not None or action.status in {"SUCCEEDED", "RECONCILED"}:
                raise ValueError("Application success has no verified settled bank original")
            proof = "NOT_RECEIVED" if action.status != "UNKNOWN" else "UNKNOWN"
            issues.append("ORIGINAL_MATURITY_NOT_SETTLED_AND_RECONCILED")
        else:
            issues.append("BANK_MAY_HAVE_SETTLED_BUT_APPLICATION_RECEIPT_UNRESOLVED")
    except (KeyError, TypeError, ValueError, PolicyLifecycleError) as error:
        proof = "INVALID"
        issues.append(
            error.code if isinstance(error, PolicyLifecycleError) else "INVALID_MATURITY_ORIGINALS"
        )
    if action.product_id is None:
        raise PolicyLifecycleError("INVALID_MATURITY_ORIGINALS", "原到期产品身份缺失", 409)
    return OriginalMaturityEvent(
        action_id=action.id,
        position_id=action.position_id,
        original_product_id=action.product_id,
        original_policy_version_id=action.policy_version_id,
        goal_id=action.goal_id,
        destination_account_id=destination,
        original_action_status=action.status,
        bank_operation_id=operation.id if operation else bank.id if bank else None,
        original_bank_status=operation.status if operation else bank.status if bank else None,
        original_receipt_id=receipt.id if receipt else None,
        principal_cents=action.amount_cents,
        settled_at=bank.settled_at if bank else None,
        proof_status=proof,
        service_receipt_verified=proof == "VERIFIED",
        originals_hash=configuration_hash(originals),
        originals=originals,
        issues=issues,
    )


def current_asset_policy(
    session: Session, user_id: UUID, policy_id: UUID, now: datetime
) -> CurrentMaturityPolicy:
    legacy = session.get(Policy, policy_id)
    if legacy is None:
        full = read_full_policy(session, user_id, policy_id, now)
        if full.template_name != "AssetAuthorizationPolicy":
            raise PolicyLifecycleError("INVALID_ASSET_POLICY", "需要当前资产配置策略", 422)
        v = full.current_version
        config = v.configuration
        return CurrentMaturityPolicy(
            policy_id=full.policy_id,
            version_id=v.version_id,
            kind="FULL",
            configuration_hash=v.content_hash,
            scope=config["scope"],
            goal_id=UUID(config["goal_id"]) if config.get("goal_id") else None,
            effective_status=full.effective_status,
            current_confirmation_verified=full.planning_confirmation_valid,
            current_bank_authority_verified=False,
            original_configuration=config,
        )
    if legacy.user_id != user_id or legacy.policy_type != "asset_authorization":
        raise PolicyLifecycleError("NOT_FOUND", "当前用户资产配置策略不存在", 404)
    version = session.scalar(
        select(PolicyVersion)
        .where(PolicyVersion.user_id == user_id, PolicyVersion.policy_id == policy_id)
        .order_by(PolicyVersion.version_number.desc())
        .limit(1)
    )
    if version is None or version.created_at > now:
        raise PolicyLifecycleError("CURRENT_POLICY_NOT_KNOWN", "当前原资产版本缺失或尚未知悉", 409)
    try:
        config = validate_configuration(version.configuration)
    except (TypeError, ValueError) as error:
        raise PolicyLifecycleError("INVALID_ASSET_POLICY", "当前资产原配置不一致", 409) from error
    if (
        config["type"] != "asset_authorization"
        or configuration_hash(config) != version.content_hash
    ):
        raise PolicyLifecycleError("INVALID_ASSET_POLICY", "当前资产原配置不一致", 409)
    authorized = is_version_authorized(session, user_id, version.id, now)
    return CurrentMaturityPolicy(
        policy_id=policy_id,
        version_id=version.id,
        kind="MVP",
        configuration_hash=version.content_hash,
        scope=config["scope"],
        goal_id=UUID(config["goal_id"]) if config.get("goal_id") else None,
        effective_status=legacy.status,
        current_confirmation_verified=authorized,
        current_bank_authority_verified=authorized,
        original_configuration=config,
    )


def preview_maturity_replanning(
    session: Session, user_id: UUID, request: MaturityReplanningRequest, now: datetime
) -> MaturityReplanningResponse:
    _read_snapshot(session)
    now = _now(now)
    engine_hash, files = engine_sources()
    with session.no_autoflush, historical_ledger_scope(session):
        audit = current_epoch_audit(session, user_id, [])
        if audit.epoch_id != request.expected_epoch_id:
            raise PolicyLifecycleError("STALE_AUDIT_EPOCH", "当前原epoch不同，请重新读取", 409)
        event = read_original_maturity(session, user_id, request.maturity_action_id, now)
        policy = current_asset_policy(session, user_id, request.current_asset_policy_id, now)
        legacy_plan: AssetAllocationResult | None = None
        full_plan: FullAssetPlanningResult | None = None
        catalogue: VerifiedCatalogProducts | None = None
        issues: list[BoundarySourceIssue] = []
        evidence_ids: list[UUID] = []
        financial_hash: str | None = None
        ready = False
        same_scope = event.goal_id == policy.goal_id and policy.scope == (
            "goal" if event.goal_id is not None else "general_idle_funds"
        )
        if (
            event.proof_status == "VERIFIED"
            and same_scope
            and policy.current_confirmation_verified
            and audit.complete
            and audit.status == "VALID"
        ):
            if policy.kind == "FULL":
                full = read_full_asset_allocation(session, user_id, policy.policy_id, now)
                full_plan, catalogue = full.allocation, full.catalogue
                issues, evidence_ids, financial_hash = (
                    full.source_issues,
                    full.source_evidence_ids,
                    full.input_hash,
                )
                ready = full.state == "COMPUTED" and catalogue.status == "VERIFIED" and not issues
            else:
                context, matched, exposures = load_verified_financial_context(session, user_id, now)
                _scope(session, context, policy.version_id)
                context, issues, financial_hash = finalize_financial_context(context, audit)
                evidence_ids = sorted(context.sources.used)
                catalogue = verified_catalog_products(session, now)
                ready = (
                    matched
                    and exposures is not None
                    and not issues
                    and catalogue.status == "VERIFIED"
                )
                if ready:
                    legacy = preview_asset_allocation(session, user_id, policy.policy_id, now)
                    legacy_plan = legacy.allocation
                    issues.extend(legacy.source_issues)
                    evidence_ids = sorted(set(evidence_ids) | set(legacy.source_evidence_ids))
                    originals = {p.product_id: p for p in catalogue.products}
                    ready = not issues and all(
                        c.product_id in originals
                        and c.version_number == originals[c.product_id].version_number
                        and (
                            c.exit_plan is None
                            or c.exit_plan.terms_digest == originals[c.product_id].terms_digest
                        )
                        for c in legacy_plan.candidates
                    )
        source_hash = configuration_hash(
            {
                "epoch_audit": audit.model_dump(mode="json"),
                "event_hash": event.originals_hash,
                "current_policy": policy.model_dump(mode="json"),
                "financial_sources": financial_hash,
                "catalogue": catalogue.model_dump(mode="json") if catalogue else None,
                "evidence_ids": [str(i) for i in evidence_ids],
                "issues": [i.model_dump(mode="json") for i in issues],
                "engine_hash": engine_hash,
            }
        )
        decision = decide_maturity_replanning(
            epoch_id=request.expected_epoch_id,
            event=event,
            policy=policy,
            sources_verified=ready,
            source_hash=source_hash,
            legacy_plan=legacy_plan,
            full_plan=full_plan,
        )
        _read_snapshot(session)
        if engine_sources()[0] != engine_hash:
            raise PolicyLifecycleError(
                "MATURITY_ENGINE_CHANGED", "本次重算期间原引擎源发生变化", 409
            )
        return MaturityReplanningResponse(
            user_id=user_id,
            epoch_id=request.expected_epoch_id,
            as_of=now,
            original_request=request,
            event=event,
            current_policy=policy,
            decision=decision,
            legacy_allocation=legacy_plan,
            full_allocation=full_plan,
            catalogue=catalogue,
            audit=audit,
            source_evidence_ids=evidence_ids,
            source_issues=issues,
            engine_hash=engine_hash,
            engine_files=files,
            source_hash=source_hash,
            limitations=[
                "原返还回执只验证过去到期，不是当前配置或银行授权。",
                "返还本金不新增为收入、不保留独占资金标记；按当前scope全部已核财务事实重新计算。",
                "不自动沿用原产品；旧产品只有当前目录/当前策略重新选中时才可再出现。",
                "候选仅旧purchase_asset当前意图；prepare重新计算，实际金额/产品/权限须复核新effect。",
                "完整FULL规划没有银行执行消费者；本读取不记录新DecisionRun、不创建行动或新回执。",
                "同event/epoch/policy版本提交key稳定；版本变化形成新审阅，不能自动换键重发未知请求。",
            ],
        )
