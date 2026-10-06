"""Request-local reuse of one exact actual inventory for periodic and recovery."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.full_action_set_boundary_composed import ComposedActionSetInput
from app.domain.full_action_set_boundary_recovery_composed import (
    RecoveryComposedActionSetInput,
    RecoveryComposedActionSetSnapshot,
    derive_recovery_composed_action_set,
)
from app.services.decision_recording import DecisionCapture
from app.services.full_action_set_boundary_actual import capture_actual_action_set
from app.services.full_action_set_payment_producers import (
    capture_current_periodic_payment_producers,
)
from app.services.full_action_set_recovery_producers import capture_current_recovery_producers
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class RecoveryComposedActionSetCapture:
    inputs: RecoveryComposedActionSetInput
    result: RecoveryComposedActionSetSnapshot
    originals: DecisionCapture


def capture_recovery_composed_action_set(
    session: Session, user_id: UUID, now: datetime
) -> RecoveryComposedActionSetCapture:
    original = capture_actual_action_set(session, user_id, now)
    periodic = capture_current_periodic_payment_producers(
        session, user_id, now, original_actual_capture=original
    )
    recovery = capture_current_recovery_producers(
        session, user_id, now, original_actual_capture=original
    )
    inputs = RecoveryComposedActionSetInput(
        composed=ComposedActionSetInput(periodic=periodic.inputs), recovery=recovery.inputs
    )
    originals = DecisionCapture()
    for capture in (original.originals, periodic.originals, recovery.originals):
        for key, source in capture.sources.items():
            if key in originals.sources and originals.sources[key] != source:
                raise ValueError("RECOVERY_COMPOSED_CURRENT_SOURCE_COPY_DIFFERS")
            originals.sources[key] = source
        for key, policy in capture.policies.items():
            if key in originals.policies and originals.policies[key] != policy:
                raise ValueError("RECOVERY_COMPOSED_CURRENT_POLICY_COPY_DIFFERS")
            originals.policies[key] = policy
    return RecoveryComposedActionSetCapture(
        inputs, derive_recovery_composed_action_set(inputs), originals
    )


def read_current_recovery_composed_action_set(
    session: Session, user_id: UUID, now: datetime
) -> RecoveryComposedActionSetSnapshot:
    return capture_recovery_composed_action_set(session, user_id, now).result
