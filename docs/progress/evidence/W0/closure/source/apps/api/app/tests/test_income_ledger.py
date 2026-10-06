"""Literal income conservation tests at the public reserve/commit seam."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.domain.income_ledger import (
    IncomeFragment,
    IncomeLedger,
    IncomeOrigin,
    IncomeUse,
    bank_location_snapshot,
    commit_income,
    location_id,
    release_income,
    reserve_income,
)
from hypothesis import given, settings
from hypothesis import strategies as st

NOW = datetime(2026, 10, 4, tzinfo=UTC)
ORIGIN, SOURCE, DESTINATION, USER = (UUID(int=value) for value in range(1, 5))


def ledger_fixture() -> IncomeLedger:
    return IncomeLedger(
        user_id=USER,
        as_of=NOW,
        scope_account_ids=(SOURCE, DESTINATION),
        origins=(
            IncomeOrigin(
                origin_transaction_id=ORIGIN,
                origin_account_id=SOURCE,
                amount_cents=1000,
                occurred_at=NOW,
                observed_at=NOW,
                bank_evidence_id=UUID(int=5),
                bank_evidence_hash="a" * 64,
            ),
        ),
        fragments=(
            IncomeFragment(
                fragment_id=location_id(ORIGIN, SOURCE),
                origin_transaction_id=ORIGIN,
                account_id=SOURCE,
                spent_cents=100,
                assigned_cents=100,
                reserved_cents=100,
                legacy_reserved_cents=100,
                available_cents=700,
            ),
        ),
    )


def test_partial_transfer_retains_origin_and_splits_only_available_location() -> None:
    before = ledger_fixture()
    use = IncomeUse(
        fragment_id=location_id(ORIGIN, SOURCE),
        origin_transaction_id=ORIGIN,
        account_id=SOURCE,
        amount_cents=300,
    )
    reserved = reserve_income(
        before, UUID(int=10), [use], "TRANSFER_INTERNAL", NOW, destination_account_id=DESTINATION
    )
    assert reserved.fragments[0].available_cents == 400
    assert reserved.fragments[0].reserved_cents == 400
    moved = commit_income(reserved, UUID(int=10), NOW)
    assert moved.origins == before.origins
    fragments = {fragment.account_id: fragment for fragment in moved.fragments}
    assert fragments[SOURCE].available_cents == 400
    assert fragments[SOURCE].reserved_cents == fragments[SOURCE].legacy_reserved_cents == 100
    assert fragments[DESTINATION].available_cents == 300
    assert fragments[DESTINATION].fragment_id == location_id(ORIGIN, DESTINATION)
    assert commit_income(moved, UUID(int=10), NOW) == moved
    assert before.fragments[0].available_cents == 700


@pytest.mark.parametrize(
    "case", ["unbalanced", "legacy", "fake_fragment", "duplicate_origin", "scope", "unknown_claim"]
)
def test_unproved_or_nonconserved_source_components_are_rejected(case: str) -> None:
    value = ledger_fixture().model_dump()
    fragment = value["fragments"][0]
    if case == "unbalanced":
        fragment["available_cents"] += 1
    elif case == "legacy":
        fragment["legacy_reserved_cents"] += 1
    elif case == "fake_fragment":
        fragment["fragment_id"] = UUID(int=99)
    elif case == "duplicate_origin":
        value["origins"] = (*value["origins"], value["origins"][0])
    elif case == "scope":
        value["scope_account_ids"] = (DESTINATION,)
    else:
        fragment["legacy_reserved_cents"] = 0
    with pytest.raises(ValueError):
        IncomeLedger.model_validate(value)


def test_release_requires_definite_no_effect_and_never_releases_legacy_reservation() -> None:
    ledger = ledger_fixture()
    use = IncomeUse(
        fragment_id=location_id(ORIGIN, SOURCE),
        origin_transaction_id=ORIGIN,
        account_id=SOURCE,
        amount_cents=200,
    )
    reserved = reserve_income(ledger, UUID(int=10), [use], "ALLOCATE_GOAL", NOW)
    with pytest.raises(ValueError):
        release_income(reserved, UUID(int=10), NOW, confirmed_no_effect=False)
    released = release_income(reserved, UUID(int=10), NOW, confirmed_no_effect=True)
    assert released.fragments[0].reserved_cents == 100
    assert released.fragments[0].legacy_reserved_cents == 100
    assert released.fragments[0].available_cents == 700
    assert release_income(released, UUID(int=10), NOW, confirmed_no_effect=True) == released
    with pytest.raises(ValueError):
        commit_income(released, UUID(int=10), NOW)


def test_bank_snapshot_separates_application_claims_from_legacy_bank_occupancy() -> None:
    before = ledger_fixture()
    use = IncomeUse(
        fragment_id=location_id(ORIGIN, SOURCE),
        origin_transaction_id=ORIGIN,
        account_id=SOURCE,
        amount_cents=200,
    )
    reserved = reserve_income(before, UUID(int=10), [use], "ALLOCATE_GOAL", NOW)
    value = bank_location_snapshot(reserved)
    assert value["locations"][0]["available_cents"] == 500
    assert value["locations"][0]["reserved_cents"] == 300
    assert value["locations"][0]["active_reserved_cents"] == 200
    assert value["locations"][0]["legacy_reserved_cents"] == 100
    assert value["origins"][0]["amount_cents"] == 1000


def test_goal_planner_consumes_two_locations_of_one_origin_without_duplicating_income() -> None:
    from app.domain.boundary_types import CashFact
    from app.domain.goal_allocation import IncomeLot, plan_goal_allocation
    from app.tests.test_goal_allocation import GOAL, VERSION, example_d

    snapshot, policies, original_lots = example_d()
    original = original_lots[0]
    second_account = UUID(int=90)
    snapshot = snapshot.model_copy(
        update={
            "cash_accounts": [
                snapshot.cash_accounts[0].model_copy(update={"balance_cents": 860000}),
                snapshot.cash_accounts[1],
                CashFact(
                    account_id=second_account,
                    account_type="CASH",
                    balance_cents=40000,
                    observed_at=snapshot.as_of,
                ),
            ]
        }
    )
    first = IncomeLot(
        **{
            **original.model_dump(),
            "available_cents": 60000,
            "fragment_id": location_id(original.origin_transaction_id, original.account_id),
        }
    )
    second = IncomeLot(
        **{
            **original.model_dump(),
            "account_id": second_account,
            "available_cents": 40000,
            "fragment_id": location_id(original.origin_transaction_id, second_account),
        }
    )
    result = plan_goal_allocation(GOAL, VERSION, snapshot, policies, [], [], [second, first])
    assert result.suggested_cents == result.eligible_new_funds_cents == 100000
    assert {item.fragment_id for item in result.lot_allocations} == {
        first.fragment_id,
        second.fragment_id,
    }
    assert sum(item.amount_cents for item in result.lot_allocations) == 100000
    assert result == plan_goal_allocation(
        GOAL, VERSION, snapshot, policies, [], [], [first, second]
    )


@settings(max_examples=100, derandomize=True, database=None, deadline=None)
@given(moved=st.integers(min_value=1, max_value=700))
def test_transfer_then_goal_assignment_never_requalifies_or_reuses_origin(moved: int) -> None:
    ledger = ledger_fixture()
    use = IncomeUse(
        fragment_id=location_id(ORIGIN, SOURCE),
        origin_transaction_id=ORIGIN,
        account_id=SOURCE,
        amount_cents=moved,
    )
    reserved = reserve_income(
        ledger, UUID(int=10), [use], "TRANSFER_INTERNAL", NOW, destination_account_id=DESTINATION
    )
    assert (
        reserve_income(
            reserved,
            UUID(int=10),
            [use],
            "TRANSFER_INTERNAL",
            NOW,
            destination_account_id=DESTINATION,
        )
        == reserved
    )
    transferred = commit_income(reserved, UUID(int=10), NOW)
    use_at_destination = IncomeUse(
        fragment_id=location_id(ORIGIN, DESTINATION),
        origin_transaction_id=ORIGIN,
        account_id=DESTINATION,
        amount_cents=moved,
    )
    assigned = commit_income(
        reserve_income(transferred, UUID(int=11), [use_at_destination], "ALLOCATE_GOAL", NOW),
        UUID(int=11),
        NOW,
    )
    assert assigned.origins == ledger.origins
    assert sum(item.spent_cents for item in assigned.fragments) == 100
    assert sum(item.assigned_cents for item in assigned.fragments) == 100 + moved
    assert sum(item.reserved_cents for item in assigned.fragments) == 100
    assert sum(item.available_cents for item in assigned.fragments) == 700 - moved
    assert sum(item.legacy_reserved_cents for item in assigned.fragments) == 100
    with pytest.raises(ValueError):
        reserve_income(assigned, UUID(int=12), [use_at_destination], "ALLOCATE_GOAL", NOW)
    assert commit_income(assigned, UUID(int=11), NOW) == assigned
