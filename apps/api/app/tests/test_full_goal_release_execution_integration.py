"""One real isolated-PG candidate. Only the root coordinator runs this node."""

import json
from typing import Any
from uuid import UUID

import pytest
from app.db.models import (
    Account,
    ActionPlan,
    ActionReceipt,
    Goal,
    SimulatedBankPosting,
    Transaction,
)
from app.domain.external_bank_fact_types import ExternalFactRequest
from app.domain.policy_configuration import configuration_hash
from app.services import full_goal_release_execution as service
from app.services.action_contracts import ConfirmActionRequest, GoalIntent, PrepareActionRequest
from app.services.audit_chain import verify_audit_chain
from app.services.demo_seed import DEMO_USER_ID
from app.services.execution import confirm_action, execute_action, prepare_action
from app.services.external_bank_facts import ingest_external_fact
from app.services.full_goal_release_authorization import _reader
from app.services.full_goal_release_execution import GoalReleaseExecuteRequest
from app.services.full_goal_release_inventory import read_goal_release_inventory
from app.services.full_policy_lifecycle import canonical_candidate
from app.services.income_ledger import read_income_state
from app.tests.test_full_goals_api import confirmed_existing_goal
from app.tests.test_full_projection_api import physical_snapshot
from app.tests.test_goal_api import NOW
from app.tests.test_goal_api import goal_client as goal_client
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration
URL = "/api/v1/goal-cash-releases"


def income_original(engine: Engine) -> dict[str, Any]:
    with _reader(engine) as session:
        original = read_income_state(session, DEMO_USER_ID, NOW).ledger.model_dump(mode="json")
    # Projection may update the observation clock; all monetary/source/use fields stay exact.
    original.pop("as_of")
    return original


def arrange_release(client: TestClient, engine: Engine) -> dict[str, Any]:
    goal_text, full_model = confirmed_existing_goal(client, engine)
    goal_id = UUID(goal_text)
    full_model["configuration"]["monthly_contribution"] = {
        "min_cents": 0,
        "target_cents": 20000,
        "max_cents": 20000,
    }
    full_model["configuration"]["minimum_guarantee_cents"] = 10000
    model_url = f"/api/v1/goals/{goal_id}/full-model"
    reviewed = client.post(
        model_url + "/preview",
        json={name: full_model[name] for name in ("expected_version_id", "configuration")},
    )
    assert reviewed.status_code == 200, reviewed.text
    saved = client.post(
        model_url + "/confirm",
        json=full_model
        | {
            "accepted": True,
            "reviewed_full_hash": reviewed.json()["full_configuration_hash"],
            "reviewed_base_hash": reviewed.json()["base_configuration_hash"],
        },
    )
    assert saved.status_code == 200, saved.text
    with Session(engine) as session:
        cash_id = session.scalar(
            select(Account.id)
            .where(Account.user_id == DEMO_USER_ID, Account.account_type == "CASH")
            .order_by(Account.id)
        )
        goal = session.get(Goal, goal_id)
        assert cash_id is not None and goal is not None
        goal_version = goal.policy_version_id
    bank_income = ingest_external_fact(
        engine,
        DEMO_USER_ID,
        ExternalFactRequest(
            user_id=DEMO_USER_ID,
            account_id=cash_id,
            kind="INCOME",
            amount_cents=600000,
            external_ref="release_actual_payroll",
            idempotency_key="release_actual_payroll",
            counterparty_ref="payroll",
            occurred_at=NOW,
        ),
        NOW,
    )
    assert bank_income.bank_status == "SETTLED" and bank_income.projection_status == "PROJECTED"
    allocated = prepare_action(
        engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="release_actual_original_allocation",
            intent=GoalIntent(kind="allocate_goal", goal_id=goal_id),
        ),
        NOW,
    )
    assert allocated.effect.amount_cents == 20000
    if allocated.autonomy_level == "ASK_ONCE":
        confirm_action(
            engine,
            DEMO_USER_ID,
            allocated.action_id,
            ConfirmActionRequest(effect_hash=allocated.effect_hash, accepted=True),
            NOW,
        )
    complete = execute_action(engine, DEMO_USER_ID, allocated.action_id, NOW)
    assert complete.status == "SUCCEEDED" and complete.receipt is not None
    annual = client.get("/api/v1/planning/full-annual")
    assert annual.status_code == 200, annual.text
    point = next(
        row
        for row in annual.json()["projection"]["original_execution_view"]["calculation_trace"]
        if row["day"] == 0 and row["phase"] == "BEFORE_PAYMENT"
    )
    protected = point["protected_cents_by_reason"]
    # Explicit author declaration for a functional risk fixture, derived from the actual
    # current snapshot; it is neither a frozen case nor a financial-effect experiment.
    emergency = (
        point["cash_cents"]
        - protected["goal_cash"]
        - protected["obligations"]
        - protected["living"]
        + 10000
    )
    assert emergency > 0
    epoch = full_model["expected_epoch_id"]
    declared = client.post(
        "/api/v1/policy-declarations",
        json={
            "configuration": {
                "type": "emergency_buffer",
                "name": "实际现金回拨当前保护缺口",
                "amount_cents": emergency,
            },
            "expected_epoch_id": epoch,
            "idempotency_key": "release_actual_emergency",
        },
    )
    assert declared.status_code == 200, declared.text
    confirmed = client.post(
        f"/api/v1/policy-proposals/{declared.json()['proposal_id']}/confirm",
        json={"accepted": True, "reviewed_hash": declared.json()["configuration_hash"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    configuration = {
        "type": "cross_goal_reallocation",
        "enabled": True,
        "source_goal_ids": [goal_text],
        "emergency_conditions": [
            "HARD_OBLIGATION_SHORTFALL",
            "LIVING_RESERVE_SHORTFALL",
            "EMERGENCY_BUFFER_SHORTFALL",
        ],
        "single_action_cap_cents": 50000,
        "total_cap_cents": 100000,
        "valid_from": "2026-10-01",
        "valid_until": "2026-10-31",
    }
    planned = client.post(
        "/api/v1/full-policies/confirm",
        json={
            "template_name": "CrossGoalReallocationPolicy",
            "configuration": configuration,
            "reviewed_hash": configuration_hash(
                canonical_candidate("CrossGoalReallocationPolicy", configuration)
            ),
            "accepted": True,
            "reason": "真实规划规则；仍需要独立许可和逐行动确认",
            "idempotency_key": "release_actual_planning",
        },
    )
    assert planned.status_code == 200 and planned.json()["bank_authority"] is False, planned.text
    policy = planned.json()
    auth_url = f"/api/v1/goal-release-authorizations/policies/{policy['policy_id']}"
    auth_body = {"expected_epoch_id": epoch, "expected_policy_version_id": policy["version_id"]}
    reviewed_auth = client.post(auth_url + "/preview", json=auth_body)
    assert reviewed_auth.status_code == 200, reviewed_auth.text
    auth = client.post(
        auth_url + "/confirm",
        json=auth_body
        | {
            "accepted": True,
            "reviewed_scope_hash": reviewed_auth.json()["scope_hash"],
            "idempotency_key": "release_actual_dedicated_scope",
        },
    )
    assert auth.status_code == 200 and auth.json()["current_scope_status"] == "CURRENT", auth.text
    return {
        "policy_id": policy["policy_id"],
        "source_goal_id": goal_text,
        "expected_policy_version_id": policy["version_id"],
        "expected_goal_policy_version_id": str(goal_version),
        "expected_epoch_id": epoch,
        "authorization_epoch_id": epoch,
        "authorization_idempotency_key": "release_actual_dedicated_scope",
        "destination_account_id": str(cash_id),
        "idempotency_key": "release_actual_original_action",
    }


def test_actual_goal_cash_release_consent_response_loss_rollback_and_original_key_recovery(
    goal_client: tuple[TestClient, Engine],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine = goal_client
    body = arrange_release(client, engine)
    source_goal = UUID(body["source_goal_id"])
    before_read = physical_snapshot(engine)
    preview = client.post(URL + "/preview", json=body)
    assert preview.status_code == 200, preview.text
    candidate = preview.json()
    assert candidate["state"] == "READY" and candidate["amount_cents"] == 10000, candidate
    assert candidate["protection"]["compared_point_count"] == 1098
    assert candidate["actual_inventory"]["policy_usage"]["cap_occupied_cents"] == 0
    assert candidate["bank_authority"] is candidate["creates_new_income"] is False
    for extra in ("amount_cents", "now", "bank_fact", "grant"):
        assert client.post(URL + "/prepare", json=body | {extra: True}).status_code == 422
    assert physical_snapshot(engine) == before_read
    prepared = client.post(URL + "/prepare", json=body)
    assert prepared.status_code == 200, prepared.text
    original = prepared.json()
    action_id = UUID(original["action_id"])
    effect_hash = original["original_command"]["effect_hash"]
    execute_body = {
        "accepted": True,
        "reviewed_effect_hash": effect_hash,
        "expected_epoch_id": body["expected_epoch_id"],
    }
    consent = GoalReleaseExecuteRequest.model_validate_json(json.dumps(execute_body))
    initial = physical_snapshot(engine)
    for wrong in (
        {},
        execute_body | {"accepted": False},
        execute_body | {"accepted": 1},
        execute_body | {"reviewed_effect_hash": "0" * 64},
        execute_body | {"expected_epoch_id": str(UUID(int=1))},
    ):
        assert client.post(f"{URL}/actions/{action_id}/execute", json=wrong).status_code in {
            409,
            422,
        }
    assert physical_snapshot(engine) == initial
    income_before = income_original(engine)
    with Session(engine) as session:
        action = session.get(ActionPlan, action_id)
        goal = session.get(Goal, source_goal)
        assert action is not None and goal is not None
        assert action.autonomy_level == "ASK_ONCE" and action.status == "PLANNED"
        assert action.policy_version_id == goal.policy_version_id
        assert original["action_confirmation_verified"] is False
        goal_allocated = goal.allocated_cents
        cash_before = {
            row.id: row.balance_cents
            for row in session.scalars(select(Account).where(Account.user_id == DEMO_USER_ID))
        }
        transactions_before = set(
            session.scalars(select(Transaction.id).where(Transaction.user_id == DEMO_USER_ID))
        )
    actual_bank = service.process_goal_release_bank

    def committed_response_loss(*args: Any, **kwargs: Any) -> None:
        actual_bank(*args, **kwargs)
        raise TimeoutError("actual bank COMMIT, response deliberately lost")

    monkeypatch.setattr(service, "process_goal_release_bank", committed_response_loss)
    with pytest.raises(TimeoutError, match="actual bank COMMIT"):
        service.execute_goal_release_execution(engine, DEMO_USER_ID, action_id, consent, NOW)
    monkeypatch.setattr(service, "process_goal_release_bank", actual_bank)
    unknown = client.get(f"{URL}/actions/{action_id}")
    assert unknown.status_code == 200, unknown.text
    lost = unknown.json()
    assert lost["original_action_status"] == "UNKNOWN" and lost["unresolved"] is True
    assert (
        lost["original_bank_status"] == "SETTLED" and lost["bank_settlement_legs_verified"] is True
    )
    assert lost["service_receipt_verified"] is False and lost["receipt_id"] is None
    assert lost["action_confirmation_verified"] is True and lost["action_confirmation_evidence_id"]
    with _reader(engine) as session:
        inventory = read_goal_release_inventory(
            session,
            DEMO_USER_ID,
            source_goal,
            UUID(body["expected_epoch_id"]),
            UUID(body["expected_goal_policy_version_id"]),
            UUID(body["policy_id"]),
            NOW,
        )
        assert inventory.policy_usage.settled_cents == 10000
        assert inventory.residual.exactly_attributed_goal_cash_cents == 10000
        assert inventory.application_projection_matched is False
    with Session(engine) as session:
        original_legs = list(
            session.scalars(
                select(SimulatedBankPosting).where(SimulatedBankPosting.operation_id == action_id)
            )
        )
        assert len(original_legs) == 3
        assert {row.ledger_dimension for row in original_legs} == {"ECONOMIC", "GOAL_OWNERSHIP"}
    actual_projection = service._goal_projection

    def projection_failure(*args: Any, **kwargs: Any) -> None:
        actual_projection(*args, **kwargs)
        raise RuntimeError("actual goal projection rollback")

    monkeypatch.setattr(service, "_goal_projection", projection_failure)
    with pytest.raises(RuntimeError, match="actual goal projection rollback"):
        service.execute_goal_release_execution(engine, DEMO_USER_ID, action_id, consent, NOW)
    monkeypatch.setattr(service, "_goal_projection", actual_projection)
    with Session(engine) as session:
        goal = session.get(Goal, source_goal)
        assert goal is not None and goal.allocated_cents == goal_allocated
        assert {
            row.id: row.balance_cents
            for row in session.scalars(select(Account).where(Account.user_id == DEMO_USER_ID))
        } == cash_before
        assert (
            set(session.scalars(select(Transaction.id).where(Transaction.user_id == DEMO_USER_ID)))
            == transactions_before
        )
        assert (
            session.scalar(
                select(ActionReceipt.id).where(ActionReceipt.action_plan_id == action_id)
            )
            is None
        )
        assert (
            len(
                list(
                    session.scalars(
                        select(SimulatedBankPosting).where(
                            SimulatedBankPosting.operation_id == action_id
                        )
                    )
                )
            )
            == 3
        )
    assert income_original(engine) == income_before
    revoked = client.post(
        f"/api/v1/full-policies/{body['policy_id']}/revoke",
        json={
            "expected_version_id": body["expected_policy_version_id"],
            "reason": "银行已结算后撤销未来范围",
            "idempotency_key": "release_actual_post_settlement_revoke",
        },
    )
    assert revoked.status_code == 200, revoked.text

    def no_new_permission(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("historical same-key bank recovery cannot require a new current grant")

    monkeypatch.setattr(service, "_fresh_candidate", no_new_permission)
    recovered = client.post(f"{URL}/actions/{action_id}/execute", json=execute_body)
    assert recovered.status_code == 200, recovered.text
    final = recovered.json()
    assert (
        final["original_action_status"] == "SUCCEEDED" and final["service_receipt_verified"] is True
    )
    assert final["bank_settlement_legs_verified"] is True and final["unresolved"] is False
    assert final["original_command"] == original["original_command"]
    assert final["original_request_hash"] == original["original_request_hash"]
    assert final["receipt_is_current_authority"] is final["economic_experiment_verified"] is False
    assert income_original(engine) == income_before
    with Session(engine) as session:
        goal = session.get(Goal, source_goal)
        assert goal is not None and goal.allocated_cents == goal_allocated - 10000 == 10000
        legs = list(
            session.scalars(
                select(SimulatedBankPosting).where(SimulatedBankPosting.operation_id == action_id)
            )
        )
        assert len(legs) == 3
        assert (
            len(
                list(
                    session.scalars(
                        select(ActionReceipt).where(ActionReceipt.action_plan_id == action_id)
                    )
                )
            )
            == 1
        )
        new_transactions = [
            row
            for row in session.scalars(
                select(Transaction).where(Transaction.user_id == DEMO_USER_ID)
            )
            if row.id not in transactions_before
        ]
        assert len(new_transactions) == 2 and all(
            row.category == "internal_transfer" for row in new_transactions
        )
    after = physical_snapshot(engine)
    repeated = client.post(f"{URL}/actions/{action_id}/execute", json=execute_body)
    assert repeated.status_code == 200 and repeated.json()["receipt_id"] == final["receipt_id"]
    replay = client.post(URL + "/prepare", json=body)
    assert (
        replay.status_code == 200
        and replay.json()["original_command"] == original["original_command"]
    )
    looked_up = client.get(
        f"{URL}/commands/{body['expected_epoch_id']}/by-key/{body['idempotency_key']}"
    )
    assert looked_up.status_code == 200 and looked_up.json()["original"]["action_id"] == str(
        action_id
    )
    read_receipt = client.get(f"{URL}/actions/{action_id}")
    assert read_receipt.status_code == 200, read_receipt.text
    assert read_receipt.json()["original_receipt"] == final["original_receipt"]
    assert (
        read_receipt.json()["action_confirmation_evidence_id"]
        == lost["action_confirmation_evidence_id"]
    )
    with _reader(engine) as session:
        audit = verify_audit_chain(
            session, DEMO_USER_ID, UUID(body["expected_epoch_id"]), mode="EXACT"
        )
        assert audit.status == audit.chain_status == audit.reference_status == "VALID", (
            audit.model_dump(mode="json")
        )
        assert audit.actual_count == audit.expected_count == audit.verified_through_sequence
        assert audit.errors == []
    assert physical_snapshot(engine) == after
