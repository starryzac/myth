"""Focused real-PG risks; root alone executes this isolated financial test chain."""

from uuid import uuid4

import pytest
from app.db.models import ActionPlan, BankOperation, EvidenceItem
from app.domain.policy_configuration import configuration_hash
from app.services.action_contracts import (
    ConfirmActionRequest,
    PrepareActionRequest,
    PurchaseIntent,
    TransferIntent,
)
from app.services.autonomy_envelope import (
    FullTemplateIntent,
    assess_envelope_action,
    assess_envelope_intent,
)
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF
from app.services.execution import confirm_action, execute_action, get_action, prepare_action
from app.services.policy_lifecycle import PolicyLifecycleError, suspend_policy
from app.services.scenario_runner import _fault
from app.tests.test_asset_allocation_service import authorization
from app.tests.test_autonomy_envelope import memberships
from app.tests.test_boundary_service import boundary_engine as boundary_engine
from app.tests.test_boundary_service import confirmed_policy, snapshot
from app.tests.test_execution_service import transfer_accounts
from app.tests.test_full_policy_lifecycle_integration import body, create, readonly
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_original_purchase_five_sets_are_actual_and_snapshot_only(boundary_engine: Engine) -> None:
    with Session(boundary_engine) as session, session.begin():
        policy_id, _ = confirmed_policy(session, authorization())
    before = snapshot(boundary_engine)
    with readonly(boundary_engine) as session:
        result = assess_envelope_intent(
            session,
            DEMO_USER_ID,
            PurchaseIntent(kind="purchase_asset", policy_id=policy_id),
            SEED_AS_OF,
        )
        assert result.audit.status == "VALID" and result.audit.complete
        assert set(memberships(result.assessment).values()) == {"IN"}
        assert result.assessment.intersection == "IN"
        assert result.assessment.automatic_execution_allowed
        assert result.effect is not None and result.amount_cents == result.effect.amount_cents
        assert result.sources and all(len(source.content_hash) == 64 for source in result.sources)
        assert result.authority_granted is False
        repeat = assess_envelope_intent(
            session,
            DEMO_USER_ID,
            PurchaseIntent(kind="purchase_asset", policy_id=policy_id),
            SEED_AS_OF,
        )
        assert repeat == result
    assert snapshot(boundary_engine) == before


def test_exact_transfer_confirmation_does_not_make_auto_or_reauthorize_unknown(
    boundary_engine: Engine,
) -> None:
    source, target = transfer_accounts(boundary_engine)
    intent = TransferIntent(
        kind="transfer_internal",
        source_account_id=source,
        destination_account_id=target,
        amount_cents=10000,
    )
    before = snapshot(boundary_engine)
    with readonly(boundary_engine) as session:
        result = assess_envelope_intent(session, DEMO_USER_ID, intent, SEED_AS_OF)
        assert memberships(result.assessment)["FinanciallySafeSet"] == "IN"
        assert memberships(result.assessment)["UserAuthorizedSet"] == "OUT"
        assert not result.assessment.automatic_execution_allowed
    assert snapshot(boundary_engine) == before
    prepared = prepare_action(
        boundary_engine,
        DEMO_USER_ID,
        PrepareActionRequest(idempotency_key="envelope-transfer", intent=intent),
        SEED_AS_OF,
    )
    confirm_action(
        boundary_engine,
        DEMO_USER_ID,
        prepared.action_id,
        ConfirmActionRequest(effect_hash=prepared.effect_hash, accepted=True),
        SEED_AS_OF,
    )
    before = snapshot(boundary_engine)
    with readonly(boundary_engine) as session:
        confirmed = assess_envelope_action(session, DEMO_USER_ID, prepared.action_id, SEED_AS_OF)
        assert confirmed.assessment.intersection == "IN"
        assert confirmed.assessment.execution_eligible
        assert not confirmed.assessment.automatic_execution_allowed
        assert confirmed.effect == prepared.effect
        with pytest.raises(PolicyLifecycleError) as foreign:
            assess_envelope_action(session, uuid4(), prepared.action_id, SEED_AS_OF)
        assert foreign.value.status_code == 404
    assert snapshot(boundary_engine) == before
    with (
        _fault("DROP_BANK_RESPONSE", "EXECUTE_ACTION", boundary_engine, DEMO_USER_ID),
        pytest.raises(TimeoutError, match="SIMULATED_BANK_RESPONSE_LOST"),
    ):
        execute_action(boundary_engine, DEMO_USER_ID, prepared.action_id, SEED_AS_OF)
    before = snapshot(boundary_engine)
    with readonly(boundary_engine) as session:
        unresolved = get_action(session, DEMO_USER_ID, prepared.action_id, SEED_AS_OF)
        assert unresolved.status in {"SUBMITTED", "UNKNOWN"}
        assert unresolved.bank_status == "SETTLED" and unresolved.receipt is None
        assert (
            unresolved.effect == prepared.effect and unresolved.effect_hash == prepared.effect_hash
        )
        operations = list(
            session.scalars(
                select(BankOperation).where(
                    BankOperation.user_id == DEMO_USER_ID,
                    BankOperation.action_plan_id == prepared.action_id,
                )
            )
        )
        assert len(operations) == 1
        action = session.get(ActionPlan, prepared.action_id)
        assert action is not None and action.user_id == DEMO_USER_ID
        assert action.request["intent"] == intent.model_dump(mode="json")
        assert configuration_hash(action.request) == action.request_hash
        assert operations[0].idempotency_key == action.idempotency_key
        assert operations[0].action_plan_id == action.id
        assert operations[0].request == action.request["execution"]
        assert configuration_hash(operations[0].request) == operations[0].request_hash
        assert operations[0].business_key == prepared.effect.business_key
        assert operations[0].request["effect_hash"] == prepared.effect_hash
        assert get_action(session, DEMO_USER_ID, prepared.action_id, SEED_AS_OF) == unresolved
        with pytest.raises(PolicyLifecycleError) as unknown:
            assess_envelope_action(session, DEMO_USER_ID, prepared.action_id, SEED_AS_OF)
    assert unknown.value.code == "NO_RECLASSIFICATION_AFTER_ACCEPTANCE"
    assert snapshot(boundary_engine) == before


def test_suspended_authority_and_corrupt_bank_proof_reject_without_fabricated_effect(
    boundary_engine: Engine,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        policy_id, version_id = confirmed_policy(session, authorization())
        suspend_policy(session, DEMO_USER_ID, policy_id, version_id, SEED_AS_OF)
    before = snapshot(boundary_engine)
    with readonly(boundary_engine) as session:
        result = assess_envelope_intent(
            session,
            DEMO_USER_ID,
            PurchaseIntent(kind="purchase_asset", policy_id=policy_id),
            SEED_AS_OF,
        )
        assert memberships(result.assessment)["UserAuthorizedSet"] == "OUT"
        assert result.amount_cents is None and result.effect is None
        assert memberships(result.assessment)["FinanciallySafeSet"] == "UNKNOWN"
    assert snapshot(boundary_engine) == before
    with Session(boundary_engine) as session, session.begin():
        row = session.scalars(
            select(EvidenceItem).where(EvidenceItem.source_type == "SIMULATED_BANK_BALANCE")
        ).first()
        assert row is not None
        row.content_hash = "f" * 64
    tampered = snapshot(boundary_engine)
    with readonly(boundary_engine) as session:
        result = assess_envelope_intent(
            session,
            DEMO_USER_ID,
            PurchaseIntent(kind="purchase_asset", policy_id=policy_id),
            SEED_AS_OF,
        )
        assert memberships(result.assessment)["EvidenceSufficientSet"] == "OUT"
        assert result.assessment.intersection == "OUT" and not result.assessment.execution_eligible
        assert result.amount_cents is None
    assert snapshot(boundary_engine) == tampered


def test_confirmed_full_planning_record_never_grants_unimplemented_bank_action(
    boundary_engine: Engine,
) -> None:
    declared = create(boundary_engine, body())
    before = snapshot(boundary_engine)
    with readonly(boundary_engine) as session:
        result = assess_envelope_intent(
            session,
            DEMO_USER_ID,
            FullTemplateIntent(
                kind="full_template",
                template_name="InterventionPolicy",
                policy_id=declared.policy_id,
            ),
            SEED_AS_OF,
        )
        sets = memberships(result.assessment)
        assert sets["SupportedActionSet"] == "OUT"
        assert all(
            state == "UNKNOWN" for name, state in sets.items() if name != "SupportedActionSet"
        )
        assert result.amount_cents is None and result.effect is None
        assert result.assessment.original_decision is None
        assert not result.assessment.execution_eligible
        assert not result.assessment.automatic_execution_allowed
        assert result.assessment.sets[0].policy_version_ids == [declared.version_id]
    assert snapshot(boundary_engine) == before
