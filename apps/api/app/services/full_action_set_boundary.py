"""Actual current finite producer inventory and metadata-only boundary observations."""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid5

from app.db.base import Base
from app.db.catalog_models import ProductCatalogVersion
from app.db.full_models import FullPolicy
from app.db.models import DecisionRun, User
from app.domain.decision_trace import build_trace, verify_trace
from app.domain.decision_trace_types import DecisionTrace
from app.domain.execution_types import ExecutionContext
from app.domain.full_action_set_boundary import (
    ALGORITHM,
    REQUIRED_INVENTORY,
    ActionSetInput,
    ActionSetSnapshot,
    CandidateInput,
    GlobalBoundaryObservation,
    GlobalBoundaryObserveRequest,
    compare_action_sets,
    derive_action_set,
    unsupported_producers,
)
from app.domain.full_execution_protection import validate_full_execution_protection
from app.domain.full_protection_projection import (
    FullProtectionProjectionInput,
    project_full_protection,
)
from app.domain.full_registered_account_debits import derive_full_account_debit_bounds
from app.domain.policy_configuration import configuration_hash
from app.services.action_contracts import (
    ActionIntent,
    GoalIntent,
    PaymentIntent,
    PurchaseIntent,
    RedeemIntent,
)
from app.services.audit_chain import current_audit_epoch
from app.services.autonomy import _basis, _capture_assessment, _facts
from app.services.autonomy_envelope import _snapshot
from app.services.dashboard_helpers import current_epoch_audit
from app.services.decision_recording import (
    CAPTURE_KEY,
    DecisionCapture,
    capture_evidence,
    start_capture,
)
from app.services.decision_trace import get_decision_trace, record_trace
from app.services.full_intervention import Source
from app.services.full_protection_projection import compute_full_annual_protection
from app.services.policy_lifecycle import PolicyLifecycleError, _now, _user, effective_status
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

NAMESPACE = UUID("22a1a726-6fd0-46b0-bcb0-ce282bd7e106")
MAX_ROWS = 200
MAX_CANDIDATES = 16
MAX_CAPTURE_BYTES = 524288


@dataclass(frozen=True)
class ActionSetCapture:
    inputs: ActionSetInput
    snapshot: ActionSetSnapshot
    originals: DecisionCapture


def _error(code: str, message: str) -> PolicyLifecycleError:
    return PolicyLifecycleError(code, message, 409)


def _json(value: Any) -> Any:
    # ORM integer cents and NULL remain integer/NULL. UUID/date serialization
    # never rewrites a stored canonical text or its original hash.
    return json.loads(json.dumps(value, default=str, allow_nan=False))


def _inventory(
    session: Session, user_id: UUID
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    names = set(REQUIRED_INVENTORY) | {
        name
        for name in Base.metadata.tables
        if name.startswith(("full_asset_execution_", "command_"))
    }
    assert ProductCatalogVersion.__tablename__ in names and FullPolicy.__tablename__ in names
    rows: dict[str, list[dict[str, Any]]] = {}
    reasons: list[str] = []
    for name in sorted(names):
        table = Base.metadata.tables.get(name)
        if (
            table is None
            or session.scalar(text("SELECT to_regclass(:name)"), {"name": "public." + name}) is None
        ):
            reasons.append("ORIGINAL_TABLE_MISSING:" + name)
            continue
        query = select(table).order_by(table.c.id).limit(MAX_ROWS + 1)
        if "user_id" in table.c:
            query = query.where(table.c.user_id == user_id)
        elif name == "users":
            query = query.where(table.c.id == user_id)
        elif name not in {"asset_products", "product_catalog_versions"}:
            reasons.append("UNKNOWN_UNOWNED_INVENTORY:" + name)
            continue
        actual = list(session.execute(query).mappings())
        # Keep the actually captured denominator including the excess sentinel;
        # do not relabel the first MAX_ROWS as the complete original table.
        rows[name] = [_json(dict(row)) for row in actual]
        if len(actual) > MAX_ROWS:
            reasons.append("ORIGINAL_TABLE_CAPACITY_EXCEEDED:" + name)
    return rows, reasons


def capture_current_action_set(session: Session, user_id: UUID, now: datetime) -> ActionSetCapture:
    _snapshot(session)
    now = _now(now)
    user = session.get(User, user_id)
    epoch = current_audit_epoch(session, user_id)
    if user is None or not user.is_simulated or epoch is None or epoch.status != "OPEN":
        raise _error("CURRENT_ACTION_SET_OWNER_OR_EPOCH_MISSING", "需要实际模拟用户和唯一当前轮次")
    previous_capture = session.info.get(CAPTURE_KEY)
    capture = start_capture(session)
    try:
        with session.no_autoflush:
            inventory, inventory_reasons = _inventory(session, user_id)
            audit = current_epoch_audit(session, user_id, [])
            basis = _basis(session, user_id, now)
            full = compute_full_annual_protection(session, user_id, now)
            capture_evidence(session, user_id, full.source_evidence_ids)
            source_reasons = [row.code for row in basis.issues] + [
                row.code for row in full.source_issues
            ]
            if (full.user_id, full.as_of) != (
                user_id,
                now,
            ) or not full.projection.full_obligations_complete_within_registered_current_scope:
                source_reasons.append("FULL_CURRENT_SOURCE_INVENTORY_NOT_PROVEN")
            versions: dict[str, dict[str, Any]] = {}
            for raw in inventory.get("policy_versions", []):
                identity = raw["policy_id"]
                if (
                    identity not in versions
                    or raw["version_number"] > versions[identity]["version_number"]
                ):
                    versions[identity] = raw
            intents: list[tuple[str, ActionIntent | None, bool]] = []
            for policy in inventory.get("policies", []):
                version = versions.get(policy["id"])
                if version is None:
                    intents.append(("policy:" + policy["id"], None, False))
                    continue
                from app.db.models import Policy, PolicyVersion

                actual_policy = session.get(Policy, UUID(policy["id"]))
                actual_version = session.get(PolicyVersion, UUID(version["id"]))
                assert actual_policy is not None and actual_version is not None
                inactive = effective_status(actual_policy, actual_version, now) != "ACTIVE"
                kind = version["configuration"].get("type")
                if kind == "recurring_expense":
                    intents.append(
                        (
                            "payment:" + policy["id"],
                            PaymentIntent(kind="pay_recurring", policy_id=actual_policy.id),
                            inactive,
                        )
                    )
                elif kind == "asset_allocation":
                    intents.append(
                        (
                            "purchase:" + policy["id"],
                            PurchaseIntent(kind="purchase_asset", policy_id=actual_policy.id),
                            inactive,
                        )
                    )
                elif kind == "goal_saving":
                    goals = [
                        row
                        for row in inventory.get("goals", [])
                        if row["policy_id"] == policy["id"]
                    ]
                    if not goals:
                        intents.append(("goal-policy:" + policy["id"], None, False))
                    for goal in goals:
                        intents.append(
                            (
                                "goal:" + goal["id"],
                                GoalIntent(kind="allocate_goal", goal_id=UUID(goal["id"])),
                                inactive,
                            )
                        )
                elif kind not in {"living_reserve", "emergency_buffer"}:
                    intents.append(("unsupported-policy:" + policy["id"], None, False))
            for position in inventory.get("asset_positions", []):
                intents.append(
                    (
                        "redemption:" + position["id"],
                        RedeemIntent(kind="redeem_asset", position_id=UUID(position["id"])),
                        position["status"] == "REDEEMED" or position["policy_version_id"] is None,
                    )
                )
            unsupported = unsupported_producers(inventory, epoch.id)
            expected = sorted(key for key, _, _ in intents)
            candidates = []
            if len(intents) > MAX_CANDIDATES:
                inventory_reasons.append("GLOBAL_PRODUCER_CAPACITY_EXCEEDED")
            else:
                for key, intent, excluded in sorted(intents, key=lambda value: value[0]):
                    if excluded:
                        candidates.append(
                            CandidateInput(candidate_key=key, excluded_by_current_policy=True)
                        )
                        continue
                    if intent is None:
                        candidates.append(
                            CandidateInput(
                                candidate_key=key,
                                missing_reasons=["ORIGINAL_PRODUCER_BINDING_MISSING"],
                            )
                        )
                        continue
                    try:
                        facts = _facts(session, user_id, intent, now, basis)
                        _capture_assessment(session, facts)
                        raw_context = (
                            capture.inputs.get("execution_context")
                            if facts.effect is not None
                            else None
                        )
                        context = (
                            ExecutionContext.model_validate_json(json.dumps(raw_context))
                            if raw_context is not None
                            else None
                        )
                        veto, bounds = None, None
                        if (
                            facts.effect is not None
                            and facts.validation is not None
                            and context is not None
                            and facts.validation.status in {"READY", "CONFIRMATION_REQUIRED"}
                        ):
                            bounds = None
                            if facts.validation.projected_snapshot is not None:
                                projected_input = FullProtectionProjectionInput(
                                    snapshot=facts.validation.projected_snapshot,
                                    boundary_versions=context.versions,
                                    positions=facts.validation.projected_positions,
                                    boundary_products=context.boundary_products,
                                    policies=full.full_policy_sources,
                                    reserved_cash_by_account=context.reserved_cash_by_account,
                                )
                                projected = project_full_protection(projected_input)
                                if projected.source_account_checks:
                                    bounds = derive_full_account_debit_bounds(
                                        facts.effect,
                                        context,
                                        facts.validation,
                                        projected_input,
                                        projected,
                                    )
                            veto = validate_full_execution_protection(
                                facts.effect,
                                context,
                                facts.validation,
                                full.full_policy_sources,
                                source_issues=tuple(source_reasons),
                                account_debit_bounds=bounds,
                            )
                        candidates.append(
                            CandidateInput(
                                candidate_key=key,
                                facts=facts,
                                execution_context=context,
                                full_protection=veto,
                                full_sources=full.full_policy_sources,
                                full_source_issues=source_reasons,
                                full_account_debit_bounds=bounds,
                            )
                        )
                    except (PolicyLifecycleError, ValueError, TypeError) as error:
                        candidates.append(
                            CandidateInput(
                                candidate_key=key,
                                missing_reasons=[
                                    getattr(error, "code", "ACTUAL_PRODUCER_UNSUPPORTED")
                                ],
                            )
                        )
            value = ActionSetInput(
                user_id=user_id,
                epoch_id=epoch.id,
                as_of=now,
                original_inventory=inventory,
                inventory_reasons=inventory_reasons,
                expected_candidate_keys=expected,
                candidates=candidates,
                financial_input_hash=basis.digest,
                financial_basis=capture.inputs["autonomy_basis"],
                audit_verified=audit.complete and audit.status == "VALID",
                source_reasons=source_reasons,
                unsupported_producers=unsupported,
            )
            if len(value.model_dump_json().encode("utf-8")) > MAX_CAPTURE_BYTES:
                value = value.model_copy(
                    update={
                        "inventory_reasons": [
                            *value.inventory_reasons,
                            "FULL_CAPTURE_CAPACITY_EXCEEDED",
                        ]
                    }
                )
            return ActionSetCapture(value, derive_action_set(value), capture)
    finally:
        if previous_capture is None:
            session.info.pop(CAPTURE_KEY, None)
        else:
            session.info[CAPTURE_KEY] = previous_capture


def read_current_action_set(session: Session, user_id: UUID, now: datetime) -> ActionSetSnapshot:
    return capture_current_action_set(session, user_id, now).snapshot


def verify_frozen_action_set_trace(trace: DecisionTrace) -> GlobalBoundaryObservation:
    verify_trace(trace)
    if (
        trace.algorithm_versions.get("global_action_set") != ALGORITHM
        or trace.phase != "EVALUATION"
    ):
        raise ValueError("Not the original global action-set algorithm")
    original = ActionSetInput.model_validate_json(json.dumps(trace.inputs["action_set_input"]))
    snapshot = derive_action_set(original)
    _verify_original_source_copies(trace, original, snapshot.global_action_set_complete)
    value = GlobalBoundaryObservation.model_validate_json(
        json.dumps(trace.outcome["global_boundary_observation"])
    )
    previous = (
        ActionSetSnapshot.model_validate_json(json.dumps(trace.inputs["previous_snapshot"]))
        if trace.inputs["previous_snapshot"] is not None
        else None
    )
    kind, semantic = compare_action_sets(previous, snapshot)
    if (
        value.snapshot != snapshot
        or value.kind != kind
        or value.semantic_key != semantic
        or value.requires_user_attention != (kind == "BoundaryCrossed")
        or (trace.user_id, trace.run_id, trace.as_of)
        != (value.user_id, value.observation_run_id, original.as_of)
        or value.original_request.model_dump(mode="json") != trace.inputs["original_request"]
        or configuration_hash(value.original_request.model_dump(mode="json")) != value.request_hash
        or value.epoch_id != original.epoch_id
        or trace.action_id is not None
        or value.idempotent_replay
        or trace.run_id
        != uuid5(
            NAMESPACE,
            f"{trace.user_id}:{original.epoch_id}:{value.original_request.idempotency_key}",
        )
        or (previous is None) != (trace.parent_run_id is None)
        or original.user_id != trace.user_id
        or value.original_request.expected_epoch_id != original.epoch_id
        or value.original_request.previous_observation_run_id != trace.parent_run_id
        or value.previous_observation_run_id != trace.parent_run_id
        or value.previous_snapshot_hash != (previous.snapshot_hash if previous else None)
        or value.previous_action_set_signature
        != (previous.action_set_signature if previous else None)
        or value.global_action_set_complete
        != (
            value.snapshot.global_action_set_complete
            and (previous is None or previous.global_action_set_complete)
        )
    ):
        raise ValueError("Original global action-set calculation or bindings differ")
    return value


def _verify_original_source_copies(
    trace: DecisionTrace, original: ActionSetInput, complete: bool
) -> None:
    raw = {row["id"]: row for row in original.original_inventory.get("evidence_items", [])}
    copied = {str(row.id): row for row in trace.sources}
    if len(copied) != len(trace.sources):
        raise ValueError("Duplicate original source copies")
    for identity, source in copied.items():
        actual = raw.get(identity)
        if (
            actual is None
            or source.user_id != original.user_id
            or any(
                actual.get(name) != expected
                for name, expected in {
                    "user_id": str(source.user_id),
                    "content": source.content,
                    "content_hash": source.content_hash,
                    "evidence_level": source.evidence_level,
                    "source_type": source.source_type,
                    "source_ref": source.source_ref,
                    "status": source.status_at_decision,
                }.items()
            )
        ):
            raise ValueError("Captured source differs from original full inventory")
    required = {
        str(identity)
        for candidate in original.candidates
        if candidate.facts is not None
        for identity in [
            *candidate.facts.source_evidence_ids,
            *candidate.facts.authority.evidence_ids,
            *candidate.facts.payee.evidence_ids,
        ]
    }
    required.update(row["id"] for row in original.financial_basis.get("evidence", []))
    if complete and not required.issubset(copied):
        raise ValueError("An original financial or permission source copy is missing")
    for identity in required & copied.keys() if complete else ():
        source = copied[identity]
        if (
            source.content_integrity != "VERIFIED"
            or source.captured_content_hash != configuration_hash(source.content)
        ):
            raise ValueError("Original required evidence content is not verified")
    raw_versions = {
        row["id"]: row for row in original.original_inventory.get("policy_versions", [])
    }
    policies = {row.id: row for row in trace.policies}
    for candidate in original.candidates:
        if candidate.execution_context is None:
            continue
        for version in candidate.execution_context.versions:
            policy_source, actual = (
                policies.get(version.version_id),
                raw_versions.get(str(version.version_id)),
            )
            if complete and (
                policy_source is None
                or actual is None
                or policy_source.user_id != original.user_id
                or policy_source.configuration != actual["configuration"]
                or policy_source.configuration_hash != actual["content_hash"]
                or policy_source.configuration_integrity != "VERIFIED"
            ):
                raise ValueError("Original policy copy or configuration is missing")


def read_global_boundary_observation(
    session: Session, user_id: UUID, run_id: UUID, now: datetime
) -> GlobalBoundaryObservation:
    result = get_decision_trace(session, user_id, run_id, now)
    if (
        result.trace is None
        or result.completeness != "COMPLETE"
        or result.audit_chain_status != "VALID"
    ):
        raise _error("GLOBAL_BOUNDARY_ORIGINAL_UNVERIFIED", "原全局观察轨迹与审计未完整验真")
    try:
        value = verify_frozen_action_set_trace(result.trace)
        child, child_trace = value, result.trace
        visited = {run_id}
        while child.previous_observation_run_id is not None:
            identity = child.previous_observation_run_id
            if identity in visited or len(visited) >= 64:
                raise ValueError("Original observation ancestry is cyclic or exceeds capacity")
            visited.add(identity)
            parent = get_decision_trace(session, user_id, identity, now)
            if (
                parent.trace is None
                or parent.completeness != "COMPLETE"
                or parent.audit_chain_status != "VALID"
            ):
                raise ValueError("Actual original parent observation is not verified")
            before = verify_frozen_action_set_trace(parent.trace)
            if child_trace.inputs["previous_snapshot"] != before.snapshot.model_dump(mode="json"):
                raise ValueError(
                    "Captured previous snapshot differs from its actual original trace"
                )
            child, child_trace = before, parent.trace
        return value
    except (ValueError, TypeError, KeyError) as error:
        raise _error("GLOBAL_BOUNDARY_ORIGINAL_UNVERIFIED", str(error)) from error


def observe_global_boundary(
    engine: Engine, user_id: UUID, body: GlobalBoundaryObserveRequest, now: datetime
) -> GlobalBoundaryObservation:
    now = _now(now)
    identity = uuid5(NAMESPACE, f"{user_id}:{body.expected_epoch_id}:{body.idempotency_key}")
    with Session(engine) as session, session.begin():
        _user(session, user_id)
        epoch = current_audit_epoch(session, user_id)
        if epoch is None or epoch.status != "OPEN" or epoch.id != body.expected_epoch_id:
            raise _error("STALE_GLOBAL_BOUNDARY_EPOCH", "原观察轮次已变化")
        existing = session.get(DecisionRun, identity)
        if existing is not None:
            original = read_global_boundary_observation(session, user_id, identity, now)
            if original.original_request != body:
                raise _error("IDEMPOTENCY_CONFLICT", "原全局观察键不能改变原完整请求")
            return original.model_copy(update={"idempotent_replay": True})
        before = (
            read_global_boundary_observation(
                session, user_id, body.previous_observation_run_id, now
            )
            if body.previous_observation_run_id
            else None
        )
        with (
            engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection,
            connection.begin(),
        ):
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            with Session(bind=connection) as read:
                current = capture_current_action_set(read, user_id, now)
        if current.snapshot.epoch_id != epoch.id:
            raise _error("STALE_GLOBAL_BOUNDARY_EPOCH", "实际独立快照轮次不同")
        if len(current.inputs.model_dump_json().encode("utf-8")) > MAX_CAPTURE_BYTES:
            raise _error(
                "GLOBAL_BOUNDARY_CAPTURE_CAPACITY_EXCEEDED",
                "原完整输入超观察上限，不能删减分母后记录",
            )
        previous = before.snapshot if before else None
        kind, semantic = compare_action_sets(previous, current.snapshot)
        result = GlobalBoundaryObservation(
            user_id=user_id,
            epoch_id=epoch.id,
            observation_run_id=identity,
            previous_observation_run_id=body.previous_observation_run_id,
            original_request=body,
            request_hash=configuration_hash(body.model_dump(mode="json")),
            snapshot=current.snapshot,
            kind=kind,
            semantic_key=semantic,
            requires_user_attention=kind == "BoundaryCrossed",
            previous_snapshot_hash=previous.snapshot_hash if previous else None,
            previous_action_set_signature=previous.action_set_signature if previous else None,
            global_action_set_complete=current.snapshot.global_action_set_complete
            and (previous is None or previous.global_action_set_complete),
        )
        trace = build_trace(
            run_id=identity,
            user_id=user_id,
            phase="EVALUATION",
            as_of=now,
            parent_run_id=body.previous_observation_run_id,
            action_id=None,
            algorithm_versions={"global_action_set": ALGORITHM},
            inputs={
                "original_request": body.model_dump(mode="json"),
                "action_set_input": current.inputs.model_dump(mode="json"),
                "previous_snapshot": previous.model_dump(mode="json") if previous else None,
            },
            sources=list(current.originals.sources.values()),
            policies=list(current.originals.policies.values()),
            constraints=[],
            candidates=[],
            outcome={
                "global_boundary_observation": result.model_dump(mode="json"),
                "decision_status": "COMPUTED" if kind else "UNKNOWN",
            },
        )
        verify_frozen_action_set_trace(trace)
        record_trace(session, trace)
        return result


def global_boundary_intervention_source(
    session: Session, user_id: UUID, run_id: UUID, now: datetime
) -> Source:
    """Root's new GLOBAL source branch may consume this; no legacy relabeling."""
    result = get_decision_trace(session, user_id, run_id, now)
    if (
        result.trace is None
        or result.completeness != "COMPLETE"
        or result.audit_chain_status != "VALID"
    ):
        raise _error("GLOBAL_BOUNDARY_ORIGINAL_UNVERIFIED", "全局通知须原完整观察与有效审计")
    value = read_global_boundary_observation(session, user_id, run_id, now)
    if not value.global_action_set_complete or value.semantic_key is None or value.kind is None:
        raise _error("GLOBAL_BOUNDARY_SOURCE_UNKNOWN", "不完整或初始观察不能冒充全局通知")
    return Source(
        result.trace,
        value.semantic_key,
        None,
        None,
        None,
        value.model_dump(mode="json"),
        value.requires_user_attention,
    )
