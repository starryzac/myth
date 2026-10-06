"""Read-only cash boundary adaptation against isolated real PostgreSQL databases."""

import json
from collections.abc import Iterator, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.db.base import Base
from app.db.models import (
    Account,
    AssetPosition,
    AssetProduct,
    CreditCardBill,
    EvidenceItem,
    Goal,
    PolicyProposal,
    User,
)
from app.db.session import create_database_engine
from app.db.testing import temporary_database
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.services import demo_seed, external_bank_facts
from app.services.boundary import (
    AVAILABILITY_SOURCE,
    CONTRIBUTION_SOURCE,
    OWNERSHIP_SOURCE,
    SETTLEMENT_SOURCE,
    compute_user_boundary,
)
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, SeedSummary
from app.services.execution_exposure import refresh_execution_exposure
from app.services.policy_lifecycle import (
    PolicyLifecycleError,
    change_policy,
    confirm_proposal,
    revoke_policy,
    suspend_policy,
)
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[4]
pytestmark = pytest.mark.integration


def seed_legacy_income_fixture(engine: Engine) -> SeedSummary:
    """Import the old initial bank shape before income and external clearing existed."""

    def omit_income_import(_session: Session) -> None:
        return None

    def omit_external_clearing(
        _session: Session,
        _user_id: UUID,
        _now: datetime,
        *,
        counterparty_reserves: Mapping[str, int],
    ) -> None:
        return None

    # Omit these two fresh imports before genesis. Original CASH/POSITION
    # openings and audit genesis still run; no persisted original is deleted.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(demo_seed, "_seed_income_ledger", omit_income_import)
        patch.setattr(external_bank_facts, "open_external_clearing", omit_external_clearing)
        return demo_seed.seed_demo(engine)


@pytest.fixture
def boundary_engine() -> Iterator[Engine]:
    with temporary_database() as url:
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        command.upgrade(config, "head")
        engine = create_database_engine(url)
        try:
            seed_legacy_income_fixture(engine)
            yield engine
        finally:
            engine.dispose()


def snapshot(engine: Engine) -> str:
    with engine.connect() as connection:
        content = {
            table.name: [
                dict(row)
                for row in connection.execute(select(table).order_by(table.c.id)).mappings()
            ]
            for table in Base.metadata.sorted_tables
        }
    return json.dumps(content, sort_keys=True, default=str)


def test_seed_financial_boundary_protects_unassigned_goal_cash_without_writes(
    boundary_engine: Engine,
) -> None:
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        result = compute_user_boundary(session, DEMO_USER_ID, SEED_AS_OF)
        assert result.simulation is True
        assert result.boundary.status == "READY"
        assert result.boundary.safe_idle_cents == 3157400
        assert result.source_issues == []
        assert len(result.source_evidence_ids) == 11
        assert result == compute_user_boundary(session, DEMO_USER_ID, SEED_AS_OF)
    assert snapshot(boundary_engine) == before


def confirmed_policy(
    session: Session,
    configuration: dict[str, Any],
    now: datetime = SEED_AS_OF,
) -> tuple[UUID, UUID]:
    evidence = EvidenceItem(
        id=uuid4(),
        user_id=DEMO_USER_ID,
        evidence_level="USER_DECLARED",
        source_type="TEST_USER_INTENT",
        source_ref=str(uuid4()),
        content={},
        content_hash=configuration_hash({}),
        valid_from=now,
        observed_at=now,
        status="VALID",
    )
    session.add(evidence)
    session.flush()
    candidate = PolicyProposal(
        id=uuid4(),
        user_id=DEMO_USER_ID,
        source_type="TEST",
        compiler_version="fixture",
        proposed_configuration=configuration,
        evidence_ids=[str(evidence.id)],
        idempotency_key=str(uuid4()),
    )
    session.add(candidate)
    session.flush()
    result = confirm_proposal(
        session,
        DEMO_USER_ID,
        candidate.id,
        configuration_hash(validate_configuration(configuration)),
        True,
        now,
    )
    return result.policy_id, result.current_version_id


def test_confirmed_future_policy_protects_without_becoming_current_authority(
    boundary_engine: Engine,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        confirmed_policy(
            session,
            {
                "type": "emergency_buffer",
                "amount_cents": 100000,
                "valid_from": "2026-10-20",
            },
        )
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        result = compute_user_boundary(session, DEMO_USER_ID, SEED_AS_OF)
        assert result.boundary.status == "READY"
        assert result.boundary.safe_idle_cents == 3057400
    assert snapshot(boundary_engine) == before


def assert_insufficient(engine: Engine) -> None:
    before = snapshot(engine)
    with Session(engine) as session:
        result = compute_user_boundary(session, DEMO_USER_ID, SEED_AS_OF)
        assert result.boundary.status == "INSUFFICIENT_EVIDENCE"
        assert result.boundary.safe_idle_cents is None
        assert result.boundary.minimum_margin_cents is None
        assert result.boundary.deficit_cents is None
        assert result.source_issues
    assert snapshot(engine) == before


@pytest.mark.parametrize(
    "source",
    [
        "SIMULATED_BANK_BALANCE",
        "SIMULATED_CREDIT_CARD_BILL",
        "SIMULATED_BANK_POSITION",
    ],
)
@pytest.mark.parametrize(
    "case", ["missing", "hash", "future", "wrong_user", "conflicted", "duplicate"]
)
def test_source_identity_status_hash_and_known_time_are_required(
    boundary_engine: Engine,
    source: str,
    case: str,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        proof = session.scalar(select(EvidenceItem).where(EvidenceItem.source_type == source))
        assert proof is not None
        if case == "missing":
            # Bill has a required FK, so retain an explicitly superseded source record.
            proof.status = "SUPERSEDED"
        elif case == "hash":
            proof.content = {**proof.content, "changed": True}
        elif case == "future":
            proof.observed_at = SEED_AS_OF + timedelta(seconds=1)
        elif case == "wrong_user":
            proof.content = {**proof.content, "user_id": str(uuid4())}
            proof.content_hash = configuration_hash(proof.content)
        elif case == "conflicted":
            proof.status = "CONFLICTED"
        else:
            session.add(
                EvidenceItem(
                    id=uuid4(),
                    user_id=DEMO_USER_ID,
                    evidence_level=proof.evidence_level,
                    source_type=source,
                    source_ref=proof.source_ref,
                    content=proof.content,
                    content_hash=proof.content_hash,
                    valid_from=proof.valid_from,
                    observed_at=proof.observed_at,
                    status="VALID",
                )
            )
    assert_insufficient(boundary_engine)


def test_future_income_and_yield_cannot_change_financial_boundary(boundary_engine: Engine) -> None:
    with Session(boundary_engine) as session:
        original = compute_user_boundary(session, DEMO_USER_ID, SEED_AS_OF)
    with Session(boundary_engine) as session, session.begin():
        for product in session.scalars(select(AssetProduct)):
            product.annual_yield_bps = 9999
        for position in session.scalars(select(AssetPosition)):
            position.accrued_yield_cents = 999999999
            proof = session.scalar(
                select(EvidenceItem).where(
                    EvidenceItem.source_type == "SIMULATED_BANK_POSITION",
                    EvidenceItem.content["position_id"].as_string() == str(position.id),
                )
            )
            assert proof is not None
            proof.content = {**proof.content, "accrued_yield_cents": 999999999}
            proof.content_hash = configuration_hash(proof.content)
        content = {"future_income_cents": 999999999999, "due_date": "2026-10-20"}
        session.add(
            EvidenceItem(
                id=uuid4(),
                user_id=DEMO_USER_ID,
                evidence_level="MODEL_INFERRED",
                source_type="TEST_INCOME_FORECAST",
                source_ref="salary-forecast",
                content=content,
                content_hash=configuration_hash(content),
                valid_from=SEED_AS_OF,
                observed_at=SEED_AS_OF,
                status="VALID",
            )
        )
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        changed = compute_user_boundary(session, DEMO_USER_ID, SEED_AS_OF)
        assert changed.boundary == original.boundary
        assert changed.input_digest != original.input_digest
    assert snapshot(boundary_engine) == before


def imported_proof(session: Session, source: str, content: dict[str, Any]) -> EvidenceItem:
    content = {"simulation": True, "user_id": str(DEMO_USER_ID), **content}
    proof = EvidenceItem(
        id=uuid4(),
        user_id=DEMO_USER_ID,
        evidence_level="BANK_CONFIRMED",
        source_type=source,
        source_ref=str(uuid4()),
        content=content,
        content_hash=configuration_hash(content),
        valid_from=SEED_AS_OF,
        observed_at=SEED_AS_OF,
        status="VALID",
    )
    session.add(proof)
    return proof


def goal_fixture(session: Session, *, with_proofs: bool, starts_on: str | None = None) -> UUID:
    policy_id, version_id = confirmed_policy(
        session,
        {
            "type": "goal_saving",
            "name": "Target",
            "target_cents": 1000000,
            "valid_from": starts_on,
            "deadline": "2026-12-31",
            "monthly_contribution": {
                "min_cents": 100000,
                "target_cents": 150000,
                "max_cents": 200000,
            },
        },
    )
    account = session.scalar(select(Account).where(Account.account_type == "GOAL"))
    assert account is not None and account.balance_cents == 160000
    goal = Goal(
        id=uuid4(),
        user_id=DEMO_USER_ID,
        policy_id=policy_id,
        policy_version_id=version_id,
        account_id=account.id,
        name="Target",
        target_cents=1000000,
        allocated_cents=160000,
        deadline=date(2026, 12, 31),
        monthly_min_cents=100000,
        monthly_target_cents=150000,
        monthly_max_cents=200000,
    )
    session.add(goal)
    session.flush()
    if with_proofs:
        imported_proof(
            session,
            OWNERSHIP_SOURCE,
            {
                "protocol": "goal-ownership-v1",
                "goal_id": str(goal.id),
                "policy_id": str(policy_id),
                "account_id": str(account.id),
                "allocated_cents": 160000,
                "cash_owned_cents": 160000,
                "principal_owned_cents": 0,
                "position_ids": [],
                "as_of": SEED_AS_OF.isoformat(),
            },
        )
        imported_proof(
            session,
            CONTRIBUTION_SOURCE,
            {
                "protocol": "goal-month-contribution-v1",
                "goal_id": str(goal.id),
                "period": "2026-10",
                "contributed_cents": 40000,
                "complete": True,
                "as_of": SEED_AS_OF.isoformat(),
            },
        )
        # Trusted fixture import: inherit the complete new source manifest without moving funds.
        refresh_execution_exposure(session, DEMO_USER_ID, SEED_AS_OF, goal.id)
    return goal.id


def test_goal_allocated_total_cannot_substitute_for_monthly_contribution_evidence(
    boundary_engine: Engine,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        goal_fixture(session, with_proofs=False)
    assert_insufficient(boundary_engine)


def test_goal_import_partitions_cash_and_uses_proved_monthly_contributions(
    boundary_engine: Engine,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        goal_fixture(session, with_proofs=True)
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        result = compute_user_boundary(session, DEMO_USER_ID, SEED_AS_OF)
        assert result.boundary.status == "READY"
        assert result.boundary.safe_idle_cents == 2897400
    assert snapshot(boundary_engine) == before


@pytest.mark.parametrize("source", [OWNERSHIP_SOURCE, CONTRIBUTION_SOURCE])
def test_cumulative_goal_import_must_cover_current_balance_snapshot(
    boundary_engine: Engine,
    source: str,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        goal_fixture(session, with_proofs=True)
        session.flush()
        proof = session.scalar(select(EvidenceItem).where(EvidenceItem.source_type == source))
        assert proof is not None
        proof.content = {**proof.content, "as_of": (SEED_AS_OF - timedelta(days=1)).isoformat()}
        proof.content_hash = configuration_hash(proof.content)
    assert_insufficient(boundary_engine)


@pytest.mark.parametrize("case", ["duplicate", "stale"])
def test_settlement_import_is_unique_and_covers_current_balance(
    boundary_engine: Engine,
    case: str,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        policy_id, _ = confirmed_policy(
            session,
            {
                "type": "recurring_obligation",
                "payee_id": "rent-recipient",
                "amount_rule": {"kind": "exact", "amount_cents": 180000},
                "due_day": 28,
            },
        )
        content = {
            "protocol": "recurring-settlement-v1",
            "policy_id": str(policy_id),
            "period": "2026-10",
            "paid_cents": 0,
            "payee_id": "rent-recipient",
            "complete": True,
            "as_of": (
                SEED_AS_OF - timedelta(days=1) if case == "stale" else SEED_AS_OF
            ).isoformat(),
        }
        imported_proof(session, SETTLEMENT_SOURCE, content)
        if case == "duplicate":
            imported_proof(session, SETTLEMENT_SOURCE, content)
    assert_insufficient(boundary_engine)


def test_contradictory_bill_state_is_source_insufficiency_not_server_error(
    boundary_engine: Engine,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        bill = session.scalar(select(CreditCardBill).where(CreditCardBill.status == "UNPAID"))
        assert bill is not None
        proof = session.get(EvidenceItem, bill.evidence_id)
        assert proof is not None
        bill.status = "PAID"
        proof.content = {**proof.content, "status": "PAID"}
        proof.content_hash = configuration_hash(proof.content)
    assert_insufficient(boundary_engine)


def test_goal_ownership_cannot_exceed_its_cash_account(boundary_engine: Engine) -> None:
    with Session(boundary_engine) as session, session.begin():
        identifier = goal_fixture(session, with_proofs=True)
        goal = session.get(Goal, identifier)
        assert goal is not None
        goal.allocated_cents = 170000
        proof = session.scalar(
            select(EvidenceItem).where(EvidenceItem.source_type == OWNERSHIP_SOURCE)
        )
        assert proof is not None
        proof.content = {**proof.content, "allocated_cents": 170000, "cash_owned_cents": 170000}
        proof.content_hash = configuration_hash(proof.content)
    assert_insufficient(boundary_engine)


def test_future_goal_does_not_require_a_month_with_no_commitment(boundary_engine: Engine) -> None:
    with Session(boundary_engine) as session, session.begin():
        goal_fixture(session, with_proofs=True, starts_on="2026-11-01")
        session.flush()
        proof = session.scalar(
            select(EvidenceItem).where(EvidenceItem.source_type == CONTRIBUTION_SOURCE)
        )
        assert proof is not None
        session.delete(proof)
    with Session(boundary_engine) as session:
        result = compute_user_boundary(session, DEMO_USER_ID, SEED_AS_OF)
        assert result.boundary.status == "READY"
        assert result.boundary.safe_idle_cents == 2957400


def test_goal_principal_return_preserves_scope_without_double_protection(
    boundary_engine: Engine,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        identifier = goal_fixture(session, with_proofs=True)
        goal = session.get(Goal, identifier)
        position = session.scalar(
            select(AssetPosition).where(AssetPosition.principal_cents == 250000)
        )
        assert goal is not None and position is not None
        position.goal_id = identifier
        position.available_at = SEED_AS_OF + timedelta(days=5)
        goal.allocated_cents = 410000
        proof = session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.source_type == "SIMULATED_BANK_POSITION",
                EvidenceItem.content["position_id"].as_string() == str(position.id),
            )
        )
        ownership = session.scalar(
            select(EvidenceItem).where(EvidenceItem.source_type == OWNERSHIP_SOURCE)
        )
        assert proof is not None and ownership is not None
        proof.content = {
            **proof.content,
            "goal_id": str(identifier),
            "available_at": position.available_at.isoformat(),
        }
        proof.content_hash = configuration_hash(proof.content)
        ownership.content = {
            **ownership.content,
            "allocated_cents": 410000,
            "principal_owned_cents": 250000,
            "position_ids": [str(position.id)],
        }
        ownership.content_hash = configuration_hash(ownership.content)
        imported_proof(
            session,
            AVAILABILITY_SOURCE,
            {
                "protocol": "principal-availability-v1",
                "position_id": str(position.id),
                "account_id": str(position.account_id),
                "goal_id": str(identifier),
                "principal_cents": 250000,
                "available_at": position.available_at.isoformat(),
                "principal_return_bps": 10000,
                "rollover": False,
            },
        )
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        result = compute_user_boundary(session, DEMO_USER_ID, SEED_AS_OF)
        assert result.boundary.status == "READY"
        assert result.boundary.safe_idle_cents == 2897400
        initial = result.boundary.calculation_trace[0]
        arrived = next(
            point
            for point in result.boundary.calculation_trace
            if point.day == 5 and point.phase == "AFTER_PRINCIPAL"
        )
        assert arrived.cash_cents - initial.cash_cents == 250000
        assert (
            arrived.protected_cents_by_reason["goal_cash"]
            - initial.protected_cents_by_reason["goal_cash"]
            == 250000
        )
        assert arrived.margin_cents == initial.margin_cents
    assert snapshot(boundary_engine) == before


def test_confirmed_living_method_uses_ready_estimate_and_blocks_incomplete_history(
    boundary_engine: Engine,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        confirmed_policy(
            session,
            {
                "type": "living_reserve",
                "horizon_days": 14,
                "extra_buffer_cents": 50000,
                "method": {
                    "name": "rolling_window_quantile",
                    "lookback_days": 56,
                    "quantile": 0.8,
                    "essential_categories": ["food", "transport", "daily_necessities"],
                    "exclude_one_off": True,
                },
            },
        )
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        result = compute_user_boundary(session, DEMO_USER_ID, SEED_AS_OF)
        assert result.boundary.status == "READY"
        assert result.boundary.safe_idle_cents == 3029500
    assert snapshot(boundary_engine) == before
    with Session(boundary_engine) as session, session.begin():
        proof = session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.source_type == "SIMULATED_TRANSACTION_HISTORY_COVERAGE"
            )
        )
        assert proof is not None
        proof.status = "UNKNOWN"
    assert_insufficient(boundary_engine)


def test_policy_source_must_be_known_now_even_for_future_start(boundary_engine: Engine) -> None:
    with Session(boundary_engine) as session, session.begin():
        confirmed_policy(
            session,
            {
                "type": "emergency_buffer",
                "amount_cents": 100000,
                "valid_from": "2026-10-20",
            },
        )
        proof = session.scalar(
            select(EvidenceItem).where(EvidenceItem.source_type == "TEST_USER_INTENT")
        )
        assert proof is not None
        proof.observed_at = SEED_AS_OF + timedelta(days=1)
    assert_insufficient(boundary_engine)


@pytest.mark.parametrize("case", ["expired", "suspended", "revoked", "revised"])
def test_generated_old_obligation_cannot_disappear_when_policy_changes(
    boundary_engine: Engine,
    case: str,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        configuration = {
            "type": "recurring_obligation",
            "payee_id": "rent-recipient",
            "due_day": 15,
            "amount_rule": {"kind": "exact", "amount_cents": 50000},
            "valid_until": "2026-10-03" if case == "expired" else None,
        }
        policy_id, version_id = confirmed_policy(
            session,
            configuration,
            datetime(2026, 9, 1, tzinfo=UTC),
        )
        imported_proof(
            session,
            SETTLEMENT_SOURCE,
            {
                "protocol": "recurring-settlement-v1",
                "policy_id": str(policy_id),
                "period": "2026-09",
                "paid_cents": 0,
                "payee_id": "rent-recipient",
                "complete": True,
                "as_of": SEED_AS_OF.isoformat(),
            },
        )
        if case == "suspended":
            suspend_policy(session, DEMO_USER_ID, policy_id, version_id, SEED_AS_OF)
        elif case == "revoked":
            revoke_policy(session, DEMO_USER_ID, policy_id, version_id, SEED_AS_OF)
        elif case == "revised":
            changed = {**configuration, "amount_rule": {"kind": "exact", "amount_cents": 60000}}
            change_policy(
                session,
                DEMO_USER_ID,
                policy_id,
                version_id,
                changed,
                configuration_hash(validate_configuration(changed)),
                True,
                "Changed future amount",
                "revise-test",
                SEED_AS_OF,
            )
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        result = compute_user_boundary(session, DEMO_USER_ID, SEED_AS_OF)
        if case == "expired":
            assert result.boundary.status == "READY"
            assert result.boundary.safe_idle_cents == 3107400
        else:
            assert result.boundary.status == "INSUFFICIENT_EVIDENCE"
            assert result.boundary.safe_idle_cents is None
            assert any(
                issue.code == "HISTORICAL_OBLIGATION_RECONCILIATION_REQUIRED"
                for issue in result.source_issues
            )
    assert snapshot(boundary_engine) == before


@pytest.mark.parametrize("case", ["lock", "delay", "rollover"])
def test_product_return_contract_cannot_override_longer_lock(
    boundary_engine: Engine,
    case: str,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        product = session.scalar(
            select(AssetProduct).where(AssetProduct.asset_class == "FIXED_DEPOSIT")
        )
        assert product is not None and product.lock_days == 30
        product.maturity_rule = {
            "protocol": "fixed-principal-return-v1",
            "day_basis": "CALENDAR",
            "guaranteed": True,
            "term_days": 7 if case == "lock" else 30,
            "settlement_delay_days": 0,
            "principal_return_bps": 10000,
            "rollover": False,
            "auto_rollover": case == "rollover",
        }
        if case == "delay":
            product.redemption_delay_days = 1
    assert_insufficient(boundary_engine)


def test_newer_month_contribution_cannot_reduce_minimum_against_old_ownership(
    boundary_engine: Engine,
) -> None:
    older = SEED_AS_OF - timedelta(days=1)
    with Session(boundary_engine) as session, session.begin():
        goal_fixture(session, with_proofs=True)
        session.flush()
        for account in session.scalars(select(Account)):
            account.observed_at = older
            proof = session.scalar(
                select(EvidenceItem).where(
                    EvidenceItem.source_type == "SIMULATED_BANK_BALANCE",
                    EvidenceItem.content["account_id"].as_string() == str(account.id),
                )
            )
            assert proof is not None
            proof.content = {**proof.content, "as_of": older.isoformat()}
            proof.content_hash = configuration_hash(proof.content)
            proof.observed_at = proof.valid_from = older
        ownership = session.scalar(
            select(EvidenceItem).where(EvidenceItem.source_type == OWNERSHIP_SOURCE)
        )
        assert ownership is not None
        ownership.content = {**ownership.content, "as_of": older.isoformat()}
        ownership.content_hash = configuration_hash(ownership.content)
    assert_insufficient(boundary_engine)


def test_revision_before_any_due_date_keeps_a_precise_future_boundary(
    boundary_engine: Engine,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        configuration = {
            "type": "recurring_obligation",
            "payee_id": "rent-recipient",
            "due_day": 28,
            "amount_rule": {"kind": "exact", "amount_cents": 50000},
        }
        policy_id, version_id = confirmed_policy(session, configuration)
        changed = {**configuration, "amount_rule": {"kind": "exact", "amount_cents": 60000}}
        change_policy(
            session,
            DEMO_USER_ID,
            policy_id,
            version_id,
            changed,
            configuration_hash(validate_configuration(changed)),
            True,
            "Change before first due date",
            "early-revision",
            SEED_AS_OF,
        )
    with Session(boundary_engine) as session:
        result = compute_user_boundary(session, DEMO_USER_ID, SEED_AS_OF)
        assert result.boundary.status == "READY"
        assert result.boundary.safe_idle_cents == 2977400


def test_unsettled_position_cannot_claim_already_arrived_principal(boundary_engine: Engine) -> None:
    with Session(boundary_engine) as session, session.begin():
        position = session.scalar(select(AssetPosition))
        assert position is not None
        position.available_at = SEED_AS_OF - timedelta(hours=1)
        proof = session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.source_type == "SIMULATED_BANK_POSITION",
                EvidenceItem.content["position_id"].as_string() == str(position.id),
            )
        )
        assert proof is not None
        proof.content = {**proof.content, "available_at": position.available_at.isoformat()}
        proof.content_hash = configuration_hash(proof.content)
        imported_proof(
            session,
            AVAILABILITY_SOURCE,
            {
                "protocol": "principal-availability-v1",
                "position_id": str(position.id),
                "account_id": str(position.account_id),
                "goal_id": None,
                "principal_cents": position.principal_cents,
                "available_at": position.available_at.isoformat(),
                "principal_return_bps": 10000,
                "rollover": False,
            },
        )
    assert_insufficient(boundary_engine)


def test_other_user_and_invalid_server_clock_do_not_use_demo_facts(boundary_engine: Engine) -> None:
    identifier = uuid4()
    with Session(boundary_engine) as session, session.begin():
        session.add(User(id=identifier, external_ref=str(identifier), display_name="Other"))
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        other = compute_user_boundary(session, identifier, SEED_AS_OF)
        assert other.boundary.status == "INSUFFICIENT_EVIDENCE"
        assert other.source_evidence_ids == []
        assert other.boundary.safe_idle_cents is None
        for invalid in (SEED_AS_OF.replace(tzinfo=None), datetime.max.replace(tzinfo=UTC)):
            with pytest.raises(PolicyLifecycleError) as error:
                compute_user_boundary(session, DEMO_USER_ID, invalid)
            assert error.value.code == "INVALID_CLOCK"
        with pytest.raises(PolicyLifecycleError) as error:
            compute_user_boundary(session, uuid4(), SEED_AS_OF)
        assert error.value.status_code == 404
    assert snapshot(boundary_engine) == before
