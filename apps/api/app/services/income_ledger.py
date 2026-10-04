"""Verified income locations and transaction-scoped application reservation projections."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid5

from app.db.models import Account, ActionPlan, BankOperation, EvidenceItem, Goal, Transaction, User
from app.domain.execution import ACTION_PLAN_TYPES, execution_effect_hash
from app.domain.execution_types import BankCommand
from app.domain.goal_allocation import IncomeLot
from app.domain.history_coverage import bank_fact_snapshot
from app.domain.income_ledger import (
    LEDGER_SOURCE,
    IncomeFragment,
    IncomeLedger,
    IncomeOperation,
    IncomeOrigin,
    IncomeUse,
    LedgerPayload,
    bank_location_snapshot,
    commit_income,
    location_id,
    release_income,
    reserve_income,
)
from app.domain.policy_configuration import configuration_hash
from app.services.boundary import Sources
from app.services.execution_bank import validate_income_locations
from app.services.policy_lifecycle import PolicyLifecycleError
from sqlalchemy import select
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class IncomeLedgerState:
    ledger: IncomeLedger
    evidence_id: UUID
    evidence_hash: str


def read_income_state(session: Session, user_id: UUID, now: datetime) -> IncomeLedgerState:
    ledger, proof = _read(session, user_id, now)
    return IncomeLedgerState(ledger, proof.id, proof.content_hash)


def read_income_ledger(session: Session, user_id: UUID, now: datetime) -> IncomeLedger:
    """Read a complete v1/v2 ledger without changing any bank or application fact."""
    return read_income_state(session, user_id, now).ledger


def income_lots_for_action(
    session: Session,
    user_id: UUID,
    now: datetime,
    *,
    action_id: UUID | None = None,
) -> list[IncomeLot]:
    """Read-only revalidation view; only the same reserved action sees its own claim again."""
    state = read_income_state(session, user_id, now)
    ledger = state.ledger
    restored: dict[UUID, int] = {}
    if action_id is not None:
        _, command = _action(session, user_id, action_id)
        _reservation_matches(ledger, action_id, command)
        reservation = next(item for item in ledger.reservations if item.action_id == action_id)
        if reservation.state == "RESERVED":
            restored = {use.fragment_id: use.amount_cents for use in reservation.uses}
    origins = {item.origin_transaction_id: item for item in ledger.origins}
    return [
        IncomeLot(
            origin_transaction_id=item.origin_transaction_id,
            account_id=item.account_id,
            fragment_id=item.fragment_id,
            amount_cents=origins[item.origin_transaction_id].amount_cents,
            available_cents=item.available_cents + restored.get(item.fragment_id, 0),
            occurred_at=origins[item.origin_transaction_id].occurred_at,
            observed_at=origins[item.origin_transaction_id].observed_at,
            evidence_ids=[state.evidence_id, origins[item.origin_transaction_id].bank_evidence_id],
        )
        for item in sorted(ledger.fragments, key=lambda item: item.fragment_id)
    ]


def _read(
    session: Session, user_id: UUID, now: datetime, *, current: bool = True
) -> tuple[IncomeLedger, EvidenceItem]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise PolicyLifecycleError("INVALID_NEW_FUNDS_LEDGER", "收入账目时钟必须带时区", 409)
    statements = list(
        session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.user_id == user_id,
                EvidenceItem.source_type == LEDGER_SOURCE,
                EvidenceItem.status != "SUPERSEDED",
            )
        )
    )
    if len(statements) != 1:
        raise PolicyLifecycleError(
            "MISSING_NEW_FUNDS_LEDGER" if not statements else "CONFLICTING_NEW_FUNDS_LEDGER",
            "需要唯一完整的收入来源账目",
            409,
        )
    statement = statements[0]
    sources = Sources(
        user_id,
        now,
        list(session.scalars(select(EvidenceItem).where(EvidenceItem.user_id == user_id))),
    )
    try:
        if not sources.valid(
            statement, {"simulation": True, "user_id": str(user_id), "complete": True}
        ):
            raise ValueError("Ledger bank identity, hash or known time is invalid")
        accounts = {
            account.id: account
            for account in session.scalars(
                select(Account).where(Account.user_id == user_id, Account.account_type == "CASH")
            )
        }
        raw = (
            LedgerPayload.model_validate_json(json.dumps(statement.content))
            if statement.content.get("protocol") == "new-funds-ledger-v1"
            else None
        )
        ledger = (
            IncomeLedger.model_validate_json(json.dumps(statement.content)) if raw is None else None
        )
        epoch = (raw.as_of if raw else ledger.as_of if ledger else now).astimezone(UTC)
        scope = raw.scope_account_ids if raw else list(ledger.scope_account_ids) if ledger else []
        if scope != sorted(accounts) or epoch > now or epoch > statement.observed_at:
            raise ValueError("Ledger scope or epoch is not the complete current CASH scope")
        for account in accounts.values():
            if not current:
                continue
            if account.observed_at > epoch or account.observed_at > now:
                raise ValueError("Income ledger predates adopted cash balances")
            if (
                sources.proof(
                    "SIMULATED_BANK_BALANCE",
                    "account_id",
                    account.id,
                    {
                        "simulation": True,
                        "user_id": str(user_id),
                        "account_id": str(account.id),
                        "account_type": account.account_type,
                        "currency": account.currency,
                        "balance_cents": account.balance_cents,
                        "as_of": account.observed_at.isoformat(),
                    },
                )
                is None
            ):
                raise ValueError("An income location lacks a valid bank balance")
        transactions = list(
            session.scalars(
                select(Transaction)
                .where(
                    Transaction.user_id == user_id,
                    Transaction.account_id.in_(accounts),
                    Transaction.occurred_at <= now,
                    Transaction.observed_at <= now,
                )
                .limit(100001)
            )
        )
        covered_until = epoch if current else now
        if len(transactions) > 100000 or any(
            item.occurred_at > covered_until or item.observed_at > covered_until
            for item in transactions
        ):
            raise ValueError("Income ledger capacity or known CASH activity exceeds its epoch")
        incomes: dict[UUID, Transaction] = {}
        for transaction in transactions:
            if transaction.direction != "CREDIT":
                continue
            proof = (
                sources.evidence.get(transaction.evidence_id) if transaction.evidence_id else None
            )
            if proof is None or proof.observed_at > covered_until or not sources.valid(proof, {}):
                raise ValueError("A CREDIT origin lacks intact observed bank evidence")
            bank_fact_snapshot(transaction, proof)
            if proof.content.get("economic_role") == "INCOME":
                incomes[transaction.id] = transaction
        if raw is not None:
            by_id = {item.transaction_id: item for item in raw.lots}
            if len(by_id) != len(raw.lots) or by_id.keys() != incomes.keys():
                raise ValueError("The v1 income origin set is incomplete or duplicated")
            origins, fragments = [], []
            for identity in sorted(incomes):
                transaction, item = incomes[identity], by_id[identity]
                origins.append(
                    IncomeOrigin(
                        origin_transaction_id=identity,
                        origin_account_id=item.account_id,
                        amount_cents=item.original_cents,
                        occurred_at=transaction.occurred_at,
                        observed_at=transaction.observed_at,
                        bank_evidence_id=item.bank_evidence_id,
                        bank_evidence_hash=item.bank_evidence_hash,
                    )
                )
                fragments.append(
                    IncomeFragment(
                        fragment_id=location_id(identity, item.account_id),
                        origin_transaction_id=identity,
                        account_id=item.account_id,
                        spent_cents=item.spent_cents,
                        assigned_cents=item.assigned_cents,
                        reserved_cents=item.reserved_cents,
                        legacy_reserved_cents=item.reserved_cents,
                        available_cents=item.available_cents,
                    )
                )
            ledger = IncomeLedger(
                user_id=user_id,
                as_of=epoch,
                scope_account_ids=tuple(scope),
                origins=tuple(origins),
                fragments=tuple(fragments),
            )
        assert ledger is not None
        if {item.origin_transaction_id for item in ledger.origins} != incomes.keys():
            raise ValueError("Income origins must exactly cover the actual original bank income")
        for origin in ledger.origins:
            transaction = incomes[origin.origin_transaction_id]
            proof = sources.evidence.get(origin.bank_evidence_id)
            if (
                transaction.account_id != origin.origin_account_id
                or transaction.evidence_id != origin.bank_evidence_id
                or transaction.amount_cents != origin.amount_cents
                or transaction.occurred_at != origin.occurred_at
                or transaction.observed_at != origin.observed_at
                or proof is None
                or proof.content_hash != origin.bank_evidence_hash
            ):
                raise ValueError("Immutable original income identity or economic facts changed")
        owned: dict[UUID, int] = {}
        for goal in session.scalars(select(Goal).where(Goal.user_id == user_id)):
            if not current:
                continue
            proof = sources.proof(
                "SIMULATED_GOAL_OWNERSHIP",
                "goal_id",
                goal.id,
                {"simulation": True, "user_id": str(user_id), "goal_id": str(goal.id)},
            )
            if (
                proof is None
                or type(proof.content.get("cash_owned_cents")) is not int
                or goal.account_id is None
            ):
                raise ValueError("Cash income locations require complete goal ownership")
            owned[goal.account_id] = (
                owned.get(goal.account_id, 0) + proof.content["cash_owned_cents"]
            )
        for identity, account in accounts.items():
            if not current:
                continue
            available = sum(
                item.available_cents + item.reserved_cents
                for item in ledger.fragments
                if item.account_id == identity
            )
            if available > account.balance_cents - owned.get(identity, 0):
                raise ValueError(
                    "Income availability and all reservations exceed unowned location cash"
                )
        if current and raw is None:
            validate_income_locations(session, user_id, bank_location_snapshot(ledger), now)
        return ledger, statement
    except (KeyError, ValueError, TypeError, OverflowError) as error:
        raise PolicyLifecycleError("INVALID_NEW_FUNDS_LEDGER", str(error), 409) from error


def reserve_income_for_action(
    session: Session,
    user_id: UUID,
    action_id: UUID,
    uses: Sequence[IncomeUse],
    operation: IncomeOperation,
    now: datetime,
    *,
    destination_account_id: UUID | None = None,
) -> IncomeLedger:
    """Phase 1; caller owns the transaction containing the persisted action and reservation."""
    action, command = _action(session, user_id, action_id)
    ledger, proof = _read(session, user_id, now)
    expected_operation: IncomeOperation = (
        "TRANSFER_INTERNAL"
        if command.effect.action_type == "TRANSFER_INTERNAL"
        else "ALLOCATE_GOAL"
        if command.effect.action_type == "ALLOCATE_GOAL"
        else "SPEND"
    )
    expected_destination = (
        command.effect.destination_account_id if operation == "TRANSFER_INTERNAL" else None
    )
    if (
        operation != expected_operation
        or destination_account_id != expected_destination
        or sorted(uses, key=lambda item: item.fragment_id)
        != sorted(command.effect.income_uses, key=lambda item: item.fragment_id)
        or command.effect.action_type == "REDEEM_ASSET"
    ):
        raise _error("Income reservation must match the immutable execution effect")
    existing = next((item for item in ledger.reservations if item.action_id == action_id), None)
    if existing is None:
        if action.status not in {"PLANNED", "AUTHORIZED", "SUBMITTED"}:
            raise _error("An inactive action cannot acquire income reservations")
        if not _prepared_income_matches(session, action.request.get("income_evidence"), proof, now):
            raise _error("Prepared income evidence changed; revalidation is required")
    try:
        candidate = reserve_income(
            ledger, action_id, uses, operation, now, destination_account_id=destination_account_id
        )
        validate_income_locations(session, user_id, bank_location_snapshot(candidate), now)
        return _persist(session, proof, candidate, action_id, "reserve", now)
    except (ValueError, TypeError) as error:
        raise _error(str(error)) from error


def commit_income_for_action(
    session: Session,
    user_id: UUID,
    action_id: UUID,
    now: datetime,
) -> IncomeLedger:
    """Phase 3; consume only this action's reservation after verified bank completion."""
    _, command = _action(session, user_id, action_id)
    operation = session.scalar(
        select(BankOperation).where(
            BankOperation.user_id == user_id, BankOperation.action_plan_id == action_id
        )
    )
    if (
        operation is None
        or operation.status != "SETTLED"
        or operation.request != command.model_dump(mode="json")
        or operation.request_hash != configuration_hash(operation.request)
    ):
        raise _error("Only the same independently settled bank command may consume income")
    ledger, proof = _read(session, user_id, now, current=False)
    _reservation_matches(ledger, action_id, command)
    try:
        candidate = commit_income(ledger, action_id, now)
        validate_income_locations(session, user_id, bank_location_snapshot(candidate), now)
        return _persist(session, proof, candidate, action_id, "commit", now)
    except (ValueError, TypeError) as error:
        raise _error(str(error)) from error


def release_income_for_action(
    session: Session,
    user_id: UUID,
    action_id: UUID,
    now: datetime,
    *,
    confirmed_no_effect: bool,
) -> IncomeLedger:
    """Release a known unexecuted action; never release legacy or unknown occupancy."""
    action, command = _action(session, user_id, action_id)
    bank = session.scalar(
        select(BankOperation).where(
            BankOperation.user_id == user_id, BankOperation.action_plan_id == action_id
        )
    )
    if (
        confirmed_no_effect is not True
        or (bank is not None and bank.status != "REJECTED")
        or (bank is None and action.status not in {"CANCELLED", "INVALIDATED"})
    ):
        raise _error("A failed label or unknown effect cannot release income reservations")
    if bank is not None and (
        bank.request != command.model_dump(mode="json")
        or bank.request_hash != configuration_hash(bank.request)
    ):
        raise _error("A different rejected bank request cannot release this reservation")
    ledger, proof = _read(session, user_id, now)
    _reservation_matches(ledger, action_id, command)
    try:
        candidate = release_income(ledger, action_id, now, confirmed_no_effect=True)
        validate_income_locations(session, user_id, bank_location_snapshot(candidate), now)
        return _persist(session, proof, candidate, action_id, "release", now)
    except (ValueError, TypeError) as error:
        raise _error(str(error)) from error


def _error(message: str) -> PolicyLifecycleError:
    return PolicyLifecycleError("INCOME_RECONCILIATION_REQUIRED", message, 409)


def _prepared_income_matches(
    session: Session, reference: object, current: EvidenceItem, now: datetime
) -> bool:
    """Only proven time-only successors preserve a prepared economic source identity."""
    if not isinstance(reference, dict) or set(reference) != {"id", "hash"}:
        return False
    economic = {key: value for key, value in current.content.items() if key != "as_of"}
    row: EvidenceItem | None = current
    seen: set[UUID] = set()
    latest = now
    while row is not None and len(seen) < 10000:
        if (
            row.id in seen
            or row.user_id != current.user_id
            or row.source_type != LEDGER_SOURCE
            or row.evidence_level != "BANK_CONFIRMED"
            or row.status != ("VALID" if row.id == current.id else "SUPERSEDED")
            or row.content_hash != configuration_hash(row.content)
            or row.observed_at > latest
            or row.valid_from > now
            or {key: value for key, value in row.content.items() if key != "as_of"} != economic
        ):
            return False
        try:
            epoch = datetime.fromisoformat(row.content["as_of"])
            if epoch.tzinfo is None or epoch > row.observed_at:
                return False
        except (KeyError, TypeError, ValueError):
            return False
        if reference == {"id": str(row.id), "hash": row.content_hash}:
            return True
        seen.add(row.id)
        latest = row.observed_at
        row = session.get(EvidenceItem, row.supersedes_id) if row.supersedes_id else None
    return False


def _action(session: Session, user_id: UUID, action_id: UUID) -> tuple[ActionPlan, BankCommand]:
    user = session.scalar(select(User).where(User.id == user_id).with_for_update())
    action = session.scalar(
        select(ActionPlan).where(ActionPlan.id == action_id, ActionPlan.user_id == user_id)
    )
    if user is None or not user.is_simulated or action is None:
        raise _error("Unknown income action owner")
    try:
        command = BankCommand.model_validate_json(json.dumps(action.request["execution"]))
        effect = command.effect
        if (
            configuration_hash(action.request) != action.request_hash
            or command.effect_hash != execution_effect_hash(effect)
            or effect.user_id != user_id
            or effect.operation_id != action_id
            or action.action_type != ACTION_PLAN_TYPES[effect.action_type]
            or action.amount_cents != effect.amount_cents
        ):
            raise ValueError("Persisted action and exact economic command differ")
        return action, command
    except (KeyError, TypeError, ValueError) as error:
        raise _error(str(error)) from error


def _reservation_matches(ledger: IncomeLedger, action_id: UUID, command: BankCommand) -> None:
    reservation = next((item for item in ledger.reservations if item.action_id == action_id), None)
    operation = (
        "TRANSFER_INTERNAL"
        if command.effect.action_type == "TRANSFER_INTERNAL"
        else "ALLOCATE_GOAL"
        if command.effect.action_type == "ALLOCATE_GOAL"
        else "SPEND"
    )
    destination = (
        command.effect.destination_account_id if operation == "TRANSFER_INTERNAL" else None
    )
    if (
        reservation is None
        or reservation.operation != operation
        or reservation.destination_account_id != destination
        or sorted(reservation.uses, key=lambda item: item.fragment_id)
        != sorted(command.effect.income_uses, key=lambda item: item.fragment_id)
    ):
        raise _error("The bank command differs from its exact original source reservation")


def _persist(
    session: Session,
    previous: EvidenceItem,
    ledger: IncomeLedger,
    action_id: UUID,
    transition: str,
    now: datetime,
) -> IncomeLedger:
    content = ledger.model_dump(mode="json")
    if previous.content == content:
        return ledger
    digest = configuration_hash(content)
    identity = uuid5(action_id, f"income:{transition}:{previous.id}:{digest}")
    previous.status = "SUPERSEDED"
    session.add(
        EvidenceItem(
            id=identity,
            user_id=ledger.user_id,
            created_at=now,
            evidence_level="BANK_CONFIRMED",
            source_type=LEDGER_SOURCE,
            source_ref=f"income-projection:{identity}",
            content=content,
            content_hash=digest,
            valid_from=now,
            observed_at=now,
            supersedes_id=previous.id,
            status="VALID",
        )
    )
    session.flush()
    return ledger
