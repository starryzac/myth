"""Pure independent-oracle tests; in-memory synthetic originals are TOOL_ONLY."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

import pytest

from scripts.mvp_financial_oracles import compute_timeline, validate_facts

TABLES = (
    "accounts",
    "evidence_items",
    "policies",
    "policy_versions",
    "goals",
    "transactions",
    "credit_card_bills",
    "asset_positions",
    "asset_products",
    "bank_operations",
    "action_plans",
    "action_receipts",
    "simulated_bank_postings",
    "action_resource_reservations",
)
AT = "2026-02-15T04:00:00+00:00"
CONFIRMED = "2026-02-01T04:00:00+00:00"
END = "2026-03-02T04:00:00+00:00"
OWNER = str(UUID(int=100_000))


def encoded(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf8")


def hashed(value: Any) -> str:
    return hashlib.sha256(encoded(value)).hexdigest()


class Fixture:
    """Builder exposes original rows, not precomputed financial oracle results."""

    def __init__(self, cash: int = 100_000):
        self.tables: dict[str, list[dict[str, Any]]] = {table: [] for table in TABLES}
        self.events: list[dict[str, Any]] = []
        self.coverage: dict[str, Any] | None = None
        self.payload: dict[str, Any] | None = None
        self.counter = 0
        self.account = self.row(
            "accounts", account_type="CASH", currency="CNY", balance_cents=cash
        )["id"]
        self.posting("CASH:" + self.account, cash, account_id=self.account)

    def identity(self) -> str:
        self.counter += 1
        return str(UUID(int=self.counter))

    def row(self, table: str, **fields: Any) -> dict[str, Any]:
        row = {"id": self.identity(), **fields}
        if table != "asset_products":
            row["user_id"] = OWNER
        self.tables[table].append(row)
        return row

    def evidence(
        self,
        source: str,
        content: dict[str, Any],
        level: str = "BANK_CONFIRMED",
        observed: str = AT,
    ) -> dict[str, Any]:
        return self.row(
            "evidence_items",
            source_type=source,
            evidence_level=level,
            content=content,
            content_hash=hashed(content),
            observed_at=observed,
            valid_from=observed,
            valid_to=None,
            status="VALID",
        )

    def posting(
        self,
        key: str,
        balance: int,
        *,
        dimension: str = "ECONOMIC",
        account_id: str | None = None,
        position_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.row(
            "simulated_bank_postings",
            ledger_key=key,
            ledger_dimension=dimension,
            ledger_metadata=metadata or {},
            account_id=account_id,
            position_id=position_id,
            operation_id=None,
            external_fact_id=None,
            redemption_id=None,
            previous_posting_id=None,
            entry_kind="OPENING",
            sequence_number=1,
            balance_before_cents=0,
            delta_cents=balance,
            balance_after_cents=balance,
            occurred_at=CONFIRMED,
        )

    def policy(
        self, config: dict[str, Any], confirmed: str = CONFIRMED, until: str | None = None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        policy = self.row("policies", status="ACTIVE")
        version_id = self.identity()
        confirmation = {
            "accepted": True,
            "reviewed_hash": hashed(config),
            "user_id": OWNER,
            "policy_id": policy["id"],
            "version_id": version_id,
            "confirmed_at": confirmed,
            "effective_from": confirmed,
            "effective_until": until,
            "request_key": "pure-fixture",
            "request_hash": "a" * 64,
        }
        version = self.row(
            "policy_versions",
            policy_id=policy["id"],
            configuration=config,
            content_hash=hashed(config),
            version_number=1,
            confirmed_at=confirmed,
            confirmation=confirmation,
            valid_from=confirmed,
            valid_until=until,
        )
        version["id"] = version_id
        self.evidence("POLICY_CONFIRMATION", confirmation, "USER_CONFIRMED_POLICY", confirmed)
        self.events.append(
            {
                "policy_id": policy["id"],
                "version_id": version_id,
                "occurred_at": confirmed,
                "from_status": "PROPOSED",
                "to_status": "ACTIVE",
            }
        )
        return policy, version

    def emergency(self, amount: int = 5000) -> tuple[dict[str, Any], dict[str, Any]]:
        return self.policy(
            {"type": "emergency_buffer", "name": "Pure emergency", "amount_cents": amount}
        )

    def rent(self, amount: int = 20_000, day: int = 28) -> tuple[dict[str, Any], dict[str, Any]]:
        return self.policy(
            {
                "type": "recurring_obligation",
                "name": "Pure recurring",
                "payee_id": str(UUID(int=200_000)),
                "amount_rule": {"kind": "exact", "amount_cents": amount},
                "due_day": day,
                "prepare_days_before": 7,
                "auto_execute": False,
                "priority": {"importance": 90},
            }
        )

    def settlement(
        self, policy: dict[str, Any], paid: int, period: str = "2026-02"
    ) -> dict[str, Any]:
        return self.evidence(
            "SIMULATED_RECURRING_SETTLEMENT",
            {
                "protocol": "recurring-settlement-v1",
                "policy_id": policy["id"],
                "period": period,
                "payee_id": str(UUID(int=200_000)),
                "paid_cents": paid,
                "complete": True,
                "simulation": True,
                "user_id": OWNER,
                "as_of": AT,
            },
        )

    def expense(
        self,
        amount: int,
        occurred: str,
        *,
        category: str = "food",
        confirmed: bool = True,
        one_off: bool = False,
        role: str = "CONSUMPTION",
        direction: str = "DEBIT",
    ) -> dict[str, Any]:
        row = self.row(
            "transactions",
            account_id=self.account,
            amount_cents=amount,
            balance_after_cents=100_000,
            counterparty_ref="pure-market",
            direction=direction,
            occurred_at=occurred,
            observed_at=AT,
            category=category,
            category_confirmed=confirmed,
            is_one_off=one_off,
        )
        content = {
            key: row[key]
            for key in (
                "account_id",
                "amount_cents",
                "balance_after_cents",
                "counterparty_ref",
                "direction",
                "occurred_at",
            )
        }
        content.update(transaction_id=row["id"], simulation=True, economic_role=role)
        row["evidence_id"] = self.evidence("SIMULATED_BANK_TRANSACTION", content)["id"]
        if confirmed:
            self.evidence(
                "SIMULATED_USER_CATEGORY_CONFIRMATION",
                {
                    "transaction_id": row["id"],
                    "category": category,
                    "confirmed": True,
                    "simulation": True,
                    "actor": "pure_fixture",
                },
                "USER_DECLARED",
            )
        return row

    def living(
        self, *, lookback: int = 3, horizon: int = 2, q: float = 0.5, extra: int = 500
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        self.coverage = {
            "protocol": "mvp-expense-coverage-v1",
            "complete": True,
            "start_at": (
                datetime.fromisoformat("2026-02-14T16:00:00+00:00") - timedelta(days=lookback)
            ).isoformat(),
            "end_at": "2026-02-14T16:00:00+00:00",
            "account_ids": [row["id"] for row in self.tables["accounts"]],
        }
        return self.policy(
            {
                "type": "living_reserve",
                "name": "Pure reserve",
                "horizon_days": horizon,
                "method": {
                    "name": "rolling_window_quantile",
                    "lookback_days": lookback,
                    "quantile": q,
                    "essential_categories": ["food"],
                    "exclude_one_off": True,
                },
                "extra_buffer_cents": extra,
                "reconfirm_on_boundary_crossing": True,
            }
        )

    def goal(
        self,
        cash: int = 10_000,
        principal: int = 0,
        low: int = 6000,
        cumulative: int = 15_000,
        contributed: int = 1000,
        target: int = 100_000,
    ) -> dict[str, Any]:
        policy, version = self.policy(
            {
                "type": "goal_saving",
                "name": "Pure goal",
                "target_cents": target,
                "deadline": "2026-12-31",
                "monthly_contribution": {"min_cents": low, "target_cents": low, "max_cents": low},
                "priority": {"minimum_cents": cumulative},
                "cross_goal_reallocation_allowed": False,
                "asset_policy_id": None,
            }
        )
        row = self.row(
            "goals",
            policy_id=policy["id"],
            policy_version_id=version["id"],
            account_id=self.account,
            target_cents=target,
            deadline="2026-12-31",
            allocated_cents=cash + principal,
            monthly_min_cents=low,
            monthly_target_cents=low,
            monthly_max_cents=low,
            minimum_protection_cents=cumulative,
        )
        self.evidence(
            "SIMULATED_GOAL_OWNERSHIP",
            {
                "protocol": "goal-ownership-v1",
                "goal_id": row["id"],
                "policy_id": policy["id"],
                "account_id": self.account,
                "cash_owned_cents": cash,
                "principal_owned_cents": principal,
                "allocated_cents": cash + principal,
                "position_ids": [],
                "simulation": True,
                "user_id": OWNER,
                "as_of": AT,
            },
        )
        self.evidence(
            "SIMULATED_GOAL_MONTH_CONTRIBUTION",
            {
                "protocol": "goal-month-contribution-v1",
                "goal_id": row["id"],
                "period": "2026-02",
                "contributed_cents": contributed,
                "complete": True,
                "simulation": True,
                "user_id": OWNER,
                "as_of": AT,
            },
        )
        self.posting(
            "GOAL_CASH:" + row["id"],
            cash,
            dimension="GOAL_OWNERSHIP",
            metadata={"account_id": self.account, "goal_id": row["id"]},
        )
        return row

    def asset(
        self, *, cap: int = 40_000, maximum: int = 80_000
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        policy, _ = self.policy(
            {
                "type": "asset_authorization",
                "name": "Pure authorization",
                "scope": "general_idle_funds",
                "goal_id": None,
                "allowed_asset_classes": ["CASH_MANAGEMENT"],
                "max_auto_managed_cents": maximum,
                "single_action_cap_cents": cap,
                "max_redemption_delay_days": 1,
                "max_lock_days": 7,
                "max_principal_risk_level": 0,
                "allow_auto_recovery_without_penalty": False,
                "allow_early_withdrawal_with_penalty": False,
            }
        )
        product = self.row(
            "asset_products",
            asset_class="CASH_MANAGEMENT",
            principal_fluctuation=False,
            auto_purchase_allowed=True,
            risk_level=0,
            lock_days=0,
            redemption_delay_days=1,
            minimum_purchase_cents=100,
            effective_from=CONFIRMED,
            effective_until=None,
            maturity_rule={},
        )
        return policy, product

    def income(
        self, amount: int = 50_000, occurred: str = "2026-02-10T04:00:00+00:00"
    ) -> dict[str, Any]:
        transaction = self.expense(
            amount, occurred, confirmed=False, category="salary", role="INCOME", direction="CREDIT"
        )
        bank = next(
            row for row in self.tables["evidence_items"] if row["id"] == transaction["evidence_id"]
        )
        origin = {
            "origin_transaction_id": transaction["id"],
            "origin_account_id": self.account,
            "amount_cents": amount,
            "occurred_at": occurred,
            "observed_at": AT,
            "bank_evidence_id": bank["id"],
            "bank_evidence_hash": bank["content_hash"],
        }
        identity = str(uuid5(UUID(transaction["id"]), "income-location:" + self.account))
        fragment = {
            "fragment_id": identity,
            "origin_transaction_id": transaction["id"],
            "account_id": self.account,
            "spent_cents": 0,
            "assigned_cents": 0,
            "reserved_cents": 0,
            "legacy_reserved_cents": 0,
            "available_cents": amount,
        }
        self.payload = {
            "protocol": "new-funds-ledger-v2",
            "user_id": OWNER,
            "simulation": True,
            "complete": True,
            "as_of": AT,
            "scope_account_ids": [self.account],
            "origins": [origin],
            "fragments": [fragment],
            "reservations": [],
        }
        self.evidence("SIMULATED_NEW_FUNDS_LEDGER", self.payload)
        for portion in ("SPENT", "ASSIGNED", "RESERVED", "AVAILABLE"):
            self.posting(
                "LOT_" + portion + ":" + identity,
                fragment[portion.lower() + "_cents"],
                dimension="INCOME_LOCATION",
                metadata={"account_id": self.account, "origin": origin},
            )
        return transaction

    def facts(self) -> dict[str, Any]:
        # Every generated artifact is a pure-test synthetic original, never a
        # collector substitute or a success assertion about a real experiment.
        tables = copy.deepcopy(self.tables)
        for row in tables["evidence_items"]:
            row["content_hash"] = hashed(row["content"])
        for row in tables["policy_versions"]:
            row["content_hash"] = hashed(row["configuration"])
        coverage = copy.deepcopy(self.coverage)
        if coverage:
            selected = [
                row
                for row in tables["transactions"]
                if datetime.fromisoformat(coverage["start_at"])
                <= datetime.fromisoformat(row["occurred_at"])
                < datetime.fromisoformat(coverage["end_at"])
            ]
            coverage["transaction_ids"] = sorted(row["id"] for row in selected)
            coverage["evidence_ids"] = sorted(row["evidence_id"] for row in selected)
        basis = {
            "tables": tables,
            "events": copy.deepcopy(self.events),
            "expense_history": coverage,
        }
        raw = encoded(basis)
        digest = hashlib.sha256(raw).hexdigest()

        def reference(pointer: str, value: Any) -> dict[str, str]:
            return {
                "artifact_sha256": digest,
                "json_pointer": pointer,
                "value_sha256": hashed(value),
            }

        inventory = {
            table: {
                "row_ids": sorted(row["id"] for row in rows),
                "sha256": hashed(sorted(rows, key=lambda row: row["id"])),
                "source_ref": reference("/tables/" + table, rows),
            }
            for table, rows in tables.items()
        }
        events = [
            {**event, "source_ref": reference("/events/" + str(index), event)}
            for index, event in enumerate(self.events)
        ]
        if coverage:
            coverage["source_refs"] = [reference("/expense_history", copy.deepcopy(coverage))]
        return {
            "protocol": "mvp-financial-facts-v1",
            "user_id": OWNER,
            "timezone": "Asia/Shanghai",
            "as_of": AT,
            "complete": True,
            "tables": tables,
            "inventory": inventory,
            "artifact_originals": {digest: {"utf8": raw.decode("utf8")}},
            "policy_state_events": events,
            "policy_state_events_source_ref": reference("/events", self.events),
            "expense_history": coverage,
            "income_payload": copy.deepcopy(self.payload),
        }


def checkpoint(kind: str = "PROTECTION", **values: Any) -> dict[str, Any]:
    return {
        "checkpoint_id": "pure-" + kind,
        "kind": kind,
        "at": AT,
        "scope": "GENERAL",
        "goal_id": None,
        "horizon_end_at": END,
        "available_mode": "ACTUAL_SETTLED",
        "requested_action_type": None,
        "product_id": None,
        **values,
    }


def measure(fixture: Fixture, kind: str = "PROTECTION", **values: Any) -> dict[str, Any]:
    result = compute_timeline(fixture.facts(), [checkpoint(kind, **values)])
    return result["checkpoints"][0]  # type: ignore[no-any-return]


def component(result: dict[str, Any], kind: str) -> dict[str, Any]:
    return next(item for item in result["components"] if item["kind"] == kind)


def complete_fixture() -> tuple[Fixture, dict[str, Any], dict[str, Any]]:
    fixture = Fixture()
    fixture.emergency()
    fixture.rent()
    for day, amount in ((12, 1000), (13, 2000), (14, 3000)):
        fixture.expense(amount, f"2026-02-{day:02}T04:00:00+00:00")
    fixture.living()
    fixture.goal()
    policy, product = fixture.asset()
    fixture.income()
    return fixture, policy, product


def test_protection_components_are_computed_from_original_rows() -> None:
    fixture, _, _ = complete_fixture()
    original = fixture.facts()
    before = encoded(original)
    result = compute_timeline(original, [checkpoint()])
    assert result["facts_status"] == "VERIFIED"
    measured = result["checkpoints"][0]
    assert measured["status"] == "MEASURED"
    assert measured["available_cash_cents"] == 100_000
    assert measured["protected_required_cents"] == 49_500
    assert measured["safe_authorized_deployable_cents"] is None
    assert component(measured, "LIVING_RESERVE")["windows_cents"] == [3000, 5000]
    assert component(measured, "GOAL")["minimum_remaining_cents"] == 11_000
    assert encoded(original) == before
    assert result["execution_authority"] is False
    assert result["financial_effect_evidence"] is False


def test_e4_uses_simultaneous_financial_income_and_permission_caps() -> None:
    fixture, policy, product = complete_fixture()
    result = measure(
        fixture,
        "DEPLOYMENT",
        policy_id=policy["id"],
        product_id=product["id"],
        requested_action_type="PURCHASE_ASSET",
    )
    assert result["status"] == "MEASURED"
    assert result["safe_authorized_deployable_cents"] == 40_000
    limits = component(result, "DEPLOYMENT_LIMITS")
    assert limits["financial_limit_cents"] == 50_500
    assert limits["eligible_new_income_cents"] == 50_000


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_table",
        "missing_inventory",
        "bad_table_sha",
        "bad_value_sha",
        "bad_bytes",
        "row_changed",
        "foreign_owner",
        "float_cents",
        "bool_cents",
        "naive_time",
        "duplicate_id",
        "bad_evidence_hash",
        "bad_config_hash",
    ],
)
def test_original_integrity_failures_block_even_empty_timelines(mutation: str) -> None:
    fixture, _, _ = complete_fixture()
    facts = fixture.facts()
    if mutation == "missing_table":
        del facts["tables"]["accounts"]
    elif mutation == "missing_inventory":
        del facts["inventory"]["accounts"]
    elif mutation == "bad_table_sha":
        facts["inventory"]["accounts"]["sha256"] = "0" * 64
    elif mutation == "bad_value_sha":
        facts["inventory"]["accounts"]["source_ref"]["value_sha256"] = "0" * 64
    elif mutation == "bad_bytes":
        next(iter(facts["artifact_originals"].values()))["utf8"] += " "
    elif mutation == "row_changed":
        facts["tables"]["accounts"][0]["balance_cents"] += 1
    elif mutation == "foreign_owner":
        fixture.tables["accounts"][0]["user_id"] = str(UUID(int=8888))
        facts = fixture.facts()
    elif mutation in {"float_cents", "bool_cents"}:
        fixture.tables["accounts"][0]["balance_cents"] = (
            100_000.0 if mutation == "float_cents" else True
        )
        facts = fixture.facts()
    elif mutation == "naive_time":
        fixture.tables["evidence_items"][0]["observed_at"] = "2026-02-01T04:00:00"
        facts = fixture.facts()
    elif mutation == "duplicate_id":
        fixture.tables["accounts"].append(copy.deepcopy(fixture.tables["accounts"][0]))
        facts = fixture.facts()
    elif mutation == "bad_evidence_hash":
        facts["tables"]["evidence_items"][0]["content_hash"] = "0" * 64
    else:
        facts["tables"]["policy_versions"][0]["content_hash"] = "0" * 64
    assert validate_facts(facts)["status"] == "MISSING"
    assert compute_timeline(facts, [])["facts_status"] == "MISSING"


@pytest.mark.parametrize(
    "mutation",
    [
        "virtual_cash",
        "gap",
        "wrong_predecessor",
        "no_opening",
        "metadata_change",
        "identity_change",
        "future_leg",
        "cash_mismatch",
    ],
)
def test_actual_cash_cannot_be_fabricated_from_virtual_or_broken_ledger(mutation: str) -> None:
    fixture = Fixture()
    opening = fixture.tables["simulated_bank_postings"][0]
    if mutation == "virtual_cash":
        opening["ledger_dimension"] = "INCOME_LOCATION"
    elif mutation == "no_opening":
        opening["entry_kind"] = "CREDIT"
    elif mutation == "future_leg":
        opening["occurred_at"] = "2026-02-16T04:00:00+00:00"
    elif mutation == "cash_mismatch":
        opening["balance_after_cents"] += 1
    else:
        second = fixture.row(
            "simulated_bank_postings",
            **{key: value for key, value in opening.items() if key not in {"id", "user_id"}},
        )
        second.update(
            sequence_number=2,
            balance_before_cents=100_000,
            delta_cents=0,
            balance_after_cents=100_000,
            previous_posting_id=opening["id"],
            entry_kind="SETTLED",
            external_fact_id=str(UUID(int=9999)),
        )
        if mutation == "gap":
            second["sequence_number"] = 3
        elif mutation == "wrong_predecessor":
            second["previous_posting_id"] = str(UUID(int=9999))
        elif mutation == "metadata_change":
            second["ledger_metadata"] = {"invented": True}
        else:
            second["account_id"] = None
    assert measure(fixture)["status"] == "MISSING"


def test_no_position_virtual_or_forecast_balance_is_actual_cash() -> None:
    fixture = Fixture()
    fixture.tables["accounts"][0]["balance_cents"] = 1_000_000
    fixture.posting("LOT_AVAILABLE:" + str(UUID(int=7777)), 999_999, dimension="INCOME_LOCATION")
    fixture.row(
        "asset_positions",
        principal_cents=999_999,
        status="HELD",
        available_at="2026-02-16T04:00:00+00:00",
        goal_id=None,
    )
    result = measure(fixture)
    assert result["status"] == "MEASURED"
    assert result["available_cash_cents"] == 100_000
    assert component(result, "PROTECTION_TIMELINE")["added_unsettled_principal_cents"] == 0


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_state",
        "state_hash",
        "wrong_policy",
        "accepted_integer",
        "review_hash",
        "stale_confirm",
        "future_confirm",
        "unknown_dsl",
        "precomputed_amount",
    ],
)
def test_protection_requires_original_confirmation_and_state(mutation: str) -> None:
    fixture = Fixture()
    policy, version = fixture.emergency()
    if mutation == "missing_state":
        fixture.events = []
    elif mutation == "wrong_policy":
        fixture.events[0]["policy_id"] = fixture.row("policies", status="ACTIVE")["id"]
    elif mutation == "accepted_integer":
        version["confirmation"]["accepted"] = 1
    elif mutation == "review_hash":
        version["confirmation"]["reviewed_hash"] = "0" * 64
    elif mutation == "stale_confirm":
        fixture.tables["evidence_items"][0]["status"] = "SUPERSEDED"
    elif mutation == "future_confirm":
        version["confirmed_at"] = "2026-02-16T04:00:00+00:00"
    elif mutation == "unknown_dsl":
        version["configuration"]["borrow_unconfirmed_funds"] = True
    elif mutation == "precomputed_amount":
        result = measure(fixture, protected_required_cents=0)
        assert result["status"] == "MISSING"
        return
    facts = fixture.facts()
    if mutation == "state_hash":
        facts["policy_state_events"][0]["source_ref"]["value_sha256"] = "0" * 64
    assert compute_timeline(facts, [checkpoint()])["checkpoints"][0]["status"] == "MISSING"
    assert policy["status"] == "ACTIVE"


def test_recurring_due_day_clamps_to_february_and_range_uses_upper_bound() -> None:
    fixture = Fixture()
    _, version = fixture.rent(day=31)
    version["configuration"]["amount_rule"] = {
        "kind": "range",
        "min_cents": 10_000,
        "max_cents": 20_000,
    }
    version["content_hash"] = hashed(version["configuration"])
    version["confirmation"]["reviewed_hash"] = version["content_hash"]
    result = measure(fixture, "DUE")
    assert result["status"] == "MEASURED"
    assert component(result, "RECURRING")["due_on"] == "2026-02-28"
    assert result["protected_required_cents"] == 20_000


def test_historical_unpaid_cannot_be_assumed_zero_or_cleared_by_natural_expiry() -> None:
    fixture = Fixture()
    policy, version = fixture.rent(day=10)
    assert measure(fixture, "DUE")["status"] == "MISSING"
    fixture.settlement(policy, 5000)
    assert measure(fixture, "DUE")["protected_required_cents"] == 15_000
    version["valid_until"] = "2026-02-11T04:00:00+00:00"
    version["confirmation"]["effective_until"] = version["valid_until"]
    fixture.events.append(
        {
            "policy_id": policy["id"],
            "version_id": version["id"],
            "occurred_at": version["valid_until"],
            "from_status": "ACTIVE",
            "to_status": "EXPIRED",
        }
    )
    policy["status"] = "EXPIRED"
    assert measure(fixture, "DUE")["protected_required_cents"] == 15_000


def test_bills_are_unique_actual_balances_not_duplicated_by_bill_policy() -> None:
    fixture = Fixture()
    account = fixture.row("accounts", account_type="CREDIT_CARD", currency="CNY", balance_cents=0)
    bill = fixture.row(
        "credit_card_bills",
        account_id=account["id"],
        statement_date="2026-02-01",
        due_date="2026-02-20",
        total_cents=10_000,
        paid_cents=3000,
        status="PARTIALLY_PAID",
    )
    payload = {
        key: bill[key]
        for key in (
            "account_id",
            "statement_date",
            "due_date",
            "total_cents",
            "paid_cents",
            "status",
        )
    }
    payload.update(bill_id=bill["id"], user_id=OWNER, simulation=True)
    bill["evidence_id"] = fixture.evidence("SIMULATED_CREDIT_CARD_BILL", payload)["id"]
    fixture.policy(
        {
            "type": "recurring_obligation",
            "name": "Pay original bill",
            "amount_rule": {"kind": "bill_balance", "account_id": account["id"]},
        }
    )
    result = measure(fixture, "DUE")
    assert result["status"] == "MEASURED"
    assert result["protected_required_cents"] == 7000
    assert len([item for item in result["components"] if item["kind"] == "BILL"]) == 1


def test_quantile_uses_decimal_probability_nearest_rank_without_float_ceil_drift() -> None:
    fixture = Fixture()
    start = datetime.fromisoformat("2026-02-14T16:00:00+00:00") - timedelta(days=100)
    for day in range(100):
        fixture.expense(day + 1, (start + timedelta(days=day, hours=12)).isoformat())
    fixture.living(lookback=100, horizon=1, q=0.07, extra=0)
    result = measure(fixture)
    assert result["status"] == "MEASURED"
    assert component(result, "LIVING_RESERVE")["rank"] == 7
    assert result["protected_required_cents"] == 7


def test_reserve_excludes_system_legs_unconfirmed_categories_and_one_off() -> None:
    fixture = Fixture()
    fixture.expense(1000, "2026-02-12T04:00:00+00:00")
    fixture.expense(9999, "2026-02-13T04:00:00+00:00", confirmed=False)
    fixture.expense(9999, "2026-02-13T04:00:00+00:00", one_off=True)
    fixture.expense(9999, "2026-02-13T04:00:00+00:00", role="ASSET_PURCHASE")
    fixture.expense(9999, "2026-02-13T04:00:00+00:00", role="INCOME", direction="CREDIT")
    fixture.living(horizon=1, q=1.0, extra=0)
    result = measure(fixture)
    assert result["status"] == "MEASURED"
    assert result["protected_required_cents"] == 1000
    assert len(component(result, "LIVING_RESERVE")["included_transaction_ids"]) == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "category_integer",
        "conflicting_category",
        "short_coverage",
        "missing_txn_id",
        "missing_bank_proof",
        "uncertain_role",
    ],
)
def test_expense_coverage_or_classification_gaps_are_missing(mutation: str) -> None:
    fixture = Fixture()
    transaction = fixture.expense(1000, "2026-02-12T04:00:00+00:00")
    fixture.living()
    if mutation == "category_integer":
        fixture.tables["evidence_items"][1]["content"]["confirmed"] = 1
    elif mutation == "conflicting_category":
        fixture.evidence(
            "SIMULATED_USER_CATEGORY_CONFIRMATION",
            {"transaction_id": transaction["id"], "category": "other", "confirmed": True},
            "USER_DECLARED",
        )
    elif mutation == "short_coverage":
        assert fixture.coverage is not None
        fixture.coverage["start_at"] = "2026-02-13T16:00:00+00:00"
    elif mutation == "missing_bank_proof":
        fixture.tables["evidence_items"][0]["status"] = "SUPERSEDED"
    elif mutation == "uncertain_role":
        fixture.tables["evidence_items"][0]["content"]["economic_role"] = "UNKNOWN"
    facts = fixture.facts()
    if mutation == "missing_txn_id":
        facts["expense_history"]["transaction_ids"] = []
    assert compute_timeline(facts, [checkpoint()])["checkpoints"][0]["status"] == "MISSING"


def test_goal_minimum_overlapping_commitments_use_max_and_remaining_target_cap() -> None:
    fixture = Fixture()
    fixture.goal(cash=10_000, low=6000, cumulative=50_000, target=20_000)
    result = measure(fixture)
    assert result["status"] == "MEASURED"
    assert component(result, "GOAL")["minimum_remaining_cents"] == 10_000
    assert result["protected_required_cents"] == 20_000


def test_expired_goal_never_releases_owned_cash() -> None:
    fixture = Fixture()
    fixture.goal()
    policy = fixture.tables["policies"][0]
    version = fixture.tables["policy_versions"][0]
    version["valid_until"] = "2026-02-10T04:00:00+00:00"
    version["confirmation"]["effective_until"] = version["valid_until"]
    fixture.events.append(
        {
            "policy_id": policy["id"],
            "version_id": version["id"],
            "occurred_at": version["valid_until"],
            "from_status": "ACTIVE",
            "to_status": "EXPIRED",
        }
    )
    policy["status"] = "EXPIRED"
    result = measure(fixture)
    assert result["status"] == "MEASURED"
    assert result["protected_required_cents"] == 10_000


def test_unassigned_goal_account_cash_is_held_without_virtual_double_counting() -> None:
    fixture = Fixture()
    account = fixture.row("accounts", account_type="GOAL", currency="CNY", balance_cents=20_000)
    fixture.posting("CASH:" + account["id"], 20_000, account_id=account["id"])
    result = measure(fixture)
    assert result["available_cash_cents"] == 120_000
    assert result["protected_required_cents"] == 20_000


@pytest.mark.parametrize("old", [True, False])
def test_income_occurrence_identity_survives_transfer_or_late_observation(old: bool) -> None:
    fixture = Fixture()
    policy, product = fixture.asset()
    fixture.income(30_000, occurred="2026-01-31T04:00:00+00:00" if old else CONFIRMED)
    result = measure(
        fixture,
        "DEPLOYMENT",
        policy_id=policy["id"],
        product_id=product["id"],
        requested_action_type="PURCHASE_ASSET",
    )
    assert result["status"] == "MEASURED"
    assert result["safe_authorized_deployable_cents"] == (0 if old else 30_000)


@pytest.mark.parametrize(
    "mutation",
    [
        "principal_role",
        "wrong_origin_amount",
        "wrong_fragment_id",
        "virtual_inflation",
        "future_occurrence",
        "wrong_observed",
        "source_owner",
        "unsettled_action",
    ],
)
def test_e4_rejects_unbound_or_unreconciled_income_and_execution(mutation: str) -> None:
    fixture = Fixture()
    policy, product = fixture.asset()
    transaction = fixture.income()
    assert fixture.payload is not None
    if mutation == "principal_role":
        next(
            row
            for row in fixture.tables["evidence_items"]
            if row["id"] == transaction["evidence_id"]
        )["content"]["economic_role"] = "PRINCIPAL_RETURN"
    elif mutation == "wrong_origin_amount":
        fixture.payload["origins"][0]["amount_cents"] += 1
    elif mutation == "wrong_fragment_id":
        fixture.payload["fragments"][0]["fragment_id"] = str(UUID(int=9876))
    elif mutation == "virtual_inflation":
        fixture.payload["fragments"][0]["available_cents"] += 1
    elif mutation == "future_occurrence":
        fixture.payload["origins"][0]["occurred_at"] = "2026-02-16T04:00:00+00:00"
    elif mutation == "wrong_observed":
        transaction["observed_at"] = CONFIRMED
    elif mutation == "source_owner":
        fixture.payload["user_id"] = str(UUID(int=8989))
    else:
        fixture.row("action_plans", action_type="PURCHASE_ASSET", status="UNKNOWN")
    result = measure(
        fixture,
        "DEPLOYMENT",
        policy_id=policy["id"],
        product_id=product["id"],
        requested_action_type="PURCHASE_ASSET",
    )
    assert result["status"] == "MISSING"


def test_reserved_purchase_consumes_both_actual_cash_and_managed_capacity_once() -> None:
    fixture = Fixture()
    policy, product = fixture.asset(cap=80_000, maximum=80_000)
    fixture.income(80_000)
    action = fixture.row(
        "action_plans", action_type="PURCHASE_ASSET", status="PLANNED", amount_cents=30_000
    )
    fixture.row(
        "action_resource_reservations",
        action_plan_id=action["id"],
        resource_kind="CASH",
        resource_key=fixture.account,
        amount_cents=30_000,
        status="RESERVED",
    )
    result = measure(
        fixture,
        "DEPLOYMENT",
        policy_id=policy["id"],
        product_id=product["id"],
        requested_action_type="PURCHASE_ASSET",
    )
    assert result["status"] == "MEASURED"
    assert result["safe_authorized_deployable_cents"] == 50_000
    assert component(result, "DEPLOYMENT_LIMITS")["managed_principal_cents"] == 30_000


def test_unsafe_baseline_returns_measured_zero_without_turning_gap_into_missing() -> None:
    fixture = Fixture(cash=10_000)
    fixture.emergency(20_000)
    policy, product = fixture.asset()
    fixture.income(5000)
    result = measure(
        fixture,
        "DEPLOYMENT",
        policy_id=policy["id"],
        product_id=product["id"],
        requested_action_type="PURCHASE_ASSET",
    )
    assert result["status"] == "MEASURED"
    assert result["safe_authorized_deployable_cents"] == 0


def test_future_emergency_floor_constrains_deployment_without_being_current_cash() -> None:
    fixture = Fixture()
    policy, product = fixture.asset(cap=80_000)
    fixture.income(80_000)
    _, version = fixture.emergency(90_000)
    start = "2026-02-19T16:00:00+00:00"
    version["configuration"]["valid_from"] = "2026-02-20"
    version["valid_from"] = start
    version["confirmation"]["effective_from"] = start
    version["confirmation"]["reviewed_hash"] = hashed(version["configuration"])
    result = measure(
        fixture,
        "DEPLOYMENT",
        policy_id=policy["id"],
        product_id=product["id"],
        requested_action_type="PURCHASE_ASSET",
    )
    assert result["status"] == "MEASURED"
    assert result["protected_required_cents"] == 0
    assert result["safe_authorized_deployable_cents"] == 10_000


def test_missing_selectors_unsupported_goal_and_past_time_keep_all_denominators() -> None:
    fixture = Fixture()
    declared = [
        checkpoint("DEPLOYMENT"),
        checkpoint(scope="GOAL", goal_id=str(UUID(int=9876))),
        checkpoint(at=CONFIRMED),
    ]
    result = compute_timeline(fixture.facts(), declared)
    assert len(result["checkpoints"]) == 3
    assert [item["status"] for item in result["checkpoints"]] == ["MISSING"] * 3
    assert result["facts_status"] == "VERIFIED"
    assert validate_facts(fixture.facts())["status"] == "VERIFIED"


def test_transferred_old_income_keeps_original_occurrence_and_remains_ineligible() -> None:
    fixture = Fixture()
    policy, product = fixture.asset()
    fixture.income(30_000, occurred="2026-01-31T04:00:00+00:00")
    destination = fixture.row("accounts", account_type="CASH", currency="CNY", balance_cents=30_000)
    fixture.posting("CASH:" + destination["id"], 30_000, account_id=destination["id"])
    assert fixture.payload is not None
    fragment = fixture.payload["fragments"][0]
    old_id = fragment["fragment_id"]
    fragment["account_id"] = destination["id"]
    fragment["fragment_id"] = str(
        uuid5(UUID(fragment["origin_transaction_id"]), "income-location:" + destination["id"])
    )
    fixture.payload["scope_account_ids"].append(destination["id"])
    for posting in fixture.tables["simulated_bank_postings"]:
        if old_id in posting["ledger_key"]:
            posting["ledger_key"] = posting["ledger_key"].replace(old_id, fragment["fragment_id"])
            posting["ledger_metadata"]["account_id"] = destination["id"]
    result = measure(
        fixture,
        "DEPLOYMENT",
        policy_id=policy["id"],
        product_id=product["id"],
        requested_action_type="PURCHASE_ASSET",
    )
    assert result["status"] == "MEASURED"
    assert result["available_cash_cents"] == 130_000
    assert result["safe_authorized_deployable_cents"] == 0


def fixed_terms(product: dict[str, Any]) -> None:
    product["maturity_rule"] = {
        "protocol": "fixed-principal-return-v1",
        "kind": "RETURN_TO_CASH",
        "day_basis": "CALENDAR",
        "guaranteed": True,
        "rollover": False,
        "auto_rollover": False,
        "term_days": 2,
        "settlement_delay_days": 1,
        "principal_return_bps": 10000,
        "yield_rule": {
            "protocol": "simple-annual-yield-v1",
            "simulation": True,
            "annual_yield_bps": 200,
            "basis": "ACT_365",
            "accrual": "UNTIL_MATURITY",
            "fee_cents": 0,
            "purchase_fee_bps": 0,
            "redemption_fee_bps": 0,
        },
    }


def test_original_fixed_return_terms_only_release_candidate_after_return_day() -> None:
    fixture = Fixture()
    policy, product = fixture.asset(cap=80_000)
    fixture.income(80_000)
    _, version = fixture.emergency(90_000)
    version["configuration"]["valid_from"] = "2026-02-20"
    version["valid_from"] = "2026-02-19T16:00:00+00:00"
    version["confirmation"]["effective_from"] = version["valid_from"]
    version["confirmation"]["reviewed_hash"] = hashed(version["configuration"])
    fixed_terms(product)
    result = measure(
        fixture,
        "DEPLOYMENT",
        policy_id=policy["id"],
        product_id=product["id"],
        requested_action_type="PURCHASE_ASSET",
    )
    assert result["status"] == "MEASURED"
    assert result["safe_authorized_deployable_cents"] == 80_000
    assert component(result, "DEPLOYMENT_LIMITS")["candidate_return_on"] == "2026-02-18"
    assert component(result, "PROTECTION_TIMELINE")["assumed_future_income_cents"] == 0


@pytest.mark.parametrize(
    "mutation",
    [
        "rollover",
        "fractional_principal",
        "fees",
        "future_product",
        "on_request",
        "unknown_rule",
        "unsettled_bank",
    ],
)
def test_e4_unproved_return_or_fee_terms_remain_missing(mutation: str) -> None:
    fixture = Fixture()
    policy, product = fixture.asset()
    fixture.income()
    fixed_terms(product)
    if mutation == "rollover":
        product["maturity_rule"]["auto_rollover"] = True
    elif mutation == "fractional_principal":
        product["maturity_rule"]["principal_return_bps"] = 9999
    elif mutation == "fees":
        product["maturity_rule"]["yield_rule"]["purchase_fee_bps"] = 1
    elif mutation == "future_product":
        product["effective_from"] = "2026-02-16T04:00:00+00:00"
    elif mutation == "on_request":
        product["maturity_rule"]["protocol"] = "planned-principal-return-v1"
    elif mutation == "unknown_rule":
        product["maturity_rule"]["instant_cash"] = True
    else:
        fixture.row("bank_operations", status="ACCEPTED", available_at="2026-02-16T04:00:00+00:00")
    assert (
        measure(
            fixture,
            "DEPLOYMENT",
            policy_id=policy["id"],
            product_id=product["id"],
            requested_action_type="PURCHASE_ASSET",
        )["status"]
        == "MISSING"
    )


@pytest.mark.parametrize("case", ["class", "min_purchase", "single_cap", "managed_cap", "risk"])
def test_known_product_and_cap_restrictions_produce_real_integer_limits(case: str) -> None:
    fixture = Fixture()
    policy, product = fixture.asset(cap=20_000, maximum=20_000)
    fixture.income()
    if case == "class":
        product["asset_class"] = "OTHER"
    elif case == "min_purchase":
        product["minimum_purchase_cents"] = 20_001
    elif case == "risk":
        product["risk_level"] = 1
    elif case == "managed_cap":
        version = fixture.tables["policy_versions"][0]
        version["configuration"]["max_auto_managed_cents"] = 10_000
        version["configuration"]["single_action_cap_cents"] = 10_000
        version["confirmation"]["reviewed_hash"] = hashed(version["configuration"])
    result = measure(
        fixture,
        "DEPLOYMENT",
        policy_id=policy["id"],
        product_id=product["id"],
        requested_action_type="PURCHASE_ASSET",
    )
    assert result["status"] == "MEASURED"
    assert result["safe_authorized_deployable_cents"] == (
        {"single_cap": 20_000, "managed_cap": 10_000}.get(case, 0)
    )


@pytest.mark.parametrize("manual", [True, False])
def test_managed_capacity_counts_proved_auto_principal_and_excludes_manual(manual: bool) -> None:
    fixture = Fixture()
    policy, product = fixture.asset(cap=80_000)
    fixture.income(80_000)
    version = fixture.tables["policy_versions"][0]
    action = fixture.row(
        "action_plans",
        action_type="PURCHASE_ASSET",
        status="SUCCEEDED",
        amount_cents=30_000,
        product_id=product["id"],
        policy_version_id=version["id"],
    )
    receipt = fixture.row(
        "action_receipts", action_plan_id=action["id"], status="SUCCEEDED", executed_cents=30_000
    )
    transaction = fixture.row("transactions", action_plan_id=action["id"], amount_cents=30_000)
    position = fixture.row(
        "asset_positions",
        account_id=fixture.account,
        goal_id=None,
        product_id=product["id"],
        principal_cents=30_000,
        status="HELD",
        policy_version_id=None if manual else version["id"],
    )
    proof = {
        key: position[key]
        for key in (
            "account_id",
            "goal_id",
            "product_id",
            "principal_cents",
            "status",
            "policy_version_id",
        )
    }
    proof.update(
        position_id=position["id"],
        simulation=True,
        user_id=OWNER,
        as_of=AT,
        acquisition="synthetic_user_manual_purchase" if manual else "synthetic_auto_purchase",
        purchase_action_id=action["id"],
        purchase_receipt_id=receipt["id"],
        purchase_transaction_id=transaction["id"],
    )
    fixture.evidence("SIMULATED_BANK_POSITION", proof)
    result = measure(
        fixture,
        "DEPLOYMENT",
        policy_id=policy["id"],
        product_id=product["id"],
        requested_action_type="PURCHASE_ASSET",
    )
    assert result["status"] == "MEASURED"
    assert result["available_cash_cents"] == 100_000
    assert result["safe_authorized_deployable_cents"] == (80_000 if manual else 50_000)


def test_same_day_policy_expiration_is_exclusive_and_future_cash_is_not_added() -> None:
    fixture = Fixture()
    _, version = fixture.emergency(5000)
    version["valid_until"] = AT
    version["confirmation"]["effective_until"] = AT
    result = measure(fixture)
    assert result["status"] == "MEASURED"
    assert result["protected_required_cents"] == 0


def test_raw_artifact_duplicate_json_keys_are_rejected_even_with_correct_byte_sha() -> None:
    facts = Fixture().facts()
    raw = '{"tables":[],"tables":{}}'
    digest = hashlib.sha256(raw.encode()).hexdigest()
    facts["artifact_originals"] = {digest: {"utf8": raw}}
    for descriptor in facts["inventory"].values():
        descriptor["source_ref"]["artifact_sha256"] = digest
    assert validate_facts(facts)["status"] == "MISSING"


def test_duplicate_and_malformed_checkpoints_remain_missing_without_losing_units() -> None:
    declared = [checkpoint(), checkpoint(), None]
    result = compute_timeline(Fixture().facts(), declared)  # type: ignore[arg-type]
    assert len(result["checkpoints"]) == 3
    assert [row["status"] for row in result["checkpoints"]] == ["MISSING"] * 3
    assert result["facts_status"] == "VERIFIED"


def test_first_local_day_floor_uses_original_shanghai_midnight() -> None:
    fixture = Fixture()
    _, version = fixture.emergency()
    start = "2026-02-14T16:00:00+00:00"
    version["configuration"]["valid_from"] = "2026-02-15"
    version["valid_from"] = start
    version["confirmation"]["effective_from"] = start
    version["confirmation"]["reviewed_hash"] = hashed(version["configuration"])
    assert measure(fixture)["protected_required_cents"] == 5000


def test_original_v1_product_terms_do_not_invent_a_dated_return() -> None:
    fixture = Fixture()
    policy, product = fixture.asset()
    fixture.income()
    product["maturity_rule"] = {"kind": "RETURN_TO_CASH", "auto_rollover": False}
    result = measure(
        fixture,
        "DEPLOYMENT",
        policy_id=policy["id"],
        product_id=product["id"],
        requested_action_type="PURCHASE_ASSET",
    )
    assert result["status"] == "MEASURED"
    assert component(result, "DEPLOYMENT_LIMITS")["candidate_return_on"] is None


def test_removing_captured_state_event_cannot_leave_complete_facts_verified() -> None:
    fixture = Fixture()
    fixture.emergency()
    facts = fixture.facts()
    facts["policy_state_events"] = []
    assert validate_facts(facts)["status"] == "MISSING"


def test_oracle_imports_only_standard_library_and_has_no_p_evaluation() -> None:
    path = Path(__file__).resolve().parents[1] / "mvp_financial_oracles.py"
    tree = ast.parse(path.read_text(encoding="utf8"))
    modules = {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    modules.update(
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    assert modules <= {
        "__future__",
        "calendar",
        "hashlib",
        "json",
        "math",
        "re",
        "collections",
        "datetime",
        "fractions",
        "typing",
        "uuid",
    }
