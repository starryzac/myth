"""Independent original Bank settlement and savepoint projection for paired transfers."""

from datetime import datetime
from uuid import UUID, uuid5

from app.db.models import (
    ActionPlan,
    ActionReceipt,
    AssetPosition,
    BankOperation,
    EvidenceItem,
    Goal,
    SimulatedBankPosting,
    Transaction,
)
from app.domain.audit_chain import posting_set_digest, receipt_digest
from app.domain.full_goal_transfer import GoalTransferBankCommand, goal_transfer_leg_specs
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import row_copy
from app.services.audit_recording import (
    SubjectRequest,
    _action_subjects,
    _record,
    _root_run,
    record_bank_accepted,
    record_bank_settled,
)
from app.services.execution_bank import _post
from app.services.execution_exposure import refresh_execution_exposure
from app.services.execution_projection import _cash_preflight, _proof
from app.services.full_goal_release_execution import _transition
from app.services.full_goal_transfer import _authorization, _error, _fresh_candidate, _trace
from app.services.full_goal_transfer_reader import (
    read_goal_transfer_bank_command,
    read_goal_transfer_command,
    verify_goal_transfer_legs,
    verify_goal_transfer_receipt,
)
from app.services.income_ledger import read_income_state
from app.services.policy_lifecycle import _now, _user
from app.services.recovery_projection import _epochs, replace_proof
from app.services.simulated_bank import ledger_heads, require_settlement_order
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


def process_goal_transfer_bank(
    engine: Engine, user_id: UUID, action_id: UUID, now: datetime
) -> None:
    """One distinct Bank transaction; the same key can only recover its original result."""
    now = _now(now)
    with Session(engine) as writer, writer.begin():
        _user(writer, user_id)
        action = writer.get(ActionPlan, action_id)
        if action is None or action.user_id != user_id:
            raise _error("原转移动作不存在", "NOT_FOUND", 404)
        command = read_goal_transfer_command(action)
        banks = list(
            writer.scalars(
                select(BankOperation)
                .where(BankOperation.user_id == user_id, BankOperation.action_plan_id == action_id)
                .limit(2)
            )
        )
        if banks:
            if len(banks) != 1:
                raise _error("原转移银行分母不唯一")
            existing = banks[0]
            read_goal_transfer_bank_command(writer, existing, now)
            if existing.status == "SETTLED":
                verify_goal_transfer_legs(writer, existing, now)
                return
            raise _error(
                "银行原键尚无已验真结算，不能再次生成效果", "GOAL_TRANSFER_BANK_RESULT_UNKNOWN"
            )
        if action.status != "AUTHORIZED":
            raise _error("双向转移需原USER应用提交阶段", "INVALID_ACTION_STATE")
        _authorization(action, writer)
        require_settlement_order(writer, user_id, now)
        candidate_hash = _fresh_candidate(engine, action, now)
        # A fresh independent reader must still observe the accepted USER phase.
        # Submission and its independent Bank effects commit in one transaction;
        # no SUBMITTED-without-Bank interval is created or silently explained away.
        _transition(writer, action, "SUBMITTED", now, "GOAL_TRANSFER_PENDING_UNRESERVED")
        _epochs(writer, user_id, now, action_id)
        refresh_execution_exposure(
            writer,
            user_id,
            now,
            action_id,
            declarations={
                action_id: {
                    "action_id": str(action_id),
                    "state": "TRANSFER_PENDING_UNRESERVED",
                }
            },
        )
        _trace(writer, action, now, "BANK_ACCEPT", candidate_hash)
        effect = command.effect
        operation = BankOperation(
            id=effect.operation_id,
            user_id=user_id,
            created_at=now,
            action_plan_id=action.id,
            legacy_redemption_id=None,
            closing_position_id=None,
            operation_type="TRANSFER_GOAL",
            business_key=effect.business_key,
            idempotency_key=effect.bank_idempotency_key,
            request=command.model_dump(mode="json"),
            request_hash=configuration_hash(command.model_dump(mode="json")),
            requested_at=now,
            available_at=now,
            settled_at=None,
            status="ACCEPTED",
        )
        writer.add(operation)
        writer.flush()
        record_bank_accepted(writer, operation, now)
        heads = ledger_heads(writer, user_id)
        for leg in goal_transfer_leg_specs(command):
            head = heads.get(leg.ledger_key)
            if (
                head is None
                or head.user_id != user_id
                or head.account_id != leg.account_id
                or head.ledger_dimension != leg.ledger_dimension
                or any(
                    head.ledger_metadata.get(key) != value
                    for key, value in leg.required_metadata.items()
                )
            ):
                raise _error("独立银行原账户和两目标归属头与原转移后果不一致")
            _post(writer, operation, leg.ledger_key, leg.delta_cents, now, leg.leg_ref)
        operation.status, operation.settled_at = "SETTLED", now
        writer.flush()
        verify_goal_transfer_legs(writer, operation, now)
        record_bank_settled(writer, operation, now)


def _cash_transaction(
    session: Session, command: GoalTransferBankCommand, row: SimulatedBankPosting, now: datetime
) -> UUID:
    effect = command.effect
    identity = uuid5(effect.operation_id, "transaction:" + str(row.leg_ref))
    source_ref = f"bank-operation:{effect.operation_id}:{row.leg_ref}"
    counterparty = f"goal-transfer:{effect.source_goal_id}:{effect.target_goal_id}"
    payload = {
        "simulation": True,
        "user_id": str(effect.user_id),
        "transaction_id": str(identity),
        "account_id": str(row.account_id),
        "direction": "DEBIT" if row.delta_cents < 0 else "CREDIT",
        "amount_cents": abs(row.delta_cents),
        "balance_after_cents": row.balance_after_cents,
        "occurred_at": row.occurred_at.isoformat(),
        "counterparty_ref": counterparty,
        "economic_role": "INTERNAL_TRANSFER",
        "bank_operation_id": str(effect.operation_id),
        "bank_posting_id": str(row.id),
    }
    proof_id = uuid5(identity, "evidence")
    session.add(
        EvidenceItem(
            id=proof_id,
            user_id=effect.user_id,
            created_at=now,
            evidence_level="BANK_CONFIRMED",
            source_type="SIMULATED_BANK_TRANSACTION",
            source_ref=source_ref,
            content=payload,
            content_hash=configuration_hash(payload),
            valid_from=row.occurred_at,
            observed_at=now,
            status="VALID",
        )
    )
    session.flush()
    session.add(
        Transaction(
            id=identity,
            user_id=effect.user_id,
            created_at=now,
            account_id=row.account_id,
            evidence_id=proof_id,
            source_ref=source_ref,
            direction=payload["direction"],
            amount_cents=abs(row.delta_cents),
            balance_after_cents=row.balance_after_cents,
            category="internal_transfer",
            category_confirmed=False,
            counterparty_ref=counterparty,
            is_one_off=False,
            occurred_at=row.occurred_at,
            observed_at=now,
        )
    )
    return identity


def _goal_projection(
    session: Session,
    command: GoalTransferBankCommand,
    rows: list[SimulatedBankPosting],
    now: datetime,
) -> list[UUID]:
    effect = command.effect
    identities = []
    for binding in (effect.scope.source, effect.scope.target):
        goal = session.get(Goal, binding.goal_id)
        leg = next((row for row in rows if row.leg_ref == f"goal_cash:{binding.goal_id}"), None)
        if (
            goal is None
            or goal.user_id != effect.user_id
            or goal.account_id != binding.account_id
            or goal.policy_id != binding.policy_id
            or leg is None
        ):
            raise _error("当前两目标不能替换原已结算归属", "BANK_RECONCILIATION_REQUIRED")
        proof = _proof(session, effect.user_id, "SIMULATED_GOAL_OWNERSHIP", "goal_id", goal.id, now)
        positions = list(
            session.scalars(
                select(AssetPosition).where(
                    AssetPosition.user_id == effect.user_id,
                    AssetPosition.goal_id == goal.id,
                    AssetPosition.status != "REDEEMED",
                )
            )
        )
        principal = sum(row.principal_cents for row in positions)
        expected = {
            "protocol": "goal-ownership-v1",
            "policy_id": str(goal.policy_id),
            "account_id": str(goal.account_id),
            "allocated_cents": goal.allocated_cents,
            "cash_owned_cents": goal.allocated_cents - principal,
            "principal_owned_cents": principal,
            "position_ids": sorted(str(row.id) for row in positions),
        }
        if (
            any(proof.content.get(key) != value for key, value in expected.items())
            or leg.balance_before_cents != expected["cash_owned_cents"]
        ):
            raise _error("两目标投影与原银行前归属不一致", "BANK_RECONCILIATION_REQUIRED")
        goal.allocated_cents = leg.balance_after_cents + principal
        projected = replace_proof(
            session,
            proof,
            {
                **proof.content,
                "as_of": now.isoformat(),
                "allocated_cents": goal.allocated_cents,
                "cash_owned_cents": leg.balance_after_cents,
                "principal_owned_cents": principal,
                "transfer_operation_id": str(effect.operation_id),
            },
            now,
            effect.operation_id,
        )
        identities.append(projected.id)
    return identities


def _record_transfer_projected(
    session: Session,
    action: ActionPlan,
    operation: BankOperation,
    receipt: ActionReceipt,
    rows: list[SimulatedBankPosting],
    ownership_ids: list[UUID],
    now: datetime,
) -> None:
    command = read_goal_transfer_command(action)
    subjects: list[SubjectRequest] = (
        _action_subjects(action)
        + [
            ("BANK_OPERATION", operation.id, "BASIS", None),
            ("ACTION_RECEIPT", receipt.id, "AFTER", None),
            ("GOAL", command.effect.target_goal_id, "AFTER", None),
        ]
        + [("BANK_POSTING", row.id, "BASIS", None) for row in rows]
    )
    subjects += [("EVIDENCE", identity, "AFTER", None) for identity in ownership_ids]
    for raw in receipt.response["transaction_ids"]:
        transaction = session.get(Transaction, UUID(raw))
        if (
            transaction is None
            or transaction.user_id != action.user_id
            or transaction.evidence_id is None
        ):
            raise _error("转移原交易或凭证缺失")
        subjects.extend(
            [
                ("TRANSACTION", transaction.id, "AFTER", None),
                ("EVIDENCE", transaction.evidence_id, "AFTER", None),
            ]
        )
    _record(
        session,
        user_id=action.user_id,
        event_type="ACTION_PROJECTED",
        aggregate_type="ACTION_RECEIPT",
        aggregate_id=receipt.id,
        correlation_id=_root_run(session, action.user_id, action.decision_run_id),
        correlation_kind="DECISION_RUN",
        occurred_at=receipt.occurred_at,
        observed_at=now,
        subjects=subjects,
        fact_key=f"ACTION_PROJECTED:{receipt.id}",
        run_id=action.decision_run_id,
        action_id=action.id,
        receipt_id=receipt.id,
        anchor_specs=[
            (
                "ACTION_RECEIPT",
                receipt.id,
                receipt_digest(row_copy(receipt)),
                "configuration-sha256-v1",
            ),
            (
                "BANK_POSTING_SET",
                operation.id,
                posting_set_digest(row_copy(row) for row in rows),
                "configuration-sha256-v1",
            ),
        ],
    )


def project_goal_transfer_execution(
    session: Session, operation: BankOperation, now: datetime
) -> ActionReceipt:
    """Actual savepoint; historical settlement recovery does not borrow current permission."""
    now = _now(now)
    with session.begin_nested():
        _user(session, operation.user_id)
        command = read_goal_transfer_bank_command(session, operation, now)
        rows = verify_goal_transfer_legs(session, operation, now)
        action = session.get(ActionPlan, operation.action_plan_id)
        assert action is not None
        receipts = list(
            session.scalars(
                select(ActionReceipt)
                .where(
                    ActionReceipt.user_id == operation.user_id,
                    ActionReceipt.action_plan_id == action.id,
                )
                .limit(2)
            )
        )
        if receipts:
            if len(receipts) != 1:
                raise _error("原回执不唯一", "BANK_RECONCILIATION_REQUIRED")
            verify_goal_transfer_receipt(session, operation, receipts[0], now)
            return receipts[0]
        cash = _cash_preflight(session, operation, rows, now)
        if len(cash) != 2:
            raise _error("转移需要原两账户投影", "BANK_RECONCILIATION_REQUIRED")
        # Before any mutation, current original income has no amount/bucket change.
        income = read_income_state(session, operation.user_id, now)
        income_proof = session.get(EvidenceItem, income.evidence_id)
        assert income_proof is not None
        transactions = []
        for account, proof, row in cash:
            account.balance_cents, account.observed_at = row.balance_after_cents, now
            replace_proof(
                session,
                proof,
                {
                    **proof.content,
                    "balance_cents": account.balance_cents,
                    "as_of": now.isoformat(),
                    "bank_posting_id": str(row.id),
                },
                now,
                operation.id,
            )
            transactions.append(_cash_transaction(session, command, row, now))
        ownership_ids = _goal_projection(session, command, rows, now)
        # Refresh only metadata clocks; all original income origins/fragments/uses,
        # assigned/spent amounts and monthly contributions remain byte-value equal.
        replace_proof(
            session,
            income_proof,
            {**income_proof.content, "as_of": now.isoformat()},
            now,
            operation.id,
        )
        _epochs(session, operation.user_id, now, operation.id)
        receipt = ActionReceipt(
            id=uuid5(operation.id, "receipt"),
            user_id=operation.user_id,
            created_at=now,
            action_plan_id=action.id,
            attempt_number=1,
            receipt_ref=f"bank-operation:{operation.id}",
            status="SUCCEEDED",
            executed_cents=command.effect.amount_cents,
            fee_cents=0,
            loss_cents=0,
            response={
                "bank_operation_id": str(operation.id),
                "posting_ids": sorted(str(row.id) for row in rows),
                "transaction_ids": sorted(str(value) for value in transactions),
                "goal_ownership_evidence_ids": sorted(str(value) for value in ownership_ids),
            },
            occurred_at=operation.settled_at,
            reconciled_at=now,
        )
        session.add(receipt)
        action.status = "SUCCEEDED"
        session.flush()
        refresh_execution_exposure(
            session,
            operation.user_id,
            now,
            operation.id,
            declarations={
                action.id: {
                    "action_id": str(action.id),
                    "state": "EXECUTION_SETTLED",
                    "receipt_id": str(receipt.id),
                }
            },
        )
        _record_transfer_projected(session, action, operation, receipt, rows, ownership_ids, now)
        verify_goal_transfer_receipt(session, operation, receipt, now)
        return receipt
