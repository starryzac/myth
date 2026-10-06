"""Synthetic original bindings and whole math; never a banking or corpus experiment."""

import copy
from datetime import UTC, timedelta
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from app.domain.autonomy_types import AuthorityAssessment
from app.domain.full_action_set_asset_producers import (
    AssetFamilyInput,
    AssetProducerInput,
    asset_producer_view,
    derive_asset_family,
    expected_asset_producers,
)
from app.domain.full_action_set_boundary import (
    REQUIRED_INVENTORY,
    ActionSetInput,
    CandidateInput,
    derive_action_set,
    required_producer_keys,
    unsupported_producers,
)
from app.domain.full_asset_allocation import plan_full_assets
from app.domain.full_asset_execution import FullAssetCatalogueReference, build_frozen_portfolio
from app.domain.full_protection_projection import (
    FullProtectionProjectionInput,
    project_full_protection,
)
from app.domain.policy_configuration import configuration_hash
from app.services import full_action_set_asset_producers as service
from app.services.decision_recording import CAPTURE_KEY, DecisionCapture
from app.services.full_action_set_boundary import ActionSetCapture
from app.services.full_asset_allocation import FullAssetAllocationResponse
from app.services.full_asset_execution import FullAssetExecutionPreview
from app.services.full_policy_lifecycle import FullPolicyView, FullVersionView
from app.services.full_projection import FutureIncomeProjection, _checkpoint
from app.services.full_protection_projection import FullAnnualProtectionResponse
from app.services.product_catalog import CatalogProductBinding, VerifiedCatalogProducts
from app.tests.test_full_asset_allocation import request as planning_request
from app.tests.test_full_asset_execution import literal_basis
from app.tests.test_full_projection import audit
from sqlalchemy.orm import Session


def fixture() -> AssetFamilyInput:
    body, basis = literal_basis()
    user, epoch, now = basis.user_id, basis.epoch_id, basis.as_of
    full_proof, mvp_proof = UUID(int=2001), UUID(int=2002)
    version = basis.context.versions[0]
    assert version.confirmed_at is not None
    full_confirmation = {
        "protocol": "full-policy-confirmation-v1",
        "user_id": str(user),
        "epoch_id": str(epoch),
        "policy_id": str(body.full_policy_id),
        "version_id": str(body.expected_full_policy_version_id),
        "template_name": "AssetAuthorizationPolicy",
        "reviewed_hash": basis.full_policy_content_hash,
        "confirmed_at": version.confirmed_at.isoformat(),
        "accepted": True,
        "bank_authority": False,
        "confirmation_evidence_id": str(full_proof),
        "request_key": "TOOL_ONLY_DECLARATION",
        "request_hash": "d" * 64,
    }
    mvp_confirmation = {
        "user_id": str(user),
        "policy_id": str(version.policy_id),
        "version_id": str(version.version_id),
        "reviewed_hash": version.content_hash,
        "confirmed_at": version.confirmed_at.isoformat(),
        "accepted": True,
    }
    version = version.model_copy(update={"evidence_ids": [mvp_proof]})
    full = FullPolicyView(
        policy_id=body.full_policy_id,
        epoch_id=epoch,
        template_name="AssetAuthorizationPolicy",
        name="TOOL_ONLY",
        status="ACTIVE",
        effective_status="ACTIVE",
        planning_confirmation_valid=True,
        reference_validation="CURRENT",
        current_version=FullVersionView(
            version_id=body.expected_full_policy_version_id,
            policy_id=body.full_policy_id,
            version_number=1,
            configuration=basis.full_policy_configuration.model_dump(mode="json"),
            content_hash=basis.full_policy_content_hash,
            previous_hash=None,
            summary="TOOL_ONLY",
            confirmation=full_confirmation,
            confirmed_at=version.confirmed_at,
            valid_from=version.valid_from,
            valid_until=None,
            change_reason="TOOL_ONLY",
            evidence_ids=[full_proof],
            impact_analysis={},
            confirmation_evidence_status="CURRENT_EVIDENCE_MATCHED",
        ),
        updated_at=now,
    )
    inventory: dict[str, list[dict[str, Any]]] = {name: [] for name in REQUIRED_INVENTORY}
    inventory["users"] = [{"id": str(user), "is_simulated": True}]
    inventory["accounts"] = [
        {
            "id": str(row.account_id),
            "user_id": str(user),
            "account_type": row.account_type,
            "balance_cents": row.balance_cents,
        }
        for row in basis.context.snapshot.cash_accounts
    ]
    inventory["policies"] = [
        {"id": str(version.policy_id), "user_id": str(user), "status": "ACTIVE"}
    ]
    inventory["policy_versions"] = [
        {
            "id": str(version.version_id),
            "user_id": str(user),
            "policy_id": str(version.policy_id),
            "version_number": 1,
            "configuration": version.configuration,
            "content_hash": version.content_hash,
            "confirmation": mvp_confirmation,
            "confirmed_at": version.confirmed_at.isoformat(),
            "valid_from": version.valid_from.isoformat(),
            "valid_until": None,
            "evidence_ids": [str(mvp_proof)],
        }
    ]
    inventory["full_policies"] = [
        {
            "id": str(full.policy_id),
            "user_id": str(user),
            "epoch_id": str(epoch),
            "status": "ACTIVE",
            "template_name": full.template_name,
        }
    ]
    inventory["full_policy_versions"] = [
        {
            "id": str(full.current_version.version_id),
            "user_id": str(user),
            "policy_id": str(full.policy_id),
            "version_number": 1,
            "configuration": full.current_version.configuration,
            "content_hash": full.current_version.content_hash,
            "confirmation": full_confirmation,
            "confirmed_at": version.confirmed_at.isoformat(),
            "valid_from": version.valid_from.isoformat(),
            "valid_until": None,
            "evidence_ids": [str(full_proof)],
        }
    ]
    for identity, content, source, source_ref in [
        (
            full_proof,
            full_confirmation,
            "FULL_POLICY_CONFIRMATION",
            full.current_version.version_id,
        ),
        (mvp_proof, mvp_confirmation, "POLICY_CONFIRMATION", version.version_id),
    ]:
        inventory["evidence_items"].append(
            {
                "id": str(identity),
                "user_id": str(user),
                "status": "VALID",
                "content": content,
                "content_hash": configuration_hash(content),
                "source_type": source,
                "source_ref": str(source_ref),
                "evidence_level": "USER_CONFIRMED_POLICY",
                "observed_at": version.confirmed_at.isoformat(),
                "valid_from": version.confirmed_at.isoformat(),
                "valid_to": None,
            }
        )
    sources = [full_proof, mvp_proof]
    digest = configuration_hash(
        {
            "user_id": str(user),
            "as_of": now.isoformat(),
            "sources": [
                {"id": str(key), "hash": inventory["evidence_items"][number]["content_hash"]}
                for number, key in enumerate(sources)
            ],
            "issues": [],
        }
    )
    snapshot = basis.context.snapshot.model_copy(update={"source_digest": digest})
    data = planning_request(cap=400000).model_copy(
        update={
            "policy_id": full.policy_id,
            "policy_version_id": full.current_version.version_id,
            "snapshot": snapshot,
            "boundary_versions": [version],
        }
    )
    context = basis.context.model_copy(update={"snapshot": snapshot, "versions": [version]})
    bindings = []
    for product in data.products:
        raw = product.model_dump(mode="json", exclude={"product_id", "terms_digest"}) | {
            "id": str(product.product_id)
        }
        for field in ("created_at", "effective_from", "effective_until"):
            if raw[field] is not None:
                from datetime import datetime

                raw[field] = (
                    datetime.fromisoformat(raw[field])
                    .astimezone(UTC)
                    .isoformat(timespec="microseconds")
                    .replace("+00:00", "Z")
                )
        ref = CatalogProductBinding(
            product_id=product.product_id,
            catalogue_version_id=UUID(int=3000 + product.product_id.int),
            product_record_hash=configuration_hash(raw),
            terms_digest=product.terms_digest,
        )
        bindings.append(ref)
        inventory["asset_products"].append(copy.deepcopy(raw))
        inventory["product_catalog_versions"].append(
            {
                "id": str(ref.catalogue_version_id),
                "protocol_version": "product-catalog-v1",
                "product_id": str(product.product_id),
                "product_code": product.product_code,
                "version_number": product.version_number,
                "canonical_product": raw,
                "product_hash": ref.product_record_hash,
                "terms_digest": product.terms_digest,
                "observed_at": (now - timedelta(days=1)).isoformat(),
                "effective_from": product.effective_from.isoformat(),
                "effective_until": None,
            }
        )
    basis = basis.model_copy(
        update={
            "context": context,
            "planning": plan_full_assets(data),
            "source_evidence_ids": sources,
            "catalogue": [
                FullAssetCatalogueReference.model_validate(row.model_dump()) for row in bindings
            ],
        }
    )
    projection = project_full_protection(
        FullProtectionProjectionInput(
            snapshot=snapshot,
            boundary_versions=[version],
            positions=[],
            boundary_products=data.boundary_products,
            policies=[],
        )
    )
    assert projection.full_annual_projection is not None
    points = projection.full_annual_projection.calculation_trace
    start = points[0].date
    protection = FullAnnualProtectionResponse(
        user_id=user,
        as_of=now,
        projection=projection,
        initial_checkpoint=_checkpoint(0, start, points[:3]),
        daily_checkpoints=[
            _checkpoint(day, start + timedelta(days=day), points[day * 3 : (day + 1) * 3])
            for day in range(1, 366)
        ],
        full_policy_sources=[],
        future_income=FutureIncomeProjection(),
        source_evidence_ids=sources,
        source_issues=[],
        input_digest=projection.input_hash,
        audit=audit(),
        limitations=["TOOL_ONLY"],
    )
    financial = {
        "user_id": str(user),
        "as_of": now.isoformat(),
        "snapshot": snapshot.model_dump(mode="json"),
        "versions": [version.model_dump(mode="json")],
        "positions": [],
        "income": None,
    }
    keys = required_producer_keys(inventory)
    base = ActionSetInput(
        user_id=user,
        epoch_id=epoch,
        as_of=now,
        original_inventory=inventory,
        inventory_reasons=[],
        expected_candidate_keys=keys,
        candidates=[
            CandidateInput(candidate_key=key, missing_reasons=["TOOL_ONLY_BASE_NOT_GLOBAL"])
            for key in keys
        ],
        financial_input_hash=configuration_hash(financial),
        financial_basis=financial,
        audit_verified=True,
        source_reasons=[],
        unsupported_producers=unsupported_producers(inventory, epoch),
    )
    producers = []
    for key, (_, _, mode) in expected_asset_producers(base).items():
        request = body.model_copy(update={"planning_mode": mode})
        current_data = data.model_copy(
            update={"options": data.options.model_copy(update={"mode": mode})}
        )
        allocation = plan_full_assets(current_data)
        current_basis = basis.model_copy(
            update={"planning": allocation, "batch_income_uses": [[] for _ in allocation.batches]}
        )
        planning = FullAssetAllocationResponse(
            user_id=user,
            policy_id=full.policy_id,
            as_of=now,
            state="COMPUTED",
            planning_constraints=current_data.options,
            allocation=allocation,
            catalogue=VerifiedCatalogProducts(
                status="VERIFIED", products=data.products, bindings=bindings, issues=[]
            ),
            unavailable_asset_classes=[],
            source_evidence_ids=sources,
            source_issues=[],
            input_hash=current_basis.original_planning_response_hash,
            limitations=["TOOL_ONLY"],
        )
        portfolio = build_frozen_portfolio(request, current_basis) if allocation.batches else None
        preview = FullAssetExecutionPreview(
            user_id=user,
            epoch_id=epoch,
            as_of=now,
            original_request=request,
            state="READY_TO_REVIEW" if portfolio else "UNKNOWN",
            original_full_planning=planning,
            original_full_protection=protection,
            portfolio=portfolio,
            reasons=[] if portfolio else ["NO_CURRENT_OPTIMAL_NONEMPTY_PORTFOLIO"],
            limitations=["TOOL_ONLY"],
        )
        producers.append(
            AssetProducerInput(
                candidate_key=key,
                full_policy_id=full.policy_id,
                request=request,
                original_policy=full,
                actual_preview=preview,
                planning_input=current_data,
                execution_basis=current_basis if portfolio else None,
                authority=AuthorityAssessment(
                    status="AUTHORIZED",
                    policy_version_ids=[version.version_id],
                    evidence_ids=[mvp_proof],
                ),
            )
        )
    return AssetFamilyInput(base=base, producers=producers)


def test_whole_original_two_product_signature_is_ask_not_auto_and_not_a_global_claim() -> None:
    value = fixture()
    result = derive_asset_family(value)
    assert result.family_complete, result.reasons
    portfolio = next(row for row in result.candidates if row.candidate_key.endswith(":PORTFOLIO"))
    assert (
        portfolio.state == "INCLUDED"
        and portfolio.amount_cents == 799792
        and portfolio.autonomy_level == "ASK_ONCE"
    )
    assert portfolio.signature is not None
    assert (
        not result.bank_authority
        and not result.financial_write
        and not result.replaces_original_candidate_keys
    )
    assert derive_action_set(value.base).status == "UNKNOWN"
    assert derive_asset_family(value) == result


@pytest.mark.parametrize(
    "risk",
    [
        "catalogue",
        "catalogue-window",
        "missing-catalogue",
        "full-confirmation",
        "mvp-confirmation",
        "permission",
        "snapshot",
        "claims",
        "owner",
        "source",
        "optimizer",
        "whole-cap",
        "missing-mode",
        "duplicate-mode",
        "full-floor",
        "request-version",
        "cash-row",
        "source-digest",
        "unproven-income",
    ],
)
def test_incomplete_originals_never_become_complete_or_included(risk: str) -> None:
    value = fixture().model_dump(mode="json")
    raw = value["base"]["original_inventory"]
    producer = next(
        row for row in value["producers"] if row["candidate_key"].endswith(":PORTFOLIO")
    )
    if risk == "catalogue":
        raw["asset_products"][0]["annual_yield_bps"] += 1
    elif risk == "catalogue-window":
        raw["product_catalog_versions"][0]["effective_from"] = value["base"]["as_of"]
    elif risk == "missing-catalogue":
        raw["product_catalog_versions"].pop()
    elif risk == "full-confirmation":
        raw["evidence_items"][0]["content"]["accepted"] = False
    elif risk == "mvp-confirmation":
        raw["evidence_items"][1]["content"]["reviewed_hash"] = "a" * 64
    elif risk == "permission":
        producer["authority"]["evidence_ids"] = []
    elif risk == "snapshot":
        producer["planning_input"]["snapshot"]["cash_accounts"][0]["balance_cents"] += 1
    elif risk == "claims":
        raw["action_resource_reservations"].append(
            {
                "id": str(UUID(int=4001)),
                "user_id": value["base"]["user_id"],
                "status": "RESERVED",
                "created_at": value["base"]["as_of"],
                "resource_kind": "CASH",
                "resource_key": raw["accounts"][0]["id"],
                "amount_cents": 1,
            }
        )
    elif risk == "owner":
        raw["full_policy_versions"][0]["user_id"] = str(UUID(int=4002))
    elif risk == "source":
        raw["evidence_items"][0]["observed_at"] = None
    elif risk == "optimizer":
        producer["planning_input"]["options"]["max_components"] = 1
    elif risk == "whole-cap":
        producer["execution_basis"]["full_policy_configuration"]["max_auto_managed_cents"] = 700000
    elif risk == "missing-mode":
        value["producers"].pop()
    elif risk == "duplicate-mode":
        value["producers"].append(copy.deepcopy(producer))
    elif risk == "full-floor":
        raw["full_policies"].append(
            {
                "id": str(UUID(int=4003)),
                "user_id": value["base"]["user_id"],
                "epoch_id": value["base"]["epoch_id"],
                "status": "ACTIVE",
                "template_name": "DatedExpensePolicy",
            }
        )
    elif risk == "request-version":
        producer["request"]["expected_mvp_policy_version_id"] = str(UUID(int=4004))
    elif risk == "cash-row":
        raw["accounts"][0]["balance_cents"] += 1
    elif risk == "source-digest":
        producer["planning_input"]["snapshot"]["source_digest"] = "f" * 64
    elif risk == "unproven-income":
        value["base"]["financial_basis"]["income"] = {
            "protocol": "new-funds-ledger-v2",
            "user_id": value["base"]["user_id"],
        }
        value["base"]["financial_input_hash"] = configuration_hash(value["base"]["financial_basis"])
    result = derive_asset_family(
        AssetFamilyInput.model_validate_json(__import__("json").dumps(value))
    )
    assert not result.family_complete
    assert result.reasons


def test_no_matching_mvp_keeps_sentinel_and_inactive_full_is_proved_exclusion() -> None:
    value = fixture()
    raw = copy.deepcopy(value.base.original_inventory)
    raw["policies"] = []
    raw["policy_versions"] = []
    base = value.base.model_copy(
        update={"original_inventory": raw, "expected_candidate_keys": [], "candidates": []}
    )
    expected = expected_asset_producers(base)
    assert len(expected) == 1 and next(iter(expected)).endswith("NO_MATCHING_MVP_SCOPE")
    candidate = AssetProducerInput(
        candidate_key=next(iter(expected)), full_policy_id=value.producers[0].full_policy_id
    )
    assert asset_producer_view(base, candidate).state == "UNKNOWN"
    raw["full_policies"][0]["status"] = "SUSPENDED"
    assert asset_producer_view(base, candidate).state == "EXCLUDED"


def test_read_capture_reuses_actual_preview_and_restores_outer_original_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = fixture()
    session = MagicMock(spec=Session)
    outer = DecisionCapture()
    session.info = {CAPTURE_KEY: outer}
    base = ActionSetCapture(value.base, derive_action_set(value.base), DecisionCapture())
    monkeypatch.setattr(service, "_snapshot", lambda _: None)
    calls: list[str] = []
    monkeypatch.setattr(
        service, "read_full_policy", lambda *args: value.producers[0].original_policy
    )
    from types import SimpleNamespace

    version = value.base.original_inventory["policy_versions"][0]
    session.scalar.return_value = SimpleNamespace(
        id=UUID(version["id"]), policy_id=UUID(version["policy_id"])
    )

    def preview(*args: Any) -> FullAssetExecutionPreview:
        body = args[2]
        calls.append(body.planning_mode)
        original = next(
            row.actual_preview
            for row in value.producers
            if row.request and row.request.planning_mode == body.planning_mode
        )
        assert original is not None
        # Missing basis intentionally keeps output UNKNOWN; this verifies real
        # production preview invocation/denominator, not a fabricated bank result.
        return original.model_copy(update={"portfolio": None, "original_request": body})

    monkeypatch.setattr(service, "preview_full_asset_execution", preview)
    monkeypatch.setattr(service, "_planning_input", lambda *args: value.producers[0].planning_input)
    monkeypatch.setattr(service, "_authority", lambda *args, **kwargs: value.producers[0].authority)
    monkeypatch.setattr(service, "capture_evidence", lambda *args: None)
    result = service.capture_asset_family(session, value.base.user_id, value.base.as_of, base)
    assert sorted(calls) == ["FIXED_LADDER", "PORTFOLIO"]
    assert session.info[CAPTURE_KEY] is outer and not result.result.family_complete
    session.add.assert_not_called()
    session.flush.assert_not_called()
    session.commit.assert_not_called()
    again = service.capture_asset_family(session, value.base.user_id, value.base.as_of, base)
    assert len(calls) == 4 and again.result == result.result
    assert session.info[CAPTURE_KEY] is outer
