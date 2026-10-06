"""RR/RO recovery previews from actual holdings, original prices and confirmations."""

from datetime import datetime, time
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from app.db.catalog_models import ProductCatalogVersion
from app.db.models import AssetPosition, Goal, Policy, PolicyVersion
from app.domain.full_policy_configuration import RecoveryPolicy
from app.domain.full_recovery_planning import (
    FullRecoveryHolding,
    FullRecoveryPlanningInput,
    FullRecoveryPlanningResult,
    LinkedAssetPlanningPolicy,
    plan_full_recovery,
)
from app.domain.policy_configuration import configuration_hash
from app.domain.recovery_types import RecoveryPosition
from app.services.asset_allocation import _goal_projection_matches
from app.services.asset_exposure_import import _scope
from app.services.boundary import BoundaryContext, BoundarySourceIssue
from app.services.dashboard_helpers import current_epoch_audit
from app.services.execution_sources import execution_return_account, load_execution_quote
from app.services.financial_read import finalize_financial_context, load_verified_financial_context
from app.services.full_policy_lifecycle import _read_snapshot, read_full_policy
from app.services.policy_lifecycle import PolicyLifecycleError, _now, is_version_authorized
from app.services.product_catalog import (
    CatalogProductBinding,
    CatalogReadResponse,
    immutable_terms,
    read_catalog,
)
from app.services.recovery_sources import _version
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session


class FullRecoveryPlanningResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["verified-full-recovery-planning-v1"] = (
        "verified-full-recovery-planning-v1"
    )
    simulation: Literal[True] = True
    planning_only: Literal[True] = True
    bank_authority: Literal[False] = False
    execution_support: Literal["NOT_IMPLEMENTED"] = "NOT_IMPLEMENTED"
    protection_scope: Literal["ORIGINAL_VERIFIED_365_DAY_CURVE"] = "ORIGINAL_VERIFIED_365_DAY_CURVE"
    user_id: UUID
    policy_id: UUID
    as_of: datetime
    state: Literal["COMPUTED", "UNKNOWN"]
    plan: FullRecoveryPlanningResult | None
    catalogue: CatalogReadResponse
    catalogue_bindings: list[CatalogProductBinding]
    source_evidence_ids: list[UUID]
    source_issues: list[BoundarySourceIssue]
    input_hash: str
    limitations: list[str]


def _linked_policy(
    session: Session,
    context: BoundaryContext,
    config: RecoveryPolicy,
) -> LinkedAssetPlanningPolicy:
    user_id, now = context.sources.user_id, context.snapshot.as_of
    legacy = session.get(Policy, config.asset_policy_id)
    if legacy is None:
        full = read_full_policy(session, user_id, config.asset_policy_id, now)
        if full.template_name != "AssetAuthorizationPolicy":
            raise ValueError("Recovery must reference an actual asset policy")
        current = full.current_version
        context.sources.used.update(current.evidence_ids)
        return LinkedAssetPlanningPolicy(
            policy_id=full.policy_id,
            version_id=current.version_id,
            kind="FULL_POLICY",
            configuration=current.configuration,
            content_hash=current.content_hash,
            confirmation_valid=full.planning_confirmation_valid,
            confirmed_at=current.confirmed_at,
            valid_from=current.valid_from,
            valid_until=current.valid_until,
            evidence_ids=current.evidence_ids,
        )
    if legacy.user_id != user_id or legacy.policy_type != "asset_authorization":
        raise ValueError("The linked original policy does not belong to this user and role")
    current_legacy = session.scalar(
        select(PolicyVersion)
        .where(
            PolicyVersion.user_id == user_id,
            PolicyVersion.policy_id == legacy.id,
        )
        .order_by(PolicyVersion.version_number.desc())
        .limit(1)
    )
    if current_legacy is None or current_legacy.created_at > now:
        raise ValueError("Current linked original version is missing or not yet known")
    _scope(session, context, current_legacy.id)
    version = _version(current_legacy)
    return LinkedAssetPlanningPolicy(
        policy_id=legacy.id,
        version_id=version.version_id,
        kind="MVP_POLICY",
        configuration=version.configuration,
        content_hash=version.content_hash,
        confirmation_valid=is_version_authorized(session, user_id, version.version_id, now),
        confirmed_at=version.confirmed_at,
        valid_from=version.valid_from,
        valid_until=version.valid_until,
        evidence_ids=version.evidence_ids,
    )


def _holdings(
    session: Session,
    context: BoundaryContext,
    catalogue: CatalogReadResponse,
    manual_ids: set[UUID],
) -> tuple[list[FullRecoveryHolding], list[CatalogProductBinding]]:
    user_id, now = context.sources.user_id, context.snapshot.as_of
    originals = {row.product_id: row for row in catalogue.versions}
    rows = list(
        session.scalars(
            select(AssetPosition)
            .where(
                AssetPosition.user_id == user_id,
                AssetPosition.status != "REDEEMED",
            )
            .order_by(AssetPosition.id)
            .limit(101)
        )
    )
    if len(rows) > 100:
        context.sources.issue("FULL_RECOVERY_CAPACITY", "positions", "最多100项完整持仓候选")
        return [], []
    result: list[FullRecoveryHolding] = []
    bindings: dict[UUID, CatalogProductBinding] = {}
    for row in rows:
        try:
            view = originals.get(row.product_id)
            if view is None or not view.current_source_matched:
                raise ValueError(
                    "The held original product version has no matched immutable catalogue"
                )
            original_product = session.get(ProductCatalogVersion, view.id)
            if original_product is None:
                raise ValueError("The immutable original is absent")
            product = immutable_terms(original_product)
            proofs = context.sources.candidates("SIMULATED_BANK_POSITION", "position_id", row.id)
            if len(proofs) != 1 or not context.sources.valid(proofs[0], {}):
                raise ValueError("One complete known original bank position is required")
            proof = proofs[0]
            destination = execution_return_account(session, user_id, row, now)
            original_version = None
            acquisition: Literal["AUTHORIZED_PURCHASE", "MANUAL", "UNKNOWN"] = (
                "MANUAL" if row.id in manual_ids else "UNKNOWN"
            )
            if (
                proof.content.get("acquisition") == "synthetic_auto_purchase"
                and row.policy_version_id
            ):
                _scope(session, context, row.policy_version_id)
                legacy = session.get(PolicyVersion, row.policy_version_id)
                if legacy is None or legacy.user_id != user_id:
                    raise ValueError("Original acquisition authorization is absent")
                original_version, acquisition = _version(legacy), "AUTHORIZED_PURCHASE"
            quote, quote_issue = None, None
            quote_source: Literal["BANK_CONFIRMED", "DERIVED_ORIGINAL_TERMS", "MISSING"] = "MISSING"
            prices = context.sources.candidates("SIMULATED_REDEMPTION_QUOTE", "position_id", row.id)
            if row.status in {"HELD", "MATURED"}:
                try:
                    quote = load_execution_quote(session, user_id, row.id, now)
                    quote_source = (
                        "BANK_CONFIRMED" if quote.evidence_ids else "DERIVED_ORIGINAL_TERMS"
                    )
                    context.sources.used.update(quote.evidence_ids)
                    if quote.kind == "EARLY_WITHDRAW" and (
                        len(prices) != 1
                        or prices[0].content.get("price_protocol")
                        != "simulated-fixed-early-price-v1"
                        or prices[0].content.get("rounding") != "CEIL_CENT"
                    ):
                        raise ValueError("Issued original early price protocol or rounding differs")
                except PolicyLifecycleError as error:
                    if prices:
                        # An existing invalid/expired price is not replaced with a new derived one.
                        context.sources.used.update(price.id for price in prices)
                        context.sources.issue(error.code, row.id, error.message)
                    quote_issue = "ORIGINAL_PRICE_MISSING_OR_NOT_CURRENT"
            result.append(
                FullRecoveryHolding(
                    original=RecoveryPosition(
                        position_id=row.id,
                        account_id=row.account_id,
                        destination_account_id=destination,
                        goal_id=row.goal_id,
                        purchased_at=row.purchased_at,
                        acquisition=acquisition,
                        product=product,
                        original_authorization=original_version,
                        reserved_principal_cents=row.principal_cents
                        if row.status == "REDEEMING"
                        else 0,
                        quote=quote,
                        evidence_ids=[proof.id],
                    ),
                    catalogue_version_id=view.id,
                    product_record_hash=view.product_hash,
                    early_withdrawal_rule=view.original_product["early_withdrawal_rule"],
                    maturity_at=row.maturity_at,
                    quote_source=quote_source,
                    quote_issue=quote_issue,
                )
            )
            bindings[row.product_id] = CatalogProductBinding(
                product_id=row.product_id,
                catalogue_version_id=view.id,
                product_record_hash=view.product_hash,
                terms_digest=view.terms_digest,
            )
        except (PolicyLifecycleError, ValueError, KeyError, TypeError, OverflowError) as error:
            context.sources.issue("FULL_RECOVERY_POSITION_NOT_PROVEN", row.id, str(error))
    return result, [bindings[key] for key in sorted(bindings)]


def read_full_recovery_planning(
    session: Session,
    user_id: UUID,
    policy_id: UUID,
    now: datetime,
    planning_deadline_at: datetime | None = None,
) -> FullRecoveryPlanningResponse:
    _read_snapshot(session)
    now = _now(now)
    if planning_deadline_at is not None:
        planning_deadline_at = _now(planning_deadline_at)
    with session.no_autoflush:
        policy = read_full_policy(session, user_id, policy_id, now)
        if policy.template_name != "RecoveryPolicy":
            raise PolicyLifecycleError("INVALID_FULL_RECOVERY_POLICY", "需要实际完整恢复声明", 422)
        config = RecoveryPolicy.model_validate(policy.current_version.configuration)
        context, _, exposures = load_verified_financial_context(session, user_id, now)
        context.sources.used.update(policy.current_version.evidence_ids)
        catalogue = read_catalog(session, now)
        for issue in catalogue.issues:
            context.sources.issue("IMMUTABLE_CATALOGUE_NOT_PROVEN", issue, "原产品目录未证明")
        linked = None
        try:
            linked = _linked_policy(session, context, config)
        except (PolicyLifecycleError, ValueError, TypeError) as error:
            context.sources.issue(
                "CURRENT_RECOVERY_ASSET_POLICY_NOT_PROVEN", config.asset_policy_id, str(error)
            )
        manual_ids = {
            key for scope in (exposures or []) for key in scope.excluded_manual_position_ids
        }
        holdings, bindings = _holdings(session, context, catalogue, manual_ids)
        goal_deadline, goal_verified = None, False
        if config.goal_id is not None:
            goal = session.scalar(
                select(Goal).where(Goal.user_id == user_id, Goal.id == config.goal_id)
            )
            version = (
                session.scalar(
                    select(PolicyVersion)
                    .where(
                        PolicyVersion.user_id == user_id,
                        PolicyVersion.policy_id == goal.policy_id,
                    )
                    .order_by(PolicyVersion.version_number.desc())
                    .limit(1)
                )
                if goal
                else None
            )
            goal_verified = bool(
                goal
                and version
                and goal.policy_version_id == version.id
                and _goal_projection_matches(goal, version)
                and is_version_authorized(session, user_id, version.id, now)
            )
            if goal_verified and goal and version:
                goal_deadline = datetime.combine(
                    goal.deadline, time(), ZoneInfo(context.snapshot.timezone)
                )
                context.sources.used.update(UUID(key) for key in version.evidence_ids)
            else:
                context.sources.issue(
                    "CURRENT_GOAL_REFERENCE_NOT_PROVEN", config.goal_id, "目标当前确认/归属未匹配"
                )
        audit = current_epoch_audit(session, user_id, [])
        if audit.status != "VALID" or not audit.complete:
            context.sources.issue("AUDIT_NOT_PROVEN", str(audit.epoch_id), "当前完整审计未证明")
        context, issues, digest = finalize_financial_context(context, audit)
        plan = None
        if not issues and linked is not None:
            try:
                plan = plan_full_recovery(
                    FullRecoveryPlanningInput(
                        user_id=user_id,
                        policy_id=policy_id,
                        policy_version_id=policy.current_version.version_id,
                        configuration=config,
                        planning_confirmation_valid=policy.planning_confirmation_valid,
                        confirmed_at=policy.current_version.confirmed_at,
                        valid_from=policy.current_version.valid_from,
                        valid_until=policy.current_version.valid_until,
                        linked_asset_policy=linked,
                        snapshot=context.snapshot.model_copy(update={"horizon_days": 365}),
                        boundary_versions=context.versions,
                        positions=context.positions,
                        boundary_products=context.products,
                        holdings=holdings,
                        goal_deadline_at=goal_deadline,
                        goal_reference_verified=goal_verified,
                        planning_deadline_at=planning_deadline_at,
                    )
                )
                if plan.status == "UNKNOWN":
                    context.sources.issue(
                        "FULL_RECOVERY_DOMAIN_NOT_PROVEN",
                        policy_id,
                        ",".join(plan.reasons) or "原恢复输入或保护曲线未证明",
                    )
                    plan = None
                    context, issues, digest = finalize_financial_context(context, audit)
            except (ValueError, TypeError, KeyError, OverflowError) as error:
                if planning_deadline_at is not None and "deadline" in str(error):
                    raise PolicyLifecycleError(
                        "INVALID_PLANNING_DEADLINE", "只能提前真实用款时限", 422
                    ) from error
                context.sources.issue("INVALID_FULL_RECOVERY_INPUT", policy_id, str(error))
                context, issues, digest = finalize_financial_context(context, audit)
        return FullRecoveryPlanningResponse(
            user_id=user_id,
            policy_id=policy_id,
            as_of=now,
            state="COMPUTED" if plan is not None else "UNKNOWN",
            plan=plan,
            catalogue=catalogue,
            catalogue_bindings=bindings,
            source_evidence_ids=sorted(context.sources.used),
            source_issues=issues,
            input_hash=configuration_hash(
                {
                    "financial_source_digest": digest,
                    "policy_version_id": str(policy.current_version.version_id),
                    "plan_input_hash": plan.input_hash if plan else None,
                    "catalogue": catalogue.model_dump(mode="json"),
                    "planning_deadline_at": planning_deadline_at.isoformat()
                    if planning_deadline_at
                    else None,
                }
            ),
            limitations=[
                "FULL_CONFIRMATION_IS_PLANNING_ONLY_NOT_ORIGINAL_BANK_AUTHORITY",
                "NO_EXECUTION_SUBMISSION_RECONCILIATION_OR_NEW_RECEIPT",
                "LOSS_IMPACT_IS_CONDITIONAL_NOT_CURRENT_SAFE_CASH",
                "WHOLE_POSITION_ONLY_NO_PARTIAL_BANK_REDEMPTION_CONTRACT",
                "NO_REGISTERED_BOUNDARY_SHRINK_COMPARISON_OR_NEW_FULL_PROTECTION_EVALUATOR",
            ],
        )
