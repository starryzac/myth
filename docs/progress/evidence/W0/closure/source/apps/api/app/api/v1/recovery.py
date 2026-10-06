"""Evidence-bound simulation: preview, run, and read independently reconciled recovery."""

from typing import Annotated
from uuid import UUID

from app.api.dependencies import (
    ClockDependency,
    DemoUserDependency,
    SessionDependency,
    get_engine,
)
from app.api.errors import ErrorEnvelope
from app.services.recovery import (
    RecoveryPreviewResponse,
    RecoveryRunResponse,
    get_recovery_run,
    preview_recovery,
    run_recovery,
)
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator
from sqlalchemy.engine import Engine

router = APIRouter(
    prefix="/api/v1/recovery",
    tags=["模拟安全恢复"],
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
)


class RecoveryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecoveryRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: Annotated[StrictStr, Field(min_length=1, max_length=160)]

    @field_validator("idempotency_key")
    @classmethod
    def nonblank_key(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A nonblank idempotency key is required")
        return value


@router.get("/preview", response_model=RecoveryPreviewResponse, operation_id="preview_recovery")
def read_recovery_preview(
    query: Annotated[RecoveryQuery, Query()],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> RecoveryPreviewResponse:
    return preview_recovery(session, user.id, now)


@router.post("/runs", response_model=RecoveryRunResponse, operation_id="run_recovery")
def create_recovery_run(
    body: RecoveryRunRequest,
    query: Annotated[RecoveryQuery, Query()],
    engine: Annotated[Engine, Depends(get_engine)],
    user: DemoUserDependency,
    now: ClockDependency,
) -> RecoveryRunResponse:
    # The service owns three real transaction boundaries. The dependency session
    # only resolves the synthetic identity; it must not wrap the bank transaction.
    return run_recovery(engine, user.id, body.idempotency_key, now)


@router.get("/runs/{run_id}", response_model=RecoveryRunResponse, operation_id="get_recovery_run")
def read_recovery_run(
    run_id: UUID,
    query: Annotated[RecoveryQuery, Query()],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> RecoveryRunResponse:
    return get_recovery_run(session, user.id, run_id, now)
