"""Shared snapshot-only financial verification for dashboards and explicit assumptions."""

from dataclasses import replace
from datetime import datetime
from uuid import UUID

from app.domain.asset_exposure import AssetExposure
from app.domain.boundary_details_types import BoundaryDisplayDetails
from app.domain.boundary_types import BoundaryResult, SourceIssue
from app.domain.policy_configuration import configuration_hash
from app.services.asset_exposure_import import load_all_asset_exposure
from app.services.boundary import BoundaryContext, BoundarySourceIssue, load_boundary_context
from app.services.dashboard_types import DashboardAuditCard, FinancialBoundaryCard
from app.services.income_ledger import read_income_state
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.simulated_bank import validate_bank_projection
from sqlalchemy.orm import Session


def load_verified_financial_context(
    session: Session,
    user_id: UUID,
    now: datetime,
) -> tuple[BoundaryContext, bool, list[AssetExposure] | None]:
    context = load_boundary_context(session, user_id, now)
    bank_matched = True
    try:
        validate_bank_projection(session, user_id, now)
    except PolicyLifecycleError as error:
        bank_matched = False
        context.sources.issue(error.code, "independent_bank", error.message)
    exposure = load_all_asset_exposure(session, context)
    if any(
        item.source_type == "SIMULATED_NEW_FUNDS_LEDGER" and item.status != "SUPERSEDED"
        for item in context.sources.evidence.values()
    ):
        try:
            read_income_state(session, user_id, now)
        except PolicyLifecycleError as error:
            context.sources.issue(error.code, "income_ledger", error.message)
    return context, bank_matched, exposure


def finalize_financial_context(
    context: BoundaryContext,
    audit: DashboardAuditCard,
) -> tuple[BoundaryContext, list[BoundarySourceIssue], str]:
    if audit.status == "INTEGRITY_ERROR" and not any(
        issue.code == "AUDIT_INTEGRITY_ERROR" and issue.source_ref == str(audit.epoch_id)
        for issue in context.sources.issues
    ):
        context.sources.issue(
            "AUDIT_INTEGRITY_ERROR",
            str(audit.epoch_id),
            "当前审计链完整性失效",
        )
    issues = sorted(context.sources.issues, key=lambda item: (item.code, item.source_ref))
    digest = configuration_hash(
        {
            "user_id": str(context.sources.user_id),
            "as_of": context.snapshot.as_of.isoformat(),
            "sources": [
                {"id": str(key), "hash": context.sources.evidence[key].content_hash}
                for key in sorted(context.sources.used)
            ],
            "issues": [item.model_dump() for item in issues],
        }
    )
    snapshot = context.snapshot.model_copy(
        update={
            "source_digest": digest,
            "source_issues": [
                SourceIssue(
                    code=i.code,
                    entity_type="source",
                    entity_id=i.source_ref,
                )
                for i in issues
            ],
        }
    )
    return replace(context, snapshot=snapshot), issues, digest


def financial_card(
    boundary: BoundaryResult,
    details: BoundaryDisplayDetails,
    issues: list[BoundarySourceIssue],
    input_digest: str,
) -> FinancialBoundaryCard:
    proven = boundary.status != "INSUFFICIENT_EVIDENCE"
    protection = details.current_protection.value
    constraining = min(
        boundary.calculation_trace, key=lambda point: point.margin_cents, default=None
    )
    return FinancialBoundaryCard(
        state="PROVEN" if proven else "NOT_PROVEN",
        status=boundary.status,
        safe_idle_cents=boundary.safe_idle_cents,
        minimum_margin_cents=boundary.minimum_margin_cents,
        deficit_cents=boundary.deficit_cents,
        protected_cents_by_reason=boundary.protected_cents_by_reason if proven else None,
        current_protected_cents=protection.total_cents if protection else None,
        current_protected_cents_by_reason=protection.amounts_by_reason if protection else None,
        current_margin_cents=protection.margin_cents if protection else None,
        constraining_date=constraining.date if constraining else None,
        window_start=details.window_start,
        window_end=details.window_end,
        input_digest=input_digest,
        boundary_hash=boundary.boundary_hash,
        blocking_constraints=boundary.blocking_constraints,
        calculation_notes=boundary.calculation_notes,
        issues=issues,
    )
