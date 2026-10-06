"""Actual physical-source v2 observations, preserving both original v1 protocols."""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid5

from app.db.base import Base
from app.db.catalog_models import ProductCatalogVersion
from app.db.full_models import FullPolicy
from app.db.models import Account, DecisionRun, Goal, PolicyVersion, User
from app.domain.decision_trace import build_trace, verify_trace
from app.domain.decision_trace_types import DecisionTrace
from app.domain.execution_types import ExecutionContext
from app.domain.full_action_set_asset_producers import (
    MAX_PRODUCERS,
    AssetProducerInput,
    expected_asset_producers,
)
from app.domain.full_action_set_boundary import (
    ActionSetInput,
    CandidateInput,
    GlobalBoundaryObserveRequest,
    unsupported_producers,
)
from app.domain.full_action_set_boundary_actual import (
    ALGORITHM,
    GLOBAL_TABLES,
    MAX_CANDIDATES,
    MAX_CAPTURE_BYTES,
    MAX_ROWS,
    NAMESPACE,
    REQUIRED_TABLES,
    ActualActionSetInput,
    ActualActionSetSnapshot,
    ActualGlobalBoundaryObservation,
    ActualTableCoverage,
    actual_inactive_keys,
    compare_actual_action_sets,
    derive_actual_action_set,
)
from app.domain.full_action_set_boundary_full import (
    DynamicGoalProducerInput,
    dynamic_model_inventory,
)
from app.domain.full_asset_execution import (
    FullAssetCatalogueReference,
    FullAssetExecutionBasis,
    FullAssetPrepareRequest,
)
from app.domain.full_dynamic_goal_execution import FullDynamicGoalPrepareRequest
from app.domain.full_execution_protection import validate_full_execution_protection
from app.domain.full_policy_configuration import AssetAuthorizationPolicy
from app.domain.full_protection_projection import (
    FullProtectionProjectionInput,
    project_full_protection,
)
from app.domain.full_registered_account_debits import derive_full_account_debit_bounds
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.services.action_contracts import (
    ActionIntent,
    GoalIntent,
    PaymentIntent,
    PurchaseIntent,
    RedeemIntent,
)
from app.services.audit_chain import current_audit_epoch
from app.services.autonomy import _authority, _basis, _capture_assessment, _facts
from app.services.autonomy_envelope import _snapshot
from app.services.dashboard_helpers import current_epoch_audit
from app.services.decision_recording import (
    CAPTURE_KEY,
    DecisionCapture,
    capture_evidence,
    capture_versions,
    start_capture,
)
from app.services.decision_trace import get_decision_trace, record_trace
from app.services.full_action_set_asset_producers import _planning_input
from app.services.full_action_set_boundary import _error, _json, _verify_original_source_copies
from app.services.full_asset_execution import preview_full_asset_execution
from app.services.full_dynamic_goal_execution import read_full_dynamic_goal_inputs
from app.services.full_goals import read_full_goal_model
from app.services.full_intervention import Source
from app.services.full_policy_lifecycle import read_full_policy
from app.services.full_protection_projection import compute_full_annual_protection
from app.services.policy_lifecycle import PolicyLifecycleError, _now, _user, effective_status
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class ActualBaseCapture:
    inputs: ActionSetInput
    coverage: list[ActualTableCoverage]
    originals: DecisionCapture


@dataclass(frozen=True)
class ActualActionSetCapture:
    inputs: ActualActionSetInput
    snapshot: ActualActionSetSnapshot
    originals: DecisionCapture


def _inventory(
    session: Session, user_id: UUID
) -> tuple[dict[str, list[dict[str, Any]]], list[str], list[ActualTableCoverage]]:
    # Complete actual SQL counts, bounded whole original rows and no virtual bank table.
    names = set(REQUIRED_TABLES) | {
        name for name in Base.metadata.tables if name.startswith("command_")
    }
    assert ProductCatalogVersion.__tablename__ in names and FullPolicy.__tablename__ in names
    rows: dict[str, list[dict[str, Any]]] = {}
    reasons: list[str] = []
    coverage: list[ActualTableCoverage] = []
    for name in sorted(names):
        table = Base.metadata.tables.get(name)
        if (
            table is None
            or session.scalar(text("SELECT to_regclass(:name)"), {"name": "public." + name}) is None
        ):
            rows[name] = []
            reasons.append("ACTUAL_ORIGINAL_TABLE_MISSING:" + name)
            coverage.append(
                ActualTableCoverage(
                    table=name,
                    actual_count=None,
                    captured_count=0,
                    complete=False,
                    rows_hash=configuration_hash({"rows": []}),
                )
            )
            continue
        query = select(table).order_by(table.c.id).limit(MAX_ROWS + 1)
        count = select(func.count()).select_from(table)
        if "user_id" in table.c:
            query = query.where(table.c.user_id == user_id)
            count = count.where(table.c.user_id == user_id)
        elif name == "users":
            query = query.where(table.c.id == user_id)
            count = count.where(table.c.id == user_id)
        elif name not in GLOBAL_TABLES:
            rows[name] = []
            reasons.append("ACTUAL_UNKNOWN_UNOWNED_TABLE:" + name)
            coverage.append(
                ActualTableCoverage(
                    table=name,
                    actual_count=None,
                    captured_count=0,
                    complete=False,
                    rows_hash=configuration_hash({"rows": []}),
                )
            )
            continue
        actual_count = int(session.scalar(count) or 0)
        actual = [_json(dict(row)) for row in session.execute(query).mappings()]
        rows[name] = actual
        complete = actual_count == len(actual) and actual_count <= MAX_ROWS
        coverage.append(
            ActualTableCoverage(
                table=name,
                actual_count=actual_count,
                captured_count=len(actual),
                complete=complete,
                rows_hash=configuration_hash({"rows": actual}),
            )
        )
        if not complete:
            reasons.append("ACTUAL_TABLE_CAPACITY_OR_COUNT_DIFFERS:" + name)
    return rows, reasons, coverage


def _capture_base(session: Session, user_id: UUID, now: datetime) -> ActualBaseCapture:
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
            inventory, inventory_reasons, coverage = _inventory(session, user_id)
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
                kind = validate_configuration(version["configuration"])["type"]
                if kind == "recurring_obligation":
                    intents.append(
                        (
                            "payment:" + policy["id"],
                            PaymentIntent(kind="pay_recurring", policy_id=actual_policy.id),
                            inactive,
                        )
                    )
                elif kind == "asset_authorization":
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
            candidates: list[CandidateInput] = []
            if len(intents) > MAX_CANDIDATES:
                inventory_reasons.append("ACTUAL_PRODUCER_CAPACITY_EXCEEDED")
                candidates.extend(
                    CandidateInput(
                        candidate_key=key, missing_reasons=["ACTUAL_PRODUCER_CAPACITY_EXCEEDED"]
                    )
                    for key, _, _ in intents
                )
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
            return ActualBaseCapture(value, coverage, capture)
    finally:
        if previous_capture is None:
            session.info.pop(CAPTURE_KEY, None)
        else:
            session.info[CAPTURE_KEY] = previous_capture


def _capture_dynamic(
    session: Session, user_id: UUID, now: datetime, base: ActualBaseCapture
) -> list[DynamicGoalProducerInput]:
    producers: list[DynamicGoalProducerInput] = []
    with session.no_autoflush:
        inactive = actual_inactive_keys(base.inputs.original_inventory, now)
        models = dynamic_model_inventory(base.inputs)
        for key, originals in models.items():
            if len(models) > MAX_CANDIDATES:
                producers.append(
                    DynamicGoalProducerInput(
                        candidate_key=key,
                        model_evidence_ids=originals,
                        missing_reasons=["DYNAMIC_PRODUCER_CAPACITY_EXCEEDED"],
                    )
                )
                continue
            if key in inactive:
                producers.append(
                    DynamicGoalProducerInput(
                        candidate_key=key,
                        model_evidence_ids=originals,
                        excluded_by_current_policy=True,
                    )
                )
                continue
            try:
                identity = UUID(key[5:])
                model = read_full_goal_model(session, user_id, identity, now)
                if (
                    model.status != "VERIFIED"
                    or model.evidence_id is None
                    or model.evidence_hash is None
                ):
                    raise ValueError("CURRENT_FULL_GOAL_MODEL_NOT_VERIFIED")
                request = FullDynamicGoalPrepareRequest(
                    goal_id=identity,
                    expected_policy_version_id=model.base_policy_version_id,
                    expected_model_evidence_id=model.evidence_id,
                    expected_model_evidence_hash=model.evidence_hash,
                    expected_epoch_id=base.inputs.epoch_id,
                    idempotency_key="readonly-global-dynamic:" + str(identity),
                )
                data = read_full_dynamic_goal_inputs(session, user_id, request, now)
                version = next(
                    row
                    for row in data.context.versions
                    if row.version_id == model.base_policy_version_id
                )
                authority = _authority(
                    session, user_id, [version.policy_id], now, expected=[version.version_id]
                )
                capture_evidence(
                    session,
                    user_id,
                    sorted(
                        {row.evidence_id for row in data.source_refs} | set(authority.evidence_ids)
                    ),
                )
                capture_versions(
                    session, user_id, [row.version_id for row in data.context.versions]
                )
                producers.append(
                    DynamicGoalProducerInput(
                        candidate_key=key,
                        model_evidence_ids=originals,
                        data=data,
                        authority=authority,
                    )
                )
            except (PolicyLifecycleError, ValueError, TypeError, KeyError) as error:
                producers.append(
                    DynamicGoalProducerInput(
                        candidate_key=key,
                        model_evidence_ids=originals,
                        missing_reasons=[getattr(error, "code", str(error))],
                    )
                )
    return producers


def _capture_assets(
    session: Session, user_id: UUID, now: datetime, base: ActualBaseCapture
) -> tuple[list[AssetProducerInput], list[str]]:
    _snapshot(session)
    now = _now(now)
    if (base.inputs.user_id, base.inputs.as_of) != (user_id, now):
        raise PolicyLifecycleError(
            "WHOLE_ASSET_BASE_OWNER_CLOCK_DIFFERS", "原请求用户/时点不同", 409
        )
    previous = session.info.get(CAPTURE_KEY)
    capture = start_capture(session)
    producers: list[AssetProducerInput] = []
    missing_reasons: list[str] = []
    try:
        with session.no_autoflush:
            try:
                expected = expected_asset_producers(base.inputs)
            except (ValueError, TypeError, KeyError) as error:
                expected = {}
                missing_reasons.append("ORIGINAL_WHOLE_ASSET_DENOMINATOR_INVALID:" + str(error))
            for key, (full_id, mvp_id, mode) in expected.items():
                original = None
                body = None
                preview = None
                planning_input = None
                basis = None
                authority = None
                try:
                    if len(expected) > MAX_PRODUCERS:
                        raise ValueError("WHOLE_ASSET_PRODUCER_CAPACITY_EXCEEDED")
                    original = read_full_policy(session, user_id, full_id, now)
                    if mvp_id is None or mode is None:
                        raise ValueError("CURRENT_ORIGINAL_MVP_ASSET_SCOPE_MISSING")
                    version = session.scalar(
                        select(PolicyVersion)
                        .where(PolicyVersion.user_id == user_id, PolicyVersion.policy_id == mvp_id)
                        .order_by(PolicyVersion.version_number.desc())
                        .limit(1)
                    )
                    assert version is not None
                    config = AssetAuthorizationPolicy.model_validate(
                        original.current_version.configuration
                    )
                    goal = session.get(Goal, config.goal_id) if config.goal_id is not None else None
                    if config.goal_id is not None and (goal is None or goal.user_id != user_id):
                        raise ValueError("CURRENT_ORIGINAL_ASSET_GOAL_MISSING")
                    body = FullAssetPrepareRequest(
                        full_policy_id=full_id,
                        expected_full_policy_version_id=original.current_version.version_id,
                        mvp_asset_policy_id=mvp_id,
                        expected_mvp_policy_version_id=version.id,
                        goal_id=config.goal_id,
                        expected_goal_policy_version_id=goal.policy_version_id if goal else None,
                        expected_epoch_id=base.inputs.epoch_id,
                        idempotency_key=f"readonly-global-asset:{full_id}:{mvp_id}:{mode}",
                        planning_mode="FIXED_LADDER" if mode == "FIXED_LADDER" else "PORTFOLIO",
                    )
                    # The public production preview runs the genuine whole planner.
                    # This call has no persistent parent, consent, Action or bank side effect.
                    preview = preview_full_asset_execution(session, user_id, body, now)
                    planning_input = _planning_input(session, user_id, original, preview, now)
                    ids = [version.policy_id] + ([goal.policy_id] if goal else [])
                    expected_versions = [version.id] + ([goal.policy_version_id] if goal else [])
                    authority = _authority(session, user_id, ids, now, expected=expected_versions)
                    portfolio = preview.portfolio
                    if portfolio is not None:
                        allocation = preview.original_full_planning.allocation
                        if allocation is None:
                            raise ValueError("ACTUAL_ORIGINAL_FULL_ASSET_ALLOCATION_MISSING")
                        from app.services.execution_context import load_execution_context

                        context: ExecutionContext = load_execution_context(
                            session, user_id, portfolio.batches[0].command.effect, now
                        )
                        accounts = list(
                            session.scalars(
                                select(Account)
                                .where(Account.user_id == user_id, Account.currency == "CNY")
                                .order_by(Account.id)
                            )
                        )
                        position_accounts: dict[UUID, UUID] = {}
                        for product in preview.original_full_planning.catalogue.products:
                            kind = (
                                "FIXED_DEPOSIT"
                                if product.asset_class == "FIXED_DEPOSIT"
                                else "CASH_MANAGEMENT"
                            )
                            account = next(
                                (row for row in accounts if row.account_type == kind), None
                            )
                            if account is not None:
                                position_accounts[product.product_id] = account.id
                        basis = FullAssetExecutionBasis(
                            user_id=user_id,
                            epoch_id=base.inputs.epoch_id,
                            as_of=now,
                            full_policy_configuration=config,
                            full_policy_content_hash=original.current_version.content_hash,
                            full_planning_confirmation_valid=original.planning_confirmation_valid,
                            original_planning_response_hash=preview.original_full_planning.input_hash,
                            planning=allocation,
                            context=context,
                            catalogue=[
                                FullAssetCatalogueReference.model_validate(row.model_dump())
                                for row in preview.original_full_planning.catalogue.bindings
                            ],
                            position_accounts=position_accounts,
                            batch_income_uses=[
                                batch.command.effect.income_uses for batch in portfolio.batches
                            ],
                            full_protection_sources=preview.original_full_protection.full_policy_sources,
                            full_protection_inventory_complete=preview.original_full_protection.projection.full_obligations_complete_within_registered_current_scope,
                            full_source_issues=preview.reasons,
                            source_evidence_ids=portfolio.source_evidence_ids,
                            expires_at=portfolio.expires_at,
                        )
                    capture_evidence(
                        session,
                        user_id,
                        sorted(
                            set(
                                preview.original_full_planning.source_evidence_ids
                                + preview.original_full_protection.source_evidence_ids
                                + authority.evidence_ids
                            )
                        ),
                    )
                    producers.append(
                        AssetProducerInput(
                            candidate_key=key,
                            full_policy_id=full_id,
                            request=body,
                            original_policy=original,
                            actual_preview=preview,
                            planning_input=planning_input,
                            execution_basis=basis,
                            authority=authority,
                        )
                    )
                except (PolicyLifecycleError, ValueError, TypeError, KeyError) as error:
                    producers.append(
                        AssetProducerInput(
                            candidate_key=key,
                            full_policy_id=full_id,
                            request=body,
                            original_policy=original,
                            actual_preview=preview,
                            planning_input=planning_input,
                            execution_basis=basis,
                            authority=authority,
                            missing_reasons=[getattr(error, "code", str(error))],
                        )
                    )
            return producers, missing_reasons
    finally:
        if previous is None:
            session.info.pop(CAPTURE_KEY, None)
        else:
            session.info[CAPTURE_KEY] = previous
            if isinstance(previous, DecisionCapture):
                previous.sources.update(capture.sources)
                previous.policies.update(capture.policies)


def capture_actual_action_set(
    session: Session, user_id: UUID, now: datetime
) -> ActualActionSetCapture:
    """One fresh RR/RO invocation; no writes or cross-request authority cache."""
    _snapshot(session)
    now = _now(now)
    base = _capture_base(session, user_id, now)
    previous = session.info.get(CAPTURE_KEY)
    session.info[CAPTURE_KEY] = base.originals
    try:
        dynamics = _capture_dynamic(session, user_id, now, base)
        assets, missing = _capture_assets(session, user_id, now, base)
        if missing:
            base = ActualBaseCapture(
                base.inputs.model_copy(
                    update={"inventory_reasons": [*base.inputs.inventory_reasons, *missing]}
                ),
                base.coverage,
                base.originals,
            )
        inputs = ActualActionSetInput(
            base=base.inputs, table_coverage=base.coverage, dynamic_goals=dynamics, assets=assets
        )
        return ActualActionSetCapture(inputs, derive_actual_action_set(inputs), base.originals)
    finally:
        if previous is None:
            session.info.pop(CAPTURE_KEY, None)
        else:
            session.info[CAPTURE_KEY] = previous
            if isinstance(previous, DecisionCapture):
                previous.sources.update(base.originals.sources)
                previous.policies.update(base.originals.policies)


def read_current_actual_action_set(
    session: Session, user_id: UUID, now: datetime
) -> ActualActionSetSnapshot:
    return capture_actual_action_set(session, user_id, now).snapshot


def verify_frozen_actual_action_set_trace(trace: DecisionTrace) -> ActualGlobalBoundaryObservation:
    verify_trace(trace)
    if (
        trace.algorithm_versions.get("global_action_set") != ALGORITHM
        or trace.phase != "EVALUATION"
    ):
        raise ValueError("Not the original FULL action-set algorithm")
    inputs = ActualActionSetInput.model_validate_json(
        json.dumps(trace.inputs["actual_action_set_input"])
    )
    snapshot = derive_actual_action_set(inputs)
    _verify_original_source_copies(trace, inputs.base, snapshot.global_action_set_complete)
    sources = {row.id: row for row in trace.sources}
    for producer in inputs.dynamic_goals:
        if producer.data is None:
            continue
        for ref in producer.data.source_refs:
            source = sources.get(ref.evidence_id)
            if (
                source is None
                or source.user_id != ref.user_id
                or source.content_hash != ref.content_hash
            ):
                raise ValueError("A consumed original dynamic source copy is missing or different")
            if (
                source.content_integrity != "VERIFIED"
                or configuration_hash(source.content) != ref.content_hash
            ):
                raise ValueError("An original dynamic source content is not verified")
    for asset_producer in inputs.assets:
        if asset_producer.actual_preview is None:
            continue
        required = set(
            asset_producer.actual_preview.original_full_planning.source_evidence_ids
            + asset_producer.actual_preview.original_full_protection.source_evidence_ids
        )
        if asset_producer.authority is not None:
            required.update(asset_producer.authority.evidence_ids)
        for identity in required:
            source = sources.get(identity)
            if (
                source is None
                or source.user_id != inputs.base.user_id
                or source.content_integrity != "VERIFIED"
                or source.captured_content_hash != configuration_hash(source.content)
            ):
                raise ValueError("An original whole-asset source copy is missing or unverified")
    value = ActualGlobalBoundaryObservation.model_validate_json(
        json.dumps(trace.outcome["actual_global_boundary_observation"])
    )
    previous = (
        ActualActionSetSnapshot.model_validate_json(json.dumps(trace.inputs["previous_snapshot"]))
        if trace.inputs["previous_snapshot"] is not None
        else None
    )
    kind, semantic = compare_actual_action_sets(previous, snapshot)
    comparable = previous is None or (
        previous.global_action_set_complete
        and (previous.user_id, previous.epoch_id, previous.scope, previous.algorithm_version)
        == (snapshot.user_id, snapshot.epoch_id, snapshot.scope, snapshot.algorithm_version)
        and previous.as_of <= snapshot.as_of
    )
    if (
        value.snapshot != snapshot
        or value.kind != kind
        or value.semantic_key != semantic
        or value.requires_user_attention != (kind == "BoundaryCrossed")
        or (trace.user_id, trace.run_id, trace.as_of)
        != (value.user_id, value.observation_run_id, inputs.base.as_of)
        or value.original_request.model_dump(mode="json") != trace.inputs["original_request"]
        or configuration_hash(value.original_request.model_dump(mode="json")) != value.request_hash
        or value.epoch_id != inputs.base.epoch_id
        or value.original_request.expected_epoch_id != inputs.base.epoch_id
        or trace.action_id is not None
        or value.idempotent_replay
        or trace.run_id
        != uuid5(
            NAMESPACE, f"{value.user_id}:{value.epoch_id}:{value.original_request.idempotency_key}"
        )
        or value.original_request.previous_observation_run_id != trace.parent_run_id
        or value.previous_observation_run_id != trace.parent_run_id
        or value.previous_snapshot_hash != (previous.snapshot_hash if previous else None)
        or value.previous_action_set_signature
        != (previous.action_set_signature if previous else None)
        or value.global_action_set_complete != (snapshot.global_action_set_complete and comparable)
    ):
        raise ValueError("Original FULL producer calculation or observation binding differs")
    return value


def read_actual_global_boundary_observation(
    session: Session, user_id: UUID, run_id: UUID, now: datetime
) -> ActualGlobalBoundaryObservation:
    result = get_decision_trace(session, user_id, run_id, now)
    if (
        result.trace is None
        or result.completeness != "COMPLETE"
        or result.audit_chain_status != "VALID"
    ):
        raise _error("FULL_GLOBAL_BOUNDARY_ORIGINAL_UNVERIFIED", "原全局轨迹和审计未完整验真")
    try:
        value = verify_frozen_actual_action_set_trace(result.trace)
        child, child_trace, visited = value, result.trace, {run_id}
        while child.previous_observation_run_id is not None:
            identity = child.previous_observation_run_id
            if identity in visited or len(visited) >= 64:
                raise ValueError("Original FULL observation ancestry is cyclic or exceeds capacity")
            visited.add(identity)
            parent = get_decision_trace(session, user_id, identity, now)
            if (
                parent.trace is None
                or parent.completeness != "COMPLETE"
                or parent.audit_chain_status != "VALID"
            ):
                raise ValueError("Actual original FULL parent is not verified")
            before = verify_frozen_actual_action_set_trace(parent.trace)
            if child_trace.inputs["previous_snapshot"] != before.snapshot.model_dump(mode="json"):
                raise ValueError("Captured previous FULL snapshot differs from original parent")
            child, child_trace = before, parent.trace
        return value
    except (ValueError, TypeError, KeyError) as error:
        raise _error("FULL_GLOBAL_BOUNDARY_ORIGINAL_UNVERIFIED", str(error)) from error


def observe_actual_global_boundary(
    engine: Engine, user_id: UUID, body: GlobalBoundaryObserveRequest, now: datetime
) -> ActualGlobalBoundaryObservation:
    now = _now(now)
    identity = uuid5(NAMESPACE, f"{user_id}:{body.expected_epoch_id}:{body.idempotency_key}")
    with Session(engine) as session, session.begin():
        _user(session, user_id)
        epoch = current_audit_epoch(session, user_id)
        if epoch is None or epoch.status != "OPEN" or epoch.id != body.expected_epoch_id:
            raise _error("STALE_FULL_GLOBAL_BOUNDARY_EPOCH", "原观察轮次已变化")
        if session.get(DecisionRun, identity) is not None:
            original = read_actual_global_boundary_observation(session, user_id, identity, now)
            if original.original_request != body:
                raise _error("IDEMPOTENCY_CONFLICT", "原观察键不能改变完整请求")
            return original.model_copy(update={"idempotent_replay": True})
        before = (
            read_actual_global_boundary_observation(
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
                current = capture_actual_action_set(read, user_id, now)
        if current.snapshot.epoch_id != epoch.id:
            raise _error("STALE_FULL_GLOBAL_BOUNDARY_EPOCH", "实际独立快照轮次不同")
        if len(current.inputs.model_dump_json().encode("utf-8")) > MAX_CAPTURE_BYTES:
            raise _error(
                "FULL_GLOBAL_BOUNDARY_CAPTURE_CAPACITY_EXCEEDED", "完整输入超限，不能删减分母"
            )
        previous = before.snapshot if before else None
        kind, semantic = compare_actual_action_sets(previous, current.snapshot)
        comparable = previous is None or (
            previous.global_action_set_complete
            and previous.epoch_id == current.snapshot.epoch_id
            and previous.as_of <= current.snapshot.as_of
        )
        value = ActualGlobalBoundaryObservation(
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
            global_action_set_complete=current.snapshot.global_action_set_complete and comparable,
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
                "actual_action_set_input": current.inputs.model_dump(mode="json"),
                "previous_snapshot": previous.model_dump(mode="json") if previous else None,
            },
            sources=list(current.originals.sources.values()),
            policies=list(current.originals.policies.values()),
            constraints=[],
            candidates=[],
            outcome={
                "actual_global_boundary_observation": value.model_dump(mode="json"),
                "decision_status": "COMPUTED" if kind else "UNKNOWN",
            },
        )
        verify_frozen_actual_action_set_trace(trace)
        record_trace(session, trace)
        return value


def actual_global_boundary_intervention_source(
    session: Session, user_id: UUID, run_id: UUID, now: datetime
) -> Source:
    result = get_decision_trace(session, user_id, run_id, now)
    if (
        result.trace is None
        or result.completeness != "COMPLETE"
        or result.audit_chain_status != "VALID"
    ):
        raise _error("FULL_GLOBAL_BOUNDARY_ORIGINAL_UNVERIFIED", "全局通知需完整原观察和有效审计")
    value = read_actual_global_boundary_observation(session, user_id, run_id, now)
    if not value.global_action_set_complete or value.kind is None or value.semantic_key is None:
        raise _error("FULL_GLOBAL_BOUNDARY_SOURCE_UNKNOWN", "不完整或初始观察不能冒充全局通知")
    return Source(
        result.trace,
        value.semantic_key,
        None,
        None,
        None,
        value.model_dump(mode="json"),
        value.requires_user_attention,
    )
