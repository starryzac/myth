"""Read actual finite whole-asset producers; never prepare or confirm a portfolio."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.db.models import Account, Goal, Policy, PolicyVersion
from app.domain.asset_exposure import AssetExposure
from app.domain.execution_types import ExecutionContext
from app.domain.full_action_set_asset_producers import (
    MAX_PRODUCERS,
    AssetFamilyInput,
    AssetFamilyResult,
    AssetProducerInput,
    derive_asset_family,
    expected_asset_producers,
)
from app.domain.full_asset_allocation import FullAssetPlanningInput
from app.domain.full_asset_execution import (
    FullAssetCatalogueReference,
    FullAssetExecutionBasis,
    FullAssetPrepareRequest,
)
from app.domain.full_policy_configuration import AssetAuthorizationPolicy
from app.services.asset_allocation import _goal_projection_matches
from app.services.autonomy import _authority
from app.services.autonomy_envelope import _snapshot
from app.services.dashboard_helpers import current_epoch_audit
from app.services.decision_recording import (
    CAPTURE_KEY,
    DecisionCapture,
    capture_evidence,
    start_capture,
)
from app.services.financial_read import finalize_financial_context, load_verified_financial_context
from app.services.full_action_set_boundary import ActionSetCapture, capture_current_action_set
from app.services.full_asset_execution import (
    FullAssetExecutionPreview,
    preview_full_asset_execution,
)
from app.services.full_policy_lifecycle import FullPolicyView, read_full_policy
from app.services.policy_lifecycle import PolicyLifecycleError, _now, is_version_authorized
from sqlalchemy import select
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class AssetFamilyCapture:
    inputs: AssetFamilyInput
    result: AssetFamilyResult
    originals: DecisionCapture


def _planning_input(
    session: Session,
    user_id: UUID,
    original: FullPolicyView,
    preview: FullAssetExecutionPreview,
    now: datetime,
) -> FullAssetPlanningInput:
    """Assemble the same actual producer contract; reuse the original optimizer.

    The actual public planning result must later equal its complete pure replay.
    No client facts or hand-composed financial evaluator enters this assembly.
    """
    config = AssetAuthorizationPolicy.model_validate(original.current_version.configuration)
    context, matched, exposures = load_verified_financial_context(session, user_id, now)
    if not matched or exposures is None or context.sources.issues:
        raise ValueError("ACTUAL_WHOLE_ASSET_FINANCIAL_SOURCE_NOT_VERIFIED")
    context.sources.used.update(original.current_version.evidence_ids)
    exposure = next((row for row in exposures if row.goal_id == config.goal_id), None)
    if exposure is None:
        exposure = AssetExposure(
            as_of=context.snapshot.as_of,
            scope=config.scope,
            goal_id=config.goal_id,
            managed_principal_cents=0,
            pending_purchase_cents=0,
            reserved_cash_by_account={
                key: amount
                for row in exposures
                for key, amount in row.reserved_cash_by_account.items()
            },
            reserved_goal_cash_by_goal={
                key: amount
                for row in exposures
                for key, amount in row.reserved_goal_cash_by_goal.items()
            },
            excluded_manual_position_ids=sorted(
                {key for row in exposures for key in row.excluded_manual_position_ids}
            ),
            evidence_ids=sorted({key for row in exposures for key in row.evidence_ids}),
        )
    goal_deadline, goal_version_id, goal_verified = None, None, False
    if config.goal_id is not None:
        goal = session.get(Goal, config.goal_id)
        policy = session.get(Policy, goal.policy_id) if goal is not None else None
        version = (
            session.scalar(
                select(PolicyVersion)
                .where(PolicyVersion.user_id == user_id, PolicyVersion.policy_id == goal.policy_id)
                .order_by(PolicyVersion.version_number.desc())
                .limit(1)
            )
            if goal is not None
            else None
        )
        goal_verified = bool(
            goal is not None
            and goal.user_id == user_id
            and policy is not None
            and policy.user_id == user_id
            and version is not None
            and goal.policy_version_id == version.id
            and _goal_projection_matches(goal, version)
            and is_version_authorized(session, user_id, version.id, now)
        )
        if goal_verified and goal is not None and version is not None:
            goal_deadline, goal_version_id = goal.deadline, version.id
            context.sources.used.update(UUID(key) for key in version.evidence_ids)
    audit = current_epoch_audit(session, user_id, [])
    if not audit.complete or audit.status != "VALID":
        raise ValueError("ACTUAL_WHOLE_ASSET_CURRENT_AUDIT_NOT_VERIFIED")
    context, issues, _ = finalize_financial_context(context, audit)
    if issues:
        raise ValueError("ACTUAL_WHOLE_ASSET_FINANCIAL_SOURCE_NOT_VERIFIED")
    return FullAssetPlanningInput(
        snapshot=context.snapshot.model_copy(update={"horizon_days": 365}),
        boundary_versions=context.versions,
        positions=context.positions,
        boundary_products=context.products,
        products=preview.original_full_planning.catalogue.products,
        exposure=exposure,
        policy_id=original.policy_id,
        policy_version_id=original.current_version.version_id,
        configuration=config,
        planning_confirmation_valid=original.planning_confirmation_valid,
        confirmed_at=original.current_version.confirmed_at,
        valid_from=original.current_version.valid_from,
        valid_until=original.current_version.valid_until,
        goal_deadline=goal_deadline,
        goal_policy_version_id=goal_version_id,
        goal_reference_verified=goal_verified,
        options=preview.original_full_planning.planning_constraints,
    )


def capture_asset_family(
    session: Session, user_id: UUID, now: datetime, base: ActionSetCapture | None = None
) -> AssetFamilyCapture:
    _snapshot(session)
    now = _now(now)
    base = base or capture_current_action_set(session, user_id, now)
    if (base.inputs.user_id, base.inputs.as_of) != (user_id, now):
        raise PolicyLifecycleError(
            "WHOLE_ASSET_BASE_OWNER_CLOCK_DIFFERS", "原请求用户/时点不同", 409
        )
    previous = session.info.get(CAPTURE_KEY)
    capture = start_capture(session)
    producers: list[AssetProducerInput] = []
    missing_reasons: list[str] = []
    try:
        with session.no_autoflush:
            try:
                expected = expected_asset_producers(base.inputs)
            except (ValueError, TypeError, KeyError) as error:
                expected = {}
                missing_reasons.append("ORIGINAL_WHOLE_ASSET_DENOMINATOR_INVALID:" + str(error))
            for key, (full_id, mvp_id, mode) in expected.items():
                original = None
                body = None
                preview = None
                planning_input = None
                basis = None
                authority = None
                try:
                    if len(expected) > MAX_PRODUCERS:
                        raise ValueError("WHOLE_ASSET_PRODUCER_CAPACITY_EXCEEDED")
                    original = read_full_policy(session, user_id, full_id, now)
                    if mvp_id is None or mode is None:
                        raise ValueError("CURRENT_ORIGINAL_MVP_ASSET_SCOPE_MISSING")
                    version = session.scalar(
                        select(PolicyVersion)
                        .where(PolicyVersion.user_id == user_id, PolicyVersion.policy_id == mvp_id)
                        .order_by(PolicyVersion.version_number.desc())
                        .limit(1)
                    )
                    assert version is not None
                    config = AssetAuthorizationPolicy.model_validate(
                        original.current_version.configuration
                    )
                    goal = session.get(Goal, config.goal_id) if config.goal_id is not None else None
                    if config.goal_id is not None and (goal is None or goal.user_id != user_id):
                        raise ValueError("CURRENT_ORIGINAL_ASSET_GOAL_MISSING")
                    body = FullAssetPrepareRequest(
                        full_policy_id=full_id,
                        expected_full_policy_version_id=original.current_version.version_id,
                        mvp_asset_policy_id=mvp_id,
                        expected_mvp_policy_version_id=version.id,
                        goal_id=config.goal_id,
                        expected_goal_policy_version_id=goal.policy_version_id if goal else None,
                        expected_epoch_id=base.inputs.epoch_id,
                        idempotency_key=f"readonly-global-asset:{full_id}:{mvp_id}:{mode}",
                        planning_mode="FIXED_LADDER" if mode == "FIXED_LADDER" else "PORTFOLIO",
                    )
                    # The public production preview runs the genuine whole planner.
                    # This call has no persistent parent, consent, Action or bank side effect.
                    preview = preview_full_asset_execution(session, user_id, body, now)
                    planning_input = _planning_input(session, user_id, original, preview, now)
                    ids = [version.policy_id] + ([goal.policy_id] if goal else [])
                    expected_versions = [version.id] + ([goal.policy_version_id] if goal else [])
                    authority = _authority(session, user_id, ids, now, expected=expected_versions)
                    portfolio = preview.portfolio
                    if portfolio is not None:
                        allocation = preview.original_full_planning.allocation
                        if allocation is None:
                            raise ValueError("ACTUAL_ORIGINAL_FULL_ASSET_ALLOCATION_MISSING")
                        from app.services.execution_context import load_execution_context

                        context: ExecutionContext = load_execution_context(
                            session, user_id, portfolio.batches[0].command.effect, now
                        )
                        accounts = list(
                            session.scalars(
                                select(Account)
                                .where(Account.user_id == user_id, Account.currency == "CNY")
                                .order_by(Account.id)
                            )
                        )
                        position_accounts: dict[UUID, UUID] = {}
                        for product in preview.original_full_planning.catalogue.products:
                            kind = (
                                "FIXED_DEPOSIT"
                                if product.asset_class == "FIXED_DEPOSIT"
                                else "CASH_MANAGEMENT"
                            )
                            account = next(
                                (row for row in accounts if row.account_type == kind), None
                            )
                            if account is not None:
                                position_accounts[product.product_id] = account.id
                        basis = FullAssetExecutionBasis(
                            user_id=user_id,
                            epoch_id=base.inputs.epoch_id,
                            as_of=now,
                            full_policy_configuration=config,
                            full_policy_content_hash=original.current_version.content_hash,
                            full_planning_confirmation_valid=original.planning_confirmation_valid,
                            original_planning_response_hash=preview.original_full_planning.input_hash,
                            planning=allocation,
                            context=context,
                            catalogue=[
                                FullAssetCatalogueReference.model_validate(row.model_dump())
                                for row in preview.original_full_planning.catalogue.bindings
                            ],
                            position_accounts=position_accounts,
                            batch_income_uses=[
                                batch.command.effect.income_uses for batch in portfolio.batches
                            ],
                            full_protection_sources=preview.original_full_protection.full_policy_sources,
                            full_protection_inventory_complete=preview.original_full_protection.projection.full_obligations_complete_within_registered_current_scope,
                            full_source_issues=preview.reasons,
                            source_evidence_ids=portfolio.source_evidence_ids,
                            expires_at=portfolio.expires_at,
                        )
                    capture_evidence(
                        session,
                        user_id,
                        sorted(
                            set(
                                preview.original_full_planning.source_evidence_ids
                                + preview.original_full_protection.source_evidence_ids
                                + authority.evidence_ids
                            )
                        ),
                    )
                    producers.append(
                        AssetProducerInput(
                            candidate_key=key,
                            full_policy_id=full_id,
                            request=body,
                            original_policy=original,
                            actual_preview=preview,
                            planning_input=planning_input,
                            execution_basis=basis,
                            authority=authority,
                        )
                    )
                except (PolicyLifecycleError, ValueError, TypeError, KeyError) as error:
                    producers.append(
                        AssetProducerInput(
                            candidate_key=key,
                            full_policy_id=full_id,
                            request=body,
                            original_policy=original,
                            actual_preview=preview,
                            planning_input=planning_input,
                            execution_basis=basis,
                            authority=authority,
                            missing_reasons=[getattr(error, "code", str(error))],
                        )
                    )
            inputs = AssetFamilyInput(
                base=base.inputs, producers=producers, missing_reasons=missing_reasons
            )
            return AssetFamilyCapture(inputs, derive_asset_family(inputs), capture)
    finally:
        if previous is None:
            session.info.pop(CAPTURE_KEY, None)
        else:
            session.info[CAPTURE_KEY] = previous
            if isinstance(previous, DecisionCapture):
                previous.sources.update(capture.sources)
                previous.policies.update(capture.policies)
