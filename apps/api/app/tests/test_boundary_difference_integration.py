"""One necessary isolated real-PG difference/owner/zero-write risk; root executes."""

from uuid import uuid4

import pytest
from app.db.models import PolicyVersion
from app.domain.policy_configuration import configuration_hash
from app.services.action_contracts import PrepareActionRequest, TransferIntent
from app.services.boundary_difference import BoundaryDifferenceRequest, compare_boundary_runs
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF
from app.services.execution import prepare_action
from app.services.policy_lifecycle import PolicyLifecycleError, change_policy
from app.tests.test_boundary_service import boundary_engine as boundary_engine
from app.tests.test_boundary_service import confirmed_policy, snapshot
from app.tests.test_execution_service import transfer_accounts
from app.tests.test_full_policy_lifecycle_integration import readonly
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_actual_confirmed_protection_delta_replays_historical_sources_and_never_writes(
    boundary_engine: Engine,
) -> None:
    source, target = transfer_accounts(boundary_engine)
    with Session(boundary_engine) as session, session.begin():
        policy_id, version_id = confirmed_policy(
            session, {"type": "emergency_buffer", "amount_cents": 12345}
        )
        version = session.get(PolicyVersion, version_id)
        assert version is not None
        changed = {**version.configuration, "amount_cents": 17345}
    intent = TransferIntent(
        kind="transfer_internal",
        source_account_id=source,
        destination_account_id=target,
        amount_cents=100,
    )
    first = prepare_action(
        boundary_engine,
        DEMO_USER_ID,
        PrepareActionRequest(idempotency_key="difference-before", intent=intent),
        SEED_AS_OF,
    )
    with Session(boundary_engine) as session, session.begin():
        change_policy(
            session,
            DEMO_USER_ID,
            policy_id,
            version_id,
            changed,
            configuration_hash(changed),
            True,
            "实际保护额增加5000分",
            "difference-policy",
            SEED_AS_OF,
        )
    last = prepare_action(
        boundary_engine,
        DEMO_USER_ID,
        PrepareActionRequest(idempotency_key="difference-after", intent=intent),
        SEED_AS_OF,
    )
    assert first.decision_run_id is not None and last.decision_run_id is not None
    before = snapshot(boundary_engine)
    body = BoundaryDifferenceRequest(
        before_run_id=first.decision_run_id, after_run_id=last.decision_run_id
    )
    with readonly(boundary_engine) as session:
        result = compare_boundary_runs(session, DEMO_USER_ID, body, SEED_AS_OF)
        assert result.status == "RECOMPUTED", result.reasons
        assert result.delta_cents == -5000
        assert (
            result.before_safe_idle_cents is not None and result.after_safe_idle_cents is not None
        )
        assert result.before_safe_idle_cents - result.after_safe_idle_cents == 5000
        assert [row.component for row in result.changes] == ["versions"]
        assert result.before_source_refs and result.after_source_refs
        assert result.before_trace_hash != result.after_trace_hash
        assert result.authority_granted is False and result.financial_only
        assert compare_boundary_runs(session, DEMO_USER_ID, body, SEED_AS_OF) == result
        same = compare_boundary_runs(
            session,
            DEMO_USER_ID,
            BoundaryDifferenceRequest(
                before_run_id=first.decision_run_id, after_run_id=first.decision_run_id
            ),
            SEED_AS_OF,
        )
        assert same.status == "RECOMPUTED" and same.delta_cents == 0 and not same.changes
        with pytest.raises(PolicyLifecycleError) as foreign:
            compare_boundary_runs(session, uuid4(), body, SEED_AS_OF)
        assert foreign.value.status_code == 404
    assert snapshot(boundary_engine) == before
