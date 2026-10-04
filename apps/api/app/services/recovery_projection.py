"""Project independently committed bank facts; this module never submits a bank effect."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid5

from app.db.models import (
    Account,
    ActionPlan,
    ActionReceipt,
    AssetPosition,
    DecisionRun,
    EvidenceItem,
    Goal,
    SimulatedBankPosting,
    SimulatedBankRedemption,
    Transaction,
)
from app.domain.asset_exposure import EXPOSURE_SOURCE, asset_exposure_snapshot
from app.domain.policy_configuration import configuration_hash
from app.services.boundary import AVAILABILITY_SOURCE, CONTRIBUTION_SOURCE, OWNERSHIP_SOURCE
from app.services.goal_allocation import LEDGER_SOURCE
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.simulated_bank import BankRequest
from sqlalchemy import select
from sqlalchemy.orm import Session


def _error(message: str) -> PolicyLifecycleError:
    return PolicyLifecycleError("BANK_RECONCILIATION_REQUIRED", message, 409)


def current_proof(
    session: Session,
    user_id: UUID,
    source: str,
    key: str | None = None,
    identifier: UUID | None = None,
) -> EvidenceItem:
    query = select(EvidenceItem).where(
        EvidenceItem.user_id == user_id,
        EvidenceItem.source_type == source,
        EvidenceItem.status != "SUPERSEDED",
    )
    if key is not None:
        query = query.where(EvidenceItem.content[key].as_string() == str(identifier))
    rows = list(session.scalars(query))
    if (
        len(rows) != 1
        or rows[0].status != "VALID"
        or rows[0].content_hash != configuration_hash(rows[0].content)
    ):
        raise _error(f"Expected one intact current {source} proof")
    return rows[0]


def replace_proof(
    session: Session,
    previous: EvidenceItem,
    content: dict[str, Any],
    now: datetime,
    operation_id: UUID,
) -> EvidenceItem:
    digest = configuration_hash(content)
    if previous.content == content and previous.observed_at == now:
        return previous
    identity = uuid5(operation_id, f"evidence:{previous.id}:{digest}")
    previous.status = "SUPERSEDED"
    row = EvidenceItem(
        id=identity,
        user_id=previous.user_id,
        created_at=now,
        evidence_level=previous.evidence_level,
        source_type=previous.source_type,
        source_ref=f"bank-projection:{identity}",
        content=content,
        content_hash=digest,
        valid_from=now,
        observed_at=now,
        supersedes_id=previous.id,
        status="VALID",
    )
    session.add(row)
    session.flush()
    return row


def refresh_exposure(
    session: Session,
    user_id: UUID,
    now: datetime,
    operation_id: UUID,
    declarations: dict[UUID, dict[str, Any]],
) -> None:
    previous = current_proof(session, user_id, EXPOSURE_SOURCE)
    if previous.content.get("protocol") == "asset-exposure-v3":
        from app.services.execution_exposure import refresh_execution_exposure

        refresh_execution_exposure(session, user_id, now, operation_id, declarations=declarations)
        return
    items = {UUID(item["action_id"]): item for item in previous.content["settlements"]}
    items.update(declarations)
    session.flush()
    content = asset_exposure_snapshot(
        user_id,
        now,
        accounts=session.scalars(select(Account).where(Account.user_id == user_id)),
        positions=session.scalars(select(AssetPosition).where(AssetPosition.user_id == user_id)),
        actions=session.scalars(select(ActionPlan).where(ActionPlan.user_id == user_id)),
        receipts=session.scalars(select(ActionReceipt).where(ActionReceipt.user_id == user_id)),
        evidence=session.scalars(select(EvidenceItem).where(EvidenceItem.user_id == user_id)),
        settlements=list(items.values()),
        bank_requests=session.scalars(
            select(SimulatedBankRedemption).where(SimulatedBankRedemption.user_id == user_id)
        ),
        bank_postings=session.scalars(
            select(SimulatedBankPosting).where(SimulatedBankPosting.user_id == user_id)
        ),
    )
    replace_proof(session, previous, content, now, operation_id)


def _epochs(session: Session, user_id: UUID, now: datetime, operation_id: UUID) -> None:
    """Carry forward verified unchanged ownership, contributions and consumed-income components."""
    positions = list(session.scalars(select(AssetPosition).where(AssetPosition.user_id == user_id)))
    for goal in session.scalars(select(Goal).where(Goal.user_id == user_id)):
        proof = current_proof(session, user_id, OWNERSHIP_SOURCE, "goal_id", goal.id)
        owned = [row for row in positions if row.goal_id == goal.id and row.status != "REDEEMED"]
        principal = sum(row.principal_cents for row in owned)
        replace_proof(
            session,
            proof,
            {
                **proof.content,
                "as_of": now.isoformat(),
                "principal_owned_cents": principal,
                "cash_owned_cents": goal.allocated_cents - principal,
                "position_ids": sorted(str(row.id) for row in owned),
            },
            now,
            operation_id,
        )
    for proof in list(
        session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.user_id == user_id,
                EvidenceItem.source_type.in_([CONTRIBUTION_SOURCE, LEDGER_SOURCE]),
                EvidenceItem.status == "VALID",
            )
        )
    ):
        if proof.content_hash != configuration_hash(proof.content) or proof.observed_at > now:
            raise _error(
                "A cumulative ownership or new-income source cannot be reset during recovery"
            )
        # Principal return never changes income lots, spending, reservations or contributions.
        replace_proof(
            session, proof, {**proof.content, "as_of": now.isoformat()}, now, operation_id
        )


def project_request(
    session: Session, request: SimulatedBankRedemption, now: datetime
) -> dict[str, Any]:
    action = session.get(ActionPlan, request.action_plan_id)
    position = session.get(AssetPosition, request.position_id)
    account = session.get(Account, request.destination_account_id)
    if action is None or position is None or account is None:
        raise _error("A bank operation has lost its application identity")
    command = BankRequest.model_validate(request.request)
    if (
        request.request_hash != configuration_hash(request.request)
        or action.request_hash != configuration_hash(action.request)
        or configuration_hash(action.request["bank_request"]) != request.request_hash
        or position.user_id != request.user_id
        or account.user_id != request.user_id
        or position.product_id != command.product_id
        or position.goal_id != command.goal_id
        or position.policy_version_id != command.original_policy_version_id
        or position.account_id != command.position_account_id
        or action.source_account_id != position.account_id
        or action.goal_id != position.goal_id
        or action.product_id != position.product_id
        or action.position_id != position.id
        or position.principal_cents != command.principal_cents
    ):
        raise _error(
            "Current position identity or ownership differs from the immutable bank request"
        )
    if position.goal_id is not None:
        goal = session.get(Goal, position.goal_id)
        if goal is None or goal.user_id != request.user_id or goal.account_id != account.id:
            raise _error("The original goal's return account cannot be rebound")
    receipts = list(
        session.scalars(select(ActionReceipt).where(ActionReceipt.action_plan_id == action.id))
    )
    if receipts:
        if len(receipts) != 1 or receipts[0].response.get("bank_request_id") != str(request.id):
            raise _error("A previous recovery receipt cannot be replaced")
        return {
            "action_id": str(action.id),
            "state": "REDEMPTION_SETTLED",
            "transaction_id": receipts[0].response["transaction_id"],
        }
    if request.status == "UNKNOWN":
        raise _error("The bank result is unknown; no projection is permitted")
    proof = current_proof(
        session, request.user_id, "SIMULATED_BANK_POSITION", "position_id", position.id
    )
    if request.status == "ACCEPTED":
        position.status = "REDEEMING"
        position.available_at = request.available_at
        replace_proof(
            session,
            proof,
            {
                **proof.content,
                "status": position.status,
                "available_at": position.available_at.isoformat(),
                "as_of": now.isoformat(),
                "bank_request_id": str(request.id),
            },
            now,
            request.id,
        )
        old_availability = list(
            session.scalars(
                select(EvidenceItem).where(
                    EvidenceItem.user_id == request.user_id,
                    EvidenceItem.source_type == AVAILABILITY_SOURCE,
                    EvidenceItem.content["position_id"].as_string() == str(position.id),
                    EvidenceItem.status != "SUPERSEDED",
                )
            )
        )
        payload = {
            "simulation": True,
            "user_id": str(request.user_id),
            "protocol": "principal-availability-v1",
            "position_id": str(position.id),
            "account_id": str(position.account_id),
            "goal_id": str(position.goal_id) if position.goal_id else None,
            "principal_cents": position.principal_cents,
            "available_at": request.available_at.isoformat(),
            "principal_return_bps": 10000,
            "rollover": False,
            "bank_request_id": str(request.id),
        }
        if old_availability:
            if len(old_availability) != 1:
                raise _error("Conflicting previous principal availability")
            replace_proof(session, old_availability[0], payload, now, request.id)
        else:
            identity = uuid5(request.id, "availability")
            session.add(
                EvidenceItem(
                    id=identity,
                    user_id=request.user_id,
                    created_at=now,
                    evidence_level="BANK_CONFIRMED",
                    source_type=AVAILABILITY_SOURCE,
                    source_ref=str(identity),
                    content=payload,
                    content_hash=configuration_hash(payload),
                    valid_from=now,
                    observed_at=now,
                    status="VALID",
                )
            )
        session.flush()
        from app.services.audit_recording import record_recovery_observed

        run = session.get(DecisionRun, action.decision_run_id)
        assert run is not None
        record_recovery_observed(
            session,
            run,
            now,
            kind="WAITING_PROJECTION",
            action_id=action.id,
            request=request,
            details={
                "position_status": position.status,
                "available_at": request.available_at.isoformat(),
            },
        )
        return {"action_id": str(action.id), "state": "REDEMPTION_ACCEPTED"}
    legs = list(
        session.scalars(
            select(SimulatedBankPosting).where(SimulatedBankPosting.redemption_id == request.id)
        )
    )
    cash_legs = [row for row in legs if row.entry_kind == "CASH_CREDIT"]
    principal_legs = [row for row in legs if row.entry_kind == "PRINCIPAL_DEBIT"]
    if (
        len(legs) != 2
        or len(cash_legs) != 1
        or len(principal_legs) != 1
        or cash_legs[0].delta_cents != request.principal_cents
        or principal_legs[0].delta_cents != -request.principal_cents
    ):
        raise _error("The bank settlement lacks its two conserved economic facts")
    cash = cash_legs[0]
    if (
        account.balance_cents != cash.balance_before_cents
        or position.status == "REDEEMED"
        or position.principal_cents != -principal_legs[0].delta_cents
    ):
        raise _error("Application projections changed independently of this bank posting")
    account.balance_cents = cash.balance_after_cents
    account.observed_at = now
    position.status = "REDEEMED"
    position.available_at = request.settled_at
    balance = current_proof(
        session, request.user_id, "SIMULATED_BANK_BALANCE", "account_id", account.id
    )
    replace_proof(
        session,
        balance,
        {
            **balance.content,
            "balance_cents": account.balance_cents,
            "as_of": now.isoformat(),
            "bank_posting_id": str(cash.id),
        },
        now,
        request.id,
    )
    replace_proof(
        session,
        proof,
        {
            **proof.content,
            "status": "REDEEMED",
            "available_at": request.settled_at.isoformat() if request.settled_at else None,
            "as_of": now.isoformat(),
            "bank_request_id": str(request.id),
        },
        now,
        request.id,
    )
    for old in session.scalars(
        select(EvidenceItem).where(
            EvidenceItem.user_id == request.user_id,
            EvidenceItem.source_type == AVAILABILITY_SOURCE,
            EvidenceItem.content["position_id"].as_string() == str(position.id),
            EvidenceItem.status != "SUPERSEDED",
        )
    ):
        old.status = "SUPERSEDED"
    transaction_id, evidence_id = (
        uuid5(request.id, "transaction"),
        uuid5(request.id, "transaction-evidence"),
    )
    source_ref = f"bank-redemption:{request.id}:principal"
    payload = {
        "simulation": True,
        "user_id": str(request.user_id),
        "transaction_id": str(transaction_id),
        "account_id": str(account.id),
        "direction": "CREDIT",
        "amount_cents": request.principal_cents,
        "balance_after_cents": cash.balance_after_cents,
        "occurred_at": cash.occurred_at.isoformat(),
        "counterparty_ref": f"position:{position.id}",
        "economic_role": "PRINCIPAL_RETURN",
        "bank_request_id": str(request.id),
        "bank_posting_id": str(cash.id),
    }
    session.add(
        EvidenceItem(
            id=evidence_id,
            user_id=request.user_id,
            created_at=now,
            evidence_level="BANK_CONFIRMED",
            source_type="SIMULATED_BANK_TRANSACTION",
            source_ref=source_ref,
            content=payload,
            content_hash=configuration_hash(payload),
            valid_from=cash.occurred_at,
            observed_at=now,
            status="VALID",
        )
    )
    session.flush()
    session.add(
        Transaction(
            id=transaction_id,
            user_id=request.user_id,
            created_at=now,
            account_id=account.id,
            evidence_id=evidence_id,
            source_ref=source_ref,
            direction="CREDIT",
            amount_cents=request.principal_cents,
            balance_after_cents=cash.balance_after_cents,
            category="principal_return",
            category_confirmed=False,
            counterparty_ref=f"position:{position.id}",
            is_one_off=False,
            occurred_at=cash.occurred_at,
            observed_at=now,
        )
    )
    receipt = ActionReceipt(
        id=uuid5(request.id, "receipt"),
        user_id=request.user_id,
        created_at=now,
        action_plan_id=action.id,
        attempt_number=1,
        receipt_ref=f"bank:{request.id}",
        status="SUCCEEDED",
        executed_cents=request.principal_cents,
        fee_cents=0,
        loss_cents=0,
        response={
            "bank_request_id": str(request.id),
            "posting_ids": sorted(str(row.id) for row in legs),
            "transaction_id": str(transaction_id),
        },
        occurred_at=cash.occurred_at,
        reconciled_at=now,
    )
    session.add(receipt)
    action.status = "SUCCEEDED"
    session.flush()
    from app.services.audit_recording import record_action_projected

    record_action_projected(session, action, request, receipt, now)
    return {
        "action_id": str(action.id),
        "state": "REDEMPTION_SETTLED",
        "transaction_id": str(transaction_id),
    }


def finalize_projections(
    session: Session,
    user_id: UUID,
    now: datetime,
    operation_id: UUID,
    declarations: dict[UUID, dict[str, Any]],
) -> None:
    _epochs(session, user_id, now, operation_id)
    refresh_exposure(session, user_id, now, operation_id, declarations)
