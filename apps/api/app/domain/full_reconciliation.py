"""Read-only reconciliation reports; differences never authorize a repair."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from app.domain.audit_chain_types import AuditVerification
from app.domain.boundary_types import BoundaryModel
from pydantic import AwareDatetime, Field, StrictInt

Amount = Annotated[StrictInt, Field(ge=0)]
IssueKind = Literal["DIFFERENCE", "INTEGRITY", "MISSING", "UNSUPPORTED", "PENDING"]


class ReconciliationIssue(BoundaryModel):
    code: str
    source_ref: str
    message: str
    kind: IssueKind


class ReconciliationInventory(BoundaryModel):
    table: str
    actual_count: Amount
    captured_count: Amount
    complete: bool


class BankHeadReference(BoundaryModel):
    posting_id: UUID
    ledger_key: str
    sequence_number: Annotated[StrictInt, Field(gt=0)]
    occurred_at: AwareDatetime


class ReconciliationAmount(BoundaryModel):
    entity_id: UUID
    kind: Literal["ACCOUNT_CASH", "POSITION_PRINCIPAL", "GOAL_CASH", "GOAL_PRINCIPAL"]
    application_cents: StrictInt | None
    bank_cents: StrictInt | None
    difference_cents: StrictInt | None
    state: Literal["MATCHED", "DIFFERENCE", "MISSING"]
    bank_head: BankHeadReference | None


class ReconciliationGoal(BoundaryModel):
    goal_id: UUID
    account_id: UUID | None
    allocated_cents: Amount
    position_ids: list[UUID]
    ownership_evidence_id: UUID | None
    ownership_evidence_hash: str | None
    current_ownership_proof_verified: bool
    cash: ReconciliationAmount
    principal: ReconciliationAmount


class ReconciliationPosting(BoundaryModel):
    posting_id: UUID
    operation_id: UUID | None
    redemption_id: UUID | None
    ledger_key: str
    ledger_dimension: str
    leg_ref: str | None
    sequence_number: Annotated[StrictInt, Field(gt=0)]
    balance_before_cents: Amount
    delta_cents: StrictInt
    balance_after_cents: Amount
    occurred_at: AwareDatetime


class ReconciliationAction(BoundaryModel):
    action_id: UUID
    action_type: str
    original_action_status: str
    original_idempotency_key: str
    original_request_hash: str
    effect_hash: str | None
    expected_amount_cents: Amount
    expected_fee_cents: Amount | None
    expected_loss_cents: Amount | None
    actual_executed_cents: Amount | None
    actual_fee_cents: Amount | None
    actual_loss_cents: Amount | None
    bank_operation_ids: list[UUID]
    bank_statuses: list[str]
    bank_request_hashes: list[str]
    posting_ids: list[UUID]
    receipt_ids: list[UUID]
    receipt_statuses: list[str]
    complete_settlement_legs_verified: bool
    service_receipt_verified: bool
    state: Literal[
        "SERVICE_RECEIPT_VERIFIED",
        "BANK_SETTLED_APPLICATION_UNRESOLVED",
        "PENDING_BANK",
        "PREPARED_NO_BANK_OBSERVED",
        "NO_EFFECT_OBSERVED_NOT_FINAL",
        "BANK_REJECTION_VERIFIED",
        "UNKNOWN",
        "MANUAL_REVIEW_REQUIRED",
    ]
    read_original_action_path: str
    query_original_key_only: Literal[True] = True
    retry_or_repair_performed: Literal[False] = False
    issues: list[ReconciliationIssue]


class FullReconciliationReport(BoundaryModel):
    schema_version: Literal["full-reconciliation-v1"] = "full-reconciliation-v1"
    user_id: UUID
    as_of: AwareDatetime
    simulation: Literal[True] = True
    read_only: Literal[True] = True
    bank_truth: Literal["INDEPENDENT_SIMULATED_BANK_LEDGER"] = "INDEPENDENT_SIMULATED_BANK_LEDGER"
    grants_authority: Literal[False] = False
    executes_funds: Literal[False] = False
    repairs_performed: Literal[False] = False
    receipt_is_current_authority: Literal[False] = False
    economic_verified: Literal[False] = False
    state: Literal["MATCHED", "MANUAL_REVIEW_REQUIRED", "UNKNOWN"]
    manual_review_required: bool
    bank_ledger_verified: bool
    current_application_projection_matched: bool
    pending_application_projection_explained: bool
    audit: AuditVerification
    inventory: list[ReconciliationInventory]
    account_cash: list[ReconciliationAmount]
    position_principals: list[ReconciliationAmount]
    goal_ownership: list[ReconciliationGoal]
    actions: list[ReconciliationAction]
    bank_postings: list[ReconciliationPosting]
    issues: list[ReconciliationIssue]
    uncovered: list[ReconciliationIssue]
    input_hash: str
    limitations: list[str]


def compare_amount(
    entity_id: UUID,
    kind: Literal["ACCOUNT_CASH", "POSITION_PRINCIPAL", "GOAL_CASH", "GOAL_PRINCIPAL"],
    application: int | None,
    bank: int | None,
    head: BankHeadReference | None,
) -> ReconciliationAmount:
    """Missing independent facts remain null; signed differences are application minus bank."""
    bank = bank if head is not None else None
    difference = application - bank if application is not None and bank is not None else None
    return ReconciliationAmount(
        entity_id=entity_id,
        kind=kind,
        application_cents=application,
        bank_cents=bank,
        difference_cents=difference,
        state="MISSING" if difference is None else "DIFFERENCE" if difference else "MATCHED",
        bank_head=head,
    )


def report_state(
    issues: list[ReconciliationIssue], *, complete: bool
) -> Literal["MATCHED", "MANUAL_REVIEW_REQUIRED", "UNKNOWN"]:
    if any(issue.kind in {"DIFFERENCE", "INTEGRITY"} for issue in issues):
        return "MANUAL_REVIEW_REQUIRED"
    return "MATCHED" if complete and not issues else "UNKNOWN"


def head_reference(
    identity: UUID, ledger_key: str, sequence_number: int, occurred_at: datetime
) -> BankHeadReference:
    return BankHeadReference(
        posting_id=identity,
        ledger_key=ledger_key,
        sequence_number=sequence_number,
        occurred_at=occurred_at,
    )
