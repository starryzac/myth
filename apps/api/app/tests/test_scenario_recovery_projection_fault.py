"""Installed isolation risks and owned-PG nodes; never a formal C04 or 24-case proof."""

from __future__ import annotations

import copy
import inspect
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import Mock
from uuid import uuid4

import pytest
from app.db.base import Base
from app.db.models import (
    ActionPlan,
    ActionReceipt,
    DecisionRun,
    SimulatedBankPosting,
    SimulatedBankRedemption,
)
from app.domain.policy_configuration import configuration_hash
from app.services import recovery
from app.services import scenario_runner as installed
from app.services import scenario_types as types_candidate
from app.services.audit_chain import current_audit_epoch, row_copy, verify_audit_chain
from app.services.demo_seed import DEMO_USER_ID
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.recovery import get_recovery_run, run_recovery
from app.services.scenario_types import ExpectedScenarioError, InitialState, Scenario, ScenarioStep
from app.tests.dashboard_scenario import STAGES, DashboardScenario
from app.tests.test_demo_seed import demo_engine as demo_engine
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

NOW = datetime(2026, 10, 5, 8, 20, tzinfo=UTC)
candidate = installed


class ProjectionFixture:
    """TOOL_ONLY ORM objects and a Session spec; no actual or invented bank commit."""

    def __init__(self) -> None:
        self.engine = cast(Engine, object())
        self.owner = uuid4()
        self.key = "TOOL_ONLY-original-key"
        self.bank_payload = {"TOOL_ONLY": "original request bytes"}
        self.action_payload = {"bank_request": self.bank_payload}
        self.initial = {"idempotency_key": self.key, "TOOL_ONLY": "pure"}
        self.run = DecisionRun(
            id=uuid4(),
            user_id=self.owner,
            trigger_type="SAFETY_RECOVERY",
            idempotency_key="recovery:" + configuration_hash({"key": self.key}),
            input_snapshot=self.initial,
            snapshot_hash=configuration_hash(self.initial),
        )
        self.action = ActionPlan(
            id=uuid4(),
            user_id=self.owner,
            decision_run_id=self.run.id,
            request=self.action_payload,
            request_hash=configuration_hash(self.action_payload),
        )
        self.request = SimulatedBankRedemption(
            id=uuid4(),
            user_id=self.owner,
            action_plan_id=self.action.id,
            status="SETTLED",
            settled_at=NOW,
            request=self.bank_payload,
            request_hash=configuration_hash(self.bank_payload),
        )
        self.session = Mock(spec=Session)
        self.session.get_bind.return_value = self.engine
        self.session.get.side_effect = lambda kind, identity: (
            self.action if kind is ActionPlan else self.run
        )
        self.original = Mock(return_value={"TOOL_ONLY": "original callable forwarded"})

    def invoke(self) -> Any:
        return recovery.__dict__["project_request"](cast(Session, self.session), self.request, NOW)


def test_target_original_key_fails_only_projection_and_restores_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = ProjectionFixture()
    monkeypatch.setattr(recovery, "project_request", fixture.original)
    snapshot = copy.deepcopy(fixture.request.request)
    with candidate._fault(
        "FAIL_APPLICATION_PROJECTION",
        "RUN_RECOVERY",
        fixture.engine,
        fixture.owner,
        {"idempotency_key": fixture.key},
    ):
        with pytest.raises(PolicyLifecycleError) as error:
            fixture.invoke()
        assert error.value.code == "INVALID_SCENARIO_INPUT"
        assert error.value.message == "SIMULATED_APPLICATION_PROJECTION_INTERRUPTION"
        fixture.original.assert_not_called()
    assert recovery.__dict__["project_request"] is fixture.original
    assert candidate._FAULT_CONTEXT.get() is None
    assert fixture.request.request == snapshot and fixture.request.status == "SETTLED"


@pytest.mark.parametrize(
    "mismatch",
    [
        "engine",
        "user",
        "marker",
        "key",
        "run_key",
        "run_owner",
        "action_owner",
        "run_kind",
        "snapshot_hash",
        "action_hash",
        "bank_hash",
        "bank_payload",
        "missing_action",
        "missing_run",
        "accepted",
        "unknown",
        "missing_settlement",
        "not_session",
        "not_request",
    ],
)
def test_unrelated_context_or_nonsettled_original_forwards_exact_callable(
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
) -> None:
    fixture = ProjectionFixture()
    monkeypatch.setattr(recovery, "project_request", fixture.original)
    with candidate._fault(
        "FAIL_APPLICATION_PROJECTION",
        "RUN_RECOVERY",
        fixture.engine,
        fixture.owner,
        {"idempotency_key": fixture.key},
    ):
        if mismatch == "engine":
            fixture.session.get_bind.return_value = object()
        elif mismatch == "user":
            fixture.request.user_id = uuid4()
        elif mismatch == "marker":
            candidate._FAULT_CONTEXT.set(object())
        elif mismatch == "key":
            fixture.run.input_snapshot = {"idempotency_key": "different-original"}
            fixture.run.snapshot_hash = configuration_hash(fixture.run.input_snapshot)
        elif mismatch == "run_key":
            fixture.run.idempotency_key = "different-original"
        elif mismatch == "run_owner":
            fixture.run.user_id = uuid4()
        elif mismatch == "action_owner":
            fixture.action.user_id = uuid4()
        elif mismatch == "run_kind":
            fixture.run.trigger_type = "OTHER"
        elif mismatch == "snapshot_hash":
            fixture.run.snapshot_hash = "0" * 64
        elif mismatch == "action_hash":
            fixture.action.request_hash = "0" * 64
        elif mismatch == "bank_hash":
            fixture.request.request_hash = "0" * 64
        elif mismatch == "bank_payload":
            fixture.action.request = {"bank_request": {"TOOL_ONLY": "other"}}
            fixture.action.request_hash = configuration_hash(fixture.action.request)
        elif mismatch in {"missing_action", "missing_run"}:
            fixture.session.get.side_effect = lambda kind, identity: (
                None
                if kind is (ActionPlan if mismatch == "missing_action" else DecisionRun)
                else fixture.action
            )
        elif mismatch in {"accepted", "unknown"}:
            fixture.request.status = mismatch.upper()
        elif mismatch == "missing_settlement":
            fixture.request.settled_at = None
        elif mismatch == "not_session":
            recovery.__dict__["project_request"](cast(Session, object()), fixture.request, NOW)
            fixture.original.assert_called_once()
            return
        elif mismatch == "not_request":
            recovery.__dict__["project_request"](
                cast(Session, fixture.session), cast(SimulatedBankRedemption, object()), NOW
            )
            fixture.original.assert_called_once()
            return
        actual = fixture.invoke()
        assert actual == {"TOOL_ONLY": "original callable forwarded"}
        fixture.original.assert_called_once_with(fixture.session, fixture.request, NOW)
    assert candidate._FAULT_CONTEXT.get() is None


def test_concurrent_thread_without_marker_is_not_faulted(monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = ProjectionFixture()
    monkeypatch.setattr(recovery, "project_request", fixture.original)
    with candidate._fault(
        "FAIL_APPLICATION_PROJECTION",
        "RUN_RECOVERY",
        fixture.engine,
        fixture.owner,
        {"idempotency_key": fixture.key},
    ):
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(fixture.invoke).result(timeout=5)
        assert result == {"TOOL_ONLY": "original callable forwarded"}
        with pytest.raises(PolicyLifecycleError):
            fixture.invoke()
    fixture.original.assert_called_once()


def test_default_none_does_not_patch_read_key_or_change_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = ProjectionFixture()
    monkeypatch.setattr(recovery, "project_request", fixture.original)
    with candidate._fault("NONE", "RUN_RECOVERY", fixture.engine, fixture.owner):
        assert candidate._FAULT_CONTEXT.get() is None
        assert recovery.__dict__["project_request"] is fixture.original
        fixture.invoke()
        fixture.session.get.assert_not_called()
    fixture.original.assert_called_once()


@pytest.mark.parametrize("extra", ["amount_cents", "principal_id", "user_id", "success", "clock"])
def test_recovery_fault_keeps_original_small_input_and_rejects_overrides(
    monkeypatch: pytest.MonkeyPatch,
    extra: str,
) -> None:
    fixture = ProjectionFixture()
    monkeypatch.setattr(recovery, "project_request", fixture.original)
    with pytest.raises(ValueError):
        with candidate._fault(
            "FAIL_APPLICATION_PROJECTION",
            "RUN_RECOVERY",
            fixture.engine,
            fixture.owner,
            {"idempotency_key": fixture.key, extra: True},
        ):
            pytest.fail("Extended inputs must not enter fault context")
    assert (
        recovery.__dict__["project_request"] is fixture.original
        and candidate._FAULT_CONTEXT.get() is None
    )


@pytest.mark.parametrize(
    "kind", ["RUN_RECOVERY", "EXECUTE_ACTION", "EXTERNAL_FACT", "READ_ACTION", "DECLARE_POLICY"]
)
@pytest.mark.parametrize("fault", ["NONE", "DROP_BANK_RESPONSE", "FAIL_APPLICATION_PROJECTION"])
def test_explicit_candidate_fault_matrix_is_not_a_generic_new_fault(kind: str, fault: str) -> None:
    admitted = (
        fault == "NONE"
        or kind == "EXECUTE_ACTION"
        or (kind in {"RUN_RECOVERY", "EXTERNAL_FACT"} and fault == "FAIL_APPLICATION_PROJECTION")
    )
    values = {"step_id": "TOOL_ONLY", "kind": kind, "at": NOW, "inputs": {}, "fault": fault}
    if admitted:
        assert types_candidate.ScenarioStep.model_validate(values).fault == fault
    else:
        with pytest.raises(ValueError):
            types_candidate.ScenarioStep.model_validate(values)


def require_installed() -> None:
    assert "inputs" in inspect.signature(installed._fault).parameters, (
        "UNAPPLIED candidate; actual PG must fail closed"
    )
    ScenarioStep(
        step_id="actual-guard",
        kind="RUN_RECOVERY",
        at=NOW,
        inputs={"idempotency_key": "guard"},
        fault="FAIL_APPLICATION_PROJECTION",
    )


def physical_snapshot(engine: Engine) -> bytes:
    with Session(engine) as session, session.begin():
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        session.execute(text("SET TRANSACTION READ ONLY"))
        tables = {
            table.name: [
                dict(row) for row in session.execute(select(table).order_by(table.c.id)).mappings()
            ]
            for table in Base.metadata.sorted_tables
        }
        tables["alembic_version"] = [
            dict(row)
            for row in session.execute(
                text("SELECT version_num FROM alembic_version ORDER BY version_num")
            ).mappings()
        ]
        assert len(tables) == 24
        return json.dumps(tables, sort_keys=True, default=str, separators=(",", ":")).encode()


def real_chain(engine: Engine) -> DashboardScenario:
    # Original real service path: actual facts, purchases and goal projections; no row edits.
    chain = DashboardScenario(engine)
    chain.initialize()
    for stage in STAGES[1:8]:
        chain.advance(stage)
    return chain


@pytest.mark.integration
def test_installed_original_bank_commit_then_projection_failure_same_key_recovery(
    demo_engine: Engine,
) -> None:
    require_installed()
    chain = real_chain(demo_engine)
    key = "C04-RELATED-RISK-NOT-FORMAL-" + uuid4().hex
    with Session(demo_engine) as session:
        epoch = current_audit_epoch(session, DEMO_USER_ID)
        assert epoch
        epoch_id = epoch.id
    at = chain.now + timedelta(seconds=1)
    scenario = Scenario(
        scenario_id="C04-risk-only",
        purpose="DEVELOPMENT",
        dataset_id="unfrozen-risk",
        family_id="C04-direct-fault-risk-not-formal",
        initial_state=InitialState(expected_epoch_id=epoch_id),
        steps=[
            ScenarioStep(
                step_id="failed-original-projection",
                kind="RUN_RECOVERY",
                at=at,
                inputs={"idempotency_key": key},
                fault="FAIL_APPLICATION_PROJECTION",
                expected_error=ExpectedScenarioError(
                    code="INVALID_SCENARIO_INPUT", status_code=409
                ),
            )
        ],
    )
    result = installed.ScenarioRunner(demo_engine, DEMO_USER_ID).run(scenario)
    assert result.status == "EXECUTED" and result.steps[0]["expected_error_matched"] is True
    assert "result" not in result.steps[0]
    with Session(demo_engine) as session:
        run = session.scalar(
            select(DecisionRun).where(
                DecisionRun.user_id == DEMO_USER_ID,
                DecisionRun.idempotency_key == "recovery:" + configuration_hash({"key": key}),
            )
        )
        assert run
        run_id, snapshot_hash = run.id, run.snapshot_hash
        actions = list(
            session.scalars(select(ActionPlan).where(ActionPlan.decision_run_id == run_id))
        )
        assert len(actions) == 1
        action_id, action_hash = actions[0].id, actions[0].request_hash
        bank = session.scalar(
            select(SimulatedBankRedemption).where(
                SimulatedBankRedemption.action_plan_id == action_id
            )
        )
        assert bank and bank.status == "SETTLED" and bank.settled_at is not None
        bank_id, bank_hash = bank.id, bank.request_hash
        legs = [
            row_copy(row)
            for row in session.scalars(
                select(SimulatedBankPosting)
                .where(SimulatedBankPosting.redemption_id == bank_id)
                .order_by(SimulatedBankPosting.id)
            )
        ]
        assert len(legs) == 2 and sum(int(row["delta_cents"]) for row in legs) == 0
        assert (
            session.scalar(select(ActionReceipt).where(ActionReceipt.action_plan_id == action_id))
            is None
        )
        assert verify_audit_chain(session, DEMO_USER_ID, epoch_id).status == "VALID"
    before_read = physical_snapshot(demo_engine)
    with Session(demo_engine) as session:
        unresolved = get_recovery_run(session, DEMO_USER_ID, run_id, at)
    assert unresolved.status == "RECONCILIATION_REQUIRED"
    assert (
        unresolved.actions[0].bank_request_id == bank_id
        and unresolved.actions[0].receipt_id is None
    )
    assert physical_snapshot(demo_engine) == before_read
    replay = run_recovery(demo_engine, DEMO_USER_ID, key, at + timedelta(seconds=1))
    assert replay.run_id == run_id and replay.actions[0].action_id == action_id
    assert replay.actions[0].bank_request_id == bank_id and replay.actions[0].receipt_id is not None
    with Session(demo_engine) as session:
        old_run, old_action, old_bank = (
            session.get(DecisionRun, run_id),
            session.get(ActionPlan, action_id),
            session.get(SimulatedBankRedemption, bank_id),
        )
        assert old_run and old_action and old_bank
        assert (old_run.snapshot_hash, old_action.request_hash, old_bank.request_hash) == (
            snapshot_hash,
            action_hash,
            bank_hash,
        )
        after_legs = [
            row_copy(row)
            for row in session.scalars(
                select(SimulatedBankPosting)
                .where(SimulatedBankPosting.redemption_id == bank_id)
                .order_by(SimulatedBankPosting.id)
            )
        ]
        assert after_legs == legs
        receipts = list(
            session.scalars(select(ActionReceipt).where(ActionReceipt.action_plan_id == action_id))
        )
        assert len(receipts) == 1 and receipts[0].fee_cents == receipts[0].loss_cents == 0
        assert verify_audit_chain(session, DEMO_USER_ID, epoch_id).status == "VALID"
    final = physical_snapshot(demo_engine)
    assert run_recovery(demo_engine, DEMO_USER_ID, key, at + timedelta(seconds=2)).run_id == run_id
    assert physical_snapshot(demo_engine) == final


@pytest.mark.integration
def test_installed_other_original_key_in_same_engine_context_is_not_interrupted(
    demo_engine: Engine,
) -> None:
    require_installed()
    chain = real_chain(demo_engine)
    original_projection = recovery.__dict__["project_request"]
    with cast(Any, installed._fault)(
        "FAIL_APPLICATION_PROJECTION",
        "RUN_RECOVERY",
        demo_engine,
        DEMO_USER_ID,
        {"idempotency_key": "bound-different-original-key"},
    ):
        actual = run_recovery(
            demo_engine,
            DEMO_USER_ID,
            "actual-unrelated-original-key",
            chain.now + timedelta(seconds=1),
        )
    assert actual.actions and all(
        item.bank_status == "SETTLED" and item.receipt_id is not None for item in actual.actions
    )
    assert recovery.__dict__["project_request"] is original_projection
