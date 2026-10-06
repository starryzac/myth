"""Read actual FULL commitments without rewriting MVP contexts or confirmation hashes."""

from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from app.db.models import Policy, PolicyVersion
from app.domain.boundary_types import BoundaryPoint
from app.domain.full_future_dated_history import REFERENCE_KIND
from app.domain.full_policy_configuration import SeasonalReservePolicy
from app.domain.full_protection_projection import (
    FullProtectedReference,
    FullProtectionPolicySource,
    FullProtectionProjectionInput,
    FullProtectionProjectionResult,
    ProtectionTemplate,
    project_full_protection,
)
from app.domain.full_seasonal_adoption import REFERENCE_KIND as SEASONAL_REFERENCE_KIND
from app.domain.full_seasonal_ended_adoption import REFERENCE_KIND as ENDED_SEASONAL_REFERENCE_KIND
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.services.boundary import BoundarySourceIssue
from app.services.dashboard_helpers import current_epoch_audit
from app.services.dashboard_types import DashboardAuditCard
from app.services.financial_read import finalize_financial_context, load_verified_financial_context
from app.services.full_future_dated_history import prove_current_future_dated_history
from app.services.full_payment_permissions import (
    PeriodicTransferProjectionBinding,
    verified_periodic_transfer_projection_binding,
)
from app.services.full_policy_lifecycle import (
    FullPolicyView,
    _read_snapshot,
    list_full_policies,
)
from app.services.full_projection import AnnualDailyCheckpoint, FutureIncomeProjection, _checkpoint
from app.services.full_seasonal_adoption import read_current_seasonal_adoption
from app.services.full_seasonal_ended_adoption import read_current_ended_seasonal_adoption
from app.services.policy_lifecycle import PolicyLifecycleError, _evidence, _now, effective_status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session


class FullAnnualProtectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["full-annual-protection-v1"] = "full-annual-protection-v1"
    user_id: UUID
    as_of: datetime
    simulation: Literal[True] = True
    planning_only: Literal[True] = True
    grants_authority: Literal[False] = False
    execution_support: Literal["NOT_IMPLEMENTED"] = "NOT_IMPLEMENTED"
    horizon_days: Literal[365] = 365
    projection: FullProtectionProjectionResult
    initial_checkpoint: AnnualDailyCheckpoint
    daily_checkpoints: list[AnnualDailyCheckpoint]
    full_policy_sources: list[FullProtectionPolicySource]
    future_income: FutureIncomeProjection
    source_evidence_ids: list[UUID]
    source_issues: list[BoundarySourceIssue]
    input_digest: str
    audit: DashboardAuditCard
    limitations: list[str]


def _protected_reference(
    session: Session,
    user_id: UUID,
    identity: UUID,
    now: datetime,
    full_views: dict[UUID, FullPolicyView],
    periodic_bindings: dict[UUID, PeriodicTransferProjectionBinding],
) -> FullProtectedReference:
    full = full_views.get(identity)
    if full is not None:
        current = full.current_version
        binding = periodic_bindings.get(identity)
        dedicated_current = binding is not None and binding.status == "VERIFIED_CURRENT_RELATION"
        return FullProtectedReference(
            policy_id=identity,
            version_id=current.version_id,
            kind="FULL_POLICY",
            content_hash=current.content_hash,
            current_confirmed=(full.planning_confirmation_valid or dedicated_current)
            and full.template_name
            in {
                "DatedExpensePolicy",
                "PeriodicTransferPolicy",
            },
            evidence_ids=sorted(
                {*(current.evidence_ids), *(binding.source_evidence_ids if binding else [])},
                key=str,
            ),
        )
    policy = session.scalar(select(Policy).where(Policy.user_id == user_id, Policy.id == identity))
    if policy is None:
        raise ValueError("A must-not-reduce original reference is absent")
    version = session.scalar(
        select(PolicyVersion)
        .where(
            PolicyVersion.user_id == user_id,
            PolicyVersion.policy_id == identity,
        )
        .order_by(PolicyVersion.version_number.desc())
        .limit(1)
    )
    if version is None or version.created_at > now:
        raise ValueError("The exact current protected version is not known")
    normalized = validate_configuration(version.configuration)
    if configuration_hash(normalized) != version.content_hash:
        raise ValueError("Protected current source hash differs")
    evidence = _evidence(session, user_id, version.evidence_ids, now, lock=False)
    confirmed = effective_status(policy, version, now) in {"ACTIVE", "CONFIRMED"} and any(
        row.evidence_level == "USER_CONFIRMED_POLICY"
        and row.source_type == "POLICY_CONFIRMATION"
        and row.source_ref == str(version.id)
        and row.content == version.confirmation
        and row.content.get("accepted") is True
        for row in evidence
    )
    return FullProtectedReference(
        policy_id=identity,
        version_id=version.id,
        kind="MVP_POLICY",
        content_hash=version.content_hash,
        current_confirmed=confirmed
        and normalized["type"]
        in {
            "recurring_obligation",
            "living_reserve",
            "emergency_buffer",
            "goal_saving",
        },
        evidence_ids=[row.id for row in evidence],
    )


def compute_full_annual_protection(
    session: Session,
    user_id: UUID,
    now: datetime,
) -> FullAnnualProtectionResponse:
    _read_snapshot(session)
    now = _now(now)
    with session.no_autoflush:
        context, _, exposures = load_verified_financial_context(session, user_id, now)
        audit = current_epoch_audit(session, user_id, [])
        if not audit.complete or audit.status != "VALID":
            context.sources.issue(
                "AUDIT_NOT_VERIFIED", str(audit.epoch_id), "完整年度预测需要当前有效的审计链"
            )
        # Seal this request's original financial digest before FULL-only sources are added.
        # Errors from FULL declarations must not change the original MVP hashes.
        context, financial_issues, financial_digest = finalize_financial_context(context, audit)
        full_issues: list[BoundarySourceIssue] = []
        policy_sources: list[FullProtectionPolicySource] = []
        source_ids = set(context.sources.used)
        inventory_complete = False
        try:
            originals = list_full_policies(session, user_id, now).items
            relevant: tuple[ProtectionTemplate, ...] = (
                "DatedExpensePolicy",
                "PeriodicTransferPolicy",
                "SeasonalReservePolicy",
            )
            selected = [row for row in originals if row.template_name in relevant]
            if len(selected) > 200:
                raise ValueError("FULL protection current-source capacity is 200")
            views = {row.policy_id: row for row in originals}
            # Fresh proof, local to this request. An original FULL declaration's
            # flags/hash remain untouched when newer BANK facts name the same
            # verified payee. Every other source drift keeps its original veto.
            periodic_bindings: dict[UUID, PeriodicTransferProjectionBinding] = {}
            for row in selected:
                if row.template_name == "PeriodicTransferPolicy" and (
                    not row.planning_confirmation_valid or row.reference_validation != "CURRENT"
                ):
                    observed_binding = verified_periodic_transfer_projection_binding(
                        session, user_id, row, now
                    )
                    if observed_binding.status == "VERIFIED_CURRENT_RELATION":
                        periodic_bindings[row.policy_id] = observed_binding
            references = {}
            for row in selected:
                if row.effective_status == "ARCHIVED":
                    continue
                for value in row.current_version.configuration.get(
                    "must_not_reduce_policy_ids", []
                ):
                    identity = UUID(value)
                    if identity not in references:
                        references[identity] = _protected_reference(
                            session, user_id, identity, now, views, periodic_bindings
                        )
            for row in selected:
                current = row.current_version
                binding = periodic_bindings.get(row.policy_id)
                evidence_ids = sorted(
                    {*current.evidence_ids, *(binding.source_evidence_ids if binding else [])},
                    key=str,
                )
                reference_snapshots = list(current.impact_analysis["reference_snapshots"])
                if row.template_name == "DatedExpensePolicy" and current.version_number > 1:
                    history = prove_current_future_dated_history(session, user_id, row, now)
                    if history.status == "VERIFIED_FUTURE_DATED_HISTORY":
                        reference_snapshots.append(
                            {"kind": REFERENCE_KIND, "proof": history.model_dump(mode="json")}
                        )
                        source_ids.update(
                            UUID(original["id"]) for original in history.evidence_originals
                        )
                if row.template_name == "SeasonalReservePolicy" and (
                    now.astimezone(ZoneInfo(context.snapshot.timezone)).date()
                    > SeasonalReservePolicy.model_validate(current.configuration).window.end
                ):
                    ended = read_current_ended_seasonal_adoption(
                        session, user_id, row.policy_id, now
                    )
                    if ended.status != "NO_ORIGINAL_ADOPTION":
                        reference_snapshots.append(
                            {
                                "kind": ENDED_SEASONAL_REFERENCE_KIND,
                                "proof": ended.model_dump(mode="json"),
                            }
                        )
                        if ended.inputs is not None:
                            source_ids.update(
                                UUID(original["id"])
                                for record in ended.inputs.records
                                for original in record.current_evidence_originals
                            )
                elif row.template_name == "SeasonalReservePolicy" and row.effective_status in {
                    "ACTIVE",
                    "CONFIRMED",
                }:
                    adoption = read_current_seasonal_adoption(session, user_id, row.policy_id, now)
                    if adoption.status != "ADVICE_ONLY":
                        reference_snapshots.append(
                            {
                                "kind": SEASONAL_REFERENCE_KIND,
                                "proof": adoption.model_dump(mode="json"),
                            }
                        )
                        if adoption.evidence_id is not None:
                            source_ids.add(adoption.evidence_id)
                        if adoption.current_scope is not None:
                            source_ids.update(
                                UUID(original["id"])
                                for original in adoption.current_scope.source_evidence_originals
                            )
                if binding is not None:
                    reference_snapshots.append(
                        {
                            "kind": "VERIFIED_CURRENT_PERIODIC_RELATION",
                            "original_full_planning_confirmation_valid": (
                                row.planning_confirmation_valid
                            ),
                            "original_full_reference_validation": row.reference_validation,
                            "proof": binding.model_dump(mode="json"),
                        }
                    )
                name = next(value for value in relevant if value == row.template_name)
                protected = (
                    [
                        references[UUID(value)]
                        for value in current.configuration.get("must_not_reduce_policy_ids", [])
                    ]
                    if row.effective_status != "ARCHIVED"
                    else []
                )
                if row.effective_status != "ARCHIVED":
                    source_ids.update(evidence_ids)
                    source_ids.update(
                        identity for ref in protected for identity in ref.evidence_ids
                    )
                policy_sources.append(
                    FullProtectionPolicySource(
                        policy_id=row.policy_id,
                        version_id=current.version_id,
                        template_name=name,
                        version_number=current.version_number,
                        confirmation=current.confirmation,
                        reference_snapshots=reference_snapshots,
                        configuration=current.configuration,
                        content_hash=current.content_hash,
                        confirmed_at=current.confirmed_at,
                        valid_from=current.valid_from,
                        valid_until=current.valid_until,
                        effective_status=row.effective_status,
                        planning_confirmation_valid=row.planning_confirmation_valid
                        or binding is not None,
                        references_current=row.reference_validation == "CURRENT"
                        or binding is not None,
                        evidence_ids=evidence_ids,
                        protected_references=protected,
                    )
                )
            inventory_complete = True
        except (PolicyLifecycleError, ValueError, TypeError, KeyError) as error:
            full_issues.append(
                BoundarySourceIssue(
                    code="FULL_PROTECTION_ORIGINALS_NOT_PROVEN",
                    source_ref="full_policy_inventory",
                    message=str(error),
                )
            )
        reservations = {
            key: amount
            for exposure in (exposures or [])
            for key, amount in exposure.reserved_cash_by_account.items()
        }
        try:
            projection = project_full_protection(
                FullProtectionProjectionInput(
                    snapshot=context.snapshot,
                    boundary_versions=context.versions,
                    positions=context.positions,
                    boundary_products=context.products,
                    policies=policy_sources,
                    reserved_cash_by_account=reservations,
                    full_source_inventory_complete=inventory_complete,
                    full_source_issues=[row.code + ":" + row.source_ref for row in full_issues],
                )
            )
        except (TypeError, ValueError, OverflowError) as error:
            raise PolicyLifecycleError(
                "INVALID_FULL_PROTECTION_INPUT", "年度完整保护原件或日期不一致", 409
            ) from error
        points = (
            projection.full_annual_projection.calculation_trace
            if projection.full_annual_projection
            else []
        )
        grouped: dict[int, list[BoundaryPoint]] = {}
        for point in points:
            grouped.setdefault(point.day, []).append(point)
        first = now.astimezone(ZoneInfo(context.snapshot.timezone)).date()
        issues = sorted(financial_issues + full_issues, key=lambda row: (row.code, row.source_ref))
        return FullAnnualProtectionResponse(
            user_id=user_id,
            as_of=now,
            projection=projection,
            initial_checkpoint=_checkpoint(0, first, grouped.get(0, [])),
            daily_checkpoints=[
                _checkpoint(day, first + timedelta(days=day), grouped.get(day, []))
                for day in range(1, 366)
            ],
            full_policy_sources=policy_sources,
            future_income=FutureIncomeProjection(),
            source_evidence_ids=sorted(source_ids),
            source_issues=issues,
            audit=audit,
            input_digest=configuration_hash(
                {
                    "original_financial_digest": financial_digest,
                    "full_input_hash": projection.input_hash,
                }
            ),
            limitations=[
                "CONSERVATIVE_MAX_IS_NOT_ACTUAL_INVOICE_OR_BANK_PAYMENT",
                "FULL_SETTLEMENT_BINDING_AND_OLD_EXPIRED_UNPAID_HISTORY_NOT_IMPLEMENTED",
                "CURRENT_SOURCE_ACCOUNT_CHECK_DOES_NOT_ALLOCATE_ALL_FUTURE_MVP_DEBITS",
                *(
                    ["SEASONAL_ORIGINAL_ADOPTION_IS_A_PROTECTION_FLOOR_NOT_PAYMENT"]
                    if projection.algorithm_version
                    in {
                        "registered-full-protection-adopted-seasonal-v3",
                        "registered-full-protection-ended-seasonal-v4",
                    }
                    else ["SEASONAL_ADOPTED_EXTRA_AMOUNT_NOT_PROVIDED"]
                ),
                "ORIGINAL_EXECUTION_CONSUMERS_HAVE_NOT_ADOPTED_THIS_NEW_FULL_CURVE",
            ],
        )
