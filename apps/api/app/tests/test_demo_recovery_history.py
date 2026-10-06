"""An original completed demo recovery survives later real consumption, read-only."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.db.models import ActionReceipt, AuditEvent
from app.domain.demo_identity import DEMO_USER_ID
from app.services import demo_console
from app.services.demo_seed import seed_demo
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_demo_console import _client, _confirm_template, _epoch_id
from app.tests.test_demo_seed import database_snapshot
from app.tests.test_demo_seed import demo_engine as demo_engine
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_original_completed_recovery_remains_completed_after_new_real_expense(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    epoch, clock = _epoch_id(demo_engine), [datetime.now(UTC)]
    with _client(demo_engine, clock) as client:
        for kind in ("CAR_GOAL", "LIQUID_ASSET", "FIXED_ASSET"):
            _confirm_template(client, clock, epoch, kind)
        events = {}
        for kind in ("CREATE_CAR_GOAL", "SALARY_RECEIVED", "LARGE_CONSUMPTION", "AUTO_REDEEM"):
            clock[0] += timedelta(seconds=1)
            response = client.post(
                "/api/v1/demo/events",
                json={"event_kind": kind, "expected_epoch_id": str(epoch)},
            )
            assert response.status_code == 200, response.text
            events[kind] = response.json()
            assert events[kind]["status"] == "COMPLETED", events[kind]
        original = events["AUTO_REDEEM"]
        assert original["recovery"]["status"] == "RECOVERED"
        assert original["recovery"]["actions"]
        assert all(
            a["bank_status"] == "SETTLED" and a["receipt_id"]
            for a in original["recovery"]["actions"]
        )
        with Session(demo_engine) as session:
            old_events = {r.id: r.canonical_text for r in session.scalars(select(AuditEvent))}
        # The original preset purchases an actual fixed asset, then ingests its disclosed
        # ordinary expense through the independent bank. No balance/result is injected.
        clock[0] += timedelta(seconds=1)
        fixed = client.post(
            "/api/v1/demo/events",
            json={"event_kind": "FIXED_EARLY_WITHDRAWAL", "expected_epoch_id": str(epoch)},
        )
        assert fixed.status_code == 200, fixed.text
        assert fixed.json()["status"] == "WAITING_ACTION_CONFIRMATION", fixed.text
        assert fixed.json()["fact"]["bank_status"] == "SETTLED"
        before = database_snapshot(demo_engine)
        reread = client.get(f"/api/v1/demo/commands/{original['command_id']}")
        assert reread.status_code == 200, reread.text
        actual = reread.json()
        assert actual["recovery"]["status"] == "PARTIAL_RECOVERY"
        assert actual["recovery"]["actual_boundary"]["status"] == "LIQUIDITY_RISK"
        assert actual["recovery"]["actions"] == original["recovery"]["actions"]
        assert actual["status"] == "COMPLETED", actual
        state = client.get("/api/v1/demo/state")
        assert state.status_code == 200, state.text
        assert (
            next(c for c in state.json()["commands"] if c["command_id"] == original["command_id"])[
                "status"
            ]
            == "COMPLETED"
        )
        replay = client.post(
            "/api/v1/demo/events",
            json={"event_kind": "AUTO_REDEEM", "expected_epoch_id": str(epoch)},
        )
        assert replay.status_code == 200 and replay.json()["status"] == "COMPLETED", replay.text
        assert database_snapshot(demo_engine) == before
        with Session(demo_engine) as session:
            for key, value in old_events.items():
                old_event = session.get(AuditEvent, key)
                assert old_event is not None and old_event.canonical_text == value
        # A successful historical command must still verify its original settlement.
        for field, replacement in (("executed_cents", -1), ("fee_cents", 1), ("loss_cents", 1)):
            with Session(demo_engine) as session, session.no_autoflush:
                session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
                session.execute(text("SET TRANSACTION READ ONLY"))
                receipt = session.get(
                    ActionReceipt, UUID(original["recovery"]["actions"][0]["receipt_id"])
                )
                assert receipt is not None
                setattr(receipt, field, replacement)
                with pytest.raises(PolicyLifecycleError) as rejected:
                    demo_console.get_demo_command(
                        session, DEMO_USER_ID, UUID(original["command_id"]), clock[0]
                    )
                assert rejected.value.code == "BANK_RECONCILIATION_REQUIRED"
        assert database_snapshot(demo_engine) == before
