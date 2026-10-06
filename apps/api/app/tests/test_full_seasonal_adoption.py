"""Synthetic source-shaped module risks; not actual PG or a recorded USER consent."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid5

import pytest
from app.domain.full_policy_configuration import validate_full_configuration
from app.domain.full_protection_projection import FullProtectionPolicySource
from app.domain.full_seasonal_adoption import (
    SeasonalAdoptionConfirmRequest,
    SeasonalAdoptionOriginal,
    SeasonalAdoptionPreviewRequest,
    SeasonalAdoptionProof,
    SeasonalAdoptionScope,
    assert_no_seasonal_overlap,
    original_from_json,
    seasonal_command_id,
    seasonal_request_hash,
    seasonal_review_hash,
    seasonal_source_hash,
    validate_seasonal_scope,
    verify_seasonal_adoption,
)
from app.domain.local_actor_session_types import LocalActorPrincipal
from app.domain.pattern_suggestions import SeasonalParameters
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import row_copy
from app.services.policy_suggestions import seasonal_suggestions
from app.tests.test_policy_suggestions_service import USER as USER
from app.tests.test_policy_suggestions_service import item, seasonal_memory

EPOCH, POLICY, VERSION = UUID(int=8001), UUID(int=8002), UUID(int=8003)


def scope_fixture() -> SeasonalAdoptionScope:
    memory, now = seasonal_memory()
    parameters = SeasonalParameters(window_id="CN-2026-SPRING_FESTIVAL")
    result = seasonal_suggestions(memory.as_session(), USER, now, parameters)
    config = validate_full_configuration(
        "SeasonalReservePolicy", result.suggestion.candidate_configuration or {}
    )
    # Fixture cap uses the registered parent policy limit, not an invented funded balance.
    config["adjustment_cap_cents"] = parameters.adjustment_cap_cents
    content_hash = configuration_hash(config)
    confirmation: dict[str, Any] = {
        "protocol": "full-policy-lifecycle-v1",
        "user_id": str(USER),
        "epoch_id": str(EPOCH),
        "policy_id": str(POLICY),
        "version_id": str(VERSION),
        "template_name": "SeasonalReservePolicy",
        "reviewed_hash": content_hash,
        "confirmed_at": now.isoformat(),
        "accepted": True,
        "bank_authority": False,
        "confirmation_evidence_id": str(UUID(int=8010)),
        "request_key": "synthetic-fixture-confirm",
        "request_hash": "a" * 64,
    }
    proof = item(8010, "FULL_POLICY_CONFIRMATION", confirmation, "USER_CONFIRMED_POLICY")
    proof.source_ref = str(VERSION)
    proof.observed_at = proof.valid_from = now
    version = {
        "version_id": str(VERSION),
        "policy_id": str(POLICY),
        "version_number": 1,
        "configuration": config,
        "content_hash": content_hash,
        "previous_hash": None,
        "summary": "synthetic module fixture",
        "confirmation": confirmation,
        "confirmed_at": now.isoformat(),
        "valid_from": now.isoformat(),
        "valid_until": None,
        "change_reason": "synthetic module fixture",
        "evidence_ids": [str(proof.id)],
        "impact_analysis": {},
        "confirmation_evidence_status": "CURRENT_EVIDENCE_MATCHED",
    }
    policy = {
        "simulation": True,
        "policy_id": str(POLICY),
        "epoch_id": str(EPOCH),
        "template_name": "SeasonalReservePolicy",
        "name": "synthetic module fixture",
        "status": "ACTIVE",
        "effective_status": "ACTIVE",
        "planning_confirmation_valid": True,
        "reference_validation": "CURRENT",
        "execution_support": "NOT_IMPLEMENTED",
        "current_version": version,
        "updated_at": now.isoformat(),
    }
    row = result.suggestion
    assert row.target and row.effective_window_start and row.effective_window_end
    assert row.proposed_adjustment_cents is not None and row.required_adjustment_cents is not None
    assert row.cap_limited is not None
    sources = [value for value in memory.evidence if value.id in result.source_evidence_ids]
    sources.append(proof)
    return SeasonalAdoptionScope(
        user_id=USER,
        epoch_id=EPOCH,
        policy_id=POLICY,
        version_id=VERSION,
        configuration_hash=content_hash,
        evaluated_at=now,
        window_id=row.target.window_id,
        holiday_code=row.target.holiday_code,
        official_start=row.target.start,
        official_end=row.target.end,
        protection_start=row.effective_window_start,
        protection_end=row.effective_window_end,
        adopted_adjustment_cents=row.proposed_adjustment_cents,
        required_adjustment_cents=row.required_adjustment_cents,
        cap_limited=row.cap_limited,
        full_policy_original=policy,
        suggestion_original=result.model_dump(mode="json"),
        source_evidence_originals=sorted(
            [row_copy(value) for value in sources], key=lambda value: value["id"]
        ),
        parameters=parameters,
    )


def original_fixture(scope: SeasonalAdoptionScope | None = None) -> SeasonalAdoptionOriginal:
    scope = scope or scope_fixture()
    body = SeasonalAdoptionConfirmRequest(
        expected_version_id=scope.version_id,
        expected_epoch_id=scope.epoch_id,
        window_id=scope.window_id,
        reviewed_hash=seasonal_review_hash(scope),
        accepted=True,
        reason="Explicit synthetic test consent",
        idempotency_key="synthetic-adopt-1",
    )
    return SeasonalAdoptionOriginal(
        command_id=seasonal_command_id(scope.user_id, scope.epoch_id, body.idempotency_key),
        user_id=scope.user_id,
        epoch_id=scope.epoch_id,
        policy_id=scope.policy_id,
        idempotency_key=body.idempotency_key,
        original_request=body,
        request_hash=seasonal_request_hash(scope.user_id, scope.policy_id, body),
        reviewed_hash=body.reviewed_hash,
        source_binding_hash=seasonal_source_hash(scope),
        principal_at_command=LocalActorPrincipal(
            user_id=USER,
            role="USER",
            session_id=UUID(int=8800),
            issued_at=scope.evaluated_at,
            expires_at=scope.evaluated_at + timedelta(minutes=15),
        ),
        recorded_at=scope.evaluated_at,
        scope=scope,
    )


def proof_fixture() -> tuple[FullProtectionPolicySource, SeasonalAdoptionProof]:
    original = original_fixture()
    scope = original.scope
    version = scope.full_policy_original["current_version"]
    source = FullProtectionPolicySource(
        policy_id=scope.policy_id,
        version_id=scope.version_id,
        template_name="SeasonalReservePolicy",
        configuration=version["configuration"],
        content_hash=scope.configuration_hash,
        confirmation=version["confirmation"],
        confirmed_at=scope.evaluated_at,
        valid_from=scope.evaluated_at,
        effective_status="ACTIVE",
        planning_confirmation_valid=True,
        references_current=True,
        evidence_ids=[UUID(value) for value in version["evidence_ids"]],
    )
    proof = SeasonalAdoptionProof(
        status="VERIFIED",
        user_id=scope.user_id,
        epoch_id=scope.epoch_id,
        policy_id=scope.policy_id,
        as_of=scope.evaluated_at,
        original=original,
        evidence_id=uuid5(original.command_id, "evidence"),
        evidence_hash=configuration_hash(original.model_dump(mode="json")),
        trace_hash="a" * 64,
        current_scope=scope,
        actual_adoption_count=1,
        retained_command_ids=[original.command_id],
        reasons=[],
    )
    return source, proof


def test_original_history_1800_is_only_effective_after_explicit_user_adoption() -> None:
    scope = scope_fixture()
    validate_seasonal_scope(scope)
    assert scope.adopted_adjustment_cents == 1800
    assert not scope.bank_authority and not scope.payment_or_settlement_proven
    original = original_fixture(scope)
    assert original_from_json(original.model_dump(mode="json")) == original
    source, proof = proof_fixture()
    assert verify_seasonal_adoption(source, proof, proof.as_of, "Asia/Shanghai")
    advice = proof.model_copy(update={"status": "ADVICE_ONLY", "original": None})
    assert not verify_seasonal_adoption(source, advice, proof.as_of, "Asia/Shanghai")


def test_review_hash_ignores_only_exact_read_clock_fields_within_same_day() -> None:
    scope = scope_fixture()
    later = scope.model_dump(mode="json")
    later["evaluated_at"] = (scope.evaluated_at + timedelta(seconds=1)).isoformat()
    later["suggestion_original"]["as_of"] = later["evaluated_at"].replace("+00:00", "Z")
    later["suggestion_original"]["source_digest"] = "b" * 64
    second = SeasonalAdoptionScope.model_validate_json(__import__("json").dumps(later))
    assert seasonal_review_hash(scope) == seasonal_review_hash(second)
    assert seasonal_source_hash(scope) == seasonal_source_hash(second)


@pytest.mark.parametrize(
    "case",
    [
        "amount",
        "incomplete",
        "missing_source",
        "duplicate_source",
        "foreign",
        "evidence_hash",
        "source_time",
        "bank_level",
        "calendar",
        "confirmation",
        "category",
        "version",
        "unadopted",
        "period",
        "future_income",
        "missing_count",
        "bank_authority",
    ],
)
def test_changed_originals_or_unadopted_sources_never_become_verified_protection(case: str) -> None:
    source, proof = proof_fixture()
    value = proof.model_dump(mode="json")
    assert value["current_scope"] is not None
    scope = value["current_scope"]
    if case == "amount":
        scope["adopted_adjustment_cents"] += 1
    elif case == "incomplete":
        scope["suggestion_original"]["history_proof"]["verified"] = False
    elif case == "missing_source":
        scope["source_evidence_originals"].pop()
    elif case == "duplicate_source":
        scope["source_evidence_originals"].append(scope["source_evidence_originals"][0])
    elif case == "foreign":
        scope["source_evidence_originals"][0]["user_id"] = str(UUID(int=99999))
    elif case == "evidence_hash":
        scope["source_evidence_originals"][0]["content_hash"] = "0" * 64
    elif case == "source_time":
        scope["source_evidence_originals"][0]["observed_at"] = "2027-01-01T00:00:00Z"
    elif case == "bank_level":
        next(
            row
            for row in scope["source_evidence_originals"]
            if row["evidence_level"] == "BANK_CONFIRMED"
        )["evidence_level"] = "USER_DECLARED"
    elif case == "calendar":
        scope["suggestion_original"]["public_windows"].pop()
    elif case == "confirmation":
        scope["full_policy_original"]["current_version"]["confirmation"]["accepted"] = False
    elif case == "category":
        row = next(
            row
            for row in scope["source_evidence_originals"]
            if row["source_type"] == "SIMULATED_USER_CATEGORY_CONFIRMATION"
        )
        row["content"]["category"] = "transport"
        row["content_hash"] = configuration_hash(row["content"])
    elif case == "version":
        source = source.model_copy(update={"version_id": UUID(int=99999)})
    elif case == "unadopted":
        value["status"] = "ADVICE_ONLY"
    elif case == "period":
        scope["official_end"] = "2026-02-24"
    elif case == "future_income":
        scope["future_income_in_current_cash_cents"] = 1
    elif case == "missing_count":
        value["retained_command_ids"] = []
    else:
        value["bank_authority"] = True
    try:
        changed = SeasonalAdoptionProof.model_validate_json(__import__("json").dumps(value))
    except ValueError:
        return
    assert not verify_seasonal_adoption(source, changed, proof.as_of, "Asia/Shanghai")


def test_same_period_cannot_be_double_adopted_through_another_policy_or_key() -> None:
    original = original_fixture()
    assert_no_seasonal_overlap(original.scope, [])
    with pytest.raises(ValueError, match="double count"):
        assert_no_seasonal_overlap(original.scope, [original])


@pytest.mark.parametrize(
    "field,value",
    [
        ("accepted", False),
        ("accepted", 1),
        ("amount_cents", 1800),
        ("role", "USER"),
        ("bank_fact", {}),
        ("now", "2026-02-14T00:00:00Z"),
        ("reviewed_hash", "A" * 64),
    ],
)
def test_public_confirmation_rejects_override_authority_and_implicit_acceptance(
    field: str, value: Any
) -> None:
    request = original_fixture().original_request.model_dump(mode="json")
    request[field] = value
    with pytest.raises(ValueError):
        SeasonalAdoptionConfirmRequest.model_validate_json(__import__("json").dumps(request))


def test_preview_uuid_json_is_legal_without_relaxing_unknown_fields() -> None:
    request = SeasonalAdoptionPreviewRequest.model_validate(
        {"expected_version_id": str(VERSION), "window_id": "CN-2026-SPRING_FESTIVAL"}
    )
    assert request.expected_version_id == VERSION
    with pytest.raises(ValueError):
        SeasonalAdoptionPreviewRequest.model_validate({**request.model_dump(), "amount_cents": 1})


def test_wrong_principal_or_expired_cookie_cannot_create_an_adoption_original() -> None:
    original = original_fixture()
    changes: list[dict[str, Any]] = [
        {"role": "AGENT"},
        {"user_id": UUID(int=99)},
        {
            "issued_at": original.recorded_at - timedelta(minutes=16),
            "expires_at": original.recorded_at - timedelta(minutes=1),
        },
    ]
    for changed in changes:
        principal = original.principal_at_command.model_copy(update=changed)
        with pytest.raises(ValueError):
            SeasonalAdoptionOriginal.model_validate(
                {**original.model_dump(), "principal_at_command": principal}
            )


def test_historical_evidence_time_is_not_removed_from_review_hash() -> None:
    scope = scope_fixture()
    value = deepcopy(scope.model_dump(mode="json"))
    value["source_evidence_originals"][0]["valid_from"] = "2024-01-02T00:00:00Z"
    changed = SeasonalAdoptionScope.model_validate_json(__import__("json").dumps(value))
    assert seasonal_review_hash(scope) != seasonal_review_hash(changed)


def test_past_window_and_changed_epoch_are_never_zero_adjustment_success() -> None:
    source, proof = proof_fixture()
    past = datetime(2026, 2, 24, tzinfo=UTC)
    assert not verify_seasonal_adoption(source, proof, past, "Asia/Shanghai")
    assert not verify_seasonal_adoption(
        source, proof.model_copy(update={"epoch_id": UUID(int=99)}), proof.as_of, "Asia/Shanghai"
    )
