"""Append-only simulator price revisions; this protocol grants no bank authority."""

import json
from datetime import timedelta
from typing import Any, Final, Literal, Self
from uuid import UUID, uuid5

from app.domain.asset_allocation_types import AssetProductTerms, FixedPrincipalTerms
from app.domain.boundary_types import BoundaryModel
from app.domain.full_asset_execution import Hash
from app.domain.policy_configuration import UUIDReference, configuration_hash
from app.domain.recovery_types import RecoveryQuote
from pydantic import Field, model_validator

PRICE_V1: Final = "simulated-fixed-early-price-v1"
PRICE_V2: Final = "simulated-fixed-early-price-revision-v2"
LOSS_V2: Final = "lossy-early-redemption-quote-revision-v2"
SOURCE_V2 = "SIMULATED_REDEMPTION_QUOTE_REVISION"
MAX_REVISIONS = 16


class QuoteRenewalRequest(BoundaryModel):
    expected_epoch_id: UUIDReference
    position_id: UUIDReference
    previous_quote_id: UUIDReference
    reviewed_previous_quote_hash: Hash
    client_request_id: UUIDReference


class QuoteAncestor(BoundaryModel):
    quote_id: UUID
    evidence_hash: Hash


class QuoteRenewalCommand(BoundaryModel):
    protocol: Literal["simulated-fixed-early-price-revision-v2"] = PRICE_V2
    original_request: QuoteRenewalRequest
    quote_id: UUID
    bank_authority: Literal[False] = False

    @model_validator(mode="after")
    def exact_issued_identity(self) -> Self:
        request = self.original_request
        if self.quote_id != uuid5(
            request.expected_epoch_id,
            f"{PRICE_V2}:{request.position_id}:{request.client_request_id}",
        ):
            raise ValueError("The command must bind the exact issued quote revision")
        return self


def decode_renewal_command(raw: dict[str, Any]) -> QuoteRenewalCommand:
    if (
        set(raw) != set(QuoteRenewalCommand.model_fields)
        or raw.get("bank_authority") is not False
        or not isinstance(raw.get("original_request"), dict)
        or set(raw["original_request"]) != set(QuoteRenewalRequest.model_fields)
    ):
        raise ValueError("The complete renewal command must remain closed")
    return QuoteRenewalCommand.model_validate_json(json.dumps(raw))


class QuoteRevisionHeader(BoundaryModel):
    epoch_id: UUID
    client_request_id: UUID
    revision_number: int = Field(ge=1, le=MAX_REVISIONS)
    ancestors: list[QuoteAncestor] = Field(min_length=1, max_length=MAX_REVISIONS)

    @model_validator(mode="after")
    def ordered_originals(self) -> Self:
        if type(self.revision_number) is not int or self.revision_number != len(self.ancestors):
            raise ValueError("Revision count must match the complete ordered original lineage")
        if len({item.quote_id for item in self.ancestors}) != len(self.ancestors):
            raise ValueError("Quote lineage must not contain duplicate identities")
        return self


class RevisionPricePayload(BoundaryModel):
    protocol: Literal["recovery-quote-v1"] = "recovery-quote-v1"
    price_protocol: Literal["simulated-fixed-early-price-revision-v2"] = PRICE_V2
    simulation: Literal[True] = True
    rounding: Literal["CEIL_CENT"] = "CEIL_CENT"
    user_id: UUID
    position_id: UUID
    destination_account_id: UUID
    goal_id: UUID | None
    quote: RecoveryQuote
    revision: QuoteRevisionHeader

    @model_validator(mode="after")
    def identity_and_time(self) -> Self:
        quote = self.quote
        if (
            quote.user_id != self.user_id
            or quote.position_id != self.position_id
            or quote.quote_id
            != uuid5(
                self.revision.epoch_id,
                f"{PRICE_V2}:{self.position_id}:{self.revision.client_request_id}",
            )
            or quote.quote_id in {item.quote_id for item in self.revision.ancestors}
            or self.revision.ancestors[0].quote_id != uuid5(self.position_id, PRICE_V1)
            or quote.evidence_ids != [quote.quote_id]
            or quote.expires_at != quote.request_at + timedelta(minutes=15)
            or quote.kind != "EARLY_WITHDRAW"
            or quote.fee_cents != 0
            or quote.loss_cents <= 0
            or quote.net_cents != quote.principal_cents - quote.loss_cents
        ):
            raise ValueError("A revision must retain the exact owner and principal-only price")
        return self


def decode_revision_payload(raw: dict[str, Any]) -> RevisionPricePayload:
    if (
        set(raw) != set(RevisionPricePayload.model_fields)
        or raw.get("simulation") is not True
        or not isinstance(raw.get("revision"), dict)
        or set(raw["revision"]) != set(QuoteRevisionHeader.model_fields)
        or type(raw["revision"].get("revision_number")) is not int
        or not isinstance(raw["revision"].get("ancestors"), list)
        or any(
            not isinstance(item, dict) or set(item) != set(QuoteAncestor.model_fields)
            for item in raw["revision"]["ancestors"]
        )
        or not isinstance(raw.get("quote"), dict)
        or set(raw["quote"]) != set(RecoveryQuote.model_fields)
    ):
        raise ValueError("The complete immutable revision fields must be present")
    return RevisionPricePayload.model_validate_json(json.dumps(raw))


def verify_price_terms(payload: RevisionPricePayload, terms: AssetProductTerms) -> None:
    fixed = FixedPrincipalTerms.model_validate(terms.maturity_rule)
    quote = payload.quote
    principal = quote.principal_cents
    loss = (principal * terms.early_withdrawal_loss_bps + 9_999) // 10_000
    if (
        type(principal) is not int
        or principal <= 0
        or not 0 < terms.early_withdrawal_loss_bps <= 10_000
        or terms.asset_class != "FIXED_DEPOSIT"
        or terms.principal_fluctuation
        or terms.risk_level != 0
        or fixed.term_days < terms.lock_days
        or fixed.settlement_delay_days != terms.redemption_delay_days
        or quote.product_id != terms.product_id
        or quote.product_version_number != terms.version_number
        or quote.terms_digest != terms.terms_digest
        or quote.loss_cents != loss
        or quote.principal_available_at
        != quote.request_at + timedelta(days=terms.redemption_delay_days)
    ):
        raise ValueError("The renewed price must use the complete immutable purchase terms")


def verify_lineage(
    payload: RevisionPricePayload, originals: list[dict[str, Any]]
) -> list[RecoveryQuote]:
    """Verify captured real evidence contents; references cannot authenticate themselves."""
    if len(originals) != payload.revision.revision_number:
        raise ValueError("Every ordered original quote must be independently captured")
    previous_quotes: list[RecoveryQuote] = []
    for index, (reference, original) in enumerate(
        zip(payload.revision.ancestors, originals, strict=True)
    ):
        if configuration_hash(original) != reference.evidence_hash:
            raise ValueError("Captured quote original differs from its independent evidence hash")
        if index == 0:
            expected = set(RevisionPricePayload.model_fields) - {"revision"}
            if (
                set(original) != expected
                or original.get("price_protocol") != PRICE_V1
                or original.get("protocol") != "recovery-quote-v1"
                or original.get("simulation") is not True
                or original.get("rounding") != "CEIL_CENT"
            ):
                raise ValueError("The first lineage element must be the immutable v1 price")
            quote = RecoveryQuote.model_validate_json(json.dumps(original["quote"]))
        else:
            ancestor = decode_revision_payload(original)
            if (
                ancestor.revision.epoch_id != payload.revision.epoch_id
                or ancestor.revision.revision_number != index
                or ancestor.revision.ancestors != payload.revision.ancestors[:index]
            ):
                raise ValueError("Revision ancestry cannot fork or omit an earlier original")
            quote = ancestor.quote
        if (
            quote.quote_id != reference.quote_id
            or quote.user_id != payload.user_id
            or quote.position_id != payload.position_id
            or original.get("user_id") != str(payload.user_id)
            or original.get("position_id") != str(payload.position_id)
            or original.get("destination_account_id") != str(payload.destination_account_id)
            or original.get("goal_id") != (str(payload.goal_id) if payload.goal_id else None)
            or quote.evidence_ids != [quote.quote_id]
            or quote.expires_at != quote.request_at + timedelta(minutes=15)
            or quote.expires_at > payload.quote.request_at
            or quote.kind != payload.quote.kind
            or (
                quote.product_id,
                quote.product_version_number,
                quote.terms_digest,
                quote.principal_cents,
                quote.fee_cents,
                quote.loss_cents,
                quote.net_cents,
            )
            != (
                payload.quote.product_id,
                payload.quote.product_version_number,
                payload.quote.terms_digest,
                payload.quote.principal_cents,
                payload.quote.fee_cents,
                payload.quote.loss_cents,
                payload.quote.net_cents,
            )
            or (previous_quotes and previous_quotes[-1].expires_at > quote.request_at)
        ):
            raise ValueError("A revision cannot replace ownership, terms, costs or chronology")
        previous_quotes.append(quote)
    return previous_quotes


class RevisionLossPrepareRequest(BoundaryModel):
    protocol: Literal["lossy-early-redemption-quote-revision-v2"] = LOSS_V2
    expected_epoch_id: UUIDReference
    position_id: UUIDReference
    quote_id: UUIDReference
    client_request_id: UUIDReference
    native_key: str

    @model_validator(mode="after")
    def closed_native_key(self) -> Self:
        if self.native_key != (
            f"zhiyu-next:{self.expected_epoch_id}:lossy-revision:{self.client_request_id}"
        ):
            raise ValueError("The renewed native key must bind the exact epoch and request UUID")
        return self
