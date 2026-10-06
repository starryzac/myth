"""TOOL_ONLY typed original-state/arithmetic risks; no actual financial experiment."""

from __future__ import annotations

import copy
import hashlib
from typing import Any
from uuid import UUID, uuid5

import pytest

from scripts.mvp_financial_oracles import Facts, compute_timeline, validate_facts
from scripts.tests.test_mvp_financial_oracles import (
    AT,
    OWNER,
    Fixture,
    checkpoint,
    encoded,
    hashed,
)

EPOCH = str(UUID(int=80000))


def typed(fixture: Fixture, *, confirmation_only: bool = False) -> dict[str, Any]:
    facts = fixture.facts()
    subjects = []
    events = []
    for index, original in enumerate(fixture.events):
        policy = next(
            row for row in facts["tables"]["policies"] if row["id"] == original["policy_id"]
        )
        version = next(
            row for row in facts["tables"]["policy_versions"] if row["id"] == original["version_id"]
        )
        refs = []
        values = [
            ("POLICY", "AFTER", policy),
            ("POLICY_VERSION", "AFTER" if confirmation_only else "BASIS", version),
        ]
        if not confirmation_only:
            values.append(("POLICY", "BEFORE", dict(policy, status=original["from_status"])))
        for kind, role, data in values:
            subject = {
                "schema_version": "audit-subject-v1",
                "canonical_version": "audit-canonical-json-v1",
                "simulation": True,
                "user_id": OWNER,
                "epoch_id": EPOCH,
                "kind": kind,
                "id": data["id"],
                "scope": "TENANT",
                "snapshot_version": 1,
                "data": data,
            }
            raw = encoded(subject)
            digest = hashlib.sha256(b"bounded-funds/audit-subject-v1\0" + raw).hexdigest()
            subjects.append(
                {
                    "id": str(uuid5(UUID(data["id"]), role + str(index))),
                    "user_id": OWNER,
                    "epoch_id": EPOCH,
                    "kind": kind,
                    "entity_id": data["id"],
                    "canonical_text": raw.decode(),
                    "snapshot_hash": digest,
                }
            )
            refs.append(
                {
                    "kind": kind,
                    "id": data["id"],
                    "scope": "TENANT",
                    "user_id": OWNER,
                    "role": role,
                    "snapshot_hash": digest,
                    "snapshot_version": 1,
                }
            )
        changes = (
            []
            if confirmation_only
            else [
                {
                    "kind": "POLICY",
                    "id": policy["id"],
                    "field": "status",
                    "before": original["from_status"],
                    "after": original["to_status"],
                    "before_snapshot_hash": refs[2]["snapshot_hash"],
                    "after_snapshot_hash": refs[0]["snapshot_hash"],
                }
            ]
        )
        event: dict[str, Any] = {
            "schema_version": "audit-event-v1",
            "canonical_version": "audit-canonical-json-v1",
            "simulation": True,
            "id": str(uuid5(UUID(policy["id"]), "event" + str(index))),
            "user_id": OWNER,
            "epoch_id": EPOCH,
            "event_type": "POLICY_VERSION_CONFIRMED"
            if confirmation_only
            else "POLICY_STATE_CHANGED",
            "aggregate_type": "POLICY_VERSION" if confirmation_only else "POLICY",
            "aggregate_id": version["id"] if confirmation_only else policy["id"],
            "correlation_id": policy["id"],
            "sequence_number": index + 1,
            "occurred_at": original["occurred_at"],
            "payload": {"correlation_kind": "POLICY", "references": refs, "changes": changes},
        }
        event["event_hash"] = hashlib.sha256(
            b"bounded-funds/audit-event-v1\0" + encoded(event)
        ).hexdigest()
        events.append({**event, "canonical_text": encoded(event).decode()})
    facts.update(
        protocol="mvp-financial-facts-v2",
        policy_state_events_protocol="TYPED_AUDIT_POLICY_EVENTS_V1",
        policy_state_events=events,
        policy_subjects=subjects,
    )
    rebind(facts)
    return facts


def rebind(facts: dict[str, Any]) -> None:
    # Save a new pure original rather than erasing the earlier embedded basis.
    value = {
        "audit_events": facts["policy_state_events"],
        "audit_subject_snapshots": facts["policy_subjects"],
    }
    raw = encoded(value)
    digest = hashlib.sha256(raw).hexdigest()
    facts["artifact_originals"][digest] = {"utf8": raw.decode()}
    facts["policy_state_events_source_ref"] = {
        "artifact_sha256": digest,
        "json_pointer": "/audit_events",
        "value_sha256": hashed(value["audit_events"]),
    }
    facts["policy_subjects_source_ref"] = {
        "artifact_sha256": digest,
        "json_pointer": "/audit_subject_snapshots",
        "value_sha256": hashed(value["audit_subject_snapshots"]),
    }


def test_typed_state_originals_are_independently_derived() -> None:
    fixture = Fixture()
    fixture.emergency(1234)
    facts = typed(fixture)
    checked = Facts(facts)
    assert checked.events[0]["from_status"] == "PROPOSED"
    assert checked.events[0]["to_status"] == "ACTIVE"
    assert "from_status" not in facts["policy_state_events"][0]
    assert validate_facts(facts)["status"] == "VERIFIED"
    measured = compute_timeline(facts, [checkpoint()])["checkpoints"][0]
    assert measured["status"] == "MEASURED", measured
    assert measured["available_cash_cents"] == 100000
    assert measured["protected_required_cents"] == 1234


def test_typed_confirmation_only_preserves_unknown_initial_status() -> None:
    fixture = Fixture()
    fixture.emergency(1234)
    facts = typed(fixture, confirmation_only=True)
    checked = Facts(facts)
    assert checked.events[0]["from_status"] is None
    assert checked.events[0]["to_status"] == "ACTIVE"
    assert compute_timeline(facts, [checkpoint()])["checkpoints"][0]["status"] == "MEASURED"


@pytest.mark.parametrize(
    "change",
    (
        "subject_text",
        "subject_hash",
        "subject_owner",
        "subject_epoch",
        "event_text",
        "event_row",
        "event_hash",
        "event_owner",
        "event_duplicate",
        "change_before",
        "change_after",
        "change_snapshot",
        "missing_subject",
        "missing_array",
        "missing_version",
        "v1_typed",
    ),
)
def test_typed_original_tamper_or_gap_is_missing(change: str) -> None:
    fixture = Fixture()
    fixture.emergency(1234)
    facts = typed(fixture)
    event = facts["policy_state_events"][0]
    subjects = facts["policy_subjects"]
    if change.startswith("subject_"):
        key = {
            "subject_text": "canonical_text",
            "subject_hash": "snapshot_hash",
            "subject_owner": "user_id",
            "subject_epoch": "epoch_id",
        }[change]
        subjects[0][key] = "CORRUPTED"
    elif change == "event_text":
        event["canonical_text"] += " "
    elif change == "event_row":
        event["payload"]["correlation_kind"] = "GOAL"
    elif change == "event_hash":
        event["event_hash"] = "0" * 64
    elif change == "event_owner":
        event["user_id"] = str(UUID(int=7000))
    elif change == "event_duplicate":
        facts["policy_state_events"].append(copy.deepcopy(event))
    elif change.startswith("change_"):
        # Even with a newly consistent event hash, original subject contents
        # must still bind both sides and each exact snapshot digest.
        key = {
            "change_before": "before",
            "change_after": "after",
            "change_snapshot": "before_snapshot_hash",
        }[change]
        event["payload"]["changes"][0][key] = "CORRUPTED"
        content = {
            key: value
            for key, value in event.items()
            if key not in {"canonical_text", "event_hash"}
        }
        event["event_hash"] = hashlib.sha256(
            b"bounded-funds/audit-event-v1\0" + encoded(content)
        ).hexdigest()
        event["canonical_text"] = encoded({**content, "event_hash": event["event_hash"]}).decode()
    elif change == "missing_subject":
        subjects.pop()
    elif change == "missing_array":
        facts["policy_state_events"] = []
    elif change == "missing_version":
        facts["tables"]["policy_versions"] = []
    elif change == "v1_typed":
        facts["protocol"] = "mvp-financial-facts-v1"
    if change != "missing_array":
        rebind(facts)
    assert validate_facts(facts)["status"] == "MISSING"
    result = compute_timeline(facts, [checkpoint()])["checkpoints"][0]
    assert result["status"] == "MISSING" and result["available_cash_cents"] is None


@pytest.mark.parametrize("account_type", ("CASH_MANAGEMENT", "FIXED_DEPOSIT"))
def test_actual_asset_account_kinds_do_not_become_cash(account_type: str) -> None:
    fixture = Fixture()
    fixture.row("accounts", account_type=account_type, currency="CNY", balance_cents=987654)
    facts = typed(fixture)
    measured = compute_timeline(facts, [checkpoint()])["checkpoints"][0]
    assert measured["status"] == "MEASURED", measured
    assert measured["available_cash_cents"] == 100000


def test_explicit_facts_v2_capacity_and_v1_unchanged() -> None:
    fixture = Fixture()
    v1 = fixture.facts()
    v2_facts = dict(v1, protocol="mvp-financial-facts-v2")
    assert Facts(v1).original_byte_budget == 64 * 1024 * 1024
    assert Facts(v2_facts).original_byte_budget == 512 * 1024 * 1024
    assert Facts(v2_facts).as_of.isoformat() == AT
