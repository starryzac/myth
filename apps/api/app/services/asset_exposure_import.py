"""Read and reconcile complete simulated asset exposure without consuming or reserving funds."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.db.models import (
    Account,
    ActionPlan,
    ActionReceipt,
    AssetPosition,
    PolicyVersion,
    Transaction,
)
from app.domain.asset_exposure import (
    EXPOSURE_SOURCE,
    AssetExposure,
    asset_exposure_snapshot,
)
from app.domain.history_coverage import bank_fact_snapshot
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.services.boundary import OWNERSHIP_SOURCE, BoundaryContext
from app.services.policy_lifecycle import PolicyLifecycleError, _evidence
from sqlalchemy import select
from sqlalchemy.orm import Session


def _scope(session: Session, context: BoundaryContext, version_id: UUID | None) -> str:
    if version_id is None:
        raise ValueError("An automatic acquisition needs its original authorization")
    version = session.get(PolicyVersion, version_id)
    if version is None or version.user_id != context.sources.user_id:
        raise ValueError("Unknown historical authorization")
    config = validate_configuration(version.configuration)
    if (
        config["type"] != "asset_authorization"
        or configuration_hash(config) != version.content_hash
    ):
        raise ValueError("Invalid historical asset authorization")
    proofs = _evidence(session, version.user_id, version.evidence_ids, context.snapshot.as_of)
    if (
        version.confirmed_at is None
        or version.confirmed_at > context.snapshot.as_of
        or not any(
            proof.source_type == "POLICY_CONFIRMATION"
            and proof.evidence_level == "USER_CONFIRMED_POLICY"
            and proof.source_ref == str(version.id)
            and proof.content == version.confirmation
            and proof.content.get("accepted") is True
            for proof in proofs
        )
    ):
        raise ValueError("Historical authorization lacks a bound confirmation")
    context.sources.used.update(proof.id for proof in proofs)
    return config["goal_id"] if config["scope"] == "goal" else "general"


def _goal_scope(goal_id: UUID | None) -> str:
    return str(goal_id) if goal_id else "general"


def load_asset_exposure(
    session: Session, context: BoundaryContext, configuration: dict[str, Any]
) -> AssetExposure:
    sources = context.sources
    goal_id = UUID(configuration["goal_id"]) if configuration.get("goal_id") else None
    empty = AssetExposure(
        as_of=context.snapshot.as_of,
        scope=configuration["scope"],
        goal_id=goal_id,
        managed_principal_cents=0,
        pending_purchase_cents=0,
    )
    statements = [
        row
        for row in sources.evidence.values()
        if row.source_type == EXPOSURE_SOURCE and row.status != "SUPERSEDED"
    ]
    sources.used.update(row.id for row in statements)
    if len(statements) != 1:
        sources.issue(
            "MISSING_ASSET_EXPOSURE" if not statements else "CONFLICTING_ASSET_EXPOSURE",
            EXPOSURE_SOURCE,
            "需要唯一完整的资产占用证明",
        )
        return empty
    statement = statements[0]
    accounts = list(session.scalars(select(Account).where(Account.user_id == sources.user_id)))
    positions = list(
        session.scalars(select(AssetPosition).where(AssetPosition.user_id == sources.user_id))
    )
    actions = list(session.scalars(select(ActionPlan).where(ActionPlan.user_id == sources.user_id)))
    receipts = list(
        session.scalars(select(ActionReceipt).where(ActionReceipt.user_id == sources.user_id))
    )
    transactions = list(
        session.scalars(select(Transaction).where(Transaction.user_id == sources.user_id))
    )
    try:
        if (
            len(positions) > 10000
            or len(actions) > 10000
            or len(receipts) > 100000
            or len(transactions) > 100000
        ):
            raise ValueError("Exposure input capacity exceeded")
        epoch = datetime.fromisoformat(statement.content["as_of"])
        if epoch.tzinfo is None or epoch.utcoffset() is None:
            raise ValueError("Naive exposure epoch")
        epoch = epoch.astimezone(UTC)
        settlements = statement.content["settlements"]
        if not isinstance(settlements, list):
            raise ValueError("Settlements must be a complete list")
        expected = asset_exposure_snapshot(
            sources.user_id,
            epoch,
            accounts=accounts,
            positions=positions,
            actions=actions,
            receipts=receipts,
            evidence=sources.evidence.values(),
            settlements=settlements,
        )
        if configuration_hash(expected) != configuration_hash(
            statement.content
        ) or not sources.valid(statement, expected):
            raise ValueError("Exposure statement does not bind the complete current row set")
        if epoch > context.snapshot.as_of or statement.observed_at < epoch:
            raise ValueError("Exposure epoch is not yet known")
        watermarks = [row.observed_at for row in accounts]
        watermarks += [row.purchased_at for row in positions]
        watermarks += [row.created_at for row in actions]
        watermarks += [row.authorized_at for row in actions if row.authorized_at is not None]
        watermarks += [
            stamp
            for row in receipts
            for stamp in (row.created_at, row.occurred_at, row.reconciled_at)
            if stamp is not None
        ]
        watermarks += [
            stamp
            for row in transactions
            for stamp in (row.occurred_at, row.observed_at)
            if row.occurred_at <= context.snapshot.as_of
            and row.observed_at <= context.snapshot.as_of
        ]
        if any(stamp > epoch for stamp in watermarks):
            raise ValueError("Exposure predates a known financial or execution fact")
        if goal_id is not None:
            owned = sources.candidates(OWNERSHIP_SOURCE, "goal_id", goal_id)
            if len(owned) != 1 or owned[0].content.get("as_of") != epoch.isoformat():
                raise ValueError("Goal ownership and exposure epochs differ")
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        sources.issue("INVALID_ASSET_EXPOSURE", statement.id, str(error))
        return empty

    try:
        by_action = {row.id: row for row in actions}
        by_position = {row.id: row for row in positions}
        by_transaction = {row.id: row for row in transactions}
        by_receipt = {row.id: row for row in receipts}
        by_account = {row.id: row for row in accounts}
        bank_positions = [
            proof
            for proof in sources.evidence.values()
            if proof.source_type == "SIMULATED_BANK_POSITION" and proof.status != "SUPERSEDED"
        ]
        sources.used.update(proof.id for proof in bank_positions)
        if len(bank_positions) != len(positions) or {
            UUID(proof.content["position_id"]) for proof in bank_positions
        } != set(by_position):
            raise ValueError(
                "Live bank positions and current position projections must match exactly"
            )
        if any(
            not sources.valid(proof, {"user_id": str(sources.user_id)}) for proof in bank_positions
        ):
            raise ValueError("A live bank position source is invalid or belongs to another user")
        declarations = {UUID(item["action_id"]): item for item in settlements}
        if len(declarations) != len(settlements) or set(declarations) != set(by_action):
            raise ValueError("Every action needs exactly one settlement declaration")
        managed = pending = 0
        counted_positions: list[UUID] = []
        counted_actions: list[UUID] = []
        manual_positions: list[UUID] = []
        reserved: dict[UUID, int] = {}
        reserved_goals: dict[UUID, int] = {}
        materialized: dict[UUID, UUID] = {}
        acquisition_transactions: set[UUID] = set()

        def transaction_for(identifier: UUID) -> Transaction:
            row = by_transaction[identifier]
            proof = sources.evidence[row.evidence_id] if row.evidence_id is not None else None
            if proof is None or not sources.valid(proof, {}):
                raise ValueError("An acquisition has no valid bank transaction")
            bank_fact_snapshot(row, proof)
            if (
                row.direction != "DEBIT"
                or proof.content["economic_role"] != "ASSET_PURCHASE"
                or row.occurred_at > epoch
                or row.observed_at > epoch
            ):
                raise ValueError("Acquisition is not an observed bank purchase")
            if row.id in acquisition_transactions:
                raise ValueError("A purchase transaction cannot materialize twice")
            acquisition_transactions.add(row.id)
            return row

        for action in actions:
            declaration = declarations[action.id]
            action_receipts = [row for row in receipts if row.action_plan_id == action.id]
            if configuration_hash(action.request) != action.request_hash:
                raise ValueError("Action request hash mismatch")
            state = declaration.get("state")
            if state == "NO_EFFECT":
                if (
                    set(declaration) != {"action_id", "state"}
                    or action.status not in {"CANCELLED", "INVALIDATED", "FAILED"}
                    or any(
                        row.status != "FAILED"
                        or row.executed_cents
                        or row.fee_cents
                        or row.loss_cents
                        for row in action_receipts
                    )
                ):
                    raise ValueError("No-effect assertion conflicts with an execution receipt")
                continue
            if action.action_type != "ASSET_PURCHASE" or action.product_id is None:
                raise ValueError("Only unambiguous purchase settlements are supported")
            scope = _scope(session, context, action.policy_version_id)
            if scope != _goal_scope(action.goal_id):
                raise ValueError("Action ownership differs from its original authorization")
            if state == "RESERVED_UNDEBITED":
                if (
                    set(declaration) != {"action_id", "state"}
                    or action.status not in {"PLANNED", "AUTHORIZED"}
                    or action_receipts
                    or action.position_id is not None
                ):
                    raise ValueError(
                        "Reserved purchase already has an uncertain or materialized effect"
                    )
                account = by_account[action.source_account_id]
                if action.goal_id is None:
                    if account.account_type != "CASH":
                        raise ValueError("General purchase reservation needs a CASH source")
                else:
                    owned_goal = next(
                        (item for item in context.snapshot.goals if item.goal_id == action.goal_id),
                        None,
                    )
                    if owned_goal is None or owned_goal.account_id != account.id:
                        raise ValueError("Goal reservation must use its own cash account")
                    reserved_goals[action.goal_id] = (
                        reserved_goals.get(action.goal_id, 0) + action.amount_cents
                    )
                reserved[account.id] = reserved.get(account.id, 0) + action.amount_cents
                if scope == _goal_scope(goal_id):
                    pending += action.amount_cents
                    counted_actions.append(action.id)
            elif state == "MATERIALIZED":
                if (
                    set(declaration)
                    != {"action_id", "state", "position_id", "receipt_id", "transaction_id"}
                    or action.status not in {"SUCCEEDED", "RECONCILED"}
                    or len(action_receipts) != 1
                ):
                    raise ValueError("Materialized purchase is not a single reconciled effect")
                receipt = by_receipt[UUID(declaration["receipt_id"])]
                position = by_position[UUID(declaration["position_id"])]
                if (
                    receipt.action_plan_id != action.id
                    or receipt.status != "SUCCEEDED"
                    or receipt.executed_cents != action.amount_cents
                    or receipt.fee_cents
                    or receipt.loss_cents
                    or position.id != action.position_id
                    or position.product_id != action.product_id
                    or position.goal_id != action.goal_id
                    or position.policy_version_id != action.policy_version_id
                    or position.principal_cents != action.amount_cents
                ):
                    raise ValueError("Materialized action, receipt and position do not match")
                if position.id in materialized:
                    raise ValueError("Two actions refer to one materialized position")
                materialized[position.id] = action.id
            else:
                raise ValueError("Unknown or partial settlement cannot release an exposure")

        for position in positions:
            proofs = sources.candidates("SIMULATED_BANK_POSITION", "position_id", position.id)
            if (
                len(proofs) != 1
                or not sources.valid(proofs[0], {})
                or proofs[0].observed_at > epoch
            ):
                raise ValueError("Position exposure needs one current bank source")
            proof = proofs[0]
            if position.status in {"UNKNOWN", "REDEEMED"}:
                raise ValueError("Unknown or redeemed exposure needs reconciliation outside v1")
            transaction = transaction_for(UUID(proof.content["purchase_transaction_id"]))
            if (
                transaction.amount_cents != position.principal_cents
                or transaction.occurred_at != position.purchased_at
            ):
                raise ValueError("Position principal does not match its original bank purchase")
            acquisition = proof.content.get("acquisition")
            if acquisition == "synthetic_user_manual_purchase":
                if (
                    position.policy_version_id is not None
                    or position.id in materialized
                    or any(row.position_id == position.id for row in actions)
                ):
                    raise ValueError("A manual position has an automatic authority or action link")
                manual_positions.append(position.id)
                continue
            if acquisition != "synthetic_auto_purchase" or position.id not in materialized:
                raise ValueError("Automatic principal has no complete acquisition linkage")
            action_id = materialized[position.id]
            declaration = declarations[action_id]
            if (
                proof.content.get("purchase_action_id") != str(action_id)
                or proof.content.get("purchase_receipt_id") != declaration["receipt_id"]
                or str(transaction.id) != declaration["transaction_id"]
                or transaction.account_id != by_action[action_id].source_account_id
            ):
                raise ValueError("Position bank source is not bound to its execution")
            scope = _scope(session, context, position.policy_version_id)
            if scope != _goal_scope(position.goal_id):
                raise ValueError("Position scope does not match historical authorization")
            if scope == _goal_scope(goal_id):
                managed += position.principal_cents
                counted_positions.append(position.id)
        for account_id, amount in reserved.items():
            if amount > by_account[account_id].balance_cents:
                raise ValueError("Pending purchases exceed their source cash")
        for identifier, amount in reserved_goals.items():
            owned_goal = next(item for item in context.snapshot.goals if item.goal_id == identifier)
            if amount > owned_goal.cash_owned_cents:
                raise ValueError("Pending goal purchases exceed that goal's owned cash")
        return AssetExposure(
            as_of=epoch,
            scope=configuration["scope"],
            goal_id=goal_id,
            managed_principal_cents=managed,
            pending_purchase_cents=pending,
            reserved_cash_by_account=reserved,
            reserved_goal_cash_by_goal=reserved_goals,
            counted_position_ids=sorted(counted_positions),
            counted_action_ids=sorted(counted_actions),
            excluded_manual_position_ids=sorted(manual_positions),
            evidence_ids=sorted(sources.used),
        )
    except (KeyError, TypeError, ValueError, OverflowError, PolicyLifecycleError) as error:
        sources.issue("EXPOSURE_RECONCILIATION_REQUIRED", statement.id, str(error))
        return empty
