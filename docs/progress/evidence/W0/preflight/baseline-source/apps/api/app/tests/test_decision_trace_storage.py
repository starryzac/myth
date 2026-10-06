"""Frozen decisions survive history changes and preserve their tenant and transaction."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from alembic import command
from app.db.models import (
    Account,
    ActionPlan,
    DecisionConstraint,
    DecisionRun,
    EvidenceItem,
    Policy,
    PolicyVersion,
    User,
)
from app.db.session import create_database_engine
from app.db.testing import temporary_database
from app.domain.decision_trace import build_trace
from app.domain.decision_trace_types import DecisionTrace, TraceConstraint
from app.domain.execution import execution_effect_hash
from app.domain.execution_types import BankCommand, ExecutionEffect
from app.domain.policy_configuration import configuration_hash
from app.services.action_contracts import PrepareActionRequest, TransferIntent
from app.services.decision_trace import (
    capture_available_sources,
    capture_policies,
    capture_sources,
    get_action_trace,
    get_decision_trace,
    list_decision_traces,
    record_trace,
)
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF
from app.services.execution import prepare_action
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_boundary_service import boundary_engine as boundary_engine
from app.tests.test_execution_service import transfer_accounts
from app.tests.test_migrations import migration_config
from sqlalchemy import delete, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 4, 12, tzinfo=UTC)
OWNER, OTHER = UUID(int=71), UUID(int=72)


@pytest.fixture
def trace_engine() -> Iterator[Engine]:
    with temporary_database() as url:
        command.upgrade(migration_config(url), "head")
        engine = create_database_engine(url)
        try:
            with Session(engine) as session, session.begin():
                session.add_all(
                    [
                        User(id=OWNER, external_ref="trace-owner", display_name="Synthetic"),
                        User(id=OTHER, external_ref="trace-other", display_name="Synthetic"),
                    ]
                )
            yield engine
        finally:
            engine.dispose()


def storage_trace(
    session: Session, *, run_id: UUID | None = None, **fields: object
) -> DecisionTrace:
    return build_trace(
        run_id=run_id or uuid4(),
        user_id=OWNER,
        phase="EVALUATION",
        as_of=NOW,
        algorithm_versions={"boundary": "strict-cash-boundary-v1"},
        inputs={"balance_cents": 1000},
        sources=[],
        policies=[],
        constraints=[],
        candidates=[],
        outcome={"level": "ADVISE_ONLY", "financial_evaluation": "NOT_EVALUATED"},
        **fields,
    )


def source_rows(session: Session) -> tuple[UUID, UUID, UUID]:
    source_id, policy_id, version_id = uuid4(), uuid4(), uuid4()
    payload = {"simulation": True, "balance_cents": 1000}
    session.add(
        EvidenceItem(
            id=source_id,
            user_id=OWNER,
            evidence_level="BANK_CONFIRMED",
            source_type="SIMULATED_BANK_ACCOUNT",
            source_ref="synthetic-account",
            content=payload,
            content_hash=configuration_hash(payload),
            valid_from=NOW,
            observed_at=NOW,
            status="VALID",
        )
    )
    session.add(Policy(id=policy_id, user_id=OWNER, name="Synthetic", policy_type="living_reserve"))
    session.flush()
    session.add(
        PolicyVersion(
            id=version_id,
            user_id=OWNER,
            policy_id=policy_id,
            version_number=1,
            configuration={"simulation": True},
            content_hash=configuration_hash({"simulation": True}),
            confirmed_at=NOW,
            valid_from=NOW,
        )
    )
    session.flush()
    return source_id, policy_id, version_id


def test_record_is_idempotent_and_conflicting_trace_never_overwrites(trace_engine: Engine) -> None:
    with Session(trace_engine) as session, session.begin():
        trace = storage_trace(session)
        row = record_trace(session, trace)
        assert record_trace(session, trace).id == row.id
        changed = build_trace(
            **{**trace.model_dump(exclude={"input_hash", "trace_hash"}), "outcome": {"x": 2}}
        )
        with pytest.raises(PolicyLifecycleError) as error:
            record_trace(session, changed)
        assert error.value.status_code == 409
    with Session(trace_engine) as session:
        result = get_decision_trace(session, OWNER, trace.run_id, NOW)
        assert result.completeness == "COMPLETE"
        assert result.trace == trace
        # This fixture imported account/policy facts before audit activation.
        assert result.audit_chain_status == "LEGACY_UNAUDITED"
        assert result.explanation is not None
        assert result.explanation.audit_chain == "NOT_IMPLEMENTED"
        assert len(session.scalars(select(DecisionRun)).all()) == 1


def test_attach_preserves_legacy_preview_notifications_and_execution_status(
    trace_engine: Engine,
) -> None:
    with Session(trace_engine) as session, session.begin():
        initial = {"idempotency_key": "original", "preview": {"status": "WAITING"}}
        row = DecisionRun(
            user_id=OWNER,
            idempotency_key="recovery:original",
            trigger_type="SAFETY_RECOVERY",
            algorithm_version="whole-position-recovery-v1",
            as_of=NOW,
            input_snapshot=initial,
            snapshot_hash=configuration_hash(initial),
            result={"notifications": [{"code": "REDEMPTION_ACCEPTED"}]},
            status="PENDING",
        )
        session.add(row)
        session.flush()
        trace = storage_trace(session, run_id=row.id)
        assert record_trace(session, trace, existing_run=row) is row
        assert row.input_snapshot["preview"] == {"status": "WAITING"}
        assert row.result == {"notifications": [{"code": "REDEMPTION_ACCEPTED"}]}
        assert row.status == "PENDING" and row.completed_at is None


def test_source_and_policy_history_notes_preserve_frozen_decision(trace_engine: Engine) -> None:
    with Session(trace_engine) as session, session.begin():
        source_id, policy_id, version_id = source_rows(session)
        trace = build_trace(
            **{
                **storage_trace(session).model_dump(exclude={"input_hash", "trace_hash"}),
                "sources": capture_sources(session, OWNER, [source_id]),
                "policies": capture_policies(session, OWNER, [version_id]),
            }
        )
        record_trace(session, trace)
    with Session(trace_engine) as session, session.begin():
        source = session.get(EvidenceItem, source_id)
        policy = session.get(Policy, policy_id)
        assert source is not None and policy is not None
        source.status, policy.status = "SUPERSEDED", "REVOKED"
    with Session(trace_engine) as session:
        result = get_decision_trace(session, OWNER, trace.run_id, NOW + timedelta(days=1))
        assert result.trace == trace
        assert {item.current_status for item in result.current_references} == {
            "SUPERSEDED",
            "REVOKED",
        }


@pytest.mark.parametrize("mutation", ["snapshot", "source", "constraint", "deleted_constraint"])
def test_corruption_is_detected_without_recomputing(trace_engine: Engine, mutation: str) -> None:
    with Session(trace_engine) as session, session.begin():
        source_id, _, version_id = source_rows(session)
        constraint = TraceConstraint(
            constraint_key="cash:today",
            policy_version_id=version_id,
            is_hard=True,
            satisfied=True,
            required_cents=100,
            available_cents=1000,
            due_date=NOW.date(),
            calculation={"margin_cents": 900},
            reason_code="SAFE_CASH",
        )
        trace = build_trace(
            **{
                **storage_trace(session).model_dump(exclude={"input_hash", "trace_hash"}),
                "sources": capture_sources(session, OWNER, [source_id]),
                "policies": capture_policies(session, OWNER, [version_id]),
                "constraints": [constraint],
            }
        )
        record_trace(session, trace)
    with Session(trace_engine) as session, session.begin():
        if mutation == "snapshot":
            run = session.get(DecisionRun, trace.run_id)
            assert run is not None
            run.input_snapshot = {**run.input_snapshot, "forged": True}
        elif mutation == "source":
            source = session.get(EvidenceItem, source_id)
            assert source is not None
            source.content = {"simulation": True, "balance_cents": 99}
            source.content_hash = configuration_hash(source.content)
        elif mutation == "constraint":
            row = session.scalar(
                select(DecisionConstraint).where(DecisionConstraint.decision_run_id == trace.run_id)
            )
            assert row is not None
            row.available_cents = 999
        else:
            session.execute(
                delete(DecisionConstraint).where(DecisionConstraint.decision_run_id == trace.run_id)
            )
    with Session(trace_engine) as session, pytest.raises(PolicyLifecycleError) as error:
        get_decision_trace(session, OWNER, trace.run_id, NOW)
    assert error.value.status_code == 409
    assert error.value.code == "DECISION_TRACE_INTEGRITY_ERROR"


def test_get_list_and_action_link_are_read_only_and_cross_user_is_not_found(
    trace_engine: Engine,
) -> None:
    with Session(trace_engine) as session, session.begin():
        trace = storage_trace(session)
        row = record_trace(session, trace)
        account = Account(
            user_id=OWNER, external_ref="source", name="Synthetic", balance_cents=1000
        )
        session.add(account)
        session.flush()
        action = ActionPlan(
            user_id=OWNER,
            decision_run_id=row.id,
            source_account_id=account.id,
            action_type="TRANSFER_INTERNAL",
            amount_cents=100,
            idempotency_key="synthetic-action",
            request={},
            request_hash=configuration_hash({}),
        )
        session.add(action)
        session.flush()
        action_id = action.id
    with Session(trace_engine) as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        assert get_action_trace(session, OWNER, action_id, NOW).run_id == trace.run_id
        assert get_decision_trace(session, OWNER, trace.run_id, NOW).trace == trace
        assert list_decision_traces(session, OWNER).items[0].run_id == trace.run_id
        with pytest.raises(PolicyLifecycleError) as error:
            get_decision_trace(session, OTHER, trace.run_id, NOW)
        assert error.value.status_code == 404
        with pytest.raises(PolicyLifecycleError) as error:
            get_action_trace(session, OTHER, action_id, NOW)
        assert error.value.status_code == 404
        assert list_decision_traces(session, OTHER).items == []


def test_cursor_is_stable_bounded_and_rejects_untrusted_input(trace_engine: Engine) -> None:
    with Session(trace_engine) as session, session.begin():
        for number in range(5):
            record_trace(session, storage_trace(session, run_id=UUID(int=100 + number)))
    with Session(trace_engine) as session:
        first = list_decision_traces(session, OWNER, limit=2)
        second = list_decision_traces(session, OWNER, limit=2, cursor=first.next_cursor)
        third = list_decision_traces(session, OWNER, limit=2, cursor=second.next_cursor)
        assert [item.run_id.int for item in first.items + second.items + third.items] == [
            104,
            103,
            102,
            101,
            100,
        ]
        assert third.next_cursor is None
        for cursor in ["bad!", "a" * 1000, "e30", "WzEsMl0"]:
            with pytest.raises(PolicyLifecycleError):
                list_decision_traces(session, OWNER, cursor=cursor)
        for limit in [0, 101, True]:
            with pytest.raises(PolicyLifecycleError):
                list_decision_traces(session, OWNER, limit=limit)


def test_unrecorded_legacy_and_unknown_algorithm_are_explicit(trace_engine: Engine) -> None:
    with Session(trace_engine) as session, session.begin():
        legacy = DecisionRun(
            user_id=OWNER,
            idempotency_key="old-decision",
            trigger_type="ACTION_PREPARE",
            algorithm_version="old-unavailable-version",
            as_of=NOW,
            input_snapshot={"only_old_fact": 1},
            snapshot_hash=configuration_hash({"only_old_fact": 1}),
        )
        session.add(legacy)
        session.flush()
        legacy_id = legacy.id
        trace = storage_trace(session)
        unknown = build_trace(
            **{
                **trace.model_dump(exclude={"input_hash", "trace_hash"}),
                "algorithm_versions": {"boundary": "future-version-v99"},
            }
        )
        record_trace(session, unknown)
    with Session(trace_engine) as session:
        result = get_decision_trace(session, OWNER, legacy_id, NOW)
        assert result.completeness == "LEGACY_PARTIAL" and result.trace is None
        assert result.legacy_snapshot == {"only_old_fact": 1}
        assert result.audit_chain_status == "LEGACY_UNAUDITED"
        result = get_decision_trace(session, OWNER, unknown.run_id, NOW)
        assert result.completeness == "UNSUPPORTED_VERSION"
        assert result.explanation is None
        assert result.audit_chain_status == "UNSUPPORTED_VERSION"


def test_record_rollback_does_not_leave_constraints_or_trace(trace_engine: Engine) -> None:
    with Session(trace_engine) as session:
        trace = storage_trace(session)
        record_trace(session, trace)
        session.rollback()
    with Session(trace_engine) as session:
        assert session.get(DecisionRun, trace.run_id) is None
        assert session.scalars(select(DecisionConstraint)).all() == []


def test_capture_rejects_foreign_missing_or_corrupt_source(trace_engine: Engine) -> None:
    with Session(trace_engine) as session, session.begin():
        source_id, _, version_id = source_rows(session)
        for capture_function, identity in [
            (capture_sources, source_id),
            (capture_policies, version_id),
        ]:
            with pytest.raises(PolicyLifecycleError):
                capture_function(session, OTHER, [identity])
            with pytest.raises(PolicyLifecycleError):
                capture_function(session, OWNER, [uuid4()])
        missing_id = uuid4()
        available, missing = capture_available_sources(session, OWNER, [source_id, missing_id])
        assert [item.id for item in available] == [source_id]
        assert missing == [missing_id]
        with pytest.raises(PolicyLifecycleError) as error:
            capture_available_sources(session, OTHER, [source_id, missing_id])
        assert error.value.status_code == 404
        source = session.get(EvidenceItem, source_id)
        assert source is not None
        source.content = {"forged": True}
        session.flush()
        captured = capture_sources(session, OWNER, [source_id])
        assert captured[0].content_integrity == "INVALID"
        assert captured[0].captured_content_hash == configuration_hash({"forged": True})


def test_invalid_original_claim_is_frozen_and_readable_without_becoming_valid(
    trace_engine: Engine,
) -> None:
    with Session(trace_engine) as session, session.begin():
        source_id, _, _ = source_rows(session)
        row = session.get(EvidenceItem, source_id)
        assert row is not None
        row.content_hash = "0" * 64
        session.flush()
        trace = build_trace(
            **{
                **storage_trace(session).model_dump(exclude={"input_hash", "trace_hash"}),
                "sources": capture_sources(session, OWNER, [source_id]),
                "outcome": {"level": "BLOCKED", "reasons": ["INVALID_BALANCE_EVIDENCE"]},
            }
        )
        record_trace(session, trace)
    with Session(trace_engine) as session:
        result = get_decision_trace(session, OWNER, trace.run_id, NOW)
        assert result.completeness == "COMPLETE"
        assert result.trace is not None
        assert result.trace.sources[0].content_integrity == "INVALID"
        assert result.trace.outcome["level"] == "BLOCKED"
        assert result.current_references[0].status == "UNCHANGED"


def test_child_trace_cannot_rebind_another_action_to_its_parent(trace_engine: Engine) -> None:
    with Session(trace_engine) as session, session.begin():
        first, second = storage_trace(session), storage_trace(session)
        record_trace(session, first)
        record_trace(session, second)
        account = Account(user_id=OWNER, external_ref="rebind", name="Synthetic")
        session.add(account)
        session.flush()
        action = ActionPlan(
            user_id=OWNER,
            decision_run_id=first.run_id,
            source_account_id=account.id,
            action_type="TRANSFER_INTERNAL",
            amount_cents=100,
            idempotency_key="rebind",
            request={},
            request_hash=configuration_hash({}),
        )
        session.add(action)
        session.flush()
        child = storage_trace(session, action_id=action.id, parent_run_id=second.run_id)
        with pytest.raises(PolicyLifecycleError) as error:
            record_trace(session, child)
        assert error.value.status_code == 409


def test_reference_indexes_cannot_be_silently_expanded(trace_engine: Engine) -> None:
    with Session(trace_engine) as session, session.begin():
        trace = storage_trace(session)
        record_trace(session, trace)
    with Session(trace_engine) as session, session.begin():
        row = session.get(DecisionRun, trace.run_id)
        assert row is not None
        row.evidence_ids = [str(uuid4())]
    with Session(trace_engine) as session, pytest.raises(PolicyLifecycleError) as error:
        get_decision_trace(session, OWNER, trace.run_id, NOW)
    assert error.value.code == "DECISION_TRACE_INTEGRITY_ERROR"


def test_rehashing_current_execution_cannot_rewrite_the_original_frozen_effect(
    boundary_engine: Engine,
) -> None:
    source, target = transfer_accounts(boundary_engine)
    prepared = prepare_action(
        boundary_engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="trace-original-effect",
            intent=TransferIntent(
                kind="transfer_internal",
                source_account_id=source,
                destination_account_id=target,
                amount_cents=10000,
            ),
        ),
        SEED_AS_OF,
    )
    with Session(boundary_engine) as session, session.begin():
        action = session.get(ActionPlan, prepared.action_id)
        assert action is not None
        changed = prepared.effect.model_dump()
        changed["amount_cents"] += 1
        changed["cash_uses"][0]["amount_cents"] += 1
        effect = ExecutionEffect.model_validate(changed)
        command = BankCommand(effect=effect, effect_hash=execution_effect_hash(effect))
        action.request = {**action.request, "execution": command.model_dump(mode="json")}
        action.request_hash = configuration_hash(action.request)
        action.amount_cents += 1
    with Session(boundary_engine) as session, pytest.raises(PolicyLifecycleError) as error:
        get_action_trace(session, DEMO_USER_ID, prepared.action_id, SEED_AS_OF)
    assert error.value.code == "DECISION_TRACE_INTEGRITY_ERROR"


def test_current_request_cannot_rewrite_frozen_recovery_request(trace_engine: Engine) -> None:
    with Session(trace_engine) as session, session.begin():
        trace = storage_trace(session)
        run = DecisionRun(
            id=trace.run_id,
            user_id=OWNER,
            idempotency_key="original-recovery-request",
            trigger_type="TEST",
            algorithm_version="fixture",
            as_of=NOW,
            input_snapshot={},
            snapshot_hash=configuration_hash({}),
        )
        session.add(run)
        account = Account(user_id=OWNER, external_ref="frozen-request", name="Synthetic")
        session.add(account)
        session.flush()
        request = {"bank_request": {"amount_cents": 100}}
        action = ActionPlan(
            user_id=OWNER,
            decision_run_id=run.id,
            source_account_id=account.id,
            action_type="ASSET_REDEEM",
            amount_cents=100,
            idempotency_key="frozen-request",
            request=request,
            request_hash=configuration_hash(request),
        )
        session.add(action)
        session.flush()
        action_id = action.id
        recorded = build_trace(
            **{
                **trace.model_dump(exclude={"input_hash", "trace_hash"}),
                "inputs": {"action_requests": {str(action_id): request}},
            }
        )
        record_trace(session, recorded, existing_run=run)
    with Session(trace_engine) as session, session.begin():
        current_action = session.get(ActionPlan, action_id)
        assert current_action is not None
        current_action.request = {"bank_request": {"amount_cents": 999}}
        current_action.request_hash = configuration_hash(current_action.request)
    with Session(trace_engine) as session, pytest.raises(PolicyLifecycleError) as error:
        get_decision_trace(session, OWNER, trace.run_id, NOW)
    assert error.value.code == "DECISION_TRACE_INTEGRITY_ERROR"


def test_read_clock_cannot_describe_a_future_decision_as_already_occurred(
    trace_engine: Engine,
) -> None:
    with Session(trace_engine) as session, session.begin():
        trace = storage_trace(session)
        record_trace(session, trace)
    with Session(trace_engine) as session, pytest.raises(PolicyLifecycleError) as error:
        get_decision_trace(session, OWNER, trace.run_id, NOW - timedelta(seconds=1))
    assert error.value.code == "DECISION_NOT_YET_OCCURRED"
