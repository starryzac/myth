"""Independent bank transactions for immutable execution effects."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid5

from app.db.models import (
    Account,
    ActionPlan,
    ActionResourceReservation,
    AssetPosition,
    AssetProduct,
    BankOperation,
    CreditCardBill,
    EvidenceItem,
    Goal,
    PolicyVersion,
    SimulatedBankPosting,
    Transaction,
    User,
)
from app.domain.execution import execution_effect_hash
from app.domain.execution_types import BankCommand, ExecutionEffect
from app.domain.full_dynamic_goal_execution import FullDynamicGoalProof
from app.domain.history_coverage import bank_fact_snapshot
from app.domain.income_ledger import IncomeLedger, location_id
from app.domain.policy_configuration import configuration_hash
from app.services.policy_lifecycle import PolicyLifecycleError, is_version_authorized
from app.services.simulated_bank import (
    ledger_heads,
    require_settlement_order,
    validate_bank_projection,
)
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

FULL_ASSET_BATCH_GUARDS_VERSION = "full-asset-batch-guards-v1"
FULL_RECOVERY_GUARDS_VERSION = "full-recovery-execution-guards-v1"
FULL_EXPERIMENT_ASSET_GUARDS_VERSION = "full-experiment-asset-guards-v1"
FULL_JOINT_GOAL_GUARDS_VERSION = "registered-joint-goal-execution-v2"


def has_full_asset_batch_binding(session: Session, action: ActionPlan) -> bool:
    """An immutable child row cannot be downgraded by stripping its mutable marker/key."""
    if "full_asset_execution" in action.request or action.idempotency_key.startswith("full-asset:"):
        return True
    if action.action_type not in {"PURCHASE_ASSET", "ASSET_PURCHASE"}:
        return False
    if session.scalar(text("SELECT to_regclass('public.full_asset_execution_batches')")) is None:
        return False
    from app.db.full_models import FullAssetExecutionBatch

    return (
        session.scalar(
            select(FullAssetExecutionBatch.id)
            .where(FullAssetExecutionBatch.action_plan_id == action.id)
            .limit(1)
        )
        is not None
    )


class BankOperationResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    operation_id: UUID
    action_id: UUID
    status: str
    posting_ids: list[UUID]


def process_operation(
    engine: Engine, user_id: UUID, action_id: UUID, now: datetime
) -> BankOperationResult:
    """Own a separate transaction after the application action has committed."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise _error("An aware trusted bank clock is required")
    now = now.astimezone(UTC)
    from app.services.execution_observations import observed_begin

    with (
        Session(engine) as session,
        observed_begin(session, engine, user_id, action_id, now, "INDEPENDENT_BANK"),
    ):
        from app.db.audit_guard import transaction_gate

        transaction_gate(session, user_id)
        user = session.scalar(select(User).where(User.id == user_id).with_for_update())
        action = session.get(ActionPlan, action_id)
        if user is None or not user.is_simulated or action is None or action.user_id != user_id:
            raise _error("Unknown committed simulated action")
        try:
            command = BankCommand.model_validate_json(json.dumps(action.request["execution"]))
        except (KeyError, TypeError, ValueError) as error:
            raise _error("A strict committed bank command is required") from error
        effect = command.effect
        if (
            configuration_hash(action.request) != action.request_hash
            or execution_effect_hash(effect) != command.effect_hash
            or effect.operation_id != action_id
            or effect.user_id != user_id
        ):
            raise _error("Action and effect identities or hashes disagree")
        payload = command.model_dump(mode="json")
        digest = configuration_hash(payload)
        operation = session.scalar(
            select(BankOperation).where(
                BankOperation.user_id == user_id,
                BankOperation.idempotency_key == action.idempotency_key,
            )
        )
        if operation is not None:
            if operation.action_plan_id != action_id or operation.request_hash != digest:
                raise _error("An idempotency key cannot change its original economics")
        else:
            require_settlement_order(session, user_id, now)
            dynamic_proof = _validate_new(session, action, effect, command.effect_hash, now)
            if effect.action_type == "PAY_RECURRING":
                from app.services.full_payment_permissions import enforce_full_payment_bank_scope

                enforce_full_payment_bank_scope(engine, user_id, effect, now)
            if has_full_asset_batch_binding(session, action):
                from app.services.full_asset_execution_dispatch import (
                    enforce_full_asset_batch_acceptance,
                )

                enforce_full_asset_batch_acceptance(engine, session, action, command, now)
            from app.services.full_execution_protection import (
                enforce_full_execution_protection,
                has_full_protection_policies,
            )

            if has_full_protection_policies(session, user_id):
                from app.domain.execution import revalidate_execution
                from app.services.execution_context import load_execution_context
                from app.services.execution_sources import read_execution_confirmation

                context = load_execution_context(
                    session, user_id, effect, now, own_action_id=action.id
                )
                from app.services.full_experiment_asset_execution import (
                    has_full_experiment_asset_binding,
                )

                if effect.action_type == "PURCHASE_ASSET" and has_full_experiment_asset_binding(
                    session, action
                ):
                    from app.services.execution_context import require_full_experiment_asset_context

                    context = require_full_experiment_asset_context(session, context)
                from app.services.full_recovery_execution import has_full_recovery_binding

                if effect.action_type == "REDEEM_ASSET" and has_full_recovery_binding(
                    session, action
                ):
                    from app.services.execution_context import (
                        require_full_recovery_confirmation_context,
                    )

                    context = require_full_recovery_confirmation_context(session, context)
                from app.services.full_joint_goal_execution_guards import (
                    has_full_joint_goal_binding,
                )

                if dynamic_proof is not None and has_full_joint_goal_binding(session, action):
                    from app.services.execution_joint_goal_bridge import require_joint_goal_context

                    context = require_joint_goal_context(session, effect, context, dynamic_proof)
                elif dynamic_proof is not None:
                    from app.services.execution_context import (
                        require_full_dynamic_goal_proof_context,
                    )

                    context = require_full_dynamic_goal_proof_context(
                        session, effect, context, dynamic_proof
                    )
                confirmation = read_execution_confirmation(session, effect, now)
                validation = (
                    revalidate_execution(effect, context, confirmation=confirmation)
                    if dynamic_proof is None
                    else revalidate_execution(
                        effect,
                        context,
                        confirmation=confirmation,
                        full_dynamic_goal_proof=dynamic_proof,
                    )
                )
                if validation.status != "READY":
                    raise _error("Original execution facts changed before independent acceptance")
                enforce_full_execution_protection(engine, user_id, effect, context, validation, now)
            collision = session.scalar(
                select(BankOperation).where(
                    BankOperation.user_id == user_id,
                    BankOperation.business_key == effect.business_key,
                    BankOperation.status != "REJECTED",
                )
            )
            closing = effect.position_id if effect.action_type == "REDEEM_ASSET" else None
            if closing is not None:
                collision = collision or session.scalar(
                    select(BankOperation).where(
                        BankOperation.closing_position_id == closing,
                        BankOperation.status != "REJECTED",
                    )
                )
            if collision is not None:
                raise _error(
                    "This business effect already has an unresolved or completed bank operation"
                )
            available = now + timedelta(days=effect.settlement_delay_days)
            if effect.latest_arrival_at is not None and available > effect.latest_arrival_at:
                raise _error("Actual settlement would exceed the confirmed arrival upper bound")
            operation = BankOperation(
                id=effect.operation_id,
                user_id=user_id,
                created_at=now,
                action_plan_id=action_id,
                legacy_redemption_id=None,
                closing_position_id=closing,
                operation_type=effect.action_type,
                business_key=effect.business_key,
                idempotency_key=action.idempotency_key,
                request=payload,
                request_hash=digest,
                requested_at=now,
                available_at=available,
                settled_at=None,
                status="ACCEPTED",
            )
            session.add(operation)
            session.flush()
            from app.services.audit_recording import record_bank_accepted

            record_bank_accepted(session, operation, now)
        if operation.status == "ACCEPTED" and operation.available_at <= now:
            require_settlement_order(session, user_id, now, operation)
            _settle_operation(session, operation, effect, now)
        postings = list(
            session.scalars(
                select(SimulatedBankPosting.id)
                .where(SimulatedBankPosting.operation_id == operation.id)
                .order_by(SimulatedBankPosting.id)
            )
        )
        return BankOperationResult(
            operation_id=operation.id,
            action_id=action_id,
            status=operation.status,
            posting_ids=postings,
        )


def _error(message: str) -> PolicyLifecycleError:
    return PolicyLifecycleError("BANK_RECONCILIATION_REQUIRED", message, 409)


def _confirmation(session: Session, action: ActionPlan, digest: str, now: datetime) -> bool:
    try:
        proof = session.get(EvidenceItem, UUID(action.request["confirmation_evidence_id"]))
        if proof is None:
            return False
        content = proof.content
        confirmed = datetime.fromisoformat(content["confirmed_at"])
        until = datetime.fromisoformat(content["valid_until"])
        return (
            proof.user_id == action.user_id
            and proof.status == "VALID"
            and proof.evidence_level == "USER_CONFIRMED_ACTION"
            and proof.source_type == "USER_ACTION_CONFIRMATION"
            and proof.source_ref == str(action.id)
            and configuration_hash(content) == proof.content_hash
            and content.get("user_id") == str(action.user_id)
            and content.get("action_id") == str(action.id)
            and content.get("effect_hash") == digest
            and content.get("accepted") is True
            and confirmed.tzinfo is not None
            and until.tzinfo is not None
            and confirmed <= proof.observed_at <= now < until
            and proof.valid_from <= now
            and (proof.valid_to is None or now < proof.valid_to)
        )
    except (KeyError, ValueError, TypeError):
        return False


def _validate_new(
    session: Session, action: ActionPlan, effect: ExecutionEffect, digest: str, now: datetime
) -> FullDynamicGoalProof | None:
    from app.services.execution_sources import verify_execution_sources

    names = {
        "PURCHASE_ASSET": "ASSET_PURCHASE",
        "REDEEM_ASSET": "ASSET_REDEEM",
        "ALLOCATE_GOAL": "GOAL_ALLOCATE",
        "PAY_RECURRING": "PAY_RECURRING",
        "TRANSFER_INTERNAL": "TRANSFER_INTERNAL",
    }
    if (
        action.status != "SUBMITTED"
        or action.action_type not in {effect.action_type, names[effect.action_type]}
        or action.amount_cents != effect.amount_cents
        or not effect.valid_from <= now < effect.expires_at
        or action.goal_id != effect.goal_id
        or action.product_id != effect.product_id
    ):
        raise _error("The committed action is not the current bound economic request")
    confirmed = _confirmation(session, action, digest, now)
    if (
        effect.action_type == "TRANSFER_INTERNAL" or effect.fee_cents or effect.loss_cents
    ) and not confirmed:
        raise _error("This operation requires an exact one-shot user confirmation")
    if any(
        not is_version_authorized(session, action.user_id, identity, now)
        for identity in effect.policy_version_ids
    ):
        raise _error("A current policy reference is revoked, stale or inactive")
    if effect.action_type != "TRANSFER_INTERNAL" and not effect.policy_version_ids:
        raise _error("A policy-linked operation needs current authority")
    accounts: dict[UUID, Account] = {}
    for identity in {use.account_id for use in effect.cash_uses} | {
        item
        for item in (
            effect.destination_account_id,
            effect.position_account_id,
            effect.return_account_id,
        )
        if item is not None
    }:
        account = session.get(Account, identity)
        if (
            account is None
            or account.user_id != action.user_id
            or account.account_type == "CREDIT_CARD"
        ):
            raise _error("An economic account is not an owned cash or asset account")
        accounts[identity] = account
    if effect.action_type == "PURCHASE_ASSET":
        returned = (
            accounts.get(effect.return_account_id) if effect.return_account_id is not None else None
        )
        if returned is None or returned.account_type not in {"CASH", "GOAL"}:
            raise _error("A purchase needs an explicitly bound owned return account")
    source = effect.cash_uses[0].account_id if effect.cash_uses else effect.position_account_id
    destination = effect.destination_account_id if effect.destination_account_id != source else None
    if action.source_account_id != source or action.destination_account_id != destination:
        raise _error("The action accounts differ from the frozen economic identities")
    if action.policy_version_id != effect.policy_version_id:
        raise _error("The action policy differs from the immutable effect")
    if effect.action_type == "TRANSFER_INTERNAL" and any(
        use.account_id == effect.destination_account_id for use in effect.cash_uses
    ):
        raise _error("An internal transfer must actually change cash account")
    if effect.goal_id is not None:
        goal = session.get(Goal, effect.goal_id)
        if goal is None or goal.user_id != action.user_id:
            raise _error("Unknown goal ownership")
        if (
            effect.action_type in {"ALLOCATE_GOAL", "REDEEM_ASSET"}
            and goal.account_id != effect.destination_account_id
        ):
            raise _error("Goal principal or allocations must enter their original goal account")
        if effect.action_type == "PURCHASE_ASSET" and goal.account_id != effect.return_account_id:
            raise _error("A goal purchase must preserve its original goal return account")
    if (
        effect.action_type == "ALLOCATE_GOAL"
        and sum(use.amount_cents for use in effect.income_uses) != effect.amount_cents
    ):
        raise _error("Goal allocation must reserve its entire eligible income provenance")
    if effect.product_id is not None:
        product = session.get(AssetProduct, effect.product_id)
        if (
            product is None
            or product.version_number != effect.product_version_number
            or product.created_at > now
            or configuration_hash(product.maturity_rule) != effect.terms_digest
        ):
            raise _error("Original product terms or version disagree")
    if effect.action_type == "REDEEM_ASSET":
        position = session.get(AssetPosition, effect.position_id)
        if (
            position is None
            or position.user_id != action.user_id
            or position.status not in {"HELD", "MATURED"}
            or position.account_id != effect.position_account_id
            or position.product_id != effect.product_id
            or position.goal_id != effect.goal_id
            or position.policy_version_id != effect.original_policy_version_id
            or position.principal_cents != effect.amount_cents
            or action.position_id != position.id
        ):
            raise _error("The whole original position identity or principal changed")
    if (
        effect.action_type == "PURCHASE_ASSET"
        and session.get(AssetPosition, effect.position_id) is not None
    ):
        raise _error("A purchase cannot overwrite an existing position projection")
    if effect.action_type == "PAY_RECURRING":
        _liability_amounts(session, effect, now)
    validate_bank_projection(session, action.user_id, now, allow_unprojected=True)
    heads = ledger_heads(session, action.user_id)
    own = {
        (row.resource_kind, row.resource_key): row
        for row in session.scalars(
            select(ActionResourceReservation).where(
                ActionResourceReservation.action_plan_id == action.id,
                ActionResourceReservation.user_id == action.user_id,
                ActionResourceReservation.status == "RESERVED",
            )
        )
    }
    required = [("CASH", str(use.account_id), use.amount_cents) for use in effect.cash_uses]
    required += [("INCOME", str(use.fragment_id), use.amount_cents) for use in effect.income_uses]
    required += [("BUSINESS", effect.business_key, 1)]
    if effect.action_type == "REDEEM_ASSET":
        required.append(("POSITION", str(effect.position_id), effect.amount_cents))
    for kind, key, amount in required:
        claim = own.get((kind, key))
        if claim is None or claim.amount_cents != amount:
            raise _error("Execution lacks its exact durable resource reservation")
        prefix = {"CASH": "CASH:", "INCOME": "LOT_AVAILABLE:", "POSITION": "POSITION:"}.get(kind)
        if prefix is not None:
            head = heads.get(prefix + key)
            if head is None or head.occurred_at > now or head.balance_after_cents < amount:
                raise _error("The independent bank no longer has the reserved economic resource")
    return verify_execution_sources(session, action.user_id, effect, now)


def _opening(
    session: Session,
    user_id: UUID,
    key: str,
    amount: int,
    now: datetime,
    *,
    dimension: str = "ECONOMIC",
    account_id: UUID | None = None,
    position_id: UUID | None = None,
    metadata: dict[str, Any] | None = None,
    recorded_at: datetime | None = None,
) -> SimulatedBankPosting:
    """Explicit anchor only; callers supply verified import or a newly created zero ledger."""
    identity = uuid5(user_id, "execution-opening:" + key)
    row = session.scalar(
        select(SimulatedBankPosting).where(
            SimulatedBankPosting.user_id == user_id,
            SimulatedBankPosting.ledger_key == key,
            SimulatedBankPosting.sequence_number == 1,
        )
    )
    if row is not None:
        if row.delta_cents != amount or row.ledger_metadata != (metadata or {}):
            raise _error("An independent ledger anchor cannot be rewritten")
        return row
    row = SimulatedBankPosting(
        id=identity,
        user_id=user_id,
        created_at=recorded_at or now,
        ledger_key=key,
        ledger_dimension=dimension,
        ledger_metadata=metadata or {},
        account_id=account_id,
        position_id=position_id,
        redemption_id=None,
        operation_id=None,
        leg_ref=None,
        previous_posting_id=None,
        sequence_number=1,
        entry_kind="OPENING",
        balance_before_cents=0,
        delta_cents=amount,
        balance_after_cents=amount,
        occurred_at=now,
    )
    session.add(row)
    session.flush()
    return row


def _post(
    session: Session,
    operation: BankOperation,
    key: str,
    delta: int,
    now: datetime,
    leg: str,
    *,
    dimension: str = "ECONOMIC",
    account_id: UUID | None = None,
    position_id: UUID | None = None,
    metadata: dict[str, Any] | None = None,
    allow_zero_open: bool = False,
) -> None:
    if delta == 0:
        return
    head = ledger_heads(session, operation.user_id).get(key)
    if head is None and allow_zero_open:
        head = _opening(
            session,
            operation.user_id,
            key,
            0,
            operation.available_at,
            dimension=dimension,
            account_id=account_id,
            position_id=position_id,
            metadata=metadata,
            recorded_at=now,
        )
    if (
        head is None
        or head.balance_after_cents + delta < 0
        or head.occurred_at > operation.available_at
    ):
        raise _error("The bank cannot apply this posting to its independent economic chain")
    session.add(
        SimulatedBankPosting(
            id=uuid5(operation.id, "posting:" + leg),
            user_id=operation.user_id,
            created_at=now,
            ledger_key=key,
            ledger_dimension=head.ledger_dimension,
            ledger_metadata=head.ledger_metadata,
            account_id=head.account_id,
            position_id=head.position_id,
            redemption_id=None,
            operation_id=operation.id,
            leg_ref=leg,
            previous_posting_id=head.id,
            sequence_number=head.sequence_number + 1,
            entry_kind="DEBIT" if delta < 0 else "CREDIT",
            balance_before_cents=head.balance_after_cents,
            delta_cents=delta,
            balance_after_cents=head.balance_after_cents + delta,
            occurred_at=operation.available_at,
        )
    )
    session.flush()


def _settle_operation(
    session: Session, operation: BankOperation, effect: ExecutionEffect, now: datetime
) -> None:
    deltas = {use.account_id: -use.amount_cents for use in effect.cash_uses}
    if effect.destination_account_id is not None:
        credit = effect.net_cents if effect.action_type == "REDEEM_ASSET" else effect.amount_cents
        assert credit is not None
        deltas[effect.destination_account_id] = (
            deltas.get(effect.destination_account_id, 0) + credit
        )
    for account, delta in sorted(deltas.items()):
        _post(session, operation, f"CASH:{account}", delta, now, f"cash:{account}")
    if effect.action_type in {"PURCHASE_ASSET", "REDEEM_ASSET"}:
        assert effect.position_id is not None
        _post(
            session,
            operation,
            f"POSITION:{effect.position_id}",
            effect.amount_cents if effect.action_type == "PURCHASE_ASSET" else -effect.amount_cents,
            now,
            "position",
            position_id=effect.position_id,
            allow_zero_open=effect.action_type == "PURCHASE_ASSET",
        )
    if effect.action_type == "PAY_RECURRING":
        assert effect.payee_id is not None
        key = "PAYEE:" + str(uuid5(operation.user_id, effect.payee_id))
        _post(
            session,
            operation,
            key,
            effect.amount_cents,
            now,
            "payee",
            allow_zero_open=True,
            metadata={"payee_id": effect.payee_id},
        )
    for label, amount in (("FEE", effect.fee_cents), ("LOSS", effect.loss_cents)):
        if amount:
            _post(
                session,
                operation,
                f"{label}:{operation.user_id}",
                amount,
                now,
                label.lower(),
                allow_zero_open=True,
            )
    _goal_postings(session, operation, effect, now)
    _income_postings(session, operation, effect, now)
    if effect.action_type == "PAY_RECURRING":
        _liability_postings(session, operation, effect, now)
    economic = list(
        session.scalars(
            select(SimulatedBankPosting).where(
                SimulatedBankPosting.operation_id == operation.id,
                SimulatedBankPosting.ledger_dimension == "ECONOMIC",
            )
        )
    )
    if sum(row.delta_cents for row in economic) != 0:
        raise _error("Cash, principal, payee, fees and loss do not conserve the economic amount")
    operation.status, operation.settled_at = "SETTLED", operation.available_at
    session.flush()
    from app.services.audit_recording import record_bank_settled

    record_bank_settled(session, operation, now)


def _valid_bank_evidence(
    session: Session, user_id: UUID, identity: UUID, now: datetime
) -> EvidenceItem:
    proof = session.get(EvidenceItem, identity)
    if (
        proof is None
        or proof.user_id != user_id
        or proof.status != "VALID"
        or proof.evidence_level != "BANK_CONFIRMED"
        or proof.observed_at > now
        or proof.valid_from > now
        or (proof.valid_to is not None and proof.valid_to <= now)
        or configuration_hash(proof.content) != proof.content_hash
    ):
        raise _error("Liability requires valid currently known bank evidence")
    return proof


def _liability_amounts(session: Session, effect: ExecutionEffect, now: datetime) -> tuple[int, int]:
    liability = effect.liability
    total: int | None
    if liability is None:
        raise _error("Payment requires a bound liability")
    if liability.kind == "bill":
        bill = session.get(CreditCardBill, liability.bill_id)
        if (
            bill is None
            or bill.user_id != effect.user_id
            or bill.evidence_id not in liability.evidence_ids
            or effect.business_key != f"bill:{bill.id}"
            or effect.payee_id != f"credit-card:{bill.account_id}"
            or effect.payee_evidence_id != bill.evidence_id
        ):
            raise _error("Card liability, payee and business identity disagree")
        proof = _valid_bank_evidence(session, effect.user_id, bill.evidence_id, now)
        if (
            proof.source_type != "SIMULATED_CREDIT_CARD_BILL"
            or proof.content.get("bill_id") != str(bill.id)
            or proof.content.get("account_id") != str(bill.account_id)
            or proof.content.get("total_cents") != bill.total_cents
            or proof.content.get("paid_cents") != bill.paid_cents
        ):
            raise _error("Current card projection differs from original bank debt")
        total, paid = bill.total_cents, bill.paid_cents
    else:
        version = session.get(PolicyVersion, effect.policy_version_id)
        if (
            version is None
            or version.user_id != effect.user_id
            or version.policy_id != liability.policy_id
        ):
            raise _error("Recurring occurrence policy identity is invalid")
        rule = version.configuration["amount_rule"]
        total = liability.final_total_cents
        if total is None and rule["kind"] == "exact":
            total = rule["amount_cents"]
        if total is None or type(total) is not int or total <= 0:
            raise _error("A range cannot invent an actual payable total")
        if (rule["kind"] == "exact" and total != rule["amount_cents"]) or (
            rule["kind"] == "range" and not rule["min_cents"] <= total <= rule["max_cents"]
        ):
            raise _error("The actual payable total is outside its confirmed amount rule")
        rows = [
            _valid_bank_evidence(session, effect.user_id, identity, now)
            for identity in liability.evidence_ids
        ]
        rows = [
            row
            for row in rows
            if row.source_type == "SIMULATED_RECURRING_SETTLEMENT"
            and row.content.get("policy_id") == str(liability.policy_id)
            and row.content.get("period") == liability.period
        ]
        if len(rows) != 1 or type(rows[0].content.get("paid_cents")) is not int:
            raise _error("A recurring occurrence needs one complete paid-so-far fact")
        paid = rows[0].content["paid_cents"]
    if paid < 0 or total < paid or total - paid != effect.amount_cents:
        raise _error("Payment must settle this exact remaining liability once")
    return total, paid


def _liability_postings(
    session: Session, operation: BankOperation, effect: ExecutionEffect, now: datetime
) -> None:
    total, paid = _liability_amounts(session, effect, now)
    identity = uuid5(effect.user_id, "liability:" + effect.business_key)
    metadata = {
        "business_key": effect.business_key,
        "total_cents": total,
        "liability": effect.liability.model_dump(mode="json") if effect.liability else {},
    }
    for label, initial, delta in (
        ("LIABILITY_DUE", total - paid, -effect.amount_cents),
        ("LIABILITY_PAID", paid, effect.amount_cents),
    ):
        key = f"{label}:{identity}"
        _opening(
            session,
            effect.user_id,
            key,
            initial,
            operation.requested_at,
            dimension="LIABILITY",
            metadata=metadata,
        )
        _post(session, operation, key, delta, now, label.lower(), dimension="LIABILITY")


def _goal_postings(
    session: Session, operation: BankOperation, effect: ExecutionEffect, now: datetime
) -> None:
    if effect.goal_id is None:
        return
    goal = session.get(Goal, effect.goal_id)
    assert goal is not None
    cash, principal = 0, 0
    if effect.action_type == "ALLOCATE_GOAL":
        cash = effect.amount_cents
    elif effect.action_type == "PURCHASE_ASSET":
        cash, principal = -effect.amount_cents, effect.amount_cents
    elif effect.action_type == "REDEEM_ASSET":
        assert effect.net_cents is not None
        cash, principal = effect.net_cents, -effect.amount_cents
    for label, delta in (("GOAL_CASH", cash), ("GOAL_PRINCIPAL", principal)):
        _post(
            session,
            operation,
            f"{label}:{goal.id}",
            delta,
            now,
            label.lower(),
            dimension="GOAL_OWNERSHIP",
            account_id=goal.account_id,
        )
    if effect.fee_cents + effect.loss_cents:
        _post(
            session,
            operation,
            f"GOAL_LOSS:{goal.id}",
            effect.fee_cents + effect.loss_cents,
            now,
            "goal_loss",
            dimension="GOAL_OWNERSHIP",
            allow_zero_open=True,
        )


def _income_postings(
    session: Session, operation: BankOperation, effect: ExecutionEffect, now: datetime
) -> None:
    for use in effect.income_uses:
        key = f"LOT_AVAILABLE:{use.fragment_id}"
        head = ledger_heads(session, operation.user_id).get(key)
        if head is None or use.fragment_id != location_id(
            use.origin_transaction_id, use.account_id
        ):
            raise _error("Income location identity lacks an independent origin anchor")
        if head.ledger_metadata.get("origin", {}).get("origin_transaction_id") != str(
            use.origin_transaction_id
        ) or head.ledger_metadata.get("account_id") != str(use.account_id):
            raise _error("Income transfer cannot change its immutable original identity")
        _post(session, operation, key, -use.amount_cents, now, f"income_out:{use.fragment_id}")
        target = use.fragment_id
        metadata = dict(head.ledger_metadata)
        if effect.action_type == "TRANSFER_INTERNAL":
            assert effect.destination_account_id is not None
            target = location_id(use.origin_transaction_id, effect.destination_account_id)
            bucket, account = "AVAILABLE", effect.destination_account_id
            metadata["account_id"] = str(account)
        else:
            bucket = "ASSIGNED" if effect.action_type == "ALLOCATE_GOAL" else "SPENT"
            account = use.account_id
        _post(
            session,
            operation,
            f"LOT_{bucket}:{target}",
            use.amount_cents,
            now,
            f"income_in:{use.fragment_id}",
            dimension="INCOME_LOCATION",
            account_id=account,
            metadata=metadata,
            allow_zero_open=True,
        )


def open_execution_anchors(
    session: Session,
    user_id: UUID,
    as_of: datetime,
    *,
    goal_balances: dict[UUID, tuple[int, int]] | None = None,
    income_ledger: IncomeLedger | None = None,
) -> list[UUID]:
    """Trusted seed/import only: never bootstrap mutable projections during execution."""
    user = session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None or not user.is_simulated or as_of.tzinfo is None:
        raise _error("A trusted simulated opening owner and time are required")
    result: list[UUID] = []
    for goal_id, (cash, principal) in (goal_balances or {}).items():
        goal = session.get(Goal, goal_id)
        if goal is None or goal.user_id != user_id or min(cash, principal) < 0:
            raise _error("Invalid owned goal opening")
        for label, amount in (("GOAL_CASH", cash), ("GOAL_PRINCIPAL", principal)):
            result.append(
                _opening(
                    session,
                    user_id,
                    f"{label}:{goal_id}",
                    amount,
                    as_of,
                    dimension="GOAL_OWNERSHIP",
                    account_id=goal.account_id,
                    metadata={"goal_id": str(goal_id), "account_id": str(goal.account_id)},
                ).id
            )
    if income_ledger is not None:
        if income_ledger.user_id != user_id or income_ledger.as_of != as_of:
            raise _error("Income opening must bind the same user and source epoch")
        origins = {row.origin_transaction_id: row for row in income_ledger.origins}
        for origin in origins.values():
            transaction = session.get(Transaction, origin.origin_transaction_id)
            proof = _valid_bank_evidence(session, user_id, origin.bank_evidence_id, as_of)
            if (
                transaction is None
                or transaction.user_id != user_id
                or transaction.account_id != origin.origin_account_id
                or transaction.evidence_id != origin.bank_evidence_id
                or transaction.direction != "CREDIT"
                or transaction.amount_cents != origin.amount_cents
                or transaction.occurred_at != origin.occurred_at
                or transaction.observed_at != origin.observed_at
                or proof.content_hash != origin.bank_evidence_hash
                or proof.content.get("economic_role") != "INCOME"
            ):
                raise _error("Income opening does not match its independent bank origin")
            try:
                bank_fact_snapshot(transaction, proof)
            except ValueError as error:
                raise _error("An income origin is not a valid bound bank fact") from error
        for fragment in income_ledger.fragments:
            origin = origins[fragment.origin_transaction_id]
            metadata = {
                "origin": origin.model_dump(mode="json"),
                "account_id": str(fragment.account_id),
            }
            active = fragment.reserved_cents - fragment.legacy_reserved_cents
            values = {
                "AVAILABLE": fragment.available_cents + active,
                "SPENT": fragment.spent_cents,
                "ASSIGNED": fragment.assigned_cents,
                "RESERVED": fragment.legacy_reserved_cents,
            }
            for bucket, amount in values.items():
                result.append(
                    _opening(
                        session,
                        user_id,
                        f"LOT_{bucket}:{fragment.fragment_id}",
                        amount,
                        as_of,
                        dimension="INCOME_LOCATION",
                        account_id=fragment.account_id,
                        metadata=metadata,
                    ).id
                )
    return result


def validate_income_locations(
    session: Session, user_id: UUID, snapshot: dict[str, Any], now: datetime
) -> None:
    """Compare complete settled income locations; application claims do not become bank facts."""
    if snapshot.get("protocol") != "income-location-bank-v1" or snapshot.get("user_id") != str(
        user_id
    ):
        raise _error("Unknown complete income location snapshot")
    heads = {
        key: row
        for key, row in ledger_heads(session, user_id).items()
        if row.ledger_dimension == "INCOME_LOCATION"
    }
    origins = {row["origin_transaction_id"]: row for row in snapshot["origins"]}
    locations = snapshot["locations"]
    expected_ids = {str(row["fragment_id"]) for row in locations}
    actual_ids = {key.split(":", 1)[1] for key in heads}
    if expected_ids != actual_ids or len(expected_ids) != len(locations):
        raise _error("Complete bank income locations differ from the application source ledger")
    for location in locations:
        identity = location["fragment_id"]
        origin = origins[location["origin_transaction_id"]]
        metadata = {"origin": origin, "account_id": location["account_id"]}
        amounts = {
            "AVAILABLE": location["available_cents"] + location["active_reserved_cents"],
            "RESERVED": location["legacy_reserved_cents"],
            "SPENT": location["spent_cents"],
            "ASSIGNED": location["assigned_cents"],
        }
        for bucket, amount in amounts.items():
            row = heads.get(f"LOT_{bucket}:{identity}")
            if row is None and amount == 0:
                continue
            if (
                row is None
                or row.balance_after_cents != amount
                or row.ledger_metadata != metadata
                or row.occurred_at > now
            ):
                raise _error(
                    "Income origin identity, location or settled amount disagrees with bank facts"
                )
