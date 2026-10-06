"""Read-only actual composition in the same request-owned database snapshot."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.full_action_set_boundary_composed import (
    ComposedActionSetInput,
    ComposedActionSetSnapshot,
    derive_composed_action_set,
)
from app.services.decision_recording import DecisionCapture
from app.services.full_action_set_payment_producers import (
    capture_current_periodic_payment_producers,
)
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class ComposedActionSetCapture:
    inputs: ComposedActionSetInput
    result: ComposedActionSetSnapshot
    originals: DecisionCapture


def capture_composed_action_set(
    session: Session, user_id: UUID, now: datetime
) -> ComposedActionSetCapture:
    family = capture_current_periodic_payment_producers(session, user_id, now)
    inputs = ComposedActionSetInput(periodic=family.inputs)
    return ComposedActionSetCapture(inputs, derive_composed_action_set(inputs), family.originals)


def read_current_composed_action_set(
    session: Session, user_id: UUID, now: datetime
) -> ComposedActionSetSnapshot:
    return capture_composed_action_set(session, user_id, now).result
