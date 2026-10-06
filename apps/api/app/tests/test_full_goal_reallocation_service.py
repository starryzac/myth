"""Explicit original-service doubles; no database or bank effects measured here."""

from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from app.db.models import ActionResourceReservation, Goal, User
from app.domain.boundary_types import BoundaryPoint
from app.domain.full_goal_reallocation import ReallocationPreviewRequest
from app.domain.full_reconciliation import ReconciliationGoal, compare_amount, head_reference
from app.domain.policy_configuration import configuration_hash
from app.services import full_goal_reallocation as service
from app.services.full_goals import FullGoalModelResponse
from app.services.full_policy_lifecycle import FullPolicyView, FullVersionView
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_full_goal_reallocation import (
    EPOCH,
    GOAL,
    GOAL_VERSION,
    NOW,
    POLICY,
    USER,
    VERSION,
    configuration,
)
from sqlalchemy.orm import Session


class FakeSession:
    no_autoflush = nullcontext()

    def __init__(self) -> None:
        self.user = User(id=USER, is_simulated=True, timezone="Asia/Shanghai")
        self.goal = Goal(
            id=GOAL,
            user_id=USER,
            policy_id=UUID(int=7140),
            policy_version_id=GOAL_VERSION,
            account_id=UUID(int=7141),
            allocated_cents=60000,
        )

    def scalar(self, query: Any) -> Any:
        entity = query.column_descriptions[0].get("entity")
        if entity is User:
            return self.user
        if entity is Goal:
            return self.goal
        return 0

    def scalars(self, query: Any) -> list[Any]:
        return []


def request() -> ReallocationPreviewRequest:
    return ReallocationPreviewRequest(
        policy_id=POLICY,
        source_goal_id=GOAL,
        expected_policy_version_id=VERSION,
        expected_goal_policy_version_id=GOAL_VERSION,
        expected_epoch_id=EPOCH,
    )


def setup(monkeypatch: pytest.MonkeyPatch) -> tuple[Session, SimpleNamespace]:
    session = cast(Session, FakeSession())
    config = configuration().model_dump(mode="json")
    policy = FullPolicyView(
        policy_id=POLICY,
        epoch_id=EPOCH,
        template_name="CrossGoalReallocationPolicy",
        name="synthetic source only",
        status="ACTIVE",
        effective_status="ACTIVE",
        planning_confirmation_valid=True,
        reference_validation="CURRENT",
        current_version=FullVersionView(
            version_id=VERSION,
            policy_id=POLICY,
            version_number=1,
            configuration=config,
            content_hash=configuration_hash(config),
            previous_hash=None,
            summary="synthetic original protocol double",
            confirmation={"accepted": True},
            confirmed_at=NOW,
            valid_from=NOW,
            valid_until=None,
            change_reason="synthetic",
            evidence_ids=[],
            impact_analysis={},
            confirmation_evidence_status="CURRENT_EVIDENCE_MATCHED",
        ),
        updated_at=NOW,
    )
    point = BoundaryPoint(
        day=0,
        date=NOW.date(),
        phase="BEFORE_PAYMENT",
        cash_cents=100000,
        protected_cents_by_reason={
            "obligations": 20000,
            "living": 10000,
            "emergency": 15000,
            "goal_cash": 80000,
            "goal_minimum": 0,
        },
        margin_cents=-25000,
        obligation_occurrence_ids=[],
        principal_position_ids=[],
    )
    head = head_reference(UUID(int=7142), "synthetic only", 1, NOW)
    ownership = ReconciliationGoal(
        goal_id=GOAL,
        account_id=UUID(int=7141),
        allocated_cents=60000,
        position_ids=[],
        ownership_evidence_id=UUID(int=7143),
        ownership_evidence_hash="9" * 64,
        current_ownership_proof_verified=True,
        cash=compare_amount(GOAL, "GOAL_CASH", 50000, 50000, head),
        principal=compare_amount(GOAL, "GOAL_PRINCIPAL", 10000, 10000, head),
    )
    reconciliation = SimpleNamespace(
        user_id=USER,
        as_of=NOW,
        inventory=[SimpleNamespace(complete=True)] * 8,
        bank_ledger_verified=True,
        current_application_projection_matched=True,
        audit=SimpleNamespace(status="VALID"),
        account_cash=[compare_amount(UUID(int=7141), "ACCOUNT_CASH", 100000, 100000, head)],
        goal_ownership=[ownership],
        input_hash="1" * 64,
    )
    protection = SimpleNamespace(
        user_id=USER,
        as_of=NOW,
        projection=SimpleNamespace(
            original_execution_view=SimpleNamespace(
                calculation_trace=[point], status="LIQUIDITY_RISK"
            ),
            full_annual_projection=SimpleNamespace(calculation_trace=[point]),
            status="LIQUIDITY_RISK",
        ),
        source_issues=[],
        audit=SimpleNamespace(complete=True, status="VALID"),
        input_digest="2" * 64,
    )
    full_config = {
        "type": "long_term_goal",
        "name": "synthetic",
        "target_cents": 100000,
        "deadline": "2027-10-01",
        "monthly_contribution": {"min_cents": 0, "target_cents": 100, "max_cents": 200},
        "minimum_guarantee_cents": 30000,
    }
    model = FullGoalModelResponse(
        goal_id=GOAL,
        policy_id=UUID(int=7140),
        base_policy_version_id=GOAL_VERSION,
        epoch_id=EPOCH,
        status="VERIFIED",
        full_configuration=full_config,
    )
    originals = SimpleNamespace(
        policy=policy, reconciliation=reconciliation, protection=protection, model=model
    )
    monkeypatch.setattr(service, "_read_snapshot", lambda session: None)
    monkeypatch.setattr(service, "historical_ledger_scope", lambda session: nullcontext())
    monkeypatch.setattr(
        service,
        "current_audit_epoch",
        lambda session, user: SimpleNamespace(id=EPOCH, status="OPEN"),
    )
    monkeypatch.setattr(service, "read_full_policy", lambda *args: originals.policy)
    monkeypatch.setattr(service, "full_reconciliation", lambda *args: originals.reconciliation)
    monkeypatch.setattr(
        service, "compute_full_annual_protection", lambda *args: originals.protection
    )
    monkeypatch.setattr(service, "read_full_goal_model", lambda *args: originals.model)
    return session, originals


def test_actual_adapter_shape_retains_real_math_and_missing_grant_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _ = setup(monkeypatch)
    result = service.preview_goal_reallocation(session, USER, request(), NOW)
    assert result.decision.math.minimum_repair_cents == 25000
    assert result.decision.math.source_cash_releasable_above_minimum_cents == 30000
    assert result.decision.state == "UNKNOWN" and result.decision.candidate_amount_cents is None
    assert not result.bank_authority and not result.financial_grant_created
    assert (
        result.reconciliation_input_hash == "1" * 64
        and result.full_protection_input_hash == "2" * 64
    )
    assert result.original_request == request() and not result.original_goal_bridge_cross_enabled


@pytest.mark.parametrize("failure", ["cash", "owner", "clock", "component", "goal", "model"])
def test_source_mismatch_or_missing_original_does_not_become_exact_repair(
    failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, originals = setup(monkeypatch)
    if failure == "cash":
        originals.protection.projection.original_execution_view.calculation_trace = [
            originals.protection.projection.original_execution_view.calculation_trace[0].model_copy(
                update={"cash_cents": 100001}
            )
        ]
    elif failure == "owner":
        originals.reconciliation.user_id = UUID(int=7199)
    elif failure == "clock":
        originals.protection.as_of = NOW.replace(hour=2)
    elif failure == "component":
        point = originals.protection.projection.original_execution_view.calculation_trace[0]
        originals.protection.projection.original_execution_view.calculation_trace = [
            point.model_copy(update={"protected_cents_by_reason": {"goal_cash": 80000}})
        ]
    elif failure == "goal":
        originals.reconciliation.goal_ownership = [
            originals.reconciliation.goal_ownership[0].model_copy(
                update={"current_ownership_proof_verified": False}
            )
        ]
    else:
        originals.model = FullGoalModelResponse(
            goal_id=GOAL,
            policy_id=UUID(int=7140),
            base_policy_version_id=GOAL_VERSION,
            epoch_id=EPOCH,
            status="MODEL_MISSING",
        )
    if failure in {"owner", "clock"}:
        with pytest.raises(PolicyLifecycleError) as error:
            service.preview_goal_reallocation(session, USER, request(), NOW)
        assert error.value.code == "REALLOCATION_SOURCE_BINDING_MISMATCH"
    else:
        result = service.preview_goal_reallocation(session, USER, request(), NOW)
        assert result.decision.candidate_amount_cents is None
        if failure == "model":
            assert result.decision.math.minimum_repair_cents == 25000
            assert result.decision.math.source_cash_releasable_above_minimum_cents is None
        else:
            assert (
                result.decision.math.status == "UNKNOWN"
                and result.decision.math.minimum_repair_cents is None
            )


@pytest.mark.parametrize("binding", ["epoch", "goal_version", "policy_version"])
def test_current_identity_binding_rejects_stale_client_reference(
    binding: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, _ = setup(monkeypatch)
    field = {
        "epoch": "expected_epoch_id",
        "goal_version": "expected_goal_policy_version_id",
        "policy_version": "expected_policy_version_id",
    }[binding]
    with pytest.raises(PolicyLifecycleError):
        service.preview_goal_reallocation(
            session, USER, request().model_copy(update={field: UUID(int=7199)}), NOW
        )


def test_readonly_snapshot_requirement_is_not_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    session, _ = setup(monkeypatch)

    def reject(session: Session) -> None:
        raise PolicyLifecycleError("INVALID_READ_SNAPSHOT", "real RR read-only is required", 409)

    monkeypatch.setattr(service, "_read_snapshot", reject)
    with pytest.raises(PolicyLifecycleError, match="RR read-only"):
        service.preview_goal_reallocation(session, USER, request(), NOW)


def test_inflight_claim_overlapping_owned_cash_never_fakes_a_larger_unique_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _ = setup(monkeypatch)
    original_scalar = session.scalar

    def scalar(query: Any) -> Any:
        return (
            original_scalar(query)
            if query.column_descriptions[0].get("entity") in {User, Goal}
            else 1
        )

    claim = ActionResourceReservation(
        id=UUID(int=7149),
        user_id=USER,
        created_at=NOW,
        resource_kind="CASH",
        resource_key=str(UUID(int=7141)),
        amount_cents=10000,
        status="RESERVED",
    )
    monkeypatch.setattr(session, "scalar", scalar)
    monkeypatch.setattr(session, "scalars", lambda query: [claim])
    result = service.preview_goal_reallocation(session, USER, request(), NOW)
    assert result.decision.math.status == "UNKNOWN"
    assert result.decision.math.minimum_repair_cents is None
    assert not result.decision.math.unique_minimum_for_registered_current_scope
    assert not any(
        row.code == "CURRENT_RESERVATION_INVENTORY_INCOMPLETE" for row in result.source_issues
    )
    assert any(
        row.code == "CURRENT_INFLIGHT_RESOURCE_ATTRIBUTION_NOT_IMPLEMENTED"
        for row in result.source_issues
    )


def test_original_pending_exposure_without_generic_claim_rows_also_keeps_math_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, originals = setup(monkeypatch)
    point = originals.protection.projection.full_annual_projection.calculation_trace[0]
    originals.protection.projection.full_annual_projection.calculation_trace = [
        point.model_copy(
            update={
                "protected_cents_by_reason": point.protected_cents_by_reason
                | {"pending_cash_reservations": 10000}
            }
        )
    ]
    result = service.preview_goal_reallocation(session, USER, request(), NOW)
    assert (
        result.decision.math.status == "UNKNOWN"
        and result.decision.math.minimum_repair_cents is None
    )
    assert any(
        row.code == "CURRENT_INFLIGHT_EXPOSURE_ATTRIBUTION_NOT_IMPLEMENTED"
        for row in result.source_issues
    )
