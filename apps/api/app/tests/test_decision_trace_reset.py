"""Reset real simulated execution traces atomically inside a disposable database."""

from uuid import UUID, uuid4

import pytest
from app.db.models import Account, ActionPlan, DecisionRun, User
from app.domain.policy_configuration import configuration_hash
from app.services import demo_seed
from app.services.action_contracts import ConfirmActionRequest, PrepareActionRequest, TransferIntent
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, seed_demo
from app.services.execution import confirm_action, execute_action, prepare_action
from app.tests.test_demo_seed import database_snapshot
from app.tests.test_demo_seed import demo_engine as demo_engine
from app.tests.test_execution_service import transfer_accounts
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def completed_transfer(engine: Engine) -> UUID:
    source, target = transfer_accounts(engine)
    prepared = prepare_action(
        engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="trace-reset-transfer",
            intent=TransferIntent(
                kind="transfer_internal",
                source_account_id=source,
                destination_account_id=target,
                amount_cents=10000,
            ),
        ),
        SEED_AS_OF,
    )
    confirm_action(
        engine,
        DEMO_USER_ID,
        prepared.action_id,
        ConfirmActionRequest(effect_hash=prepared.effect_hash, accepted=True),
        SEED_AS_OF,
    )
    result = execute_action(engine, DEMO_USER_ID, prepared.action_id, SEED_AS_OF)
    assert result.status == "SUCCEEDED" and result.receipt is not None
    return prepared.action_id


def other_tenant_links(engine: Engine) -> tuple[UUID, UUID, UUID]:
    owner, root, action_id, child = [uuid4() for _ in range(4)]
    with Session(engine) as session, session.begin():
        session.add(User(id=owner, external_ref=str(owner), display_name="Other synthetic tenant"))
        session.flush()
        account = Account(
            user_id=owner, external_ref="other-goal", name="Synthetic", account_type="GOAL"
        )
        session.add(account)
        session.add(
            DecisionRun(
                id=root,
                user_id=owner,
                idempotency_key="root",
                trigger_type="TEST",
                algorithm_version="fixture",
                as_of=SEED_AS_OF,
                input_snapshot={},
                snapshot_hash=configuration_hash({}),
            )
        )
        session.flush()
        session.add(
            ActionPlan(
                id=action_id,
                user_id=owner,
                decision_run_id=root,
                source_account_id=account.id,
                action_type="TRANSFER_INTERNAL",
                amount_cents=1,
                idempotency_key="action",
                request={},
                request_hash=configuration_hash({}),
            )
        )
        session.flush()
        row = session.get(DecisionRun, root)
        assert row is not None
        row.subject_action_plan_id = action_id
        session.add(
            DecisionRun(
                id=child,
                user_id=owner,
                idempotency_key="child",
                trigger_type="TEST",
                algorithm_version="fixture",
                as_of=SEED_AS_OF,
                input_snapshot={},
                snapshot_hash=configuration_hash({}),
                parent_run_id=root,
                subject_action_plan_id=action_id,
            )
        )
    return root, action_id, child


def test_reset_after_actual_three_phase_execution_is_repeatable_and_preserves_other_tenant(
    demo_engine: Engine,
) -> None:
    initial = seed_demo(demo_engine)
    root_id, action_id, child_id = other_tenant_links(demo_engine)
    baseline = database_snapshot(demo_engine)
    completed_transfer(demo_engine)
    with Session(demo_engine) as session:
        assert (
            session.scalar(
                select(DecisionRun.id).where(
                    DecisionRun.user_id == DEMO_USER_ID,
                    DecisionRun.subject_action_plan_id.is_not(None),
                )
            )
            is not None
        )
    assert seed_demo(demo_engine) == initial
    assert database_snapshot(demo_engine) == baseline
    assert seed_demo(demo_engine) == initial
    assert database_snapshot(demo_engine) == baseline
    with Session(demo_engine) as session:
        root, child = session.get(DecisionRun, root_id), session.get(DecisionRun, child_id)
        assert root is not None and child is not None
        assert root.subject_action_plan_id == child.subject_action_plan_id == action_id
        assert child.parent_run_id == root_id


def test_failed_reset_restores_original_links_and_economic_history(
    demo_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed_demo(demo_engine)
    completed_transfer(demo_engine)
    before = database_snapshot(demo_engine)

    def injected_failure(session: Session) -> None:
        raise RuntimeError("Injected failure after deleting original trace links")

    monkeypatch.setattr(demo_seed, "_insert_facts", injected_failure)
    with pytest.raises(RuntimeError, match="Injected failure"):
        seed_demo(demo_engine)
    assert database_snapshot(demo_engine) == before
