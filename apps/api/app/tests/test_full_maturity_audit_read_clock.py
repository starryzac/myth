"""Synthetic clock seams; real bank/receipt acceptance is checked separately."""

from datetime import timedelta
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest
from app.domain.audit_chain_types import AuditEvent
from app.services.audit_chain import _verified_maturity_link_clock
from app.tests.test_full_maturity_execution import history_fixture


def test_new_verified_maturity_reads_exact_projection_observation_without_editing_original() -> (
    None
):
    trace = history_fixture("PREPARE")
    before = trace.model_dump_json()
    later = trace.as_of + timedelta(days=1)
    original = cast(
        AuditEvent,
        SimpleNamespace(
            event_type="ACTION_PROJECTED",
            user_id=trace.user_id,
            action_plan_id=trace.action_id,
            observed_at=later,
        ),
    )
    assert (
        _verified_maturity_link_clock(trace, [original], trace.as_of - timedelta(days=30)) == later
    )
    assert trace.model_dump_json() == before
    assert _verified_maturity_link_clock(
        trace, [original], later + timedelta(days=1)
    ) == later + timedelta(days=1)


@pytest.mark.parametrize(
    "change",
    ["old_algorithm", "no_action", "other_action", "other_user", "not_projection", "absent"],
)
def test_other_labels_or_unrelated_facts_cannot_advance_old_or_new_read_clock(change: str) -> None:
    trace = history_fixture("PREPARE")
    wall = trace.as_of - timedelta(days=30)
    event = SimpleNamespace(
        event_type="ACTION_PROJECTED",
        user_id=trace.user_id,
        action_plan_id=trace.action_id,
        observed_at=trace.as_of,
    )
    if change == "old_algorithm":
        trace = trace.model_copy(
            update={"algorithm_versions": {"recovery": "whole-position-recovery-v1"}}
        )
    elif change == "no_action":
        trace = trace.model_copy(update={"action_id": None})
    elif change == "other_action":
        event.action_plan_id = UUID(int=654)
    elif change == "other_user":
        event.user_id = UUID(int=654)
    elif change == "not_projection":
        event.event_type = "BANK_SETTLED"
    events = [] if change == "absent" else [cast(AuditEvent, event)]
    assert _verified_maturity_link_clock(trace, events, wall) == wall
