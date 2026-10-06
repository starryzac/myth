"""Hand-worked SYNTHETIC algorithm and API-shape risks; not bank/PG/browser evidence."""

import copy
from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from app.api.dependencies import get_demo_user, get_now, get_session
from app.api.v1.scenario_simulation import router
from app.domain.boundary import compute_boundary
from app.domain.boundary_types import (
    BillFact,
    BoundaryPolicyVersion,
    BoundaryPosition,
    BoundaryProduct,
    BoundarySnapshot,
    CashFact,
    FixedReturnTerms,
    GoalOwnership,
    SourceIssue,
)
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.domain.scenario_simulation import (
    ScenarioBasis,
    ScenarioCompareRequest,
    choices,
    compare_scenario,
)
from app.services.boundary import BoundaryContext, Sources
from app.services.dashboard_types import DashboardAuditCard
from app.services.financial_read import finalize_financial_context
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.scenario_simulation import (
    engine_sources,
    read_scenario_context,
    source_fingerprint,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

NOW = datetime(2026, 10, 5, 0, tzinfo=UTC)
USER, EPOCH, CASH, POLICY, VERSION, PRODUCT = [UUID(int=i) for i in range(1, 7)]


def basis() -> ScenarioBasis:
    configuration = validate_configuration({"type": "emergency_buffer", "amount_cents": 200})
    future = validate_configuration(
        {"type": "emergency_buffer", "amount_cents": 200, "valid_from": "2026-10-25"}
    )
    return ScenarioBasis(
        user_id=USER,
        epoch_id=EPOCH,
        source_hash="a" * 64,
        engine_hash="b" * 64,
        snapshot=BoundarySnapshot(
            as_of=NOW,
            timezone="UTC",
            cash_accounts=[
                CashFact(
                    account_id=CASH,
                    account_type="CASH",
                    balance_cents=1000,
                    observed_at=NOW,
                    evidence_ids=[UUID(int=21)],
                ),
                CashFact(
                    account_id=UUID(int=23),
                    account_type="CREDIT_CARD",
                    balance_cents=0,
                    observed_at=NOW,
                ),
            ],
            bills=[
                BillFact(
                    bill_id=UUID(int=22),
                    account_id=UUID(int=23),
                    statement_date=date(2026, 10, 5),
                    due_date=date(2026, 10, 15),
                    total_cents=400,
                    paid_cents=0,
                    status="UNPAID",
                )
            ],
        ),
        versions=[
            BoundaryPolicyVersion(
                policy_id=POLICY,
                version_id=VERSION,
                configuration=configuration,
                content_hash=configuration_hash(configuration),
                confirmed_at=NOW,
                valid_from=NOW,
                evidence_ids=[UUID(int=24)],
            ),
            BoundaryPolicyVersion(
                policy_id=UUID(int=40),
                version_id=UUID(int=41),
                configuration=future,
                content_hash=configuration_hash(future),
                confirmed_at=NOW,
                valid_from=NOW + timedelta(days=20),
                evidence_ids=[UUID(int=42)],
            ),
        ],
        positions=[BoundaryPosition(position_id=UUID(int=25), principal_cents=100, status="HELD")],
        products=[
            BoundaryProduct(
                product_id=PRODUCT,
                version_number=1,
                asset_class="FIXED_DEPOSIT",
                minimum_purchase_cents=50,
                fixed_return=FixedReturnTerms(term_days=30, settlement_delay_days=0),
                terms_digest="c" * 64,
            )
        ],
    )


def request(**changes: Any) -> ScenarioCompareRequest:
    return ScenarioCompareRequest.model_validate(
        {
            "expected_epoch_id": EPOCH,
            "expected_source_hash": "a" * 64,
            "expected_engine_hash": "b" * 64,
            **changes,
        }
    )


def test_no_op_and_explicit_replay_are_original_engine_and_do_not_mutate_originals() -> None:
    original = basis()
    before = copy.deepcopy(original.model_dump(mode="json"))
    body = request()
    result = compare_scenario(original, body)
    assert result.baseline == compute_boundary(
        original.snapshot, original.versions, original.positions, original.products
    )
    assert result.hypothetical == result.baseline
    assert result.delta_safe_idle_cents == 0
    assert compare_scenario(original, body) == result
    assert original.model_dump(mode="json") == before
    assert (
        result.grants_authority
        is result.executes_funds
        is result.writes_facts
        is result.receipt_verified
        is False
    )
    assert result.resets_history is result.economic_verified is False
    assert result.future_income_included_cents == 0


def test_cash_reserve_product_parameters_recompute_real_three_phase_engine() -> None:
    original = basis()
    before = copy.deepcopy(original.model_dump(mode="json"))
    result = compare_scenario(
        original,
        request(
            cash_change={"account_id": CASH, "delta_cents": 100},
            emergency_change={
                "policy_id": POLICY,
                "expected_version_id": VERSION,
                "amount_cents": 300,
            },
            product_change={
                "product_id": PRODUCT,
                "expected_version_number": 1,
                "term_days": 2,
                "settlement_delay_days": 0,
            },
        ),
    )
    assert result.baseline.safe_idle_cents == result.hypothetical.safe_idle_cents == 200
    assert result.baseline.max_allocatable_by_product[str(PRODUCT)] == 200
    assert result.hypothetical.max_allocatable_by_product[str(PRODUCT)] == 400
    assert result.hypothetical.calculation_trace[0].cash_cents == 1100
    assert result.hypothetical.calculation_trace[0].protected_cents_by_reason["emergency"] == 300
    assert len(result.hypothetical.calculation_trace) == 91 * 3
    assert not any(p.principal_position_ids for p in result.hypothetical.calculation_trace)
    assert original.model_dump(mode="json") == before
    assert [p.kind for p in result.changed_parameters] == [
        "CASH_BALANCE_DELTA",
        "PRODUCT_OCCUPANCY",
        "EMERGENCY_AMOUNT",
    ]


def test_365_is_366_dates_three_phases_and_never_adds_future_income() -> None:
    result = compare_scenario(basis(), request(horizon_days=365))
    assert len(result.hypothetical.calculation_trace) == 366 * 3
    assert result.hypothetical.calculation_trace[-1].date == date(2027, 10, 5)
    assert result.future_income_included_cents == 0


def test_unknown_source_remains_null_after_optimistic_cash_assumption() -> None:
    current = basis()
    current.snapshot.source_issues.append(
        SourceIssue(code="MISSING", entity_type="source", entity_id="original")
    )
    result = compare_scenario(
        current, request(cash_change={"account_id": CASH, "delta_cents": 5000})
    )
    assert result.baseline.status == result.hypothetical.status == "INSUFFICIENT_EVIDENCE"
    assert result.delta_safe_idle_cents is result.hypothetical.safe_idle_cents is None
    assert result.hypothetical.calculation_trace == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("expected_epoch_id", UUID(int=999)),
        ("expected_source_hash", "d" * 64),
        ("expected_engine_hash", "e" * 64),
    ],
)
def test_changed_epoch_original_or_engine_is_rejected(field: str, value: Any) -> None:
    with pytest.raises(ValueError, match="differs"):
        compare_scenario(basis(), request(**{field: value}))


@pytest.mark.parametrize(
    "change",
    [
        {"cash_change": {"account_id": UUID(int=999), "delta_cents": 1}},
        {"cash_change": {"account_id": CASH, "delta_cents": -1001}},
        {
            "emergency_change": {
                "policy_id": POLICY,
                "expected_version_id": UUID(int=999),
                "amount_cents": 0,
            }
        },
        {
            "emergency_change": {
                "policy_id": UUID(int=999),
                "expected_version_id": VERSION,
                "amount_cents": 0,
            }
        },
        {
            "product_change": {
                "product_id": PRODUCT,
                "expected_version_number": 2,
                "term_days": 1,
                "settlement_delay_days": 0,
            }
        },
    ],
)
def test_foreign_entities_negative_cash_and_changed_versions_do_not_become_hypotheses(
    change: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        compare_scenario(basis(), request(**change))


def test_goal_or_credit_cash_and_unproven_products_are_not_editable() -> None:
    current = basis()
    current.snapshot.goals.append(
        GoalOwnership(
            goal_id=UUID(int=30),
            policy_id=UUID(int=31),
            account_id=CASH,
            cash_owned_cents=300,
            principal_owned_cents=0,
            allocated_cents=300,
        )
    )
    cash, _, _ = choices(current)
    assert cash == []
    with pytest.raises(ValueError):
        compare_scenario(current, request(cash_change={"account_id": CASH, "delta_cents": 1}))
    current = basis()
    current.products[0] = current.products[0].model_copy(update={"fixed_return": None})
    assert choices(current)[2] == []
    with pytest.raises(ValueError):
        compare_scenario(
            current,
            request(
                product_change={
                    "product_id": PRODUCT,
                    "expected_version_number": 1,
                    "term_days": 1,
                    "settlement_delay_days": 0,
                }
            ),
        )


@pytest.mark.parametrize(
    "extra",
    ["result", "receipt", "user_id", "now", "as_of", "grants_authority", "reset", "bank_result"],
)
def test_input_forbids_forged_results_authority_clocks_and_history_reset(extra: str) -> None:
    with pytest.raises(ValidationError):
        request(**{extra: {"success": True}})


@pytest.mark.parametrize(
    "change",
    [
        {"horizon_days": True},
        {"horizon_days": 91},
        {"cash_change": {"account_id": CASH, "delta_cents": True}},
        {"cash_change": {"account_id": CASH, "delta_cents": 10_000_001}},
        {
            "emergency_change": {
                "policy_id": POLICY,
                "expected_version_id": VERSION,
                "amount_cents": -1,
            }
        },
        {
            "product_change": {
                "product_id": PRODUCT,
                "expected_version_number": 1,
                "term_days": 366,
                "settlement_delay_days": 0,
            }
        },
        {
            "product_change": {
                "product_id": PRODUCT,
                "expected_version_number": 1,
                "term_days": 365,
                "settlement_delay_days": 1,
            }
        },
    ],
)
def test_parameter_budgets_and_strict_numbers(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        request(**change)


def test_fingerprint_clock_exclusion_does_not_exclude_original_fact_or_epoch_changes() -> None:
    current = basis()
    ctx = BoundaryContext(
        current.snapshot,
        current.versions,
        current.positions,
        current.products,
        Sources(USER, NOW, []),
    )
    audit = DashboardAuditCard(
        epoch_id=EPOCH, status="VALID", complete=True, anchored_run_statuses={}
    )
    old = source_fingerprint(ctx, audit)
    ctx = replace(
        ctx,
        snapshot=ctx.snapshot.model_copy(
            update={"as_of": NOW + timedelta(seconds=1), "source_digest": "f" * 64}
        ),
    )
    assert source_fingerprint(ctx, audit) == old
    ctx.snapshot.cash_accounts[0] = ctx.snapshot.cash_accounts[0].model_copy(
        update={"balance_cents": 1001}
    )
    assert source_fingerprint(ctx, audit) != old
    ctx = replace(ctx, snapshot=basis().snapshot)
    assert source_fingerprint(ctx, audit.model_copy(update={"epoch_id": UUID(int=99)})) != old
    ctx = replace(
        ctx, snapshot=basis().snapshot.model_copy(update={"as_of": NOW + timedelta(days=1)})
    )
    assert source_fingerprint(ctx, audit) != old


def test_actual_engine_digest_contains_original_algorithm_and_new_adapter_bytes() -> None:
    digest, originals = engine_sources()
    assert digest == configuration_hash(originals)
    assert "domain/boundary.py" in originals and "services/scenario_simulation.py" in originals
    assert all(len(value) == 64 for value in originals.values())


@pytest.mark.parametrize("bad", ["dirty", "writable", "read_committed"])
def test_service_rejects_non_readonly_or_dirty_transaction_before_loading_sources(bad: str) -> None:
    class UnsafeSession:
        new: list[Any] = []
        dirty = [object()] if bad == "dirty" else []
        deleted: list[Any] = []
        no_autoflush = __import__("contextlib").nullcontext()

        def connection(self) -> Any:
            return SimpleNamespace(
                get_isolation_level=lambda: (
                    "READ COMMITTED" if bad == "read_committed" else "REPEATABLE READ"
                )
            )

        def scalar(self, statement: Any) -> str:
            return "off" if bad == "writable" else "on"

    with pytest.raises(PolicyLifecycleError, match="只读"):
        read_scenario_context(UnsafeSession(), USER, NOW)  # type: ignore[arg-type]


def test_actual_router_rejects_injected_results_and_query_without_financial_dependencies() -> None:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = object
    app.dependency_overrides[get_demo_user] = lambda: SimpleNamespace(id=USER)
    client = TestClient(app)
    body = request().model_dump(mode="json")
    for field in ("result", "receipt", "clock", "user_id", "authorization"):
        response = client.post(
            "/api/v1/scenario-simulation/compare", json={**body, field: {"success": True}}
        )
        assert response.status_code == 422
    assert client.get("/api/v1/scenario-simulation/context?now=2020-01-01").status_code == 422
    assert (
        client.post("/api/v1/scenario-simulation/compare?reset=true", json=body).status_code == 422
    )


def test_actual_fastapi_json_uuid_request_reaches_original_service_and_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SYNTHETIC read-source doubles; original route/service/engine, no PG or bank proof."""
    original = basis()
    original_bytes = original.model_dump(mode="json")
    loaded: list[tuple[UUID, datetime]] = []

    class SyntheticReadOnlySession:
        new: list[Any] = []
        dirty: list[Any] = []
        deleted: list[Any] = []
        no_autoflush = nullcontext()

        def connection(self) -> Any:
            return SimpleNamespace(get_isolation_level=lambda: "REPEATABLE READ")

        def scalar(self, statement: Any) -> str:
            assert str(statement) == "SHOW transaction_read_only"
            return "on"

    session = SyntheticReadOnlySession()

    def synthetic_sources(
        actual_session: Any, actual_user: UUID, actual_now: datetime
    ) -> tuple[BoundaryContext, None, None]:
        assert actual_session is session
        assert actual_user == USER and actual_now == NOW
        loaded.append((actual_user, actual_now))
        copied = original.model_copy(deep=True)
        return (
            BoundaryContext(
                copied.snapshot,
                copied.versions,
                copied.positions,
                copied.products,
                Sources(USER, NOW, []),
            ),
            None,
            None,
        )

    def synthetic_audit(actual_session: Any, actual_user: UUID, issues: Any) -> DashboardAuditCard:
        assert actual_session is session and actual_user == USER
        return DashboardAuditCard(
            epoch_id=EPOCH, status="VALID", complete=True, anchored_run_statuses={}
        )

    monkeypatch.setattr(
        "app.services.scenario_simulation.load_verified_financial_context", synthetic_sources
    )
    monkeypatch.setattr("app.services.scenario_simulation.current_epoch_audit", synthetic_audit)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_demo_user] = lambda: SimpleNamespace(id=USER)
    app.dependency_overrides[get_now] = lambda: NOW
    client = TestClient(app)
    context = client.get("/api/v1/scenario-simulation/context")
    assert context.status_code == 200, context.text
    observed = context.json()
    assert observed["engine_hash"] == engine_sources()[0]
    body: dict[str, Any] = {
        "expected_epoch_id": str(EPOCH),
        "expected_source_hash": observed["source_hash"],
        "expected_engine_hash": observed["engine_hash"],
        "horizon_days": 90,
        "cash_change": {"account_id": str(CASH), "delta_cents": 100},
        "emergency_change": {
            "policy_id": str(POLICY),
            "expected_version_id": str(VERSION),
            "amount_cents": 300,
        },
        "product_change": {
            "product_id": str(PRODUCT),
            "expected_version_number": 1,
            "term_days": 2,
            "settlement_delay_days": 0,
        },
    }
    response = client.post("/api/v1/scenario-simulation/compare", json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    finalized, _, _ = finalize_financial_context(
        BoundaryContext(
            original.snapshot,
            original.versions,
            original.positions,
            original.products,
            Sources(USER, NOW, []),
        ),
        synthetic_audit(session, USER, []),
    )
    bound_original = original.model_copy(
        update={
            "snapshot": finalized.snapshot,
            "source_hash": observed["source_hash"],
            "engine_hash": observed["engine_hash"],
        }
    )
    expected = compare_scenario(bound_original, ScenarioCompareRequest.model_validate(body))
    assert result == expected.model_dump(mode="json")
    assert result["original_request"] == body
    assert result["request_hash"] == configuration_hash(body)
    assert result["baseline"]["safe_idle_cents"] == 200
    assert result["hypothetical"]["safe_idle_cents"] == 200
    assert result["grants_authority"] is result["executes_funds"] is False
    assert result["writes_facts"] is result["resets_history"] is False
    assert result["receipt_verified"] is result["economic_verified"] is False
    assert loaded == [(USER, NOW), (USER, NOW)]
    assert original.model_dump(mode="json") == original_bytes
    for invalid in (
        {**body, "expected_epoch_id": "not-a-uuid"},
        {**body, "cash_change": {"account_id": str(CASH), "delta_cents": True}},
        {**body, "cash_change": {"account_id": str(CASH), "delta_cents": "100"}},
        {**body, "product_change": {**body["product_change"], "receipt": "fake"}},
        {**body, "result": {"safe_idle_cents": 10_000_000}},
    ):
        assert client.post("/api/v1/scenario-simulation/compare", json=invalid).status_code == 422
    assert (
        client.post("/api/v1/scenario-simulation/compare?reset=true", json=body).status_code == 422
    )
    assert loaded == [(USER, NOW), (USER, NOW)]
