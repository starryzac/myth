"""Direct synthetic scope/identity risks; no bank or FULL acceptance claims."""

import json
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from app.db.models import ActionPlan, DecisionRun
from app.domain.decision_trace import build_trace
from app.domain.decision_trace_types import DecisionTrace, TraceEvidence
from app.domain.execution import execution_effect_hash
from app.domain.execution_types import BankCommand, ExecutionEffect
from app.domain.full_recovery_execution import (
    ALGORITHM,
    MARKER,
    FullRecoveryConfirmation,
    FullRecoveryExecuteRequest,
    FullRecoveryExecutionInput,
    FullRecoveryPrepareRequest,
    FullRecoveryUserConsent,
    build_full_recovery_effect,
    derive_full_recovery_execution_proof,
    read_frozen_full_recovery_proof,
    recovery_bank_key,
    verify_full_recovery_consent,
)
from app.domain.full_recovery_planning import plan_full_recovery
from app.domain.local_actor_session_types import LocalActorPrincipal
from app.domain.multi_goal_allocation import SourceReference
from app.domain.policy_configuration import configuration_hash
from app.services import full_recovery_execution as service
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_full_recovery_planning import holding, inputs
from app.tests.test_recovery import NOW, USER
from pydantic import ValidationError
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

EPOCH, ACTION = UUID(int=800), UUID(int=801)


def fixture(delay: int = 0) -> FullRecoveryExecutionInput:
    original = inputs([holding(delay=delay)])
    plan = plan_full_recovery(original)
    candidate = plan.candidates[0]
    quote = candidate.original_quote
    assert quote is not None and candidate.original_policy_version_id is not None
    # An explicitly synthetic scope-only fixture: no assertion that this
    # planning deadline was derived from these original current deficit facts.
    # The service derives it from the actual planner; original financial gates
    # stay separate and cannot be replaced by VERIFIED_SCOPE.
    candidate = candidate.model_copy(update={"on_time": True})
    product = original.holdings[0].original.product
    effect = ExecutionEffect(
        operation_id=ACTION,
        user_id=USER,
        business_key="redeem:" + str(candidate.position_id),
        action_type="REDEEM_ASSET",
        amount_cents=candidate.principal_cents,
        destination_account_id=candidate.destination_account_id,
        goal_id=candidate.goal_id,
        policy_id=plan.linked_asset_policy_id,
        policy_version_id=plan.linked_asset_policy_version_id,
        policy_version_ids=[plan.linked_asset_policy_version_id],
        product_id=product.product_id,
        product_version_number=product.version_number,
        terms_digest=product.terms_digest,
        position_id=candidate.position_id,
        position_account_id=original.holdings[0].original.account_id,
        original_policy_version_id=candidate.original_policy_version_id,
        fee_cents=0,
        loss_cents=0,
        net_cents=candidate.principal_cents,
        quote_id=quote.quote_id,
        settlement_delay_days=delay,
        latest_arrival_at=NOW + timedelta(days=delay, minutes=5),
        valid_from=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )
    return FullRecoveryExecutionInput(
        user_id=USER,
        epoch_id=EPOCH,
        as_of=NOW,
        request=FullRecoveryPrepareRequest(
            policy_id=plan.policy_id,
            expected_version_id=plan.policy_version_id,
            expected_epoch_id=EPOCH,
            position_id=candidate.position_id,
            idempotency_key="original",
        ),
        full_policy_id=plan.policy_id,
        full_policy_version_id=plan.policy_version_id,
        full_configuration=original.configuration,
        full_configuration_hash=configuration_hash(original.configuration.model_dump(mode="json")),
        planning_confirmation_valid=True,
        reference_validation="CURRENT",
        full_effective_status="ACTIVE",
        confirmed_at=original.confirmed_at,
        valid_from=original.valid_from,
        valid_until=None,
        linked_asset_policy_id=plan.linked_asset_policy_id,
        linked_asset_policy_version_id=plan.linked_asset_policy_version_id,
        linked_asset_configuration_hash=plan.linked_asset_configuration_hash,
        planning_status=plan.status,
        planning_response_hash=configuration_hash(plan.model_dump(mode="json")),
        planning_input_hash=plan.input_hash,
        candidate_position_ids=[candidate.position_id],
        selected_position_ids=[candidate.position_id],
        candidate=candidate,
        deadline_at=NOW + timedelta(days=delay, minutes=2),
        original_effect=effect,
        source_refs=[SourceReference(user_id=USER, evidence_id=UUID(int=6), content_hash="a" * 64)],
        protection_inventory_complete=True,
    )


@pytest.mark.parametrize("delay", [0, 1])
def test_original_t0_t1_effect_is_only_tightened_and_proof_is_not_permission(delay: int) -> None:
    data = fixture(delay)
    before = data.model_dump_json()
    effect, proof = build_full_recovery_effect(data)
    assert proof.status == "VERIFIED_SCOPE" and not proof.bank_authority
    assert not proof.grants_authority
    assert effect.latest_arrival_at == data.deadline_at
    assert effect.model_dump(exclude={"latest_arrival_at"}) == data.original_effect.model_dump(
        exclude={"latest_arrival_at"}
    )
    assert execution_effect_hash(effect) != execution_effect_hash(data.original_effect)
    assert derive_full_recovery_execution_proof(data, effect) == proof
    assert build_full_recovery_effect(data) == (effect, proof)
    assert data.model_dump_json() == before


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"epoch_id": UUID(int=999)}, "CURRENT_CONFIRMED_FULL_VERSION_REQUIRED"),
        ({"full_policy_id": UUID(int=999)}, "CURRENT_CONFIRMED_FULL_VERSION_REQUIRED"),
        ({"full_policy_version_id": UUID(int=999)}, "CURRENT_CONFIRMED_FULL_VERSION_REQUIRED"),
        ({"planning_confirmation_valid": False}, "CURRENT_CONFIRMED_FULL_VERSION_REQUIRED"),
        (
            {"reference_validation": "CHANGED_OR_UNAVAILABLE"},
            "CURRENT_CONFIRMED_FULL_VERSION_REQUIRED",
        ),
        ({"full_effective_status": "PAUSED"}, "CURRENT_CONFIRMED_FULL_VERSION_REQUIRED"),
        ({"valid_until": NOW}, "CURRENT_CONFIRMED_FULL_VERSION_REQUIRED"),
        ({"selected_position_ids": []}, "EXACT_SELECTED_ORIGINAL_POSITION_REQUIRED"),
        ({"candidate_position_ids": []}, "EXACT_SELECTED_ORIGINAL_POSITION_REQUIRED"),
        ({"planning_status": "NO_RECOVERY_NEEDED"}, "ACTUAL_RECOVERY_TRIGGER_REQUIRED"),
        ({"deadline_at": NOW - timedelta(seconds=1)}, "ORIGINAL_CONFIRMED_ARRIVAL_BOUND_EXCEEDED"),
        ({"linked_asset_policy_id": UUID(int=999)}, "ORIGINAL_EFFECT_SCOPE_OR_TERMS_DIFFER"),
        (
            {"linked_asset_policy_version_id": UUID(int=999)},
            "ORIGINAL_EFFECT_SCOPE_OR_TERMS_DIFFER",
        ),
    ],
)
def test_current_version_scope_window_trigger_and_timing_cannot_be_bypassed(
    changes: dict[str, Any], reason: str
) -> None:
    data = fixture().model_copy(update=changes)
    result = derive_full_recovery_execution_proof(data)
    assert result.status == "BLOCKED" and reason in result.reasons
    with pytest.raises(ValueError, match="NOT_READY"):
        build_full_recovery_effect(data)


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"source_refs": []}, "COMPLETE_OWNED_ORIGINAL_SOURCES_REQUIRED"),
        ({"protection_inventory_complete": False}, "FULL_PROTECTION_INVENTORY_NOT_PROVEN"),
        ({"source_issues": ["BANK_POSITION_DRIFT"]}, "BANK_POSITION_DRIFT"),
    ],
)
def test_missing_originals_remain_unknown(changes: dict[str, Any], reason: str) -> None:
    result = derive_full_recovery_execution_proof(fixture().model_copy(update=changes))
    assert result.status == "UNKNOWN" and reason in result.reasons


def test_mature_loss_cross_goal_late_candidate_and_principal_tamper_block() -> None:
    data = fixture()
    quote = data.candidate.original_quote
    assert quote is not None
    for candidate in [
        data.candidate.model_copy(
            update={"original_quote": quote.model_copy(update={"kind": "MATURE"})}
        ),
        data.candidate.model_copy(
            update={
                "original_quote": quote.model_copy(
                    update={"loss_cents": 1, "net_cents": quote.net_cents - 1}
                )
            }
        ),
        data.candidate.model_copy(update={"goal_id": UUID(int=999)}),
        data.candidate.model_copy(update={"on_time": False}),
    ]:
        assert (
            derive_full_recovery_execution_proof(
                data.model_copy(update={"candidate": candidate})
            ).status
            == "BLOCKED"
        )
    effect, _ = build_full_recovery_effect(data)
    assert effect.net_cents is not None and data.deadline_at is not None
    for changed in [
        effect.model_copy(
            update={"amount_cents": effect.amount_cents + 1, "net_cents": effect.net_cents + 1}
        ),
        effect.model_copy(update={"destination_account_id": UUID(int=999)}),
        effect.model_copy(update={"quote_id": UUID(int=999)}),
        effect.model_copy(update={"latest_arrival_at": data.deadline_at + timedelta(seconds=1)}),
    ]:
        assert derive_full_recovery_execution_proof(data, changed).status == "BLOCKED"


def test_current_instant_deadline_is_not_advanced_to_make_real_confirmation_possible() -> None:
    data = fixture().model_copy(update={"deadline_at": NOW})
    effect, proof = build_full_recovery_effect(data)
    assert proof.status == "VERIFIED_SCOPE" and effect.latest_arrival_at == NOW
    later = data.model_copy(update={"as_of": NOW + timedelta(microseconds=1)})
    assert derive_full_recovery_execution_proof(later, effect).status == "BLOCKED"


def test_strict_small_json_and_no_client_financial_fields() -> None:
    raw = fixture().request.model_dump(mode="json")
    assert (
        FullRecoveryPrepareRequest.model_validate_json(json.dumps(raw)).model_dump(mode="json")
        == raw
    )
    for field, value in [
        ("amount_cents", 1),
        ("role", "USER"),
        ("at", NOW.isoformat()),
        ("success", True),
        ("quote", {}),
        ("actor", {}),
    ]:
        with pytest.raises(ValidationError):
            FullRecoveryPrepareRequest.model_validate_json(json.dumps({**raw, field: value}))
    for value in [True, 1, "1", None]:
        with pytest.raises(ValidationError):
            FullRecoveryPrepareRequest.model_validate_json(
                json.dumps({**raw, "position_id": value})
            )
    for accepted in [False, 1, "true"]:
        with pytest.raises(ValidationError):
            FullRecoveryConfirmation.model_validate_json(
                json.dumps(
                    {
                        "expected_epoch_id": str(EPOCH),
                        "reviewed_effect_hash": "a" * 64,
                        "accepted": accepted,
                    }
                )
            )


def test_original_key_is_stable_and_different_keys_never_merge() -> None:
    assert recovery_bank_key("original") == recovery_bank_key("original")
    assert recovery_bank_key("original") != recovery_bank_key("other")
    for value in ["", " ", "x" * 121]:
        with pytest.raises(ValueError):
            recovery_bank_key(value)


def consent_fixture() -> tuple[FullRecoveryUserConsent, ExecutionEffect]:
    effect, _ = build_full_recovery_effect(fixture())
    principal = LocalActorPrincipal(
        user_id=USER,
        role="USER",
        session_id=UUID(int=820),
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=10),
    )
    consent = FullRecoveryUserConsent(
        user_id=USER,
        epoch_id=EPOCH,
        action_id=ACTION,
        original_effect_hash=execution_effect_hash(effect),
        original_confirmation_evidence_id=UUID(int=821),
        principal_at_confirmation=principal,
        confirmed_at=NOW,
    )
    return consent, effect


def test_signed_user_original_consent_binds_effect_epoch_and_original_observation() -> None:
    consent, effect = consent_fixture()
    verify_full_recovery_consent(consent, effect, EPOCH)
    for changed in [
        consent.model_copy(update={"epoch_id": UUID(int=999)}),
        consent.model_copy(update={"action_id": UUID(int=999)}),
        consent.model_copy(update={"original_effect_hash": "b" * 64}),
        consent.model_copy(update={"confirmed_at": effect.expires_at}),
        consent.model_copy(
            update={
                "principal_at_confirmation": consent.principal_at_confirmation.model_copy(
                    update={"role": "AGENT"}
                )
            }
        ),
    ]:
        with pytest.raises(ValueError):
            verify_full_recovery_consent(changed, effect, EPOCH)
    # Historical receipt verification uses the original observation, not a renewed grant.
    assert not consent.grants_authority


def test_missing_installed_bank_hook_refuses_before_original_prepare(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = MagicMock()
    monkeypatch.setattr(service, "prepare_action", called)
    consent, _ = consent_fixture()
    with pytest.raises(PolicyLifecycleError, match="严格接缝"):
        service.prepare_full_recovery_execution(
            cast(Engine, object()),
            USER,
            fixture().request,
            consent.principal_at_confirmation,
            NOW,
        )
    called.assert_not_called()


def test_agent_cannot_start_even_if_it_knows_the_exact_request() -> None:
    consent, _ = consent_fixture()
    with pytest.raises(PolicyLifecycleError, match="USER"):
        service.prepare_full_recovery_execution(
            cast(Engine, object()),
            USER,
            fixture().request,
            consent.principal_at_confirmation.model_copy(update={"role": "AGENT"}),
            NOW,
        )


def test_stripped_live_marker_is_still_recognized_from_retained_prepare() -> None:
    run = MagicMock(spec=DecisionRun)
    run.user_id, run.subject_action_plan_id = USER, ACTION
    run.input_snapshot = {
        "decision_trace": {"algorithm_versions": {MARKER: ALGORITHM}, "inputs": {}}
    }
    run.snapshot_hash = configuration_hash(run.input_snapshot)
    action = MagicMock(spec=ActionPlan)
    action.request, action.idempotency_key = {}, "action:stripped"
    action.id, action.user_id, action.decision_run_id = ACTION, USER, UUID(int=822)
    session = MagicMock(spec=Session)
    session.get.return_value = run
    assert service.has_full_recovery_binding(session, action)
    run.snapshot_hash = "b" * 64
    with pytest.raises(PolicyLifecycleError, match="摘要"):
        service.has_full_recovery_binding(session, action)


def frozen_fixture() -> tuple[DecisionTrace, FullRecoveryExecutionInput, BankCommand]:
    data = fixture()
    source_hash = configuration_hash({})
    data = data.model_copy(
        update={
            "source_refs": [
                SourceReference(user_id=USER, evidence_id=UUID(int=6), content_hash=source_hash)
            ]
        }
    )
    effect, proof = build_full_recovery_effect(data)
    command = BankCommand(effect=effect, effect_hash=execution_effect_hash(effect))
    marker = {
        "protocol": ALGORITHM,
        "user_id": str(USER),
        "epoch_id": str(EPOCH),
        "request": data.request.model_dump(mode="json"),
        "request_hash": configuration_hash(data.request.model_dump(mode="json")),
        "effect_hash": command.effect_hash,
        "original_proof": proof.model_dump(mode="json"),
    }
    source = TraceEvidence(
        id=UUID(int=6),
        user_id=USER,
        evidence_level="BANK_CONFIRMED",
        source_type="SYNTHETIC_RISK",
        source_ref="scope-only-fixture",
        content={},
        content_hash=source_hash,
        captured_content_hash=source_hash,
        content_integrity="VERIFIED",
        status_at_decision="VALID",
        observed_at=NOW,
        valid_from=NOW,
    )
    trace = build_trace(
        run_id=UUID(int=822),
        user_id=USER,
        phase="PREPARE",
        as_of=NOW,
        action_id=ACTION,
        algorithm_versions={"trace": "decision-trace-v1", MARKER: ALGORITHM},
        inputs={
            "effect": effect.model_dump(mode="json"),
            "planning": {
                MARKER: {
                    "inputs": data.model_dump(mode="json"),
                    "proof": proof.model_dump(mode="json"),
                }
            },
            "action_request": {
                "execution": command.model_dump(mode="json"),
                MARKER: marker,
                "intent": {"kind": "redeem_asset", "position_id": str(data.request.position_id)},
            },
        },
        sources=[source],
        policies=[],
        outcome={},
    )
    return trace, data, command


def test_historical_typed_entry_recalculates_actual_frozen_source_subdenominator() -> None:
    trace, data, command = frozen_fixture()
    expected = derive_full_recovery_execution_proof(data, command.effect)
    assert read_frozen_full_recovery_proof(trace) == expected
    # A valid newly computed trace hash does not excuse missing/substituted originals.
    for change in ["source", "input_owner", "marker", "effect", "intent"]:
        fields = trace.model_dump(mode="python", exclude={"input_hash", "trace_hash"})
        if change == "source":
            fields["sources"] = []
        elif change == "input_owner":
            fields["inputs"]["planning"][MARKER]["inputs"]["user_id"] = str(UUID(int=999))
        elif change == "marker":
            fields["inputs"]["action_request"][MARKER]["request_hash"] = "b" * 64
        elif change == "effect":
            fields["inputs"]["effect"]["destination_account_id"] = str(UUID(int=999))
        else:
            fields["inputs"]["action_request"]["intent"]["position_id"] = str(UUID(int=999))
        with pytest.raises(ValueError):
            changed = build_trace(**fields)
            read_frozen_full_recovery_proof(changed)


def test_original_reader_checks_live_projection_even_with_matching_frozen_hashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace, data, command = frozen_fixture()
    action = MagicMock(spec=ActionPlan)
    effect = command.effect
    action.id, action.user_id, action.decision_run_id = ACTION, USER, trace.run_id
    action.request = trace.inputs["action_request"]
    action.request_hash = configuration_hash(action.request)
    action.idempotency_key = recovery_bank_key(data.request.idempotency_key)
    action.action_type, action.amount_cents, action.goal_id = (
        "ASSET_REDEEM",
        effect.amount_cents,
        None,
    )
    action.policy_version_id, action.source_account_id = (
        effect.policy_version_id,
        effect.position_account_id,
    )
    action.destination_account_id, action.expires_at = (
        effect.destination_account_id,
        effect.expires_at,
    )
    monkeypatch.setattr(
        service, "get_action", lambda *args: SimpleNamespace(effect_hash=command.effect_hash)
    )
    monkeypatch.setattr(
        service,
        "get_decision_trace",
        lambda *args: SimpleNamespace(
            completeness="COMPLETE", audit_chain_status="VALID", trace=trace
        ),
    )
    session = MagicMock(spec=Session)
    assert (
        service.read_original_full_recovery_request(session, USER, action, command, NOW)
        == data.request
    )
    action.destination_account_id = UUID(int=999)
    with pytest.raises(PolicyLifecycleError, match="projection"):
        service.read_original_full_recovery_request(session, USER, action, command, NOW)


def test_not_found_is_nonfinal_and_lookup_does_not_prepare(monkeypatch: pytest.MonkeyPatch) -> None:
    session = MagicMock(spec=Session)
    session.scalar.return_value = None
    monkeypatch.setattr(service, "_read_snapshot", lambda *_: None)
    result = service.lookup_full_recovery_execution(session, USER, "original", NOW)
    assert result.status == "NOT_FOUND_NOT_FINAL" and not result.not_found_is_final
    assert result.action is None and not result.current_authority


def test_existing_operation_reconciles_same_identity_without_fresh_planning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    consent, effect = consent_fixture()
    command = BankCommand(effect=effect, effect_hash=execution_effect_hash(effect))
    request = fixture().request
    session = MagicMock(spec=Session)
    session.scalar.return_value = object()  # Explicit synthetic existing-operation branch.
    action = MagicMock(spec=ActionPlan)
    action.id, action.user_id = ACTION, USER

    @contextmanager
    def reader(_engine: Engine) -> Iterator[Session]:
        yield session

    monkeypatch.setattr(service, "_reader", reader)
    monkeypatch.setattr(service, "audit_command_guard", lambda *_: nullcontext())
    monkeypatch.setattr(service, "require_installed_full_recovery_guards", lambda: None)
    monkeypatch.setattr(service, "_original_action", lambda *_: (action, command, request))
    monkeypatch.setattr(service, "_consent", lambda *_: consent)
    fresh = MagicMock(side_effect=AssertionError("Must not create a fresh bank grant on recovery"))
    monkeypatch.setattr(service, "read_full_recovery_execution_inputs", fresh)
    original_execute = MagicMock(return_value=object())
    monkeypatch.setattr(service, "execute_action", original_execute)
    later = NOW + timedelta(days=1)
    renewed = consent.principal_at_confirmation.model_copy(
        update={
            "issued_at": later - timedelta(minutes=1),
            "expires_at": later + timedelta(minutes=10),
        }
    )
    engine = cast(Engine, object())
    service.execute_full_recovery_action(
        engine,
        USER,
        ACTION,
        FullRecoveryExecuteRequest(
            expected_epoch_id=EPOCH, reviewed_effect_hash=command.effect_hash
        ),
        renewed,
        later,
    )
    fresh.assert_not_called()
    original_execute.assert_called_once_with(engine, USER, ACTION, later)
