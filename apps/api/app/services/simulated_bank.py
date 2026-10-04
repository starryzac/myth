"""Independent simulated bank ledger; application projections are never its balance source."""

from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from app.db.models import (
    Account,
    ActionPlan,
    ActionReceipt,
    ActionResourceReservation,
    AssetPosition,
    AssetProduct,
    BankOperation,
    EvidenceItem,
    SimulatedBankPosting,
    SimulatedBankRedemption,
    User,
)
from app.domain.asset_allocation_types import FixedPrincipalTerms
from app.domain.asset_exposure import EXPOSURE_SOURCE, asset_exposure_snapshot
from app.domain.policy_configuration import configuration_hash
from app.services.policy_lifecycle import PolicyLifecycleError, is_version_authorized
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


class BankRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    user_id: UUID
    position_id: UUID
    position_account_id: UUID
    product_id: UUID
    goal_id: UUID | None
    original_policy_version_id: UUID | None = None
    destination_account_id: UUID
    principal_cents: StrictInt = Field(gt=0)
    requested_at: AwareDatetime
    available_at: AwareDatetime
    expires_at: AwareDatetime
    kind: Literal["REDEEM", "MATURE"] = "REDEEM"


class BankResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    request_id: UUID
    action_id: UUID
    status: Literal["ACCEPTED", "SETTLED", "UNKNOWN"]
    posting_ids: list[UUID]


def _error(message: str) -> PolicyLifecycleError:
    return PolicyLifecycleError("BANK_RECONCILIATION_REQUIRED", message, 409)


def open_simulated_bank(
    session: Session,
    user_id: UUID,
    as_of: datetime,
    *,
    cash_balances: dict[UUID, int],
    position_principals: dict[UUID, int],
) -> list[UUID]:
    """Install explicit trusted seed anchors once; never use this as live reconciliation."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise _error("Bank opening requires an aware timestamp")
    as_of = as_of.astimezone(UTC)
    user = session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None or not user.is_simulated:
        raise _error("Only an existing simulated user can open the simulated bank")
    specs: list[tuple[str, UUID | None, UUID | None, int]] = [
        (f"CASH:{key}", key, None, value) for key, value in cash_balances.items()
    ]
    specs += [(f"POSITION:{key}", None, key, value) for key, value in position_principals.items()]
    results: list[UUID] = []
    for ledger_key, account_id, position_id, amount in sorted(specs):
        if type(amount) is not int or amount < 0:
            raise _error("Opening balances must be nonnegative integer cents")
        if account_id is not None:
            account = session.get(Account, account_id)
            if (
                account is None
                or account.user_id != user_id
                or account.account_type == "CREDIT_CARD"
            ):
                raise _error("Invalid simulated cash ledger owner")
        else:
            position = session.get(AssetPosition, position_id)
            if position is None or position.user_id != user_id:
                raise _error("Invalid simulated position ledger owner")
        identity = uuid5(NAMESPACE_URL, f"bounded-funds:bank-opening:{user_id}:{ledger_key}")
        existing = session.get(SimulatedBankPosting, identity)
        if existing is not None:
            if (
                existing.ledger_key != ledger_key
                or existing.delta_cents != amount
                or existing.occurred_at != as_of
                or existing.entry_kind != "OPENING"
            ):
                raise _error("An existing opening cannot be replaced")
        else:
            session.add(
                SimulatedBankPosting(
                    id=identity,
                    user_id=user_id,
                    created_at=as_of,
                    ledger_key=ledger_key,
                    account_id=account_id,
                    position_id=position_id,
                    redemption_id=None,
                    previous_posting_id=None,
                    sequence_number=1,
                    entry_kind="OPENING",
                    balance_before_cents=0,
                    delta_cents=amount,
                    balance_after_cents=amount,
                    occurred_at=as_of,
                )
            )
        results.append(identity)
    session.flush()
    return results


def ledger_heads(session: Session, user_id: UUID) -> dict[str, SimulatedBankPosting]:
    """Verify each economic chain from its independent opening, not an application balance."""
    heads: dict[str, SimulatedBankPosting] = {}
    rows = session.scalars(
        select(SimulatedBankPosting)
        .where(SimulatedBankPosting.user_id == user_id)
        .order_by(SimulatedBankPosting.ledger_key, SimulatedBankPosting.sequence_number)
    )
    for row in rows:
        previous = heads.get(row.ledger_key)
        if previous is None:
            if (
                row.sequence_number != 1
                or row.entry_kind != "OPENING"
                or row.previous_posting_id is not None
                or row.balance_before_cents != 0
            ):
                raise _error("A bank ledger has no valid opening")
        elif (
            row.previous_posting_id != previous.id
            or row.sequence_number != previous.sequence_number + 1
            or row.balance_before_cents != previous.balance_after_cents
            or row.occurred_at < previous.occurred_at
        ):
            raise _error("A bank ledger economic chain is inconsistent")
        if row.balance_after_cents != row.balance_before_cents + row.delta_cents:
            raise _error("A bank posting violates conservation")
        heads[row.ledger_key] = row
    return heads


def validate_bank_projection(
    session: Session,
    user_id: UUID,
    now: datetime,
    *,
    allow_unprojected: bool = False,
) -> None:
    heads = {
        key: row
        for key, row in ledger_heads(session, user_id).items()
        if key.startswith(("CASH:", "POSITION:"))
    }
    accounts = list(session.scalars(select(Account).where(Account.user_id == user_id)))
    positions = list(session.scalars(select(AssetPosition).where(AssetPosition.user_id == user_id)))
    expected = {
        f"CASH:{row.id}": row.balance_cents for row in accounts if row.account_type != "CREDIT_CARD"
    }
    expected.update(
        {
            f"POSITION:{row.id}": 0 if row.status == "REDEEMED" else row.principal_cents
            for row in positions
        }
    )
    if allow_unprojected:
        for request in session.scalars(
            select(SimulatedBankRedemption).where(
                SimulatedBankRedemption.user_id == user_id,
                SimulatedBankRedemption.status == "SETTLED",
            )
        ):
            receipt = session.scalar(
                select(ActionReceipt).where(ActionReceipt.action_plan_id == request.action_plan_id)
            )
            if receipt is None:
                cash_key, position_key = (
                    f"CASH:{request.destination_account_id}",
                    f"POSITION:{request.position_id}",
                )
                if cash_key not in expected or position_key not in expected:
                    raise _error("An unprojected bank operation lost its economic identity")
                expected[cash_key] += request.principal_cents
                expected[position_key] -= request.principal_cents
        for operation in session.scalars(
            select(BankOperation).where(
                BankOperation.user_id == user_id,
                BankOperation.legacy_redemption_id.is_(None),
                BankOperation.status == "SETTLED",
            )
        ):
            receipt = session.scalar(
                select(ActionReceipt).where(
                    ActionReceipt.action_plan_id == operation.action_plan_id
                )
            )
            if receipt is None:
                for posting in session.scalars(
                    select(SimulatedBankPosting).where(
                        SimulatedBankPosting.operation_id == operation.id
                    )
                ):
                    if posting.ledger_key.startswith(("CASH:", "POSITION:")):
                        if posting.ledger_key not in expected:
                            if (
                                operation.operation_type != "PURCHASE_ASSET"
                                or not posting.ledger_key.startswith("POSITION:")
                            ):
                                raise _error(
                                    "An unprojected operation lost an original economic identity"
                                )
                            expected[posting.ledger_key] = 0
                        expected[posting.ledger_key] += posting.delta_cents
    if set(expected) != set(heads):
        raise _error("Independent bank ledger identities differ from application projections")
    if any(
        heads[key].balance_after_cents != amount or heads[key].occurred_at > now
        for key, amount in expected.items()
    ):
        raise _error("Application cash or principal disagrees with independent bank postings")


def validate_recovery_exposure(session: Session, user_id: UUID, now: datetime) -> None:
    """After recovery starts, every financial adapter requires the same complete bank v2 facts."""
    requests = list(
        session.scalars(
            select(SimulatedBankRedemption).where(SimulatedBankRedemption.user_id == user_id)
        )
    )
    evidence = list(session.scalars(select(EvidenceItem).where(EvidenceItem.user_id == user_id)))
    operations = list(
        session.scalars(select(BankOperation).where(BankOperation.user_id == user_id))
    )
    statements = [
        row for row in evidence if row.source_type == EXPOSURE_SOURCE and row.status != "SUPERSEDED"
    ]
    if (
        not requests
        and not operations
        and not any(
            row.content.get("protocol") in {"asset-exposure-v2", "asset-exposure-v3"}
            for row in statements
        )
    ):
        return
    if len(statements) != 1:
        raise _error("Recovery requires one current complete bank exposure statement")
    proof = statements[0]
    try:
        epoch = datetime.fromisoformat(proof.content["as_of"])
        if (
            epoch.tzinfo is None
            or epoch > now
            or proof.observed_at > now
            or proof.observed_at < epoch
            or proof.valid_from > now
            or (proof.valid_to is not None and proof.valid_to <= now)
            or proof.status != "VALID"
            or proof.evidence_level != "BANK_CONFIRMED"
            or proof.content_hash != configuration_hash(proof.content)
        ):
            raise ValueError("The recovery exposure is invalid or unknown")
        postings = list(
            session.scalars(
                select(SimulatedBankPosting).where(SimulatedBankPosting.user_id == user_id)
            )
        )
        extra: dict[str, Any] = {}
        if proof.content.get("protocol") == "asset-exposure-v3":
            extra = {
                "bank_operations": operations,
                "resource_reservations": list(
                    session.scalars(
                        select(ActionResourceReservation).where(
                            ActionResourceReservation.user_id == user_id
                        )
                    )
                ),
            }
        elif any(row.legacy_redemption_id is None for row in operations):
            raise ValueError("Generic bank effects require complete v3 exposure")
        if (
            any(row.occurred_at > epoch for row in postings)
            or any(row.created_at > epoch for row in requests)
            or any(row.created_at > epoch for row in operations)
        ):
            raise ValueError("Recovery exposure predates an independent bank fact")
        expected = asset_exposure_snapshot(
            user_id,
            epoch,
            accounts=session.scalars(select(Account).where(Account.user_id == user_id)),
            positions=session.scalars(
                select(AssetPosition).where(AssetPosition.user_id == user_id)
            ),
            actions=session.scalars(select(ActionPlan).where(ActionPlan.user_id == user_id)),
            receipts=session.scalars(select(ActionReceipt).where(ActionReceipt.user_id == user_id)),
            evidence=evidence,
            settlements=proof.content["settlements"],
            bank_requests=requests,
            bank_postings=postings,
            **extra,
        )
        if configuration_hash(expected) != proof.content_hash:
            raise ValueError(
                "The complete recovery exposure differs from current bank or application facts"
            )
        validate_bank_projection(session, user_id, now)
    except (KeyError, TypeError, ValueError) as error:
        raise _error(str(error)) from error


def require_settlement_order(
    session: Session, user_id: UUID, now: datetime, operation: BankOperation | None = None
) -> None:
    """Do not append newer economics while earlier accepted bank events need reconciliation."""
    query = select(BankOperation.id).where(
        BankOperation.user_id == user_id,
        BankOperation.status == "ACCEPTED",
        BankOperation.available_at <= now,
    )
    if operation is not None:
        query = query.where(
            BankOperation.id != operation.id,
            BankOperation.available_at < operation.available_at,
        )
    if session.scalar(query.limit(1)) is not None:
        raise _error(
            "Earlier accepted bank settlement requires reconciliation before new economics"
        )


def _settle(session: Session, request: SimulatedBankRedemption, now: datetime) -> None:
    if request.status != "ACCEPTED" or request.available_at > now:
        return
    heads = ledger_heads(session, request.user_id)
    position_key = f"POSITION:{request.position_id}"
    cash_key = f"CASH:{request.destination_account_id}"
    if position_key not in heads or cash_key not in heads:
        raise _error("A bank request lacks its original ledger anchors")
    if heads[position_key].balance_after_cents != request.principal_cents:
        raise _error("The complete position principal is no longer available")
    if any(heads[key].occurred_at > request.available_at for key in (position_key, cash_key)):
        raise _error("A promised settlement cannot be inserted behind newer bank economics")
    for key, kind, delta in (
        (position_key, "PRINCIPAL_DEBIT", -request.principal_cents),
        (cash_key, "CASH_CREDIT", request.principal_cents),
    ):
        head = heads[key]
        session.add(
            SimulatedBankPosting(
                id=uuid5(request.id, kind),
                user_id=request.user_id,
                created_at=now,
                ledger_key=key,
                account_id=head.account_id,
                position_id=head.position_id,
                redemption_id=request.id,
                operation_id=request.id,
                leg_ref=kind,
                previous_posting_id=head.id,
                sequence_number=head.sequence_number + 1,
                entry_kind=kind,
                balance_before_cents=head.balance_after_cents,
                delta_cents=delta,
                balance_after_cents=head.balance_after_cents + delta,
                occurred_at=request.available_at,
            )
        )
    request.status = "SETTLED"
    request.settled_at = request.available_at
    session.flush()


def process_redemption(engine: Engine, user_id: UUID, action_id: UUID, now: datetime) -> BankResult:
    """Bank phase: owns and commits its transaction after the application request committed."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise _error("The simulated bank requires a trusted aware clock")
    now = now.astimezone(UTC)
    with Session(engine) as session, session.begin():
        user = session.scalar(select(User).where(User.id == user_id).with_for_update())
        if user is None or not user.is_simulated:
            raise _error("Unknown simulated bank owner")
        action = session.scalar(
            select(ActionPlan).where(ActionPlan.id == action_id, ActionPlan.user_id == user_id)
        )
        if action is None or action.action_type not in {"ASSET_REDEEM", "ASSET_MATURITY"}:
            raise _error("A committed recovery request is required")
        if configuration_hash(action.request) != action.request_hash:
            raise _error("The committed action request is corrupt")
        try:
            command = BankRequest.model_validate(action.request["bank_request"])
        except (KeyError, TypeError, ValueError) as error:
            raise _error("Invalid bank economic payload") from error
        payload = command.model_dump(mode="json")
        payload_hash = configuration_hash(payload)
        request = session.scalar(
            select(SimulatedBankRedemption).where(
                SimulatedBankRedemption.user_id == user_id,
                SimulatedBankRedemption.idempotency_key == action.idempotency_key,
            )
        )
        if request is not None:
            if request.action_plan_id != action_id or request.request_hash != payload_hash:
                raise _error("The original bank idempotency key cannot change economic content")
        else:
            require_settlement_order(session, user_id, now)
            reserved = session.scalar(
                select(ActionResourceReservation.id).where(
                    ActionResourceReservation.user_id == user_id,
                    ActionResourceReservation.resource_kind == "POSITION",
                    ActionResourceReservation.resource_key == str(command.position_id),
                    ActionResourceReservation.action_plan_id != action_id,
                    ActionResourceReservation.status == "RESERVED",
                )
            )
            if reserved is not None:
                raise _error("This position is reserved by another committed application action")
            unified = session.scalar(
                select(BankOperation).where(
                    BankOperation.closing_position_id == command.position_id,
                    BankOperation.status != "REJECTED",
                )
            )
            if unified is not None:
                raise _error("This position already has a bank closing operation")
            collision = session.scalar(
                select(SimulatedBankRedemption).where(
                    SimulatedBankRedemption.position_id == command.position_id
                )
            )
            if collision is not None:
                raise _error("This position already has a bank redemption request")
            position = session.get(AssetPosition, command.position_id)
            destination = session.get(Account, command.destination_account_id)
            contract = False
            if (
                position is not None
                and command.kind == "MATURE"
                and action.action_type == "ASSET_MATURITY"
            ):
                product = session.get(AssetProduct, position.product_id)
                try:
                    if product is None:
                        raise ValueError("Missing contract product")
                    terms = FixedPrincipalTerms.model_validate(product.maturity_rule)
                    maturity = position.purchased_at + timedelta(days=terms.term_days)
                    contract = (
                        product.created_at <= now
                        and product.effective_from <= position.purchased_at
                        and (
                            product.effective_until is None
                            or product.effective_until > position.purchased_at
                        )
                        and terms.term_days >= product.lock_days
                        and terms.settlement_delay_days == product.redemption_delay_days
                        and position.maturity_at == maturity
                        and now >= maturity + timedelta(days=terms.settlement_delay_days)
                        and action.request.get("contract_terms_digest")
                        == configuration_hash(product.maturity_rule)
                        and command.available_at == command.requested_at
                    )
                except (ValueError, TypeError, OverflowError):
                    contract = False
            if (
                command.user_id != user_id
                or position is None
                or position.user_id != user_id
                or command.position_id != action.position_id
                or command.position_account_id != position.account_id
                or action.source_account_id != position.account_id
                or command.product_id != position.product_id
                or action.product_id != position.product_id
                or command.goal_id != position.goal_id
                or command.original_policy_version_id != position.policy_version_id
                or action.goal_id != position.goal_id
                or command.principal_cents != position.principal_cents
                or action.amount_cents != command.principal_cents
                or destination is None
                or destination.user_id != user_id
                or destination.account_type not in {"CASH", "GOAL"}
                or command.requested_at > now
                or command.expires_at <= now
                or command.available_at < command.requested_at
                or action.status != "SUBMITTED"
                or action.autonomy_level != "AUTO_EXECUTE"
                or (
                    not contract
                    and (
                        command.kind != "REDEEM"
                        or action.policy_version_id is None
                        or not is_version_authorized(
                            session, user_id, action.policy_version_id, now
                        )
                    )
                )
            ):
                raise _error(
                    "The new bank request is not a currently authorized whole-position operation"
                )
            heads = ledger_heads(session, user_id)
            validate_bank_projection(session, user_id, now, allow_unprojected=True)
            if (
                f"CASH:{destination.id}" not in heads
                or f"POSITION:{position.id}" not in heads
                or heads[f"POSITION:{position.id}"].balance_after_cents != command.principal_cents
            ):
                raise _error("The independent bank cannot account for the requested principal")
            request = SimulatedBankRedemption(
                id=uuid5(action.id, "simulated-bank-redemption"),
                user_id=user_id,
                created_at=now,
                action_plan_id=action_id,
                position_id=command.position_id,
                destination_account_id=command.destination_account_id,
                product_id=command.product_id,
                goal_id=command.goal_id,
                principal_cents=command.principal_cents,
                idempotency_key=action.idempotency_key,
                request=payload,
                request_hash=payload_hash,
                requested_at=command.requested_at,
                available_at=command.available_at,
                status="ACCEPTED",
                settled_at=None,
            )
            session.add(request)
            session.flush()
        unified = session.get(BankOperation, request.id)
        if unified is None:
            unified = BankOperation(
                id=request.id,
                user_id=user_id,
                created_at=request.created_at,
                action_plan_id=request.action_plan_id,
                legacy_redemption_id=request.id,
                closing_position_id=request.position_id,
                operation_type="LEGACY_REDEMPTION",
                business_key=f"close:{request.position_id}",
                idempotency_key=request.idempotency_key,
                request=request.request,
                request_hash=request.request_hash,
                requested_at=request.requested_at,
                available_at=request.available_at,
                settled_at=request.settled_at,
                status=request.status,
            )
            session.add(unified)
            session.flush()
        elif unified.request_hash != request.request_hash or unified.action_plan_id != action_id:
            raise _error("Legacy and unified bank request identities disagree")
        if request.status == "ACCEPTED" and request.available_at <= now:
            require_settlement_order(session, user_id, now, unified)
        _settle(session, request, now)
        unified.status, unified.settled_at = request.status, request.settled_at
        session.flush()
        postings = list(
            session.scalars(
                select(SimulatedBankPosting.id)
                .where(SimulatedBankPosting.redemption_id == request.id)
                .order_by(SimulatedBankPosting.id)
            )
        )
        return BankResult.model_validate(
            {
                "request_id": request.id,
                "action_id": action_id,
                "status": request.status,
                "posting_ids": postings,
            }
        )
