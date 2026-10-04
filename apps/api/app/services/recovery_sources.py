"""Read-only recovery inputs: original acquisitions, current authority and bank-bound quotes."""

import json
from datetime import timedelta
from typing import Literal
from uuid import UUID, uuid5

from app.db.models import Account, AssetPosition, AssetProduct, Policy, PolicyVersion
from app.domain.asset_allocation_types import (
    AssetProductTerms,
    FixedPrincipalTerms,
    PlannedPrincipalTerms,
)
from app.domain.boundary_types import BoundaryPolicyVersion
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.domain.recovery_types import RecoveryAuthorization, RecoveryPosition, RecoveryQuote
from app.services.asset_exposure_import import _scope, load_asset_exposure
from app.services.boundary import BoundaryContext
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.simulated_bank import validate_bank_projection
from sqlalchemy import select
from sqlalchemy.orm import Session

QUOTE_SOURCE = "SIMULATED_REDEMPTION_QUOTE"


def _version(row: PolicyVersion) -> BoundaryPolicyVersion:
    if row.confirmed_at is None or row.valid_from is None:
        raise ValueError("A recovery authorization must have a confirmed effective window")
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


def _product(row: AssetProduct) -> AssetProductTerms:
    return AssetProductTerms(
        **{
            key: getattr(row, key)
            for key in AssetProductTerms.model_fields
            if key not in {"product_id", "terms_digest"}
        },
        product_id=row.id,
        terms_digest=configuration_hash(row.maturity_rule),
    )


def _quote(
    context: BoundaryContext, row: AssetPosition, product: AssetProductTerms, destination: UUID
) -> RecoveryQuote | None:
    now = context.snapshot.as_of
    proofs = context.sources.candidates(QUOTE_SOURCE, "position_id", row.id)
    if proofs:
        if len(proofs) != 1:
            raise ValueError("Conflicting position redemption quotes")
        proof = proofs[0]
        expected = {
            "simulation": True,
            "protocol": "recovery-quote-v1",
            "user_id": str(row.user_id),
            "position_id": str(row.id),
            "destination_account_id": str(destination),
            "goal_id": str(row.goal_id) if row.goal_id else None,
        }
        if not context.sources.valid(proof, expected):
            raise ValueError("Invalid or unknown redemption quote source")
        quote = RecoveryQuote.model_validate_json(json.dumps(proof.content["quote"]))
        if (
            quote.user_id != row.user_id
            or quote.position_id != row.id
            or quote.product_id != row.product_id
            or quote.product_version_number != product.version_number
            or quote.terms_digest != product.terms_digest
            or quote.principal_cents != row.principal_cents
            or quote.request_at != now
            or quote.expires_at <= now
        ):
            raise ValueError("Redemption quote is stale or belongs to another economic position")
        return quote.model_copy(update={"evidence_ids": [proof.id]})
    rule = product.maturity_rule
    kind: Literal["REDEEM", "EARLY_WITHDRAW", "MATURE"]
    try:
        if rule.get("protocol") == "planned-principal-return-v1":
            terms = PlannedPrincipalTerms.model_validate(rule)
            if (
                terms.settlement_delay_days != product.redemption_delay_days
                or row.purchased_at + timedelta(days=product.lock_days) > now
            ):
                return None
            available = now + timedelta(days=terms.settlement_delay_days)
            kind = "REDEEM"
        elif rule.get("protocol") == "fixed-principal-return-v1":
            fixed = FixedPrincipalTerms.model_validate(rule)
            if (
                fixed.term_days < product.lock_days
                or fixed.settlement_delay_days != product.redemption_delay_days
            ):
                return None
            maturity = row.purchased_at + timedelta(
                days=fixed.term_days + fixed.settlement_delay_days
            )
            if now < maturity or row.maturity_at is None or row.maturity_at > now:
                return None
            available, kind = now, "MATURE"
        else:
            return None
    except (ValueError, TypeError, OverflowError):
        return None
    return RecoveryQuote(
        quote_id=uuid5(row.id, f"redemption-quote:{product.terms_digest}:{now.isoformat()}"),
        user_id=row.user_id,
        position_id=row.id,
        product_id=product.product_id,
        product_version_number=product.version_number,
        terms_digest=product.terms_digest,
        kind=kind,
        principal_cents=row.principal_cents,
        fee_cents=0,
        loss_cents=0,
        net_cents=row.principal_cents,
        request_at=now,
        principal_available_at=available,
        expires_at=now + timedelta(minutes=15),
        evidence_ids=[],
    )


def recovery_inputs(
    session: Session, context: BoundaryContext
) -> tuple[list[RecoveryPosition], list[RecoveryAuthorization]]:
    user_id, now = context.sources.user_id, context.snapshot.as_of
    exposure = load_asset_exposure(session, context, {"scope": "general_idle_funds"})
    try:
        validate_bank_projection(session, user_id, now)
    except PolicyLifecycleError as error:
        context.sources.issue(error.code, "independent_bank", error.message)
    versions = list(session.scalars(select(PolicyVersion).where(PolicyVersion.user_id == user_id)))
    by_id = {row.id: row for row in versions}
    policies = list(
        session.scalars(
            select(Policy).where(
                Policy.user_id == user_id, Policy.policy_type == "asset_authorization"
            )
        )
    )
    current: list[RecoveryAuthorization] = []
    for policy in policies:
        candidates = [row for row in versions if row.policy_id == policy.id]
        if not candidates:
            continue
        latest = max(candidates, key=lambda row: row.version_number)
        try:
            _scope(session, context, latest.id)
            current.append(
                RecoveryAuthorization.model_validate(
                    {
                        **_version(latest).model_dump(),
                        "user_id": user_id,
                        "policy_status": policy.status,
                        "latest_version_id": latest.id,
                    }
                )
            )
        except (ValueError, TypeError, PolicyLifecycleError) as error:
            context.sources.issue("INVALID_RECOVERY_AUTHORITY", latest.id, str(error))
    positions = list(session.scalars(select(AssetPosition).where(AssetPosition.user_id == user_id)))
    accounts = list(session.scalars(select(Account).where(Account.user_id == user_id)))
    cash = sorted(row.id for row in accounts if row.account_type == "CASH")
    products = {row.id: row for row in session.scalars(select(AssetProduct))}
    result: list[RecoveryPosition] = []
    for row in sorted(positions, key=lambda item: item.id):
        if row.status == "REDEEMED":
            continue
        try:
            proofs = context.sources.candidates("SIMULATED_BANK_POSITION", "position_id", row.id)
            if len(proofs) != 1 or not context.sources.valid(proofs[0], {}):
                raise ValueError("One known position proof is required")
            proof = proofs[0]
            source = proof.content.get("acquisition")
            acquisition: Literal["AUTHORIZED_PURCHASE", "MANUAL", "UNKNOWN"] = (
                "MANUAL" if row.id in exposure.excluded_manual_position_ids else "UNKNOWN"
            )
            original = None
            if source == "synthetic_auto_purchase" and row.policy_version_id in by_id:
                _scope(session, context, row.policy_version_id)
                original = _version(by_id[row.policy_version_id])
                acquisition = "AUTHORIZED_PURCHASE"
            if row.goal_id is None:
                if not cash:
                    raise ValueError("No destination CASH account exists")
                # Acquisition bank transaction explicitly fixes the original funding account.
                from app.db.models import Transaction

                purchase = session.get(Transaction, UUID(proof.content["purchase_transaction_id"]))
                if purchase is None or purchase.account_id not in cash:
                    raise ValueError("The original cash account is required for principal return")
                destination = purchase.account_id
                if proof.content.get("acquisition_protocol") == "execution-purchase-v1":
                    from app.services.execution_sources import execution_return_account

                    destination = execution_return_account(session, user_id, row, now)
            else:
                goal = next(
                    (item for item in context.snapshot.goals if item.goal_id == row.goal_id), None
                )
                if goal is None or goal.account_id is None:
                    raise ValueError("The original goal ownership must be known")
                destination = goal.account_id
            product = _product(products[row.product_id])
            quote = _quote(context, row, product, destination)
            result.append(
                RecoveryPosition(
                    position_id=row.id,
                    account_id=row.account_id,
                    destination_account_id=destination,
                    goal_id=row.goal_id,
                    purchased_at=row.purchased_at,
                    acquisition=acquisition,
                    product=product,
                    original_authorization=original,
                    reserved_principal_cents=row.principal_cents
                    if row.status == "REDEEMING"
                    else 0,
                    quote=quote,
                    evidence_ids=[proof.id],
                )
            )
        except (KeyError, ValueError, TypeError, PolicyLifecycleError) as error:
            context.sources.issue("INVALID_RECOVERY_POSITION", row.id, str(error))
    return result, current
