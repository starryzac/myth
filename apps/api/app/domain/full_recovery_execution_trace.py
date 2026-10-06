"""Pure verification of new recovery traces, without rewriting legacy hashes."""

import json
from uuid import uuid5

from app.domain.decision_trace import verify_trace
from app.domain.decision_trace_types import DecisionTrace
from app.domain.execution import revalidate_execution
from app.domain.execution_types import ConfirmationGrant, ExecutionContext, ExecutionEffect
from app.domain.full_recovery_execution import (
    ALGORITHM,
    CONSENT_SOURCE,
    MARKER,
    FullRecoveryConfirmation,
    FullRecoveryUserConsent,
    read_frozen_full_recovery_proof,
)
from app.domain.local_actor_session_types import require_local_user


def verify_frozen_full_recovery_trace(trace: DecisionTrace) -> None:
    verify_trace(trace)
    if trace.algorithm_versions.get(MARKER) != ALGORITHM:
        raise ValueError("Exact new recovery trace algorithm required")
    try:
        if trace.phase == "EVALUATION":
            consent = FullRecoveryUserConsent.model_validate_json(
                json.dumps(trace.outcome["full_recovery_user_consent"])
            )
            request = FullRecoveryConfirmation.model_validate_json(json.dumps(trace.inputs))
            require_local_user(
                consent.principal_at_confirmation, trace.user_id, consent.confirmed_at
            )
            if len(trace.sources) != 1:
                raise ValueError("Recovery consent requires its one original evidence")
            source = trace.sources[0]
            if (
                trace.user_id != consent.user_id
                or trace.action_id != consent.action_id
                or trace.as_of != consent.confirmed_at
                or trace.run_id != uuid5(consent.action_id, "full-recovery-user-consent-trace")
                or trace.parent_run_id is None
                or request.expected_epoch_id != consent.epoch_id
                or request.reviewed_effect_hash != consent.original_effect_hash
                or source.id != uuid5(consent.action_id, "full-recovery-user-consent")
                or source.user_id != consent.user_id
                or source.source_type != CONSENT_SOURCE
                or source.source_ref != str(consent.action_id)
                or source.evidence_level != "USER_CONFIRMED_ACTION"
                or source.content != consent.model_dump(mode="json")
                or source.status_at_decision != "VALID"
                or source.content_integrity != "VERIFIED"
                or source.observed_at != trace.as_of
                or source.valid_from != trace.as_of
                or source.valid_to is None
                or source.valid_to <= trace.as_of
            ):
                raise ValueError("Frozen recovery USER consent identities differ")
            return
        read_frozen_full_recovery_proof(trace)
        effect = ExecutionEffect.model_validate_json(json.dumps(trace.inputs["effect"]))
        context = ExecutionContext.model_validate_json(
            json.dumps(trace.inputs["execution_context"])
        )
        if not context.requires_confirmation:
            raise ValueError("Every new recovery phase retains mandatory one-shot confirmation")
        raw = trace.inputs.get("confirmation")
        confirmation = ConfirmationGrant.model_validate_json(json.dumps(raw)) if raw else None
        validation = revalidate_execution(effect, context, confirmation=confirmation)
        if validation.model_dump(mode="json") != trace.outcome["validation"]:
            raise ValueError("Frozen recovery financial validation cannot be reproduced")
        expected_status = "CONFIRMATION_REQUIRED" if trace.phase == "PREPARE" else "READY"
        if validation.status != expected_status:
            raise ValueError("Recorded recovery phase must retain its actual financial acceptance")
        if trace.outcome["autonomy_level"] != "ASK_ONCE":
            raise ValueError("New recovery retains the original mandatory ASK_ONCE action")
    except (KeyError, TypeError) as error:
        raise ValueError("Original recovery trace lost required typed fields") from error
