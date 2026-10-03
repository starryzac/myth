"""Deterministic observation proposals against disposable PostgreSQL databases."""

import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.db.base import Base
from app.db.models import (
    Account,
    CreditCardBill,
    EvidenceItem,
    Policy,
    PolicyProposal,
    PolicyVersion,
    Transaction,
    User,
)
from app.db.session import create_database_engine
from app.db.testing import temporary_database
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, seed_demo
from app.services.policy_discovery import DiscoveryResult, discover_policies
from app.services.policy_lifecycle import (
    PolicyLifecycleError,
    confirm_proposal,
    is_version_authorized,
)
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[4]
pytestmark = pytest.mark.integration


@pytest.fixture
def discovery_engine() -> Iterator[Engine]:
    with temporary_database() as url:
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        command.upgrade(config, "head")
        engine = create_database_engine(url)
        try:
            seed_demo(engine)
            yield engine
        finally:
            engine.dispose()


def protected_snapshot(engine: Engine) -> str:
    with engine.connect() as connection:
        snapshot = {
            table.name: [
                dict(row)
                for row in connection.execute(select(table).order_by(table.c.id)).mappings()
            ]
            for table in Base.metadata.sorted_tables
            if table.name not in {"evidence_items", "policy_proposals"}
        }
    return json.dumps(snapshot, sort_keys=True, default=str)


def test_seed_yields_only_rent_and_bill_observations_without_authority(
    discovery_engine: Engine,
) -> None:
    before = protected_snapshot(discovery_engine)
    with Session(discovery_engine) as session, session.begin():
        result = discover_policies(session, DEMO_USER_ID, SEED_AS_OF)
        assert result.simulation is True
        assert result.rule_version == "mvp-104-v1"
        assert result.user_id == DEMO_USER_ID
        assert result.as_of == SEED_AS_OF
        assert len(result.created_proposal_ids) == 2
        assert result.reused_proposal_ids == []
        proposals = session.scalars(select(PolicyProposal)).all()
        assert len(proposals) == 2
        assert {proposal.status for proposal in proposals} == {"PROPOSED"}
        rent = next(p for p in proposals if p.proposed_configuration["due_day"] == 28)
        card = next(p for p in proposals if p.proposed_configuration["due_day"] == 20)
        assert rent.proposed_configuration["amount_rule"] == {
            "kind": "range",
            "min_cents": 180000,
            "max_cents": 180000,
        }
        assert rent.proposed_configuration["name"] == "房租预留候选"
        assert card.proposed_configuration["amount_rule"]["kind"] == "bill_balance"
        assert card.proposed_configuration["payee_id"].startswith("credit-card:")
        for proposal in proposals:
            assert proposal.proposed_configuration == validate_configuration(
                proposal.proposed_configuration
            )
            assert proposal.proposed_configuration["auto_execute"] is False
            assert proposal.proposed_configuration["valid_from"] is None
            assert proposal.proposed_configuration["valid_until"] is None
            assert proposal.confirmed_policy_id is None
            evidence = session.scalars(
                select(EvidenceItem).where(EvidenceItem.id.in_(proposal.evidence_ids))
            ).all()
            assert len(evidence) == 3
            derived = next(e for e in evidence if e.evidence_level == "BANK_OBSERVED")
            assert derived.source_type == "DETERMINISTIC_POLICY_DISCOVERY"
            assert derived.content_hash == configuration_hash(derived.content)
            assert derived.content["rule_version"] == result.rule_version
            assert derived.content["future_obligation_guaranteed"] is False
            assert len(derived.content["sources"]) == 2
            assert derived.content["observation_window"] == {
                "start_date": "2026-08-05",
                "end_date": "2026-10-03",
                "timezone": "Asia/Shanghai",
                "days": 60,
            }
    assert protected_snapshot(discovery_engine) == before


def sync_transaction_evidence(session: Session, row: Transaction) -> EvidenceItem:
    evidence = session.get(EvidenceItem, row.evidence_id)
    assert evidence is not None
    evidence.content = {
        **evidence.content,
        "transaction_id": str(row.id),
        "account_id": str(row.account_id),
        "direction": row.direction,
        "amount_cents": row.amount_cents,
        "occurred_at": row.occurred_at.isoformat(),
        "counterparty_ref": row.counterparty_ref,
    }
    evidence.content_hash = configuration_hash(evidence.content)
    return evidence


@pytest.mark.parametrize(
    "case",
    [
        "different_payee",
        "empty_payee",
        "same_month",
        "day_drift",
        "amount_drift",
        "one_off",
        "income",
        "internal_transfer",
        "asset_purchase",
        "card_payment",
        "future_transaction",
        "future_observation",
        "missing_evidence",
        "hash_corrupt",
        "conflicted",
        "unknown",
        "future_evidence",
        "expired_evidence",
        "unconfirmed_evidence",
        "spliced_fact",
        "wrong_account",
        "wrong_evidence_amount",
    ],
)
def test_rent_pattern_and_evidence_fail_closed(discovery_engine: Engine, case: str) -> None:
    discover(discovery_engine)
    with Session(discovery_engine) as session, session.begin():
        rent = session.scalar(
            select(Transaction)
            .where(Transaction.category == "rent")
            .order_by(Transaction.occurred_at.desc())
        )
        assert rent is not None
        evidence = session.get(EvidenceItem, rent.evidence_id)
        assert evidence is not None
        if case == "different_payee":
            rent.counterparty_ref = "different-landlord"
        elif case == "empty_payee":
            rent.counterparty_ref = ""
        elif case == "same_month":
            rent.occurred_at = rent.occurred_at.replace(month=8)
        elif case == "day_drift":
            rent.occurred_at += timedelta(days=1)
        elif case == "amount_drift":
            rent.amount_cents += 100
        elif case == "one_off":
            rent.is_one_off = True
        elif case == "income":
            rent.direction = "CREDIT"
        elif case in {"internal_transfer", "asset_purchase", "card_payment"}:
            rent.category = "credit_card_payment" if case == "card_payment" else case
        elif case == "future_transaction":
            rent.occurred_at = SEED_AS_OF + timedelta(seconds=1)
        elif case == "future_observation":
            rent.observed_at = SEED_AS_OF + timedelta(seconds=1)
        elif case == "missing_evidence":
            rent.evidence_id = None
        sync_transaction_evidence(session, rent) if rent.evidence_id else None
        if case == "hash_corrupt":
            evidence.content = {**evidence.content, "amount_cents": 1}
        elif case in {"conflicted", "unknown"}:
            evidence.status = case.upper()
        elif case == "future_evidence":
            evidence.observed_at = SEED_AS_OF + timedelta(seconds=1)
        elif case == "expired_evidence":
            evidence.valid_to = SEED_AS_OF
        elif case == "unconfirmed_evidence":
            evidence.evidence_level = "BANK_OBSERVED"
        elif case in {"spliced_fact", "wrong_account", "wrong_evidence_amount"}:
            key = {
                "spliced_fact": "transaction_id",
                "wrong_account": "account_id",
                "wrong_evidence_amount": "amount_cents",
            }[case]
            evidence.content = {
                **evidence.content,
                key: 1 if key == "amount_cents" else str(uuid4()),
            }
            evidence.content_hash = configuration_hash(evidence.content)
    result = discover(discovery_engine)
    assert result.created_proposal_ids == []
    assert len(result.reused_proposal_ids) == 1
    with Session(discovery_engine) as session:
        rent_proposal = session.scalar(
            select(PolicyProposal).where(
                PolicyProposal.proposed_configuration["due_day"].as_integer() == 28
            )
        )
        assert rent_proposal is not None and rent_proposal.status == "EXPIRED"


@pytest.mark.parametrize(
    "case",
    [
        "nonconsecutive",
        "duplicate_period",
        "due_drift",
        "future_statement",
        "statement_outside_window",
        "spliced_source",
        "wrong_amount",
        "wrong_date",
        "conflicted",
    ],
)
def test_card_periods_use_due_month_and_require_valid_issued_evidence(
    discovery_engine: Engine,
    case: str,
) -> None:
    with Session(discovery_engine) as session, session.begin():
        bill = session.scalar(select(CreditCardBill).order_by(CreditCardBill.statement_date.desc()))
        assert bill is not None
        evidence = session.get(EvidenceItem, bill.evidence_id)
        assert evidence is not None
        if case == "nonconsecutive":
            bill.due_date = bill.due_date.replace(month=11)
        elif case == "duplicate_period":
            bill.statement_date = date(2026, 9, 19)
            bill.due_date = date(2026, 9, 20)
        elif case == "due_drift":
            bill.due_date = bill.due_date.replace(day=21)
        elif case == "future_statement":
            bill.statement_date = date(2026, 10, 4)
        elif case == "statement_outside_window":
            bill.statement_date = date(2026, 8, 4)
        evidence.content = {
            **evidence.content,
            "statement_date": bill.statement_date.isoformat(),
            "due_date": bill.due_date.isoformat(),
        }
        if case == "spliced_source":
            evidence.source_ref = "unrelated-statement"
        elif case == "wrong_amount":
            evidence.content = {**evidence.content, "total_cents": 1}
        elif case == "wrong_date":
            evidence.content = {**evidence.content, "due_date": "2026-10-21"}
        elif case == "conflicted":
            evidence.status = "CONFLICTED"
        evidence.content_hash = configuration_hash(evidence.content)
    result = discover(discovery_engine)
    assert len(result.created_proposal_ids) == 1
    with Session(discovery_engine) as session:
        proposal = session.get(PolicyProposal, result.created_proposal_ids[0])
        assert proposal is not None and proposal.proposed_configuration["due_day"] == 28


def test_discovery_never_joins_different_users(
    discovery_engine: Engine,
) -> None:
    other_id = uuid4()
    with Session(discovery_engine) as session, session.begin():
        user = User(
            id=other_id,
            external_ref=f"isolated-{other_id}",
            display_name="隔离用户",
            timezone="Asia/Shanghai",
            is_simulated=True,
        )
        session.add(user)
        session.flush()
        # Put one valid rent record in each user. Neither has two months of own observations.
        original = session.scalar(
            select(Transaction)
            .where(Transaction.category == "rent")
            .order_by(Transaction.occurred_at)
        )
        assert original is not None
        original.category = "not_rent"
        account = Account(
            id=uuid4(),
            user_id=other_id,
            external_ref="other-cash",
            name="他人账户",
            account_type="CASH",
            balance_cents=0,
            observed_at=SEED_AS_OF,
        )
        session.add(account)
        session.flush()
        row_id, evidence_id = uuid4(), uuid4()
        content = {
            "transaction_id": str(row_id),
            "account_id": str(account.id),
            "direction": "DEBIT",
            "amount_cents": 180000,
            "balance_after_cents": 0,
            "occurred_at": original.occurred_at.isoformat(),
            "counterparty_ref": original.counterparty_ref,
        }
        session.add(
            EvidenceItem(
                id=evidence_id,
                user_id=other_id,
                evidence_level="BANK_CONFIRMED",
                source_type="TEST",
                source_ref="other-rent",
                content=content,
                content_hash=configuration_hash(content),
                valid_from=original.occurred_at,
                observed_at=original.occurred_at,
                status="VALID",
            )
        )
        session.flush()
        session.add(
            Transaction(
                id=row_id,
                user_id=other_id,
                account_id=account.id,
                evidence_id=evidence_id,
                source_ref="other-rent",
                direction="DEBIT",
                amount_cents=180000,
                balance_after_cents=0,
                category="rent",
                counterparty_ref=original.counterparty_ref,
                is_one_off=False,
                category_confirmed=False,
                occurred_at=original.occurred_at,
                observed_at=original.occurred_at,
            )
        )
    assert len(discover(discovery_engine).created_proposal_ids) == 1
    with Session(discovery_engine) as session, session.begin():
        isolated = discover_policies(session, other_id, SEED_AS_OF)
        assert isolated.created_proposal_ids == []
        assert isolated.reused_proposal_ids == []


@pytest.mark.parametrize("inside_window", [True, False])
def test_window_includes_local_midnight_and_expires_when_it_moves(
    discovery_engine: Engine,
    inside_window: bool,
) -> None:
    with Session(discovery_engine) as session, session.begin():
        rows = session.scalars(
            select(Transaction)
            .where(Transaction.category == "rent")
            .order_by(Transaction.occurred_at)
        ).all()
        rows[0].occurred_at = datetime(2026, 8, 4, 16, tzinfo=UTC)
        rows[1].occurred_at = datetime(2026, 9, 4, 16, tzinfo=UTC)
        if not inside_window:
            for row in rows:
                row.occurred_at -= timedelta(seconds=1)
        for row in rows:
            sync_transaction_evidence(session, row)
    first = discover(discovery_engine)
    assert len(first.created_proposal_ids) == (2 if inside_window else 1)
    if inside_window:
        second = discover(discovery_engine, SEED_AS_OF + timedelta(days=1))
        assert second.created_proposal_ids == []
        assert len(second.reused_proposal_ids) == 1
        with Session(discovery_engine) as session:
            assert (
                len(
                    session.scalars(
                        select(PolicyProposal).where(PolicyProposal.status == "EXPIRED")
                    ).all()
                )
                == 1
            )


def test_database_failure_rolls_back_partial_discovery_but_preserves_caller_transaction(
    discovery_engine: Engine,
) -> None:
    with discovery_engine.begin() as connection:
        connection.execute(
            text("""
            CREATE FUNCTION reject_card_proposal() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.proposed_configuration ->> 'due_day' = '20' THEN
                    RAISE EXCEPTION 'injected database failure after rent proposal';
                END IF;
                RETURN NEW;
            END $$
        """)
        )
        connection.execute(
            text("""
            CREATE TRIGGER fail_card BEFORE INSERT ON policy_proposals
            FOR EACH ROW EXECUTE FUNCTION reject_card_proposal()
        """)
        )
    with Session(discovery_engine) as session, session.begin():
        user = session.get(User, DEMO_USER_ID)
        assert user is not None
        user.display_name = "外层事务保留"
        with pytest.raises(DBAPIError, match="injected database failure"):
            discover_policies(session, DEMO_USER_ID, SEED_AS_OF)
        assert session.scalars(select(PolicyProposal)).all() == []
        assert (
            session.scalars(
                select(EvidenceItem).where(
                    EvidenceItem.source_type == "DETERMINISTIC_POLICY_DISCOVERY"
                )
            ).all()
            == []
        )
    with Session(discovery_engine) as session:
        user = session.get(User, DEMO_USER_ID)
        assert user is not None and user.display_name == "外层事务保留"


def test_confirmation_and_discovery_share_user_serialization(discovery_engine: Engine) -> None:
    discover(discovery_engine)
    with Session(discovery_engine) as session:
        proposal = session.scalar(select(PolicyProposal))
        assert proposal is not None
        proposal_id, digest = proposal.id, configuration_hash(proposal.proposed_configuration)
    barrier = Barrier(2)

    def confirm() -> None:
        barrier.wait(timeout=10)
        with Session(discovery_engine) as session, session.begin():
            confirm_proposal(session, DEMO_USER_ID, proposal_id, digest, True, SEED_AS_OF)

    def scan() -> None:
        barrier.wait(timeout=10)
        discover(discovery_engine)

    with ThreadPoolExecutor(max_workers=2) as executor:
        for future in [executor.submit(confirm), executor.submit(scan)]:
            future.result(timeout=30)
    with Session(discovery_engine) as session:
        proposal = session.get(PolicyProposal, proposal_id)
        assert proposal is not None and proposal.status == "CONFIRMED"
        versions = session.scalars(select(PolicyVersion)).all()
        assert len(versions) == 1
        assert is_version_authorized(session, DEMO_USER_ID, versions[0].id, SEED_AS_OF)


def test_invalid_clock_or_user_does_not_create_candidates(discovery_engine: Engine) -> None:
    with Session(discovery_engine) as session, session.begin():
        with pytest.raises(PolicyLifecycleError, match="必须带时区"):
            discover_policies(session, DEMO_USER_ID, SEED_AS_OF.replace(tzinfo=None))
        with pytest.raises(PolicyLifecycleError) as error:
            discover_policies(session, uuid4(), SEED_AS_OF)
        assert error.value.status_code == 404
        assert session.scalars(select(PolicyProposal)).all() == []


def discover(engine: Engine, now: datetime = SEED_AS_OF) -> DiscoveryResult:
    with Session(engine) as session, session.begin():
        return discover_policies(session, DEMO_USER_ID, now)


def change_rent(session: Session, amount_cents: int, *, both: bool) -> None:
    rows = session.scalars(
        select(Transaction).where(Transaction.category == "rent").order_by(Transaction.occurred_at)
    ).all()
    for row in rows if both else rows[:1]:
        row.amount_cents = amount_cents
        evidence = session.get(EvidenceItem, row.evidence_id)
        assert evidence is not None
        evidence.content = {**evidence.content, "amount_cents": amount_cents}
        evidence.content_hash = configuration_hash(evidence.content)


def test_repeated_observations_reuse_ids_even_when_clock_advances(discovery_engine: Engine) -> None:
    first = discover(discovery_engine)
    second = discover(discovery_engine, SEED_AS_OF + timedelta(days=1))
    assert second.created_proposal_ids == []
    assert set(second.reused_proposal_ids) == set(first.created_proposal_ids)
    with Session(discovery_engine) as session:
        assert len(session.scalars(select(PolicyProposal)).all()) == 2
        assert (
            len(
                session.scalars(
                    select(EvidenceItem).where(
                        EvidenceItem.source_type == "DETERMINISTIC_POLICY_DISCOVERY"
                    )
                ).all()
            )
            == 2
        )


def test_concurrent_discovery_creates_one_revision_per_object(discovery_engine: Engine) -> None:
    barrier = Barrier(2)

    def run() -> DiscoveryResult:
        barrier.wait(timeout=10)
        return discover(discovery_engine)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [
            future.result(timeout=30) for future in [executor.submit(run), executor.submit(run)]
        ]
    assert sorted(len(result.created_proposal_ids) for result in results) == [0, 2]
    assert sorted(len(result.reused_proposal_ids) for result in results) == [0, 2]


@pytest.mark.parametrize("status", ["REJECTED", "EXPIRED"])
def test_closed_candidates_are_never_revived(discovery_engine: Engine, status: str) -> None:
    first = discover(discovery_engine)
    with Session(discovery_engine) as session, session.begin():
        for proposal in session.scalars(select(PolicyProposal)):
            proposal.status = status
    repeated = discover(discovery_engine)
    assert repeated.created_proposal_ids == []
    assert repeated.reused_proposal_ids == []
    assert len(repeated.skipped) == 2
    with Session(discovery_engine) as session:
        proposals = session.scalars(select(PolicyProposal)).all()
        assert {row.id for row in proposals} == set(first.created_proposal_ids)
        assert {row.status for row in proposals} == {status}


def test_new_revision_expires_old_proposal_and_invalid_pattern_expires_latest(
    discovery_engine: Engine,
) -> None:
    discover(discovery_engine)
    with Session(discovery_engine) as session, session.begin():
        old = session.scalar(
            select(PolicyProposal).where(
                PolicyProposal.proposed_configuration["due_day"].as_integer() == 28
            )
        )
        assert old is not None
        old_id = old.id
        change_rent(session, 190000, both=True)
    changed = discover(discovery_engine)
    assert len(changed.created_proposal_ids) == 1
    assert len(changed.reused_proposal_ids) == 1
    with Session(discovery_engine) as session, session.begin():
        old = session.get(PolicyProposal, old_id)
        assert old is not None and old.status == "EXPIRED"
        latest = session.get(PolicyProposal, changed.created_proposal_ids[0])
        assert latest is not None and latest.status == "PROPOSED"
        assert latest.proposed_configuration["amount_rule"]["max_cents"] == 190000
        change_rent(session, 200000, both=False)
    invalidated = discover(discovery_engine)
    assert invalidated.created_proposal_ids == []
    with Session(discovery_engine) as session:
        latest = session.get(PolicyProposal, changed.created_proposal_ids[0])
        assert latest is not None and latest.status == "EXPIRED"


def test_confirmation_alone_creates_authority_and_suppresses_new_discovery(
    discovery_engine: Engine,
) -> None:
    discover(discovery_engine)
    with Session(discovery_engine) as session, session.begin():
        proposal = session.scalar(
            select(PolicyProposal).where(
                PolicyProposal.proposed_configuration["due_day"].as_integer() == 28
            )
        )
        assert proposal is not None
        result = confirm_proposal(
            session,
            DEMO_USER_ID,
            proposal.id,
            configuration_hash(proposal.proposed_configuration),
            True,
            SEED_AS_OF,
        )
        policy = session.get(Policy, result.policy_id)
        version = session.get(PolicyVersion, result.current_version_id)
        assert policy is not None and version is not None
        assert is_version_authorized(session, DEMO_USER_ID, version.id, SEED_AS_OF)
        change_rent(session, 190000, both=True)
    before = protected_snapshot(discovery_engine)
    discovered = discover(discovery_engine)
    assert discovered.created_proposal_ids == []
    assert any(item.reason_code == "ALREADY_CONFIRMED" for item in discovered.skipped)
    assert protected_snapshot(discovery_engine) == before


@pytest.mark.parametrize(
    "change", ["corrected_amount", "source_ref", "source_level", "missing_source"]
)
def test_confirmation_rejects_changed_discovery_evidence_without_rediscovery(
    discovery_engine: Engine,
    change: str,
) -> None:
    discover(discovery_engine)
    with Session(discovery_engine) as session, session.begin():
        proposal = session.scalar(
            select(PolicyProposal).where(
                PolicyProposal.proposed_configuration["due_day"].as_integer() == 28
            )
        )
        assert proposal is not None
        proposal_id = proposal.id
        reviewed_hash = configuration_hash(proposal.proposed_configuration)
        transaction = session.scalar(select(Transaction).where(Transaction.category == "rent"))
        assert transaction is not None
        source = session.get(EvidenceItem, transaction.evidence_id)
        assert source is not None
        if change == "corrected_amount":
            change_rent(session, 190000, both=True)
        elif change == "source_ref":
            source.source_ref = "different-bank-source"
        elif change == "source_level":
            source.evidence_level = "BANK_OBSERVED"
        elif change == "missing_source":
            proposal.evidence_ids = [
                identifier for identifier in proposal.evidence_ids if identifier != str(source.id)
            ]
    with Session(discovery_engine) as session, session.begin():
        with pytest.raises(PolicyLifecycleError) as error:
            confirm_proposal(session, DEMO_USER_ID, proposal_id, reviewed_hash, True, SEED_AS_OF)
        assert error.value.code == "INVALID_EVIDENCE"
        proposal = session.get(PolicyProposal, proposal_id)
        assert proposal is not None and proposal.status == "PROPOSED"
        assert session.scalars(select(Policy)).all() == []
        assert session.scalars(select(PolicyVersion)).all() == []
