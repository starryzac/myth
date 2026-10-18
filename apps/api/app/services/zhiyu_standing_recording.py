"""Audited whole originals and semantic traces for delegated SYSTEM v2 only."""

import json
import zlib
from _thread import LockType
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from threading import Lock
from typing import Any, cast
from uuid import UUID, uuid5

from app.db.models import ActionPlan, DecisionRun, EvidenceItem
from app.domain.decision_trace import build_trace, verify_trace
from app.domain.decision_trace_types import DecisionTrace
from app.domain.execution import execution_effect_hash
from app.domain.execution_types import BankCommand
from app.domain.local_actor_session_types import require_local_user
from app.domain.policy_configuration import configuration_hash
from app.domain.zhiyu_standing_journal import StandingRun, grant_record_payload, verify_transition
from app.domain.zhiyu_standing_native import (
    ALGORITHM,
    MARKER,
    DelegatedNativeInput,
    DelegatedNativeProof,
    derive_delegated_native_proof,
    original_effects,
)
from app.domain.zhiyu_standing_transport import pack_native_original, read_native_original
from app.services.decision_trace import capture_policies, capture_sources, record_trace
from app.services.demo_console import _epoch
from app.services.zhiyu_orchestration import MARKER as EXTENSION_MARKER
from app.services.zhiyu_orchestration import _valid, store
from app.services.zhiyu_standing_store import StandingControl
from sqlalchemy.orm import Session

PROOF_PROTOCOL = "zhiyu-standing-native-proof-record-v2"
BINDING_PROTOCOL = "zhiyu-standing-fixed-action-binding-v2"
PROOF_FIELDS = {"protocol", "native_original", "native_original_hash", "proof", "bank_authority"}
BINDING_FIELDS = {
    "protocol",
    "event_key",
    "grant_id",
    "whole_plan_hash",
    "accepted_proof_evidence_id",
    "accepted_proof_evidence_hash",
    "child_number",
    "bank_key",
}


# Only successful historical record replays live here; fresh native inputs and
# current permission checks never enter this memo. Private bytes isolate callers.
_REPLAY_MAX_ENTRIES = 16
_REPLAY_MAX_BYTES = 8 * 1024 * 1024
_REPLAY_MAX_DECODED_BYTES = 64 * 1024 * 1024
_ReplayEntry = tuple[bytes, int, bytes, int]
_recorded_replays: ContextVar[tuple[dict[str, _ReplayEntry], LockType] | None] = ContextVar(
    "recorded_native_replay_invocation", default=None
)


@contextmanager
def recorded_native_replay_scope() -> Iterator[None]:
    if _recorded_replays.get() is not None:
        yield
        return
    token = _recorded_replays.set(({}, Lock()))
    try:
        yield
    finally:
        _recorded_replays.reset(token)


def _recorded_bytes(raw: bytes, length: int) -> bytes:
    if not 0 < length <= _REPLAY_MAX_DECODED_BYTES:
        raise ValueError("Recorded replay exceeds the private decoded byte bound")
    decoder = zlib.decompressobj()
    result = decoder.decompress(raw, length + 1)
    if (
        len(result) != length or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
    ):
        raise ValueError("Recorded replay private bytes changed")
    return result


def _known_variant(variant: str) -> None:
    if variant not in {ALGORITHM, "zhiyu-standing-income-goal-native-v1"}:
        raise ValueError("Unsupported standing native variant")


def binding_protocol(variant: str) -> str:
    _known_variant(variant)
    return (
        "standing-income-goal-fixed-binding-v1"
        if variant == "zhiyu-standing-income-goal-native-v1"
        else BINDING_PROTOCOL
    )


def record_protocol(variant: str) -> str:
    _known_variant(variant)
    return (
        "standing-income-goal-proof-record-v1"
        if variant == "zhiyu-standing-income-goal-native-v1"
        else PROOF_PROTOCOL
    )


def proof_key(proof_hash: str, variant: str = ALGORITHM) -> str:
    _known_variant(variant)
    return (
        "standing-income-goal:proof:"
        if variant == "zhiyu-standing-income-goal-native-v1"
        else "standing-v2:proof:"
    ) + proof_hash


def record_native_proof(
    session: Session, value: DelegatedNativeInput, proof: DelegatedNativeProof, now: datetime
) -> EvidenceItem:
    if (
        proof != derive_delegated_native_proof(value)
        or value.as_of != now
        or proof.status != "READY"
    ):
        raise ValueError("Only the real complete READY native proof can be persisted")
    packed = pack_native_original(value.model_dump(mode="json"))
    payload = {
        "protocol": record_protocol(value.protocol),
        "native_original": packed,
        "native_original_hash": configuration_hash(packed),
        "proof": proof.model_dump(mode="json"),
        "bank_authority": False,
    }
    store(
        session, value.user_id, "EVENT", proof_key(proof.proof_hash, value.protocol), payload, now
    )
    identity = uuid5(
        value.epoch_id, f"{EXTENSION_MARKER}:EVENT:{proof_key(proof.proof_hash, value.protocol)}"
    )
    row = session.get(EvidenceItem, identity)
    if row is None:
        raise ValueError("Audited native proof original was not persisted")
    return row


def parse_payload(payload: dict[str, Any]) -> tuple[DelegatedNativeInput, DelegatedNativeProof]:
    if (
        set(payload) != PROOF_FIELDS
        or payload["protocol"] not in {PROOF_PROTOCOL, "standing-income-goal-proof-record-v1"}
        or payload["bank_authority"] is not False
    ):
        raise ValueError("Exact closed delegated original proof record required")
    memo = _recorded_replays.get()
    identity = (
        configuration_hash(
            {"validator": PROOF_PROTOCOL, "algorithm": ALGORITHM, "payload": payload}
        )
        if memo else None
    )
    if memo is not None and identity is not None:
        with memo[1]:
            cached = memo[0].get(identity)
        if cached is not None:
            result = (
                DelegatedNativeInput.model_validate_json(_recorded_bytes(cached[0], cached[1])),
                DelegatedNativeProof.model_validate_json(_recorded_bytes(cached[2], cached[3])),
            )
            if identity != configuration_hash(
                {"validator": PROOF_PROTOCOL, "algorithm": ALGORITHM, "payload": payload}
            ):
                raise ValueError("Recorded replay complete original changed during the read")
            return result
    decoded = read_native_original(
        payload["native_original"], expected_record_hash=payload["native_original_hash"]
    )
    value = DelegatedNativeInput.model_validate_json(json.dumps(decoded))
    proof = DelegatedNativeProof.model_validate_json(json.dumps(payload["proof"]))
    if (
        payload["protocol"] != record_protocol(value.protocol)
        or proof.protocol != value.protocol
        or proof != derive_delegated_native_proof(value)
        or proof.status != "READY"
    ):
        raise ValueError("Delegated native original mathematical replay differs")
    if memo is not None and identity is not None:
        if identity != configuration_hash(
            {"validator": PROOF_PROTOCOL, "algorithm": ALGORITHM, "payload": payload}
        ):
            raise ValueError("Recorded replay complete original changed during verification")
        encoded_input = value.model_dump_json().encode("utf-8")
        encoded_proof = proof.model_dump_json().encode("utf-8")
        if len(encoded_input) + len(encoded_proof) <= _REPLAY_MAX_DECODED_BYTES:
            entry = (
                zlib.compress(encoded_input, level=1), len(encoded_input),
                zlib.compress(encoded_proof, level=1), len(encoded_proof),
            )
            with memo[1]:
                size = sum(len(row[0]) + len(row[2]) for row in memo[0].values())
                if len(memo[0]) < _REPLAY_MAX_ENTRIES and (
                    size + len(entry[0]) + len(entry[2]) <= _REPLAY_MAX_BYTES
                ):
                    memo[0][identity] = entry
    return value, proof


def read_native_proof(
    session: Session, user: UUID, identity: UUID, expected_hash: str
) -> tuple[DelegatedNativeInput, DelegatedNativeProof]:
    row = session.get(EvidenceItem, identity)
    if row is None or row.content_hash != expected_hash or row.user_id != user:
        raise ValueError("Independent original delegated proof reference differs")
    epoch_id = _epoch(session, user)
    _valid(session, row, user, epoch_id)
    value, proof = parse_payload(row.content["payload"])
    if value.user_id != user or value.epoch_id != epoch_id:
        raise ValueError("Inner native proof owner/epoch differs from its audited original")
    if (
        row.content["key"] != proof_key(proof.proof_hash, value.protocol)
        or row.observed_at != value.as_of
    ):
        raise ValueError("Original proof key or server clock differs")
    return value, proof


def binding(action: ActionPlan) -> dict[str, Any]:
    marker = action.request.get(MARKER)
    if (
        type(marker) is not dict
        or set(marker) != BINDING_FIELDS
        or marker["protocol"] not in {BINDING_PROTOCOL, "standing-income-goal-fixed-binding-v1"}
        or type(marker["child_number"]) is not int
        or not 1 <= marker["child_number"] <= 8
        or marker["bank_key"] != action.idempotency_key
        or action.autonomy_level != "AUTO_EXECUTE"
        or not {"intent", "execution", "prepared_validation", MARKER} <= set(action.request)
        or not set(action.request)
        <= {"intent", "execution", "prepared_validation", MARKER, "income_evidence"}
        or configuration_hash(action.request) != action.request_hash
    ):
        raise ValueError("Exact original delegated SYSTEM action binding required")
    return marker


def verify_action_binding(
    action: ActionPlan, frozen: DelegatedNativeInput, marker: dict[str, Any]
) -> int:
    command = BankCommand.model_validate_json(json.dumps(action.request["execution"]))
    effects = original_effects(frozen.native)
    number = marker["child_number"]
    if (
        marker["protocol"] != binding_protocol(frozen.protocol)
        or marker["event_key"] != frozen.original_source.source.event_key
        or marker["grant_id"] != str(frozen.grant.grant_id)
        or marker["whole_plan_hash"] != configuration_hash(frozen.native.model_dump(mode="json"))
        or not 1 <= number <= len(effects)
        or command.effect != effects[number - 1]
        or action.id != command.effect.operation_id
        or action.user_id != frozen.user_id
        or action.amount_cents != command.effect.amount_cents
    ):
        raise ValueError("Original action is not this complete accepted whole native vector")
    return cast(int, number)


def record_standing_phase_trace(
    session: Session,
    action: ActionPlan,
    value: DelegatedNativeInput,
    proof: DelegatedNativeProof,
    *,
    existing_run: DecisionRun | None = None,
) -> DecisionRun:
    marker = binding(action)
    frozen, _ = read_native_proof(
        session,
        action.user_id,
        UUID(marker["accepted_proof_evidence_id"]),
        marker["accepted_proof_evidence_hash"],
    )
    number = verify_action_binding(action, frozen, marker)
    if value.original_source != frozen.original_source or original_effects(
        value.native
    ) != original_effects(frozen.native):
        raise ValueError("Current revalidation cannot replace original effects or trigger")
    row = record_native_proof(session, value, proof, value.as_of)
    evidence_ids = {row.id, UUID(marker["accepted_proof_evidence_id"])}
    for fact in value.current_facts:
        evidence_ids.update(fact.source_evidence_ids)
    from app.domain.zhiyu_standing_native import AssetOriginal

    if isinstance(value.native, AssetOriginal) and value.native.owned_read_proof is not None:
        evidence_ids.update(
            UUID(row["id"]) for row in value.native.owned_read_proof.evidence_originals
        )
    # Independently audited source/grant/control/current journal originals are
    # copied in full, never replaced with claimed proof booleans.
    from app.services.zhiyu_standing_store import _rows

    evidence_ids.update(item.id for item in _rows(session, action.user_id, "standing-v1:"))
    trace = build_trace(
        run_id=existing_run.id
        if existing_run
        else uuid5(
            action.id,
            f"{value.protocol}:{value.phase}:{value.as_of.isoformat()}:{proof.proof_hash}",
        ),
        user_id=action.user_id,
        action_id=action.id,
        parent_run_id=None if existing_run else action.decision_run_id,
        phase=value.phase,
        as_of=value.as_of,
        algorithm_versions={"trace": "decision-trace-v1", MARKER: value.protocol},
        inputs={
            MARKER: {
                "proof_evidence_id": str(row.id),
                "proof_evidence_hash": row.content_hash,
                "child_number": number,
            },
            "effect": action.request["execution"]["effect"],
            "action_request": action.request,
        },
        sources=capture_sources(session, action.user_id, evidence_ids),
        policies=capture_policies(
            session,
            action.user_id,
            {
                identity
                for effect in original_effects(value.native)
                for identity in effect.policy_version_ids
            },
        ),
        outcome={
            "proof_hash": proof.proof_hash,
            "native_status": "READY",
            "autonomy_level": "AUTO_EXECUTE",
            "decision_status": "COMPUTED",
        },
    )
    verify_standing_trace(trace)
    return record_trace(session, trace, existing_run=existing_run)


def verify_captured_standing_originals(
    contents: list[dict[str, Any]], value: DelegatedNativeInput
) -> None:
    """Cross-bind native copies to complete independently captured store originals.

    Caller must first verify the enclosing trace and every source content hash.
    PREPARE permits only its atomic accepted journal successor, never a different
    grant, event, effect vector, budget or later control revision.
    """
    from app.domain.zhiyu_standing_native import AssetOriginal

    if isinstance(value.native, AssetOriginal) and value.native.owned_read_proof is not None:
        owned = value.native.owned_read_proof
        matching = [
            row for row in contents if configuration_hash(row) == owned.accepted_evidence_hash
        ]
        if len(matching) != 1:
            raise ValueError("Owned residual view requires the independent captured accepted proof")
        accepted, _ = parse_payload(matching[0]["payload"])
        if (
            not isinstance(accepted.native, AssetOriginal)
            or accepted.native.portfolio != value.native.portfolio
            or accepted.native.current_mode_evaluation != value.native.current_mode_evaluation
            or accepted.native.mode_source_manifest != value.native.mode_source_manifest
            or original_effects(accepted.native) != owned.effects
            or configuration_hash(accepted.native.model_dump(mode="json"))
            != owned.accepted_native_hash
            or uuid5(value.epoch_id, f"{EXTENSION_MARKER}:EVENT:{matching[0]['key']}")
            != owned.accepted_evidence_id
        ):
            raise ValueError("Independently captured accepted whole differs from owned read scope")
        for original in owned.evidence_originals:
            if (
                original["content_hash"] != configuration_hash(original["content"])
                or original["content"] not in contents
            ):
                raise ValueError("Source lineage needs complete independent native proof contents")
        exposure_sources = [row for row in contents if row == owned.exposure_original]
        if len(exposure_sources) != 1:
            raise ValueError("Owned current complete exposure must be an independent trace source")

    records = [row for row in contents if str(row.get("key", "")).startswith("standing-v1:")]
    for row in records:
        if (
            set(row)
            != {"protocol", "simulation", "environment_id", "user_id", "epoch_id", "key", "payload"}
            or row["protocol"] != EXTENSION_MARKER
            or row["simulation"] is not True
            or row["environment_id"] != value.original_source.source.environment_id
            or row["user_id"] != str(value.user_id)
            or row["epoch_id"] != str(value.epoch_id)
        ):
            raise ValueError("Independently captured standing original ownership differs")
    grant_key = "standing-v1:grant:" + str(value.grant.grant_id)
    grants = [row for row in records if row["key"] == grant_key]
    source_key = "standing-v1:source:" + value.original_source.source.event_key
    sources = [row for row in records if row["key"] == source_key]
    if (
        len(grants) != 1
        or grants[0]["payload"] != grant_record_payload(value.grant)
        or len(sources) != 1
        or sources[0]["payload"]
        != {
            "protocol": "standing-observed-original-record-v1",
            "observation": value.original_source.model_dump(mode="json"),
            "observation_hash": configuration_hash(value.original_source.model_dump(mode="json")),
            "native_bank_authority": False,
        }
    ):
        raise ValueError("Captured finite signed grant or source original differs")
    controls = [
        StandingControl.model_validate_json(json.dumps(row["payload"]))
        for row in records
        if row["key"].startswith("standing-v1:control:" + str(value.grant.grant_id) + ":")
    ]
    if not controls or max(controls, key=lambda row: row.revision) != value.current_control:
        raise ValueError("Captured latest current USER control differs")
    previous_control = None
    for current_control in sorted(controls, key=lambda row: row.revision):
        require_local_user(current_control.actor, value.user_id, current_control.recorded_at)
        if (
            current_control.grant_id != value.grant.grant_id
            or current_control.expected_previous_revision != current_control.revision - 1
            or (
                previous_control is None
                and (
                    current_control.revision != 1
                    or current_control.state != "ACTIVE"
                    or current_control.previous_record_hash is not None
                )
            )
            or (
                previous_control is not None
                and (
                    current_control.revision != previous_control.revision + 1
                    or current_control.previous_record_hash
                    != configuration_hash(previous_control.model_dump(mode="json"))
                    or current_control.recorded_at < previous_control.recorded_at
                    or previous_control.state == "REVOKED"
                )
            )
        ):
            raise ValueError("Captured complete finite USER control chain differs")
        previous_control = current_control
    histories: dict[str, list[StandingRun]] = {}
    for row in records:
        if row["key"].startswith("standing-v1:run:"):
            current = StandingRun.model_validate_json(json.dumps(row["payload"]))
            if row["key"] != "standing-v1:run:" + current.source.event_key + ":" + str(
                current.revision
            ):
                raise ValueError("Captured original journal key differs")
            histories.setdefault(current.source.event_key, []).append(current)
    if set(histories) != {row.source.event_key for row in value.current_runs}:
        raise ValueError("Captured complete current journal denominator differs")
    for expected in value.current_runs:
        history = sorted(histories[expected.source.event_key], key=lambda row: row.revision)
        previous = None
        for current in history:
            verify_transition(previous, current)
            previous = current
        exact = [row for row in history if row.revision == expected.revision]
        if exact != [expected]:
            raise ValueError("Captured current journal original differs")
        later = [row for row in history if row.revision > expected.revision]
        if later:
            effects = original_effects(value.native)
            if (
                value.phase != "PREPARE"
                or expected.source != value.original_source.source
                or len(later) != 1
                or later[0].state != "ACCEPTED"
                or later[0].grant_id != value.grant.grant_id
                or later[0].grant_hash != configuration_hash(value.grant.model_dump(mode="json"))
                or later[0].control_revision_at_acceptance != value.current_control.revision
                or later[0].whole_plan_hash
                != configuration_hash(value.native.model_dump(mode="json"))
                or later[0].original_action_ids != [effect.operation_id for effect in effects]
                or later[0].original_effect_hashes
                != [execution_effect_hash(effect) for effect in effects]
                or later[0].original_bank_keys
                != [
                    "standing:" + expected.source.event_key + ":" + str(number)
                    for number in range(1, len(effects) + 1)
                ]
                or later[0].accepted_total_cents != sum(effect.amount_cents for effect in effects)
                or later[0].recorded_at != value.as_of
            ):
                raise ValueError("Captured journal successor is not this atomic whole acceptance")


def verify_standing_trace(trace: DecisionTrace) -> None:
    verify_trace(trace)
    if (
        trace.algorithm_versions.get(MARKER)
        not in {ALGORITHM, "zhiyu-standing-income-goal-native-v1"}
        or trace.phase not in {"PREPARE", "RESERVE", "BANK_ACCEPT"}
        or set(trace.inputs) != {MARKER, "effect", "action_request"}
        or set(trace.outcome)
        != {"proof_hash", "native_status", "autonomy_level", "decision_status"}
    ):
        raise ValueError("Exact delegated SYSTEM semantic trace required")
    reference = trace.inputs[MARKER]
    if type(reference) is not dict or set(reference) != {
        "proof_evidence_id",
        "proof_evidence_hash",
        "child_number",
    }:
        raise ValueError("Exact independently audited delegated trace reference required")
    rows = [row for row in trace.sources if str(row.id) == reference["proof_evidence_id"]]
    if (
        len(rows) != 1
        or rows[0].content_hash != reference["proof_evidence_hash"]
        or rows[0].source_type != "ZHIYU_NEXT_EVENT"
    ):
        raise ValueError("Original source proof evidence differs")
    value, proof = parse_payload(rows[0].content["payload"])
    if trace.algorithm_versions.get(MARKER) != value.protocol:
        raise ValueError("Native trace variant differs from audited proof")
    verify_captured_standing_originals([row.content for row in trace.sources], value)
    number = reference["child_number"]
    effects = original_effects(value.native)
    if type(number) is not int or not value.settled_prefix < number <= len(effects):
        raise ValueError("Original fixed remaining child order differs")
    effect = effects[number - 1]
    request = trace.inputs["action_request"]
    marker = request.get(MARKER)
    if (
        value.phase != trace.phase
        or value.user_id != trace.user_id
        or value.as_of != trace.as_of
        or trace.action_id != effect.operation_id
        or trace.inputs["effect"] != effect.model_dump(mode="json")
        or type(marker) is not dict
        or set(marker) != BINDING_FIELDS
        or marker["event_key"] != value.original_source.source.event_key
        or marker["grant_id"] != str(value.grant.grant_id)
        or marker["child_number"] != number
        or request.get("execution")
        != BankCommand(effect=effect, effect_hash=execution_effect_hash(effect)).model_dump(
            mode="json"
        )
        or trace.outcome
        != {
            "proof_hash": proof.proof_hash,
            "native_status": "READY",
            "autonomy_level": "AUTO_EXECUTE",
            "decision_status": "COMPUTED",
        }
    ):
        raise ValueError("Complete delegated trace phase, original effect or native proof differs")
    accepted = [row for row in trace.sources if str(row.id) == marker["accepted_proof_evidence_id"]]
    if len(accepted) != 1 or accepted[0].content_hash != marker["accepted_proof_evidence_hash"]:
        raise ValueError("Independent accepted whole-native original reference differs")
    frozen, accepted_proof = parse_payload(accepted[0].content["payload"])
    if (
        accepted_proof.phase != "PREPARE"
        or frozen.original_source != value.original_source
        or original_effects(frozen.native) != effects
        or marker["whole_plan_hash"] != configuration_hash(frozen.native.model_dump(mode="json"))
        or marker["protocol"] != binding_protocol(frozen.protocol)
    ):
        raise ValueError("Original whole delegation cannot be replaced by current or USER consent")
