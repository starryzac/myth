"""Server-source read composition with ORM doubles; no actual PG/financial claim."""

from collections.abc import Callable
from typing import Any, cast
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from app.db.models import EvidenceItem
from app.domain.multi_goal_allocation import (
    MultiGoalAllocationInput,
    SourceReference,
    solve_multi_goal_allocation,
)
from app.domain.policy_configuration import configuration_hash
from app.services import full_joint_goal_planning as service
from app.services.full_projection import FutureIncomeProjection, _checkpoint
from app.services.full_protection_projection import FullAnnualProtectionResponse
from app.services.multi_goal_planning import JointPlanningResponse
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_full_joint_goal_planning import fixture
from app.tests.test_full_projection import NOW, USER, audit
from sqlalchemy.orm import Session


def setup(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[
    Session,
    MultiGoalAllocationInput,
    JointPlanningResponse,
    FullAnnualProtectionResponse,
    list[EvidenceItem],
    list[Session],
]:
    candidate, projection, refs = fixture()
    rows = [
        EvidenceItem(
            id=row.evidence_id,
            user_id=USER,
            content={"synthetic_test_source": str(row.evidence_id)},
            content_hash=configuration_hash({"synthetic_test_source": str(row.evidence_id)}),
            source_type="SYNTHETIC_UNIT_FIXTURE",
            status="VALID",
            evidence_level="USER_DECLARED",
            observed_at=NOW,
            valid_from=NOW,
            valid_to=None,
        )
        for row in refs
    ]
    actual = {
        row.id: SourceReference(
            user_id=row.user_id, evidence_id=row.id, content_hash=row.content_hash
        )
        for row in rows
    }
    candidate = MultiGoalAllocationInput.model_validate(
        candidate.model_copy(
            update={
                "income_lots": [
                    row.model_copy(
                        update={
                            "source_refs": [actual[ref.evidence_id] for ref in row.source_refs],
                            "bank_evidence_hash": actual[row.bank_evidence_id].content_hash,
                        }
                    )
                    for row in candidate.income_lots
                ],
                "goals": [
                    row.model_copy(
                        update={"source_refs": [actual[ref.evidence_id] for ref in row.source_refs]}
                    )
                    for row in candidate.goals
                ],
                "hard_protection_points": [
                    row.model_copy(
                        update={"source_refs": [actual[ref.evidence_id] for ref in row.source_refs]}
                    )
                    for row in candidate.hard_protection_points
                ],
            }
        ).model_dump()
    )
    original = JointPlanningResponse(
        user_id=USER,
        as_of=NOW,
        independent_bank_projection_matched=True,
        registered_goal_count=len(candidate.goals),
        included_goal_ids=[row.goal_id for row in candidate.goals],
        uncovered_goal_ids=[],
        allocation=solve_multi_goal_allocation(candidate),
        conflict=None,
        state="COMPUTED",
        source_evidence_ids=[row.id for row in rows[:-1]],
        source_issues=[],
        input_hash="a" * 64,
        limitations=[],
    )
    curve = projection.full_annual_projection
    assert curve is not None
    full = FullAnnualProtectionResponse(
        user_id=USER,
        as_of=NOW,
        projection=projection,
        initial_checkpoint=_checkpoint(
            0, curve.calculation_trace[0].date, curve.calculation_trace[:3]
        ),
        daily_checkpoints=[
            _checkpoint(
                day,
                curve.calculation_trace[day * 3].date,
                curve.calculation_trace[day * 3 : day * 3 + 3],
            )
            for day in range(1, 366)
        ],
        full_policy_sources=[],
        future_income=FutureIncomeProjection(),
        source_evidence_ids=[row.id for row in rows],
        source_issues=[],
        input_digest="b" * 64,
        audit=audit(),
        limitations=[],
    )
    fake = MagicMock(spec=Session)
    fake.new = set()
    fake.dirty = set()
    fake.deleted = set()
    fake.connection.return_value.get_isolation_level.return_value = "REPEATABLE READ"
    fake.scalar.return_value = "on"
    fake.scalars.return_value.all.return_value = rows
    session = cast(Session, fake)
    calls: list[Session] = []

    def joint(
        current: Session,
        user_id: UUID,
        now: Any,
        *,
        capture_inputs: Callable[[MultiGoalAllocationInput], None] | None = None,
    ) -> JointPlanningResponse:
        assert user_id == USER and now == NOW
        calls.append(current)
        assert capture_inputs is not None
        capture_inputs(candidate.model_copy(deep=True))
        return original

    def annual(current: Session, user_id: UUID, now: Any) -> FullAnnualProtectionResponse:
        assert user_id == USER and now == NOW
        calls.append(current)
        return full

    monkeypatch.setattr(service, "joint_goal_planning", joint)
    monkeypatch.setattr(service, "compute_full_annual_protection", annual)
    return session, candidate, original, full, rows, calls


def test_same_request_original_input_and_full_floor_use_fresh_original_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, candidate, old, full, _, calls = setup(monkeypatch)
    old_before = old.model_dump(mode="json")
    full_before = full.model_dump(mode="json")
    result = service.full_joint_goal_planning(session, USER, NOW)
    assert calls == [session, session]
    assert result.state == "COMPUTED"
    assert result.binding is not None and result.binding.status == "VERIFIED"
    assert result.binding.original_input_hash == configuration_hash(
        candidate.model_dump(mode="json")
    )
    assert result.allocation is not None and result.allocation.budget_cents == 11
    assert result.reasons == sorted(set(result.allocation.reasons))
    assert result.original_joint == old and result.full_protection == full
    assert result.allocation.goals[0].amount_cents == 11
    assert result.grants_authority is False and result.execution_support == "NOT_IMPLEMENTED"
    assert old.model_dump(mode="json") == old_before and full.model_dump(mode="json") == full_before
    assert service.full_joint_goal_planning(session, USER, NOW) == result
    assert len(calls) == 4
    sql = str(cast(Any, session).scalars.call_args.args[0])
    assert "evidence_items.user_id" in sql and "FOR UPDATE" not in sql
    cast(Any, session).add.assert_not_called()
    cast(Any, session).commit.assert_not_called()


@pytest.mark.parametrize(
    "violation",
    [
        "missing_source",
        "changed_hash",
        "expired",
        "unknown",
        "future_observed",
        "future_validity",
        "owner",
        "clock",
        "capture_hash",
        "no_capture",
        "bank",
        "audit",
    ],
)
def test_incomplete_actual_context_retains_goal_denominator_and_null_amounts(
    violation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, candidate, old, full, rows, _ = setup(monkeypatch)
    from datetime import timedelta

    if violation == "missing_source":
        rows.pop()
    elif violation == "changed_hash":
        rows[0].content = {"changed": True}
    elif violation == "expired":
        rows[0].valid_to = NOW
    elif violation == "unknown":
        rows[0].status = "UNKNOWN"
    elif violation == "future_observed":
        rows[0].observed_at = NOW + timedelta(seconds=1)
    elif violation == "future_validity":
        rows[0].valid_from = NOW + timedelta(seconds=1)
    elif violation == "owner":
        monkeypatch.setattr(
            service,
            "compute_full_annual_protection",
            lambda *args: full.model_copy(update={"user_id": UUID(int=999)}),
        )
    elif violation == "clock":
        monkeypatch.setattr(
            service,
            "compute_full_annual_protection",
            lambda *args: full.model_copy(update={"as_of": NOW + timedelta(seconds=1)}),
        )
    elif violation == "capture_hash":
        assert old.allocation is not None
        old.allocation = old.allocation.model_copy(update={"input_hash": "c" * 64})
    elif violation == "no_capture":
        monkeypatch.setattr(service, "joint_goal_planning", lambda *args, **kwargs: old)
    elif violation == "bank":
        old.independent_bank_projection_matched = False
    else:
        monkeypatch.setattr(
            service,
            "compute_full_annual_protection",
            lambda *args: full.model_copy(update={"audit": audit("INTEGRITY_ERROR")}),
        )
    result = service.full_joint_goal_planning(session, USER, NOW)
    assert result.state == "UNKNOWN" and result.reasons
    assert result.original_joint.registered_goal_count == len(candidate.goals)
    if violation == "no_capture":
        assert result.allocation is None and result.binding is None
    else:
        assert result.allocation is not None and result.allocation.status == "UNKNOWN"
        assert len(result.allocation.goals) == len(candidate.goals)
        assert all(row.amount_cents is None for row in result.allocation.goals)


@pytest.mark.parametrize(
    "violation", ["dirty", "new", "deleted", "isolation", "writeable", "clock"]
)
def test_service_rejects_mutating_non_rr_readonly_or_untrusted_clock_before_original_reads(
    violation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, _, _, _, _, calls = setup(monkeypatch)
    fake = cast(Any, session)
    if violation in {"dirty", "new", "deleted"}:
        setattr(fake, violation, {object()})
    elif violation == "isolation":
        fake.connection.return_value.get_isolation_level.return_value = "READ COMMITTED"
    elif violation == "writeable":
        fake.scalar.return_value = "off"
    with pytest.raises(PolicyLifecycleError):
        service.full_joint_goal_planning(
            session, USER, NOW.replace(tzinfo=None) if violation == "clock" else NOW
        )
    assert calls == []


def test_original_three_position_call_and_private_capture_are_same_reads_and_hash_and_copy_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from app.db.models import User
    from app.services import multi_goal_planning as original_service
    from app.tests.test_full_projection import context

    fake = MagicMock(spec=Session)
    fake.new, fake.dirty, fake.deleted = set(), set(), set()
    fake.scalar.side_effect = lambda query: "repeatable read" if "isolation" in str(query) else "on"
    fake.get.return_value = User(id=USER, is_simulated=True)
    fake.scalars.return_value = []
    ctx = context()
    evidence = EvidenceItem(id=UUID(int=600), user_id=USER, content_hash="a" * 64)
    ctx.sources.evidence[evidence.id] = evidence
    income = SimpleNamespace(
        evidence_id=evidence.id, ledger=SimpleNamespace(origins=[], fragments=[])
    )
    monkeypatch.setattr(original_service, "load_boundary_context", lambda *args: ctx)
    monkeypatch.setattr(original_service, "validate_bank_projection", lambda *args: None)
    monkeypatch.setattr(original_service, "read_income_state", lambda *args: income)
    monkeypatch.setattr(original_service, "current_epoch_audit", lambda *args: audit())
    session = cast(Session, fake)
    old = original_service.joint_goal_planning(session, USER, NOW)
    sql_reads = (fake.scalar.call_count, fake.get.call_count, fake.scalars.call_count)
    captures: list[MultiGoalAllocationInput] = []

    def mutate_copy(candidate: MultiGoalAllocationInput) -> None:
        captures.append(candidate)
        candidate.source_issues.append("OBSERVER_MUST_NOT_MUTATE_ORIGINAL_INPUT")
        candidate.hard_protection_points[0].source_refs.clear()

    new = original_service.joint_goal_planning(session, USER, NOW, capture_inputs=mutate_copy)
    assert new == old and new.input_hash == old.input_hash
    assert len(captures) == 1 and captures[0].source_issues
    assert fake.scalar.call_count == 2 * sql_reads[0]
    assert fake.get.call_count == 2 * sql_reads[1]
    assert fake.scalars.call_count == 2 * sql_reads[2]
    assert new.allocation is not None and new.allocation.status == "OPTIMAL"
    fake.add.assert_not_called()
    fake.commit.assert_not_called()
