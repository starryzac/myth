"""Actual rent template bank identity and stale-action preservation regression."""

from datetime import timedelta
from uuid import UUID

import pytest
from app.db.models import (
    ActionPlan,
    ActionReceipt,
    AuditEvent,
    BankOperation,
    EvidenceItem,
    PolicyProposal,
)
from app.services import demo_console
from app.services.audit_chain import verify_audit_chain
from app.services.demo_console_types import TemplateKind
from app.services.demo_seed import DEMO_USER_ID, seed_demo
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.scenario_runner import ScenarioRunner
from app.services.scenario_types import ScenarioRPC
from app.tests.test_demo_console import NOW, _client, _confirm_template, _epoch_id
from app.tests.test_demo_seed import database_snapshot
from app.tests.test_demo_seed import demo_engine as demo_engine
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("old_payee", ["demo-landlord", "synthetic-landlord-001"])
def test_old_declared_rent_is_read_unchanged_after_new_template_and_day_change(
    demo_engine: Engine, monkeypatch: pytest.MonkeyPatch, old_payee: str
) -> None:
    """Generate actual old input declarations; never inject a financial outcome."""
    seed_demo(demo_engine)
    epoch_id, clock = _epoch_id(demo_engine), [NOW]
    original_config = demo_console.template_configuration

    def older_declaration(kind: TemplateKind, *, rent_due_day: int = 15) -> dict[str, object]:
        config = original_config(kind, rent_due_day=15)
        if kind == "RENT":
            config = {**config, "payee_id": old_payee}
        return config

    with _client(demo_engine, clock) as client:
        with monkeypatch.context() as older:
            older.setattr(demo_console, "template_configuration", older_declaration)
            _confirm_template(client, clock, epoch_id, "RENT")
            previous = client.get("/api/v1/demo/state").json()["templates"]
        clock[0] += timedelta(days=1)
        before = database_snapshot(demo_engine)
        response = client.get("/api/v1/demo/state")
        assert response.status_code == 200, response.text
        original_rent = next(row for row in previous if row["kind"] == "RENT")
        current_rent = next(row for row in response.json()["templates"] if row["kind"] == "RENT")
        assert current_rent == original_rent
        assert current_rent["configuration"]["due_day"] == 15
        assert current_rent["configuration"]["payee_id"] == old_payee
        assert database_snapshot(demo_engine) == before
        replay = client.post(
            "/api/v1/demo/templates/RENT/prepare", json={"expected_epoch_id": str(epoch_id)}
        )
        assert replay.status_code == 200 and replay.json() == original_rent
        assert database_snapshot(demo_engine) == before
        # A changed template amount in a loaded ORM object still fails the source
        # family gate. No tamper is committed, and the original DB remains exact.
        with Session(demo_engine) as session, session.no_autoflush:
            proposal = session.get(PolicyProposal, UUID(original_rent["proposal_id"]))
            assert proposal is not None
            proposal.proposed_configuration = {
                **proposal.proposed_configuration,
                "amount_rule": {"kind": "exact", "amount_cents": 150001},
            }
            with pytest.raises(PolicyLifecycleError, match="原声明"):
                demo_console.get_demo_state(session, DEMO_USER_ID, clock[0])
        assert database_snapshot(demo_engine) == before


def test_original_rent_template_prepares_known_payee_then_change_invalidates_same_action(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    epoch_id = _epoch_id(demo_engine)
    clock = [NOW]
    with _client(demo_engine, clock) as client:
        _confirm_template(client, clock, epoch_id, "RENT")
        state = client.get("/api/v1/demo/state").json()
        rent = next(row for row in state["templates"] if row["kind"] == "RENT")
        assert rent["status"] == "CONFIRMED"
        policy_id = UUID(rent["confirmed_policy_id"])
        with Session(demo_engine) as session:
            original_events = {
                row.id: row.canonical_text for row in session.scalars(select(AuditEvent))
            }
        actual = ScenarioRunner(demo_engine, DEMO_USER_ID).rpc(
            ScenarioRPC(
                scenario_id="rent-template-real-bank-identity",
                purpose="DEVELOPMENT",
                operation="prepare_rent_old_action",
                expected_epoch_id=epoch_id,
                policy_id=policy_id,
            ),
            clock[0],
        )
        action_id = UUID(actual["action_id"])
        action = client.get(f"/api/v1/actions/{action_id}").json()
        assert action["status"] == "PLANNED" and action["receipt"] is None
        assert action["effect"]["payee_id"] == "synthetic-landlord-001"
        assert action["effect"]["policy_id"] == str(policy_id)
        with Session(demo_engine) as session:
            proof = session.get(EvidenceItem, UUID(action["effect"]["payee_evidence_id"]))
            assert proof is not None and proof.evidence_level == "BANK_CONFIRMED"
            assert proof.content["counterparty_ref"] == "synthetic-landlord-001"
            assert session.get(BankOperation, action_id) is None
            assert session.scalars(select(ActionReceipt)).all() == []
        clock[0] += timedelta(seconds=1)
        proposed = client.post(
            "/api/v1/demo/events",
            json={"event_kind": "CHANGE_RENT", "expected_epoch_id": str(epoch_id)},
        )
        assert proposed.status_code == 200, proposed.text
        command = proposed.json()
        assert command["status"] == "WAITING_POLICY_CHANGE"
        request = command["policy_change"]
        clock[0] += timedelta(seconds=1)
        confirmation = client.patch(
            f"/api/v1/policies/{policy_id}",
            json={
                key: value
                for key, value in {**request, "accepted": True}.items()
                if key != "policy_id"
            },
        )
        assert confirmation.status_code == 200, confirmation.text
        invalidated = client.get(f"/api/v1/actions/{action_id}").json()
        assert invalidated["status"] == "INVALIDATED"
        assert invalidated["effect_hash"] == action["effect_hash"]
        assert invalidated["receipt"] is None
        with Session(demo_engine) as session:
            original = session.get(ActionPlan, action_id)
            assert original is not None
            assert original.request["execution"]["effect_hash"] == action["effect_hash"]
            assert session.get(BankOperation, action_id) is None
            assert session.scalars(select(ActionReceipt)).all() == []
            assert verify_audit_chain(session, DEMO_USER_ID, epoch_id).status == "VALID"
            for identity, canonical_text in original_events.items():
                row = session.get(AuditEvent, identity)
                assert row is not None and row.canonical_text == canonical_text
