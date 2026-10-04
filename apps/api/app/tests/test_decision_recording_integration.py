"""Actual recording seams through public simulated execution and assessment commands."""

from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from app.db.models import ActionPlan, DecisionRun, EvidenceItem, PolicyVersion
from app.domain.decision_trace import verify_trace
from app.domain.policy_configuration import configuration_hash
from app.services.action_contracts import (
    ConfirmActionRequest,
    GoalIntent,
    PaymentIntent,
    PrepareActionRequest,
    PurchaseIntent,
    RedeemIntent,
    TransferIntent,
)
from app.services.decision_assessment import SaveAssessmentRequest, save_assessment
from app.services.decision_trace import get_action_trace, get_decision_trace
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF
from app.services.execution import confirm_action, execute_action, prepare_action
from app.services.recovery import run_recovery
from app.tests.test_asset_allocation_service import authorization
from app.tests.test_boundary_service import (
    boundary_engine,
    confirmed_policy,
    imported_proof,
    snapshot,
)
from app.tests.test_execution_projection import zero_goal_income_setup
from app.tests.test_execution_service import transfer_accounts
from app.tests.test_recovery_service import mature_recovery_fixture, recovery_fixture
from sqlalchemy import delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

__all__ = ["boundary_engine"]
pytestmark = pytest.mark.integration


def intent_for(engine: Engine, kind: str) -> Any:
    if kind == "TRANSFER_INTERNAL":
        source, target = transfer_accounts(engine)
        return TransferIntent(
            kind="transfer_internal",
            source_account_id=source,
            destination_account_id=target,
            amount_cents=10000,
        )
    if kind == "ALLOCATE_GOAL":
        goal, _ = zero_goal_income_setup(engine)
        return GoalIntent(kind="allocate_goal", goal_id=goal)
    if kind == "REDEEM_ASSET":
        _, position, _ = recovery_fixture(engine)
        return RedeemIntent(kind="redeem_asset", position_id=position)
    with Session(engine) as session, session.begin():
        if kind == "PURCHASE_ASSET":
            policy, _ = confirmed_policy(session, authorization())
            return PurchaseIntent(kind="purchase_asset", policy_id=policy)
        policy, _ = confirmed_policy(
            session,
            {
                "type": "recurring_obligation",
                "payee_id": "synthetic-landlord-001",
                "due_day": 4,
                "auto_execute": True,
                "amount_rule": {"kind": "exact", "amount_cents": 180000},
            },
        )
        imported_proof(
            session,
            "SIMULATED_RECURRING_SETTLEMENT",
            {
                "protocol": "recurring-settlement-v1",
                "policy_id": str(policy),
                "period": "2026-10",
                "paid_cents": 10000,
                "payee_id": "synthetic-landlord-001",
                "complete": True,
                "as_of": SEED_AS_OF.isoformat(),
            },
        )
        return PaymentIntent(kind="pay_recurring", policy_id=policy, period="2026-10")


@pytest.mark.parametrize(
    "kind",
    ["TRANSFER_INTERNAL", "PAY_RECURRING", "ALLOCATE_GOAL", "PURCHASE_ASSET", "REDEEM_ASSET"],
)
def test_all_five_receipt_chains_preserve_frozen_real_preparation_and_fresh_phases(
    boundary_engine: Engine,
    kind: str,
) -> None:
    prepared = prepare_action(
        boundary_engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="trace-seam:" + kind, intent=intent_for(boundary_engine, kind)
        ),
        SEED_AS_OF,
    )
    with Session(boundary_engine) as session:
        original = get_action_trace(session, DEMO_USER_ID, prepared.action_id, SEED_AS_OF)
        assert original.trace is not None and original.completeness == "COMPLETE"
        trace = original.trace
        verify_trace(trace)
        assert trace.phase == "PREPARE"
        assert trace.inputs["effect"] == prepared.effect.model_dump(mode="json")
        assert trace.sources and trace.inputs["execution_context"]
        if kind != "TRANSFER_INTERNAL":
            assert prepared.effect.policy_version_id in {p.id for p in trace.policies}
        if kind == "PURCHASE_ASSET":
            planning = trace.inputs["planning"]["asset_planning"]
            assert planning["result"]["candidates"]
            assert [c.result for c in trace.candidates] == planning["result"]["candidates"]
            assert planning["result"]["selected_product_id"] == str(prepared.effect.product_id)
            assert all("maturity_rule" in p for p in planning["products"])
        if kind == "ALLOCATE_GOAL":
            planning = trace.inputs["planning"]["goal_planning"]
            assert planning["lots"] and planning["result"]["lot_allocations"]
            assert planning["result"]["suggested_cents"] == prepared.effect.amount_cents
            assert trace.candidates[0].result == planning["result"]
            assert "remaining_max_cents" in planning["result"]
    if prepared.autonomy_level == "ASK_ONCE":
        confirm_action(
            boundary_engine,
            DEMO_USER_ID,
            prepared.action_id,
            ConfirmActionRequest(effect_hash=prepared.effect_hash, accepted=True),
            SEED_AS_OF + timedelta(seconds=1),
        )
    settled = execute_action(
        boundary_engine, DEMO_USER_ID, prepared.action_id, SEED_AS_OF + timedelta(seconds=2)
    )
    assert settled.receipt is not None and settled.status == "SUCCEEDED"
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        historical = get_action_trace(
            session, DEMO_USER_ID, prepared.action_id, SEED_AS_OF + timedelta(days=1)
        )
        assert historical.trace == trace
        assert historical.actions[0].receipt_id == settled.receipt.receipt_id
        assert historical.actions[0].decision_run_id == trace.run_id
        children = [
            get_decision_trace(session, DEMO_USER_ID, i, SEED_AS_OF + timedelta(days=1))
            for i in historical.children
        ]
        phases = {child.trace.phase for child in children if child.trace}
        assert {"RESERVE", "BANK_ACCEPT"} <= phases
        if prepared.autonomy_level == "ASK_ONCE":
            assert "CONFIRM" in phases
        for child in children:
            assert child.trace is not None
            assert child.trace.parent_run_id == trace.run_id
            assert child.trace.outcome["autonomy_level"] == prepared.autonomy_level
            assert child.explanation is not None
            assert child.explanation.level == prepared.autonomy_level
        assert historical.explanation is not None
        assert historical.explanation.level == prepared.autonomy_level
    assert snapshot(boundary_engine) == before


def test_saved_bad_source_assessment_records_blocked_original_copy_without_creating_action(
    boundary_engine: Engine,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        policy, _ = confirmed_policy(session, authorization())
        source = session.scalars(
            select(EvidenceItem).where(EvidenceItem.source_type == "SIMULATED_BANK_BALANCE")
        ).first()
        assert source is not None
        source_id = source.id
        source.content_hash = "f" * 64
    saved = save_assessment(
        boundary_engine,
        DEMO_USER_ID,
        SaveAssessmentRequest(
            idempotency_key="saved-bad-source",
            intent=PurchaseIntent(kind="purchase_asset", policy_id=policy),
        ),
        SEED_AS_OF,
    )
    assert saved.trace is not None
    assert saved.trace.phase == "EVALUATION"
    assert saved.trace.outcome["decision"]["level"] == "BLOCKED"
    bad = next(s for s in saved.trace.sources if s.id == source_id)
    assert bad.content_integrity == "INVALID" and bad.content_hash == "f" * 64
    assert saved.trace.inputs["autonomy_facts"]["source_issues"]
    with Session(boundary_engine) as session:
        assert not list(session.scalars(select(ActionPlan)))


def test_finite_amount_assessment_saves_each_world_context_without_single_execution_payload(
    boundary_engine: Engine,
) -> None:
    source, target = transfer_accounts(boundary_engine)
    saved = save_assessment(
        boundary_engine,
        DEMO_USER_ID,
        SaveAssessmentRequest(
            idempotency_key="saved-amount-worlds",
            intent=TransferIntent(
                kind="transfer_internal",
                source_account_id=source,
                destination_account_id=target,
                amount_cents=10000,
            ),
            amount_options_cents=[10000, 20000],
        ),
        SEED_AS_OF,
    )
    assert saved.trace is not None
    trace = saved.trace
    assert trace.phase == "EVALUATION"
    assert trace.outcome["decision"]["uncertainty_status"] == "DIVERGENT"
    assert trace.outcome["effect"] is None
    assert trace.outcome["decision"]["effect_hash"] is None
    worlds = trace.inputs["uncertainty"]["worlds"]
    assert len(worlds) == 2
    assert {w["facts"]["effect"]["amount_cents"] for w in worlds} == {10000, 20000}
    assert len({w["facts"]["source_context_hash"] for w in worlds}) == 1
    assert set(trace.inputs["world_contexts"]) == {"10000", "20000"}
    assert len(trace.candidates) == 2
    for candidate in trace.candidates:
        assert candidate.inputs["effect"]["amount_cents"] == int(candidate.candidate_key)
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        read = get_decision_trace(session, DEMO_USER_ID, saved.run_id, SEED_AS_OF)
        assert read.trace == trace
        assert not list(session.scalars(select(ActionPlan)))
    assert snapshot(boundary_engine) == before


def test_mature_contract_after_revoke_preserves_original_terms_and_no_new_authority(
    boundary_engine: Engine,
) -> None:
    _, position_id, _ = mature_recovery_fixture(boundary_engine)
    result = run_recovery(boundary_engine, DEMO_USER_ID, "saved-maturity", SEED_AS_OF)
    assert result.status == "RECOVERED" and len(result.actions) == 1
    action_id = result.actions[0].action_id
    assert result.actions[0].receipt_id is not None
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        trace = get_action_trace(session, DEMO_USER_ID, action_id, SEED_AS_OF)
        assert trace.trace is not None and trace.trace.phase == "RECOVERY_PLAN"
        children = [
            get_decision_trace(session, DEMO_USER_ID, key, SEED_AS_OF) for key in trace.children
        ]
        contract = next(
            c.trace for c in children if c.trace and c.trace.phase == "CONTRACT_SETTLEMENT"
        )
        assert contract is not None
        assert contract.inputs["original_contract"]["position_id"] == str(position_id)
        assert contract.inputs["original_contract"]["product"]["maturity_rule"]
        assert contract.outcome["new_authority"] is False
        assert contract.outcome["settlement_kind"] == "ORIGINAL_CONTRACT"
        assert any(p.status_at_decision == "REVOKED" for p in contract.policies)
        assert trace.actions[0].receipt_id == result.actions[0].receipt_id
    assert snapshot(boundary_engine) == before


def test_legacy_t1_trace_read_keeps_actual_risk_and_future_principal_unsettled(
    boundary_engine: Engine,
) -> None:
    recovery_fixture(boundary_engine, delay=1)
    result = run_recovery(boundary_engine, DEMO_USER_ID, "saved-t1-wait", SEED_AS_OF)
    assert result.actions[0].receipt_id is None
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        run = session.scalars(
            select(DecisionRun).where(DecisionRun.trigger_type == "SAFETY_RECOVERY")
        ).one()
        read = get_decision_trace(session, DEMO_USER_ID, run.id, SEED_AS_OF + timedelta(days=1))
        assert read.trace is not None
        plan = read.trace.outcome["recovery"]
        assert plan["actual_boundary"]["status"] == "LIQUIDITY_RISK"
        assert plan["steps"][0]["quote"]["principal_available_at"] > SEED_AS_OF.isoformat()
        assert read.actions[0].receipt_id is None
        assert read.actions[0].bank_status == "ACCEPTED"
    assert snapshot(boundary_engine) == before


def test_missing_policy_evidence_is_saved_as_blocked_with_explicit_missing_reference(
    boundary_engine: Engine,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        policy, version_id = confirmed_policy(session, authorization())
        version = session.get(PolicyVersion, version_id)
        assert version is not None
        missing_id = UUID(version.evidence_ids[0])
        source = session.get(EvidenceItem, missing_id)
        assert source is not None and source.source_type == "TEST_USER_INTENT"
        # This JSON reference has no FK; retain every immutable policy byte and use
        # ordinary SQL deletion in the disposable test database, with constraints on.
        session.execute(delete(EvidenceItem).where(EvidenceItem.id == missing_id))
    saved = save_assessment(
        boundary_engine,
        DEMO_USER_ID,
        SaveAssessmentRequest(
            idempotency_key="saved-missing-policy-proof",
            intent=PurchaseIntent(kind="purchase_asset", policy_id=policy),
        ),
        SEED_AS_OF,
    )
    assert saved.trace is not None
    assert saved.trace.outcome["decision"]["level"] == "BLOCKED"
    assert saved.trace.inputs["autonomy_facts"]["authority"]["status"] == "MISSING_EVIDENCE"
    assert str(missing_id) in saved.trace.inputs["missing_evidence_references"]
    assert missing_id not in {source.id for source in saved.trace.sources}
    assert any(p.id == version_id for p in saved.trace.policies)
    with Session(boundary_engine) as session:
        assert not list(session.scalars(select(ActionPlan)))


def test_saved_bad_source_owner_and_boolean_money_remain_raw_rejected_claims(
    boundary_engine: Engine,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        policy, _ = confirmed_policy(session, authorization())
        row = session.scalars(
            select(EvidenceItem).where(EvidenceItem.source_type == "SIMULATED_BANK_BALANCE")
        ).first()
        assert row is not None
        identifier = row.id
        false_owner = str(UUID(int=99))
        content = {**row.content, "user_id": false_owner, "balance_cents": True}
        row.content = content
        row.content_hash = configuration_hash(content)
    saved = save_assessment(
        boundary_engine,
        DEMO_USER_ID,
        SaveAssessmentRequest(
            idempotency_key="saved-bad-owner-money",
            intent=PurchaseIntent(kind="purchase_asset", policy_id=policy),
        ),
        SEED_AS_OF,
    )
    assert saved.trace is not None
    assert saved.trace.outcome["decision"]["level"] == "BLOCKED"
    copy = next(s for s in saved.trace.sources if s.id == identifier)
    assert copy.user_id == DEMO_USER_ID
    assert copy.content["user_id"] == false_owner and copy.content["balance_cents"] is True
    assert copy.content_integrity == "VERIFIED"  # Hash equality is not a validity verdict.
    assert saved.trace.inputs["autonomy_facts"]["source_issues"]
