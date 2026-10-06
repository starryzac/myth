"""Source inventory contract tests with synthetic originals; no DB or economic proof."""

from typing import Any
from uuid import UUID

import pytest
from app.domain.goal_release_provenance import (
    GoalCashSourceProof,
    OriginalRowReference,
    prove_goal_cash_sources,
)
from app.domain.income_ledger import IncomeLedger
from app.domain.policy_configuration import configuration_hash
from app.services.full_goal_release_inventory import validate_retained_goal_cash_basis
from app.tests.test_goal_release_provenance import fixture


def originals() -> tuple[
    GoalCashSourceProof, dict[tuple[str, UUID], dict[str, Any]], IncomeLedger, set[UUID]
]:
    data = fixture()
    assert data.income is not None
    proof = prove_goal_cash_sources(data)
    rows: dict[tuple[str, UUID], dict[str, Any]] = {}

    def ref(original: OriginalRowReference) -> OriginalRowReference:
        key = (original.table, original.row_id)
        row = {
            "table": original.table,
            "id": str(original.row_id),
            "original": "synthetic-original",
        }
        rows[key] = row
        return original.model_copy(update={"row_hash": configuration_hash(row)})

    proof = proof.model_copy(
        update={
            "original_operation_refs": [ref(row) for row in proof.original_operation_refs],
            "original_goal_cash_posting_refs": [
                ref(row) for row in proof.original_goal_cash_posting_refs
            ],
            "sources": [
                row.model_copy(
                    update={
                        "source_original_refs": [ref(item) for item in row.source_original_refs]
                    }
                )
                for row in proof.sources
            ],
            "original_allocation_slices": [
                row.model_copy(update={"original_refs": [ref(item) for item in row.original_refs]})
                for row in proof.original_allocation_slices
            ],
        }
    )
    banks = {row.row_id for row in proof.original_operation_refs if row.table == "bank_operations"}
    return proof, rows, data.income, banks


def test_retained_basis_rechecks_all_original_rows_and_all_assigned_denominators() -> None:
    proof, rows, income, banks = originals()
    before = (proof.model_dump_json(), income.model_dump_json())
    validate_retained_goal_cash_basis(proof, rows, income, banks)
    assert (proof.model_dump_json(), income.model_dump_json()) == before
    assert proof.available_income_increase_cents == proof.assigned_income_decrease_cents == 0


@pytest.mark.parametrize(
    "table", ["action_plans", "bank_operations", "action_receipts", "simulated_bank_postings"]
)
def test_any_retained_original_missing_or_changed_is_rejected(table: str) -> None:
    proof, rows, income, banks = originals()
    key = next(key for key in rows if key[0] == table)
    rows[key] = {**rows[key], "original": "different"}
    with pytest.raises(ValueError, match="bytes differ"):
        validate_retained_goal_cash_basis(proof, rows, income, banks)
    rows.pop(key)
    with pytest.raises(ValueError, match="bytes differ"):
        validate_retained_goal_cash_basis(proof, rows, income, banks)


@pytest.mark.parametrize(
    "change",
    [
        "unknown",
        "reason",
        "fragment_missing",
        "assigned",
        "origin",
        "location",
        "extra_bank",
        "missing_bank",
    ],
)
def test_retained_state_string_never_replaces_actual_current_originals(change: str) -> None:
    proof, rows, income, banks = originals()
    if change == "unknown":
        proof = proof.model_copy(update={"state": "UNKNOWN"})
    elif change == "reason":
        proof = proof.model_copy(update={"reasons": ["ACTUAL_SOURCE_UNKNOWN"]})
    elif change == "fragment_missing":
        income = income.model_copy(update={"fragments": ()})
    elif change in {"assigned", "location"}:
        fragment = income.fragments[0]
        update = (
            {"assigned_cents": fragment.assigned_cents - 1}
            if change == "assigned"
            else {"account_id": UUID(int=1)}
        )
        income = income.model_copy(update={"fragments": (fragment.model_copy(update=update),)})
    elif change == "origin":
        income = income.model_copy(
            update={
                "origins": (income.origins[0].model_copy(update={"bank_evidence_hash": "0" * 64}),)
            }
        )
    elif change == "extra_bank":
        banks.add(UUID(int=1))
    else:
        banks.pop()
    with pytest.raises(ValueError):
        validate_retained_goal_cash_basis(proof, rows, income, banks)
