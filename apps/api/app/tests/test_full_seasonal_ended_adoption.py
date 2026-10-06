"""Synthetic retained originals only; not actual expired-adoption or financial evidence."""

from contextlib import nullcontext
from copy import deepcopy
from datetime import UTC, datetime, time, timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

import pytest
from app.db.models import AuditEpoch, EvidenceItem
from app.domain.audit_chain_types import AuditVerification
from app.domain.decision_trace import build_trace
from app.domain.full_seasonal_adoption import (
    PROTOCOL,
    SeasonalAdoptionOriginal,
    seasonal_command_id,
    seasonal_request_hash,
    seasonal_review_hash,
    seasonal_source_hash,
)
from app.domain.full_seasonal_ended_adoption import (
    REFERENCE_KIND,
    EndedSeasonalAdoptionInput,
    EndedSeasonalAdoptionRecord,
    derive_ended_seasonal_adoption,
    verify_ended_seasonal_adoption,
)
from app.domain.policy_configuration import configuration_hash
from app.services import full_seasonal_ended_adoption as service
from app.services.audit_chain import row_copy
from app.services.decision_trace import evidence_copy
from app.services.full_policy_lifecycle import FullPolicyView
from app.services.full_seasonal_adoption import SeasonalAdoptionReceipt
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_full_seasonal_adoption import (
    EPOCH,
    POLICY,
    USER,
    original_fixture,
    proof_fixture,
)
from app.tests.test_full_seasonal_adoption_service import Memory
from sqlalchemy.orm import Session


def evidence(raw: dict[str, Any]) -> EvidenceItem:
    fields = dict(raw)
    for key in ("id", "user_id"):
        fields[key] = UUID(fields[key])
    for key in ("created_at", "observed_at", "valid_from", "valid_to"):
        if fields[key] is not None:
            fields[key] = datetime.fromisoformat(fields[key])
    return EvidenceItem(**fields)


def record_for(original: SeasonalAdoptionOriginal) -> EndedSeasonalAdoptionRecord:
    content = original.model_dump(mode="json")
    proof = EvidenceItem(
        id=uuid5(original.command_id, "evidence"),
        user_id=original.user_id,
        created_at=original.recorded_at,
        observed_at=original.recorded_at,
        valid_from=original.recorded_at,
        valid_to=None,
        status="VALID",
        source_type="FULL_SEASONAL_ADOPTION",
        source_ref=str(original.command_id),
        evidence_level="USER_CONFIRMED_POLICY",
        content=content,
        content_hash=configuration_hash(content),
    )
    rows = [evidence(raw) for raw in original.scope.source_evidence_originals] + [proof]
    trace = build_trace(
        run_id=original.command_id,
        user_id=original.user_id,
        phase="EVALUATION",
        as_of=original.recorded_at,
        action_id=None,
        parent_run_id=None,
        algorithm_versions={"trace": "decision-trace-v1", "seasonal_adoption": PROTOCOL},
        inputs={
            "original_request": original.original_request.model_dump(mode="json"),
            "request_hash": original.request_hash,
            "reviewed_hash": original.reviewed_hash,
        },
        sources=[evidence_copy(row) for row in rows],
        policies=[],
        outcome={"seasonal_adoption_original": content},
    )
    return EndedSeasonalAdoptionRecord(
        adoption_evidence_original=row_copy(proof),
        trace=trace,
        current_evidence_originals=[row_copy(row) for row in rows],
    )


def fixture() -> EndedSeasonalAdoptionInput:
    _, proof = proof_fixture()
    assert proof.original is not None
    scope_raw = proof.original.scope.model_dump(mode="json")
    # Retain the complete actual FullPolicyView contract, including its original
    # no-authority flags. Old hand-built scope fixtures omit those DTO defaults.
    scope_raw["full_policy_original"] = FullPolicyView.model_validate_json(
        __import__("json").dumps(scope_raw["full_policy_original"])
    ).model_dump(mode="json")
    scope = type(proof.original.scope).model_validate_json(__import__("json").dumps(scope_raw))
    original = original_fixture(scope)
    next_day = original.scope.protection_end + timedelta(days=1)
    now = datetime.combine(next_day, time.min, ZoneInfo("Asia/Shanghai")).astimezone(UTC)
    record = record_for(original)
    return EndedSeasonalAdoptionInput(
        user_id=USER,
        epoch_id=EPOCH,
        policy_id=POLICY,
        as_of=now,
        epoch_original={
            "id": str(EPOCH),
            "user_id": str(USER),
            "status": "OPEN",
            "event_count": 1,
            "last_sequence": 1,
            "last_event_id": str(UUID(int=700)),
            "last_event_hash": "a" * 64,
        },
        audit=AuditVerification(
            user_id=USER,
            epoch_id=EPOCH,
            status="VALID",
            chain_status="VALID",
            reference_status="VALID",
            checkpoint_status="NOT_REQUESTED",
            actual_count=1,
            expected_count=1,
            actual_tail_id=UUID(int=700),
            expected_tail_id=UUID(int=700),
            actual_tail_hash="a" * 64,
            expected_tail_hash="a" * 64,
            verified_through_sequence=1,
        ),
        current_policy_original=deepcopy(original.scope.full_policy_original),
        registered_adoption_evidence_count=1,
        registered_adoption_evidence_ids=[UUID(record.adoption_evidence_original["id"])],
        records=[record],
    )


def test_original_integer_1800_becomes_zero_floor_only_after_original_local_end() -> None:
    value = fixture()
    original_json = value.model_dump_json()
    result = derive_ended_seasonal_adoption(value)
    assert result.status == "VERIFIED_ENDED" and result.current_floor_cents == 0
    assert result.original_adopted_cents == 1800 and result.original is not None
    assert result.original.scope.suggestion_original["suggestion"]["status"] == "READY"
    assert result.protection_end == result.original.scope.protection_end
    assert result.floor_released_on == result.protection_end + timedelta(days=1)
    assert not result.cash_balance_changed and not result.financial_execution_performed
    assert not result.bank_authority and not result.current_permission_proven
    assert result.future_income_in_current_cash_cents == 0
    assert value.model_dump_json() == original_json
    source, _ = proof_fixture()
    assert verify_ended_seasonal_adoption(source, result, value.as_of, "Asia/Shanghai")
    assert not verify_ended_seasonal_adoption(
        source, result, value.as_of, "Asia/Shanghai", expected_user_id=UUID(int=99)
    )


def test_end_day_last_microsecond_is_not_released_utc_day_cannot_override_local_day() -> None:
    value = fixture()
    still_end = value.as_of - timedelta(microseconds=1)
    assert still_end.date() == value.as_of.date()  # UTC still has the same calendar day.
    assert (
        still_end.astimezone(ZoneInfo("Asia/Shanghai")).date()
        < value.as_of.astimezone(ZoneInfo("Asia/Shanghai")).date()
    )
    changed = value.model_copy(update={"as_of": still_end})
    result = derive_ended_seasonal_adoption(changed)
    assert result.status == "UNKNOWN" and result.current_floor_cents is None
    assert result.original_adopted_cents == 1800
    assert "ENDED_ADOPTION_END_LOCAL_DAY_NOT_PASSED" in result.reasons


def test_declared_dto_clock_same_instant_does_not_rewrite_original_hash_or_body() -> None:
    value = fixture()
    raw = deepcopy(value.current_policy_original)
    original_body = value.records[0].trace.model_dump_json()
    raw["updated_at"] = datetime.fromisoformat(raw["updated_at"]).isoformat().replace("+00:00", "Z")
    version = raw["current_version"]
    for key in ("confirmed_at", "valid_from"):
        version[key] = datetime.fromisoformat(version[key]).isoformat().replace("+00:00", "Z")
    result = derive_ended_seasonal_adoption(
        value.model_copy(update={"current_policy_original": raw})
    )
    assert result.status == "VERIFIED_ENDED" and result.inputs is not None
    assert result.inputs.current_policy_original == raw
    assert result.inputs.records[0].trace.model_dump_json() == original_body


def test_natural_current_expiry_preserves_original_confirmation_without_live_permission() -> None:
    value = fixture()
    first = SeasonalAdoptionOriginal.model_validate_json(
        __import__("json").dumps(value.records[0].trace.outcome["seasonal_adoption_original"])
    )
    scope_raw = first.scope.model_dump(mode="json")
    scope_raw["full_policy_original"]["current_version"]["valid_until"] = value.as_of.isoformat()
    scope = type(first.scope).model_validate_json(__import__("json").dumps(scope_raw))
    original = original_fixture(scope)
    record = record_for(original)
    current = deepcopy(original.scope.full_policy_original)
    current["effective_status"] = "EXPIRED"
    current["planning_confirmation_valid"] = False
    changed = value.model_copy(
        update={
            "records": [record],
            "current_policy_original": current,
            "registered_adoption_evidence_ids": [UUID(record.adoption_evidence_original["id"])],
        }
    )
    result = derive_ended_seasonal_adoption(changed)
    assert result.status == "VERIFIED_ENDED" and not result.current_permission_proven
    assert result.original is not None
    assert result.original.scope.full_policy_original["effective_status"] == "ACTIVE"
    assert result.inputs is not None
    assert result.inputs.current_policy_original["effective_status"] == "EXPIRED"


@pytest.mark.parametrize(
    "case",
    [
        "count",
        "missing_row",
        "duplicate",
        "missing_original",
        "current_source",
        "source_metadata",
        "owner",
        "epoch",
        "sealed",
        "audit",
        "audit_denominator",
        "epoch_head",
        "audit_checkpoint",
        "policy_version",
        "new_confirmation",
        "revoked",
        "suspended",
        "unknown_reference",
        "original_amount",
        "old_trace",
        "future_original",
        "hash",
        "not_ended",
    ],
)
def test_unknown_missing_or_changed_originals_never_release_as_zero(case: str) -> None:
    value = fixture()
    raw = value.model_dump(mode="json")
    record = raw["records"][0]
    if case == "count":
        raw["registered_adoption_evidence_count"] += 1
    elif case == "missing_row":
        raw["records"] = []
    elif case == "duplicate":
        raw["registered_adoption_evidence_ids"].append(raw["registered_adoption_evidence_ids"][0])
        raw["registered_adoption_evidence_count"] += 1
    elif case == "missing_original":
        record["current_evidence_originals"].pop()
    elif case == "current_source":
        record["current_evidence_originals"][0]["content_hash"] = "0" * 64
    elif case == "source_metadata":
        record["current_evidence_originals"][0]["observed_at"] = raw["as_of"]
    elif case == "owner":
        raw["user_id"] = str(UUID(int=99))
    elif case == "epoch":
        raw["epoch_id"] = str(UUID(int=99))
    elif case == "sealed":
        raw["epoch_original"]["status"] = "SEALED"
    elif case == "audit":
        raw["audit"]["status"] = "INCOMPLETE"
    elif case == "audit_denominator":
        raw["audit"]["verified_through_sequence"] = 0
    elif case == "epoch_head":
        raw["epoch_original"]["last_event_hash"] = "0" * 64
    elif case == "audit_checkpoint":
        raw["audit"]["checkpoint_status"] = "MISMATCH"
    elif case == "policy_version":
        raw["current_policy_original"]["current_version"]["version_id"] = str(UUID(int=99))
    elif case == "new_confirmation":
        raw["current_policy_original"]["current_version"]["confirmation"]["request_key"] = "new"
    elif case in {"revoked", "suspended"}:
        raw["current_policy_original"]["status"] = case.upper()
        raw["current_policy_original"]["effective_status"] = case.upper()
    elif case == "unknown_reference":
        raw["current_policy_original"]["reference_validation"] = "CHANGED_OR_UNAVAILABLE"
    elif case == "original_amount":
        record["adoption_evidence_original"]["content"]["scope"]["adopted_adjustment_cents"] = 0
        record["adoption_evidence_original"]["content_hash"] = configuration_hash(
            record["adoption_evidence_original"]["content"]
        )
    elif case == "old_trace":
        record["trace"]["algorithm_versions"]["seasonal_adoption"] = "unregistered-new-algorithm"
    elif case == "future_original":
        raw["as_of"] = "2024-01-01T00:00:00Z"
    elif case == "hash":
        record["adoption_evidence_original"]["content_hash"] = "0" * 64
    else:
        raw["as_of"] = value.records[0].trace.as_of.isoformat()
    try:
        changed = EndedSeasonalAdoptionInput.model_validate_json(__import__("json").dumps(raw))
        result = derive_ended_seasonal_adoption(changed)
    except ValueError:
        return
    assert result.status == "UNKNOWN" and result.current_floor_cents is None


def test_no_original_adoption_retains_complete_zero_row_denominator_without_zero_floor() -> None:
    value = fixture().model_copy(
        update={
            "records": [],
            "registered_adoption_evidence_ids": [],
            "registered_adoption_evidence_count": 0,
        }
    )
    result = derive_ended_seasonal_adoption(value)
    assert result.status == "NO_ORIGINAL_ADOPTION"
    assert result.registered_adoption_evidence_count == 0
    assert result.current_floor_cents is None and result.original_adopted_cents is None


def test_same_window_second_actual_original_policy_is_not_double_released() -> None:
    value = fixture()
    original = value.records[0].trace.outcome["seasonal_adoption_original"]
    first = SeasonalAdoptionOriginal.model_validate_json(__import__("json").dumps(original))
    scope_raw = first.scope.model_dump(mode="json")
    other_id = UUID(int=9991)
    scope_raw["policy_id"] = str(other_id)
    policy = scope_raw["full_policy_original"]
    policy["policy_id"] = str(other_id)
    policy["current_version"]["policy_id"] = str(other_id)
    confirmation = policy["current_version"]["confirmation"]
    confirmation["policy_id"] = str(other_id)
    for row in scope_raw["source_evidence_originals"]:
        if row["source_type"] == "FULL_POLICY_CONFIRMATION":
            row["content"] = confirmation
            row["content_hash"] = configuration_hash(confirmation)
    scope = type(first.scope).model_validate_json(__import__("json").dumps(scope_raw))
    body = first.original_request.model_copy(
        update={
            "idempotency_key": "synthetic-second-adoption",
            "reviewed_hash": seasonal_review_hash(scope),
        }
    )
    second = SeasonalAdoptionOriginal(
        command_id=seasonal_command_id(USER, EPOCH, body.idempotency_key),
        user_id=USER,
        epoch_id=EPOCH,
        policy_id=other_id,
        idempotency_key=body.idempotency_key,
        original_request=body,
        request_hash=seasonal_request_hash(USER, other_id, body),
        reviewed_hash=body.reviewed_hash,
        source_binding_hash=seasonal_source_hash(scope),
        principal_at_command=first.principal_at_command,
        recorded_at=first.recorded_at,
        scope=scope,
    )
    other_record = record_for(second)
    changed = value.model_copy(
        update={
            "records": [*value.records, other_record],
            "registered_adoption_evidence_count": 2,
            "registered_adoption_evidence_ids": [
                *value.registered_adoption_evidence_ids,
                UUID(other_record.adoption_evidence_original["id"]),
            ],
        }
    )
    result = derive_ended_seasonal_adoption(changed)
    assert result.status == "UNKNOWN" and result.current_floor_cents is None
    assert "ENDED_ADOPTION_REGISTERED_WINDOWS_OVERLAP" in result.reasons


def test_rehashed_derived_wrapper_and_wrong_projection_source_cannot_be_verified() -> None:
    value = fixture()
    proof = derive_ended_seasonal_adoption(value)
    source, _ = proof_fixture()
    assert REFERENCE_KIND != "VERIFIED_SEASONAL_ADOPTION"
    changed = proof.model_copy(update={"original_adopted_cents": 0})
    changed = changed.model_copy(
        update={
            "proof_hash": configuration_hash(
                changed.model_dump(mode="json", exclude={"proof_hash"})
            )
        }
    )
    assert not verify_ended_seasonal_adoption(source, changed, value.as_of, "Asia/Shanghai")
    for field, bad in (
        ("version_id", UUID(int=99)),
        ("content_hash", "0" * 64),
        ("references_current", False),
    ):
        assert not verify_ended_seasonal_adoption(
            source.model_copy(update={field: bad}), proof, value.as_of, "Asia/Shanghai"
        )


class EndedMemory(Memory):
    def __init__(self) -> None:
        super().__init__()
        value = fixture()
        self.value = value
        self.now = value.as_of
        self.original = SeasonalAdoptionOriginal.model_validate_json(
            __import__("json").dumps(value.records[0].trace.outcome["seasonal_adoption_original"])
        )
        self.proof = evidence(value.records[0].adoption_evidence_original)
        # The old Memory's created-at convenience value is not the original SQL row.
        self.evidence = {
            UUID(raw["id"]): evidence(raw) for raw in value.records[0].current_evidence_originals
        }
        self.epoch = AuditEpoch(
            id=EPOCH,
            user_id=USER,
            status="OPEN",
            event_count=1,
            last_sequence=1,
            last_event_id=UUID(int=700),
            last_event_hash="a" * 64,
        )


def patch_service(monkeypatch: pytest.MonkeyPatch, memory: EndedMemory) -> None:
    monkeypatch.setattr(service, "audit_read_scope", lambda _session: nullcontext())
    monkeypatch.setattr(service, "current_audit_epoch", lambda *_args: memory.epoch)
    monkeypatch.setattr(service, "verify_audit_chain", lambda *_args, **_kw: memory.value.audit)
    monkeypatch.setattr(
        service,
        "read_full_policy",
        lambda *_args: FullPolicyView.model_validate_json(
            __import__("json").dumps(memory.value.current_policy_original)
        ),
    )
    original = memory.original
    record = memory.value.records[0]
    monkeypatch.setattr(
        service,
        "get_decision_trace",
        lambda *_args: SimpleNamespace(
            trace=record.trace, completeness="COMPLETE", audit_chain_status="VALID"
        ),
    )
    monkeypatch.setattr(
        service,
        "_original",
        lambda *_args, **_kw: SeasonalAdoptionReceipt(
            original=original,
            evidence_id=memory.proof.id,
            evidence_hash=memory.proof.content_hash,
            trace_hash=record.trace.trace_hash,
            idempotent_replay=True,
        ),
    )


def test_read_service_retains_exact_source_rows_and_rejects_missing_evidence_without_scope_rerun(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = EndedMemory()
    patch_service(monkeypatch, memory)
    result = service.read_current_ended_seasonal_adoption(
        memory.session(), USER, POLICY, memory.now
    )
    assert result.status == "VERIFIED_ENDED" and result.current_floor_cents == 0
    assert result.inputs is not None and result.registered_adoption_evidence_count == 1
    assert result.inputs.records[0].current_evidence_originals == sorted(
        memory.value.records[0].current_evidence_originals, key=lambda row: row["id"]
    )
    identity = next(raw.id for raw in memory.evidence.values() if raw.id != memory.proof.id)
    memory.evidence.pop(identity)
    missing = service.read_current_ended_seasonal_adoption(
        memory.session(), USER, POLICY, memory.now
    )
    assert missing.status == "UNKNOWN" and missing.current_floor_cents is None
    assert missing.registered_adoption_evidence_count is None


@pytest.mark.parametrize("case", ["dirty", "writable", "isolation", "sealed", "foreign_epoch"])
def test_service_requires_actual_clean_rrro_current_owner_open_epoch(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    memory = EndedMemory()
    patch_service(monkeypatch, memory)
    if case == "dirty":
        memory.dirty.add(object())
    elif case == "writable":
        memory.readonly = "off"
    elif case == "isolation":
        memory.connection_state = "READ COMMITTED"
    elif case == "sealed":
        memory.epoch.status = "SEALED"
    else:
        memory.epoch.user_id = UUID(int=99)
    with pytest.raises(PolicyLifecycleError):
        service.read_current_ended_seasonal_adoption(
            cast(Session, memory), USER, POLICY, memory.now
        )


def test_service_empty_inventory_cannot_ignore_incomplete_current_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = EndedMemory()
    patch_service(monkeypatch, memory)
    memory.evidence.pop(memory.proof.id)
    monkeypatch.setattr(
        service,
        "verify_audit_chain",
        lambda *_args, **_kw: memory.value.audit.model_copy(update={"status": "INCOMPLETE"}),
    )
    result = service.read_current_ended_seasonal_adoption(
        memory.session(), USER, POLICY, memory.now
    )
    assert result.status == "UNKNOWN" and result.current_floor_cents is None
