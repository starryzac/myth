"""Private same-RR/RO-session read scope anchored to an actual accepted whole."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.db.models import (
    Account,
    ActionPlan,
    ActionReceipt,
    ActionResourceReservation,
    AssetPosition,
    BankOperation,
    EvidenceItem,
    SimulatedBankPosting,
)
from app.domain.asset_exposure import EXPOSURE_SOURCE, AssetExposure, _json
from app.domain.policy_configuration import configuration_hash
from app.domain.standing_owned_asset_read import (
    AcceptedAssetReadProof,
    OwnedAssetSlot,
    normalize_exposures,
    pending_ids,
)
from app.domain.zhiyu_standing_journal import StandingRun
from app.services.asset_exposure_import import load_all_asset_exposure
from app.services.boundary import BoundaryContext, load_boundary_context
from app.services.full_policy_lifecycle import _read_snapshot
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session, SessionTransaction

if TYPE_CHECKING:
    from app.domain.zhiyu_standing_native import DelegatedNativeInput


@dataclass(frozen=True)
class OwnedAssetSessionScope:
    session: Session
    transaction: SessionTransaction
    proof: AcceptedAssetReadProof


CURRENT: ContextVar[OwnedAssetSessionScope | None] = ContextVar(
    "standing_owned_asset_session", default=None
)


def source_original(row: object) -> dict[str, Any]:
    # Match the native exposure manifest's exact UTC datetime bytes, which are
    # intentionally different from audit canonical datetime presentation.
    mapper = inspect(type(row))
    if mapper is None:
        raise ValueError("An actual mapped native physical original is required")
    original: dict[str, Any] = _json(
        {column.key: getattr(row, column.key) for column in mapper.columns}
    )
    return original


def read_owned_asset_proof(
    session: Session,
    frozen: "DelegatedNativeInput",
    run: StandingRun,
    now: datetime,
) -> AcceptedAssetReadProof:
    from app.domain.zhiyu_standing_native import AssetOriginal, original_effects
    from app.services.zhiyu_standing_recording import (
        binding,
        read_native_proof,
        verify_action_binding,
    )
    from app.services.zhiyu_standing_snapshot import settled_originals

    _read_snapshot(session)
    if CURRENT.get() is not None or not isinstance(frozen.native, AssetOriginal):
        raise ValueError("A fresh exact accepted asset original is required")
    effects = original_effects(frozen.native)
    if (
        run.source != frozen.original_source.source
        or run.grant_id != frozen.grant.grant_id
        or run.whole_plan_hash != configuration_hash(frozen.native.model_dump(mode="json"))
    ):
        raise ValueError("Current journal does not bind the original accepted native whole")
    first = session.get(ActionPlan, effects[0].operation_id)
    if first is None:
        raise ValueError("Atomic accepted journal lacks its first original action")
    first_marker = binding(first)
    independently_read, _ = read_native_proof(
        session,
        frozen.user_id,
        UUID(first_marker["accepted_proof_evidence_id"]),
        first_marker["accepted_proof_evidence_hash"],
    )
    if independently_read != frozen:
        raise ValueError("Independent accepted proof reference and original whole differ")
    prefix = settled_originals(session, frozen.user_id, effects, now)
    context = load_boundary_context(session, frozen.user_id, now)
    raw_exposures = load_all_asset_exposure(session, context)
    if raw_exposures is None or context.sources.issues:
        raise ValueError("Original complete financial exposure did not pass native verification")
    exposure_rows = [
        item
        for item in context.sources.evidence.values()
        if item.source_type == EXPOSURE_SOURCE and item.status != "SUPERSEDED"
    ]
    if len(exposure_rows) != 1:
        raise ValueError("One complete independently verified exposure original required")
    exposure = exposure_rows[0]
    slots: list[OwnedAssetSlot] = []
    for number, effect in enumerate(effects):
        action = session.get(ActionPlan, effect.operation_id)
        claims = list(
            session.scalars(
                select(ActionResourceReservation).where(
                    ActionResourceReservation.user_id == frozen.user_id,
                    ActionResourceReservation.action_plan_id == effect.operation_id,
                )
            )
        )
        if action is None:
            if claims:
                raise ValueError("An absent original child cannot possess resources")
            state = "ABSENT"
        else:
            marker = binding(action)
            verify_action_binding(action, frozen, marker)
            if (
                marker["accepted_proof_evidence_id"] != first_marker["accepted_proof_evidence_id"]
                or marker["accepted_proof_evidence_hash"]
                != first_marker["accepted_proof_evidence_hash"]
            ):
                raise ValueError("Sibling action must retain the same independent accepted proof")
            if number < len(prefix):
                state = "SETTLED_PREFIX"
            else:
                operations = list(
                    session.scalars(
                        select(BankOperation).where(
                            BankOperation.user_id == frozen.user_id,
                            BankOperation.action_plan_id == action.id,
                        )
                    )
                )
                receipts = list(
                    session.scalars(
                        select(ActionReceipt).where(
                            ActionReceipt.user_id == frozen.user_id,
                            ActionReceipt.action_plan_id == action.id,
                        )
                    )
                )
                if operations or receipts:
                    raise ValueError(
                        "Original bank recovery precedes every new accepted read scope"
                    )
                state = (
                    "EXECUTION_RESERVED"
                    if action.status in {"SUBMITTED", "UNKNOWN"}
                    else "PLANNED_UNRESERVED"
                )
        slots.append(
            OwnedAssetSlot.model_validate_json(
                json.dumps(
                    {
                        "action_id": str(effect.operation_id),
                        "state": state,
                        "action_original": source_original(action) if action else None,
                        "resource_originals": [source_original(claim) for claim in claims],
                    }
                )
            )
        )
    proof = AcceptedAssetReadProof.model_validate_json(
        json.dumps(
            {
                "user_id": str(frozen.user_id),
                "epoch_id": str(frozen.epoch_id),
                "as_of": now.isoformat(),
                "accepted_evidence_id": first_marker["accepted_proof_evidence_id"],
                "accepted_evidence_hash": first_marker["accepted_proof_evidence_hash"],
                "accepted_native_hash": run.whole_plan_hash,
                "current_run": run.model_dump(mode="json"),
                "effects": [effect.model_dump(mode="json") for effect in effects],
                "settled_prefix": len(prefix),
                "slots": [slot.model_dump(mode="json") for slot in slots],
                "exposure_evidence_id": str(exposure.id),
                "exposure_evidence_hash": exposure.content_hash,
                "exposure_original": exposure.content,
                "prefix_originals": [
                    {
                        "bank": source_original(
                            session.get(BankOperation, UUID(item["bank"]["id"]))
                        ),
                        "receipt": source_original(
                            session.get(ActionReceipt, UUID(item["receipt"]["id"]))
                        ),
                        "postings": [
                            source_original(session.get(SimulatedBankPosting, UUID(post["id"])))
                            for post in item["postings"]
                        ],
                    }
                    for item in prefix
                ],
                "account_originals": [
                    source_original(row)
                    for row in session.scalars(
                        select(Account)
                        .where(Account.user_id == frozen.user_id)
                        .order_by(Account.id)
                    )
                ],
                "position_originals": [
                    source_original(row)
                    for row in session.scalars(
                        select(AssetPosition)
                        .where(AssetPosition.user_id == frozen.user_id)
                        .order_by(AssetPosition.id)
                    )
                ],
                "evidence_originals": [
                    source_original(row)
                    for row in session.scalars(
                        select(EvidenceItem)
                        .where(
                            EvidenceItem.user_id == frozen.user_id,
                            EvidenceItem.source_type.in_(
                                [
                                    "SIMULATED_BANK_BALANCE",
                                    "SIMULATED_BANK_POSITION",
                                    "SIMULATED_BANK_TRANSACTION",
                                    "SIMULATED_GOAL_OWNERSHIP",
                                    "SIMULATED_GOAL_MONTH_CONTRIBUTION",
                                    "SIMULATED_PRINCIPAL_AVAILABILITY",
                                    "SIMULATED_NEW_FUNDS_LEDGER",
                                ]
                            ),
                        )
                        .order_by(EvidenceItem.id)
                    )
                ],
            }
        )
    )
    # The first complete importer pass must precede the scope and subtraction.
    normalize_exposures(proof, raw_exposures)
    return proof


@contextmanager
def owned_asset_read_scope(session: Session, proof: AcceptedAssetReadProof) -> Iterator[None]:
    _read_snapshot(session)
    proof = AcceptedAssetReadProof.model_validate_json(proof.model_dump_json())
    transaction = session.get_transaction()
    if CURRENT.get() is not None or transaction is None:
        raise ValueError("Owned native read scope needs one fresh exact RR/RO transaction")
    scope = OwnedAssetSessionScope(session, transaction, proof)
    token = CURRENT.set(scope)
    try:
        yield
    finally:
        CURRENT.reset(token)


def current_owned_scope(
    session: Session, user: UUID, now: datetime
) -> AcceptedAssetReadProof | None:
    scope = CURRENT.get()
    if scope is None:
        return None
    if (
        scope.session is not session
        or scope.transaction is not session.get_transaction()
        or scope.proof.user_id != user
        or scope.proof.as_of != now
    ):
        raise ValueError("Owned read scope cannot cross sessions, users, transactions or clocks")
    return scope.proof


@contextmanager
def unowned_asset_read_scope() -> Iterator[None]:
    """Other business questions use their unchanged raw native current sources."""
    token = CURRENT.set(None)
    try:
        yield
    finally:
        CURRENT.reset(token)


def normalize_verified_asset_exposures(
    session: Session, context: BoundaryContext, original: list[AssetExposure]
) -> list[AssetExposure]:
    proof = current_owned_scope(session, context.sources.user_id, context.snapshot.as_of)
    if proof is None:
        return original
    row = context.sources.evidence.get(proof.exposure_evidence_id)
    if (
        row is None
        or row.source_type != EXPOSURE_SOURCE
        or row.status != "VALID"
        or row.content != proof.exposure_original
        or row.content_hash != proof.exposure_evidence_hash
        or configuration_hash(row.content) != row.content_hash
        or context.sources.issues
    ):
        raise ValueError("Current full native source must match the independent scope original")
    return normalize_exposures(proof, original)


def is_owned_reserved_claim(
    session: Session, claim: ActionResourceReservation, now: datetime
) -> bool:
    proof = current_owned_scope(session, claim.user_id, now)
    if proof is None or claim.action_plan_id not in pending_ids(proof):
        return False
    wanted = [
        original
        for slot in proof.slots
        for original in slot.resource_originals
        if original["id"] == str(claim.id)
    ]
    if wanted != [source_original(claim)]:
        raise ValueError("Exact same original own resource claim is required")
    return True
