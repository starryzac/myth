"""TOOL_ONLY full purchase originals; independent cents/legs/projection risks."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid5

import pytest

from scripts import mvp_financial_metrics as metrics
from scripts.mvp_financial_oracles import Facts, _cash, _income
from scripts.mvp_observations import ObservationError
from scripts.mvp_trace_metrics import EFFECT_FIELDS, digest_value
from scripts.tests.test_mvp_financial_oracles import AT, OWNER, Fixture
from scripts.tests.test_mvp_financial_permissions_v2 import ACTION, current


class Purchase:
    """Separate before/after bound synthetic originals, no DB/service calls."""

    def __init__(self) -> None:
        self.fixture = Fixture()
        policy, product = self.fixture.asset()
        current(self.fixture)
        product["version_number"] = 1
        version = self.fixture.tables["policy_versions"][0]
        income = self.fixture.income()
        assert self.fixture.payload is not None
        fragment = self.fixture.payload["fragments"][0]
        position_id, asset_account = str(UUID(int=900_002)), str(UUID(int=900_003))
        self.fixture.tables["accounts"].append(
            {
                "id": asset_account,
                "user_id": OWNER,
                "account_type": "ASSET",
                "currency": "CNY",
                "balance_cents": 0,
            }
        )
        self.fixture.posting("POSITION:" + position_id, 0, position_id=position_id)
        for row in self.fixture.tables["simulated_bank_postings"]:
            row.update(created_at=AT, leg_ref=None)
        self.effect: dict[str, Any] = dict.fromkeys(EFFECT_FIELDS)
        self.effect.update(
            simulation=True,
            operation_id=ACTION,
            user_id=OWNER,
            action_type="PURCHASE_ASSET",
            amount_cents=1000,
            business_key="tool-purchase",
            cash_uses=[{"account_id": self.fixture.account, "amount_cents": 1000}],
            income_uses=[
                {
                    "account_id": self.fixture.account,
                    "fragment_id": fragment["fragment_id"],
                    "origin_transaction_id": income["id"],
                    "amount_cents": 1000,
                }
            ],
            policy_id=policy["id"],
            policy_version_id=version["id"],
            policy_version_ids=[version["id"]],
            product_id=product["id"],
            product_version_number=1,
            terms_digest=digest_value(product["maturity_rule"]),
            position_id=position_id,
            position_account_id=asset_account,
            return_account_id=self.fixture.account,
            valid_from=AT,
            expires_at=(datetime.fromisoformat(AT) + timedelta(days=1)).isoformat(),
            fee_cents=0,
            loss_cents=0,
            settlement_delay_days=0,
        )
        command = {"effect": self.effect, "effect_hash": digest_value(self.effect)}
        request = {"execution": command}
        self.action = self.fixture.row(
            "action_plans",
            action_type="PURCHASE_ASSET",
            amount_cents=1000,
            request=request,
            request_hash=digest_value(request),
            idempotency_key="tool-only-purchase",
            status="PLANNED",
        )
        self.action["id"] = ACTION
        self.before = self.fixture.facts()
        self.before["protocol"] = "mvp-financial-facts-v2"
        self.bank = self.fixture.row(
            "bank_operations",
            action_plan_id=ACTION,
            operation_type="PURCHASE_ASSET",
            business_key=self.effect["business_key"],
            amount_cents=1000,
            request=copy.deepcopy(command),
            request_hash=digest_value(command),
            idempotency_key=self.action["idempotency_key"],
            requested_at=AT,
            available_at=AT,
            settled_at=AT,
            status="SETTLED",
        )
        self.bank["id"] = ACTION
        legs = [
            self.post("CASH:" + self.fixture.account, -1000, "cash:" + self.fixture.account),
            self.post("POSITION:" + position_id, 1000, "position"),
            self.post(
                "LOT_AVAILABLE:" + fragment["fragment_id"],
                -1000,
                "income_out:" + fragment["fragment_id"],
            ),
            self.post(
                "LOT_SPENT:" + fragment["fragment_id"], 1000, "income_in:" + fragment["fragment_id"]
            ),
        ]
        self.legs = legs
        self.fixture.tables["accounts"][0]["balance_cents"] -= 1000
        self.fixture.tables["asset_positions"].append(
            {
                "id": position_id,
                "user_id": OWNER,
                "account_id": asset_account,
                "product_id": product["id"],
                "goal_id": None,
                "policy_version_id": version["id"],
                "principal_cents": 1000,
                "purchased_at": AT,
                "available_at": None,
                "status": "HELD",
            }
        )
        self.transaction = self.fixture.row(
            "transactions",
            account_id=self.fixture.account,
            direction="DEBIT",
            amount_cents=1000,
            balance_after_cents=99_000,
            occurred_at=AT,
            observed_at=AT,
            counterparty_ref="position:" + position_id,
        )
        proof = self.fixture.evidence(
            "SIMULATED_BANK_TRANSACTION",
            {
                "simulation": True,
                "user_id": OWNER,
                "transaction_id": self.transaction["id"],
                "account_id": self.fixture.account,
                "direction": "DEBIT",
                "amount_cents": 1000,
                "balance_after_cents": 99_000,
                "occurred_at": AT,
                "economic_role": "ASSET_PURCHASE",
                "counterparty_ref": "position:" + position_id,
                "bank_operation_id": ACTION,
                "bank_posting_id": legs[0]["id"],
            },
        )
        self.transaction["evidence_id"] = proof["id"]
        fragment.update(available_cents=49_000, spent_cents=1000)
        self.action["status"] = "SUCCEEDED"
        self.receipt = self.fixture.row(
            "action_receipts",
            action_plan_id=ACTION,
            status="SUCCEEDED",
            executed_cents=1000,
            fee_cents=0,
            loss_cents=0,
            occurred_at=AT,
            response={"bank_operation_id": ACTION, "posting_ids": [leg["id"] for leg in legs]},
        )
        self.record = {
            "before_facts_ref": "before",
            "after_facts_ref": "after",
            "action_id": ACTION,
        }

    def post(self, key: str, delta: int, leg: str) -> dict[str, Any]:
        previous = next(
            row
            for row in self.fixture.tables["simulated_bank_postings"]
            if row["ledger_key"] == key
        )
        row = copy.deepcopy(previous)
        row.update(
            id=str(uuid5(UUID(ACTION), "posting:" + leg)),
            operation_id=ACTION,
            entry_kind="DEBIT" if delta < 0 else "CREDIT",
            leg_ref=leg,
            occurred_at=AT,
            previous_posting_id=previous["id"],
            sequence_number=2,
            balance_before_cents=previous["balance_after_cents"],
            delta_cents=delta,
            balance_after_cents=previous["balance_after_cents"] + delta,
        )
        self.fixture.tables["simulated_bank_postings"].append(row)
        return row

    def facts(self, ref: Any) -> dict[str, Any]:
        if ref == "before":
            return copy.deepcopy(self.before)
        value = self.fixture.facts()
        value["protocol"] = "mvp-financial-facts-v2"
        return value

    def settled(self) -> dict[str, Any]:
        return metrics._settled(cast(metrics.FinanceBundle, self), self.record)


def test_full_purchase_settlement_and_projection_are_independent() -> None:
    fixture = Purchase()
    actual = fixture.settled()
    assert metrics._permission(actual)["authorized"] is True
    metrics._application_projection(actual)
    facts = Facts(actual["after"])
    _, heads, _ = _cash(facts, facts.as_of)
    assert _income(facts, heads, facts.as_of, {"start": datetime.fromisoformat(AT)}) == 0
    assert len(actual["legs"]) == 4 and actual["receipt"]["executed_cents"] == 1000


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_income_leg",
        "wrong_income_delta",
        "virtual_cash",
        "double_receipt",
        "missing_transaction",
        "wrong_economic_role",
        "wrong_transaction_amount",
        "wrong_cash_projection",
        "wrong_position_product",
        "wrong_position_owner_scope",
        "wrong_position_time",
        "retained_reservation",
        "wrong_income_projection",
    ],
)
def test_full_purchase_evidence_cannot_be_shortened_or_rebound(mutation: str) -> None:
    fixture = Purchase()
    if mutation == "missing_income_leg":
        fixture.fixture.tables["simulated_bank_postings"].remove(fixture.legs[-1])
    elif mutation == "wrong_income_delta":
        fixture.legs[-1]["delta_cents"] -= 1
    elif mutation == "virtual_cash":
        fixture.legs[0]["ledger_dimension"] = "INCOME_LOCATION"
    elif mutation == "double_receipt":
        receipt = copy.deepcopy(fixture.receipt)
        receipt["id"] = str(UUID(int=987_000))
        fixture.fixture.tables["action_receipts"].append(receipt)
    elif mutation == "missing_transaction":
        fixture.fixture.tables["transactions"].remove(fixture.transaction)
    elif mutation in {"wrong_economic_role", "wrong_transaction_amount"}:
        proof = next(
            row
            for row in fixture.fixture.tables["evidence_items"]
            if row["id"] == fixture.transaction["evidence_id"]
        )
        if mutation == "wrong_economic_role":
            proof["content"]["economic_role"] = "INCOME"
        else:
            fixture.transaction["amount_cents"] += 1
    elif mutation == "wrong_cash_projection":
        fixture.fixture.tables["accounts"][0]["balance_cents"] += 1
    elif mutation == "wrong_income_projection":
        assert fixture.fixture.payload is not None
        fixture.fixture.payload["fragments"][0]["spent_cents"] += 1
    elif mutation == "retained_reservation":
        fixture.fixture.row(
            "action_resource_reservations",
            action_plan_id=ACTION,
            status="RESERVED",
            amount_cents=1000,
            resource_kind="CASH",
        )
    else:
        position = fixture.fixture.tables["asset_positions"][0]
        if mutation == "wrong_position_product":
            position["product_id"] = str(UUID(int=987_000))
        elif mutation == "wrong_position_owner_scope":
            position["goal_id"] = str(UUID(int=987_000))
        else:
            position["purchased_at"] = "2026-02-15T04:00:01+00:00"
    with pytest.raises((ObservationError, ValueError, KeyError)):
        actual = fixture.settled()
        metrics._application_projection(actual)
