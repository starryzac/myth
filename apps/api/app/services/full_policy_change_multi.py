"""Same RR/RO actual-source v3 previews; no candidate versions or permissions are written."""

from datetime import datetime, time
from typing import Any, Literal, cast
from uuid import UUID
from zoneinfo import ZoneInfo

from app.db.models import ActionPlan, EvidenceItem, Goal, PolicyVersion, User
from app.domain.asset_exposure import AssetExposure
from app.domain.boundary_types import BoundaryModel
from app.domain.full_asset_allocation import FullAssetPlanningInput
from app.domain.full_joint_goal_planning import bind_full_joint_input, captured_references
from app.domain.full_policy_change_multi import (
    MultiTemplateChangeInput,
    MultiTemplateFinancialImpact,
    SourceKind,
    derive_multi_template_impact,
)
from app.domain.full_policy_configuration import (
    MVP_TYPE_MAPPING,
    AssetAuthorizationPolicy,
    RecoveryPolicy,
    TemplateName,
)
from app.domain.full_recovery_planning import FullRecoveryPlanningInput
from app.domain.multi_goal_allocation import MultiGoalAllocationInput, SourceReference
from app.domain.policy_change_types import LivingReserveChangeEstimate
from app.domain.policy_configuration import (
    UUIDReference,
    configuration_hash,
    validate_configuration,
)
from app.services.asset_allocation import _goal_projection_matches
from app.services.audit_chain import audit_read_scope, current_audit_epoch, row_copy
from app.services.dashboard_helpers import current_epoch_audit
from app.services.financial_read import finalize_financial_context, load_verified_financial_context
from app.services.full_policy_lifecycle import (
    _binding,
    _full_window,
    _read_snapshot,
    _references,
    canonical_candidate,
    changed_fields,
    read_full_policy,
)
from app.services.full_protection_projection import compute_full_annual_protection
from app.services.full_recovery_planning import _holdings, _linked_policy
from app.services.living_reserve import estimate_living_reserve
from app.services.multi_goal_planning import joint_goal_planning
from app.services.policy_lifecycle import (
    PolicyLifecycleError,
    _evidence,
    _goal_asset_reference,
    _now,
    _window,
    is_version_authorized,
)
from app.services.policy_preview import _actual_goal_month, _source
from app.services.product_catalog import read_catalog, verified_catalog_products
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session


class MultiTemplatePreviewRequest(BoundaryModel):
    expected_version_id: UUIDReference
    expected_epoch_id: UUIDReference
    configuration: dict[str, Any]


class MultiTemplatePreviewResponse(BoundaryModel):
    protocol: Literal["full-policy-multi-template-change-v3"] = (
        "full-policy-multi-template-change-v3"
    )
    simulation: Literal[True] = True
    hypothetical: Literal[True] = True
    preview_only: Literal[True] = True
    grants_authority: Literal[False] = False
    bank_authority: Literal[False] = False
    writes_policy_or_bank: Literal[False] = False
    user_id: UUID
    epoch_id: UUID
    source_kind: SourceKind
    policy_id: UUID
    expected_version_id: UUID
    template_name: TemplateName
    as_of: datetime
    current_configuration_hash: str
    candidate_configuration_hash: str
    before_configuration: dict[str, Any]
    after_configuration: dict[str, Any]
    changed_fields: list[str]
    current_fact_digest: str
    source_counts: dict[str, int]
    source_evidence_ids: list[UUID]
    source_originals: dict[str, Any]
    financial_impact: MultiTemplateFinancialImpact
    original_action_ids: list[UUID]
    original_position_ids: list[UUID]
    limitations: list[str] = Field(default_factory=list)


def _mvp_history(session: Session, user_id: UUID, policy_id: UUID, now: datetime) -> dict[str, Any]:
    """Verify every original configuration link and its exact retained confirmation evidence."""
    rows = list(
        session.scalars(
            select(PolicyVersion)
            .where(
                PolicyVersion.user_id == user_id,
                PolicyVersion.policy_id == policy_id,
            )
            .order_by(PolicyVersion.version_number)
        )
    )
    if not rows or len(rows) > 1000:
        raise ValueError("MVP_HISTORY_COMPLETE_DENOMINATOR_NOT_SUPPORTED")
    confirmations: list[dict[str, Any]] = []
    previous = None
    for number, row in enumerate(rows, 1):
        canonical = validate_configuration(row.configuration)
        confirmation = row.confirmation
        if (
            row.version_number != number
            or row.previous_hash != previous
            or (
                canonical != row.configuration
                or configuration_hash(canonical) != row.content_hash
                or row.created_at > now
                or row.confirmed_at is None
                or row.confirmed_at > now
                or confirmation.get("accepted") is not True
                or confirmation.get("reviewed_hash") != row.content_hash
                or confirmation.get("user_id") != str(user_id)
                or confirmation.get("policy_id") != str(policy_id)
                or confirmation.get("version_id") != str(row.id)
                or confirmation.get("confirmed_at") != row.confirmed_at.isoformat()
            )
        ):
            raise ValueError("MVP_ORIGINAL_VERSION_CHAIN_OR_CONFIRMATION_MISMATCH")
        proofs = list(
            session.scalars(
                select(EvidenceItem).where(
                    EvidenceItem.user_id == user_id,
                    EvidenceItem.id.in_([UUID(v) for v in row.evidence_ids]),
                )
            )
        )
        if len({p.id for p in proofs}) != len(set(row.evidence_ids)):
            raise ValueError("MVP_ORIGINAL_HISTORY_EVIDENCE_MISSING")
        for proof in proofs:
            if configuration_hash(proof.content) != proof.content_hash or proof.observed_at > now:
                raise ValueError("MVP_ORIGINAL_HISTORY_EVIDENCE_HASH_OR_KNOWN_TIME_MISMATCH")
        matches = [
            p
            for p in proofs
            if p.source_type == "POLICY_CONFIRMATION"
            and p.evidence_level == "USER_CONFIRMED_POLICY"
            and p.source_ref == str(row.id)
            and p.content == confirmation
            and p.status == "VALID"
            and p.observed_at <= row.confirmed_at
            and p.valid_from <= row.confirmed_at
            and (p.valid_to is None or row.confirmed_at < p.valid_to)
        ]
        if len(matches) != 1:
            raise ValueError("MVP_EXACT_ORIGINAL_CONFIRMATION_NOT_PROVEN")
        confirmations.extend(row_copy(p) for p in proofs)
        previous = row.content_hash
    return {"versions": [row_copy(row) for row in rows], "evidence": confirmations}


def _scope_exposure(
    rows: list[AssetExposure], config: AssetAuthorizationPolicy, now: datetime
) -> AssetExposure:
    matches = [row for row in rows if row.scope == config.scope and row.goal_id == config.goal_id]
    if len(matches) > 1:
        raise ValueError("EXPOSURE_SCOPE_NOT_UNIQUE")
    if matches:
        return matches[0]
    cash: dict[UUID, int] = {}
    goals: dict[UUID, int] = {}
    for row in rows:
        for target, source in (
            (cash, row.reserved_cash_by_account),
            (goals, row.reserved_goal_cash_by_goal),
        ):
            for key, value in source.items():
                if key in target and target[key] != value:
                    raise ValueError("GLOBAL_EXPOSURE_RESERVATION_MAP_CONFLICT")
                target[key] = value
    return AssetExposure(
        as_of=now,
        scope=config.scope,
        goal_id=config.goal_id,
        managed_principal_cents=0,
        pending_purchase_cents=0,
        reserved_cash_by_account=cash,
        reserved_goal_cash_by_goal=goals,
        excluded_manual_position_ids=sorted(
            {key for r in rows for key in r.excluded_manual_position_ids}
        ),
        evidence_ids=sorted({key for r in rows for key in r.evidence_ids}),
    )


def preview_multi_template_financial_change(
    session: Session,
    user_id: UUID,
    source_kind: SourceKind,
    policy_id: UUID,
    body: MultiTemplatePreviewRequest,
    now: datetime,
) -> MultiTemplatePreviewResponse:
    """An actual immutable request, not a persistent auth cache or candidate authority."""
    _read_snapshot(session)
    now = _now(now)
    with session.no_autoflush, audit_read_scope(session):
        epoch = current_audit_epoch(session, user_id)
        user = session.get(User, user_id)
        if (
            user is None
            or not user.is_simulated
            or epoch is None
            or epoch.status != "OPEN"
            or (epoch.id != body.expected_epoch_id or epoch.opened_at > now)
        ):
            raise PolicyLifecycleError(
                "CURRENT_OWNER_EPOCH_REQUIRED", "需要当前模拟用户及OPEN周期", 409
            )
        originals: dict[str, Any] = {"user": row_copy(user), "epoch": row_copy(epoch)}
        issues: list[str] = []
        mvp, mvp_status, full = None, None, None
        if source_kind == "MVP_POLICY":
            _, policy, mvp, status = _source(
                session, user_id, policy_id, body.expected_version_id, now
            )
            mvp_status = cast(Literal["ACTIVE", "CONFIRMED", "SUSPENDED"], status)
            template = next(
                name for name, tag in MVP_TYPE_MAPPING.items() if tag == policy.policy_type
            )
            try:
                canonical = validate_configuration(body.configuration)
            except (TypeError, ValueError) as error:
                raise PolicyLifecycleError(
                    "INVALID_CONFIGURATION", "候选策略配置不完整或非法", 422
                ) from error
            if canonical["type"] != policy.policy_type:
                raise PolicyLifecycleError("POLICY_TYPE_CHANGE", "候选不能改变原策略类型", 409)
            _goal_asset_reference(session, user_id, canonical)
            start, end = _window(user, canonical, now)
            before = mvp.configuration
            originals["policy"] = row_copy(policy)
            try:
                originals["mvp_history"] = _mvp_history(session, user_id, policy_id, now)
            except (ValueError, TypeError, KeyError) as error:
                issues.append(str(error))
        else:
            full = read_full_policy(session, user_id, policy_id, now)
            if (
                full.epoch_id != epoch.id
                or full.current_version.version_id != body.expected_version_id
            ):
                raise PolicyLifecycleError("STALE_POLICY_VERSION", "原周期或当前版本已变化", 409)
            if full.effective_status not in {"ACTIVE", "CONFIRMED"}:
                raise PolicyLifecycleError("INVALID_POLICY_STATE", "需要当前有效的完整声明", 409)
            template = full.template_name
            canonical = canonical_candidate(template, body.configuration)
            start, end = _full_window(user, canonical, now)
            refs, _ = _references(session, user_id, canonical, now)
            policy_full, versions, commands, live = _binding(session, user_id, policy_id, now)
            originals["full_history"] = {
                "policy": row_copy(policy_full),
                "versions": [row_copy(row) for row in versions],
                "commands": [row_copy(row) for row in commands],
                "candidate_references": refs,
            }
            if (
                not live
                or not full.planning_confirmation_valid
                or full.reference_validation != "CURRENT"
            ):
                issues.append("CURRENT_FULL_CONFIRMATION_OR_REFERENCES_NOT_PROVEN")
            if (
                template in {"AssetAuthorizationPolicy", "RecoveryPolicy", "GoalAllocationPolicy"}
                and full.effective_status != "ACTIVE"
            ):
                issues.append("CURRENT_PLANNING_TEMPLATE_NOT_ACTIVE_NOW")
            before = full.current_version.configuration
        context, bank_matched, exposures = load_verified_financial_context(session, user_id, now)
        audit = current_epoch_audit(session, user_id, [])
        context, financial_issues, _ = finalize_financial_context(context, audit)
        annual = compute_full_annual_protection(session, user_id, now)
        if not bank_matched or exposures is None or not audit.complete or audit.status != "VALID":
            issues.append("ACTUAL_BANK_EXPOSURE_OR_AUDIT_NOT_PROVEN")
        issues.extend(row.code + ":" + row.source_ref for row in financial_issues)
        issues.extend(row.code + ":" + row.source_ref for row in annual.source_issues)
        living = None
        if mvp is not None and canonical["type"] == "living_reserve":
            estimate = estimate_living_reserve(session, user_id, now, canonical)
            originals["candidate_living_estimate"] = estimate.model_dump(mode="json")
            ready = estimate.estimation.status == "READY" and not estimate.source_issues
            living = LivingReserveChangeEstimate(
                status="READY" if ready else "INSUFFICIENT_EVIDENCE",
                amount_cents=estimate.estimation.recommended_reserve_cents if ready else None,
                as_of=now,
                configuration_hash=configuration_hash(canonical),
                estimation_input_digest=estimate.input_digest,
            )
        if mvp is not None and canonical["type"] == "goal_saving":
            context = _actual_goal_month(session, context, policy_id)
        actions = list(
            session.scalars(
                select(ActionPlan).where(ActionPlan.user_id == user_id).order_by(ActionPlan.id)
            )
        )
        if len(actions) > 10000:
            issues.append("COMPLETE_ACTION_INVENTORY_CAPACITY_EXCEEDED")
        originals.update(
            {
                "annual": annual.model_dump(mode="json"),
                "boundary_snapshot": context.snapshot.model_dump(mode="json"),
                "boundary_versions": [r.model_dump(mode="json") for r in context.versions],
                "positions": [r.model_dump(mode="json") for r in context.positions],
                "products": [r.model_dump(mode="json") for r in context.products],
                "actions": [row_copy(r) for r in actions],
                "exposure": [r.model_dump(mode="json") for r in (exposures or [])],
                "evidence": [row_copy(r) for r in context.sources.evidence.values()],
            }
        )
        reservations: dict[UUID, int] = {}
        for exposure in exposures or []:
            for identity, amount in exposure.reserved_cash_by_account.items():
                if identity in reservations and reservations[identity] != amount:
                    issues.append("EXPOSURE_RESERVATION_CONFLICT")
                reservations[identity] = amount
        asset, recovery, joint = None, None, None
        if full is not None and full.template_name in {
            "AssetAuthorizationPolicy",
            "RecoveryPolicy",
        }:
            catalogue = read_catalog(session, now)
            originals["catalogue"] = catalogue.model_dump(mode="json")
            if catalogue.state != "REGISTERED" or not catalogue.complete_within_registered_capacity:
                issues.extend("CATALOGUE:" + str(v) for v in catalogue.issues)
            goal_id = before.get("goal_id")
            goal = session.get(Goal, UUID(goal_id)) if goal_id is not None else None
            goal_version = (
                session.scalar(
                    select(PolicyVersion)
                    .where(
                        PolicyVersion.user_id == user_id,
                        PolicyVersion.policy_id == goal.policy_id,
                    )
                    .order_by(PolicyVersion.version_number.desc())
                    .limit(1)
                )
                if goal is not None
                else None
            )
            goal_verified = bool(
                goal is not None
                and goal.user_id == user_id
                and goal_version is not None
                and goal.policy_version_id == goal_version.id
                and _goal_projection_matches(goal, goal_version)
                and is_version_authorized(session, user_id, goal_version.id, now)
            )
            if goal_id is not None and not goal_verified:
                issues.append("CURRENT_GOAL_REFERENCE_NOT_PROVEN")
            if goal is not None:
                originals["scope_goal"] = row_copy(goal)
            if full.template_name == "AssetAuthorizationPolicy" and exposures is not None:
                config = AssetAuthorizationPolicy.model_validate(before)
                immutable = verified_catalog_products(session, now)
                originals["verified_catalogue"] = immutable.model_dump(mode="json")
                if immutable.status != "VERIFIED" or len(immutable.products) > 100:
                    issues.append("COMPLETE_IMMUTABLE_PRODUCT_INVENTORY_NOT_PROVEN")
                else:
                    asset = FullAssetPlanningInput(
                        snapshot=context.snapshot.model_copy(update={"horizon_days": 365}),
                        boundary_versions=context.versions,
                        positions=context.positions,
                        boundary_products=context.products,
                        products=immutable.products,
                        exposure=_scope_exposure(exposures, config, now),
                        policy_id=policy_id,
                        policy_version_id=body.expected_version_id,
                        configuration=config,
                        planning_confirmation_valid=full.planning_confirmation_valid,
                        confirmed_at=full.current_version.confirmed_at,
                        valid_from=full.current_version.valid_from,
                        valid_until=full.current_version.valid_until,
                        goal_deadline=goal.deadline if goal_verified and goal else None,
                        goal_policy_version_id=goal_version.id
                        if goal_verified and goal_version
                        else None,
                        goal_reference_verified=goal_verified,
                    )
            elif full.template_name == "RecoveryPolicy":
                config_recovery = RecoveryPolicy.model_validate(before)
                linked = _linked_policy(session, context, config_recovery)
                holdings, bindings = _holdings(
                    session,
                    context,
                    catalogue,
                    {key for r in (exposures or []) for key in r.excluded_manual_position_ids},
                )
                originals["holding_bindings"] = [r.model_dump(mode="json") for r in bindings]
                issues.extend(row.code + ":" + row.source_ref for row in context.sources.issues)
                recovery = FullRecoveryPlanningInput(
                    user_id=user_id,
                    policy_id=policy_id,
                    policy_version_id=body.expected_version_id,
                    configuration=config_recovery,
                    planning_confirmation_valid=full.planning_confirmation_valid,
                    confirmed_at=full.current_version.confirmed_at,
                    valid_from=full.current_version.valid_from,
                    valid_until=full.current_version.valid_until,
                    linked_asset_policy=linked,
                    snapshot=context.snapshot.model_copy(update={"horizon_days": 365}),
                    boundary_versions=context.versions,
                    positions=context.positions,
                    boundary_products=context.products,
                    holdings=holdings,
                    goal_deadline_at=datetime.combine(
                        goal.deadline, time(), ZoneInfo(context.snapshot.timezone)
                    )
                    if goal_verified and goal
                    else None,
                    goal_reference_verified=goal_verified,
                )
        if full is not None and full.template_name == "GoalAllocationPolicy":
            captured: list[MultiGoalAllocationInput] = []
            original_joint = joint_goal_planning(
                session, user_id, now, capture_inputs=captured.append
            )
            originals["joint_original"] = original_joint.model_dump(mode="json")
            if (
                len(captured) != 1
                or original_joint.allocation is None
                or not original_joint.independent_bank_projection_matched
            ):
                issues.append("COMPLETE_CURRENT_JOINT_INPUT_NOT_CAPTURED")
            else:
                actual = captured[0]
                ids = set(annual.source_evidence_ids) | set(original_joint.source_evidence_ids)
                ids.update(r.evidence_id for r in captured_references(actual))
                evidence = _evidence(
                    session, user_id, [str(v) for v in sorted(ids)], now, lock=False
                )
                binding = bind_full_joint_input(
                    actual,
                    annual.projection,
                    [
                        SourceReference(
                            user_id=user_id, evidence_id=r.id, content_hash=r.content_hash
                        )
                        for r in evidence
                    ],
                    source_issues=issues,
                    seasonal_proof_sources=annual.full_policy_sources,
                )
                originals["joint_binding"] = binding.model_dump(mode="json")
                if binding.status == "VERIFIED":
                    joint = binding.candidate
                else:
                    issues.extend(binding.reasons)
        data = MultiTemplateChangeInput(
            user_id=user_id,
            epoch_id=epoch.id,
            policy_id=policy_id,
            version_id=body.expected_version_id,
            source_kind=source_kind,
            template_name=template,
            current_configuration=before,
            candidate_configuration=canonical,
            candidate_valid_from=start,
            candidate_valid_until=end,
            snapshot=context.snapshot,
            boundary_versions=context.versions,
            positions=context.positions,
            boundary_products=context.products,
            full_projection=annual.projection,
            full_sources=annual.full_policy_sources,
            reserved_cash_by_account=reservations,
            source_originals=originals,
            source_issues=sorted(set(issues)),
            mvp_source=mvp,
            mvp_source_status=mvp_status,
            living_estimate=living,
            asset_input=asset,
            recovery_input=recovery,
            joint_input=joint,
        )
        try:
            impact = derive_multi_template_impact(data)
        except (TypeError, ValueError, OverflowError) as error:
            raise PolicyLifecycleError(
                "INVALID_MULTI_TEMPLATE_PREVIEW", "原金融截面或候选不一致", 409
            ) from error
        counts = {
            "actions": len(actions),
            "positions": len(context.positions),
            "boundary_products": len(context.products),
            "boundary_versions": len(context.versions),
            "full_protection_sources": len(annual.full_policy_sources),
            "evidence": len(context.sources.evidence),
            "goals": len(context.snapshot.goals),
            "asset_catalogue": len(asset.products) if asset else 0,
            "recovery_holdings": len(recovery.holdings) if recovery else 0,
            "joint_goals": len(joint.goals) if joint else 0,
        }
        return MultiTemplatePreviewResponse(
            user_id=user_id,
            epoch_id=epoch.id,
            source_kind=source_kind,
            policy_id=policy_id,
            expected_version_id=body.expected_version_id,
            template_name=template,
            as_of=now,
            current_configuration_hash=configuration_hash(before),
            candidate_configuration_hash=configuration_hash(canonical),
            before_configuration=before,
            after_configuration=canonical,
            changed_fields=changed_fields(before, canonical),
            current_fact_digest=configuration_hash(
                {
                    "protocol": "full-change-current-facts-v3",
                    "user_id": str(user_id),
                    "epoch_id": str(epoch.id),
                    "as_of": now.isoformat(),
                    "originals": originals,
                }
            ),
            source_counts=counts,
            source_evidence_ids=sorted(set(context.sources.used) | set(annual.source_evidence_ids)),
            source_originals=originals,
            financial_impact=impact,
            original_action_ids=[r.id for r in actions],
            original_position_ids=[r.position_id for r in context.positions],
            limitations=[
                "FUTURE_INCOME_REGISTRATION_IS_NOT_A_13TH_POLICY_TEMPLATE_OR_CURRENT_CASH",
                "QUESTION_WORKFLOW_AND_INTERVENTION_POLICY_ARE_NOT_BANK_AUTHORITY",
                "OLD_V1_AND_FUTURE_DATED_HISTORY_V2_INPUTS_AND_HASHES_UNCHANGED",
                "NO_POLICY_CONFIRMATION_ACTION_RESET_OR_BANK_WRITE",
            ],
        )
