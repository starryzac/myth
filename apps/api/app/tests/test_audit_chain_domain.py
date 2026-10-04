"""Pure public audit contracts; no database, clock lookup or financial recomputation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import UUID

import pytest
from app.domain.audit_chain import canonical_bytes

if TYPE_CHECKING:
    from app.domain.audit_chain_types import AuditEnvelope, AuditHead, ReferenceBundle


def test_canonical_facts_preserve_signed_money_and_normalize_actual_utc() -> None:
    encoded = canonical_bytes(
        {
            "发生": datetime(2026, 10, 4, 8, 0, 0, 123, tzinfo=timezone(timedelta(hours=8))),
            "id": UUID("00000000-0000-0000-0000-000000000001"),
            "delta_cents": -200,
        }
    )
    assert (
        encoded
        == (
            '{"delta_cents":-200,"id":"00000000-0000-0000-0000-000000000001",'
            '"发生":"2026-10-04T00:00:00.000123Z"}'
        ).encode()
    )


def test_original_subject_roundtrip_preserves_bad_claims_and_float_types() -> None:
    from app.domain.audit_chain import (
        build_subject,
        parse_subject,
        subject_canonical_text,
        subject_hash,
    )

    user = UUID("00000000-0000-0000-0000-000000000002")
    subject = build_subject(
        user_id=user,
        epoch_id=UUID("00000000-0000-0000-0000-000000000003"),
        kind="EVIDENCE",
        id=UUID("00000000-0000-0000-0000-000000000004"),
        scope="TENANT",
        data={
            "id": "00000000-0000-0000-0000-000000000004",
            "user_id": str(user),
            "content": {
                "user_id": "wrong-raw-owner",
                "balance_cents": True,
                "quantile": 0.8,
                "original_float": 1.0,
                "negative_zero": -0.0,
            },
        },
    )
    text = subject_canonical_text(subject)
    restored = parse_subject(text)
    assert restored == subject
    assert '"original_float":1.0' in text and '"negative_zero":-0.0' in text
    assert restored.data["content"]["balance_cents"] is True
    assert subject_hash(restored) == subject_hash(subject)


def test_epoch_event_roundtrip_hash_and_replay_keep_first_observation() -> None:
    import pytest
    from app.domain.audit_chain import (
        build_event,
        event_canonical_text,
        parse_event,
        same_intent,
        verify_event,
    )
    from app.domain.audit_chain_types import AuditEpochTransition, AuditIntent, AuditPayload

    user = UUID("00000000-0000-0000-0000-000000000002")
    epoch = UUID("00000000-0000-0000-0000-000000000003")
    now = datetime(2026, 10, 4, tzinfo=UTC)
    intent = AuditIntent(
        user_id=user,
        event_type="EPOCH_STARTED",
        aggregate_type="EPOCH",
        aggregate_id=epoch,
        correlation_id=epoch,
        idempotency_key="start",
        occurred_at=now,
        payload=AuditPayload(
            fact_key="start",
            correlation_kind="EPOCH",
            epoch_transition=AuditEpochTransition(kind="INIT", legacy_history=True),
        ),
    )
    event = build_event(
        intent,
        event_id=UUID("00000000-0000-0000-0000-000000000005"),
        epoch_id=epoch,
        sequence_number=1,
        previous_hash=None,
        observed_at=now,
        appended_at=now + timedelta(seconds=1),
    )
    assert parse_event(event_canonical_text(event)) == event
    assert same_intent(event, intent)
    later = event.model_copy(update={"observed_at": now + timedelta(days=1)})
    assert same_intent(event, intent) and event.observed_at == now
    with pytest.raises(ValueError):
        verify_event(later)
    event.payload.context.details["injected"] = True
    with pytest.raises(ValueError):
        verify_event(event)


def recorded_chain() -> tuple[list[AuditEnvelope], AuditHead, ReferenceBundle]:
    from app.domain.audit_chain import build_event, build_subject, subject_hash
    from app.domain.audit_chain_types import (
        AuditAnchor,
        AuditEpochTransition,
        AuditHead,
        AuditIntent,
        AuditPayload,
        AuditReference,
        ReferenceBundle,
    )
    from app.domain.decision_trace import build_trace
    from app.domain.policy_configuration import configuration_hash

    user = UUID("00000000-0000-0000-0000-000000000002")
    epoch = UUID("00000000-0000-0000-0000-000000000003")
    run = UUID("00000000-0000-0000-0000-000000000006")
    now = datetime(2026, 10, 4, tzinfo=UTC)
    intent = AuditIntent(
        user_id=user,
        event_type="EPOCH_STARTED",
        aggregate_type="EPOCH",
        aggregate_id=epoch,
        correlation_id=epoch,
        idempotency_key="start",
        occurred_at=now,
        payload=AuditPayload(
            fact_key="start",
            correlation_kind="EPOCH",
            epoch_transition=AuditEpochTransition(kind="INIT"),
        ),
    )
    first = build_event(
        intent,
        event_id=UUID("00000000-0000-0000-0000-000000000005"),
        epoch_id=epoch,
        sequence_number=1,
        previous_hash=None,
        observed_at=now,
        appended_at=now,
    )
    trace = build_trace(
        run_id=run,
        user_id=user,
        phase="EVALUATION",
        as_of=now,
        algorithm_versions={"trace": "decision-trace-v1"},
        inputs={},
        outcome={"level": "BLOCKED", "reasons": ["INSUFFICIENT_EVIDENCE"]},
    )
    snapshot = {"decision_trace": trace.model_dump(mode="json")}
    subject = build_subject(
        user_id=user,
        epoch_id=epoch,
        kind="DECISION_RUN",
        id=run,
        data={
            "id": str(run),
            "user_id": str(user),
            "as_of": now,
            "input_snapshot": snapshot,
            "snapshot_hash": configuration_hash(snapshot),
        },
    )
    digest = subject_hash(subject)
    payload = AuditPayload(
        fact_key="decision",
        correlation_kind="DECISION_RUN",
        references=[
            AuditReference(kind="DECISION_RUN", id=run, user_id=user, snapshot_hash=digest)
        ],
        anchors=[
            AuditAnchor(
                kind="DECISION_TRACE",
                reference_id=run,
                snapshot_hash=digest,
                digest=trace.trace_hash,
                hash_algorithm="decision-trace-v1",
            )
        ],
    )
    second = build_event(
        AuditIntent(
            user_id=user,
            event_type="DECISION_RECORDED",
            aggregate_type="DECISION_RUN",
            aggregate_id=run,
            correlation_id=run,
            decision_run_id=run,
            idempotency_key="decision",
            payload=payload,
            occurred_at=now,
        ),
        event_id=UUID("00000000-0000-0000-0000-000000000007"),
        epoch_id=epoch,
        sequence_number=2,
        previous_hash=first.event_hash,
        observed_at=now,
        appended_at=now,
    )
    head = AuditHead(
        user_id=user,
        epoch_id=epoch,
        epoch_number=1,
        event_count=2,
        last_sequence=2,
        last_event_id=second.id,
        last_event_hash=second.event_hash,
        genesis_event_id=first.id,
        genesis_event_hash=first.event_hash,
    )
    return (
        [cast("AuditEnvelope", first), cast("AuditEnvelope", second)],
        head,
        ReferenceBundle(subjects=[subject]),
    )


def test_full_original_decision_chain_detects_deleted_tail_and_empty_chain() -> None:
    from app.domain.audit_chain import build_checkpoint, verify_epoch

    events, head, references = recorded_chain()
    checkpoint = build_checkpoint(head, captured_at=datetime(2026, 10, 4, 1, tzinfo=UTC))
    valid = verify_epoch(
        events,
        head=head,
        expected_user_id=head.user_id,
        references=references,
        checkpoint=checkpoint,
    )
    assert valid.status == "VALID" and valid.actual_count == 2
    truncated = verify_epoch(
        events[:1], head=head, expected_user_id=head.user_id, references=references
    )
    cleared = verify_epoch([], head=head, expected_user_id=head.user_id, references=references)
    assert truncated.status == cleared.status == "INTEGRITY_ERROR"
    assert "HEAD_MISMATCH" in {error.code for error in truncated.errors}
    assert "HEAD_MISMATCH" in {error.code for error in cleared.errors}


def test_epoch_seal_is_independent_of_tail_and_binds_original_head() -> None:
    import pytest
    from app.domain.audit_chain import build_seal, parse_seal, seal_canonical_text, verify_seal

    _, head, _ = recorded_chain()
    sealed = head.model_copy(update={"status": "SEALED"})
    seal = build_seal(
        user_id=head.user_id,
        epoch_id=head.epoch_id,
        epoch_number=1,
        head=sealed,
        archive_manifest_hash="a" * 64,
        archive_record_counts={"DECISION_RUN": 1},
        reset_key="reset-1",
        reason="explicit simulated reset",
        principal="demo",
        sealed_at=datetime(2026, 10, 4, 1, tzinfo=UTC),
    )
    assert parse_seal(seal_canonical_text(seal)) == seal
    assert seal.seal_hash != sealed.last_event_hash
    with pytest.raises(ValueError):
        verify_seal(seal.model_copy(update={"archive_record_counts": {"DECISION_RUN": 0}}))


def test_original_bad_evidence_hash_can_be_anchored_without_financial_validity() -> None:
    from app.domain.audit_chain import build_event, build_subject, subject_hash, verify_epoch
    from app.domain.audit_chain_types import AuditAnchor, AuditReference

    events, head, references = recorded_chain()
    source_id = UUID("00000000-0000-0000-0000-000000000008")
    source = build_subject(
        user_id=head.user_id,
        epoch_id=head.epoch_id,
        kind="EVIDENCE",
        id=source_id,
        data={
            "id": str(source_id),
            "user_id": str(head.user_id),
            "content": {"user_id": "false raw owner", "balance_cents": True},
            "content_hash": "f" * 64,
            "evidence_level": "BANK_CONFIRMED",
            "source_type": "BANK_BALANCE",
            "source_ref": "original",
            "status": "VALID",
            "observed_at": events[1].observed_at,
            "valid_from": events[1].observed_at,
            "valid_to": None,
            "supersedes_id": None,
        },
    )
    digest = subject_hash(source)
    payload = events[1].payload.model_copy(
        update={
            "references": events[1].payload.references
            + [
                AuditReference(
                    kind="EVIDENCE", id=source_id, user_id=head.user_id, snapshot_hash=digest
                )
            ],
            "anchors": events[1].payload.anchors
            + [
                AuditAnchor(
                    kind="EVIDENCE_CONTENT",
                    reference_id=source_id,
                    snapshot_hash=digest,
                    digest="f" * 64,
                    hash_algorithm="configuration-sha256-v1",
                )
            ],
        }
    )
    event = build_event(
        events[1].model_copy(update={"payload": payload}),
        event_id=events[1].id,
        epoch_id=head.epoch_id,
        sequence_number=2,
        previous_hash=events[0].event_hash,
        observed_at=events[1].observed_at,
        appended_at=events[1].appended_at,
    )
    new_head = head.model_copy(update={"last_event_hash": event.event_hash})
    bundle = references.model_copy(update={"subjects": references.subjects + [source]})
    verified = verify_epoch(
        [events[0], event], head=new_head, expected_user_id=head.user_id, references=bundle
    )
    assert verified.status == "VALID"


def test_bank_posting_and_receipt_digests_bind_the_full_original_money_set() -> None:
    import pytest
    from app.domain.audit_chain import posting_set_digest, receipt_digest

    _, _, postings, _, _ = transfer_originals()
    first, second = postings
    assert posting_set_digest([second, first]) == posting_set_digest([first, second])
    assert posting_set_digest([first]) != posting_set_digest([first, second])
    receipt = {
        "id": "00000000-0000-0000-0000-000000000011",
        "executed_cents": 100,
        "fee_cents": 0,
        "loss_cents": 0,
        "response": {"transaction_ids": [first["id"]]},
    }
    assert receipt_digest(receipt) != receipt_digest({**receipt, "fee_cents": 1})
    with pytest.raises(ValueError):
        posting_set_digest([{**first, "delta_cents": True}])
    with pytest.raises(ValueError):
        receipt_digest({**receipt, "executed_cents": 100.0})


@pytest.mark.parametrize(
    "field,value",
    [("schema_version", "audit-event-v99"), ("canonical_version", "audit-canonical-json-v99")],
)
def test_unknown_original_protocol_is_unsupported_not_a_fake_missing_tail(
    field: str, value: str
) -> None:
    from app.domain.audit_chain import verify_epoch

    events, head, references = recorded_chain()
    raw = events[1].model_dump(mode="json")
    raw[field] = value
    result = verify_epoch(
        [events[0], raw], head=head, expected_user_id=head.user_id, references=references
    )
    assert result.status == "UNSUPPORTED_VERSION"
    assert "HEAD_MISMATCH" not in {error.code for error in result.errors}


@pytest.mark.parametrize(
    "kind,anchor_kind,field,hash_field",
    [
        ("POLICY_VERSION", "POLICY_CONFIGURATION", "configuration", "content_hash"),
        ("ACTION_PLAN", "EXECUTION_REQUEST", "request", "request_hash"),
    ],
)
def test_original_configuration_anchors_and_current_immutable_content(
    kind: str, anchor_kind: str, field: str, hash_field: str
) -> None:
    from app.domain.audit_chain import build_event, build_subject, subject_hash, verify_epoch
    from app.domain.audit_chain_types import AuditAnchor, AuditReference
    from app.domain.policy_configuration import configuration_hash

    events, head, bundle = recorded_chain()
    identity = UUID("00000000-0000-0000-0000-000000000012")
    value = {"method": {"quantile": 0.8}} if kind == "POLICY_VERSION" else {"amount_cents": 100}
    data: dict[str, Any] = {
        "id": str(identity),
        "user_id": str(head.user_id),
        field: value,
        hash_field: configuration_hash(value),
    }
    if kind == "ACTION_PLAN":
        data["status"] = "PLANNED"
    original = build_subject(
        user_id=head.user_id, epoch_id=head.epoch_id, kind=kind, id=identity, data=data
    )
    digest = subject_hash(original)
    payload = events[1].payload.model_copy(
        update={
            "references": events[1].payload.references
            + [AuditReference(kind=kind, id=identity, user_id=head.user_id, snapshot_hash=digest)],
            "anchors": events[1].payload.anchors
            + [
                AuditAnchor(
                    kind=anchor_kind,
                    reference_id=identity,
                    snapshot_hash=digest,
                    digest=data[hash_field],
                    hash_algorithm="configuration-sha256-v1",
                )
            ],
        }
    )
    event = build_event(
        events[1].model_copy(update={"payload": payload}),
        event_id=events[1].id,
        epoch_id=head.epoch_id,
        sequence_number=2,
        previous_hash=events[0].event_hash,
        observed_at=events[1].observed_at,
        appended_at=events[1].appended_at,
    )
    head = head.model_copy(update={"last_event_hash": event.event_hash})
    current = build_subject(
        user_id=head.user_id,
        epoch_id=head.epoch_id,
        kind=kind,
        id=identity,
        data={**data, **({"status": "SUCCEEDED"} if kind == "ACTION_PLAN" else {})},
    )
    bundle = bundle.model_copy(
        update={"subjects": bundle.subjects + [original], "current_subjects": [current]}
    )
    assert (
        verify_epoch(
            [events[0], event], head=head, expected_user_id=head.user_id, references=bundle
        ).status
        == "VALID"
    )
    changed_value = {"amount_cents": 101}
    corrupted = build_subject(
        user_id=head.user_id,
        epoch_id=head.epoch_id,
        kind=kind,
        id=identity,
        data={**data, field: changed_value, hash_field: configuration_hash(changed_value)},
    )
    broken = bundle.model_copy(update={"current_subjects": [corrupted]})
    assert (
        verify_epoch(
            [events[0], event], head=head, expected_user_id=head.user_id, references=broken
        ).status
        == "INTEGRITY_ERROR"
    )


def transfer_originals() -> tuple[
    dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, Any], datetime
]:
    from uuid import uuid5

    from app.domain.execution import execution_effect_hash
    from app.domain.execution_types import BankCommand, CashUse, ExecutionEffect
    from app.domain.policy_configuration import configuration_hash

    user = UUID("00000000-0000-0000-0000-000000000002")
    action_id = UUID("00000000-0000-0000-0000-000000000020")
    source = UUID("00000000-0000-0000-0000-000000000021")
    target = UUID("00000000-0000-0000-0000-000000000022")
    now = datetime(2026, 10, 4, tzinfo=UTC)
    effect = ExecutionEffect(
        operation_id=action_id,
        user_id=user,
        business_key="transfer:original",
        action_type="TRANSFER_INTERNAL",
        amount_cents=100,
        cash_uses=[CashUse(account_id=source, amount_cents=100)],
        destination_account_id=target,
        valid_from=now,
        expires_at=now + timedelta(hours=1),
    )
    command = BankCommand(effect=effect, effect_hash=execution_effect_hash(effect)).model_dump(
        mode="json"
    )
    request = {"execution": command}
    action = {
        "id": str(action_id),
        "user_id": str(user),
        "action_type": "TRANSFER_INTERNAL",
        "amount_cents": 100,
        "source_account_id": str(source),
        "destination_account_id": str(target),
        "goal_id": None,
        "product_id": None,
        "policy_version_id": None,
        "position_id": None,
        "request": request,
        "request_hash": configuration_hash(request),
        "idempotency_key": "transfer-original",
        "status": "SUCCEEDED",
    }
    operation = {
        "id": str(action_id),
        "user_id": str(user),
        "action_plan_id": str(action_id),
        "operation_type": "TRANSFER_INTERNAL",
        "business_key": effect.business_key,
        "request": command,
        "request_hash": configuration_hash(command),
        "idempotency_key": action["idempotency_key"],
        "requested_at": now,
        "available_at": now,
        "settled_at": now,
        "status": "SETTLED",
    }
    postings: list[dict[str, Any]] = [
        {
            "id": str(uuid5(action_id, "posting:cash:" + str(account))),
            "user_id": str(user),
            "operation_id": str(action_id),
            "leg_ref": "cash:" + str(account),
            "ledger_key": "CASH:" + str(account),
            "ledger_dimension": "ECONOMIC",
            "ledger_metadata": {},
            "redemption_id": None,
            "previous_posting_id": str(uuid5(user, "execution-opening:CASH:" + str(account))),
            "sequence_number": 2,
            "entry_kind": "EXECUTION",
            "delta_cents": delta,
            "balance_before_cents": before,
            "balance_after_cents": before + delta,
            "account_id": str(account),
            "position_id": None,
            "occurred_at": now,
            "created_at": now,
        }
        for account, delta, before in [(source, -100, 1000), (target, 100, 0)]
    ]
    receipt = {
        "id": str(uuid5(action_id, "receipt")),
        "user_id": str(user),
        "action_plan_id": str(action_id),
        "status": "SUCCEEDED",
        "attempt_number": 1,
        "receipt_ref": "bank-operation:" + str(action_id),
        "executed_cents": 100,
        "fee_cents": 0,
        "loss_cents": 0,
        "response": {
            "bank_operation_id": str(action_id),
            "posting_ids": [p["id"] for p in postings],
        },
        "occurred_at": now,
        "reconciled_at": now,
        "created_at": now,
    }
    return operation, action, postings, receipt, now


def test_frozen_receipt_core_rejects_balanced_fabrication_without_latest_rows() -> None:
    from app.domain.audit_chain import verify_frozen_projection, verify_frozen_settlement
    from app.domain.policy_configuration import configuration_hash

    operation, action, postings, receipt, now = transfer_originals()
    verify_frozen_projection(operation, action, receipt, postings, observed_at=now)
    verify_frozen_settlement(operation, action, reversed(postings), observed_at=now)
    forged = [
        {
            **p,
            "delta_cents": p["delta_cents"] * 2,
            "balance_after_cents": p["balance_before_cents"] + p["delta_cents"] * 2,
        }
        for p in postings
    ]
    with pytest.raises(ValueError):
        verify_frozen_projection(operation, action, receipt, forged, observed_at=now)
    with pytest.raises(ValueError):
        verify_frozen_projection(
            operation, action, {**receipt, "executed_cents": 99}, postings, observed_at=now
        )
    with pytest.raises(ValueError):
        verify_frozen_projection(
            operation,
            {**action, "source_account_id": postings[1]["account_id"]},
            receipt,
            postings,
            observed_at=now,
        )
    with pytest.raises(ValueError):
        verify_frozen_settlement(
            {**operation, "request_hash": configuration_hash({})}, action, postings, observed_at=now
        )


def projected_chain() -> tuple[list[AuditEnvelope], AuditHead, ReferenceBundle]:
    from app.domain.audit_chain import (
        build_event,
        build_subject,
        posting_set_digest,
        receipt_digest,
        subject_hash,
    )
    from app.domain.audit_chain_types import AuditAnchor, AuditIntent, AuditPayload, AuditReference

    events, head, bundle = recorded_chain()
    operation, action, postings, receipt, now = transfer_originals()
    action["decision_run_id"] = str(events[1].decision_run_id)
    originals = [
        build_subject(
            user_id=head.user_id, epoch_id=head.epoch_id, kind=kind, id=UUID(data["id"]), data=data
        )
        for kind, data in [
            ("ACTION_PLAN", action),
            ("BANK_OPERATION", operation),
            ("ACTION_RECEIPT", receipt),
        ]
        + [("BANK_POSTING", row) for row in postings]
    ]
    refs = events[1].payload.references + [
        AuditReference(
            kind=s.kind, id=s.id, user_id=head.user_id, snapshot_hash=subject_hash(s), role="AFTER"
        )
        for s in originals
    ]
    anchors = [
        AuditAnchor(
            kind="ACTION_RECEIPT",
            reference_id=originals[2].id,
            snapshot_hash=subject_hash(originals[2]),
            digest=receipt_digest(originals[2].data),
            hash_algorithm="configuration-sha256-v1",
        ),
        AuditAnchor(
            kind="BANK_POSTING_SET",
            reference_id=originals[1].id,
            snapshot_hash=subject_hash(originals[1]),
            digest=posting_set_digest(s.data for s in originals[3:]),
            hash_algorithm="configuration-sha256-v1",
        ),
    ]
    intent = AuditIntent(
        user_id=head.user_id,
        event_type="ACTION_PROJECTED",
        aggregate_type="ACTION_RECEIPT",
        aggregate_id=originals[2].id,
        correlation_id=events[1].correlation_id,
        causation_id=events[1].id,
        decision_run_id=events[1].decision_run_id,
        action_plan_id=originals[0].id,
        action_receipt_id=originals[2].id,
        idempotency_key="projected",
        occurred_at=now,
        payload=AuditPayload(
            fact_key="projected", correlation_kind="DECISION_RUN", references=refs, anchors=anchors
        ),
    )
    projected = build_event(
        intent,
        event_id=UUID("00000000-0000-0000-0000-000000000025"),
        epoch_id=head.epoch_id,
        sequence_number=3,
        previous_hash=events[-1].event_hash,
        observed_at=now,
        appended_at=now,
    )
    head = head.model_copy(
        update={
            "event_count": 3,
            "last_sequence": 3,
            "last_event_id": projected.id,
            "last_event_hash": projected.event_hash,
        }
    )
    return (
        events + [cast("AuditEnvelope", projected)],
        head,
        bundle.model_copy(update={"subjects": bundle.subjects + originals}),
    )


def test_projected_original_receipt_and_posting_anchors_verify_without_session() -> None:
    from app.domain.audit_chain import build_subject, verify_epoch

    events, head, bundle = projected_chain()
    valid = verify_epoch(events, head=head, expected_user_id=head.user_id, references=bundle)
    assert valid.status == "VALID", valid.errors
    posting = next(s for s in bundle.subjects if s.kind == "BANK_POSTING")
    changed = build_subject(
        user_id=head.user_id,
        epoch_id=head.epoch_id,
        kind=posting.kind,
        id=posting.id,
        data={
            **posting.data,
            "delta_cents": -200,
            "balance_after_cents": posting.data["balance_before_cents"] - 200,
        },
    )
    current = bundle.model_copy(update={"current_subjects": [changed]})
    assert (
        verify_epoch(events, head=head, expected_user_id=head.user_id, references=current).status
        == "INTEGRITY_ERROR"
    )


def test_current_rows_repeated_by_original_versions_are_one_actual_snapshot() -> None:
    from app.domain.audit_chain import verify_epoch

    events, head, bundle = projected_chain()
    receipt = next(s for s in bundle.subjects if s.kind == "ACTION_RECEIPT")
    bundle = bundle.model_copy(update={"current_subjects": [receipt, receipt]})
    result = verify_epoch(events, head=head, expected_user_id=head.user_id, references=bundle)
    assert result.status == "VALID", result.errors


@pytest.mark.parametrize(
    "updates",
    [
        {"aggregate_type": "ACTION_PLAN"},
        {"decision_run_id": None},
        {"payload_version": 2},
    ],
)
def test_registry_rejects_fabricated_decision_event_shape(updates: dict[str, Any]) -> None:
    from app.domain.audit_chain import build_event

    events, head, _ = recorded_chain()
    with pytest.raises(ValueError):
        build_event(
            events[1].model_copy(update=updates),
            event_id=events[1].id,
            epoch_id=head.epoch_id,
            sequence_number=2,
            previous_hash=events[0].event_hash,
            observed_at=events[1].observed_at,
            appended_at=events[1].appended_at,
        )


def test_checkpoint_prefix_mismatch_is_explicit_and_limit_is_incomplete() -> None:
    from app.domain.audit_chain import build_checkpoint, verify_epoch

    events, head, bundle = recorded_chain()
    old = head.model_copy(update={"last_event_hash": "f" * 64})
    checkpoint = build_checkpoint(old, captured_at=events[1].appended_at)
    result = verify_epoch(
        events, head=head, expected_user_id=head.user_id, references=bundle, checkpoint=checkpoint
    )
    assert result.checkpoint_status == "MISMATCH"
    limited = verify_epoch(
        events, head=head, expected_user_id=head.user_id, references=bundle, event_limit=1
    )
    assert limited.status == "INCOMPLETE"
    assert "HEAD_MISMATCH" not in {e.code for e in limited.errors}


def test_original_change_must_bind_actual_before_and_after_values() -> None:
    from app.domain.audit_chain import build_event, build_subject, subject_hash, verify_epoch
    from app.domain.audit_chain_types import AuditChange, AuditReference

    events, head, bundle = recorded_chain()
    identity = UUID("00000000-0000-0000-0000-000000000026")
    before = build_subject(
        user_id=head.user_id,
        epoch_id=head.epoch_id,
        kind="ACTION_PLAN",
        id=identity,
        data={"id": str(identity), "user_id": str(head.user_id), "status": "PLANNED"},
    )
    after = build_subject(
        user_id=head.user_id,
        epoch_id=head.epoch_id,
        kind="ACTION_PLAN",
        id=identity,
        data={"id": str(identity), "user_id": str(head.user_id), "status": "AUTHORIZED"},
    )
    payload = events[1].payload.model_copy(
        update={
            "references": events[1].payload.references
            + [
                AuditReference(
                    kind=s.kind,
                    id=s.id,
                    user_id=head.user_id,
                    snapshot_hash=subject_hash(s),
                    role=cast(Literal["BASIS", "BEFORE", "AFTER"], role),
                )
                for s, role in [(before, "BEFORE"), (after, "AFTER")]
            ],
            "changes": [
                AuditChange(
                    kind="ACTION_PLAN",
                    id=identity,
                    field="status",
                    before="PLANNED",
                    after="SUCCEEDED",
                    before_snapshot_hash=subject_hash(before),
                    after_snapshot_hash=subject_hash(after),
                )
            ],
        }
    )
    from app.domain.audit_chain_types import AuditFactContext

    payload = payload.model_copy(
        update={"context": AuditFactContext(reason_code="ACTUAL_STATUS_CHANGE")}
    )
    event = build_event(
        events[1].model_copy(
            update={
                "payload": payload,
                "event_type": "ACTION_STATE_CHANGED",
                "aggregate_type": "ACTION_PLAN",
                "aggregate_id": identity,
                "action_plan_id": identity,
            }
        ),
        event_id=events[1].id,
        epoch_id=head.epoch_id,
        sequence_number=2,
        previous_hash=events[0].event_hash,
        observed_at=events[1].observed_at,
        appended_at=events[1].appended_at,
    )
    head = head.model_copy(update={"last_event_hash": event.event_hash})
    result = verify_epoch(
        [events[0], event],
        head=head,
        expected_user_id=head.user_id,
        references=bundle.model_copy(update={"subjects": bundle.subjects + [before, after]}),
    )
    assert result.status == "INTEGRITY_ERROR"


def test_sealed_epoch_requires_actual_terminal_seal_and_exact_archive_manifest() -> None:
    from app.domain.audit_chain import verify_epoch

    events, head, bundle = recorded_chain()
    result = verify_epoch(
        events,
        head=head.model_copy(update={"status": "SEALED"}),
        expected_user_id=head.user_id,
        references=bundle,
    )
    assert result.status == "INTEGRITY_ERROR"


def test_cross_action_cause_is_rejected_even_when_parent_run_matches() -> None:
    from app.domain.audit_chain import build_event, build_subject, subject_hash, verify_epoch
    from app.domain.audit_chain_types import (
        AuditChange,
        AuditFactContext,
        AuditIntent,
        AuditPayload,
        AuditReference,
    )

    events, head, bundle = projected_chain()
    identity = UUID("00000000-0000-0000-0000-000000000027")
    original = build_subject(
        user_id=head.user_id,
        epoch_id=head.epoch_id,
        kind="ACTION_PLAN",
        id=identity,
        data={"id": str(identity), "user_id": str(head.user_id), "status": "AUTHORIZED"},
    )
    before = build_subject(
        user_id=head.user_id,
        epoch_id=head.epoch_id,
        kind="ACTION_PLAN",
        id=identity,
        data={**original.data, "status": "PLANNED"},
    )
    payload = AuditPayload(
        fact_key="other-action-state",
        correlation_kind="DECISION_RUN",
        context=AuditFactContext(reason_code="ACTUAL_STATUS_CHANGE"),
        changes=[
            AuditChange(
                kind="ACTION_PLAN",
                id=identity,
                field="status",
                before="PLANNED",
                after="AUTHORIZED",
                before_snapshot_hash=subject_hash(before),
                after_snapshot_hash=subject_hash(original),
            )
        ],
        references=events[1].payload.references
        + [
            AuditReference(
                kind="ACTION_PLAN",
                id=identity,
                user_id=head.user_id,
                snapshot_hash=subject_hash(original),
                role="AFTER",
            )
        ]
        + [
            AuditReference(
                kind="ACTION_PLAN",
                id=identity,
                user_id=head.user_id,
                snapshot_hash=subject_hash(before),
                role="BEFORE",
            )
        ],
    )
    unrelated = build_event(
        AuditIntent(
            user_id=head.user_id,
            event_type="ACTION_STATE_CHANGED",
            aggregate_type="ACTION_PLAN",
            aggregate_id=identity,
            correlation_id=events[1].correlation_id,
            decision_run_id=events[1].decision_run_id,
            action_plan_id=identity,
            idempotency_key="other-action",
            occurred_at=events[1].occurred_at,
            payload=payload,
        ),
        event_id=identity,
        epoch_id=head.epoch_id,
        sequence_number=3,
        previous_hash=events[1].event_hash,
        observed_at=events[1].observed_at,
        appended_at=events[1].appended_at,
    )
    last = build_event(
        events[2].model_copy(update={"causation_id": unrelated.id}),
        event_id=events[2].id,
        epoch_id=head.epoch_id,
        sequence_number=4,
        previous_hash=unrelated.event_hash,
        observed_at=events[2].observed_at,
        appended_at=events[2].appended_at,
    )
    head = head.model_copy(
        update={"event_count": 4, "last_sequence": 4, "last_event_hash": last.event_hash}
    )
    bundle = bundle.model_copy(update={"subjects": bundle.subjects + [original, before]})
    result = verify_epoch(
        events[:2] + [unrelated, last], head=head, expected_user_id=head.user_id, references=bundle
    )
    assert result.status == "INTEGRITY_ERROR"


def test_unknown_canonical_parser_and_legacy_genesis_have_explicit_status() -> None:
    import json

    from app.domain.audit_chain import (
        AuditUnsupportedVersion,
        build_event,
        parse_event,
        verify_epoch,
    )

    events, head, bundle = recorded_chain()
    raw = events[0].model_dump(mode="json")
    raw["schema_version"] = "audit-event-v99"
    with pytest.raises(AuditUnsupportedVersion):
        parse_event(json.dumps(raw))
    original_transition = events[0].payload.epoch_transition
    assert original_transition is not None
    transition = original_transition.model_copy(update={"legacy_history": True})
    first = build_event(
        events[0].model_copy(
            update={
                "payload": events[0].payload.model_copy(update={"epoch_transition": transition})
            }
        ),
        event_id=events[0].id,
        epoch_id=head.epoch_id,
        sequence_number=1,
        previous_hash=None,
        observed_at=events[0].observed_at,
        appended_at=events[0].appended_at,
    )
    second = build_event(
        events[1],
        event_id=events[1].id,
        epoch_id=head.epoch_id,
        sequence_number=2,
        previous_hash=first.event_hash,
        observed_at=events[1].observed_at,
        appended_at=events[1].appended_at,
    )
    head = head.model_copy(
        update={"genesis_event_hash": first.event_hash, "last_event_hash": second.event_hash}
    )
    result = verify_epoch(
        [first, second], head=head, expected_user_id=head.user_id, references=bundle
    )
    assert result.status == result.reference_status == "LEGACY_UNAUDITED"
    assert result.chain_status == "VALID"


def test_registered_projection_cannot_omit_original_economic_anchors() -> None:
    from app.domain.audit_chain import build_event

    events, head, _ = projected_chain()
    payload = events[-1].payload.model_copy(update={"anchors": events[-1].payload.anchors[:1]})
    with pytest.raises(ValueError):
        build_event(
            events[-1].model_copy(update={"payload": payload}),
            event_id=events[-1].id,
            epoch_id=head.epoch_id,
            sequence_number=3,
            previous_hash=events[-2].event_hash,
            observed_at=events[-1].observed_at,
            appended_at=events[-1].appended_at,
        )


def test_actual_sealing_event_archive_and_exported_prefix_verify() -> None:
    from app.domain.audit_chain import (
        archive_manifest,
        archive_manifest_digest,
        build_checkpoint,
        build_event,
        verify_epoch,
    )
    from app.domain.audit_chain_types import AuditEpochTransition, AuditIntent, AuditPayload

    events, head, bundle = recorded_chain()
    checkpoint = build_checkpoint(head, captured_at=events[-1].appended_at)
    transition = AuditEpochTransition(
        kind="SEAL",
        reset_key="reset-original",
        reason="simulated reset",
        principal="demo",
        pre_seal_head=head,
        archive_manifest_hash=archive_manifest_digest(bundle.subjects),
        archive_record_counts=archive_manifest(bundle.subjects)["counts"],
    )
    sealed = build_event(
        AuditIntent(
            user_id=head.user_id,
            event_type="EPOCH_SEALED",
            aggregate_type="EPOCH",
            aggregate_id=head.epoch_id,
            correlation_id=head.epoch_id,
            idempotency_key="sealed",
            occurred_at=events[-1].occurred_at,
            payload=AuditPayload(
                fact_key="sealed", correlation_kind="EPOCH", epoch_transition=transition
            ),
        ),
        event_id=UUID("00000000-0000-0000-0000-000000000030"),
        epoch_id=head.epoch_id,
        sequence_number=3,
        previous_hash=events[-1].event_hash,
        observed_at=events[-1].observed_at,
        appended_at=events[-1].appended_at,
    )
    final_head = head.model_copy(
        update={
            "status": "SEALED",
            "event_count": 3,
            "last_sequence": 3,
            "last_event_id": sealed.id,
            "last_event_hash": sealed.event_hash,
        }
    )
    result = verify_epoch(
        events + [sealed],
        head=final_head,
        expected_user_id=head.user_id,
        references=bundle,
        checkpoint=checkpoint,
    )
    assert result.status == "VALID", result.errors
    exact = verify_epoch(
        events + [sealed],
        head=final_head,
        expected_user_id=head.user_id,
        references=bundle,
        checkpoint=checkpoint,
        checkpoint_mode="EXACT",
    )
    assert exact.checkpoint_status == "MISMATCH"
    missing = verify_epoch(
        events + [sealed],
        head=final_head,
        expected_user_id=head.user_id,
        references=bundle.model_copy(update={"subjects": []}),
    )
    assert missing.status == "INTEGRITY_ERROR"


def test_legacy_redemption_frozen_receipt_preserves_actual_original_transaction() -> None:
    from uuid import uuid5

    from app.domain.audit_chain import build_subject, verify_frozen_projection
    from app.domain.policy_configuration import configuration_hash

    _, action, _, _, now = transfer_originals()
    operation_id = uuid5(UUID(action["id"]), "simulated-bank-redemption")
    position = UUID("00000000-0000-0000-0000-000000000031")
    position_account = UUID("00000000-0000-0000-0000-000000000032")
    target = UUID("00000000-0000-0000-0000-000000000022")
    command = {
        "user_id": action["user_id"],
        "position_id": str(position),
        "position_account_id": str(position_account),
        "product_id": "00000000-0000-0000-0000-000000000033",
        "goal_id": None,
        "original_policy_version_id": None,
        "destination_account_id": str(target),
        "principal_cents": 100,
        "requested_at": now.isoformat(),
        "available_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "kind": "MATURE",
    }
    request = {"bank_request": command}
    action = {
        **action,
        "action_type": "ASSET_MATURITY",
        "position_id": str(position),
        "source_account_id": str(position_account),
        "destination_account_id": str(target),
        "product_id": command["product_id"],
        "request": request,
        "request_hash": configuration_hash(request),
        "expires_at": command["expires_at"],
    }
    operation = {
        "id": str(operation_id),
        "user_id": action["user_id"],
        "action_plan_id": action["id"],
        "operation_type": "LEGACY_REDEMPTION",
        "business_key": "close:" + str(position),
        "legacy_redemption_id": str(operation_id),
        "closing_position_id": str(position),
        "request": command,
        "request_hash": configuration_hash(command),
        "idempotency_key": action["idempotency_key"],
        "requested_at": now,
        "available_at": now,
        "settled_at": now,
        "created_at": now,
        "status": "SETTLED",
    }
    postings = [
        {
            "id": str(uuid5(operation_id, leg)),
            "user_id": action["user_id"],
            "operation_id": str(operation_id),
            "redemption_id": str(operation_id),
            "leg_ref": leg,
            "entry_kind": leg,
            "ledger_key": key,
            "ledger_dimension": "ECONOMIC",
            "delta_cents": delta,
            "balance_before_cents": before,
            "balance_after_cents": before + delta,
            "account_id": account,
            "position_id": position_id,
            "occurred_at": now,
            "created_at": now,
            "sequence_number": 2,
        }
        for leg, key, delta, before, account, position_id in [
            ("CASH_CREDIT", "CASH:" + str(target), 100, 0, str(target), None),
            ("PRINCIPAL_DEBIT", "POSITION:" + str(position), -100, 100, None, str(position)),
        ]
    ]
    tx_id, proof_id = (
        uuid5(operation_id, "transaction"),
        uuid5(operation_id, "transaction-evidence"),
    )
    transaction = {
        "id": str(tx_id),
        "user_id": action["user_id"],
        "account_id": str(target),
        "evidence_id": str(proof_id),
        "direction": "CREDIT",
        "amount_cents": 100,
        "balance_after_cents": 100,
        "occurred_at": now,
        "source_ref": "bank-redemption:" + str(operation_id) + ":principal",
        "counterparty_ref": "position:" + str(position),
        "observed_at": now,
        "created_at": now,
    }
    content = {
        **{
            k: v
            for k, v in transaction.items()
            if k
            not in {"id", "evidence_id", "occurred_at", "source_ref", "observed_at", "created_at"}
        },
        "transaction_id": str(tx_id),
        "simulation": True,
        "occurred_at": now.isoformat(),
        "economic_role": "PRINCIPAL_RETURN",
        "bank_request_id": str(operation_id),
        "bank_posting_id": postings[0]["id"],
    }
    evidence = {
        "id": str(proof_id),
        "user_id": action["user_id"],
        "evidence_level": "BANK_CONFIRMED",
        "source_type": "SIMULATED_BANK_TRANSACTION",
        "content": content,
        "content_hash": configuration_hash(content),
        "source_ref": transaction["source_ref"],
        "valid_from": now,
        "observed_at": now,
        "created_at": now,
    }
    epoch = UUID("00000000-0000-0000-0000-000000000003")
    subjects = [
        build_subject(
            user_id=UUID(action["user_id"]), epoch_id=epoch, kind=kind, id=UUID(row["id"]), data=row
        )
        for kind, row in [("TRANSACTION", transaction), ("EVIDENCE", evidence)]
    ]
    receipt = {
        "id": str(uuid5(operation_id, "receipt")),
        "user_id": action["user_id"],
        "action_plan_id": action["id"],
        "status": "SUCCEEDED",
        "attempt_number": 1,
        "receipt_ref": "bank:" + str(operation_id),
        "executed_cents": 100,
        "fee_cents": 0,
        "loss_cents": 0,
        "occurred_at": now,
        "reconciled_at": now,
        "created_at": now,
        "response": {
            "bank_request_id": str(operation_id),
            "posting_ids": [p["id"] for p in postings],
            "transaction_id": str(tx_id),
        },
    }
    verify_frozen_projection(
        operation, action, receipt, postings, observed_at=now, subjects=subjects
    )
    with pytest.raises(ValueError):
        verify_frozen_projection(operation, action, receipt, postings, observed_at=now, subjects=[])
    with pytest.raises(ValueError):
        verify_frozen_projection(
            operation,
            action,
            {**receipt, "fee_cents": 1},
            postings,
            observed_at=now,
            subjects=subjects,
        )

    false_content = {**content, "economic_role": "INCOME"}
    false_proof = build_subject(
        user_id=UUID(action["user_id"]),
        epoch_id=epoch,
        kind="EVIDENCE",
        id=proof_id,
        data={
            **evidence,
            "content": false_content,
            "content_hash": configuration_hash(false_content),
        },
    )
    with pytest.raises(ValueError):
        verify_frozen_projection(
            operation,
            action,
            receipt,
            postings,
            observed_at=now,
            subjects=[subjects[0], false_proof],
        )


def test_frozen_archive_ledgers_require_the_actual_opening_and_predecessor_balances() -> None:
    from app.domain.audit_chain import verify_frozen_ledgers

    _, _, legs, _, _ = transfer_originals()
    first = {
        **legs[0],
        "id": "00000000-0000-0000-0000-000000000036",
        "entry_kind": "OPENING",
        "sequence_number": 1,
        "previous_posting_id": None,
        "operation_id": None,
        "leg_ref": None,
        "balance_before_cents": 0,
        "delta_cents": 1000,
        "balance_after_cents": 1000,
    }
    next_leg = {**legs[0], "previous_posting_id": first["id"]}
    verify_frozen_ledgers([next_leg, first])
    with pytest.raises(ValueError):
        verify_frozen_ledgers([next_leg])
    with pytest.raises(ValueError):
        verify_frozen_ledgers(
            [first, {**next_leg, "balance_before_cents": 2000, "balance_after_cents": 1900}]
        )


def test_unknown_trace_algorithm_and_subject_budget_are_never_passes() -> None:
    from app.domain.audit_chain import (
        build_checkpoint,
        build_event,
        build_subject,
        subject_hash,
        verify_epoch,
    )
    from app.domain.decision_trace import build_trace
    from app.domain.policy_configuration import configuration_hash

    events, head, bundle = recorded_chain()
    original = bundle.subjects[0]
    raw_trace = original.data["input_snapshot"]["decision_trace"]
    fields = {
        k: v
        for k, v in raw_trace.items()
        if k not in {"input_hash", "trace_hash", "as_of", "run_id", "user_id"}
    }
    fields["algorithm_versions"] = {"trace": "unknown-future-algorithm"}
    trace = build_trace(
        **fields, run_id=original.id, user_id=head.user_id, as_of=events[1].occurred_at
    )
    snapshot = {"decision_trace": trace.model_dump(mode="json")}
    changed = build_subject(
        user_id=head.user_id,
        epoch_id=head.epoch_id,
        kind=original.kind,
        id=original.id,
        data={
            **original.data,
            "input_snapshot": snapshot,
            "snapshot_hash": configuration_hash(snapshot),
        },
    )
    digest = subject_hash(changed)
    payload = events[1].payload.model_copy(
        update={
            "references": [
                events[1].payload.references[0].model_copy(update={"snapshot_hash": digest})
            ],
            "anchors": [
                events[1]
                .payload.anchors[0]
                .model_copy(update={"snapshot_hash": digest, "digest": trace.trace_hash})
            ],
        }
    )
    changed_event = build_event(
        events[1].model_copy(update={"payload": payload}),
        event_id=events[1].id,
        epoch_id=head.epoch_id,
        sequence_number=2,
        previous_hash=events[0].event_hash,
        observed_at=events[1].observed_at,
        appended_at=events[1].appended_at,
    )
    changed_head = head.model_copy(update={"last_event_hash": changed_event.event_hash})
    result = verify_epoch(
        [events[0], changed_event],
        head=changed_head,
        expected_user_id=head.user_id,
        references=bundle.model_copy(update={"subjects": [changed]}),
    )
    assert result.status == result.reference_status == "UNSUPPORTED_VERSION"
    assert result.chain_status == "VALID"
    broken_payload = payload.model_copy(
        update={"anchors": [payload.anchors[0].model_copy(update={"digest": "f" * 64})]}
    )
    broken_event = build_event(
        changed_event.model_copy(update={"payload": broken_payload}),
        event_id=changed_event.id,
        epoch_id=head.epoch_id,
        sequence_number=2,
        previous_hash=events[0].event_hash,
        observed_at=changed_event.observed_at,
        appended_at=changed_event.appended_at,
    )
    broken_head = changed_head.model_copy(update={"last_event_hash": broken_event.event_hash})
    broken = verify_epoch(
        [events[0], broken_event],
        head=broken_head,
        expected_user_id=head.user_id,
        references=bundle.model_copy(update={"subjects": [changed]}),
    )
    assert broken.status == "INTEGRITY_ERROR"
    checkpoint = build_checkpoint(head, captured_at=events[1].appended_at)
    limited = verify_epoch(
        events,
        head=head,
        expected_user_id=head.user_id,
        references=bundle,
        checkpoint=checkpoint,
        byte_limit=1,
    )
    assert limited.status == "INCOMPLETE"
    assert limited.checkpoint_status == "UNAVAILABLE"
    from app.domain.audit_chain_types import ReferenceBundle

    unsupported = events[0].model_dump(mode="json")
    unsupported["canonical_version"] = "future-codec"
    first_head = head.model_copy(
        update={
            "event_count": 1,
            "last_sequence": 1,
            "last_event_id": events[0].id,
            "last_event_hash": events[0].event_hash,
        }
    )
    unknown_limit = verify_epoch(
        [unsupported],
        head=first_head,
        expected_user_id=head.user_id,
        references=ReferenceBundle(),
        byte_limit=1,
    )
    assert unknown_limit.status == "INCOMPLETE"


def test_state_change_without_change_or_resolved_claim_is_not_an_audit_fact() -> None:
    from app.domain.audit_chain import build_event

    events, head, _ = projected_chain()
    old = events[-1]
    payload = old.payload.model_copy(
        update={
            "anchors": [],
            "context": old.payload.context.model_copy(update={"reason_code": "NO_REAL_CHANGE"}),
        }
    )
    with pytest.raises(ValueError):
        build_event(
            old.model_copy(
                update={
                    "event_type": "ACTION_STATE_CHANGED",
                    "action_receipt_id": None,
                    "aggregate_type": "ACTION_PLAN",
                    "aggregate_id": old.action_plan_id,
                    "payload": payload,
                }
            ),
            event_id=old.id,
            epoch_id=head.epoch_id,
            sequence_number=3,
            previous_hash=events[-2].event_hash,
            observed_at=old.observed_at,
            appended_at=old.appended_at,
        )
