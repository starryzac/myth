"""SYNTHETIC domain, original-reader dispatch and real FastAPI JSON risks; no PG proof."""

import copy
from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from app.api.dependencies import get_demo_user, get_now, get_session
from app.api.v1.full_maturity_replanning import router
from app.db.models import (
    ActionPlan,
    ActionReceipt,
    AssetPosition,
    BankOperation,
    Policy,
    SimulatedBankRedemption,
)
from app.domain.asset_allocation import select_asset
from app.domain.full_asset_allocation import plan_full_assets
from app.domain.full_maturity_replanning import (
    CurrentMaturityPolicy,
    MaturityReplanningRequest,
    OriginalMaturityEvent,
    decide_maturity_replanning,
)
from app.domain.policy_configuration import configuration_hash
from app.services.action_contracts import PrepareActionRequest
from app.services.boundary import BoundaryContext, Sources
from app.services.dashboard_types import DashboardAuditCard
from app.services.full_maturity_replanning import (
    current_asset_policy,
    preview_maturity_replanning,
    read_original_maturity,
)
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.product_catalog import CatalogProductBinding, VerifiedCatalogProducts
from app.tests.test_asset_allocation import (
    NOW,
    POLICY,
    VERSION,
    authorization,
    exposure,
    financial_products,
    product,
    snapshot,
)
from app.tests.test_full_asset_allocation import request as full_request
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

USER, EPOCH, ACTION, POSITION, BANK, RECEIPT = [UUID(int=i) for i in range(101, 107)]


def event(**updates: Any) -> OriginalMaturityEvent:
    return OriginalMaturityEvent.model_validate(
        {
            "action_id": ACTION,
            "position_id": POSITION,
            "original_product_id": UUID(int=20),
            "original_policy_version_id": UUID(int=50),
            "goal_id": None,
            "destination_account_id": UUID(int=1),
            "original_action_status": "SUCCEEDED",
            "bank_operation_id": BANK,
            "original_bank_status": "SETTLED",
            "original_receipt_id": RECEIPT,
            "principal_cents": 50000,
            "settled_at": NOW,
            "proof_status": "VERIFIED",
            "service_receipt_verified": True,
            "originals_hash": configuration_hash({"SYNTHETIC": "not bank or receipt proof"}),
            "originals": {"SYNTHETIC": "not bank or receipt proof"},
            "issues": [],
            **updates,
        }
    )


def current_policy(**updates: Any) -> CurrentMaturityPolicy:
    v = authorization()
    return CurrentMaturityPolicy.model_validate(
        {
            "policy_id": POLICY,
            "version_id": VERSION,
            "kind": "MVP",
            "configuration_hash": v.content_hash,
            "scope": "general_idle_funds",
            "goal_id": None,
            "effective_status": "ACTIVE",
            "current_confirmation_verified": True,
            "current_bank_authority_verified": True,
            "original_configuration": v.configuration,
            **updates,
        }
    )


def current_plan() -> Any:
    products = [product(10), product(11, bps=180)]
    return select_asset(
        snapshot(100000),
        [],
        [],
        financial_products(products),
        authorization(),
        products,
        exposure(),
    )


def decision(**updates: Any) -> Any:
    return decide_maturity_replanning(
        **{
            "epoch_id": EPOCH,
            "event": event(),
            "policy": current_policy(),
            "sources_verified": True,
            "source_hash": "b" * 64,
            "legacy_plan": current_plan(),
            "full_plan": None,
            **updates,
        }
    )


def test_received_event_reselects_current_product_and_only_prepares_current_intent() -> None:
    e, p, plan = event(), current_policy(), current_plan()
    originals = copy.deepcopy(
        [e.model_dump(mode="json"), p.model_dump(mode="json"), plan.model_dump(mode="json")]
    )
    result = decision(event=e, policy=p, legacy_plan=plan)
    assert result.state == "PREPARE_CURRENT_INTENT" and result.candidate is not None
    candidate = result.candidate
    assert candidate.reviewed_product_id == UUID(int=11) != e.original_product_id
    body = PrepareActionRequest.model_validate(candidate.request)
    assert body.intent.kind == "purchase_asset" and body.intent.policy_id == POLICY
    assert set(candidate.request) == {"intent", "idempotency_key"}
    assert set(candidate.request["intent"]) == {"kind", "policy_id"}
    assert candidate.request_hash == configuration_hash(candidate.request)
    assert result.received_principal_cents == 50000
    assert candidate.prepare_recomputes_and_may_differ and candidate.must_review_new_prepared_effect
    assert not result.bank_authority and not result.executes_funds and not result.enqueues_action
    assert not result.event_is_exclusive_funding_reservation and not result.automatic_rollover
    assert result.future_income_added_cents == 0
    assert [
        e.model_dump(mode="json"),
        p.model_dump(mode="json"),
        plan.model_dump(mode="json"),
    ] == originals
    assert decision(event=e, policy=p, legacy_plan=plan) == result


def test_stable_event_version_key_and_explicit_new_version_review() -> None:
    first = decision()
    second = decision(source_hash="c" * 64)
    assert first.input_hash != second.input_hash
    assert first.candidate.request == second.candidate.request
    new_version = UUID(int=51)
    changed = decision(
        policy=current_policy(version_id=new_version),
        legacy_plan=current_plan().model_copy(update={"policy_version_id": new_version}),
    )
    assert (
        changed.candidate.request["idempotency_key"] != first.candidate.request["idempotency_key"]
    )
    assert not changed.executes_funds


@pytest.mark.parametrize("proof", ["NOT_RECEIVED", "UNKNOWN", "INVALID"])
def test_unreceived_or_unresolved_event_never_publishes_cash_or_candidate(proof: str) -> None:
    result = decision(event=event(proof_status=proof, service_receipt_verified=False))
    assert result.state == (
        "ORIGINAL_RETURN_NOT_RECEIVED" if proof == "NOT_RECEIVED" else "UNKNOWN"
    )
    assert result.received_principal_cents is None and result.candidate is None


@pytest.mark.parametrize("reason", ["revoked", "scope", "unverified", "version", "catalogue"])
def test_current_policy_goal_and_source_changes_block_historical_permission_reuse(
    reason: str,
) -> None:
    args: dict[str, Any] = {}
    if reason == "revoked":
        args["policy"] = current_policy(
            current_confirmation_verified=False,
            current_bank_authority_verified=False,
            effective_status="REVOKED",
        )
    if reason == "scope":
        args["event"] = event(goal_id=UUID(int=70))
    if reason in {"unverified", "catalogue"}:
        args["sources_verified"] = False
    if reason == "version":
        args["legacy_plan"] = current_plan().model_copy(update={"policy_version_id": UUID(int=99)})
    result = decision(**args)
    assert result.state in {"BLOCKED", "UNKNOWN"} and result.candidate is None
    assert result.received_principal_cents == 50000


def test_current_cash_selection_retains_cash_and_full_planning_never_becomes_bank_intent() -> None:
    plan = current_plan().model_copy(update={"selected_product_id": None, "suggested_cents": 0})
    assert decision(legacy_plan=plan).state == "RETAIN_CASH"
    full = full_request(cash=100000, cap=100000)
    result = decision(
        policy=current_policy(
            kind="FULL",
            current_bank_authority_verified=False,
            configuration_hash=configuration_hash(full.configuration.model_dump(mode="json")),
            original_configuration=full.configuration.model_dump(mode="json"),
        ),
        legacy_plan=None,
        full_plan=plan_full_assets(full),
    )
    assert result.state == "PLANNING_ONLY" and result.candidate is None
    assert not result.bank_authority


def test_original_event_hash_scope_and_full_non_authority_cannot_be_relabeled() -> None:
    with pytest.raises(ValueError, match="binding hash"):
        event(originals={"altered": True})
    with pytest.raises(ValueError, match="precise policy scope"):
        current_policy(scope="goal", goal_id=UUID(int=70))
    with pytest.raises(ValueError, match="bank grant"):
        current_policy(kind="FULL")


@pytest.mark.parametrize(
    "field",
    [
        "amount_cents",
        "product_id",
        "now",
        "result",
        "receipt",
        "user_id",
        "accepted",
        "grant",
        "reset",
    ],
)
def test_request_only_allows_three_original_identities(field: str) -> None:
    with pytest.raises(ValueError):
        MaturityReplanningRequest.model_validate(
            {
                "maturity_action_id": str(ACTION),
                "current_asset_policy_id": str(POLICY),
                "expected_epoch_id": str(EPOCH),
                field: 1,
            }
        )


class SyntheticSession:
    """Only RO transaction and mapped-row reading protocol, no connection or money proof."""

    new: list[Any] = []
    dirty: list[Any] = []
    deleted: list[Any] = []
    no_autoflush = nullcontext()

    def __init__(
        self, action: Any = None, bank: Any = None, receipt: Any = None, operation: Any = None
    ) -> None:
        self.action, self.bank, self.receipt = action, bank, receipt
        self.operation = operation

    def get(self, model: Any, identifier: Any) -> Any:
        return (
            self.action
            if model in {ActionPlan, Policy}
            else AssetPosition(id=POSITION, status="REDEEMED")
            if model is AssetPosition
            else None
        )

    def scalars(self, statement: Any) -> list[Any]:
        return (
            [self.bank]
            if "simulated_bank_redemptions" in str(statement) and self.bank
            else [self.receipt]
            if "action_receipts" in str(statement) and self.receipt
            else [self.operation]
            if "bank_operations" in str(statement) and self.operation
            else []
        )

    def connection(self) -> Any:
        return SimpleNamespace(get_isolation_level=lambda: "REPEATABLE READ")

    def scalar(self, statement: Any) -> str:
        assert str(statement) == "SHOW transaction_read_only"
        return "on"


def original_action() -> ActionPlan:
    body = {
        "bank_request": {
            "user_id": str(USER),
            "position_id": str(POSITION),
            "position_account_id": str(UUID(int=80)),
            "product_id": str(UUID(int=20)),
            "goal_id": None,
            "original_policy_version_id": str(UUID(int=50)),
            "destination_account_id": str(UUID(int=1)),
            "principal_cents": 50000,
            "requested_at": NOW.isoformat(),
            "available_at": NOW.isoformat(),
            "expires_at": (NOW + timedelta(minutes=15)).isoformat(),
            "kind": "MATURE",
        }
    }
    return ActionPlan(
        id=ACTION,
        user_id=USER,
        created_at=NOW,
        action_type="ASSET_MATURITY",
        position_id=POSITION,
        product_id=UUID(int=20),
        amount_cents=50000,
        policy_version_id=UUID(int=50),
        status="SUCCEEDED",
        request=body,
        request_hash=configuration_hash(body),
    )


def test_actual_original_reader_keeps_settled_bank_without_receipt_unknown() -> None:
    session = SyntheticSession(
        original_action(), SimulatedBankRedemption(id=BANK, status="SETTLED", settled_at=NOW)
    )
    result = read_original_maturity(session, USER, ACTION, NOW)  # type: ignore[arg-type]
    assert result.proof_status == "UNKNOWN" and not result.service_receipt_verified
    assert result.original_bank_status == "SETTLED" and result.original_receipt_id is None
    assert result.originals["bank_requests"][0]["status"] == "SETTLED"
    assert "BANK_MAY_HAVE_SETTLED_BUT_APPLICATION_RECEIPT_UNRESOLVED" in result.issues


def test_actual_unified_bank_settled_without_legacy_receipt_is_unknown_not_no_effect() -> None:
    a = original_action()
    a.status = "UNKNOWN"
    session = SyntheticSession(a, operation=BankOperation(id=BANK, status="SETTLED"))
    result = read_original_maturity(session, USER, ACTION, NOW)  # type: ignore[arg-type]
    assert result.proof_status == "UNKNOWN" and result.original_bank_status == "SETTLED"
    assert result.bank_operation_id == BANK and not result.absence_is_final
    assert result.originals["bank_operations"][0]["status"] == "SETTLED"


def test_original_reader_dispatches_native_receipt_verifier_and_does_not_borrow_current_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = SimulatedBankRedemption(id=BANK, status="SETTLED", settled_at=NOW)
    receipt = ActionReceipt(id=RECEIPT)
    session = SyntheticSession(original_action(), bank, receipt)
    calls: list[Any] = []
    # Explicit SYNTHETIC verifier double only proves dispatch, not receipt validity.
    monkeypatch.setattr(
        "app.services.full_maturity_replanning.verify_recovery_receipt",
        lambda *args: calls.append(args),
    )
    result = read_original_maturity(session, USER, ACTION, NOW)  # type: ignore[arg-type]
    assert calls == [(session, bank, receipt, NOW)]
    assert result.proof_status == "VERIFIED" and result.receipt_is_current_authority is False
    assert result.economic_verified is False

    def fail(*args: Any) -> None:
        raise PolicyLifecycleError(
            "BANK_RECONCILIATION_REQUIRED", "synthetic corrupted original", 409
        )

    monkeypatch.setattr("app.services.full_maturity_replanning.verify_recovery_receipt", fail)
    rejected = read_original_maturity(session, USER, ACTION, NOW)  # type: ignore[arg-type]
    assert rejected.proof_status == "INVALID" and not rejected.service_receipt_verified


def test_original_reader_rejects_other_owner_and_non_maturity_action() -> None:
    a = original_action()
    a.user_id = UUID(int=999)
    with pytest.raises(PolicyLifecycleError, match="原到期动作"):
        read_original_maturity(SyntheticSession(a), USER, ACTION, NOW)  # type: ignore[arg-type]
    a.user_id = USER
    a.action_type = "ASSET_REDEEM"
    with pytest.raises(PolicyLifecycleError, match="原合同到期"):
        read_original_maturity(SyntheticSession(a), USER, ACTION, NOW)  # type: ignore[arg-type]


def test_real_fastapi_json_calls_original_current_replanning_service_with_explicit_source_doubles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.full_maturity_replanning as service

    session = SyntheticSession()
    products = [product(10), product(11, bps=180)]
    catalogue = VerifiedCatalogProducts(
        status="VERIFIED",
        products=products,
        bindings=[
            CatalogProductBinding(
                product_id=p.product_id,
                catalogue_version_id=UUID(int=200 + i),
                product_record_hash="a" * 64,
                terms_digest=p.terms_digest,
            )
            for i, p in enumerate(products)
        ],
        issues=[],
    )
    audit = DashboardAuditCard(
        epoch_id=EPOCH, status="VALID", complete=True, anchored_run_statuses={}
    )
    monkeypatch.setattr(service, "historical_ledger_scope", lambda actual: nullcontext())
    monkeypatch.setattr(service, "current_epoch_audit", lambda *args: audit)
    monkeypatch.setattr(service, "read_original_maturity", lambda *args: event())
    monkeypatch.setattr(service, "current_asset_policy", lambda *args: current_policy())

    def financial(*args: Any) -> tuple[BoundaryContext, bool, list[Any]]:
        return (
            BoundaryContext(
                snapshot(100000), [], [], financial_products(products), Sources(USER, NOW, [])
            ),
            True,
            [exposure()],
        )

    monkeypatch.setattr(service, "load_verified_financial_context", financial)
    monkeypatch.setattr(service, "_scope", lambda *args: "general")
    monkeypatch.setattr(service, "verified_catalog_products", lambda *args: catalogue)
    monkeypatch.setattr(
        service,
        "preview_asset_allocation",
        lambda *args: SimpleNamespace(
            allocation=current_plan(), source_issues=[], source_evidence_ids=[]
        ),
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_demo_user] = lambda: SimpleNamespace(id=USER)
    app.dependency_overrides[get_now] = lambda: NOW
    client = TestClient(app)
    body = {
        "maturity_action_id": str(ACTION),
        "current_asset_policy_id": str(POLICY),
        "expected_epoch_id": str(EPOCH),
    }
    response = client.post("/api/v1/full-maturity-replanning/preview", json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["decision"]["state"] == "PREPARE_CURRENT_INTENT"
    assert result["original_request"] == body and result["read_only"]
    assert result["decision"]["candidate"]["request"]["intent"] == {
        "kind": "purchase_asset",
        "policy_id": str(POLICY),
    }
    assert (
        not result["dedicated_decision_recorded"]
        and not result["current_prepare_consumes_reviewed_decision_hash"]
    )
    assert (
        client.post(
            "/api/v1/full-maturity-replanning/preview?now=2028-01-01", json=body
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/full-maturity-replanning/preview",
            json={**body, "receipt": {"status": "SUCCEEDED"}},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/full-maturity-replanning/preview", json={**body, "expected_epoch_id": True}
        ).status_code
        == 422
    )
    stale = MaturityReplanningRequest.model_validate({**body, "expected_epoch_id": UUID(int=999)})
    with pytest.raises(PolicyLifecycleError, match="epoch不同"):
        preview_maturity_replanning(session, USER, stale, NOW)  # type: ignore[arg-type]


@pytest.mark.parametrize("dirty", ["new", "dirty", "deleted"])
def test_no_dirty_read_transaction_or_financial_writes(dirty: str) -> None:
    session = SyntheticSession()
    setattr(session, dirty, [object()])
    with pytest.raises(PolicyLifecycleError, match="RR只读事务"):
        preview_maturity_replanning(
            cast(Session, session),
            USER,
            MaturityReplanningRequest(
                maturity_action_id=ACTION, current_asset_policy_id=POLICY, expected_epoch_id=EPOCH
            ),
            NOW,
        )


def test_missing_or_wrong_role_policy_has_no_historical_fallback() -> None:
    session = SyntheticSession()
    session.action = SimpleNamespace(user_id=USER, policy_type="goal_saving")
    # Current-policy lookup never accepts a non-asset Policy as the previous grant.
    with pytest.raises(PolicyLifecycleError, match="资产配置策略不存在"):
        current_asset_policy(session, USER, POLICY, NOW)  # type: ignore[arg-type]
