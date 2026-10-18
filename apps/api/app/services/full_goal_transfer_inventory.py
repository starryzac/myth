"""Current native original allocation universe; each historical source stays immutable."""

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from app.db.models import (
    ActionPlan,
    ActionReceipt,
    BankOperation,
    EvidenceItem,
    SimulatedBankPosting,
)
from app.domain.boundary_types import BoundaryModel
from app.domain.execution_types import BankCommand
from app.domain.full_goal_release_execution import (
    GoalReleasePostingOriginal,
    GoalReleaseSettlementOriginal,
)
from app.domain.full_goal_transfer import GoalTransferCurrentScope, GoalTransferSettlementOriginal
from app.domain.full_goal_transfer_current_proof import (
    GoalTransferCurrentInput,
    GoalTransferCurrentProof,
    GoalTransferHistoricalSource,
    make_current_goal_transfer_proof,
    replay_current_goal_transfer,
)
from app.domain.full_goal_transfer_provenance import GoalTransferOwnershipResult
from app.domain.goal_release_provenance import (
    AllocationOriginal,
    GoalCashSourceProof,
    OriginalRowReference,
)
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import current_audit_epoch, row_copy
from app.services.full_goal_release_reader import (
    read_goal_release_bank_command,
    read_goal_release_command,
    verify_goal_release_legs,
    verify_goal_release_receipt,
)
from app.services.full_goal_transfer_reader import (
    read_goal_transfer_bank_original,
    read_goal_transfer_command,
    verify_goal_transfer_receipt,
)
from app.services.full_reconciliation import full_reconciliation
from app.services.income_ledger import read_income_state
from app.services.policy_lifecycle import PolicyLifecycleError
from sqlalchemy import func, select
from sqlalchemy.orm import Session


class GoalTransferInventory(BoundaryModel):
    bases: list[GoalCashSourceProof]
    current_proof: GoalTransferCurrentProof
    ownership: GoalTransferOwnershipResult
    releases: list[GoalReleaseSettlementOriginal]
    transfers: list[GoalTransferSettlementOriginal]
    reconciliation_input_hash: str
    target_ownership_hash: str
    input_hash: str


def _error(message: str) -> PolicyLifecycleError:
    return PolicyLifecycleError("GOAL_TRANSFER_CURRENT_SOURCE_NOT_VERIFIED", message, 409)


def _ref(row: Any) -> OriginalRowReference:
    return OriginalRowReference(
        table=row.__tablename__, row_id=row.id, row_hash=configuration_hash(row_copy(row))
    )


def _release_original(
    session: Session, bank: BankOperation, now: datetime
) -> GoalReleaseSettlementOriginal:
    command = read_goal_release_bank_command(session, bank, now)
    rows = verify_goal_release_legs(session, bank, now) if bank.status == "SETTLED" else []
    payload = {
        "command": command.model_dump(mode="json"),
        "bank_operation_id": str(bank.id),
        "action_plan_id": str(bank.action_plan_id),
        "user_id": str(bank.user_id),
        "request_hash": bank.request_hash,
        "business_key": bank.business_key,
        "idempotency_key": bank.idempotency_key,
        "status": bank.status,
        "requested_at": bank.requested_at.isoformat(),
        "available_at": bank.available_at.isoformat(),
        "settled_at": bank.settled_at.isoformat() if bank.settled_at else None,
        "postings": [
            {
                **{
                    key: value
                    for key, value in row_copy(row).items()
                    if key in GoalReleasePostingOriginal.model_fields
                },
                "posting_id": str(row.id),
                "original_row_hash": configuration_hash(row_copy(row)),
            }
            for row in rows
        ],
        "original_bank_row_hash": configuration_hash(row_copy(bank)),
    }
    return GoalReleaseSettlementOriginal.model_validate_json(json.dumps(payload))


def read_goal_transfer_inventory(
    session: Session,
    user_id: UUID,
    source_goal_id: UUID,
    target_goal_id: UUID,
    expected_epoch_id: UUID,
    now: datetime,
    *,
    retained_bases: list[GoalCashSourceProof] | None = None,
) -> GoalTransferInventory:
    """retained_bases is compatibility input only; old bytes never become current facts."""
    epoch = current_audit_epoch(session, user_id)
    if epoch is None or epoch.status != "OPEN" or epoch.id != expected_epoch_id:
        raise _error("当前来源证明需要本人开放周期")
    report = full_reconciliation(session, user_id, now)
    if report.state != "MATCHED" or report.issues or report.audit.status != "VALID":
        raise _error("当前完整银行、投影或审计未核实，不能刷新来源")
    captured: dict[str, list[Any]] = {}
    models: tuple[Any, ...] = (ActionPlan, ActionReceipt, BankOperation, SimulatedBankPosting)
    for model in models:
        maximum = 100000 if model is SimulatedBankPosting else 10000
        count = session.scalar(
            select(func.count()).select_from(model).where(model.user_id == user_id)
        )
        rows: list[Any] = list(
            session.scalars(
                select(model).where(model.user_id == user_id).order_by(model.id).limit(maximum)
            )
        )
        if type(count) is not int or count != len(rows):
            raise _error("真实原件分母超出有限读取范围，不得截断")
        captured[model.__tablename__] = rows
    actions = {row.id: row for row in captured[ActionPlan.__tablename__]}
    receipts = {row.id: row for row in captured[ActionReceipt.__tablename__]}
    reconciled = {row.action_id: row for row in report.actions}
    bases_by_hash: dict[tuple[UUID, UUID, UUID, str], dict[str, Any]] = {}
    for action in actions.values():
        if action.action_type == "RELEASE_GOAL" or "goal_release_execution" in action.request:
            command = read_goal_release_command(action)
            base = GoalCashSourceProof.model_validate_json(
                json.dumps(action.request["goal_release_source_basis"])
            )
            key = user_id, base.epoch_id, base.goal_id, base.source_binding_hash
            content = base.model_dump(mode="json")
            if command.effect.source_provenance_hash != base.source_binding_hash:
                raise _error("旧现金动作原source hash已变化")
            if key in bases_by_hash and bases_by_hash[key] != content:
                raise _error("同历史basis hash对应不同原件")
            bases_by_hash[key] = content
        elif action.action_type == "TRANSFER_GOAL" or "goal_transfer_execution" in action.request:
            transfer = read_goal_transfer_command(action)
            if action.request.get("protocol") == "goal-transfer-action-v1":
                for raw in action.request["goal_transfer_source_bases"]:
                    base = GoalCashSourceProof.model_validate_json(json.dumps(raw))
                    key = user_id, base.epoch_id, base.goal_id, base.source_binding_hash
                    content = base.model_dump(mode="json")
                    if key in bases_by_hash and bases_by_hash[key] != content:
                        raise _error("历史来源hash碰撞或原件已改")
                    bases_by_hash[key] = content
            else:
                proof = GoalTransferCurrentProof.model_validate_json(
                    json.dumps(action.request["goal_transfer_current_proof"])
                )
                key = user_id, proof.epoch_id, proof.source_goal_id, proof.proof_hash
                content = proof.model_dump(mode="json")
                if (
                    proof.proof_hash != transfer.effect.source_provenance_hash
                    or key in bases_by_hash
                    and bases_by_hash[key] != content
                ):
                    raise _error("旧current proof必须按其原hash读取，不能换成今日证明")
                bases_by_hash[key] = content
    # Caller-retained objects cannot override the actual immutable historical index.
    for base in retained_bases or []:
        key = base.user_id, base.epoch_id, base.goal_id, base.source_binding_hash
        if key in bases_by_hash and bases_by_hash[key] != base.model_dump(mode="json"):
            raise _error("客户端或旧调用保留basis与真实原件不同")
    originals: list[AllocationOriginal] = []
    releases: list[GoalReleaseSettlementOriginal] = []
    transfers: list[GoalTransferSettlementOriginal] = []
    history: list[GoalTransferHistoricalSource] = []
    for bank in captured[BankOperation.__tablename__]:
        action, actual = actions.get(bank.action_plan_id), reconciled.get(bank.action_plan_id)
        if action is None or actual is None:
            raise _error("原银行必须有本人原action与完整核对记录")
        refs = [_ref(action), _ref(bank)]
        for identity in actual.receipt_ids:
            receipt = receipts.get(identity)
            if receipt is None:
                raise _error("原receipt分母缺失")
            refs.append(_ref(receipt))
        native = None
        if bank.operation_type in {"RELEASE_GOAL", "TRANSFER_GOAL"}:
            if bank.status != "SETTLED" or len(actual.receipt_ids) != 1:
                raise _error("未决、缺receipt或非settled专用银行不能由刷新抹除")
            receipt = receipts[actual.receipt_ids[0]]
            if bank.operation_type == "RELEASE_GOAL":
                release = _release_original(session, bank, now)
                verify_goal_release_receipt(session, bank, receipt, now)
                releases.append(release)
                effect = release.command.effect
                history.append(
                    GoalTransferHistoricalSource(
                        operation_id=bank.id,
                        source_goal_id=effect.source_goal_id,
                        original_action_request_hash=action.request_hash,
                        source_provenance_hash=effect.source_provenance_hash,
                        source_protocol="PROTECTED_CASH_V1",
                    )
                )
            else:
                transfer_original = read_goal_transfer_bank_original(session, bank, now)
                verify_goal_transfer_receipt(session, bank, receipt, now)
                transfers.append(transfer_original)
                transfer_effect = transfer_original.command.effect
                history.append(
                    GoalTransferHistoricalSource(
                        operation_id=bank.id,
                        source_goal_id=transfer_effect.source_goal_id,
                        original_action_request_hash=action.request_hash,
                        source_provenance_hash=transfer_effect.source_provenance_hash,
                        source_protocol="GOAL_TRANSFER_CURRENT_V1"
                        if isinstance(transfer_effect.scope, GoalTransferCurrentScope)
                        else "GOAL_TRANSFER_BASIS_V1",
                    )
                )
        elif bank.legacy_redemption_id is None:
            try:
                native = BankCommand.model_validate_json(json.dumps(bank.request))
            except (ValueError, TypeError) as error:
                raise _error("当前普通银行原command无有效native解释") from error
        if bank.operation_type in {"RELEASE_GOAL", "TRANSFER_GOAL"}:
            continue
        originals.append(
            AllocationOriginal(
                command=native,
                action_id=action.id,
                action_goal_id=action.goal_id,
                action_type=action.action_type,
                action_request=action.request,
                action_request_hash=action.request_hash,
                bank_id=bank.id,
                bank_request=bank.request,
                bank_request_hash=bank.request_hash,
                bank_idempotency_key=bank.idempotency_key,
                legacy_redemption_id=bank.legacy_redemption_id,
                original_refs=refs,
            )
        )
    state = read_income_state(session, user_id, now)
    income_evidence = session.get(EvidenceItem, state.evidence_id)
    if income_evidence is None:
        raise _error("当前原income evidence缺失")
    try:
        proof = make_current_goal_transfer_proof(
            GoalTransferCurrentInput(
                user_id=user_id,
                epoch_id=expected_epoch_id,
                as_of=now,
                source_goal_id=source_goal_id,
                target_goal_id=target_goal_id,
                current_income=state.ledger,
                income_evidence_id=state.evidence_id,
                income_evidence_hash=state.evidence_hash,
                income_evidence_row=row_copy(income_evidence),
                reconciliation=report,
                originals=originals,
                releases=releases,
                transfers=transfers,
                historical_sources=history,
                original_row_refs=[_ref(row) for rows in captured.values() for row in rows],
            )
        )
        ownership = replay_current_goal_transfer(proof)
    except (ValueError, KeyError, TypeError) as error:
        raise _error("当前原allocation/收入/全部历史消费无法完整解释") from error
    target = next((row for row in report.goal_ownership if row.goal_id == target_goal_id), None)
    if target is None or target.ownership_evidence_hash is None:
        raise _error("当前接收目标归属摘要缺失")
    return GoalTransferInventory(
        bases=[],
        current_proof=proof,
        ownership=ownership,
        releases=releases,
        transfers=transfers,
        reconciliation_input_hash=report.input_hash,
        target_ownership_hash=target.ownership_evidence_hash,
        input_hash=proof.proof_hash,
    )
