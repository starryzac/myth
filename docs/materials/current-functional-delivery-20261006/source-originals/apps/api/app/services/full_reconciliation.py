"""Compare current application rows to original independent bank facts, without writes."""

import json
from collections import defaultdict
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from app.db.models import (
    Account,
    ActionPlan,
    ActionReceipt,
    AssetPosition,
    BankOperation,
    Goal,
    SimulatedBankPosting,
    SimulatedBankRedemption,
    User,
)
from app.domain.execution import ACTION_PLAN_TYPES
from app.domain.execution_types import BankCommand
from app.domain.full_reconciliation import (
    BankHeadReference,
    FullReconciliationReport,
    IssueKind,
    ReconciliationAction,
    ReconciliationAmount,
    ReconciliationGoal,
    ReconciliationInventory,
    ReconciliationIssue,
    ReconciliationPosting,
    compare_amount,
    head_reference,
    report_state,
)
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import row_copy, verify_audit_chain
from app.services.boundary import OWNERSHIP_SOURCE
from app.services.execution_projection import (
    _identity,
    _legs,
    _matches,
    _proof,
    verify_execution_receipt,
)
from app.services.full_policy_lifecycle import _read_snapshot
from app.services.historical_read import historical_ledger_scope
from app.services.policy_lifecycle import PolicyLifecycleError, _now
from app.services.recovery_receipt_integrity import verify_recovery_receipt
from app.services.simulated_bank import BankRequest, ledger_heads, validate_bank_projection
from sqlalchemy import func, select
from sqlalchemy.orm import Session

ROW_LIMIT = 10000
POSTING_LIMIT = 100000


def _issue(code: str, ref: object, message: str, kind: IssueKind) -> ReconciliationIssue:
    return ReconciliationIssue(code=code, source_ref=str(ref), message=message, kind=kind)


def _load(
    session: Session, model: Any, user_id: UUID, limit: int
) -> tuple[list[Any], ReconciliationInventory]:
    count = session.scalar(select(func.count()).select_from(model).where(model.user_id == user_id))
    if type(count) is not int:
        raise PolicyLifecycleError("INVALID_READ_SNAPSHOT", "缺少真实行数分母", 409)
    rows: list[Any] = list(
        session.scalars(
            select(model).where(model.user_id == user_id).order_by(model.id).limit(limit)
        )
    )
    return rows, ReconciliationInventory(
        table=model.__tablename__,
        actual_count=count,
        captured_count=len(rows),
        complete=count == len(rows),
    )


def _head(heads: dict[str, SimulatedBankPosting], key: str) -> BankHeadReference | None:
    row = heads.get(key)
    return head_reference(row.id, key, row.sequence_number, row.occurred_at) if row else None


def _amount(
    identity: UUID,
    kind: Literal["ACCOUNT_CASH", "POSITION_PRINCIPAL", "GOAL_CASH", "GOAL_PRINCIPAL"],
    application: int | None,
    heads: dict[str, SimulatedBankPosting],
    key: str,
    now: datetime,
    issues: list[ReconciliationIssue],
    *,
    supported: bool = True,
    goal_account_id: UUID | None = None,
) -> ReconciliationAmount:
    row = heads.get(key)
    identity_matches = row is None or (
        row.account_id == identity and row.ledger_dimension == "ECONOMIC"
        if kind == "ACCOUNT_CASH"
        else row.position_id == identity and row.ledger_dimension == "ECONOMIC"
        if kind == "POSITION_PRINCIPAL"
        else row.ledger_dimension == "GOAL_OWNERSHIP"
        and row.ledger_metadata.get("goal_id") == str(identity)
        and row.account_id == goal_account_id
        and row.ledger_metadata.get("account_id") == str(goal_account_id)
    )
    if row is not None and supported and not identity_matches:
        issues.append(
            _issue("BANK_HEAD_IDENTITY_MISMATCH", key, "账本head归属与原实体不一致", "INTEGRITY")
        )
    bank = (
        row.balance_after_cents
        if row is not None and row.occurred_at <= now and supported and identity_matches
        else None
    )
    value = compare_amount(identity, kind, application, bank, _head(heads, key))
    if value.state != "MATCHED":
        issues.append(
            _issue(
                "APPLICATION_BANK_DIFFERENCE"
                if value.state == "DIFFERENCE"
                else "BANK_VALUE_MISSING",
                key,
                "账面数值减独立银行数值存在差额"
                if value.state == "DIFFERENCE"
                else "没有本时点可核的独立银行数值，不补零",
                "DIFFERENCE" if value.state == "DIFFERENCE" else "MISSING",
            )
        )
    return value


def _posting(row: SimulatedBankPosting) -> ReconciliationPosting:
    return ReconciliationPosting(
        posting_id=row.id,
        operation_id=row.operation_id,
        redemption_id=row.redemption_id,
        ledger_key=row.ledger_key,
        ledger_dimension=row.ledger_dimension,
        leg_ref=row.leg_ref,
        sequence_number=row.sequence_number,
        balance_before_cents=row.balance_before_cents,
        delta_cents=row.delta_cents,
        balance_after_cents=row.balance_after_cents,
        occurred_at=row.occurred_at,
    )


def _action(
    session: Session,
    action: ActionPlan,
    operations: list[BankOperation],
    requests: list[SimulatedBankRedemption],
    receipts: list[ActionReceipt],
    postings: list[SimulatedBankPosting],
    now: datetime,
    *,
    inventory_complete: bool,
    ledger_verified: bool,
) -> ReconciliationAction:
    issues: list[ReconciliationIssue] = []
    result = ReconciliationAction(
        action_id=action.id,
        action_type=action.action_type,
        original_action_status=action.status,
        original_idempotency_key=action.idempotency_key,
        original_request_hash=action.request_hash,
        effect_hash=None,
        expected_amount_cents=action.amount_cents,
        expected_fee_cents=None,
        expected_loss_cents=None,
        actual_executed_cents=None,
        actual_fee_cents=None,
        actual_loss_cents=None,
        bank_operation_ids=[row.id for row in operations],
        bank_statuses=[row.status for row in operations],
        bank_request_hashes=[row.request_hash for row in operations],
        posting_ids=[row.id for row in postings],
        receipt_ids=[row.id for row in receipts],
        receipt_statuses=[row.status for row in receipts],
        complete_settlement_legs_verified=False,
        service_receipt_verified=False,
        state="UNKNOWN",
        read_original_action_path=f"/api/v1/actions/{action.id}",
        issues=[],
    )
    if not inventory_complete:
        issues.append(
            _issue("ACTION_INVENTORY_INCOMPLETE", action.id, "缺少完整原行分母", "MISSING")
        )
    if action.request_hash != configuration_hash(action.request):
        issues.append(
            _issue("ACTION_REQUEST_HASH_MISMATCH", action.id, "原行动请求hash不一致", "INTEGRITY")
        )
    if action.action_type not in {
        *ACTION_PLAN_TYPES.values(),
        "ASSET_REDEEM",
        "ASSET_MATURITY",
        "RELEASE_GOAL",
    }:
        issues.append(
            _issue(
                "ACTION_TYPE_NOT_SUPPORTED",
                action.id,
                "现有生产核验未支持该动作，不能以成功字符串补完整效果",
                "UNSUPPORTED",
            )
        )
    elif action.action_type == "RELEASE_GOAL":
        try:
            from app.services.full_goal_release_reader import read_goal_release_command

            release_command = read_goal_release_command(action)
            result = result.model_copy(
                update={
                    "effect_hash": release_command.effect_hash,
                    "expected_fee_cents": 0,
                    "expected_loss_cents": 0,
                    "read_original_action_path": f"/api/v1/goal-cash-releases/actions/{action.id}",
                }
            )
        except (PolicyLifecycleError, ValueError, TypeError, KeyError) as error:
            issues.append(
                _issue(
                    "ACTION_EFFECT_HASH_OR_IDENTITY_MISMATCH", action.id, str(error), "INTEGRITY"
                )
            )
    elif "execution" in action.request:
        try:
            original = BankCommand.model_validate_json(json.dumps(action.request["execution"]))
            effect = original.effect
            if (
                effect.user_id != action.user_id
                or effect.operation_id != action.id
                or effect.amount_cents != action.amount_cents
                or ACTION_PLAN_TYPES[effect.action_type] != action.action_type
            ):
                raise ValueError("Original action does not bind its complete command identity")
            result = result.model_copy(
                update={
                    "effect_hash": original.effect_hash,
                    "expected_fee_cents": effect.fee_cents,
                    "expected_loss_cents": effect.loss_cents,
                }
            )
        except (ValueError, TypeError, KeyError) as error:
            issues.append(
                _issue(
                    "ACTION_EFFECT_HASH_OR_IDENTITY_MISMATCH", action.id, str(error), "INTEGRITY"
                )
            )
    if len(operations) > 1 or len(requests) > 1 or len(receipts) > 1:
        issues.append(
            _issue(
                "DUPLICATE_ACTION_EFFECT_OR_RECEIPT",
                action.id,
                "同一原行动存在重复效果或回执",
                "INTEGRITY",
            )
        )
    if any(issue.kind == "INTEGRITY" for issue in issues):
        return result.model_copy(update={"state": "MANUAL_REVIEW_REQUIRED", "issues": issues})
    if any(issue.kind == "UNSUPPORTED" for issue in issues):
        return result.model_copy(update={"state": "UNKNOWN", "issues": issues})
    if not inventory_complete:
        return result.model_copy(update={"issues": issues})
    if not operations:
        if requests or postings or receipts or action.status in {"SUCCEEDED", "RECONCILED"}:
            issues.append(
                _issue(
                    "ACTION_BANK_LINK_MISSING",
                    action.id,
                    "原结果或副作用没有完整银行操作引用",
                    "INTEGRITY",
                )
            )
        elif action.status in {"PLANNED", "AUTHORIZED"}:
            result = result.model_copy(update={"state": "PREPARED_NO_BANK_OBSERVED"})
        else:
            result = result.model_copy(update={"state": "NO_EFFECT_OBSERVED_NOT_FINAL"})
            issues.append(
                _issue(
                    "BANK_ABSENCE_NOT_FINAL",
                    action.id,
                    "当前未见原键银行结果不能视作终败或盲重试",
                    "MISSING",
                )
            )
    elif len(operations) == 1:
        operation = operations[0]
        try:
            if operation.legacy_redemption_id is not None:
                if len(requests) != 1 or requests[0].id != operation.legacy_redemption_id:
                    raise ValueError("Legacy operation lost its exact original request")
                request = requests[0]
                command = BankRequest.model_validate(request.request)
                if command.user_id != action.user_id or request.request_hash != configuration_hash(
                    request.request
                ):
                    raise ValueError("Legacy bank request owner or hash changed")
                if operation.status == "SETTLED":
                    if len(receipts) != 1:
                        issues.append(
                            _issue(
                                "LEGACY_SETTLED_RECEIPT_MISSING",
                                action.id,
                                "原恢复银行已结算但完整历史回执未核，不能补造两腿成功",
                                "MISSING",
                            )
                        )
                        result = result.model_copy(
                            update={"state": "BANK_SETTLED_APPLICATION_UNRESOLVED"}
                        )
                    elif ledger_verified:
                        verify_recovery_receipt(session, request, receipts[0], now)
                        result = result.model_copy(
                            update={
                                "state": "SERVICE_RECEIPT_VERIFIED",
                                "complete_settlement_legs_verified": True,
                                "service_receipt_verified": True,
                                "actual_executed_cents": command.principal_cents,
                                "actual_fee_cents": 0,
                                "actual_loss_cents": 0,
                                "expected_fee_cents": 0,
                                "expected_loss_cents": 0,
                            }
                        )
                else:
                    issues.append(
                        _issue(
                            "LEGACY_BANK_PENDING_NOT_VERIFIED",
                            action.id,
                            "旧恢复未完成，保留原状态；未核完整终态",
                            "PENDING",
                        )
                    )
                    result = result.model_copy(update={"state": "PENDING_BANK"})
            elif operation.operation_type == "RELEASE_GOAL":
                from app.services.full_goal_release_reader import (
                    read_goal_release_bank_command,
                    verify_goal_release_legs,
                    verify_goal_release_receipt,
                )

                if requests:
                    raise ValueError("Dedicated release cannot share a legacy redemption")
                original_release = read_goal_release_bank_command(session, operation, now)
                result = result.model_copy(
                    update={
                        "effect_hash": original_release.effect_hash,
                        "expected_fee_cents": 0,
                        "expected_loss_cents": 0,
                    }
                )
                if operation.status == "SETTLED" and ledger_verified:
                    verify_goal_release_legs(session, operation, now)
                    result = result.model_copy(
                        update={
                            "complete_settlement_legs_verified": True,
                            "actual_executed_cents": original_release.effect.amount_cents,
                            "actual_fee_cents": 0,
                            "actual_loss_cents": 0,
                            "state": "BANK_SETTLED_APPLICATION_UNRESOLVED",
                        }
                    )
                    if len(receipts) == 1:
                        verify_goal_release_receipt(session, operation, receipts[0], now)
                        result = result.model_copy(
                            update={
                                "state": "SERVICE_RECEIPT_VERIFIED",
                                "service_receipt_verified": True,
                            }
                        )
                    else:
                        issues.append(
                            _issue(
                                "BANK_SETTLED_APPLICATION_UNRESOLVED",
                                action.id,
                                "原银行三条腿已核，应用回执缺失；只恢复原键，不重扣",
                                "PENDING",
                            )
                        )
                elif operation.status == "REJECTED" and not postings and not receipts:
                    result = result.model_copy(
                        update={
                            "state": "BANK_REJECTION_VERIFIED",
                            "actual_executed_cents": 0,
                            "actual_fee_cents": 0,
                            "actual_loss_cents": 0,
                        }
                    )
                elif operation.status in {"ACCEPTED", "UNKNOWN"} and not postings and not receipts:
                    result = result.model_copy(update={"state": "PENDING_BANK"})
                    issues.append(
                        _issue(
                            "BANK_RESULT_PENDING",
                            action.id,
                            "专用回拨原银行结果未知，不能换键或释放原累计占用",
                            "PENDING",
                        )
                    )
                elif operation.status == "SETTLED" and not ledger_verified:
                    issues.append(
                        _issue(
                            "BANK_LEGS_NOT_VERIFIED",
                            action.id,
                            "缺完整原账本，SETTLED字符串不能证明三腿回拨",
                            "MISSING",
                        )
                    )
                else:
                    raise ValueError(
                        "Dedicated release status disagrees with original legs or receipts"
                    )
            else:
                if requests:
                    raise ValueError("Generic operation incorrectly shares a legacy request")
                _, effect = _identity(session, operation, now, lock_user=False)
                command_generic = BankCommand.model_validate_json(json.dumps(operation.request))
                result = result.model_copy(
                    update={
                        "effect_hash": command_generic.effect_hash,
                        "expected_fee_cents": effect.fee_cents,
                        "expected_loss_cents": effect.loss_cents,
                    }
                )
                if operation.status == "SETTLED" and ledger_verified:
                    _legs(session, operation, effect, now)
                    result = result.model_copy(
                        update={
                            "complete_settlement_legs_verified": True,
                            "actual_executed_cents": effect.amount_cents,
                            "actual_fee_cents": effect.fee_cents,
                            "actual_loss_cents": effect.loss_cents,
                        }
                    )
                    if len(receipts) == 1:
                        verify_execution_receipt(session, operation, receipts[0], now)
                        result = result.model_copy(
                            update={
                                "state": "SERVICE_RECEIPT_VERIFIED",
                                "service_receipt_verified": True,
                            }
                        )
                    else:
                        result = result.model_copy(
                            update={"state": "BANK_SETTLED_APPLICATION_UNRESOLVED"}
                        )
                        issues.append(
                            _issue(
                                "BANK_SETTLED_APPLICATION_UNRESOLVED",
                                action.id,
                                "真实银行两腿已核，但原应用回执缺失；只查询原key，不重扣",
                                "PENDING",
                            )
                        )
                elif operation.status == "REJECTED" and not postings and not receipts:
                    result = result.model_copy(
                        update={
                            "state": "BANK_REJECTION_VERIFIED",
                            "actual_executed_cents": 0,
                            "actual_fee_cents": 0,
                            "actual_loss_cents": 0,
                        }
                    )
                elif operation.status in {"ACCEPTED", "UNKNOWN"} and not postings and not receipts:
                    result = result.model_copy(update={"state": "PENDING_BANK"})
                    issues.append(
                        _issue(
                            "BANK_RESULT_PENDING",
                            action.id,
                            "独立银行仍未完成，原UNKNOWN不能按失败重试",
                            "PENDING",
                        )
                    )
                elif operation.status == "SETTLED" and not ledger_verified:
                    issues.append(
                        _issue(
                            "BANK_LEGS_NOT_VERIFIED",
                            action.id,
                            "账本完整性未核，SETTLED字符串不能证明成功",
                            "MISSING",
                        )
                    )
                else:
                    raise ValueError(
                        "A non-settled bank result has unexpected effect legs or receipts"
                    )
        except (PolicyLifecycleError, ValueError, TypeError, KeyError) as error:
            issues.append(
                _issue("ACTION_BANK_OR_RECEIPT_INTEGRITY_ERROR", action.id, str(error), "INTEGRITY")
            )
    state = (
        "MANUAL_REVIEW_REQUIRED"
        if any(row.kind in {"INTEGRITY", "DIFFERENCE"} for row in issues)
        else result.state
    )
    return result.model_copy(update={"state": state, "issues": issues})


def full_reconciliation(session: Session, user_id: UUID, now: datetime) -> FullReconciliationReport:
    _read_snapshot(session)
    now = _now(now)
    user = session.scalar(select(User).where(User.id == user_id))
    if user is None or not user.is_simulated:
        raise PolicyLifecycleError("NOT_FOUND", "当前模拟用户不存在", 404)
    with session.no_autoflush, historical_ledger_scope(session):
        return _read(session, user_id, now)


def _read(session: Session, user_id: UUID, now: datetime) -> FullReconciliationReport:
    issues: list[ReconciliationIssue] = []
    tables: dict[str, list[Any]] = {}
    inventory = []
    for model in (
        Account,
        AssetPosition,
        Goal,
        ActionPlan,
        BankOperation,
        SimulatedBankRedemption,
        ActionReceipt,
        SimulatedBankPosting,
    ):
        rows, item = _load(
            session, model, user_id, POSTING_LIMIT if model is SimulatedBankPosting else ROW_LIMIT
        )
        tables[model.__tablename__] = rows
        inventory.append(item)
        if not item.complete:
            issues.append(
                _issue(
                    "RECONCILIATION_ROW_CAPACITY",
                    item.table,
                    f"原行分母{item.actual_count}，本次仅捕获{item.captured_count}，不删除未覆盖",
                    "MISSING",
                )
            )
    complete = all(item.complete for item in inventory)
    audit = verify_audit_chain(session, user_id, mode="EXACT")
    if audit.status != "VALID":
        issues.append(
            _issue(
                "AUDIT_" + audit.status,
                audit.epoch_id,
                "原生产审计核验未VALID，保留完整诊断",
                "INTEGRITY" if audit.status == "INTEGRITY_ERROR" else "MISSING",
            )
        )
    heads: dict[str, SimulatedBankPosting] = {}
    ledger_verified = False
    if complete:
        try:
            heads = ledger_heads(session, user_id)
            ledger_verified = True
        except PolicyLifecycleError as error:
            issues.append(_issue(error.code, "independent_bank_ledger", error.message, "INTEGRITY"))
    postings: list[SimulatedBankPosting] = tables[SimulatedBankPosting.__tablename__]
    if any(row.occurred_at > now for row in postings):
        issues.append(
            _issue(
                "BANK_POSTING_AFTER_BUSINESS_CLOCK",
                "independent_bank_ledger",
                "存在业务时点之后的独立银行原件，不能借未来入账",
                "MISSING",
            )
        )
        ledger_verified = False
        heads = {}
    projection_matched = False
    pending_explained = False
    if ledger_verified:
        try:
            validate_bank_projection(session, user_id, now)
            projection_matched = True
        except PolicyLifecycleError as error:
            issues.append(_issue(error.code, "current_projection", error.message, "DIFFERENCE"))
            try:
                validate_bank_projection(session, user_id, now, allow_unprojected=True)
                pending_explained = True
            except PolicyLifecycleError:
                pass
    accounts: list[Account] = tables[Account.__tablename__]
    positions: list[AssetPosition] = tables[AssetPosition.__tablename__]
    goals: list[Goal] = tables[Goal.__tablename__]
    actions: list[ActionPlan] = tables[ActionPlan.__tablename__]
    operations: list[BankOperation] = tables[BankOperation.__tablename__]
    requests: list[SimulatedBankRedemption] = tables[SimulatedBankRedemption.__tablename__]
    receipts: list[ActionReceipt] = tables[ActionReceipt.__tablename__]
    for account in accounts:
        if account.observed_at > now:
            issues.append(
                _issue(
                    "APPLICATION_ACCOUNT_AFTER_BUSINESS_CLOCK",
                    account.id,
                    "未来账户观测不能当本时点已知事实",
                    "MISSING",
                )
            )
    for position in positions:
        if position.purchased_at > now:
            issues.append(
                _issue(
                    "APPLICATION_POSITION_AFTER_BUSINESS_CLOCK",
                    position.id,
                    "未来持仓不能当本时点已知事实",
                    "MISSING",
                )
            )
    cash = [
        _amount(row.id, "ACCOUNT_CASH", row.balance_cents, heads, f"CASH:{row.id}", now, issues)
        for row in accounts
        if row.account_type != "CREDIT_CARD"
    ]
    principals = [
        _amount(
            row.id,
            "POSITION_PRINCIPAL",
            0 if row.status == "REDEEMED" else row.principal_cents,
            heads,
            f"POSITION:{row.id}",
            now,
            issues,
        )
        for row in positions
    ]
    known_ledgers = {f"CASH:{row.id}" for row in accounts if row.account_type != "CREDIT_CARD"} | {
        f"POSITION:{row.id}" for row in positions
    }
    if complete and any(
        key.startswith(("CASH:", "POSITION:")) and key not in known_ledgers for key in heads
    ):
        issues.append(
            _issue(
                "ORPHAN_BANK_BALANCE_IDENTITY",
                "independent_bank_ledger",
                "独立银行现金/持仓不存在对应当前应用行",
                "INTEGRITY",
            )
        )
    goal_results = []
    for goal in goals:
        owned = [row for row in positions if row.goal_id == goal.id and row.status != "REDEEMED"]
        principal = sum(row.principal_cents for row in owned)
        proof = None
        proof_verified = False
        try:
            if not complete:
                raise ValueError("Current goal position/ownership inventory is incomplete")
            proof = _proof(session, user_id, OWNERSHIP_SOURCE, "goal_id", goal.id, now)
            if (
                not _matches(
                    proof.content,
                    {
                        "protocol": "goal-ownership-v1",
                        "policy_id": str(goal.policy_id),
                        "account_id": str(goal.account_id) if goal.account_id else None,
                        "allocated_cents": goal.allocated_cents,
                        "cash_owned_cents": goal.allocated_cents - principal,
                        "principal_owned_cents": principal,
                        "position_ids": sorted(str(row.id) for row in owned),
                    },
                )
                or goal.allocated_cents < principal
            ):
                raise ValueError(
                    "Current goal ownership disagrees with complete original bank proof"
                )
            proof_verified = True
        except PolicyLifecycleError as error:
            issues.append(
                _issue("GOAL_OWNERSHIP_PROOF_NOT_VERIFIED", goal.id, str(error), "MISSING")
            )
        except ValueError as error:
            issues.append(
                _issue(
                    "GOAL_OWNERSHIP_PROOF_INCONSISTENT",
                    goal.id,
                    str(error),
                    "INTEGRITY" if complete else "MISSING",
                )
            )
        legacy = any(row.goal_id == goal.id for row in requests)
        if legacy:
            issues.append(
                _issue(
                    "LEGACY_GOAL_OWNERSHIP_DIMENSION_NOT_POSTED",
                    goal.id,
                    "旧两腿恢复协议未逐次写产权维度；不由应用成功日志补造产权真值",
                    "UNSUPPORTED",
                )
            )
        goal_results.append(
            ReconciliationGoal(
                goal_id=goal.id,
                account_id=goal.account_id,
                allocated_cents=goal.allocated_cents,
                position_ids=[row.id for row in owned],
                ownership_evidence_id=proof.id if proof else None,
                ownership_evidence_hash=proof.content_hash if proof else None,
                current_ownership_proof_verified=proof_verified,
                cash=_amount(
                    goal.id,
                    "GOAL_CASH",
                    goal.allocated_cents - principal if complete else None,
                    heads,
                    f"GOAL_CASH:{goal.id}",
                    now,
                    issues,
                    supported=not legacy,
                    goal_account_id=goal.account_id,
                ),
                principal=_amount(
                    goal.id,
                    "GOAL_PRINCIPAL",
                    principal if complete else None,
                    heads,
                    f"GOAL_PRINCIPAL:{goal.id}",
                    now,
                    issues,
                    supported=not legacy,
                    goal_account_id=goal.account_id,
                ),
            )
        )
    by_operation: dict[UUID, list[BankOperation]] = defaultdict(list)
    by_request: dict[UUID, list[SimulatedBankRedemption]] = defaultdict(list)
    by_receipt: dict[UUID, list[ActionReceipt]] = defaultdict(list)
    by_posting: dict[UUID, list[SimulatedBankPosting]] = defaultdict(list)
    owner_action_ids = {row.id for row in actions}
    operation_actions = {row.id: row.action_plan_id for row in operations}
    active_operations = [row for row in operations if row.status != "REJECTED"]
    if (
        len({row.idempotency_key for row in operations}) != len(operations)
        or len({row.business_key for row in active_operations}) != len(active_operations)
        or len(
            {
                row.closing_position_id
                for row in active_operations
                if row.closing_position_id is not None
            }
        )
        != len([row for row in active_operations if row.closing_position_id is not None])
    ):
        issues.append(
            _issue(
                "DUPLICATE_BANK_LOGICAL_EFFECT",
                "bank_operations",
                "同原key/business/closing-position存在重复银行效果",
                "INTEGRITY",
            )
        )
    for operation_row in operations:
        by_operation[operation_row.action_plan_id].append(operation_row)
    for request_row in requests:
        by_request[request_row.action_plan_id].append(request_row)
    for receipt_row in receipts:
        by_receipt[receipt_row.action_plan_id].append(receipt_row)
    for posting_row in postings:
        if posting_row.operation_id is not None:
            action_id = operation_actions.get(posting_row.operation_id)
            if action_id is None:
                issues.append(
                    _issue(
                        "ORPHAN_BANK_POSTING_OPERATION",
                        posting_row.id,
                        "银行非opening原腿缺操作引用",
                        "INTEGRITY" if complete else "MISSING",
                    )
                )
            else:
                by_posting[action_id].append(posting_row)
    for identity in set(by_operation) | set(by_request) | set(by_receipt):
        if identity not in owner_action_ids:
            issues.append(
                _issue(
                    "ORPHAN_ACTION_REFERENCE",
                    identity,
                    "当前银行操作/回执没有同owner原行动",
                    "INTEGRITY" if complete else "MISSING",
                )
            )
    action_results = [
        _action(
            session,
            row,
            by_operation[row.id],
            by_request[row.id],
            by_receipt[row.id],
            by_posting[row.id],
            now,
            inventory_complete=complete,
            ledger_verified=ledger_verified,
        )
        for row in actions
    ]
    issues.extend(issue for row in action_results for issue in row.issues)
    for position_row in positions:
        if position_row.status == "UNKNOWN":
            issues.append(
                _issue(
                    "POSITION_STATUS_UNKNOWN",
                    position_row.id,
                    "保留原持仓UNKNOWN，不把本金匹配当可赎回",
                    "PENDING",
                )
            )
    state = report_state(
        issues,
        complete=complete and ledger_verified and projection_matched and audit.status == "VALID",
    )
    return FullReconciliationReport(
        user_id=user_id,
        as_of=now,
        state=state,
        manual_review_required=state == "MANUAL_REVIEW_REQUIRED",
        bank_ledger_verified=ledger_verified,
        current_application_projection_matched=projection_matched,
        pending_application_projection_explained=pending_explained,
        audit=audit,
        inventory=inventory,
        account_cash=cash,
        position_principals=principals,
        goal_ownership=goal_results,
        actions=action_results,
        bank_postings=[_posting(row) for row in postings],
        issues=issues,
        uncovered=[row for row in issues if row.kind in {"MISSING", "UNSUPPORTED", "PENDING"}],
        input_hash=configuration_hash(
            {
                "user_id": str(user_id),
                "as_of": now.isoformat(),
                "rows": {key: [row_copy(row) for row in rows] for key, rows in tables.items()},
                "audit": audit.model_dump(mode="json"),
                "goal_proofs": [
                    {
                        "goal_id": str(row.goal_id),
                        "evidence_id": str(row.ownership_evidence_id)
                        if row.ownership_evidence_id
                        else None,
                        "content_hash": row.ownership_evidence_hash,
                        "verified": row.current_ownership_proof_verified,
                    }
                    for row in goal_results
                ],
                "issues": [row.model_dump(mode="json") for row in issues],
            }
        ),
        limitations=[
            "只读报告；人工待核对为当前推导状态，不创建修复/人工任务或改账。",
            "账面减独立银行为差额；MATCHED仅本时点已核范围，不授予当前权限或独立正式经济证明。",
            "仅已有MVP五类执行效果及旧恢复回执；FULL新增未实现银行动作、九类全部动作/进程硬重启仍未验收。",
            "账户比较不含信用卡负债；持仓核本金，不用未到账利息或市场收益估值当现金。",
            "旧恢复未完整写目标产权维度时保留UNKNOWN；缺opening或当前完整产权证据不补零。",
            "银行已结算但应用回执缺失时保留原SUBMITTED/UNKNOWN；只查询同原action/key，不重扣。",
            "本包不执行恢复/对账写入、外部资金接口或跨请求授权缓存。",
        ],
    )
