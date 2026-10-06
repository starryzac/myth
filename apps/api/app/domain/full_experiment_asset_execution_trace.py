"""Pure selected-effect and exact USER decision checks for the explicit private protocol."""

import json
from datetime import datetime
from uuid import uuid5

from app.domain.decision_trace_types import DecisionTrace
from app.domain.execution import revalidate_execution
from app.domain.execution_types import ConfirmationGrant, ExecutionContext, ExecutionEffect
from app.domain.full_experiment_asset_execution import read_frozen_full_experiment_asset_proof
from app.domain.policy_configuration import configuration_hash


def verify_frozen_full_experiment_asset_trace(trace: DecisionTrace) -> None:
    read_frozen_full_experiment_asset_proof(trace)
    if trace.phase not in {"PREPARE", "CONFIRM", "RESERVE", "BANK_ACCEPT"}:
        raise ValueError("Unknown private selected-effect execution phase")
    effect = ExecutionEffect.model_validate_json(json.dumps(trace.inputs["effect"]))
    context = ExecutionContext.model_validate_json(json.dumps(trace.inputs["execution_context"]))
    original_effect = trace.inputs["action_request"]["execution"]["effect"]
    if (
        effect.model_dump(mode="json") != original_effect
        or not context.requires_confirmation
        or trace.outcome["autonomy_level"] != "ASK_ONCE"
        or trace.outcome["decision_status"] != "COMPUTED"
    ):
        raise ValueError("The private selected effect cannot acquire generic AUTO authority")
    raw_confirmation = trace.inputs.get("confirmation")
    confirmation = (
        ConfirmationGrant.model_validate_json(json.dumps(raw_confirmation))
        if raw_confirmation is not None
        else None
    )
    result = revalidate_execution(effect, context, confirmation=confirmation)
    if result.model_dump(mode="json") != trace.outcome["validation"]:
        raise ValueError("The exact selected-effect financial decision cannot reproduce")
    if trace.phase == "PREPARE":
        if confirmation is not None or result.status != "CONFIRMATION_REQUIRED":
            raise ValueError("The private prepare is an explicit unconfirmed ASK proposal")
    else:
        if (
            confirmation is None
            or result.status != "READY"
            or trace.parent_run_id != uuid5(effect.operation_id, "decision")
        ):
            raise ValueError("The private phase requires the original exact USER confirmation")
        source = next((row for row in trace.sources if row.id == confirmation.evidence_id), None)
        if source is None:
            raise ValueError("The actual frozen USER confirmation original is missing")
        content = source.content
        if (
            source.id != uuid5(effect.operation_id, "confirmation:" + confirmation.effect_hash)
            or source.user_id != effect.user_id
            or source.evidence_level != "USER_CONFIRMED_ACTION"
            or source.source_type != "USER_ACTION_CONFIRMATION"
            or source.source_ref != str(effect.operation_id)
            or source.status_at_decision != "VALID"
            or source.content_integrity != "VERIFIED"
            or configuration_hash(content) != source.content_hash
            or content.get("simulation") is not True
            or content.get("accepted") is not True
            or content.get("user_id") != str(effect.user_id)
            or content.get("action_id") != str(effect.operation_id)
            or content.get("effect_hash") != confirmation.effect_hash
            or datetime.fromisoformat(content["confirmed_at"]) != confirmation.confirmed_at
            or datetime.fromisoformat(content["valid_until"]) != confirmation.expires_at
            or not confirmation.confirmed_at <= source.observed_at <= trace.as_of
            or source.valid_from > trace.as_of
            or (source.valid_to is not None and trace.as_of >= source.valid_to)
        ):
            raise ValueError("Frozen confirmation cannot be replaced by a label or receipt")
