"""Read-only financial change previews; candidate rules never become source evidence."""

from datetime import datetime
from typing import Any, Literal, cast
from uuid import UUID

from app.db.models import (
    Account,
    ActionPlan,
    ActionResourceReservation,
    AssetPosition,
    AssetProduct,
    EvidenceItem,
    Goal,
    User,
)
from app.domain.boundary_types import BoundaryModel, GoalOwnership
from app.domain.full_policy_change_impact import (
    FullPolicyFinancialImpact,
    FullPolicyImpactInput,
    project_full_policy_change,
)
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import row_copy
from app.services.boundary import Sources, _cash, _positions, _products
from app.services.full_policy_lifecycle import (
    FullPreviewRequest,
    _full_window,
    _read_snapshot,
    _references,
    canonical_candidate,
    changed_fields,
    derived_state,
    next_state,
    read_full_policy,
)
from app.services.full_protection_projection import compute_full_annual_protection
from app.services.policy_lifecycle import PolicyLifecycleError, _now
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session


class FullPolicyChangeFinancialPreview(BoundaryModel):
    simulation: Literal[True] = True
    hypothetical: Literal[True] = True
    grants_authority: Literal[False] = False
    policy_id: UUID
    epoch_id: UUID
    expected_version_id: UUID
    as_of: datetime
    configuration_hash: str
    current_configuration_hash: str
    before_configuration: dict[str, Any]
    after_configuration: dict[str, Any]
    changed_fields: list[str]
    current_fact_digest: str
    reference_snapshots: list[dict[str, Any]]
    financial_impact: FullPolicyFinancialImpact
    original_action_ids: list[UUID] = Field(default_factory=list)
    original_position_ids: list[UUID] = Field(default_factory=list)
    action_impact: str = (
        "ORIGINAL_ACTIONS_UNCHANGED_REQUIRES_FRESH_RECOMPUTATION_AFTER_CONFIRMATION"
    )


def _goal_facts(
    rows: list[Goal], positions: list[AssetPosition], sources: Sources, verified_ids: set[UUID]
) -> list[GoalOwnership]:
    result = []
    for row in rows:
        principal = sum(
            position.principal_cents
            for position in positions
            if position.goal_id == row.id and position.status != "REDEEMED"
        )
        cash = row.allocated_cents - principal
        matches = sources.candidates("SIMULATED_GOAL_OWNERSHIP", "goal_id", row.id)
        if len(matches) != 1 or matches[0].id not in verified_ids:
            raise ValueError("Original annual goal ownership proof was not verified")
        proof = matches[0]
        expected = {
            "simulation": True,
            "user_id": str(sources.user_id),
            "protocol": "goal-ownership-v1",
            "goal_id": str(row.id),
            "policy_id": str(row.policy_id),
            "account_id": str(row.account_id) if row.account_id else None,
            "allocated_cents": row.allocated_cents,
            "cash_owned_cents": cash,
            "principal_owned_cents": principal,
            "position_ids": sorted(
                str(position.id)
                for position in positions
                if position.goal_id == row.id and position.status != "REDEEMED"
            ),
        }
        if not sources.valid(proof, expected) or cash < 0:
            raise ValueError("Current original goal projection does not match its verified proof")
        result.append(
            GoalOwnership(
                goal_id=row.id,
                policy_id=row.policy_id,
                account_id=row.account_id,
                cash_owned_cents=cash,
                principal_owned_cents=principal,
                allocated_cents=row.allocated_cents,
                evidence_ids=[proof.id],
            )
        )
    return result


def preview_full_policy_financial_impact(
    session: Session,
    user_id: UUID,
    policy_id: UUID,
    body: FullPreviewRequest,
    now: datetime,
) -> FullPolicyChangeFinancialPreview:
    """One RR/RO request verifies the bank/income/audit annual originals exactly once.

    Narrow cash/product/ownership DTO reads reuse that verified snapshot locally;
    no mutable context or permission is stored on the Session or across requests.
    """
    _read_snapshot(session)
    now = _now(now)
    with session.no_autoflush:
        current = read_full_policy(session, user_id, policy_id, now)
        if current.effective_status == "ARCHIVED":
            raise PolicyLifecycleError("ARCHIVED_FULL_POLICY", "旧周期不能预览当前修改", 409)
        if body.expected_version_id != current.current_version.version_id:
            raise PolicyLifecycleError("STALE_POLICY_VERSION", "完整策略版本已变化", 409)
        canonical = canonical_candidate(current.template_name, body.configuration)
        user = session.get(User, user_id)
        assert user is not None
        start, end = _full_window(user, canonical, now)
        next_state(
            "CHANGE", current.effective_status, derived_state("ACTIVE", now, start, end, now)
        )
        refs, _ = _references(session, user_id, canonical, now)
        annual = compute_full_annual_protection(session, user_id, now)
        source = next(
            (row for row in annual.full_policy_sources if row.policy_id == policy_id), None
        )
        evidence = list(
            session.scalars(select(EvidenceItem).where(EvidenceItem.user_id == user_id))
        )
        sources = Sources(user_id, now, evidence)
        accounts = list(session.scalars(select(Account).where(Account.user_id == user_id)))
        positions = list(
            session.scalars(
                select(AssetPosition)
                .where(AssetPosition.user_id == user_id)
                .order_by(AssetPosition.id)
            )
        )
        goals = list(session.scalars(select(Goal).where(Goal.user_id == user_id).order_by(Goal.id)))
        actions = list(
            session.scalars(
                select(ActionPlan).where(ActionPlan.user_id == user_id).order_by(ActionPlan.id)
            )
        )
        products = list(session.scalars(select(AssetProduct).order_by(AssetProduct.id)))
        claims = list(
            session.scalars(
                select(ActionResourceReservation)
                .where(
                    ActionResourceReservation.user_id == user_id,
                    ActionResourceReservation.status == "RESERVED",
                    ActionResourceReservation.resource_kind == "CASH",
                )
                .order_by(ActionResourceReservation.id)
            )
        )
        issues = [row.code + ":" + row.source_ref for row in annual.source_issues]
        if not annual.audit.complete or annual.audit.status != "VALID":
            issues.append("CURRENT_COMPLETE_AUDIT_NOT_VERIFIED")
        cash = _cash(accounts, sources)
        product_facts = _products(products, sources)
        position_facts = _positions(positions, sources)
        goal_facts = []
        if annual.projection.status != "UNKNOWN":
            try:
                goal_facts = _goal_facts(goals, positions, sources, set(annual.source_evidence_ids))
            except (ValueError, TypeError, KeyError):
                issues.append("CURRENT_GOAL_OWNERSHIP_NOT_PROVEN")
        issues.extend(row.code + ":" + row.source_ref for row in sources.issues)
        if len(actions) > 10000 or len(positions) > 10000 or len(products) > 10000:
            issues.append("PREVIEW_ORIGINAL_INVENTORY_CAPACITY_EXCEEDED")
        references_verified = True
        if source is not None and source.template_name == "DatedExpensePolicy":
            required = set(canonical["must_not_reduce_policy_ids"])
            protected = {str(row.policy_id): row for row in source.protected_references}
            # Newly named protected references need their own current confirmation
            # adapter. Existing verified floors remain untouched in every case.
            references_verified = required.issubset(protected) and all(
                protected[key].current_confirmed for key in required
            )
        try:
            impact = project_full_policy_change(
                FullPolicyImpactInput(
                    as_of=now,
                    timezone=cast(Literal["Asia/Shanghai", "UTC"], user.timezone),
                    selected_source=source,
                    candidate_configuration=canonical,
                    candidate_valid_from=start,
                    candidate_valid_until=end,
                    candidate_references_verified=references_verified,
                    original=annual.projection,
                    cash_accounts=cash,
                    goals=goal_facts,
                    positions=position_facts,
                    products=product_facts,
                    active_cash_claims_cents=sum(row.amount_cents for row in claims),
                    source_issues=issues,
                )
            )
        except (ValueError, TypeError, OverflowError) as error:
            raise PolicyLifecycleError(
                "INVALID_FULL_CHANGE_PROJECTION", "策略变更预览原件或年度曲线不一致", 409
            ) from error
        fact_digest = configuration_hash(
            {
                "protocol": "full-change-preview-actual-facts-v1",
                "user_id": str(user_id),
                "epoch_id": str(current.epoch_id),
                "as_of": now.isoformat(),
                "full_policy": current.model_dump(mode="json"),
                "annual_input_digest": annual.input_digest,
                "candidate_references": refs,
                "actions": [row_copy(row) for row in actions],
                "positions": [row_copy(row) for row in positions],
                "goals": [row_copy(row) for row in goals],
                "products": [row_copy(row) for row in products],
                "active_cash_claims": [row_copy(row) for row in claims],
            }
        )
        return FullPolicyChangeFinancialPreview(
            policy_id=policy_id,
            epoch_id=current.epoch_id,
            expected_version_id=current.current_version.version_id,
            as_of=now,
            configuration_hash=configuration_hash(canonical),
            current_configuration_hash=current.current_version.content_hash,
            before_configuration=current.current_version.configuration,
            after_configuration=canonical,
            changed_fields=changed_fields(current.current_version.configuration, canonical),
            current_fact_digest=fact_digest,
            reference_snapshots=refs,
            financial_impact=impact,
            original_action_ids=[row.id for row in actions],
            original_position_ids=[row.id for row in positions],
        )
