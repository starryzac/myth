"""STAGED private one-shot transport fault after a real independent Bank commit.

Not installed. No amount, bank result, permission, USER/SYSTEM role or receipt
may be supplied in this server-owned profile. The future native call site is
inside execution's existing try/UNKNOWN handler, AFTER process_operation.
An absent pin disables this seam without a database read. Sidecars record only
the lost response; they never prove a financial result or grant authority.
"""

import hashlib
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from app.db.models import (
    ActionPlan,
    ActionReceipt,
    AuditEvent,
    BankOperation,
    DecisionRun,
    SimulatedBankPosting,
)
from app.domain.boundary_types import BoundaryModel
from app.domain.execution import execution_effect_hash
from app.domain.execution_types import BankCommand
from app.domain.policy_configuration import UUIDReference, configuration_hash
from app.domain.zhiyu_standing_journal import StandingRun
from app.domain.zhiyu_standing_native import (
    MARKER,
    DelegatedNativeInput,
    DelegatedNativeProof,
    JointGoalOriginal,
    original_effects,
)
from app.services.audit_chain import audit_read_scope, row_copy
from app.services.decision_trace import get_decision_trace
from app.services.demo_console import _epoch
from app.services.execution_projection import _identity as native_bank_identity
from app.services.execution_projection import _legs as native_bank_legs
from app.services.historical_read import historical_ledger_scope
from app.services.simulated_bank import ledger_heads
from app.services.zhiyu_orchestration import _audit_healthy
from app.services.zhiyu_standing_recording import (
    binding,
    read_native_proof,
    verify_action_binding,
    verify_standing_trace,
)
from app.services.zhiyu_standing_store import original_grant, original_runs
from app.zhiyu_next_isolation import require_zhiyu_next_engine
from pydantic import Field, model_validator
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

PIN_ENV = "ZHIYU_NEXT_STANDING_SERVER_LOSS_SHA256"
PROFILE_NAME = "standing-private-post-bank-loss.json"
CONSUMED_NAME = "standing-private-post-bank-loss.consumed.json"
MAX_PROFILE_BYTES = 16 * 1024
# Private fault-injection transport record only; native/audit evidence limits stay unchanged.
MAX_CONSUMED_BYTES = 16 * 1024 * 1024
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
RoundName = Annotated[str, Field(pattern=r"^\d{8}T\d{6}Z-[0-9a-f]{32}$")]


class PrivateLossProfile(BoundaryModel):
    protocol: Literal["standing-private-post-bank-response-loss-v1"]
    purpose: Literal["DEVELOPMENT_TRANSPORT_ONLY"]
    family: Literal["SINGLE_GOAL", "JOINT_GOALS"]
    profile_id: UUIDReference
    round: RoundName
    environment_id: Annotated[str, Field(pattern=r"^bf_test_[0-9a-f]{32}$")]
    user_id: UUIDReference
    epoch_id: UUIDReference
    grant_id: UUIDReference
    event_key: Hash
    created_at: datetime
    expires_at: datetime
    # Explicitly fixed; this setting cannot prove acceptance or change a native gate.
    bank_authority: Literal[False]

    @model_validator(mode="before")
    @classmethod
    def literal_no_authority(cls, value: Any) -> Any:
        if isinstance(value, dict) and value.get("bank_authority") is not False:
            raise ValueError("Private transport setting must explicitly carry no bank authority")
        if isinstance(value, dict):
            value = dict(value)
            # A before-model validator returns Python inputs to strict fields.
            # Decode only the two fixed server profile timestamps, not arbitrary facts.
            for name in ("created_at", "expires_at"):
                if isinstance(value.get(name), str):
                    value[name] = datetime.fromisoformat(value[name])
                instant = value.get(name)
                if isinstance(instant, datetime):
                    if instant.tzinfo is None or instant.utcoffset() is None:
                        raise ValueError("Private transport timestamps must be timezone-aware")
                    value[name] = instant.astimezone(UTC)
        return value

    @model_validator(mode="after")
    def finite_exact_namespace(self) -> Self:
        if self.round[-32:] != self.environment_id.removeprefix("bf_test_") or not timedelta(
            0
        ) < self.expires_at - self.created_at <= timedelta(days=1):
            raise ValueError(
                "Private transport profile requires one exact finite development round"
            )
        return self


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate private transport JSON field")
        value[key] = item
    return value


def _bounded_bytes(path: Path, capacity: int) -> bytes:
    if path.is_symlink() or path.is_junction() or not path.is_file():
        raise ValueError("Private transport original must be a regular owned file")
    with path.open("rb") as stream:
        raw = stream.read(capacity + 1)
    if len(raw) > capacity:
        raise ValueError("Private transport original exceeds its capacity")
    return raw


def read_profile(directory: Path, reviewed_sha256: str) -> PrivateLossProfile:
    if re.fullmatch(r"[0-9a-f]{64}", reviewed_sha256) is None:
        raise ValueError("Explicit private server profile SHA256 required")
    if (
        directory.is_symlink()
        or directory.is_junction()
        or directory.resolve() != directory.absolute()
    ):
        raise ValueError("Private profile cannot redirect the owned round")
    raw = _bounded_bytes(directory / PROFILE_NAME, MAX_PROFILE_BYTES)
    if hashlib.sha256(raw).hexdigest() != reviewed_sha256:
        raise ValueError("Private server profile differs from the explicit launch pin")
    data = json.loads(raw, object_pairs_hook=_unique_object)
    value = PrivateLossProfile.model_validate_json(json.dumps(data))
    if value.round != directory.name or value.bank_authority is not False:
        raise ValueError("Private transport profile changed its exact round or authority")
    return value


def _fixed_claim(profile: PrivateLossProfile, digest: str) -> dict[str, Any]:
    return {
        "protocol": "standing-private-response-loss-consumed-v1",
        "profile_id": str(profile.profile_id),
        "profile_sha256": digest,
        "round": profile.round,
        "environment_id": profile.environment_id,
        "user_id": str(profile.user_id),
        "epoch_id": str(profile.epoch_id),
        "grant_id": str(profile.grant_id),
        "event_key": profile.event_key,
        "bank_authority": False,
        "financial_proof": False,
    }


def consumed(directory: Path, profile: PrivateLossProfile, digest: str) -> bool:
    path = directory / CONSUMED_NAME
    if not path.exists():
        return False
    record = json.loads(_bounded_bytes(path, MAX_CONSUMED_BYTES), object_pairs_hook=_unique_object)
    if (
        type(record) is not dict
        or set(record) != {"fixed", "original_references", "discarded_at", "record_hash"}
        or record["fixed"] != _fixed_claim(profile, digest)
        or record["fixed"]["bank_authority"] is not False
        or record["fixed"]["financial_proof"] is not False
        or configuration_hash({key: value for key, value in record.items() if key != "record_hash"})
        != record["record_hash"]
    ):
        raise ValueError("Preserve an invalid/partial one-shot claim for review; never replace it")
    # This only disables a second injected loss. Native Bank validation/projection
    # independently verifies the original. A sidecar is never financial evidence.
    return True


def claim_once(
    directory: Path,
    profile: PrivateLossProfile,
    digest: str,
    original_references: dict[str, Any],
    now: datetime,
) -> bool:
    if consumed(directory, profile, digest):
        return False
    value = {
        "fixed": _fixed_claim(profile, digest),
        "original_references": original_references,
        "discarded_at": now.isoformat(),
    }
    value["record_hash"] = configuration_hash(value)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    if len(raw) > MAX_CONSUMED_BYTES:
        raise ValueError("Preserve oversized original references; do not inject a loss")
    try:
        with (directory / CONSUMED_NAME).open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        # A concurrent/incomplete claim is a review boundary, never overwritten.
        consumed(directory, profile, digest)
        return False
    return True


def _joint_first_child_inventory(
    frozen: DelegatedNativeInput, proof: DelegatedNativeProof, run: StandingRun, number: int
) -> dict[str, Any]:
    """Only an already native/audited READY complete JOINT may use this branch."""
    native = frozen.native
    if not isinstance(native, JointGoalOriginal):
        raise ValueError("Private JOINT loss requires a typed original joint whole")
    effects = original_effects(native)
    hashes = [execution_effect_hash(effect) for effect in effects]
    identities = [effect.operation_id for effect in effects]
    keys = [
        "standing:" + frozen.original_source.source.event_key + ":" + str(i)
        for i in range(1, len(effects) + 1)
    ]
    if (
        frozen.phase != "PREPARE"
        or proof.phase != "PREPARE"
        or frozen.protocol != "zhiyu-standing-delegated-native-v2"
        or proof.protocol != frozen.protocol
        or frozen.grant.scope.family != "JOINT_GOALS"
        or proof.family != "JOINT_GOALS"
        or number != 1
        or not 2 <= len(effects) <= 8
        or len(set(identities)) != len(effects)
        or len({effect.goal_id for effect in effects}) != len(effects)
        or any(effect.action_type != "ALLOCATE_GOAL" for effect in effects)
        or proof.status != "READY"
        or proof.executor_role != "SYSTEM"
        or proof.user_id != frozen.user_id
        or proof.epoch_id != frozen.epoch_id
        or proof.event_key != frozen.original_source.source.event_key
        or proof.grant_id != frozen.grant.grant_id
        or proof.grant_hash != configuration_hash(frozen.grant.model_dump(mode="json"))
        or frozen.settled_prefix != 0
        or frozen.original_settlements
        or frozen.current_control.state != "ACTIVE"
        or frozen.knowledge.status != "KNOWN"
        or frozen.knowledge.ordered_effect_hashes != hashes
        or proof.ordered_effect_hashes != hashes
        or run.state != "ACCEPTED"
        or run.source != frozen.original_source.source
        or run.grant_id != frozen.grant.grant_id
        or run.grant_hash != proof.grant_hash
        or run.control_revision_at_acceptance != proof.control_revision
        or run.whole_plan_hash != configuration_hash(native.model_dump(mode="json"))
        or run.original_action_ids != identities
        or run.original_effect_hashes != hashes
        or run.original_bank_keys != keys
        or run.original_receipt_ids
        or run.accepted_total_cents != sum(effect.amount_cents for effect in effects)
        or proof.original_total_cents != run.accepted_total_cents
        or native.plan.user_id != frozen.user_id
        or native.plan.epoch_id != frozen.epoch_id
    ):
        raise ValueError("Private JOINT loss requires exact accepted whole/vector/source/SYSTEM")
    return {
        "family": "JOINT_GOALS",
        "native_protocol": frozen.protocol,
        "native_plan_hash": native.plan.plan_hash,
        "whole_plan_hash": run.whole_plan_hash,
        "run_record_hash": run.record_hash,
        "original_action_ids": [str(identity) for identity in identities],
        "original_effect_hashes": hashes,
        "original_bank_keys": keys,
        "accepted_total_cents": run.accepted_total_cents,
        "bank_authority": False,
        "financial_proof": False,
    }


def _joint_unaccepted_suffix(
    session: Session, user: UUID, frozen: DelegatedNativeInput, first_marker: dict[str, Any]
) -> list[dict[str, Any]]:
    result = []
    for number, effect in enumerate(original_effects(frozen.native)[1:], start=2):
        expected_key = "standing:" + frozen.original_source.source.event_key + ":" + str(number)
        action = session.get(ActionPlan, effect.operation_id)
        if action is not None:
            marker = binding(action)
            if (
                action.user_id != user
                or action.status != "PLANNED"
                or action.idempotency_key != expected_key
                or verify_action_binding(action, frozen, marker) != number
                or marker["accepted_proof_evidence_id"]
                != first_marker["accepted_proof_evidence_id"]
                or marker["accepted_proof_evidence_hash"]
                != first_marker["accepted_proof_evidence_hash"]
            ):
                raise ValueError(
                    "Private JOINT suffix materialized action must remain original PLANNED"
                )
        banks = list(
            session.scalars(
                select(BankOperation).where(BankOperation.action_plan_id == effect.operation_id)
            )
        )
        receipts = list(
            session.scalars(
                select(ActionReceipt).where(ActionReceipt.action_plan_id == effect.operation_id)
            )
        )
        if banks or receipts:
            raise ValueError("Private JOINT suffix must have no accepted Bank or receipt")
        result.append(
            {
                "child_number": number,
                "action_id": str(effect.operation_id),
                "original_effect_hash": execution_effect_hash(effect),
                "original_bank_key": expected_key,
                "state": "PLANNED" if action is not None else "NOT_YET_MATERIALIZED",
                "original_action": row_copy(action) if action is not None else None,
                "bank_count": 0,
                "receipt_count": 0,
            }
        )
    return result


def _verified_accepted_bank(
    engine: Engine, user: UUID, action_id: UUID, now: datetime, profile: PrivateLossProfile
) -> dict[str, Any] | None:
    require_zhiyu_next_engine(engine, check_connection=True)
    if str(engine.url.database) != profile.environment_id or user != profile.user_id:
        raise ValueError("Private transport loss cannot escape the real owned database/user")
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            with Session(bind=connection, autoflush=False) as session:
                with historical_ledger_scope(session), audit_read_scope(session):
                    _audit_healthy(session, user)
                    if _epoch(session, user) != profile.epoch_id:
                        raise ValueError("Private transport epoch differs from the actual original")
                    action = session.get(ActionPlan, action_id)
                    if action is None or action.user_id != user or MARKER not in action.request:
                        return None
                    marker = binding(action)
                    if marker["event_key"] != profile.event_key:
                        return None  # This narrowly pinned source alone can lose its response.
                    frozen, proof = read_native_proof(
                        session,
                        user,
                        UUID(marker["accepted_proof_evidence_id"]),
                        marker["accepted_proof_evidence_hash"],
                    )
                    number = verify_action_binding(action, frozen, marker)
                    grant = original_grant(session, user, profile.grant_id)
                    runs = [
                        run
                        for run in original_runs(session, user)
                        if run.source.event_key == profile.event_key
                    ]
                    if (
                        proof.status != "READY"
                        or proof.executor_role != "SYSTEM"
                        or frozen.grant != grant
                        or grant.scope.family != profile.family
                        or marker["grant_id"] != str(profile.grant_id)
                        or number != 1
                        or len(runs) != 1
                        or runs[0].state != "ACCEPTED"
                        or runs[0].grant_id != grant.grant_id
                        or (
                            profile.family == "SINGLE_GOAL"
                            and runs[0].original_action_ids != [action_id]
                        )
                        or action.status != "SUBMITTED"
                    ):
                        raise ValueError(
                            "Private transport loss requires the real one-child SYSTEM original"
                        )
                    joint_original: dict[str, Any] = {}
                    if profile.family == "JOINT_GOALS":
                        joint_original = _joint_first_child_inventory(
                            frozen, proof, runs[0], number
                        )
                        joint_original["unaccepted_suffix"] = _joint_unaccepted_suffix(
                            session, user, frozen, marker
                        )
                    command = BankCommand.model_validate_json(
                        json.dumps(action.request["execution"])
                    )
                    banks = list(
                        session.scalars(
                            select(BankOperation).where(
                                BankOperation.user_id == user,
                                BankOperation.action_plan_id == action_id,
                            )
                        )
                    )
                    receipts = list(
                        session.scalars(
                            select(ActionReceipt).where(
                                ActionReceipt.user_id == user,
                                ActionReceipt.action_plan_id == action_id,
                            )
                        )
                    )
                    if len(banks) != 1 or receipts:
                        raise ValueError(
                            "Response loss needs one actual accepted Bank and no receipt"
                        )
                    bank = banks[0]
                    if (
                        bank.status != "SETTLED"
                        or bank.settled_at is None
                        or bank.settled_at > now
                        or bank.requested_at > now
                        or bank.idempotency_key != action.idempotency_key
                        or bank.request != command.model_dump(mode="json")
                        or bank.request_hash != configuration_hash(bank.request)
                        or command.effect_hash != execution_effect_hash(command.effect)
                        or (
                            profile.family == "SINGLE_GOAL"
                            and (
                                runs[0].original_effect_hashes != [command.effect_hash]
                                or runs[0].original_bank_keys != [bank.idempotency_key]
                            )
                        )
                        or (
                            profile.family == "JOINT_GOALS"
                            and (
                                runs[0].original_effect_hashes[0] != command.effect_hash
                                or runs[0].original_bank_keys[0] != bank.idempotency_key
                            )
                        )
                    ):
                        raise ValueError(
                            "Actual settled Bank differs from the immutable original "
                            "request/effect/key"
                        )
                    ledger_heads(session, user)
                    native_action, native_effect = native_bank_identity(
                        session, bank, now, lock_user=False
                    )
                    if native_action.id != action.id or native_effect != command.effect:
                        raise ValueError("Native Bank owner/request/effect reader differs")
                    # Reuse the original native complete economic/location leg verifier.
                    native_bank_legs(session, bank, native_effect, now)
                    postings = [
                        row_copy(row)
                        for row in session.scalars(
                            select(SimulatedBankPosting)
                            .where(
                                SimulatedBankPosting.user_id == user,
                                SimulatedBankPosting.operation_id == bank.id,
                            )
                            .order_by(SimulatedBankPosting.id)
                        )
                    ]
                    if not postings:
                        raise ValueError("Actual original Bank postings are required")
                    bank_phase: dict[str, Any] | None = None
                    for run in session.scalars(
                        select(DecisionRun).where(
                            DecisionRun.user_id == user,
                            DecisionRun.subject_action_plan_id == action_id,
                        )
                    ):
                        declared = run.input_snapshot.get("decision_trace", {})
                        if declared.get("algorithm_versions", {}).get(MARKER) != frozen.protocol:
                            continue
                        view = get_decision_trace(session, user, run.id, now)
                        trace = view.trace
                        if trace is None or trace.phase != "BANK_ACCEPT":
                            continue
                        if view.completeness != "COMPLETE" or view.audit_chain_status != "VALID":
                            raise ValueError(
                                "A real native BANK_ACCEPT trace must already be complete"
                            )
                        verify_standing_trace(trace)
                        reference = trace.inputs[MARKER]
                        phase_input, phase_proof = read_native_proof(
                            session,
                            user,
                            UUID(reference["proof_evidence_id"]),
                            reference["proof_evidence_hash"],
                        )
                        if (
                            trace.action_id != action.id
                            or phase_input.phase != "BANK_ACCEPT"
                            or phase_proof.status != "READY"
                            or phase_proof.executor_role != "SYSTEM"
                            or phase_input.original_source != frozen.original_source
                            or phase_input.grant != frozen.grant
                        ):
                            raise ValueError(
                                "Native BANK_ACCEPT must bind this exact frozen source/grant"
                            )
                        verify_action_binding(action, phase_input, marker)
                        if profile.family == "JOINT_GOALS" and (
                            not isinstance(phase_input.native, JointGoalOriginal)
                            or not isinstance(frozen.native, JointGoalOriginal)
                            or phase_input.native.plan != frozen.native.plan
                            or original_effects(phase_input.native)
                            != original_effects(frozen.native)
                            or phase_input.settled_prefix != 0
                            or phase_input.original_settlements
                            or phase_input.current_control.state != "ACTIVE"
                            or phase_input.knowledge.status != "KNOWN"
                            or phase_proof.ordered_effect_hashes != runs[0].original_effect_hashes
                        ):
                            raise ValueError(
                                "Private JOINT BANK_ACCEPT whole/current proof differs"
                            )
                        bank_phase = {
                            "decision_trace_id": str(run.id),
                            "decision_trace_hash": trace.trace_hash,
                            "native_proof_hash": phase_proof.proof_hash,
                            "proof_reference": reference,
                        }
                    if bank_phase is None:
                        raise ValueError(
                            "Private loss cannot substitute a missing native BANK_ACCEPT"
                        )
                    bank_events = list(
                        session.scalars(
                            select(AuditEvent)
                            .where(
                                AuditEvent.user_id == user,
                                AuditEvent.epoch_id == profile.epoch_id,
                                AuditEvent.aggregate_type == "BANK_OPERATION",
                                AuditEvent.aggregate_id == bank.id,
                                AuditEvent.event_type.in_(["BANK_ACCEPTED", "BANK_SETTLED"]),
                            )
                            .order_by(AuditEvent.sequence_number)
                        )
                    )
                    if len(bank_events) != 2 or [row.event_type for row in bank_events] != [
                        "BANK_ACCEPTED",
                        "BANK_SETTLED",
                    ]:
                        raise ValueError(
                            "Exact independently verified Bank audit originals required"
                        )
                    audit_head = session.scalar(
                        select(AuditEvent)
                        .where(
                            AuditEvent.user_id == user,
                            AuditEvent.epoch_id == profile.epoch_id,
                        )
                        .order_by(AuditEvent.sequence_number.desc())
                        .limit(1)
                    )
                    if audit_head is None:
                        raise ValueError("Actual audit head must be retained")
                    return {
                        **({"joint_original": joint_original} if joint_original else {}),
                        "action_id": str(action.id),
                        "action_request_hash": action.request_hash,
                        "original_effect_hash": command.effect_hash,
                        "bank_id": str(bank.id),
                        "bank_key": bank.idempotency_key,
                        "bank_request_hash": bank.request_hash,
                        "bank_row_hash": configuration_hash(row_copy(bank)),
                        "postings_hash": configuration_hash({"postings": postings}),
                        "posting_ids": [row["id"] for row in postings],
                        "whole_plan_hash": marker["whole_plan_hash"],
                        "accepted_proof_evidence_id": marker["accepted_proof_evidence_id"],
                        "accepted_proof_evidence_hash": marker["accepted_proof_evidence_hash"],
                        "bank_phase": bank_phase,
                        "original_action": row_copy(action),
                        "original_bank": row_copy(bank),
                        "original_postings": postings,
                        "original_scope": grant.scope.model_dump(mode="json"),
                        "original_bank_audit_events": [row_copy(row) for row in bank_events],
                        "audit_head": {
                            "id": str(audit_head.id),
                            "sequence": audit_head.sequence_number,
                            "event_hash": audit_head.event_hash,
                        },
                    }


def maybe_lose_original_response(
    engine: Engine, user: UUID, action_id: UUID, now: datetime
) -> None:
    pin = os.environ.get(PIN_ENV)
    if pin is None:
        return  # Default production operation performs no extra read or loss.
    from scripts import zhiyu_next as launcher

    directory = launcher.current_round(os.environ.get("ZHIYU_NEXT_ROUND"))
    metadata, _ = launcher.read_round(directory)
    profile = read_profile(directory, pin)
    if (
        metadata["simulation"] is not True
        or metadata["epoch_id"] != str(profile.epoch_id)
        or metadata["environment_id"] != profile.environment_id
    ):
        raise ValueError("Private loss requires the exact registered development epoch")
    if consumed(directory, profile, pin):
        return
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Native server transport clock must be aware")
    if not profile.created_at <= now.astimezone(UTC) < profile.expires_at:
        return  # Expired transport test settings never replace original Bank recovery.
    original = _verified_accepted_bank(engine, user, action_id, now, profile)
    if original is not None and claim_once(directory, profile, pin, original, now):
        raise TimeoutError("SIMULATED_STANDING_BANK_RESPONSE_LOST")
