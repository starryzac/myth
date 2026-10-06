"""One necessary real-PG original engine/source/consent/denominator/zero-write risk."""

from uuid import uuid4

import pytest
from app.api.dependencies import get_engine, get_now
from app.db.models import Account, EvidenceItem
from app.domain.finite_uncertainty import AccountChoice, FiniteChoice, FinitePlanningVariable
from app.main import create_app
from app.services.action_contracts import ConfirmActionRequest, PrepareActionRequest, TransferIntent
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF
from app.services.execution import confirm_action, prepare_action
from app.services.finite_uncertainty import FinitePlanningRequest, analyze_finite_planning
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_boundary_service import boundary_engine as boundary_engine
from app.tests.test_boundary_service import snapshot
from app.tests.test_execution_service import transfer_accounts
from app.tests.test_finite_uncertainty import amount_variable
from app.tests.test_full_policy_lifecycle_integration import readonly
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_actual_multivariable_worlds_reuse_original_engine_without_consent_or_writes(
    boundary_engine: Engine,
) -> None:
    source, target = transfer_accounts(boundary_engine)
    with Session(boundary_engine) as session:
        goal_account = session.scalar(
            select(Account.id).where(
                Account.user_id == DEMO_USER_ID, Account.account_type == "GOAL"
            )
        )
    assert goal_account is not None
    first = prepare_action(
        boundary_engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="finite-base",
            intent=TransferIntent(
                kind="transfer_internal",
                source_account_id=source,
                destination_account_id=target,
                amount_cents=100,
            ),
        ),
        SEED_AS_OF,
    )
    confirm_action(
        boundary_engine,
        DEMO_USER_ID,
        first.action_id,
        ConfirmActionRequest(effect_hash=first.effect_hash, accepted=True),
        SEED_AS_OF,
    )
    destination = FinitePlanningVariable(
        variable_id="destination",
        field="TRANSFER_DESTINATION",
        choices=[
            FiniteChoice(key="cash", value=AccountChoice(kind="account", account_id=target)),
            FiniteChoice(key="goal", value=AccountChoice(kind="account", account_id=goal_account)),
        ],
    )
    body = FinitePlanningRequest(
        base_action_id=first.action_id, variables=[amount_variable(), destination]
    )
    before = snapshot(boundary_engine)
    with readonly(boundary_engine) as session:
        result = analyze_finite_planning(session, DEMO_USER_ID, body, SEED_AS_OF)
        assert result.audit.complete and result.audit.status == "VALID"
        assert result.base_decision.confirmation_satisfied
        assert result.original_run_id and len(result.original_trace_hash) == 64
        assert result.sources and all(
            ref.bank_fact is False and ref.authorization is False for ref in result.declarations
        )
        assert (
            result.result.expected_world_count
            == result.result.evaluated_world_count
            == result.result.known_world_count
            == 4
        )
        assert result.result.complete_within_declared_domain and result.result.status == "DIVERGENT"
        assert result.result.question is not None and result.result.question.variable_id == "amount"
        assert {
            world.outcome.decision.level for world in result.result.worlds if world.outcome.decision
        } == {"ASK_ONCE", "BLOCKED"}
        assert all(
            world.outcome.decision is not None and not world.outcome.decision.confirmation_satisfied
            for world in result.result.worlds
        )
        assert all(
            world.outcome.effect is not None
            and world.outcome.effect.operation_id != first.action_id
            for world in result.result.worlds
        )
        assert analyze_finite_planning(session, DEMO_USER_ID, body, SEED_AS_OF) == result
        assert (
            result.result.execution_eligible is False and result.result.authority_granted is False
        )
        foreign = destination.model_copy(
            update={
                "choices": [
                    destination.choices[0],
                    FiniteChoice(
                        key="foreign", value=AccountChoice(kind="account", account_id=uuid4())
                    ),
                ]
            }
        )
        unknown = analyze_finite_planning(
            session,
            DEMO_USER_ID,
            FinitePlanningRequest(
                base_action_id=first.action_id, variables=[amount_variable(), foreign]
            ),
            SEED_AS_OF,
        )
        assert unknown.result.expected_world_count == unknown.result.evaluated_world_count == 4
        assert unknown.result.unknown_or_unsupported_world_count == 2
        assert unknown.result.status == "UNKNOWN" and unknown.result.should_ask is None
        bank = session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.user_id == DEMO_USER_ID,
                EvidenceItem.source_type == "SIMULATED_BANK_BALANCE",
            )
        ).first()
        assert bank is not None
        declared = amount_variable().model_copy(
            update={"source": "REGISTERED_EVIDENCE", "evidence_id": bank.id}
        )
        refused = analyze_finite_planning(
            session,
            DEMO_USER_ID,
            FinitePlanningRequest(base_action_id=first.action_id, variables=[declared]),
            SEED_AS_OF,
        )
        assert (
            refused.result.status == "UNKNOWN"
            and refused.result.unknown_or_unsupported_world_count == 2
        )
        assert refused.declarations[0].status == "MISSING_OR_INVALID"
        with pytest.raises(PolicyLifecycleError) as owner:
            analyze_finite_planning(session, uuid4(), body, SEED_AS_OF)
        assert owner.value.status_code == 404
    assert snapshot(boundary_engine) == before
    api = create_app()
    api.dependency_overrides[get_engine] = lambda: boundary_engine
    api.dependency_overrides[get_now] = lambda: SEED_AS_OF
    with TestClient(api) as client:
        actual = client.post("/api/v1/finite-planning/analyze", json=body.model_dump(mode="json"))
        assert actual.status_code == 200
        assert actual.json() == result.model_dump(mode="json")
        invalid = body.model_dump(mode="json")
        invalid["variables"][0]["choices"][0]["value"]["amount_cents"] = "100"
        assert client.post("/api/v1/finite-planning/analyze", json=invalid).status_code == 422
    assert snapshot(boundary_engine) == before
