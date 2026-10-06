"""Synthetic integer source risks only; no bank/receipt/runtime success evidence."""

import copy
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from app.db.models import ActionPlan, EvidenceItem
from app.domain.execution import execution_effect_hash
from app.domain.execution_types import BankCommand, CashUse, ExecutionEffect
from app.domain.full_dynamic_goal_execution import FullDynamicGoalProof
from app.domain.full_joint_goal_execution import FullJointGoalChild
from app.domain.income_ledger import (
    LEDGER_SOURCE,
    IncomeFragment,
    IncomeLedger,
    IncomeOrigin,
    IncomeUse,
    commit_income,
    location_id,
    reserve_income,
)
from app.domain.policy_configuration import configuration_hash
from app.services import full_joint_goal_income_reservation as service
from app.services.policy_lifecycle import PolicyLifecycleError
from sqlalchemy.orm import Session

NOW = datetime(2026, 10, 6, 5, tzinfo=UTC)
USER, CASH, ORIGIN, BANK = (UUID(int=value) for value in (1, 2, 3, 4))
FRAGMENT = location_id(ORIGIN, CASH)


@contextmanager
def refused(code: str) -> Iterator[None]:
    with pytest.raises(PolicyLifecycleError) as captured:
        yield
    assert captured.value.code == code


def ledger() -> IncomeLedger:
    return IncomeLedger(
        user_id=USER,
        as_of=NOW,
        scope_account_ids=(CASH,),
        origins=(
            IncomeOrigin(
                origin_transaction_id=ORIGIN,
                origin_account_id=CASH,
                amount_cents=500000,
                occurred_at=NOW - timedelta(days=1),
                observed_at=NOW,
                bank_evidence_id=BANK,
                bank_evidence_hash="a" * 64,
            ),
        ),
        fragments=(
            IncomeFragment(
                fragment_id=FRAGMENT,
                origin_transaction_id=ORIGIN,
                account_id=CASH,
                available_cents=500000,
            ),
        ),
    )


def child(number: int, amount: int) -> FullJointGoalChild:
    action_id = UUID(int=100 + number)
    version_id = UUID(int=200 + number)
    effect = ExecutionEffect(
        operation_id=action_id,
        user_id=USER,
        business_key=f"SYNTHETIC_ONLY:{number}",
        action_type="ALLOCATE_GOAL",
        amount_cents=amount,
        cash_uses=[CashUse(account_id=CASH, amount_cents=amount)],
        income_uses=[
            IncomeUse(
                fragment_id=FRAGMENT,
                origin_transaction_id=ORIGIN,
                account_id=CASH,
                amount_cents=amount,
            )
        ],
        destination_account_id=UUID(int=300 + number),
        goal_id=UUID(int=400 + number),
        policy_id=UUID(int=500 + number),
        policy_version_id=version_id,
        policy_version_ids=[version_id],
        valid_from=NOW,
        expires_at=NOW + timedelta(hours=1),
    )
    return FullJointGoalChild(
        child_number=number,
        goal_id=UUID(int=400 + number),
        action_id=action_id,
        bank_idempotency_key=f"joint-goal:synthetic:{number}",
        command=BankCommand(effect=effect, effect_hash=execution_effect_hash(effect)),
        original_model_evidence_id=UUID(int=600 + number),
        original_model_evidence_hash="b" * 64,
        range_proof_hash="c" * 64,
    )


def settled(data: IncomeLedger, item: FullJointGoalChild) -> IncomeLedger:
    return commit_income(
        reserve_income(data, item.action_id, item.command.effect.income_uses, "ALLOCATE_GOAL", NOW),
        item.action_id,
        NOW,
    )


def evidence(
    identity: int,
    data: IncomeLedger,
    *,
    previous: EvidenceItem | None = None,
    status: str = "VALID",
) -> EvidenceItem:
    content = data.model_dump(mode="json")
    return EvidenceItem(
        id=UUID(int=identity),
        user_id=USER,
        created_at=NOW,
        evidence_level="BANK_CONFIRMED",
        source_type=LEDGER_SOURCE,
        source_ref="SYNTHETIC_ONLY",
        content=content,
        content_hash=configuration_hash(content),
        observed_at=NOW,
        valid_from=NOW,
        valid_to=None,
        status=status,
        supersedes_id=previous.id if previous else None,
    )


def action(original: EvidenceItem) -> ActionPlan:
    request = {"income_evidence": {"id": str(original.id), "hash": original.content_hash}}
    return ActionPlan(
        id=UUID(int=102), user_id=USER, request=request, request_hash=configuration_hash(request)
    )


def test_second_fixed_uses_keep_first_assigned_and_original_request() -> None:
    original = ledger()
    children = [child(1, 50000), child(2, 100000)]
    current = settled(original, children[0])
    saved = original.model_dump_json()
    service._validate_ledger_progress(original, current, children, 2)
    reserved = reserve_income(
        current, children[1].action_id, children[1].command.effect.income_uses, "ALLOCATE_GOAL", NOW
    )
    assert (
        reserved.fragments[0].assigned_cents,
        reserved.fragments[0].reserved_cents,
        reserved.fragments[0].available_cents,
    ) == (50000, 100000, 350000)
    assert original.model_dump_json() == saved
    assert children[1].command.effect.amount_cents == 100000


def test_third_fixed_uses_count_every_verified_prefix_commit() -> None:
    original = ledger()
    children = [child(1, 50000), child(2, 100000), child(3, 70000)]
    current = settled(settled(original, children[0]), children[1])
    service._validate_ledger_progress(original, current, children, 3)
    assert (current.fragments[0].assigned_cents, current.fragments[0].available_cents) == (
        150000,
        350000,
    )


def test_already_committed_prepared_prefix_is_not_counted_twice() -> None:
    children = [child(1, 50000), child(2, 100000)]
    prepared = settled(ledger(), children[0])
    service._validate_ledger_progress(prepared, prepared, children, 2)
    assert prepared.fragments[0].assigned_cents == 50000


@pytest.mark.parametrize("state", ["RESERVED", "RELEASED"])
def test_unsettled_prefix_cannot_be_treated_as_bank_receipt(state: str) -> None:
    children = [child(1, 50000), child(2, 100000)]
    current = settled(ledger(), children[0])
    command = current.reservations[0].model_copy(update={"state": state})
    if state == "RESERVED":
        fragment = current.fragments[0].model_copy(
            update={"assigned_cents": 0, "reserved_cents": 50000}
        )
    else:
        fragment = current.fragments[0]
    current = current.model_copy(update={"reservations": (command,), "fragments": (fragment,)})
    with refused("JOINT_PRIOR_VERIFIED_RECEIPT_INCOME_COMMIT_DIFFERS"):
        service._validate_ledger_progress(ledger(), current, children, 2)


def test_missing_prefix_commit_is_not_zero_consumption() -> None:
    with refused("JOINT_PRIOR_VERIFIED_RECEIPT_INCOME_COMMIT_DIFFERS"):
        service._validate_ledger_progress(
            ledger(), ledger(), [child(1, 50000), child(2, 100000)], 2
        )


def test_prior_commit_exact_original_amount_required() -> None:
    current = settled(ledger(), child(1, 40000))
    with refused("JOINT_PRIOR_VERIFIED_RECEIPT_INCOME_COMMIT_DIFFERS"):
        service._validate_ledger_progress(ledger(), current, [child(1, 50000), child(2, 100000)], 2)


def test_committed_label_cannot_erase_assigned_denominator() -> None:
    current = settled(ledger(), child(1, 50000))
    current = current.model_copy(update={"fragments": ledger().fragments})
    with refused("JOINT_PRIOR_BANK_RECEIPT_NOT_RETAINED_IN_ASSIGNED_DENOMINATOR"):
        service._validate_ledger_progress(ledger(), current, [child(1, 50000), child(2, 100000)], 2)


@pytest.mark.parametrize("number", [2, 3])
def test_current_or_later_claim_cannot_be_borrowed(number: int) -> None:
    children = [child(1, 50000), child(2, 100000), child(3, 70000)]
    current = reserve_income(
        settled(ledger(), children[0]),
        children[number - 1].action_id,
        children[number - 1].command.effect.income_uses,
        "ALLOCATE_GOAL",
        NOW,
    )
    with refused("JOINT_CURRENT_INCOME_SCOPE_OR_SIBLING_CLAIM_DIFFERS"):
        service._validate_ledger_progress(ledger(), current, children, 2)


def test_other_actual_claim_is_retained_and_cannot_fund_fixed_child() -> None:
    children = [child(1, 50000), child(2, 100000)]
    current = reserve_income(
        settled(ledger(), children[0]),
        UUID(int=999),
        child(4, 400000).command.effect.income_uses,
        "ALLOCATE_GOAL",
        NOW,
    )
    with refused("JOINT_CURRENT_FIXED_FRAGMENT_NOT_ORIGINALLY_AND_CURRENTLY_AVAILABLE"):
        service._validate_ledger_progress(ledger(), current, children, 2)
    assert current.fragments[0].reserved_cents == 400000


def test_current_original_origin_bank_identity_must_not_change() -> None:
    current = settled(ledger(), child(1, 50000))
    current = current.model_copy(
        update={
            "origins": (current.origins[0].model_copy(update={"bank_evidence_hash": "d" * 64}),)
        }
    )
    with refused("JOINT_CURRENT_INCOME_SCOPE_OR_SIBLING_CLAIM_DIFFERS"):
        service._validate_ledger_progress(ledger(), current, [child(1, 50000), child(2, 100000)], 2)


def test_additional_account_does_not_relabel_original_scope() -> None:
    current = settled(ledger(), child(1, 50000))
    current = current.model_copy(update={"scope_account_ids": (CASH, UUID(int=999))})
    with refused("JOINT_CURRENT_INCOME_SCOPE_OR_SIBLING_CLAIM_DIFFERS"):
        service._validate_ledger_progress(ledger(), current, [child(1, 50000), child(2, 100000)], 2)


def test_self_consistent_assigned_to_available_is_not_a_joint_successor() -> None:
    old = ledger().model_copy(
        update={
            "fragments": (
                ledger()
                .fragments[0]
                .model_copy(update={"assigned_cents": 100000, "available_cents": 400000}),
            )
        }
    )
    current = ledger().model_copy(
        update={
            "fragments": (
                ledger()
                .fragments[0]
                .model_copy(update={"assigned_cents": 50000, "available_cents": 450000}),
            )
        }
    )
    with refused("JOINT_CURRENT_FIXED_FRAGMENT_NOT_ORIGINALLY_AND_CURRENTLY_AVAILABLE"):
        service._validate_ledger_progress(old, current, [child(1, 100000)], 1)


@pytest.mark.parametrize("number", [0, 3])
def test_fixed_child_denominator_cannot_be_skipped(number: int) -> None:
    with refused("JOINT_COMPLETE_FIXED_CHILD_OR_LEDGER_IDENTITY_DIFFERS"):
        service._validate_ledger_progress(ledger(), ledger(), [child(1, 100000)], number)


def test_original_native_ledger_lineage_retains_hash_and_known_clocks() -> None:
    old = evidence(700, ledger(), status="SUPERSEDED")
    current = evidence(701, settled(ledger(), child(1, 50000)), previous=old)
    item = action(old)
    saved = copy.deepcopy(item.request)
    session = MagicMock(spec=Session)
    session.get.return_value = old
    assert service._prepared_ledger(session, item, current, NOW) == ledger()
    session.get.assert_called_once_with(EvidenceItem, old.id)
    assert item.request == saved and item.request_hash == configuration_hash(saved)


@pytest.mark.parametrize(
    "field,value",
    [
        ("user_id", UUID(int=999)),
        ("evidence_level", "USER_DECLARED"),
        ("source_type", "UNREGISTERED"),
        ("status", "UNKNOWN"),
        ("content_hash", "f" * 64),
        ("observed_at", NOW + timedelta(seconds=1)),
        ("valid_from", NOW + timedelta(seconds=1)),
        ("valid_to", NOW),
    ],
)
def test_original_source_metadata_refuses_dirty_or_future_facts(field: str, value: Any) -> None:
    row = evidence(700, ledger())
    setattr(row, field, value)
    with refused("JOINT_INCOME_ORIGINAL_LINEAGE_NOT_VERIFIED"):
        service._original_ledger(row, USER, NOW)


@pytest.mark.parametrize("field", ["protocol", "simulation", "complete"])
def test_missing_native_protocol_fields_cannot_use_pydantic_defaults(field: str) -> None:
    row = evidence(700, ledger())
    del row.content[field]
    row.content_hash = configuration_hash(row.content)
    with refused("JOINT_INCOME_ORIGINAL_OWNER_OR_CLOCK_DIFFERS"):
        service._original_ledger(row, USER, NOW)


def test_v1_cannot_be_reconstructed_into_native_v2_proof() -> None:
    row = evidence(700, ledger())
    row.content["protocol"] = "new-funds-ledger-v1"
    row.content_hash = configuration_hash(row.content)
    with refused("JOINT_NATIVE_V2_INCOME_ORIGINAL_REQUIRED"):
        service._original_ledger(row, USER, NOW)


@pytest.mark.parametrize(
    "kind", ["missing", "cycle", "nonmonotonic", "capacity", "wrongstatus", "hash"]
)
def test_lineage_missing_tampered_or_ambiguous_is_not_success(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = evidence(700, ledger(), status="SUPERSEDED")
    current = evidence(701, settled(ledger(), child(1, 50000)), previous=old)
    item = action(old)
    session = MagicMock(spec=Session)
    session.get.return_value = old
    if kind == "missing":
        session.get.return_value = None
    elif kind == "cycle":
        session.get.return_value = current
    elif kind == "nonmonotonic":
        current.observed_at = NOW - timedelta(seconds=1)
        current.content["as_of"] = current.observed_at.isoformat()
        current.content["origins"][0]["observed_at"] = current.observed_at.isoformat()
        current.content_hash = configuration_hash(current.content)
    elif kind == "capacity":
        monkeypatch.setattr(service, "MAX_LEDGER_LINEAGE", 1)
    elif kind == "wrongstatus":
        old.status = "VALID"
    else:
        item.request["income_evidence"]["hash"] = "e" * 64
    with pytest.raises(PolicyLifecycleError):
        service._prepared_ledger(session, item, current, NOW)


def test_superseded_current_target_cannot_short_circuit_current_valid_gate() -> None:
    row = evidence(700, ledger(), status="SUPERSEDED")
    session = MagicMock(spec=Session)
    with refused("JOINT_PREPARED_INCOME_LINEAGE_MISSING_OR_OVER_CAPACITY"):
        service._prepared_ledger(session, action(row), row, NOW)
    session.get.assert_not_called()


@pytest.mark.parametrize(
    "original_request",
    [
        {},
        {"income_evidence": {"id": "bad", "hash": "a" * 64}},
        {"income_evidence": {"id": str(UUID(int=700)), "hash": "a" * 64, "success": True}},
    ],
)
def test_original_income_reference_has_no_extra_success_fields(
    original_request: dict[str, Any],
) -> None:
    row = evidence(700, ledger())
    item = action(row)
    item.request = original_request
    with pytest.raises(PolicyLifecycleError):
        service._prepared_ledger(MagicMock(spec=Session), item, row, NOW)


def test_missing_joint_binding_rejects_before_any_database_read_or_grant() -> None:
    item = action(evidence(700, ledger()))
    session = MagicMock(spec=Session)
    with refused("JOINT_PERSISTED_CHILD_MARKER_REQUIRED"):
        service.validate_current_joint_income_reservation(
            session, item, cast(FullDynamicGoalProof, None), NOW
        )
    assert session.mock_calls == []


def test_original_request_hash_tamper_is_not_a_new_successor() -> None:
    item = action(evidence(700, ledger()))
    item.request["full_joint_goal_execution"] = {"success": True}
    session = MagicMock(spec=Session)
    with refused("JOINT_PERSISTED_CHILD_MARKER_REQUIRED"):
        service.validate_current_joint_income_reservation(
            session, item, cast(FullDynamicGoalProof, None), NOW
        )
    assert session.mock_calls == []
