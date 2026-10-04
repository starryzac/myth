"""Actual two-transaction external simulator ingress in disposable PostgreSQL databases."""

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from app.db.models import (
    Account,
    ActionPlan,
    ActionResourceReservation,
    AuditEvent,
    EvidenceItem,
    ExternalBankFact,
    Goal,
    SimulatedBankPosting,
    Transaction,
    User,
)
from app.domain.external_bank_fact_types import ExternalFactRequest
from app.domain.policy_configuration import configuration_hash
from app.services import execution, external_bank_facts
from app.services.action_contracts import ConfirmActionRequest, GoalIntent, PrepareActionRequest
from app.services.audit_chain import verify_audit_chain
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, seed_demo
from app.services.execution import confirm_action, execute_action, prepare_action
from app.services.external_bank_facts import ingest_external_fact, open_external_clearing
from app.services.goals import create_goal_projection
from app.services.income_ledger import read_income_state
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.simulated_bank import ledger_heads, open_simulated_bank, validate_bank_projection
from app.tests.test_boundary_service import confirmed_policy
from app.tests.test_demo_seed import database_snapshot
from app.tests.test_demo_seed import demo_engine as demo_engine
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration
NOW = SEED_AS_OF + timedelta(minutes=1)


def fact_request(
    engine: Engine,
    *,
    kind: str = "INCOME",
    amount: int = 600000,
    ref: str = "salary-2026-10",
    key: str = "salary-first",
) -> ExternalFactRequest:
    with Session(engine) as session:
        account = session.scalars(
            select(Account).where(Account.user_id == DEMO_USER_ID, Account.account_type == "CASH")
        ).one()
        return ExternalFactRequest.model_validate_json(
            json.dumps(
                {
                    "user_id": str(DEMO_USER_ID),
                    "account_id": str(account.id),
                    "kind": kind,
                    "amount_cents": amount,
                    "external_ref": ref,
                    "idempotency_key": key,
                    "counterparty_ref": "payroll" if kind == "INCOME" else "merchant",
                    "occurred_at": NOW.isoformat(),
                }
            )
        )


def test_real_salary_then_consumption_have_two_bank_legs_and_actual_income_conservation(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    salary = fact_request(demo_engine)
    with Session(demo_engine) as session:
        before = read_income_state(session, DEMO_USER_ID, NOW).ledger
        initial_cash = session.get(Account, salary.account_id)
        assert initial_cash is not None
        balance_before = initial_cash.balance_cents
    settled = ingest_external_fact(demo_engine, DEMO_USER_ID, salary, NOW)
    assert settled.bank_status == "SETTLED" and settled.projection_status == "PROJECTED", settled
    assert settled.transaction_id is not None and len(settled.economic_posting_ids) == 2
    with Session(demo_engine) as session:
        income = read_income_state(session, DEMO_USER_ID, NOW).ledger
        transaction = session.get(Transaction, settled.transaction_id)
        assert transaction is not None and transaction.direction == "CREDIT"
        assert transaction.amount_cents == salary.amount_cents
        proof = session.get(EvidenceItem, transaction.evidence_id)
        assert proof is not None and proof.content["economic_role"] == "INCOME"
        assert proof.content["external_fact_id"] == str(settled.external_fact_id)
        assert len(income.origins) == len(before.origins) + 1
        origin = next(row for row in income.origins if row.origin_transaction_id == transaction.id)
        assert (
            origin.bank_evidence_id == proof.id and origin.bank_evidence_hash == proof.content_hash
        )
        assert (
            ledger_heads(session, DEMO_USER_ID)[
                "CASH:" + str(salary.account_id)
            ].balance_after_cents
            == balance_before + salary.amount_cents
        )
        original = session.get(ExternalBankFact, settled.external_fact_id)
        assert original is not None
        original_result = original.bank_result_canonical_text, original.bank_result_hash
        validate_bank_projection(session, DEMO_USER_ID, NOW)
    unchanged = database_snapshot(demo_engine)
    assert ingest_external_fact(demo_engine, DEMO_USER_ID, salary, NOW) == settled
    assert database_snapshot(demo_engine) == unchanged
    consumption = fact_request(
        demo_engine, kind="CONSUMPTION", amount=1800, ref="merchant-real-1", key="consume-first"
    )
    consumed = ingest_external_fact(
        demo_engine, DEMO_USER_ID, consumption, NOW + timedelta(minutes=1)
    )
    assert consumed.bank_status == "SETTLED" and consumed.projection_status == "PROJECTED", consumed
    with Session(demo_engine) as session:
        after = read_income_state(session, DEMO_USER_ID, NOW + timedelta(minutes=1)).ledger
        assert after.origins == income.origins and after.reservations == income.reservations
        assert (
            sum(row.available_cents for row in after.fragments)
            == sum(row.available_cents for row in income.fragments) - consumption.amount_cents
        )
        assert (
            sum(row.spent_cents for row in after.fragments)
            == sum(row.spent_cents for row in income.fragments) + consumption.amount_cents
        )
        assert [(row.reserved_cents, row.assigned_cents) for row in after.fragments] == [
            (row.reserved_cents, row.assigned_cents) for row in income.fragments
        ]
        original = session.get(ExternalBankFact, settled.external_fact_id)
        assert (
            original is not None
            and (original.bank_result_canonical_text, original.bank_result_hash) == original_result
        )
        validate_bank_projection(session, DEMO_USER_ID, NOW + timedelta(minutes=1))
        verified = verify_audit_chain(session, DEMO_USER_ID)
        assert verified.status == "VALID", verified


def test_original_key_projection_unknown_retry_never_resends_economic_legs(
    demo_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_demo(demo_engine)
    request = fact_request(demo_engine)
    project = external_bank_facts._project

    def lost_application_observation(session: Session, fact: ExternalBankFact, now: object) -> None:
        raise PolicyLifecycleError(
            "SIMULATED_PROJECTION_INTERRUPTION",
            "Bank committed; application observation interrupted",
            409,
        )

    monkeypatch.setattr(external_bank_facts, "_project", lost_application_observation)
    unknown = ingest_external_fact(demo_engine, DEMO_USER_ID, request, NOW)
    assert unknown.bank_status == "SETTLED" and unknown.projection_status == "UNKNOWN"
    assert unknown.transaction_id is None
    with Session(demo_engine) as session:
        original = session.get(ExternalBankFact, unknown.external_fact_id)
        assert original is not None
        bank_original = (
            original.request_canonical_text,
            original.observed_at,
            original.bank_result_canonical_text,
            original.bank_result_hash,
        )
        rows = list(
            session.scalars(
                select(SimulatedBankPosting).where(
                    SimulatedBankPosting.external_fact_id == original.id
                )
            )
        )
        assert len(rows) == 2 and {row.id for row in rows} == set(unknown.economic_posting_ids)
    monkeypatch.setattr(external_bank_facts, "_project", project)
    completed = ingest_external_fact(demo_engine, DEMO_USER_ID, request, NOW + timedelta(minutes=1))
    assert completed.bank_status == "SETTLED" and completed.projection_status == "PROJECTED", (
        completed
    )
    assert completed.economic_posting_ids == unknown.economic_posting_ids
    with Session(demo_engine) as session:
        original = session.get(ExternalBankFact, unknown.external_fact_id)
        assert (
            original is not None
            and (
                original.request_canonical_text,
                original.observed_at,
                original.bank_result_canonical_text,
                original.bank_result_hash,
            )
            == bank_original
        )
        recorded = list(
            session.scalars(select(AuditEvent).where(AuditEvent.aggregate_id == original.id))
        )
        assert [row.event_type for row in recorded].count("EXTERNAL_BANK_FACT_SETTLED") == 1
        assert [row.event_type for row in recorded].count("EXTERNAL_BANK_FACT_PROJECTED") == 1
    unchanged = database_snapshot(demo_engine)
    assert (
        ingest_external_fact(demo_engine, DEMO_USER_ID, request, NOW + timedelta(minutes=2))
        == completed
    )
    assert database_snapshot(demo_engine) == unchanged


def test_same_ref_new_key_and_same_key_other_fact_are_conflicts_without_writes(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    request = fact_request(demo_engine)
    result = ingest_external_fact(demo_engine, DEMO_USER_ID, request, NOW)
    before = database_snapshot(demo_engine)
    for changes in (
        {"idempotency_key": "alias-key"},
        {"external_ref": "another-ref"},
        {"amount_cents": 1},
    ):
        changed = ExternalFactRequest.model_validate({**request.model_dump(), **changes})
        with pytest.raises(PolicyLifecycleError) as caught:
            ingest_external_fact(demo_engine, DEMO_USER_ID, changed, NOW)
        assert (
            caught.value.status_code == 409
            and caught.value.code == "EXTERNAL_BANK_IDEMPOTENCY_CONFLICT"
        )
        assert str(result.external_fact_id) in caught.value.message
        assert database_snapshot(demo_engine) == before
    with pytest.raises(PolicyLifecycleError) as foreign:
        ingest_external_fact(demo_engine, uuid4(), request, NOW)
    assert foreign.value.status_code == 404 and database_snapshot(demo_engine) == before


def test_missing_income_basis_preserves_settled_bank_and_does_not_repair_app_cash(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    owner, account_id = uuid4(), uuid4()
    with Session(demo_engine) as session, session.begin():
        session.add(
            User(
                id=owner,
                external_ref=str(owner),
                display_name="Explicit isolated simulated tenant",
                is_simulated=True,
                created_at=SEED_AS_OF,
            )
        )
        session.flush()
        session.add(
            Account(
                id=account_id,
                user_id=owner,
                external_ref="cash",
                name="Trusted opening",
                account_type="CASH",
                balance_cents=10000,
                observed_at=SEED_AS_OF,
                created_at=SEED_AS_OF,
            )
        )
        session.flush()
        content = {
            "simulation": True,
            "user_id": str(owner),
            "account_id": str(account_id),
            "account_type": "CASH",
            "currency": "CNY",
            "balance_cents": 10000,
            "as_of": SEED_AS_OF.isoformat(),
        }
        session.add(
            EvidenceItem(
                id=uuid4(),
                user_id=owner,
                created_at=SEED_AS_OF,
                source_type="SIMULATED_BANK_BALANCE",
                source_ref="trusted-opening",
                evidence_level="BANK_CONFIRMED",
                content=content,
                content_hash=configuration_hash(content),
                observed_at=SEED_AS_OF,
                valid_from=SEED_AS_OF,
                status="VALID",
            )
        )
        open_simulated_bank(
            session, owner, SEED_AS_OF, cash_balances={account_id: 10000}, position_principals={}
        )
        open_external_clearing(
            session, owner, SEED_AS_OF, counterparty_reserves={"payroll": 1000000}
        )
    request = ExternalFactRequest(
        user_id=owner,
        account_id=account_id,
        kind="INCOME",
        amount_cents=20000,
        external_ref="actual-payroll",
        idempotency_key="original-key",
        counterparty_ref="payroll",
        occurred_at=NOW,
    )
    result = ingest_external_fact(demo_engine, owner, request, NOW)
    assert result.bank_status == "SETTLED" and result.projection_status == "UNKNOWN", result
    assert result.transaction_id is None
    with Session(demo_engine) as session:
        account = session.get(Account, account_id)
        assert (
            account is not None
            and account.balance_cents == 10000
            and account.observed_at == SEED_AS_OF
        )
        assert ledger_heads(session, owner)["CASH:" + str(account_id)].balance_after_cents == 30000
        assert session.scalar(select(Transaction.id).where(Transaction.user_id == owner)) is None
        assert (
            session.scalar(
                select(EvidenceItem.id).where(
                    EvidenceItem.user_id == owner,
                    EvidenceItem.source_type == "SIMULATED_NEW_FUNDS_LEDGER",
                )
            )
            is None
        )
        assert (
            len(
                list(
                    session.scalars(
                        select(SimulatedBankPosting).where(
                            SimulatedBankPosting.external_fact_id == result.external_fact_id
                        )
                    )
                )
            )
            == 2
        )


@pytest.mark.parametrize("protected", ["allocated_goal", "active_income_claim"])
def test_actual_external_consumption_cannot_release_goal_or_pending_income_claims(
    demo_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    protected: str,
) -> None:
    seed_demo(demo_engine)
    with Session(demo_engine) as session, session.begin():
        account = session.scalars(
            select(Account).where(Account.user_id == DEMO_USER_ID, Account.account_type == "CASH")
        ).one()
        policy_id, version_id = confirmed_policy(
            session,
            {
                "type": "goal_saving",
                "name": "Protected actual cash",
                "target_cents": 100000,
                "deadline": "2027-10-01",
                "monthly_contribution": {"min_cents": 0, "target_cents": 10000, "max_cents": 10000},
            },
            SEED_AS_OF,
        )
        created = create_goal_projection(
            session, DEMO_USER_ID, policy_id, version_id, account.id, SEED_AS_OF
        )
        goal_id, account_id = created.goal.id, account.id
    salary = ingest_external_fact(demo_engine, DEMO_USER_ID, fact_request(demo_engine), NOW)
    assert salary.bank_status == "SETTLED" and salary.projection_status == "PROJECTED", salary
    prepared = prepare_action(
        demo_engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="actual-protected-claim",
            intent=GoalIntent(kind="allocate_goal", goal_id=goal_id),
        ),
        NOW,
    )
    confirm_action(
        demo_engine,
        DEMO_USER_ID,
        prepared.action_id,
        ConfirmActionRequest(effect_hash=prepared.effect_hash, accepted=True),
        NOW,
    )
    protected_cents = prepared.effect.amount_cents
    assert protected_cents == 10000
    if protected == "active_income_claim":

        def interrupted_before_bank_observation(*args: object, **kwargs: object) -> None:
            raise RuntimeError(
                "Simulated interrupted bank observation after real reservation commit"
            )

        monkeypatch.setattr(execution, "process_operation", interrupted_before_bank_observation)
        with pytest.raises(RuntimeError, match="interrupted bank observation"):
            execute_action(demo_engine, DEMO_USER_ID, prepared.action_id, NOW)
    else:
        completed = execute_action(demo_engine, DEMO_USER_ID, prepared.action_id, NOW)
        assert completed.status == "SUCCEEDED" and completed.receipt is not None
    with Session(demo_engine) as session:
        current = session.get(Account, account_id)
        goal = session.get(Goal, goal_id)
        action = session.get(ActionPlan, prepared.action_id)
        assert current is not None and goal is not None and action is not None
        balance = current.balance_cents
        before_income = read_income_state(session, DEMO_USER_ID, NOW)
        before_app = {
            model.__tablename__: [
                dict(row)
                for row in session.execute(select(model.__table__).order_by(model.id)).mappings()
            ]
            for model in (
                Account,
                Goal,
                ActionPlan,
                ActionResourceReservation,
                EvidenceItem,
                Transaction,
            )
        }
        if protected == "active_income_claim":
            assert action.status == "UNKNOWN" and goal.allocated_cents == 0
            assert (
                sum(row.reserved_cents for row in before_income.ledger.fragments) == protected_cents
            )
            assert (
                session.scalar(
                    select(ActionResourceReservation.id).where(
                        ActionResourceReservation.action_plan_id == prepared.action_id,
                        ActionResourceReservation.resource_kind == "CASH",
                        ActionResourceReservation.status == "RESERVED",
                    )
                )
                is not None
            )
        else:
            assert goal.allocated_cents == protected_cents
    request = ExternalFactRequest(
        user_id=DEMO_USER_ID,
        account_id=account_id,
        kind="CONSUMPTION",
        amount_cents=balance - protected_cents + 1,
        external_ref="funded-external-consumption",
        idempotency_key="external-original",
        counterparty_ref="merchant",
        occurred_at=NOW + timedelta(minutes=1),
    )
    result = ingest_external_fact(demo_engine, DEMO_USER_ID, request, NOW + timedelta(minutes=1))
    assert result.bank_status == "SETTLED" and result.projection_status == "UNKNOWN", result
    assert result.transaction_id is None and len(result.economic_posting_ids) == 2
    with Session(demo_engine) as session:
        after_app = {
            model.__tablename__: [
                dict(row)
                for row in session.execute(select(model.__table__).order_by(model.id)).mappings()
            ]
            for model in (
                Account,
                Goal,
                ActionPlan,
                ActionResourceReservation,
                EvidenceItem,
                Transaction,
            )
        }
        assert after_app == before_app
        assert read_income_state(session, DEMO_USER_ID, NOW + timedelta(minutes=1)) == before_income
        assert (
            ledger_heads(session, DEMO_USER_ID)["CASH:" + str(account_id)].balance_after_cents
            == protected_cents - 1
        )
        assert (
            len(
                list(
                    session.scalars(
                        select(SimulatedBankPosting).where(
                            SimulatedBankPosting.external_fact_id == result.external_fact_id
                        )
                    )
                )
            )
            == 2
        )
        with pytest.raises(PolicyLifecycleError):
            validate_bank_projection(session, DEMO_USER_ID, NOW + timedelta(minutes=1))
