"""Original aggregate funding partition, no database or financial execution."""

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from app.db.models import User
from app.domain.execution_types import CashUse
from app.domain.income_ledger import IncomeUse, location_id
from app.services import audit_chain as audit
from app.services import full_asset_execution_store as store
from app.services.full_asset_execution import partition_original_income
from app.services.policy_lifecycle import PolicyLifecycleError
from sqlalchemy.orm import Session


def income(amount: int) -> IncomeUse:
    return IncomeUse(
        fragment_id=location_id(UUID(int=2), UUID(int=1)),
        origin_transaction_id=UUID(int=2),
        account_id=UUID(int=1),
        amount_cents=amount,
    )


def test_one_actual_aggregate_read_is_partitioned_without_reusing_original_income() -> None:
    cash = [
        [CashUse(account_id=UUID(int=1), amount_cents=400)],
        [CashUse(account_id=UUID(int=1), amount_cents=400)],
    ]
    actual = [income(300)]
    result = partition_original_income(cash, actual)
    assert result == [[], [income(300)]]
    assert actual == [income(300)]
    assert sum(use.amount_cents for parts in result for use in parts) == 300


def test_original_fragment_can_be_split_but_its_denominator_stays_exact() -> None:
    cash = [
        [CashUse(account_id=UUID(int=1), amount_cents=200)],
        [CashUse(account_id=UUID(int=1), amount_cents=200)],
    ]
    assert partition_original_income(cash, [income(350)]) == [[income(150)], [income(200)]]


@pytest.mark.parametrize(
    "original", [[income(900)], [income(1).model_copy(update={"account_id": UUID(int=3)})]]
)
def test_missing_cash_scope_or_excess_original_source_is_not_relabelled(
    original: list[IncomeUse],
) -> None:
    with pytest.raises(ValueError, match="ORIGINAL_AGGREGATE_INCOME"):
        partition_original_income([[CashUse(account_id=UUID(int=1), amount_cents=100)]], original)


def test_portfolio_reader_scopes_one_invocation_and_never_keeps_a_request_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TOOL_ONLY wiring: synthetic RO admission, no database or audit success."""
    entered: list[object] = []
    admissions: list[Session] = []
    expected = cast(store.FullAssetExecutionResponse, object())
    monkeypatch.setattr(store, "_read_snapshot", admissions.append)
    # The SQL RO/RR gate is independently exercised by the real PG audit risk.
    # This synthetic admission only tests this reader's context lifetime.
    monkeypatch.setattr(audit, "stable_read_snapshot", lambda session: True)

    def read_original(
        session: Session, user_id: UUID, portfolio_id: UUID, now: datetime
    ) -> store.FullAssetExecutionResponse:
        context = audit._audit_read_scope.get()
        assert context is not None and context.session is session
        assert admissions[-1] is session
        entered.append(context)
        return expected

    monkeypatch.setattr(store, "_read_full_asset_execution", read_original)
    previous = audit._audit_read_scope.get()
    with Session() as session:
        for _ in range(2):
            assert (
                store.read_full_asset_execution(
                    session, UUID(int=1), UUID(int=2), datetime(2026, 10, 5, tzinfo=UTC)
                )
                is expected
            )
            assert audit._audit_read_scope.get() is previous
    assert len(entered) == 2 and entered[0] is not entered[1]


def test_portfolio_reader_exception_closes_its_audit_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TOOL_ONLY: original rejection propagates and leaves no request proof."""
    monkeypatch.setattr(store, "_read_snapshot", lambda session: None)
    monkeypatch.setattr(audit, "stable_read_snapshot", lambda session: True)

    def refuse(
        session: Session, user_id: UUID, portfolio_id: UUID, now: datetime
    ) -> store.FullAssetExecutionResponse:
        assert audit._audit_read_scope.get() is not None
        raise ValueError("retained original mismatch")

    monkeypatch.setattr(store, "_read_full_asset_execution", refuse)
    previous = audit._audit_read_scope.get()
    with Session() as session, pytest.raises(ValueError, match="retained original mismatch"):
        store.read_full_asset_execution(
            session, UUID(int=1), UUID(int=2), datetime(2026, 10, 5, tzinfo=UTC)
        )
    assert audit._audit_read_scope.get() is previous


def test_dirty_portfolio_reader_uses_original_no_reuse_fallback_without_flushing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TOOL_ONLY: real Session.new gate, no SQL or financial result doubled."""
    monkeypatch.setattr(store, "_read_snapshot", lambda session: None)
    expected = cast(store.FullAssetExecutionResponse, object())

    def read_original(
        session: Session, user_id: UUID, portfolio_id: UUID, now: datetime
    ) -> store.FullAssetExecutionResponse:
        assert session.new and audit._audit_read_scope.get() is None
        return expected

    monkeypatch.setattr(store, "_read_full_asset_execution", read_original)
    with Session() as session:
        pending = User(id=UUID(int=91), external_ref="scope-only", display_name="TOOL_ONLY")
        session.add(pending)
        assert (
            store.read_full_asset_execution(
                session, UUID(int=1), UUID(int=2), datetime(2026, 10, 5, tzinfo=UTC)
            )
            is expected
        )
        assert pending in session.new


def test_naive_clock_is_rejected_before_reader_scope_or_original_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(store, "_read_snapshot", lambda session: None)

    def unexpected_scope(session: Session) -> None:
        raise AssertionError("a naive server clock must never open a read scope")

    monkeypatch.setattr(store, "audit_read_scope", unexpected_scope)
    with Session() as session, pytest.raises(PolicyLifecycleError) as caught:
        store.read_full_asset_execution(session, UUID(int=1), UUID(int=2), datetime(2026, 10, 5))
    assert caught.value.code == "INVALID_CLOCK"
