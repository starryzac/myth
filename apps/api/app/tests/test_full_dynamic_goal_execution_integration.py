"""Root-only disposable PG candidate; no execution claimed before its actual run."""

import json
from uuid import UUID

import pytest
from app.db.models import Account, ActionPlan, BankOperation, EvidenceItem
from app.domain.demo_identity import DEMO_USER_ID
from app.domain.execution_types import (
    ConfirmationGrant,
    ExecutionContext,
    ExecutionEffect,
    ExecutionValidation,
)
from app.domain.external_bank_fact_types import ExternalFactRequest
from app.domain.full_dynamic_goal_execution import (
    MARKER,
    FullDynamicGoalProof,
    dynamic_goal_bank_key,
    native_income_original_matches,
)
from app.services import execution as execution_service
from app.services.audit_chain import verify_audit_chain
from app.services.execution import execute_action, get_action
from app.services.external_bank_facts import ingest_external_fact
from app.services.income_ledger import read_income_state
from app.services.policy_lifecycle import revoke_policy
from app.services.scenario_runner import _fault
from app.tests.test_full_dynamic_goal_reserve_api import full_dynamic_goal
from app.tests.test_full_projection_api import physical_snapshot
from app.tests.test_goal_api import NOW
from app.tests.test_goal_api import goal_client as goal_client
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_actual_dynamic_above_nominal_original_income_and_response_loss_key_recovery(
    goal_client: tuple[TestClient, Engine],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_validation = execution_service.revalidate_execution

    def diagnostic_validation(
        effect: ExecutionEffect,
        context: ExecutionContext,
        *,
        confirmation: ConfirmationGrant | None = None,
        full_dynamic_goal_proof: FullDynamicGoalProof | None = None,
    ) -> ExecutionValidation:
        if full_dynamic_goal_proof is not None:
            expected = full_dynamic_goal_proof.inputs.context.model_dump(mode="json")
            actual = context.model_dump(mode="json")
            differences = {
                key: {"producer": expected.get(key), "consumer": actual.get(key)}
                for key in expected.keys() | actual.keys()
                if expected.get(key) != actual.get(key)
            }
            print("FULL_DYNAMIC_CONTEXT_DIFFERENCE=" + json.dumps(differences, ensure_ascii=False))
        return original_validation(
            effect,
            context,
            confirmation=confirmation,
            full_dynamic_goal_proof=full_dynamic_goal_proof,
        )

    monkeypatch.setattr(execution_service, "revalidate_execution", diagnostic_validation)

    client, engine = goal_client
    goal_id, model_id = full_dynamic_goal(client, engine)
    with Session(engine) as session:
        cash = session.scalar(
            select(Account)
            .where(Account.user_id == DEMO_USER_ID, Account.account_type == "CASH")
            .order_by(Account.id)
        )
        assert cash is not None
        cash_id = cash.id
    income = ingest_external_fact(
        engine,
        DEMO_USER_ID,
        ExternalFactRequest(
            user_id=DEMO_USER_ID,
            account_id=cash_id,
            kind="INCOME",
            amount_cents=600000,
            external_ref="actual-dynamic-consumer-payroll",
            idempotency_key="actual-dynamic-consumer-payroll",
            counterparty_ref="payroll",
            occurred_at=NOW,
        ),
        NOW,
    )
    assert income.bank_status == "SETTLED" and income.projection_status == "PROJECTED"
    assert income.transaction_id is not None
    current = client.get(f"/api/v1/goals/{goal_id}/full-model")
    assert current.status_code == 200 and current.json()["status"] == "VERIFIED"
    model = current.json()
    assert model["evidence_id"] == model_id and model["bank_authority"] is False
    body = {
        "goal_id": goal_id,
        "expected_policy_version_id": model["base_policy_version_id"],
        "expected_model_evidence_id": model_id,
        "expected_model_evidence_hash": model["evidence_hash"],
        "expected_epoch_id": model["epoch_id"],
        "idempotency_key": "actual-dynamic-consumer-key",
    }
    with Session(engine) as session:
        actual_income = read_income_state(session, DEMO_USER_ID, NOW)
        retained_income = session.get(EvidenceItem, actual_income.evidence_id)
        assert retained_income is not None
        assert native_income_original_matches(retained_income.content, actual_income.ledger), (
            json.dumps(
                {
                    "original": retained_income.content,
                    "typed": actual_income.ledger.model_dump(mode="json"),
                },
                ensure_ascii=False,
            )
        )
    before = physical_snapshot(engine)
    preview = client.post("/api/v1/dynamic-goal-actions/preview", json=body)
    assert preview.status_code == 200, preview.text
    proof = preview.json()["proof"]
    assert proof["status"] == "VERIFIED_RANGE" and proof["dynamic_cap_cents"] == 20000
    assert proof["nominal_remaining_target_cents"] == 10000
    assert proof["remaining_max_cents"] == 20000 and proof["bank_authority"] is False
    for field in ("amount_cents", "income_uses", "now", "user_id", "authority"):
        assert (
            client.post("/api/v1/dynamic-goal-actions/preview", json={**body, field: 1}).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/dynamic-goal-actions/preview", json=body, params={field: "fake"}
            ).status_code
            == 422
        )
    assert physical_snapshot(engine) == before
    prepared = client.post("/api/v1/dynamic-goal-actions/prepare", json=body)
    assert prepared.status_code == 200, prepared.text
    action = prepared.json()
    assert action["effect"]["amount_cents"] == 20000
    assert action["effect"]["goal_id"] == goal_id
    assert action["effect"]["policy_version_id"] == model["base_policy_version_id"]
    assert sum(row["amount_cents"] for row in action["effect"]["income_uses"]) == 20000
    assert all(
        row["origin_transaction_id"] == str(income.transaction_id)
        for row in action["effect"]["income_uses"]
    )
    original_id = UUID(action["action_id"])
    after_prepare = physical_snapshot(engine)
    assert client.post("/api/v1/dynamic-goal-actions/prepare", json=body).json() == action
    changed = client.post(
        "/api/v1/dynamic-goal-actions/prepare",
        json={**body, "expected_model_evidence_hash": "b" * 64},
    )
    assert changed.status_code == 409
    assert physical_snapshot(engine) == after_prepare
    lookup = client.get(f"/api/v1/dynamic-goal-actions/by-key/{body['idempotency_key']}")
    assert lookup.status_code == 200, lookup.text
    assert lookup.json()["original_request"] == body
    assert lookup.json()["action"] == action
    assert lookup.json()["not_found_is_final"] is False
    assert lookup.json()["current_authority"] is False
    assert physical_snapshot(engine) == after_prepare
    if action["autonomy_level"] == "ASK_ONCE":
        consent = client.post(
            f"/api/v1/actions/{original_id}/confirm",
            json={
                "effect_hash": action["effect_hash"],
                "accepted": True,
            },
        )
        assert consent.status_code == 200, consent.text
    else:
        assert action["autonomy_level"] == "AUTO_EXECUTE"
    after_confirmation = physical_snapshot(engine)
    confirmation_lookup = client.get(
        f"/api/v1/dynamic-goal-actions/by-key/{body['idempotency_key']}"
    )
    assert confirmation_lookup.status_code == 200, confirmation_lookup.text
    if action["autonomy_level"] == "ASK_ONCE":
        actual_confirmation = confirmation_lookup.json()["confirmation"]
        assert actual_confirmation["effect_hash"] == action["effect_hash"]
        assert actual_confirmation["operation_id"] == str(original_id)
        assert confirmation_lookup.json()["confirmation_status"] == "VERIFIED_AT_CONFIRMATION"
    assert confirmation_lookup.json()["confirmation_is_current_authority"] is False
    assert physical_snapshot(engine) == after_confirmation
    with (
        _fault("DROP_BANK_RESPONSE", "EXECUTE_ACTION", engine, DEMO_USER_ID),
        pytest.raises(TimeoutError, match="SIMULATED_BANK_RESPONSE_LOST"),
    ):
        execute_action(engine, DEMO_USER_ID, original_id, NOW)
    with Session(engine) as session:
        unresolved = get_action(session, DEMO_USER_ID, original_id, NOW)
        assert unresolved.status in {"SUBMITTED", "UNKNOWN"}
        assert unresolved.bank_status == "SETTLED" and unresolved.receipt is None
        row = session.get(ActionPlan, original_id)
        assert row is not None and row.request[MARKER]["request"] == body
        bank_rows = list(
            session.scalars(
                select(BankOperation).where(BankOperation.action_plan_id == original_id)
            )
        )
        assert len(bank_rows) == 1 and bank_rows[0].request == row.request["execution"]
        assert (
            row.idempotency_key
            == bank_rows[0].idempotency_key
            == dynamic_goal_bank_key(body["idempotency_key"])
        )
    with Session(engine) as session, session.begin():
        revoke_policy(
            session,
            DEMO_USER_ID,
            UUID(action["effect"]["policy_id"]),
            UUID(body["expected_policy_version_id"]),
            NOW,
        )
    recovered = execute_action(engine, DEMO_USER_ID, original_id, NOW)
    assert recovered.status == "SUCCEEDED" and recovered.effect_hash == action["effect_hash"]
    assert recovered.receipt is not None and recovered.receipt.executed_cents == 20000
    final = physical_snapshot(engine)
    with Session(engine) as session:
        state = read_income_state(session, DEMO_USER_ID, NOW)
        fragments = [
            row
            for row in state.ledger.fragments
            if row.origin_transaction_id == income.transaction_id
        ]
        assert sum(row.assigned_cents for row in fragments) == 20000
        assert sum(row.available_cents for row in fragments) == 580000
        audit = verify_audit_chain(session, DEMO_USER_ID, mode="EXACT")
        assert audit.status == "VALID"
    assert execute_action(engine, DEMO_USER_ID, original_id, NOW) == recovered
    assert physical_snapshot(engine) == final
