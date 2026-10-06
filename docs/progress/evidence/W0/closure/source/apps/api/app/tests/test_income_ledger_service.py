"""PostgreSQL compatibility at the public income-ledger service seam."""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from app.db.models import (
    ActionPlan,
    BankOperation,
    DecisionRun,
    EvidenceItem,
    Goal,
    SimulatedBankPosting,
)
from app.domain.execution import execution_effect_hash
from app.domain.execution_types import BankCommand, CashUse, ExecutionEffect
from app.domain.income_ledger import IncomeUse, location_id
from app.domain.policy_configuration import configuration_hash
from app.services.demo_seed import DEMO_USER_ID
from app.services.execution_bank import open_execution_anchors
from app.services.income_ledger import (
    commit_income_for_action,
    income_lots_for_action,
    read_income_ledger,
    read_income_state,
    release_income_for_action,
    reserve_income_for_action,
)
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.recovery_projection import replace_proof
from app.tests.test_boundary_service import boundary_engine as boundary_engine
from app.tests.test_boundary_service import goal_fixture, snapshot
from app.tests.test_goal_allocation_service import NOW, income_ledger
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("economic_change", [False, True])
def test_pure_epoch_successor_keeps_prepared_income_identity(
    boundary_engine: Engine, economic_change: bool
) -> None:
    with Session(boundary_engine) as session, session.begin():
        goal_id = goal_fixture(session, with_proofs=True)
        origin_id, proof_id = income_ledger(session)
        initial = read_income_ledger(session, DEMO_USER_ID, NOW)
        open_execution_anchors(session, DEMO_USER_ID, NOW, income_ledger=initial)
        fragment = next(f for f in initial.fragments if f.origin_transaction_id == origin_id)
        use = IncomeUse(
            fragment_id=fragment.fragment_id,
            origin_transaction_id=origin_id,
            account_id=fragment.account_id,
            amount_cents=20000,
        )
        action = action_fixture(session, goal_id, use)
        immutable_request = dict(action.request)
        old = session.get(EvidenceItem, proof_id)
        assert old is not None
        later = NOW + timedelta(seconds=1)
        content = {**old.content, "as_of": later.isoformat()}
        if economic_change:
            content["lots"] = [dict(lot) for lot in content["lots"]]
            changed = next(
                lot for lot in content["lots"] if lot["transaction_id"] == str(origin_id)
            )
            changed["spent_cents"] += 1
            changed["available_cents"] -= 1
            changed["prior_unspent_cents"] -= 1
        replace_proof(session, old, content, later, uuid4())
        if economic_change:
            with pytest.raises(PolicyLifecycleError, match="Prepared income evidence changed"):
                reserve_income_for_action(
                    session, DEMO_USER_ID, action.id, [use], "ALLOCATE_GOAL", later
                )
            assert action.request == immutable_request
            return
        reserved = reserve_income_for_action(
            session, DEMO_USER_ID, action.id, [use], "ALLOCATE_GOAL", later
        )
        assert (
            next(
                f for f in reserved.fragments if f.fragment_id == fragment.fragment_id
            ).available_cents
            == 120000
        )
        assert action.request == immutable_request


def test_v1_import_preserves_immutable_origin_and_unknown_legacy_reservation(
    boundary_engine: Engine,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        goal_fixture(session, with_proofs=True)
        origin_id, _ = income_ledger(session)
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        ledger = read_income_ledger(session, DEMO_USER_ID, NOW)
    origin = next(item for item in ledger.origins if item.origin_transaction_id == origin_id)
    fragment = next(item for item in ledger.fragments if item.origin_transaction_id == origin_id)
    assert origin.amount_cents == 200000
    assert fragment.fragment_id == location_id(origin_id, origin.origin_account_id)
    assert (
        fragment.spent_cents,
        fragment.assigned_cents,
        fragment.reserved_cents,
        fragment.legacy_reserved_cents,
        fragment.available_cents,
    ) == (20000, 10000, 30000, 30000, 140000)
    assert ledger.reservations == ()
    assert snapshot(boundary_engine) == before


def action_fixture(session: Session, goal_id: UUID, use: IncomeUse) -> ActionPlan:
    goal = session.get(Goal, goal_id)
    assert goal is not None
    state = read_income_state(session, DEMO_USER_ID, NOW)
    identity = uuid4()
    effect = ExecutionEffect(
        operation_id=identity,
        user_id=DEMO_USER_ID,
        business_key=f"income-test:{identity}",
        action_type="ALLOCATE_GOAL",
        amount_cents=use.amount_cents,
        cash_uses=[CashUse(account_id=use.account_id, amount_cents=use.amount_cents)],
        income_uses=[use],
        destination_account_id=goal.account_id,
        goal_id=goal.id,
        policy_id=goal.policy_id,
        policy_version_id=goal.policy_version_id,
        policy_version_ids=[goal.policy_version_id],
        valid_from=NOW,
        expires_at=NOW + timedelta(hours=1),
    )
    command = BankCommand(effect=effect, effect_hash=execution_effect_hash(effect))
    request = {
        "execution": command.model_dump(mode="json"),
        "income_evidence": {"id": str(state.evidence_id), "hash": state.evidence_hash},
    }
    run = DecisionRun(
        id=uuid4(),
        user_id=DEMO_USER_ID,
        idempotency_key=str(uuid4()),
        trigger_type="INCOME_TEST",
        algorithm_version="literal",
        as_of=NOW,
        input_snapshot={},
        snapshot_hash=configuration_hash({}),
    )
    session.add(run)
    session.flush()
    action = ActionPlan(
        id=identity,
        user_id=DEMO_USER_ID,
        decision_run_id=run.id,
        policy_version_id=goal.policy_version_id,
        source_account_id=use.account_id,
        destination_account_id=goal.account_id if goal.account_id != use.account_id else None,
        goal_id=goal.id,
        action_type="ALLOCATE_GOAL",
        amount_cents=use.amount_cents,
        autonomy_level="AUTO_EXECUTE",
        status="AUTHORIZED",
        idempotency_key=str(identity),
        request=request,
        request_hash=configuration_hash(request),
        authorized_at=NOW,
        expires_at=effect.expires_at,
    )
    session.add(action)
    session.flush()
    return action


def test_reservation_is_idempotent_and_versions_v1_without_erasing_legacy(
    boundary_engine: Engine,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        goal_id = goal_fixture(session, with_proofs=True)
        origin_id, old_proof_id = income_ledger(session)
        initial = read_income_ledger(session, DEMO_USER_ID, NOW)
        open_execution_anchors(session, DEMO_USER_ID, NOW, income_ledger=initial)
        original_fragment = next(
            item for item in initial.fragments if item.origin_transaction_id == origin_id
        )
        use = IncomeUse(
            fragment_id=original_fragment.fragment_id,
            origin_transaction_id=origin_id,
            account_id=original_fragment.account_id,
            amount_cents=20000,
        )
        action = action_fixture(session, goal_id, use)
        old = session.get(EvidenceItem, old_proof_id)
        assert old is not None
        old_content, old_hash = dict(old.content), old.content_hash
        reserved = reserve_income_for_action(
            session, DEMO_USER_ID, action.id, [use], "ALLOCATE_GOAL", NOW
        )
        fragment = next(
            item for item in reserved.fragments if item.origin_transaction_id == origin_id
        )
        assert (
            fragment.available_cents,
            fragment.reserved_cents,
            fragment.legacy_reserved_cents,
        ) == (120000, 50000, 30000)
        assert (
            old.status == "SUPERSEDED"
            and old.content == old_content
            and old.content_hash == old_hash
        )
        assert (
            reserve_income_for_action(session, DEMO_USER_ID, action.id, [use], "ALLOCATE_GOAL", NOW)
            == reserved
        )
        assert read_income_ledger(session, DEMO_USER_ID, NOW) == reserved


def reserved_fixture(session: Session) -> tuple[UUID, IncomeUse]:
    goal_id = goal_fixture(session, with_proofs=True)
    origin_id, _ = income_ledger(session)
    initial = read_income_ledger(session, DEMO_USER_ID, NOW)
    open_execution_anchors(session, DEMO_USER_ID, NOW, income_ledger=initial)
    fragment = next(item for item in initial.fragments if item.origin_transaction_id == origin_id)
    use = IncomeUse(
        fragment_id=fragment.fragment_id,
        origin_transaction_id=origin_id,
        account_id=fragment.account_id,
        amount_cents=20000,
    )
    action = action_fixture(session, goal_id, use)
    reserve_income_for_action(session, DEMO_USER_ID, action.id, [use], "ALLOCATE_GOAL", NOW)
    return action.id, use


def recorded_bank_result(
    session: Session, action_id: UUID, status: str, *, changed: bool = False
) -> BankOperation:
    """An explicit source-ledger fixture, not proof of the whole cash execution adapter."""
    action = session.get(ActionPlan, action_id)
    assert action is not None
    command = action.request["execution"]
    if changed:
        command = {**command, "different_economic_effect": True}
    bank = BankOperation(
        id=uuid4(),
        user_id=DEMO_USER_ID,
        action_plan_id=action_id,
        operation_type="ALLOCATE_GOAL",
        business_key=command["effect"]["business_key"],
        idempotency_key=action.idempotency_key,
        request=command,
        request_hash=configuration_hash(command),
        requested_at=NOW,
        available_at=NOW,
        settled_at=NOW if status == "SETTLED" else None,
        status=status,
    )
    session.add(bank)
    session.flush()
    return bank


def test_settled_bank_locations_consume_exact_reservation_once(boundary_engine: Engine) -> None:
    with Session(boundary_engine) as session, session.begin():
        action_id, use = reserved_fixture(session)
        bank = recorded_bank_result(session, action_id, "SETTLED")
        for bucket, change in (("AVAILABLE", -20000), ("ASSIGNED", 20000)):
            opening = session.scalar(
                select(SimulatedBankPosting).where(
                    SimulatedBankPosting.user_id == DEMO_USER_ID,
                    SimulatedBankPosting.ledger_key == f"LOT_{bucket}:{use.fragment_id}",
                )
            )
            assert opening is not None
            session.add(
                SimulatedBankPosting(
                    id=uuid4(),
                    user_id=DEMO_USER_ID,
                    ledger_key=opening.ledger_key,
                    ledger_dimension="INCOME_LOCATION",
                    ledger_metadata=opening.ledger_metadata,
                    account_id=use.account_id,
                    operation_id=bank.id,
                    leg_ref=bucket,
                    previous_posting_id=opening.id,
                    sequence_number=2,
                    entry_kind="OPERATION",
                    balance_before_cents=opening.balance_after_cents,
                    delta_cents=change,
                    balance_after_cents=opening.balance_after_cents + change,
                    occurred_at=NOW,
                )
            )
        session.flush()
        with pytest.raises(PolicyLifecycleError):
            read_income_ledger(session, DEMO_USER_ID, NOW)
        committed = commit_income_for_action(session, DEMO_USER_ID, action_id, NOW)
        fragment = next(item for item in committed.fragments if item.fragment_id == use.fragment_id)
        assert (fragment.available_cents, fragment.assigned_cents, fragment.reserved_cents) == (
            120000,
            30000,
            30000,
        )
        assert commit_income_for_action(session, DEMO_USER_ID, action_id, NOW) == committed
        assert read_income_ledger(session, DEMO_USER_ID, NOW) == committed


@pytest.mark.parametrize("status", ["ACCEPTED", "UNKNOWN", "REJECTED"])
def test_unsettled_bank_results_cannot_consume_and_only_rejection_releases(
    boundary_engine: Engine,
    status: str,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        action_id, use = reserved_fixture(session)
        recorded_bank_result(session, action_id, status)
        with pytest.raises(PolicyLifecycleError):
            commit_income_for_action(session, DEMO_USER_ID, action_id, NOW)
        if status != "REJECTED":
            with pytest.raises(PolicyLifecycleError):
                release_income_for_action(
                    session, DEMO_USER_ID, action_id, NOW, confirmed_no_effect=True
                )
        else:
            released = release_income_for_action(
                session, DEMO_USER_ID, action_id, NOW, confirmed_no_effect=True
            )
            fragment = next(
                item for item in released.fragments if item.fragment_id == use.fragment_id
            )
            assert fragment.available_cents == 140000
            assert fragment.reserved_cents == fragment.legacy_reserved_cents == 30000


def test_rehashed_v2_location_forgery_is_rejected_by_independent_bank(
    boundary_engine: Engine,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        action_id, use = reserved_fixture(session)
        state = read_income_state(session, DEMO_USER_ID, NOW)
        proof = session.get(EvidenceItem, state.evidence_id)
        assert proof is not None
        content = state.ledger.model_dump(mode="json")
        fragment = next(
            item for item in content["fragments"] if item["fragment_id"] == str(use.fragment_id)
        )
        fragment["available_cents"] += 1
        fragment["spent_cents"] -= 1
        proof.content = content
        proof.content_hash = configuration_hash(content)
        with pytest.raises(PolicyLifecycleError):
            read_income_ledger(session, DEMO_USER_ID, NOW)
        with pytest.raises(PolicyLifecycleError):
            reserve_income_for_action(session, DEMO_USER_ID, action_id, [use], "ALLOCATE_GOAL", NOW)


def test_rejection_of_a_different_economic_request_cannot_release_this_income(
    boundary_engine: Engine,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        action_id, _ = reserved_fixture(session)
        recorded_bank_result(session, action_id, "REJECTED", changed=True)
        with pytest.raises(PolicyLifecycleError):
            release_income_for_action(
                session, DEMO_USER_ID, action_id, NOW, confirmed_no_effect=True
            )


def test_action_revalidation_sees_only_its_own_claim_without_releasing_it(
    boundary_engine: Engine,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        action_id, use = reserved_fixture(session)
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        ordinary = income_lots_for_action(session, DEMO_USER_ID, NOW)
        own = income_lots_for_action(session, DEMO_USER_ID, NOW, action_id=action_id)
        assert (
            next(item for item in ordinary if item.fragment_id == use.fragment_id).available_cents
            == 120000
        )
        assert (
            next(item for item in own if item.fragment_id == use.fragment_id).available_cents
            == 140000
        )
        unchanged = read_income_ledger(session, DEMO_USER_ID, NOW)
        fragment = next(item for item in unchanged.fragments if item.fragment_id == use.fragment_id)
        assert fragment.reserved_cents == 50000 and fragment.legacy_reserved_cents == 30000
    assert snapshot(boundary_engine) == before
