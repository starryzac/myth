"""A notification error cannot replace an already committed global observation."""

from datetime import datetime
from uuid import UUID

from fastapi import Response
from sqlalchemy.engine import Engine

from app.domain.full_action_set_boundary import GlobalBoundaryObservation
from app.domain.full_action_set_boundary_actual import ActualGlobalBoundaryObservation
from app.domain.full_action_set_boundary_full import FullGlobalBoundaryObservation
from app.services.global_boundary_intervention_producer import produce_global_boundary_intervention


def postcommit_global_observation[
    Observation: (
        GlobalBoundaryObservation,
        FullGlobalBoundaryObservation,
        ActualGlobalBoundaryObservation,
    )
](
    engine: Engine, user_id: UUID, result: Observation, now: datetime, response: Response
) -> Observation:
    status = "NOT_CROSSED"
    if (
        result.kind == "BoundaryCrossed"
        and result.global_action_set_complete
        and result.requires_user_attention
        and not result.idempotent_replay
    ):
        try:
            status = produce_global_boundary_intervention(
                engine, user_id, result.observation_run_id, now
            ).status
        except Exception:
            status = "SOURCE_UNVERIFIED"
    response.headers["X-Global-Intervention-Status"] = status
    return result
