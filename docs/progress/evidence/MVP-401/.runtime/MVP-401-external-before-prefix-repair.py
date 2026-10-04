"""Trusted simulator ingress: committed bank facts precede atomic application projection."""

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid5

from app.db.audit_guard import audit_command_guard, transaction_gate
from app.db.models import (
    Account,
    ActionPlan,
    ActionResourceReservation,
    AssetPosition,
    AuditEpoch,
    EvidenceItem,
    ExternalBankFact,
    Goal,
    SimulatedBankPosting,
    Transaction,
    User,
)
from app.domain.audit_chain import build_subject
from app.domain.audit_chain_types import AuditSubject
from app.domain.bank_posting_codec import POSTING_V2_FIELDS, bank_posting_data
from app.domain.execution_types import BankCommand
from app.domain.external_bank_fact import (
    add_income_origin,
    apply_consumption,
    economic_legs,
    external_hash,
    external_json,
    fact_id,
    plan_consumption,
    posting_digest,
    semantic_request,
    validate_external_fact_original,
    verify_external_projection,
    verify_external_settlement,
)
from app.domain.external_bank_fact import (
    external_text as canonical_text,
)
from app.domain.external_bank_fact_types import (
    ExternalCashAttribution,
    ExternalFactRequest,
    ExternalFactResult,
    ExternalProjectionResult,
    ExternalSettlementResult,
)
from app.domain.income_ledger import IncomeLedger, IncomeOrigin, bank_location_snapshot, location_id
from app.domain.policy_configuration import configuration_hash
from app.services.boundary import OWNERSHIP_SOURCE, Sources
from app.services.execution_bank import _opening, validate_income_locations
from app.services.execution_exposure import refresh_execution_exposure
from app.services.income_ledger import persist_external_income, read_income_state
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.recovery_projection import _epochs, current_proof, replace_proof
from app.services.simulated_bank import ledger_heads, validate_bank_projection
from sqlalchemy import inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


def _error(
    message: str, code: str = "EXTERNAL_BANK_RECONCILIATION_REQUIRED"
) -> PolicyLifecycleError:
    return PolicyLifecycleError(code, message, 409)


def _data(row: object) -> dict[str, Any]:
    mapper = inspect(type(row))
    assert mapper is not None
    return {column.key: getattr(row, column.key) for column in mapper.columns}


def _posting_data(row: SimulatedBankPosting) -> dict[str, Any]:
    return bank_posting_data({key: getattr(row, key) for key in POSTING_V2_FIELDS})


def _lock(session: Session, user_id: UUID) -> User:
    transaction_gate(session, user_id)
    user = session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None or not user.is_simulated:
        raise PolicyLifecycleError("SIMULATED_USER_NOT_FOUND", "Unknown simulated bank owner", 404)
    return user


def open_external_clearing(
    session: Session,
    user_id: UUID,
    now: datetime,
    *,
    counterparty_reserves: Mapping[str, int],
) -> None:
    """Explicit trusted seed capital, before genesis; never invoked by fact ingestion."""
    _lock(session, user_id)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Clearing bootstrap time must be aware")
    for counterparty, reserve in sorted(counterparty_reserves.items()):
        if (
            type(counterparty) is not str
            or not 1 <= len(counterparty) <= 96
            or type(reserve) is not int
            or not 0 <= reserve <= 2**63 - 1
        ):
            raise ValueError(
                "Clearing bootstrap requires bounded identity and explicit integer capital"
            )
        key = "CLEARING:bounded-funds-external-v1:" + counterparty
        metadata = {
            "protocol_version": "bank-external-clearing-v1",
            "simulation": True,
            "source_id": "bounded-funds-external-v1",
            "counterparty_ref": counterparty,
            "initial_reserve_cents": reserve,
        }
        existing = session.scalar(
            select(SimulatedBankPosting).where(
                SimulatedBankPosting.user_id == user_id,
                SimulatedBankPosting.ledger_key == key,
                SimulatedBankPosting.sequence_number == 1,
            )
        )
        if existing is not None:
            if (
                existing.id != uuid5(user_id, "external-clearing-opening:" + key)
                or existing.delta_cents != reserve
                or existing.ledger_metadata != metadata
                or existing.external_fact_id is not None
            ):
                raise _error(
                    "Clearing capital cannot be rebased or inferred from customer balances"
                )
            continue
        active = session.scalar(
            select(AuditEpoch).where(AuditEpoch.user_id == user_id, AuditEpoch.status == "OPEN")
        )
        if active is not None and active.event_count:
            raise _error("New clearing capital requires an explicit audited bootstrap")
        session.add(
            SimulatedBankPosting(
                id=uuid5(user_id, "external-clearing-opening:" + key),
                user_id=user_id,
                created_at=now,
                ledger_key=key,
                ledger_dimension="ECONOMIC",
                ledger_metadata=metadata,
                account_id=None,
                position_id=None,
                redemption_id=None,
                operation_id=None,
                external_fact_id=None,
                leg_ref=None,
                previous_posting_id=None,
                sequence_number=1,
                entry_kind="OPENING",
                balance_before_cents=0,
                delta_cents=reserve,
                balance_after_cents=reserve,
                occurred_at=now,
            )
        )
    session.flush()


def _post(
    session: Session,
    fact: ExternalBankFact,
    key: str,
    delta: int,
    leg: str,
    *,
    occurred_at: datetime,
    now: datetime,
) -> SimulatedBankPosting:
    head = ledger_heads(session, fact.user_id).get(key)
    if head is None or head.occurred_at > occurred_at or head.balance_after_cents + delta < 0:
        raise _error("The independent bank ledger cannot represent this exact external leg")
    row = SimulatedBankPosting(
        id=uuid5(fact.id, "external-posting:" + leg),
        user_id=fact.user_id,
        created_at=now,
        ledger_key=key,
        ledger_dimension=head.ledger_dimension,
        ledger_metadata=head.ledger_metadata,
        account_id=head.account_id,
        position_id=head.position_id,
        redemption_id=None,
        operation_id=None,
        external_fact_id=fact.id,
        leg_ref=leg,
        previous_posting_id=head.id,
        sequence_number=head.sequence_number + 1,
        entry_kind="DEBIT" if delta < 0 else "CREDIT",
        balance_before_cents=head.balance_after_cents,
        delta_cents=delta,
        balance_after_cents=head.balance_after_cents + delta,
        occurred_at=occurred_at,
    )
    session.add(row)
    session.flush()
    return row


def _fact_postings(session: Session, fact: ExternalBankFact) -> list[SimulatedBankPosting]:
    rows = list(
        session.scalars(
            select(SimulatedBankPosting)
            .where(
                SimulatedBankPosting.user_id == fact.user_id,
                SimulatedBankPosting.external_fact_id == fact.id,
            )
            .limit(10001)
        )
    )
    if len(rows) > 10000:
        raise _error("External posting set exceeds its bounded reconciliation capacity")
    return rows


def verify_external_facts(
    session: Session,
    user_id: UUID,
    now: datetime,
    *,
    require_projected: bool = True,
) -> list[ExternalBankFact]:
    """Read actual complete external originals; unresolved facts never become idle cash."""
    facts = list(
        session.scalars(
            select(ExternalBankFact).where(ExternalBankFact.user_id == user_id).limit(10001)
        )
    )
    if len(facts) > 10000:
        raise _error("External fact set exceeds its bounded verification capacity")
    for fact in facts:
        validate_external_fact_original(_data(fact))
        if fact.observed_at > now or fact.updated_at > now:
            raise _error("External bank observation is not yet known")
        rows = [_posting_data(row) for row in _fact_postings(session, fact)]
        if fact.bank_status == "SETTLED":
            verify_external_settlement(_data(fact), rows)
            if fact.projection_status == "PROJECTED":
                verify_external_projection(_data(fact), rows, subjects=_subjects(session, fact))
            elif require_projected:
                raise _error("Settled external cash awaits its complete application projection")
        elif rows or fact.bank_status != "REJECTED":
            raise _error("An unresolved external bank result requires reconciliation")
    return facts


def _bank_transaction(
    session: Session,
    user_id: UUID,
    request: ExternalFactRequest,
    observed_at: datetime,
) -> UUID:
    _lock(session, user_id)
    by_ref = session.scalar(
        select(ExternalBankFact).where(
            ExternalBankFact.user_id == user_id,
            ExternalBankFact.source_id == request.source_id,
            ExternalBankFact.external_ref == request.external_ref,
        )
    )
    by_key = session.scalar(
        select(ExternalBankFact).where(
            ExternalBankFact.user_id == user_id,
            ExternalBankFact.idempotency_key == request.idempotency_key,
        )
    )
    if by_ref is not None or by_key is not None:
        original = by_ref or by_key
        assert original is not None
        if (
            by_ref is not by_key
            or original.idempotency_key != request.idempotency_key
            or original.request_hash != external_hash(semantic_request(request))
        ):
            raise _error(
                "Idempotency conflict; original external fact " + str(original.id),
                "EXTERNAL_BANK_IDEMPOTENCY_CONFLICT",
            )
        validate_external_fact_original(_data(original))
        if original.bank_status == "SETTLED":
            verify_external_settlement(
                _data(original), [_posting_data(row) for row in _fact_postings(session, original)]
            )
            return original.id
        # An unresolved original never silently resubmits its economic legs.
        return original.id
    account = session.scalar(
        select(Account).where(Account.user_id == user_id, Account.id == request.account_id)
    )
    if account is None or account.account_type != "CASH" or account.currency != request.currency:
        raise PolicyLifecycleError(
            "EXTERNAL_BANK_ACCOUNT_NOT_FOUND", "Unknown owned CASH account", 404
        )
    if (
        observed_at.tzinfo is None
        or observed_at.utcoffset() is None
        or request.occurred_at > observed_at
    ):
        raise _error("Trusted observation cannot precede its actual external bank occurrence")
    payload = semantic_request(request)
    fact = ExternalBankFact(
        id=fact_id(request),
        user_id=user_id,
        created_at=observed_at,
        protocol_version=request.protocol_version,
        source_id=request.source_id,
        external_ref=request.external_ref,
        kind=request.kind,
        account_id=request.account_id,
        amount_cents=request.amount_cents,
        currency=request.currency,
        counterparty_ref=request.counterparty_ref,
        occurred_at=request.occurred_at,
        observed_at=observed_at,
        idempotency_key=request.idempotency_key,
        request=external_json(payload),
        request_canonical_text=canonical_text(payload),
        request_hash=external_hash(payload),
        bank_status="ACCEPTED",
        accepted_at=observed_at,
        settled_at=None,
        bank_result=None,
        bank_result_canonical_text=None,
        bank_result_hash=None,
        projection_status="PENDING",
        projected_at=None,
        projection_result=None,
        projection_result_canonical_text=None,
        projection_result_hash=None,
        updated_at=observed_at,
    )
    session.add(fact)
    session.flush()
    # These balances come exclusively from trusted, continuous bank subledgers.
    rows = [
        _post(session, fact, key, delta, leg, occurred_at=request.occurred_at, now=observed_at)
        for leg, key, delta in economic_legs(request)
    ]
    result = ExternalSettlementResult(
        user_id=user_id,
        external_fact_id=fact.id,
        request_hash=fact.request_hash,
        settled_at=observed_at,
        economic_posting_ids=(rows[0].id, rows[1].id),
        posting_digest=posting_digest([_posting_data(row) for row in rows]),
    )
    fact.bank_status, fact.settled_at = "SETTLED", observed_at
    fact.bank_result = external_json(result.model_dump())
    fact.bank_result_canonical_text = canonical_text(result.model_dump())
    fact.bank_result_hash = external_hash(result.model_dump())
    session.flush()
    verify_external_settlement(_data(fact), [_posting_data(row) for row in rows])
    from app.services.audit_recording import record_external_bank_settled

    record_external_bank_settled(session, fact, observed_at)
    return fact.id


def _validated_goal_cash(session: Session, sources: Sources, account_id: UUID) -> int:
    owned = 0
    for goal in session.scalars(select(Goal).where(Goal.user_id == sources.user_id)):
        positions = list(
            session.scalars(
                select(AssetPosition).where(
                    AssetPosition.user_id == sources.user_id,
                    AssetPosition.goal_id == goal.id,
                    AssetPosition.status != "REDEEMED",
                )
            )
        )
        principal = sum(row.principal_cents for row in positions)
        expected: dict[str, Any] = {
            "simulation": True,
            "user_id": str(sources.user_id),
            "protocol": "goal-ownership-v1",
            "goal_id": str(goal.id),
            "policy_id": str(goal.policy_id),
            "account_id": str(goal.account_id) if goal.account_id else None,
            "allocated_cents": goal.allocated_cents,
            "cash_owned_cents": goal.allocated_cents - principal,
            "principal_owned_cents": principal,
            "position_ids": sorted(str(row.id) for row in positions),
        }
        proof = sources.proof(OWNERSHIP_SOURCE, "goal_id", goal.id, expected)
        if proof is None or expected["cash_owned_cents"] < 0:
            raise _error("Financial projection lacks complete actual goal cash ownership")
        if goal.account_id == account_id:
            owned += expected["cash_owned_cents"]
    return owned


def _cash_attribution(
    session: Session,
    request: ExternalFactRequest,
    ledger: IncomeLedger,
    balance_before: int,
    now: datetime,
) -> ExternalCashAttribution:
    evidence = list(
        session.scalars(
            select(EvidenceItem).where(EvidenceItem.user_id == request.user_id).limit(10001)
        )
    )
    if len(evidence) > 10000:
        raise _error("Consumption source set exceeds its bounded attribution capacity")
    sources = Sources(request.user_id, now, evidence)
    goal_cash = _validated_goal_cash(session, sources, request.account_id)
    reservations = {row.action_id: row for row in ledger.reservations if row.state == "RESERVED"}
    claims = list(
        session.scalars(
            select(ActionResourceReservation)
            .where(
                ActionResourceReservation.user_id == request.user_id,
                ActionResourceReservation.status == "RESERVED",
            )
            .limit(10001)
        )
    )
    if len(claims) > 10000:
        raise _error("Consumption claims exceed their bounded reconciliation capacity")
    non_income = 0
    cash_actions = {
        row.action_plan_id
        for row in claims
        if row.resource_kind == "CASH" and row.resource_key == str(request.account_id)
    }
    for action_id in cash_actions:
        action = session.get(ActionPlan, action_id)
        if (
            action is None
            or action.user_id != request.user_id
            or configuration_hash(action.request) != action.request_hash
        ):
            raise _error("External consumption cannot map an active cash claim")
        command = BankCommand.model_validate_json(json.dumps(action.request["execution"]))
        if command.effect.operation_id != action.id or command.effect.user_id != request.user_id:
            raise _error("Active cash claim differs from its original five-command request")
        owned = [row for row in claims if row.action_plan_id == action.id]
        actual = {(row.resource_kind, row.resource_key): row.amount_cents for row in owned}
        required = {
            ("CASH", str(use.account_id)): use.amount_cents for use in command.effect.cash_uses
        }
        required.update(
            {
                ("INCOME", str(use.fragment_id)): use.amount_cents
                for use in command.effect.income_uses
            }
        )
        if any(actual.get(key) != value for key, value in required.items()) or len(actual) != len(
            owned
        ):
            raise _error("Active cash/income claims do not exactly cover their original command")
        income = sum(
            use.amount_cents
            for use in command.effect.income_uses
            if use.account_id == request.account_id
        )
        reserved = reservations.get(action.id)
        if income and (
            reserved is None
            or sorted((str(use.fragment_id), use.amount_cents) for use in reserved.uses)
            != sorted(
                (str(use.fragment_id), use.amount_cents) for use in command.effect.income_uses
            )
        ):
            raise _error("Cash and income reservations have unknown overlap")
        cash = actual[("CASH", str(request.account_id))]
        if cash < income:
            raise _error("An income claim exceeds its exact corresponding cash claim")
        if command.effect.action_type == "PURCHASE_ASSET" and command.effect.goal_id is not None:
            claimed_goal = session.get(Goal, command.effect.goal_id)
            if (
                claimed_goal is None
                or claimed_goal.user_id != request.user_id
                or claimed_goal.account_id != request.account_id
                or income
            ):
                raise _error("Goal purchase claim has an unknown cash ownership overlap")
        else:
            non_income += cash - income
    # Older reserved purchases have no normalized claim mapping in this protocol.
    if (
        session.scalar(
            select(ActionPlan.id).where(
                ActionPlan.user_id == request.user_id,
                ActionPlan.source_account_id == request.account_id,
                ActionPlan.status.in_(["SUBMITTED", "UNKNOWN", "AUTHORIZED", "PLANNED"]),
                ActionPlan.action_type == "ASSET_PURCHASE",
            )
        )
        is not None
    ):
        for action in session.scalars(
            select(ActionPlan).where(
                ActionPlan.user_id == request.user_id,
                ActionPlan.source_account_id == request.account_id,
                ActionPlan.status.in_(["SUBMITTED", "UNKNOWN", "AUTHORIZED", "PLANNED"]),
                ActionPlan.action_type == "ASSET_PURCHASE",
            )
        ):
            if "execution" not in action.request:
                raise _error("A legacy purchase claim has no exact external-consumption mapping")
    return ExternalCashAttribution(
        account_id=request.account_id,
        bank_balance_before_cents=balance_before,
        goal_cash_owned_cents=goal_cash,
        non_income_claim_cents=non_income,
    )


def _transaction(
    session: Session, fact: ExternalBankFact, cash: SimulatedBankPosting, now: datetime
) -> tuple[Transaction, EvidenceItem]:
    transaction_id, proof_id = uuid5(fact.id, "transaction"), uuid5(fact.id, "transaction-evidence")
    content = {
        "simulation": True,
        "user_id": str(fact.user_id),
        "transaction_id": str(transaction_id),
        "account_id": str(fact.account_id),
        "direction": "CREDIT" if fact.kind == "INCOME" else "DEBIT",
        "amount_cents": fact.amount_cents,
        "balance_after_cents": cash.balance_after_cents,
        "occurred_at": fact.occurred_at.isoformat(),
        "counterparty_ref": fact.counterparty_ref,
        "economic_role": fact.kind,
        "external_fact_id": str(fact.id),
        "posting_id": str(cash.id),
        "request_hash": fact.request_hash,
    }
    proof = EvidenceItem(
        id=proof_id,
        user_id=fact.user_id,
        created_at=now,
        evidence_level="BANK_CONFIRMED",
        source_type="SIMULATED_BANK_TRANSACTION",
        source_ref="external-bank-fact:" + str(fact.id),
        content=content,
        content_hash=configuration_hash(content),
        valid_from=fact.occurred_at,
        observed_at=now,
        status="VALID",
    )
    transaction = Transaction(
        id=transaction_id,
        user_id=fact.user_id,
        created_at=now,
        account_id=fact.account_id,
        evidence_id=proof_id,
        source_ref="external-bank-fact:" + str(fact.id),
        direction=content["direction"],
        amount_cents=fact.amount_cents,
        balance_after_cents=cash.balance_after_cents,
        category="SALARY" if fact.kind == "INCOME" else "EXTERNAL_CONSUMPTION",
        counterparty_ref=fact.counterparty_ref,
        is_one_off=False,
        category_confirmed=False,
        occurred_at=fact.occurred_at,
        observed_at=now,
    )
    session.add_all([proof, transaction])
    session.flush()
    return transaction, proof


def _subjects(session: Session, fact: ExternalBankFact) -> list[AuditSubject]:
    result = ExternalProjectionResult.model_validate_json(
        str(fact.projection_result_canonical_text)
    )
    subjects = []
    transaction = session.get(Transaction, result.transaction_id)
    if transaction is None:
        raise _error("External projected transaction is missing")
    subjects.append(
        build_subject(
            user_id=fact.user_id,
            epoch_id=UUID(int=0),
            kind="TRANSACTION",
            id=transaction.id,
            data=external_json(_data(transaction)),
        )
    )
    pending = [*result.proof_successor_ids, result.transaction_evidence_id]
    seen: set[UUID] = set()
    while pending:
        identity = pending.pop()
        if identity in seen:
            continue
        if len(seen) >= 10000:
            raise _error("Projection proof ancestry exceeds its bounded capacity")
        proof = session.get(EvidenceItem, identity)
        if proof is None or proof.user_id != fact.user_id:
            raise _error("External projection original proof is missing or foreign")
        seen.add(identity)
        subjects.append(
            build_subject(
                user_id=fact.user_id,
                epoch_id=UUID(int=0),
                kind="EVIDENCE",
                id=proof.id,
                data=external_json(_data(proof)),
            )
        )
        if proof.supersedes_id:
            pending.append(proof.supersedes_id)
        if proof.source_type == "SIMULATED_NEW_FUNDS_LEDGER":
            content = proof.content
            origins = (
                content.get("lots", [])
                if content.get("protocol") == "new-funds-ledger-v1"
                else content.get("origins", [])
            )
            for origin in origins:
                original_transaction_id = UUID(
                    origin.get("transaction_id", origin.get("origin_transaction_id"))
                )
                original = session.get(Transaction, original_transaction_id)
                if (
                    original is None
                    or original.user_id != fact.user_id
                    or original.evidence_id is None
                ):
                    raise _error("An income lot lost its original bank transaction")
                subjects.append(
                    build_subject(
                        user_id=fact.user_id,
                        epoch_id=UUID(int=0),
                        kind="TRANSACTION",
                        id=original.id,
                        data=external_json(_data(original)),
                    )
                )
                pending.append(original.evidence_id)
    return subjects


def _project(session: Session, fact: ExternalBankFact, now: datetime) -> None:
    request = validate_external_fact_original(_data(fact))
    rows = _fact_postings(session, fact)
    verify_external_settlement(_data(fact), [_posting_data(row) for row in rows])
    cash = next(row for row in rows if row.ledger_key == "CASH:" + str(fact.account_id))
    account = session.get(Account, fact.account_id)
    if (
        account is None
        or account.user_id != fact.user_id
        or account.balance_cents != cash.balance_before_cents
        or account.observed_at > now
    ):
        raise _error("Application cash has not reconciled the bank's exact previous balance")
    previous_balance = current_proof(
        session, fact.user_id, "SIMULATED_BANK_BALANCE", "account_id", account.id
    )
    expected_balance = {
        "simulation": True,
        "user_id": str(fact.user_id),
        "account_id": str(account.id),
        "account_type": account.account_type,
        "currency": account.currency,
        "balance_cents": account.balance_cents,
        "as_of": account.observed_at.isoformat(),
    }
    if (
        any(previous_balance.content.get(key) != value for key, value in expected_balance.items())
        or previous_balance.observed_at > now
    ):
        raise _error("Application before balance has no intact matching bank source")
    validate_bank_projection(session, fact.user_id, now, allow_unprojected=True)
    sources = Sources(
        fact.user_id,
        now,
        list(
            session.scalars(
                select(EvidenceItem).where(EvidenceItem.user_id == fact.user_id).limit(10001)
            )
        ),
    )
    if len(sources.evidence) > 10000:
        raise _error("Projection source capacity exceeded")
    _validated_goal_cash(session, sources, account.id)
    before_proof_ids = set(sources.evidence)
    state = read_income_state(session, fact.user_id, now)
    ledger = state.ledger
    plan = None
    if fact.kind == "CONSUMPTION":
        plan = plan_consumption(
            request,
            ledger,
            _cash_attribution(session, request, ledger, cash.balance_before_cents, now),
        )
        if plan.status != "READY":
            raise _error(plan.reason_code)
    transaction, transaction_proof = _transaction(session, fact, cash, now)
    memo = []
    if fact.kind == "INCOME":
        origin = IncomeOrigin(
            origin_transaction_id=transaction.id,
            origin_account_id=account.id,
            amount_cents=fact.amount_cents,
            occurred_at=fact.occurred_at,
            observed_at=now,
            bank_evidence_id=transaction_proof.id,
            bank_evidence_hash=transaction_proof.content_hash,
        )
        projected_ledger = add_income_origin(ledger, origin, now)
        fragment = location_id(transaction.id, account.id)
        metadata = {"origin": origin.model_dump(mode="json"), "account_id": str(account.id)}
        for bucket in ("AVAILABLE", "RESERVED", "SPENT", "ASSIGNED"):
            _opening(
                session,
                fact.user_id,
                "LOT_" + bucket + ":" + str(fragment),
                0,
                fact.occurred_at,
                dimension="INCOME_LOCATION",
                account_id=account.id,
                metadata=metadata,
                recorded_at=now,
            )
        memo.append(
            _post(
                session,
                fact,
                "LOT_AVAILABLE:" + str(fragment),
                fact.amount_cents,
                "external-income:" + str(fragment),
                occurred_at=now,
                now=now,
            )
        )
    else:
        assert plan is not None
        projected_ledger = apply_consumption(ledger, plan, now)
        for use in plan.uses:
            for bucket, delta in (("AVAILABLE", -use.amount_cents), ("SPENT", use.amount_cents)):
                memo.append(
                    _post(
                        session,
                        fact,
                        "LOT_" + bucket + ":" + str(use.fragment_id),
                        delta,
                        "external-consumption:" + str(use.fragment_id) + ":" + bucket,
                        occurred_at=now,
                        now=now,
                    )
                )
    validate_income_locations(session, fact.user_id, bank_location_snapshot(projected_ledger), now)
    account.balance_cents, account.observed_at = cash.balance_after_cents, now
    balance = replace_proof(
        session,
        previous_balance,
        {
            **previous_balance.content,
            "balance_cents": account.balance_cents,
            "as_of": now.isoformat(),
        },
        now,
        fact.id,
    )
    income = persist_external_income(
        session, fact.user_id, state.evidence_id, projected_ledger, fact.id, now
    )
    _epochs(session, fact.user_id, now, fact.id)
    session.flush()
    successors = list(
        session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.user_id == fact.user_id,
                EvidenceItem.created_at == now,
                EvidenceItem.supersedes_id.is_not(None),
                EvidenceItem.status == "VALID",
            )
        )
    )
    successors = [row for row in successors if row.id not in before_proof_ids]
    exposure_id = uuid5(fact.id, "exposure-evidence")
    result = ExternalProjectionResult(
        user_id=fact.user_id,
        external_fact_id=fact.id,
        request_hash=fact.request_hash,
        bank_result_hash=str(fact.bank_result_hash),
        projected_at=now,
        transaction_id=transaction.id,
        transaction_evidence_id=transaction_proof.id,
        balance_evidence_id=balance.id,
        income_evidence_id=income.id,
        exposure_evidence_id=exposure_id,
        proof_successor_ids=tuple(
            sorted({*(row.id for row in successors), balance.id, income.id, exposure_id})
        ),
        memo_posting_ids=tuple(sorted(row.id for row in memo)),
        memo_posting_digest=posting_digest([_posting_data(row) for row in memo]),
        income_uses=plan.uses if plan else (),
        untracked_spent_cents=plan.untracked_spent_cents if plan else 0,
    )
    fact.projection_status, fact.projected_at, fact.updated_at = "PROJECTED", now, now
    fact.projection_result = external_json(result.model_dump())
    fact.projection_result_canonical_text = canonical_text(result.model_dump())
    fact.projection_result_hash = external_hash(result.model_dump())
    session.flush()
    refresh_execution_exposure(session, fact.user_id, now, fact.id, evidence_id=exposure_id)
    session.flush()
    validate_bank_projection(session, fact.user_id, now)
    verify_external_projection(
        _data(fact),
        [_posting_data(row) for row in _fact_postings(session, fact)],
        subjects=_subjects(session, fact),
    )
    from app.services.audit_recording import record_external_bank_projected

    record_external_bank_projected(session, fact, now)


def ingest_external_fact(
    engine: Engine,
    user_id: UUID,
    request: ExternalFactRequest,
    observed_at: datetime,
) -> ExternalFactResult:
    """Internal trusted runner only; preserve settled legs when projection is incomplete."""
    request = ExternalFactRequest.model_validate(request.model_dump())
    if (
        observed_at.tzinfo is None
        or observed_at.utcoffset() is None
        or observed_at < request.occurred_at
    ):
        raise _error("Trusted observation must be aware and follow the actual economic time")
    observed_at = observed_at.astimezone(UTC)
    if request.user_id != user_id:
        raise PolicyLifecycleError("EXTERNAL_BANK_FACT_NOT_FOUND", "Unknown bank fact owner", 404)
    with audit_command_guard(engine, user_id):
        with Session(engine, expire_on_commit=False) as session, session.begin():
            identity = _bank_transaction(session, user_id, request, observed_at)
        with Session(engine, expire_on_commit=False) as session, session.begin():
            _lock(session, user_id)
            fact = session.get(ExternalBankFact, identity)
            assert fact is not None
            validate_external_fact_original(_data(fact))
            error = None
            if fact.bank_status == "SETTLED":
                if fact.projection_status == "PROJECTED":
                    verify_external_projection(
                        _data(fact),
                        [_posting_data(row) for row in _fact_postings(session, fact)],
                        subjects=_subjects(session, fact),
                    )
                else:
                    try:
                        with session.begin_nested():
                            _project(session, fact, observed_at)
                    except (PolicyLifecycleError, ValueError, KeyError, TypeError) as failure:
                        session.refresh(fact)
                        fact.projection_status = "UNKNOWN"
                        fact.updated_at = max(observed_at, fact.updated_at)
                        error = (
                            failure.code
                            if isinstance(failure, PolicyLifecycleError)
                            else "PROJECTION_ORIGINALS_INVALID"
                        )
            settlement = (
                ExternalSettlementResult.model_validate_json(str(fact.bank_result_canonical_text))
                if fact.bank_status == "SETTLED"
                else None
            )
            projection = (
                ExternalProjectionResult.model_validate_json(
                    str(fact.projection_result_canonical_text)
                )
                if fact.projection_status == "PROJECTED"
                else None
            )
            return ExternalFactResult.model_validate_json(
                json.dumps(
                    {
                        "simulation": True,
                        "external_fact_id": str(fact.id),
                        "bank_status": fact.bank_status,
                        "projection_status": fact.projection_status,
                        "economic_posting_ids": [
                            str(value) for value in settlement.economic_posting_ids
                        ]
                        if settlement
                        else [],
                        "transaction_id": str(projection.transaction_id) if projection else None,
                        "projection_error": error,
                    }
                )
            )
