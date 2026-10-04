"""Pure whole-position safety recovery planning (ADR 0008)."""

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from app.domain.asset_allocation_types import PlannedPrincipalTerms
from app.domain.boundary import compute_boundary
from app.domain.boundary_types import (
    BoundaryPolicyVersion,
    BoundaryPosition,
    BoundaryProduct,
    BoundaryResult,
    BoundarySnapshot,
    SourceIssue,
)
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.domain.recovery_types import (
    RecoveryAction as RecoveryAction,
)
from app.domain.recovery_types import (
    RecoveryAuthorization as RecoveryAuthorization,
)
from app.domain.recovery_types import (
    RecoveryCandidate as RecoveryCandidate,
)
from app.domain.recovery_types import (
    RecoveryPlan as RecoveryPlan,
)
from app.domain.recovery_types import (
    RecoveryPosition as RecoveryPosition,
)
from app.domain.recovery_types import (
    RecoveryQuote as RecoveryQuote,
)

ALGORITHM_VERSION = "whole-position-recovery-v1"


def plan_recovery(
    snapshot: BoundarySnapshot,
    boundary_versions: Sequence[BoundaryPolicyVersion],
    positions: Sequence[BoundaryPosition],
    boundary_products: Sequence[BoundaryProduct],
    recovery_positions: Sequence[RecoveryPosition],
    current_authorizations: Sequence[RecoveryAuthorization],
    *,
    user_id: UUID,
    source_issues: Sequence[SourceIssue] = (),
) -> RecoveryPlan:
    """Plan conditional effects; only bank posting reconciliation can change actual facts."""
    if not isinstance(user_id, UUID):
        raise ValueError("Recovery user_id must be a UUID")
    if (
        len(recovery_positions) > 100
        or len(current_authorizations) > 100
        or len(source_issues) > 1000
        or len(positions) > 10000
    ):
        raise ValueError("Recovery capacity is 100 candidates/authorities and 1000 source issues")
    snapshot = BoundarySnapshot.model_validate(
        _canonical_evidence(snapshot.model_dump(warnings=False))
    )
    positions = [
        BoundaryPosition.model_validate(_canonical_evidence(p.model_dump(warnings=False)))
        for p in positions
    ]
    recovery_positions = [
        RecoveryPosition.model_validate(_canonical_evidence(p.model_dump(warnings=False)))
        for p in recovery_positions
    ]
    current_authorizations = [
        RecoveryAuthorization.model_validate(_canonical_evidence(a.model_dump(warnings=False)))
        for a in current_authorizations
    ]
    source_issues = [
        SourceIssue.model_validate(issue.model_dump(warnings=False)) for issue in source_issues
    ]
    if not snapshot.source_issues and not source_issues:
        _validate_links(snapshot, positions, recovery_positions, current_authorizations, user_id)
    reconciliation = [
        SourceIssue(
            code="RECONCILIATION_REQUIRED", entity_type="position", entity_id=str(p.position_id)
        )
        for p in positions
        if p.status != "REDEEMED"
        and p.principal_cents > 0
        and p.principal_available_at is not None
        and p.principal_available_at <= snapshot.as_of
    ]
    source_issues = [*source_issues, *reconciliation]
    described = {candidate.position_id for candidate in recovery_positions}
    source_issues.extend(
        SourceIssue(
            code="MISSING_RECOVERY_POSITION_METADATA",
            entity_type="position",
            entity_id=str(p.position_id),
        )
        for p in positions
        if p.status != "REDEEMED" and p.principal_cents and p.position_id not in described
    )
    snapshot = snapshot.model_copy(
        update={"source_issues": [*snapshot.source_issues, *source_issues]}
    )
    baseline = compute_boundary(snapshot, boundary_versions, positions, boundary_products)
    inputs_hash = configuration_hash(
        {
            "algorithm_version": ALGORITHM_VERSION,
            "baseline": baseline.boundary_hash,
            "positions": [
                item.model_dump(mode="json")
                for item in sorted(recovery_positions, key=lambda p: p.position_id)
            ],
            "authorizations": [
                item.model_dump(mode="json")
                for item in sorted(current_authorizations, key=lambda a: a.policy_id)
            ],
            "user_id": str(user_id),
        }
    )
    if baseline.status == "INSUFFICIENT_EVIDENCE":
        return RecoveryPlan(
            algorithm_version=ALGORITHM_VERSION,
            user_id=user_id,
            as_of=snapshot.as_of,
            status="INSUFFICIENT_EVIDENCE",
            actual_boundary=baseline,
            plan_hash=inputs_hash,
            reasons=["RECONCILIATION_REQUIRED"]
            if reconciliation
            else ["SOURCE_EVIDENCE_INCOMPLETE"],
        )
    projected = baseline
    working = snapshot
    working_positions = list(positions)
    steps: list[RecoveryAction] = []
    candidates: list[RecoveryCandidate] = []
    ordering = {"CASH_MGMT_T0": 0, "CASH_MGMT_T1": 1, "FIXED_DEPOSIT": 2}
    if baseline.status == "LIQUIDITY_RISK":
        for candidate in sorted(
            recovery_positions,
            key=lambda p: (ordering.get(p.product.asset_class, 3), p.position_id),
        ):
            if projected.status == "READY":
                candidates.append(
                    RecoveryCandidate(
                        position_id=candidate.position_id,
                        decision="BLOCKED",
                        reasons=["NO_NEGATIVE_POINT_IMPROVED"],
                    )
                )
                continue
            held = next(p for p in positions if p.position_id == candidate.position_id)
            if held.status in {"REDEEMING", "REDEEMED"} or candidate.reserved_principal_cents:
                candidates.append(
                    RecoveryCandidate(
                        position_id=candidate.position_id,
                        decision="BLOCKED",
                        reasons=["POSITION_ALREADY_REDEEMED_OR_RESERVED"],
                    )
                )
                continue
            if candidate.acquisition == "MANUAL":
                candidates.append(
                    RecoveryCandidate(
                        position_id=candidate.position_id,
                        decision="ADVISE_ONLY",
                        reasons=["MANUAL_POSITION_HAS_NO_ORIGINAL_RECOVERY_AUTHORITY"],
                    )
                )
                continue
            original = candidate.original_authorization
            quote = candidate.quote
            if original is None or quote is None or candidate.acquisition == "UNKNOWN":
                candidates.append(
                    RecoveryCandidate(
                        position_id=candidate.position_id,
                        decision="ASK_ONCE",
                        reasons=["MISSING_ACQUISITION_AUTHORITY_OR_QUOTE_EVIDENCE"],
                    )
                )
                continue
            if quote.request_at != snapshot.as_of or quote.expires_at <= snapshot.as_of:
                candidates.append(
                    RecoveryCandidate(
                        position_id=candidate.position_id,
                        decision="ASK_ONCE",
                        reasons=["FRESH_TIME_BOUND_QUOTE_REQUIRED"],
                    )
                )
                continue
            authority = next(
                (a for a in current_authorizations if a.policy_id == original.policy_id), None
            )
            if authority is None:
                candidates.append(
                    RecoveryCandidate(
                        position_id=candidate.position_id,
                        decision="ASK_ONCE",
                        reasons=["CURRENT_AUTHORIZATION_EVIDENCE_MISSING"],
                    )
                )
                continue
            reason = _authority_reason(candidate, authority, snapshot.as_of)
            if reason is not None:
                candidates.append(
                    RecoveryCandidate(
                        position_id=candidate.position_id, decision="ADVISE_ONLY", reasons=[reason]
                    )
                )
                continue
            if quote.fee_cents or quote.loss_cents:
                candidates.append(
                    RecoveryCandidate(
                        position_id=candidate.position_id,
                        decision="ASK_ONCE",
                        reasons=["POSITIVE_FEE_OR_LOSS_REQUIRES_NEW_CONFIRMATION"],
                        action=_action(
                            user_id,
                            candidate,
                            authority,
                            baseline,
                            inputs_hash,
                            autonomy="ASK_ONCE",
                        ),
                    )
                )
                continue
            terms_reason = _terms_reason(candidate, snapshot.as_of)
            if terms_reason is not None:
                candidates.append(
                    RecoveryCandidate(
                        position_id=candidate.position_id,
                        decision="ASK_ONCE",
                        reasons=[terms_reason],
                    )
                )
                continue
            proposed_snapshot, proposed_positions = _project(working, working_positions, candidate)
            proposed = compute_boundary(
                proposed_snapshot, boundary_versions, proposed_positions, boundary_products
            )
            before_points = projected.calculation_trace
            after_points = proposed.calculation_trace
            if any(
                after.margin_cents < before.margin_cents
                for before, after in zip(before_points, after_points, strict=True)
            ):
                candidates.append(
                    RecoveryCandidate(
                        position_id=candidate.position_id,
                        decision="BLOCKED",
                        reasons=["RECOVERY_WORSENS_A_CHECKPOINT"],
                    )
                )
                continue
            if not any(
                before.margin_cents < 0 and after.margin_cents > before.margin_cents
                for before, after in zip(before_points, after_points, strict=True)
            ):
                candidates.append(
                    RecoveryCandidate(
                        position_id=candidate.position_id,
                        decision="BLOCKED",
                        reasons=["NO_NEGATIVE_POINT_IMPROVED"],
                        projected_boundary=proposed,
                    )
                )
                continue
            action = _action(user_id, candidate, authority, baseline, inputs_hash)
            steps.append(action)
            candidates.append(
                RecoveryCandidate(
                    position_id=candidate.position_id,
                    decision="AUTO_EXECUTE",
                    action=action,
                    projected_boundary=proposed,
                )
            )
            projected = proposed
            working, working_positions = proposed_snapshot, proposed_positions
    uncovered = [p for p in projected.calculation_trace if p.margin_cents < 0]
    last_unsafe = max(
        (
            index
            for index, point in enumerate(projected.calculation_trace)
            if point.margin_cents < 0
        ),
        default=-1,
    )
    safe_point = projected.calculation_trace[last_unsafe + 1] if last_unsafe < 272 else None
    return RecoveryPlan(
        algorithm_version=ALGORITHM_VERSION,
        user_id=user_id,
        as_of=snapshot.as_of,
        status=("PARTIAL_RECOVERY_AVAILABLE" if uncovered else "AUTO_RECOVERY_AVAILABLE")
        if steps
        else (
            "NO_RECOVERY_NEEDED"
            if baseline.status == "READY"
            else (
                "ASK_ONCE"
                if any(c.decision == "ASK_ONCE" for c in candidates)
                else (
                    "ADVISE_ONLY"
                    if any(c.decision == "ADVISE_ONLY" for c in candidates)
                    else "NO_SAFE_RECOVERY"
                )
            )
        ),
        actual_boundary=baseline,
        projected_boundary=projected,
        steps=steps,
        candidates=candidates,
        uncovered_checkpoints=uncovered,
        first_sustained_safe_point=safe_point,
        plan_hash=inputs_hash,
    )


def _project(
    snapshot: BoundarySnapshot, positions: Sequence[BoundaryPosition], candidate: RecoveryPosition
) -> tuple[BoundarySnapshot, list[BoundaryPosition]]:
    quote = candidate.quote
    if quote is None:
        raise ValueError("A quote is required for projection")
    immediate = quote.principal_available_at == snapshot.as_of
    working = snapshot.model_copy(
        update={
            "cash_accounts": [
                account.model_copy(
                    update={"balance_cents": account.balance_cents + quote.net_cents}
                )
                if immediate and account.account_id == candidate.destination_account_id
                else account
                for account in snapshot.cash_accounts
            ],
            "goals": [
                goal.model_copy(
                    update={
                        "cash_owned_cents": goal.cash_owned_cents + quote.net_cents,
                        "principal_owned_cents": goal.principal_owned_cents - quote.principal_cents,
                    }
                )
                if immediate and goal.goal_id == candidate.goal_id
                else goal
                for goal in snapshot.goals
            ],
        }
    )
    updated_positions = [
        position.model_copy(
            update={"status": "REDEEMED"}
            if immediate
            else {
                "status": "REDEEMING",
                "principal_available_at": quote.principal_available_at,
                "availability_evidence_ids": quote.evidence_ids,
            }
        )
        if position.position_id == candidate.position_id
        else position
        for position in positions
    ]
    return working, updated_positions


def _authorization_config(version: BoundaryPolicyVersion) -> dict[str, Any]:
    config = validate_configuration(version.configuration)
    if (
        config["type"] != "asset_authorization"
        or configuration_hash(config) != version.content_hash
    ):
        raise ValueError("Recovery authority must be a hash-bound asset authorization")
    return config


def _validate_links(
    snapshot: BoundarySnapshot,
    positions: Sequence[BoundaryPosition],
    candidates: Sequence[RecoveryPosition],
    authorities: Sequence[RecoveryAuthorization],
    user_id: UUID,
) -> None:
    held = {p.position_id: p for p in positions}
    accounts = {a.account_id: a for a in snapshot.cash_accounts}
    goals = {g.goal_id: g for g in snapshot.goals}
    product_facts: dict[UUID, dict[str, Any]] = {}
    catalog_versions: dict[tuple[str, int], UUID] = {}
    for identities in (
        [p.position_id for p in candidates],
        [a.policy_id for a in authorities],
        [a.version_id for a in authorities],
        [p.quote.quote_id for p in candidates if p.quote is not None],
    ):
        if len(set(identities)) != len(identities):
            raise ValueError("Recovery metadata, quote and authority identities must be unique")
    for authority in authorities:
        if authority.user_id != user_id:
            raise ValueError("Current recovery authority must belong to the user")
        _authorization_config(authority)
    for candidate in candidates:
        if candidate.position_id not in held:
            raise ValueError("Recovery position must reference a financial position")
        position = held[candidate.position_id]
        if (
            candidate.goal_id != position.goal_id
            or candidate.reserved_principal_cents > position.principal_cents
        ):
            raise ValueError("Recovery ownership or reserved principal contradicts the position")
        quote = candidate.quote
        product = candidate.product
        product_fact = product.model_dump(mode="json")
        if (
            product.product_id in product_facts
            and product_facts[product.product_id] != product_fact
        ):
            raise ValueError("One original product identity cannot have conflicting catalog facts")
        product_facts[product.product_id] = product_fact
        catalog_key = (product.product_code, product.version_number)
        if catalog_key in catalog_versions and catalog_versions[catalog_key] != product.product_id:
            raise ValueError("Product code/version must identify exactly one original product")
        catalog_versions[catalog_key] = product.product_id
        if candidate.original_authorization is not None:
            _authorization_config(candidate.original_authorization)
        if (
            candidate.purchased_at > snapshot.as_of
            or product.created_at > snapshot.as_of
            or product.effective_from > candidate.purchased_at
            or (
                product.effective_until is not None
                and candidate.purchased_at >= product.effective_until
            )
        ):
            raise ValueError("Original product must be currently known and effective at purchase")
        destination = accounts.get(candidate.destination_account_id)
        if destination is None or destination.account_type not in {"CASH", "GOAL"}:
            raise ValueError("Recovery requires a known cash destination account")
        if candidate.goal_id is None and destination.account_type != "CASH":
            raise ValueError("General principal cannot be assigned to a GOAL destination")
        if candidate.goal_id is not None and (
            candidate.goal_id not in goals
            or goals[candidate.goal_id].account_id != candidate.destination_account_id
        ):
            raise ValueError("Goal principal must return to the same goal ownership account")
        if quote is not None and (
            quote.user_id != user_id
            or quote.position_id != position.position_id
            or quote.product_id != product.product_id
            or quote.product_version_number != product.version_number
            or quote.terms_digest != product.terms_digest
            or quote.principal_cents != position.principal_cents
        ):
            raise ValueError(
                "Quote must bind the same user, whole position and exact original product"
            )


def _canonical_evidence(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("Recovery object keys must be strings")
            if key.endswith("evidence_ids") and isinstance(child, list):
                if (
                    any(not isinstance(item, UUID) for item in child)
                    or len(child) > 10000
                    or len(set(child)) != len(child)
                ):
                    raise ValueError("Evidence identities must be unique and within capacity")
                result[key] = sorted(child)
            else:
                result[key] = _canonical_evidence(child)
        return result
    if isinstance(value, list):
        return [_canonical_evidence(child) for child in value]
    return value


def _terms_reason(candidate: RecoveryPosition, now: datetime) -> str | None:
    quote = candidate.quote
    if quote is None:
        return "QUOTED_PRINCIPAL_TERMS_MISSING"
    product = candidate.product
    try:
        terms = PlannedPrincipalTerms.model_validate(product.maturity_rule)
    except ValueError:
        return "ORIGINAL_PRODUCT_HAS_NO_VERIFIED_REQUEST_REDEMPTION_TERMS"
    if not product.auto_redeem_allowed or product.principal_fluctuation or product.risk_level:
        return "PRODUCT_HAS_NO_AUTOMATIC_LOSSLESS_REDEMPTION"
    if quote.kind != "REDEEM" or product.asset_class not in {"CASH_MGMT_T0", "CASH_MGMT_T1"}:
        return "REDEMPTION_KIND_REQUIRES_REVIEW"
    try:
        available_at = now + timedelta(days=terms.settlement_delay_days)
        unlocked_at = candidate.purchased_at + timedelta(days=product.lock_days)
    except OverflowError:
        return "PRODUCT_DATES_OUTSIDE_SUPPORTED_CALENDAR"
    if (
        terms.settlement_delay_days != product.redemption_delay_days
        or quote.principal_available_at != available_at
    ):
        return "QUOTE_ARRIVAL_CONTRADICTS_ORIGINAL_TERMS"
    if now < unlocked_at:
        return "POSITION_STILL_LOCKED"
    return None


def _authority_reason(
    candidate: RecoveryPosition, authority: RecoveryAuthorization, now: datetime
) -> str | None:
    original = candidate.original_authorization
    quote = candidate.quote
    if original is None or quote is None:
        raise ValueError("A quote and original authorization are required")
    if authority.policy_status != "ACTIVE" or authority.latest_version_id != authority.version_id:
        return "CURRENT_AUTHORIZATION_NOT_ACTIVE_LATEST_VERSION"
    if max(authority.confirmed_at, authority.valid_from) > now or (
        authority.valid_until is not None and now >= authority.valid_until
    ):
        return "CURRENT_AUTHORIZATION_NOT_EFFECTIVE"
    if max(original.confirmed_at, original.valid_from) > candidate.purchased_at or (
        original.valid_until is not None and candidate.purchased_at >= original.valid_until
    ):
        return "ORIGINAL_AUTHORIZATION_NOT_EFFECTIVE_AT_PURCHASE"
    for label, version in (("ORIGINAL", original), ("CURRENT", authority)):
        config = _authorization_config(version)
        goal_id = str(candidate.goal_id) if candidate.goal_id is not None else None
        if (
            config["scope"] != ("goal" if goal_id else "general_idle_funds")
            or config.get("goal_id") != goal_id
        ):
            return label + "_AUTHORIZATION_SCOPE_MISMATCH"
        if not config["allow_auto_recovery_without_penalty"]:
            return label + "_RECOVERY_NOT_AUTHORIZED"
        if quote.principal_cents > config["single_action_cap_cents"]:
            return label + "_WHOLE_POSITION_EXCEEDS_SINGLE_ACTION_CAP"
        if candidate.product.asset_class not in config["allowed_asset_classes"]:
            return label + "_ASSET_CLASS_NOT_AUTHORIZED"
        delay = quote.principal_available_at - quote.request_at
        delay_days = delay.days + int(bool(delay.seconds or delay.microseconds))
        if delay_days > config["max_redemption_delay_days"]:
            return label + "_REDEMPTION_DELAY_EXCEEDS_AUTHORIZATION"
        if candidate.product.lock_days > config["max_lock_days"]:
            return label + "_PRODUCT_LOCK_EXCEEDS_AUTHORIZATION"
        if candidate.product.risk_level > config["max_principal_risk_level"]:
            return label + "_PRODUCT_RISK_EXCEEDS_AUTHORIZATION"
    return None


def _action(
    user_id: UUID,
    candidate: RecoveryPosition,
    authority: RecoveryAuthorization,
    baseline: BoundaryResult,
    inputs_hash: str,
    *,
    autonomy: Literal["AUTO_EXECUTE", "ASK_ONCE"] = "AUTO_EXECUTE",
) -> RecoveryAction:
    quote = candidate.quote
    original = candidate.original_authorization
    if quote is None or original is None:
        raise ValueError("A recovery quote and original authorization are required")
    action = RecoveryAction(
        user_id=user_id,
        position_id=candidate.position_id,
        source_account_id=candidate.account_id,
        destination_account_id=candidate.destination_account_id,
        goal_id=candidate.goal_id,
        product_id=candidate.product.product_id,
        product_version_number=candidate.product.version_number,
        terms_digest=candidate.product.terms_digest,
        original_policy_version_id=original.version_id,
        current_policy_id=authority.policy_id,
        current_policy_version_id=authority.version_id,
        current_authorization_hash=authority.content_hash,
        quote=quote,
        autonomy_level=autonomy,
        baseline_boundary_hash=baseline.boundary_hash,
        plan_inputs_hash=inputs_hash,
        expires_at=min(quote.expires_at, authority.valid_until)
        if authority.valid_until is not None
        else quote.expires_at,
        request_hash="",
    )
    action = action.model_copy(
        update={
            "request_hash": configuration_hash(
                action.model_dump(mode="json", exclude={"request_hash"})
            )
        }
    )
    return action
