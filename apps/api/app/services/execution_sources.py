"""Read-only economic source bindings, also checked inside the bank transaction."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

from app.db.models import (
    Account,
    ActionPlan,
    AssetPosition,
    AssetProduct,
    BankOperation,
    CreditCardBill,
    EvidenceItem,
    Goal,
    SimulatedBankPosting,
    Transaction,
)
from app.domain.asset_allocation_types import (
    AssetProductTerms,
    FixedPrincipalTerms,
    PlannedPrincipalTerms,
)
from app.domain.execution import execution_effect_hash
from app.domain.execution_types import BankCommand, ConfirmationGrant, ExecutionEffect
from app.domain.full_dynamic_goal_execution import FullDynamicGoalProof
from app.domain.history_coverage import bank_fact_snapshot
from app.domain.policy_configuration import configuration_hash
from app.domain.recovery_types import RecoveryQuote
from app.services.policy_lifecycle import PolicyLifecycleError
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

FULL_JOINT_GOAL_GUARDS_VERSION = "registered-joint-goal-execution-v2"


def _source_error(message: str) -> PolicyLifecycleError:
    return PolicyLifecycleError("INVALID_EXECUTION_SOURCE", message, 409)


def _bank_proof(item: EvidenceItem | None, user_id: UUID, now: datetime) -> EvidenceItem:
    if now.tzinfo is None or now.utcoffset() is None:
        raise _source_error("执行来源时钟必须带时区")
    if (
        item is None
        or item.user_id != user_id
        or item.evidence_level != "BANK_CONFIRMED"
        or item.status != "VALID"
        or item.content.get("simulation") is not True
        or item.observed_at > now
        or item.valid_from > now
        or (item.valid_to is not None and item.valid_to <= now)
        or configuration_hash(item.content) != item.content_hash
    ):
        raise _source_error("收款或经济事实缺少完整、当前已知的银行来源")
    return item


def _card_binding(
    session: Session,
    user_id: UUID,
    payee_id: str,
    bill_id: UUID,
    now: datetime,
) -> UUID:
    bill = session.get(CreditCardBill, bill_id)
    if bill is None or bill.user_id != user_id:
        raise _source_error("收款账单不存在或不属于当前用户")
    account = session.get(Account, bill.account_id)
    if (
        account is None
        or account.user_id != user_id
        or account.account_type != "CREDIT_CARD"
        or payee_id != f"credit-card:{account.id}"
    ):
        raise _source_error("收款对象必须是本张账单所属的本人信用卡")
    proof = _bank_proof(session.get(EvidenceItem, bill.evidence_id), user_id, now)
    expected = {
        "user_id": str(user_id),
        "bill_id": str(bill.id),
        "account_id": str(account.id),
        "source_ref": bill.source_ref,
        "total_cents": bill.total_cents,
        "paid_cents": bill.paid_cents,
        "minimum_due_cents": bill.minimum_due_cents,
        "statement_date": bill.statement_date.isoformat(),
        "due_date": bill.due_date.isoformat(),
        "status": bill.status,
    }
    if proof.source_type != "SIMULATED_CREDIT_CARD_BILL" or any(
        proof.content.get(key) != value for key, value in expected.items()
    ):
        raise _source_error("收款账单投影与银行来源不一致")
    return proof.id


def _counterparty_binding(
    session: Session,
    user_id: UUID,
    payee_id: str,
    proof_id: UUID,
    now: datetime,
) -> UUID:
    proof = _bank_proof(session.get(EvidenceItem, proof_id), user_id, now)
    rows = list(
        session.scalars(
            select(Transaction).where(
                Transaction.user_id == user_id,
                Transaction.evidence_id == proof_id,
            )
        )
    )
    if len(rows) != 1:
        raise _source_error("收款身份必须关联唯一银行交易来源")
    transaction = rows[0]
    try:
        bank_fact_snapshot(transaction, proof)
    except ValueError as error:
        raise _source_error("收款身份与银行交易载荷不一致") from error
    if (
        transaction.counterparty_ref != payee_id
        or transaction.direction != "DEBIT"
        or proof.content.get("economic_role") != "CONSUMPTION"
        or transaction.occurred_at > now
        or transaction.observed_at > now
    ):
        raise _source_error("收款对象不能由收入、内部划转或未知未来记录冒充")
    return proof.id


def resolve_payee_binding(
    session: Session,
    user_id: UUID,
    payee_id: str,
    now: datetime,
    *,
    bill_id: UUID | None = None,
) -> UUID:
    if bill_id is not None:
        return _card_binding(session, user_id, payee_id, bill_id, now)
    rows = list(
        session.scalars(
            select(Transaction)
            .where(
                Transaction.user_id == user_id,
                Transaction.counterparty_ref == payee_id,
                Transaction.direction == "DEBIT",
                Transaction.occurred_at <= now,
                Transaction.observed_at <= now,
            )
            .order_by(Transaction.occurred_at.desc(), Transaction.id)
        )
    )
    if not rows or rows[0].evidence_id is None:
        raise _source_error("收款对象没有已知银行身份，不能自动创建新收款人")
    return _counterparty_binding(session, user_id, payee_id, rows[0].evidence_id, now)


def load_execution_quote(
    session: Session,
    user_id: UUID,
    position_id: UUID,
    now: datetime,
    *,
    requested_at: datetime | None = None,
) -> RecoveryQuote:
    """Keep the original price/identity through confirmation; never guess early-exit costs."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise _source_error("报价时钟必须带时区")
    now = now.astimezone(UTC)
    requested = requested_at or now
    if requested.tzinfo is None or requested.utcoffset() is None or requested > now:
        raise _source_error("报价原时间不合法")
    row = session.get(AssetPosition, position_id)
    if row is None or row.user_id != user_id or row.status not in {"HELD", "MATURED"}:
        raise _source_error("报价必须绑定当前用户尚未提交赎回的本金")
    product = session.get(AssetProduct, row.product_id)
    if product is None:
        raise _source_error("缺少原产品条款")
    try:
        terms = AssetProductTerms(
            **{
                key: getattr(product, key)
                for key in AssetProductTerms.model_fields
                if key not in {"product_id", "terms_digest"}
            },
            product_id=product.id,
            terms_digest=configuration_hash(product.maturity_rule),
        )
        destination = execution_return_account(session, user_id, row, now)
        proofs = list(
            session.scalars(
                select(EvidenceItem).where(
                    EvidenceItem.user_id == user_id,
                    EvidenceItem.source_type == "SIMULATED_REDEMPTION_QUOTE",
                    EvidenceItem.content["position_id"].as_string() == str(row.id),
                    EvidenceItem.status != "SUPERSEDED",
                )
            )
        )
        if proofs:
            if len(proofs) != 1:
                raise ValueError("Conflicting redemption quotes")
            proof = _bank_proof(proofs[0], user_id, now)
            expected = {
                "protocol": "recovery-quote-v1",
                "user_id": str(user_id),
                "position_id": str(row.id),
                "destination_account_id": str(destination),
                "goal_id": str(row.goal_id) if row.goal_id else None,
            }
            if any(proof.content.get(key) != value for key, value in expected.items()):
                raise ValueError("Quote has a different economic owner or return account")
            quote = RecoveryQuote.model_validate_json(json.dumps(proof.content["quote"]))
            if (
                quote.user_id != user_id
                or quote.position_id != row.id
                or quote.product_id != row.product_id
                or quote.product_version_number != terms.version_number
                or quote.terms_digest != terms.terms_digest
                or quote.principal_cents != row.principal_cents
                or not quote.request_at <= now < quote.expires_at
                or quote.principal_available_at - quote.request_at
                != timedelta(days=terms.redemption_delay_days)
            ):
                raise ValueError("Quote is expired or rebound")
            return quote.model_copy(update={"evidence_ids": [proof.id]})
        kind = "REDEEM"
        if terms.maturity_rule.get("protocol") == "planned-principal-return-v1":
            planned = PlannedPrincipalTerms.model_validate(terms.maturity_rule)
            if (
                planned.settlement_delay_days != terms.redemption_delay_days
                or row.purchased_at + timedelta(days=terms.lock_days) > requested
            ):
                raise ValueError("Original contract is still locked")
            available = requested + timedelta(days=planned.settlement_delay_days)
        elif terms.maturity_rule.get("protocol") == "fixed-principal-return-v1":
            fixed = FixedPrincipalTerms.model_validate(terms.maturity_rule)
            maturity = row.purchased_at + timedelta(
                days=fixed.term_days + fixed.settlement_delay_days
            )
            if (
                fixed.term_days < terms.lock_days
                or fixed.settlement_delay_days != terms.redemption_delay_days
                or maturity > requested
                or row.maturity_at is None
                or row.maturity_at > requested
            ):
                raise ValueError("Early withdrawal needs an explicit bank price")
            available, kind = requested, "MATURE"
        else:
            raise ValueError("No deterministic principal return rule")
        if now >= requested + timedelta(minutes=15):
            raise ValueError("Contract quote expired")
        return RecoveryQuote.model_validate(
            {
                "quote_id": uuid5(
                    row.id, f"redemption-quote:{terms.terms_digest}:{requested.isoformat()}"
                ),
                "user_id": user_id,
                "position_id": row.id,
                "product_id": row.product_id,
                "product_version_number": terms.version_number,
                "terms_digest": terms.terms_digest,
                "kind": kind,
                "principal_cents": row.principal_cents,
                "fee_cents": 0,
                "loss_cents": 0,
                "net_cents": row.principal_cents,
                "request_at": requested,
                "principal_available_at": available,
                "expires_at": requested + timedelta(minutes=15),
                "evidence_ids": [],
            }
        )
    except (KeyError, ValueError, TypeError, OverflowError) as error:
        raise _source_error(f"赎回报价来源无效：{error}") from error


def execution_return_account(
    session: Session, user_id: UUID, row: AssetPosition, now: datetime
) -> UUID:
    proofs = list(
        session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.user_id == user_id,
                EvidenceItem.source_type == "SIMULATED_BANK_POSITION",
                EvidenceItem.content["position_id"].as_string() == str(row.id),
                EvidenceItem.status != "SUPERSEDED",
            )
        )
    )
    if len(proofs) != 1:
        raise _source_error("原仓位银行证明缺失或冲突")
    proof = _bank_proof(proofs[0], user_id, now)
    expected = {
        "position_id": str(row.id),
        "account_id": str(row.account_id),
        "product_id": str(row.product_id),
        "goal_id": str(row.goal_id) if row.goal_id else None,
        "policy_version_id": str(row.policy_version_id) if row.policy_version_id else None,
        "principal_cents": row.principal_cents,
        "status": row.status,
        "purchased_at": row.purchased_at.astimezone(UTC).isoformat(),
    }
    if any(proof.content.get(key) != value for key, value in expected.items()):
        raise _source_error("原仓位投影与银行事实不一致")
    if row.goal_id is not None:
        goal = session.get(Goal, row.goal_id)
        if goal is None or goal.user_id != user_id:
            raise _source_error("原目标归属不存在")
        destination = goal.account_id
    else:
        try:
            purchases = acquisition_transactions(session, user_id, row, proof, now)
            destination = (
                UUID(proof.content["return_account_id"])
                if proof.content.get("acquisition_protocol") == "execution-purchase-v1"
                else purchases[0].account_id
            )
        except (KeyError, ValueError, TypeError) as error:
            raise _source_error("缺少已知原始购买账户") from error
    if destination is None:
        raise _source_error("原归属没有返本账户")
    account = session.get(Account, destination)
    if (
        account is None
        or account.user_id != user_id
        or account.account_type not in {"CASH", "GOAL"}
    ):
        raise _source_error("返本账户不属于原归属")
    return destination


def acquisition_transactions(
    session: Session, user_id: UUID, position: AssetPosition, proof: EvidenceItem, now: datetime
) -> list[Transaction]:
    """Each funding leg remains a real account transaction, including multi-account buys."""
    protocol = proof.content.get("acquisition_protocol")
    identifiers = (
        proof.content["purchase_transaction_ids"]
        if protocol == "execution-purchase-v1"
        else [proof.content["purchase_transaction_id"]]
    )
    if (
        not isinstance(identifiers, list)
        or not identifiers
        or len(set(identifiers)) != len(identifiers)
    ):
        raise ValueError("Original purchase transaction identities must be unique")
    if proof.content["purchase_transaction_id"] not in identifiers:
        raise ValueError("Original primary transaction is missing")
    result = []
    for identity in identifiers:
        purchase = session.get(Transaction, UUID(identity))
        if purchase is None or purchase.user_id != user_id or purchase.evidence_id is None:
            raise ValueError("Missing original purchase transaction")
        source = _bank_proof(session.get(EvidenceItem, purchase.evidence_id), user_id, now)
        bank_fact_snapshot(purchase, source)
        if (
            purchase.direction != "DEBIT"
            or source.content.get("economic_role") != "ASSET_PURCHASE"
            or purchase.occurred_at != position.purchased_at
            or purchase.observed_at > now
        ):
            raise ValueError("Purchase requires actual, originally observed bank funding")
        result.append(purchase)
    if sum(row.amount_cents for row in result) != position.principal_cents:
        raise ValueError("All original funding legs must sum to this whole position")
    if protocol == "execution-purchase-v1":
        action = session.get(ActionPlan, UUID(proof.content["purchase_action_id"]))
        if (
            action is None
            or action.user_id != user_id
            or action.request_hash != configuration_hash(action.request)
        ):
            raise ValueError("Original action is missing or rehashed incorrectly")
        command = BankCommand.model_validate_json(json.dumps(action.request["execution"]))
        effect = command.effect
        operation = session.get(BankOperation, effect.operation_id)
        if (
            effect.action_type != "PURCHASE_ASSET"
            or effect.position_id != position.id
            or effect.product_id != position.product_id
            or effect.goal_id != position.goal_id
            or effect.position_account_id != position.account_id
            or effect.amount_cents != position.principal_cents
            or effect.policy_version_id != position.policy_version_id
            or effect.return_account_id is None
            or proof.content.get("return_account_id") != str(effect.return_account_id)
            or operation is None
            or operation.status != "SETTLED"
            or operation.request != action.request["execution"]
            or operation.request_hash != configuration_hash(operation.request)
            or {row.account_id: row.amount_cents for row in result}
            != {use.account_id: use.amount_cents for use in effect.cash_uses}
            or len(result) != len(effect.cash_uses)
        ):
            raise ValueError(
                "Original funding or return account differs from the independent purchase"
            )
        for purchase in result:
            transaction_proof = session.get(EvidenceItem, purchase.evidence_id)
            assert transaction_proof is not None
            leg = session.get(
                SimulatedBankPosting, UUID(transaction_proof.content["bank_posting_id"])
            )
            if (
                leg is None
                or leg.operation_id != operation.id
                or leg.account_id != purchase.account_id
                or leg.delta_cents != -purchase.amount_cents
                or leg.ledger_key != f"CASH:{purchase.account_id}"
                or transaction_proof.content.get("bank_operation_id") != str(operation.id)
            ):
                raise ValueError("An acquisition transaction is not a real independent funding leg")
    elif protocol is not None:
        raise ValueError("Unknown purchase acquisition protocol")
    return result


def verify_execution_sources(
    session: Session,
    user_id: UUID,
    effect: ExecutionEffect,
    now: datetime,
) -> FullDynamicGoalProof | None:
    """Does not submit bank effects or trust an application validation label."""
    from app.domain.execution import revalidate_execution
    from app.services.execution_context import load_execution_context

    if effect.user_id != user_id:
        raise _source_error("执行经济对象不属于当前用户")
    if effect.action_type == "PAY_RECURRING":
        if effect.payee_id is None or effect.payee_evidence_id is None or effect.liability is None:
            raise _source_error("缺少精确收款身份")
        if effect.liability.kind == "bill":
            proof_id = _card_binding(
                session, user_id, effect.payee_id, effect.liability.bill_id, now
            )
            if proof_id != effect.payee_evidence_id:
                raise _source_error("收款账单已变化，必须重新准备")
        else:
            _counterparty_binding(session, user_id, effect.payee_id, effect.payee_evidence_id, now)
    from app.services.decision_recording import record_execution_trace, start_capture

    start_capture(session)
    context = load_execution_context(
        session, user_id, effect, now, own_action_id=effect.operation_id
    )
    confirmation = read_execution_confirmation(session, effect, now)
    from app.services.full_dynamic_goal_execution import (
        has_full_dynamic_goal_binding,
        recheck_full_dynamic_goal_proof,
    )

    action = session.get(ActionPlan, effect.operation_id)
    assert action is not None
    dynamic_proof = None
    from app.services.full_recovery_execution import (
        has_full_recovery_binding,
        recheck_full_recovery_proof,
    )

    recovery_bound = effect.action_type == "REDEEM_ASSET" and has_full_recovery_binding(
        session, action
    )
    from app.services.full_experiment_asset_execution import (
        has_full_experiment_asset_binding,
        recheck_full_experiment_asset_proof,
    )

    experiment_asset_bound = effect.action_type == "PURCHASE_ASSET" and (
        has_full_experiment_asset_binding(session, action)
    )
    if experiment_asset_bound:
        from app.services.execution_context import require_full_experiment_asset_context

        binding = session.get_bind()
        if not isinstance(binding, Engine):
            raise _source_error("机制银行重验需要原事务的实际Engine")
        recheck_full_experiment_asset_proof(
            binding,
            session,
            action,
            BankCommand(effect=effect, effect_hash=execution_effect_hash(effect)),
            now,
            own_action_id=action.id,
        )
        context = require_full_experiment_asset_context(session, context)
    if recovery_bound:
        from app.services.execution_context import require_full_recovery_confirmation_context

        binding = session.get_bind()
        if not isinstance(binding, Engine):
            raise _source_error("完整恢复银行重验需要原事务的实际Engine")
        recheck_full_recovery_proof(
            binding,
            session,
            action,
            BankCommand(effect=effect, effect_hash=execution_effect_hash(effect)),
            now,
            own_action_id=action.id,
            require_user_consent=True,
        )
        context = require_full_recovery_confirmation_context(session, context)
    from app.services.full_joint_goal_execution_guards import (
        has_full_joint_goal_binding,
        recheck_full_joint_goal_proof,
    )

    joint_bound = has_full_joint_goal_binding(session, action)
    if joint_bound:
        from app.services.execution_joint_goal_bridge import require_joint_goal_context

        binding = session.get_bind()
        if not isinstance(binding, Engine):
            raise _source_error("联合目标银行重验需要原事务的实际Engine")
        dynamic_proof = recheck_full_joint_goal_proof(
            binding,
            session,
            action,
            BankCommand(effect=effect, effect_hash=execution_effect_hash(effect)),
            now,
            own_action_id=action.id,
        )
        context = require_joint_goal_context(session, effect, context, dynamic_proof)
    elif effect.action_type == "ALLOCATE_GOAL" and has_full_dynamic_goal_binding(session, action):
        binding = session.get_bind()
        if not isinstance(binding, Engine):
            raise _source_error("动态目标银行重验需要原事务的实际Engine")
        dynamic_proof = recheck_full_dynamic_goal_proof(
            binding,
            session,
            action,
            BankCommand(effect=effect, effect_hash=execution_effect_hash(effect)),
            now,
            own_action_id=action.id,
        )
    if dynamic_proof is not None and not joint_bound:
        from app.services.execution_context import require_full_dynamic_goal_proof_context

        context = require_full_dynamic_goal_proof_context(session, effect, context, dynamic_proof)
    validation = (
        revalidate_execution(effect, context, confirmation=confirmation)
        if dynamic_proof is None
        else revalidate_execution(
            effect, context, confirmation=confirmation, full_dynamic_goal_proof=dynamic_proof
        )
    )
    if validation.status != "READY":
        raise _source_error("执行前财务重验未通过：" + ",".join(validation.reasons))
    record_execution_trace(
        session,
        effect,
        validation,
        now,
        "BANK_ACCEPT",
        parent_run_id=action.decision_run_id,
        autonomy_level=action.autonomy_level,
        confirmation=confirmation,
        action_request=action.request
        if dynamic_proof is not None or recovery_bound or experiment_asset_bound
        else None,
    )
    return dynamic_proof


def read_execution_confirmation(
    session: Session,
    effect: ExecutionEffect,
    now: datetime,
) -> ConfirmationGrant | None:
    """Only the exact server-recorded one-shot consent can satisfy confirmation."""
    from app.domain.execution import execution_effect_hash

    digest = execution_effect_hash(effect)
    identity = uuid5(effect.operation_id, "confirmation:" + digest)
    action = session.get(ActionPlan, effect.operation_id)
    proof = session.get(EvidenceItem, identity)
    if proof is None:
        return None
    try:
        content = proof.content
        confirmed = datetime.fromisoformat(content["confirmed_at"])
        until = datetime.fromisoformat(content["valid_until"])
        if (
            action is None
            or action.user_id != effect.user_id
            or action.request.get("confirmation_evidence_id") != str(identity)
            or proof.user_id != effect.user_id
            or proof.status != "VALID"
            or proof.evidence_level != "USER_CONFIRMED_ACTION"
            or proof.source_type != "USER_ACTION_CONFIRMATION"
            or proof.source_ref != str(effect.operation_id)
            or content.get("simulation") is not True
            or configuration_hash(content) != proof.content_hash
            or content.get("action_id") != str(effect.operation_id)
            or content.get("user_id") != str(effect.user_id)
            or content.get("effect_hash") != digest
            or content.get("accepted") is not True
            or confirmed.tzinfo is None
            or until.tzinfo is None
            or not effect.valid_from
            <= confirmed
            <= proof.observed_at
            <= now
            < min(until, effect.expires_at)
            or proof.valid_from > now
            or (proof.valid_to is not None and proof.valid_to <= now)
        ):
            raise ValueError("One-shot confirmation is expired or rebound")
        return ConfirmationGrant(
            user_id=effect.user_id,
            operation_id=effect.operation_id,
            effect_hash=digest,
            evidence_id=identity,
            confirmed_at=confirmed,
            expires_at=until,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise _source_error("单次确认来源与经济后果不一致") from error
