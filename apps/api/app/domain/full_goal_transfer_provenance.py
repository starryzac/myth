"""Single-hop ownership replay over original allocations, releases and transfers.

Received cash is an already assigned ownership lot, never a new income fragment.
The adapter must obtain complete original inventories and verified bank/receipt
rows. This pure replay cannot authenticate supplied rows or authorize execution.
"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from app.domain.boundary_types import BoundaryModel
from app.domain.full_goal_release_execution import (
    Cents,
    GoalReleaseSettlementOriginal,
    GoalReleaseUse,
    _validate_original_bank_identity,
    validate_goal_release_settlement,
)
from app.domain.full_goal_transfer import (
    GoalTransferSettlementOriginal,
    validate_goal_transfer_bank_original,
)
from app.domain.goal_release_provenance import GoalAllocationSourceSlice, GoalCashSourceProof
from app.domain.income_ledger import IncomeLedger
from app.domain.policy_configuration import configuration_hash


class ReceivedGoalTransferLot(BoundaryModel):
    transfer_operation_id: UUID
    source_goal_id: UUID
    target_goal_id: UUID
    target_account_id: UUID
    amount_cents: Cents
    original_allocation_uses: list[GoalReleaseUse]
    received_as_income: Literal[False] = False
    available_for_new_income_allocation: Literal[False] = False
    subsequent_transfer_supported: Literal[False] = False


class GoalTransferOwnershipResult(BoundaryModel):
    protocol: Literal["goal-transfer-single-hop-ownership-v1"] = (
        "goal-transfer-single-hop-ownership-v1"
    )
    goal_cash_cents: dict[UUID, Cents]
    remaining_original_uses: dict[UUID, list[GoalReleaseUse]]
    received_lots: list[ReceivedGoalTransferLot]
    input_hash: str
    creates_new_income: Literal[False] = False
    assigned_income_changed: Literal[False] = False
    principal_changed: Literal[False] = False
    bank_authority: Literal[False] = False


def _same_slice(use: GoalReleaseUse, original: GoalAllocationSourceSlice) -> bool:
    fields = (
        "allocation_action_id",
        "fragment_id",
        "origin_transaction_id",
        "income_location_account_id",
        "original_policy_version_id",
        "allocation_effect_hash",
        "allocation_bank_request_hash",
        "allocation_action_request_hash",
    )
    return all(getattr(use, key) == getattr(original, key) for key in fields)


def _complete_original_ids(originals: list[Any], actual_ids: set[UUID]) -> None:
    identifiers = [row.bank_operation_id for row in originals]
    if len(set(identifiers)) != len(identifiers) or set(identifiers) != actual_ids:
        raise ValueError("The complete original Bank denominator is missing or duplicated")


def _release_original(original: GoalReleaseSettlementOriginal, user: UUID, now: datetime) -> None:
    _validate_original_bank_identity(original, now)
    if original.user_id != user:
        raise ValueError("Release original belongs to another user")
    if original.status == "SETTLED":
        validate_goal_release_settlement(original, now)
    elif original.postings or original.settled_at is not None:
        raise ValueError("Unsettled release contains unexplained economic legs")


def replay_goal_transfer_ownership(
    bases: list[GoalCashSourceProof],
    releases: list[GoalReleaseSettlementOriginal],
    transfers: list[GoalTransferSettlementOriginal],
    *,
    actual_release_bank_ids: set[UUID],
    actual_transfer_bank_ids: set[UUID],
    current_goal_cash: dict[UUID, int],
    current_goal_principal: dict[UUID, int],
    current_income: IncomeLedger,
    now: datetime,
) -> GoalTransferOwnershipResult:
    """Full source replay; a failure stays unsupported/UNKNOWN in the adapter."""
    if not bases or len({row.goal_id for row in bases}) != len(bases):
        raise ValueError("Every participating goal requires its distinct original cash basis")
    user, epoch = bases[0].user_id, bases[0].epoch_id
    by_goal = {row.goal_id: row for row in bases}
    if set(current_goal_cash) != set(by_goal) or set(current_goal_principal) != set(by_goal):
        raise ValueError("Both complete current goal-cash and principal denominators are required")
    _complete_original_ids(releases, actual_release_bank_ids)
    _complete_original_ids(transfers, actual_transfer_bank_ids)
    if actual_release_bank_ids & actual_transfer_bank_ids:
        raise ValueError("An original Bank operation cannot be both a release and transfer")
    incoming = {
        row.command.effect.scope.target.goal_id for row in transfers if row.status != "REJECTED"
    }
    outgoing = {
        row.command.effect.scope.source.goal_id for row in transfers if row.status != "REJECTED"
    }
    if incoming & outgoing:
        raise ValueError(
            "Received transfer cash cannot be a later transfer source in the single-hop protocol"
        )
    income_now = {
        row.fragment_id: (
            row.assigned_cents,
            row.available_cents,
            row.reserved_cents,
            row.spent_cents,
        )
        for row in current_income.fragments
    }
    if current_income.user_id != user or current_income.as_of > now:
        raise ValueError("Original income owner or observation time differs")
    income_origins = {row.origin_transaction_id: row for row in current_income.origins}
    originals: dict[UUID, dict[tuple[UUID, UUID], GoalAllocationSourceSlice]] = {}
    consumed: dict[tuple[UUID, UUID, UUID], int] = {}
    expected_cash: dict[UUID, int] = {}
    for basis in bases:
        if (
            (basis.user_id, basis.epoch_id) != (user, epoch)
            or basis.state != "VERIFIED_CASH_ONLY"
            or basis.reasons
            or basis.as_of > now
            or basis.exactly_attributed_goal_cash_cents is None
            or current_goal_principal[basis.goal_id] != basis.original_goal_principal_cents
            or type(current_goal_principal[basis.goal_id]) is not int
            or type(current_goal_cash[basis.goal_id]) is not int
            or current_goal_cash[basis.goal_id] < 0
        ):
            raise ValueError("Original allocation basis, current principal or owner differs")
        income_before = {
            row.fragment_id: (
                row.original_assigned_cents,
                row.original_available_cents,
                row.original_reserved_cents,
                row.original_spent_cents,
            )
            for row in basis.sources
        }
        if income_before != income_now:
            raise ValueError(
                "Goal transfer must not invent income or change assigned/available totals"
            )
        if {row.origin_transaction_id for row in basis.sources} != set(income_origins):
            raise ValueError("Complete original income origin inventory differs")
        for source in basis.sources:
            origin = income_origins[source.origin_transaction_id]
            if (origin.origin_account_id, origin.bank_evidence_id, origin.bank_evidence_hash) != (
                source.origin_account_id,
                source.origin_bank_evidence_id,
                source.origin_bank_evidence_hash,
            ):
                raise ValueError(
                    "Original income source identity/evidence cannot change on transfer"
                )
        slices = {
            (row.allocation_action_id, row.fragment_id): row
            for row in basis.original_allocation_slices
        }
        if len(slices) != len(basis.original_allocation_slices):
            raise ValueError("Original allocation slices are duplicated")
        originals[basis.goal_id] = slices
        expected_cash[basis.goal_id] = basis.exactly_attributed_goal_cash_cents

    def consume(goal: UUID, uses: list[GoalReleaseUse]) -> None:
        if goal not in originals:
            raise ValueError(
                "Transfer or release source is outside the complete original goal basis"
            )
        for use in uses:
            source = originals[goal].get((use.allocation_action_id, use.fragment_id))
            identity = (goal, use.allocation_action_id, use.fragment_id)
            if source is None or not _same_slice(use, source):
                raise ValueError("Transferred use does not bind its unchanged original ALLOCATION")
            consumed[identity] = consumed.get(identity, 0) + use.amount_cents
            if consumed[identity] > source.original_allocated_cents:
                raise ValueError("An original ALLOCATION slice was released or transferred twice")
            expected_cash[goal] -= use.amount_cents

    for release in releases:
        _release_original(release, user, now)
        if release.status in {"ACCEPTED", "UNKNOWN"}:
            raise ValueError("A pending release cannot restore source capacity or be omitted")
        if release.status == "SETTLED" and release.command.effect.source_goal_id in by_goal:
            consume(release.command.effect.source_goal_id, release.command.effect.release_uses)
    received = []
    for transfer in transfers:
        validate_goal_transfer_bank_original(transfer, now)
        effect, scope = transfer.command.effect, transfer.command.effect.scope
        if transfer.user_id != user or scope.epoch_id != epoch:
            raise ValueError("Transfer original belongs to another owner or epoch")
        if transfer.status in {"ACCEPTED", "UNKNOWN"}:
            raise ValueError("A pending transfer cannot restore source capacity or be omitted")
        if transfer.status == "REJECTED":
            continue
        if scope.source.goal_id not in by_goal or scope.target.goal_id not in by_goal:
            raise ValueError("Both transferred goals must remain in the complete current inventory")
        if (
            scope.source.account_id != by_goal[scope.source.goal_id].goal_account_id
            or scope.target.account_id != by_goal[scope.target.goal_id].goal_account_id
            or effect.source_provenance_hash != by_goal[scope.source.goal_id].source_binding_hash
        ):
            raise ValueError(
                "Transfer cash accounts or source hash differ from the original goal basis"
            )
        consume(scope.source.goal_id, effect.source_uses)
        expected_cash[scope.target.goal_id] += scope.amount_cents
        received.append(
            ReceivedGoalTransferLot(
                transfer_operation_id=transfer.bank_operation_id,
                source_goal_id=scope.source.goal_id,
                target_goal_id=scope.target.goal_id,
                target_account_id=scope.target.account_id,
                amount_cents=scope.amount_cents,
                original_allocation_uses=effect.source_uses,
            )
        )
    if expected_cash != current_goal_cash:
        raise ValueError(
            "Current Bank GOAL_CASH is not explained by original allocations and transfers"
        )
    remaining = {}
    for goal, slices in originals.items():
        remaining[goal] = [
            GoalReleaseUse(
                **{
                    key: getattr(part, key)
                    for key in GoalReleaseUse.model_fields
                    if key != "amount_cents"
                },
                amount_cents=part.original_allocated_cents - consumed.get((goal, *identity), 0),
            )
            for identity, part in sorted(slices.items())
            if part.original_allocated_cents > consumed.get((goal, *identity), 0)
            and goal not in incoming
        ]
    return GoalTransferOwnershipResult(
        goal_cash_cents=expected_cash,
        remaining_original_uses=remaining,
        received_lots=received,
        input_hash=configuration_hash(
            {
                "bases": [row.model_dump(mode="json") for row in bases],
                "releases": [row.model_dump(mode="json") for row in releases],
                "transfers": [row.model_dump(mode="json") for row in transfers],
                "income": current_income.model_dump(mode="json"),
                "now": now.isoformat(),
            }
        ),
    )


def goal_transfer_policy_lifetime_usage(
    user: UUID,
    policy: UUID,
    releases: list[GoalReleaseSettlementOriginal],
    transfers: list[GoalTransferSettlementOriginal],
    *,
    actual_release_bank_ids: set[UUID],
    actual_transfer_bank_ids: set[UUID],
    now: datetime,
) -> int:
    """Count old releases plus new transfers across policy versions, including ACCEPTED."""
    _complete_original_ids(releases, actual_release_bank_ids)
    _complete_original_ids(transfers, actual_transfer_bank_ids)
    if actual_release_bank_ids & actual_transfer_bank_ids:
        raise ValueError("An original Bank operation cannot be both a release and transfer")
    total = 0
    for release in releases:
        _release_original(release, user, now)
        if release.status == "UNKNOWN":
            raise ValueError("Original unresolved Bank key makes policy usage unknown")
        if release.command.effect.policy_id == policy and release.status in {"ACCEPTED", "SETTLED"}:
            total += release.command.effect.amount_cents
    for transfer in transfers:
        validate_goal_transfer_bank_original(transfer, now)
        if transfer.user_id != user:
            raise ValueError("Transfer budget inventory belongs to another user")
        if transfer.status == "UNKNOWN":
            raise ValueError("Original unresolved Bank key makes policy usage unknown")
        scope = transfer.command.effect.scope
        if scope.policy.policy_id == policy and transfer.status in {"ACCEPTED", "SETTLED"}:
            total += scope.amount_cents
    return total
