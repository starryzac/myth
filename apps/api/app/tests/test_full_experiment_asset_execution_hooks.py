"""Direct synthetic risks for installed original hooks; no actual financial evidence."""

from typing import Any, cast
from unittest.mock import MagicMock
from uuid import UUID, uuid5

import pytest
from app.domain.decision_trace import build_trace
from app.domain.decision_trace_types import DecisionTrace, TraceEvidence
from app.domain.execution import revalidate_execution
from app.domain.execution_types import BankCommand, ConfirmationGrant
from app.domain.full_experiment_asset_execution import (
    ALGORITHM,
    MARKER,
    build_full_experiment_asset_effect,
)
from app.domain.full_experiment_asset_execution_trace import (
    verify_frozen_full_experiment_asset_trace,
)
from app.domain.policy_configuration import configuration_hash
from app.services.action_contracts import PrepareActionRequest, PurchaseIntent
from app.services.decision_recording import start_capture
from app.services.execution import prepare_action
from app.services.execution_context import require_full_experiment_asset_context
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_full_experiment_asset_execution import fixture
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def frozen(phase: str = "PREPARE", change: str | None = None) -> DecisionTrace:
    data = fixture("B1")
    effect, proof = build_full_experiment_asset_effect(data, UUID(int=123))
    context = data.context
    consent = None
    sources = list(data.source_originals)
    if phase != "PREPARE":
        identity = uuid5(effect.operation_id, "confirmation:" + proof.effect_hash)
        consent = ConfirmationGrant(
            user_id=data.user_id,
            operation_id=effect.operation_id,
            effect_hash=proof.effect_hash,
            evidence_id=identity,
            confirmed_at=data.as_of,
            expires_at=effect.expires_at,
        )
        content = {
            "simulation": True,
            "user_id": str(data.user_id),
            "action_id": str(effect.operation_id),
            "effect_hash": proof.effect_hash,
            "accepted": True,
            "confirmed_at": data.as_of.isoformat(),
            "valid_until": effect.expires_at.isoformat(),
        }
        digest = configuration_hash(content)
        sources.append(
            TraceEvidence(
                id=identity,
                user_id=data.user_id,
                evidence_level="USER_CONFIRMED_ACTION",
                source_type="USER_ACTION_CONFIRMATION",
                source_ref=str(effect.operation_id),
                content=content,
                content_hash=digest,
                captured_content_hash=digest,
                content_integrity="VERIFIED",
                status_at_decision="VALID",
                observed_at=data.as_of,
                valid_from=data.as_of,
                valid_to=effect.expires_at,
            )
        )
    validation = revalidate_execution(effect, context, confirmation=consent)
    marker = {
        "protocol": ALGORITHM,
        "purpose": "DEVELOPMENT",
        "user_id": str(data.user_id),
        "epoch_id": str(data.epoch_id),
        "request": data.request.model_dump(mode="json"),
        "request_hash": configuration_hash(data.request.model_dump(mode="json")),
        "effect_hash": proof.effect_hash,
        "original_proof": proof.model_dump(mode="json"),
    }
    inputs: dict[str, Any] = {
        "planning": {
            MARKER: {"inputs": data.model_dump(mode="json"), "proof": proof.model_dump(mode="json")}
        },
        "action_request": {
            MARKER: marker,
            "execution": BankCommand(effect=effect, effect_hash=proof.effect_hash).model_dump(
                mode="json"
            ),
        },
        "effect": effect.model_dump(mode="json"),
        "execution_context": context.model_dump(mode="json"),
        "confirmation": consent.model_dump(mode="json") if consent is not None else None,
    }
    outcome: dict[str, Any] = {
        "validation": validation.model_dump(mode="json"),
        "autonomy_level": "ASK_ONCE",
        "decision_status": "COMPUTED",
    }
    if change == "auto":
        outcome["autonomy_level"] = "AUTO_EXECUTE"
    elif change == "requires_false":
        inputs["execution_context"]["requires_confirmation"] = False
    elif change == "outcome":
        outcome["validation"]["reasons"] = ["FABRICATED_PASS"]
    elif change == "missing_consent_original":
        sources = list(data.source_originals)
    elif change == "other_effect":
        inputs["effect"]["amount_cents"] += 1
    return build_trace(
        run_id=uuid5(effect.operation_id, "decision")
        if phase == "PREPARE"
        else uuid5(effect.operation_id, "synthetic-" + phase),
        user_id=data.user_id,
        action_id=effect.operation_id,
        parent_run_id=None if phase == "PREPARE" else uuid5(effect.operation_id, "decision"),
        phase=phase,
        as_of=data.as_of,
        algorithm_versions={MARKER: ALGORITHM},
        inputs=inputs,
        sources=sources,
        policies=[],
        outcome=outcome,
    )


@pytest.mark.parametrize("phase", ["PREPARE", "CONFIRM", "RESERVE", "BANK_ACCEPT"])
def test_original_private_frozen_phase_recomputes_with_exact_user_consent(phase: str) -> None:
    verify_frozen_full_experiment_asset_trace(frozen(phase))


@pytest.mark.parametrize(
    "change", ["auto", "requires_false", "outcome", "missing_consent_original", "other_effect"]
)
def test_rehashed_private_phase_cannot_acquire_auto_or_replace_current_economics(
    change: str,
) -> None:
    with pytest.raises(ValueError):
        verify_frozen_full_experiment_asset_trace(frozen("BANK_ACCEPT", change))


def test_private_context_adds_confirmation_without_editing_original_and_records_it() -> None:
    context = fixture().context.model_copy(update={"requires_confirmation": False})
    mock_session = MagicMock(spec=Session)
    session = cast(Session, mock_session)
    session.info = {}
    capture = start_capture(session)
    bound = require_full_experiment_asset_context(session, context)
    assert bound.requires_confirmation and not context.requires_confirmation
    assert capture.inputs["execution_context"] == bound.model_dump(mode="json")
    mock_session.execute.assert_not_called()
    mock_session.add.assert_not_called()


def test_installed_private_parameter_rejects_non_owned_database_before_query() -> None:
    data = fixture()
    engine = create_engine("sqlite://")
    try:
        request = PrepareActionRequest(
            idempotency_key=data.request.original_request.idempotency_key,
            intent=PurchaseIntent(
                kind="purchase_asset", policy_id=data.request.original_request.mvp_asset_policy_id
            ),
        )
        with pytest.raises(PolicyLifecycleError) as denied:
            prepare_action(
                engine,
                data.user_id,
                request,
                data.as_of,
                _full_experiment_asset_request=data.request,
            )
        assert denied.value.code == "EXPERIMENT_NOT_IMPLEMENTED"
    finally:
        engine.dispose()
