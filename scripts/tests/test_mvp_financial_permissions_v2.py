"""Independent authority arithmetic risk fixtures: TOOL_ONLY, no real money."""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any
from uuid import UUID, uuid5

import pytest

from scripts import mvp_financial_metrics as metrics
from scripts.mvp_financial_oracles import Facts, _cash, _income
from scripts.mvp_observations import ObservationError
from scripts.mvp_trace_metrics import EFFECT_FIELDS, Missing, digest_value
from scripts.tests.test_mvp_financial_oracles import AT, CONFIRMED, OWNER, Fixture

ACTION = str(UUID(int=900_001))


def current(fixture: Fixture) -> None:
    for version in fixture.tables["policy_versions"]:
        proof = next(
            row
            for row in fixture.tables["evidence_items"]
            if row.get("content") == version["confirmation"]
        )
        proof["source_ref"] = version["id"]


def actual(fixture: Fixture, version: dict[str, Any], kind: str, **fields: Any) -> dict[str, Any]:
    current(fixture)
    effect: dict[str, Any] = dict.fromkeys(EFFECT_FIELDS)
    effect.update(
        simulation=True,
        operation_id=ACTION,
        user_id=OWNER,
        action_type=kind,
        policy_id=version["policy_id"],
        policy_version_id=version["id"],
        policy_version_ids=[version["id"]],
        cash_uses=[],
        income_uses=[],
        fee_cents=0,
        loss_cents=0,
        amount_cents=1000,
        **fields,
    )
    facts = fixture.facts()
    return {
        "before": facts,
        "after": copy.deepcopy(facts),
        "effect": effect,
        "action": {"id": ACTION, "request": {}},
        "effect_hash": digest_value(effect),
        "at": datetime.fromisoformat(AT),
    }


def goal_actual(
    *, amount: int = 1000, contributed: int = 1000, occurred: str = "2026-02-10T04:00:00+00:00"
) -> tuple[Fixture, dict[str, Any]]:
    fixture = Fixture()
    goal = fixture.goal(low=6000, contributed=contributed)
    transaction = fixture.income(50_000, occurred)
    version = next(
        row for row in fixture.tables["policy_versions"] if row["id"] == goal["policy_version_id"]
    )
    fragment = str(uuid5(UUID(transaction["id"]), "income-location:" + fixture.account))
    value = actual(
        fixture,
        version,
        "ALLOCATE_GOAL",
        goal_id=goal["id"],
        destination_account_id=fixture.account,
    )
    value["effect"].update(
        amount_cents=amount,
        cash_uses=[{"account_id": fixture.account, "amount_cents": amount}],
        income_uses=[
            {
                "origin_transaction_id": transaction["id"],
                "fragment_id": fragment,
                "account_id": fixture.account,
                "amount_cents": amount,
            }
        ],
    )
    return fixture, value


def payment_actual(
    *, automatic: bool = True, range_rule: bool = False, due: int = 20
) -> tuple[Fixture, dict[str, Any]]:
    fixture = Fixture()
    policy, version = fixture.rent(amount=20_000, day=due)
    version["configuration"]["auto_execute"] = automatic
    if range_rule:
        version["configuration"]["amount_rule"] = {
            "kind": "range",
            "min_cents": 15_000,
            "target_cents": 20_000,
            "max_cents": 25_000,
        }
    version["content_hash"] = digest_value(version["configuration"])
    version["confirmation"]["reviewed_hash"] = version["content_hash"]
    settlement = fixture.settlement(policy, 4000)
    transaction = fixture.expense(100, "2026-02-10T04:00:00+00:00", confirmed=False)
    payee = version["configuration"]["payee_id"]
    transaction["counterparty_ref"] = payee
    evidence = next(
        row for row in fixture.tables["evidence_items"] if row["id"] == transaction["evidence_id"]
    )
    evidence["content"].update(counterparty_ref=payee, user_id=OWNER)
    value = actual(
        fixture,
        version,
        "PAY_RECURRING",
        payee_id=payee,
        payee_evidence_id=evidence["id"],
        business_key="recurring:" + policy["id"] + ":2026-02",
        liability={
            "kind": "occurrence",
            "policy_id": policy["id"],
            "period": "2026-02",
            "final_total_cents": 20_000 if range_rule else None,
            "evidence_ids": [settlement["id"]],
        },
    )
    value["effect"].update(
        amount_cents=16_000, cash_uses=[{"account_id": fixture.account, "amount_cents": 16_000}]
    )
    return fixture, value


def test_goal_permission_uses_actual_month_target_and_after_consent_income() -> None:
    _, value = goal_actual()
    assert metrics._permission(value) == {
        "authorized": True,
        "automatic_permission": True,
        "exact_action_consent": False,
    }


@pytest.mark.parametrize(
    "amount,contributed,occurred",
    [
        (5001, 1000, "2026-02-10T04:00:00+00:00"),
        (1000, 6000, "2026-02-10T04:00:00+00:00"),
        (1000, 1000, "2026-01-31T04:00:00+00:00"),
        (50_001, 0, "2026-02-10T04:00:00+00:00"),
    ],
)
def test_goal_hard_limits_do_not_become_permission(
    amount: int, contributed: int, occurred: str
) -> None:
    _, value = goal_actual(amount=amount, contributed=contributed, occurred=occurred)
    assert metrics._permission(value)["authorized"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        "goal_owner",
        "destination",
        "missing_month",
        "old_version",
        "future_confirmation",
        "changed_confirmation",
        "fragment",
    ],
)
def test_goal_source_identity_and_exact_authority_are_required(mutation: str) -> None:
    fixture, value = goal_actual()
    if mutation == "goal_owner":
        value["before"]["tables"]["goals"][0]["policy_id"] = str(UUID(int=899))
    elif mutation == "destination":
        value["effect"]["destination_account_id"] = str(UUID(int=899))
    elif mutation == "missing_month":
        fixture.tables["evidence_items"] = [
            row
            for row in fixture.tables["evidence_items"]
            if row["source_type"] != "SIMULATED_GOAL_MONTH_CONTRIBUTION"
        ]
        value["before"] = fixture.facts()
    elif mutation == "old_version":
        version = copy.deepcopy(fixture.tables["policy_versions"][0])
        version["id"] = str(UUID(int=899))
        version["version_number"] = 2
        version["confirmation"]["version_id"] = version["id"]
        proof = copy.deepcopy(
            next(
                row
                for row in fixture.tables["evidence_items"]
                if row.get("source_ref") == value["effect"]["policy_version_id"]
            )
        )
        proof.update(
            id=fixture.identity(),
            source_ref=version["id"],
            content=copy.deepcopy(version["confirmation"]),
        )
        proof["content_hash"] = digest_value(proof["content"])
        fixture.tables["evidence_items"].append(proof)
        fixture.tables["policy_versions"].append(version)
        fixture.events.append(
            {
                "policy_id": version["policy_id"],
                "version_id": version["id"],
                "occurred_at": version["confirmed_at"],
                "from_status": "ACTIVE",
                "to_status": "ACTIVE",
            }
        )
        value["before"] = fixture.facts()
        assert metrics._permission(value)["authorized"] is False
        return
    elif mutation == "future_confirmation":
        version = value["before"]["tables"]["policy_versions"][0]
        version["confirmed_at"] = "2026-02-16T04:00:00+00:00"
    elif mutation == "changed_confirmation":
        value["before"]["tables"]["policy_versions"][0]["confirmation"]["accepted"] = False
    else:
        value["effect"]["income_uses"][0]["fragment_id"] = str(UUID(int=899))
    with pytest.raises((Missing, ValueError, KeyError)):
        metrics._permission(value)


def test_actual_recurring_permission_preserves_paid_so_far_and_due_window() -> None:
    _, value = payment_actual()
    assert metrics._permission(value)["automatic_permission"] is True
    debt = metrics._liability_basis(value["before"], value["effect"], value["at"])
    assert debt["total_cents"] == 20_000 and debt["paid_cents"] == 4000
    assert debt["due_date"] == "2026-02-20"


@pytest.mark.parametrize("automatic,due", [(False, 20), (True, 28), (True, 14)])
def test_recurring_auto_false_or_outside_due_window_is_not_automatic(
    automatic: bool, due: int
) -> None:
    _, value = payment_actual(automatic=automatic, due=due)
    assert metrics._permission(value)["authorized"] is False


def test_range_debt_requires_actual_reviewed_total() -> None:
    _, value = payment_actual(range_rule=True)
    assert metrics._permission(value)["authorized"] is True
    value["effect"]["liability"]["final_total_cents"] = None
    with pytest.raises(Missing):
        metrics._permission(value)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_paid",
        "period",
        "partial_amount",
        "payee",
        "income_is_payee",
        "future_payee",
        "bool_final",
    ],
)
def test_payment_never_infers_unpaid_zero_or_known_payee(mutation: str) -> None:
    fixture, value = payment_actual()
    if mutation == "missing_paid":
        fixture.tables["evidence_items"] = [
            row
            for row in fixture.tables["evidence_items"]
            if row["source_type"] != "SIMULATED_RECURRING_SETTLEMENT"
        ]
    elif mutation == "period":
        value["effect"]["liability"]["period"] = "2026-2"
    elif mutation == "partial_amount":
        value["effect"]["amount_cents"] -= 1
    elif mutation == "payee":
        value["effect"]["payee_evidence_id"] = str(UUID(int=899))
    elif mutation == "bool_final":
        value["effect"]["liability"]["final_total_cents"] = False
    else:
        transaction = fixture.tables["transactions"][0]
        proof = next(
            row
            for row in fixture.tables["evidence_items"]
            if row["id"] == transaction["evidence_id"]
        )
        if mutation == "income_is_payee":
            proof["content"]["economic_role"] = "INCOME"
        else:
            transaction["occurred_at"] = "2026-02-16T04:00:00+00:00"
            proof["content"]["occurred_at"] = transaction["occurred_at"]
    value["before"] = fixture.facts()
    with pytest.raises((Missing, ValueError, KeyError)):
        metrics._permission(value)


def reserve(fixture: Fixture, value: dict[str, Any], *, owner: str = ACTION) -> None:
    assert fixture.payload is not None
    fragment = fixture.payload["fragments"][0]
    amount = value["effect"]["amount_cents"]
    fragment["available_cents"] -= amount
    fragment["reserved_cents"] = amount
    fixture.payload["reservations"] = [
        {
            "action_id": owner,
            "operation": "ALLOCATE_GOAL",
            "destination_account_id": fixture.account,
            "uses": copy.deepcopy(value["effect"]["income_uses"]),
            "state": "RESERVED",
        }
    ]
    fixture.tables["action_plans"].append(
        {
            "id": owner,
            "user_id": OWNER,
            "request": {},
            "action_type": "ALLOCATE_GOAL",
            "status": "PLANNED",
        }
    )
    value["before"] = fixture.facts()
    value["before"]["protocol"] = "mvp-financial-facts-v2"


def test_v2_app_claim_does_not_create_a_bank_reserved_leg() -> None:
    fixture, value = goal_actual(amount=5000)
    reserve(fixture, value)
    facts = Facts(value["before"])
    _, heads, _ = _cash(facts, value["at"])
    assert (
        _income(facts, heads, value["at"], {"start": datetime.fromisoformat(CONFIRMED)}) == 45_000
    )
    assert metrics._permission(value)["authorized"] is True
    # The old V1 physical bucket interpretation is retained and fails here.
    value["before"]["protocol"] = "mvp-financial-facts-v1"
    with pytest.raises(ValueError, match="virtual ownership ledger"):
        _income(
            Facts(value["before"]), heads, value["at"], {"start": datetime.fromisoformat(CONFIRMED)}
        )


def test_own_reserved_income_must_match_exact_uses() -> None:
    fixture, value = goal_actual(amount=5000)
    reserve(fixture, value)
    value["effect"]["income_uses"][0]["amount_cents"] += 1
    with pytest.raises(ObservationError, match="original income reservation"):
        metrics._permission(value)
