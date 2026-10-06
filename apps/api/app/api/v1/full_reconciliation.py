"""Current independent-bank reconciliation; no write or repair endpoint."""

from typing import Annotated

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.domain.full_reconciliation import FullReconciliationReport
from app.services.full_reconciliation import full_reconciliation
from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

router = APIRouter(
    prefix="/api/v1/reconciliation",
    tags=["独立模拟账本只读对账"],
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
)


class ReconciliationQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")


@router.get(
    "/current",
    response_model=FullReconciliationReport,
    operation_id="read_full_current_reconciliation",
)
def read_current_reconciliation(
    query: Annotated[ReconciliationQuery, Query()],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullReconciliationReport:
    return full_reconciliation(session, user.id, now)
