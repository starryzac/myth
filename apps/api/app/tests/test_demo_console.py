"""Durable console retries and epoch-bound reset use actual isolated PostgreSQL engines."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.api.dependencies import get_engine, get_now
from app.db.models import (
    Account,
    AuditEpoch,
    AuditEvent,
    EvidenceItem,
    ExternalBankFact,
    PolicyVersion,
    User,
)
from app.domain.demo_identity import DEMO_USER_ID
from app.domain.external_bank_fact_types import ExternalFactRequest
from app.main import create_app
from app.services import demo_console, external_bank_facts
from app.services.audit_chain import current_audit_epoch, verify_audit_chain
from app.services.demo_console_types import DemoCommandView, DemoEventRequest, DemoResetRequest
from app.services.demo_seed import seed_demo
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.scenario_runner import ScenarioRunner
from app.tests.test_decision_readonly_transaction import _first_user_query
from app.tests.test_demo_seed import database_snapshot
from app.tests.test_demo_seed import demo_engine as demo_engine
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 4, 1, tzinfo=UTC)


def _client(engine: Engine, clock: list[datetime]) -> TestClient:
    api = create_app()
    api.dependency_overrides[get_engine] = lambda: engine
    api.dependency_overrides[get_now] = lambda: clock[0]
    return TestClient(api)


def _epoch_id(engine: Engine) -> UUID:
    with Session(engine) as session:
        epoch = current_audit_epoch(session, DEMO_USER_ID)
        assert epoch is not None
        return epoch.id


def _confirm_template(client: TestClient, clock: list[datetime], epoch_id: UUID, kind: str) -> None:
    response = client.post(
        f"/api/v1/demo/templates/{kind}/prepare", json={"expected_epoch_id": str(epoch_id)}
    )
    assert response.status_code == 200, response.text
    template = response.json()
    assert template["simulation"] is True
    clock[0] += timedelta(seconds=1)
    confirmation = client.post(
        f"/api/v1/policy-proposals/{template['proposal_id']}/confirm",
        json={"accepted": True, "reviewed_hash": template["configuration_hash"]},
    )
    assert confirmation.status_code == 200, confirmation.text
    clock[0] += timedelta(seconds=1)


def test_completed_salary_reset_archives_actual_actions_and_retains_original_events(
    demo_engine: Engine,
) -> None:
    """Regression of the real Edge second-case reset, through original services."""
    seed_demo(demo_engine)
    first_epoch = _epoch_id(demo_engine)
    clock = [datetime.now(UTC)]
    first = demo_console.reset_demo(
        demo_engine,
        DEMO_USER_ID,
        DemoResetRequest(
            reset_key="salary-reset-initial", expected_epoch_id=first_epoch, accepted=True
        ),
        clock[0],
    )
    epoch = first.epoch_id
    with _client(demo_engine, clock) as client:
        for kind in ["CAR_GOAL", "LIQUID_ASSET", "FIXED_ASSET"]:
            _confirm_template(client, clock, epoch, kind)
        goal = client.post(
            "/api/v1/demo/events",
            json={"event_kind": "CREATE_CAR_GOAL", "expected_epoch_id": str(epoch)},
        )
        assert goal.status_code == 200 and goal.json()["status"] == "COMPLETED", goal.text
        clock[0] += timedelta(seconds=1)
        salary = client.post(
            "/api/v1/demo/events",
            json={"event_kind": "SALARY_RECEIVED", "expected_epoch_id": str(epoch)},
        )
        assert salary.status_code == 200 and salary.json()["status"] == "COMPLETED", salary.text
        assert len(salary.json()["actions"]) == 2
        assert all(action["receipt"] for action in salary.json()["actions"])
    with Session(demo_engine) as session:
        originals = {row.id: row.canonical_text for row in session.scalars(select(AuditEvent))}
        assert verify_audit_chain(session, DEMO_USER_ID, epoch).status == "VALID"
    # Call the same original reset service directly, preserving an actual stack on error.
    reset = demo_console.reset_demo(
        demo_engine,
        DEMO_USER_ID,
        DemoResetRequest(
            reset_key="salary-reset-after-completion", expected_epoch_id=epoch, accepted=True
        ),
        clock[0] + timedelta(seconds=1),
    )
    assert reset.epoch_id != epoch
    with Session(demo_engine) as session:
        sealed = session.get(AuditEpoch, epoch)
        assert sealed is not None and sealed.status == "SEALED"
        assert verify_audit_chain(session, DEMO_USER_ID, epoch).status == "VALID"
        assert verify_audit_chain(session, DEMO_USER_ID, reset.epoch_id).status == "VALID"
        for identity, original in originals.items():
            row = session.get(AuditEvent, identity)
            assert row is not None and row.canonical_text == original


def test_fixed_preset_records_real_liquidity_expense_and_requires_exact_loss_consent(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    epoch_id, clock = _epoch_id(demo_engine), [NOW]
    with _client(demo_engine, clock) as client:
        _confirm_template(client, clock, epoch_id, "FIXED_ASSET")
        body = {"event_kind": "FIXED_EARLY_WITHDRAWAL", "expected_epoch_id": str(epoch_id)}
        response = client.post("/api/v1/demo/events", json=body)
        assert response.status_code == 200, response.text
        original = response.json()
        assert original["status"] == "WAITING_ACTION_CONFIRMATION", original
        fact = original["fact"]
        assert fact["bank_status"] == "SETTLED" and fact["projection_status"] == "PROJECTED"
        assert len(set(fact["economic_posting_ids"])) == 2 and fact["transaction_id"]
        with Session(demo_engine) as session:
            bank_fact = session.get(ExternalBankFact, UUID(fact["external_fact_id"]))
            assert bank_fact is not None and bank_fact.kind == "CONSUMPTION"
            assert bank_fact.amount_cents > 0
            bank_identity = (bank_fact.request_hash, bank_fact.bank_result_hash)
        purchase, redemption = original["actions"]
        assert purchase["effect"]["action_type"] == "PURCHASE_ASSET"
        assert purchase["receipt"] and purchase["receipt"]["fee_cents"] == 0
        assert purchase["receipt"]["loss_cents"] == 0
        assert redemption["effect"]["action_type"] == "REDEEM_ASSET"
        assert redemption["autonomy_level"] == "ASK_ONCE" and redemption["receipt"] is None
        assert redemption["effect"]["loss_cents"] > 0 and redemption["effect"]["quote_id"]
        before_consent = database_snapshot(demo_engine)
        refused = client.post(f"/api/v1/actions/{redemption['action_id']}/execute", json={})
        assert refused.status_code == 409, refused.text
        assert database_snapshot(demo_engine) == before_consent
        clock[0] += timedelta(seconds=1)
        confirmed = client.post(
            f"/api/v1/actions/{redemption['action_id']}/confirm",
            json={"accepted": True, "effect_hash": redemption["effect_hash"]},
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["effect_hash"] == redemption["effect_hash"]
        clock[0] += timedelta(seconds=1)
        executed = client.post(f"/api/v1/actions/{redemption['action_id']}/execute", json={})
        assert executed.status_code == 200, executed.text
        receipt = executed.json()["receipt"]
        assert executed.json()["status"] == "SUCCEEDED"
        assert executed.json()["effect_hash"] == redemption["effect_hash"]
        assert receipt["loss_cents"] == redemption["effect"]["loss_cents"]
        completed = client.post("/api/v1/demo/events", json=body)
        assert completed.status_code == 200 and completed.json()["status"] == "COMPLETED"
        assert completed.json()["fact"] == fact
        settled_snapshot = database_snapshot(demo_engine)
        assert client.post("/api/v1/demo/events", json=body).json() == completed.json()
        assert (
            client.get(f"/api/v1/demo/commands/{original['command_id']}").json() == completed.json()
        )
        properties = ScenarioRunner(demo_engine, DEMO_USER_ID).check_properties(
            ["AUDIT_VALID", "BANK_LEDGER_VALID", "NO_LOSS_AUTOMATIC"], epoch_id, clock[0]
        )
        assert all(item.passed for item in properties), properties
        assert database_snapshot(demo_engine) == settled_snapshot
        with Session(demo_engine) as session:
            bank_fact = session.get(ExternalBankFact, UUID(fact["external_fact_id"]))
            assert bank_fact is not None
            assert (bank_fact.request_hash, bank_fact.bank_result_hash) == bank_identity


def test_console_gets_are_readonly_and_templates_only_declare_unconfirmed_inputs(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    epoch_id = _epoch_id(demo_engine)
    with _client(demo_engine, [NOW]) as client:
        before = database_snapshot(demo_engine)
        for path, code in [("presets", 200), ("state", 200), (f"commands/{uuid4()}", 404)]:
            with _first_user_query(demo_engine) as first_queries:
                response = client.get("/api/v1/demo/" + path)
                assert response.status_code == code, response.text
            assert first_queries == [("repeatable read", "on")]
        assert database_snapshot(demo_engine) == before
        financial_before = client.get("/api/v1/accounts/summary").json()
        response = client.post(
            "/api/v1/demo/templates/FIXED_ASSET/prepare", json={"expected_epoch_id": str(epoch_id)}
        )
        assert response.status_code == 200, response.text
        template = response.json()
        assert template["status"] == "PROPOSED" and template["confirmed_policy_id"] is None
        with Session(demo_engine) as session:
            assert session.scalars(select(PolicyVersion)).all() == []
            declaration = session.get(EvidenceItem, UUID(template["evidence_id"]))
            assert declaration is not None and declaration.evidence_level == "USER_DECLARED"
        assert client.get("/api/v1/accounts/summary").json() == financial_before
        original = database_snapshot(demo_engine)
        replay = client.post(
            "/api/v1/demo/templates/FIXED_ASSET/prepare", json={"expected_epoch_id": str(epoch_id)}
        )
        assert replay.status_code == 200 and replay.json() == template
        assert database_snapshot(demo_engine) == original
        for payload in [
            {"event_kind": "SALARY_RECEIVED", "expected_epoch_id": str(uuid4())},
            {
                "event_kind": "SALARY_RECEIVED",
                "expected_epoch_id": str(epoch_id),
                "amount_cents": 10_000_000,
            },
        ]:
            response = client.post("/api/v1/demo/events", json=payload)
            assert response.status_code in {409, 422}
            assert database_snapshot(demo_engine) == original


def test_console_salary_unknown_retry_keeps_original_bank_legs_and_actual_actions(
    demo_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_demo(demo_engine)
    epoch_id, clock = _epoch_id(demo_engine), [NOW]
    with _client(demo_engine, clock) as client:
        for kind in ["CAR_GOAL", "LIQUID_ASSET"]:
            _confirm_template(client, clock, epoch_id, kind)
        response = client.post(
            "/api/v1/demo/events",
            json={"event_kind": "CREATE_CAR_GOAL", "expected_epoch_id": str(epoch_id)},
        )
        assert response.status_code == 200 and response.json()["status"] == "COMPLETED", (
            response.text
        )
        original_project = external_bank_facts._project

        def interrupted(*_: object) -> None:
            raise PolicyLifecycleError(
                "SIMULATED_APPLICATION_INTERRUPTION",
                "Actual bank transaction already committed",
                409,
            )

        monkeypatch.setattr(external_bank_facts, "_project", interrupted)
        clock[0] += timedelta(seconds=1)
        body = {"event_kind": "SALARY_RECEIVED", "expected_epoch_id": str(epoch_id)}
        first = client.post("/api/v1/demo/events", json=body)
        assert first.status_code == 200, first.text
        command = first.json()
        assert command["status"] == "UNKNOWN"
        assert command["fact"]["bank_status"] == "SETTLED"
        assert command["fact"]["projection_status"] == "UNKNOWN"
        fact_id, postings = (
            command["fact"]["external_fact_id"],
            command["fact"]["economic_posting_ids"],
        )
        assert len(postings) == 2
        with Session(demo_engine) as session:
            original = session.get(ExternalBankFact, UUID(fact_id))
            assert original is not None
            request_hash, bank_hash, first_key = (
                original.request_hash,
                original.bank_result_hash,
                original.idempotency_key,
            )
        before_get = database_snapshot(demo_engine)
        assert (
            client.get(f"/api/v1/demo/commands/{command['command_id']}").json()["status"]
            == "UNKNOWN"
        )
        assert database_snapshot(demo_engine) == before_get
        monkeypatch.setattr(external_bank_facts, "_project", original_project)
        clock[0] += timedelta(seconds=1)
        resumed = client.post("/api/v1/demo/events", json=body)
        assert resumed.status_code == 200, resumed.text
        result = resumed.json()
        assert result["command_id"] == command["command_id"]
        assert result["status"] == "COMPLETED", result
        assert result["fact"]["external_fact_id"] == fact_id
        assert result["fact"]["economic_posting_ids"] == postings
        assert len(result["actions"]) == 2 and all(a["receipt"] for a in result["actions"])
        with Session(demo_engine) as session:
            original = session.get(ExternalBankFact, UUID(fact_id))
            assert original is not None
            assert (original.request_hash, original.bank_result_hash, original.idempotency_key) == (
                request_hash,
                bank_hash,
                first_key,
            )
            assert verify_audit_chain(session, DEMO_USER_ID).status == "VALID"
        completed = database_snapshot(demo_engine)
        assert (
            client.post("/api/v1/demo/events", json=body).json()["command_id"]
            == command["command_id"]
        )
        assert database_snapshot(demo_engine) == completed


def test_console_reset_old_key_replay_preserves_later_epoch_and_other_user(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    first_epoch = _epoch_id(demo_engine)
    foreign_id, account_id = uuid4(), uuid4()
    with Session(demo_engine) as session, session.begin():
        session.add(
            User(
                id=foreign_id,
                external_ref=f"foreign:{foreign_id}",
                display_name="Other synthetic tenant",
                timezone="Asia/Shanghai",
                is_simulated=True,
                created_at=NOW,
            )
        )
        session.flush()
        session.add(
            Account(
                id=account_id,
                user_id=foreign_id,
                external_ref="foreign-cash",
                name="Foreign cash",
                account_type="CASH",
                currency="CNY",
                balance_cents=123456,
                observed_at=NOW,
                created_at=NOW,
            )
        )
    with _client(demo_engine, [NOW]) as client:
        command = client.post(
            "/api/v1/demo/events",
            json={"event_kind": "CREATE_CAR_GOAL", "expected_epoch_id": str(first_epoch)},
        )
        assert command.status_code == 200 and command.json()["status"] == "WAITING_TEMPLATE"
        with Session(demo_engine) as session:
            originals = {row.id: row.canonical_text for row in session.scalars(select(AuditEvent))}
        body = {
            "reset_key": "console-first-reset",
            "expected_epoch_id": str(first_epoch),
            "accepted": True,
        }
        first = client.post("/api/v1/demo/reset", json=body)
        assert first.status_code == 200, first.text
        second_epoch = UUID(first.json()["epoch_id"])
        assert second_epoch != first_epoch and first.json()["reset_epoch_id"] == str(second_epoch)
        body2 = {
            "reset_key": "console-second-reset",
            "expected_epoch_id": str(second_epoch),
            "accepted": True,
        }
        second = client.post("/api/v1/demo/reset", json=body2)
        assert second.status_code == 200, second.text
        third_epoch = second.json()["epoch_id"]
        before = database_snapshot(demo_engine)
        replay = client.post("/api/v1/demo/reset", json=body)
        assert replay.status_code == 200, replay.text
        assert replay.json()["epoch_id"] == third_epoch
        assert replay.json()["reset_epoch_id"] == str(second_epoch)
        assert replay.json()["seed_summary"] == first.json()["seed_summary"]
        assert database_snapshot(demo_engine) == before
        stale = client.post("/api/v1/demo/reset", json={**body, "reset_key": "new-stale-reset"})
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "STALE_DEMO_EPOCH"
        assert database_snapshot(demo_engine) == before
        assert (
            client.post(
                "/api/v1/demo/events",
                json={"event_kind": "SALARY_RECEIVED", "expected_epoch_id": str(first_epoch)},
            ).status_code
            == 409
        )
        assert (
            client.get(f"/api/v1/demo/commands/{command.json()['command_id']}").status_code == 404
        )
        assert database_snapshot(demo_engine) == before
        with Session(demo_engine) as session:
            epochs = list(
                session.scalars(
                    select(AuditEpoch)
                    .where(AuditEpoch.user_id == DEMO_USER_ID)
                    .order_by(AuditEpoch.epoch_number)
                )
            )
            assert [e.status for e in epochs] == ["SEALED", "SEALED", "OPEN"]
            for identity, text in originals.items():
                row = session.get(AuditEvent, identity)
                assert row is not None and row.canonical_text == text
            for epoch in epochs:
                assert verify_audit_chain(session, DEMO_USER_ID, epoch.id).status == "VALID"
            foreign = session.get(Account, account_id)
            assert (
                foreign is not None
                and foreign.user_id == foreign_id
                and foreign.balance_cents == 123456
            )


def test_console_rejects_another_policy_action_using_its_reserved_stage_key(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    epoch_id, clock = _epoch_id(demo_engine), [NOW]
    with _client(demo_engine, clock) as client:
        admission = client.post(
            "/api/v1/demo/events",
            json={"event_kind": "SALARY_RECEIVED", "expected_epoch_id": str(epoch_id)},
        )
        assert admission.status_code == 200 and admission.json()["status"] == "WAITING_TEMPLATE"
        command_id = admission.json()["command_id"]
        for kind in ["CAR_GOAL", "LIQUID_ASSET", "FIXED_ASSET"]:
            _confirm_template(client, clock, epoch_id, kind)
        created = client.post(
            "/api/v1/demo/events",
            json={"event_kind": "CREATE_CAR_GOAL", "expected_epoch_id": str(epoch_id)},
        )
        assert created.status_code == 200, created.text
        assert created.json()["status"] == "COMPLETED"
        templates = client.get("/api/v1/demo/state").json()["templates"]
        fixed = next(template for template in templates if template["kind"] == "FIXED_ASSET")
        response = client.post(
            "/api/v1/actions/prepare",
            json={
                "idempotency_key": f"demo:{command_id}:liquid",
                "intent": {"kind": "purchase_asset", "policy_id": fixed["confirmed_policy_id"]},
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["effect"]["policy_id"] == fixed["confirmed_policy_id"]
        before = database_snapshot(demo_engine)
        wrong = client.get(f"/api/v1/demo/commands/{command_id}")
        assert wrong.status_code == 409, wrong.text
        assert wrong.json()["error"]["code"] == "INVALID_DEMO_ORIGINAL"
        assert database_snapshot(demo_engine) == before


def test_salary_without_goal_preserves_blocked_status_across_post_get_and_state(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    epoch_id, clock = _epoch_id(demo_engine), [NOW]
    with _client(demo_engine, clock) as client:
        for kind in ["CAR_GOAL", "LIQUID_ASSET"]:
            _confirm_template(client, clock, epoch_id, kind)
        body = {"event_kind": "SALARY_RECEIVED", "expected_epoch_id": str(epoch_id)}
        response = client.post("/api/v1/demo/events", json=body)
        assert response.status_code == 200, response.text
        blocked = response.json()
        assert blocked["status"] == "BLOCKED"
        assert blocked["goal_id"] is None and blocked["fact"] is None
        assert blocked["actions"] == []
        before_reads = database_snapshot(demo_engine)
        command = client.get(f"/api/v1/demo/commands/{blocked['command_id']}")
        assert command.status_code == 200, command.text
        assert command.json() == blocked
        state = client.get("/api/v1/demo/state")
        assert state.status_code == 200, state.text
        assert (
            next(
                item
                for item in state.json()["commands"]
                if item["command_id"] == blocked["command_id"]
            )
            == blocked
        )
        assert database_snapshot(demo_engine) == before_reads


def test_salary_after_actual_monthly_target_has_no_fake_goal_action_or_permanent_pending(
    demo_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Preserve the real service result while surfacing exceptions hidden by HTTP's 500 envelope.
    original_run = demo_console.run_demo_event
    service_errors: list[Exception] = []

    def capture_error(
        engine: Engine, user_id: UUID, body: DemoEventRequest, now: datetime
    ) -> DemoCommandView:
        try:
            return original_run(engine, user_id, body, now)
        except Exception as error:
            service_errors.append(error)
            raise

    monkeypatch.setattr(demo_console, "run_demo_event", capture_error)
    seed_demo(demo_engine)
    epoch_id, clock = _epoch_id(demo_engine), [NOW]
    with _client(demo_engine, clock) as client:
        for kind in ["CAR_GOAL", "LIQUID_ASSET"]:
            _confirm_template(client, clock, epoch_id, kind)
        created = client.post(
            "/api/v1/demo/events",
            json={"event_kind": "CREATE_CAR_GOAL", "expected_epoch_id": str(epoch_id)},
        )
        assert created.status_code == 200, created.text
        goal_id = created.json()["goal_id"]
        assert goal_id is not None
        with Session(demo_engine) as session:
            cash = session.scalar(
                select(Account)
                .where(Account.user_id == DEMO_USER_ID, Account.account_type == "CASH")
                .order_by(Account.id)
                .limit(1)
            )
            assert cash is not None
            account_id = cash.id
        clock[0] += timedelta(seconds=1)
        first_income = external_bank_facts.ingest_external_fact(
            demo_engine,
            DEMO_USER_ID,
            ExternalFactRequest(
                user_id=DEMO_USER_ID,
                idempotency_key="actual-monthly-target-income",
                external_ref="actual-monthly-target-income",
                kind="INCOME",
                account_id=account_id,
                amount_cents=200_000,
                counterparty_ref="payroll",
                occurred_at=clock[0],
            ),
            clock[0],
        )
        assert first_income.bank_status == "SETTLED"
        assert first_income.projection_status == "PROJECTED"
        clock[0] += timedelta(seconds=1)
        prepared = client.post(
            "/api/v1/actions/prepare",
            json={
                "idempotency_key": "actual-monthly-target-allocation",
                "intent": {"kind": "allocate_goal", "goal_id": goal_id},
            },
        )
        assert prepared.status_code == 200, prepared.text
        allocation = prepared.json()
        assert allocation["effect"]["amount_cents"] == 200_000
        assert allocation["autonomy_level"] == "AUTO_EXECUTE"
        clock[0] += timedelta(seconds=1)
        executed = client.post(f"/api/v1/actions/{allocation['action_id']}/execute", json={})
        assert executed.status_code == 200, executed.text
        assert executed.json()["status"] == "SUCCEEDED"
        assert executed.json()["receipt"]["executed_cents"] == 200_000
        before_salary = json.loads(database_snapshot(demo_engine))
        clock[0] += timedelta(seconds=1)
        body = {"event_kind": "SALARY_RECEIVED", "expected_epoch_id": str(epoch_id)}
        response = client.post("/api/v1/demo/events", json=body)
        if response.status_code == 500 and service_errors:
            raise service_errors[-1]
        assert response.status_code == 200, response.text
        completed = response.json()
        assert completed["status"] == "COMPLETED", completed
        assert completed["fact"]["bank_status"] == "SETTLED"
        assert completed["fact"]["projection_status"] == "PROJECTED"
        assert len(completed["actions"]) == 1
        purchase = completed["actions"][0]
        assert purchase["effect"]["action_type"] == "PURCHASE_ASSET"
        assert purchase["receipt"] is not None
        after_salary = json.loads(database_snapshot(demo_engine))
        assert len(after_salary["action_plans"]) == len(before_salary["action_plans"]) + 1
        assert len(after_salary["action_receipts"]) == len(before_salary["action_receipts"]) + 1
        assert sum(
            row["action_type"] == "ALLOCATE_GOAL" for row in after_salary["action_plans"]
        ) == sum(row["action_type"] == "ALLOCATE_GOAL" for row in before_salary["action_plans"])
        completed_snapshot = database_snapshot(demo_engine)
        assert client.get(f"/api/v1/demo/commands/{completed['command_id']}").json() == completed
        assert (
            next(
                item
                for item in client.get("/api/v1/demo/state").json()["commands"]
                if item["command_id"] == completed["command_id"]
            )
            == completed
        )
        assert client.post("/api/v1/demo/events", json=body).json() == completed
        assert database_snapshot(demo_engine) == completed_snapshot
