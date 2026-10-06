"""Pure archive encoding checks; no SQL, bank, browser or financial success is produced.

The large fixture reconstructs the SQL manifest from an immutable failed UI
checkpoint. It is not a successful database seal or a new acceptance run.
"""

from __future__ import annotations

import copy
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

import pytest
from app.domain import audit_chain as domain
from app.domain.bank_posting_codec import bank_posting_snapshot_version
from app.services.audit_chain import SUBJECT_MODELS
from sqlalchemy import Table

from scripts.browser_checkpoint_oracles import normalized_original

if TYPE_CHECKING:
    from app.domain.audit_chain_types import AuditSubject

ROOT = Path(__file__).resolve().parents[4]
SNAPSHOT = ROOT / (
    "output/playwright/w1-20261005T033611Z-5216c9ce/checkpoints/"
    "0007-independent-case-start-before.json.gz"
)
SNAPSHOT_SHA = "46eacfa7aaa95ea6ebc1ea9830558abe0e717cb8d0f759643662c1bbb34156ef"
SNAPSHOT_DATA_SHA = "a7a93173fce5f13bd97c48589a2c7aeb42a37970f8adcd56f9fee952ba886517"
ARCHIVE_PREFIX = b"bounded-funds/audit-archive-v1\0"
SMALL_BYTES = (
    b'{"counts":{"ACCOUNT":2,"USER":1},"entries":['
    b'{"id":"00000000-0000-0000-0000-000000000001","kind":"ACCOUNT",'
    b'"snapshot_hash":"' + b"1" * 64 + b'"},'
    b'{"id":"00000000-0000-0000-0000-000000000001","kind":"ACCOUNT",'
    b'"snapshot_hash":"' + b"2" * 64 + b'"},'
    b'{"id":"00000000-0000-0000-0000-000000000002","kind":"USER",'
    b'"snapshot_hash":"' + b"3" * 64 + b'"}]}'
)
SMALL_DIGEST = "387376992e20348ef3c4363981d9ba907aa2fbba2da5968e91e4d56c0677b509"


def _json_bytes(value: dict[str, Any]) -> bytes:
    """Independent literal JSON bytes, without the production event byte limit."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _small() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(SMALL_BYTES))


@pytest.fixture(scope="module")
def actual_failed_checkpoint() -> dict[str, Any]:
    raw = SNAPSHOT.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SNAPSHOT_SHA, "Original failed evidence changed"
    uncompressed = gzip.decompress(raw)
    assert hashlib.sha256(uncompressed).hexdigest() == SNAPSHOT_DATA_SHA
    assert len(uncompressed) == 24_532_825
    value = cast(dict[str, Any], json.loads(uncompressed))
    assert sum(len(rows) for rows in value.values()) == 7610
    return value


@pytest.fixture(scope="module")
def reconstructed_manifest(
    actual_failed_checkpoint: dict[str, Any],
) -> tuple[dict[str, Any], tuple[AuditSubject, ...]]:
    """Reconstruct capture dedup and the SQL shape; do not claim SQL was executed."""
    checkpoint = actual_failed_checkpoint
    epoch = next(row for row in checkpoint["audit_epochs"] if row["status"] == "OPEN")
    user_id, epoch_id = UUID(epoch["user_id"]), UUID(epoch["id"])
    subjects: list[AuditSubject] = []
    entries: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in checkpoint["audit_subject_snapshots"]:
        if row["epoch_id"] != str(epoch_id):
            continue
        subject = domain.parse_subject(row["canonical_text"])
        assert domain.subject_hash(subject) == row["snapshot_hash"]
        key = row["kind"], row["entity_id"], row["snapshot_hash"]
        seen.add(key)
        subjects.append(subject)
        entries.append({"kind": key[0], "id": key[1], "snapshot_hash": key[2]})
    assert len(entries) == 107
    validated = 0
    for kind, model in SUBJECT_MODELS.items():
        for row in sorted(checkpoint[model.__tablename__], key=lambda row: row["id"]):
            if kind == "USER" and row["id"] != str(user_id):
                continue
            if kind not in {"USER", "ASSET_PRODUCT"} and row["user_id"] != str(user_id):
                continue
            data = normalized_original(cast(Table, model.__table__), row)
            subject, _, digest = domain.encode_subject_original(
                user_id=user_id,
                epoch_id=epoch_id,
                kind=kind,
                id=UUID(row["id"]),
                scope="GLOBAL_CATALOG" if kind == "ASSET_PRODUCT" else "TENANT",
                snapshot_version=bank_posting_snapshot_version(data)
                if kind == "BANK_POSTING"
                else 1,
                data=data,
            )
            validated += 1
            key = kind, row["id"], digest
            if key not in seen:
                seen.add(key)
                subjects.append(subject)
                entries.append({"kind": key[0], "id": key[1], "snapshot_hash": key[2]})
    assert validated == 7057
    entries.sort(key=lambda row: (row["kind"], row["id"], row["snapshot_hash"]))
    manifest: dict[str, Any] = {
        "entries": entries,
        "counts": dict(Counter(row["kind"] for row in entries)),
    }
    assert len(entries) == 7092
    assert manifest["counts"]["DECISION_CONSTRAINT"] == 6552
    assert len(_json_bytes(manifest)) == 1_115_737
    return manifest, tuple(subjects)


def test_small_archive_preserves_literal_original_bytes_hash_and_input() -> None:
    manifest = _small()
    original = copy.deepcopy(manifest)
    encoded = domain.archive_manifest_bytes(manifest)
    assert encoded == SMALL_BYTES == domain.canonical_bytes(manifest)
    assert hashlib.sha256(ARCHIVE_PREFIX + encoded).hexdigest() == SMALL_DIGEST
    assert manifest == original
    assert len(manifest["entries"]) == 3  # Distinct original versions of one account remain legal.


def test_empty_sql_manifest_remains_exact() -> None:
    assert domain.archive_manifest_bytes({"entries": [], "counts": {}}) == (
        b'{"counts":{},"entries":[]}'
    )


def test_actual_failed_checkpoint_large_manifest_encodes_without_relaxing_event_gate(
    reconstructed_manifest: tuple[dict[str, Any], tuple[AuditSubject, ...]],
) -> None:
    manifest, subjects = reconstructed_manifest
    independent = _json_bytes(manifest)
    assert len(independent) > domain.MAX_EVENT_BYTES == 1024 * 1024
    assert domain.MAX_ARCHIVE_MANIFEST_BYTES == 16 * 1024 * 1024
    with pytest.raises(ValueError, match="byte limit"):
        domain.canonical_bytes(manifest)
    assert domain.archive_manifest_bytes(manifest) == independent
    assert domain.archive_manifest(subjects) == manifest
    assert (
        domain.archive_manifest_digest(subjects)
        == hashlib.sha256(ARCHIVE_PREFIX + independent).hexdigest()
    )
    assert hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest() == SNAPSHOT_SHA


@pytest.mark.parametrize("offset", [0, -1])
def test_archive_uses_its_own_exact_byte_boundary(
    monkeypatch: pytest.MonkeyPatch, offset: int
) -> None:
    # The registered 50000-entry shape bound precedes 16MiB for ordinary rows.
    # Shrink only the archive cap to directly verify its inclusive byte boundary.
    monkeypatch.setattr(domain, "MAX_ARCHIVE_MANIFEST_BYTES", len(SMALL_BYTES) + offset)
    if offset == 0:
        assert domain.archive_manifest_bytes(_small()) == SMALL_BYTES
    else:
        with pytest.raises(ValueError, match="byte limit"):
            domain.archive_manifest_bytes(_small())
    assert domain.MAX_EVENT_BYTES == 1024 * 1024
    assert domain.canonical_bytes(_small()) == SMALL_BYTES


def _mutated_manifest(case: str) -> Any:
    manifest = _small()
    entry = manifest["entries"][0]
    if case == "duplicate":
        manifest["entries"].insert(0, copy.deepcopy(entry))
        manifest["counts"]["ACCOUNT"] += 1
    elif case == "reverse":
        manifest["entries"].reverse()
    elif case == "hash_order":
        manifest["entries"][0], manifest["entries"][1] = (
            manifest["entries"][1],
            manifest["entries"][0],
        )
    elif case == "delete_entry_keep_count":
        manifest["entries"].pop()
    elif case == "delete_count":
        del manifest["counts"]["USER"]
    elif case == "wrong_count":
        manifest["counts"]["USER"] = 2
    elif case == "unused_count":
        manifest["counts"]["EVIDENCE"] = 0
    elif case == "unknown_kind":
        entry["kind"] = "UNREGISTERED_ORIGINAL"
    elif case == "unknown_count_kind":
        manifest["counts"]["UNREGISTERED_ORIGINAL"] = 0
    elif case == "hash_short":
        entry["snapshot_hash"] = "1" * 63
    elif case == "hash_upper":
        entry["snapshot_hash"] = "A" * 64
    elif case == "hash_nonhex":
        entry["snapshot_hash"] = "z" * 64
    elif case == "uuid_invalid":
        entry["id"] = "not-a-uuid"
    elif case == "uuid_upper":
        entry["id"] = "AAAAAAAA-0000-0000-0000-000000000001"
    elif case == "uuid_compact":
        entry["id"] = "00000000000000000000000000000001"
    elif case == "extra_entry":
        entry["authority_granted"] = True
    elif case == "missing_entry_field":
        del entry["snapshot_hash"]
    elif case == "extra_top":
        manifest["success"] = True
    elif case == "missing_top":
        del manifest["counts"]
    elif case == "entries_object":
        manifest["entries"] = {}
    elif case == "entries_tuple":
        manifest["entries"] = tuple(manifest["entries"])
    elif case == "entry_scalar":
        manifest["entries"][0] = False
    elif case == "counts_array":
        manifest["counts"] = []
    elif case == "top_list":
        return [manifest]
    else:
        raise AssertionError(case)
    return manifest


@pytest.mark.parametrize(
    "case",
    [
        "duplicate",
        "reverse",
        "hash_order",
        "delete_entry_keep_count",
        "delete_count",
        "wrong_count",
        "unused_count",
        "unknown_kind",
        "unknown_count_kind",
        "hash_short",
        "hash_upper",
        "hash_nonhex",
        "uuid_invalid",
        "uuid_upper",
        "uuid_compact",
        "extra_entry",
        "missing_entry_field",
        "extra_top",
        "missing_top",
        "entries_object",
        "entries_tuple",
        "entry_scalar",
        "counts_array",
        "top_list",
    ],
)
def test_archive_rejects_malformed_or_missing_original_manifest_entries(case: str) -> None:
    with pytest.raises(ValueError):
        domain.archive_manifest_bytes(_mutated_manifest(case))


@pytest.mark.parametrize("value", [True, False, 1.0, -1, "1", None, 2**63])
def test_archive_counts_require_nonnegative_strict_signed64_integers(value: Any) -> None:
    manifest = _small()
    manifest["counts"]["USER"] = value
    with pytest.raises(ValueError):
        domain.archive_manifest_bytes(manifest)


@pytest.mark.parametrize("field", ["kind", "id", "snapshot_hash"])
@pytest.mark.parametrize("value", [True, 1.0, 1, None, []])
def test_archive_entry_fields_require_original_strings(field: str, value: Any) -> None:
    manifest = _small()
    manifest["entries"][0][field] = value
    with pytest.raises(ValueError):
        domain.archive_manifest_bytes(manifest)


def test_archive_keeps_registered_entry_bound() -> None:
    entries = [
        {"kind": "ACCOUNT", "id": str(UUID(int=index)), "snapshot_hash": "1" * 64}
        for index in range(50_001)
    ]
    with pytest.raises(ValueError):
        domain.archive_manifest_bytes({"entries": entries, "counts": {"ACCOUNT": len(entries)}})


def _previous_archive(checkpoint: dict[str, Any]) -> tuple[dict[str, Any], list[AuditSubject]]:
    sealed = next(row for row in checkpoint["audit_epochs"] if row["status"] == "SEALED")
    subjects = []
    for row in checkpoint["audit_subject_snapshots"]:
        if row["epoch_id"] != sealed["id"]:
            continue
        subject = domain.parse_subject(row["canonical_text"])
        assert domain.subject_hash(subject) == row["snapshot_hash"]
        subjects.append(subject)
    assert len(subjects) == 420
    return sealed, subjects


def test_real_previous_sealed_manifest_and_seal_bytes_remain_original(
    actual_failed_checkpoint: dict[str, Any],
) -> None:
    sealed, subjects = _previous_archive(actual_failed_checkpoint)
    seal = domain.parse_seal(sealed["seal_canonical_text"])
    assert domain.seal_canonical_text(seal) == sealed["seal_canonical_text"]
    assert seal.seal_hash == sealed["seal_hash"]
    manifest = domain.archive_manifest(subjects)
    assert manifest["counts"] == sealed["archive_record_counts"]
    assert domain.archive_manifest_bytes(manifest) == domain.canonical_bytes(manifest)
    assert domain.archive_manifest_digest(subjects) == sealed["archive_manifest_hash"]


@pytest.mark.parametrize("mutation", ["user_original", "bank_original", "deleted_original"])
def test_changes_to_real_previous_archive_never_match_the_original_sealed_digest(
    actual_failed_checkpoint: dict[str, Any], mutation: str
) -> None:
    sealed, subjects = _previous_archive(actual_failed_checkpoint)
    if mutation == "deleted_original":
        subjects.pop()
    else:
        kind = "USER" if mutation == "user_original" else "BANK_POSTING"
        index = next(index for index, subject in enumerate(subjects) if subject.kind == kind)
        fields = subjects[index].model_dump(mode="python")
        if kind == "USER":
            fields["data"]["display_name"] += " changed"
        else:
            fields["data"]["ledger_key"] += ":changed"
        subjects[index] = domain.build_subject(**fields)
    assert domain.archive_manifest_digest(subjects) != sealed["archive_manifest_hash"]
    assert hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest() == SNAPSHOT_SHA


@pytest.mark.parametrize("mutation", ["archive_hash", "archive_count", "head_count", "seal_hash"])
def test_real_previous_seal_original_hash_still_rejects_tampering(
    actual_failed_checkpoint: dict[str, Any], mutation: str
) -> None:
    sealed, _ = _previous_archive(actual_failed_checkpoint)
    fields = json.loads(sealed["seal_canonical_text"])
    if mutation == "archive_hash":
        fields["archive_manifest_hash"] = "0" * 64
    elif mutation == "archive_count":
        fields["archive_record_counts"]["USER"] += 1
    elif mutation == "head_count":
        fields["head"]["event_count"] += 1
    else:
        fields["seal_hash"] = "0" * 64
    with pytest.raises(ValueError):
        domain.parse_seal(_json_bytes(fields).decode("utf-8"))
