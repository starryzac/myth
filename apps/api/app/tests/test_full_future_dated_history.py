"""Hand-authored original-shape risks only; not PostgreSQL or financial acceptance."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from app.domain.full_future_dated_history import (
    REFERENCE_KIND,
    FutureDatedHistoryProof,
    history_digest,
    validate_future_dated_history,
    verify_future_dated_history,
)
from app.domain.full_policy_configuration import validate_full_configuration
from app.domain.full_protection_projection import FullProtectionPolicySource
from app.domain.policy_configuration import configuration_hash
from app.services.full_policy_lifecycle import FullLifecycleResult
from pydantic import ValidationError

NOW = datetime(2026, 10, 6, 7, 59, tzinfo=UTC)
USER, EPOCH, POLICY = UUID(int=8001), UUID(int=8002), UUID(int=8003)


def seal(proof: FutureDatedHistoryProof) -> FutureDatedHistoryProof:
    return proof.model_copy(update={"source_digest": history_digest(proof)})


def originals() -> FutureDatedHistoryProof:
    today = NOW.astimezone(ZoneInfo("Asia/Shanghai")).date()
    versions: list[dict[str, Any]] = []
    commands: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    for index in range(1, 3):
        confirmed = NOW - timedelta(minutes=3 - index)
        version_id, evidence_id, command_id = (
            UUID(int=8100 + index),
            UUID(int=8200 + index),
            UUID(int=8300 + index),
        )
        raw = {
            "type": "dated_expense",
            "window": {
                "start": (today + timedelta(days=index)).isoformat(),
                "end": (today + timedelta(days=index + 1)).isoformat(),
            },
            "amount": {"min_cents": 10001, "target_cents": 20002, "max_cents": 33303 + index},
        }
        config = validate_full_configuration("DatedExpensePolicy", raw)
        content_hash = configuration_hash(config)
        kind = "CREATE" if index == 1 else "CHANGE"
        body = {
            "accepted": True,
            "reviewed_hash": content_hash,
            "reason": "SYNTHETIC explicit future candidate",
            "idempotency_key": f"synthetic-dated-{index}",
            "configuration": raw,
        }
        if index == 1:
            body["template_name"] = "DatedExpensePolicy"
        else:
            body["expected_version_id"] = str(versions[-1]["id"])
        request = {
            "protocol": "full-policy-command-v1",
            "kind": kind,
            "user_id": str(USER),
            "policy_id": None if index == 1 else str(POLICY),
            "body": body,
        }
        request_hash = configuration_hash(request)
        confirmation = {
            "protocol": "full-policy-confirmation-v1",
            "user_id": str(USER),
            "epoch_id": str(EPOCH),
            "policy_id": str(POLICY),
            "version_id": str(version_id),
            "template_name": "DatedExpensePolicy",
            "reviewed_hash": content_hash,
            "confirmed_at": confirmed.isoformat(),
            "accepted": True,
            "bank_authority": False,
            "confirmation_evidence_id": str(evidence_id),
            "request_key": body["idempotency_key"],
            "request_hash": request_hash,
        }
        versions.append(
            {
                "id": str(version_id),
                "user_id": str(USER),
                "policy_id": str(POLICY),
                "version_number": index,
                "configuration": config,
                "content_hash": content_hash,
                "previous_hash": versions[-1]["content_hash"] if versions else None,
                "created_at": confirmed.isoformat(),
                "confirmed_at": confirmed.isoformat(),
                "valid_from": confirmed.isoformat(),
                "valid_until": None,
                "confirmation": confirmation,
                "evidence_ids": [str(evidence_id)],
                "impact_analysis": {"reference_snapshots": []},
            }
        )
        evidence.append(
            {
                "id": str(evidence_id),
                "user_id": str(USER),
                "source_type": "FULL_POLICY_CONFIRMATION",
                "evidence_level": "USER_CONFIRMED_POLICY",
                "source_ref": str(version_id),
                "status": "VALID",
                "content": deepcopy(confirmation),
                "content_hash": configuration_hash(confirmation),
                "observed_at": confirmed.isoformat(),
                "valid_from": confirmed.isoformat(),
            }
        )
        previous = commands[-1]["result_hash"] if commands else None
        result = FullLifecycleResult(
            policy_id=POLICY,
            epoch_id=EPOCH,
            version_id=version_id,
            command_id=command_id,
            command_number=index,
            previous_command_hash=previous,
            status="ACTIVE",
            configuration_hash=content_hash,
        ).model_dump(mode="json")
        commands.append(
            {
                "id": str(command_id),
                "user_id": str(USER),
                "epoch_id": str(EPOCH),
                "policy_id": str(POLICY),
                "version_id": str(version_id),
                "command_number": index,
                "previous_hash": previous,
                "previous_status": None if index == 1 else "ACTIVE",
                "resulting_status": "ACTIVE",
                "kind": kind,
                "idempotency_key": body["idempotency_key"],
                "request": request,
                "request_hash": request_hash,
                "result": result,
                "result_hash": configuration_hash(result),
                "created_at": confirmed.isoformat(),
            }
        )
    return seal(
        FutureDatedHistoryProof(
            status="VERIFIED_FUTURE_DATED_HISTORY",
            user_id=USER,
            epoch_id=EPOCH,
            policy_id=POLICY,
            current_version_id=UUID(versions[-1]["id"]),
            current_content_hash=versions[-1]["content_hash"],
            as_of=NOW,
            timezone="Asia/Shanghai",
            today=today,
            actual_version_count=2,
            captured_version_count=2,
            actual_command_count=2,
            captured_command_count=2,
            expected_evidence_count=2,
            captured_evidence_count=2,
            policy_original={
                "id": str(POLICY),
                "user_id": str(USER),
                "epoch_id": str(EPOCH),
                "template_name": "DatedExpensePolicy",
                "dsl_version": "FULL_V1",
                "status": "ACTIVE",
                "updated_at": commands[-1]["created_at"],
            },
            user_original={"id": str(USER), "timezone": "Asia/Shanghai", "is_simulated": True},
            epoch_original={"id": str(EPOCH), "user_id": str(USER), "status": "OPEN"},
            versions=versions,
            commands=commands,
            evidence_originals=evidence,
            source_digest="0" * 64,
            reasons=[],
        )
    )


def current_source(proof: FutureDatedHistoryProof) -> FullProtectionPolicySource:
    row = proof.versions[-1]
    return FullProtectionPolicySource(
        policy_id=POLICY,
        version_id=proof.current_version_id,
        version_number=2,
        template_name="DatedExpensePolicy",
        configuration=row["configuration"],
        content_hash=row["content_hash"],
        confirmation=row["confirmation"],
        reference_snapshots=[],
        confirmed_at=datetime.fromisoformat(row["confirmed_at"]),
        valid_from=datetime.fromisoformat(row["valid_from"]),
        valid_until=None,
        effective_status="ACTIVE",
        planning_confirmation_valid=True,
        references_current=True,
        evidence_ids=[UUID(value) for value in row["evidence_ids"]],
    )


def test_complete_future_originals_and_raw_noncanonical_request_have_no_unpaid_zero_or_grant() -> (
    None
):
    proof = originals()
    assert validate_future_dated_history(proof)
    source = current_source(proof)
    assert verify_future_dated_history(source, proof, NOW, "Asia/Shanghai")
    embedded = source.model_copy(
        update={
            "reference_snapshots": [
                {"kind": REFERENCE_KIND, "proof": proof.model_dump(mode="json")}
            ]
        }
    )
    assert verify_future_dated_history(embedded, proof, NOW, "Asia/Shanghai")
    assert proof.unpaid_amount_proven is proof.settlement_proven is proof.grants_authority is False
    assert "unpaid_cents" not in proof.model_dump()


@pytest.mark.parametrize(
    "case",
    [
        "today",
        "overdue",
        "periodic",
        "duplicate_version",
        "missing_version",
        "version_gap",
        "previous_hash",
        "missing_evidence",
        "evidence_owner",
        "evidence_hash",
        "unconfirmed",
        "boolean_number",
        "command_hash",
        "command_gap",
        "duplicate_command",
        "epoch",
        "command_epoch",
        "future_confirmation",
        "current_version",
        "count",
        "evidence_count",
        "status_claim",
    ],
)
def test_complete_proof_rejects_tampering_even_when_outer_digest_is_recomputed(case: str) -> None:
    proof = originals()
    if case in {"today", "overdue"}:
        due = proof.today - timedelta(days=case == "overdue")
        proof.versions[0]["configuration"]["window"] = {
            "start": due.isoformat(),
            "end": due.isoformat(),
        }
        proof.versions[0]["content_hash"] = configuration_hash(proof.versions[0]["configuration"])
    elif case == "periodic":
        proof.versions[0]["configuration"]["type"] = "periodic_transfer"
    elif case == "duplicate_version":
        proof.versions[1] = deepcopy(proof.versions[0])
    elif case == "missing_version":
        proof.versions.pop(0)
    elif case == "version_gap":
        proof.versions[0]["version_number"] = 2
    elif case == "previous_hash":
        proof.versions[1]["previous_hash"] = "f" * 64
    elif case == "missing_evidence":
        proof.evidence_originals.pop(0)
    elif case == "evidence_owner":
        proof.evidence_originals[0]["user_id"] = str(UUID(int=999))
    elif case == "evidence_hash":
        proof.evidence_originals[0]["content_hash"] = "f" * 64
    elif case == "unconfirmed":
        proof.versions[0]["confirmation"]["accepted"] = False
    elif case == "boolean_number":
        proof.versions[0]["version_number"] = True
    elif case == "command_hash":
        proof.commands[0]["request_hash"] = "f" * 64
    elif case == "command_gap":
        proof.commands[0]["command_number"] = 2
    elif case == "duplicate_command":
        proof.commands[1] = deepcopy(proof.commands[0])
    elif case == "epoch":
        assert proof.epoch_original is not None
        proof.epoch_original["status"] = "SEALED"
    elif case == "command_epoch":
        proof.commands[0]["epoch_id"] = str(UUID(int=999))
    elif case == "future_confirmation":
        proof.versions[0]["confirmed_at"] = (NOW + timedelta(seconds=1)).isoformat()
    elif case == "current_version":
        proof = proof.model_copy(update={"current_version_id": UUID(int=999)})
    elif case == "count":
        proof = proof.model_copy(update={"actual_version_count": 3})
    elif case == "evidence_count":
        proof = proof.model_copy(update={"expected_evidence_count": None})
    elif case == "status_claim":
        proof = proof.model_copy(update={"status": "UNKNOWN", "reasons": ["source missing"]})
    proof = seal(proof)
    assert not verify_future_dated_history(current_source(originals()), proof, NOW, "Asia/Shanghai")


@pytest.mark.parametrize(
    "change",
    [
        "clock",
        "zone",
        "owner",
        "version",
        "hash",
        "reference",
        "confirmation",
        "stale",
        "periodic",
        "duplicate_proof",
    ],
)
def test_proof_is_not_reusable_across_current_source_clock_or_epoch(change: str) -> None:
    proof = originals()
    source = current_source(proof)
    now, zone = NOW, "Asia/Shanghai"
    if change == "clock":
        now += timedelta(microseconds=1)
    elif change == "zone":
        zone = "UTC"
    elif change == "owner":
        source = source.model_copy(
            update={"confirmation": {**source.confirmation, "user_id": str(UUID(int=999))}}
        )
    elif change == "version":
        source = source.model_copy(update={"version_id": UUID(int=999)})
    elif change == "hash":
        source = source.model_copy(update={"content_hash": "f" * 64})
    elif change == "reference":
        source = source.model_copy(update={"reference_snapshots": [{"kind": "unknown"}]})
    elif change == "confirmation":
        source = source.model_copy(
            update={"confirmation": {**source.confirmation, "epoch_id": str(UUID(int=999))}}
        )
    elif change == "stale":
        source = source.model_copy(update={"references_current": False})
    elif change == "periodic":
        source = source.model_copy(update={"template_name": "PeriodicTransferPolicy"})
    elif change == "duplicate_proof":
        source = source.model_copy(
            update={
                "reference_snapshots": [
                    {"kind": REFERENCE_KIND, "proof": proof.model_dump(mode="json")}
                ]
                * 2
            }
        )
    assert not verify_future_dated_history(source, proof, now, zone)


def test_local_day_rollover_cannot_use_previous_day_strict_future_proof() -> None:
    proof = originals()
    assert not verify_future_dated_history(
        current_source(proof), proof, datetime(2026, 10, 6, 16, tzinfo=UTC), "Asia/Shanghai"
    )


@pytest.mark.parametrize(
    "field", ["actual_version_count", "captured_command_count", "expected_evidence_count"]
)
def test_boolean_counts_and_extra_authority_are_not_coerced(field: str) -> None:
    values = originals().model_dump()
    values[field] = True
    with pytest.raises(ValidationError):
        FutureDatedHistoryProof.model_validate(values)
    values = originals().model_dump()
    values["role"] = "USER"
    with pytest.raises(ValidationError):
        FutureDatedHistoryProof.model_validate(values)
    values = originals().model_dump()
    values["bank_authority"] = True
    with pytest.raises(ValidationError):
        FutureDatedHistoryProof.model_validate(values)
