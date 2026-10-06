"""Root-only actual whole-T0 read-only producer candidate; NOT_RUN until scheduled."""

from uuid import UUID

import pytest
from app.domain.full_action_set_recovery_producers import (
    derive_recovery_producers,
    verify_frozen_recovery_producer_inputs,
)
from app.services.audit_chain import verify_audit_chain
from app.services.demo_seed import DEMO_USER_ID
from app.services.full_action_set_boundary_actual import capture_actual_action_set
from app.services.full_action_set_recovery_producers import capture_current_recovery_producers
from app.services.full_recovery_execution import _reader, require_installed_full_recovery_guards
from app.tests.test_full_asset_allocation_api import actual_income
from app.tests.test_full_projection import NOW
from app.tests.test_full_projection_api import annual_client as annual_client
from app.tests.test_full_projection_api import physical_snapshot
from app.tests.test_full_recovery_planning_api import (
    actual_purchase,
    actual_recovery_confirmation,
    actual_shrink_to_deficit,
)
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration


def test_actual_complete_t0_recovery_producer_ask_original_365_sources_and_zero_writes(
    annual_client: tuple[TestClient, Engine],
) -> None:
    client, engine = annual_client
    require_installed_full_recovery_guards()
    actual_income(engine)
    asset_id, position_id, receipt = actual_purchase(client, engine, "LIQUID_ASSET")
    full_id = UUID(actual_recovery_confirmation(client, asset_id))
    actual_shrink_to_deficit(client, engine)
    before = physical_snapshot(engine)
    with _reader(engine) as read:
        audit = verify_audit_chain(read, DEMO_USER_ID, mode="EXACT")
        assert audit.status == "VALID", audit.model_dump()
        actual = capture_actual_action_set(read, DEMO_USER_ID, NOW)
        actual_bytes = actual.inputs.model_dump_json()
        captured = capture_current_recovery_producers(
            read, DEMO_USER_ID, NOW, original_actual_capture=actual
        )
        result = captured.result
        assert result.recovery_family_complete, result.model_dump(mode="json")
        assert full_id in result.expected_full_policy_ids
        item = next(row for row in result.results if row.full_policy_id == full_id)
        assert item.view.state == "INCLUDED" and item.view.autonomy_level == "ASK_ONCE"
        assert item.selected_position_ids == [position_id]
        assert item.view.amount_cents == receipt["executed_cents"]
        assert item.requires_new_exact_user_confirmation
        assert not result.bank_authority and not result.financial_write
        source = next(row for row in captured.inputs.producers if row.full_policy_id == full_id)
        assert source.planning_inputs is not None and source.original_planning is not None
        assert source.planning_inputs.snapshot.horizon_days == 365
        assert source.original_planning.plan is not None
        assert len(source.original_planning.plan.actual_boundary.calculation_trace) == 1098
        assert source.execution_inputs is not None and source.execution_inputs.deadline_at == NOW
        assert source.candidate is not None and source.candidate.facts is not None
        assert source.candidate.facts.confirmation is None
        assert derive_recovery_producers(captured.inputs) == result
        assert (
            verify_frozen_recovery_producer_inputs(captured.inputs.model_dump(mode="json"))
            == result
        )
        assert actual.inputs.model_dump_json() == actual_bytes
    assert physical_snapshot(engine) == before
    # A separate GET invocation reconstructs the complete originals, with no permission cache.
    with _reader(engine) as read:
        fresh = capture_current_recovery_producers(read, DEMO_USER_ID, NOW)
        assert fresh.result == result
    assert physical_snapshot(engine) == before
