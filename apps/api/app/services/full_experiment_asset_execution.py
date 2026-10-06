"""Private owned DEVELOPMENT consumer; the original execution pipeline owns writes."""

import inspect
import json
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Literal, cast
from uuid import UUID
from zoneinfo import ZoneInfo

from app.db.models import (
    Account,
    ActionPlan,
    AssetProduct,
    AuditEpoch,
    BankOperation,
    DecisionRun,
    User,
)
from app.db.testing import require_test_database
from app.domain.boundary_types import BoundaryModel
from app.domain.execution import ACTION_PLAN_TYPES, execution_effect_hash
from app.domain.execution_types import BankCommand, CashUse, ExecutionEffect
from app.domain.full_asset_allocation import FullAssetPlanningInput, FullAssetPlanOptions
from app.domain.full_asset_execution import FullAssetCatalogueReference
from app.domain.full_experiment_asset_execution import (
    ALGORITHM,
    GUARDS_VERSION,
    KEY_PREFIX,
    MARKER,
    FullExperimentAssetExecutionInput,
    FullExperimentAssetExecutionProof,
    FullExperimentAssetRequest,
    build_full_experiment_asset_effect,
    experiment_asset_bank_key,
    read_frozen_full_experiment_asset_proof,
)
from app.domain.full_experiment_asset_selection import FullExperimentAssetSelection
from app.domain.full_policy_configuration import AssetAuthorizationPolicy
from app.domain.full_protection_projection import (
    FullProtectionProjectionInput,
    project_full_protection,
)
from app.domain.policy_configuration import configuration_hash
from app.services.action_contracts import ActionResponse, PrepareActionRequest, PurchaseIntent
from app.services.audit_chain import row_copy
from app.services.dashboard_helpers import current_epoch_audit
from app.services.decision_recording import capture_evidence, current_capture
from app.services.decision_trace import capture_sources, get_decision_trace
from app.services.execution import get_action, prepare_action
from app.services.execution_context import load_execution_context
from app.services.execution_planning import _current, funding_income
from app.services.financial_read import finalize_financial_context, load_verified_financial_context
from app.services.full_experiment_asset_selection import (
    _owned_read,
    load_registered_rule,
    read_current_general_purchase_selection,
)
from app.services.full_policy_lifecycle import _read_snapshot, read_full_policy
from app.services.full_protection_projection import compute_full_annual_protection
from app.services.full_recovery_execution import _reader
from app.services.income_ledger import read_income_state
from app.services.policy_lifecycle import PolicyLifecycleError, _now
from app.services.product_catalog import read_catalog, verified_catalog_products
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


def _fail(message: str) -> PolicyLifecycleError:
    return PolicyLifecycleError("FULL_EXPERIMENT_ASSET_REJECTED", message, 409)


def _same_engine(engine: Engine, session: Session) -> None:
    if session.get_bind().engine is not engine:
        raise _fail("Private proof and original locked writer must use the same database")


def read_current_full_experiment_asset_inputs(
    session: Session,
    user_id: UUID,
    body: FullExperimentAssetRequest,
    action_id: UUID,
    now: datetime,
    *,
    original_selection: FullExperimentAssetSelection | None = None,
    own_effect: ExecutionEffect | None = None,
) -> FullExperimentAssetExecutionInput:
    """Complete fresh RR/RO originals; own claim restoration is the old strict adapter."""
    now = _now(now)
    body = FullExperimentAssetRequest.model_validate_json(body.model_dump_json())
    request = body.original_request
    _owned_read(session, user_id, request)
    epoch = session.get(AuditEpoch, request.expected_epoch_id)
    if epoch is None or epoch.user_id != user_id or epoch.status != "OPEN" or epoch.opened_at > now:
        raise _fail("The actual original OPEN epoch must be known at this server time")
    rule, raw = load_registered_rule(body.rule_original, request, user_id)
    full = read_full_policy(session, user_id, request.full_policy_id, now)
    configuration = AssetAuthorizationPolicy.model_validate(full.current_version.configuration)
    if (
        full.template_name != "AssetAuthorizationPolicy"
        or full.epoch_id != request.expected_epoch_id
        or full.current_version.version_id != request.expected_full_policy_version_id
        or full.effective_status not in {"ACTIVE", "CONFIRMED"}
        or not full.planning_confirmation_valid
        or full.reference_validation != "CURRENT"
        or configuration.goal_id is not None
        or full.current_version.confirmed_at is None
        or configuration_hash(configuration.model_dump(mode="json"))
        != full.current_version.content_hash
    ):
        raise _fail("Current actual confirmed FULL GENERAL version is required")
    version = _current(session, user_id, request.mvp_asset_policy_id, now)
    if version.id != request.expected_mvp_policy_version_id:
        raise _fail("The original current MVP version changed")
    unresolved = list(
        session.scalars(
            select(ActionPlan)
            .where(ActionPlan.user_id == user_id, ActionPlan.status.in_(["SUBMITTED", "UNKNOWN"]))
            .order_by(ActionPlan.id)
            .limit(1001)
        )
    )
    bank_unresolved = list(
        session.scalars(
            select(BankOperation)
            .where(
                BankOperation.user_id == user_id, BankOperation.status.in_(["ACCEPTED", "UNKNOWN"])
            )
            .order_by(BankOperation.id)
            .limit(1001)
        )
    )
    own = own_effect.operation_id if own_effect is not None else None
    if (
        len(unresolved) > 1000
        or len(bank_unresolved) > 1000
        or any(row.id != own for row in unresolved)
        or bank_unresolved
    ):
        raise _fail(
            "All other unresolved identities must be recovered; no denominator may be hidden"
        )
    base, matched, exposures = load_verified_financial_context(session, user_id, now)
    audit = current_epoch_audit(session, user_id, [])
    if not matched or exposures is None or not audit.complete or audit.status != "VALID":
        raise _fail("Complete original BANK/exposure/audit sources are not verified")
    base, issues, digest = finalize_financial_context(base, audit)
    if issues:
        raise _fail("Actual sources are unknown:" + ",".join(row.code for row in issues))
    catalogue = read_catalog(session, now)
    products = verified_catalog_products(session, now)
    physical = session.scalar(select(func.count()).select_from(AssetProduct))
    protection = compute_full_annual_protection(session, user_id, now)
    if (
        catalogue.state != "REGISTERED"
        or catalogue.issues
        or catalogue.unregistered_product_ids
        or not catalogue.complete_within_registered_capacity
        or physical != len(catalogue.versions)
        or products.status != "VERIFIED"
        or protection.source_issues
        or not protection.projection.full_obligations_complete_within_registered_current_scope
    ):
        raise _fail("Complete actual catalogue and registered FULL protection are required")
    if original_selection is None:
        if own_effect is not None:
            raise _fail("An own original effect cannot manufacture its PREPARE selection")
        original_selection = read_current_general_purchase_selection(
            session, user_id, request, body.rule_original, now
        )
    if original_selection.status != "PROPOSED" or original_selection.selected_portfolio is None:
        raise _fail("Actual original mechanism selection is not a nonempty proposal")
    selected = original_selection.selected_portfolio.batch
    selected_product = next(
        (row for row in products.products if row.product_id == selected.product_id), None
    )
    if selected_product is None:
        raise _fail("Original product is no longer present in the actual current catalogue")
    account_type = (
        "FIXED_DEPOSIT" if selected_product.asset_class == "FIXED_DEPOSIT" else "CASH_MANAGEMENT"
    )
    accounts = list(
        session.scalars(
            select(Account)
            .where(
                Account.user_id == user_id,
                Account.currency == "CNY",
                Account.account_type == account_type,
            )
            .order_by(Account.id)
        )
    )
    if not accounts:
        raise _fail("Actual owned position account is missing")
    cash = [
        CashUse(account_id=row.account_id, amount_cents=row.amount_cents)
        for row in selected.cash_uses
    ]
    income = (
        funding_income(session, user_id, cash, now)
        if own_effect is None
        else own_effect.income_uses
    )
    expiry = (
        min(
            [now + timedelta(minutes=15)]
            + [
                value
                for value in [full.current_version.valid_until, version.valid_until]
                if value is not None
            ]
        )
        if own_effect is None
        else own_effect.expires_at
    )
    from uuid import uuid5

    probe = own_effect or ExecutionEffect(
        operation_id=action_id,
        user_id=user_id,
        business_key="purchase:" + str(action_id),
        action_type="PURCHASE_ASSET",
        amount_cents=selected.amount_cents,
        cash_uses=cash,
        income_uses=income,
        policy_id=request.mvp_asset_policy_id,
        policy_version_id=version.id,
        policy_version_ids=[version.id],
        product_id=selected.product_id,
        product_version_number=selected.version_number,
        terms_digest=selected.terms_digest,
        position_id=uuid5(action_id, "position"),
        position_account_id=accounts[0].id,
        return_account_id=cash[0].account_id,
        purchase_exit=selected.exit_plan,
        latest_arrival_at=selected.principal_available_at + (expiry - now),
        valid_from=now,
        expires_at=expiry,
    )
    context = load_execution_context(
        session, user_id, probe, now, own_action_id=own, base_context=base
    )
    context = context.model_copy(
        update={
            "snapshot": context.snapshot.model_copy(update={"horizon_days": 365}),
            "requires_confirmation": True,
        }
    )
    if context.exposure is None or context.source_issues:
        raise _fail("Own reservations are not fully matched to this exact original Effect")
    projection = project_full_protection(
        FullProtectionProjectionInput(
            snapshot=context.snapshot,
            positions=context.positions,
            boundary_versions=context.versions,
            boundary_products=context.boundary_products,
            policies=protection.full_policy_sources,
            reserved_cash_by_account=context.reserved_cash_by_account,
        )
    )
    if (
        not projection.full_obligations_complete_within_registered_current_scope
        or projection.full_annual_projection is None
    ):
        raise _fail("The complete current original FULL curve is unknown")
    planning = FullAssetPlanningInput(
        snapshot=context.snapshot,
        boundary_versions=context.versions,
        positions=context.positions,
        boundary_products=context.boundary_products,
        products=products.products,
        exposure=context.exposure,
        policy_id=full.policy_id,
        policy_version_id=full.current_version.version_id,
        configuration=configuration,
        planning_confirmation_valid=full.planning_confirmation_valid,
        confirmed_at=full.current_version.confirmed_at,
        valid_from=full.current_version.valid_from,
        valid_until=full.current_version.valid_until,
        options=FullAssetPlanOptions(
            comparison_days=rule.comparison_days,
            max_components=1,
            funds_use_date=rule.funds_use_date.astimezone(
                ZoneInfo(context.snapshot.timezone)
            ).date()
            if rule.funds_use_date
            else None,
        ),
    )
    identities = (
        set(base.sources.used)
        | set(full.current_version.evidence_ids)
        | set(protection.source_evidence_ids)
        | {UUID(key) for key in version.evidence_ids}
        | {identity for lot in context.lots for identity in lot.evidence_ids}
    )
    originals = capture_sources(session, user_id, identities)
    income_original = None
    try:
        ledger = read_income_state(session, user_id, now)
        income_original = {
            "ledger": ledger.ledger.model_dump(mode="json"),
            "evidence_id": str(ledger.evidence_id),
            "hash": ledger.evidence_hash,
        }
    except PolicyLifecycleError as error:
        if error.code != "MISSING_NEW_FUNDS_LEDGER":
            raise
    current_originals = {
        "protocol": ALGORITHM,
        "purpose": "DEVELOPMENT",
        "rule_original_utf8": raw.decode("utf-8"),
        "full_policy": full.model_dump(mode="json"),
        "mvp_version": row_copy(version),
        "complete_catalogue": catalogue.model_dump(mode="json"),
        "current_products": products.model_dump(mode="json"),
        "full_protection": protection.model_dump(mode="json"),
        "raw_financial_digest": digest,
        "original_income_ledger": income_original,
        "raw_exposures": [row.model_dump(mode="json") for row in exposures],
        "actual_unresolved_actions": [row_copy(row) for row in unresolved],
        "actual_unresolved_bank_operations": [row_copy(row) for row in bank_unresolved],
        "own_action_id": str(own) if own else None,
        "own_claim_view": context.model_dump(mode="json"),
        "counts": {
            "physical_products": physical,
            "catalogue_originals": len(catalogue.versions),
            "current_products": len(products.products),
            "raw_unresolved_actions": len(unresolved),
            "raw_unresolved_banks": len(bank_unresolved),
            "exposure_scopes": len(exposures),
            "used_evidence": len(originals),
        },
    }
    return FullExperimentAssetExecutionInput(
        user_id=user_id,
        epoch_id=request.expected_epoch_id,
        as_of=now,
        request=body,
        original_selection=original_selection,
        rule=rule,
        rule_original_utf8=raw.decode("utf-8"),
        current_planning=planning,
        current_curve=projection.full_annual_projection,
        context=context,
        catalogue=[
            FullAssetCatalogueReference.model_validate(row.model_dump())
            for row in products.bindings
        ],
        position_account_id=accounts[0].id,
        income_uses=income,
        expires_at=expiry,
        protection_sources=protection.full_policy_sources,
        protection_inventory_complete=True,
        source_issues=[],
        source_originals=originals,
        current_originals=current_originals,
        own_action_id=own,
    )


def _fresh(
    engine: Engine,
    user_id: UUID,
    body: FullExperimentAssetRequest,
    action_id: UUID,
    now: datetime,
    *,
    original_selection: FullExperimentAssetSelection | None = None,
    own_effect: ExecutionEffect | None = None,
) -> FullExperimentAssetExecutionInput:
    try:
        with _reader(engine) as read:
            return read_current_full_experiment_asset_inputs(
                read,
                user_id,
                body,
                action_id,
                now,
                original_selection=original_selection,
                own_effect=own_effect,
            )
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        raise _fail("Current complete private source is invalid:" + str(error)) from error


def _capture(
    session: Session,
    data: FullExperimentAssetExecutionInput,
    proof: FullExperimentAssetExecutionProof,
) -> None:
    capture = current_capture(session)
    if capture is None:
        raise _fail("The original transaction decision capture is mandatory")
    capture.inputs[MARKER] = {
        "inputs": data.model_dump(mode="json"),
        "proof": proof.model_dump(mode="json"),
    }
    capture.algorithms[MARKER] = ALGORITHM
    capture_evidence(session, data.user_id, [row.id for row in data.source_originals])
    if any(capture.sources.get(row.id) != row for row in data.source_originals):
        raise _fail("Fresh complete original source copies changed before capture")


def produce_full_experiment_asset_effect(
    engine: Engine,
    locked_session: Session,
    user_id: UUID,
    action_id: UUID,
    body: FullExperimentAssetRequest,
    now: datetime,
) -> tuple[ExecutionEffect, FullExperimentAssetExecutionProof, dict[str, Any]]:
    _same_engine(engine, locked_session)
    data = _fresh(engine, user_id, body, action_id, now)
    try:
        effect, proof = build_full_experiment_asset_effect(data, action_id)
    except (ValueError, TypeError, KeyError) as error:
        raise _fail(str(error)) from error
    _capture(locked_session, data, proof)
    marker = {
        "protocol": ALGORITHM,
        "purpose": "DEVELOPMENT",
        "user_id": str(user_id),
        "epoch_id": str(data.epoch_id),
        "request": body.model_dump(mode="json"),
        "request_hash": configuration_hash(body.model_dump(mode="json")),
        "effect_hash": execution_effect_hash(effect),
        "original_proof": proof.model_dump(mode="json"),
    }
    return effect, proof, marker


def has_full_experiment_asset_binding(session: Session, action: ActionPlan) -> bool:
    if MARKER in action.request or action.idempotency_key.startswith(KEY_PREFIX):
        return True
    run = session.get(DecisionRun, action.decision_run_id)
    if run is None:
        return False
    if (
        run.user_id != action.user_id
        or run.subject_action_plan_id != action.id
        or configuration_hash(run.input_snapshot) != run.snapshot_hash
    ):
        raise _fail("Actual original PREPARE identity/hash differs")
    trace = run.input_snapshot.get("decision_trace", {})
    if not isinstance(trace, dict):
        raise _fail("Actual original PREPARE is not a complete object")
    algorithms, inputs = trace.get("algorithm_versions", {}), trace.get("inputs", {})
    action_request = inputs.get("action_request", {}) if isinstance(inputs, dict) else {}
    planning = inputs.get("planning", {}) if isinstance(inputs, dict) else {}
    return bool(
        isinstance(algorithms, dict)
        and algorithms.get(MARKER) == ALGORITHM
        or isinstance(action_request, dict)
        and MARKER in action_request
        or isinstance(planning, dict)
        and MARKER in planning
    )


def read_original_full_experiment_asset_request(
    session: Session, user_id: UUID, action: ActionPlan, command: BankCommand, now: datetime
) -> tuple[FullExperimentAssetRequest, FullExperimentAssetSelection]:
    try:
        current = get_action(session, user_id, action.id, now)
        recorded = get_decision_trace(session, user_id, action.decision_run_id, now)
        trace = recorded.trace
        if (
            recorded.completeness != "COMPLETE"
            or recorded.audit_chain_status != "VALID"
            or trace is None
            or trace.phase != "PREPARE"
            or trace.user_id != user_id
            or trace.action_id != action.id
            or trace.run_id != action.decision_run_id
            or trace.algorithm_versions.get(MARKER) != ALGORITHM
        ):
            raise ValueError("Actual complete original PREPARE/audit is mandatory")
        original = trace.inputs["action_request"]
        marker = original[MARKER]
        body = FullExperimentAssetRequest.model_validate_json(json.dumps(marker["request"]))
        data = FullExperimentAssetExecutionInput.model_validate_json(
            json.dumps(trace.inputs["planning"][MARKER]["inputs"])
        )
        proof = read_frozen_full_experiment_asset_proof(trace)
        effect = command.effect
        if (
            body != data.request
            or marker["protocol"] != ALGORITHM
            or marker["purpose"] != "DEVELOPMENT"
            or marker["user_id"] != str(user_id)
            or marker["epoch_id"] != str(data.epoch_id)
            or marker["request_hash"] != configuration_hash(body.model_dump(mode="json"))
            or marker["effect_hash"] != command.effect_hash
            or marker["original_proof"] != proof.model_dump(mode="json")
            or original != action.request
            or configuration_hash(original) != action.request_hash
            or BankCommand.model_validate_json(json.dumps(original["execution"])) != command
            or current.effect_hash != command.effect_hash
            or action.idempotency_key
            != experiment_asset_bank_key(body.original_request.idempotency_key)
            or effect.operation_id != action.id
            or effect.user_id != user_id
            or action.user_id != user_id
            or action.action_type != ACTION_PLAN_TYPES["PURCHASE_ASSET"]
            or action.autonomy_level != "ASK_ONCE"
            or action.amount_cents != effect.amount_cents
            or action.goal_id is not None
            or action.product_id != effect.product_id
            or action.position_id is not None
            or action.policy_version_id != effect.policy_version_id
            or action.source_account_id != effect.cash_uses[0].account_id
            or action.destination_account_id is not None
            or action.expires_at != effect.expires_at
            or action.created_at != effect.valid_from
        ):
            raise ValueError("Whole original effect/request/selection or Action projection differs")
        return body, data.original_selection
    except (ValueError, TypeError, KeyError, IndexError) as error:
        raise _fail(str(error)) from error


def recheck_full_experiment_asset_proof(
    engine: Engine,
    locked_session: Session,
    action: ActionPlan,
    command: BankCommand,
    now: datetime,
    *,
    own_action_id: UUID | None = None,
) -> FullExperimentAssetExecutionProof:
    _same_engine(engine, locked_session)
    body, selection = read_original_full_experiment_asset_request(
        locked_session, action.user_id, action, command, now
    )
    if own_action_id not in {None, action.id}:
        raise _fail("No other operation's claim may be restored")
    data = _fresh(
        engine,
        action.user_id,
        body,
        action.id,
        now,
        original_selection=selection,
        own_effect=command.effect,
    )
    try:
        _, proof = build_full_experiment_asset_effect(data, action.id, command.effect)
    except (ValueError, TypeError, KeyError) as error:
        raise _fail(str(error)) from error
    if current_capture(locked_session) is not None:
        _capture(locked_session, data, proof)
    return proof


def verify_full_experiment_asset_prepare_replay(
    session: Session,
    user_id: UUID,
    action: ActionPlan,
    body: FullExperimentAssetRequest,
    now: datetime,
) -> ActionResponse:
    command = BankCommand.model_validate_json(json.dumps(action.request["execution"]))
    original, _ = read_original_full_experiment_asset_request(
        session, user_id, action, command, now
    )
    if original != body:
        raise PolicyLifecycleError(
            "IDEMPOTENCY_CONFLICT", "Same key cannot replace original rule/body", 409
        )
    return get_action(session, user_id, action.id, now)


def prepare_full_experiment_asset_execution(
    engine: Engine, user_id: UUID, body: FullExperimentAssetRequest, now: datetime
) -> ActionResponse:
    """Private trusted entry; explicitly blocked until all original strict hooks are installed."""
    from app.services import execution, execution_bank

    parameter = "_full_experiment_asset_request"
    if (
        parameter not in inspect.signature(prepare_action).parameters
        or getattr(execution, "FULL_EXPERIMENT_ASSET_GUARDS_VERSION", None) != GUARDS_VERSION
        or getattr(execution_bank, "FULL_EXPERIMENT_ASSET_GUARDS_VERSION", None) != GUARDS_VERSION
    ):
        raise PolicyLifecycleError(
            "FULL_EXPERIMENT_ASSET_NOT_IMPLEMENTED",
            "Original pipeline guards/capture are not installed",
            409,
        )
    original = PrepareActionRequest(
        idempotency_key=body.original_request.idempotency_key,
        intent=PurchaseIntent(
            kind="purchase_asset", policy_id=body.original_request.mvp_asset_policy_id
        ),
    )
    return cast(Callable[..., ActionResponse], prepare_action)(
        engine, user_id, original, now, **{parameter: body}
    )


class FullExperimentAssetLookup(BoundaryModel):
    simulation: Literal[True] = True
    purpose: Literal["DEVELOPMENT"] = "DEVELOPMENT"
    bank_authority: Literal[False] = False
    receipt_is_current_authority: Literal[False] = False
    not_found_is_final: Literal[False] = False
    user_id: UUID
    idempotency_key: str
    status: Literal["RECORDED", "NOT_FOUND_NOT_FINAL"]
    original_request: FullExperimentAssetRequest | None = None
    original_action_request: dict[str, Any] | None = None
    request_hash: str | None = None
    action: ActionResponse | None = None


def lookup_full_experiment_asset_execution(
    session: Session, user_id: UUID, original_key: str, now: datetime
) -> FullExperimentAssetLookup:
    """Private exact-key original read, including Bank UNKNOWN; never reselects/files/POST."""
    _read_snapshot(session)
    url = session.get_bind().engine.url
    database = require_test_database(url.database or "")
    owner = session.get(User, user_id)
    if (
        url.host != "127.0.0.1"
        or url.port != 54329
        or url.get_backend_name() != "postgresql"
        or session.scalar(text("SELECT current_database()")) != database
        or owner is None
        or not owner.is_simulated
    ):
        raise _fail("Only this actual owned simulated PostgreSQL identity may read originals")
    key = experiment_asset_bank_key(original_key)
    action = session.scalar(
        select(ActionPlan).where(ActionPlan.user_id == user_id, ActionPlan.idempotency_key == key)
    )
    empty = FullExperimentAssetLookup(
        user_id=user_id, idempotency_key=original_key, status="NOT_FOUND_NOT_FINAL"
    )
    if action is None:
        return empty
    command = BankCommand.model_validate_json(json.dumps(action.request["execution"]))
    original, _ = read_original_full_experiment_asset_request(
        session, user_id, action, command, _now(now)
    )
    if original.original_request.idempotency_key != original_key:
        raise _fail("The original private key differs")
    return empty.model_copy(
        update={
            "status": "RECORDED",
            "original_request": original,
            "original_action_request": action.request,
            "request_hash": action.request_hash,
            "action": get_action(session, user_id, action.id, _now(now)),
        }
    )
