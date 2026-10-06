"""Current original financial sources over a caller-owned read-only snapshot; no writes."""

import copy
import hashlib
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID

from app.domain.boundary import compute_boundary
from app.domain.boundary_types import BoundaryResult
from app.domain.policy_configuration import configuration_hash
from app.domain.scenario_simulation import (
    LIMITATIONS,
    CashChoice,
    EmergencyChoice,
    ProductChoice,
    ScenarioBasis,
    ScenarioCompareRequest,
    ScenarioComparison,
    SimulationFlags,
    choices,
    compare_scenario,
)
from app.services.boundary import BoundaryContext, BoundarySourceIssue
from app.services.dashboard_helpers import current_epoch_audit
from app.services.dashboard_types import DashboardAuditCard
from app.services.financial_read import finalize_financial_context, load_verified_financial_context
from app.services.policy_lifecycle import PolicyLifecycleError, _now
from sqlalchemy import text
from sqlalchemy.orm import Session

ENGINE_FILES = (
    "domain/boundary.py",
    "domain/boundary_types.py",
    "domain/policy_change_types.py",
    "domain/policy_configuration.py",
    "domain/scenario_simulation.py",
    "services/boundary.py",
    "services/financial_read.py",
    "services/dashboard_helpers.py",
    "services/audit_chain.py",
    "services/policy_lifecycle.py",
    "services/simulated_bank.py",
    "services/asset_exposure_import.py",
    "services/income_ledger.py",
    "api/v1/scenario_simulation.py",
    "services/scenario_simulation.py",
)


class ScenarioContext(SimulationFlags):
    schema_version: Literal["counterfactual-context-v1"] = "counterfactual-context-v1"
    user_id: UUID
    epoch_id: UUID
    as_of: datetime
    timezone: Literal["Asia/Shanghai", "UTC"]
    source_hash: str
    engine_hash: str
    engine_files: dict[str, str]
    cash_choices: list[CashChoice]
    emergency_choices: list[EmergencyChoice]
    product_choices: list[ProductChoice]
    baseline_90: BoundaryResult
    baseline_365: BoundaryResult
    source_evidence_ids: list[UUID]
    source_issues: list[BoundarySourceIssue]
    audit: DashboardAuditCard
    limitations: list[str]


def engine_sources() -> tuple[str, dict[str, str]]:
    root = Path(__file__).resolve().parents[1]
    hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in ENGINE_FILES}
    return configuration_hash(hashes), hashes


def source_fingerprint(context: BoundaryContext, audit: DashboardAuditCard) -> str:
    """Retain actual consulted originals; exclude only recomputation-clock fields."""
    snapshot = copy.deepcopy(context.snapshot.model_dump(mode="json"))
    snapshot.pop("as_of")
    snapshot.pop("source_digest", None)
    for row in snapshot["living_reserves"]:
        row.pop("estimation_input_digest", None)
    zone = UTC if context.snapshot.timezone == "UTC" else timezone(timedelta(hours=8))
    sources = []
    for identifier in sorted(context.sources.used):
        item = context.sources.evidence.get(identifier)
        sources.append(
            None
            if item is None
            else {
                "id": str(item.id),
                "user_id": str(item.user_id),
                "source_type": item.source_type,
                "evidence_level": item.evidence_level,
                "status": item.status,
                "observed_at": item.observed_at.isoformat(),
                "valid_from": item.valid_from.isoformat(),
                "valid_to": item.valid_to.isoformat() if item.valid_to else None,
                "content": item.content,
                "content_hash": item.content_hash,
            }
        )
    return configuration_hash(
        {
            "protocol": "counterfactual-source-v1",
            "user_id": str(context.sources.user_id),
            "local_date": context.snapshot.as_of.astimezone(zone).date().isoformat(),
            "snapshot": snapshot,
            "versions": [v.model_dump(mode="json") for v in context.versions],
            "positions": [p.model_dump(mode="json") for p in context.positions],
            "products": [p.model_dump(mode="json") for p in context.products],
            "source_ids": [str(i) for i in sorted(context.sources.used)],
            "sources": sources,
            "audit": audit.model_dump(mode="json"),
        }
    )


def _fresh(
    session: Session, user_id: UUID, now: datetime
) -> tuple[ScenarioBasis, BoundaryContext, DashboardAuditCard, dict[str, str]]:
    if (
        session.new
        or session.dirty
        or session.deleted
        or session.connection().get_isolation_level() != "REPEATABLE READ"
        or session.scalar(text("SHOW transaction_read_only")) != "on"
    ):
        raise PolicyLifecycleError(
            "INVALID_READ_SNAPSHOT", "反事实计算必须使用无待写入状态的只读可重复读事务", 409
        )
    now = _now(now)
    context, _, _ = load_verified_financial_context(session, user_id, now)
    audit = current_epoch_audit(session, user_id, [])
    if audit.epoch_id is None:
        raise PolicyLifecycleError(
            "SIMULATION_EPOCH_MISSING", "没有当前原epoch，不能绑定只读反事实", 409
        )
    if not audit.complete or audit.status != "VALID":
        context.sources.issue(
            "AUDIT_NOT_VERIFIED", str(audit.epoch_id), "当前审计不完整；反事实金额仍未证明"
        )
    context, _, _ = finalize_financial_context(context, audit)
    engine_hash, files = engine_sources()
    basis = ScenarioBasis(
        user_id=user_id,
        epoch_id=audit.epoch_id,
        source_hash=source_fingerprint(context, audit),
        engine_hash=engine_hash,
        snapshot=context.snapshot,
        versions=context.versions,
        positions=context.positions,
        products=context.products,
    )
    return basis, context, audit, files


def read_scenario_context(session: Session, user_id: UUID, now: datetime) -> ScenarioContext:
    with session.no_autoflush:
        basis, context, audit, files = _fresh(session, user_id, now)
        try:
            cash, emergency, products = choices(basis)
            result = ScenarioContext(
                user_id=user_id,
                epoch_id=basis.epoch_id,
                as_of=basis.snapshot.as_of,
                timezone=basis.snapshot.timezone,
                source_hash=basis.source_hash,
                engine_hash=basis.engine_hash,
                engine_files=files,
                cash_choices=cash,
                emergency_choices=emergency,
                product_choices=products,
                baseline_90=compute_boundary(
                    basis.snapshot.model_validate(
                        {**basis.snapshot.model_dump(), "horizon_days": 90}
                    ),
                    basis.versions,
                    basis.positions,
                    basis.products,
                ),
                baseline_365=compute_boundary(
                    basis.snapshot.model_validate(
                        {**basis.snapshot.model_dump(), "horizon_days": 365}
                    ),
                    basis.versions,
                    basis.positions,
                    basis.products,
                ),
                source_evidence_ids=sorted(context.sources.used),
                source_issues=context.sources.issues,
                audit=audit,
                limitations=LIMITATIONS,
            )
            _stable_engine(basis.engine_hash)
            return result
        except (TypeError, ValueError, OverflowError) as error:
            raise PolicyLifecycleError(
                "INVALID_SIMULATION_INPUT", "原反事实输入不一致或日期超出支持范围", 409
            ) from error


def compare_current_scenario(
    session: Session, user_id: UUID, now: datetime, request: ScenarioCompareRequest
) -> ScenarioComparison:
    with session.no_autoflush:
        basis, _, _, _ = _fresh(session, user_id, now)
        try:
            result = compare_scenario(basis, request)
            _stable_engine(basis.engine_hash)
            return result
        except (TypeError, ValueError, OverflowError) as error:
            raise PolicyLifecycleError(
                "STALE_OR_INVALID_SIMULATION",
                "原epoch、事实、引擎或选定参数已变化/无效；请只读刷新后重新审阅",
                409,
            ) from error


def _stable_engine(expected: str) -> None:
    if engine_sources()[0] != expected:
        raise PolicyLifecycleError(
            "SIMULATION_ENGINE_CHANGED", "本次计算期间引擎源发生变化；不能确认本响应源绑定", 409
        )
