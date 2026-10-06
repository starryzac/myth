"""Synthetic algorithm/HTTP risks, never financial or browser acceptance evidence."""

import copy
import json
from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from app.api.dependencies import get_demo_user, get_now, get_session
from app.api.v1.scenario_risk_review import router
from app.domain.boundary import compute_boundary
from app.domain.boundary_types import (
    BoundaryPolicyVersion,
    GoalMonthFact,
    GoalOwnership,
    SourceIssue,
)
from app.domain.full_asset_allocation import FullAssetPlanningInput
from app.domain.full_protection_projection import (
    FullProtectionProjectionInput,
    project_full_protection,
)
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.domain.scenario_risk_review import (
    BillHypothesis,
    GoalHypothesis,
    ProductHypothesis,
    ScenarioRiskRequest,
    bill_curve,
    goal_curve,
    product_sensitivity,
    retain_full_floors,
)
from app.services import scenario_risk_review as service
from app.services.boundary import BoundaryContext, Sources
from app.services.dashboard_types import DashboardAuditCard
from app.services.full_projection import FutureIncomeProjection, _checkpoint
from app.services.full_protection_projection import FullAnnualProtectionResponse
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.product_catalog import VerifiedCatalogProducts
from app.tests.test_asset_allocation import product
from app.tests.test_full_asset_allocation import request as product_input
from app.tests.test_full_protection_projection import dated, source
from app.tests.test_scenario_simulation import EPOCH, USER, basis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session


def bill_case() -> tuple[Any, Any]:
    original = basis()
    original = original.model_copy(
        update={
            "snapshot": original.snapshot.model_copy(
                update={
                    "horizon_days": 365,
                    "bills": [
                        original.snapshot.bills[0].model_copy(
                            update={
                                "paid_cents": 100,
                                "total_cents": 500,
                                "status": "PARTIALLY_PAID",
                                "evidence_ids": [UUID(int=100)],
                            }
                        )
                    ],
                }
            )
        }
    )
    projection = project_full_protection(
        FullProtectionProjectionInput(
            snapshot=original.snapshot,
            boundary_versions=original.versions,
            positions=original.positions,
            boundary_products=original.products,
            policies=[source("DatedExpensePolicy", dated(amount=100))],
        )
    )
    return original, projection


def bill_hypothesis(**changes: Any) -> BillHypothesis:
    return BillHypothesis(
        kind="BILL", bill_id=UUID(int=22), remaining_due_cents=300, due_offset_days=0, **changes
    )


def test_bill_remaining_is_hypothetical_preserves_paid_original_and_full_floors() -> None:
    original, full = bill_case()
    before = copy.deepcopy(original.model_dump(mode="json"))
    bill, result = bill_curve(
        original.snapshot,
        original.versions,
        original.positions,
        original.products,
        bill_hypothesis(),
    )
    curve = retain_full_floors(full.original_annual_projection, full.full_annual_projection, result)
    assert curve is not None and full.full_annual_projection is not None
    assert curve.safe_idle_cents == full.full_annual_projection.safe_idle_cents + 100
    assert curve.minimum_margin_cents == curve.safe_idle_cents
    assert len(curve.calculation_trace) == 1098
    assert all(
        point.protected_cents_by_reason["full_dated_expense"] >= 0
        for point in curve.calculation_trace
    )
    assert bill.paid_cents == 100 and bill.total_cents == 500 and bill.evidence_ids
    assert original.model_dump(mode="json") == before
    assert not curve.financial_capacity_is_authority


def test_bill_hypothesis_does_not_release_unknown_original_or_candidate() -> None:
    original, full = bill_case()
    unknown = compute_boundary(
        original.snapshot.model_copy(
            update={"source_issues": [SourceIssue(code="MISSING", entity_type="source")]}
        ),
        original.versions,
        original.positions,
        original.products,
    )
    assert (
        retain_full_floors(full.original_annual_projection, None, full.original_annual_projection)
        is None
    )
    assert (
        retain_full_floors(full.original_annual_projection, full.full_annual_projection, unknown)
        is None
    )


def test_full_source_account_risk_remains_risk_even_if_aggregate_margin_positive() -> None:
    original, full = bill_case()
    _, result = bill_curve(
        original.snapshot,
        original.versions,
        original.positions,
        original.products,
        bill_hypothesis(),
    )
    curve = retain_full_floors(
        full.original_annual_projection,
        full.full_annual_projection,
        result,
        source_account_risk=True,
    )
    assert curve is not None and curve.status == "LIQUIDITY_RISK"
    assert curve.minimum_margin_cents > 0 and curve.safe_idle_cents == 0


@pytest.mark.parametrize("mutation", ["missing_phase", "more_cash", "released_floor"])
def test_tampered_full_overlay_cannot_claim_known_curve(mutation: str) -> None:
    _, full = bill_case()
    assert full.full_annual_projection is not None
    altered = full.full_annual_projection.model_copy(deep=True)
    if mutation == "missing_phase":
        altered.calculation_trace.pop()
    elif mutation == "more_cash":
        altered.calculation_trace[0] = altered.calculation_trace[0].model_copy(
            update={"cash_cents": 1001}
        )
    else:
        altered.calculation_trace[0] = altered.calculation_trace[0].model_copy(
            update={"protected_cents_by_reason": {"emergency_buffer": 0}}
        )
    with pytest.raises(ValueError):
        retain_full_floors(
            full.original_annual_projection, altered, full.original_annual_projection
        )


def test_bill_foreign_source_and_due_before_statement_rejected() -> None:
    original, _ = bill_case()
    for change in (
        bill_hypothesis().model_copy(update={"bill_id": UUID(int=999)}),
        bill_hypothesis().model_copy(update={"due_offset_days": -365}),
    ):
        with pytest.raises(ValueError):
            bill_curve(
                original.snapshot, original.versions, original.positions, original.products, change
            )


def goal_case() -> tuple[Any, GoalHypothesis]:
    original = basis()
    config = validate_configuration(
        {
            "type": "goal_saving",
            "target_cents": 500,
            "deadline": "2027-02-01",
            "monthly_contribution": {"min_cents": 50, "target_cents": 60, "max_cents": 100},
        }
    )
    version = BoundaryPolicyVersion(
        policy_id=UUID(int=70),
        version_id=UUID(int=71),
        configuration=config,
        content_hash=configuration_hash(config),
        confirmed_at=original.snapshot.as_of,
        valid_from=original.snapshot.as_of,
        evidence_ids=[UUID(int=72)],
    )
    original = original.model_copy(
        update={
            "versions": [*original.versions, version],
            "snapshot": original.snapshot.model_copy(
                update={
                    "horizon_days": 365,
                    "goal_month_contributions": [
                        GoalMonthFact(
                            goal_id=UUID(int=73),
                            period="2026-10",
                            contributed_cents=0,
                            evidence_ids=[UUID(int=75)],
                        )
                    ],
                    "goals": [
                        GoalOwnership(
                            goal_id=UUID(int=73),
                            policy_id=version.policy_id,
                            cash_owned_cents=50,
                            principal_owned_cents=0,
                            allocated_cents=50,
                            evidence_ids=[UUID(int=74)],
                        )
                    ],
                }
            ),
        }
    )
    return original, GoalHypothesis(
        kind="GOAL",
        goal_id=UUID(int=73),
        expected_version_id=version.version_id,
        monthly_min_cents=150,
        monthly_target_cents=160,
        monthly_max_cents=200,
        deadline_offset_days=10,
    )


def test_goal_parameters_use_original_hypothesis_engine_and_keep_ownership() -> None:
    original, hypothesis = goal_case()
    before = original.model_dump(mode="json")
    version, candidate, result = goal_curve(
        USER,
        original.snapshot,
        original.versions,
        original.positions,
        original.products,
        hypothesis,
    )
    base = compute_boundary(
        original.snapshot, original.versions, original.positions, original.products
    )
    assert result.safe_idle_cents is not None and base.safe_idle_cents is not None
    assert result.safe_idle_cents <= base.safe_idle_cents
    assert candidate["monthly_contribution"]["min_cents"] == 150
    assert candidate["deadline"] == "2027-02-11"
    assert version.version_id == hypothesis.expected_version_id
    assert original.model_dump(mode="json") == before


def test_goal_wrong_current_version_and_unordered_amounts_rejected() -> None:
    original, hypothesis = goal_case()
    with pytest.raises(ValueError):
        goal_curve(
            USER,
            original.snapshot,
            original.versions,
            original.positions,
            original.products,
            hypothesis.model_copy(update={"expected_version_id": UUID(int=900)}),
        )
    with pytest.raises(ValidationError):
        GoalHypothesis.model_validate({**hypothesis.model_dump(), "monthly_max_cents": 1})


def product_hypothesis(data: FullAssetPlanningInput, **changes: Any) -> ProductHypothesis:
    row = data.products[0]
    return ProductHypothesis.model_validate(
        {
            "kind": "PRODUCT",
            "product_id": row.product_id,
            "expected_version_number": row.version_number,
            "asset_policy_id": data.policy_id,
            "expected_policy_version_id": data.policy_version_id,
            "risk_level": row.risk_level,
            "lock_days": row.lock_days,
            "redemption_delay_days": row.redemption_delay_days,
            "minimum_purchase_cents": row.minimum_purchase_cents,
            "early_withdrawal_loss_bps": row.early_withdrawal_loss_bps,
            **changes,
        }
    )


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"risk_level": 1}, "RISK_LEVEL_EXCEEDS_CONFIRMED_CAP"),
        ({"lock_days": 365}, "NO_CONFIRMED_REDEMPTION_AFTER_LOCK"),
        ({"redemption_delay_days": 2}, "REDEMPTION_DELAY_EXCEEDS_CONFIRMED_CAP"),
    ],
)
def test_product_risk_lock_delay_are_consumed_by_original_optimizer(
    changes: dict[str, int], reason: str
) -> None:
    data = product_input(products=[product_input().products[0]], cash=1000, cap=1000)
    original = data.model_dump(mode="json")
    result = product_sensitivity(data, product_hypothesis(data, **changes))
    assert result.before.status == "OPTIMAL"
    assert result.after.total_purchase_cents == 0
    assert any(reason in row.reasons for row in result.after.candidates)
    assert data.model_dump(mode="json") == original
    assert result.hypothetical_product.product_id == result.original_product.product_id
    assert not result.quotation_verified and not result.full_protection_consumed_by_optimizer


def test_product_minimum_is_consumed_and_loss_bound_does_not_claim_real_charge() -> None:
    data = product_input(products=[product(10, bps=10000)], cash=31, cap=31)
    higher = product_sensitivity(data, product_hypothesis(data, minimum_purchase_cents=32))
    # 31 * 89 // 365 == 7; the original least-turnover tie break chooses
    # ceil(7 * 365 / 89) == 29 cents to obtain that same rounded yield.
    expected_principal = (7 * 365 + 88) // 89
    assert expected_principal == 29
    assert higher.before.total_purchase_cents == expected_principal
    assert higher.after.total_purchase_cents == 0
    loss = product_sensitivity(data, product_hypothesis(data, early_withdrawal_loss_bps=3333))
    assert loss.before.batches == loss.after.batches
    assert (
        loss.hypothetical_early_loss_upper_bound_cents
        == (expected_principal * 3333 + 9999) // 10000
    )
    assert loss.actual_loss_cents is None and not loss.early_loss_consumed_by_optimizer


def captured() -> service.Captured:
    original, projection = bill_case()
    zone = original.snapshot.timezone
    first = original.snapshot.as_of.date()
    rows = (
        projection.full_annual_projection.calculation_trace
        if projection.full_annual_projection
        else []
    )
    audit = DashboardAuditCard(
        epoch_id=EPOCH, anchored_run_statuses={}, status="VALID", complete=True
    )
    annual = FullAnnualProtectionResponse(
        user_id=USER,
        as_of=original.snapshot.as_of,
        projection=projection,
        initial_checkpoint=_checkpoint(0, first, rows[:3]),
        daily_checkpoints=[
            _checkpoint(day, first + timedelta(days=day), rows[day * 3 : day * 3 + 3])
            for day in range(1, 366)
        ],
        full_policy_sources=[],
        future_income=FutureIncomeProjection(),
        source_evidence_ids=[],
        source_issues=[],
        input_digest="c" * 64,
        audit=audit,
        limitations=[],
    )
    digest, files = service.engine_sources()
    curve = retain_full_floors(
        projection.original_annual_projection,
        projection.full_annual_projection,
        projection.original_annual_projection,
    )
    review = service.ScenarioRiskContext(
        user_id=USER,
        epoch_id=EPOCH,
        as_of=original.snapshot.as_of,
        timezone=zone,
        local_date=first.isoformat(),
        source_hash="a" * 64,
        engine_hash=digest,
        engine_files=files,
        bills=original.snapshot.bills,
        goals=[],
        full_policies=[],
        products=[],
        catalogue_status="UNKNOWN",
        baseline_full=curve,
        original_full_protection=annual,
        source_issues=[],
        inventory_counts={
            "bills": 1,
            "goals": 0,
            "goal_choices": 0,
            "full_policies": 0,
            "catalogue_products": 0,
            "eligible_product_choices": 0,
        },
    )
    return service.Captured(
        BoundaryContext(
            snapshot=original.snapshot,
            versions=original.versions,
            positions=original.positions,
            products=original.products,
            sources=Sources(USER, original.snapshot.as_of, []),
        ),
        [],
        audit,
        annual,
        [],
        VerifiedCatalogProducts(
            status="UNKNOWN", products=[], bindings=[], issues=["NO_REGISTERED_CATALOG"]
        ),
        review,
    )


def test_real_json_uuid_http_reaches_original_engine_and_replays_without_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = captured()
    monkeypatch.setattr(service, "_capture", lambda *args: data)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: SimpleNamespace(no_autoflush=nullcontext())
    app.dependency_overrides[get_demo_user] = lambda: SimpleNamespace(id=USER)
    app.dependency_overrides[get_now] = lambda: data.review.as_of
    body = {
        "expected_epoch_id": str(EPOCH),
        "expected_source_hash": data.review.source_hash,
        "expected_engine_hash": data.review.engine_hash,
        "hypothesis": bill_hypothesis().model_dump(mode="json"),
    }
    originals = data.review.model_dump(mode="json")
    with TestClient(app) as client:
        assert client.get("/api/v1/scenario-risk-review/context").status_code == 200
        first = client.post("/api/v1/scenario-risk-review/compare", json=body)
        second = client.post("/api/v1/scenario-risk-review/compare", json=body)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    result = first.json()
    assert result["status"] == "PROJECTED" and result["delta_safe_idle_cents"] == 100
    assert result["original_selected"]["paid_cents"] == 100
    assert result["hypothetical_selected"]["original_paid_cents_retained"] == 100
    assert result["request_hash"] == configuration_hash(body)
    assert not result["writes_facts"] and not result["changes_bank_originals"]
    assert data.review.model_dump(mode="json") == originals


@pytest.mark.parametrize(
    "field,value",
    [
        ("result", 100000),
        ("clock", "2029-01-01"),
        ("user_id", str(USER)),
        ("receipt", {}),
        ("authority", True),
    ],
)
def test_client_results_bank_identity_and_clock_are_not_request_fields(
    field: str, value: Any
) -> None:
    data = captured()
    payload = {
        "expected_epoch_id": str(EPOCH),
        "expected_source_hash": "a" * 64,
        "expected_engine_hash": data.review.engine_hash,
        "hypothesis": bill_hypothesis().model_dump(mode="json"),
        field: value,
    }
    with pytest.raises(ValidationError):
        ScenarioRiskRequest.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("bad", [True, "100", -1, 10_000_001])
def test_bill_amount_strict_and_bounded(bad: Any) -> None:
    with pytest.raises(ValidationError):
        BillHypothesis.model_validate(
            {**bill_hypothesis().model_dump(), "remaining_due_cents": bad}
        )


def test_stale_actual_source_rejected_before_hypothesis_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = captured()
    monkeypatch.setattr(service, "_capture", lambda *args: data)
    with pytest.raises(PolicyLifecycleError, match="轮次"):
        service.compare_scenario_risk(
            cast(Session, SimpleNamespace()),
            USER,
            data.review.as_of,
            ScenarioRiskRequest(
                expected_epoch_id=EPOCH,
                expected_source_hash="f" * 64,
                expected_engine_hash=data.review.engine_hash,
                hypothesis=bill_hypothesis(),
            ),
        )


def test_write_or_non_readonly_session_cannot_capture_sources() -> None:
    for pending in ("new", "dirty", "deleted"):
        session = SimpleNamespace(new=[], dirty=[], deleted=[])
        setattr(session, pending, [object()])
        with pytest.raises(PolicyLifecycleError):
            service.read_scenario_risk_context(cast(Session, session), USER, basis().snapshot.as_of)


@pytest.mark.parametrize("mutation", ["query", "nested_receipt", "bool_money"])
def test_real_http_extra_query_and_forged_originals_stop_before_capture(
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    data = captured()
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(service, "_capture", lambda *args: calls.append(args))
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: SimpleNamespace()
    app.dependency_overrides[get_demo_user] = lambda: SimpleNamespace(id=USER)
    app.dependency_overrides[get_now] = lambda: data.review.as_of
    body: dict[str, Any] = {
        "expected_epoch_id": str(EPOCH),
        "expected_source_hash": data.review.source_hash,
        "expected_engine_hash": data.review.engine_hash,
        "hypothesis": bill_hypothesis().model_dump(mode="json"),
    }
    path = "/api/v1/scenario-risk-review/compare"
    if mutation == "query":
        path += "?clock=2027-01-01"
    elif mutation == "nested_receipt":
        body["hypothesis"]["bank_receipt"] = {"status": "SETTLED"}
    else:
        body["hypothesis"]["remaining_due_cents"] = True
    with TestClient(app) as client:
        response = client.post(path, json=body)
    assert response.status_code == 422
    assert not calls
