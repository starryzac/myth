"""Record actual server-side facts in the caller's original transaction.

These helpers never authorize, settle, repair, or commit. BEFORE dictionaries must
come from audit_subject_data on the actual mapped object before its mutation.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from app.db.models import (
    ActionPlan,
    ActionReceipt,
    ActionResourceReservation,
    AuditEvent,
    BankOperation,
    DecisionRun,
    EvidenceItem,
    ExternalBankFact,
    Goal,
    OwnedMixin,
    Policy,
    PolicyVersion,
    SimulatedBankPosting,
    SimulatedBankRedemption,
    Transaction,
)
from app.domain.bank_posting_codec import bank_posting_data
from app.domain.policy_configuration import configuration_hash
from app.services.policy_lifecycle import PolicyLifecycleError
from sqlalchemy import inspect, or_, select
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from app.domain.decision_trace_types import DecisionTrace

JsonObject = dict[str, Any]
SubjectRequest = tuple[str, UUID, Literal["BASIS", "BEFORE", "AFTER"], JsonObject | None]


def _json(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise PolicyLifecycleError("AUDIT_INVALID_CLOCK", "审计事实时间必须带时区")
        return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if type(value) is dict:
        return {key: _json(item) for key, item in value.items()}
    if type(value) is list:
        return [_json(item) for item in value]
    return value


def audit_subject_data(row: object) -> JsonObject:
    """Copy actual mapped columns now; never reconstruct a purported old row."""
    mapper = inspect(type(row))
    if mapper is None:
        raise PolicyLifecycleError("AUDIT_REFERENCE_ERROR", "审计副本必须来自实际映射对象", 409)
    full = {column.key: _json(getattr(row, column.key)) for column in mapper.column_attrs}
    return bank_posting_data(full) if isinstance(row, SimulatedBankPosting) else full


def _owned[OwnedRow: OwnedMixin](
    session: Session, model: type[OwnedRow], identity: UUID, user_id: UUID
) -> OwnedRow:
    row = session.get(model, identity)
    if row is None or row.user_id != user_id:
        raise PolicyLifecycleError("NOT_FOUND", "审计事实或关联对象不存在", 404)
    return row


def _root_run(session: Session, user_id: UUID, run_id: UUID) -> UUID:
    seen: set[UUID] = set()
    while True:
        if run_id in seen:
            raise PolicyLifecycleError("AUDIT_REFERENCE_ERROR", "决策父链循环", 409)
        seen.add(run_id)
        row = _owned(session, DecisionRun, run_id, user_id)
        if row.parent_run_id is None:
            return run_id
        run_id = row.parent_run_id


def _action_subjects(action: ActionPlan) -> list[SubjectRequest]:
    result: list[SubjectRequest] = [
        ("ACTION_PLAN", action.id, "AFTER", None),
        ("DECISION_RUN", action.decision_run_id, "BASIS", None),
        ("ACCOUNT", action.source_account_id, "BASIS", None),
    ]
    for kind, identity in [
        ("ACCOUNT", action.destination_account_id),
        ("POLICY_VERSION", action.policy_version_id),
        ("ASSET_PRODUCT", action.product_id),
        ("ASSET_POSITION", action.position_id),
        ("GOAL", action.goal_id),
    ]:
        if identity is not None:
            result.append((kind, identity, "BASIS", None))
    return result


def _record(
    session: Session,
    *,
    user_id: UUID,
    event_type: str,
    aggregate_type: str,
    aggregate_id: UUID,
    correlation_id: UUID,
    correlation_kind: str,
    occurred_at: datetime,
    observed_at: datetime,
    subjects: list[SubjectRequest],
    fact_key: str,
    run_id: UUID | None = None,
    action_id: UUID | None = None,
    receipt_id: UUID | None = None,
    context: JsonObject | None = None,
    observation: JsonObject | None = None,
    missing: list[UUID] | None = None,
    anchor_specs: list[tuple[str, UUID, str, str]] | None = None,
    status_change: tuple[str, UUID, str, str] | None = None,
    payload_version: int = 1,
) -> None:
    # Local imports avoid the decision-recording/policy hook module cycle.
    from app.domain.audit_chain_types import AuditIntent, AuditIntentV2
    from app.services.audit_chain import (
        append_audit_event,
        capture_audit_subject,
        capture_audit_subject_data,
        ensure_audit_epoch,
        find_audit_fact,
        subject_reference,
    )

    appended_at = datetime.now(UTC)
    epoch = ensure_audit_epoch(session, user_id, appended_at)
    existing = find_audit_fact(session, user_id, event_type, fact_key, epoch.id)
    if existing is not None:
        # Only business helpers for a fixed fact may return early; generic append
        # still rejects changed intent. No old state is recaptured with a new clock.
        from app.domain.audit_chain import parse_event, verify_event
        from app.services.audit_chain import verify_audit_chain

        if existing.canonical_text is None:
            raise PolicyLifecycleError("AUDIT_REFERENCE_ERROR", "现存事实没有原规范文本", 409)
        verify_event(parse_event(existing.canonical_text))
        verified = verify_audit_chain(session, user_id, epoch_id=epoch.id)
        # A declared pre-activation history gap remains LEGACY_UNAUDITED. It
        # does not invalidate the verified current chain or authorize money.
        if (
            verified.status not in {"VALID", "LEGACY_UNAUDITED"}
            or verified.chain_status != "VALID"
            or verified.reference_status not in {"VALID", "LEGACY_UNAUDITED"}
            or verified.errors
        ):
            raise PolicyLifecycleError("AUDIT_REFERENCE_ERROR", "现存事实或原内容未通过核验", 409)
        return
    references: list[Any] = []
    registered: dict[tuple[str, UUID, str], Any] = {}
    for kind, identity, role, before in subjects:
        key = kind, identity, role
        if key in registered:
            continue
        snapshot = (
            capture_audit_subject(session, user_id, epoch.id, kind, identity)
            if before is None
            else capture_audit_subject_data(session, user_id, epoch.id, kind, identity, before)
        )
        reference = subject_reference(snapshot, role)
        references.append(reference)
        registered[key] = reference
    anchors = []
    for kind, identity, digest, algorithm in anchor_specs or []:
        subject_kind = {
            "DECISION_TRACE": "DECISION_RUN",
            "EXECUTION_REQUEST": "ACTION_PLAN",
            "POLICY_CONFIGURATION": "POLICY_VERSION",
            "EVIDENCE_CONTENT": "EVIDENCE",
            "ACTION_RECEIPT": "ACTION_RECEIPT",
            "BANK_POSTING_SET": "BANK_OPERATION",
            "EXTERNAL_BANK_REQUEST": "BANK_EXTERNAL_FACT",
            "EXTERNAL_BANK_RESULT": "BANK_EXTERNAL_FACT",
            "EXTERNAL_BANK_POSTING_SET": "BANK_EXTERNAL_FACT",
            "EXTERNAL_BANK_PROJECTION": "BANK_EXTERNAL_FACT",
        }.get(kind)
        if subject_kind is None:
            raise PolicyLifecycleError("AUDIT_REFERENCE_ERROR", "审计内容锚类型未支持", 409)
        matching = [
            ref
            for ref in references
            if ref.kind == subject_kind and ref.id == identity and ref.role == "AFTER"
        ]
        if not matching:
            matching = [
                ref
                for ref in references
                if ref.kind == subject_kind and ref.id == identity and ref.role == "BASIS"
            ]
        if len(matching) != 1:
            raise PolicyLifecycleError("AUDIT_REFERENCE_ERROR", "审计内容锚缺少唯一原件", 409)
        anchors.append(
            {
                "kind": kind,
                "reference_id": identity,
                "snapshot_hash": matching[0].snapshot_hash,
                "digest": digest,
                "hash_algorithm": algorithm,
            }
        )
    changes = []
    if status_change is not None:
        kind, identity, before_status, after_status = status_change
        before_ref = registered.get((kind, identity, "BEFORE"))
        after_ref = registered.get((kind, identity, "AFTER"))
        if before_ref is None or after_ref is None:
            raise PolicyLifecycleError("AUDIT_REFERENCE_ERROR", "状态变化缺少真实前后副本", 409)
        changes.append(
            {
                "kind": kind,
                "id": identity,
                "field": "status",
                "before": before_status,
                "after": after_status,
                "before_snapshot_hash": before_ref.snapshot_hash,
                "after_snapshot_hash": after_ref.snapshot_hash,
            }
        )
    cause_query = select(AuditEvent).where(
        AuditEvent.user_id == user_id,
        AuditEvent.epoch_id == epoch.id,
        AuditEvent.correlation_id == correlation_id,
    )
    if action_id is not None:
        cause_query = cause_query.where(AuditEvent.action_plan_id == action_id)
    if payload_version == 2:
        cause_query = cause_query.where(
            AuditEvent.event_type == "EXTERNAL_BANK_FACT_SETTLED",
            AuditEvent.aggregate_type == "BANK_EXTERNAL_FACT",
            AuditEvent.aggregate_id == aggregate_id,
        )
        if event_type == "EXTERNAL_BANK_FACT_SETTLED":
            cause_query = cause_query.where(AuditEvent.id.is_(None))
    cause = session.scalars(cause_query.order_by(AuditEvent.sequence_number.desc())).first()
    payload: JsonObject = {
        "fact_key": fact_key,
        "correlation_kind": correlation_kind,
        "references": references,
        "anchors": anchors,
        "context": context or {},
    }
    if payload_version == 1:
        payload.update(
            {"changes": changes, "missing_evidence_ids": missing or [], "observation": observation}
        )
    intent_model = AuditIntent if payload_version == 1 else AuditIntentV2
    intent = intent_model.model_validate(
        {
            "user_id": user_id,
            "event_type": event_type,
            "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id,
            "correlation_id": correlation_id,
            "causation_id": None if cause is None else cause.id,
            "decision_run_id": run_id,
            "action_plan_id": action_id,
            "action_receipt_id": receipt_id,
            "idempotency_key": "audit:" + hashlib.sha256(fact_key.encode()).hexdigest(),
            "payload_version": payload_version,
            "payload": payload,
            "occurred_at": occurred_at,
        }
    )
    append_audit_event(session, intent, observed_at, appended_at)


def record_decision(session: Session, trace: DecisionTrace) -> None:
    subjects: list[SubjectRequest] = [("DECISION_RUN", trace.run_id, "AFTER", None)]
    subjects += [("EVIDENCE", row.id, "BASIS", None) for row in trace.sources]
    subjects += [("POLICY_VERSION", row.id, "BASIS", None) for row in trace.policies]
    if trace.action_id is not None:
        subjects += _action_subjects(_owned(session, ActionPlan, trace.action_id, trace.user_id))
    root = _root_run(session, trace.user_id, trace.run_id)
    if root != trace.run_id:
        subjects.append(("DECISION_RUN", root, "BASIS", None))
    _record(
        session,
        user_id=trace.user_id,
        event_type="DECISION_RECORDED",
        aggregate_type="DECISION_RUN",
        aggregate_id=trace.run_id,
        correlation_id=root,
        correlation_kind="DECISION_RUN",
        occurred_at=trace.as_of,
        observed_at=trace.as_of,
        subjects=subjects,
        fact_key=f"DECISION_RECORDED:{trace.run_id}",
        run_id=trace.run_id,
        action_id=trace.action_id,
        missing=[UUID(value) for value in trace.inputs.get("missing_evidence_references", [])],
        anchor_specs=[("DECISION_TRACE", trace.run_id, trace.trace_hash, "decision-trace-v1")],
    )


def record_action_created(session: Session, action: ActionPlan, now: datetime) -> None:
    _record(
        session,
        user_id=action.user_id,
        event_type="ACTION_CREATED",
        aggregate_type="ACTION_PLAN",
        aggregate_id=action.id,
        correlation_id=_root_run(session, action.user_id, action.decision_run_id),
        correlation_kind="DECISION_RUN",
        occurred_at=action.created_at,
        observed_at=now,
        subjects=_action_subjects(action),
        fact_key=f"ACTION_CREATED:{action.id}",
        run_id=action.decision_run_id,
        action_id=action.id,
        anchor_specs=[
            ("EXECUTION_REQUEST", action.id, action.request_hash, "configuration-sha256-v1")
        ],
    )


def record_action_transition(
    session: Session,
    action: ActionPlan,
    before_status: str,
    now: datetime,
    *,
    reason_code: str,
    cause_ref: str | None = None,
    details: JsonObject | None = None,
    before_data: JsonObject | None = None,
) -> None:
    if before_status == action.status and not (details or {}).get("released_claim_ids"):
        return
    if before_data is None or before_data.get("status") != before_status:
        raise PolicyLifecycleError("AUDIT_REFERENCE_ERROR", "状态变化未捕获实际前状态", 409)
    subjects = _action_subjects(action) + [("ACTION_PLAN", action.id, "BEFORE", before_data)]
    actual_details = details or {}
    for field, kind in [
        ("confirmation_evidence_id", "EVIDENCE"),
        ("reservation_run_id", "DECISION_RUN"),
    ]:
        if actual_details.get(field) is not None:
            subjects.append((kind, UUID(str(actual_details[field])), "BASIS", None))
    for identity in actual_details.get("policy_version_ids", []):
        subjects.append(("POLICY_VERSION", UUID(str(identity)), "BASIS", None))
    claims = session.scalars(
        select(ActionResourceReservation).where(
            ActionResourceReservation.user_id == action.user_id,
            ActionResourceReservation.action_plan_id == action.id,
        )
    ).all()
    subjects += [("RESOURCE_CLAIM", row.id, "AFTER", None) for row in claims]
    facts = {"reason_code": reason_code, "cause_ref": cause_ref, "details": details or {}}
    identity = configuration_hash(
        {
            "before": before_data,
            "after": audit_subject_data(action),
            "context": facts,
        }
    )
    _record(
        session,
        user_id=action.user_id,
        event_type="ACTION_STATE_CHANGED",
        aggregate_type="ACTION_PLAN",
        aggregate_id=action.id,
        correlation_id=_root_run(session, action.user_id, action.decision_run_id),
        correlation_kind="DECISION_RUN",
        occurred_at=now,
        observed_at=now,
        subjects=subjects,
        fact_key=f"ACTION_STATE_CHANGED:{action.id}:{identity}",
        run_id=action.decision_run_id,
        action_id=action.id,
        context=facts,
        status_change=("ACTION_PLAN", action.id, before_status, action.status)
        if before_status != action.status
        else None,
    )


def _bank(session: Session, row: BankOperation | SimulatedBankRedemption) -> BankOperation:
    return _owned(session, BankOperation, row.id, row.user_id)


def _postings(session: Session, operation: BankOperation) -> list[SimulatedBankPosting]:
    return list(
        session.scalars(
            select(SimulatedBankPosting)
            .where(
                SimulatedBankPosting.user_id == operation.user_id,
                SimulatedBankPosting.operation_id == operation.id,
                SimulatedBankPosting.entry_kind != "OPENING",
            )
            .order_by(
                SimulatedBankPosting.ledger_key,
                SimulatedBankPosting.sequence_number,
                SimulatedBankPosting.id,
            )
        ).all()
    )


def _bank_fact(
    session: Session, row: BankOperation | SimulatedBankRedemption, now: datetime, *, settled: bool
) -> None:
    operation = _bank(session, row)
    action = _owned(session, ActionPlan, operation.action_plan_id, operation.user_id)
    subjects = _action_subjects(action) + [("BANK_OPERATION", operation.id, "AFTER", None)]
    if operation.legacy_redemption_id is not None:
        subjects.append(("BANK_REDEMPTION", operation.legacy_redemption_id, "AFTER", None))
    anchors = [("EXECUTION_REQUEST", action.id, action.request_hash, "configuration-sha256-v1")]
    occurred = operation.requested_at
    if settled:
        if operation.status != "SETTLED" or operation.settled_at is None:
            raise PolicyLifecycleError("AUDIT_REFERENCE_ERROR", "不能记录尚未结算的银行事实", 409)
        postings = _postings(session, operation)
        if not postings:
            raise PolicyLifecycleError("AUDIT_REFERENCE_ERROR", "银行结算缺少实际分录", 409)
        subjects += [("BANK_POSTING", posting.id, "AFTER", None) for posting in postings]
        from app.domain.audit_chain import posting_set_digest

        digest = posting_set_digest(audit_subject_data(p) for p in postings)
        anchors.append(("BANK_POSTING_SET", operation.id, digest, "configuration-sha256-v1"))
        occurred = operation.settled_at
    event_type = "BANK_SETTLED" if settled else "BANK_ACCEPTED"
    _record(
        session,
        user_id=operation.user_id,
        event_type=event_type,
        aggregate_type="BANK_OPERATION",
        aggregate_id=operation.id,
        correlation_id=_root_run(session, action.user_id, action.decision_run_id),
        correlation_kind="DECISION_RUN",
        occurred_at=occurred,
        observed_at=now,
        subjects=subjects,
        fact_key=f"{event_type}:{operation.id}",
        run_id=action.decision_run_id,
        action_id=action.id,
        anchor_specs=anchors,
    )


def record_bank_accepted(
    session: Session, operation: BankOperation | SimulatedBankRedemption, now: datetime
) -> None:
    _bank_fact(session, operation, now, settled=False)


def record_bank_settled(
    session: Session, operation: BankOperation | SimulatedBankRedemption, now: datetime
) -> None:
    _bank_fact(session, operation, now, settled=True)


def _external_postings(session: Session, fact: ExternalBankFact) -> list[SimulatedBankPosting]:
    return list(
        session.scalars(
            select(SimulatedBankPosting)
            .where(
                SimulatedBankPosting.user_id == fact.user_id,
                SimulatedBankPosting.external_fact_id == fact.id,
            )
            .order_by(SimulatedBankPosting.id)
        )
    )


def _external_anchors(fact: ExternalBankFact) -> list[tuple[str, UUID, str, str]]:
    if fact.bank_result is None or fact.bank_result_hash is None:
        raise PolicyLifecycleError("AUDIT_REFERENCE_ERROR", "银行事实缺少真实结算原件", 409)
    algorithm = "bank-external-canonical-sha256-v1"
    return [
        ("EXTERNAL_BANK_REQUEST", fact.id, fact.request_hash, algorithm),
        ("EXTERNAL_BANK_RESULT", fact.id, fact.bank_result_hash, algorithm),
        ("EXTERNAL_BANK_POSTING_SET", fact.id, fact.bank_result["posting_digest"], algorithm),
    ]


def record_external_bank_settled(session: Session, fact: ExternalBankFact, now: datetime) -> None:
    """Record a committed external economic fact without Agent authority or a fake action."""
    if fact.settled_at is None or fact.bank_status != "SETTLED":
        raise PolicyLifecycleError("AUDIT_REFERENCE_ERROR", "外部银行事实尚未真实结算", 409)
    postings = _external_postings(session, fact)
    subjects: list[SubjectRequest] = [
        ("BANK_EXTERNAL_FACT", fact.id, "AFTER", None),
        ("ACCOUNT", fact.account_id, "BASIS", None),
    ]
    subjects += [("BANK_POSTING", row.id, "BASIS", None) for row in postings]
    _record(
        session,
        user_id=fact.user_id,
        event_type="EXTERNAL_BANK_FACT_SETTLED",
        aggregate_type="BANK_EXTERNAL_FACT",
        aggregate_id=fact.id,
        correlation_id=fact.id,
        correlation_kind="EXTERNAL_BANK_FACT",
        occurred_at=fact.settled_at,
        observed_at=now,
        subjects=subjects,
        fact_key=f"EXTERNAL_BANK_FACT_SETTLED:{fact.id}",
        anchor_specs=_external_anchors(fact),
        payload_version=2,
    )


def record_external_bank_projected(session: Session, fact: ExternalBankFact, now: datetime) -> None:
    """Capture the actual one-time external projection and its retained proof predecessors."""
    if (
        fact.projected_at is None
        or fact.projection_result is None
        or fact.projection_result_hash is None
    ):
        raise PolicyLifecycleError("AUDIT_REFERENCE_ERROR", "外部事实缺少实际导入原件", 409)
    result = fact.projection_result
    subjects: list[SubjectRequest] = [
        ("BANK_EXTERNAL_FACT", fact.id, "AFTER", None),
        ("ACCOUNT", fact.account_id, "AFTER", None),
        ("TRANSACTION", UUID(result["transaction_id"]), "AFTER", None),
    ]
    subjects += [
        ("BANK_POSTING", row.id, "BASIS", None) for row in _external_postings(session, fact)
    ]
    proof_ids = {
        UUID(result[field])
        for field in (
            "transaction_evidence_id",
            "balance_evidence_id",
            "income_evidence_id",
            "exposure_evidence_id",
        )
        if result.get(field) is not None
    }
    proof_ids.update(UUID(identity) for identity in result["proof_successor_ids"])
    seen: set[UUID] = set()
    income_origins: set[UUID] = set()

    def retain_income_origins(proof: EvidenceItem) -> None:
        if proof.content.get("protocol") == "new-funds-ledger-v1":
            income_origins.update(UUID(lot["transaction_id"]) for lot in proof.content["lots"])
        elif proof.content.get("protocol") == "new-funds-ledger-v2":
            income_origins.update(
                UUID(origin["origin_transaction_id"]) for origin in proof.content["origins"]
            )

    for identity in sorted(proof_ids, key=str):
        current = _owned(session, EvidenceItem, identity, fact.user_id)
        retain_income_origins(current)
        subjects.append(("EVIDENCE", current.id, "AFTER", None))
        while current.supersedes_id is not None:
            if current.supersedes_id in seen:
                break
            seen.add(current.supersedes_id)
            current = _owned(session, EvidenceItem, current.supersedes_id, fact.user_id)
            retain_income_origins(current)
            subjects.append(("EVIDENCE", current.id, "BASIS", None))
    for identity in sorted(income_origins, key=str):
        transaction = _owned(session, Transaction, identity, fact.user_id)
        subjects.append(("TRANSACTION", transaction.id, "BASIS", None))
        if transaction.evidence_id is None:
            raise PolicyLifecycleError("AUDIT_REFERENCE_ERROR", "收入原件缺少实际银行证明", 409)
        subjects.append(("EVIDENCE", transaction.evidence_id, "BASIS", None))
    _record(
        session,
        user_id=fact.user_id,
        event_type="EXTERNAL_BANK_FACT_PROJECTED",
        aggregate_type="BANK_EXTERNAL_FACT",
        aggregate_id=fact.id,
        correlation_id=fact.id,
        correlation_kind="EXTERNAL_BANK_FACT",
        occurred_at=fact.projected_at,
        observed_at=now,
        subjects=subjects,
        fact_key=f"EXTERNAL_BANK_FACT_PROJECTED:{fact.id}",
        anchor_specs=_external_anchors(fact)
        + [
            (
                "EXTERNAL_BANK_PROJECTION",
                fact.id,
                fact.projection_result_hash,
                "bank-external-canonical-sha256-v1",
            )
        ],
        payload_version=2,
    )


def record_action_projected(
    session: Session,
    action: ActionPlan,
    operation: BankOperation | SimulatedBankRedemption,
    receipt: ActionReceipt,
    now: datetime,
) -> None:
    from app.domain.audit_chain import posting_set_digest, receipt_digest

    bank = _bank(session, operation)
    postings = _postings(session, bank)
    subjects = (
        _action_subjects(action)
        + [
            ("BANK_OPERATION", bank.id, "BASIS", None),
            ("ACTION_RECEIPT", receipt.id, "AFTER", None),
        ]
        + [("BANK_POSTING", posting.id, "BASIS", None) for posting in postings]
    )
    identities = list(receipt.response.get("transaction_ids", []))
    if receipt.response.get("transaction_id") is not None:
        identities.append(receipt.response["transaction_id"])
    transactions = [
        _owned(session, Transaction, UUID(identity), action.user_id) for identity in identities
    ]
    for transaction in transactions:
        subjects.append(("TRANSACTION", transaction.id, "AFTER", None))
        if transaction.evidence_id is not None:
            subjects.append(("EVIDENCE", transaction.evidence_id, "AFTER", None))
    _record(
        session,
        user_id=action.user_id,
        event_type="ACTION_PROJECTED",
        aggregate_type="ACTION_RECEIPT",
        aggregate_id=receipt.id,
        correlation_id=_root_run(session, action.user_id, action.decision_run_id),
        correlation_kind="DECISION_RUN",
        occurred_at=receipt.occurred_at,
        observed_at=now,
        subjects=subjects,
        fact_key=f"ACTION_PROJECTED:{receipt.id}",
        run_id=action.decision_run_id,
        action_id=action.id,
        receipt_id=receipt.id,
        anchor_specs=[
            (
                "ACTION_RECEIPT",
                receipt.id,
                receipt_digest(audit_subject_data(receipt)),
                "configuration-sha256-v1",
            ),
            (
                "BANK_POSTING_SET",
                bank.id,
                posting_set_digest(audit_subject_data(p) for p in postings),
                "configuration-sha256-v1",
            ),
        ],
    )


def record_recovery_observed(
    session: Session,
    run: DecisionRun,
    now: datetime,
    *,
    kind: str,
    action_id: UUID | None = None,
    request: SimulatedBankRedemption | None = None,
    error_code: str | None = None,
    details: JsonObject | None = None,
) -> None:
    original_kind = kind
    kind = {"BANK_REQUEST_NOT_CONFIRMED": "BANK_ERROR", "COMPLETED": "RUN_COMPLETED"}.get(
        kind, kind
    )
    subjects: list[SubjectRequest] = [("DECISION_RUN", run.id, "AFTER", None)]
    if action_id is not None:
        subjects += _action_subjects(_owned(session, ActionPlan, action_id, run.user_id))
    if request is not None:
        subjects += [
            ("BANK_REDEMPTION", request.id, "BASIS", None),
            ("BANK_OPERATION", request.id, "BASIS", None),
        ]
    bank_status = None if request is None else request.status
    identity = configuration_hash(
        {
            "kind": kind,
            "run": str(run.id),
            "action": str(action_id),
            "request": None if request is None else str(request.id),
            "error": error_code,
            "bank_status": bank_status,
        }
    )
    _record(
        session,
        user_id=run.user_id,
        event_type="RECOVERY_OBSERVED",
        aggregate_type="DECISION_RUN",
        aggregate_id=run.id,
        correlation_id=_root_run(session, run.user_id, run.id),
        correlation_kind="DECISION_RUN",
        occurred_at=now,
        observed_at=now,
        subjects=subjects,
        fact_key=f"RECOVERY_OBSERVED:{identity}",
        run_id=run.id,
        action_id=action_id,
        context={"reason_code": error_code or original_kind, "details": details or {}},
        observation={
            "kind": kind,
            "error_code": error_code,
            "bank_status": bank_status,
            "run_id": run.id,
            "action_id": action_id,
            "request_id": None if request is None else request.id,
        },
    )


def record_policy_version(
    session: Session,
    policy: Policy,
    version: PolicyVersion,
    previous: PolicyVersion | None,
    now: datetime,
) -> None:
    subjects: list[SubjectRequest] = [
        ("POLICY", policy.id, "AFTER", None),
        ("POLICY_VERSION", version.id, "AFTER", None),
    ]
    subjects += [("EVIDENCE", UUID(identity), "BASIS", None) for identity in version.evidence_ids]
    if previous is not None:
        subjects.append(("POLICY_VERSION", previous.id, "BASIS", None))
    _record(
        session,
        user_id=policy.user_id,
        event_type="POLICY_VERSION_CONFIRMED",
        aggregate_type="POLICY_VERSION",
        aggregate_id=version.id,
        correlation_id=policy.id,
        correlation_kind="POLICY",
        occurred_at=version.confirmed_at or now,
        observed_at=now,
        subjects=subjects,
        fact_key=f"POLICY_VERSION_CONFIRMED:{version.id}",
        context={
            "details": {"previous_version_id": None if previous is None else str(previous.id)}
        },
        anchor_specs=[
            ("POLICY_CONFIGURATION", version.id, version.content_hash, "configuration-sha256-v1")
        ],
    )


def record_policy_state(
    session: Session,
    policy: Policy,
    version: PolicyVersion,
    before_status: str,
    now: datetime,
    *,
    reason_code: str,
    before_data: JsonObject | None = None,
) -> None:
    if before_status == policy.status:
        return
    if before_data is None or before_data.get("status") != before_status:
        raise PolicyLifecycleError("AUDIT_REFERENCE_ERROR", "策略变化未捕获实际前状态", 409)
    _record(
        session,
        user_id=policy.user_id,
        event_type="POLICY_STATE_CHANGED",
        aggregate_type="POLICY",
        aggregate_id=policy.id,
        correlation_id=policy.id,
        correlation_kind="POLICY",
        occurred_at=now,
        observed_at=now,
        subjects=[
            ("POLICY", policy.id, "BEFORE", before_data),
            ("POLICY", policy.id, "AFTER", None),
            ("POLICY_VERSION", version.id, "BASIS", None),
        ],
        fact_key=f"POLICY_STATE_CHANGED:{policy.id}:"
        + configuration_hash(
            {
                "before": before_data,
                "after": audit_subject_data(policy),
                "reason": reason_code,
            }
        ),
        context={"reason_code": reason_code},
        status_change=("POLICY", policy.id, before_status, policy.status),
    )


def record_goal_initialized(session: Session, goal: Goal, now: datetime) -> None:
    if goal.account_id is None:
        raise PolicyLifecycleError("AUDIT_REFERENCE_ERROR", "目标初始化缺少实际目标账户", 409)
    proofs = session.scalars(
        select(EvidenceItem).where(
            EvidenceItem.user_id == goal.user_id,
            or_(
                EvidenceItem.content["goal_id"].astext == str(goal.id),
                EvidenceItem.content["policy_version_id"].astext == str(goal.policy_version_id),
            ),
        )
    ).all()
    subjects: list[SubjectRequest] = [
        ("GOAL", goal.id, "AFTER", None),
        ("ACCOUNT", goal.account_id, "AFTER", None),
        ("POLICY", goal.policy_id, "BASIS", None),
        ("POLICY_VERSION", goal.policy_version_id, "BASIS", None),
    ]
    subjects.extend(("EVIDENCE", proof.id, "BASIS", None) for proof in proofs)
    _record(
        session,
        user_id=goal.user_id,
        event_type="GOAL_INITIALIZED",
        aggregate_type="GOAL",
        aggregate_id=goal.id,
        correlation_id=goal.id,
        correlation_kind="GOAL",
        occurred_at=goal.created_at,
        observed_at=now,
        subjects=subjects,
        fact_key=f"GOAL_INITIALIZED:{goal.id}",
    )
