"""Policy consent and replacement against real PostgreSQL transactions."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Barrier
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.db.models import (
    Account,
    ActionPlan,
    ActionReceipt,
    AssetPosition,
    AssetProduct,
    DecisionConstraint,
    DecisionRun,
    EvidenceItem,
    Goal,
    Policy,
    PolicyProposal,
    PolicyVersion,
    User,
)
from app.db.session import create_database_engine
from app.db.testing import temporary_database
from app.domain.policy_configuration import configuration_hash
from app.services.policy_lifecycle import (
    PolicyLifecycleError,
    change_policy,
    confirm_proposal,
    effective_status,
    is_version_authorized,
    refresh_time_states,
    revoke_policy,
    suspend_policy,
)
from sqlalchemy import select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[4]
NOW = datetime(2026, 10, 4, 4, tzinfo=UTC)
pytestmark = pytest.mark.integration


def configuration() -> dict[str, Any]:
    return {
        "type": "recurring_obligation",
        "name": "房租",
        "payee_id": "synthetic-landlord",
        "amount_rule": {"kind": "range", "min_cents": 175000, "max_cents": 185000},
        "due_day": 28,
        "prepare_days_before": 3,
        "valid_from": "2026-10-01",
        "valid_until": "2027-06-30",
        "auto_execute": True,
        "priority": {
            "importance": 100,
            "minimum_cents": 175000,
            "reducible": False,
            "deferrable": False,
        },
    }


def reviewed(configuration: dict[str, Any]) -> str:
    from app.domain.policy_configuration import configuration_hash, validate_configuration

    return configuration_hash(validate_configuration(configuration))


@pytest.fixture
def policy_engine() -> Iterator[tuple[Engine, UUID, UUID]]:
    with temporary_database() as url:
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        command.upgrade(config, "head")
        engine = create_database_engine(url)
        user_id, proposal_id = uuid4(), uuid4()
        try:
            with Session(engine) as session, session.begin():
                session.add(User(id=user_id, external_ref="policy-user", display_name="Synthetic"))
                session.flush()
                evidence = EvidenceItem(
                    user_id=user_id,
                    evidence_level="BANK_OBSERVED",
                    source_type="TEST_HISTORY",
                    source_ref="rent-history",
                    content={},
                    content_hash=configuration_hash({}),
                    valid_from=datetime(2026, 9, 1, tzinfo=UTC),
                    observed_at=NOW,
                )
                session.add(evidence)
                session.flush()
                session.add(
                    PolicyProposal(
                        id=proposal_id,
                        user_id=user_id,
                        source_type="TEST",
                        compiler_version="fixture",
                        proposed_configuration=configuration(),
                        evidence_ids=[str(evidence.id)],
                        idempotency_key="proposal-1",
                    )
                )
            yield engine, user_id, proposal_id
        finally:
            engine.dispose()


def test_confirmation_is_explicit_reviewed_and_idempotent(
    policy_engine: tuple[Engine, UUID, UUID],
) -> None:
    engine, user_id, proposal_id = policy_engine
    with Session(engine) as session, session.begin():
        with pytest.raises(PolicyLifecycleError):
            confirm_proposal(session, user_id, proposal_id, reviewed(configuration()), False, NOW)
        with pytest.raises(PolicyLifecycleError):
            confirm_proposal(session, user_id, proposal_id, "0" * 64, True, NOW)
        first = confirm_proposal(
            session, user_id, proposal_id, reviewed(configuration()), True, NOW
        )
        second = confirm_proposal(
            session, user_id, proposal_id, reviewed(configuration()), True, NOW
        )
        assert first == second
        assert first.status == "ACTIVE"
        version = session.get(PolicyVersion, first.current_version_id)
        assert version is not None and version.confirmed_at == NOW
        assert len(session.scalars(select(PolicyVersion)).all()) == 1
        confirmations = session.scalars(
            select(EvidenceItem).where(EvidenceItem.evidence_level == "USER_CONFIRMED_POLICY")
        ).all()
        assert len(confirmations) == 1
        assert is_version_authorized(session, user_id, first.current_version_id, NOW)
        assert not is_version_authorized(
            session, user_id, first.current_version_id, datetime(2026, 10, 2, tzinfo=UTC)
        )


def test_change_appends_a_version_and_rejects_stale_or_mismatched_replay(
    policy_engine: tuple[Engine, UUID, UUID],
) -> None:
    engine, user_id, proposal_id = policy_engine
    with Session(engine) as session, session.begin():
        first = confirm_proposal(
            session, user_id, proposal_id, reviewed(configuration()), True, NOW
        )
        changed = configuration() | {"name": "房租新约"}
        second = change_policy(
            session,
            user_id,
            first.policy_id,
            first.current_version_id,
            changed,
            reviewed(changed),
            True,
            "续租",
            "change-1",
            NOW,
        )
        replay = change_policy(
            session,
            user_id,
            first.policy_id,
            first.current_version_id,
            changed,
            reviewed(changed),
            True,
            "续租",
            "change-1",
            NOW,
        )
        assert second == replay
        old = session.get(PolicyVersion, first.current_version_id)
        new = session.get(PolicyVersion, second.current_version_id)
        assert old is not None and new is not None
        assert new.version_number == 2 and new.previous_hash == old.content_hash
        assert old.configuration["name"] == "房租"
        assert not is_version_authorized(session, user_id, old.id, NOW)
        assert is_version_authorized(session, user_id, new.id, NOW)
        with pytest.raises(PolicyLifecycleError) as error:
            change_policy(
                session,
                user_id,
                first.policy_id,
                first.current_version_id,
                changed,
                reviewed(changed),
                True,
                "different",
                "change-2",
                NOW,
            )
        assert error.value.status_code == 409


def test_pause_revoke_and_expiry_do_not_reactivate_old_authority(
    policy_engine: tuple[Engine, UUID, UUID],
) -> None:
    engine, user_id, proposal_id = policy_engine
    with Session(engine) as session, session.begin():
        first = confirm_proposal(
            session, user_id, proposal_id, reviewed(configuration()), True, NOW
        )
        paused = suspend_policy(session, user_id, first.policy_id, first.current_version_id, NOW)
        assert paused.status == "SUSPENDED"
        assert not is_version_authorized(session, user_id, first.current_version_id, NOW)
        changed = configuration() | {"name": "暂停中更新"}
        second = change_policy(
            session,
            user_id,
            first.policy_id,
            first.current_version_id,
            changed,
            reviewed(changed),
            True,
            "仍暂停",
            "change-paused",
            NOW,
        )
        assert second.status == "SUSPENDED"
        revoked = revoke_policy(session, user_id, first.policy_id, second.current_version_id, NOW)
        assert revoked.status == "REVOKED"
        assert not is_version_authorized(session, user_id, second.current_version_id, NOW)
        with pytest.raises(PolicyLifecycleError):
            change_policy(
                session,
                user_id,
                first.policy_id,
                second.current_version_id,
                changed,
                reviewed(changed),
                True,
                "不得复活",
                "revive",
                NOW,
            )


def test_future_start_and_exclusive_local_end_boundary(
    policy_engine: tuple[Engine, UUID, UUID],
) -> None:
    engine, user_id, proposal_id = policy_engine
    candidate = configuration() | {"valid_from": "2026-10-05", "valid_until": "2026-10-05"}
    with Session(engine) as session, session.begin():
        proposal = session.get(PolicyProposal, proposal_id)
        assert proposal is not None
        proposal.proposed_configuration = candidate
        first = confirm_proposal(session, user_id, proposal_id, reviewed(candidate), True, NOW)
        assert first.status == "CONFIRMED"
        start = datetime(2026, 10, 4, 16, tzinfo=UTC)
        end = datetime(2026, 10, 5, 16, tzinfo=UTC)
        refresh_time_states(session, user_id, start)
        policy = session.get(Policy, first.policy_id)
        version = session.get(PolicyVersion, first.current_version_id)
        assert policy is not None and version is not None
        assert policy.status == "ACTIVE"
        assert effective_status(policy, version, end) == "EXPIRED"
        assert not is_version_authorized(session, user_id, version.id, end)
        refresh_time_states(session, user_id, end)
        assert policy.status == "EXPIRED"
        assert refresh_time_states(session, user_id, end).updated_policy_ids == []


@pytest.mark.parametrize("fault", ["UNKNOWN", "CONFLICTED", "future", "missing", "other-user"])
def test_invalid_source_evidence_never_becomes_permission(
    policy_engine: tuple[Engine, UUID, UUID],
    fault: str,
) -> None:
    engine, user_id, proposal_id = policy_engine
    with Session(engine) as session, session.begin():
        proposal = session.get(PolicyProposal, proposal_id)
        assert proposal is not None
        evidence = session.get(EvidenceItem, UUID(proposal.evidence_ids[0]))
        assert evidence is not None
        if fault in {"UNKNOWN", "CONFLICTED"}:
            evidence.status = fault
        elif fault == "future":
            evidence.observed_at = NOW + timedelta(days=1)
        elif fault == "missing":
            proposal.evidence_ids = [str(uuid4())]
        else:
            other = User(external_ref="foreign-evidence", display_name="Other")
            session.add(other)
            session.flush()
            evidence.user_id = other.id
        with pytest.raises(PolicyLifecycleError) as error:
            confirm_proposal(session, user_id, proposal_id, reviewed(configuration()), True, NOW)
        assert error.value.code == "INVALID_EVIDENCE"
        assert session.scalars(select(PolicyVersion)).all() == []


def _plan(
    session: Session,
    user_id: UUID,
    account_id: UUID,
    label: str,
    status: str,
    direct: UUID | None = None,
    run_version: UUID | None = None,
    constraint_version: UUID | None = None,
    receipt: str | None = None,
    executed_cents: int = 0,
) -> ActionPlan:
    run = DecisionRun(
        user_id=user_id,
        idempotency_key=f"run-{label}",
        trigger_type="TEST",
        algorithm_version="fixture",
        as_of=NOW,
        input_snapshot={},
        snapshot_hash="b" * 64,
        policy_version_ids=[str(run_version)] if run_version else [],
    )
    session.add(run)
    session.flush()
    if constraint_version:
        session.add(
            DecisionConstraint(
                user_id=user_id,
                decision_run_id=run.id,
                policy_version_id=constraint_version,
                constraint_key=label,
                is_hard=True,
                calculation={},
                reason_code="TEST",
            )
        )
    plan = ActionPlan(
        user_id=user_id,
        decision_run_id=run.id,
        policy_version_id=direct,
        source_account_id=account_id,
        action_type="TEST",
        amount_cents=100,
        status=status,
        idempotency_key=label,
        request={"label": label},
        request_hash="c" * 64,
    )
    session.add(plan)
    session.flush()
    if receipt:
        session.add(
            ActionReceipt(
                user_id=user_id,
                action_plan_id=plan.id,
                attempt_number=1,
                receipt_ref=f"receipt-{label}",
                status=receipt,
                response={},
                occurred_at=NOW,
                executed_cents=executed_cents,
            )
        )
        session.flush()
    return plan


@pytest.mark.parametrize("operation", ["change", "suspend", "revoke", "expire"])
def test_all_old_direct_and_indirect_actions_are_invalidated_without_rewriting_inflight(
    policy_engine: tuple[Engine, UUID, UUID],
    operation: str,
) -> None:
    engine, user_id, proposal_id = policy_engine
    with Session(engine) as session, session.begin():
        first = confirm_proposal(
            session, user_id, proposal_id, reviewed(configuration()), True, NOW
        )
        changed = configuration() | {"name": "第二版"}
        second = change_policy(
            session,
            user_id,
            first.policy_id,
            first.current_version_id,
            changed,
            reviewed(changed),
            True,
            "second",
            "second",
            NOW,
        )
        account = Account(
            user_id=user_id, external_ref="actions", name="Actions", balance_cents=10000
        )
        session.add(account)
        session.flush()
        safe = [
            _plan(
                session,
                user_id,
                account.id,
                "direct-old",
                "PLANNED",
                direct=first.current_version_id,
            ),
            _plan(
                session,
                user_id,
                account.id,
                "direct-current",
                "AUTHORIZED",
                direct=second.current_version_id,
            ),
            _plan(
                session,
                user_id,
                account.id,
                "json-dependency",
                "PLANNED",
                run_version=first.current_version_id,
            ),
            _plan(
                session,
                user_id,
                account.id,
                "constraint-dependency",
                "AUTHORIZED",
                constraint_version=second.current_version_id,
            ),
        ]
        inflight = [
            _plan(
                session,
                user_id,
                account.id,
                "submitted",
                "SUBMITTED",
                direct=first.current_version_id,
            ),
            _plan(
                session,
                user_id,
                account.id,
                "unknown",
                "UNKNOWN",
                run_version=second.current_version_id,
            ),
            _plan(
                session,
                user_id,
                account.id,
                "planned-with-receipt",
                "PLANNED",
                direct=first.current_version_id,
                receipt="UNKNOWN",
            ),
            _plan(
                session,
                user_id,
                account.id,
                "authorized-with-receipt",
                "AUTHORIZED",
                direct=second.current_version_id,
                receipt="SUCCEEDED",
            ),
            _plan(
                session,
                user_id,
                account.id,
                "failed-unknown",
                "FAILED",
                direct=second.current_version_id,
                receipt="UNKNOWN",
            ),
            _plan(
                session,
                user_id,
                account.id,
                "failed-effects",
                "FAILED",
                direct=second.current_version_id,
                receipt="FAILED",
                executed_cents=10,
            ),
        ]
        historical = [
            _plan(
                session,
                user_id,
                account.id,
                "succeeded",
                "SUCCEEDED",
                direct=first.current_version_id,
                receipt="SUCCEEDED",
            ),
            _plan(
                session,
                user_id,
                account.id,
                "failed",
                "FAILED",
                direct=second.current_version_id,
                receipt="FAILED",
            ),
        ]
        unrelated = _plan(session, user_id, account.id, "unrelated", "PLANNED")
        immutable = {
            plan.id: (
                plan.request_hash,
                plan.idempotency_key,
                plan.policy_version_id,
                dict(plan.request),
            )
            for plan in safe + inflight + historical + [unrelated]
        }
        preserved_states = {plan.id: plan.status for plan in inflight + historical + [unrelated]}
        if operation == "change":
            third = changed | {"name": "第三版"}
            result = change_policy(
                session,
                user_id,
                first.policy_id,
                second.current_version_id,
                third,
                reviewed(third),
                True,
                "third",
                "third",
                NOW,
            )
        elif operation == "suspend":
            result = suspend_policy(
                session, user_id, first.policy_id, second.current_version_id, NOW
            )
        elif operation == "revoke":
            result = revoke_policy(
                session, user_id, first.policy_id, second.current_version_id, NOW
            )
        else:
            refreshed = refresh_time_states(session, user_id, datetime(2027, 6, 30, 16, tzinfo=UTC))
            assert set(refreshed.invalidated_action_ids) == {plan.id for plan in safe}
            assert set(refreshed.inflight_action_ids) == {plan.id for plan in inflight}
            result = None
        if result is not None:
            assert set(result.invalidated_action_ids) == {plan.id for plan in safe}
            assert set(result.inflight_action_ids) == {plan.id for plan in inflight}
        for plan in safe:
            assert plan.status == "INVALIDATED"
        for plan in inflight + historical + [unrelated]:
            assert plan.status == preserved_states[plan.id]
        for plan in safe + inflight + historical + [unrelated]:
            assert (
                plan.request_hash,
                plan.idempotency_key,
                plan.policy_version_id,
                dict(plan.request),
            ) == immutable[plan.id]


def test_database_prevents_updates_but_lifecycle_can_append(
    policy_engine: tuple[Engine, UUID, UUID],
) -> None:
    engine, user_id, proposal_id = policy_engine
    with Session(engine) as session, session.begin():
        first = confirm_proposal(
            session, user_id, proposal_id, reviewed(configuration()), True, NOW
        )
    with Session(engine) as session, session.begin():
        with pytest.raises(DBAPIError, match="immutable"), session.begin_nested():
            session.execute(
                update(PolicyVersion)
                .where(PolicyVersion.id == first.current_version_id)
                .values(summary="tampered")
            )
        old = session.get(PolicyVersion, first.current_version_id)
        assert old is not None and old.summary == "房租"


def test_failed_version_insert_rolls_back_evidence_policy_and_action_invalidation(
    policy_engine: tuple[Engine, UUID, UUID],
) -> None:
    engine, user_id, proposal_id = policy_engine
    with Session(engine) as session, session.begin():
        first = confirm_proposal(
            session, user_id, proposal_id, reviewed(configuration()), True, NOW
        )
        account = Account(user_id=user_id, external_ref="rollback", name="Rollback")
        session.add(account)
        session.flush()
        plan = _plan(
            session, user_id, account.id, "rollback", "PLANNED", direct=first.current_version_id
        )
        plan_id = plan.id
        evidence_count = len(session.scalars(select(EvidenceItem)).all())
    with engine.begin() as connection:
        connection.execute(
            text("""
            CREATE FUNCTION reject_new_policy_version() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF NEW.version_number > 1 THEN RAISE EXCEPTION 'forced version failure'; END IF;
              RETURN NEW;
            END $$
        """)
        )
        connection.execute(
            text("""
            CREATE TRIGGER reject_new_policy_version BEFORE INSERT ON policy_versions
            FOR EACH ROW EXECUTE FUNCTION reject_new_policy_version()
        """)
        )
    with Session(engine) as session, session.begin():
        changed = configuration() | {"name": "必须回滚"}
        with pytest.raises(DBAPIError, match="forced version failure"):
            change_policy(
                session,
                user_id,
                first.policy_id,
                first.current_version_id,
                changed,
                reviewed(changed),
                True,
                "failure",
                "failure",
                NOW,
            )
        policy = session.get(Policy, first.policy_id)
        restored = session.get(ActionPlan, plan_id)
        assert policy is not None and policy.name == "房租"
        assert restored is not None and restored.status == "PLANNED"
        assert len(session.scalars(select(EvidenceItem)).all()) == evidence_count
        assert len(session.scalars(select(PolicyVersion)).all()) == 1


def test_concurrent_changes_from_same_expected_version_have_exactly_one_winner(
    policy_engine: tuple[Engine, UUID, UUID],
) -> None:
    engine, user_id, proposal_id = policy_engine
    with Session(engine) as session, session.begin():
        first = confirm_proposal(
            session, user_id, proposal_id, reviewed(configuration()), True, NOW
        )
    barrier = Barrier(2)

    def attempt(number: int) -> str:
        candidate = configuration() | {"name": f"并发修改{number}"}
        with Session(engine) as session, session.begin():
            barrier.wait(timeout=10)
            try:
                change_policy(
                    session,
                    user_id,
                    first.policy_id,
                    first.current_version_id,
                    candidate,
                    reviewed(candidate),
                    True,
                    f"change {number}",
                    f"race-{number}",
                    NOW,
                )
                return "success"
            except PolicyLifecycleError as error:
                assert error.status_code == 409
                return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, [1, 2]))
    assert sorted(results) == ["STALE_POLICY_VERSION", "success"]
    with Session(engine) as session:
        assert len(session.scalars(select(PolicyVersion)).all()) == 2


def test_existing_goal_projection_changes_without_moving_cash_or_positions(
    policy_engine: tuple[Engine, UUID, UUID],
) -> None:
    engine, user_id, proposal_id = policy_engine
    goal_config: dict[str, Any] = {
        "type": "goal_saving",
        "name": "买车",
        "target_cents": 3000000,
        "deadline": "2027-10-01",
        "monthly_contribution": {"min_cents": 180000, "target_cents": 200000, "max_cents": 250000},
    }
    with Session(engine) as session, session.begin():
        proposal = session.get(PolicyProposal, proposal_id)
        assert proposal is not None
        proposal.proposed_configuration = goal_config
        first = confirm_proposal(session, user_id, proposal_id, reviewed(goal_config), True, NOW)
        account = Account(
            user_id=user_id, external_ref="goal", name="Goal cash", balance_cents=70000
        )
        product = AssetProduct(
            product_code="GOAL_TEST",
            version_number=1,
            name="Synthetic",
            asset_class="CASH_MGMT_T0",
            effective_from=NOW,
        )
        session.add_all([account, product])
        session.flush()
        goal = Goal(
            user_id=user_id,
            policy_id=first.policy_id,
            policy_version_id=first.current_version_id,
            account_id=account.id,
            name="买车",
            target_cents=3000000,
            allocated_cents=120000,
            deadline=date(2027, 10, 1),
            monthly_min_cents=180000,
            monthly_target_cents=200000,
            monthly_max_cents=250000,
        )
        session.add(goal)
        session.flush()
        position = AssetPosition(
            user_id=user_id,
            account_id=account.id,
            product_id=product.id,
            goal_id=goal.id,
            policy_version_id=first.current_version_id,
            principal_cents=50000,
            purchased_at=NOW,
        )
        session.add(position)
        session.flush()
        changed = goal_config | {"target_cents": 4000000, "deadline": "2028-01-01"}
        second = change_policy(
            session,
            user_id,
            first.policy_id,
            first.current_version_id,
            changed,
            reviewed(changed),
            True,
            "目标调整",
            "goal-change",
            NOW,
        )
        assert goal.policy_version_id == second.current_version_id
        assert goal.target_cents == 4000000 and goal.deadline == date(2028, 1, 1)
        assert goal.allocated_cents == 120000 and account.balance_cents == 70000
        assert position.principal_cents == 50000
        assert position.policy_version_id == first.current_version_id


def test_idempotency_key_cannot_be_reused_for_another_request(
    policy_engine: tuple[Engine, UUID, UUID],
) -> None:
    engine, user_id, proposal_id = policy_engine
    with Session(engine) as session, session.begin():
        first = confirm_proposal(
            session, user_id, proposal_id, reviewed(configuration()), True, NOW
        )
        changed = configuration() | {"name": "修改"}
        change_policy(
            session,
            user_id,
            first.policy_id,
            first.current_version_id,
            changed,
            reviewed(changed),
            True,
            "reason",
            "same-key",
            NOW,
        )
        with pytest.raises(PolicyLifecycleError) as error:
            change_policy(
                session,
                user_id,
                first.policy_id,
                first.current_version_id,
                changed,
                reviewed(changed),
                True,
                "DIFFERENT",
                "same-key",
                NOW,
            )
        assert error.value.code == "IDEMPOTENCY_CONFLICT"


@pytest.mark.parametrize("rehash", [False, True])
def test_confirmation_evidence_tampering_never_grants_authority(
    policy_engine: tuple[Engine, UUID, UUID],
    rehash: bool,
) -> None:
    engine, user_id, proposal_id = policy_engine
    with Session(engine) as session, session.begin():
        first = confirm_proposal(
            session, user_id, proposal_id, reviewed(configuration()), True, NOW
        )
        proof = session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.source_type == "POLICY_CONFIRMATION",
            )
        ).one()
        proof.content = proof.content | {"version_id": str(uuid4())}
        if rehash:
            proof.content_hash = configuration_hash(proof.content)
        session.flush()
        assert not is_version_authorized(session, user_id, first.current_version_id, NOW)


def test_historical_command_replay_after_revocation_does_not_restore_permissions(
    policy_engine: tuple[Engine, UUID, UUID],
) -> None:
    engine, user_id, proposal_id = policy_engine
    with Session(engine) as session, session.begin():
        first = confirm_proposal(
            session, user_id, proposal_id, reviewed(configuration()), True, NOW
        )
        changed = configuration() | {"name": "历史修改"}
        second = change_policy(
            session,
            user_id,
            first.policy_id,
            first.current_version_id,
            changed,
            reviewed(changed),
            True,
            "历史修改",
            "historical-change",
            NOW,
        )
        revoke_policy(session, user_id, first.policy_id, second.current_version_id, NOW)
        assert (
            confirm_proposal(session, user_id, proposal_id, reviewed(configuration()), True, NOW)
            == first
        )
        assert (
            change_policy(
                session,
                user_id,
                first.policy_id,
                first.current_version_id,
                changed,
                reviewed(changed),
                True,
                "历史修改",
                "historical-change",
                NOW,
            )
            == second
        )
        policy = session.get(Policy, first.policy_id)
        assert policy is not None and policy.status == "REVOKED"
        assert not is_version_authorized(session, user_id, first.current_version_id, NOW)
        assert not is_version_authorized(session, user_id, second.current_version_id, NOW)
        assert len(session.scalars(select(PolicyVersion)).all()) == 2


@pytest.mark.parametrize(
    "field,value", [("valid_until", "9999-12-31"), ("valid_from", "0001-01-01")]
)
def test_unrepresentable_date_windows_fail_with_a_domain_error(
    policy_engine: tuple[Engine, UUID, UUID],
    field: str,
    value: str,
) -> None:
    engine, user_id, proposal_id = policy_engine
    with Session(engine) as session, session.begin():
        candidate = configuration() | {field: value}
        proposal = session.get(PolicyProposal, proposal_id)
        assert proposal is not None
        proposal.proposed_configuration = candidate
        with pytest.raises(PolicyLifecycleError) as error:
            confirm_proposal(session, user_id, proposal_id, reviewed(candidate), True, NOW)
        assert error.value.code == "INVALID_WINDOW"
        assert session.scalars(select(PolicyVersion)).all() == []


@pytest.mark.parametrize("kind", ["missing", "other-user", "wrong-type"])
def test_bill_balance_reference_requires_own_credit_card_on_confirm_and_change(
    policy_engine: tuple[Engine, UUID, UUID],
    kind: str,
) -> None:
    engine, user_id, proposal_id = policy_engine
    with Session(engine) as session, session.begin():
        bad_account_id = uuid4()
        if kind != "missing":
            owner_id = user_id
            if kind == "other-user":
                owner_id = uuid4()
                session.add(User(id=owner_id, external_ref="other-card", display_name="Other"))
                session.flush()
            session.add(
                Account(
                    id=bad_account_id,
                    user_id=owner_id,
                    external_ref="bad-card",
                    name="Bad reference",
                    account_type="CASH" if kind == "wrong-type" else "CREDIT_CARD",
                )
            )
            session.flush()
        first = confirm_proposal(
            session, user_id, proposal_id, reviewed(configuration()), True, NOW
        )
        candidate = configuration() | {
            "amount_rule": {"kind": "bill_balance", "account_id": str(bad_account_id)}
        }
        with pytest.raises(PolicyLifecycleError) as error:
            change_policy(
                session,
                user_id,
                first.policy_id,
                first.current_version_id,
                candidate,
                reviewed(candidate),
                True,
                "bad-ref",
                "bad-ref",
                NOW,
            )
        assert error.value.code == "INVALID_BILL_ACCOUNT"
        other_proposal = PolicyProposal(
            user_id=user_id,
            source_type="TEST",
            compiler_version="fixture",
            proposed_configuration=candidate,
            evidence_ids=[],
            idempotency_key="bad-confirm",
        )
        session.add(other_proposal)
        session.flush()
        with pytest.raises(PolicyLifecycleError) as error:
            confirm_proposal(session, user_id, other_proposal.id, reviewed(candidate), True, NOW)
        assert error.value.code == "INVALID_BILL_ACCOUNT"


@pytest.mark.parametrize("kind", ["missing", "other-user"])
def test_goal_scoped_asset_authorization_requires_own_existing_goal(
    policy_engine: tuple[Engine, UUID, UUID],
    kind: str,
) -> None:
    engine, user_id, proposal_id = policy_engine
    with Session(engine) as session, session.begin():
        target_id = uuid4()
        if kind == "other-user":
            owner_id, owner_policy_id, owner_version_id = uuid4(), uuid4(), uuid4()
            session.add(User(id=owner_id, external_ref="other-goal", display_name="Other"))
            session.flush()
            session.add(
                Policy(
                    id=owner_policy_id,
                    user_id=owner_id,
                    name="Other goal",
                    policy_type="goal_saving",
                )
            )
            session.flush()
            session.add(
                PolicyVersion(
                    id=owner_version_id,
                    user_id=owner_id,
                    policy_id=owner_policy_id,
                    version_number=1,
                    configuration={},
                    content_hash=configuration_hash({}),
                )
            )
            session.flush()
            session.add(
                Goal(
                    id=target_id,
                    user_id=owner_id,
                    policy_id=owner_policy_id,
                    policy_version_id=owner_version_id,
                    name="Other goal",
                    target_cents=10000,
                    deadline=date(2027, 10, 1),
                    monthly_min_cents=0,
                    monthly_target_cents=0,
                    monthly_max_cents=0,
                )
            )
            session.flush()
        candidate: dict[str, Any] = {
            "type": "asset_authorization",
            "scope": "goal",
            "goal_id": str(target_id),
            "allowed_asset_classes": ["CASH"],
            "max_auto_managed_cents": 10000,
            "single_action_cap_cents": 10000,
            "max_redemption_delay_days": 0,
            "max_lock_days": 0,
        }
        proposal = session.get(PolicyProposal, proposal_id)
        assert proposal is not None
        proposal.proposed_configuration = candidate
        with pytest.raises(PolicyLifecycleError) as error:
            confirm_proposal(session, user_id, proposal_id, reviewed(candidate), True, NOW)
        assert error.value.code == "INVALID_GOAL"


def test_expired_end_with_implicit_start_has_a_domain_error_not_a_database_error(
    policy_engine: tuple[Engine, UUID, UUID],
) -> None:
    engine, user_id, proposal_id = policy_engine
    with Session(engine) as session, session.begin():
        candidate = {key: value for key, value in configuration().items() if key != "valid_from"}
        candidate["valid_until"] = "2026-09-30"
        proposal = session.get(PolicyProposal, proposal_id)
        assert proposal is not None
        proposal.proposed_configuration = candidate
        with pytest.raises(PolicyLifecycleError) as error:
            confirm_proposal(session, user_id, proposal_id, reviewed(candidate), True, NOW)
        assert error.value.code == "INVALID_WINDOW"
