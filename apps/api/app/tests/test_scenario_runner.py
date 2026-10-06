"""Scenario orchestration risks exercised against owned real PostgreSQL databases."""

from datetime import timedelta
from uuid import UUID

import pytest
from app.db.models import (
    Account,
    ActionPlan,
    ActionReceipt,
    BankOperation,
    ExternalBankFact,
    PolicyVersion,
)
from app.domain.external_bank_fact_types import ExternalFactRequest
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import current_audit_epoch, verify_audit_chain
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, seed_demo
from app.services.execution import execute_action
from app.services.scenario_runner import ScenarioRunner
from app.services.scenario_types import (
    ExpectedScenarioError,
    InitialState,
    Scenario,
    ScenarioRPC,
    ScenarioStep,
)
from app.tests.test_boundary_service import boundary_engine as boundary_engine
from app.tests.test_demo_seed import database_snapshot
from app.tests.test_demo_seed import demo_engine as demo_engine
from app.tests.test_fixed_purchase_clock import NOW, prepare_fixed
from app.tests.test_recovery_receipt_integrity import settled
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def epoch(engine: Engine) -> UUID:
    with Session(engine) as session:
        actual = current_audit_epoch(session, DEMO_USER_ID)
        assert actual is not None
        return actual.id


def scenario(engine: Engine, step: ScenarioStep) -> Scenario:
    return Scenario(
        scenario_id="actual-risk-case",
        purpose="DEVELOPMENT",
        dataset_id="w1-risk-tests",
        family_id="integration-not-frozen",
        initial_state=InitialState(expected_epoch_id=epoch(engine)),
        steps=[step],
    )


def test_json_bank_fact_projection_interruption_and_retry_keep_original_bank_legs(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    with Session(demo_engine) as session:
        account_id = session.scalars(
            select(Account.id)
            .where(Account.user_id == DEMO_USER_ID, Account.account_type == "CASH")
            .order_by(Account.id)
        ).first()
        assert account_id is not None
    request = ExternalFactRequest(
        user_id=DEMO_USER_ID,
        idempotency_key="scenario-original-income",
        external_ref="scenario-original-income",
        kind="INCOME",
        account_id=account_id,
        amount_cents=10_000,
        counterparty_ref="payroll",
        occurred_at=NOW,
    )
    step = ScenarioStep(
        step_id="income",
        kind="EXTERNAL_FACT",
        at=NOW,
        inputs=request.model_dump(mode="json"),
        fault="FAIL_APPLICATION_PROJECTION",
    )
    runner = ScenarioRunner(demo_engine, DEMO_USER_ID)
    first = runner.run(scenario(demo_engine, step))
    assert (
        first.status == "EXECUTED"
    )  # Actual API result exposes UNKNOWN, not successful projection.
    original = first.steps[0]["result"]
    assert original["bank_status"] == "SETTLED" and original["projection_status"] == "UNKNOWN"
    assert len(original["economic_posting_ids"]) == 2
    with Session(demo_engine) as session:
        fact = session.get(ExternalBankFact, UUID(original["external_fact_id"]))
        assert fact is not None
        identity = (fact.request_hash, fact.bank_result_hash, fact.bank_result_canonical_text)
    replay = step.model_copy(update={"fault": "NONE", "at": NOW + timedelta(seconds=1)})
    second = runner.run(scenario(demo_engine, replay))
    actual = second.steps[0]["result"]
    assert actual["projection_status"] == "PROJECTED"
    assert actual["external_fact_id"] == original["external_fact_id"]
    assert actual["economic_posting_ids"] == original["economic_posting_ids"]
    with Session(demo_engine) as session:
        fact = session.get(ExternalBankFact, UUID(original["external_fact_id"]))
        assert fact is not None
        assert (
            fact.request_hash,
            fact.bank_result_hash,
            fact.bank_result_canonical_text,
        ) == identity
        assert verify_audit_chain(session, DEMO_USER_ID).status == "VALID"


def test_bank_response_loss_is_unknown_after_real_commit_then_replays_same_action(
    demo_engine: Engine,
) -> None:
    prepared = prepare_fixed(demo_engine)
    step = ScenarioStep(
        step_id="execute",
        kind="EXECUTE_ACTION",
        at=NOW + timedelta(seconds=3),
        inputs={"action_id": str(prepared.action_id)},
        fault="DROP_BANK_RESPONSE",
    )
    result = ScenarioRunner(demo_engine, DEMO_USER_ID).run(scenario(demo_engine, step))
    assert (
        result.status == "FAILED"
        and result.steps[0]["error"]["code"] == "SIMULATED_BANK_RESPONSE_LOST"
    )
    with Session(demo_engine) as session:
        action = session.get(ActionPlan, prepared.action_id)
        bank = session.get(BankOperation, prepared.action_id)
        assert action is not None and action.status == "UNKNOWN"
        assert bank is not None and bank.status == "SETTLED"
        bank_original = (bank.request_hash, bank.settled_at)
        assert (
            session.scalars(
                select(ActionReceipt).where(ActionReceipt.action_plan_id == action.id)
            ).all()
            == []
        )
    actual = execute_action(
        demo_engine, DEMO_USER_ID, prepared.action_id, NOW + timedelta(seconds=4)
    )
    assert actual.status == "SUCCEEDED" and actual.receipt is not None
    assert actual.effect_hash == prepared.effect_hash
    with Session(demo_engine) as session:
        bank = session.get(BankOperation, prepared.action_id)
        assert bank is not None and (bank.request_hash, bank.settled_at) == bank_original
        assert verify_audit_chain(session, DEMO_USER_ID).status == "VALID"


def test_malformed_step_and_readonly_snapshot_preserve_all_business_tables(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    original = database_snapshot(demo_engine)
    runner = ScenarioRunner(demo_engine, DEMO_USER_ID)
    invalid = ScenarioStep(
        step_id="bad-id", kind="EXECUTE_ACTION", at=NOW, inputs={"action_id": "invalid"}
    )
    result = runner.run(scenario(demo_engine, invalid))
    assert result.status == "FAILED" and result.steps[0]["error"]["code"] == "INVALID_SCENARIO_STEP"
    read = runner.rpc(
        ScenarioRPC(
            scenario_id="readonly",
            purpose="DEVELOPMENT",
            operation="snapshot",
            expected_epoch_id=epoch(demo_engine),
        ),
        NOW,
    )
    assert read["table_count"] == 23 and sum(read["rows_by_table"].values()) > 0
    assert database_snapshot(demo_engine) == original


def test_actual_template_reference_and_registered_refusal_continue_original_confirmation(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    initial_epoch = epoch(demo_engine)
    original = database_snapshot(demo_engine)
    previous = {"$ref": {"step_id": "prepare", "pointer": "/result/proposal_id"}}
    case = Scenario(
        scenario_id="actual-template-pointer-and-refusal",
        purpose="DEVELOPMENT",
        dataset_id="w1-risk-tests",
        family_id="integration-not-frozen",
        initial_state=InitialState(expected_epoch_id=initial_epoch),
        steps=[
            ScenarioStep(
                step_id="prepare",
                kind="PREPARE_TEMPLATE",
                at=NOW,
                inputs={"kind": "LIQUID_ASSET", "expected_epoch_id": str(initial_epoch)},
            ),
            ScenarioStep(
                step_id="bad-review",
                kind="CONFIRM_TEMPLATE",
                at=NOW + timedelta(seconds=1),
                inputs={"proposal_id": previous, "reviewed_hash": "0" * 64, "accepted": True},
                expected_error=ExpectedScenarioError(code="REVIEW_MISMATCH", status_code=409),
            ),
            ScenarioStep(
                step_id="confirm",
                kind="CONFIRM_TEMPLATE",
                at=NOW + timedelta(seconds=2),
                inputs={
                    "proposal_id": previous,
                    "reviewed_hash": {
                        "$ref": {"step_id": "prepare", "pointer": "/result/configuration_hash"}
                    },
                    "accepted": True,
                },
            ),
        ],
        expected_properties=["BANK_LEDGER_VALID", "AUDIT_VALID"],
    )
    result = ScenarioRunner(demo_engine, DEMO_USER_ID).run(case)
    assert result.status == "EXECUTED" and len(result.steps) == 3
    assert result.steps[1]["expected_error_matched"] is True
    assert result.steps[1]["error"]["code"] == "REVIEW_MISMATCH"
    assert (
        result.steps[2]["resolved_inputs"]["proposal_id"]
        == result.steps[0]["result"]["proposal_id"]
    )
    with Session(demo_engine) as session:
        versions = session.scalars(
            select(PolicyVersion).where(PolicyVersion.user_id == DEMO_USER_ID)
        ).all()
        assert (
            len(versions) == 1
            and configuration_hash(versions[0].configuration)
            == result.steps[0]["result"]["configuration_hash"]
        )
        assert session.scalar(select(BankOperation.id).limit(1)) is None
        assert session.scalar(select(ActionPlan.id).limit(1)) is None
        assert verify_audit_chain(session, DEMO_USER_ID).status == "VALID"
    assert database_snapshot(demo_engine) != original


@pytest.mark.parametrize("mature", [False, True])
def test_legacy_receipt_rpc_is_readonly_and_opaque_purchase_cannot_prove_all_actions_safe(
    boundary_engine: Engine, mature: bool
) -> None:
    request_id, receipt_id = settled(boundary_engine, mature=mature)
    with Session(boundary_engine) as session:
        receipt = session.get(ActionReceipt, receipt_id)
        assert receipt is not None
        action_id = receipt.action_plan_id
    original = database_snapshot(boundary_engine)
    runner = ScenarioRunner(boundary_engine, DEMO_USER_ID)
    result = runner.rpc(
        ScenarioRPC(
            scenario_id="legacy-real",
            purpose="DEVELOPMENT",
            operation="verify_legacy_recovery",
            expected_epoch_id=epoch(boundary_engine),
            action_id=action_id,
        ),
        SEED_AS_OF,
    )
    assert result["verified"] is True and result["bank_request_id"] == str(request_id)
    assert result["receipt_id"] == str(receipt_id) and result["receipt"]["action_plan_id"] == str(
        action_id
    )
    assert result["receipt"]["fee_cents"] == result["receipt"]["loss_cents"] == 0
    properties = runner.check_properties(["NO_LOSS_AUTOMATIC"], epoch(boundary_engine), SEED_AS_OF)
    # settled() retains its old synthetic acquisition stub with no execution or
    # bank_request protocol. A verified redemption does not prove that acquisition.
    # The global property must refuse incomplete history without rewriting it.
    assert not properties[0].passed
    assert properties[0].detail["error_code"] == "INVALID_SCENARIO_INPUT"
    assert "original action protocol" in properties[0].detail["message"]
    assert database_snapshot(boundary_engine) == original
