"""Project immutable, independently settled generic bank operations exactly once."""

import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid5

from app.db.models import (
    Account,
    ActionPlan,
    ActionReceipt,
    AssetPosition,
    AssetProduct,
    BankOperation,
    CreditCardBill,
    EvidenceItem,
    Goal,
    SimulatedBankPosting,
    Transaction,
    User,
)
from app.domain.asset_allocation_types import FixedPrincipalTerms, PlannedPrincipalTerms
from app.domain.execution import ACTION_PLAN_TYPES
from app.domain.execution_types import BankCommand, ExecutionEffect
from app.domain.income_ledger import location_id
from app.domain.policy_configuration import configuration_hash
from app.services.boundary import (
    AVAILABILITY_SOURCE,
    CONTRIBUTION_SOURCE,
    OWNERSHIP_SOURCE,
    SETTLEMENT_SOURCE,
)
from app.services.execution_exposure import refresh_execution_exposure
from app.services.execution_reservations import resolve_resources
from app.services.income_ledger import commit_income_for_action, read_income_state
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.recovery_projection import current_proof, replace_proof
from app.services.simulated_bank import ledger_heads
from sqlalchemy import select
from sqlalchemy.orm import Session


def _error(message: str) -> PolicyLifecycleError:
    return PolicyLifecycleError("BANK_RECONCILIATION_REQUIRED", message, 409)


def _matches(content: dict[str, Any], expected: dict[str, Any]) -> bool:
    return all(key in content for key in expected) and configuration_hash(
        {key: content[key] for key in expected}
    ) == configuration_hash(expected)


def _proof(
    session: Session, user_id: UUID, source: str, key: str, identity: UUID, now: datetime
) -> EvidenceItem:
    proof = current_proof(session, user_id, source, key, identity)
    if (
        proof.evidence_level != "BANK_CONFIRMED"
        or proof.observed_at > now
        or proof.valid_from > now
        or (proof.valid_to is not None and proof.valid_to <= now)
        or proof.content.get("simulation") is not True
        or proof.content.get("user_id") != str(user_id)
    ):
        raise _error("A current projection has no intact known bank source")
    return proof


def _identity(
    session: Session, operation: BankOperation, now: datetime, *, lock_user: bool = True
) -> tuple[ActionPlan, ExecutionEffect]:
    query = select(User).where(User.id == operation.user_id)
    user = session.scalar(query.with_for_update() if lock_user else query)
    action = session.get(ActionPlan, operation.action_plan_id)
    if user is None or not user.is_simulated or action is None or action.user_id != user.id:
        raise _error("The bank operation has no matching simulated action owner")
    try:
        command = BankCommand.model_validate_json(json.dumps(operation.request))
        effect = command.effect
        if (
            operation.request_hash != configuration_hash(operation.request)
            or action.request_hash != configuration_hash(action.request)
            or action.request.get("execution") != operation.request
            or effect.operation_id != operation.id
            or operation.id != action.id
            or effect.user_id != user.id
            or operation.operation_type != effect.action_type
            or operation.business_key != effect.business_key
            or operation.idempotency_key != action.idempotency_key
            or action.action_type != ACTION_PLAN_TYPES[effect.action_type]
            or action.amount_cents != effect.amount_cents
            or action.goal_id != effect.goal_id
            or action.product_id != effect.product_id
            or action.policy_version_id != effect.policy_version_id
            or action.source_account_id
            != (effect.cash_uses[0].account_id if effect.cash_uses else effect.position_account_id)
            or (
                effect.action_type == "PURCHASE_ASSET"
                and action.position_id not in {None, effect.position_id}
            )
            or (effect.action_type != "PURCHASE_ASSET" and action.position_id != effect.position_id)
            or action.destination_account_id
            != (
                effect.destination_account_id
                if effect.destination_account_id != action.source_account_id
                else None
            )
            or operation.requested_at > now
            or operation.available_at < operation.requested_at
        ):
            raise ValueError("Application and independent bank identities disagree")
    except (KeyError, TypeError, ValueError) as error:
        raise _error(str(error)) from error
    return action, effect


def _legs(
    session: Session, operation: BankOperation, effect: ExecutionEffect, now: datetime
) -> list[SimulatedBankPosting]:
    ledger_heads(session, operation.user_id)
    rows = list(
        session.scalars(
            select(SimulatedBankPosting).where(SimulatedBankPosting.operation_id == operation.id)
        )
    )
    expected = {use.account_id: -use.amount_cents for use in effect.cash_uses}
    if effect.destination_account_id is not None:
        credit = effect.net_cents if effect.action_type == "REDEEM_ASSET" else effect.amount_cents
        assert credit is not None
        expected[effect.destination_account_id] = (
            expected.get(effect.destination_account_id, 0) + credit
        )
    specs = {
        f"cash:{key}": (f"CASH:{key}", "ECONOMIC", value)
        for key, value in expected.items()
        if value
    }
    if effect.action_type in {"PURCHASE_ASSET", "REDEEM_ASSET"}:
        specs["position"] = (
            f"POSITION:{effect.position_id}",
            "ECONOMIC",
            effect.amount_cents if effect.action_type == "PURCHASE_ASSET" else -effect.amount_cents,
        )
    if effect.action_type == "PAY_RECURRING":
        specs["payee"] = (
            "PAYEE:" + str(uuid5(effect.user_id, str(effect.payee_id))),
            "ECONOMIC",
            effect.amount_cents,
        )
        liability_id = uuid5(effect.user_id, "liability:" + effect.business_key)
        specs["liability_due"] = (
            f"LIABILITY_DUE:{liability_id}",
            "LIABILITY",
            -effect.amount_cents,
        )
        specs["liability_paid"] = (
            f"LIABILITY_PAID:{liability_id}",
            "LIABILITY",
            effect.amount_cents,
        )
    for label, value in (("FEE", effect.fee_cents), ("LOSS", effect.loss_cents)):
        if value:
            specs[label.lower()] = (f"{label}:{effect.user_id}", "ECONOMIC", value)
    if effect.goal_id is not None:
        goal_cash = (
            effect.amount_cents
            if effect.action_type == "ALLOCATE_GOAL"
            else -effect.amount_cents
            if effect.action_type == "PURCHASE_ASSET"
            else effect.net_cents
        )
        principal = (
            effect.amount_cents
            if effect.action_type == "PURCHASE_ASSET"
            else -effect.amount_cents
            if effect.action_type == "REDEEM_ASSET"
            else 0
        )
        for label, goal_value in (
            ("GOAL_CASH", goal_cash),
            ("GOAL_PRINCIPAL", principal),
            ("GOAL_LOSS", effect.fee_cents + effect.loss_cents),
        ):
            if goal_value:
                specs[label.lower()] = (f"{label}:{effect.goal_id}", "GOAL_OWNERSHIP", goal_value)
    for use in effect.income_uses:
        specs[f"income_out:{use.fragment_id}"] = (
            f"LOT_AVAILABLE:{use.fragment_id}",
            "INCOME_LOCATION",
            -use.amount_cents,
        )
        target = (
            location_id(use.origin_transaction_id, effect.destination_account_id)
            if effect.action_type == "TRANSFER_INTERNAL"
            and effect.destination_account_id is not None
            else use.fragment_id
        )
        bucket = (
            "AVAILABLE"
            if effect.action_type == "TRANSFER_INTERNAL"
            else "ASSIGNED"
            if effect.action_type == "ALLOCATE_GOAL"
            else "SPENT"
        )
        specs[f"income_in:{use.fragment_id}"] = (
            f"LOT_{bucket}:{target}",
            "INCOME_LOCATION",
            use.amount_cents,
        )
    actual = {row.leg_ref: (row.ledger_key, row.ledger_dimension, row.delta_cents) for row in rows}
    if (
        actual != specs
        or not rows
        or operation.settled_at is None
        or operation.settled_at > now
        or operation.settled_at < operation.available_at
        or sum(row.delta_cents for row in rows if row.ledger_dimension == "ECONOMIC") != 0
        or len({row.leg_ref for row in rows}) != len(rows)
        or any(
            row.user_id != operation.user_id
            or row.id != uuid5(operation.id, "posting:" + str(row.leg_ref))
            or row.occurred_at != operation.settled_at
            for row in rows
        )
    ):
        raise _error("Independent settlement legs do not bind the exact complete cash effect")
    return rows


def _cash_preflight(
    session: Session, operation: BankOperation, rows: list[SimulatedBankPosting], now: datetime
) -> list[tuple[Account, EvidenceItem, SimulatedBankPosting]]:
    result = []
    for row in rows:
        if not row.ledger_key.startswith("CASH:"):
            continue
        account = session.get(Account, row.account_id)
        if (
            account is None
            or account.user_id != operation.user_id
            or account.account_type == "CREDIT_CARD"
            or row.ledger_key != f"CASH:{account.id}"
            or row.leg_ref != f"cash:{account.id}"
            or row.ledger_dimension != "ECONOMIC"
            or account.balance_cents != row.balance_before_cents
            or account.observed_at > now
        ):
            raise _error("Cash projection differs from the bank's previous balance")
        proof = _proof(
            session, operation.user_id, "SIMULATED_BANK_BALANCE", "account_id", account.id, now
        )
        if not _matches(
            proof.content,
            {
                "balance_cents": account.balance_cents,
                "account_type": account.account_type,
                "currency": account.currency,
                "as_of": account.observed_at.isoformat(),
            },
        ):
            raise _error("Cash projection does not match its previous bank proof")
        result.append((account, proof, row))
    return result


def _cash_transaction(
    session: Session, effect: ExecutionEffect, row: SimulatedBankPosting, now: datetime
) -> UUID:
    first_purchase = (
        effect.action_type == "PURCHASE_ASSET" and row.account_id == effect.cash_uses[0].account_id
    )
    identity = uuid5(
        effect.operation_id,
        "transaction:purchase" if first_purchase else f"transaction:{row.leg_ref}",
    )
    proof_id = uuid5(identity, "evidence")
    source_ref = f"bank-operation:{effect.operation_id}:{row.leg_ref}"
    role = (
        "ASSET_PURCHASE"
        if effect.action_type == "PURCHASE_ASSET"
        else "PRINCIPAL_RETURN"
        if effect.action_type == "REDEEM_ASSET"
        else "CREDIT_CARD_PAYMENT"
        if effect.liability is not None and effect.liability.kind == "bill"
        else "CONSUMPTION"
        if effect.action_type == "PAY_RECURRING"
        else "INTERNAL_TRANSFER"
    )
    counterparty = effect.payee_id or (
        f"position:{effect.position_id}"
        if effect.position_id
        else f"operation:{effect.operation_id}"
    )
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
        "economic_role": role,
        "bank_operation_id": str(effect.operation_id),
        "bank_posting_id": str(row.id),
    }
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
            category=role.lower(),
            category_confirmed=False,
            counterparty_ref=counterparty,
            is_one_off=False,
            occurred_at=row.occurred_at,
            observed_at=now,
        )
    )
    return identity


def _new_proof(
    session: Session,
    user_id: UUID,
    identity: UUID,
    source: str,
    content: dict[str, Any],
    now: datetime,
) -> EvidenceItem:
    proof = EvidenceItem(
        id=identity,
        user_id=user_id,
        created_at=now,
        evidence_level="BANK_CONFIRMED",
        source_type=source,
        source_ref=f"bank-projection:{identity}",
        content=content,
        content_hash=configuration_hash(content),
        valid_from=now,
        observed_at=now,
        status="VALID",
    )
    session.add(proof)
    session.flush()
    return proof


def _position_payload(position: AssetPosition, now: datetime) -> dict[str, Any]:
    return {
        "simulation": True,
        "user_id": str(position.user_id),
        "position_id": str(position.id),
        "account_id": str(position.account_id),
        "product_id": str(position.product_id),
        "goal_id": str(position.goal_id) if position.goal_id else None,
        "policy_version_id": str(position.policy_version_id)
        if position.policy_version_id
        else None,
        "principal_cents": position.principal_cents,
        "purchased_at": position.purchased_at.isoformat(),
        "maturity_at": position.maturity_at.isoformat() if position.maturity_at else None,
        "available_at": position.available_at.isoformat() if position.available_at else None,
        "status": position.status,
        "as_of": now.isoformat(),
    }


def _goal_preflight(
    session: Session, effect: ExecutionEffect, rows: list[SimulatedBankPosting], now: datetime
) -> list[tuple[Goal, EvidenceItem]]:
    result = []
    for goal in session.scalars(select(Goal).where(Goal.user_id == effect.user_id)):
        proof = _proof(session, effect.user_id, OWNERSHIP_SOURCE, "goal_id", goal.id, now)
        positions = list(
            session.scalars(
                select(AssetPosition).where(
                    AssetPosition.user_id == effect.user_id,
                    AssetPosition.goal_id == goal.id,
                    AssetPosition.status != "REDEEMED",
                )
            )
        )
        principal = sum(p.principal_cents for p in positions)
        cash = goal.allocated_cents - principal
        expected = {
            "protocol": "goal-ownership-v1",
            "policy_id": str(goal.policy_id),
            "account_id": str(goal.account_id) if goal.account_id else None,
            "allocated_cents": goal.allocated_cents,
            "cash_owned_cents": cash,
            "principal_owned_cents": principal,
            "position_ids": sorted(str(p.id) for p in positions),
        }
        if cash < 0 or not _matches(proof.content, expected):
            raise _error("Goal ownership changed independently of its previous complete proof")
        if goal.id == effect.goal_id:
            if (
                effect.action_type in {"ALLOCATE_GOAL", "REDEEM_ASSET"}
                and goal.account_id != effect.destination_account_id
            ):
                raise _error("Goal proceeds must return to the original owned account")
            for name, before in (("goal_cash", cash), ("goal_principal", principal)):
                leg = next((row for row in rows if row.leg_ref == name), None)
                if leg is not None and (
                    leg.balance_before_cents != before or leg.account_id != goal.account_id
                ):
                    raise _error("Goal projection differs from independent pre-effect ownership")
        result.append((goal, proof))
    if effect.goal_id is not None and not any(g.id == effect.goal_id for g, _ in result):
        raise _error("The original goal no longer exists")
    return result


def _goal_apply(
    session: Session,
    effect: ExecutionEffect,
    operation: BankOperation,
    goals: list[tuple[Goal, EvidenceItem]],
    rows: list[SimulatedBankPosting],
    now: datetime,
) -> None:
    user = session.get(User, effect.user_id)
    assert user is not None and operation.settled_at is not None
    zone = UTC if user.timezone == "UTC" else timezone(timedelta(hours=8))
    period = operation.settled_at.astimezone(zone).strftime("%Y-%m")
    for goal, proof in goals:
        cash, principal = proof.content["cash_owned_cents"], proof.content["principal_owned_cents"]
        if goal.id == effect.goal_id:
            cash_leg = next((row for row in rows if row.leg_ref == "goal_cash"), None)
            principal_leg = next((row for row in rows if row.leg_ref == "goal_principal"), None)
            if cash_leg is not None:
                cash = cash_leg.balance_after_cents
            if principal_leg is not None:
                principal = principal_leg.balance_after_cents
            goal.allocated_cents = cash + principal
        positions = list(
            session.scalars(
                select(AssetPosition).where(
                    AssetPosition.user_id == effect.user_id,
                    AssetPosition.goal_id == goal.id,
                    AssetPosition.status != "REDEEMED",
                )
            )
        )
        if sum(p.principal_cents for p in positions) != principal:
            raise _error("Goal bank principal and materialized application positions disagree")
        replace_proof(
            session,
            proof,
            {
                **proof.content,
                "as_of": now.isoformat(),
                "allocated_cents": goal.allocated_cents,
                "cash_owned_cents": cash,
                "principal_owned_cents": principal,
                "position_ids": sorted(str(p.id) for p in positions),
            },
            now,
            operation.id,
        )
        statements = list(
            session.scalars(
                select(EvidenceItem).where(
                    EvidenceItem.user_id == effect.user_id,
                    EvidenceItem.source_type == CONTRIBUTION_SOURCE,
                    EvidenceItem.content["goal_id"].as_string() == str(goal.id),
                    EvidenceItem.status != "SUPERSEDED",
                )
            )
        )
        current_month = [s for s in statements if s.content.get("period") == period]
        if (
            effect.action_type == "ALLOCATE_GOAL"
            and goal.id == effect.goal_id
            and len(current_month) != 1
        ):
            raise _error("A contribution cannot invent a missing complete monthly statement")
        seen = set()
        for statement in statements:
            if (
                statement.status != "VALID"
                or statement.evidence_level != "BANK_CONFIRMED"
                or statement.content_hash != configuration_hash(statement.content)
                or statement.observed_at > now
                or statement.content.get("complete") is not True
                or statement.content.get("user_id") != str(effect.user_id)
                or statement.content.get("protocol") != "goal-month-contribution-v1"
                or statement.content.get("period") in seen
                or type(statement.content.get("contributed_cents")) is not int
                or statement.content["contributed_cents"] < 0
            ):
                raise _error("Monthly contribution evidence cannot be repaired by projection")
            seen.add(statement.content["period"])
            amount = statement.content["contributed_cents"]
            if (
                goal.id == effect.goal_id
                and effect.action_type == "ALLOCATE_GOAL"
                and statement in current_month
            ):
                amount += effect.amount_cents
            replace_proof(
                session,
                statement,
                {**statement.content, "as_of": now.isoformat(), "contributed_cents": amount},
                now,
                operation.id,
            )


def _purchase(
    session: Session,
    action: ActionPlan,
    effect: ExecutionEffect,
    operation: BankOperation,
    rows: list[SimulatedBankPosting],
    transactions: list[UUID],
    now: datetime,
) -> None:
    if effect.position_id is None or effect.position_account_id is None:
        raise _error("A purchase requires its exact future position identity")
    if session.get(AssetPosition, effect.position_id) is not None:
        raise _error("A purchase cannot overwrite an existing application position")
    leg = next(row for row in rows if row.leg_ref == "position")
    product = session.get(AssetProduct, effect.product_id)
    if (
        leg.balance_before_cents != 0
        or leg.balance_after_cents != effect.amount_cents
        or product is None
        or product.version_number != effect.product_version_number
        or configuration_hash(product.maturity_rule) != effect.terms_digest
    ):
        raise _error("The original purchase principal or exact product terms changed")
    try:
        fixed = product.maturity_rule.get("protocol") == "fixed-principal-return-v1"
        terms = (
            FixedPrincipalTerms.model_validate(product.maturity_rule)
            if fixed
            else PlannedPrincipalTerms.model_validate(product.maturity_rule)
        )
    except ValueError as error:
        raise _error("The purchased contract has no explicit principal return protocol") from error
    assert operation.settled_at is not None
    maturity = (
        operation.settled_at + timedelta(days=terms.term_days)
        if isinstance(terms, FixedPrincipalTerms)
        else None
    )
    available = maturity + timedelta(days=terms.settlement_delay_days) if maturity else None
    exit_plan = effect.purchase_exit
    if exit_plan is None or exit_plan.terms_digest != effect.terms_digest:
        raise _error("The bank purchase lost its original bound exit plan")
    if available is not None and (
        exit_plan.kind != "FIXED_MATURITY"
        or available > exit_plan.principal_available_at
        or effect.latest_arrival_at is None
        or available > effect.latest_arrival_at
    ):
        raise _error("Actual contractual maturity exceeds the immutable purchase bound")
    if available is None and exit_plan.kind != "PLANNED_REDEMPTION":
        raise _error("An unrequested redemption cannot become contractual maturity")
    position = AssetPosition(
        id=effect.position_id,
        user_id=effect.user_id,
        created_at=now,
        account_id=effect.position_account_id,
        product_id=effect.product_id,
        goal_id=effect.goal_id,
        policy_version_id=effect.policy_version_id,
        principal_cents=effect.amount_cents,
        accrued_yield_cents=0,
        purchased_at=operation.settled_at,
        maturity_at=maturity,
        available_at=available,
        status="HELD",
    )
    session.add(position)
    session.flush()
    action.position_id = position.id
    _new_proof(
        session,
        effect.user_id,
        uuid5(operation.id, "position-evidence"),
        "SIMULATED_BANK_POSITION",
        {
            **_position_payload(position, now),
            "acquisition": "synthetic_auto_purchase",
            "acquisition_protocol": "execution-purchase-v1",
            "purchase_exit_plan": exit_plan.model_dump(mode="json"),
            "purchase_exit_status": "CONTRACTUAL_MATURITY"
            if available is not None
            else "UNSUBMITTED_PLAN",
            "purchase_transaction_id": str(uuid5(action.id, "transaction:purchase")),
            "purchase_transaction_ids": sorted(str(identity) for identity in transactions),
            "purchase_action_id": str(action.id),
            "purchase_receipt_id": str(uuid5(operation.id, "receipt")),
            "return_account_id": str(effect.return_account_id),
            "bank_operation_id": str(operation.id),
            "bank_posting_id": str(leg.id),
        },
        now,
    )
    if available is not None:
        _new_proof(
            session,
            effect.user_id,
            uuid5(operation.id, "availability"),
            AVAILABILITY_SOURCE,
            {
                "simulation": True,
                "user_id": str(effect.user_id),
                "protocol": "principal-availability-v1",
                "position_id": str(position.id),
                "account_id": str(position.account_id),
                "goal_id": str(position.goal_id) if position.goal_id else None,
                "principal_cents": position.principal_cents,
                "available_at": available.isoformat(),
                "principal_return_bps": 10000,
                "rollover": False,
                "bank_operation_id": str(operation.id),
            },
            now,
        )


def _redeem(
    session: Session,
    effect: ExecutionEffect,
    operation: BankOperation,
    rows: list[SimulatedBankPosting],
    now: datetime,
) -> None:
    position = session.get(AssetPosition, effect.position_id)
    leg = next(row for row in rows if row.leg_ref == "position")
    if (
        position is None
        or position.user_id != effect.user_id
        or position.account_id != effect.position_account_id
        or position.product_id != effect.product_id
        or position.goal_id != effect.goal_id
        or position.policy_version_id != effect.original_policy_version_id
        or position.principal_cents != effect.amount_cents
        or position.status not in {"HELD", "MATURED", "REDEEMING"}
        or leg.balance_before_cents != position.principal_cents
        or leg.balance_after_cents != 0
        or operation.settled_at is None
    ):
        raise _error("The original whole position differs from the independent closure")
    proof = _proof(
        session, effect.user_id, "SIMULATED_BANK_POSITION", "position_id", position.id, now
    )
    expected = _position_payload(position, proof.observed_at)
    if not _matches(proof.content, expected):
        raise _error("Original position proof cannot be repaired by its principal return")
    position.status, position.available_at = "REDEEMED", operation.settled_at
    replace_proof(
        session,
        proof,
        {
            **proof.content,
            **_position_payload(position, now),
            "redemption_bank_operation_id": str(operation.id),
            "bank_posting_id": str(leg.id),
        },
        now,
        operation.id,
    )
    for old in session.scalars(
        select(EvidenceItem).where(
            EvidenceItem.user_id == effect.user_id,
            EvidenceItem.source_type == AVAILABILITY_SOURCE,
            EvidenceItem.content["position_id"].as_string() == str(position.id),
            EvidenceItem.status != "SUPERSEDED",
        )
    ):
        old.status = "SUPERSEDED"
    session.flush()


def _payment(
    session: Session,
    effect: ExecutionEffect,
    operation: BankOperation,
    rows: list[SimulatedBankPosting],
    now: datetime,
) -> None:
    liability = effect.liability
    assert liability is not None
    due = next(row for row in rows if row.leg_ref == "liability_due")
    paid = next(row for row in rows if row.leg_ref == "liability_paid")
    total = due.balance_before_cents + paid.balance_before_cents
    metadata = {
        "business_key": effect.business_key,
        "total_cents": total,
        "liability": liability.model_dump(mode="json"),
    }
    if due.ledger_metadata != metadata or paid.ledger_metadata != metadata:
        raise _error("The bank settlement has a different original liability identity")
    if liability.kind == "bill":
        bill = session.get(CreditCardBill, liability.bill_id)
        if (
            bill is None
            or bill.user_id != effect.user_id
            or bill.evidence_id not in liability.evidence_ids
            or bill.total_cents != total
            or bill.paid_cents != paid.balance_before_cents
            or effect.payee_id != f"credit-card:{bill.account_id}"
            or effect.business_key != f"bill:{bill.id}"
        ):
            raise _error("The bill changed independently of this bank repayment")
        proof = _proof(
            session, effect.user_id, "SIMULATED_CREDIT_CARD_BILL", "bill_id", bill.id, now
        )
        expected = {
            "account_id": str(bill.account_id),
            "source_ref": bill.source_ref,
            "total_cents": bill.total_cents,
            "minimum_due_cents": bill.minimum_due_cents,
            "paid_cents": bill.paid_cents,
            "status": bill.status,
            "statement_date": bill.statement_date.isoformat(),
            "due_date": bill.due_date.isoformat(),
        }
        if proof.id != bill.evidence_id or not _matches(proof.content, expected):
            raise _error("Original bill source differs from the current bill projection")
        bill.paid_cents = paid.balance_after_cents
        bill.status = "PAID" if bill.paid_cents == bill.total_cents else "PARTIALLY_PAID"
        proof = replace_proof(
            session,
            proof,
            {
                **proof.content,
                "paid_cents": bill.paid_cents,
                "status": bill.status,
                "as_of": now.isoformat(),
                "bank_operation_id": str(operation.id),
            },
            now,
            operation.id,
        )
        bill.evidence_id = proof.id
    else:
        matches = list(
            session.scalars(
                select(EvidenceItem).where(
                    EvidenceItem.user_id == effect.user_id,
                    EvidenceItem.source_type == SETTLEMENT_SOURCE,
                    EvidenceItem.content["policy_id"].as_string() == str(liability.policy_id),
                    EvidenceItem.content["period"].as_string() == liability.period,
                    EvidenceItem.status != "SUPERSEDED",
                )
            )
        )
        if len(matches) != 1:
            raise _error("A recurring settlement needs its unique complete original statement")
        proof = matches[0]
        if (
            proof.id not in liability.evidence_ids
            or proof.status != "VALID"
            or proof.evidence_level != "BANK_CONFIRMED"
            or proof.observed_at > now
            or proof.content_hash != configuration_hash(proof.content)
            or proof.content.get("complete") is not True
            or proof.content.get("simulation") is not True
            or proof.content.get("user_id") != str(effect.user_id)
            or proof.content.get("protocol") != "recurring-settlement-v1"
            or proof.content.get("payee_id") != effect.payee_id
            or type(proof.content.get("paid_cents")) is not int
            or proof.content.get("paid_cents") != paid.balance_before_cents
            or (liability.final_total_cents is not None and liability.final_total_cents != total)
        ):
            raise _error("Recurring paid or final payable amount changed after bank submission")
        replace_proof(
            session,
            proof,
            {
                **proof.content,
                "paid_cents": paid.balance_after_cents,
                "as_of": now.isoformat(),
                "bank_operation_id": str(operation.id),
            },
            now,
            operation.id,
        )


def verify_execution_receipt(
    session: Session, operation: BankOperation, receipt: ActionReceipt, now: datetime
) -> None:
    """Read-only historical verification; never reauthorizes or repairs an economic effect."""
    action, effect = _identity(session, operation, now, lock_user=False)
    rows = _legs(session, operation, effect, now)
    posting_ids = receipt.response.get("posting_ids")
    if (
        operation.status != "SETTLED"
        or action.status not in {"SUCCEEDED", "RECONCILED"}
        or receipt.id != uuid5(operation.id, "receipt")
        or receipt.user_id != operation.user_id
        or receipt.action_plan_id != action.id
        or receipt.status != "SUCCEEDED"
        or receipt.attempt_number != 1
        or receipt.receipt_ref != f"bank-operation:{operation.id}"
        or receipt.executed_cents != effect.amount_cents
        or receipt.fee_cents != effect.fee_cents
        or receipt.loss_cents != effect.loss_cents
        or receipt.response.get("bank_operation_id") != str(operation.id)
        or not isinstance(posting_ids, list)
        or any(not isinstance(key, str) for key in posting_ids)
        or len(posting_ids) != len(rows)
        or set(posting_ids) != {str(row.id) for row in rows}
        or receipt.occurred_at != operation.settled_at
        or receipt.reconciled_at is None
        or receipt.reconciled_at < receipt.occurred_at
        or receipt.reconciled_at > now
        or receipt.created_at != receipt.reconciled_at
    ):
        raise _error("A previous receipt cannot be replaced or rebound")


def project_execution(
    session: Session, operation: BankOperation, now: datetime
) -> ActionReceipt | None:
    """One savepoint keeps a rejected projection atomic even if its caller handles the error."""
    with session.begin_nested():
        return _project_execution(session, operation, now)


def _project_execution(
    session: Session, operation: BankOperation, now: datetime
) -> ActionReceipt | None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise _error("Projection requires an aware server clock")
    now = now.astimezone(UTC)
    action, effect = _identity(session, operation, now)
    if operation.status == "ACCEPTED":
        return None
    if operation.status != "SETTLED":
        raise _error("Only independently settled facts can become application balances")
    rows = _legs(session, operation, effect, now)
    receipts = list(
        session.scalars(select(ActionReceipt).where(ActionReceipt.action_plan_id == action.id))
    )
    if receipts:
        if len(receipts) != 1:
            raise _error("A previous receipt cannot be replaced or rebound")
        verify_execution_receipt(session, operation, receipts[0], now)
        return receipts[0]
    cash = _cash_preflight(session, operation, rows, now)
    goals = _goal_preflight(session, effect, rows, now)
    income_proof = None
    if not effect.income_uses:
        try:
            state = read_income_state(session, operation.user_id, now)
            income_proof = session.get(EvidenceItem, state.evidence_id)
        except PolicyLifecycleError as error:
            if error.code != "MISSING_NEW_FUNDS_LEDGER":
                raise
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
        transactions.append(_cash_transaction(session, effect, row, now))
    session.flush()
    if effect.action_type == "PURCHASE_ASSET":
        _purchase(session, action, effect, operation, rows, transactions, now)
    elif effect.action_type == "REDEEM_ASSET":
        _redeem(session, effect, operation, rows, now)
    elif effect.action_type == "PAY_RECURRING":
        _payment(session, effect, operation, rows, now)
    _goal_apply(session, effect, operation, goals, rows, now)
    if effect.income_uses:
        commit_income_for_action(session, operation.user_id, action.id, now)
    elif income_proof is not None:
        replace_proof(
            session,
            income_proof,
            {**income_proof.content, "as_of": now.isoformat()},
            now,
            operation.id,
        )
    receipt = ActionReceipt(
        id=uuid5(operation.id, "receipt"),
        user_id=operation.user_id,
        created_at=now,
        action_plan_id=action.id,
        attempt_number=1,
        receipt_ref=f"bank-operation:{operation.id}",
        status="SUCCEEDED",
        executed_cents=effect.amount_cents,
        fee_cents=effect.fee_cents,
        loss_cents=effect.loss_cents,
        response={
            "bank_operation_id": str(operation.id),
            "posting_ids": sorted(str(row.id) for row in rows),
            "transaction_ids": sorted(str(identity) for identity in transactions),
        },
        occurred_at=operation.settled_at,
        reconciled_at=now,
    )
    session.add(receipt)
    action.status = "SUCCEEDED"
    resolve_resources(session, operation.user_id, action.id, "CONSUMED", now)
    refresh_execution_exposure(session, operation.user_id, now, operation.id)
    session.flush()
    from app.services.audit_recording import record_action_projected

    record_action_projected(session, action, operation, receipt, now)
    return receipt
