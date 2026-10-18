"""Explicit source-verified worker over durable originals and SYSTEM delegation."""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Self
from uuid import UUID, uuid5

from pydantic import Field, StrictInt, StrictStr, model_validator
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db.session import create_database_engine
from app.domain.boundary_types import BoundaryModel
from app.domain.demo_identity import DEMO_USER_ID
from app.domain.execution_types import BankCommand
from app.domain.immutable_joint_archive_scope import immutable_joint_archive_scope
from app.domain.immutable_joint_validation_scope import immutable_joint_validation_scope
from app.domain.policy_configuration import UUIDReference, configuration_hash
from app.domain.standing_income_goal_scope import (
    AnyScope,
    IncomeGoalScope,
    NativeProtocol,
    SignedIncomeGoalGrant,
    native_variant,
    parse_scope,
)
from app.domain.zhiyu_standing_journal import StandingSource
from app.domain.zhiyu_standing_scope import Family, Hash, StandingScope
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.zhiyu_local_actor import ROOT
from app.services.zhiyu_orchestration import _audit_healthy
from app.services.zhiyu_standing_observer import observe_once
from app.services.zhiyu_standing_recording import recorded_native_replay_scope
from app.services.zhiyu_standing_store import original_grant, original_runs
from app.zhiyu_next_isolation import require_zhiyu_next_engine

FAMILIES = {
    "SINGLE_GOAL",
    "JOINT_GOALS",
    "ASSET_PORTFOLIO",
    "MATURITY_REINVESTMENT",
    "PERIODIC_PAYMENT",
}
REQUIRED = {
    "scripts/zhiyu_next.py",
    "uv.lock",
    "pyproject.toml",
    "deploy/initialize_demo.py",
    "apps/api/app/workers/zhiyu_standing_worker.py",
    "apps/api/app/services/zhiyu_standing_dispatcher.py",
    "apps/api/app/services/zhiyu_standing_observer.py",
}

MAX_PROOF_BYTES = 2 * 1024 * 1024
NATIVE_PROTOCOL = "zhiyu-standing-delegated-native-v2"
BankKey = Annotated[StrictStr, Field(min_length=1, max_length=160)]
Zero = Annotated[StrictInt, Field(ge=0, le=0)]


class ActualPhaseOriginal(BoundaryModel):
    phase: Literal["PREPARE", "RESERVE", "BANK_ACCEPT"]
    native_protocol: NativeProtocol
    executor_role: Literal["SYSTEM"]
    proof_evidence_id: UUIDReference
    proof_evidence_hash: Hash
    native_proof_hash: Hash
    decision_trace_id: UUIDReference
    decision_trace_hash: Hash
    current_native_guard_status: Literal["READY"]
    complete_worlds_status: Literal["COMPLETE"]
    audit_status: Literal["VALID"]


class ActualReceiptOriginal(BoundaryModel):
    receipt_id: UUIDReference
    action_id: UUIDReference
    bank_operation_id: UUIDReference
    status: Literal["SUCCEEDED"]
    executed_cents: Annotated[StrictInt, Field(gt=0)]
    fee_cents: Zero
    loss_cents: Zero
    posting_ids: Annotated[list[UUIDReference], Field(min_length=2, max_length=512)]
    receipt_response_hash: Hash


class ActualActionOriginal(BoundaryModel):
    action_id: UUIDReference
    child_number: Annotated[StrictInt, Field(ge=1, le=8)]
    source_scope_hash: Hash
    original_source: StandingSource
    action_request_hash: Hash
    bank_operation_id: UUIDReference
    bank_idempotency_key: BankKey
    original_bank_command: BankCommand
    bank_request_hash: Hash
    bank_status: Literal["SETTLED"]
    receipt: ActualReceiptOriginal
    phases: Annotated[list[ActualPhaseOriginal], Field(min_length=3, max_length=3)]

    @model_validator(mode="before")
    @classmethod
    def complete_command(cls, value: Any) -> Any:
        from app.domain.execution_types import ExecutionEffect

        if isinstance(value, dict):
            command = value.get("original_bank_command")
            if (
                not isinstance(command, dict)
                or set(command) != set(BankCommand.model_fields)
                or not isinstance(command.get("effect"), dict)
                or set(command["effect"]) != set(ExecutionEffect.model_fields)
                or not isinstance(value.get("original_source"), dict)
                or set(value["original_source"]) != set(StandingSource.model_fields)
            ):
                raise ValueError("Actual command must include every original economic field")
            # A before-validator returns Python mappings. Preserve the native
            # models' strict JSON clock/UUID interpretation without coercion.
            value = dict(value)
            value["original_bank_command"] = BankCommand.model_validate_json(json.dumps(command))
            value["original_source"] = StandingSource.model_validate_json(
                json.dumps(value["original_source"])
            )
        return value

    @model_validator(mode="after")
    def bound_original(self) -> Self:
        effect = self.original_bank_command.effect
        expected_key = f"standing:{self.original_source.event_key}:{self.child_number}"
        if (
            effect.operation_id != self.action_id
            or self.bank_idempotency_key != expected_key
            or self.bank_request_hash
            != configuration_hash(self.original_bank_command.model_dump(mode="json"))
            or effect.fee_cents != 0
            or effect.loss_cents != 0
            or self.receipt.action_id != self.action_id
            or self.receipt.bank_operation_id != self.bank_operation_id
            or self.receipt.executed_cents != effect.amount_cents
            or len(set(self.receipt.posting_ids)) != len(self.receipt.posting_ids)
            or {row.phase for row in self.phases} != {"PREPARE", "RESERVE", "BANK_ACCEPT"}
        ):
            raise ValueError("Actual original action/command/receipt/native phases differ")
        return self


class ActualUnknownRecovery(BoundaryModel):
    unknown_state: Literal["UNKNOWN"]
    unknown_action_id: UUIDReference
    unknown_bank_operation_id: UUIDReference
    unknown_bank_idempotency_key: BankKey
    unknown_action_request_hash: Hash
    unknown_bank_request_hash: Hash
    unknown_effect_hash: Hash
    recovered_state: Literal["SUCCEEDED"]
    recovered_action_id: UUIDReference
    recovered_bank_operation_id: UUIDReference
    recovered_bank_idempotency_key: BankKey
    recovered_action_request_hash: Hash
    recovered_bank_request_hash: Hash
    recovered_effect_hash: Hash
    recovered_receipt_id: UUIDReference
    bank_original_hash_before: Hash
    bank_original_hash_after: Hash

    @model_validator(mode="after")
    def same_original(self) -> Self:
        if (
            self.unknown_action_id != self.recovered_action_id
            or self.unknown_bank_operation_id != self.recovered_bank_operation_id
            or self.unknown_bank_idempotency_key != self.recovered_bank_idempotency_key
            or self.unknown_action_request_hash != self.recovered_action_request_hash
            or self.unknown_bank_request_hash != self.recovered_bank_request_hash
            or self.unknown_effect_hash != self.recovered_effect_hash
            or self.bank_original_hash_before != self.bank_original_hash_after
        ):
            raise ValueError(
                "UNKNOWN recovery replaced its original action/bank/key/request/effect"
            )
        return self


class ActualFamilyReport(BoundaryModel):
    """Bound artifact format, NOT self-authenticating database or bank evidence.

    Root must independently verify these native originals in the actual test DB
    before pinning the complete manifest for an explicit worker launch.
    """

    protocol: Literal["zhiyu-next-standing-actual-family-report-v1"]
    validation_kind: Literal["ACTUAL_OWNED_POSTGRES_API_BANK"]
    status: Literal["PASSED"]
    family: Family
    executor_role: Literal["SYSTEM"]
    native_protocol: NativeProtocol
    old_one_shot_user_consent_inherited: Literal[False]
    round: Annotated[StrictStr, Field(min_length=1, max_length=160)]
    database: Annotated[StrictStr, Field(pattern=r"^bf_test_[0-9a-f]{32}$")]
    owner: Annotated[StrictStr, Field(pattern=r"^zhiyu_next_[0-9a-f]{32}$")]
    epoch_id: UUIDReference
    user_id: UUIDReference
    test_node: Annotated[StrictStr, Field(min_length=1, max_length=500)]
    source_scope: AnyScope
    reviewed_scope_hash: Hash
    grant_id: UUIDReference
    grant_hash: Hash
    actions: Annotated[list[ActualActionOriginal], Field(min_length=1, max_length=8)]
    unknown_recoveries: Annotated[list[ActualUnknownRecovery], Field(min_length=1, max_length=8)]
    audit_after_status: Literal["VALID"]
    ledger_after_status: Literal["VALID"]
    source_hashes_before: Annotated[dict[str, Hash], Field(min_length=1, max_length=20000)]
    source_hashes_after: Annotated[dict[str, Hash], Field(min_length=1, max_length=20000)]

    @model_validator(mode="before")
    @classmethod
    def explicit_scope_and_boolean(cls, value: Any) -> Any:
        if isinstance(value, dict) and (
            value.get("old_one_shot_user_consent_inherited") is not False
            or not isinstance(value.get("source_scope"), dict)
            or set(value["source_scope"])
            != set(
                IncomeGoalScope.model_fields
                if value.get("native_protocol") == "zhiyu-standing-income-goal-native-v1"
                else StandingScope.model_fields
            )
        ):
            raise ValueError("Actual report requires explicit complete reviewed scope and role")
        if isinstance(value, dict):
            selected = value["source_scope"]
            if value.get("native_protocol") == "zhiyu-standing-income-goal-native-v1" and (
                not isinstance(selected.get("native_scope"), dict)
                or set(selected["native_scope"]) != set(StandingScope.model_fields)
            ):
                raise ValueError("Complete native scope original required in cumulative report")
            value = dict(value)
            value["source_scope"] = parse_scope(json.dumps(value["source_scope"]))
        return value

    @model_validator(mode="after")
    def entire_family_binding(self) -> Self:
        identity = self.database.removeprefix("bf_test_")
        scope = self.source_scope
        if (
            re.fullmatch(r"\d{8}T\d{6}Z-" + identity, self.round) is None
            or self.owner != "zhiyu_next_" + identity
            or (scope.user_id, scope.epoch_id, scope.family)
            != (self.user_id, self.epoch_id, self.family)
            or configuration_hash(scope.model_dump(mode="json")) != self.reviewed_scope_hash
            or self.source_hashes_before != self.source_hashes_after
        ):
            raise ValueError("Actual family round/scope/source inventory is inconsistent")
        if (self.native_protocol == "zhiyu-standing-income-goal-native-v1") != isinstance(
            scope, IncomeGoalScope
        ) or any(
            phase.native_protocol != self.native_protocol
            for row in self.actions
            for phase in row.phases
        ):
            raise ValueError(
                "Actual original scope and every native phase must have the same explicit variant"
            )
        expected_type = (
            "ALLOCATE_GOAL"
            if self.family in {"SINGLE_GOAL", "JOINT_GOALS"}
            else "PAY_RECURRING"
            if self.family == "PERIODIC_PAYMENT"
            else "PURCHASE_ASSET"
        )
        if (
            len({row.action_id for row in self.actions}) != len(self.actions)
            or len({row.bank_idempotency_key for row in self.actions}) != len(self.actions)
            or len({row.bank_operation_id for row in self.actions}) != len(self.actions)
            or len({row.receipt.receipt_id for row in self.actions}) != len(self.actions)
            or len(self.actions) > scope.maximum_children
            or sorted(row.child_number for row in self.actions)
            != list(range(1, len(self.actions) + 1))
            or len({row.original_source.event_key for row in self.actions}) != 1
        ):
            raise ValueError("Actual family needs unique bounded original action/bank/receipts")
        for row in self.actions:
            effect = row.original_bank_command.effect
            if self.native_protocol == "zhiyu-standing-income-goal-native-v1" and (
                effect.business_key != self.native_protocol + ":" + row.original_source.event_key
                or not effect.income_uses
                or sum(use.amount_cents for use in effect.income_uses) != effect.amount_cents
            ):
                raise ValueError(
                    "Cumulative actual proof needs its own original event income effect"
                )
            if (
                row.source_scope_hash != self.reviewed_scope_hash
                or effect.user_id != self.user_id
                or effect.action_type != expected_type
                or row.original_source.environment_id != self.database
                or (row.original_source.user_id, row.original_source.epoch_id)
                != (self.user_id, self.epoch_id)
                or effect.amount_cents > scope.per_action_cap_cents
                or not effect.policy_version_ids
                or not set(effect.policy_version_ids).issubset(scope.policy_version_ids)
                or any(use.account_id not in scope.source_account_ids for use in effect.cash_uses)
                or effect.goal_id is not None
                and effect.goal_id not in scope.goal_ids
                or effect.product_id is not None
                and effect.product_id not in scope.product_ids
                or self.family == "PERIODIC_PAYMENT"
                and effect.payee_id != scope.payee_id
                or self.family == "MATURITY_REINVESTMENT"
                and row.original_source.source_kind != "SETTLED_MATURITY_RECEIPT"
            ):
                raise ValueError("Actual action does not belong to its original delegated scope")
        if (
            self.family == "JOINT_GOALS"
            and len({row.original_bank_command.effect.goal_id for row in self.actions}) < 2
        ):
            raise ValueError("Actual joint family must exercise two distinct goal destinations")
        if (
            sum(row.original_bank_command.effect.amount_cents for row in self.actions)
            > scope.per_event_cap_cents
        ):
            raise ValueError("Actual vector exceeds its original event cap")
        originals = {row.action_id: row for row in self.actions}
        if len({row.unknown_action_id for row in self.unknown_recoveries}) != len(
            self.unknown_recoveries
        ):
            raise ValueError("Unknown recovery originals must be unique")
        for restored in self.unknown_recoveries:
            restored_original = originals.get(restored.unknown_action_id)
            if restored_original is None or (
                restored.unknown_bank_operation_id != restored_original.bank_operation_id
                or restored.unknown_bank_idempotency_key != restored_original.bank_idempotency_key
                or restored.unknown_action_request_hash != restored_original.action_request_hash
                or restored.unknown_bank_request_hash != restored_original.bank_request_hash
                or restored.unknown_effect_hash
                != restored_original.original_bank_command.effect_hash
                or restored.recovered_receipt_id != restored_original.receipt.receipt_id
            ):
                raise ValueError("UNKNOWN proof does not recover a verified family original")
        return self


class CumulativeTwoEventReport(BoundaryModel):
    """Root-reviewed artifacts only; this schema cannot authenticate actuality."""

    protocol: Literal["zhiyu-ux1-cumulative-two-event-actual-report-v1"]
    validation_kind: Literal["ACTUAL_OWNED_POSTGRES_API_BANK_INDEPENDENT_RECOVERY_PROCESS"]
    status: Literal["PASSED"]
    native_protocol: Literal["zhiyu-standing-income-goal-native-v1"]
    grant: SignedIncomeGoalGrant
    first_event_recovery: ActualFamilyReport
    settled_events: Annotated[list[ActualActionOriginal], Field(min_length=2, max_length=2)]
    total_actual_receipt_cents: Literal[200]
    second_event_unknown_fabricated: Literal[False]
    per_income_user_confirmation: Literal[False]
    worker_permit_issued: Literal[False]

    @model_validator(mode="before")
    @classmethod
    def explicit_closed_report(cls, value: Any) -> Any:
        if isinstance(value, dict):
            if type(value.get("total_actual_receipt_cents")) is not int or any(
                value.get(name) is not False
                for name in (
                    "second_event_unknown_fabricated",
                    "per_income_user_confirmation",
                    "worker_permit_issued",
                )
            ):
                raise ValueError("Two real income events require explicit non-inherited originals")
            raw_grant = value.get("grant")
            if isinstance(raw_grant, dict):
                if set(raw_grant) != set(SignedIncomeGoalGrant.model_fields):
                    raise ValueError("Complete exact signed cumulative grant original required")
                value = dict(value)
                value["grant"] = SignedIncomeGoalGrant.model_validate_json(json.dumps(raw_grant))
        return value

    @property
    def family(self) -> Literal["SINGLE_GOAL"]:
        return "SINGLE_GOAL"

    @property
    def source_hashes_after(self) -> dict[str, Hash]:
        return self.first_event_recovery.source_hashes_after

    @model_validator(mode="after")
    def exact_two_original_events(self) -> Self:
        first = self.first_event_recovery
        scope = self.grant.scope
        if (
            first.family != "SINGLE_GOAL"
            or first.native_protocol != self.native_protocol
            or first.grant_id != self.grant.grant_id
            or first.grant_hash != configuration_hash(self.grant.model_dump(mode="json"))
            or first.source_scope != scope
            or first.reviewed_scope_hash != self.grant.reviewed_scope_hash
            or len(first.actions) != 1
            or len(first.unknown_recoveries) != 1
            or first.actions != [self.settled_events[0]]
            or sum(row.receipt.executed_cents for row in self.settled_events) != 200
            or scope.daily_cap_cents < 200
        ):
            raise ValueError(
                "Two-event variant requires one real same-key recovery and the same grant"
            )
        for key in ("action_id", "bank_operation_id", "bank_idempotency_key"):
            if len({getattr(row, key) for row in self.settled_events}) != 2:
                raise ValueError(
                    "Two income events cannot duplicate an original action or Bank key"
                )
        if (
            len({row.receipt.receipt_id for row in self.settled_events}) != 2
            or len({row.original_source.event_key for row in self.settled_events}) != 2
            or len({row.original_source.original_source_id for row in self.settled_events}) != 2
        ):
            raise ValueError("Two actual income source and receipt identities must differ")
        for row in self.settled_events:
            effect = row.original_bank_command.effect
            source = row.original_source
            if (
                (source.user_id, source.epoch_id, source.environment_id)
                != (first.user_id, first.epoch_id, first.database)
                or source.source_kind != "SETTLED_INCOME"
                or row.source_scope_hash != self.grant.reviewed_scope_hash
                or any(phase.native_protocol != self.native_protocol for phase in row.phases)
                or effect.user_id != first.user_id
                or effect.action_type != "ALLOCATE_GOAL"
                or effect.amount_cents != 100
                or row.receipt.executed_cents != 100
                or effect.business_key != self.native_protocol + ":" + source.event_key
                or not effect.income_uses
                or sum(use.amount_cents for use in effect.income_uses) != 100
                or any(
                    use.origin_transaction_id
                    != uuid5(UUID(source.original_source_id), "transaction")
                    for use in effect.income_uses
                )
                or any(use.account_id not in scope.source_account_ids for use in effect.cash_uses)
                or effect.goal_id not in scope.goal_ids
                or not effect.policy_version_ids
                or not set(effect.policy_version_ids).issubset(scope.policy_version_ids)
                or effect.amount_cents > min(scope.per_action_cap_cents, scope.per_event_cap_cents)
                or not scope.issued_at <= effect.valid_from < effect.expires_at <= scope.expires_at
            ):
                raise ValueError(
                    "Each original event must be its own bounded 100-cent SYSTEM income slice"
                )
        return self


def _file_digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def _closed_path(root: Path, relative: str) -> Path:
    if (
        not isinstance(relative, str)
        or not relative
        or "\\" in relative
        or Path(relative).is_absolute()
        or any(part in {"", ".", ".."} for part in relative.split("/"))
    ):
        raise ValueError("Original path escaped canonical checkout")
    source = root / relative
    if source.resolve() != source.absolute() or not source.is_file() or source.is_symlink():
        raise ValueError("Original path escaped or redirected this checkout")
    if not source.resolve().is_relative_to(root.resolve()):
        raise ValueError("Original path escaped this checkout")
    return source


def _strict_json(raw: bytes) -> Any:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError("Duplicate proof JSON member")
            result[key] = value
        return result

    def forbidden(value: str) -> Any:
        raise ValueError("Nonfinite proof JSON is forbidden")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=forbidden)
    except (RecursionError, UnicodeError) as error:
        raise ValueError("Invalid bounded proof JSON") from error


def consumer_source_paths(root: Path) -> set[str]:
    return REQUIRED | {
        path.relative_to(root).as_posix()
        for directory in (root / "apps/api/app", root / "apps/api/alembic")
        for path in directory.rglob("*.py")
        if "tests" not in path.relative_to(directory).parts
    }


def require_verified_source(
    family: Family,
    *,
    root: Path = ROOT,
    reviewed_manifest_sha256: str | None = None,
    native_protocol: NativeProtocol = "zhiyu-standing-delegated-native-v2",
) -> dict[str, Any]:
    """Require root's explicit launch pin, not a report's self-claimed PASSED.

    The trusted operator independently verifies actual native DB/API/BANK
    originals before pinning the manifest. This format does not mint actuality.
    """
    if (
        not isinstance(reviewed_manifest_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", reviewed_manifest_sha256) is None
    ):
        raise ValueError("Explicit root-reviewed manifest SHA256 pin is missing or invalid")
    if native_protocol not in {NATIVE_PROTOCOL, "zhiyu-standing-income-goal-native-v1"}:
        raise ValueError("Unsupported explicit native protocol")
    cumulative = native_protocol == "zhiyu-standing-income-goal-native-v1"
    if cumulative and family != "SINGLE_GOAL":
        raise ValueError("Cumulative native variant is only SINGLE_GOAL")
    relative_permit = ".runtime/zhiyu-next-validation/" + (
        "standing-income-goal-acceptance.json" if cumulative else "standing-acceptance.json"
    )
    candidate = root / relative_permit
    if not candidate.is_file():
        raise ValueError("Actual delegated-family acceptance permit is missing")
    path = _closed_path(root, relative_permit)
    if path.stat().st_size > MAX_PROOF_BYTES:
        raise ValueError("Delegated source manifest exceeds bounded capacity")
    raw = path.read_bytes()
    if len(raw) > MAX_PROOF_BYTES or hashlib.sha256(raw).hexdigest() != reviewed_manifest_sha256:
        raise ValueError("Actual acceptance manifest differs from root's reviewed launch pin")
    data: Any = _strict_json(raw)
    if (
        not isinstance(data, dict)
        or set(data)
        != (
            {"protocol", "status", "scope", "family_proofs", "source_hashes"}
            | ({"native_variant"} if cumulative else set())
        )
        or (
            data["protocol"]
            != (
                "zhiyu-next-standing-variant-acceptance-v1"
                if cumulative
                else "zhiyu-next-standing-acceptance-v2"
            )
            or cumulative
            and data["native_variant"] != native_protocol
            or data["status"] != "PASSED"
            or data["scope"] != "SYSTEM_DELEGATED_NATIVE_ZERO_COST"
        )
    ):
        raise ValueError("Actual delegated-family acceptance permit is not accepted")
    proofs = data["family_proofs"]
    if not isinstance(proofs, dict) or not set(proofs).issubset(FAMILIES) or family not in proofs:
        raise ValueError("This action family has no actual delegated acceptance")
    if cumulative and set(proofs) != {"SINGLE_GOAL"}:
        raise ValueError("Explicit cumulative permit cannot authorize other native families")
    for actual_family, proof in proofs.items():
        if (
            not isinstance(proof, dict)
            or set(proof)
            != {
                "family",
                "status",
                "executor_role",
                "original_receipts_verified",
                "original_unknown_recovery_verified",
                "current_native_guard_verified",
                "complete_worlds_verified",
                "proof_path",
                "proof_sha256",
            }
            | ({"native_protocol"} if cumulative else set())
            or (
                cumulative
                and proof["native_protocol"] != native_protocol
                or proof["family"] != actual_family
                or proof["status"] != "PASSED"
                or proof["executor_role"] != "SYSTEM"
                or any(
                    proof[field] is not True
                    for field in (
                        "original_receipts_verified",
                        "original_unknown_recovery_verified",
                        "current_native_guard_verified",
                        "complete_worlds_verified",
                    )
                )
            )
        ):
            raise ValueError("Delegated proof must be actual and complete for its action family")
        relative = proof["proof_path"]
        if not isinstance(relative, str):
            raise ValueError("Original proof path required")
        if not relative.startswith(".runtime/zhiyu-next-validation/"):
            raise ValueError("Actual delegated proof original changed or escaped this checkout")
        original = _closed_path(root, relative)
        if original.stat().st_size > MAX_PROOF_BYTES:
            raise ValueError("Actual delegated proof exceeds bounded capacity")
        proof_raw = original.read_bytes()
        if (
            len(proof_raw) > MAX_PROOF_BYTES
            or not isinstance(proof["proof_sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", proof["proof_sha256"]) is None
            or hashlib.sha256(proof_raw).hexdigest() != proof["proof_sha256"]
        ):
            raise ValueError("Actual delegated proof original changed or escaped this checkout")
        report: ActualFamilyReport | CumulativeTwoEventReport
        if cumulative:
            report = CumulativeTwoEventReport.model_validate_json(
                json.dumps(_strict_json(proof_raw))
            )
        else:
            report = ActualFamilyReport.model_validate_json(json.dumps(_strict_json(proof_raw)))
        if (
            report.native_protocol != native_protocol
            or report.family != actual_family
            or report.source_hashes_after != data["source_hashes"]
        ):
            raise ValueError("Actual report differs from its exact family/source permit")
    hashes = data["source_hashes"]
    if not isinstance(hashes, dict) or not consumer_source_paths(root).issubset(hashes):
        raise ValueError("All current financial consumers must be covered by the exact proof")
    if cumulative and set(hashes) != consumer_source_paths(root):
        raise ValueError(
            "Cumulative acceptance requires the exact complete current consumer inventory"
        )
    for relative, digest in hashes.items():
        if not isinstance(relative, str) or not isinstance(digest, str):
            raise ValueError("Exact consumer source hash required")
        source = _closed_path(root, relative)
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None or _file_digest(source) != digest:
            raise ValueError("Delegated financial consumer changed after actual verification")
    return data


def drain_original_once(
    engine: Engine,
    user_id: UUID,
    grant_id: UUID,
    now: datetime,
    *,
    reviewed_manifest_sha256: str | None = None,
    native_protocol: NativeProtocol | None = None,
) -> dict[str, Any]:
    """Dispatch exact journal keys, including unresolved originals after a pause."""
    require_zhiyu_next_engine(engine, check_connection=True)
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            with (
                Session(bind=connection) as read,
                immutable_joint_validation_scope(),
                immutable_joint_archive_scope(),
                recorded_native_replay_scope(),
            ):
                _audit_healthy(read, user_id)
                grant = original_grant(read, user_id, grant_id)
    variant = native_variant(grant)
    if native_protocol is not None and native_protocol != variant:
        raise ValueError("Worker launch variant differs from the actual signed grant")
    require_verified_source(
        grant.scope.family,
        reviewed_manifest_sha256=reviewed_manifest_sha256,
        native_protocol=variant,
    )
    observed = observe_once(engine, user_id, grant_id, now)
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            with (
                Session(bind=connection) as read,
                immutable_joint_validation_scope(),
                immutable_joint_archive_scope(),
                recorded_native_replay_scope(),
            ):
                _audit_healthy(read, user_id)
                runs = original_runs(read, user_id)
    pending = [
        run
        for run in runs
        if run.grant_id == grant_id
        and run.state
        in {"OBSERVED", "WAITING_INFORMATION", "WAITING_CONFIRMATION", "ACCEPTED", "UNKNOWN"}
    ]
    # Concrete module only: no caller/model executor or fake USER confirmation callback.
    from app.services.zhiyu_standing_dispatcher import dispatch_original_event

    results = []
    for run in sorted(
        pending,
        key=lambda value: (value.state not in {"UNKNOWN", "ACCEPTED"}, value.source.event_key),
    ):
        require_verified_source(
            grant.scope.family,
            reviewed_manifest_sha256=reviewed_manifest_sha256,
            native_protocol=variant,
        )
        current = dispatch_original_event(engine, user_id, grant_id, run.source.event_key, now)
        if current.source != run.source or current.grant_id != grant_id:
            raise ValueError("SYSTEM dispatcher replaced the original source or grant")
        results.append(current.model_dump(mode="json"))
        if current.state == "UNKNOWN":
            # Preserve original acceptance budget and stop advancing later events.
            break
    return {
        "protocol": "zhiyu-next-standing-worker-cycle-v1",
        "executor_role": "SYSTEM",
        "observation": observed,
        "runs": results,
        "old_one_shot_user_consent_inherited": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round", required=True)
    parser.add_argument("--grant-id", type=UUID, required=True)
    parser.add_argument("--enable-standing", action="store_true")
    parser.add_argument("--reviewed-acceptance-sha256", required=True)
    parser.add_argument(
        "--native-protocol",
        choices=(NATIVE_PROTOCOL, "zhiyu-standing-income-goal-native-v1"),
        default=NATIVE_PROTOCOL,
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval-seconds", type=int, default=30)
    options = parser.parse_args()
    if re.fullmatch(r"[0-9a-f]{64}", options.reviewed_acceptance_sha256) is None:
        raise ValueError("Explicit root-reviewed acceptance SHA256 required")
    if not options.enable_standing or not 5 <= options.interval_seconds <= 300:
        raise ValueError(
            "Explicit actual-verified standing enablement and bounded interval required"
        )
    if Path(sys.prefix).resolve() != ROOT / ".venv":
        raise ValueError("Standing worker requires the independent Next virtual environment")
    sys.path.insert(0, str(ROOT))
    from scripts.zhiyu_next import current_round, read_round

    directory = current_round(options.round)
    metadata, environment = read_round(directory)
    os.environ.update(environment)
    os.environ["ZHIYU_NEXT_WORKER_ENABLED"] = "true"
    os.environ.pop("ZHIYU_NEXT_ADMIN_DATABASE_URL", None)
    if options.native_protocol == "zhiyu-standing-income-goal-native-v1":
        require_verified_source(
            "SINGLE_GOAL",
            native_protocol=options.native_protocol,
            reviewed_manifest_sha256=options.reviewed_acceptance_sha256,
        )
    engine = create_database_engine(environment["DATABASE_URL"])
    require_zhiyu_next_engine(engine, check_connection=True)
    registration = {
        "protocol": "zhiyu-next-standing-worker-v1",
        "pid": os.getpid(),
        "round": directory.name,
        "environment_id": metadata["environment_id"],
        "epoch_id": metadata["epoch_id"],
        "grant_id": str(options.grant_id),
        "cwd": str(ROOT),
        "started_at": datetime.now(UTC).isoformat(),
        "interval_seconds": options.interval_seconds,
        "status": "RUNNING",
        "executor_role": "SYSTEM",
        "scope": "SYSTEM_DELEGATED_NATIVE_ZERO_COST",
        "reviewed_acceptance_sha256": options.reviewed_acceptance_sha256,
        "native_protocol": options.native_protocol,
    }
    path = directory / f"standing-worker-{os.getpid()}.json"
    path.write_text(json.dumps(registration, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        while True:
            try:
                result = drain_original_once(
                    engine,
                    DEMO_USER_ID,
                    options.grant_id,
                    datetime.now(UTC),
                    reviewed_manifest_sha256=options.reviewed_acceptance_sha256,
                    native_protocol=options.native_protocol,
                )
                print(json.dumps(result, ensure_ascii=False), flush=True)
            except (PolicyLifecycleError, ValueError) as error:
                code = (
                    error.code
                    if isinstance(error, PolicyLifecycleError)
                    else "SOURCE_OR_SCOPE_INVALID"
                )
                registration.update(status="STOPPED_FOR_REVIEW", error_code=code)
                print(json.dumps({"status": "STOPPED_FOR_REVIEW", "error_code": code}), flush=True)
                return 1
            if options.once:
                return 0
            time.sleep(options.interval_seconds)
    finally:
        if registration["status"] == "RUNNING":
            registration["status"] = "STOPPED"
        registration["stopped_at"] = datetime.now(UTC).isoformat()
        path.write_text(json.dumps(registration, ensure_ascii=False, indent=2), encoding="utf-8")
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
