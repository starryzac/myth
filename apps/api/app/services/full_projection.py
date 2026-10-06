"""Annual conditional cash projections from one verified current database snapshot."""

from datetime import UTC, date, datetime, timedelta, timezone
from typing import Annotated, Literal
from uuid import UUID

from app.domain.boundary import compute_boundary
from app.domain.boundary_types import BoundaryModel, BoundaryPoint, BoundaryResult
from app.services.boundary import BoundaryContext, BoundarySourceIssue
from app.services.dashboard_helpers import current_epoch_audit
from app.services.dashboard_types import DashboardAuditCard
from app.services.financial_read import finalize_financial_context, load_verified_financial_context
from app.services.policy_lifecycle import PolicyLifecycleError
from pydantic import Field, StrictInt
from sqlalchemy import text
from sqlalchemy.orm import Session

ANNUAL_HORIZON_DAYS = 365


class FutureIncomeProjection(BoundaryModel):
    status: Literal["NOT_IMPLEMENTED_NO_REGISTERED_SOURCE"] = "NOT_IMPLEMENTED_NO_REGISTERED_SOURCE"
    included_in_execution_cents: Literal[0] = 0
    included_in_planning_cents: Literal[0] = 0
    reason: str = "未来收入尚无已登记来源适配器；当前曲线不包含未来收入预测。"


class AnnualDailyCheckpoint(BoundaryModel):
    day: Annotated[StrictInt, Field(ge=0, le=365)]
    date: date
    status: Literal["PROVEN", "NOT_PROVEN"]
    before_payment: BoundaryPoint | None
    after_payment: BoundaryPoint | None
    after_principal: BoundaryPoint | None
    minimum_intraday_margin_cents: StrictInt | None


class UnavailablePrincipal(BoundaryModel):
    position_id: UUID
    reason: Literal["NO_VERIFIED_RETURN_DATE"] = "NO_VERIFIED_RETURN_DATE"


class AnnualProjectionResponse(BoundaryModel):
    schema_version: Literal["annual-planning-v1"] = "annual-planning-v1"
    simulation: Literal[True] = True
    user_id: UUID
    as_of: datetime
    timezone: Literal["Asia/Shanghai", "UTC"]
    horizon_days: Literal[365] = 365
    grants_authority: Literal[False] = False
    projection_basis: Literal["CURRENT_VERIFIED_FACTS_CONDITIONAL_COMMITMENTS"] = (
        "CURRENT_VERIFIED_FACTS_CONDITIONAL_COMMITMENTS"
    )
    future_points_are_settled_cash: Literal[False] = False
    execution_view_horizon_days: Literal[90] = 90
    execution_view: BoundaryResult
    annual_projection: BoundaryResult
    initial_checkpoint: AnnualDailyCheckpoint
    daily_checkpoints: Annotated[list[AnnualDailyCheckpoint], Field(min_length=365, max_length=365)]
    future_income: FutureIncomeProjection
    unavailable_principal: list[UnavailablePrincipal]
    source_evidence_ids: list[UUID]
    input_digest: str
    source_issues: list[BoundarySourceIssue]
    audit: DashboardAuditCard


def _checkpoint(
    day: int, calendar_date: date, points: list[BoundaryPoint]
) -> AnnualDailyCheckpoint:
    if not points:
        return AnnualDailyCheckpoint(
            day=day,
            date=calendar_date,
            status="NOT_PROVEN",
            before_payment=None,
            after_payment=None,
            after_principal=None,
            minimum_intraday_margin_cents=None,
        )
    phases = {point.phase: point for point in points}
    if (
        len(points) != 3
        or set(phases) != {"BEFORE_PAYMENT", "AFTER_PAYMENT", "AFTER_PRINCIPAL"}
        or any(point.day != day or point.date != calendar_date for point in points)
    ):
        raise ValueError("Annual curve must contain each original intraday phase exactly once")
    return AnnualDailyCheckpoint(
        day=day,
        date=calendar_date,
        status="PROVEN",
        before_payment=phases["BEFORE_PAYMENT"],
        after_payment=phases["AFTER_PAYMENT"],
        after_principal=phases["AFTER_PRINCIPAL"],
        minimum_intraday_margin_cents=min(point.margin_cents for point in points),
    )


def project_verified_context(
    context: BoundaryContext,
    audit: DashboardAuditCard,
) -> AnnualProjectionResponse:
    """Use verified current facts; a curve never promotes a future amount to current cash."""
    if not audit.complete or audit.status != "VALID":
        context.sources.issue(
            "AUDIT_NOT_VERIFIED", str(audit.epoch_id), "完整年度预测需要当前有效的审计链"
        )
    context, issues, digest = finalize_financial_context(context, audit)
    # Explicitly revalidate copies instead of trusting model_copy(update=...). The
    # public MVP view stays ninety days even if an internal caller supplies another horizon.
    execution_snapshot = context.snapshot.model_validate(
        {**context.snapshot.model_dump(), "horizon_days": 90}
    )
    annual_snapshot = context.snapshot.model_validate(
        {**context.snapshot.model_dump(), "horizon_days": ANNUAL_HORIZON_DAYS}
    )
    execution = compute_boundary(
        execution_snapshot, context.versions, context.positions, context.products
    )
    annual = compute_boundary(
        annual_snapshot, context.versions, context.positions, context.products
    )
    zone = UTC if annual_snapshot.timezone == "UTC" else timezone(timedelta(hours=8))
    first = annual_snapshot.as_of.astimezone(zone).date()
    grouped: dict[int, list[BoundaryPoint]] = {}
    for point in annual.calculation_trace:
        grouped.setdefault(point.day, []).append(point)
    if annual.status != "INSUFFICIENT_EVIDENCE" and set(grouped) != set(range(366)):
        raise ValueError("Annual financial computation did not cover all 365 future calendar days")
    return AnnualProjectionResponse(
        user_id=context.sources.user_id,
        as_of=annual_snapshot.as_of,
        timezone=annual_snapshot.timezone,
        execution_view=execution,
        annual_projection=annual,
        initial_checkpoint=_checkpoint(0, first, grouped.get(0, [])),
        daily_checkpoints=[
            _checkpoint(day, first + timedelta(days=day), grouped.get(day, []))
            for day in range(1, 366)
        ],
        future_income=FutureIncomeProjection(),
        unavailable_principal=[
            UnavailablePrincipal(position_id=position.position_id)
            for position in sorted(context.positions, key=lambda position: position.position_id)
            if position.status not in {"REDEEMED", "UNKNOWN"}
            and position.principal_available_at is None
        ],
        source_evidence_ids=sorted(context.sources.used),
        input_digest=digest,
        source_issues=issues,
        audit=audit,
    )


def compute_annual_projection(
    session: Session,
    user_id: UUID,
    now: datetime,
) -> AnnualProjectionResponse:
    """One caller-owned RO/RR transaction verifies bank, income, exposure and audit originals."""
    if (
        session.new
        or session.dirty
        or session.deleted
        or session.connection().get_isolation_level() != "REPEATABLE READ"
        or session.scalar(text("SHOW transaction_read_only")) != "on"
    ):
        raise PolicyLifecycleError(
            "INVALID_READ_SNAPSHOT", "年度规划必须在无待写入状态的只读可重复读事务中读取", 409
        )
    with session.no_autoflush:
        context, _, _ = load_verified_financial_context(session, user_id, now)
        audit = current_epoch_audit(session, user_id, [])
        try:
            return project_verified_context(context, audit)
        except (TypeError, ValueError, OverflowError) as error:
            raise PolicyLifecycleError(
                "INVALID_PROJECTION_INPUT", "年度规划输入不一致或日期超出支持范围", 409
            ) from error
