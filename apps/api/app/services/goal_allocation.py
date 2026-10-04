"""Verified new-income lot imports for read-only single-goal allocation previews."""

import json
from datetime import UTC, datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from app.db.models import Goal, PolicyVersion, Transaction
from app.domain.boundary_types import SourceIssue
from app.domain.goal_allocation import GoalAllocationResult, IncomeLot, plan_goal_allocation
from app.domain.history_coverage import bank_fact_snapshot
from app.domain.income_ledger import LEDGER_PROTOCOL_V2
from app.domain.policy_configuration import MoneyCents, configuration_hash
from app.services.boundary import (
    CONTRIBUTION_SOURCE,
    OWNERSHIP_SOURCE,
    BoundaryContext,
    BoundarySourceIssue,
    load_boundary_context,
)
from app.services.policy_lifecycle import PolicyLifecycleError, is_version_authorized
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

LEDGER_SOURCE = "SIMULATED_NEW_FUNDS_LEDGER"
LEDGER_PROTOCOL = "new-funds-ledger-v1"


class LedgerLot(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
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
            != (self.spent_cents + self.assigned_cents + self.reserved_cents + self.available_cents)
            or self.prior_unspent_cents != self.reserved_cents + self.available_cents
        ):
            raise ValueError("The original income and all consumed or reserved parts must conserve")
        return self


class LedgerPayload(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    simulation: Literal[True]
    protocol: Literal["new-funds-ledger-v1"]
    user_id: UUID
    complete: Literal[True]
    as_of: AwareDatetime
    scope_account_ids: Annotated[list[UUID], Field(max_length=100)]
    lots: Annotated[list[LedgerLot], Field(max_length=100000)]


class GoalAllocationResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    simulation: Literal[True] = True
    user_id: UUID
    goal_id: UUID
    as_of: datetime
    allocation: GoalAllocationResult
    source_evidence_ids: list[UUID]
    input_digest: str
    source_issues: list[BoundarySourceIssue]


def _income_lots(session: Session, context: BoundaryContext, goal_id: UUID) -> list[IncomeLot]:
    sources = context.sources
    statements = [
        item
        for item in sources.evidence.values()
        if item.source_type == LEDGER_SOURCE and item.status != "SUPERSEDED"
    ]
    sources.used.update(item.id for item in statements)
    if len(statements) != 1:
        sources.issue(
            "MISSING_NEW_FUNDS_LEDGER" if not statements else "CONFLICTING_NEW_FUNDS_LEDGER",
            goal_id,
            "需要唯一且完整的原始新收入可用账目",
        )
        return []
    statement = statements[0]
    try:
        if statement.content.get("protocol") == LEDGER_PROTOCOL_V2:
            from app.services.income_ledger import read_income_state

            state = read_income_state(session, sources.user_id, sources.now)
            ledger = state.ledger
            for source in (OWNERSHIP_SOURCE, CONTRIBUTION_SOURCE):
                proofs = sources.candidates(source, "goal_id", goal_id)
                if source == CONTRIBUTION_SOURCE:
                    proofs = [item for item in proofs if item.id in sources.used]
                if len(proofs) != 1 or proofs[0].content.get("as_of") != ledger.as_of.isoformat():
                    raise ValueError("Ledger and goal ownership/contribution must have one epoch")
            origins = {item.origin_transaction_id: item for item in ledger.origins}
            sources.used.update(origin.bank_evidence_id for origin in ledger.origins)
            return [
                IncomeLot(
                    origin_transaction_id=fragment.origin_transaction_id,
                    account_id=fragment.account_id,
                    fragment_id=fragment.fragment_id,
                    amount_cents=origins[fragment.origin_transaction_id].amount_cents,
                    available_cents=fragment.available_cents,
                    occurred_at=origins[fragment.origin_transaction_id].occurred_at,
                    observed_at=origins[fragment.origin_transaction_id].observed_at,
                    evidence_ids=[
                        state.evidence_id,
                        origins[fragment.origin_transaction_id].bank_evidence_id,
                    ],
                )
                for fragment in ledger.fragments
            ]
        payload = LedgerPayload.model_validate_json(json.dumps(statement.content))
        if not sources.valid(
            statement,
            {
                "simulation": True,
                "protocol": LEDGER_PROTOCOL,
                "user_id": str(sources.user_id),
                "complete": True,
            },
        ):
            raise ValueError("Ledger identity, hash or known time is invalid")
        epoch = payload.as_of.astimezone(UTC)
        if (
            epoch > sources.now
            or epoch > statement.observed_at
            or (sources.balance_as_of is not None and epoch < sources.balance_as_of)
        ):
            raise ValueError("Ledger does not cover the adopted balance snapshot")
        for source in (OWNERSHIP_SOURCE, CONTRIBUTION_SOURCE):
            proofs = sources.candidates(source, "goal_id", goal_id)
            if source == CONTRIBUTION_SOURCE:
                proofs = [item for item in proofs if item.id in sources.used]
            if len(proofs) != 1 or proofs[0].content.get("as_of") != epoch.isoformat():
                raise ValueError("Ledger and goal ownership/contribution must have one epoch")
        accounts = {
            item.account_id: item
            for item in context.snapshot.cash_accounts
            if item.account_type == "CASH"
        }
        if payload.scope_account_ids != sorted(accounts):
            raise ValueError("Ledger must cover all current original CASH accounts exactly once")
        known_rows = list(
            session.scalars(
                select(Transaction)
                .where(
                    Transaction.user_id == sources.user_id,
                    Transaction.account_id.in_(accounts),
                    Transaction.occurred_at <= sources.now,
                    Transaction.observed_at <= sources.now,
                )
                .limit(100001)
            )
        )
        if len(known_rows) > 100000:
            raise ValueError("Income ledger capacity exceeded")
        if any(row.occurred_at > epoch or row.observed_at > epoch for row in known_rows):
            raise ValueError("Income ledger predates known CASH transaction activity")
        transactions = [row for row in known_rows if row.direction == "CREDIT"]
        incomes: dict[UUID, Transaction] = {}
        for transaction in transactions:
            proof = (
                sources.evidence.get(transaction.evidence_id) if transaction.evidence_id else None
            )
            if proof is None or proof.observed_at > epoch or not sources.valid(proof, {}):
                raise ValueError("A CREDIT origin has no valid known bank evidence")
            bank_fact_snapshot(transaction, proof)
            if proof.content.get("economic_role") == "INCOME":
                incomes[transaction.id] = transaction
        by_id = {item.transaction_id: item for item in payload.lots}
        if len(by_id) != len(payload.lots) or by_id.keys() != incomes.keys():
            raise ValueError("Ledger origins are duplicated, omitted or not original bank income")
        available_by_account: dict[UUID, int] = {}
        lots: list[IncomeLot] = []
        for identifier in sorted(incomes):
            transaction, record = incomes[identifier], by_id[identifier]
            proof = sources.evidence[record.bank_evidence_id]
            if (
                transaction.account_id != record.account_id
                or transaction.evidence_id != record.bank_evidence_id
                or proof.content_hash != record.bank_evidence_hash
                or transaction.amount_cents != record.original_cents
            ):
                raise ValueError("Ledger must bind the original transaction, account and bank hash")
            available_by_account[record.account_id] = (
                available_by_account.get(record.account_id, 0) + record.available_cents
            )
            lots.append(
                IncomeLot(
                    origin_transaction_id=identifier,
                    account_id=record.account_id,
                    amount_cents=record.original_cents,
                    available_cents=record.available_cents,
                    occurred_at=transaction.occurred_at,
                    observed_at=transaction.observed_at,
                    evidence_ids=[statement.id, proof.id],
                )
            )
        for identifier, available in available_by_account.items():
            owned = sum(
                item.cash_owned_cents
                for item in context.snapshot.goals
                if item.account_id == identifier
            )
            if available > accounts[identifier].balance_cents - owned:
                raise ValueError("Available income exceeds remaining cash in the original account")
        return lots
    except (KeyError, TypeError, ValueError, OverflowError, PolicyLifecycleError) as error:
        sources.issue("INVALID_NEW_FUNDS_LEDGER", statement.id, str(error))
        return []


def preview_goal_allocation(
    session: Session,
    user_id: UUID,
    goal_id: UUID,
    now: datetime,
) -> GoalAllocationResponse:
    with session.no_autoflush:
        goal = session.scalar(select(Goal).where(Goal.id == goal_id, Goal.user_id == user_id))
        if goal is None:
            raise PolicyLifecycleError("NOT_FOUND", "目标不存在", 404)
        context = load_boundary_context(session, user_id, now)
        current = session.scalar(
            select(PolicyVersion)
            .where(
                PolicyVersion.policy_id == goal.policy_id,
                PolicyVersion.user_id == user_id,
            )
            .order_by(PolicyVersion.version_number.desc())
            .limit(1)
        )
        eligible = (
            current is not None
            and current.id == goal.policy_version_id
            and is_version_authorized(
                session,
                user_id,
                goal.policy_version_id,
                context.snapshot.as_of,
            )
        )
        lots = _income_lots(session, context, goal_id) if eligible else []
        issues = sorted(context.sources.issues, key=lambda item: (item.code, item.source_ref))
        source_issues = [
            SourceIssue(code=item.code, entity_type="source", entity_id=item.source_ref)
            for item in issues
        ]
        try:
            allocation = plan_goal_allocation(
                goal_id,
                goal.policy_version_id,
                context.snapshot,
                context.versions,
                context.positions,
                context.products,
                lots,
                source_issues=source_issues,
            )
        except (TypeError, ValueError, OverflowError) as error:
            raise PolicyLifecycleError(
                "INVALID_ALLOCATION_INPUT", "目标分配输入不一致或超出范围"
            ) from error
        digest = configuration_hash(
            {
                "boundary_sources": context.snapshot.source_digest,
                "evidence": [
                    {
                        "id": str(identifier),
                        "hash": context.sources.evidence[identifier].content_hash,
                    }
                    for identifier in sorted(context.sources.used)
                ],
                "issues": [item.model_dump(mode="json") for item in issues],
            }
        )
        from app.domain.decision_trace_types import TraceCandidate
        from app.services.decision_recording import capture_boundary, current_capture

        capture = current_capture(session)
        if capture is not None:
            capture_boundary(session, "goal_boundary", context)
            capture.algorithms["goal_allocation"] = allocation.algorithm_version
            capture.inputs["goal_planning"] = {
                "goal_id": str(goal_id),
                "policy_version_id": str(goal.policy_version_id),
                "lots": [lot.model_dump(mode="json") for lot in lots],
                "source_issues": [issue.model_dump(mode="json") for issue in source_issues],
                "result": allocation.model_dump(mode="json"),
            }
            capture.candidates.append(
                TraceCandidate(
                    candidate_key="goal:" + str(goal_id),
                    kind="GOAL_ALLOCATION",
                    status=allocation.status,
                    inputs=capture.inputs["goal_planning"],
                    result=allocation.model_dump(mode="json"),
                    reasons=allocation.reasons,
                )
            )
        return GoalAllocationResponse(
            user_id=user_id,
            goal_id=goal_id,
            as_of=context.snapshot.as_of,
            allocation=allocation,
            source_evidence_ids=sorted(context.sources.used),
            input_digest=digest,
            source_issues=issues,
        )
