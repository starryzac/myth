"""Root-only actual isolated-PG candidate; collection is not financial evidence."""

from uuid import UUID

import pytest
from app.services.audit_chain import verify_audit_chain
from app.services.demo_seed import DEMO_USER_ID
from app.services.full_action_set_boundary_actual import capture_actual_action_set
from app.services.full_action_set_release_producers import capture_current_release_producers
from app.services.full_goal_release_authorization import _reader
from app.tests.test_full_goal_release_execution_integration import arrange_release
from app.tests.test_full_projection_api import physical_snapshot
from app.tests.test_goal_api import NOW
from app.tests.test_goal_api import goal_client as goal_client
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration


def test_actual_complete_release_family_original_scope_sources_365_and_zero_writes(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    body = arrange_release(client, engine)
    before = physical_snapshot(engine)
    with _reader(engine) as session:
        original = capture_actual_action_set(session, DEMO_USER_ID, NOW)
        original_bytes = original.inputs.model_dump_json()
        captured = capture_current_release_producers(
            session, DEMO_USER_ID, NOW, original_actual_capture=original
        )
        result = captured.result
        assert result.release_family_complete, result.model_dump(mode="json")
        assert result.original_authorization_source_ids and result.original_action_ids == []
        assert result.expected_full_policy_ids == [UUID(body["policy_id"])]
        assert original.inputs.model_dump_json() == original_bytes
        included = [row for row in result.results if row.view.state == "INCLUDED"]
        assert len(included) == 1, result.model_dump(mode="json")
        action = included[0]
        assert action.source_goal_id == UUID(body["source_goal_id"])
        assert action.destination_account_id == UUID(body["destination_account_id"])
        assert action.view.action_type == "RELEASE_GOAL" and action.view.amount_cents == 10000
        assert action.view.autonomy_level == "ASK_ONCE" and action.view.signature is not None
        assert action.shadow_original_candidate_key is None
        assert action.requires_new_exact_user_confirmation is True
        item = next(
            row
            for row in captured.inputs.producers
            if row.source_goal_id == action.source_goal_id
            and row.destination_account_id == action.destination_account_id
        )
        assert item.command is not None and item.actual_candidate is not None
        assert item.actual_candidate.protection is not None
        assert item.actual_candidate.protection.compared_point_count == 1098
        assert item.actual_candidate.actual_inventory.policy_usage.cap_occupied_cents == 0
        assert sum(row.amount_cents for row in item.command.effect.release_uses) == 10000
        assert item.command.effect.available_income_increase_cents == 0
        assert item.command.effect.assigned_income_decrease_cents == 0
        assert item.command.effect.principal_change_cents == 0
        assert result.bank_authority is result.financial_write is False
        assert verify_audit_chain(session, DEMO_USER_ID).status == "VALID"
    assert physical_snapshot(engine) == before
