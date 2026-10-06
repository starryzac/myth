"""Current assessment of the new recovery scope in one original read snapshot."""

from datetime import datetime
from uuid import UUID

from app.db.models import ActionPlan
from app.domain.execution_types import BankCommand
from app.domain.full_recovery_execution import (
    FullRecoveryExecutionProof,
    derive_full_recovery_execution_proof,
)
from app.services.full_recovery_execution import (
    read_full_recovery_execution_inputs,
    read_original_full_recovery_request,
)
from app.services.policy_lifecycle import PolicyLifecycleError
from sqlalchemy.orm import Session


def read_current_full_recovery_proof(
    session: Session,
    user_id: UUID,
    action: ActionPlan,
    command: BankCommand,
    now: datetime,
) -> FullRecoveryExecutionProof:
    body = read_original_full_recovery_request(session, user_id, action, command, now)
    data = read_full_recovery_execution_inputs(
        session, user_id, body, action.id, now, original_effect=command.effect
    )
    proof = derive_full_recovery_execution_proof(data, command.effect)
    if proof.status != "VERIFIED_SCOPE":
        raise PolicyLifecycleError(
            "FULL_RECOVERY_CURRENT_SCOPE_REJECTED", ",".join(proof.reasons), 409
        )
    return proof
