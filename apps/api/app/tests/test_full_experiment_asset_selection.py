"""Pure synthetic risks, not an actual seven-arm or financial experiment."""

import hashlib
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from app.db.settings import REPOSITORY_ROOT
from app.domain.boundary import compute_boundary
from app.domain.full_asset_execution import FullAssetCatalogueReference, FullAssetPrepareRequest
from app.domain.full_experiment_asset_selection import (
    FullExperimentAssetSelection,
    FullMechanismRuleOriginal,
    FullMechanismSelectedPortfolio,
    RegisteredFullMechanismRule,
    select_current_general_purchase,
)
from app.domain.full_experiment_mechanisms import Rule
from app.domain.policy_configuration import configuration_hash
from app.services import full_experiment_asset_selection as service
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_asset_allocation import NOW, authorization, product
from app.tests.test_full_asset_allocation import changed_product, request
from pydantic import ValidationError
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session


@pytest.fixture
def author_directory() -> Path:
    # Pytest's mode-0700 tmp roots are not readable in this Windows sandbox.
    # Keep unique synthetic author bytes under the repository, with no deletion.
    directory = REPOSITORY_ROOT / ".runtime" / ("mechanism-rule-pure-" + uuid4().hex)
    directory.mkdir()
    return directory


def original(arm: str = "P", **changes: Any) -> FullMechanismRuleOriginal:
    rule: dict[str, Any] = {"arm_id": arm}
    if arm == "B0":
        rule["manual_candidate_id"] = str(UUID(int=10))
    if arm == "B2":
        rule["fixed_candidate_id"] = str(UUID(int=10))
    rule.update(changes.pop("rule", {}))
    return FullMechanismRuleOriginal(
        user_id=UUID(int=90),
        opportunity_id="SYNTHETIC-TOOL-ONLY",
        original_request=FullAssetPrepareRequest(
            full_policy_id=UUID(int=2),
            expected_full_policy_version_id=UUID(int=3),
            mvp_asset_policy_id=UUID(int=4),
            expected_mvp_policy_version_id=UUID(int=5),
            expected_epoch_id=UUID(int=6),
            idempotency_key="synthetic-not-formal",
        ),
        rule=Rule.model_validate(rule),
        manual_amount_cents=12345 if arm == "B0" else None,
        **changes,
    )


def selected(arm: str = "P", *, planning: Any = None, **changes: Any) -> Any:
    planning = request() if planning is None else planning
    body = original(arm, **changes)
    curve = compute_boundary(
        planning.snapshot,
        planning.boundary_versions,
        planning.positions,
        planning.boundary_products,
    )
    catalog = [
        FullAssetCatalogueReference(
            product_id=p.product_id,
            catalogue_version_id=UUID(int=p.product_id.int + 100),
            terms_digest=p.terms_digest,
            product_record_hash=configuration_hash(p.model_dump(mode="json")),
        )
        for p in planning.products
    ]
    return select_current_general_purchase(
        planning,
        curve,
        authorization().configuration,
        catalog,
        body,
        "a" * 64,
        "b" * 64,
    )


@pytest.mark.parametrize("arm", ["B0", "B1", "B2", "B3", "P"])
def test_each_actual_rule_changes_or_preserves_exact_proposal_without_execution(arm: str) -> None:
    inputs, decision, records, batch = selected(
        arm,
        rule={"threshold_cents": 800000, "fixed_budget_cents": 850000},
    )
    assert inputs.source_status == "CURRENT_COMPLETE" and len(records) == 2
    assert batch is not None and decision.status == "PROPOSAL"
    assert (
        batch.amount_cents
        == {"B0": 12345, "B1": 200000, "B2": 150000, "B3": 1000000, "P": 1000000}[arm]
    )
    assert sum(c.amount_cents for c in batch.cash_uses) == batch.amount_cents
    assert batch.product_id == (UUID(int=10) if arm in {"B0", "B2"} else UUID(int=11))
    assert not decision.bank_authority and decision.execution_status == "NOT_RUN"
    assert decision.runtime_adapter_status == "NOT_CONNECTED"
    assert decision.actual_safety_metrics is None


def test_b5_proposes_original_late_fixed_maturity_while_p_rejects_liquidity() -> None:
    planning = request(products=[product(10), product(12, term=30, bps=10000)])
    _, p, records, p_batch = selected(planning=planning, comparison_days=10)
    _, b5, _, b5_batch = selected("B5", planning=planning, comparison_days=10)
    assert p_batch is not None and p_batch.product_id == UUID(int=10)
    assert (
        "LIQUIDITY_FILTER"
        in next(a for a in p.assessments if a.candidate_id == str(UUID(int=12))).rejection_reasons
    )
    assert b5_batch is not None and b5_batch.product_id == UUID(int=12)
    assert b5_batch.principal_available_at > NOW
    assert b5.proposal_net_simulated_yield_cents == b5_batch.net_simulated_yield_cents
    assert len(records) == 2 and not b5.bank_authority


def test_no_model_confidence_is_created_for_b4() -> None:
    _, decision, records, batch = selected("B4")
    assert decision.status == "NO_CANDIDATE" and batch is None
    assert all(
        r.candidate is not None and r.candidate.model_confidence_basis_points is None
        for r in records
    )
    assert all(
        "MISSING_OR_LOW_ORIGINAL_MODEL_CONFIDENCE" in a.rejection_reasons
        for a in decision.assessments
    )


def test_planned_exit_uses_original_funds_date_and_exclusive_confirmed_window() -> None:
    planning = request(products=[product(10)])
    _, decision, _, batch = selected(
        planning=planning, comparison_days=90, funds_use_date=NOW + timedelta(days=10)
    )
    assert decision.status == "PROPOSAL" and batch is not None
    assert batch.exit_plan.earning_days == 10
    assert batch.principal_available_at == NOW + timedelta(days=10)
    expiring = planning.model_copy(update={"valid_until": NOW + timedelta(days=8)})
    _, _, _, limited = selected(planning=expiring, comparison_days=90)
    assert limited is not None and limited.exit_plan.request_at == NOW + timedelta(days=7)


def test_existing_cash_claims_are_counted_once_and_never_available_funding() -> None:
    from app.tests.test_asset_allocation import exposure

    planning = request(exposure=exposure(reserved_cash_by_account={UUID(int=1): 200000}))
    inputs, _, _, batch = selected("B1", planning=planning, rule={"threshold_cents": 100000})
    assert inputs.facts.active_reserved_cents == 200000
    assert batch is not None and batch.amount_cents == 700000


def test_unknown_protection_component_refuses_instead_of_dropping_hard_floor() -> None:
    planning = request()
    curve = compute_boundary(planning.snapshot, [], [], planning.boundary_products)
    point = curve.calculation_trace[0].model_copy(
        update={"protected_cents_by_reason": {"new-hard-floor": 7}}
    )
    changed = curve.model_copy(update={"calculation_trace": [point]})
    catalogue = [
        FullAssetCatalogueReference(
            product_id=p.product_id,
            catalogue_version_id=UUID(int=p.product_id.int + 100),
            terms_digest=p.terms_digest,
            product_record_hash=configuration_hash(p.model_dump(mode="json")),
        )
        for p in planning.products
    ]
    with pytest.raises(ValueError, match="UNREGISTERED"):
        select_current_general_purchase(
            planning,
            changed,
            authorization().configuration,
            catalogue,
            original(),
            "a" * 64,
            "b" * 64,
        )


def test_original_product_denominator_retains_noneligible_and_future_versions() -> None:
    current = product(10)
    future = changed_product(product(11), created_at=NOW.replace(year=2027))
    risk = changed_product(product(12), principal_fluctuation=True)
    _, _, records, batch = selected(planning=request(products=[current, future, risk]))
    assert len(records) == 3 and batch is not None and batch.product_id == current.product_id
    assert (
        records[1].candidate is None
        and "PRODUCT_NOT_CURRENTLY_KNOWN_AND_EFFECTIVE" in records[1].exclusion_reasons
    )
    assert (
        records[2].candidate is None
        and "PRINCIPAL_RISK_NOT_SUPPORTED_BY_THIS_PRODUCER" in records[2].exclusion_reasons
    )


def test_original_minimum_is_not_rounded_up_into_an_unregistered_rule_amount() -> None:
    p = changed_product(product(10), minimum_purchase_cents=20000)
    with pytest.raises(ValueError, match="BELOW_ORIGINAL_MINIMUM"):
        selected("B0", planning=request(products=[p]))


@pytest.mark.parametrize(
    "field,value",
    [("threshold_cents", True), ("fixed_budget_cents", 1.5), ("model_result", {"confidence": 1})],
)
def test_strict_registered_rule_rejects_bool_float_or_client_model_result(
    field: str, value: Any
) -> None:
    raw = original().model_dump(mode="json")
    raw["rule"][field] = value
    with pytest.raises(ValidationError):
        FullMechanismRuleOriginal.model_validate_json(json.dumps(raw))


def test_original_rule_bytes_request_owner_and_hash_are_required(
    author_directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "REPOSITORY_ROOT", author_directory)
    folder = author_directory / ".runtime"
    folder.mkdir()
    rule = original()
    raw = rule.model_dump_json().encode()
    path = folder / "rule.json"
    path.write_bytes(raw)
    locator = RegisteredFullMechanismRule(
        original_path=".runtime/rule.json", sha256=hashlib.sha256(raw).hexdigest()
    )
    actual, actual_bytes = service.load_registered_rule(
        locator, rule.original_request, rule.user_id
    )
    assert actual == rule and actual_bytes == raw
    with pytest.raises(PolicyLifecycleError, match="owner/request"):
        service.load_registered_rule(locator, rule.original_request, UUID(int=91))
    changed_key = rule.original_request.model_copy(update={"idempotency_key": "different"})
    with pytest.raises(PolicyLifecycleError, match="owner/request"):
        service.load_registered_rule(locator, changed_key, rule.user_id)
    path.write_bytes(raw + b" ")
    with pytest.raises(PolicyLifecycleError, match="bytes changed"):
        service.load_registered_rule(locator, rule.original_request, rule.user_id)


def test_duplicate_rule_json_keys_and_out_of_registry_files_are_rejected(
    author_directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "REPOSITORY_ROOT", author_directory)
    folder = author_directory / ".runtime"
    folder.mkdir()
    rule = original()
    raw = b'{"protocol":"full-general-purchase-rule-original-v1","protocol":"other"}'
    (folder / "rule.json").write_bytes(raw)
    locator = RegisteredFullMechanismRule(
        original_path=".runtime/rule.json", sha256=hashlib.sha256(raw).hexdigest()
    )
    with pytest.raises(PolicyLifecycleError, match="Duplicate"):
        service.load_registered_rule(locator, rule.original_request, rule.user_id)
    with pytest.raises(PolicyLifecycleError, match="repository-local"):
        service.load_registered_rule(
            locator.model_copy(update={"original_path": "README.md"}),
            rule.original_request,
            rule.user_id,
        )


def test_selected_portfolio_hash_covers_exact_batch_not_a_p_optimal_result() -> None:
    _, decision, records, batch = selected("B1", rule={"threshold_cents": 700000})
    assert batch is not None
    record = next(r for r in records if r.product.product_id == batch.product_id)
    value = {
        "protocol": "full-mechanism-selected-portfolio-v1",
        "user_id": str(original().user_id),
        "epoch_id": str(original().original_request.expected_epoch_id),
        "original_request": original().original_request.model_dump(mode="json"),
        "rule_original_sha256": "a" * 64,
        "current_source_hash": "b" * 64,
        "mechanism_input_hash": decision.input_sha256,
        "selected_product": record.catalogue.model_dump(mode="json"),
        "batch": batch.model_dump(mode="json"),
        "bank_authority": False,
        "funds_reserved": False,
        "preparation_status": "NOT_PREPARED",
    }
    loaded = FullMechanismSelectedPortfolio.model_validate_json(
        json.dumps({**value, "portfolio_hash": configuration_hash(value)})
    )
    assert not loaded.bank_authority and loaded.preparation_status == "NOT_PREPARED"
    value["batch"]["amount_cents"] += 1
    with pytest.raises(ValidationError, match="identity/hash"):
        FullMechanismSelectedPortfolio.model_validate_json(
            json.dumps({**value, "portfolio_hash": loaded.portfolio_hash})
        )
    with pytest.raises(ValidationError, match="identity/hash"):
        FullMechanismSelectedPortfolio.model_validate_json(
            json.dumps({**value, "portfolio_hash": configuration_hash(value)})
        )


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://localhost:54329/bf_test_" + "a" * 32,
        "postgresql://127.0.0.1:5432/bf_test_" + "a" * 32,
        "postgresql://127.0.0.1:54329/bounded_funds",
        "sqlite://",
    ],
)
def test_unowned_or_nonlocal_database_is_refused_before_any_financial_read(url: str) -> None:
    session = SimpleNamespace(get_bind=lambda: SimpleNamespace(url=make_url(url)))
    with pytest.raises((PolicyLifecycleError, ValueError)):
        service._owned_read(cast(Session, session), original().user_id, original().original_request)


def test_global_reserved_maps_are_not_summed_per_scope_and_disagreement_is_unknown() -> None:
    from app.tests.test_asset_allocation import exposure

    first = exposure(reserved_cash_by_account={UUID(int=1): 200000}).model_copy(
        update={"goal_id": UUID(int=30), "scope": "goal"}
    )
    second = first.model_copy(update={"goal_id": UUID(int=31)})
    result = service._exposure([first, second], NOW)
    assert result.reserved_cash_by_account == {UUID(int=1): 200000}
    bad = second.model_copy(update={"reserved_cash_by_account": {UUID(int=1): 200001}})
    with pytest.raises(ValueError, match="MAPS_DISAGREE"):
        service._exposure([first, bad], NOW)


def missing_output() -> dict[str, Any]:
    body = original()
    raw = body.model_dump_json()
    originals = {"rule_original_utf8": raw}
    counts = {"unresolved_action_rows_captured": 0}
    source_hash = configuration_hash(
        {
            "user_id": str(body.user_id),
            "epoch_id": str(body.original_request.expected_epoch_id),
            "as_of": NOW.isoformat(),
            "originals": originals,
            "counts": counts,
        }
    )
    return {
        "user_id": body.user_id,
        "epoch_id": body.original_request.expected_epoch_id,
        "as_of": NOW,
        "status": "MISSING",
        "original_request": body.original_request,
        "rule_original": body,
        "rule_original_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "current_source_hash": source_hash,
        "source_evidence_ids": [],
        "source_originals": originals,
        "source_counts": counts,
        "planning_input": None,
        "original_p_result": None,
        "mechanism_input": None,
        "mechanism_decision": None,
        "product_originals": [],
        "selected_portfolio": None,
        "reasons": ["SYNTHETIC_NOT_RUN"],
        "limitations": [],
    }


def test_missing_original_output_does_not_imply_p_result_or_execution() -> None:
    value = FullExperimentAssetSelection.model_validate(missing_output())
    assert value.status == "MISSING" and value.original_p_result is None
    assert not value.bank_authority and value.actual_metrics is None
    assert value.execution_status == "NOT_RUN" and value.runtime_consumer_status == "NOT_CONNECTED"


@pytest.mark.parametrize("field,value", [("current_source_hash", "f" * 64), ("status", "PROPOSED")])
def test_output_rejects_source_drift_or_invented_proposed_status(field: str, value: str) -> None:
    values = missing_output()
    values[field] = value
    with pytest.raises(ValidationError):
        FullExperimentAssetSelection.model_validate(values)
