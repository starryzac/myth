"""Income origins retain bank identity while their unspent cash changes location."""

from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID, uuid5

from app.domain.policy_configuration import MoneyCents
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

LEDGER_SOURCE = "SIMULATED_NEW_FUNDS_LEDGER"
LEDGER_PROTOCOL_V2 = "new-funds-ledger-v2"
IncomeOperation = Literal["TRANSFER_INTERNAL", "ALLOCATE_GOAL", "SPEND"]


class LedgerModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class LedgerLot(LedgerModel):
    transaction_id: UUID
    account_id: UUID
    bank_evidence_id: UUID
    bank_evidence_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    original_cents: MoneyCents
    prior_unspent_cents: MoneyCents
    spent_cents: MoneyCents
    assigned_cents: MoneyCents
    reserved_cents: MoneyCents
    available_cents: MoneyCents

    @model_validator(mode="after")
    def conservation(self) -> Self:
        if (
            self.original_cents
            != self.spent_cents + self.assigned_cents + self.reserved_cents + self.available_cents
            or self.prior_unspent_cents != self.reserved_cents + self.available_cents
        ):
            raise ValueError("The original income and all consumed or reserved parts must conserve")
        return self


class LedgerPayload(LedgerModel):
    simulation: Literal[True]
    protocol: Literal["new-funds-ledger-v1"]
    user_id: UUID
    complete: Literal[True]
    as_of: AwareDatetime
    scope_account_ids: Annotated[list[UUID], Field(max_length=100)]
    lots: Annotated[list[LedgerLot], Field(max_length=100000)]


def location_id(origin_transaction_id: UUID, account_id: UUID) -> UUID:
    return uuid5(origin_transaction_id, "income-location:" + str(account_id))


class IncomeOrigin(LedgerModel):
    origin_transaction_id: UUID
    origin_account_id: UUID
    amount_cents: Annotated[MoneyCents, Field(gt=0)]
    occurred_at: AwareDatetime
    observed_at: AwareDatetime
    bank_evidence_id: UUID
    bank_evidence_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class IncomeUse(LedgerModel):
    fragment_id: UUID
    origin_transaction_id: UUID
    account_id: UUID
    amount_cents: Annotated[MoneyCents, Field(gt=0)]


class IncomeFragment(LedgerModel):
    fragment_id: UUID
    origin_transaction_id: UUID
    account_id: UUID
    spent_cents: MoneyCents = 0
    assigned_cents: MoneyCents = 0
    reserved_cents: MoneyCents = 0
    legacy_reserved_cents: MoneyCents = 0
    available_cents: MoneyCents = 0


class IncomeReservation(LedgerModel):
    action_id: UUID
    operation: IncomeOperation
    destination_account_id: UUID | None = None
    uses: tuple[IncomeUse, ...]
    state: Literal["RESERVED", "COMMITTED", "RELEASED"] = "RESERVED"


class IncomeLedger(LedgerModel):
    simulation: Literal[True] = True
    protocol: Literal["new-funds-ledger-v2"] = "new-funds-ledger-v2"
    user_id: UUID
    complete: Literal[True] = True
    as_of: AwareDatetime
    scope_account_ids: tuple[UUID, ...]
    origins: tuple[IncomeOrigin, ...]
    fragments: tuple[IncomeFragment, ...]
    reservations: tuple[IncomeReservation, ...] = ()

    @model_validator(mode="after")
    def conserved_locations(self) -> Self:
        origins = {item.origin_transaction_id: item for item in self.origins}
        fragments = {item.fragment_id: item for item in self.fragments}
        if (
            len(origins) != len(self.origins)
            or len(fragments) != len(self.fragments)
            or len(set(self.scope_account_ids)) != len(self.scope_account_ids)
            or len({item.action_id for item in self.reservations}) != len(self.reservations)
            or len(origins) > 100000
            or len(fragments) > 100000
            or len(self.reservations) > 100000
            or not 1 <= len(self.scope_account_ids) <= 100
        ):
            raise ValueError("Income ledger identities must be unique and within capacity")
        if any(
            origin.origin_account_id not in self.scope_account_ids
            or not origin.occurred_at <= origin.observed_at <= self.as_of
            for origin in self.origins
        ):
            raise ValueError("An origin requires an included original account and known bank time")
        totals: dict[UUID, int] = {}
        reserved: dict[UUID, int] = {}
        for item in self.fragments:
            if (
                item.origin_transaction_id not in origins
                or item.account_id not in self.scope_account_ids
                or item.fragment_id != location_id(item.origin_transaction_id, item.account_id)
                or item.legacy_reserved_cents > item.reserved_cents
            ):
                raise ValueError("Income fragment identity, scope or legacy reservation is invalid")
            totals[item.origin_transaction_id] = totals.get(item.origin_transaction_id, 0) + (
                item.spent_cents + item.assigned_cents + item.reserved_cents + item.available_cents
            )
        if totals != {identity: item.amount_cents for identity, item in origins.items()}:
            raise ValueError(
                "Origin amounts must equal all spent, assigned, reserved and available locations"
            )
        for command in self.reservations:
            if not command.uses or len({use.fragment_id for use in command.uses}) != len(
                command.uses
            ):
                raise ValueError("Action source uses must be nonempty and unique")
            if command.operation == "TRANSFER_INTERNAL":
                if command.destination_account_id not in self.scope_account_ids or any(
                    use.account_id == command.destination_account_id for use in command.uses
                ):
                    raise ValueError("Transfer destinations must be a different included account")
            elif command.destination_account_id is not None:
                raise ValueError("Consumption does not relabel an available income location")
            for use in command.uses:
                fragment = fragments.get(use.fragment_id)
                if fragment is None or (fragment.origin_transaction_id, fragment.account_id) != (
                    use.origin_transaction_id,
                    use.account_id,
                ):
                    raise ValueError("Action uses must refer to the exact origin fragment")
                if command.state == "RESERVED":
                    reserved[use.fragment_id] = reserved.get(use.fragment_id, 0) + use.amount_cents
        if any(
            item.reserved_cents != item.legacy_reserved_cents + reserved.get(item.fragment_id, 0)
            for item in self.fragments
        ):
            raise ValueError("Reserved income must retain every legacy and current action claim")
        return self


def reserve_income(
    ledger: IncomeLedger,
    action_id: UUID,
    uses: Sequence[IncomeUse],
    operation: IncomeOperation,
    now: datetime,
    *,
    destination_account_id: UUID | None = None,
) -> IncomeLedger:
    ledger = _validated(ledger, now)
    command = IncomeReservation(
        action_id=action_id,
        operation=operation,
        destination_account_id=destination_account_id,
        uses=tuple(sorted(uses, key=lambda item: item.fragment_id)),
    )
    existing = next((item for item in ledger.reservations if item.action_id == action_id), None)
    if existing is not None:
        if existing.model_copy(update={"state": "RESERVED"}) != command:
            raise ValueError("An action cannot change its reserved income command")
        return ledger
    by_id = {item.fragment_id: item for item in ledger.fragments}
    if not uses or len({item.fragment_id for item in uses}) != len(uses):
        raise ValueError("An income reservation needs unique nonempty fragment uses")
    if operation == "TRANSFER_INTERNAL" and (
        destination_account_id not in ledger.scope_account_ids
        or any(item.account_id == destination_account_id for item in uses)
    ):
        raise ValueError("A transfer needs a different included CASH destination")
    if operation != "TRANSFER_INTERNAL" and destination_account_id is not None:
        raise ValueError("Only an internal transfer changes available income location")
    for use in command.uses:
        fragment = by_id.get(use.fragment_id)
        if (
            fragment is None
            or fragment.origin_transaction_id != use.origin_transaction_id
            or (
                fragment.account_id != use.account_id or fragment.available_cents < use.amount_cents
            )
        ):
            raise ValueError("Reserved uses must fit their exact available source fragments")
        by_id[use.fragment_id] = fragment.model_copy(
            update={
                "available_cents": fragment.available_cents - use.amount_cents,
                "reserved_cents": fragment.reserved_cents + use.amount_cents,
            }
        )
    return _changed(ledger, now, tuple(by_id.values()), (*ledger.reservations, command))


def commit_income(ledger: IncomeLedger, action_id: UUID, now: datetime) -> IncomeLedger:
    ledger = _validated(ledger, now)
    reservation = next((item for item in ledger.reservations if item.action_id == action_id), None)
    if reservation is None or reservation.state == "RELEASED":
        raise ValueError("Only an existing reserved income action can commit")
    if reservation.state == "COMMITTED":
        return ledger
    by_id = {item.fragment_id: item for item in ledger.fragments}
    for use in reservation.uses:
        fragment = by_id[use.fragment_id]
        changes = {"reserved_cents": fragment.reserved_cents - use.amount_cents}
        if reservation.operation == "TRANSFER_INTERNAL":
            destination = reservation.destination_account_id
            assert destination is not None
            identity = location_id(use.origin_transaction_id, destination)
            target = by_id.get(identity) or IncomeFragment(
                fragment_id=identity,
                origin_transaction_id=use.origin_transaction_id,
                account_id=destination,
            )
            by_id[identity] = target.model_copy(
                update={"available_cents": target.available_cents + use.amount_cents}
            )
        else:
            field = "assigned_cents" if reservation.operation == "ALLOCATE_GOAL" else "spent_cents"
            changes[field] = getattr(fragment, field) + use.amount_cents
        by_id[use.fragment_id] = fragment.model_copy(update=changes)
    reservations = tuple(
        item.model_copy(update={"state": "COMMITTED"}) if item.action_id == action_id else item
        for item in ledger.reservations
    )
    return _changed(ledger, now, tuple(by_id.values()), reservations)


def release_income(
    ledger: IncomeLedger,
    action_id: UUID,
    now: datetime,
    *,
    confirmed_no_effect: bool,
) -> IncomeLedger:
    ledger = _validated(ledger, now)
    if confirmed_no_effect is not True:
        raise ValueError("Unknown effects cannot release reserved income")
    command = next((item for item in ledger.reservations if item.action_id == action_id), None)
    if command is None or command.state == "COMMITTED":
        raise ValueError("Only a proved unexecuted income command may be released")
    if command.state == "RELEASED":
        return ledger
    uses = {item.fragment_id: item.amount_cents for item in command.uses}
    fragments = tuple(
        item.model_copy(
            update={
                "reserved_cents": item.reserved_cents - uses.get(item.fragment_id, 0),
                "available_cents": item.available_cents + uses.get(item.fragment_id, 0),
            }
        )
        for item in ledger.fragments
    )
    commands = tuple(
        item.model_copy(update={"state": "RELEASED"}) if item.action_id == action_id else item
        for item in ledger.reservations
    )
    return _changed(ledger, now, fragments, commands)


def bank_location_snapshot(ledger: IncomeLedger) -> dict[str, Any]:
    """Canonical import/verification facts; the caller must prove their trusted origin."""
    ledger = IncomeLedger.model_validate(ledger.model_dump())
    return {
        "protocol": "income-location-bank-v1",
        "user_id": str(ledger.user_id),
        "origins": [
            item.model_dump(mode="json")
            for item in sorted(ledger.origins, key=lambda item: item.origin_transaction_id)
        ],
        "locations": [
            {
                **item.model_dump(mode="json"),
                "active_reserved_cents": item.reserved_cents - item.legacy_reserved_cents,
            }
            for item in sorted(ledger.fragments, key=lambda item: item.fragment_id)
        ],
    }


def _validated(ledger: IncomeLedger, now: datetime) -> IncomeLedger:
    result = IncomeLedger.model_validate(ledger.model_dump())
    if now.tzinfo is None or now.utcoffset() is None or now < result.as_of:
        raise ValueError("Income ledger clocks must be aware and cannot move backwards")
    return result


def _changed(
    ledger: IncomeLedger,
    now: datetime,
    fragments: tuple[IncomeFragment, ...],
    reservations: tuple[IncomeReservation, ...],
) -> IncomeLedger:
    return IncomeLedger.model_validate(
        {
            **ledger.model_dump(),
            "as_of": now,
            "fragments": tuple(
                item.model_dump() for item in sorted(fragments, key=lambda item: item.fragment_id)
            ),
            "reservations": tuple(
                item.model_dump() for item in sorted(reservations, key=lambda item: item.action_id)
            ),
        }
    )
