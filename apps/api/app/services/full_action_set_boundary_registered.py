"""One RR/RO actual capture drives every registered current family in v5."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.full_action_set_boundary_composed import ComposedActionSetInput
from app.domain.full_action_set_boundary_recovery_composed import RecoveryComposedActionSetInput
from app.domain.full_action_set_boundary_registered import (
    RegisteredActionSetInput,
    RegisteredActionSetSnapshot,
    derive_registered_action_set,
)
from app.services.decision_recording import DecisionCapture
from app.services.full_action_set_boundary_actual import capture_actual_action_set
from app.services.full_action_set_joint_producers import capture_current_joint_producers
from app.services.full_action_set_payment_producers import (
    capture_current_periodic_payment_producers,
)
from app.services.full_action_set_recovery_producers import capture_current_recovery_producers
from app.services.full_action_set_release_producers import capture_current_release_producers
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class RegisteredActionSetCapture:
    inputs: RegisteredActionSetInput
    result: RegisteredActionSetSnapshot
    originals: DecisionCapture


def capture_registered_action_set(
    session: Session, user_id: UUID, now: datetime
) -> RegisteredActionSetCapture:
    actual = capture_actual_action_set(session, user_id, now)
    periodic = capture_current_periodic_payment_producers(
        session, user_id, now, original_actual_capture=actual
    )
    recovery = capture_current_recovery_producers(
        session, user_id, now, original_actual_capture=actual
    )
    release = capture_current_release_producers(
        session, user_id, now, original_actual_capture=actual
    )
    joint = capture_current_joint_producers(session, user_id, now, original_actual_capture=actual)
    inputs = RegisteredActionSetInput(
        recovery_composed=RecoveryComposedActionSetInput(
            composed=ComposedActionSetInput(periodic=periodic.inputs), recovery=recovery.inputs
        ),
        release=release.inputs,
        joint=joint.inputs,
    )
    originals = DecisionCapture()
    for capture in (
        actual.originals,
        periodic.originals,
        recovery.originals,
        release.originals,
        joint.originals,
    ):
        for identity, source in capture.sources.items():
            if identity in originals.sources and originals.sources[identity] != source:
                raise ValueError("REGISTERED_CURRENT_SOURCE_COPY_DIFFERS")
            originals.sources[identity] = source
        for identity, policy in capture.policies.items():
            if identity in originals.policies and originals.policies[identity] != policy:
                raise ValueError("REGISTERED_CURRENT_POLICY_COPY_DIFFERS")
            originals.policies[identity] = policy
    return RegisteredActionSetCapture(inputs, derive_registered_action_set(inputs), originals)


def read_current_registered_action_set(
    session: Session, user_id: UUID, now: datetime
) -> RegisteredActionSetSnapshot:
    return capture_registered_action_set(session, user_id, now).result
