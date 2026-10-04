"""Read-only current financial facts for immutable simulated execution effects."""

import json
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from app.db.models import (
    ActionPlan,
    ActionResourceReservation,
    AssetPosition,
    AssetProduct,
    Goal,
    PolicyVersion,
)
from app.domain.asset_allocation_types import AssetProductTerms, PlannedPrincipalTerms
from app.domain.boundary_types import BoundaryPolicyVersion, SourceIssue
from app.domain.execution import execution_effect_hash
from app.domain.execution_types import BankCommand, ExecutionContext, ExecutionEffect
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.services.asset_allocation import _goal_projection_matches
from app.services.asset_exposure_import import _scope, load_asset_exposure
from app.services.boundary import BoundaryContext, load_boundary_context
from app.services.execution_sources import execution_return_account, load_execution_quote
from app.services.income_ledger import income_lots_for_action, read_income_state
from app.services.policy_lifecycle import PolicyLifecycleError, is_version_authorized
from app.services.simulated_bank import validate_bank_projection
from sqlalchemy import select
from sqlalchemy.orm import Session


def load_execution_context(
    session: Session,
    user_id: UUID,
    effect: ExecutionEffect,
    now: datetime,
    *,
    own_action_id: UUID | None = None,
) -> ExecutionContext:
    effect = ExecutionEffect.model_validate(effect.model_dump(warnings=False))
    if effect.user_id != user_id:
        raise PolicyLifecycleError("NOT_FOUND", "动作不属于当前用户", 404)
    with session.no_autoflush:
        if own_action_id is not None:
            own = session.get(ActionPlan, own_action_id)
            if own is None or own.user_id != user_id:
                raise PolicyLifecycleError("NOT_FOUND", "自身动作预留身份不存在", 404)
            try:
                command = BankCommand.model_validate_json(json.dumps(own.request["execution"]))
                if (
                    own.id != effect.operation_id
                    or command.effect_hash != execution_effect_hash(effect)
                    or configuration_hash(own.request) != own.request_hash
                ):
                    raise ValueError("Own claims must belong to this exact immutable effect")
            except (KeyError, ValueError, TypeError) as error:
                raise PolicyLifecycleError(
                    "INVALID_EXECUTION_RESERVATION", "不能隐藏其他动作的资源预留", 409
                ) from error
        base = load_boundary_context(session, user_id, now)
        versions = list(base.versions)
        selected = None
        for identity in effect.policy_version_ids:
            row = session.get(PolicyVersion, identity)
            if (
                row is None
                or row.user_id != user_id
                or not is_version_authorized(session, user_id, identity, now)
            ):
                base.sources.issue(
                    "INVALID_EXECUTION_AUTHORITY", identity, "动作必须引用当前有效的正式确认版本"
                )
                continue
            if identity == effect.policy_version_id:
                selected = row
            if not any(v.version_id == identity for v in versions):
                versions.append(_version(row))
        if effect.action_type != "TRANSFER_INTERNAL" and (
            selected is None or selected.policy_id != effect.policy_id
        ):
            base.sources.issue(
                "INVALID_EXECUTION_AUTHORITY", str(effect.policy_id), "精确策略身份不一致"
            )
        config = validate_configuration(selected.configuration) if selected is not None else {}
        scope = (
            {"scope": "goal", "goal_id": str(effect.goal_id)}
            if effect.goal_id is not None
            else {"scope": "general_idle_funds"}
        )
        exposure = load_asset_exposure(session, base, scope)
        try:
            validate_bank_projection(session, user_id, now)
        except PolicyLifecycleError as error:
            base.sources.issue(error.code, "independent_bank", error.message)
        if effect.action_type in {"PURCHASE_ASSET", "REDEEM_ASSET"} and (
            config.get("type") != "asset_authorization"
            or config.get("scope") != scope["scope"]
            or config.get("goal_id") != (str(effect.goal_id) if effect.goal_id else None)
        ):
            base.sources.issue(
                "EXECUTION_ASSET_SCOPE_MISMATCH", str(effect.policy_id), "资产权限归属与动作不符"
            )
        if effect.goal_id is not None and effect.action_type in {"PURCHASE_ASSET", "ALLOCATE_GOAL"}:
            _goal_binding(session, base, effect)
        products = [
            AssetProductTerms(
                **{
                    key: getattr(row, key)
                    for key in AssetProductTerms.model_fields
                    if key not in {"product_id", "terms_digest"}
                },
                product_id=row.id,
                terms_digest=configuration_hash(row.maturity_rule),
            )
            for row in session.scalars(select(AssetProduct).order_by(AssetProduct.id))
        ]
        quote = None
        if effect.action_type == "REDEEM_ASSET":
            try:
                _original_purchase(session, base, effect, exposure.counted_position_ids, config)
                if effect.position_id is None:
                    raise ValueError("Missing original position")
                quote = load_execution_quote(
                    session, user_id, effect.position_id, now, requested_at=effect.valid_from
                )
                if not (effect.fee_cents or effect.loss_cents) and quote.kind != "REDEEM":
                    raise RedemptionPermissionDenied("ZERO_COST_QUOTE_IS_NOT_AUTOMATIC_REDEMPTION")
            except RedemptionPermissionDenied as error:
                base.sources.issue(
                    "EXECUTION_REDEMPTION_PERMISSION_DENIED", str(effect.position_id), str(error)
                )
            except (ValueError, TypeError, KeyError) as error:
                base.sources.issue(
                    "INVALID_ORIGINAL_REDEMPTION_SOURCE", str(effect.position_id), str(error)
                )
        lots = []
        legacy: dict[UUID, int] = {}
        has_ledger = any(
            item.source_type == "SIMULATED_NEW_FUNDS_LEDGER" and item.status != "SUPERSEDED"
            for item in base.sources.evidence.values()
        )
        if has_ledger or effect.action_type == "ALLOCATE_GOAL" or effect.income_uses:
            try:
                income = read_income_state(session, user_id, now)
                lots = income_lots_for_action(
                    session, user_id, now, action_id=own_action_id if effect.income_uses else None
                )
                for fragment in income.ledger.fragments:
                    legacy[fragment.account_id] = (
                        legacy.get(fragment.account_id, 0) + fragment.legacy_reserved_cents
                    )
            except PolicyLifecycleError as error:
                base.sources.issue(error.code, "income_ledger", error.message)
        cash: dict[UUID, int] = {}
        goals: dict[UUID, int] = {}
        own_cash: dict[UUID, int] = {}
        own_goals: dict[UUID, int] = {}
        reserved_positions = []
        for claim in session.scalars(
            select(ActionResourceReservation).where(
                ActionResourceReservation.user_id == user_id,
                ActionResourceReservation.status == "RESERVED",
            )
        ):
            try:
                if claim.created_at > now:
                    raise ValueError("Future reservation is not a current fact")
                if claim.resource_kind == "CASH":
                    identity = UUID(claim.resource_key)
                    mapping = own_cash if claim.action_plan_id == own_action_id else cash
                    mapping[identity] = mapping.get(identity, 0) + claim.amount_cents
                elif claim.resource_kind == "GOAL_CASH":
                    identity = UUID(claim.resource_key)
                    mapping = own_goals if claim.action_plan_id == own_action_id else goals
                    mapping[identity] = mapping.get(identity, 0) + claim.amount_cents
                elif claim.resource_kind == "POSITION" and claim.action_plan_id != own_action_id:
                    reserved_positions.append(UUID(claim.resource_key))
            except ValueError:
                base.sources.issue(
                    "INVALID_EXECUTION_RESERVATION", claim.id, "资源身份不是有效UUID"
                )
        remaining_cash = dict(exposure.reserved_cash_by_account)
        remaining_goals = dict(exposure.reserved_goal_cash_by_goal)
        for original, own_amounts in ((remaining_cash, own_cash), (remaining_goals, own_goals)):
            for identity, amount in own_amounts.items():
                if amount > original.get(identity, 0):
                    base.sources.issue(
                        "INCONSISTENT_OWN_EXPOSURE", str(identity), "自身资源预留与完整曝光不一致"
                    )
                else:
                    original[identity] -= amount
        own_pending = effect.amount_cents if own_action_id in exposure.counted_action_ids else 0
        if own_pending > exposure.pending_purchase_cents:
            base.sources.issue(
                "INCONSISTENT_OWN_EXPOSURE", str(own_action_id), "自身申购超过待申购曝光"
            )
            own_pending = 0
        exposure = exposure.model_copy(
            update={
                "reserved_cash_by_account": remaining_cash,
                "reserved_goal_cash_by_goal": remaining_goals,
                "pending_purchase_cents": exposure.pending_purchase_cents - own_pending,
                "counted_action_ids": [
                    identity
                    for identity in exposure.counted_action_ids
                    if identity != own_action_id
                ],
            }
        )
        for identity, amount in legacy.items():
            if not amount:
                continue
            if remaining_cash.get(identity, 0) > cash.get(identity, 0):
                base.sources.issue(
                    "LEGACY_INCOME_RESERVATION_RECONCILIATION_REQUIRED",
                    str(identity),
                    "旧收入预留与未映射资产占用可能重叠，不能猜测释放或重复归属",
                )
            else:
                cash[identity] = cash.get(identity, 0) + amount
        context = ExecutionContext(
            user_id=user_id,
            snapshot=base.snapshot,
            versions=versions,
            positions=base.positions,
            boundary_products=base.products,
            products=products,
            lots=lots,
            exposure=exposure,
            redemption_quote=quote,
            requires_confirmation=(
                effect.action_type == "PAY_RECURRING"
                and (
                    not config.get("auto_execute", False)
                    or config.get("amount_rule", {}).get("kind") == "range"
                )
            ),
            reserved_cash_by_account=cash,
            reserved_goal_cash_by_goal=goals,
            reserved_position_ids=sorted(set(reserved_positions)),
            source_issues=[
                SourceIssue(code=i.code, entity_type="source", entity_id=i.source_ref)
                for i in base.sources.issues
            ],
        )
        from app.services.decision_recording import capture_execution_context

        capture_execution_context(session, base, effect, context)
        return context


def _version(row: PolicyVersion) -> BoundaryPolicyVersion:
    if row.confirmed_at is None or row.valid_from is None:
        raise ValueError("Confirmed version lacks its effective window")
    return BoundaryPolicyVersion(
        policy_id=row.policy_id,
        version_id=row.id,
        configuration=validate_configuration(row.configuration),
        content_hash=row.content_hash,
        confirmed_at=row.confirmed_at,
        valid_from=row.valid_from,
        valid_until=row.valid_until,
        evidence_ids=[UUID(identifier) for identifier in row.evidence_ids],
    )


def _goal_binding(session: Session, base: BoundaryContext, effect: ExecutionEffect) -> None:
    goal = session.get(Goal, effect.goal_id)
    current = (
        session.scalar(
            select(PolicyVersion)
            .where(
                PolicyVersion.policy_id == goal.policy_id,
                PolicyVersion.user_id == base.sources.user_id,
            )
            .order_by(PolicyVersion.version_number.desc())
        )
        if goal is not None
        else None
    )
    if (
        goal is None
        or goal.user_id != base.sources.user_id
        or current is None
        or goal.policy_version_id != current.id
        or not _goal_projection_matches(goal, current)
        or not is_version_authorized(session, base.sources.user_id, current.id, base.snapshot.as_of)
        or current.id not in effect.policy_version_ids
        or (
            effect.action_type == "PURCHASE_ASSET"
            and (
                goal.asset_policy_id != effect.policy_id
                or current.configuration.get("asset_policy_id") != str(effect.policy_id)
            )
        )
    ):
        base.sources.issue(
            "INVALID_EXECUTION_GOAL_LINK", str(effect.goal_id), "目标投影或当前双重权限不完整"
        )


def _original_purchase(
    session: Session,
    base: BoundaryContext,
    effect: ExecutionEffect,
    counted: list[UUID],
    current_configuration: dict[str, Any],
) -> None:
    row = session.get(AssetPosition, effect.position_id)
    if (
        row is None
        or row.user_id != base.sources.user_id
        or row.id not in counted
        or row.policy_version_id is None
        or row.policy_version_id != effect.original_policy_version_id
        or row.account_id != effect.position_account_id
        or row.goal_id != effect.goal_id
        or row.product_id != effect.product_id
        or row.principal_cents != effect.amount_cents
    ):
        raise ValueError("Original automatic position and its historical policy must match")
    original = session.get(PolicyVersion, row.policy_version_id)
    expected = str(row.goal_id) if row.goal_id is not None else "general"
    if (
        original is None
        or original.policy_id != effect.policy_id
        or _scope(session, base, original.id) != expected
    ):
        raise ValueError("Original purchase scope cannot be adopted by a new authorization")
    version = _version(original)
    if not max(version.confirmed_at, version.valid_from) <= row.purchased_at or (
        version.valid_until is not None and row.purchased_at >= version.valid_until
    ):
        raise ValueError("Original authority was not effective at purchase")
    product = session.get(AssetProduct, row.product_id)
    if (
        product is None
        or product.created_at > base.snapshot.as_of
        or product.effective_from > row.purchased_at
        or (product.effective_until is not None and row.purchased_at >= product.effective_until)
    ):
        raise ValueError("Original exact product was not known and effective at purchase")
    if (
        execution_return_account(session, base.sources.user_id, row, base.snapshot.as_of)
        != effect.destination_account_id
    ):
        raise ValueError("Principal must return to its original ownership")
    _redemption_permissions(effect, product, version.configuration, current_configuration)
    if not (effect.fee_cents or effect.loss_cents):
        if (
            not product.auto_redeem_allowed
            or product.principal_fluctuation
            or product.risk_level
            or product.asset_class not in {"CASH_MGMT_T0", "CASH_MGMT_T1"}
        ):
            raise RedemptionPermissionDenied("ORIGINAL_PRODUCT_NOT_AUTOMATIC_LOSSLESS")
        terms = PlannedPrincipalTerms.model_validate(product.maturity_rule)
        if (
            terms.settlement_delay_days != product.redemption_delay_days
            or effect.settlement_delay_days != terms.settlement_delay_days
            or base.snapshot.as_of < row.purchased_at + timedelta(days=product.lock_days)
        ):
            raise RedemptionPermissionDenied("ORIGINAL_CONTRACT_NOT_AVAILABLE_FOR_AUTOMATIC_EXIT")


class RedemptionPermissionDenied(ValueError):
    """A known formal policy explicitly forbids this new redemption request."""


def _redemption_permissions(
    effect: ExecutionEffect,
    product: AssetProduct,
    original: dict[str, Any],
    current: dict[str, Any],
) -> None:
    permission = (
        "allow_early_withdrawal_with_penalty"
        if effect.fee_cents or effect.loss_cents
        else "allow_auto_recovery_without_penalty"
    )
    for label, configuration in (("ORIGINAL", original), ("CURRENT", current)):
        if not configuration[permission]:
            raise RedemptionPermissionDenied(f"{label}_{permission.upper()}_DENIED")
        constraints = (
            (effect.amount_cents > configuration["single_action_cap_cents"], "SINGLE_ACTION_CAP"),
            (product.asset_class not in configuration["allowed_asset_classes"], "ASSET_CLASS"),
            (effect.settlement_delay_days > configuration["max_redemption_delay_days"], "DELAY"),
            (product.lock_days > configuration["max_lock_days"], "LOCK"),
            (product.risk_level > configuration["max_principal_risk_level"], "PRINCIPAL_RISK"),
        )
        for denied, code in constraints:
            if denied:
                raise RedemptionPermissionDenied(f"{label}_{code}_EXCEEDS_AUTHORIZATION")
