"""One generated-bf_test risk node; only the root coordinator executes real PG."""

from collections.abc import Iterator
from datetime import datetime
from uuid import UUID

import app.services.execution as execution
import pytest
from app.api.dependencies import get_engine, get_now
from app.db.models import Account, ActionPlan, ActionReceipt, BankOperation, SimulatedBankPosting
from app.domain.external_bank_fact_types import ExternalFactRequest
from app.domain.full_reconciliation import FullReconciliationReport
from app.main import create_app
from app.services.action_contracts import (
    ConfirmActionRequest,
    GoalIntent,
    PrepareActionRequest,
    TransferIntent,
)
from app.services.demo_seed import (
    DEMO_USER_ID,
    SECOND_CASH_EXTERNAL_REF,
    SEED_VERSION,
    seed_demo,
)
from app.services.execution_bank import BankOperationResult, process_operation
from app.services.external_bank_facts import ingest_external_fact
from app.tests.test_demo_seed import demo_engine as demo_engine
from app.tests.test_full_goals_api import confirmed_existing_goal
from app.tests.test_full_projection_api import physical_snapshot
from app.tests.test_goal_api import NOW
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


@pytest.fixture
def reconciliation_client(demo_engine: Engine) -> Iterator[tuple[TestClient, Engine]]:
    # This original private production seed only accepts a brand-new generated local bf_test.
    # Its zero second cash opening precedes genesis; no formal history is reset or relabelled.
    seed_demo(demo_engine, _experiment_seed_extension="MVP503_SECOND_CASH_INITIAL_FACT_V1")
    api = create_app()
    api.dependency_overrides[get_engine] = lambda: demo_engine
    api.dependency_overrides[get_now] = lambda: NOW
    with TestClient(api) as client:
        yield client, demo_engine


def read_original_report(client: TestClient, engine: Engine) -> FullReconciliationReport:
    before = physical_snapshot(engine)
    response = client.get("/api/v1/reconciliation/current")
    assert response.status_code == 200, response.text
    report = FullReconciliationReport.model_validate_json(response.text)
    assert report.user_id == DEMO_USER_ID and report.as_of == NOW
    assert report.read_only and report.simulation and not report.economic_verified
    assert (
        not report.grants_authority and not report.repairs_performed and not report.executes_funds
    )
    assert len(report.inventory) == 8 and all(row.complete for row in report.inventory)
    assert all(row.actual_count == row.captured_count for row in report.inventory)
    assert (
        physical_snapshot(engine) == before
    )  # Dynamic physical inventory includes Alembic/new tables.
    return report


def test_actual_reconciliation_preserves_bank_truth_unknown_and_original_key_without_repair(
    reconciliation_client: tuple[TestClient, Engine], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine = reconciliation_client
    initial = read_original_report(client, engine)
    assert initial.state == "MATCHED" and initial.bank_ledger_verified
    assert initial.current_application_projection_matched and initial.audit.status == "VALID"
    assert all(row.state == "MATCHED" for row in initial.account_cash + initial.position_principals)
    before_invalid = physical_snapshot(engine)
    for key in ("user_id", "now", "bank_balance_cents", "receipt", "economic_verified", "repair"):
        assert client.get("/api/v1/reconciliation/current", params={key: "fake"}).status_code == 422
    assert client.post("/api/v1/reconciliation/current", json={"repair": True}).status_code == 405
    assert physical_snapshot(engine) == before_invalid

    with Session(engine) as session:
        source = session.scalar(
            select(Account).where(Account.external_ref == SEED_VERSION + ":account:cash")
        )
        target = session.scalar(
            select(Account).where(Account.external_ref == SECOND_CASH_EXTERNAL_REF)
        )
        assert source is not None and target is not None
        source_id, target_id = source.id, target.id

    # Real production external bank income and public goal creation, not fabricated ledger proofs.
    income = ingest_external_fact(
        engine,
        DEMO_USER_ID,
        ExternalFactRequest(
            user_id=DEMO_USER_ID,
            idempotency_key="FULL607_development_actual_income",
            external_ref="FULL607_development_payroll",
            kind="INCOME",
            account_id=source_id,
            amount_cents=600000,
            counterparty_ref="payroll",
            occurred_at=NOW,
        ),
        NOW,
    )
    assert income.bank_status == "SETTLED" and income.projection_status == "PROJECTED"
    goal_id_text, _ = confirmed_existing_goal(client, engine)
    goal_id = UUID(goal_id_text)
    allocated = execution.prepare_action(
        engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="FULL607_development_goal",
            intent=GoalIntent(kind="allocate_goal", goal_id=goal_id),
        ),
        NOW,
    )
    assert allocated.effect.amount_cents > 0 and allocated.effect.goal_id == goal_id
    if allocated.autonomy_level == "ASK_ONCE":
        execution.confirm_action(
            engine,
            DEMO_USER_ID,
            allocated.action_id,
            ConfirmActionRequest(effect_hash=allocated.effect_hash, accepted=True),
            NOW,
        )
    goal_result = execution.execute_action(engine, DEMO_USER_ID, allocated.action_id, NOW)
    assert goal_result.status == "SUCCEEDED" and goal_result.receipt is not None
    with_goal = read_original_report(client, engine)
    assert with_goal.state == "MATCHED" and with_goal.audit.status == "VALID"
    ownership = next(row for row in with_goal.goal_ownership if row.goal_id == goal_id)
    assert (
        ownership.current_ownership_proof_verified
        and ownership.allocated_cents == allocated.effect.amount_cents
    )
    assert ownership.cash.state == ownership.principal.state == "MATCHED"
    assert (
        ownership.cash.bank_cents == ownership.allocated_cents
        and ownership.principal.bank_cents == 0
    )
    assert next(
        row for row in with_goal.actions if row.action_id == allocated.action_id
    ).service_receipt_verified

    transfer = execution.prepare_action(
        engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="FULL607_development_transfer",
            intent=TransferIntent(
                kind="transfer_internal",
                source_account_id=source_id,
                destination_account_id=target_id,
                amount_cents=47143,
            ),
        ),
        NOW,
    )
    assert transfer.autonomy_level == "ASK_ONCE" and transfer.receipt is None
    execution.confirm_action(
        engine,
        DEMO_USER_ID,
        transfer.action_id,
        ConfirmActionRequest(effect_hash=transfer.effect_hash, accepted=True),
        NOW,
    )
    original_bank = process_operation

    def lost_after_commit(
        engine: Engine, user: UUID, action: UUID, now: datetime
    ) -> BankOperationResult:
        original_bank(engine, user, action, now)
        raise RuntimeError("FULL607 actual bank committed; response intentionally lost")

    monkeypatch.setattr(execution, "process_operation", lost_after_commit)
    with pytest.raises(RuntimeError, match="actual bank committed"):
        execution.execute_action(engine, DEMO_USER_ID, transfer.action_id, NOW)
    with Session(engine) as session:
        action = session.get(ActionPlan, transfer.action_id)
        operation = session.get(BankOperation, transfer.action_id)
        assert action is not None and action.status == "UNKNOWN"
        assert operation is not None and operation.status == "SETTLED"
        original_key = action.idempotency_key
        assert operation.idempotency_key == original_key
        assert action.request["execution"]["effect_hash"] == transfer.effect_hash
        assert (
            session.scalar(
                select(ActionReceipt.id).where(ActionReceipt.action_plan_id == action.id)
            )
            is None
        )
        posting_ids = set(
            session.scalars(
                select(SimulatedBankPosting.id).where(
                    SimulatedBankPosting.operation_id == operation.id
                )
            )
        )
        assert len(posting_ids) >= 2
    unknown = read_original_report(client, engine)
    actual = next(row for row in unknown.actions if row.action_id == transfer.action_id)
    assert actual.state == "BANK_SETTLED_APPLICATION_UNRESOLVED"
    assert (
        actual.original_action_status == "UNKNOWN"
        and actual.original_idempotency_key == original_key
    )
    assert actual.effect_hash == transfer.effect_hash and actual.actual_executed_cents == 47143
    assert actual.complete_settlement_legs_verified and not actual.service_receipt_verified
    assert set(actual.posting_ids) == posting_ids and actual.receipt_ids == []
    assert unknown.state == "MANUAL_REVIEW_REQUIRED" and unknown.manual_review_required
    assert unknown.bank_ledger_verified and not unknown.current_application_projection_matched
    assert unknown.pending_application_projection_explained
    assert (
        next(row for row in unknown.account_cash if row.entity_id == source_id).difference_cents
        == 47143
    )
    assert (
        next(row for row in unknown.account_cash if row.entity_id == target_id).difference_cents
        == -47143
    )

    # The report did not retry. Explicit original production recovery reuses the same operation/key.
    monkeypatch.setattr(execution, "process_operation", original_bank)
    recovered = execution.execute_action(engine, DEMO_USER_ID, transfer.action_id, NOW)
    assert recovered.status == "SUCCEEDED" and recovered.receipt is not None
    assert (
        recovered.effect_hash == transfer.effect_hash and recovered.receipt.executed_cents == 47143
    )
    settled_rows = physical_snapshot(engine)
    replay = execution.execute_action(engine, DEMO_USER_ID, transfer.action_id, NOW)
    assert replay.receipt == recovered.receipt and physical_snapshot(engine) == settled_rows
    matched = read_original_report(client, engine)
    assert matched.state == "MATCHED" and matched.current_application_projection_matched
    complete_action = next(row for row in matched.actions if row.action_id == transfer.action_id)
    assert (
        complete_action.state == "SERVICE_RECEIPT_VERIFIED"
        and complete_action.service_receipt_verified
    )
    assert (
        complete_action.bank_operation_ids == [transfer.action_id]
        and set(complete_action.posting_ids) == posting_ids
    )
    with Session(engine) as session:
        assert (
            len(
                list(
                    session.scalars(
                        select(BankOperation).where(
                            BankOperation.action_plan_id == transfer.action_id
                        )
                    )
                )
            )
            == 1
        )

    # Deliberately inconsistent mutable application projection, exclusively in this disposable DB.
    # Independent bank postings/keys/effects/hashes remain the original facts.
    with Session(engine) as session, session.begin():
        source = session.get(Account, source_id)
        receipt = session.get(ActionReceipt, recovered.receipt.receipt_id)
        assert source is not None and receipt is not None
        source.balance_cents += 7
        receipt.executed_cents += 1
    review = read_original_report(client, engine)
    assert review.state == "MANUAL_REVIEW_REQUIRED" and review.manual_review_required
    assert review.bank_ledger_verified and not review.current_application_projection_matched
    assert (
        next(row for row in review.account_cash if row.entity_id == source_id).difference_cents == 7
    )
    refused_receipt = next(row for row in review.actions if row.action_id == transfer.action_id)
    assert (
        refused_receipt.state == "MANUAL_REVIEW_REQUIRED"
        and not refused_receipt.service_receipt_verified
    )
    assert any(issue.kind == "INTEGRITY" for issue in refused_receipt.issues)
    assert not review.repairs_performed and not review.economic_verified
