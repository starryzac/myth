"""Fresh private experiment initial facts; not frozen24 or formal-history edits."""

from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from app.db.models import Account, EvidenceItem, SimulatedBankPosting
from app.domain.external_bank_fact_types import ExternalFactRequest
from app.domain.history_coverage import COVERAGE_SOURCE_TYPE
from app.services import demo_seed as seed
from app.services.action_contracts import ConfirmActionRequest, PrepareActionRequest, TransferIntent
from app.services.audit_chain import current_audit_epoch, verify_audit_chain
from app.services.execution import confirm_action, execute_action, prepare_action
from app.services.external_bank_facts import ingest_external_fact
from app.services.income_ledger import read_income_state
from app.tests.test_demo_seed import demo_engine as demo_engine
from app.tests.test_scenario_service_steps import original_rows
from sqlalchemy import select
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session

LOCAL = "postgresql+psycopg://unused@127.0.0.1:54329/bf_test_" + "a" * 32


@pytest.mark.parametrize(
    "url, options",
    [
        (LOCAL.replace("bf_test_" + "a" * 32, "bounded_funds"), {}),
        (LOCAL.replace("127.0.0.1", "localhost"), {}),
        (LOCAL.replace("127.0.0.1", "remote.invalid"), {}),
        (LOCAL.replace(":54329", ":5432"), {}),
        (LOCAL.replace("a" * 32, "invalid"), {}),
        (LOCAL, {"reset_key": "not-an-initialization"}),
        (LOCAL, {"expected_epoch_id": UUID(int=1)}),
        (LOCAL, {"check_expected_epoch": True}),
        (LOCAL, {"_experiment_seed_extension": "UNKNOWN"}),
        (LOCAL, {"_experiment_seed_extension": True}),
    ],
)
def test_private_initial_extension_refuses_before_any_database_session(
    url: str, options: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("An invalid experiment initialization must not construct a Session")

    monkeypatch.setattr(seed, "Session", forbidden)
    engine = cast(
        Engine, SimpleNamespace(url=make_url(url), dialect=SimpleNamespace(name="postgresql"))
    )
    options = {"_experiment_seed_extension": seed.SECOND_CASH_INITIAL_EXTENSION} | options
    with pytest.raises(ValueError):
        seed.seed_demo(engine, **options)


@pytest.mark.integration
def test_fresh_second_cash_has_original_zero_bank_scope_and_actual_conserved_transfer(
    demo_engine: Engine,
) -> None:
    summary = seed.seed_demo(
        demo_engine, _experiment_seed_extension="MVP503_SECOND_CASH_INITIAL_FACT_V1"
    )
    assert summary.seed_version == "mvp-301-v6" and summary.summary_version == "seed-summary-v3"
    assert summary.counts["accounts"] == 6 and summary.counts["policies"] == 0
    assert summary.counts["action_plans"] == summary.counts["action_receipts"] == 0
    # Current seed includes ten immutable income openings as well as cash/positions.
    assert summary.counts["simulated_bank_postings"] == 18
    assert summary.cash_balance_cents == 3462400  # Added opening is exactly zero.
    clock = seed.SEED_AS_OF + timedelta(seconds=1)
    with Session(demo_engine) as session:
        source = session.scalar(
            select(Account).where(Account.external_ref == seed.SEED_VERSION + ":account:cash")
        )
        target = session.scalar(
            select(Account).where(Account.external_ref == seed.SECOND_CASH_EXTERNAL_REF)
        )
        assert source is not None and target is not None
        source_id, target_id, source_balance = source.id, target.id, source.balance_cents
        assert target.account_type == "CASH" and target.currency == "CNY"
        assert target.user_id == seed.DEMO_USER_ID and target.balance_cents == 0
        opening = session.scalar(
            select(SimulatedBankPosting).where(
                SimulatedBankPosting.account_id == target.id,
                SimulatedBankPosting.entry_kind == "OPENING",
            )
        )
        assert opening is not None and opening.delta_cents == opening.balance_after_cents == 0
        assert opening.operation_id is None and opening.redemption_id is None
        extension = session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.source_type == "SIMULATED_SEED_INITIAL_EXTENSION"
            )
        )
        assert extension is not None and extension.content["creates_authority"] is False
        assert extension.content["account_id"] == str(target.id)
        coverage = session.scalar(
            select(EvidenceItem).where(EvidenceItem.source_type == COVERAGE_SOURCE_TYPE)
        )
        assert coverage is not None and str(target.id) in str(coverage.content)
        initial = read_income_state(session, seed.DEMO_USER_ID, clock)
        assert set(initial.ledger.scope_account_ids) == {source.id, target.id}
        assert {origin.origin_account_id for origin in initial.ledger.origins} == {source.id}
        assert {fragment.account_id for fragment in initial.ledger.fragments} == {source.id}
        initial_ledger = initial.ledger
        epoch = current_audit_epoch(session, seed.DEMO_USER_ID)
        assert epoch is not None and epoch.status == "OPEN"
        assert verify_audit_chain(session, seed.DEMO_USER_ID).status == "VALID"
    before = original_rows(demo_engine)
    assert len(before) == 24
    # Retained seed income and untracked opening capital are distinct. Also
    # ingest a real payroll fact; transferring old capital must not relabel it.
    income = ingest_external_fact(
        demo_engine,
        seed.DEMO_USER_ID,
        ExternalFactRequest(
            user_id=seed.DEMO_USER_ID,
            idempotency_key="DEVELOPMENT_second_cash_actual_income",
            external_ref="DEVELOPMENT_second_cash_actual_income",
            kind="INCOME",
            account_id=source_id,
            amount_cents=83047,
            counterparty_ref="payroll",
            occurred_at=clock,
        ),
        clock,
    )
    assert income.bank_status == "SETTLED" and income.projection_status == "PROJECTED"
    assert len(income.economic_posting_ids) == 2
    clock += timedelta(seconds=1)
    with Session(demo_engine) as session:
        source = session.get(Account, source_id)
        assert source is not None and source.balance_cents == source_balance + 83047
        source_balance = source.balance_cents
        prior_available = sum(fragment.available_cents for fragment in initial_ledger.fragments)
        initial_ledger = read_income_state(session, seed.DEMO_USER_ID, clock).ledger
        assert (
            sum(fragment.available_cents for fragment in initial_ledger.fragments)
            == prior_available + 83047
        )
    planned = prepare_action(
        demo_engine,
        seed.DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="DEVELOPMENT_second_cash_legitimate_transfer",
            intent=TransferIntent(
                kind="transfer_internal",
                source_account_id=source_id,
                destination_account_id=target_id,
                amount_cents=47143,
            ),
        ),
        clock,
    )
    assert planned.autonomy_level == "ASK_ONCE" and planned.receipt is None
    assert planned.prepared_validation.status == "CONFIRMATION_REQUIRED"
    assert planned.effect.income_uses == []  # This small transfer uses old untracked capital.
    confirmed = confirm_action(
        demo_engine,
        seed.DEMO_USER_ID,
        planned.action_id,
        ConfirmActionRequest(effect_hash=planned.effect_hash, accepted=True),
        clock + timedelta(seconds=1),
    )
    assert confirmed.effect_hash == planned.effect_hash
    actual = execute_action(
        demo_engine, seed.DEMO_USER_ID, planned.action_id, clock + timedelta(seconds=2)
    )
    assert actual.status == "SUCCEEDED" and actual.bank_status == "SETTLED"
    assert actual.receipt is not None and actual.receipt.executed_cents == 47143
    assert actual.receipt.fee_cents == actual.receipt.loss_cents == 0
    with Session(demo_engine) as session:
        source = session.get(Account, source_id)
        target = session.get(Account, target_id)
        assert source is not None and target is not None
        assert source.balance_cents == source_balance - 47143 and target.balance_cents == 47143
        legs = list(
            session.scalars(
                select(SimulatedBankPosting).where(
                    SimulatedBankPosting.operation_id == actual.receipt.bank_operation_id
                )
            )
        )
        cash_legs = [leg for leg in legs if leg.account_id in {source_id, target_id}]
        assert len(cash_legs) == 2 and sum(leg.delta_cents for leg in cash_legs) == 0
        assert sorted(leg.delta_cents for leg in cash_legs) == [-47143, 47143]
        resulting = read_income_state(session, seed.DEMO_USER_ID, clock + timedelta(seconds=3))
        assert resulting.ledger.origins == initial_ledger.origins
        assert (
            sum(
                fragment.available_cents
                for fragment in resulting.ledger.fragments
                if fragment.account_id == target_id
            )
            == 0
        )
        assert resulting.ledger.fragments == initial_ledger.fragments
        assert verify_audit_chain(session, seed.DEMO_USER_ID).status == "VALID"
    settled = original_rows(demo_engine)
    replay = execute_action(
        demo_engine, seed.DEMO_USER_ID, planned.action_id, clock + timedelta(seconds=4)
    )
    assert replay.receipt == actual.receipt and replay.effect_hash == planned.effect_hash
    assert original_rows(demo_engine) == settled


@pytest.mark.integration
def test_original_default_seed_and_epoch_cannot_be_replaced_by_private_initial_extension(
    demo_engine: Engine,
) -> None:
    original = seed.seed_demo(demo_engine)
    assert original.counts["accounts"] == 5 and original.counts["simulated_bank_postings"] == 17
    before = original_rows(demo_engine)
    with pytest.raises(seed.SeedConflictError, match="cannot replace"):
        seed.seed_demo(demo_engine, _experiment_seed_extension="MVP503_SECOND_CASH_INITIAL_FACT_V1")
    assert original_rows(demo_engine) == before
    with Session(demo_engine) as session:
        assert verify_audit_chain(session, seed.DEMO_USER_ID).status == "VALID"
        assert (
            session.scalar(
                select(Account.id).where(Account.external_ref == seed.SECOND_CASH_EXTERNAL_REF)
            )
            is None
        )
