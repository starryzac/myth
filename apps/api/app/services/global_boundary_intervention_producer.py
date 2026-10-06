"""Append a notification from an original verified crossing, never deliver or grant."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from app.domain.boundary_types import BoundaryModel
from app.domain.full_intervention import GlobalBoundaryObservationRequest
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import audit_read_scope
from app.services.full_intervention import (
    InterventionCommandResponse,
    _global_boundary_source,
    _reader,
    observe_intervention,
)
from app.services.policy_lifecycle import PolicyLifecycleError, _now
from sqlalchemy.engine import Engine


class GlobalBoundaryProducerResult(BoundaryModel):
    protocol: Literal["global-boundary-intervention-producer-v1"] = (
        "global-boundary-intervention-producer-v1"
    )
    simulation: Literal[True] = True
    status: Literal["OBSERVED", "ORIGINAL_RECOVERED", "NOT_CROSSED", "SOURCE_UNVERIFIED"]
    observation_run_id: UUID
    original_response: InterventionCommandResponse | None = None
    error_code: str | None = None
    delivered: Literal[False] = False
    acknowledged: Literal[False] = False
    actual_human_view_verified: Literal[False] = False
    authority_granted: Literal[False] = False


def produce_global_boundary_intervention(
    engine: Engine, user_id: UUID, observation_run_id: UUID, now: datetime
) -> GlobalBoundaryProducerResult:
    now = _now(now)
    try:
        with _reader(engine) as read, audit_read_scope(read):
            source = _global_boundary_source(read, user_id, observation_run_id, now)
            value = source.boundary
            if value is None or value.get("kind") != "BoundaryCrossed" or not source.attention:
                return GlobalBoundaryProducerResult(
                    status="NOT_CROSSED", observation_run_id=observation_run_id
                )
            if value.get("global_action_set_complete") is not True or value.get("user_id") != str(
                user_id
            ):
                raise PolicyLifecycleError(
                    "GLOBAL_PRODUCER_SOURCE_UNKNOWN", "原完整观察未验真", 409
                )
            epoch = UUID(value["epoch_id"])
            key = configuration_hash(
                {
                    "protocol": "global-boundary-intervention-producer-v1",
                    "user_id": str(user_id),
                    "epoch_id": str(epoch),
                    "observation_run_id": str(observation_run_id),
                    "source_trace_hash": source.trace.trace_hash,
                    "semantic_key": source.semantic_key,
                }
            )
            body = GlobalBoundaryObservationRequest(
                kind="GLOBAL_ACTION_SET_BOUNDARY",
                observation_run_id=observation_run_id,
                reviewed_source_trace_hash=source.trace.trace_hash,
                expected_epoch_id=epoch,
                idempotency_key="global-boundary-producer:" + key,
            )
        result = observe_intervention(engine, user_id, body, now)
        return GlobalBoundaryProducerResult(
            status="ORIGINAL_RECOVERED" if result.replayed_original_receipt else "OBSERVED",
            observation_run_id=observation_run_id,
            original_response=result,
        )
    except PolicyLifecycleError as error:
        return GlobalBoundaryProducerResult(
            status="SOURCE_UNVERIFIED", observation_run_id=observation_run_id, error_code=error.code
        )
