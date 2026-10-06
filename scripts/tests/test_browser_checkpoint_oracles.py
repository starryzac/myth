"""TOOL_TEST_ONLY: read actual immutable reset evidence and reject mutated copies.

No database, bank operation, permission or browser is created by these tests. A
positive offline checkpoint result cannot replace the original failed UI run.
"""

from __future__ import annotations

import copy
import gzip
import hashlib
import json
import runpy
from datetime import date, datetime
from typing import Any, cast
from uuid import UUID

import pytest
from app.db.base import UTCDateTime
from app.db.models import SimulatedBankPosting, User
from app.domain.bank_posting_codec import POSTING_V1_FIELDS, POSTING_V2_FIELDS
from app.services.audit_chain import SUBJECT_MODELS
from sqlalchemy import Column, Date, DateTime, MetaData, Table, Uuid
from sqlalchemy.types import TypeDecorator

from scripts import browser_checkpoint_oracles as oracles

EVIDENCE = oracles.ROOT / "output/playwright/w1-20261005T031259Z-2473e2a7"
USER_TABLE = cast(Table, User.__table__)
BANK_TABLE = cast(Table, SimulatedBankPosting.__table__)
CHECKPOINTS = (
    (
        "0001-native-baseline.json.gz",
        "5a6e1cd253b19b83b20adbb4bf7f995d9badad9d0318e10a25461e34e0af605a",
    ),
    (
        "0002-independent-case-start-before.json.gz",
        "5a6e1cd253b19b83b20adbb4bf7f995d9badad9d0318e10a25461e34e0af605a",
    ),
    (
        "0003-independent-case-start-after.json.gz",
        "bff1c6fc43cee850fa817b66d4260a401516b26e1bcf4f570f2794ecd7a2753e",
    ),
)


@pytest.fixture
def snapshots() -> tuple[oracles.Snapshot, oracles.Snapshot, oracles.Snapshot]:
    loaded = []
    for filename, expected in CHECKPOINTS:
        raw = (EVIDENCE / "checkpoints" / filename).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == expected, "Original evidence changed"
        loaded.append(json.loads(gzip.decompress(raw)))
    baseline, before, after = loaded
    return before, after, baseline


def test_original_checker_still_reproduces_exact_user_failure(snapshots: Any) -> None:
    before, after, baseline = snapshots
    loaded = runpy.run_path(
        str(oracles.ORIGINAL_ORACLE_PATH), run_name="old_oracle_red_reproduction"
    )
    with pytest.raises(ValueError, match="Original USER/.+ absent from the actual sealed archive"):
        loaded["reset_oracle"](before, after, baseline)


def test_actual_reset_all_420_originals_with_no_mutation(snapshots: Any) -> None:
    before, after, baseline = snapshots
    originals = copy.deepcopy(snapshots)
    source = oracles.ORIGINAL_ORACLE_PATH.read_bytes()
    result = oracles.reset_oracle(before, after, baseline)
    counts = result["all20_business_originals_archived"]
    assert set(counts) == set(SUBJECT_MODELS)
    assert sum(counts.values()) == 420
    assert counts["USER"] == 1 and counts["BANK_POSTING"] == 17
    assert result["business20_baseline_exact"] is True
    assert result["reset_key"] == "337962f0-cabf-4453-bb59-f2708b014c57"
    assert snapshots == originals
    assert oracles.ORIGINAL_ORACLE_PATH.read_bytes() == source
    for filename, expected in CHECKPOINTS:
        assert (
            hashlib.sha256((EVIDENCE / "checkpoints" / filename).read_bytes()).hexdigest()
            == expected
        )


def test_user_wrapper_resolves_actual_utc_timestamp_without_other_changes(snapshots: Any) -> None:
    before, after, _ = snapshots
    row = before["users"][0]
    assert USER_TABLE.c.created_at.type.python_type is object
    assert cast(UTCDateTime, USER_TABLE.c.created_at.type).impl.python_type is datetime
    actual = oracles.normalized_original(USER_TABLE, row)
    assert actual["created_at"] == "2026-10-03T16:00:00.000000Z"
    assert {k: v for k, v in actual.items() if k != "created_at"} == {
        k: v for k, v in row.items() if k != "created_at"
    }
    archived = next(r for r in after["audit_subject_snapshots"] if r["kind"] == "USER")
    assert actual == json.loads(archived["canonical_text"])["data"]


class DateWrapper(TypeDecorator[date]):
    impl = Date
    cache_ok = True


class UUIDWrapper(TypeDecorator[UUID]):
    impl = Uuid
    cache_ok = True


def test_object_wrappers_restore_date_uuid_and_timezone_microseconds() -> None:
    table = Table(
        "tool_temporal_fixture",
        MetaData(),
        Column("on", DateWrapper()),
        Column("id", UUIDWrapper()),
        Column("at", DateTime(timezone=True)),
    )
    row = {"on": "2026-10-03", "id": str(UUID(int=5)), "at": "2026-10-03 16:00:00.123456+08:00"}
    assert oracles.normalized_original(table, row) == {
        **row,
        "at": "2026-10-03T08:00:00.123456Z",
    }


def test_bank_posting_registered_original_layout_and_jsonb_remain_exact(snapshots: Any) -> None:
    before, after, _ = snapshots
    for row in before["simulated_bank_postings"]:
        original = oracles.normalized_original(BANK_TABLE, row)
        archived = next(
            r
            for r in after["audit_subject_snapshots"]
            if r["kind"] == "BANK_POSTING" and r["entity_id"] == row["id"]
        )
        assert original == json.loads(archived["canonical_text"])["data"]
        columns = POSTING_V1_FIELDS if archived["snapshot_version"] == 1 else POSTING_V2_FIELDS
        assert set(original) == set(columns)
        if archived["snapshot_version"] == 1:
            assert "external_fact_id" not in original
        else:
            assert original["external_fact_id"] == row["external_fact_id"]
        assert original["ledger_metadata"] == row["ledger_metadata"]
        for key in (
            "balance_before_cents",
            "delta_cents",
            "balance_after_cents",
            "sequence_number",
        ):
            assert original[key] == row[key] and type(original[key]) is int


@pytest.mark.parametrize(
    "field,value",
    [
        ("created_at", "2026-10-04 16:00:00+00:00"),
        ("display_name", "TOOL_TEST_ONLY_CHANGED_USER"),
        ("timezone", "UTC"),
    ],
)
def test_changed_original_user_fields_rejected(snapshots: Any, field: str, value: str) -> None:
    before, after, baseline = snapshots
    before["users"][0][field] = value
    with pytest.raises(ValueError, match="Original USER/.+ absent"):
        oracles.reset_oracle(before, after, baseline)


@pytest.mark.parametrize(
    "field,value",
    [
        ("delta_cents", 1),
        ("ledger_metadata", {"changed": "TOOL_TEST_ONLY"}),
        ("occurred_at", "2026-10-04 16:00:00+00:00"),
    ],
)
def test_changed_original_bank_columns_rejected(snapshots: Any, field: str, value: Any) -> None:
    before, after, baseline = snapshots
    before["simulated_bank_postings"][0][field] = value
    with pytest.raises(ValueError):
        oracles.reset_oracle(before, after, baseline)


@pytest.mark.parametrize("value", ["0", 0.0, False])
def test_bank_cents_are_never_coerced(snapshots: Any, value: Any) -> None:
    before, _, _ = snapshots
    row = before["simulated_bank_postings"][0]
    row["delta_cents"] = value
    with pytest.raises(ValueError, match="must remain a strict integer"):
        oracles.normalized_original(BANK_TABLE, row)


@pytest.mark.parametrize("operation", ["delete_user", "change_user_date", "change_user_field"])
def test_actual_archive_missing_or_changed_original_rejected(
    snapshots: Any, operation: str
) -> None:
    before, after, baseline = snapshots
    subject = next(r for r in after["audit_subject_snapshots"] if r["kind"] == "USER")
    if operation == "delete_user":
        after["audit_subject_snapshots"].remove(subject)
    else:
        original = json.loads(subject["canonical_text"])
        key = "created_at" if operation == "change_user_date" else "external_ref"
        original["data"][key] = "TOOL_TEST_ONLY_TAMPER"
        subject["canonical_text"] = json.dumps(original)
    with pytest.raises(ValueError, match="Original USER/.+ absent"):
        oracles.reset_oracle(before, after, baseline)


@pytest.mark.parametrize(
    "target", ["old_seal_hash", "new_previous_hash", "old_audit", "new_baseline"]
)
def test_original_epoch_audit_and_baseline_invariants_retained(snapshots: Any, target: str) -> None:
    before, after, baseline = snapshots
    if target == "old_seal_hash":
        next(r for r in after["audit_epochs"] if r["status"] == "SEALED")["seal_hash"] = "0" * 64
    elif target == "new_previous_hash":
        next(r for r in after["audit_epochs"] if r["status"] == "OPEN")["previous_seal_hash"] = (
            "0" * 64
        )
    elif target == "old_audit":
        original_id = before["audit_events"][0]["id"]
        next(r for r in after["audit_events"] if r["id"] == original_id)["event_hash"] = "0" * 64
    else:
        after["accounts"][0]["balance_cents"] += 1
    with pytest.raises(ValueError):
        oracles.reset_oracle(before, after, baseline)


def test_retained_snapshot_hash_change_rejected_by_original_invariant(snapshots: Any) -> None:
    before, after, baseline = snapshots
    # TOOL_TEST_ONLY retained-copy fixture: the actual attempt had no prior
    # snapshots. This mutation tests the unchanged retained() invariant only.
    subject = next(r for r in after["audit_subject_snapshots"] if r["kind"] == "USER")
    before["audit_subject_snapshots"].append(copy.deepcopy(subject))
    subject["snapshot_hash"] = "0" * 64
    with pytest.raises(
        ValueError, match="Original audit_subject_snapshots/.+ changed or disappeared"
    ):
        oracles.reset_oracle(before, after, baseline)


def test_changed_original_checker_source_refuses_to_load(monkeypatch: Any) -> None:
    original = oracles.ORIGINAL_ORACLE_PATH.read_bytes()

    class AlteredSource:
        def read_bytes(self) -> bytes:
            return original + b"\n# TOOL_TEST_ONLY\n"

    # Pure byte fixture; do not write another oracle or need filesystem tmpdir.
    monkeypatch.setattr(oracles, "ORIGINAL_ORACLE_PATH", AlteredSource())
    with pytest.raises(ValueError, match="Original reset oracle source changed"):
        oracles.reset_oracle({}, {}, {})
