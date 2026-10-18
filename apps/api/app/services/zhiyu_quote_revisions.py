"""Trusted append-only fixed-principal price revisions in the owned Next environment."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid5

from app.db.models import (
    ActionPlan,
    ActionReceipt,
    ActionResourceReservation,
    AssetPosition,
    AssetProduct,
    BankOperation,
    EvidenceItem,
    SimulatedBankPosting,
    SimulatedBankRedemption,
)
from app.domain.asset_allocation_types import AssetProductTerms, FixedPrincipalTerms
from app.domain.execution_types import BankCommand
from app.domain.policy_configuration import configuration_hash
from app.domain.recovery_types import RecoveryQuote
from app.domain.zhiyu_quote_revision import (
    MAX_REVISIONS,
    PRICE_V1,
    PRICE_V2,
    SOURCE_V2,
    QuoteAncestor,
    QuoteRenewalCommand,
    QuoteRenewalRequest,
    QuoteRevisionHeader,
    RevisionPricePayload,
    decode_renewal_command,
    decode_revision_payload,
    verify_lineage,
    verify_price_terms,
)
from app.services.audit_chain import verify_audit_chain
from app.services.audit_recording import audit_subject_data
from app.services.demo_console import _epoch
from app.services.execution_sources import execution_return_account
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.simulated_bank import validate_bank_projection
from app.services.simulated_redemption_quote import early_loss_cents
from app.services.zhiyu_orchestration import MARKER, _valid, serial_user, store
from app.zhiyu_next_isolation import require_zhiyu_next_engine
from sqlalchemy import or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


def refusal(code: str = "QUOTE_REVISION_INVALID") -> PolicyLifecycleError:
    return PolicyLifecycleError(code, "续报价原件、原条款或未执行边界无法核验", 409)


def _binding(session: Session, user_id: UUID, quote_id: UUID) -> dict[str, Any]:
    epoch = _epoch(session, user_id)
    row = session.get(
        EvidenceItem, uuid5(epoch, f"{MARKER}:BINDING:asset-quote-revision:{quote_id}")
    )
    if row is None:
        raise refusal()
    return _valid(session, row, user_id, epoch)["payload"]  # type: ignore[no-any-return]


def read_quote_evidence(
    session: Session, user_id: UUID, quote_id: UUID, now: datetime
) -> EvidenceItem:
    row = session.get(EvidenceItem, quote_id)
    if (
        row is None
        or row.user_id != user_id
        or row.evidence_level != "BANK_CONFIRMED"
        or row.status != "VALID"
        or row.supersedes_id is not None
        or row.created_at > now
        or row.observed_at > now
        or row.content_hash != configuration_hash(row.content)
    ):
        raise refusal()
    try:
        quote = RecoveryQuote.model_validate_json(json.dumps(row.content["quote"]))
        position_id = UUID(row.content["position_id"])
        if (
            quote.quote_id != row.id
            or quote.user_id != user_id
            or quote.position_id != position_id
            or quote.request_at != row.created_at
            or row.observed_at != row.created_at
            or row.valid_from != quote.request_at
            or row.valid_to != quote.expires_at
            or quote.expires_at != quote.request_at + timedelta(minutes=15)
        ):
            raise ValueError("Original evidence columns differ from the quoted price")
        if row.source_type == SOURCE_V2:
            payload = decode_revision_payload(row.content)
            bound = _binding(session, user_id, row.id)
            if (
                payload.revision.epoch_id != _epoch(session, user_id)
                or row.source_ref != f"{PRICE_V2}:{position_id}:{payload.revision.revision_number}"
                or set(bound) != {"protocol", "bank_evidence", "bank_authority"}
                or bound["protocol"] != PRICE_V2
                or bound["bank_authority"] is not False
                or bound["bank_evidence"] != audit_subject_data(row)
            ):
                raise ValueError("The real simulator evidence is not independently audited")
        elif (
            row.source_type != "SIMULATED_REDEMPTION_QUOTE"
            or row.source_ref != f"{PRICE_V1}:{position_id}"
            or row.id != uuid5(position_id, PRICE_V1)
            or row.content.get("price_protocol") != PRICE_V1
            or row.content.get("simulation") is not True
            or row.content.get("rounding") != "CEIL_CENT"
            or set(row.content) != set(RevisionPricePayload.model_fields) - {"revision"}
        ):
            raise ValueError("Unknown or changed original quote protocol")
    except (KeyError, ValueError, TypeError, OverflowError):
        raise refusal() from None
    return row


def read_revision_original(
    session: Session, user_id: UUID, quote_id: UUID, now: datetime
) -> tuple[RevisionPricePayload, EvidenceItem]:
    if verify_audit_chain(session, user_id, _epoch(session, user_id)).status != "VALID":
        raise refusal()
    row = read_quote_evidence(session, user_id, quote_id, now)
    if row.source_type != SOURCE_V2:
        raise refusal()
    payload = decode_revision_payload(row.content)
    originals = [
        read_quote_evidence(session, user_id, item.quote_id, now).content
        for item in payload.revision.ancestors
    ]
    try:
        verify_lineage(payload, originals)
    except (KeyError, ValueError, TypeError, OverflowError):
        raise refusal() from None
    return payload, row


def _position_terms(
    session: Session, user_id: UUID, position_id: UUID, now: datetime
) -> tuple[AssetPosition, AssetProductTerms, UUID]:
    position = session.get(AssetPosition, position_id)
    if (
        position is None
        or position.user_id != user_id
        or position.status != "HELD"
        or position.purchased_at > now
        or position.maturity_at is None
        or now >= position.maturity_at
        or position.accrued_yield_cents != 0
        or position.policy_version_id is None
    ):
        raise refusal()
    validate_bank_projection(session, user_id, now)
    destination = execution_return_account(session, user_id, position, now)
    product = session.get(AssetProduct, position.product_id)
    if product is None:
        raise refusal()
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
        product.early_withdrawal_rule
        != {
            "allowed": True,
            "requires_confirmation_if_loss": True,
            "loss_basis": "principal_cents",
            "simulation": True,
        }
        or position.maturity_at != position.purchased_at + timedelta(days=fixed.term_days)
        or product.created_at > position.purchased_at
        or terms.effective_from > position.purchased_at
        or (terms.effective_until is not None and position.purchased_at >= terms.effective_until)
    ):
        raise refusal()
    return position, terms, destination


def require_no_accepted_redemption(session: Session, user_id: UUID, position_id: UUID) -> None:
    """Caller holds the native cross-process user writer lock; absence is not recovery."""
    actions = list(
        session.scalars(
            select(ActionPlan).where(
                ActionPlan.user_id == user_id, ActionPlan.position_id == position_id
            )
        )
    )
    for action in actions:
        if action.action_type != "ASSET_REDEEM":
            continue
        try:
            command = BankCommand.model_validate_json(json.dumps(action.request["execution"]))
        except (KeyError, ValueError, TypeError):
            raise refusal() from None
        if (
            action.request_hash != configuration_hash(action.request)
            or command.effect.position_id != position_id
            or command.effect.user_id != user_id
            or command.effect.operation_id != action.id
            or action.status not in {"PLANNED", "AUTHORIZED", "CANCELLED", "INVALIDATED"}
            or session.scalar(
                select(ActionReceipt.id).where(ActionReceipt.action_plan_id == action.id)
            )
            is not None
            or session.scalar(
                select(ActionResourceReservation.id).where(
                    ActionResourceReservation.action_plan_id == action.id
                )
            )
            is not None
        ):
            raise refusal("QUOTE_RENEWAL_REQUIRES_ORIGINAL_RECOVERY")
    if (
        session.scalar(
            select(BankOperation.id).where(
                or_(
                    BankOperation.closing_position_id == position_id,
                    BankOperation.business_key == f"redeem:{position_id}",
                )
            )
        )
        is not None
        or session.scalar(
            select(SimulatedBankRedemption.id).where(
                SimulatedBankRedemption.position_id == position_id
            )
        )
        is not None
        or session.scalar(
            select(SimulatedBankPosting.id).where(
                SimulatedBankPosting.position_id == position_id,
                SimulatedBankPosting.entry_kind.in_(
                    ["REDEEM_PRINCIPAL", "REDEEM_LOSS", "REDEEM_FEE"]
                ),
            )
        )
        is not None
    ):
        raise refusal("QUOTE_RENEWAL_REQUIRES_ORIGINAL_RECOVERY")


def require_latest_quote_for_acceptance(
    session: Session, user_id: UUID, position_id: UUID, quote_id: UUID, now: datetime
) -> None:
    """A committed renewal invalidates older first acceptance, including a queued old clock.

    Existing Bank recovery never calls this function. The caller still holds the
    native user lock and all ordinary current source/financial guards remain.
    """
    rows = list(
        session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.user_id == user_id,
                EvidenceItem.source_type == SOURCE_V2,
                EvidenceItem.content["position_id"].as_string() == str(position_id),
            )
        )
    )
    if not rows:
        if quote_id != uuid5(position_id, PRICE_V1):
            raise refusal()
        return
    # Use the caller clock only after detecting any newer original. An in-flight
    # earlier caller cannot make an already committed renewal disappear.
    if any(row.created_at > now for row in rows):
        raise refusal("QUOTE_REVISION_CHANGED")
    originals = [read_revision_original(session, user_id, row.id, now)[0] for row in rows]
    originals.sort(key=lambda value: value.revision.revision_number)
    if (
        [item.revision.revision_number for item in originals] != list(range(1, len(rows) + 1))
        or len(rows) > MAX_REVISIONS
        or originals[-1].quote.quote_id != quote_id
    ):
        raise refusal("QUOTE_REVISION_CHANGED")


def load_current_revision_quote(
    session: Session, user_id: UUID, position_id: UUID, quote_id: UUID, now: datetime
) -> RecoveryQuote:
    require_zhiyu_next_engine(session.get_bind().engine)
    if verify_audit_chain(session, user_id, _epoch(session, user_id)).status != "VALID":
        raise refusal()
    payload, _ = read_revision_original(session, user_id, quote_id, now)
    require_latest_quote_for_acceptance(session, user_id, position_id, quote_id, now)
    position, terms, destination = _position_terms(session, user_id, position_id, now)
    try:
        verify_price_terms(payload, terms)
        if (
            payload.position_id != position_id
            or payload.destination_account_id != destination
            or payload.goal_id != position.goal_id
            or payload.quote.principal_cents != position.principal_cents
            or not payload.quote.request_at <= now < payload.quote.expires_at
        ):
            raise ValueError("The renewed price is stale or rebound")
    except (KeyError, ValueError, TypeError, OverflowError):
        raise refusal() from None
    return payload.quote


def renew_quote(
    engine: Engine, user_id: UUID, request: QuoteRenewalRequest, now: datetime
) -> RevisionPricePayload:
    require_zhiyu_next_engine(engine)
    if now.tzinfo is None or now.utcoffset() is None:
        raise refusal("INVALID_CLOCK")
    now = now.astimezone(UTC)
    request = QuoteRenewalRequest.model_validate_json(request.model_dump_json())
    with serial_user(engine, user_id), Session(engine) as session, session.begin():
        from app.services.execution import _lock_user

        _lock_user(session, user_id)
        epoch = _epoch(session, user_id)
        if epoch != request.expected_epoch_id:
            raise refusal("EPOCH_CONFLICT")
        if verify_audit_chain(session, user_id, epoch).status != "VALID":
            raise refusal()
        command_id = uuid5(
            epoch, f"{MARKER}:BINDING:asset-quote-renew-command:{request.client_request_id}"
        )
        command = session.get(EvidenceItem, command_id)
        if command is not None:
            original = decode_renewal_command(_valid(session, command, user_id, epoch)["payload"])
            if original.original_request != request:
                raise refusal("REQUEST_CONFLICT")
            return read_revision_original(session, user_id, original.quote_id, now)[0]
        position, terms, destination = _position_terms(session, user_id, request.position_id, now)
        require_no_accepted_redemption(session, user_id, position.id)
        previous = read_quote_evidence(session, user_id, request.previous_quote_id, now)
        old_quote = RecoveryQuote.model_validate_json(json.dumps(previous.content["quote"]))
        revisions = list(
            session.scalars(
                select(EvidenceItem).where(
                    EvidenceItem.user_id == user_id,
                    EvidenceItem.source_type == SOURCE_V2,
                    EvidenceItem.content["position_id"].as_string() == str(position.id),
                )
            )
        )
        rows = [read_revision_original(session, user_id, row.id, now)[0] for row in revisions]
        rows.sort(key=lambda item: item.revision.revision_number)
        if (
            len(rows) >= MAX_REVISIONS
            or [item.revision.revision_number for item in rows] != list(range(1, len(rows) + 1))
            or len({item.quote.quote_id for item in rows}) != len(rows)
            or request.previous_quote_id
            != (rows[-1].quote.quote_id if rows else uuid5(position.id, PRICE_V1))
            or old_quote.position_id != position.id
            or old_quote.user_id != user_id
            or configuration_hash(old_quote.model_dump(mode="json"))
            != request.reviewed_previous_quote_hash
            or now < old_quote.expires_at
        ):
            raise refusal("QUOTE_RENEWAL_STALE_OR_NOT_EXPIRED")
        ancestors = (list(rows[-1].revision.ancestors) if rows else []) + [
            QuoteAncestor(quote_id=previous.id, evidence_hash=previous.content_hash)
        ]
        quote_id = uuid5(epoch, f"{PRICE_V2}:{position.id}:{request.client_request_id}")
        if session.get(EvidenceItem, quote_id) is not None:
            raise refusal("QUOTE_REVISION_PARTIAL_NOT_FINAL")
        loss = early_loss_cents(position.principal_cents, terms.early_withdrawal_loss_bps)
        payload = RevisionPricePayload(
            user_id=user_id,
            position_id=position.id,
            destination_account_id=destination,
            goal_id=position.goal_id,
            quote=RecoveryQuote(
                quote_id=quote_id,
                user_id=user_id,
                position_id=position.id,
                product_id=position.product_id,
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
                evidence_ids=[quote_id],
            ),
            revision=QuoteRevisionHeader(
                epoch_id=epoch,
                client_request_id=request.client_request_id,
                revision_number=len(ancestors),
                ancestors=ancestors,
            ),
        )
        verify_price_terms(payload, terms)
        verify_lineage(
            payload,
            [read_quote_evidence(session, user_id, a.quote_id, now).content for a in ancestors],
        )
        row = EvidenceItem(
            id=quote_id,
            user_id=user_id,
            created_at=now,
            evidence_level="BANK_CONFIRMED",
            source_type=SOURCE_V2,
            source_ref=f"{PRICE_V2}:{position.id}:{len(ancestors)}",
            content=payload.model_dump(mode="json"),
            content_hash=configuration_hash(payload.model_dump(mode="json")),
            status="VALID",
            valid_from=now,
            valid_to=payload.quote.expires_at,
            observed_at=now,
        )
        session.add(row)
        session.flush()
        store(
            session,
            user_id,
            "BINDING",
            f"asset-quote-revision:{quote_id}",
            {
                "protocol": PRICE_V2,
                "bank_evidence": audit_subject_data(row),
                "bank_authority": False,
            },
            now,
        )
        store(
            session,
            user_id,
            "BINDING",
            f"asset-quote-renew-command:{request.client_request_id}",
            QuoteRenewalCommand(original_request=request, quote_id=quote_id).model_dump(
                mode="json"
            ),
            now,
        )
        return payload
