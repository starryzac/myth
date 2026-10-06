"""Trusted simulator prices from original fixed-deposit terms, never browser prices."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

from app.db.audit_guard import audit_command_guard, transaction_gate
from app.db.models import AssetPosition, AssetProduct, EvidenceItem, User
from app.domain.asset_allocation_types import AssetProductTerms, FixedPrincipalTerms
from app.domain.policy_configuration import configuration_hash
from app.domain.recovery_types import RecoveryQuote
from app.services.execution_sources import execution_return_account, load_execution_quote
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.simulated_bank import validate_bank_projection
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

PROTOCOL = "simulated-fixed-early-price-v1"


def early_loss_cents(principal_cents: int, loss_bps: int) -> int:
    """Original principal loss, rounded upward to an integer cent by this simulator."""
    if (
        type(principal_cents) is not int
        or not 0 < principal_cents <= 2**63 - 1
        or type(loss_bps) is not int
        or not 0 < loss_bps <= 10_000
    ):
        raise ValueError("An early price requires positive integer principal and original loss bps")
    return (principal_cents * loss_bps + 9_999) // 10_000


def issue_fixed_early_quote(
    engine: Engine, user_id: UUID, position_id: UUID, now: datetime
) -> RecoveryQuote:
    """Publish a price only; standing authority and concrete consent are checked at execution.

    A position gets one immutable 15-minute price in this demo. Replays return the
    original price or refuse an expired/changed original; they never reprice an action.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise PolicyLifecycleError("INVALID_CLOCK", "模拟报价时钟必须带时区", 409)
    now = now.astimezone(UTC)
    with audit_command_guard(engine, user_id), Session(engine) as session, session.begin():
        transaction_gate(session, user_id)
        user = session.scalar(select(User).where(User.id == user_id).with_for_update())
        if user is None or not user.is_simulated:
            raise PolicyLifecycleError("NOT_FOUND", "模拟用户不存在", 404)
        position = session.get(AssetPosition, position_id)
        if position is None or position.user_id != user_id:
            raise PolicyLifecycleError("NOT_FOUND", "原定存仓位不存在", 404)
        try:
            if position.status != "HELD" or position.purchased_at > now:
                raise ValueError("Only known held principal can receive an early price")
            validate_bank_projection(session, user_id, now)
            destination = execution_return_account(session, user_id, position, now)
            product = session.get(AssetProduct, position.product_id)
            if product is None:
                raise ValueError("Original product is missing")
            terms = AssetProductTerms(
                **{
                    key: getattr(product, key)
                    for key in AssetProductTerms.model_fields
                    if key not in {"product_id", "terms_digest"}
                },
                product_id=product.id,
                terms_digest=configuration_hash(product.maturity_rule),
            )
            fixed = FixedPrincipalTerms.model_validate(terms.maturity_rule)
            if (
                terms.asset_class != "FIXED_DEPOSIT"
                or terms.principal_fluctuation
                or terms.risk_level != 0
                or product.created_at > position.purchased_at
                or terms.effective_from > position.purchased_at
                or (
                    terms.effective_until is not None
                    and position.purchased_at >= terms.effective_until
                )
                or fixed.term_days < terms.lock_days
                or fixed.settlement_delay_days != terms.redemption_delay_days
                or position.maturity_at != position.purchased_at + timedelta(days=fixed.term_days)
                or now >= position.maturity_at
                or product.early_withdrawal_rule
                != {
                    "allowed": True,
                    "requires_confirmation_if_loss": True,
                    "loss_basis": "principal_cents",
                    "simulation": True,
                }
            ):
                raise ValueError("An explicit original early-exit contract is required")
            loss = early_loss_cents(position.principal_cents, terms.early_withdrawal_loss_bps)
            proof_id = uuid5(position.id, PROTOCOL)
            # The deterministic ID is occupied even when changed original fields no
            # longer match the eligible-source query below. Never insert a replacement
            # for a superseded, foreign or rebound original and turn refusal into a
            # duplicate-primary-key database error.
            original = session.get(EvidenceItem, proof_id)
            if original is not None and (
                original.user_id != user_id
                or original.status != "VALID"
                or original.evidence_level != "BANK_CONFIRMED"
                or original.source_type != "SIMULATED_REDEMPTION_QUOTE"
                or original.source_ref != f"{PROTOCOL}:{position.id}"
            ):
                raise ValueError("The original simulator price identity cannot be replaced")
            existing = list(
                session.scalars(
                    select(EvidenceItem).where(
                        EvidenceItem.user_id == user_id,
                        EvidenceItem.source_type == "SIMULATED_REDEMPTION_QUOTE",
                        EvidenceItem.content["position_id"].as_string() == str(position.id),
                        EvidenceItem.status != "SUPERSEDED",
                    )
                )
            )
            if existing:
                quote = load_execution_quote(session, user_id, position.id, now)
                proof = existing[0]
                if (
                    len(existing) != 1
                    or proof.id != proof_id
                    or proof.source_ref != f"{PROTOCOL}:{position.id}"
                    or proof.content.get("price_protocol") != PROTOCOL
                    or quote.quote_id != proof_id
                    or quote.kind != "EARLY_WITHDRAW"
                    or quote.fee_cents != 0
                    or quote.loss_cents != loss
                    or quote.net_cents != position.principal_cents - loss
                    or quote.expires_at != quote.request_at + timedelta(minutes=15)
                ):
                    raise ValueError("The original simulator price cannot be replaced or rebound")
                return quote
            if original is not None:
                raise ValueError("The original simulator price no longer matches its position")
            quote = RecoveryQuote(
                quote_id=proof_id,
                user_id=user_id,
                position_id=position.id,
                product_id=product.id,
                product_version_number=terms.version_number,
                terms_digest=terms.terms_digest,
                kind="EARLY_WITHDRAW",
                principal_cents=position.principal_cents,
                fee_cents=0,
                loss_cents=loss,
                net_cents=position.principal_cents - loss,
                request_at=now,
                principal_available_at=now + timedelta(days=terms.redemption_delay_days),
                expires_at=now + timedelta(minutes=15),
                evidence_ids=[proof_id],
            )
            content = {
                "simulation": True,
                "protocol": "recovery-quote-v1",
                "price_protocol": PROTOCOL,
                "rounding": "CEIL_CENT",
                "user_id": str(user_id),
                "position_id": str(position.id),
                "destination_account_id": str(destination),
                "goal_id": str(position.goal_id) if position.goal_id else None,
                "quote": quote.model_dump(mode="json"),
            }
            session.add(
                EvidenceItem(
                    id=proof_id,
                    user_id=user_id,
                    created_at=now,
                    evidence_level="BANK_CONFIRMED",
                    source_type="SIMULATED_REDEMPTION_QUOTE",
                    source_ref=f"{PROTOCOL}:{position.id}",
                    content=content,
                    content_hash=configuration_hash(content),
                    status="VALID",
                    valid_from=now,
                    valid_to=quote.expires_at,
                    observed_at=now,
                )
            )
            session.flush()
            return load_execution_quote(session, user_id, position.id, now)
        except (TypeError, ValueError, OverflowError) as error:
            raise PolicyLifecycleError(
                "INVALID_SIMULATED_PRICE", "原定存条款或模拟报价未通过核验", 409
            ) from error
