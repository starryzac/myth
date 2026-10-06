"""Private current-source experiment producer; no HTTP grants or bank writes."""

import hashlib
import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from app.db.models import ActionPlan, AssetProduct, BankOperation, User
from app.db.settings import REPOSITORY_ROOT
from app.db.testing import require_test_database
from app.domain.asset_exposure import AssetExposure
from app.domain.full_asset_allocation import FullAssetPlanningInput, FullAssetPlanOptions
from app.domain.full_asset_execution import FullAssetCatalogueReference, FullAssetPrepareRequest
from app.domain.full_experiment_asset_selection import (
    FullExperimentAssetSelection,
    FullMechanismProductOriginal,
    FullMechanismRuleOriginal,
    FullMechanismSelectedPortfolio,
    RegisteredFullMechanismRule,
    select_current_general_purchase,
)
from app.domain.full_policy_configuration import AssetAuthorizationPolicy
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import audit_read_scope, current_audit_epoch, row_copy
from app.services.dashboard_helpers import current_epoch_audit
from app.services.execution_planning import _current
from app.services.financial_read import finalize_financial_context, load_verified_financial_context
from app.services.full_asset_allocation import read_full_asset_allocation
from app.services.full_policy_lifecycle import _read_snapshot, read_full_policy
from app.services.full_protection_projection import compute_full_annual_protection
from app.services.income_ledger import read_income_state
from app.services.policy_lifecycle import PolicyLifecycleError, _now
from app.services.product_catalog import read_catalog
from sqlalchemy import func, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

LIMITATIONS = [
    "Private DEVELOPMENT current deterministic GENERAL purchase only; no formal arm run.",
    "P original planning result is retained unchanged; this selected proposal is not P OPTIMAL.",
    "The full original catalogue retains excluded and superseded products in its denominator.",
    "Verified 365-day component maxima define a conservative finite mechanism budget.",
    "Known future income and principal are not added to current CASH funding.",
    "B4 has no actual confidence-producing model; it remains MISSING.",
    "Goal, payment, recovery, uncertain-world adapters and a bank consumer are NOT_CONNECTED.",
    "Current authority, whole protection, reservation and bank gates still apply at consumption.",
]


def _fail(reason: str) -> PolicyLifecycleError:
    return PolicyLifecycleError("INVALID_REGISTERED_FULL_MECHANISM", reason, 409)


def load_registered_rule(
    locator: RegisteredFullMechanismRule, request: FullAssetPrepareRequest, user_id: UUID
) -> tuple[FullMechanismRuleOriginal, bytes]:
    """Read exact private author bytes; no source locator is an HTTP request field."""
    root = REPOSITORY_ROOT.resolve()
    path = (root / locator.original_path).resolve()
    if not any(path.is_relative_to(root / area) for area in (".runtime", "docs/experiments")):
        raise _fail("The original rule must be a registered repository-local author file")
    if not path.is_file() or path.stat().st_size > 1048576:
        raise _fail("Missing or oversized original rule")
    raw = path.read_bytes()
    if len(raw) > 1048576 or hashlib.sha256(raw).hexdigest() != locator.sha256:
        raise _fail("Original rule bytes changed")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate original rule key")
            result[key] = value
        return result

    def finite(value: str) -> Any:
        raise ValueError("Nonfinite original rule value: " + value)

    try:
        parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=finite)
        original = FullMechanismRuleOriginal.model_validate_json(
            json.dumps(parsed, ensure_ascii=False, allow_nan=False)
        )
    except (ValueError, TypeError) as error:
        raise _fail(str(error)) from error
    if original.user_id != user_id or original.original_request != request:
        raise _fail("Original owner/request/epoch/version/key differs")
    return original, raw


def _owned_read(session: Session, user_id: UUID, request: FullAssetPrepareRequest) -> None:
    bind = session.get_bind()
    engine = bind.engine if isinstance(bind, Connection) else bind
    url = engine.url
    if url.host != "127.0.0.1" or url.port != 54329 or url.get_backend_name() != "postgresql":
        raise _fail("Only the owned local simulated PostgreSQL endpoint is supported")
    database = require_test_database(url.database or "")
    _read_snapshot(session)
    if session.scalar(text("SELECT current_database()")) != database:
        raise _fail("Actual database identity differs")
    user = session.get(User, user_id)
    epoch = current_audit_epoch(session, user_id)
    if (
        user is None
        or not user.is_simulated
        or epoch is None
        or epoch.status != "OPEN"
        or epoch.id != request.expected_epoch_id
    ):
        raise _fail("Actual simulated owner/open epoch differs")


def _exposure(exposures: list[AssetExposure], now: datetime) -> AssetExposure:
    general = next((row for row in exposures if row.goal_id is None), None)
    if general is not None:
        return general
    cash: dict[UUID, int] = {}
    goals: dict[UUID, int] = {}
    for row in exposures:
        for target, source in (
            (cash, row.reserved_cash_by_account),
            (goals, row.reserved_goal_cash_by_goal),
        ):
            for identity, cents in source.items():
                if identity in target and target[identity] != cents:
                    raise ValueError("GLOBAL_RESERVATION_SCOPE_MAPS_DISAGREE")
                target[identity] = cents
    return AssetExposure(
        as_of=now,
        scope="general_idle_funds",
        managed_principal_cents=0,
        pending_purchase_cents=0,
        reserved_cash_by_account=cash,
        reserved_goal_cash_by_goal=goals,
        excluded_manual_position_ids=sorted(
            {i for r in exposures for i in r.excluded_manual_position_ids}
        ),
        evidence_ids=sorted({i for r in exposures for i in r.evidence_ids}),
    )


def read_current_general_purchase_selection(
    session: Session,
    user_id: UUID,
    request: FullAssetPrepareRequest,
    rule_original: RegisteredFullMechanismRule,
    now: datetime,
) -> FullExperimentAssetSelection:
    """Produce from fresh verified originals in this one clean RR/RO transaction."""
    request = FullAssetPrepareRequest.model_validate_json(request.model_dump_json())
    rule_original = RegisteredFullMechanismRule.model_validate_json(rule_original.model_dump_json())
    original, raw = load_registered_rule(rule_original, request, user_id)
    now = _now(now)
    _owned_read(session, user_id, request)
    originals: dict[str, object] = {"rule_original_utf8": raw.decode("utf-8")}
    counts: dict[str, int] = {}
    evidence: list[UUID] = []
    planning_input = None
    p_result = None
    inputs = None
    decision = None
    products: list[FullMechanismProductOriginal] = []
    portfolio = None
    reasons: list[str] = []
    status: Literal["PROPOSED", "NO_CANDIDATE", "UNKNOWN", "MISSING"] = "UNKNOWN"
    if request.goal_id is not None or request.planning_mode != "PORTFOLIO":
        status, reasons = "MISSING", ["GOAL_OR_LADDER_MECHANISM_ADAPTER_NOT_IMPLEMENTED"]
    else:
        try:
            with audit_read_scope(session), session.no_autoflush:
                full = read_full_policy(session, user_id, request.full_policy_id, now)
                unresolved_actions = list(
                    session.scalars(
                        select(ActionPlan)
                        .where(
                            ActionPlan.user_id == user_id,
                            ActionPlan.status.in_(["SUBMITTED", "UNKNOWN"]),
                        )
                        .order_by(ActionPlan.id)
                        .limit(1001)
                    )
                )
                unresolved_bank = list(
                    session.scalars(
                        select(BankOperation)
                        .where(
                            BankOperation.user_id == user_id,
                            BankOperation.status.in_(["ACCEPTED", "UNKNOWN"]),
                        )
                        .order_by(BankOperation.id)
                        .limit(1001)
                    )
                )
                originals["unresolved_actions"] = [row_copy(row) for row in unresolved_actions]
                originals["unresolved_bank_operations"] = [row_copy(row) for row in unresolved_bank]
                counts["unresolved_action_rows_captured"] = len(unresolved_actions)
                counts["unresolved_bank_rows_captured"] = len(unresolved_bank)
                if unresolved_actions or unresolved_bank:
                    raise ValueError(
                        "ORIGINAL_UNRESOLVED_OPERATION_REQUIRES_SAME_IDENTITY_RECOVERY"
                    )
                config = AssetAuthorizationPolicy.model_validate(full.current_version.configuration)
                if (
                    full.template_name != "AssetAuthorizationPolicy"
                    or config.goal_id is not None
                    or full.current_version.version_id != request.expected_full_policy_version_id
                    or not full.planning_confirmation_valid
                    or full.reference_validation != "CURRENT"
                    or full.effective_status not in {"ACTIVE", "CONFIRMED"}
                    or full.current_version.confirmed_at is None
                ):
                    raise ValueError("ACTUAL_CURRENT_FULL_GENERAL_PLANNING_NOT_VERIFIED")
                version = _current(session, user_id, request.mvp_asset_policy_id, now)
                if version.id != request.expected_mvp_policy_version_id:
                    raise ValueError("ACTUAL_CURRENT_MVP_VERSION_DIFFERS")
                context, matched, exposures = load_verified_financial_context(session, user_id, now)
                audit = current_epoch_audit(session, user_id, [])
                if (
                    not matched
                    or exposures is None
                    or not audit.complete
                    or audit.status != "VALID"
                ):
                    raise ValueError("ACTUAL_BANK_EXPOSURE_OR_COMPLETE_AUDIT_NOT_VERIFIED")
                context, issues, digest = finalize_financial_context(context, audit)
                if issues:
                    raise ValueError(
                        "ACTUAL_FINANCIAL_SOURCE_ISSUES:" + ",".join(i.code for i in issues)
                    )
                planning_response = read_full_asset_allocation(
                    session, user_id, request.full_policy_id, now
                )
                protection = compute_full_annual_protection(session, user_id, now)
                catalog = read_catalog(session, now)
                actual_products = session.scalar(select(func.count()).select_from(AssetProduct))
                complete_protection = (
                    protection.projection.full_obligations_complete_within_registered_current_scope
                )
                if (
                    catalog.state != "REGISTERED"
                    or not catalog.complete_within_registered_capacity
                    or catalog.issues
                    or catalog.unregistered_product_ids
                    or actual_products is None
                    or actual_products != len(catalog.versions)
                    or planning_response.catalogue.status != "VERIFIED"
                    or planning_response.source_issues
                    or protection.source_issues
                    or not complete_protection
                    or protection.projection.full_annual_projection is None
                ):
                    raise ValueError("COMPLETE_IMMUTABLE_CATALOGUE_OR_FULL_PROTECTION_NOT_VERIFIED")
                counts.update(
                    {
                        "catalogue_physical_products": actual_products,
                        "catalogue_originals": len(catalog.versions),
                        "current_effective_products": len(planning_response.catalogue.products),
                        "cash_facts": len(context.snapshot.cash_accounts),
                        "positions": len(context.positions),
                        "boundary_versions": len(context.versions),
                        "verified_exposure_scopes": len(exposures),
                        "full_protection_sources": len(protection.full_policy_sources),
                        "full_protection_points": len(
                            protection.projection.full_annual_projection.calculation_trace
                        ),
                    }
                )
                options = FullAssetPlanOptions(
                    comparison_days=original.comparison_days,
                    max_components=1,
                    funds_use_date=(
                        original.funds_use_date.astimezone(
                            ZoneInfo(context.snapshot.timezone)
                        ).date()
                        if original.funds_use_date is not None
                        else None
                    ),
                )
                planning_input = FullAssetPlanningInput(
                    snapshot=context.snapshot.model_copy(update={"horizon_days": 365}),
                    boundary_versions=context.versions,
                    positions=context.positions,
                    boundary_products=context.products,
                    products=planning_response.catalogue.products,
                    exposure=_exposure(exposures, now),
                    policy_id=full.policy_id,
                    policy_version_id=full.current_version.version_id,
                    configuration=config,
                    planning_confirmation_valid=full.planning_confirmation_valid,
                    confirmed_at=full.current_version.confirmed_at,
                    valid_from=full.current_version.valid_from,
                    valid_until=full.current_version.valid_until,
                    options=options,
                )
                try:
                    income = read_income_state(session, user_id, now)
                    originals["income_ledger"] = {
                        "ledger": income.ledger.model_dump(mode="json"),
                        "evidence_id": str(income.evidence_id),
                        "evidence_hash": income.evidence_hash,
                    }
                except PolicyLifecycleError as error:
                    if error.code != "MISSING_NEW_FUNDS_LEDGER":
                        raise
                    originals["income_ledger"] = None
                evidence = sorted(
                    set(context.sources.used)
                    | set(planning_response.source_evidence_ids)
                    | set(protection.source_evidence_ids)
                    | {UUID(i) for i in version.evidence_ids}
                )
                originals.update(
                    {
                        "full_policy": full.model_dump(mode="json"),
                        "mvp_version": row_copy(version),
                        "financial_source_digest": digest,
                        "planning_input": planning_input.model_dump(mode="json"),
                        "original_p_response": planning_response.model_dump(mode="json"),
                        "full_protection": protection.model_dump(mode="json"),
                        "complete_catalogue": catalog.model_dump(mode="json"),
                        "verified_exposures": [e.model_dump(mode="json") for e in exposures],
                        "audit": audit.model_dump(mode="json"),
                        "used_financial_evidence": [
                            row_copy(context.sources.evidence[i])
                            for i in sorted(context.sources.used)
                        ],
                    }
                )
                source_hash = configuration_hash(
                    {
                        "user_id": str(user_id),
                        "epoch_id": str(request.expected_epoch_id),
                        "as_of": now.isoformat(),
                        "originals": originals,
                        "counts": counts,
                    }
                )
                p_result = planning_response.allocation
                inputs, decision, products, batch = select_current_general_purchase(
                    planning_input,
                    protection.projection.full_annual_projection,
                    version.configuration,
                    [
                        FullAssetCatalogueReference.model_validate(b.model_dump())
                        for b in planning_response.catalogue.bindings
                    ],
                    original,
                    rule_original.sha256,
                    source_hash,
                )
                if original.rule.arm_id == "B4":
                    status, reasons = "MISSING", ["ACTUAL_CONFIDENCE_MODEL_NOT_CALLED"]
                elif original.rule.ablation in {
                    "EVIDENCE_LEVEL",
                    "POLICY_VERSION",
                    "MINIMUM_QUESTION",
                    "SAFE_RECOVERY",
                }:
                    status, reasons = (
                        "MISSING",
                        ["ABLATION_CONTRAST_NOT_IMPLEMENTED_FOR_CURRENT_DETERMINISTIC_PURCHASE"],
                    )
                elif decision.status == "PROPOSAL" and batch is not None:
                    selected = next(
                        p.catalogue for p in products if p.product.product_id == batch.product_id
                    )
                    values = {
                        "protocol": "full-mechanism-selected-portfolio-v1",
                        "user_id": str(user_id),
                        "epoch_id": str(request.expected_epoch_id),
                        "original_request": request.model_dump(mode="json"),
                        "rule_original_sha256": rule_original.sha256,
                        "current_source_hash": source_hash,
                        "mechanism_input_hash": decision.input_sha256,
                        "selected_product": selected.model_dump(mode="json"),
                        "batch": batch.model_dump(mode="json"),
                        "bank_authority": False,
                        "funds_reserved": False,
                        "preparation_status": "NOT_PREPARED",
                    }
                    portfolio = FullMechanismSelectedPortfolio.model_validate_json(
                        json.dumps(
                            {
                                **values,
                                "portfolio_hash": configuration_hash(values),
                            }
                        )
                    )
                    status = "PROPOSED"
                else:
                    status = "NO_CANDIDATE" if decision.status == "NO_CANDIDATE" else "UNKNOWN"
                    reasons = ["NO_NONEMPTY_CURRENT_SELECTED_BATCH"]
        except (ValueError, TypeError, KeyError, OverflowError, PolicyLifecycleError) as error:
            reasons = [error.code if isinstance(error, PolicyLifecycleError) else str(error)]
    final_hash = configuration_hash(
        {
            "user_id": str(user_id),
            "epoch_id": str(request.expected_epoch_id),
            "as_of": now.isoformat(),
            "originals": originals,
            "counts": counts,
        }
    )
    return FullExperimentAssetSelection(
        user_id=user_id,
        epoch_id=request.expected_epoch_id,
        as_of=now,
        status=status,
        original_request=request,
        rule_original=original,
        rule_original_sha256=rule_original.sha256,
        current_source_hash=final_hash,
        source_evidence_ids=evidence,
        source_originals=originals,
        source_counts=counts,
        planning_input=planning_input,
        original_p_result=p_result,
        mechanism_input=inputs,
        mechanism_decision=decision,
        product_originals=products,
        selected_portfolio=portfolio,
        reasons=reasons,
        limitations=LIMITATIONS,
    )
